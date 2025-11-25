import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import os
import requests
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

load_dotenv()

# --------------------------------------------------
# CONFIG (EDIT THESE)
# --------------------------------------------------

GUILD_ID = 1097913605082579024
MAPVOTE_CHANNEL_ID = 1441751747935735878

# Fallback match length (CRCON cannot provide this on your host)
MATCH_LENGTH_MINUTES = 60   # <-- EDIT if needed (e.g., 60 for Warfare only)
MATCH_LENGTH_SECONDS = MATCH_LENGTH_MINUTES * 60

# Vote ends 2 minutes before match end
VOTE_END_OFFSET_SECONDS = 120

# How often to re-check / update embed (seconds)
EMBED_UPDATE_INTERVAL = 1

# --- In-game / Discord announcement text (EDIT THESE) ---
INGAME_2MIN_WARNING = "Next-map vote closes in 2 minutes! Vote now in Discord."
DISCORD_2MIN_WARNING = "⏳ **Next-map vote closes in 2 minutes! Get your votes in now.**"

# --- Maps to vote on (dropdown supports up to 25) ---
# Pretty name -> CRCON map id
MAPS = {
    "Elsenborn Ridge Warfare (Day)": "elsenbornridge_warfare_day",
    "Carentan Warfare": "carentan_warfare",
    "Foy Warfare": "foy_warfare",
    "Hill 400 Warfare": "hill400_warfare",
    # add more here
}

# --- Your CDN images for CURRENT map display ---
# Key should match the CRCON pretty_name for current map
MAP_CDN_IMAGES = {
    "Elsenborn Ridge Warfare (Day)": "https://cdn.discordapp.com/attachments/1365401621110067281/1365408158012407840/Elsenborn_Custom_MLL.png?ex=69260270&is=6924b0f0&hm=da89e48d9a833c3a4c2f25e460b4997568545a15357f077b8c62a5a24523c295",
    "Carentan Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365403110197166191/Carentan_SP_NoHQ.png?ex=6925fdbd&is=6924ac3d&hm=5e5308525d18a34f3249bdb7bc54f9cc9f8225c0871aca9825307e8189e58439",
    "Foy Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365404141337186304/Foy_SP_NoHQ.png?ex=6925feb3&is=6924ad33&hm=280c8db3b1d8da6b9fc32c4439cf31952a63e5aae1a1b587d1e3a4287a489774",
    "Hill 400 Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365404269116919930/Hill400_SP_NoHQ_1.png?ex=6925fed1&is=6924ad51&hm=4e2db05512162439e98154adf75f940803750f35f6f47f8dd908d1de1e9ec7ad",
}

# --------------------------------------------------
# CRCON API WRAPPER (Bearer token)
# --------------------------------------------------

CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"
CRCON_API_KEY = os.getenv("CRCON_API_KEY")

def rcon_get(endpoint: str):
    try:
        r = requests.get(
            CRCON_PANEL_URL + endpoint,
            headers={"Authorization": f"Bearer {CRCON_API_KEY}"},
            timeout=10
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def rcon_post(endpoint: str, payload: dict):
    try:
        r = requests.post(
            CRCON_PANEL_URL + endpoint,
            json=payload,
            headers={"Authorization": f"Bearer {CRCON_API_KEY}"},
            timeout=10
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}

# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def parse_crcon_start(ts: str):
    """
    CRCON returns ISO timestamp like:
    '2025-11-24T19:10:22.123Z'
    """
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts).astimezone(timezone.utc)
    except Exception:
        return None

async def get_current_map_info():
    data = rcon_get("get_current_map")
    if data.get("failed") or "error" in data:
        return None

    result = data.get("result") or {}
    pretty = result.get("pretty_name") or result.get("name") or "Unknown"
    map_id = result.get("id") or "unknown"
    start_raw = result.get("start")
    start_dt = parse_crcon_start(start_raw) if start_raw else None

    return {
        "pretty_name": pretty,
        "id": map_id,
        "start_dt": start_dt,
        "start_raw": start_raw
    }

async def send_ingame_broadcast(message: str):
    """
    Your commands list says:
      ServerBroadcast <Message>

    We call the REST endpoint as:
      POST /api/ServerBroadcast
      body {"message": "..."}
    """
    return rcon_post("ServerBroadcast", {"message": message})

# --------------------------------------------------
# VOTE STATE
# --------------------------------------------------

class VoteState:
    def __init__(self):
        self.active = False
        self.vote_message_id = None
        self.vote_channel = None

        self.current_map = None
        self.match_start_dt = None
        self.match_end_dt = None
        self.vote_end_dt = None

        self.user_votes = {}   # user_id -> map_id
        self.vote_counts = {}  # map_id -> count

        self.warning_sent = False

    def reset_for_new_match(self, current_map):
        self.active = True
        self.vote_message_id = None

        self.current_map = current_map
        self.match_start_dt = current_map.get("start_dt")

        self.user_votes.clear()
        self.vote_counts.clear()

        if self.match_start_dt:
            self.match_end_dt = self.match_start_dt + timedelta(seconds=MATCH_LENGTH_SECONDS)
            self.vote_end_dt = self.match_end_dt - timedelta(seconds=VOTE_END_OFFSET_SECONDS)
        else:
            self.match_end_dt = None
            self.vote_end_dt = None

        self.warning_sent = False

    def set_user_vote(self, user_id: int, map_id: str):
        old = self.user_votes.get(user_id)
        if old == map_id:
            return

        if old:
            self.vote_counts[old] = max(0, self.vote_counts.get(old, 1) - 1)
            if self.vote_counts[old] == 0:
                del self.vote_counts[old]

        self.user_votes[user_id] = map_id
        self.vote_counts[map_id] = self.vote_counts.get(map_id, 0) + 1

    def winner_map_id(self):
        if not self.vote_counts:
            return None
        return max(self.vote_counts.items(), key=lambda kv: kv[1])[0]

# --------------------------------------------------
# DROPDOWN UI
# --------------------------------------------------

class MapVoteSelect(discord.ui.Select):
    def __init__(self, vote_state: VoteState, cog_ref):
        self.vote_state = vote_state
        self.cog_ref = cog_ref

        options = [
            discord.SelectOption(
                label=pretty,
                value=map_id,
                description="Vote for this map"
            )
            for pretty, map_id in MAPS.items()
        ]

        super().__init__(
            placeholder="Vote for the next map…",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        if not self.vote_state.active:
            return await interaction.response.send_message("Vote is not active.", ephemeral=True)

        chosen_map_id = self.values[0]
        self.vote_state.set_user_vote(interaction.user.id, chosen_map_id)

        await interaction.response.send_message(
            f"🗳️ Vote recorded for **{chosen_map_id}**. You can change it any time.",
            ephemeral=True
        )

        # update embed immediately on vote
        await self.cog_ref.update_vote_embed()

class MapVoteView(discord.ui.View):
    def __init__(self, vote_state: VoteState, cog_ref):
        super().__init__(timeout=None)
        self.add_item(MapVoteSelect(vote_state, cog_ref))

# --------------------------------------------------
# MAIN COG
# --------------------------------------------------

class MapVote(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.state = VoteState()
        self.last_map_start_raw = None

        self.tick_task.start()

    def cog_unload(self):
        self.tick_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        guild = discord.Object(id=GUILD_ID)
        try:
            await self.bot.tree.sync(guild=guild)
            print("[MapVote] Commands synced.")
        except Exception as e:
            print("[MapVote] Sync error:", e)

    # ---------- EMBED ----------
    def build_embed(self):
        cm = self.state.current_map or {}
        current_name = cm.get("pretty_name", "Unknown")

        now = datetime.now(timezone.utc)
        remaining_match = None
        remaining_vote = None

        if self.state.match_end_dt:
            remaining_match = max(0, (self.state.match_end_dt - now).total_seconds())
        if self.state.vote_end_dt:
            remaining_vote = max(0, (self.state.vote_end_dt - now).total_seconds())

        def fmt_secs(s):
            if s is None:
                return "Unknown"
            m = int(s) // 60
            sec = int(s) % 60
            return f"{m:02d}:{sec:02d}"

        # vote lines (only maps with votes)
        if self.state.vote_counts:
            sorted_votes = sorted(self.state.vote_counts.items(), key=lambda kv: kv[1], reverse=True)
            lines = []
            for map_id, count in sorted_votes:
                pretty = next((k for k, v in MAPS.items() if v == map_id), map_id)
                lines.append(f"**{pretty}** — {count} vote(s)")
            votes_text = "\n".join(lines)
        else:
            votes_text = "*No votes yet.*"

        embed = discord.Embed(
            title="🗺️ Next Map Vote",
            description=(
                f"**Current map:** {current_name}\n"
                f"**Match remaining:** `{fmt_secs(remaining_match)}`\n"
                f"**Vote closes in:** `{fmt_secs(remaining_vote)}` (2 mins before end)\n\n"
                f"**Live votes:**\n{votes_text}"
            ),
            color=discord.Color.green()
        )

        # set current map image from your CDN if available
        img_url = MAP_CDN_IMAGES.get(current_name)
        if img_url:
            embed.set_image(url=img_url)

        embed.set_footer(text="Vote any time during the match. You can change your vote.")
        return embed

    async def update_vote_embed(self):
        if not self.state.vote_channel or not self.state.vote_message_id:
            return
        try:
            msg = await self.state.vote_channel.fetch_message(self.state.vote_message_id)
            await msg.edit(embed=self.build_embed(), view=MapVoteView(self.state, self))
        except Exception as e:
            print("[MapVote] Failed updating embed:", e)

    # ---------- START NEW MATCH VOTE ----------
    async def start_new_vote_for_match(self, current_map):
        channel = self.bot.get_channel(MAPVOTE_CHANNEL_ID)
        if not channel:
            print("[MapVote] vote channel not found")
            return

        self.state.vote_channel = channel
        self.state.reset_for_new_match(current_map)

        view = MapVoteView(self.state, self)
        embed = self.build_embed()

        # tidy previous bot messages
        try:
            async for m in channel.history(limit=50):
                if m.author == self.bot.user:
                    await m.delete()
        except Exception:
            pass

        msg = await channel.send(embed=embed, view=view)
        self.state.vote_message_id = msg.id

    # ---------- END VOTE & SET MAP ----------
    async def end_vote_and_set_map(self):
        self.state.active = False
        channel = self.state.vote_channel
        if not channel:
            return

        winner_id = self.state.winner_map_id()
        if not winner_id:
            await channel.send("🏁 Vote closed — no votes were cast. Rotation stays unchanged.")
            return

        result = rcon_post("set_map", {"map_name": winner_id})
        pretty = next((k for k, v in MAPS.items() if v == winner_id), winner_id)

        await channel.send(
            f"🏆 **Vote closed!**\n"
            f"Winner: **{pretty}**\n"
            f"Map ID: `{winner_id}`\n\n"
            f"📡 CRCON response:\n```{result}```"
        )

    # ---------- BACKGROUND LOOP ----------
    @tasks.loop(seconds=EMBED_UPDATE_INTERVAL)
    async def tick_task(self):
        current_map = await get_current_map_info()
        if not current_map:
            return

        start_raw = current_map.get("start_raw")

        # Detect new match by changed start timestamp
        if start_raw and start_raw != self.last_map_start_raw:
            self.last_map_start_raw = start_raw
            print(f"[MapVote] New match detected: {current_map.get('pretty_name')}")
            await self.start_new_vote_for_match(current_map)

        if not self.state.active:
            return

        # Update embed every tick
        await self.update_vote_embed()

        # Send 2-minute warning & end vote
        if self.state.vote_end_dt:
            now = datetime.now(timezone.utc)
            remaining_vote = (self.state.vote_end_dt - now).total_seconds()

            # warning once at <= 2 mins
            if remaining_vote <= 120 and not self.state.warning_sent:
                self.state.warning_sent = True

                try:
                    await self.state.vote_channel.send(DISCORD_2MIN_WARNING)
                except Exception:
                    pass

                ingame_result = await send_ingame_broadcast(INGAME_2MIN_WARNING)
                print("[MapVote] In-game warning result:", ingame_result)

            # end vote when time up
            if remaining_vote <= 0:
                await self.end_vote_and_set_map()

    @tick_task.before_loop
    async def before_tick(self):
        await self.bot.wait_until_ready()

    # ---------- OPTIONAL TEST COMMAND ----------
    @app_commands.command(name="force_mapvote", description="Force-start a vote now (testing).")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def force_mapvote(self, interaction: discord.Interaction):
        cm = await get_current_map_info()
        if not cm:
            return await interaction.response.send_message("Could not read current map.", ephemeral=True)

        await self.start_new_vote_for_match(cm)
        await interaction.response.send_message("Forced vote started.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(MapVote(bot))

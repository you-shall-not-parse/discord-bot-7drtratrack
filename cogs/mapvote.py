import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import os
import random
import requests
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

load_dotenv()

# --------------------------------------------------
# CONFIG (EDIT THESE)
# --------------------------------------------------

GUILD_ID = 1097913605082579024
MAPVOTE_CHANNEL_ID = 1441751747935735878

# Vote ends this many seconds before match end
VOTE_END_OFFSET_SECONDS = 120

# How often to refresh the embed / timers
EMBED_UPDATE_INTERVAL = 1  # seconds

# Map voting pool (Pretty name -> CRCON map id)
# Keep these defined in code (as you asked)
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

# How many options to show in each vote.
# Discord dropdown max is 25. If you have more maps, we randomly sample.
OPTIONS_PER_VOTE = 25  # set up to 25

# --- Announcement text (edit these whenever you want) ---
DISCORD_VOTE_START_MSG = "🗺️ **Next-map voting is now OPEN!** Vote in the dropdown below."
DISCORD_2MIN_WARNING = "⏳ **Next-map vote closes in 2 minutes! Get your votes in now.**"
DISCORD_VOTE_END_MSG = "🏁 **Voting closed!** Setting next map now…"

INGAME_VOTE_START_MSG = "Next-map vote is OPEN on Discord! Cast your vote now."
INGAME_2MIN_WARNING = "Next-map vote closes in 2 minutes! Vote now in Discord."
INGAME_VOTE_END_MSG = "Voting closed. Next map will be set shortly."

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

async def get_gamestate():
    """Uses /api/get_gamestate for timing + current map."""
    data = rcon_get("get_gamestate")
    if not data or data.get("failed") or "error" in data:
        return None

    res = data.get("result") or {}
    current_map = res.get("current_map") or {}

    return {
        "current_map_id": current_map.get("id"),
        "current_map_pretty": current_map.get("pretty_name"),
        "current_image_name": current_map.get("image_name"),
        "time_remaining": float(res.get("time_remaining", 0.0)),
        "raw_time_remaining": res.get("raw_time_remaining", "0:00:00"),
        "match_time": int(res.get("match_time", 0)),
        "next_map_id": (res.get("next_map") or {}).get("id"),
        "game_mode": res.get("game_mode", "unknown")
    }

async def broadcast_ingame(message: str):
    """Your host uses /api/broadcast."""
    return rcon_post("broadcast", {"message": message})

# --------------------------------------------------
# VOTE STATE
# --------------------------------------------------

class VoteState:
    def __init__(self):
        self.active = False
        self.vote_message_id = None
        self.vote_channel = None

        self.match_map_id = None
        self.match_map_pretty = None
        self.match_start_detected_at = None  # datetime UTC

        self.vote_end_at = None  # datetime UTC
        self.warning_sent = False

        self.options_pretty_to_id = {}
        self.user_votes = {}   # user_id -> map_id
        self.vote_counts = {}  # map_id -> count

    def reset_for_new_match(self, gs):
        self.active = True
        self.vote_message_id = None

        self.match_map_id = gs["current_map_id"]
        self.match_map_pretty = gs["current_map_pretty"]
        self.match_start_detected_at = datetime.now(timezone.utc)

        self.warning_sent = False
        self.user_votes.clear()
        self.vote_counts.clear()

        # vote end time based on time_remaining right now
        now = datetime.now(timezone.utc)
        self.vote_end_at = now + timedelta(seconds=max(0, gs["time_remaining"] - VOTE_END_OFFSET_SECONDS))

    def set_options(self, options_pretty_to_id):
        self.options_pretty_to_id = options_pretty_to_id

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
            for pretty, map_id in vote_state.options_pretty_to_id.items()
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
            f"🗳️ Vote recorded for `{chosen_map_id}`. You can change it any time.",
            ephemeral=True
        )

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

        # for match-change detection
        self.last_map_id = None
        self.last_time_remaining = None

        self.tick_task.start()

    def cog_unload(self):
        self.tick_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        # guild-sync for instant command appearance
        guild = discord.Object(id=GUILD_ID)
        try:
            await self.bot.tree.sync(guild=guild)
            print("[MapVote] Commands synced.")
        except Exception as e:
            print("[MapVote] Sync error:", e)

    # ---------- EMBED ----------
    def build_embed(self, gs):
        current_pretty = gs["current_map_pretty"] or "Unknown"
        raw_remaining = gs["raw_time_remaining"] or "0:00:00"

        now = datetime.now(timezone.utc)
        vote_remaining = None
        if self.state.vote_end_at:
            vote_remaining = max(0, (self.state.vote_end_at - now).total_seconds())

        def fmt_secs(s):
            if s is None:
                return "Unknown"
            m = int(s) // 60
            sec = int(s) % 60
            return f"{m:02d}:{sec:02d}"

        # live vote summary (only maps with votes)
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
                f"**Current map:** {current_pretty}\n"
                f"**Match remaining:** `{raw_remaining}`\n"
                f"**Vote closes in:** `{fmt_secs(vote_remaining)}` (2 mins before end)\n\n"
                f"{DISCORD_VOTE_START_MSG}\n\n"
                f"**Live votes:**\n{votes_text}"
            ),
            color=discord.Color.green()
        )

        # current map image from your CDN
        img_url = MAP_CDN_IMAGES.get(current_pretty)
        if img_url:
            embed.set_image(url=img_url)

        embed.set_footer(text="Vote any time during the match. You can change your vote.")
        return embed

    async def update_vote_embed(self):
        if not self.state.vote_channel or not self.state.vote_message_id:
            return

        gs = await get_gamestate()
        if not gs:
            return

        try:
            msg = await self.state.vote_channel.fetch_message(self.state.vote_message_id)
            await msg.edit(embed=self.build_embed(gs), view=MapVoteView(self.state, self))
        except Exception as e:
            print("[MapVote] Failed updating embed:", e)

    # ---------- START NEW MATCH VOTE ----------
    async def start_new_vote_for_match(self, gs):
        channel = self.bot.get_channel(MAPVOTE_CHANNEL_ID)
        if not channel:
            print("[MapVote] vote channel not found")
            return

        self.state.vote_channel = channel
        self.state.reset_for_new_match(gs)

        # pick options (exclude current map)
        pool = [(pretty, mid) for pretty, mid in MAPS.items() if mid != gs["current_map_id"]]
        random.shuffle(pool)
        pool = pool[:min(OPTIONS_PER_VOTE, 25, len(pool))]

        options_dict = {pretty: mid for pretty, mid in pool}
        self.state.set_options(options_dict)

        view = MapVoteView(self.state, self)
        embed = self.build_embed(gs)

        # tidy old bot messages
        try:
            async for m in channel.history(limit=50):
                if m.author == self.bot.user:
                    await m.delete()
        except Exception:
            pass

        msg = await channel.send(embed=embed, view=view)
        self.state.vote_message_id = msg.id

        # announce start
        await channel.send(DISCORD_VOTE_START_MSG)
        ingame = await broadcast_ingame(INGAME_VOTE_START_MSG)
        print("[MapVote] In-game vote-start broadcast:", ingame)

    # ---------- END VOTE & SET MAP ----------
    async def end_vote_and_set_map(self):
        self.state.active = False
        channel = self.state.vote_channel
        if not channel:
            return

        await channel.send(DISCORD_VOTE_END_MSG)
        await broadcast_ingame(INGAME_VOTE_END_MSG)

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

        await broadcast_ingame(f"Next map set to: {pretty}")

    # ---------- BACKGROUND LOOP ----------
    @tasks.loop(seconds=EMBED_UPDATE_INTERVAL)
    async def tick_task(self):
        gs = await get_gamestate()
        if not gs:
            return

        current_map_id = gs["current_map_id"]
        time_remaining = gs["time_remaining"]

        # Detect new match:
        # 1) map id changed OR
        # 2) time_remaining jumped up a lot (server reset)
        new_match = False
        if self.last_map_id and current_map_id != self.last_map_id:
            new_match = True
        elif self.last_time_remaining is not None and time_remaining > self.last_time_remaining + 300:
            new_match = True

        self.last_map_id = current_map_id
        self.last_time_remaining = time_remaining

        if new_match:
            print(f"[MapVote] New match detected: {gs['current_map_pretty']}")
            await self.start_new_vote_for_match(gs)
            return

        if not self.state.active:
            return

        # Update embed live
        await self.update_vote_embed()

        # Handle warnings / end based on vote_end_at
        if self.state.vote_end_at:
            now = datetime.now(timezone.utc)
            remaining_vote = (self.state.vote_end_at - now).total_seconds()

            # 2-minute warning once
            if remaining_vote <= 120 and not self.state.warning_sent:
                self.state.warning_sent = True
                try:
                    await self.state.vote_channel.send(DISCORD_2MIN_WARNING)
                except Exception:
                    pass
                ingame = await broadcast_ingame(INGAME_2MIN_WARNING)
                print("[MapVote] In-game 2min warning:", ingame)

            # End vote
            if remaining_vote <= 0:
                await self.end_vote_and_set_map()

    @tick_task.before_loop
    async def before_tick(self):
        await self.bot.wait_until_ready()

    # ---------- OPTIONAL TEST COMMAND ----------
    @app_commands.command(name="force_mapvote", description="Force-start a vote now (testing).")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def force_mapvote(self, interaction: discord.Interaction):
        gs = await get_gamestate()
        if not gs:
            return await interaction.response.send_message("Could not read gamestate.", ephemeral=True)

        await self.start_new_vote_for_match(gs)
        await interaction.response.send_message("Forced vote started.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(MapVote(bot))

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
# CONFIG YOU EDIT
# --------------------------------------------------

GUILD_ID = 1097913605082579024
MAPVOTE_CHANNEL_ID = 1441751747935735878

# Vote ends this many seconds before match end
# (Used only when total match length is >= 90 minutes)
VOTE_END_OFFSET_SECONDS = 120

# Embed update speed (testing = 1 second)
EMBED_UPDATE_INTERVAL = 1

# How many map options to show
OPTIONS_PER_VOTE = 10

# Pretty name → CRCON ID
MAPS = {
    "Elsenborn Ridge Warfare": "elsenbornridge_warfare_day",
    "Carentan Warfare": "carentan_warfare_day",
    "Foy Warfare": "foy_warfare_day",
    "Hill 400 Warfare": "hill400_warfare_day",
}

# CDN images by pretty_name (must match EXACT pretty_name)
MAP_CDN_IMAGES = {
    "Elsenborn Ridge Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365408158012407840/Elsenborn_Custom_MLL.png",
    "Carentan Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365403110197166191/Carentan_SP_NoHQ.png",
    "Foy Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365404141337186304/Foy_SP_NoHQ.png",
    "Hill 400 Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365404269116919930/Hill400_SP_NoHQ_1.png",
}

# Broadcast templates (these now use per-player messages)
BROADCAST_START = "🗳️ Next-map voting is OPEN on Discord!"
BROADCAST_ENDING_SOON = "⏳ Vote closes in 2 minutes!"
BROADCAST_NO_VOTES = "No votes, the map rotation wins :("

# --------------------------------------------------
# CRCON API (Bearer token)
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
        print(f"[MapVote] rcon_get error on {endpoint}: {e}")
        return {"error": str(e)}


def rcon_post(endpoint: str, payload: dict):
    try:
        r = requests.post(
            CRCON_PANEL_URL + endpoint,
            json=payload,
            headers={"Authorization": f"Bearer {CRCON_API_KEY}"},
            timeout=10
        )
        # We don't assume JSON here; many endpoints return empty bodies
        try:
            return r.json()
        except Exception:
            return {
                "status": r.status_code,
                "text": r.text or "",
            }
    except Exception as e:
        print(f"[MapVote] rcon_post error on {endpoint}: {e}")
        return {"error": str(e)}


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

async def get_gamestate():
    data = rcon_get("get_gamestate")

    if (not data) or data.get("failed") or data.get("error"):
        print("[MapVote] Gamestate read failed:", data)
        return None

    res = data.get("result") or {}
    cur = res.get("current_map") or {}

    try:
        return {
            "current_map_id": cur.get("id"),
            "current_map_pretty": cur.get("pretty_name"),
            "current_image_name": cur.get("image_name"),
            "time_remaining": float(res.get("time_remaining") or 0),
            "raw_time_remaining": res.get("raw_time_remaining") or "0:00:00",
            "match_time": int(res.get("match_time") or 0),
            "axis_players": int(res.get("num_axis_players") or 0),
            "allied_players": int(res.get("num_allied_players") or 0),
        }
    except Exception as e:
        print("[MapVote] Error parsing gamestate:", e, data)
        return None


async def rot_add_map(map_name: str, after_map_name: str, ordinal=1):
    payload = {
        "map_name": map_name,
        "after_map_name": after_map_name,
        "after_map_ordinal": ordinal
    }
    res = rcon_post("rot_add", payload)
    return res


def fmt_vote_secs(sec):
    if sec is None:
        return "Unknown"
    sec = max(0, int(sec))
    m = sec // 60
    s = sec % 60
    return f"{m:02d}:{s:02d}"


# --------------------------------------------------
# VOTE STATE
# --------------------------------------------------

class VoteState:
    def __init__(self):
        self.active = False
        self.vote_channel: discord.TextChannel | None = None
        self.vote_message_id: int | None = None

        self.match_map_id = None
        self.match_map_pretty = None
        self.vote_start_at: datetime | None = None
        self.vote_end_at: datetime | None = None
        self.warning_sent = False

        self.options: dict[str, str] = {}   # pretty → map_id
        self.user_votes: dict[int, str] = {}     # user_id → map_id
        self.vote_counts: dict[str, int] = {}    # map_id → int

        self.total_match_length: int | None = None  # seconds, dynamic

    def reset_for_match(self, gs):
        self.active = True
        self.vote_message_id = None
        self.vote_channel = None

        self.match_map_id = gs["current_map_id"]
        self.match_map_pretty = gs["current_map_pretty"]

        now = datetime.now(timezone.utc)
        tr = float(gs["time_remaining"] or 0)
        mt = int(gs["match_time"] or 0)

        # Dynamically detect match length
        total_len = 0
        if tr > 0 or mt > 0:
            total_len = int(tr) + int(mt)
        self.total_match_length = total_len if total_len > 0 else None

        # Decide when the vote should end
        if tr > 0:
            # If match length < 90 mins → always close 2 mins before end
            if self.total_match_length is not None and self.total_match_length < 5400:
                end_in = max(0, tr - 120)
            else:
                end_in = max(0, tr - VOTE_END_OFFSET_SECONDS)
        else:
            # Fallback: no time_remaining, just use match_time if available
            if mt > 0:
                end_in = max(0, mt - VOTE_END_OFFSET_SECONDS)
            else:
                # Worst case fallback
                end_in = max(0, VOTE_END_OFFSET_SECONDS)

        self.vote_start_at = now
        self.vote_end_at = now + timedelta(seconds=end_in)
        self.warning_sent = False

        self.user_votes.clear()
        self.vote_counts.clear()

    def set_options(self, mapping: dict[str, str]):
        self.options = mapping

    def record_vote(self, user_id: int, map_id: str):
        old = self.user_votes.get(user_id)
        if old == map_id:
            return

        # remove old vote
        if old:
            self.vote_counts[old] = max(0, self.vote_counts.get(old, 1) - 1)
            if self.vote_counts[old] == 0:
                self.vote_counts.pop(old, None)

        # add new vote
        self.user_votes[user_id] = map_id
        self.vote_counts[map_id] = self.vote_counts.get(map_id, 0) + 1

    def winner(self):
        if not self.vote_counts:
            return None
        return max(self.vote_counts.items(), key=lambda kv: kv[1])[0]


# --------------------------------------------------
# UI
# --------------------------------------------------

class MapVoteSelect(discord.ui.Select):
    def __init__(self, vote_state: VoteState, cog_ref: "MapVote"):
        self.state = vote_state
        self.cog = cog_ref

        options = [
            discord.SelectOption(label=pretty, value=map_id)
            for pretty, map_id in vote_state.options.items()
        ]

        super().__init__(
            placeholder="Vote for next map…",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        if not self.state.active:
            return await interaction.response.send_message(
                "Voting not active.",
                ephemeral=True
            )

        map_id = self.values[0]
        self.state.record_vote(interaction.user.id, map_id)

        await interaction.response.send_message(
            f"Vote recorded for `{map_id}`",
            ephemeral=True
        )

        # Update the embed after each vote
        await self.cog.update_vote_embed()


class MapVoteView(discord.ui.View):
    def __init__(self, state: VoteState, cog: "MapVote"):
        super().__init__(timeout=None)
        self.add_item(MapVoteSelect(state, cog))


# --------------------------------------------------
# COG
# --------------------------------------------------

class MapVote(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = VoteState()
        self.last_map_id = None
        self.vote_view: MapVoteView | None = None

    def cog_unload(self):
        if self.tick_task.is_running():
            self.tick_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        # Sync commands once per startup
        try:
            await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))
            print("[MapVote] Commands synced.")
        except Exception as e:
            print("[MapVote] Sync error:", e)

        # Start background task once
        if not self.tick_task.is_running():
            self.tick_task.start()
            print("[MapVote] tick_task started")

    # --------------------------------------------------
    # PER-PLAYER "BROADCAST" USING /message + /get_players
    # (A + C: one message per player, quiet on per-player failures)
    # --------------------------------------------------
    async def broadcast_to_all(self, message: str):
        if not message:
            return

        data = rcon_get("get_players")
        if not data or data.get("error") or data.get("failed"):
            # One-time log, no per-player spam
            print("[MapVote] broadcast_to_all: failed to get players:", data)
            return

        players = data.get("result") or []
        if not players:
            return

        # One message per player; per-player errors are ignored silently
        for p in players:
            uid = p.get("steam_id_64") or p.get("steam_id")
            if not uid:
                continue

            payload = {"player": uid, "message": message}
            _ = rcon_post("message", payload)
            # No logging per player; keep it quiet as requested
            await asyncio.sleep(0.1)  # gentle pacing to avoid hammering API

    # --------------------------------------------------
    # FORCE START SLASH COMMAND
    # --------------------------------------------------
    @app_commands.command(
        name="force_mapvote",
        description="Force start a map vote"
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def force_mapvote_cmd(self, interaction: discord.Interaction):

        await interaction.response.send_message("Fetching gamestate…", ephemeral=True)
        gs = await get_gamestate()
        if not gs:
            return await interaction.followup.send("❌ Could not read gamestate.", ephemeral=True)

        await self.start_vote(gs)
        await interaction.followup.send("Vote started!", ephemeral=True)

    # --------------------------------------------------
    # EMBED BUILDER
    # --------------------------------------------------
    def build_embed(self, gs):
        current = gs["current_map_pretty"] or "Unknown"
        raw_time = gs["raw_time_remaining"]

        axis = gs["axis_players"]
        allied = gs["allied_players"]

        now = datetime.now(timezone.utc)
        vote_left = None
        if self.state.vote_end_at:
            vote_left = (self.state.vote_end_at - now).total_seconds()

        # Live vote lines
        if self.state.vote_counts:
            sorted_votes = sorted(self.state.vote_counts.items(), key=lambda x: x[1], reverse=True)
            lines = []
            for map_id, count in sorted_votes:
                pretty = next((p for p, mid in MAPS.items() if mid == map_id), map_id)
                lines.append(f"**{pretty}** — {count} vote{'s' if count != 1 else ''}")
            votetext = "\n".join(lines)
        else:
            votetext = "*No votes yet.*"

        # Optional debug string for match length
        if self.state.total_match_length:
            total_m = self.state.total_match_length // 60
            total_s = self.state.total_match_length % 60
            total_str = f"{total_m:02d}:{total_s:02d}"
            length_line = f"**Match length (detected):** `{total_str}`\n"
        else:
            length_line = ""

        embed = discord.Embed(
            title="🗺️ Next Map Vote",
            description=(
                f"**Current map:** {current}\n"
                f"**Match remaining:** `{raw_time}`\n"
                f"{length_line}"
                f"**Players:** Allied: `{allied}` — Axis: `{axis}`\n"
                f"**Vote closes in:** `{fmt_vote_secs(vote_left)}`\n\n"
                f"**Live votes:**\n{votetext}"
            ),
            color=discord.Color.red()
        )

        # Image from CDN
        img = MAP_CDN_IMAGES.get(current)
        if img:
            embed.set_image(url=img)

        return embed

    # --------------------------------------------------
    # UPDATE EMBED
    # --------------------------------------------------
    async def update_vote_embed(self):
        if not (self.state.vote_channel and self.state.vote_message_id):
            # This will happen if vote hasn't started properly yet
            return

        gs = await get_gamestate()
        if not gs:
            return

        try:
            msg = await self.state.vote_channel.fetch_message(self.state.vote_message_id)

            # Reuse the same view instead of recreating it every second
            if self.vote_view is None:
                self.vote_view = MapVoteView(self.state, self)

            await msg.edit(embed=self.build_embed(gs), view=self.vote_view)
        except Exception as e:
            print("[MapVote] Failed to update embed:", e)

    # --------------------------------------------------
    # START NEW VOTE
    # --------------------------------------------------
    async def start_vote(self, gs):
        channel = self.bot.get_channel(MAPVOTE_CHANNEL_ID)
        if not channel or not isinstance(channel, discord.TextChannel):
            print("[MapVote] Vote channel invalid")
            return

        # Reset state & set channel
        self.state.reset_for_match(gs)
        self.state.vote_channel = channel

        # Build option list (exclude current map)
        pool = [(p, mid) for p, mid in MAPS.items() if mid != gs["current_map_id"]]
        random.shuffle(pool)
        pool = pool[:min(len(pool), OPTIONS_PER_VOTE, 25)]
        self.state.set_options({p: mid for p, mid in pool})

        # Clean old messages from bot
        try:
            async for m in channel.history(limit=50):
                if m.author == self.bot.user:
                    await m.delete()
        except Exception as e:
            print("[MapVote] Failed to clean old messages:", e)

        # Send embed with persistent view
        embed = self.build_embed(gs)
        self.vote_view = MapVoteView(self.state, self)
        msg = await channel.send(embed=embed, view=self.vote_view)
        self.state.vote_message_id = msg.id

        print(f"[MapVote] Vote started for {gs['current_map_pretty']}")
        await self.broadcast_to_all(BROADCAST_START)

    # --------------------------------------------------
    # END VOTE
    # --------------------------------------------------
    async def end_vote_and_queue(self):
        self.state.active = False
        channel = self.state.vote_channel
        if not channel:
            print("[MapVote] end_vote_and_queue called with no channel")
            return

        winner_id = self.state.winner()

        if not winner_id:
            await channel.send("No votes — map rotation continues.")
            await self.broadcast_to_all(BROADCAST_NO_VOTES)
            return

        pretty = next((p for p, mid in MAPS.items() if mid == winner_id), winner_id)

        # Queue winner after current map
        result = await rot_add_map(winner_id, self.state.match_map_id, 1)

        await self.broadcast_to_all(f"{pretty} has won the vote!")
        await channel.send(
            f"🏆 **Winner: {pretty}**\n"
            f"Queued next via RotAdd.\n"
            f"CRCON Response:\n```{result}```"
        )
        print(f"[MapVote] Vote ended, winner {pretty}")

    # --------------------------------------------------
    # BACKGROUND LOOP — updates every second
    # --------------------------------------------------
    @tasks.loop(seconds=EMBED_UPDATE_INTERVAL)
    async def tick_task(self):
        gs = await get_gamestate()
        if not gs:
            return

        # Detect map change = new match
        if self.last_map_id is not None and gs["current_map_id"] != self.last_map_id:
            print(f"[MapVote] New match detected: {gs['current_map_pretty']}")
            await self.start_vote(gs)
            self.last_map_id = gs["current_map_id"]
            return

        # Initialise last_map_id on first successful gs
        if self.last_map_id is None:
            self.last_map_id = gs["current_map_id"]

        # If no active vote, nothing to do
        if not self.state.active:
            return

        # Update the live embed
        await self.update_vote_embed()

        # Handle vote timing
        now = datetime.now(timezone.utc)
        remaining = (self.state.vote_end_at - now).total_seconds() if self.state.vote_end_at else None

        if remaining is None:
            return

        # Warning at 2 minutes left
        if remaining <= 120 and not self.state.warning_sent:
            self.state.warning_sent = True
            await self.broadcast_to_all(BROADCAST_ENDING_SOON)
            if self.state.vote_channel:
                await self.state.vote_channel.send("⏳ Vote closes in 2 minutes!")

        # End vote
        if remaining <= 0:
            await self.end_vote_and_queue()

    @tick_task.before_loop
    async def before_tick(self):
        print("[MapVote] Waiting until bot is ready before starting tick_task...")
        await self.bot.wait_until_ready()
        print("[MapVote] Bot ready, tick_task will now run.")


async def setup(bot: commands.Bot):
    await bot.add_cog(MapVote(bot))

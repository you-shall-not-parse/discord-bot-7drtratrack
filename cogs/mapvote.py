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
VOTE_END_OFFSET_SECONDS = 120

# Embed update speed (testing = 1 second)
EMBED_UPDATE_INTERVAL = 1

# How many map options to show
OPTIONS_PER_VOTE = 10

# Pretty name → CRCON ID (must match CRCON "id" fields)
MAPS = {
    "Elsenborn Ridge Warfare": "elsenbornridge_warfare_day",
    "Carentan Warfare": "carentan_warfare",
    "Foy Warfare": "foy_warfare",
    "Hill 400 Warfare": "hill400_warfare",
}

# CDN images by pretty_name (must match EXACT pretty_name from CRCON)
MAP_CDN_IMAGES = {
    "Elsenborn Ridge Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365408158012407840/Elsenborn_Custom_MLL.png",
    "Carentan Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365403110197166191/Carentan_SP_NoHQ.png",
    "Foy Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365404141337186304/Foy_SP_NoHQ.png",
    "Hill 400 Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365404269116919930/Hill400_SP_NoHQ_1.png",
}

# Broadcast templates (per-player)
BROADCAST_START = "🗳️ Next-map voting is OPEN on Discord!"
BROADCAST_ENDING_SOON = "⏳ Vote closes in 2 minutes!"
BROADCAST_NO_VOTES = "No votes, the map rotation wins :("

# --------------------------------------------------
# CRCON API (Bearer token)
# --------------------------------------------------

CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"
CRCON_API_KEY = os.getenv("CRCON_API_KEY")


def rcon_get(endpoint: str):
    """Simple GET wrapper around CRCON API."""
    try:
        r = requests.get(
            CRCON_PANEL_URL + endpoint,
            headers={"Authorization": f"Bearer {CRCON_API_KEY}"},
            timeout=10
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[MapVote] rcon_get error on {endpoint}: {e}")
        return {"error": str(e)}


def rcon_post(endpoint: str, payload: dict | None = None):
    """Simple POST wrapper around CRCON API."""
    if payload is None:
        payload = {}
    try:
        r = requests.post(
            CRCON_PANEL_URL + endpoint,
            json=payload,
            headers={"Authorization": f"Bearer {CRCON_API_KEY}"},
            timeout=10
        )
        # body may be empty or non-JSON
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


def secs_to_hms_str(sec: float | int | None) -> str:
    """Convert seconds to H:MM:SS string for display."""
    if sec is None:
        return "Unknown"
    sec = max(0, int(sec))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h}:{m:02d}:{s:02d}"


def fmt_vote_secs(sec: float | int | None) -> str:
    """Convert seconds to MM:SS for 'vote closes in'."""
    if sec is None:
        return "Unknown"
    sec = max(0, int(sec))
    m = sec // 60
    s = sec % 60
    return f"{m:02d}:{s:02d}"


async def get_game_status():
    """
    Build a lightweight 'gamestate' using smaller endpoints:
      - get_map
      - get_round_time_remaining
      - get_slots
    Returns dict:
      {
        current_map_id,
        current_map_pretty,
        current_image_name,
        time_remaining,           # seconds (float)
        raw_time_remaining,       # "H:MM:SS"
        num_players,
        max_players,
      }
    """
    # ---- get_map ----
    map_data = rcon_get("get_map")
    if not map_data or map_data.get("failed") or map_data.get("error"):
        print("[MapVote] get_map failed:", map_data)
        return None

    cur = map_data.get("result") or map_data.get("map") or {}
    current_map_id = cur.get("id")
    current_map_pretty = cur.get("pretty_name") or cur.get("name") or "Unknown"
    current_image_name = cur.get("image_name")

    # ---- get_round_time_remaining ----
    time_data = rcon_get("get_round_time_remaining")
    time_remaining = 0.0
    if time_data and not time_data.get("failed") and not time_data.get("error"):
        # In many CRCON setups, result is directly the float seconds
        raw_tr = time_data.get("result", time_data.get("time_remaining"))
        try:
            time_remaining = float(raw_tr)
        except (TypeError, ValueError):
            time_remaining = 0.0
    raw_time_remaining = secs_to_hms_str(time_remaining)

    # ---- get_slots ----
    slots_data = rcon_get("get_slots")
    num_players = 0
    max_players = 0
    if slots_data and not slots_data.get("failed") and not slots_data.get("error"):
        slots_res = slots_data.get("result") or slots_data
        # be flexible about key names
        num_players = (
            slots_res.get("num_players")
            or slots_res.get("player_count")
            or slots_res.get("players")
            or 0
        )
        max_players = (
            slots_res.get("max_players")
            or slots_res.get("max_slots")
            or slots_res.get("max")
            or 0
        )
        try:
            num_players = int(num_players)
        except Exception:
            num_players = 0
        try:
            max_players = int(max_players)
        except Exception:
            max_players = 0

    return {
        "current_map_id": current_map_id,
        "current_map_pretty": current_map_pretty,
        "current_image_name": current_image_name,
        "time_remaining": time_remaining,
        "raw_time_remaining": raw_time_remaining,
        "num_players": num_players,
        "max_players": max_players,
    }


async def set_rotation_with_winner_first(winner_id: str):
    """
    Option C:
      - Get current map rotation
      - Build new rotation: [winner] + [all others, without winner]
      - POST set_map_rotation
    """
    rotation_data = rcon_get("get_map_rotation")
    new_rotation: list[str] = [winner_id]

    if rotation_data and not rotation_data.get("failed") and not rotation_data.get("error"):
        layers = rotation_data.get("result") or []
        for layer in layers:
            mid = layer.get("id") or layer.get("name")
            if mid and mid != winner_id:
                new_rotation.append(mid)

    payload = {"map_names": new_rotation}
    res = rcon_post("set_map_rotation", payload)
    return res


# --------------------------------------------------
# VOTE STATE
# --------------------------------------------------

class VoteState:
    def __init__(self):
        self.active = False
        self.vote_channel: discord.TextChannel | None = None
        self.vote_message_id: int | None = None

        self.match_map_id: str | None = None
        self.match_map_pretty: str | None = None
        self.vote_start_at: datetime | None = None
        self.vote_end_at: datetime | None = None
        self.warning_sent = False

        self.options: dict[str, str] = {}     # pretty → map_id
        self.user_votes: dict[int, str] = {}  # user_id → map_id
        self.vote_counts: dict[str, int] = {} # map_id → count

    def reset_for_match(self, status: dict):
        self.active = True
        self.vote_message_id = None
        self.vote_channel = None

        self.match_map_id = status["current_map_id"]
        self.match_map_pretty = status["current_map_pretty"]

        now = datetime.now(timezone.utc)
        tr = float(status.get("time_remaining") or 0)

        # Vote ends (time_remaining - offset)
        end_in = max(0, tr - VOTE_END_OFFSET_SECONDS)

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
        # map_id with highest count
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
        self.last_map_id: str | None = None
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
    # PER-PLAYER BROADCAST USING message_player + get_players
    # --------------------------------------------------
    async def broadcast_to_all(self, message: str):
        if not message:
            return

        data = rcon_get("get_players")
        if not data or data.get("error") or data.get("failed"):
            print("[MapVote] broadcast_to_all: failed to get players:", data)
            return

        players = data.get("result") or []
        if not players:
            return

        for p in players:
            uid = p.get("steam_id_64") or p.get("steam_id")
            if not uid:
                continue

            payload = {
                "player_id": uid,
                "message": message,
            }
            _ = rcon_post("message_player", payload)
            await asyncio.sleep(0.1)  # gentle pacing

    # --------------------------------------------------
    # FORCE START SLASH COMMAND
    # --------------------------------------------------
    @app_commands.command(
        name="force_mapvote",
        description="Force start a map vote"
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def force_mapvote_cmd(self, interaction: discord.Interaction):
        await interaction.response.send_message("Fetching server status…", ephemeral=True)
        status = await get_game_status()
        if not status:
            return await interaction.followup.send("❌ Could not read status from CRCON.", ephemeral=True)

        await self.start_vote(status)
        await interaction.followup.send("Vote started!", ephemeral=True)

    # --------------------------------------------------
    # EMBED BUILDER
    # --------------------------------------------------
    def build_embed(self, status: dict):
        current = status["current_map_pretty"] or "Unknown"
        raw_time = status["raw_time_remaining"]

        num_players = status.get("num_players", 0)
        max_players = status.get("max_players", 0)

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

        embed = discord.Embed(
            title="🗺️ Next Map Vote",
            description=(
                f"**Current map:** {current}\n"
                f"**Match remaining:** `{raw_time}`\n"
                f"**Players:** `{num_players}` / `{max_players}`\n"
                f"**Vote closes in:** `{fmt_vote_secs(vote_left)}`\n\n"
                f"**Live votes:**\n{votetext}"
            ),
            color=discord.Color.red()
        )

        # Image from CDN by pretty name
        img = MAP_CDN_IMAGES.get(current)
        if img:
            embed.set_image(url=img)

        return embed

    # --------------------------------------------------
    # UPDATE EMBED
    # --------------------------------------------------
    async def update_vote_embed(self):
        if not (self.state.vote_channel and self.state.vote_message_id):
            return

        status = await get_game_status()
        if not status:
            return

        try:
            msg = await self.state.vote_channel.fetch_message(self.state.vote_message_id)

            if self.vote_view is None:
                self.vote_view = MapVoteView(self.state, self)

            await msg.edit(embed=self.build_embed(status), view=self.vote_view)
        except Exception as e:
            print("[MapVote] Failed to update embed:", e)

    # --------------------------------------------------
    # START NEW VOTE
    # --------------------------------------------------
    async def start_vote(self, status: dict):
        channel = self.bot.get_channel(MAPVOTE_CHANNEL_ID)
        if not channel or not isinstance(channel, discord.TextChannel):
            print("[MapVote] Vote channel invalid")
            return

        # Reset state & set channel
        self.state.reset_for_match(status)
        self.state.vote_channel = channel

        # Build option list (exclude current map)
        pool = [(p, mid) for p, mid in MAPS.items() if mid != status["current_map_id"]]
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
        embed = self.build_embed(status)
        self.vote_view = MapVoteView(self.state, self)
        msg = await channel.send(embed=embed, view=self.vote_view)
        self.state.vote_message_id = msg.id

        print(f"[MapVote] Vote started on map {status['current_map_pretty']}")
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

        # Option C: set rotation with winner first
        result = await set_rotation_with_winner_first(winner_id)

        await self.broadcast_to_all(f"{pretty} has won the vote!")
        await channel.send(
            f"🏆 **Winner: {pretty}**\n"
            f"Rotation updated with this map first.\n"
            f"CRCON Response:\n```{result}```"
        )
        print(f"[MapVote] Vote ended, winner {pretty}")

    # --------------------------------------------------
    # BACKGROUND LOOP — updates every second
    # --------------------------------------------------
    @tasks.loop(seconds=EMBED_UPDATE_INTERVAL)
    async def tick_task(self):
        status = await get_game_status()
        if not status:
            return

        current_map_id = status["current_map_id"]

        # Detect map change = new match
        if self.last_map_id is not None and current_map_id != self.last_map_id:
            print(f"[MapVote] New match detected: {status['current_map_pretty']}")
            await self.start_vote(status)
            self.last_map_id = current_map_id
            return

        # Initialise last_map_id on first successful status
        if self.last_map_id is None:
            self.last_map_id = current_map_id

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

import discord
from discord.ext import commands, tasks
from discord import app_commands
from discord.app_commands import checks
import asyncio
import os
import json
import random
import requests
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

load_dotenv()

# --------------------------------------------------
# CONFIG
# --------------------------------------------------

GUILD_ID = 1097913605082579024
MAPVOTE_CHANNEL_ID = 1441751747935735878

# Role allowed to use /force_mapvote and /mapvote_stop
MAPVOTE_ADMIN_ROLE_ID = 1213495462632361994  # TODO: set this to your real role ID

# Vote ends this many seconds before match end
VOTE_END_OFFSET_SECONDS = 120

# Embed update speed
EMBED_UPDATE_INTERVAL = 1

# How many map options to show
OPTIONS_PER_VOTE = 10

# Pretty name → CRCON ID
MAPS = {
    "Elsenborn Ridge Warfare": "elsenbornridge_warfare_day",
    "Carentan Warfare": "carentan_warfare",
    "Foy Warfare": "foy_warfare",
    "Hill 400 Warfare": "hill400_warfare",
}

# Default rotation when map voting is OFF
DEFAULT_ROTATION = [
    "elsenbornridge_warfare_day",
    "carentan_warfare",
    "foy_warfare",
    "hill400_warfare",
]

# CDN images by pretty_name (must match EXACT pretty_name)
MAP_CDN_IMAGES = {
    "Elsenborn Ridge Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365408158012407840/Elsenborn_Custom_MLL.png",
    "Carentan Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365403110197166191/Carentan_SP_NoHQ.png",
    "Foy Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365404141337186304/Foy_SP_NoHQ.png",
    "Hill 400 Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365404269116919930/Hill400_SP_NoHQ_1.png",
}

# NEW: CDN images for status modes (fill these yourself)
STANDBY_STATUS_IMAGE = "https://cdn.discordapp.com/attachments/1365401621110067281/1365404269116919930/Hill400_SP_NoHQ_1.png"  # e.g. "https://cdn.discordapp.com/.../mapvote_standby.png"
OFFLINE_STATUS_IMAGE = "https://cdn.discordapp.com/attachments/1365401621110067281/1365404269116919930/Hill400_SP_NoHQ_1.png"  # e.g. "https://cdn.discordapp.com/.../mapvote_offline.png"

# Broadcast templates
BROADCAST_START = "Next-map voting is OPEN on Discord!"
BROADCAST_ENDING_SOON = "Vote closes in 2 minutes!"
BROADCAST_NO_VOTES = "No votes, the map rotation wins :("

# CRCON API
CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"
CRCON_API_KEY = os.getenv("CRCON_API_KEY")

# Persisted state file
STATE_FILE = "mapvote_state.json"


# --------------------------------------------------
# CRCON HELPERS
# --------------------------------------------------

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
        try:
            return r.json()
        except Exception:
            return {"status": r.status_code, "text": r.text or ""}
    except Exception as e:
        print(f"[MapVote] rcon_post error on {endpoint}: {e}")
        return {"error": str(e)}


async def get_gamestate():
    """
    Wrap CRCON get_gamestate in a Discord-friendly dict.
    """
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
            "server_name": res.get("server_name") or "Unknown",
        }
    except Exception as e:
        print("[MapVote] Error parsing gamestate:", e, data)
        return None


async def set_full_rotation(map_ids: list[str]):
    """
    Overwrite entire map rotation.
    """
    payload = {"map_names": map_ids}
    return rcon_post("set_map_rotation", payload)


# --------------------------------------------------
# GENERIC HELPERS
# --------------------------------------------------

def fmt_vote_secs(sec):
    if sec is None:
        return "Unknown"
    sec = max(0, int(sec))
    m = sec // 60
    s = sec % 60
    return f"{m:02d}:{s:02d}"


def load_persisted_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print("[MapVote] Failed to load state file:", e)
        return {}


def save_persisted_state(vote_message_id: int | None, offline: bool):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(
                {
                    "vote_message_id": vote_message_id,
                    "offline": offline,
                },
                f,
                indent=4,
            )
    except Exception as e:
        print("[MapVote] Failed to save state file:", e)


# --------------------------------------------------
# VOTE STATE
# --------------------------------------------------

class VoteState:
    def __init__(self):
        self.active = False

        self.vote_channel: discord.TextChannel | None = None
        self.vote_message_id: int | None = None  # Persistent main embed

        self.match_map_id = None
        self.match_map_pretty = None
        self.vote_start_at: datetime | None = None
        self.vote_end_at: datetime | None = None
        self.warning_sent = False

        self.options: dict[str, str] = {}     # pretty → map_id
        self.user_votes: dict[int, str] = {}  # user_id → map_id
        self.vote_counts: dict[str, int] = {} # map_id → int

        self.total_match_length: int | None = None  # seconds

    def reset_for_match(self, gs: dict):
        self.active = True

        self.match_map_id = gs["current_map_id"]
        self.match_map_pretty = gs["current_map_pretty"]

        now = datetime.now(timezone.utc)
        tr = float(gs["time_remaining"] or 0)
        mt = int(gs["match_time"] or 0)

        total_len = 0
        if tr > 0 or mt > 0:
            total_len = int(tr) + int(mt)
        self.total_match_length = total_len if total_len > 0 else None

        if tr > 0:
            if self.total_match_length is not None and self.total_match_length < 5400:
                end_in = max(0, tr - 120)
            else:
                end_in = max(0, tr - VOTE_END_OFFSET_SECONDS)
        else:
            if mt > 0:
                end_in = max(0, mt - VOTE_END_OFFSET_SECONDS)
            else:
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

        if old:
            self.vote_counts[old] = max(0, self.vote_counts.get(old, 1) - 1)
            if self.vote_counts[old] == 0:
                self.vote_counts.pop(old, None)

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
        if not self.state.active or self.cog.offline:
            return await interaction.response.send_message(
                "Voting is not currently active.",
                ephemeral=True
            )

        map_id = self.values[0]
        self.state.record_vote(interaction.user.id, map_id)

        await interaction.response.send_message(
            f"Vote recorded for `{map_id}`",
            ephemeral=True
        )

        # Refresh embed after each vote
        gs = await get_gamestate()
        if gs:
            await self.cog.update_main_embed(gs)


class MapVoteView(discord.ui.View):
    def __init__(self, state: VoteState, cog: "MapVote", enabled: bool):
        super().__init__(timeout=None)
        self.enabled = enabled
        select = MapVoteSelect(state, cog)
        select.disabled = not enabled
        self.add_item(select)


# --------------------------------------------------
# COG
# --------------------------------------------------

class MapVote(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = VoteState()
        self.last_map_id = None

        self.vote_view: MapVoteView | None = None
        self.offline: bool = False  # "Map voting offline" mode

        persisted = load_persisted_state()
        if persisted:
            self.state.vote_message_id = persisted.get("vote_message_id")
            self.offline = persisted.get("offline", False)

    def cog_unload(self):
        if self.tick_task.is_running():
            self.tick_task.cancel()

    # ---------------- on_ready / startup ----------------

    @commands.Cog.listener()
    async def on_ready(self):
        try:
            await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))
            print("[MapVote] Commands synced.")
        except Exception as e:
            print("[MapVote] Sync error:", e)

        if not self.tick_task.is_running():
            self.tick_task.start()
            print("[MapVote] tick_task started")

        # Ensure we have an embed on startup
        await self.ensure_embed_on_startup()

    async def ensure_embed_on_startup(self):
        channel = self.bot.get_channel(MAPVOTE_CHANNEL_ID)
        if not channel or not isinstance(channel, discord.TextChannel):
            print("[MapVote] Map vote channel invalid on startup")
            return

        self.state.vote_channel = channel

        # If we have a stored message ID, try to fetch it
        if self.state.vote_message_id:
            try:
                await channel.fetch_message(self.state.vote_message_id)
                print(f"[MapVote] Reattached to existing message {self.state.vote_message_id}")
                return
            except discord.NotFound:
                print("[MapVote] Stored message not found, creating a new one...")
            except Exception as e:
                print("[MapVote] Error fetching stored message:", e)

        # No stored or valid message, create a fresh one
        dummy_embed = discord.Embed(
            title="🗺️ Map Vote",
            description="Initialising map vote status...",
            color=discord.Color.red()
        )
        msg = await channel.send(embed=dummy_embed, view=None)
        self.state.vote_message_id = msg.id
        save_persisted_state(self.state.vote_message_id, self.offline)
        print(f"[MapVote] Created new map vote message: {msg.id}")

    # ---------------- Broadcast helper ----------------

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
                "by": "7DRBot",
                "save_message": False
            }
            _ = rcon_post("message_player", payload)
            await asyncio.sleep(0.1)

    # ---------------- Slash commands ----------------

    @app_commands.command(
        name="force_mapvote",
        description="Force start a map vote (or re-enable map voting if offline)"
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @checks.has_role(MAPVOTE_ADMIN_ROLE_ID)
    async def force_mapvote_cmd(self, interaction: discord.Interaction):
        await interaction.response.send_message("Fetching gamestate…", ephemeral=True)
        gs = await get_gamestate()
        if not gs:
            return await interaction.followup.send("❌ Could not read gamestate.", ephemeral=True)

        # Re-enable system if it was offline
        self.offline = False
        save_persisted_state(self.state.vote_message_id, self.offline)

        await self.start_vote(gs)
        await interaction.followup.send("✅ Vote started (and map voting enabled).", ephemeral=True)

    @app_commands.command(
        name="mapvote_stop",
        description="Turn off map voting and restore default rotation"
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @checks.has_role(MAPVOTE_ADMIN_ROLE_ID)
    async def mapvote_stop_cmd(self, interaction: discord.Interaction):
        await interaction.response.send_message("Stopping map voting…", ephemeral=True)

        # Turn off voting
        self.offline = True
        self.state.active = False
        self.state.options.clear()
        self.state.vote_counts.clear()
        self.state.user_votes.clear()
        save_persisted_state(self.state.vote_message_id, self.offline)

        # Restore default rotation
        result = await set_full_rotation(DEFAULT_ROTATION)

        # Update embed to "offline" mode
        gs = await get_gamestate()
        if gs:
            await self.update_main_embed(gs)

        await interaction.followup.send(
            f"🛑 Map voting stopped.\n"
            f"Default rotation applied.\n"
            f"CRCON response:\n```{result}```",
            ephemeral=True
        )

    # ---------------- Embed + view builders ----------------

    def is_standby(self, gs: dict) -> bool:
        # Standby = match timer hasn't started at all
        return gs["match_time"] == 0 and gs["time_remaining"] == 0

    def get_vote_view(self) -> MapVoteView | None:
        if not self.state.options:
            # No options yet means no select UI
            return None

        enabled = (not self.offline) and self.state.active
        if self.vote_view is None or self.vote_view.enabled != enabled:
            self.vote_view = MapVoteView(self.state, self, enabled)
        return self.vote_view

    def build_embed(self, gs: dict) -> discord.Embed:
        current = gs["current_map_pretty"] or "Unknown"
        raw_time = gs["raw_time_remaining"]
        axis = gs["axis_players"]
        allied = gs["allied_players"]

        standby = self.is_standby(gs)
        now = datetime.now(timezone.utc)
        vote_left = None
        if self.state.vote_end_at:
            vote_left = (self.state.vote_end_at - now).total_seconds()

        if self.offline:
            # OFFLINE MODE
            desc = (
                f"**Status:** 🛑 Map voting is **OFFLINE**\n"
                f"**Server:** {gs['server_name']}\n"
                f"**Current map:** {current}\n"
                f"**Match timer:** `{raw_time}`\n"
                f"**Players:** Allied: `{allied}` — Axis: `{axis}`\n\n"
                f"An admin has disabled map voting. Rotation is set to the default list."
            )
            embed = discord.Embed(
                title="🛑 Map Voting Offline",
                description=desc,
                color=discord.Color.dark_grey()
            )
            if OFFLINE_STATUS_IMAGE:
                embed.set_image(url=OFFLINE_STATUS_IMAGE)
            else:
                img = MAP_CDN_IMAGES.get(current)
                if img:
                    embed.set_image(url=img)
            return embed

        if standby:
            # STANDBY MODE
            desc = (
                f"**Status:** ⏸️ Standby (match not started)\n"
                f"**Server:** {gs['server_name']}\n"
                f"**Current map:** {current}\n"
                f"**Match timer:** `{raw_time}`\n"
                f"**Players:** Allied: `{allied}` — Axis: `{axis}`\n\n"
                f"Waiting for the match timer to start.\n"
                f"Voting will activate automatically once the round begins."
            )
            embed = discord.Embed(
                title="⏸️ Map Voting Standby",
                description=desc,
                color=discord.Color.orange()
            )
            if STANDBY_STATUS_IMAGE:
                embed.set_image(url=STANDBY_STATUS_IMAGE)
            else:
                img = MAP_CDN_IMAGES.get(current)
                if img:
                    embed.set_image(url=img)
            return embed

        if self.state.active:
            # ACTIVE VOTE
            # Live votes text
            if self.state.vote_counts:
                sorted_votes = sorted(
                    self.state.vote_counts.items(),
                    key=lambda x: x[1],
                    reverse=True
                )
                lines = []
                for map_id, count in sorted_votes:
                    pretty = next((p for p, mid in MAPS.items() if mid == map_id), map_id)
                    lines.append(f"**{pretty}** — {count} vote{'s' if count != 1 else ''}")
                votetext = "\n".join(lines)
            else:
                votetext = "*No votes yet.*"

            if self.state.total_match_length:
                total_m = self.state.total_match_length // 60
                total_s = self.state.total_match_length % 60
                total_str = f"{total_m:02d}:{total_s:02d}"
                length_line = f"**Match length (detected):** `{total_str}`\n"
            else:
                length_line = ""

            desc = (
                f"**Status:** 🗳️ Map voting is **ACTIVE**\n"
                f"**Current map:** {current}\n"
                f"**Match remaining:** `{raw_time}`\n"
                f"{length_line}"
                f"**Players:** Allied: `{allied}` — Axis: `{axis}`\n"
                f"**Vote closes in:** `{fmt_vote_secs(vote_left)}`\n\n"
                f"**Live votes:**\n{votetext}"
            )

            embed = discord.Embed(
                title="🗺️ 7DR Hell Let Loose Map Voting",
                description=desc,
                color=discord.Color.red()
            )
            img = MAP_CDN_IMAGES.get(current)
            if img:
                embed.set_image(url=img)
            return embed

        # FALLBACK (should rarely appear)
        desc = (
            f"**Status:** Idle\n"
            f"**Current map:** {current}\n"
            f"**Match timer:** `{raw_time}`\n"
            f"**Players:** Allied: `{allied}` — Axis: `{axis}`\n\n"
            f"Map voting will start automatically when the next round begins."
        )
        embed = discord.Embed(
            title="🗺️ Map Vote",
            description=desc,
            color=discord.Color.blue()
        )
        img = MAP_CDN_IMAGES.get(current)
        if img:
            embed.set_image(url=img)
        return embed

    async def update_main_embed(self, gs: dict):
        """
        Edit the single persistent embed according to current mode + gamestate.
        """
        if not (self.state.vote_channel and self.state.vote_message_id):
            return

        try:
            msg = await self.state.vote_channel.fetch_message(self.state.vote_message_id)
        except discord.NotFound:
            # Message disappeared – recreate
            await self.ensure_embed_on_startup()
            if not self.state.vote_message_id:
                return
            msg = await self.state.vote_channel.fetch_message(self.state.vote_message_id)
        except Exception as e:
            print("[MapVote] Failed to fetch embed message:", e)
            return

        embed = self.build_embed(gs)
        view = self.get_vote_view()
        await msg.edit(embed=embed, view=view)

    # ---------------- Vote lifecycle ----------------

    async def start_vote(self, gs: dict):
        """
        Start a fresh vote for the current match.
        """
        if not self.state.vote_channel:
            channel = self.bot.get_channel(MAPVOTE_CHANNEL_ID)
            if not channel or not isinstance(channel, discord.TextChannel):
                print("[MapVote] Vote channel invalid")
                return
            self.state.vote_channel = channel

        # Reset state for this match
        self.state.reset_for_match(gs)

        # Build options (exclude current map)
        pool = [(p, mid) for p, mid in MAPS.items() if mid != gs["current_map_id"]]
        random.shuffle(pool)
        pool = pool[:min(len(pool), OPTIONS_PER_VOTE, 25)]
        self.state.set_options({p: mid for p, mid in pool})

        # Make sure we have a message to edit
        if not self.state.vote_message_id:
            await self.ensure_embed_on_startup()

        # Update embed to show active vote
        await self.update_main_embed(gs)
        self.last_map_id = gs["current_map_id"]

        print(f"[MapVote] Vote started for {gs['current_map_pretty']}")
        await self.broadcast_to_all(BROADCAST_START)

    async def end_vote_and_queue(self):
        """
        End a vote and set the map rotation accordingly.
        """
        self.state.active = False

        channel = self.state.vote_channel
        if not channel:
            print("[MapVote] end_vote_and_queue called with no channel")
            return

        winner_id = self.state.winner()
        current_map_id = self.state.match_map_id

        if not current_map_id:
            print("[MapVote] No match_map_id when ending vote; aborting rotation change.")
            await channel.send("⚠️ Could not determine current map – rotation unchanged.")
            return

        if not winner_id:
            # No votes – repeat the same map forever
            await self.broadcast_to_all(BROADCAST_NO_VOTES)
            result = await set_full_rotation([current_map_id])
            await channel.send(
                "❌ No votes were cast.\n"
                "Map rotation set to repeat the **current map**.\n"
                f"CRCON Response:\n```{result}```"
            )
            print("[MapVote] No votes, repeating current map.")
            return

        pretty = next((p for p, mid in MAPS.items() if mid == winner_id), winner_id)

        # Winner → single-map rotation
        result = await set_full_rotation([winner_id])

        await self.broadcast_to_all(f"{pretty} has won the vote!")
        await channel.send(
            f"🏆 **Winner: {pretty}**\n"
            f"Map rotation set to play **only this map**.\n"
            f"CRCON Response:\n```{result}```"
        )
        print(f"[MapVote] Vote ended, winner {pretty}")

    # ---------------- Background loop ----------------

    @tasks.loop(seconds=EMBED_UPDATE_INTERVAL)
    async def tick_task(self):
        gs = await get_gamestate()
        if not gs:
            return

        # Ensure we have the channel set
        if not self.state.vote_channel:
            channel = self.bot.get_channel(MAPVOTE_CHANNEL_ID)
            if isinstance(channel, discord.TextChannel):
                self.state.vote_channel = channel

        standby_now = self.is_standby(gs)

        # Always keep embed up to date
        if self.state.vote_message_id and self.state.vote_channel:
            await self.update_main_embed(gs)

        # If offline, do nothing else
        if self.offline:
            self.last_map_id = gs["current_map_id"]
            return

        # Initialize last_map_id
        if self.last_map_id is None:
            self.last_map_id = gs["current_map_id"]
            # If match is already active and no vote, start one
            if not standby_now and not self.state.active:
                await self.start_vote(gs)
            return

        # Detect map change → new match
        if gs["current_map_id"] != self.last_map_id:
            print(f"[MapVote] New map detected: {gs['current_map_pretty']}")
            self.last_map_id = gs["current_map_id"]
            if standby_now:
                # New map but timer not started yet → standby
                self.state.active = False
                return
            else:
                await self.start_vote(gs)
                return

        # Same map as last tick
        if not self.state.active:
            if standby_now:
                # Still in standby
                return
            else:
                # Match active but vote not started yet
                await self.start_vote(gs)
                return

        # At this point we have an active vote
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

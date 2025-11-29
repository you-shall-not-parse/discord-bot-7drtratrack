import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import os
import json
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

# Role that can use /mapvote_* commands (plus admins)
MAPVOTE_ADMIN_ROLE_ID = 1213495462632361994  # TODO: set this to your role ID

# Vote ends this many seconds before match end
VOTE_END_OFFSET_SECONDS = 120

# Embed update speed
EMBED_UPDATE_INTERVAL = 1

# How many map options to show
OPTIONS_PER_VOTE = 10

# Persistent state file (message id, enabled flag, etc.)
MAPVOTE_STATE_FILE = "mapvote_state.json"

# Pretty name → CRCON ID
MAPS = {
    "Elsenborn Ridge Warfare": "elsenbornridge_warfare_day",
    "Carentan Warfare": "carentan_warfare",
    "Foy Warfare": "foy_warfare",
    "Hill 400 Warfare": "hill400_warfare",
}

# Default rotation when map voting is disabled
# (Used when /mapvote_stop is run)
DEFAULT_ROTATION = [
    # TODO: fill this with your normal rotation map IDs, e.g.:
    # "foy_warfare_day",
    # "carentan_warfare_day",
    # "elsenbornridge_warfare_day",
    # "hill400_warfare_day",
]

# CDN images by pretty_name (must match EXACT pretty_name)
MAP_CDN_IMAGES = {
    "Elsenborn Ridge Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365408158012407840/Elsenborn_Custom_MLL.png",
    "Carentan Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365403110197166191/Carentan_SP_NoHQ.png",
    "Foy Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365404141337186304/Foy_SP_NoHQ.png",
    "Hill 400 Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365404269116919930/Hill400_SP_NoHQ_1.png",
}

# Extra CDN images for status modes – YOU define these
STANDBY_CDN_IMAGE = "https://cdn.discordapp.com/attachments/1098976074852999261/1442258185137295380/file_000000009ba871f4b7700cb80af3a3f3.png?ex=692cb01b&is=692b5e9b&hm=8a65ad3f987dfd4e8876613ee2188d0fb9ae3b84abd7987163061535082a107e&"   # server empty / timer not running
OFFLINE_CDN_IMAGE = "https://cdn.discordapp.com/attachments/1098976074852999261/1442258185137295380/file_000000009ba871f4b7700cb80af3a3f3.png?ex=692cb01b&is=692b5e9b&hm=8a65ad3f987dfd4e8876613ee2188d0fb9ae3b84abd7987163061535082a107e&"   # CRCON/API unreachable
DISABLED_CDN_IMAGE = "https://cdn.discordapp.com/attachments/1098976074852999261/1442258185137295380/file_000000009ba871f4b7700cb80af3a3f3.png?ex=692cb01b&is=692b5e9b&hm=8a65ad3f987dfd4e8876613ee2188d0fb9ae3b84abd7987163061535082a107e&" # voting disabled by staff

# Broadcast templates (per-player messages)
BROADCAST_START = "Next-map voting is OPEN on Discord!"
BROADCAST_ENDING_SOON = "Map vote closes in 2 minutes!"
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


def rcon_set_rotation(map_ids: list[str]):
    """Wrapper around set_map_rotation."""
    return rcon_post("set_map_rotation", {"map_names": map_ids})


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def fmt_vote_secs(sec):
    if sec is None:
        return "Unknown"
    sec = max(0, int(sec))
    m = sec // 60
    s = sec % 60
    return f"{m:02d}:{s:02d}"


def load_persistent_state() -> dict:
    if not os.path.exists(MAPVOTE_STATE_FILE):
        return {}
    try:
        with open(MAPVOTE_STATE_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print("[MapVote] Failed to load state file:", e)
        return {}


def save_persistent_state(data: dict):
    try:
        with open(MAPVOTE_STATE_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print("[MapVote] Failed to save state file:", e)


async def fetch_gamestate():
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
            "time_remaining": float(res.get("time_remaining") or 0.0),
            "raw_time_remaining": res.get("raw_time_remaining") or "0:00:00",
            "axis_players": int(res.get("num_axis_players") or 0),
            "allied_players": int(res.get("num_allied_players") or 0),
            "server_name": res.get("server_name") or "Unknown server",
        }
    except Exception as e:
        print("[MapVote] Error parsing gamestate:", e, data)
        return None


def classify_status(gs: dict | None, enabled: bool) -> str:
    """
    Returns one of: "OFFLINE", "DISABLED", "STANDBY", "ACTIVE"
    """
    if gs is None:
        return "OFFLINE"

    if not enabled:
        return "DISABLED"

    total_players = gs["axis_players"] + gs["allied_players"]
    time_remaining = gs["time_remaining"]

    # Standby: no players, timer not running
    if total_players == 0 and time_remaining == 0:
        return "STANDBY"

    return "ACTIVE"


def mapvote_staff_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            return False
        if interaction.user.guild_permissions.administrator:
            return True
        role = interaction.guild.get_role(MAPVOTE_ADMIN_ROLE_ID)
        return role is not None and role in interaction.user.roles

    return app_commands.check(predicate)


# --------------------------------------------------
# VOTE STATE
# --------------------------------------------------

class VoteState:
    def __init__(self):
        self.active: bool = False
        self.vote_channel: discord.TextChannel | None = None
        self.vote_message_id: int | None = None

        self.match_map_id: str | None = None
        self.match_map_pretty: str | None = None
        self.vote_start_at: datetime | None = None
        self.vote_end_at: datetime | None = None
        self.warning_sent: bool = False

        self.options: dict[str, str] = {}       # pretty → map_id
        self.user_votes: dict[int, str] = {}    # user_id → map_id
        self.vote_counts: dict[str, int] = {}   # map_id → int

    def reset_for_match(self, gs: dict):
        self.active = True
        self.vote_message_id = None

        self.match_map_id = gs["current_map_id"]
        self.match_map_pretty = gs["current_map_pretty"]

        now = datetime.now(timezone.utc)
        tr = float(gs["time_remaining"] or 0.0)

        # Vote closes VOTE_END_OFFSET_SECONDS before match end.
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
            placeholder="Vote for the next map…",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        if not self.state.active:
            return await interaction.response.send_message(
                "Voting is not currently active.",
                ephemeral=True
            )

        map_id = self.values[0]
        self.state.record_vote(interaction.user.id, map_id)

        await interaction.response.send_message(
            f"Your vote has been recorded.",
            ephemeral=True
        )

        # Refresh the live embed
        await self.cog.refresh_active_embed()


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

        # Persisted data
        persisted = load_persistent_state()
        self.saved_message_id: int | None = persisted.get("message_id")
        self.saved_channel_id: int | None = persisted.get("channel_id")
        self.mapvote_enabled: bool = persisted.get("mapvote_enabled", True)
        self.last_map_id: str | None = persisted.get("last_map_id")

        # UI view
        self.vote_view: MapVoteView | None = None

    # ---------------- Persistence helpers ----------------

    def _save_state_file(self):
        data = {
            "message_id": self.saved_message_id,
            "channel_id": self.saved_channel_id,
            "mapvote_enabled": self.mapvote_enabled,
            "last_map_id": self.last_map_id,
        }
        save_persistent_state(data)

    # ---------------- Lifecycle ----------------

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

        # Ensure initial embed exists in some state
        await self.ensure_initial_embed()

        # Start background task once
        if not self.tick_task.is_running():
            self.tick_task.start()
            print("[MapVote] tick_task started")

    # --------------------------------------------------
    # Per-player broadcast using message_player
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
                "by": "7DRBot",
                "save_message": False,
            }
            _ = rcon_post("message_player", payload)
            await asyncio.sleep(0.1)

    # --------------------------------------------------
    # Slash commands
    # --------------------------------------------------

    @app_commands.command(
        name="force_mapvote",
        description="Force start a map vote for the current match."
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @mapvote_staff_check()
    async def force_mapvote_cmd(self, interaction: discord.Interaction):
        await interaction.response.send_message("Fetching gamestate…", ephemeral=True)
        gs = await fetch_gamestate()
        if not gs:
            return await interaction.followup.send(
                "❌ Could not read gamestate (CRCON offline?).",
                ephemeral=True
            )

        status = classify_status(gs, self.mapvote_enabled)
        if status == "STANDBY":
            return await interaction.followup.send(
                "⚠️ Server is in standby (no players, timer not running). "
                "Map voting will start automatically when a player joins.",
                ephemeral=True
            )

        self.mapvote_enabled = True
        self._save_state_file()

        await self.start_vote(gs)
        await interaction.followup.send("✅ Map vote forced for this match.", ephemeral=True)

    @app_commands.command(
        name="mapvote_start",
        description="Enable the automatic map voting system."
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @mapvote_staff_check()
    async def mapvote_start_cmd(self, interaction: discord.Interaction):
        self.mapvote_enabled = True
        self._save_state_file()
        await interaction.response.send_message(
            "✅ Map voting has been **enabled**. It will run automatically when the server is active.",
            ephemeral=True
        )

        # Refresh embed state immediately
        await self.refresh_status_embed()

    @app_commands.command(
        name="mapvote_stop",
        description="Disable map voting and restore default map rotation."
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @mapvote_staff_check()
    async def mapvote_stop_cmd(self, interaction: discord.Interaction):
        self.mapvote_enabled = False
        self.state.active = False
        self.state.warning_sent = False
        self._save_state_file()

        if DEFAULT_ROTATION:
            rcon_set_rotation(DEFAULT_ROTATION)

        await interaction.response.send_message(
            "⛔ Map voting has been **disabled** and the **default map rotation** has been restored.",
            ephemeral=True
        )

        await self.refresh_status_embed()

    # --------------------------------------------------
    # Embed & status handling
    # --------------------------------------------------

    def build_embed(self, status: str, gs: dict | None) -> discord.Embed:
        now = datetime.now(timezone.utc)

        # Defaults
        current = (gs or {}).get("current_map_pretty") if gs else None
        raw_time = (gs or {}).get("raw_time_remaining") if gs else None
        axis = (gs or {}).get("axis_players") if gs else 0
        allied = (gs or {}).get("allied_players") if gs else 0
        server_name = (gs or {}).get("server_name") if gs else "Unknown server"

        if not current:
            current = "Unknown"
        if not raw_time:
            raw_time = "0:00:00"

        total_players = axis + allied if gs else 0

        # Base embed
        embed = discord.Embed(
            title="🗺️ 7DR Map Voting",
            color=discord.Color.red()
        )
        embed.set_footer(text=server_name)

        # Status-specific description + image
        if status == "OFFLINE":
            desc = (
                "⚠️ **CRCON / API unreachable or server offline.**\n\n"
                "Map voting is currently **offline**.\n"
                "The server will continue using its current map rotation."
            )
            embed.description = desc
            embed.set_image(url=OFFLINE_CDN_IMAGE)

        elif status == "DISABLED":
            desc = (
                "⛔ **Map voting is disabled by staff.**\n\n"
                "The server is running the **default map rotation**.\n\n"
                f"**Current map:** {current}\n"
                f"**Players:** Allied `{allied}` — Axis `{axis}`"
            )
            embed.description = desc
            embed.set_image(url=DISABLED_CDN_IMAGE)

        elif status == "STANDBY":
            desc = (
                "🕓 **Server is in standby.**\n\n"
                "No players are connected and the round timer is not running.\n"
                "Map voting will **start automatically** when a player joins "
                "and the round timer begins.\n\n"
                f"**Current map:** {current}\n"
                f"**Match remaining:** `{raw_time}`\n"
                f"**Players:** Allied `{allied}` — Axis `{axis}`"
            )
            embed.description = desc
            embed.set_image(url=STANDBY_CDN_IMAGE)

        elif status == "ACTIVE":
            # Active server. Show vote info if any.
            if self.state.active and self.state.vote_end_at:
                vote_left = (self.state.vote_end_at - now).total_seconds()
                vote_left_str = fmt_vote_secs(vote_left)
            else:
                vote_left = None
                vote_left_str = "—"

            # Live votes
            if self.state.active and self.state.vote_counts:
                sorted_votes = sorted(
                    self.state.vote_counts.items(),
                    key=lambda x: x[1],
                    reverse=True
                )
                lines = []
                for map_id, count in sorted_votes:
                    pretty = next(
                        (p for p, mid in MAPS.items() if mid == map_id),
                        map_id
                    )
                    lines.append(
                        f"**{pretty}** — {count} vote{'s' if count != 1 else ''}"
                    )
                votetext = "\n".join(lines)
            elif self.state.active:
                votetext = "*No votes yet.*"
            else:
                votetext = "*No active vote for this round (either finished or not started yet).*"

            desc = (
                f"✅ **Server active** — map voting system enabled.\n\n"
                f"**Current map:** {current}\n"
                f"**Match remaining:** `{raw_time}`\n"
                f"**Players:** Allied `{allied}` — Axis `{axis}`\n"
            )

            if self.state.active and self.state.vote_end_at:
                desc += f"**Vote closes in:** `{vote_left_str}`\n\n"
            else:
                desc += "\n"

            desc += f"**Live votes:**\n{votetext}"
            embed.description = desc

            # Active map image
            img = MAP_CDN_IMAGES.get(current)
            if img:
                embed.set_image(url=img)

        else:
            embed.description = "Unknown status."
            embed.set_image(url=OFFLINE_CDN_IMAGE)

        return embed

    async def ensure_embed(self, status: str, gs: dict | None) -> discord.Message | None:
        """Ensure the mapvote embed exists and is updated in place."""
        channel_id = self.saved_channel_id or MAPVOTE_CHANNEL_ID
        channel = self.bot.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            print("[MapVote] Vote channel invalid")
            return None

        msg: discord.Message | None = None

        if self.saved_message_id:
            try:
                msg = await channel.fetch_message(self.saved_message_id)
            except discord.NotFound:
                msg = None
            except Exception as e:
                print("[MapVote] Failed to fetch existing mapvote message:", e)
                msg = None

        embed = self.build_embed(status, gs)

        # Decide if view should be attached
        view = None
        if status == "ACTIVE" and self.state.active and self.state.options:
            if self.vote_view is None:
                self.vote_view = MapVoteView(self.state, self)
            view = self.vote_view

        if msg is None:
            # Create new message
            msg = await channel.send(embed=embed, view=view)
            self.saved_message_id = msg.id
            self.saved_channel_id = channel.id
            self._save_state_file()
        else:
            # Update existing message
            try:
                await msg.edit(embed=embed, view=view)
            except Exception as e:
                print("[MapVote] Failed to edit mapvote message:", e)

        # Attach to state for convenience
        self.state.vote_channel = channel
        self.state.vote_message_id = msg.id

        return msg

    async def ensure_initial_embed(self):
        gs = await fetch_gamestate()
        status = classify_status(gs, self.mapvote_enabled)
        await self.ensure_embed(status, gs)

    async def refresh_status_embed(self):
        gs = await fetch_gamestate()
        status = classify_status(gs, self.mapvote_enabled)
        await self.ensure_embed(status, gs)

    async def refresh_active_embed(self):
        gs = await fetch_gamestate()
        if not gs:
            await self.ensure_embed("OFFLINE", None)
            return
        await self.ensure_embed("ACTIVE", gs)

    # --------------------------------------------------
    # Voting flow
    # --------------------------------------------------

    async def start_vote(self, gs: dict):
        """Start a new vote for the given match."""
        # Reset state for this match
        self.state.reset_for_match(gs)

        # Build option list (exclude current map)
        pool = [(p, mid) for p, mid in MAPS.items() if mid != gs["current_map_id"]]
        random.shuffle(pool)
        pool = pool[:min(len(pool), OPTIONS_PER_VOTE, 25)]
        self.state.set_options({p: mid for p, mid in pool})

        # Refresh embed into ACTIVE mode (with dropdown)
        await self.ensure_embed("ACTIVE", gs)

        print(f"[MapVote] Vote started for {gs['current_map_pretty']}")
        await self.broadcast_to_all(BROADCAST_START)

    async def end_vote_and_queue(self, gs: dict):
        """End the current vote and update map rotation."""
        self.state.active = False
        channel = self.state.vote_channel
        if not channel:
            print("[MapVote] end_vote_and_queue called with no channel")
            return

        winner_id = self.state.winner()

        if not winner_id:
            # No votes: keep playing the same map over and over
            current_id = gs["current_map_id"]
            res = rcon_set_rotation([current_id])

            await self.broadcast_to_all(BROADCAST_NO_VOTES)
            await channel.send(
                "⚖️ **No votes were cast.**\n"
                "The current map has been set as the only map in rotation.\n"
                f"CRCON Response:\n```{res}```"
            )
            print("[MapVote] Vote ended with no votes — locked rotation to current map.")
        else:
            pretty = next((p for p, mid in MAPS.items() if mid == winner_id), winner_id)
            res = rcon_set_rotation([winner_id])

            await self.broadcast_to_all(f"🏆 {pretty} has won the vote!")
            await channel.send(
                f"🏆 **Winner: {pretty}**\n"
                f"The next rotation has been set to this map only.\n"
                f"CRCON Response:\n```{res}```"
            )
            print(f"[MapVote] Vote ended, winner {pretty}")

        # Refresh embed to reflect that the vote is no longer active
        await self.refresh_status_embed()

    # --------------------------------------------------
    # Background loop — updates every second
    # --------------------------------------------------
    @tasks.loop(seconds=EMBED_UPDATE_INTERVAL)
    async def tick_task(self):
        gs = await fetch_gamestate()
        status = classify_status(gs, self.mapvote_enabled)

        # Status-based behaviour
        if status == "OFFLINE":
            await self.ensure_embed("OFFLINE", None)
            self.state.active = False
            return

        if status == "DISABLED":
            await self.ensure_embed("DISABLED", gs)
            self.state.active = False
            return

        if status == "STANDBY":
            await self.ensure_embed("STANDBY", gs)
            self.state.active = False
            return

        # ACTIVE
        # Detect new match by current_map_id change
        if gs:
            current_map_id = gs["current_map_id"]
        else:
            current_map_id = None

        if current_map_id and current_map_id != self.last_map_id:
            # New match detected
            self.last_map_id = current_map_id
            self._save_state_file()
            await self.start_vote(gs)
        else:
            # No new match: just update embed / timing
            await self.ensure_embed("ACTIVE", gs)

        # If a vote is active, handle countdown / end
        if self.state.active and self.state.vote_end_at:
            now = datetime.now(timezone.utc)
            remaining = (self.state.vote_end_at - now).total_seconds()

            if remaining <= 0:
                await self.end_vote_and_queue(gs)
                return

            # 2-minute warning
            if remaining <= 120 and not self.state.warning_sent:
                self.state.warning_sent = True
                await self.broadcast_to_all(BROADCAST_ENDING_SOON)
                if self.state.vote_channel:
                    await self.state.vote_channel.send("⏳ Vote closes in 2 minutes!")

    @tick_task.before_loop
    async def before_tick(self):
        print("[MapVote] Waiting until bot is ready before starting tick_task...")
        await self.bot.wait_until_ready()
        print("[MapVote] Bot ready, tick_task will now run.")


async def setup(bot: commands.Bot):
    await bot.add_cog(MapVote(bot))

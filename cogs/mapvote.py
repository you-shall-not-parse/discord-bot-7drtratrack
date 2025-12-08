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
# CONFIG
# --------------------------------------------------

GUILD_ID = 1097913605082579024
MAPVOTE_CHANNEL_ID = 1441751747935735878
MAPVOTE_RESULTS_CHANNEL_ID = 1441751747935735878  # Change this to your desired results channel ID

MAPVOTE_ADMIN_ROLE_ID = 1279832920479109160  # role allowed to control mapvote

VOTE_END_OFFSET_SECONDS = 120          # vote closes this many seconds before match end
EMBED_UPDATE_INTERVAL = 1              # how often to refresh the status embed
OPTIONS_PER_VOTE = 22                  # how many map options to show
MAPVOTE_STATE_FILE = "mapvote_state.json"
BROADCAST_DELAY = 0.1                  # delay between player broadcasts (seconds)

# Pretty name → CRCON ID
MAPS = {
    "Elsenborn Ridge Warfare": "elsenbornridge_warfare_morning",
    "Carentan Warfare": "carentan_warfare",
    "Foy Warfare": "foy_warfare",
    "Hill 400 Warfare": "hill400_warfare",
    "St. Marie Du Mont": "stmariedumont_warfare",
    "Utah Beach": "utahbeach_warfare",
    "St. Mere Eglise": "stmereeglise_warfare",
    "El Alamein": "elalamein_warfare",
    "Mortain": "mortain_warfare_dusk",
    "Smolensk": "smolensk_warfare_dusk",
    "Driel": "driel_warfare",
    "Kursk": "kursk_warfare",
    "Carentan Warfare Night": "carentan_warfare_night",
    "Hurtgen Forest": "hurtgenforest_warfare_V2",
    "Remagen": "remagen_warfare",
    "Omaha Beach": "omahabeach_warfare",
    "Kharkov": "kharkov_warfare",
    "Mortain": "mortain_warfare_day",
    "Purple Heart Lane": "PHL_L_1944_Warfare",
    "Tobruk": "tobruk_warfare_morning",
    "Stalingrad": "STA_L_1942_Warfare",
}

# Default rotation when mapvote is disabled
DEFAULT_ROTATION = [
    "stmariedumont_warfare",
    "elalamein_warfare_night",
    "elsenbornridge_warfare_morning",
    "stmereeglise_warfare",
    "elalamein_warfare",
    "smolensk_warfare_dusk",
]

# Map images (put your real CDN URLs back in here)
MAP_CDN_IMAGES = {
    "Elsenborn Ridge Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1444494673149300796/ChatGPT_Image_Nov_30_2025_01_05_17_AM.png?ex=69381ebf&is=6936cd3f&hm=cdb114a6a2550d2d83318d3b3c1d6717022fa0c8665c645818fb8c78b8f71fa3",
    "Carentan Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1444515451727253544/file_00000000e5f871f488f94dd458b30c09.png?ex=69383219&is=6936e099&hm=40998a104cbffc2fe0b37c515f6158c9722606b7c1ec5d33bdc03e5eb4341e2a",
    "Foy Warfare": "https://media.discordapp.net/attachments/1098976074852999261/1444492145913499800/ChatGPT_Image_Nov_30_2025_12_55_43_AM.png?ex=69381c64&is=6936cae4&hm=dc9f2577c73c1b1bb2f5403c10b7f9a6ae5f926799ef6c5909025434de018429&=&format=webp&quality=lossless&width=1240&height=826",
    "Hill 400 Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1444497579210707004/ChatGPT_Image_Nov_30_2025_01_15_52_AM.png?ex=69382174&is=6936cff4&hm=f9e16ba8d2b9f20dd799bd5970c11f38c1f427689585e2d139cfd1294888a612",
}

STANDBY_CDN_IMAGE = "https://cdn.discordapp.com/attachments/1098976074852999261/1442258185137295380/file_000000009ba871f4b7700cb80af3a3f3.png?ex=6937e4db&is=6936935b&hm=ffcf7d5e580476b6af6f2c5a1a1055ed656aa86034c14094d9434b0d2019f8cc&g"
OFFLINE_CDN_IMAGE = "https://cdn.discordapp.com/attachments/1098976074852999261/1444486531531280505/ChatGPT_Image_Nov_30_2025_12_33_09_AM.png?ex=6938172a&is=6936c5aa&hm=b08120d9cf51a7bf212e0926cb12036c429d6287a7b542fc8f4bc3b1aac36017"
DISABLED_CDN_IMAGE = "https://cdn.discordapp.com/attachments/1098976074852999261/1444486531531280505/ChatGPT_Image_Nov_30_2025_12_33_09_AM.png?ex=6938172a&is=6936c5aa&hm=b08120d9cf51a7bf212e0926cb12036c429d6287a7b542fc8f4bc3b1aac36017"

# Broadcasts into game to all players
BROADCAST_START = "Vote for the next map on discord.gg/7drc!\nYou can select one of up to 25 maps!\n Join us now as a clan member or join as a Blueberry to keep up to date with the latest news, map vote and see our melee kills feed!"
BROADCAST_ENDING_SOON = "Map vote closes in 2 minutes!\nHead over to discord.gg/7drc to cast your vote!"
BROADCAST_NO_VOTES = "No votes, the map rotation wins.\nHead over to discord.gg/7drc to cast your vote!"

# CRCON
CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"
CRCON_API_KEY = os.getenv("CRCON_API_KEY")


# --------------------------------------------------
# CRCON helpers
# --------------------------------------------------

def rcon_get(endpoint: str, params: dict | None = None):
    try:
        r = requests.get(
            CRCON_PANEL_URL + endpoint,
            params=params,
            headers={"Authorization": f"Bearer {CRCON_API_KEY}"},
            timeout=10,
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
            timeout=10,
        )
        try:
            return r.json()
        except Exception:
            return {"status": r.status_code, "text": r.text or ""}
    except Exception as e:
        print(f"[MapVote] rcon_post error on {endpoint}: {e}")
        return {"error": str(e)}


def rcon_set_rotation(map_ids: list[str]):
    """Set next map rotation (single-map rotation for winner)."""
    return rcon_post("set_map_rotation", {"map_names": map_ids})


def rcon_get_logs(action: str, limit: int = 50):
    """Return recent logs filtered by action name."""
    return rcon_get("get_recent_logs", params={"filter_action": action, "limit": limit})


# --------------------------------------------------
# Helpers
# --------------------------------------------------

def fmt_vote_secs(sec: float | None) -> str:
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


async def fetch_gamestate() -> dict | None:
    data = rcon_get("get_gamestate")
    if not data or data.get("failed") or data.get("error"):
        print("[MapVote] Gamestate read failed:", data)
        return None

    res = data.get("result") or {}
    cur = res.get("current_map") or {}

    try:
        return {
            "current_map_id": cur.get("id"),
            "current_map_pretty": cur.get("pretty_name"),
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
    """OFFLINE / DISABLED / STANDBY / ACTIVE."""
    if gs is None:
        return "OFFLINE"
    if not enabled:
        return "DISABLED"

    total_players = gs["axis_players"] + gs["allied_players"]
    time_remaining = gs["time_remaining"]

    if total_players == 0 and time_remaining == 0:
        return "STANDBY"

    return "ACTIVE"


def parse_log_timestamp(ts_str: str | None) -> datetime | None:
    if not ts_str:
        return None
    ts_str = ts_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        try:
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None


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
# Vote state
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

        self.options: dict[str, str] = {}       # pretty -> map_id
        self.user_votes: dict[int, str] = {}    # user_id -> map_id

    def reset_for_match(self, gs: dict):
        self.active = True
        self.vote_message_id = None

        self.match_map_id = gs["current_map_id"]
        self.match_map_pretty = gs["current_map_pretty"]

        now = datetime.now(timezone.utc)
        tr = float(gs.get("time_remaining") or 0.0)
        end_in = max(0, tr - VOTE_END_OFFSET_SECONDS)

        self.vote_start_at = now
        self.vote_end_at = now + timedelta(seconds=end_in)
        self.warning_sent = False

        self.user_votes.clear()

    def set_options(self, mapping: dict[str, str]):
        self.options = mapping

    def record_vote(self, user_id: int, map_id: str):
        old = self.user_votes.get(user_id)
        if old == map_id:
            return
        self.user_votes[user_id] = map_id

    def get_vote_counts(self) -> dict[str, int]:
        """Compute vote counts on-demand from user_votes."""
        counts = {}
        for map_id in self.user_votes.values():
            counts[map_id] = counts.get(map_id, 0) + 1
        return counts

    def winner(self) -> str | None:
        counts = self.get_vote_counts()
        if not counts:
            return None
        return max(counts.items(), key=lambda kv: kv[1])[0]


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
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if not self.state.active:
            return await interaction.response.send_message(
                "Voting is not currently active.",
                ephemeral=True,
            )

        map_id = self.values[0]
        self.state.record_vote(interaction.user.id, map_id)

        await interaction.response.send_message(
            "Your vote has been recorded.",
            ephemeral=True,
        )

        await self.cog.refresh_active_embed()


class MapVoteView(discord.ui.View):
    def __init__(self, state: VoteState, cog: "MapVote"):
        super().__init__(timeout=None)
        self.add_item(MapVoteSelect(state, cog))


# --------------------------------------------------
# Cog
# --------------------------------------------------

class MapVote(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = VoteState()

        persisted = load_persistent_state()
        self.saved_message_id: int | None = persisted.get("message_id")
        self.saved_channel_id: int | None = persisted.get("channel_id")
        self.mapvote_enabled: bool = persisted.get("mapvote_enabled", True)

        self.vote_view: MapVoteView | None = None

        # log-based detection
        self.last_log_check: datetime = datetime.now(timezone.utc) - timedelta(minutes=2)

    # --------------- persistence ---------------

    def _save_state_file(self):
        data = {
            "message_id": self.saved_message_id,
            "channel_id": self.saved_channel_id,
            "mapvote_enabled": self.mapvote_enabled,
        }
        save_persistent_state(data)

    # --------------- lifecycle ---------------

    def cog_unload(self):
        if self.tick_task.is_running():
            self.tick_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        try:
            await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))
            print("[MapVote] Commands synced.")
        except Exception as e:
            print("[MapVote] Sync error:", e)

        await self.ensure_initial_embed()

        if not self.tick_task.is_running():
            self.tick_task.start()
            print("[MapVote] tick_task started")

    # --------------- broadcasts ---------------

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
            uid = p.get("player_id")
            if not uid:
                continue

            payload = {
                "player_id": uid,
                "message": message,
                "by": "7DRBot",
                "save_message": False,
            }
            _ = rcon_post("message_player", payload)
            await asyncio.sleep(BROADCAST_DELAY)

    # --------------- slash commands ---------------

    @app_commands.command(
        name="force_mapvote",
        description="Force start a map vote for the current match.",
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @mapvote_staff_check()
    async def force_mapvote_cmd(self, interaction: discord.Interaction):
        await interaction.response.send_message("Fetching gamestate…", ephemeral=True)
        gs = await fetch_gamestate()
        if not gs:
            return await interaction.followup.send(
                "❌ Could not read gamestate (CRCON offline?).",
                ephemeral=True,
            )

        status = classify_status(gs, self.mapvote_enabled)
        if status == "STANDBY":
            return await interaction.followup.send(
                "⚠️ Server is in standby (no players, timer not running). "
                "Map voting will start automatically when a player joins.",
                ephemeral=True,
            )

        self.mapvote_enabled = True
        self._save_state_file()

        await self.start_vote(gs)
        await interaction.followup.send(
            "✅ Map vote forced for this match.", ephemeral=True
        )

    @app_commands.command(
        name="mapvote_start",
        description="Enable the automatic map voting system.",
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @mapvote_staff_check()
    async def mapvote_start_cmd(self, interaction: discord.Interaction):
        self.mapvote_enabled = True
        self._save_state_file()
        await interaction.response.send_message(
            "✅ Map voting has been **enabled**. It will run automatically when the server is active.",
            ephemeral=True,
        )
        await self.refresh_status_embed()

    @app_commands.command(
        name="mapvote_stop",
        description="Disable map voting and restore default map rotation.",
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
            ephemeral=True,
        )
        await self.refresh_status_embed()

    # --------------- embed handling ---------------

    def build_embed(self, status: str, gs: dict | None) -> discord.Embed:
        current = (gs or {}).get("current_map_pretty") or "Unknown"
        raw_time = (gs or {}).get("raw_time_remaining") or "0:00:00"
        axis = (gs or {}).get("axis_players") or 0
        allied = (gs or {}).get("allied_players") or 0
        server_name = (gs or {}).get("server_name") or "Unknown server"

        embed = discord.Embed(
            title="🗺️ 7DR Map Voting",
            color=discord.Color.red(),
        )
        # Make each edit unique so Discord displays the update
        embed.timestamp = datetime.now(timezone.utc)
        embed.set_footer(text=server_name)

        if status == "OFFLINE":
            embed.description = (
                "⚠️ **CRCON / API unreachable or server offline.**\n\n"
                "Map voting is currently **offline**.\n"
                "The server will continue using its current map rotation."
            )
            embed.set_image(url=OFFLINE_CDN_IMAGE)

        elif status == "DISABLED":
            embed.description = (
                "⛔ **Map voting is disabled by staff.**\n\n"
                "The server is running the **default map rotation**.\n\n"
                f"**Current map:** {current}\n"
                f"**Players:** Allied `{allied}` — Axis `{axis}`"
            )
            embed.set_image(url=DISABLED_CDN_IMAGE)

        elif status == "STANDBY":
            embed.description = (
                "🕓 **Server is in standby.**\n\n"
                "No players are connected and the round timer is not running.\n"
                "Map voting will **start automatically** when a player joins.\n\n"
                f"**Current map:** {current}\n"
                f"**Match remaining:** `{raw_time}`\n"
                f"**Players:** Allied `{allied}` — Axis `{axis}`"
            )
            embed.set_image(url=STANDBY_CDN_IMAGE)

        elif status == "ACTIVE":
            if self.state.active and self.state.vote_end_at:
                vote_left = (self.state.vote_end_at - datetime.now(timezone.utc)).total_seconds()
                vote_left_str = fmt_vote_secs(vote_left)
            else:
                vote_left_str = "—"

            if self.state.active:
                vote_counts = self.state.get_vote_counts()
                if vote_counts:
                    sorted_votes = sorted(
                        vote_counts.items(),
                        key=lambda x: x[1],
                        reverse=True,
                    )
                    lines = []
                    for map_id, count in sorted_votes:
                        pretty = next((p for p, mid in MAPS.items() if mid == map_id), map_id)
                        lines.append(
                            f"**{pretty}** — {count} vote{'s' if count != 1 else ''}"
                        )
                    votetext = "\n".join(lines)
                else:
                    votetext = "*No votes yet.*"
            else:
                votetext = "*No active vote for this round (either finished or not started yet).*"

            desc = (
                "✅ **Server active** — map voting system enabled.\n\n"
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

            img = MAP_CDN_IMAGES.get(current)
            if img:
                embed.set_image(url=img)
        else:
            embed.description = "Unknown status."
            embed.set_image(url=OFFLINE_CDN_IMAGE)

        return embed


    async def ensure_embed(self, status: str, gs: dict | None) -> discord.Message | None:
        """Ensure there is one live mapvote message, update it and attach view if needed."""
        channel_id = self.saved_channel_id or MAPVOTE_CHANNEL_ID
        channel = self.bot.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            print("[MapVote] Vote channel invalid")
            return None

        embed = self.build_embed(status, gs)

        view = None
        if status == "ACTIVE" and self.state.active and self.state.options:
            if self.vote_view is None:
                self.vote_view = MapVoteView(self.state, self)
            view = self.vote_view

        # Prepare a compact signature of the content we care about to skip redundant edits
        # Include timestamp so per-second updates count as a change
        new_sig = (status, embed.description, embed.image.url if embed.image else None, embed.timestamp)

        # Try to edit using partial message (doesn't require fetch)
        if self.saved_message_id:
            try:
                partial_msg = channel.get_partial_message(self.saved_message_id)
                if getattr(self, "_last_embed_sig", None) != new_sig:
                    await partial_msg.edit(embed=embed, view=view)
                    self._last_embed_sig = new_sig
                self.state.vote_channel = channel
                self.state.vote_message_id = self.saved_message_id
                return partial_msg
            except discord.NotFound:
                print("[MapVote] Saved message not found, will create new one")
                self.saved_message_id = None
            except discord.Forbidden:
                # Fall back to creating a new message so updates don't stall
                print("[MapVote] No permission to edit message, creating new one")
                self.saved_message_id = None
            except Exception as e:
                print(f"[MapVote] Failed to edit via partial message: {e}, creating new")
                self.saved_message_id = None

        # Create new message if needed
        try:
            msg = await channel.send(embed=embed, view=view)
            self.saved_message_id = msg.id
            self.saved_channel_id = channel.id
            self._last_embed_sig = new_sig
            self._save_state_file()
            print(f"[MapVote] Created new embed message: {msg.id}")
            
            self.state.vote_channel = channel
            self.state.vote_message_id = msg.id
            return msg
        except Exception as e:
            print(f"[MapVote] Failed to create new message: {e}")
            return None

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

    # --------------- voting flow ---------------

    async def start_vote(self, gs: dict):
        """Start vote for current match (triggered by Match Start log or /force_mapvote)."""
        self.state.reset_for_match(gs)

        # Build selection pool, excluding current map
        pool = [(p, mid) for p, mid in MAPS.items() if mid != gs["current_map_id"]]
        random.shuffle(pool)
        pool = pool[: min(len(pool), OPTIONS_PER_VOTE, 25)]
        self.state.set_options({p: mid for p, mid in pool})

        await self.ensure_embed("ACTIVE", gs)

        print(f"[MapVote] Vote started for {gs['current_map_pretty']}")
        await self.broadcast_to_all(BROADCAST_START)

    async def end_vote_and_queue(self, gs: dict | None = None):
        """End vote, broadcast result and set next rotation."""
        if gs is None:
            gs = await fetch_gamestate()
        if not gs:
            print("[MapVote] end_vote_and_queue called but gamestate unavailable.")
            self.state.active = False
            await self.refresh_status_embed()
            return

        self.state.active = False
        channel = self.state.vote_channel
        
        # Get the results channel for CRCON responses
        results_channel = self.bot.get_channel(MAPVOTE_RESULTS_CHANNEL_ID)
        if not results_channel:
            print("[MapVote] Results channel not found, falling back to vote channel")
            results_channel = channel
        
        if not channel:
            print("[MapVote] end_vote_and_queue called with no channel")
            await self.refresh_status_embed()
            return

        winner_id = self.state.winner()

        if not winner_id:
            current_id = gs["current_map_id"]
            res = rcon_set_rotation([current_id])
            await self.broadcast_to_all(BROADCAST_NO_VOTES)
            
            # Send result notification to vote channel
            await channel.send("⚖️ **No votes were cast.** The current map has been set as the only map in rotation.")
            
            # Send CRCON response to results channel
            if results_channel:
                await results_channel.send(
                    f"**CRCON Set Rotation Response (No Votes):**\n```{res}```"
                )
            
            print("[MapVote] Vote ended with no votes — locked rotation to current map.")
        else:
            pretty = next((p for p, mid in MAPS.items() if mid == winner_id), winner_id)
            res = rcon_set_rotation([winner_id])
            await self.broadcast_to_all(f"🏆 {pretty} has won the vote!")
            
            # Send result notification to vote channel
            await channel.send(f"🏆 **Winner: {pretty}**\n\nHead over to discord.gg/7drc to cast your vote on the next map!")
            
            # Send CRCON response to results channel
            if results_channel:
                await results_channel.send(
                    f"**CRCON Set Rotation Response ({pretty}):**\n```{res}```"
                )
            
            print(f"[MapVote] Vote ended, winner {pretty}")

        await self.refresh_status_embed()

    # --------------- log-based detection ---------------

    async def check_match_events(self, gs: dict):
        """
        Check CRCON logs for Match Start / Match Ended
        since last_log_check.
        """
        now = datetime.now(timezone.utc)

        if not self.mapvote_enabled:
            self.last_log_check = now
            return

        # ---- Match Start ----
        start_logs = rcon_get_logs("Match Start", limit=10)
        if start_logs and not start_logs.get("failed") and not start_logs.get("error"):
            logs = start_logs.get("result") or []
            newest_start = None
            for log in logs:
                ts = parse_log_timestamp(log.get("timestamp"))
                if ts and ts > self.last_log_check:
                    if newest_start is None or ts > newest_start:
                        newest_start = ts

            if newest_start and not self.state.active:
                print("[MapVote] Detected MATCH START in logs")
                await self.start_vote(gs)

        # ---- Match Ended ----
        end_logs = rcon_get_logs("Match Ended", limit=10)
        if end_logs and not end_logs.get("failed") and not end_logs.get("error"):
            logs = end_logs.get("result") or []
            newest_end = None
            for log in logs:
                ts = parse_log_timestamp(log.get("timestamp"))
                if ts and ts > self.last_log_check:
                    if newest_end is None or ts > newest_end:
                        newest_end = ts

            if newest_end and self.state.active:
                print("[MapVote] Detected MATCH ENDED in logs")
                await self.end_vote_and_queue(gs)

        self.last_log_check = now

    # --------------- background loop ---------------

    @tasks.loop(seconds=EMBED_UPDATE_INTERVAL)
    async def tick_task(self):
        gs = await fetch_gamestate()
        status = classify_status(gs, self.mapvote_enabled)

        if status == "OFFLINE":
            self.state.active = False
            await self.ensure_embed("OFFLINE", None)
            return

        if status == "DISABLED":
            self.state.active = False
            await self.ensure_embed("DISABLED", gs)
            return

        if status == "STANDBY":
            self.state.active = False
            await self.ensure_embed("STANDBY", gs)
            await self.check_match_events(gs)
            return

        # ACTIVE
        await self.ensure_embed("ACTIVE", gs)
        await self.check_match_events(gs)

        # handle countdown if vote active
        if self.state.active and self.state.vote_end_at:
            now = datetime.now(timezone.utc)
            remaining = (self.state.vote_end_at - now).total_seconds()

            if remaining <= 0:
                await self.end_vote_and_queue(gs)
                return

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
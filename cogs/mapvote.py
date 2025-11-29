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

# Role allowed to use the mapvote slash commands
MAPVOTE_ADMIN_ROLE_ID = 1213495462632361994  # <-- PUT YOUR ROLE ID HERE

# Vote ends this many seconds before match end
VOTE_END_OFFSET_SECONDS = 120

# Embed update speed (testing = 1 second)
EMBED_UPDATE_INTERVAL = 1

# How many map options to show
OPTIONS_PER_VOTE = 10

# Pretty name → CRCON ID (map_names)
MAPS = {
    "Elsenborn Ridge Warfare": "elsenbornridge_warfare_day",
    "Carentan Warfare": "carentan_warfare",
    "Foy Warfare": "foy_warfare",
    "Hill 400 Warfare": "hill400_warfare",
}

# Default server map rotation when voting is OFF
DEFAULT_ROTATION = [
    "foy_warfare_day",
    "carentan_warfare_day",
    "hill400_warfare_day",
    "elsenbornridge_warfare_day",
]

# CDN images for maps (by pretty name)
MAP_CDN_IMAGES = {
    "Elsenborn Ridge Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365408158012407840/Elsenborn_Custom_MLL.png",
    "Carentan Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365403110197166191/Carentan_SP_NoHQ.png",
    "Foy Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365404141337186304/Foy_SP_NoHQ.png",
    "Hill 400 Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365404269116919930/Hill400_SP_NoHQ_1.png",
}

# Special CDN images for states (fill these in later)
STANDBY_IMAGE_URL = ""  # when server is empty & timer 0/0
OFFLINE_IMAGE_URL = ""  # when map voting is turned OFF
UNKNOWN_MAP_IMAGE_URL = ""  # fallback if you want for unknown maps

# Broadcast templates (per-player messages)
BROADCAST_START = "🗳️ Next-map voting is OPEN on Discord!"
BROADCAST_ENDING_SOON = "⏳ Vote closes in 2 minutes!"
BROADCAST_NO_VOTES = "No votes, the map rotation wins :("

# --------------------------------------------------
# CRCON API (Bearer token)
# --------------------------------------------------

CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"
CRCON_API_KEY = os.getenv("CRCON_API_KEY")

STATE_FILE = "mapvote_state.json"


def rcon_get(endpoint: str):
    try:
        r = requests.get(
            CRCON_PANEL_URL + endpoint,
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


# --------------------------------------------------
# HELPERS
# --------------------------------------------------


async def get_gamestate():
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
            "current_image_name": cur.get("image_name"),
            "time_remaining": float(res.get("time_remaining") or 0.0),
            "raw_time_remaining": res.get("raw_time_remaining") or "0:00:00",
            "match_time": int(res.get("match_time") or 0),
            "axis_players": int(res.get("num_axis_players") or 0),
            "allied_players": int(res.get("num_allied_players") or 0),
            "server_name": res.get("server_name") or "Unknown Server",
        }
    except Exception as e:
        print("[MapVote] Error parsing gamestate:", e, data)
        return None


def fmt_vote_secs(sec):
    if sec is None:
        return "Unknown"
    sec = max(0, int(sec))
    m = sec // 60
    s = sec % 60
    return f"{m:02d}:{s:02d}"


def load_state_file():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print("[MapVote] Failed to load state file:", e)
        return {}


def save_state_file(data: dict):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print("[MapVote] Failed to save state file:", e)


def app_has_role(role_id: int):
    async def predicate(interaction: discord.Interaction) -> bool:
        user = interaction.user
        if not isinstance(user, discord.Member):
            return False
        return any(r.id == role_id for r in user.roles)

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

        self.options: dict[str, str] = {}  # pretty → map_id
        self.user_votes: dict[int, str] = {}  # user_id → map_id
        self.vote_counts: dict[str, int] = {}  # map_id → int

        self.total_match_length: int | None = None  # seconds

    def reset_for_match(self, gs: dict):
        self.active = True
        self.match_map_id = gs["current_map_id"]
        self.match_map_pretty = gs["current_map_pretty"]

        now = datetime.now(timezone.utc)
        tr = float(gs["time_remaining"] or 0)
        mt = int(gs["match_time"] or 0)

        # dynamic total match length
        total_len = int(tr) + int(mt)
        self.total_match_length = total_len if total_len > 0 else None

        # decide when the vote should end
        if tr > 0:
            end_in = max(0, tr - VOTE_END_OFFSET_SECONDS)
        else:
            if mt > 0:
                end_in = max(0, mt - VOTE_END_OFFSET_SECONDS)
            else:
                end_in = VOTE_END_OFFSET_SECONDS

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
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if not self.state.active or not self.cog.enabled:
            return await interaction.response.send_message(
                "Voting is not currently active.", ephemeral=True
            )

        map_id = self.values[0]
        self.state.record_vote(interaction.user.id, map_id)

        await interaction.response.send_message(
            f"Vote recorded for `{map_id}`", ephemeral=True
        )

        await self.cog.update_main_embed()  # refresh live votes


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

        self.enabled: bool = True  # global toggle
        self.embed_channel_id: int = MAPVOTE_CHANNEL_ID
        self.embed_message_id: int | None = None

        self._load_persistent_state()

    # ---------------- PERSISTENCE ----------------

    def _load_persistent_state(self):
        data = load_state_file()
        self.enabled = data.get("enabled", True)
        self.embed_channel_id = data.get("embed_channel_id", MAPVOTE_CHANNEL_ID)
        self.embed_message_id = data.get("embed_message_id")
        self.last_map_id = data.get("last_map_id")

    def _save_persistent_state(self):
        data = {
            "enabled": self.enabled,
            "embed_channel_id": self.embed_channel_id,
            "embed_message_id": self.embed_message_id,
            "last_map_id": self.last_map_id,
        }
        save_state_file(data)

    # ---------------- DISCORD LIFECYCLE ----------------

    def cog_unload(self):
        if hasattr(self, "tick_task") and self.tick_task.is_running():
            self.tick_task.cancel()

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

    # ---------------- UTILITIES ----------------

    async def ensure_main_message(self):
        """Ensure there is a single embed message we always edit."""
        channel = self.bot.get_channel(self.embed_channel_id)
        if not isinstance(channel, discord.TextChannel):
            print("[MapVote] Map vote channel invalid")
            return None

        self.state.vote_channel = channel

        if self.embed_message_id is not None:
            try:
                msg = await channel.fetch_message(self.embed_message_id)
                self.state.vote_message_id = msg.id
                return msg
            except discord.NotFound:
                print("[MapVote] Stored message not found, creating new one")
                self.embed_message_id = None

        # If we reach here, send a fresh embed
        gs = await get_gamestate()
        embed = self.build_embed(gs)

        # Only show dropdown if enabled & we actually have options (active vote)
        view = self.vote_view if (self.enabled and self.state.active) else None

        msg = await channel.send(embed=embed, view=view)
        self.embed_message_id = msg.id
        self.state.vote_message_id = msg.id
        self._save_persistent_state()
        return msg

    async def broadcast_to_all(self, message: str):
        """Per-player message using get_players + message_player."""
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
            uid = p.get("steam_id_64") or p.get("steam_id") or p.get("player_id")
            if not uid:
                continue

            payload = {"player_id": uid, "message": message}
            _ = rcon_post("message_player", payload)
            await asyncio.sleep(0.1)

    @staticmethod
    def is_standby(gs: dict | None) -> bool:
        """Standby when: timer 0/0 and no players on either team."""
        if not gs:
            return False
        mt = gs["match_time"]
        tr = gs["time_remaining"]
        total_players = gs["axis_players"] + gs["allied_players"]
        return mt == 0 and tr == 0 and total_players == 0

    # ---------------- EMBED ----------------

    def build_embed(self, gs: dict | None) -> discord.Embed:
        now = datetime.now(timezone.utc)

        if not gs:
            status_text = "Unable to read game state."
            status_type = "error"
            current_map = "Unknown"
            raw_time = "0:00:00"
            axis = allied = 0
            vote_left = None
        else:
            current_map = gs["current_map_pretty"] or "Unknown"
            raw_time = gs["raw_time_remaining"]
            axis = gs["axis_players"]
            allied = gs["allied_players"]

            if self.state.vote_end_at and self.state.active and self.enabled:
                vote_left = (self.state.vote_end_at - now).total_seconds()
            else:
                vote_left = None

            if not self.enabled:
                status_type = "offline"
                status_text = "🚫 Map voting is currently **OFFLINE**."
            elif self.is_standby(gs):
                status_type = "standby"
                status_text = "🕒 Server is in **STANDBY** (no players, timer not started)."
            elif self.state.active:
                status_type = "active"
                status_text = "🗳️ Map voting is **ACTIVE**."
            else:
                status_type = "idle"
                status_text = "✅ Map voting is **ENABLED**, waiting for next match."

        # Live votes text
        if self.state.vote_counts and self.state.active and self.enabled:
            sorted_votes = sorted(
                self.state.vote_counts.items(), key=lambda x: x[1], reverse=True
            )
            lines = []
            for map_id, count in sorted_votes:
                pretty = next(
                    (p for p, mid in MAPS.items() if mid == map_id), map_id
                )
                lines.append(
                    f"**{pretty}** — {count} vote{'s' if count != 1 else ''}"
                )
            votetext = "\n".join(lines)
        else:
            votetext = "*No votes yet.*"

        desc_lines = [
            f"**Status:** {status_text}",
            f"**Current map:** {current_map}",
            f"**Match remaining:** `{raw_time}`",
            f"**Players:** Allied: `{allied}` — Axis: `{axis}`",
        ]

        if self.state.active and self.enabled:
            desc_lines.append(
                f"**Vote closes in:** `{fmt_vote_secs(vote_left)}`"
            )

        desc_lines.append("")
        desc_lines.append("**Live votes:**")
        desc_lines.append(votetext)

        embed = discord.Embed(
            title="🗺️ Next Map Vote",
            description="\n".join(desc_lines),
            color=discord.Color.red(),
        )

        # Pick image
        img_url = None
        if not self.enabled and OFFLINE_IMAGE_URL:
            img_url = OFFLINE_IMAGE_URL
        elif gs and self.is_standby(gs) and STANDBY_IMAGE_URL:
            img_url = STANDBY_IMAGE_URL
        elif gs:
            img_url = MAP_CDN_IMAGES.get(current_map)
            if not img_url and UNKNOWN_MAP_IMAGE_URL:
                img_url = UNKNOWN_MAP_IMAGE_URL

        if img_url:
            embed.set_image(url=img_url)

        return embed

    async def update_main_embed(self, gs: dict | None = None):
        if gs is None:
            gs = await get_gamestate()

        msg = await self.ensure_main_message()
        if not msg:
            return

        # Only show dropdown when voting is actually active & enabled
        if self.enabled and self.state.active and self.state.options:
            if not self.vote_view:
                self.vote_view = MapVoteView(self.state, self)
            view = self.vote_view
        else:
            view = None

        try:
            await msg.edit(embed=self.build_embed(gs), view=view)
        except Exception as e:
            print("[MapVote] Failed to update main embed:", e)

    # ---------------- VOTE LIFECYCLE ----------------

    async def start_vote(self, gs: dict):
        """Start a fresh vote for the current match (if enabled)."""
        if not self.enabled:
            return

        channel = self.bot.get_channel(MAPVOTE_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            print("[MapVote] Vote channel invalid")
            return

        self.state.vote_channel = channel
        self.state.reset_for_match(gs)

        # Build options excluding current map
        pool = [
            (p, mid)
            for p, mid in MAPS.items()
            if mid != gs["current_map_id"]
        ]
        random.shuffle(pool)
        pool = pool[: min(len(pool), OPTIONS_PER_VOTE, 25)]
        self.state.set_options({p: mid for p, mid in pool})

        self.vote_view = MapVoteView(self.state, self)
        print(f"[MapVote] Vote started for {gs['current_map_pretty']}")

        await self.broadcast_to_all(BROADCAST_START)
        await self.update_main_embed(gs)
        self._save_persistent_state()

    async def end_vote_and_queue(self, gs: dict):
        """End vote and set rotation appropriately."""
        self.state.active = False
        channel = self.state.vote_channel

        if not gs:
            gs = await get_gamestate()

        current_map_id = gs["current_map_id"] if gs else None
        winner_id = self.state.winner()

        # Decide new rotation
        if winner_id:
            pretty = next(
                (p for p, mid in MAPS.items() if mid == winner_id), winner_id
            )
            payload = {"map_names": [winner_id]}
            result = rcon_post("set_map_rotation", payload)

            if channel:
                await channel.send(
                    f"🏆 **Winner: {pretty}**\n"
                    f"Rotation set to this map only.\n"
                    f"CRCON Response:\n```{result}```"
                )
            await self.broadcast_to_all(f"{pretty} has won the vote!")
            print(f"[MapVote] Vote ended, winner {pretty}")
        else:
            # No votes: keep current map forever (single-entry rotation)
            if current_map_id:
                payload = {"map_names": [current_map_id]}
                result = rcon_post("set_map_rotation", payload)
                if channel:
                    await channel.send(
                        "No votes — current map will repeat.\n"
                        f"CRCON Response:\n```{result}```"
                    )
            await self.broadcast_to_all(BROADCAST_NO_VOTES)
            print("[MapVote] Vote ended, no winner")

        await self.update_main_embed(gs)
        self._save_persistent_state()

    # ---------------- SLASH COMMANDS ----------------

    @app_commands.command(
        name="force_mapvote",
        description="Force start a map vote for the current match",
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_has_role(MAPVOTE_ADMIN_ROLE_ID)
    async def force_mapvote_cmd(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Fetching gamestate…", ephemeral=True
        )
        gs = await get_gamestate()
        if not gs:
            return await interaction.followup.send(
                "❌ Could not read gamestate.", ephemeral=True
            )

        if self.is_standby(gs):
            return await interaction.followup.send(
                "Server is in standby (no timer/players). Map voting will start when a match actually begins.",
                ephemeral=True,
            )

        await self.start_vote(gs)
        await interaction.followup.send("Vote started!", ephemeral=True)

    @app_commands.command(
        name="mapvote_stop",
        description="Turn OFF map voting and restore default rotation",
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_has_role(MAPVOTE_ADMIN_ROLE_ID)
    async def mapvote_stop_cmd(self, interaction: discord.Interaction):
        self.enabled = False
        self.state.active = False

        # Restore default rotation
        result = rcon_post("set_map_rotation", {"map_names": DEFAULT_ROTATION})

        gs = await get_gamestate()
        await self.update_main_embed(gs)

        self._save_persistent_state()

        await interaction.response.send_message(
            f"Map voting turned **OFF**. Default rotation restored.\n```{result}```",
            ephemeral=True,
        )

    @app_commands.command(
        name="mapvote_start",
        description="Turn ON map voting (auto for each match)",
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_has_role(MAPVOTE_ADMIN_ROLE_ID)
    async def mapvote_start_cmd(self, interaction: discord.Interaction):
        self.enabled = True
        self._save_persistent_state()

        gs = await get_gamestate()
        if gs and not self.is_standby(gs):
            # Start vote immediately if a match is running
            await self.start_vote(gs)
        else:
            await self.update_main_embed(gs)

        await interaction.response.send_message(
            "Map voting turned **ON**. It will run each match while the server is active.",
            ephemeral=True,
        )

    # ---------------- BACKGROUND LOOP ----------------

    @tasks.loop(seconds=EMBED_UPDATE_INTERVAL)
    async def tick_task(self):
        gs = await get_gamestate()
        await self.ensure_main_message()  # make sure we always have the embed

        if not gs:
            await self.update_main_embed(None)
            return

        standby = self.is_standby(gs)

        # Track last_map_id to detect new matches
        if self.last_map_id is None:
            self.last_map_id = gs["current_map_id"]

        # If map changed → new match
        if (
            gs["current_map_id"] != self.last_map_id
            and self.enabled
            and not standby
        ):
            print(f"[MapVote] New match detected: {gs['current_map_pretty']}")
            await self.start_vote(gs)
            self.last_map_id = gs["current_map_id"]
            self._save_persistent_state()
            await self.update_main_embed(gs)
            return

        # If voting disabled, just keep embed in OFFLINE state
        if not self.enabled:
            self.state.active = False
            await self.update_main_embed(gs)
            return

        # If server is in standby, stop active vote and show standby state
        if standby:
            if self.state.active:
                print("[MapVote] Entering standby, stopping vote")
                self.state.active = False
            await self.update_main_embed(gs)
            return

        # If enabled & not standby & no active vote yet, start one
        if not self.state.active:
            await self.start_vote(gs)
            self.last_map_id = gs["current_map_id"]
            self._save_persistent_state()
            return

        # If we get here, active vote is running → update embed & timers
        await self.update_main_embed(gs)

        now = datetime.now(timezone.utc)
        remaining = (
            (self.state.vote_end_at - now).total_seconds()
            if self.state.vote_end_at
            else None
        )
        if remaining is None:
            return

        # Warning at 2 minutes left
        if remaining <= 120 and not self.state.warning_sent:
            self.state.warning_sent = True
            await self.broadcast_to_all(BROADCAST_ENDING_SOON)
            if self.state.vote_channel:
                await self.state.vote_channel.send(
                    "⏳ Vote closes in 2 minutes!"
                )

        # End vote
        if remaining <= 0:
            await self.end_vote_and_queue(gs)

    @tick_task.before_loop
    async def before_tick(self):
        print(
            "[MapVote] Waiting until bot is ready before starting tick_task..."
        )
        await self.bot.wait_until_ready()
        await self.ensure_main_message()
        print("[MapVote] Bot ready, tick_task will now run.")


async def setup(bot: commands.Bot):
    await bot.add_cog(MapVote(bot))

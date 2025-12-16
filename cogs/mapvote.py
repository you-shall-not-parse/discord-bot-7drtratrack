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
MAPVOTE_LOG_CHANNEL_ID = 1279831955935854712

# Role that can use /mapvote_* commands (plus admins)
MAPVOTE_ADMIN_ROLE_ID = 1279832920479109160  # set this to your role ID

# Vote ends this many seconds before match end
VOTE_END_OFFSET_SECONDS = 120

# Embed update speed
EMBED_UPDATE_INTERVAL = 2

# How many map options to show
OPTIONS_PER_VOTE = 20

# Persistent state file (message id, enabled flag, etc.)
MAPVOTE_STATE_FILE = "mapvote_state.json"

# Pretty name → CRCON ID
MAPS = {
    "Elsenborn Ridge Warfare": "elsenbornridge_warfare_morning",
    "Carentan Warfare": "carentan_warfare",
    "Foy Warfare": "foy_warfare",
    "Hill 400 Warfare": "hill400_warfare",
    "St. Marie Du Mont Warfare": "stmariedumont_warfare",
    "Utah Beach Warfare": "utahbeach_warfare",
    "St. Mere Eglise Warfare": "stmereeglise_warfare",
    "El Alamein Warfare": "elalamein_warfare",
    "Mortain Warfare": "mortain_warfare_dusk",
    "Smolensk Warfare (Dusk)": "smolensk_warfare_dusk",
    "Driel Warfare": "driel_warfare",
    "Kursk Warfare": "kursk_warfare",
    "Carentan Warfare (Night)": "carentan_warfare_night",
    "Hurtgen Forest Warfare": "hurtgenforest_warfare_V2",
    "Remagen Warfare": "remagen_warfare",
    "Omaha Beach Warfare": "omahabeach_warfare",
    "Kharkov Warfare": "kharkov_warfare",
    "Purple Heart Lane Warfare (Rain)": "PHL_L_1944_Warfare",
    "Tobruk Warfare (Dawn)": "tobruk_warfare_morning",
    "Stalingrad Warfare": "STA_L_1942_Warfare",
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
    "Foy Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1444492145913499800/ChatGPT_Image_Nov_30_2025_12_55_43_AM.png?ex=69400564&is=693eb3e4&hm=b9c95afd2e8cb88158af73e707f8dbae744e4458be20369029dd92e8a8a467ab",
    "Hill 400 Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1444497579210707004/ChatGPT_Image_Nov_30_2025_01_15_52_AM.png?ex=69382174&is=6936cff4&hm=f9e16ba8d2b9f20dd799bd5970c11f38c1f427689585e2d139cfd1294888a612",
    "St. Marie Du Mont Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1444515451727253544/file_00000000e5f871f488f94dd458b30c09.png?ex=69383219&is=6936e099&hm=40998a104cbffc2fe0b37c515f6158c9722606b7c1ec5d33bdc03e5eb4341e2a",
    "Utah Beach Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1449831598160740402/ChatGPT_Image_Dec_14_2025_06_32_36_PM.png?ex=69405465&is=693f02e5&hm=ec9dbcc1d930df308756a775714ce19d26bebf261a42f384d20af05dc0014004",
    "St. Mere Eglise Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1447681599117463692/file_000000009b64720e96132fbd67f95f72.png?ex=6938820d&is=6937308d&hm=148aca7f2e9de99f00b1f2cb6c55660ae5ece263e62afa83fbece2f9193610ef",
    "El Alamein Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1448462224795373588/file_00000000627c71f4bbc1994fb582be8c.png?ex=693ff651&is=693ea4d1&hm=e6096c26fb8a2c74e9347ebd8477d3b5956521829486e7b192e18f92cffe8830",
    "Mortain Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1448462040632004802/76807A80-FA7B-4965-9A21-0798CEA11042.png?ex=693ff625&is=693ea4a5&hm=3a05171a2a203ba1487a324a893829466e68342cebd2659215d53ab9bc93f4b4",
    "Smolensk Warfare (Dusk)": "https://cdn.discordapp.com/attachments/1098976074852999261/1449390736989491363/file_0000000022f071f4a9771a3645023ed5.png?ex=69400b50&is=693eb9d0&hm=5d2d3dffc888d136aacd11c3525e1e3070907f147277785651ef3c79ee2dae7f&",
    "Driel Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1444671257730744360/file_00000000d254720eb1ce02f6506ae926.png?ex=69381a74&is=6936c8f4&hm=e2772de15b5aa855d3abad443e614d5b2280f7a4f529aaf759f515c70d3ca7cc&",
    "Kursk Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1449501011214598214/Screenshot_20251213_221442_Discord.jpg?ex=693fc943&is=693e77c3&hm=a80dc5533d1f73573ea6d3b0bb1adfa1f51cbd936d81a3fefd5535a1fd3dce67",
    "Carentan Warfare (Night)": "https://cdn.discordapp.com/attachments/1098976074852999261/1449390736410939574/file_0000000083ec72468f8a73042c9f9913.png?ex=69400b4f&is=693eb9cf&hm=48754f26b1b1d209ac351b795e906663f0e9c09d2cd21f6e470d8f72970b9005&",
    "Hurtgen Forest Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1444676650653450411/file_000000005384720e8f124201b4e379a9.png?ex=69381f7a&is=6936cdfa&hm=e2d5ea8302bfd2744a5be5a199388945c8eb60218216aae29a5b2ea71aa1e302",
    "Remagen Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1449390736003960889/file_00000000aa3071f492f35b0111fed5e2.png?ex=69400b4f&is=693eb9cf&hm=d776d5f87f3d73a1b1fdcb782c3204a29a055677368edfbc1aac18e04f53bc94&",
    "Omaha Beach Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1448106330052362301/ChatGPT_Image_Dec_10_2025_12_16_56_AM.png?ex=693a0d9d&is=6938bc1d&hm=6614c98b63a7c58eaea7638a718ef854e5c074796001808cb6faf0557b46ea2a",
    "Kharkov Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1444687960845979780/file_0000000068b47208b053f27323047cda.png?ex=69382a02&is=6936d882&hm=5c7745f15e886825b5b26d3ed4b18a33808332cd2dbedc71e5dba0f8bd9bda8c&",
    "Purple Heart Lane Warfare (Rain)": "https://cdn.discordapp.com/attachments/1098976074852999261/1442258185137295380/file_000000009ba871f4b7700cb80af3a3f3.png?ex=6937e4db&is=6936935b&hm=ffcf7d5e580476b6af6f2c5a1a1055ed656aa86034c14094d9434b0d2019f8cc&g",
    "Tobruk Warfare (Dawn)": "https://cdn.discordapp.com/attachments/1098976074852999261/1449390737593602259/file_00000000735871f4bb2cbbbced7ffbf7.png?ex=69400b50&is=693eb9d0&hm=5ec261995e8bb89a059a686f41ef8da731a5cbdd44dddb4bc356ddec9f368309&",
    "Stalingrad Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1449396751206191364/file_00000000d4c871f4ac3d6d200f6a92ca_1.png?ex=694010e9&is=693ebf69&hm=1a90a0b6c9af30b6d400cc70d89d36ad778d88fb759d125abffc669b8511acf2&",
}

STANDBY_CDN_IMAGE = "https://cdn.discordapp.com/attachments/1098976074852999261/1450604361586573373/from_the_Johnsons_20251216_214335_0000.png?ex=69432416&is=6941d296&hm=84f8e8e5d85b663d1a1547383afe29a1d48461140b104598ac9f3036026f5698&"
OFFLINE_CDN_IMAGE = "https://cdn.discordapp.com/attachments/1098976074852999261/1444486531531280505/ChatGPT_Image_Nov_30_2025_12_33_09_AM.png?ex=6938172a&is=6936c5aa&hm=b08120d9cf51a7bf212e0926cb12036c429d6287a7b542fc8f4bc3b1aac36017"
DISABLED_CDN_IMAGE = "https://cdn.discordapp.com/attachments/1098976074852999261/1444486531531280505/ChatGPT_Image_Nov_30_2025_12_33_09_AM.png?ex=6938172a&is=6936c5aa&hm=b08120d9cf51a7bf212e0926cb12036c429d6287a7b542fc8f4bc3b1aac36017"

# Broadcasts into game to all players
BROADCAST_START = "Vote for the next map on discord.gg/7drc!\nYou can select one of up to 25 maps!\n\nJoin us now as a recruit or just join as a Blueberry to keep up to date with the latest news, map vote and see our kill feed!"
BROADCAST_ENDING_SOON = "Map vote closes in 2 minutes!\n\nHead over to discord.gg/7drc to cast your vote!\n\nJoin us now as a recruit or just join as a Blueberry to keep up to date with the latest news, map vote and see our kill feed!"
BROADCAST_NO_VOTES = "No votes, the map rotation wins.\n\nHead over to discord.gg/7drc to cast your vote!\n\nJoin us now as a recruit or just join as a Blueberry to keep up to date with the latest news, map vote and see our kill feed!"
BROADCAST_50 = "Vote ended prematurely due to 5-0. The next map is {next_map}."

# --------------------------------------------------
# CRCON API (Bearer token)
# -----------------------------------------------

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


def rcon_get_recent_logs(filter_actions: list[str], limit: int = 100):
    """Get recent logs filtered by action types."""
    params = "&".join([f"filter_action={action}" for action in filter_actions])
    endpoint = f"get_recent_logs?{params}&limit={limit}"
    return rcon_get(endpoint)


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
    if not data or data.get("failed") or data.get("error"):
        print("[MapVote] Gamestate read failed:", data)
        return None

    res = data.get("result", {})
    cur = res.get("current_map", {})

    try:
        return {
            "current_map_id": cur.get("id"),
            "current_map_pretty": cur.get("pretty_name"),
            "current_image_name": cur.get("image_name"),
            "time_remaining": float(res.get("time_remaining", 0)),
            "raw_time_remaining": res.get("raw_time_remaining", "0:00:00"),
            "axis_players": int(res.get("num_axis_players", 0)),
            "allied_players": int(res.get("num_allied_players", 0)),
            "axis_score": int(res.get("axis_score", 0)),
            "allied_score": int(res.get("allied_score", 0)),
            "server_name": res.get("server_name", "Unknown server"),
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

    def winners_tied(self) -> list[str]:
        """Return all map_ids tied for the highest vote count."""
        if not self.vote_counts:
            return []
        top = max(self.vote_counts.values())
        return [mid for mid, cnt in self.vote_counts.items() if cnt == top]


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
        self.last_processed_log_id: int | None = persisted.get("last_processed_log_id")
        # If previously stored small incremental IDs, reset to None so we don't skip timestamp_ms logs
        if self.last_processed_log_id and self.last_processed_log_id < 10_000_000_000:
            self.last_processed_log_id = None
        self.last_warning_msg_id: int | None = None
        self.last_winner_msg_id: int | None = None

        # UI view
        self.vote_view: MapVoteView | None = None
        # Serialize edits to prevent races that can trigger reposts
        self._embed_lock = asyncio.Lock()
        # Cooldown to avoid immediate re-posts if Discord returns stale fetch
        self._last_create_ts: float | None = None

    # ---------------- Persistence helpers ----------------

    def _save_state_file(self):
        data = {
            "message_id": self.saved_message_id,
            "channel_id": self.saved_channel_id,
            "mapvote_enabled": self.mapvote_enabled,
            "last_processed_log_id": self.last_processed_log_id,
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

        # If we have an old message saved, delete it and clear state
        try:
            if self.saved_channel_id and self.saved_message_id:
                channel = self.bot.get_channel(self.saved_channel_id)
                if isinstance(channel, discord.TextChannel):
                    try:
                        old_msg = await channel.fetch_message(self.saved_message_id)
                        await old_msg.delete()
                        print(f"[MapVote] Deleted old embed message {self.saved_message_id}")
                    except discord.NotFound:
                        pass
                    except Exception as e:
                        print(f"[MapVote] Failed to delete old embed: {e}")
            # Clear saved IDs so a fresh message is created
            self.saved_message_id = None
            self.saved_channel_id = MAPVOTE_CHANNEL_ID
            self._save_state_file()
        except Exception as e:
            print(f"[MapVote] Error while clearing old embed: {e}")

        # Ensure initial embed exists in some state (fresh)
        await self.ensure_initial_embed()

        # Force-start vote if match is active and no vote running after a restart
        try:
            gs = await fetch_gamestate()
            if gs and self.mapvote_enabled:
                status = classify_status(gs, self.mapvote_enabled)
                if status == "ACTIVE" and not self.state.active and gs.get("time_remaining", 0) > 0:
                    await self.start_vote(gs)
        except Exception as e:
            print(f"[MapVote] on_ready auto-start error: {e}")

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
            await asyncio.sleep(0.1)

    # --------------------------------------------------
    # Slash commands
    # --------------------------------------------------

    @app_commands.command(
        name="mapvote_enable",
        description="Enable map voting (starts immediately if a match is active)."
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @mapvote_staff_check()
    async def mapvote_enable_cmd(self, interaction: discord.Interaction):
        # If already enabled, still force-start vote if a match is active and no vote running
        await interaction.response.send_message("Enabling map voting…", ephemeral=True)

        self.mapvote_enabled = True
        self._save_state_file()

        gs = await fetch_gamestate()
        if gs:
            status = classify_status(gs, self.mapvote_enabled)
            if status == "ACTIVE" and gs.get("time_remaining", 0) > 0:
                if not self.state.active:
                    await self.start_vote(gs)
                    await interaction.followup.send(
                        "✅ Map voting enabled and vote started for the current match.",
                        ephemeral=True
                    )
                    return
                else:
                    await interaction.followup.send(
                        "✅ Map voting enabled. A vote is already active.",
                        ephemeral=True
                    )
                    return

        await interaction.followup.send(
            "✅ Map voting enabled. It will start automatically when the next match begins.",
            ephemeral=True
        )
        await self.refresh_status_embed()

    @app_commands.command(
        name="mapvote_disable",
        description="Disable map voting and restore default map rotation."
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @mapvote_staff_check()
    async def mapvote_disable_cmd(self, interaction: discord.Interaction):
        if not self.mapvote_enabled:
            return await interaction.response.send_message(
                "⚠️ Map voting is already disabled.",
                ephemeral=True
            )
        
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

        # Extract gamestate values with defaults
        current = gs.get("current_map_pretty", "Unknown") if gs else "Unknown"
        raw_time = gs.get("raw_time_remaining", "0:00:00") if gs else "0:00:00"
        axis = gs.get("axis_players", 0) if gs else 0
        allied = gs.get("allied_players", 0) if gs else 0
        axis_score = gs.get("axis_score", 0) if gs else 0
        allied_score = gs.get("allied_score", 0) if gs else 0
        server_name = gs.get("server_name", "Unknown server") if gs else "Unknown server"

        # Base embed
        embed = discord.Embed(
            title="🗺️ 7DR Map Voting",
            color=discord.Color.red(),
            timestamp=now
        )
        embed.set_footer(text=server_name)

        # Status-specific description + image
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
                f"**Score:** Allied `{allied_score}` — Axis `{axis_score}`\n"
                f"**Players:** Allied `{allied}` — Axis `{axis}`"
            )
            embed.set_image(url=DISABLED_CDN_IMAGE)

        elif status == "STANDBY":
            embed.description = (
                "🕓 **Server is in standby.**\n\n"
                "No players are in the game and the match timer is not running.\n"
                "Map voting will **start automatically** when a player joins "
                "and when the match timer begins.\n\n"
                f"**Incoming map:** {current}\n"
                f"**Time remaining:** `{raw_time}`\n"
                f"**Score:** Allied `{allied_score}` — Axis `{axis_score}`\n"
                f"**Players:** Allied `{allied}` — Axis `{axis}`"
            )
            embed.set_image(url=STANDBY_CDN_IMAGE)

        elif status == "ACTIVE":
            # Calculate vote time remaining
            vote_left_str = "—"
            if self.state.active and self.state.vote_end_at:
                vote_left = (self.state.vote_end_at - now).total_seconds()
                vote_left_str = fmt_vote_secs(vote_left)

            # Format live votes
            votetext = self._format_vote_results()

            desc = (
                f"✅ **Server active** — map voting available!\n\n"
                f"**Current map:** {current}\n"
                f"**Time remaining:** `{raw_time}`\n"
                f"**Score:** Allied `{allied_score}` — Axis `{axis_score}`\n"
                f"**Players:** Allied `{allied}` — Axis `{axis}`\n"
            )

            if self.state.active and self.state.vote_end_at:
                desc += f"**Vote closes in:** `{vote_left_str}`\n\n"
            else:
                desc += "\n"

            desc += f"**Live votes:**\n{votetext}"
            embed.description = desc

            # Set map image
            img = MAP_CDN_IMAGES.get(current)
            if img:
                embed.set_image(url=img)

        else:
            embed.description = "Unknown status."
            embed.set_image(url=OFFLINE_CDN_IMAGE)

        return embed

    def _format_vote_results(self) -> str:
        """Format current vote results into a readable string."""
        if not self.state.active:
            return "*No active vote for this match (either finished or not started yet).*"
        
        if not self.state.vote_counts:
            return "*No votes yet.*"
        
        sorted_votes = sorted(
            self.state.vote_counts.items(),
            key=lambda x: x[1],
            reverse=True
        )
        lines = []
        for map_id, count in sorted_votes:
            pretty = next((p for p, mid in MAPS.items() if mid == map_id), map_id)
            lines.append(f"**{pretty}** — {count} vote{'s' if count != 1 else ''}")
        
        return "\n".join(lines)

    async def ensure_embed(self, status: str, gs: dict | None) -> discord.Message | None:
        """Ensure the mapvote embed exists and is updated in place."""
        async with self._embed_lock:
            channel_id = self.saved_channel_id or MAPVOTE_CHANNEL_ID
            channel = self.bot.get_channel(channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                print("[MapVote] Vote channel invalid")
                return None

            msg = None
            if self.saved_message_id:
                try:
                    msg = await channel.fetch_message(self.saved_message_id)
                except discord.NotFound:
                    # Truly gone, allow re-creation below
                    msg = None
                except discord.HTTPException as e:
                    # Transient API error — skip this tick to avoid reposting
                    print("[MapVote] Fetch message HTTP error; will retry next tick:", e)
                    return None
                except Exception as e:
                    # Unknown transient error — do not recreate
                    print("[MapVote] Failed to fetch existing mapvote message:", e)
                    return None

            embed = self.build_embed(status, gs)

            # Attach view only when voting is active
            view = None
            if status == "ACTIVE" and self.state.active and self.state.options:
                if self.vote_view is None:
                    self.vote_view = MapVoteView(self.state, self)
                view = self.vote_view

            if msg is None:
                # Creation cooldown: avoid rapid double-creates (e.g., overlapping ticks)
                now_ts = asyncio.get_event_loop().time()
                if self._last_create_ts and (now_ts - self._last_create_ts) < 5:
                    # Recently created; skip re-creating
                    return None

                try:
                    msg = await channel.send(embed=embed, view=view)
                except Exception as e:
                    print("[MapVote] Failed to send mapvote message:", e)
                    return None

                self.saved_message_id = msg.id
                self.saved_channel_id = channel.id
                self._last_create_ts = now_ts
                self._save_state_file()
            else:
                try:
                    await msg.edit(embed=embed, view=view)
                except discord.HTTPException as e:
                    # Skip on transient edit errors (do not repost)
                    print("[MapVote] Failed to edit mapvote message (HTTP):", e)
                    return None
                except Exception as e:
                    print("[MapVote] Failed to edit mapvote message:", e)
                    return None

            # Update state references
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
        # Cleanup transient messages from previous match
        await self._cleanup_transient_messages()

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

    async def _cleanup_transient_messages(self):
        """Delete warning and winner messages from previous match."""
        if not self.state.vote_channel:
            return
        
        for msg_id in [self.last_warning_msg_id, self.last_winner_msg_id]:
            if msg_id:
                try:
                    msg = await self.state.vote_channel.fetch_message(msg_id)
                    await msg.delete()
                except discord.NotFound:
                    pass
                except Exception as e:
                    print(f"[MapVote] Failed to delete message {msg_id}: {e}")
        
        self.last_warning_msg_id = None
        self.last_winner_msg_id = None

    async def end_vote_and_queue(self, gs: dict, premature: bool = False):
        """End the current vote and update map rotation."""
        self.state.active = False
        channel = self.state.vote_channel
        if not channel:
            print("[MapVote] end_vote_and_queue called with no channel")
            return

        # Resolve log channel for CRCON responses
        log_channel = self.bot.get_channel(MAPVOTE_LOG_CHANNEL_ID)
        if not isinstance(log_channel, discord.TextChannel):
            log_channel = channel  # Fallback to vote channel

        winner_id = None
        tied = self.state.winners_tied()
        if not tied:
            # No votes: use default rotation
            res = rcon_set_rotation(DEFAULT_ROTATION)

            await self.broadcast_to_all(BROADCAST_NO_VOTES)
            await log_channel.send(f"CRCON Response (restored default rotation - no votes):\n```{res}```")
            print("[MapVote] Vote ended with no votes — restored default rotation.")
        else:
            if len(tied) == 1:
                winner_id = tied[0]
                pretty = next((p for p, mid in MAPS.items() if mid == winner_id), winner_id)
                res = rcon_set_rotation([winner_id])

                await self.broadcast_to_all(f"{pretty} has won the vote!\nHead over to discord.gg/7drc to cast your vote on the next map!")
                winner_msg = await channel.send(
                    f"🏆 **Winner: {pretty}**\n"
                    f"The next rotation has been set to this map only."
                )
                self.last_winner_msg_id = winner_msg.id

                await log_channel.send(f"CRCON Response (set rotation to winner):\n```{res}```")
                print(f"[MapVote] Vote ended, winner {pretty}")
            else:
                # Tie: choose a random winner from tied maps and announce tie
                winner_id = random.choice(tied)
                pretty_winner = next((p for p, mid in MAPS.items() if mid == winner_id), winner_id)
                pretty_tied = [next((p for p, mid in MAPS.items() if mid == mid_t), mid_t) for mid_t in tied]

                res = rcon_set_rotation([winner_id])

                await self.broadcast_to_all(f"Tie detected! {pretty_winner} was randomly selected as the next map.")
                winner_msg = await channel.send(
                    "🤝 **Tie detected!**\n"
                    f"Tied maps: {', '.join(pretty_tied)}\n"
                    f"🎲 Randomly selected winner: **{pretty_winner}**\n"
                    f"The next rotation has been set to this map only."
                )
                self.last_winner_msg_id = winner_msg.id

                await log_channel.send(f"CRCON Response (tie - set rotation to random winner):\n```{res}```")
                print(f"[MapVote] Tie among {pretty_tied}. Random winner: {pretty_winner}")

        # If the match ended prematurely, send BROADCAST_50 after 20 seconds
        # if premature and winner_id:
        #    pretty_winner = next((p for p, mid in MAPS.items() if mid == winner_id), winner_id)
        #    await asyncio.sleep(20)  # Wait 20 seconds
        #    await self.broadcast_to_all(BROADCAST_50.format(next_map=pretty_winner))

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

        # ACTIVE - check audit logs for match events and handle voting
        await self.check_match_events(gs)

        # Update embed
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
                    warn_msg = await self.state.vote_channel.send("⏳ Vote closes in 2 minutes!")
                    self.last_warning_msg_id = warn_msg.id

    async def check_match_events(self, gs: dict):
        """Check audit logs for match start/end events."""
        logs_data = rcon_get_recent_logs(["Match Start", "Match Ended", "Match"], limit=100)
        if not logs_data or logs_data.get("error") or logs_data.get("failed"):
            return

        logs = logs_data.get("result", {}).get("logs", [])
        if not logs:
            return

        # Use timestamp_ms as stable ordering/identifier
        logs.sort(key=lambda x: x.get("timestamp_ms", 0))

        for log in logs:
            # Fallback to timestamp_ms when 'id' is not present
            log_id = log.get("timestamp_ms") or log.get("id") or 0
            action = (log.get("action") or "").strip().upper()

            # Skip already processed logs
            if self.last_processed_log_id and log_id <= self.last_processed_log_id:
                continue

            # Update last processed ID first to avoid double-processing
            self.last_processed_log_id = log_id
            self._save_state_file()

            # Normalize actions: API returns "MATCH START"/"MATCH ENDED" (sometimes "MATCH")
            if action in ("MATCH START", "MATCH"):
                if not self.state.active and self.mapvote_enabled and gs:
                    print(f"[MapVote] MATCH START detected (#{log_id})")
                    await self.start_vote(gs)
            elif action == "MATCH ENDED":
                print(f"[MapVote] MATCH ENDED detected (#{log_id})")
                if self.state.active:
                    # End the vote immediately when the match ends
                    await self.end_vote_and_queue(gs, premature=True)

    @tick_task.before_loop
    async def before_tick(self):
        print("[MapVote] Waiting until bot is ready before starting tick_task...")
        await self.bot.wait_until_ready()
        print("[MapVote] Bot ready, tick_task will now run.")

async def setup(bot: commands.Bot):
    await bot.add_cog(MapVote(bot))
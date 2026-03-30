import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import asyncio
import logging
import random
import re
import aiohttp
from typing import List, Optional
from urllib.parse import parse_qs, unquote, urlparse

from data_paths import data_path

# Set up logging (always minimal)
# Removed VERBOSE_LOGGING, enforce ERROR level
logging_level = logging.ERROR
logging.basicConfig(level=logging_level, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('GameMonCog')
logger.setLevel(logging_level)

# Silence chatty discord loggers unconditionally
logging.getLogger('discord.gateway').setLevel(logging.ERROR)
logging.getLogger('discord.client').setLevel(logging.ERROR)
logging.getLogger('discord.http').setLevel(logging.ERROR)

# ---------------- CONFIG ----------------
GUILD_ID = 1097913605082579024   # Replace with your guild/server ID
THREAD_ID = 1412934277133369494  # replace with your thread ID
# If set, Hell Let Loose posts are routed here; other games go to THREAD_ID.
# Can be a text channel or thread ID.
HLL_CHANNEL_ID = 1099090838203666474
# Users with any of these roles will never generate GameMon posts.
# Put role IDs in this list, e.g. [123, 456]. Leave empty to disable.
EXCLUDED_ROLE_IDS: list[int] = [1098206797900284035, 1103762811491975218]
IGNORED_GAMES = ["Spotify", "Discord", "Pornhub", "Netflix", "Disney", "Sky TV", "Youtube", "RedTube"]

# For custom image links: Discord embeds generally require a *direct* image URL.
DIRECT_IMAGE_EXTENSIONS = (".gif", ".png", ".jpg", ".jpeg", ".webp")

# Per-game GIFs (URLs). Keys are treated case-insensitively.
# (Recommended: use lowercase normalized game names.)
# Add more games/URLs here.
GAME_GIF_URLS = {
    "Hell Let Loose": [
        "https://media.tenor.com/uwluoIbniJwAAAAd/hell-let-loose-hll.gif",
        "https://media.tenor.com/6j5fK6jPtegAAAAd/arty-hell-let-loose.gif",
        "https://media.tenor.com/N_O18E66mKQAAAAd/hell-let-loose-flamethrower.gif",
        "https://media.tenor.com/qMxHeWaGylQAAAAd/kek-m60.gif",
        "https://media.tenor.com/GEYZLiYatRoAAAAC/hell-let-loose-hll.gif",
        "https://media.tenor.com/bO2URHoai5kAAAAC/krrc-hell-let-loose.gif",
        "https://media.tenor.com/wTO4Un397JsAAAAC/arti-artillery.gif",
        "https://media.tenor.com/HDXJEh0lusgAAAAC/band-of-brothers-hll.gif",
        "https://media.tenor.com/0mZvkVi5HzkAAAAd/help-medic.gif",
        "https://media.tenor.com/PUh8QHHGQ7IAAAAC/hell-let.gif",
        "https://media.tenor.com/luLMy2KSgOMAAAAd/hell-let-loose.gif"
    ],
}

# If True, put the GIF in the embed thumbnail; if False, use the main image.
GIF_AS_THUMBNAIL = True

PREFS_FILE = data_path("game_prefs.json")
FEED_STATE_FILE = data_path("game_feed_state.json")
DEFAULT_PREFERENCE = "opt_in"  # Default preference for users (opt_in or opt_out)
# Admin-only slash commands are gated by this role ID.
ADMIN_ROLE_ID = 1213495462632361994
ADMIN_USER_IDS = [1109147750932676649]  # Replace with your admin user IDs who can use special commands
TEMP_DISABLE_DEFAULT_MONITORING = False  # Set to True to temporarily disable all monitoring for users without explicit preferences
# Throttle: minimum seconds between feed posts (global debounce)
FEED_POST_MIN_INTERVAL = 5  # increase if still rate-limited
# Prune: keep only the newest N messages in the thread (excluding pinned)
KEEP_LAST_MESSAGES = 20
# How many messages beyond KEEP_LAST_MESSAGES to fetch per prune pass
PRUNE_EXTRA_FETCH = 50

SQUAD_SUFFIX = "and is looking for a squad! 🗡️"
JOIN_SUFFIX = "is looking to join ⚔️"
# ----------------------------------------

class PreferenceView(discord.ui.View):
    """Persistent preference dropdown (attach to every feed message)."""
    def __init__(self, cog):
        super().__init__(timeout=None)  # No timeout for persistent view
        self.cog = cog

    @discord.ui.select(
        placeholder="Your Game Feed Preferences…",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(
                label="About Game Feed❓",
                value="about_feed",
                description="What this is and what the options do"
            ),
            discord.SelectOption(
                label="Show my games 🎮",
                value="opt_in",
                description="Post when I start playing"
            ),
            discord.SelectOption(
                label="Looking for squad ⚔️",
                value="lfs",
                description="Mark this post as LFS (or join if this is not your post)"
            ),
            discord.SelectOption(
                label="How to link my console? 🕹️",
                value="console_help",
                description="Show Xbox/PlayStation linking guides"
            ),
            discord.SelectOption(
                label="Set my post image/GIF 🖼️",
                value="custom_image",
                description="DM the bot an image/GIF or link"
            ),
            discord.SelectOption(
                label="Hide me 🚫",
                value="opt_out",
                description="Do not post my games"
            ),
        ],
        custom_id="pref:select"
    )
    async def preference_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        pref = select.values[0]
        if pref == "about_feed":
            await self.cog.handle_about_feed_select(interaction)
            return

        if pref == "lfs":
            await self.cog.handle_lfs_select(interaction)
            return

        if pref == "console_help":
            await self.cog.handle_console_help_select(interaction)
            return

        if pref == "custom_image":
            await self.cog.handle_custom_image_select(interaction)
            return

        await self._set_preference(interaction, pref)
        
    async def _set_preference(self, interaction, pref):
        user_id = str(interaction.user.id)
        current_pref = self.cog.get_user_preference(user_id)
        if current_pref == pref:
            await interaction.response.send_message(
                f"You're already set to '{pref}'.",
                ephemeral=True
            )
            return

        record = self.cog.ensure_user_pref_record(user_id)
        record["pref"] = pref
        self.cog.prefs[user_id] = record
        success = await self.cog.save_json(PREFS_FILE, self.cog.prefs)
        
        if success:
            if pref == "opt_in":
                await interaction.response.send_message(
                    "You're opted in! When you start playing a game, it will be posted in the feed.",
                    ephemeral=True
                )
            else:  # opt_out
                await interaction.response.send_message(
                    "You're opted out. Your games will not be posted in the feed.",
                    ephemeral=True
                )
        else:
            await interaction.response.send_message(
                "Error saving preference. Please try again.",
                ephemeral=True
            )


class GameMonCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.prefs = self.load_json(PREFS_FILE)

        self.feed_state = self.load_json(FEED_STATE_FILE)
        if not isinstance(self.feed_state, dict):
            self.feed_state = {}
        if "messages" not in self.feed_state or not isinstance(self.feed_state.get("messages"), dict):
            self.feed_state["messages"] = {}
            
        # File lock to prevent race conditions
        self.file_lock = asyncio.Lock()

        # Feed batching/debounce state
        self._feed_events: List[dict] = []
        self._last_feed_post = 0.0
        self._feed_post_lock = asyncio.Lock()
        self._feed_post_task: Optional[asyncio.Task] = None

        # Prune lock to avoid concurrent history sweeps
        self._prune_lock = asyncio.Lock()

        # Persistent view registration guard (on_ready can fire multiple times)
        self._persistent_view_registered = False

        # Pending DM-based custom image flow: user_id -> {"expires_at": float}
        self._pending_custom_image: dict[str, dict] = {}

    # ---------- Custom Image via DM ----------
    def _custom_image_dm_expires_at(self) -> float:
        # 10 minutes from now
        return asyncio.get_running_loop().time() + 600

    def _pick_first_image_attachment_url(self, message: discord.Message) -> Optional[str]:
        atts = getattr(message, "attachments", None) or []
        for att in atts:
            try:
                content_type = (getattr(att, "content_type", None) or "").lower()
                filename = (getattr(att, "filename", None) or "").lower()
                url = getattr(att, "url", None)
            except Exception:
                continue

            if not isinstance(url, str) or not url.strip():
                continue

            # Prefer content_type when available; otherwise fall back to file extension.
            if content_type.startswith("image/"):
                return url
            if filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                return url

        return None

    def _pick_first_image_url_from_embeds(self, message: discord.Message) -> Optional[str]:
        """Try to extract a direct image URL from message embeds.

        This is primarily to support the Discord GIF picker (Tenor), which often
        sends a message containing an embed preview rather than a file attachment.
        """
        embeds = getattr(message, "embeds", None) or []
        candidates: list[str] = []

        def _normalize_tenor_media_url(url: str) -> str:
            """Normalize Tenor media URLs to a more embed-friendly form.

            Tenor often serves GIF-picker media as `mediaN.tenor.com/m/...`.
            The bot's built-in GIFs use `media.tenor.com/...` without `/m/`,
            which tends to embed more reliably.
            """
            try:
                parsed = urlparse(url)
            except Exception:
                return url

            host = (parsed.netloc or "").lower()
            if "tenor.com" not in host:
                return url

            path = parsed.path or ""
            if path.startswith("/m/"):
                path = path[2:]  # drop leading '/m'

            # If this is a Tenor "gif" URL that uses an AAAP* rendition (often actually mp4),
            # rewrite it to the common GIF rendition code (AAAAd) which tends to embed.
            try:
                parts = path.split("/")
                if len(parts) >= 2 and parts[0] == "":
                    # path like /<idcode>/<name>.gif
                    idcode = parts[1]
                    filename = parts[2] if len(parts) > 2 else ""
                    if isinstance(idcode, str) and idcode and isinstance(filename, str) and filename.lower().endswith(".gif"):
                        if len(idcode) > 5:
                            base = idcode[:-5]
                            code = idcode[-5:]
                            if code.startswith("AAAP"):
                                parts[1] = base + "AAAAd"
                                path = "/".join(parts)
            except Exception:
                pass

            # Prefer the canonical host if it's a tenor media host.
            if host.startswith("media") and host.endswith(".tenor.com"):
                host = "media.tenor.com"

            try:
                return parsed._replace(netloc=host, path=path).geturl()
            except Exception:
                return url

        def _maybe_decode_proxy(url: str) -> str:
            """If url looks like a proxy with a `url=` param, return the decoded target."""
            try:
                parsed = urlparse(url)
                qs = parse_qs(parsed.query or "")
                raw = qs.get("url", [None])[0]
                if isinstance(raw, str) and raw.strip():
                    try:
                        return unquote(raw.strip())
                    except Exception:
                        return raw.strip()
            except Exception:
                pass
            return url

        def _add_candidate(url: object) -> None:
            if not isinstance(url, str):
                return
            candidate = url.strip()
            if not candidate:
                return

            # Prefer the underlying target when Discord provides a proxy URL.
            candidate = _maybe_decode_proxy(candidate).strip()
            if candidate:
                candidate = _normalize_tenor_media_url(candidate).strip()
            if not candidate:
                return

            if self.is_valid_direct_image_url(candidate):
                candidates.append(candidate)

        for emb in embeds:
            try:
                data = emb.to_dict() if hasattr(emb, "to_dict") else {}
            except Exception:
                data = {}

            if not isinstance(data, dict):
                continue

            # Still preview URLs (often .png/.webp) live here.
            for key in ("image", "thumbnail"):
                try:
                    _add_candidate((data.get(key) or {}).get("url"))
                except Exception:
                    pass

            # Tenor GIF picker commonly provides the animated media as a video URL.
            # If it's an .mp4, try swapping to the matching .gif on the same path.
            try:
                video_url = (data.get("video") or {}).get("url")
            except Exception:
                video_url = None

            if isinstance(video_url, str) and video_url.strip():
                v = video_url.strip()
                v = _maybe_decode_proxy(v).strip()
                if v:
                    v = _normalize_tenor_media_url(v).strip()
                try:
                    parsed = urlparse(v)
                    path = parsed.path or ""
                    if path.lower().endswith(".mp4"):
                        gif_path = path[:-4] + ".gif"
                        gif_url = parsed._replace(path=gif_path).geturl()
                        _add_candidate(gif_url)
                except Exception:
                    pass

        # Prefer Tenor GIF renditions that are likely to be real GIFs.
        for url in candidates:
            if url.lower().endswith(".gif") and any(code in url for code in ("AAAAd", "AAAAC")):
                return url

        # Then prefer any GIF over still previews.
        for url in candidates:
            if url.lower().endswith(".gif"):
                return url

        return candidates[0] if candidates else None

    def _pick_first_image_url_from_message(self, message: discord.Message) -> Optional[str]:
        """Pick the best image/GIF URL from a DM message.

        Priority:
        1) Image attachment uploads
        2) GIF picker / rich embeds that contain direct image URLs
        3) Direct image URL pasted in message content
        """
        return (
            self._pick_first_image_attachment_url(message)
            or self._pick_first_image_url_from_embeds(message)
            or self._pick_first_url_from_text((getattr(message, "content", None) or "").strip())
        )

    def _pick_first_url_from_text(self, text: str) -> Optional[str]:
        if not isinstance(text, str):
            return None

        # Split on whitespace; tolerate Discord's <https://...> formatting.
        for token in text.split():
            candidate = token.strip().strip("<>").strip()
            if not candidate:
                continue
            if self.is_valid_direct_image_url(candidate):
                return candidate

        # Fallback: find any http(s) substring.
        match = re.search(r"https?://\S+", text)
        if match:
            candidate = match.group(0).strip().strip("<>").strip()
            if self.is_valid_direct_image_url(candidate):
                return candidate

        return None

    def _pick_first_http_url_from_text(self, text: str) -> Optional[str]:
        """Return the first http(s) URL from text (not necessarily a direct image)."""
        if not isinstance(text, str):
            return None

        for token in text.split():
            candidate = token.strip().strip("<>").strip()
            if not candidate:
                continue
            if candidate.startswith("http://") or candidate.startswith("https://"):
                return candidate

        match = re.search(r"https?://\S+", text)
        if match:
            return match.group(0).strip().strip("<>").strip()
        return None

    async def _resolve_tenor_page_to_direct_gif(self, url: str) -> Optional[str]:
        """Resolve a Tenor page URL (tenor.com/view/...) to a direct .gif URL.

        Tenor 'share' links are often page URLs; embeds require direct media URLs.
        """
        if not isinstance(url, str) or not url.strip():
            return None

        candidate = url.strip().strip("<>").strip()
        try:
            parsed = urlparse(candidate)
        except Exception:
            return None

        host = (parsed.netloc or "").lower()
        path = (parsed.path or "").lower()
        if "tenor.com" not in host:
            return None
        if "/view/" not in path:
            return None

        timeout = aiohttp.ClientTimeout(total=6)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(candidate, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True) as resp:
                    if resp.status != 200:
                        return None
                    html = await resp.text(errors="ignore")
        except Exception:
            return None

        # Prefer the direct GIF (Tenor commonly includes media*.tenor.com/...gif in the page).
        gif_matches = re.findall(r"https?://media\d*\.tenor\.com/[^\s\"']+\.gif", html, flags=re.IGNORECASE)
        if not gif_matches:
            return None

        # Heuristic: prefer /m/ URLs (common on tenor pages), else first.
        gif_matches = [m.strip() for m in gif_matches if isinstance(m, str) and m.strip()]
        def _normalize(url: str) -> str:
            try:
                parsed = urlparse(url)
            except Exception:
                return url
            host = "media.tenor.com" if (parsed.netloc or "").lower().endswith(".tenor.com") else (parsed.netloc or "")
            path = parsed.path or ""
            if path.startswith("/m/"):
                path = path[2:]

            # Rewrite AAAP* renditions to AAAAd for better embed behavior.
            try:
                parts = path.split("/")
                if len(parts) >= 2 and parts[0] == "":
                    idcode = parts[1]
                    filename = parts[2] if len(parts) > 2 else ""
                    if isinstance(idcode, str) and idcode and isinstance(filename, str) and filename.lower().endswith(".gif"):
                        if len(idcode) > 5:
                            base = idcode[:-5]
                            code = idcode[-5:]
                            if code.startswith("AAAP"):
                                parts[1] = base + "AAAAd"
                                path = "/".join(parts)
            except Exception:
                pass
            try:
                return parsed._replace(netloc=host, path=path).geturl()
            except Exception:
                return url

        gif_matches = [_normalize(m.strip()) for m in gif_matches if isinstance(m, str) and m.strip()]
        # Prefer GIF renditions likely to be actual GIFs.
        for m in gif_matches:
            if m.lower().endswith(".gif") and any(code in m for code in ("AAAAd", "AAAAC")):
                return m
        for m in gif_matches:
            if m.lower().endswith(".gif") and "/m/" not in m:
                return m
        return gif_matches[0] if gif_matches else None

    async def _clear_user_custom_image(self, user_id: str, channel: discord.abc.Messageable) -> None:
        user_id = str(user_id)
        record = self.ensure_user_pref_record(user_id)
        if "custom_image_url" not in record:
            await channel.send("You don't currently have a custom post image set.")
            return

        record.pop("custom_image_url", None)
        self.prefs[user_id] = record
        success = await self.save_json(PREFS_FILE, self.prefs)
        if not success:
            await channel.send("Error clearing your image. Please try again.")
            return

        await channel.send("Removed. Your posts will use the default images again.")

    def _is_hll_game(self, game_name: Optional[str]) -> bool:
        if not game_name:
            return False
        return str(game_name).strip().lower() == "hell let loose"

    # ---------- Preference Helpers ----------
    def ensure_user_pref_record(self, user_id: str) -> dict:
        """Return a mutable per-user record, migrating legacy string prefs on the fly."""
        user_id = str(user_id)
        existing = self.prefs.get(user_id)

        if isinstance(existing, dict):
            record = dict(existing)
        elif isinstance(existing, str):
            record = {"pref": existing}
        else:
            record = {"pref": DEFAULT_PREFERENCE}

        # Normalize fields
        if "pref" not in record or not isinstance(record.get("pref"), str):
            record["pref"] = DEFAULT_PREFERENCE
        if "custom_image_url" in record and not isinstance(record.get("custom_image_url"), str):
            record.pop("custom_image_url", None)

        return record

    def get_user_preference(self, user_id: str) -> str:
        user_id = str(user_id)
        value = self.prefs.get(user_id)
        if isinstance(value, dict):
            pref = value.get("pref")
            return pref if isinstance(pref, str) and pref else DEFAULT_PREFERENCE
        if isinstance(value, str) and value:
            return value
        return DEFAULT_PREFERENCE

    def get_user_custom_image_url(self, user_id: Optional[str]) -> Optional[str]:
        if user_id is None:
            return None
        user_id = str(user_id)
        value = self.prefs.get(user_id)
        if isinstance(value, dict):
            url = value.get("custom_image_url")
            return url.strip() if isinstance(url, str) and url.strip() else None
        return None

    def is_valid_media_url(self, url: str) -> bool:
        if not isinstance(url, str):
            return False
        url = url.strip()
        if not url:
            return False
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        if parsed.scheme not in {"http", "https"}:
            return False
        if not parsed.netloc:
            return False
        return True

    def is_valid_direct_image_url(self, url: str) -> bool:
        """True if url is http(s) and looks like a direct image/GIF link."""
        if not self.is_valid_media_url(url):
            return False

        try:
            parsed = urlparse(url.strip())
        except Exception:
            return False

        path = (parsed.path or "").lower()
        if any(path.endswith(ext) for ext in DIRECT_IMAGE_EXTENSIONS):
            return True

        # Discord and some CDNs serve images through proxy URLs where the file extension
        # isn't in the path, but the query string includes `format=webp|png|gif|...`.
        try:
            qs = parse_qs(parsed.query or "")
        except Exception:
            qs = {}

        fmt = (qs.get("format", [""])[0] or "").lower()
        if fmt in {"gif", "png", "jpg", "jpeg", "webp"}:
            return True

        # Some proxy URLs store the real target as a `url=` query param.
        raw = qs.get("url", [None])[0]
        if isinstance(raw, str) and raw.strip():
            try:
                decoded = unquote(raw.strip())
            except Exception:
                decoded = raw.strip()
            if self.is_valid_media_url(decoded):
                try:
                    inner = urlparse(decoded)
                    inner_path = (inner.path or "").lower()
                except Exception:
                    inner_path = ""
                if any(inner_path.endswith(ext) for ext in DIRECT_IMAGE_EXTENSIONS):
                    return True

        return False

    def cog_unload(self):
        """Clean up when cog is unloaded"""
        logger.info("GameMonCog unloaded, background tasks stopped")

    # ---------- JSON Helpers ----------
    def load_json(self, filename):
        """Load JSON with error handling"""
        if not os.path.exists(filename):
            logger.info(f"File {filename} not found, creating new")
            return {}
            
        try:
            with open(filename, "r") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing {filename}: {e}")
            return {}
        except Exception as e:
            logger.error(f"Error reading {filename}: {e}")
            return {}

    async def save_json(self, filename, data):
        """Save JSON with error handling and file locking"""
        async with self.file_lock:
            try:
                with open(filename, "w") as f:
                    json.dump(data, f, indent=4)
                return True
            except Exception as e:
                logger.error(f"Error saving {filename}: {e}")
                return False

    # ---------- Game Name Normalization ----------
    def normalize_game_name(self, game_name):
        """Normalize game names by removing special characters and standardizing case"""
        if not game_name:
            return None
        
        # Replace trademark, registered, and copyright symbols
        normalized = game_name.replace("™", "").replace("®", "").replace("©", "")
        
        # Remove extra whitespace and trim
        normalized = " ".join(normalized.split())
        
        # Change noisy normalization logs to debug
        logger.debug(f"Normalized game name: '{game_name}' -> '{normalized}'")
        return normalized

    # ---------- Game Activity Detection ----------
    def get_game_from_activity(self, activity):
        """Extract game name from any type of activity"""
        # Debug logging for activity
        logger.debug(f"Activity: {activity} | Type: {type(activity)}")
        
        # Game name to return
        game_name = None
        
        # Standard Game activity
        if isinstance(activity, discord.Game):
            game_name = activity.name
        
        # Rich Presence for games
        elif isinstance(activity, discord.Activity):
            # Debug logging for rich activity details
            if hasattr(activity, 'application_id'):
                logger.debug(f"Application ID: {activity.application_id}")
            if hasattr(activity, 'name'):
                logger.debug(f"Name: {activity.name}")
            if hasattr(activity, 'details'):
                logger.debug(f"Details: {activity.details}")
            if hasattr(activity, 'state'):
                logger.debug(f"State: {activity.state}")
            
            # Playing activities
            if activity.type == discord.ActivityType.playing:
                game_name = activity.name
            
            # Some games set their name in details or state fields
            elif hasattr(activity, 'details') and activity.details:
                game_name = activity.name or activity.details
            
            # Check for Xbox specific indicators
            if hasattr(activity, 'assets') and activity.assets:
                # Check for Xbox assets or platform identifiers
                large_image = getattr(activity.assets, 'large_image', '')
                small_image = getattr(activity.assets, 'small_image', '')
                large_text = getattr(activity.assets, 'large_text', '')
                small_text = getattr(activity.assets, 'small_text', '')
                
                logger.debug(f"Assets - Large image: {large_image}, Small image: {small_image}")
                logger.debug(f"Asset text - Large: {large_text}, Small: {small_text}")
                
                # Look for Xbox indicators in the assets
                xbox_indicators = ['xbox', 'xboxlive', 'xbl']
                assets_text = f"{large_image} {small_image} {large_text} {small_text}".lower()
                
                if any(indicator in assets_text for indicator in xbox_indicators):
                    logger.info(f"Xbox game detected: {activity.name}")
                    game_name = activity.name
        
        # For Streaming activities (if we want to track those)
        elif hasattr(activity, 'type') and activity.type == discord.ActivityType.streaming:
            game_name = f"Streaming: {activity.name}" if activity.name else None
        
        # Custom "Playing X" status
        elif isinstance(activity, discord.CustomActivity) and activity.name:
            if "playing" in activity.name.lower():
                parts = activity.name.lower().split("playing ", 1)
                if len(parts) > 1:
                    game_name = parts[1].strip()
        
        # Normalize the game name if one was found
        if game_name:
            return self.normalize_game_name(game_name)
            
        # No game detected from this activity
        return None

    # ---------- Bot Ready Event ----------
    @commands.Cog.listener()
    async def on_ready(self):
        """Called when the bot is ready and connected"""
        logger.info(f"GameMonCog ready - Connected as {self.bot.user}")
        
        # Wait a moment to ensure bot is fully connected before attempting message operations
        await asyncio.sleep(5)
        
        # Validate destination channels exist (try cache first, then API). If not found, continue startup.
        for cid, label in (
            (THREAD_ID, "default"),
            (HLL_CHANNEL_ID, "hll"),
        ):
            if not isinstance(cid, int) or not cid:
                continue

            chan = self.bot.get_channel(cid)
            if not chan:
                try:
                    chan = await self.bot.fetch_channel(cid)
                    logger.info(f"Fetched {label} channel via API during on_ready: {cid}")
                except Exception as e:
                    logger.warning(f"Channel with ID {cid} not found during on_ready ({label}): {e}")
                    continue

            try:
                bot_member = chan.guild.get_member(self.bot.user.id)
                if not bot_member:
                    logger.warning(f"Bot is not a member of the guild for channel {cid} (on_ready)")
                else:
                    permissions = chan.permissions_for(bot_member)
                    if not permissions.send_messages or not permissions.embed_links:
                        logger.warning(f"Bot lacks required permissions in channel {cid} (on_ready)")
                    else:
                        logger.info(f"Channel validation successful ({label}): {cid}")
            except Exception:
                pass
        
        # Register persistent view handlers (required for buttons to work after restarts)
        if not self._persistent_view_registered:
            try:
                self.bot.add_view(PreferenceView(self))
                self._persistent_view_registered = True
            except Exception as e:
                logger.error(f"Failed to register persistent PreferenceView: {e}")

    # ---------- Message Event Handler ----------
    @commands.Cog.listener()
    async def on_message(self, message):
        """Monitor messages and delete human messages in the tracked thread"""
        # Handle DM-based custom image uploads first.
        try:
            if getattr(message, "guild", None) is None and message.author and not message.author.bot:
                user_id = str(message.author.id)

                content = (getattr(message, "content", None) or "").strip()
                content_lc = content.lower()

                pending = self._pending_custom_image.get(user_id)
                if pending:
                    now = asyncio.get_running_loop().time()
                    expires_at = float(pending.get("expires_at") or 0)
                    if expires_at and now > expires_at:
                        self._pending_custom_image.pop(user_id, None)
                        await message.channel.send(
                            "That request expired. Use the dropdown again to start over.",
                        )
                        return

                    # Only allow clearing during the active pending window.
                    if content_lc in {"remove", "clear", "reset", "default", "none"}:
                        self._pending_custom_image.pop(user_id, None)
                        await self._clear_user_custom_image(user_id, message.channel)
                        return

                    url = self._pick_first_image_url_from_message(message)
                    if not url:
                        raw = self._pick_first_http_url_from_text(content)
                        resolved = await self._resolve_tenor_page_to_direct_gif(raw) if raw else None
                        if resolved and self.is_valid_direct_image_url(resolved):
                            url = resolved
                    if isinstance(url, str) and url.strip():
                        # Normalize Tenor media URLs before saving.
                        try:
                            parsed = urlparse(url.strip())
                            if (parsed.netloc or "").lower().endswith(".tenor.com"):
                                path = parsed.path or ""
                                if path.startswith("/m/"):
                                    path = path[2:]
                                # Rewrite AAAP* renditions to AAAAd for better embed behavior.
                                try:
                                    parts = path.split("/")
                                    if len(parts) >= 2 and parts[0] == "":
                                        idcode = parts[1]
                                        filename = parts[2] if len(parts) > 2 else ""
                                        if isinstance(idcode, str) and idcode and isinstance(filename, str) and filename.lower().endswith(".gif"):
                                            if len(idcode) > 5:
                                                base = idcode[:-5]
                                                code = idcode[-5:]
                                                if code.startswith("AAAP"):
                                                    parts[1] = base + "AAAAd"
                                                    path = "/".join(parts)
                                except Exception:
                                    pass

                                url = parsed._replace(netloc="media.tenor.com", path=path).geturl()
                        except Exception:
                            pass
                    if not url:
                        await message.channel.send(
                            "Please send an image/GIF as an attachment, use the Discord GIF picker (Tenor), or paste a direct image/GIF link.\n"
                            "Direct links usually end with .gif/.png/.jpg/.webp.\n"
                            "Tip: send `remove` to clear your custom image (within the 10 minute window)."
                        )
                        return

                    record = self.ensure_user_pref_record(user_id)
                    record["custom_image_url"] = url
                    self.prefs[user_id] = record

                    success = await self.save_json(PREFS_FILE, self.prefs)
                    if not success:
                        await message.channel.send("Error saving your image. Please try again.")
                        return

                    self._pending_custom_image.pop(user_id, None)
                    suffix = ""
                    try:
                        if isinstance(url, str) and url.lower().endswith(".gif"):
                            suffix = " (GIF)"
                    except Exception:
                        pass

                    await message.channel.send(
                        "Saved! Your future GameMon posts will use that image as the main embed image.\n"
                        f"Saved URL{suffix}: {url}"
                    )
                    return
        except Exception as e:
            logger.error(f"Error handling DM custom image flow: {e}")

        # Skip messages from bots (including the bot itself)
        if message.author.bot:
            return
            
        # Skip messages from admin users
        if message.author.id in ADMIN_USER_IDS:
            logger.info(f"Admin message from {message.author.name} - not deleting")
            return
            
        # Check if the message is in the monitored thread
        if message.channel.id == THREAD_ID:
            try:
                # Delete the message
                await message.delete()
            except discord.Forbidden:
                logger.error("Bot lacks permission to delete messages")
            except discord.NotFound:
                logger.warning("Message was already deleted")
            except discord.HTTPException as e:
                logger.error(f"HTTP error deleting message: {e}")

    # ---------- Presence / Activity Updates ----------
    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        """Track users starting/stopping games and post a feed message on starts (globally debounced)."""
        try:
            # Ignore bots and other guilds
            if after.bot:
                return
            if after.guild is None or after.guild.id != GUILD_ID:
                return

            # Optional: exclude members with specific roles from ever posting.
            if EXCLUDED_ROLE_IDS:
                try:
                    excluded = set(int(r) for r in EXCLUDED_ROLE_IDS)
                    if any(getattr(role, "id", None) in excluded for role in (getattr(after, "roles", None) or [])):
                        return
                except Exception:
                    pass

            user_id = str(after.id)

            # Optional: disable tracking for users without explicit preferences
            if TEMP_DISABLE_DEFAULT_MONITORING and user_id not in self.prefs:
                return

            pref = self.get_user_preference(user_id)

            # If opted out, don't post anything
            if pref != "opt_in":
                return

            before_games = self._get_tracked_games(before)
            after_games = self._get_tracked_games(after)

            # Feed message on game starts (no per-user cooldown; only global debounced posting)
            for game_name in [g for g in after_games if g not in before_games]:
                await self.enqueue_feed_event(after, game_name)
        except Exception as e:
            logger.error(f"Error in on_presence_update: {e}")

    # ---------- Feed Helpers ----------
    def _get_tracked_games(self, member: discord.Member) -> List[str]:
        games: List[str] = []
        seen = set()
        for activity in getattr(member, "activities", []) or []:
            game = self.get_game_from_activity(activity)
            if not game or game in IGNORED_GAMES:
                continue
            if game in seen:
                continue
            seen.add(game)
            games.append(game)
        return games

    def _format_started_playing(self, member: discord.Member, game_name: str) -> str:
        # Base feed line (no mention/tag)
        display = member.display_name
        return f"**{display}** started playing {game_name} 🗡️"

    def _render_feed_description(self, message_ctx: dict) -> str:
        target_display = message_ctx.get("target_display", "Someone")
        game_name = message_ctx.get("game", "a game")
        lfs_enabled = bool(message_ctx.get("lfs_enabled"))

        base = f"**{target_display}** started playing {game_name} 🗡️"
        if lfs_enabled:
            base = f"{base} {SQUAD_SUFFIX}"

        joiners = message_ctx.get("joiners", [])
        lines = [base]
        if isinstance(joiners, list) and joiners:
            for joiner_display in joiners:
                if joiner_display:
                    lines.append(f"...**and** {joiner_display} {JOIN_SUFFIX}")
        return "\n".join(lines)

    def _pick_gif_url_for_game(self, game_name: str) -> Optional[str]:
        """Pick a random GIF URL for a given game name (if configured)."""
        if not game_name:
            return None

        key = str(game_name).strip().lower()
        urls = GAME_GIF_URLS.get(key)
        if urls is None:
            # Case-insensitive key lookup so users can type natural casing in config.
            for map_key, map_urls in GAME_GIF_URLS.items():
                if str(map_key).strip().lower() == key:
                    urls = map_urls
                    break
        if not isinstance(urls, list) or not urls:
            return None

        urls = [u for u in urls if isinstance(u, str) and u.strip()]
        if not urls:
            return None

        return random.choice(urls)

    def _apply_gif_to_embed(self, embed: discord.Embed, gif_url: Optional[str]) -> None:
        """Apply the chosen GIF to the embed as either thumbnail or main image."""
        if not gif_url:
            return

        if GIF_AS_THUMBNAIL:
            embed.set_thumbnail(url=gif_url)
            # Some discord.py versions don't expose Embed.Empty; None clears the field.
            embed.set_image(url=None)
        else:
            embed.set_image(url=gif_url)
            embed.set_thumbnail(url=None)

    def _apply_custom_image_to_embed(self, embed: discord.Embed, image_url: Optional[str]) -> None:
        """Apply a user-provided image/GIF as the main embed image (never thumbnail)."""
        if not image_url:
            return

        embed.set_image(url=image_url)
        embed.set_thumbnail(url=None)

    def _apply_ctx_media_to_embed(self, embed: discord.Embed, ctx: dict) -> None:
        """Apply the correct media to an embed based on message context."""
        custom_image_url = ctx.get("custom_image_url") if isinstance(ctx, dict) else None
        if isinstance(custom_image_url, str) and custom_image_url.strip():
            candidate = custom_image_url.strip()
            if self.is_valid_direct_image_url(candidate):
                self._apply_custom_image_to_embed(embed, candidate)
                return
        self._apply_gif_to_embed(embed, ctx.get("gif_url") if isinstance(ctx, dict) else None)

    async def _get_channel(self, channel_id: int):
        channel = self.bot.get_channel(int(channel_id))
        if channel:
            return channel
        try:
            return await self.bot.fetch_channel(int(channel_id))
        except Exception as e:
            logger.error(f"Channel with ID {channel_id} not found. Cannot post feed message: {e}")
            return None

    def _get_destination_channel_id_for_game(self, game_name: Optional[str]) -> int:
        if self._is_hll_game(game_name) and isinstance(HLL_CHANNEL_ID, int) and HLL_CHANNEL_ID:
            return HLL_CHANNEL_ID
        return THREAD_ID

    async def _post_feed_message(
        self,
        content: str,
        game_name: Optional[str] = None,
        view: Optional[discord.ui.View] = None,
        gif_url: Optional[str] = None,
        custom_image_url: Optional[str] = None,
    ) -> Optional[discord.Message]:
        dest_id = self._get_destination_channel_id_for_game(game_name)
        channel = await self._get_channel(dest_id)
        if not channel:
            return None
        try:
            # Use a fresh View instance per message; keep persistent handlers registered via bot.add_view(...)
            embed = discord.Embed(description=content, color=discord.Color.green())
            if custom_image_url and self.is_valid_direct_image_url(custom_image_url):
                self._apply_custom_image_to_embed(embed, custom_image_url)
            else:
                self._apply_gif_to_embed(embed, gif_url)
            embed.timestamp = discord.utils.utcnow()
            msg = await channel.send(embed=embed, view=view or PreferenceView(self))
            # Keep the destination tidy
            await self.prune_channel_messages(dest_id)
            return msg
        except discord.Forbidden as e:
            logger.error(f"Permission error posting feed message: {e}")
            return None
        except discord.HTTPException as e:
            status = getattr(e, 'status', None)
            retry_after = getattr(e, 'retry_after', 10)
            if status == 429:
                logger.warning(f"Rate limited while posting feed message. Retrying in {retry_after} seconds.")
                await asyncio.sleep(retry_after)
                try:
                    embed = discord.Embed(description=content, color=discord.Color.green())
                    if custom_image_url:
                        self._apply_custom_image_to_embed(embed, custom_image_url)
                    else:
                        self._apply_gif_to_embed(embed, gif_url)
                    embed.timestamp = discord.utils.utcnow()
                    msg = await channel.send(embed=embed, view=view or PreferenceView(self))
                    await self.prune_channel_messages(dest_id)
                    return msg
                except Exception as e2:
                    logger.error(f"Retry failed posting feed message: {e2}")
                    return None
            logger.error(f"HTTP error posting feed message: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error posting feed message: {e}")
            return None

    async def prune_channel_messages(self, channel_id: int) -> None:
        """Delete this cog's (GameMon) non-pinned messages older than the newest KEEP_LAST_MESSAGES in the destination channel/thread."""
        # Avoid overlapping prune runs (posting can happen in bursts)
        async with self._prune_lock:
            channel = await self._get_channel(int(channel_id))
            if not channel:
                return

            bot_user = getattr(self.bot, "user", None)
            if not bot_user:
                return

            tracked = self.feed_state.get("messages", {})
            if not isinstance(tracked, dict) or not tracked:
                return

            # Collect newest messages created by this cog first (tracked by message id), skipping pinned.
            bot_messages: List[discord.Message] = []
            try:
                async for msg in channel.history(limit=None, oldest_first=False):
                    if msg.pinned:
                        continue
                    if not msg.author or msg.author.id != bot_user.id:
                        continue

                    msg_id = str(msg.id)
                    if msg_id not in tracked:
                        continue

                    bot_messages.append(msg)
                    # Only fetch enough history to delete an extra batch
                    if len(bot_messages) >= KEEP_LAST_MESSAGES + PRUNE_EXTRA_FETCH:
                        break
            except Exception as e:
                logger.error(f"Failed to fetch thread history for pruning: {e}")
                return

            # Nothing to do if we haven't exceeded the cap for bot messages.
            if len(bot_messages) <= KEEP_LAST_MESSAGES:
                return

            to_delete = bot_messages[KEEP_LAST_MESSAGES:]
            state_changed = False
            for msg in to_delete:
                try:
                    await msg.delete()
                    if str(msg.id) in self.feed_state.get("messages", {}):
                        self.feed_state["messages"].pop(str(msg.id), None)
                        state_changed = True
                    # Small spacing helps avoid hitting per-route limits when lots of deletes happen
                    await asyncio.sleep(0.25)
                except discord.Forbidden:
                    logger.error("Missing permissions to delete messages while pruning")
                    return
                except discord.NotFound:
                    continue
                except discord.HTTPException as e:
                    logger.error(f"HTTP error deleting message during prune: {e}")
                    return

            if state_changed:
                await self.save_json(FEED_STATE_FILE, self.feed_state)

    async def enqueue_feed_event(self, member: discord.Member, game_name: str) -> None:
        await self.enqueue_feed_event_custom(
            target_user_id=str(member.id),
            target_display=member.display_name,
            game_name=game_name,
        )

    async def enqueue_feed_event_custom(
        self,
        target_user_id: Optional[str],
        target_display: str,
        game_name: str,
    ) -> None:
        """Queue a feed post with explicit target display/user id.

        Used by tests/admin commands to simulate a post without a real presence update.
        """
        event = {
            "target_user_id": str(target_user_id) if target_user_id is not None else None,
            "target_display": str(target_display) if target_display else "Someone",
            "game": game_name,
        }
        async with self._feed_post_lock:
            self._feed_events.append(event)
        await self._schedule_feed_post()

    @app_commands.command(
        name="gamemon_test_hll",
        description="Post a test Hell Let Loose feed message (admin only).",
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.describe(player_name="Fake player name to display in the post")
    async def gamemon_test_hll(self, interaction: discord.Interaction, player_name: Optional[str] = None):
        # Permission check: must have the admin role in this guild.
        invoker = interaction.user
        has_admin_role = any(r.id == ADMIN_ROLE_ID for r in getattr(invoker, "roles", []) or [])
        if not has_admin_role:
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        fake_name = (player_name or "Test Soldier").strip() or "Test Soldier"

        # Post the test embed in the channel where the command is run (not the monitored thread).
        ctx = {
            "target_user_id": str(invoker.id),
            "target_display": str(fake_name),
            "game": "Hell Let Loose",
            "gif_url": None,
            "custom_image_url": None,
            "lfs_enabled": False,
            "joiners": [],
        }
        ctx["custom_image_url"] = self.get_user_custom_image_url(str(invoker.id))
        if not ctx.get("custom_image_url"):
            ctx["gif_url"] = self._pick_gif_url_for_game(ctx.get("game"))
        description = self._render_feed_description(ctx)

        embed = discord.Embed(description=description, color=discord.Color.green())
        self._apply_ctx_media_to_embed(embed, ctx)
        embed.timestamp = discord.utils.utcnow()

        await interaction.response.send_message(embed=embed, view=PreferenceView(self))
        try:
            msg = await interaction.original_response()
        except Exception:
            msg = None

        if msg:
            self.feed_state.setdefault("messages", {})
            self.feed_state["messages"][str(msg.id)] = ctx
            await self.save_json(FEED_STATE_FILE, self.feed_state)

    async def _schedule_feed_post(self) -> None:
        async with self._feed_post_lock:
            now = asyncio.get_event_loop().time()

            def _schedule(delay: float):
                if self._feed_post_task and not self._feed_post_task.done():
                    return

                async def runner():
                    try:
                        await asyncio.sleep(delay)
                        async with self._feed_post_lock:
                            if not self._feed_events:
                                return

                            event = self._feed_events.pop(0)

                        # One message per start event so LFS is per-post.
                        ctx = {
                            "target_user_id": event.get("target_user_id"),
                            "target_display": event.get("target_display"),
                            "game": event.get("game"),
                            "gif_url": None,
                            "custom_image_url": None,
                            "lfs_enabled": False,
                            "joiners": [],
                        }

                        ctx["custom_image_url"] = self.get_user_custom_image_url(ctx.get("target_user_id"))
                        if not ctx.get("custom_image_url"):
                            ctx["gif_url"] = self._pick_gif_url_for_game(ctx.get("game"))

                        description = self._render_feed_description(ctx)
                        msg = await self._post_feed_message(
                            description,
                            game_name=ctx.get("game"),
                            view=PreferenceView(self),
                            gif_url=ctx.get("gif_url"),
                            custom_image_url=ctx.get("custom_image_url"),
                        )

                        if msg:
                            self.feed_state["messages"][str(msg.id)] = ctx
                            await self.save_json(FEED_STATE_FILE, self.feed_state)
                    finally:
                        self._last_feed_post = asyncio.get_event_loop().time()
                        self._feed_post_task = None

                        async with self._feed_post_lock:
                            has_more = bool(self._feed_events)
                        if has_more:
                            await self._schedule_feed_post()

                self._feed_post_task = asyncio.create_task(runner())

            elapsed = now - self._last_feed_post
            if elapsed >= FEED_POST_MIN_INTERVAL:
                _schedule(0)
            else:
                _schedule(FEED_POST_MIN_INTERVAL - elapsed)

    async def handle_lfs_select(self, interaction: discord.Interaction) -> None:
        """Per-message 'Looking for squad' action.

        - If the target user clicks: mark the post as LFS.
        - If anyone else clicks: append them as '... and X is looking to join'.
        """
        try:
            message = interaction.message
            if not message:
                await interaction.response.send_message("Couldn't find the message for this action.", ephemeral=True)
                return

            msg_id = str(message.id)
            ctx = self.feed_state.get("messages", {}).get(msg_id)
            if not ctx:
                await interaction.response.send_message(
                    "This post is too old to modify (state not found).",
                    ephemeral=True
                )
                return

            target_user_id = str(ctx.get("target_user_id")) if ctx.get("target_user_id") is not None else None
            clicker_id = str(interaction.user.id)
            changed = False

            if target_user_id and clicker_id == target_user_id:
                if not ctx.get("lfs_enabled"):
                    ctx["lfs_enabled"] = True
                    changed = True
                    await interaction.response.send_message("Marked this post as looking for a squad.", ephemeral=True)
                else:
                    await interaction.response.send_message("This post is already marked as LFS.", ephemeral=True)
            else:
                joiners = ctx.get("joiners")
                if not isinstance(joiners, list):
                    joiners = []
                    ctx["joiners"] = joiners

                joiner_name = getattr(interaction.user, "display_name", None) or getattr(interaction.user, "name", "Someone")
                if joiner_name in joiners:
                    await interaction.response.send_message("You're already listed as looking to join.", ephemeral=True)
                else:
                    joiners.append(joiner_name)
                    changed = True
                    await interaction.response.send_message("Added you as looking to join.", ephemeral=True)

            if not changed:
                return

            description = self._render_feed_description(ctx)
            embed = message.embeds[0] if message.embeds else discord.Embed(color=discord.Color.green())
            embed.description = description
            self._apply_ctx_media_to_embed(embed, ctx)
            embed.timestamp = discord.utils.utcnow()

            await message.edit(embed=embed, view=PreferenceView(self))

            self.feed_state["messages"][msg_id] = ctx
            await self.save_json(FEED_STATE_FILE, self.feed_state)
        except Exception as e:
            logger.error(f"Error handling LFS select: {e}")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send("Something went wrong handling that action.", ephemeral=True)
                else:
                    await interaction.response.send_message("Something went wrong handling that action.", ephemeral=True)
            except Exception:
                pass

    async def handle_custom_image_select(self, interaction: discord.Interaction) -> None:
        """Start a DM flow to set the clicker's per-user custom image/GIF.

        The dropdown is attached to every feed post, so anyone can start the DM flow from any post.
        The saved image applies to the clicker's future posts only.
        """
        try:
            message = interaction.message
            if not message:
                await interaction.response.send_message("Couldn't find the message for this action.", ephemeral=True)
                return

            clicker_id = str(interaction.user.id)

            # Arm the DM flow.
            self._pending_custom_image[clicker_id] = {"expires_at": self._custom_image_dm_expires_at()}

            # DM the user instructions.
            try:
                dm = await interaction.user.create_dm()
                await dm.send(
                    "Reply to this DM with the image/GIF you want game monitor to use on all of your future posts.\n"
                    "- You can attach an image/GIF, use Discord's GIF picker (Tenor), OR paste a direct image/GIF link (ending in .gif/.png/.jpg/.webp).\n"
                    "- To remove your custom image and go back to defaults, send: `remove` (within 10 minutes)\n"
                    "I will use the first valid attachment/GIF-picker/direct-link you send in the next 10 minutes."
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "I couldn't DM you (your DMs might be closed). Please enable DMs from server members and try again.",
                    ephemeral=True,
                )
                return

            await interaction.response.send_message(
                "Check your DMs — send me an image/GIF (attachment, GIF picker, or direct link) to save it for your future posts.",
                ephemeral=True,
            )
        except Exception as e:
            logger.error(f"Error handling custom image select: {e}")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send("Something went wrong handling that action.", ephemeral=True)
                else:
                    await interaction.response.send_message("Something went wrong handling that action.", ephemeral=True)
            except Exception:
                pass

    async def handle_console_help_select(self, interaction: discord.Interaction) -> None:
        """Send an ephemeral message with console-linking guides."""
        xbox_url = "https://support.discord.com/hc/en-us/articles/360003953831-Discord-and-Xbox-Connection-FAQ"
        ps_url = "https://support.discord.com/hc/en-us/articles/4419534960919-Discord-and-PlayStation-Network-Connection-FAQ"

        help_text = (
            "Here are the official Discord guides for linking your console:\n\n"
            f"Xbox: {xbox_url}\n"
            f"PlayStation: {ps_url}"
        )

        try:
            if interaction.response.is_done():
                await interaction.followup.send(help_text, ephemeral=True)
            else:
                await interaction.response.send_message(help_text, ephemeral=True)
        except Exception as e:
            logger.error(f"Error sending console help message: {e}")
            if interaction.response.is_done():
                await interaction.followup.send("Something went wrong sending that message.", ephemeral=True)
            else:
                await interaction.response.send_message("Something went wrong sending that message.", ephemeral=True)

    async def handle_about_feed_select(self, interaction: discord.Interaction) -> None:
        """Send an ephemeral explanation of the Game Feed and dropdown options."""
        hll_line = ""
        try:
            if isinstance(HLL_CHANNEL_ID, int) and HLL_CHANNEL_ID:
                hll_line = f" or <#{HLL_CHANNEL_ID}> if the game is Hell Let Loose (only)."
        except Exception:
            hll_line = ""

        text = (
            "**About the Game Feed❓**\n"
            f"This bot watches your Discord activity (when you start playing a game) and posts it into the <#{THREAD_ID}> channel{hll_line}\n"
            "This only works if you have opted-in and have your device/console linked to your Discord account.\n"
            "**Dropdown options**\n"
            "• **Show my games 🎮** — Opt in to posting when you start playing.\n"
            "• **Hide me 🚫** — Opt out so your games are not posted or tracked whatsoever.\n"
            "• **Looking for squad ⚔️** — If you click this on *your* post, it marks it as looking for a squad. If you click it on someone else’s post, it adds you as looking to join.\n"
            "• **Set my post image/GIF 🖼️** — Starts a DM flow where you can send an image/GIF (attachment or link). That image will be used as the *main embed image* on your future Game Feed posts unless you remove it via DM.\n"
            "• **How to link my console? 🕹️** — Shows official Xbox/PlayStation linking guides."
        )

        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except Exception as e:
            logger.error(f"Error sending about feed message: {e}")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send("Something went wrong sending that message.", ephemeral=True)
                else:
                    await interaction.response.send_message("Something went wrong sending that message.", ephemeral=True)
            except Exception:
                pass

async def setup(bot):
    await bot.add_cog(GameMonCog(bot))
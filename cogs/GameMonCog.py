import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import asyncio
import logging
import random
from typing import List, Optional
from urllib.parse import urlparse

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
IGNORED_GAMES = ["Spotify", "Discord", "Pornhub", "Netflix", "Disney", "Sky TV", "Youtube"]

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
                description="Use your own image on your posts"
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


class CustomImageModal(discord.ui.Modal, title="Set your post image/GIF"):
    def __init__(self, cog: "GameMonCog", user_id: str, message_id: Optional[int] = None):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = str(user_id)
        self.message_id = int(message_id) if message_id is not None else None

        self.image_url = discord.ui.TextInput(
            label="Direct image/GIF URL",
            placeholder="https://...",
            required=True,
            max_length=500,
        )
        self.add_item(self.image_url)

    async def on_submit(self, interaction: discord.Interaction):
        url = (str(self.image_url.value) if self.image_url.value is not None else "").strip()
        if not self.cog.is_valid_media_url(url):
            await interaction.response.send_message(
                "That doesn't look like a valid http(s) URL. Please paste a direct image/GIF link.",
                ephemeral=True,
            )
            return

        # Acknowledge quickly to avoid interaction timeouts, then do work.
        await interaction.response.defer(ephemeral=True)

        record = self.cog.ensure_user_pref_record(self.user_id)
        record["custom_image_url"] = url
        self.cog.prefs[self.user_id] = record

        success = await self.cog.save_json(PREFS_FILE, self.cog.prefs)
        if not success:
            await interaction.followup.send("Error saving your image. Please try again.", ephemeral=True)
            return

        # Update the specific message (if provided) so the user sees immediate effect.
        if self.message_id is not None:
            try:
                channel = interaction.channel
                msg = None
                if channel and hasattr(channel, "fetch_message"):
                    msg = await channel.fetch_message(self.message_id)

                if msg:
                    msg_id = str(msg.id)
                    ctx = self.cog.feed_state.get("messages", {}).get(msg_id)
                    if isinstance(ctx, dict):
                        # Only allow editing when the clicker is the original poster.
                        target_user_id = str(ctx.get("target_user_id")) if ctx.get("target_user_id") is not None else None
                        if target_user_id and str(interaction.user.id) == target_user_id:
                            ctx["custom_image_url"] = url
                            description = self.cog._render_feed_description(ctx)
                            embed = msg.embeds[0] if msg.embeds else discord.Embed(color=discord.Color.green())
                            embed.description = description
                            self.cog._apply_ctx_media_to_embed(embed, ctx)
                            embed.timestamp = discord.utils.utcnow()
                            await msg.edit(embed=embed, view=PreferenceView(self.cog))
                            self.cog.feed_state.setdefault("messages", {})
                            self.cog.feed_state["messages"][msg_id] = ctx
                            await self.cog.save_json(FEED_STATE_FILE, self.cog.feed_state)
            except Exception as e:
                logger.error(f"Failed to update message after setting custom image: {e}")

        await interaction.followup.send(
            "Saved! Your future GameMon posts will use that image as the main embed image.",
            ephemeral=True,
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
        
        # Validate thread exists (try cache first, then API). If not found, continue startup
        thread = self.bot.get_channel(THREAD_ID)
        if not thread:
            try:
                thread = await self.bot.fetch_channel(THREAD_ID)
                logger.info(f"Fetched thread via API during on_ready: {THREAD_ID}")
            except Exception as e:
                logger.warning(f"Thread with ID {THREAD_ID} not found during on_ready: {e}")

        if thread:
            # Check permissions when we have the thread object
            bot_member = thread.guild.get_member(self.bot.user.id)
            if not bot_member:
                logger.warning("Bot is not a member of the guild for the thread (on_ready)")
            else:
                permissions = thread.permissions_for(bot_member)
                if not permissions.send_messages or not permissions.embed_links:
                    logger.warning("Bot lacks some required permissions in the thread (on_ready)")
                else:
                    logger.info("Thread validation successful")
        
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
            self._apply_custom_image_to_embed(embed, custom_image_url.strip())
            return
        self._apply_gif_to_embed(embed, ctx.get("gif_url") if isinstance(ctx, dict) else None)

    async def _get_thread(self):
        thread = self.bot.get_channel(THREAD_ID)
        if thread:
            return thread
        try:
            return await self.bot.fetch_channel(THREAD_ID)
        except Exception as e:
            logger.error(f"Thread with ID {THREAD_ID} not found. Cannot post feed message: {e}")
            return None

    async def _post_feed_message(
        self,
        content: str,
        view: Optional[discord.ui.View] = None,
        gif_url: Optional[str] = None,
        custom_image_url: Optional[str] = None,
    ) -> Optional[discord.Message]:
        thread = await self._get_thread()
        if not thread:
            return None
        try:
            # Use a fresh View instance per message; keep persistent handlers registered via bot.add_view(...)
            embed = discord.Embed(description=content, color=discord.Color.green())
            if custom_image_url:
                self._apply_custom_image_to_embed(embed, custom_image_url)
            else:
                self._apply_gif_to_embed(embed, gif_url)
            embed.timestamp = discord.utils.utcnow()
            msg = await thread.send(embed=embed, view=view or PreferenceView(self))
            # Keep the thread tidy
            await self.prune_thread_messages()
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
                    msg = await thread.send(embed=embed, view=view or PreferenceView(self))
                    await self.prune_thread_messages()
                    return msg
                except Exception as e2:
                    logger.error(f"Retry failed posting feed message: {e2}")
                    return None
            logger.error(f"HTTP error posting feed message: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error posting feed message: {e}")
            return None

    async def prune_thread_messages(self) -> None:
        """Delete this cog's (GameMon) non-pinned messages older than the newest KEEP_LAST_MESSAGES in the monitored thread."""
        # Avoid overlapping prune runs (posting can happen in bursts)
        async with self._prune_lock:
            thread = await self._get_thread()
            if not thread:
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
                async for msg in thread.history(limit=None, oldest_first=False):
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
        """Open a modal to set the clicker's per-user custom image/GIF.

        The dropdown is attached to every feed post, so anyone can open the modal from any post.
        The clicked message is only updated if the clicker is also that post's original poster
        (enforced inside the modal using the stored message context).
        """
        try:
            message = interaction.message
            if not message:
                await interaction.response.send_message("Couldn't find the message for this action.", ephemeral=True)
                return

            clicker_id = str(interaction.user.id)
            await interaction.response.send_modal(CustomImageModal(self, user_id=clicker_id, message_id=message.id))
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

async def setup(bot):
    await bot.add_cog(GameMonCog(bot))
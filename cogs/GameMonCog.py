import discord
from discord.ext import commands
import json
import os
import asyncio
import logging
from typing import List, Optional

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
PREFS_FILE = data_path("game_prefs.json")
DEFAULT_PREFERENCE = "opt_in"  # Default preference for users (opt_in or opt_out)
ADMIN_USER_IDS = [1109147750932676649]  # Replace with your admin user IDs who can use special commands
TEMP_DISABLE_DEFAULT_MONITORING = False  # Set to True to temporarily disable all monitoring for users without explicit preferences
# Throttle: minimum seconds between feed posts (global debounce)
FEED_POST_MIN_INTERVAL = 5  # increase if still rate-limited
# ----------------------------------------

class PreferenceView(discord.ui.View):
    """Persistent preference buttons (attach to every feed message)."""
    def __init__(self, cog):
        super().__init__(timeout=None)  # No timeout for persistent view
        self.cog = cog
        
    @discord.ui.button(label="Opt-In (Show My Games)", style=discord.ButtonStyle.green, custom_id="pref:opt_in")
    async def opt_in(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_preference(interaction, "opt_in")
        
    @discord.ui.button(label="Opt-Out (Hide My Games)", style=discord.ButtonStyle.red, custom_id="pref:opt_out")
    async def opt_out(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_preference(interaction, "opt_out")
        
    async def _set_preference(self, interaction, pref):
        user_id = str(interaction.user.id)
        current_pref = self.cog.prefs.get(user_id, DEFAULT_PREFERENCE)
        if current_pref == pref:
            await interaction.response.send_message(
                f"You're already set to '{pref}'.",
                ephemeral=True
            )
            return

        self.cog.prefs[user_id] = pref
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
            
        # File lock to prevent race conditions
        self.file_lock = asyncio.Lock()

        # Feed batching/debounce state
        self._feed_lines: List[str] = []
        self._last_feed_post = 0.0
        self._feed_post_lock = asyncio.Lock()
        self._feed_post_task: Optional[asyncio.Task] = None

        # Persistent view registration guard (on_ready can fire multiple times)
        self._persistent_view_registered = False

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
                if not permissions.send_messages:
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

            pref = self.prefs.get(user_id, DEFAULT_PREFERENCE)

            # If opted out, don't post anything
            if pref != "opt_in":
                return

            before_games = self._get_tracked_games(before)
            after_games = self._get_tracked_games(after)

            # Feed message on game starts (no per-user cooldown; only global debounced posting)
            for game_name in [g for g in after_games if g not in before_games]:
                await self.enqueue_feed_line(self._format_started_playing(after, game_name))
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
        # Plain-text feed line (simple + readable)
        display = member.display_name
        return f"{display} started playing {game_name}"

    async def _get_thread(self):
        thread = self.bot.get_channel(THREAD_ID)
        if thread:
            return thread
        try:
            return await self.bot.fetch_channel(THREAD_ID)
        except Exception as e:
            logger.error(f"Thread with ID {THREAD_ID} not found. Cannot post feed message: {e}")
            return None

    async def _post_feed_message(self, content: str) -> bool:
        thread = await self._get_thread()
        if not thread:
            return False
        try:
            # Use a fresh View instance per message; keep persistent handlers registered via bot.add_view(...)
            await thread.send(content=content, view=PreferenceView(self))
            return True
        except discord.Forbidden as e:
            logger.error(f"Permission error posting feed message: {e}")
            return False
        except discord.HTTPException as e:
            status = getattr(e, 'status', None)
            retry_after = getattr(e, 'retry_after', 10)
            if status == 429:
                logger.warning(f"Rate limited while posting feed message. Retrying in {retry_after} seconds.")
                await asyncio.sleep(retry_after)
                try:
                    await thread.send(content=content, view=PreferenceView(self))
                    return True
                except Exception as e2:
                    logger.error(f"Retry failed posting feed message: {e2}")
                    return False
            logger.error(f"HTTP error posting feed message: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error posting feed message: {e}")
            return False

    async def enqueue_feed_line(self, line: str) -> None:
        async with self._feed_post_lock:
            self._feed_lines.append(line)
        await self._schedule_feed_post()

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
                            if not self._feed_lines:
                                return
                            lines = self._feed_lines
                            self._feed_lines = []

                        await self._post_feed_message("\n".join(lines))
                    finally:
                        self._last_feed_post = asyncio.get_event_loop().time()
                        self._feed_post_task = None

                self._feed_post_task = asyncio.create_task(runner())

            elapsed = now - self._last_feed_post
            if elapsed >= FEED_POST_MIN_INTERVAL:
                _schedule(0)
            else:
                _schedule(FEED_POST_MIN_INTERVAL - elapsed)

async def setup(bot):
    await bot.add_cog(GameMonCog(bot))
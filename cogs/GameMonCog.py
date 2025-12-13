import discord
from discord.ext import commands, tasks
import json
import os
import datetime
import asyncio
import logging

# Set up logging with configurable verbosity
VERBOSE_LOGGING = False  # Set to False to reduce terminal output

# Configure logging level based on verbosity
logging_level = logging.INFO if VERBOSE_LOGGING else logging.WARNING
logging.basicConfig(level=logging_level, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('GameMonCog')

# For even more control, silence specific loggers that are too chatty
if not VERBOSE_LOGGING:
    # Silence these specific loggers that are too chatty
    logging.getLogger('discord.gateway').setLevel(logging.ERROR)
    logging.getLogger('discord.client').setLevel(logging.ERROR)
    logging.getLogger('discord.http').setLevel(logging.ERROR)

# ---------------- CONFIG ----------------
GUILD_ID = 1097913605082579024   # Replace with your guild/server ID
THREAD_ID = 1412934277133369494  # replace with your thread ID
IGNORED_GAMES = ["Spotify", "Discord", "Pornhub", "Netflix", "Disney", "Sky TV", "Youtube"]
PREFS_FILE = "game_prefs.json"
STATE_FILE = "game_state.json"
INACTIVE_CHECK_MINUTES = 60  # how often to check for inactive users
MAX_INACTIVE_HOURS = 12  # maximum time a user can be inactive before removal
DEFAULT_PREFERENCE = "opt_in"  # Default preference for users (opt_in or opt_out)
ADMIN_USER_IDS = [1109147750932676649]  # Replace with your admin user IDs who can use special commands
TEMP_DISABLE_DEFAULT_MONITORING = False  # Set to True to temporarily disable all monitoring for users without explicit preferences
# Throttle: minimum seconds between embed updates
EMBED_UPDATE_MIN_INTERVAL = 5  # increase if still rate-limited
# ----------------------------------------

class PreferenceView(discord.ui.View):
    """View with simplified preference buttons for the Now Playing embed"""
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
        self.cog.prefs[user_id] = pref
        success = await self.cog.save_json(PREFS_FILE, self.cog.prefs)
        
        if success:
            # If user opted in and is currently playing a game, add it
            if pref == "opt_in":
                # Check if user is playing a game now
                member = interaction.guild.get_member(interaction.user.id)
                if member and member.activities:
                    for activity in member.activities:
                        game = self.cog.get_game_from_activity(activity)
                        if game and game not in IGNORED_GAMES:
                            # Update the game-based state structure
                            if game not in self.cog.state["games"]:
                                self.cog.state["games"][game] = []
                            if user_id not in self.cog.state["games"][game]:
                                self.cog.state["games"][game].append(user_id)
                                
                            self.cog.state["last_seen"][user_id] = datetime.datetime.utcnow().isoformat()
                            await self.cog.save_json(STATE_FILE, self.cog.state)
                            await self.cog.schedule_update()
                            await interaction.response.send_message(
                                f"You've opted in! Your current game '{game}' has been added to the list.",
                                ephemeral=True
                            )
                            return
                
                # No game or couldn't detect one
                await interaction.response.send_message(
                    f"You've opted in! Your games will now appear in the Now Playing list.",
                    ephemeral=True
                )
            else:  # opt_out
                # Remove any current games
                removed = False
                for game, users in list(self.cog.state["games"].items()):
                    if user_id in users:
                        users.remove(user_id)
                        removed = True
                        # If no users left for this game, remove the game
                        if not users:
                            self.cog.state["games"].pop(game)
                
                if removed:
                    await self.cog.save_json(STATE_FILE, self.cog.state)
                    await self.cog.schedule_update()
                
                await interaction.response.send_message(
                    f"You've opted out. Your games will no longer appear in the Now Playing list.",
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
        self.state = self.load_json(STATE_FILE)
        
        # Initialize state with proper structure
        if "games" not in self.state:
            self.state["games"] = {}  # Structure: {"game_name": ["user_id1", "user_id2"]}
        if "message_id" not in self.state:
            self.state["message_id"] = None
        if "last_seen" not in self.state:
            self.state["last_seen"] = {}
            
        # Migrate old player-based structure to new game-based structure if needed
        if "players" in self.state and self.state["players"]:
            self._migrate_player_to_game_structure()
            
        # Flag to track if initial posting has been done
        self.initial_post_done = False
        
        # File lock to prevent race conditions
        self.file_lock = asyncio.Lock()
        
        # Preference view for the embed
        self.preference_view = PreferenceView(self)
        
        # Debounce/Rate-limit state for embed updates
        self._last_embed_update = 0.0
        self._embed_update_lock = asyncio.Lock()
        self._embed_update_task = None
        self._embed_force_new_pending = False

        # Start background tasks
        self.cleanup_inactive_users.start()
        self.ensure_message_exists.start()

    def _migrate_player_to_game_structure(self):
        """Migrate from old player-based structure to new game-based structure"""
        try:
            for user_id, game in self.state["players"].items():
                if game not in self.state["games"]:
                    self.state["games"][game] = []
                if user_id not in self.state["games"][game]:
                    self.state["games"][game].append(user_id)
            
            # Remove old players structure
            self.state.pop("players")
            logger.info("Successfully migrated from player-based to game-based structure")
        except Exception as e:
            logger.error(f"Error migrating data structure: {e}")

    def cog_unload(self):
        """Clean up when cog is unloaded"""
        # Stop background tasks
        self.cleanup_inactive_users.cancel()
        self.ensure_message_exists.cancel()
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

    # ---------- Message Deletion Helper ----------
    async def delete_previous_message(self):
        """Dedicated method to delete the previous embed message"""
        if not self.state.get("message_id"):
            logger.info("No previous message ID in state to delete")
            return False
            
        logger.info(f"Attempting to delete previous message with ID: {self.state['message_id']}")
        
        try:
            # Get the thread/channel
            thread = self.bot.get_channel(THREAD_ID)
            if not thread:
                logger.error(f"Thread with ID {THREAD_ID} not found for message deletion")
                # Even if we can't find the thread, clear the message ID from state
                self.state["message_id"] = None
                await self.save_json(STATE_FILE, self.state)
                return False
                
            # Convert message ID to int and validate
            try:
                message_id = int(self.state["message_id"])
            except (ValueError, TypeError):
                logger.error(f"Invalid message ID in state: {self.state['message_id']}")
                self.state["message_id"] = None
                await self.save_json(STATE_FILE, self.state)
                return False
                
            # Try to fetch and delete the message
            try:
                message = await thread.fetch_message(message_id)
                await message.delete()
                logger.info(f"Successfully deleted previous message: {message_id}")
                deleted = True
            except discord.NotFound:
                logger.warning(f"Message {message_id} not found, it may have been deleted already")
                deleted = False
            except discord.Forbidden:
                logger.error(f"Bot lacks permission to delete message {message_id}")
                deleted = False
            except discord.HTTPException as e:
                logger.error(f"HTTP error deleting message {message_id}: {e}")
                deleted = False
                
            # Always clear the message ID from state
            self.state["message_id"] = None
            await self.save_json(STATE_FILE, self.state)
            return deleted
            
        except Exception as e:
            logger.error(f"Unexpected error in delete_previous_message: {e}")
            # Still clear the message ID from state
            self.state["message_id"] = None
            await self.save_json(STATE_FILE, self.state)
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

    # ---------- Helper Function to Check Admin Access ----------
    def is_admin_user(self, user_id):
        """Check if a user is in the admin list"""
        return user_id in ADMIN_USER_IDS

    # ---------- Bot Ready Event ----------
    @commands.Cog.listener()
    async def on_ready(self):
        """Called when the bot is ready and connected"""
        logger.info(f"GameMonCog ready - Connected as {self.bot.user}")
        
        # Wait a moment to ensure bot is fully connected before attempting message operations
        await asyncio.sleep(5)
        
        # Register guild commands now that the bot is fully connected
        if GUILD_ID != 0:
            try:
                # Make sure we're registered with the right guild
                guild = self.bot.get_guild(GUILD_ID)
                if guild:
                    logger.info(f"Registering commands for guild: {guild.name}")
                    # Register the commands with the specific guild
                    self.bot.tree.copy_global_to(guild=discord.Object(id=GUILD_ID))
                    try:
                        await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))
                        logger.info(f"Successfully registered commands for guild ID: {GUILD_ID}")
                    except discord.HTTPException as e:
                        # Backoff and retry once on rate limit
                        status = getattr(e, 'status', None)
                        retry_after = getattr(e, 'retry_after', 30)
                        if status == 429:
                            logger.warning(f"Rate limited while syncing commands. Retrying in {retry_after} seconds.")
                            await asyncio.sleep(retry_after)
                            try:
                                await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))
                                logger.info("Retry successful for command sync.")
                            except Exception as e2:
                                logger.error(f"Retry failed for command sync: {e2}")
                        else:
                            logger.error(f"Failed to register guild commands: {e}")
                else:
                    logger.warning(f"Could not find guild with ID {GUILD_ID}")
            except Exception as e:
                logger.error(f"Failed to register guild commands: {e}")
        
        # Validate thread exists
        thread = self.bot.get_channel(THREAD_ID)
        if not thread:
            logger.error(f"Thread with ID {THREAD_ID} not found!")
            return
            
        # Check permissions
        bot_member = thread.guild.get_member(self.bot.user.id)
        if not bot_member:
            logger.error("Bot is not a member of the guild!")
            return
            
        permissions = thread.permissions_for(bot_member)
        if not permissions.send_messages or not permissions.embed_links:
            logger.error("Bot lacks required permissions in the thread!")
            return
        
        logger.info("Thread validation successful")
        
        # Clean out users who haven't explicitly opted in
        if DEFAULT_PREFERENCE == "opt_out":
            logger.info("Default preference is opt-out, removing users without explicit preferences")
            cleaned_users = 0
            
            # Find all user_ids in games
            all_tracked_users = set()
            for game, users in list(self.state["games"].items()):
                all_tracked_users.update(users)
            
            # Check each user if they have explicitly opted in
            for user_id in list(all_tracked_users):
                # Only keep users who explicitly have "opt_in" in the prefs file
                if self.prefs.get(user_id) != "opt_in":
                    # Remove from all games
                    for game, users in list(self.state["games"].items()):
                        if user_id in users:
                            users.remove(user_id)
                            cleaned_users += 1
                            # If no users left for this game, remove the game
                            if not users:
                                self.state["games"].pop(game)
            
            if cleaned_users > 0:
                logger.info(f"Removed {cleaned_users} users who hadn't explicitly opted in")
                await self.save_json(STATE_FILE, self.state)
        
        # Delete existing message using our dedicated method
        await self.delete_previous_message()
        
        # Add the persistent view to the bot
        self.bot.add_view(self.preference_view)
        
        # Add startup scan to detect current games from all members
        logger.info("Starting full server scan for games")
        guild = self.bot.get_guild(GUILD_ID)
        if guild:
            scan_count = 0
            added_count = 0
            
            for member in guild.members:
                if member.bot:
                    continue
                    
                scan_count += 1
                user_id = str(member.id)
                pref = self.prefs.get(user_id, DEFAULT_PREFERENCE)
                
                # Only check users who have opted in
                if pref == "opt_in":
                    # Find any current game
                    current_game = None
                    for activity in member.activities:
                        game = self.get_game_from_activity(activity)
                        if game and game not in IGNORED_GAMES:
                            current_game = game
                            break
                            
                    if current_game:
                        # Add to game list
                        if current_game not in self.state["games"]:
                            self.state["games"][current_game] = []
                        if user_id not in self.state["games"][current_game]:
                            self.state["games"][current_game].append(user_id)
                            added_count += 1
                            
                        # Update last seen time
                        self.state["last_seen"][user_id] = datetime.datetime.utcnow().isoformat()
            
            logger.info(f"Server scan complete - Checked {scan_count} users, added {added_count} games")
            
            # Save if any changes were made
            if added_count > 0:
                await self.save_json(STATE_FILE, self.state)
                logger.info("Saved state after startup scan")
        # Force an immediate update to create a new message
        await self.schedule_update(force_new=True)

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
                logger.info(f"Deleted human message from {message.author.name} in thread")
            except discord.Forbidden:
                logger.error("Bot lacks permission to delete messages")
            except discord.NotFound:
                logger.warning("Message was already deleted")
            except discord.HTTPException as e:
                logger.error(f"HTTP error deleting message: {e}")

    # ---------- Preference Command ----------
    @discord.app_commands.command(name="gamemon-gamepref", description="Set your game listing preference")
    @discord.app_commands.describe(pref="opt_in / opt_out")
    async def gamepref(self, interaction: discord.Interaction, pref: str):
        if pref not in ["opt_in", "opt_out"]:
            await interaction.response.send_message(
                "Invalid preference. Use opt_in / opt_out.", 
                ephemeral=True
            )
            return

        current_pref = self.prefs.get(str(interaction.user.id), DEFAULT_PREFERENCE)
        self.prefs[str(interaction.user.id)] = pref
        success = await self.save_json(PREFS_FILE, self.prefs)
        
        if success:
            # Handle same logic as the button version
            user_id = str(interaction.user.id)
            if pref == "opt_in":
                # Check if user is playing a game now
                member = interaction.guild.get_member(interaction.user.id)
                if member and member.activities:
                    for activity in member.activities:
                        game = self.get_game_from_activity(activity)
                        if game and game not in IGNORED_GAMES:
                            # Update the game-based state structure
                            if game not in self.state["games"]:
                                self.state["games"][game] = []
                            if user_id not in self.state["games"][game]:
                                self.state["games"][game].append(user_id)
                                
                            self.state["last_seen"][user_id] = datetime.datetime.utcnow().isoformat()
                            await self.save_json(STATE_FILE, self.state)
                            await self.schedule_update()
                            await interaction.response.send_message(
                                f"You've opted in! Your current game '{game}' has been added to the list.",
                                ephemeral=True
                            )
                            return
            
                await interaction.response.send_message(
                    f"You've opted in! Your games will now appear in the Now Playing list.", 
                    ephemeral=True
                )
            else:  # opt_out
                # Remove any current games
                removed = False
                for game, users in list(self.state["games"].items()):
                    if user_id in users:
                        users.remove(user_id)
                        removed = True
                        # If no users left for this game, remove the game
                        if not users:
                            self.state["games"].pop(game)
                
                if removed:
                    await self.save_json(STATE_FILE, self.state)
                    await self.schedule_update()
                
                await interaction.response.send_message(
                    f"You've opted out. Your games will no longer appear in the Now Playing list.", 
                    ephemeral=True
                )
        else:
            await interaction.response.send_message(
                "Error saving preference. Please try again.", 
                ephemeral=True
            )

    # ---------- Manual Refresh Command ----------
    @discord.app_commands.command(name="gamemon-refreshgames", description="Refresh the Now Playing list")
    async def refreshgames(self, interaction: discord.Interaction):
        # Check if user is in the admin list
        if not self.is_admin_user(interaction.user.id):
            await interaction.response.send_message(
                "You don't have permission to use this command.",
                ephemeral=True
            )
            return
            
        cleaned = await self.cleanup_stale_players()
        
        # Force a new message regardless of existing one
        success = await self.schedule_update(force_new=True)
        
        if success:
            await interaction.response.send_message(
                f"Refreshed game list. Removed {cleaned} inactive players and created a new message.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"Removed {cleaned} inactive players but failed to update the message. Check logs.",
                ephemeral=True
            )

    # ---------- Fix Preference Command ----------
    @discord.app_commands.command(name="gamemon-fixgame", description="Fix your game display (opt-in and force add current game)")
    async def fixgame(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        
        # Set preference to opt_in
        old_pref = self.prefs.get(user_id, DEFAULT_PREFERENCE)
        self.prefs[user_id] = "opt_in"
        await self.save_json(PREFS_FILE, self.prefs)
        
        # Check for current games and force add to state
        member = interaction.guild.get_member(interaction.user.id)
        
        current_game = None
        if member and member.activities:
            for activity in member.activities:
                game = self.get_game_from_activity(activity)
                if game and game not in IGNORED_GAMES:
                    current_game = game
                    break
        
        if current_game:
            # Force add to state
            if current_game not in self.state["games"]:
                self.state["games"][current_game] = []
            if user_id not in self.state["games"][current_game]:
                self.state["games"][current_game].append(user_id)
            
            self.state["last_seen"][user_id] = datetime.datetime.utcnow().isoformat()
            await self.save_json(STATE_FILE, self.state)
            logger.info(f"Force added game for {interaction.user.name}: {current_game}")
            
            # Update the embed
            success = await self.schedule_update()
            
            if success:
                await interaction.response.send_message(
                    f"Changed preference from `{old_pref}` to `opt_in` and added your current game: {current_game}",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "Fixed preferences but failed to update the message. Try /refreshgames.",
                    ephemeral=True
                )
        else:
            await interaction.response.send_message(
                f"Changed preference from `{old_pref}` to `opt_in`, but no game currently detected.",
                ephemeral=True
            )

    # ---------- Get Current Games Command ----------
    @discord.app_commands.command(name="gamemon-currentgames", description="Show currently detected games for users")
    async def currentgames(self, interaction: discord.Interaction):
        # Check if user is in the admin list
        if not self.is_admin_user(interaction.user.id):
            await interaction.response.send_message(
                "You don't have permission to use this command.",
                ephemeral=True
            )
            return
            
        # Build a report of current games being played
        result = []
        result.append("**Current Games by User:**")
        
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            await interaction.response.send_message("Error: Could not find the guild.", ephemeral=True)
            return
            
        # Get all members in the guild
        members = guild.members
        
        # Get all games being played
        games_by_user = {}
        
        for member in members:
            if member.bot:
                continue
                
            username = member.display_name
            user_id = str(member.id)
            
            if member.activities:
                detected_games = []
                for activity in member.activities:
                    game = self.get_game_from_activity(activity)
                    if game and game not in IGNORED_GAMES:
                        detected_games.append(game)
                        
                if detected_games:
                    games_by_user[username] = detected_games
        
        # Add detected games to the result
        if games_by_user:
            for username, games in games_by_user.items():
                result.append(f"\n**{username}**:")
                for game in games:
                    result.append(f"- {game}")
        else:
            result.append("\nNo games currently detected for any users.")
                
        # Also show what's in the current state
        result.append("\n**Currently Tracked Games:**")
        for game, users in self.state["games"].items():
            user_names = []
            for user_id in users:
                member = guild.get_member(int(user_id))
                if member:
                    user_names.append(member.display_name)
                else:
                    user_names.append(f"Unknown User ({user_id})")
            
            result.append(f"- **{game}**: {', '.join(user_names)}")
        
        # Show user preferences
        result.append("\n**User Preferences:**")
        for user_id, pref in self.prefs.items():
            member = guild.get_member(int(user_id))
            if member:
                result.append(f"- {member.display_name}: {pref}")
            
        await interaction.response.send_message("\n".join(result), ephemeral=True)

    # ---------- Event: Member updates ----------
    @commands.Cog.listener()
    async def on_presence_update(self, before, after):
        # Skip bot users
        if after.bot:
            return

        user_id = str(after.id)
        
        # Check preference - only track users who have explicitly opted in
        pref = self.prefs.get(user_id, DEFAULT_PREFERENCE)
        
        # If default is opt_out and the TEMP_DISABLE flag is on, ignore users without explicit preference
        if DEFAULT_PREFERENCE == "opt_out" and TEMP_DISABLE_DEFAULT_MONITORING and user_id not in self.prefs:
            return  # Skip processing completely for users without explicit preference
            
        if pref != "opt_in":
            logger.debug(f"User {after.name} has not opted in. Preference: {pref}")
            # Also remove user from any existing games to be extra safe
            removed = False
            for game, users in list(self.state["games"].items()):
                if user_id in users:
                    users.remove(user_id)
                    removed = True
                    logger.info(f"Removing non-opted-in user {after.name} from {game}")
                    # If no users left for this game, remove the game
                    if not users:
                        self.state["games"].pop(game)
            
            if removed:
                await self.save_json(STATE_FILE, self.state)
                await self.schedule_update()
            return

        # Use our improved game detection method
        before_game = None
        after_game = None
        
        # Check for gaming activities in before state
        for activity in before.activities:
            game = self.get_game_from_activity(activity)
            if game and game not in IGNORED_GAMES:
                before_game = game
                break
        
        # Check for gaming activities in after state  
        for activity in after.activities:
            game = self.get_game_from_activity(activity)
            if game and game not in IGNORED_GAMES:
                after_game = game
                break

        # Debug logging
        # Downgrade generic presence transition logs to debug
        logger.debug(f"Presence update for {after.name} ({after.id}): {before_game} -> {after_game}")

        # If unchanged, do nothing
        if before_game == after_game:
            logger.debug(f"Game unchanged for {after.name}: {after_game}")
            return
        
        # Update last seen timestamp
        self.state["last_seen"][user_id] = datetime.datetime.utcnow().isoformat()
        await self.save_json(STATE_FILE, self.state)
        
        # Started playing or changed games
        if after_game:
            # Promote meaningful changes to info
            logger.info(f"User {after.name} ({after.id}) playing {after_game}")
            
            # Remove user from any other games they might be in
            for game, users in list(self.state["games"].items()):
                if user_id in users and game != after_game:
                    users.remove(user_id)
                    # If no users left for this game, remove the game
                    if not users:
                        self.state["games"].pop(game)
            
            # Add user to the new game
            if after_game not in self.state["games"]:
                self.state["games"][after_game] = []
            if user_id not in self.state["games"][after_game]:
                self.state["games"][after_game].append(user_id)
                
            await self.save_json(STATE_FILE, self.state)
            logger.info(f"Updated game for {after.name}: {after_game}")
            await self.schedule_update()

        # Stopped playing
        elif before_game and not after_game:
            # Promote meaningful changes to info
            logger.info(f"User {after.name} ({after.id}) stopped playing {before_game}")
            
            # Remove user from the game they were playing
            if before_game in self.state["games"]:
                if user_id in self.state["games"][before_game]:
                    self.state["games"][before_game].remove(user_id)
                    # If no users left for this game, remove the game
                    if not self.state["games"][before_game]:
                        self.state["games"].pop(before_game)
                    
                    await self.save_json(STATE_FILE, self.state)
                    logger.info(f"Removed game for {after.name}")
                    await self.schedule_update()

    # ---------- Embed Update ----------
    async def update_embed(self, force_new=False):
        """Update the embed message in the thread, or create a new one if needed or forced"""
        thread = self.bot.get_channel(THREAD_ID)
        if not thread:
            logger.error(f"Thread with ID {THREAD_ID} not found. Cannot post message.")
            return False

        # If we're forcing a new message, delete the old one first
        if force_new and self.state.get("message_id"):
            await self.delete_previous_message()

        message = None
        if self.state.get("message_id") and not force_new:
            try:
                message = await thread.fetch_message(self.state["message_id"])
                logger.info(f"Found existing message {self.state['message_id']}")
            except discord.NotFound:
                logger.warning(f"Message {self.state['message_id']} not found, will create new")
                message = None
                # Clear the state since message doesn't exist
                self.state["message_id"] = None
                await self.save_json(STATE_FILE, self.state)
            except discord.HTTPException as e:
                logger.error(f"HTTP error fetching message: {e}")
                message = None

        logger.info(f"Creating embed with games: {self.state['games']}")
        embed = discord.Embed(title="🎮 Now Playing", color=discord.Color.green())
        embed.timestamp = discord.utils.utcnow()
        
        if self.state["games"]:
            # Sort games by number of players (most players first)
            sorted_games = sorted(
                self.state["games"].items(), 
                key=lambda x: len(x[1]), 
                reverse=True
            )
            
            guild = self.bot.get_guild(GUILD_ID)
            
            for game, user_ids in sorted_games:
                # Get display names for all users playing this game
                user_names = []
                for user_id in user_ids:
                    member = guild.get_member(int(user_id)) if guild else None
                    if member:
                        user_names.append(member.display_name)
                    else:
                        # Try to fetch from bot cache
                        user = self.bot.get_user(int(user_id))
                        if user:
                            user_names.append(user.name)
                        else:
                            user_names.append(f"User {user_id}")
                
                # Add field with game as name and players as value
                embed.add_field(
                    name=game,
                    value="• " + "\n• ".join(user_names),
                    inline=False
                )
        else:
            embed.description = "Nobody is playing tracked games right now."

        # Add footer with instructions for preference buttons
        embed.set_footer(text="Use the buttons below to show or hide your games • Last updated")

        try:
            if message and not force_new:
                logger.info(f"Updating existing message {message.id}")
                await message.edit(embed=embed, view=self.preference_view)
                self.initial_post_done = True
                return True
            else:
                # Create a new message
                try:
                    logger.info("Creating new message in thread")
                    msg = await thread.send(embed=embed, view=self.preference_view)
                    self.state["message_id"] = msg.id
                    await self.save_json(STATE_FILE, self.state)
                    self.initial_post_done = True
                    logger.info(f"Created new message with ID {msg.id}")
                    return True
                except Exception as e:
                    logger.error(f"Failed to create new message: {e}")
                    return False
        except discord.Forbidden as e:
            logger.error(f"Permission error posting message: {e}")
            return False
        except discord.HTTPException as e:
            # Basic backoff on HTTP 429
            status = getattr(e, 'status', None)
            retry_after = getattr(e, 'retry_after', 10)
            if status == 429:
                logger.warning(f"Rate limited while updating embed. Retrying in {retry_after} seconds.")
                await asyncio.sleep(retry_after)
                # Retry once
                try:
                    return await self.update_embed(force_new=force_new)
                except Exception as e2:
                    logger.error(f"Retry failed updating embed: {e2}")
                    return False
            logger.error(f"HTTP error posting message: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error updating embed: {e}")
            return False

    async def schedule_update(self, force_new=False):
        """Debounce/throttle embed updates to avoid rate limits."""
        async with self._embed_update_lock:
            now = asyncio.get_event_loop().time()
            # Merge force_new requests
            self._embed_force_new_pending = self._embed_force_new_pending or force_new

            def _schedule(delay: float):
                if self._embed_update_task and not self._embed_update_task.done():
                    return
                async def runner():
                    try:
                        await asyncio.sleep(delay)
                        await self.update_embed(force_new=self._embed_force_new_pending)
                    finally:
                        self._last_embed_update = asyncio.get_event_loop().time()
                        self._embed_force_new_pending = False
                        self._embed_update_task = None
                self._embed_update_task = asyncio.create_task(runner())

            elapsed = now - self._last_embed_update
            if elapsed >= EMBED_UPDATE_MIN_INTERVAL:
                # Run immediately
                _schedule(0)
            else:
                # Delay until the interval passes
                _schedule(EMBED_UPDATE_MIN_INTERVAL - elapsed)

            # Return True to keep existing calling semantics
            return True

    # ---------- Background Tasks ----------
    @tasks.loop(minutes=5)
    async def ensure_message_exists(self):
        """Check periodically that a message exists, create one if it doesn't"""
        if not self.initial_post_done:
            logger.info("Periodic check: No initial post detected, creating one")
            await self.schedule_update(force_new=True)
        else:
            # Verify the message still exists
            thread = self.bot.get_channel(THREAD_ID)
            if not thread:
                logger.error(f"Thread {THREAD_ID} not found in periodic check")
                return
            if self.state.get("message_id"):
                try:
                    await thread.fetch_message(self.state["message_id"])
                except (discord.NotFound, discord.HTTPException):
                    logger.warning("Message not found in periodic check, creating new one")
                    await self.schedule_update(force_new=True)

    # Task to handle player cleanup
    @tasks.loop(minutes=INACTIVE_CHECK_MINUTES)
    async def cleanup_inactive_users(self):
        """Background task to check and remove inactive users"""
        logger.info("Running cleanup of inactive users")
        await self.cleanup_stale_players()
    
    @cleanup_inactive_users.before_loop
    async def before_cleanup(self):
        """Wait until the bot is ready before starting the task"""
        await self.bot.wait_until_ready()
    
    async def cleanup_stale_players(self):
        """Remove players who haven't been seen for a while"""
        now = datetime.datetime.utcnow()
        max_inactive = datetime.timedelta(hours=MAX_INACTIVE_HOURS)
        removed_count = 0
        
        # Get a list of users to remove from games
        users_to_remove = []
        
        # Find users who haven't been seen recently
        for user_id, last_seen_str in self.state["last_seen"].items():
            try:
                last_seen = datetime.datetime.fromisoformat(last_seen_str)
                time_diff = now - last_seen
                
                if time_diff > max_inactive:
                    logger.info(f"User {user_id} inactive for {time_diff}, marking for removal")
                    users_to_remove.append(user_id)
                    removed_count += 1
            except (ValueError, TypeError) as e:
                logger.error(f"Error parsing timestamp for user {user_id}: {e}")
                # If timestamp is invalid, update it
                self.state["last_seen"][user_id] = now.isoformat()
        
        # Remove inactive users from all games
        update_needed = False
        for user_id in users_to_remove:
            for game, users in list(self.state["games"].items()):
                if user_id in users:
                    users.remove(user_id)
                    update_needed = True
                    # If no users left for this game, remove the game
                    if not users:
                        self.state["games"].pop(game)
        
        # If any users were removed, update the state and embed
        if update_needed:
            await self.save_json(STATE_FILE, self.state)
            await self.schedule_update()
            
        return removed_count

    # ---------- Reset State Command ----------
    @discord.app_commands.command(name="gamemon-resetstate", description="Reset the game state (admin only)")
    async def resetstate(self, interaction: discord.Interaction):
        # Check if user has admin permission
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "You need administrator permissions to use this command.",
                ephemeral=True
            )
            return
            
        # Reset the state to defaults
        self.state = {
            "games": {},
            "message_id": None,
            "last_seen": {}
        }
        
        # Save the empty state
        await self.save_json(STATE_FILE, self.state)
        
        # Create a new embed
        await self.update_embed(force_new=True)
        
        await interaction.response.send_message(
            "Game state has been reset. All tracked games have been cleared.",
            ephemeral=True
        )
        
    # ---------- Toggle Monitoring Command ----------
    @discord.app_commands.command(name="gamemon-togglemonitoring", description="Toggle monitoring for users without preferences (admin only)")
    async def toggle_monitoring(self, interaction: discord.Interaction):
        # Check if user is in the admin list
        if not self.is_admin_user(interaction.user.id):
            await interaction.response.send_message(
                "You don't have permission to use this command.",
                ephemeral=True
            )
            return
        
        global TEMP_DISABLE_DEFAULT_MONITORING
        TEMP_DISABLE_DEFAULT_MONITORING = not TEMP_DISABLE_DEFAULT_MONITORING
        status = "disabled" if TEMP_DISABLE_DEFAULT_MONITORING else "enabled"
        
        await interaction.response.send_message(
            f"Monitoring for users without explicit preferences is now {status}.",
            ephemeral=True
        )
        
    # ---------- Toggle Verbose Logging Command ----------
    @discord.app_commands.command(name="gamemon-toggleverbose", description="Toggle verbose logging (admin only)")
    async def toggle_verbose(self, interaction: discord.Interaction):
        # Check if user is in the admin list
        if not self.is_admin_user(interaction.user.id):
            await interaction.response.send_message(
                "You don't have permission to use this command.",
                ephemeral=True
            )
            return
        
        global VERBOSE_LOGGING
        VERBOSE_LOGGING = not VERBOSE_LOGGING
        
        # Update logging levels
        logging_level = logging.INFO if VERBOSE_LOGGING else logging.WARNING
        logger.setLevel(logging_level)
        
        # Update global noisy loggers accordingly
        if VERBOSE_LOGGING:
            logging.getLogger('discord.gateway').setLevel(logging.INFO)
            logging.getLogger('discord.client').setLevel(logging.INFO)
            logging.getLogger('discord.http').setLevel(logging.WARNING)
        else:
            logging.getLogger('discord.gateway').setLevel(logging.ERROR)
            logging.getLogger('discord.client').setLevel(logging.ERROR)
            logging.getLogger('discord.http').setLevel(logging.ERROR)
        
        status = "enabled" if VERBOSE_LOGGING else "disabled"
        await interaction.response.send_message(
            f"Verbose logging is now {status}.",
            ephemeral=True
        )

async def setup(bot):
    await bot.add_cog(GameMonCog(bot))

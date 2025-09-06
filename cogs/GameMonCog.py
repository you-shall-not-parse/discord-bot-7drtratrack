import discord
from discord.ext import commands, tasks
import json
import os
import datetime
import asyncio
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('GameMonCog')

# ---------------- CONFIG ----------------
GUILD_ID = 1097913605082579024   # Replace with your guild/server ID
THREAD_ID = 1412934277133369494  # replace with your thread ID
TRACKED_USERS = [1109147750932676649]  # list of user IDs to track
IGNORED_GAMES = ["Spotify", "Discord", "Pornhub", "Netflix", "Disney", "Sky TV", "Youtube"]
PREFS_FILE = "game_prefs.json"
STATE_FILE = "game_state.json"
PROMPT_TIMEOUT = 300  # seconds (5 min) -> change here to configure timeout
INACTIVE_CHECK_MINUTES = 60  # how often to check for inactive users
MAX_INACTIVE_HOURS = 12  # maximum time a user can be inactive before removal
# ----------------------------------------

class PreferenceView(discord.ui.View):
    """View with preference buttons for the Now Playing embed"""
    def __init__(self, cog):
        super().__init__(timeout=None)  # No timeout for persistent view
        self.cog = cog
        
    @discord.ui.button(label="Always Accept", style=discord.ButtonStyle.green, custom_id="pref:always_accept")
    async def always_accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_preference(interaction, "always_accept")
        
    @discord.ui.button(label="Ask", style=discord.ButtonStyle.blurple, custom_id="pref:ask")
    async def ask(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_preference(interaction, "ask")
        
    @discord.ui.button(label="Always Reject", style=discord.ButtonStyle.red, custom_id="pref:always_reject")
    async def always_reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_preference(interaction, "always_reject")
        
    async def _set_preference(self, interaction, pref):
        user_id = str(interaction.user.id)
        current_pref = self.cog.prefs.get(user_id, "always_reject")  # Default changed to always_reject
        self.cog.prefs[user_id] = pref
        success = await self.cog.save_json(PREFS_FILE, self.cog.prefs)
        
        if success:
            await interaction.response.send_message(
                f"Preference updated from `{current_pref}` to `{pref}`",
                ephemeral=True
            )
            
            # If preference is ask, send test DM
            if pref == "ask":
                try:
                    await interaction.user.send(
                        "This is a test message to confirm you can receive DMs from this bot. " +
                        "You'll receive prompts here when you play games."
                    )
                except discord.Forbidden:
                    await interaction.followup.send(
                        "⚠️ I couldn't send you a DM. Please enable DMs from server members to receive game prompts.",
                        ephemeral=True
                    )
                except Exception as e:
                    logger.error(f"Error sending test DM to {interaction.user.name}: {e}")
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
        if "players" not in self.state:
            self.state["players"] = {}
        if "message_id" not in self.state:
            self.state["message_id"] = None
        if "last_seen" not in self.state:
            self.state["last_seen"] = {}
            
        # Flag to track if initial posting has been done
        self.initial_post_done = False
        
        # File lock to prevent race conditions
        self.file_lock = asyncio.Lock()
        
        # Preference view for the embed
        self.preference_view = PreferenceView(self)
        
        # Start background tasks
        self.cleanup_inactive_users.start()
        self.ensure_message_exists.start()

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

    # ---------- Game Name Normalization ----------
    def normalize_game_name(self, game_name):
        """Normalize game names by removing special characters and standardizing case"""
        if not game_name:
            return None
        
        # Replace trademark, registered, and copyright symbols
        normalized = game_name.replace("™", "").replace("®", "").replace("©", "")
        
        # Remove extra whitespace and trim
        normalized = " ".join(normalized.split())
        
        logger.info(f"Normalized game name: '{game_name}' -> '{normalized}'")
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
        
        # Register guild commands now that the bot is fully connected
        if GUILD_ID != 0:
            try:
                # Make sure we're registered with the right guild
                guild = self.bot.get_guild(GUILD_ID)
                if guild:
                    logger.info(f"Registering commands for guild: {guild.name}")
                    # Register the commands with the specific guild
                    self.bot.tree.copy_global_to(guild=discord.Object(id=GUILD_ID))
                    await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))
                    logger.info(f"Successfully registered commands for guild ID: {GUILD_ID}")
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
        
        # Delete existing message if it exists
        if self.state.get("message_id"):
            try:
                old_message = await thread.fetch_message(self.state["message_id"])
                await old_message.delete()
                logger.info(f"Deleted previous message: {self.state['message_id']}")
                self.state["message_id"] = None
                await self.save_json(STATE_FILE, self.state)
            except (discord.NotFound, discord.HTTPException) as e:
                logger.warning(f"Could not delete previous message: {e}")
        
        # Add the persistent view to the bot
        self.bot.add_view(self.preference_view)
        
        # Force an immediate update to create a new message
        await self.update_embed(force_new=True)

    # ---------- Presence Pref Command ----------
    @discord.app_commands.command(name="presencepref", description="Set your game listing preference")
    @discord.app_commands.describe(pref="ask / always_accept / always_reject")
    async def presencepref(self, interaction: discord.Interaction, pref: str):
        if pref not in ["ask", "always_accept", "always_reject"]:
            await interaction.response.send_message(
                "Invalid preference. Use ask / always_accept / always_reject.", 
                ephemeral=True
            )
            return

        current_pref = self.prefs.get(str(interaction.user.id), "always_reject")  # Default changed to always_reject
        self.prefs[str(interaction.user.id)] = pref
        success = await self.save_json(PREFS_FILE, self.prefs)
        
        if success:
            await interaction.response.send_message(
                f"Preference updated from `{current_pref}` to `{pref}`", 
                ephemeral=True
            )
            
            # If changing to "ask", try to send a test DM
            if pref == "ask":
                try:
                    test_dm = await interaction.user.send(
                        "This is a test message to confirm you can receive DMs from this bot. " +
                        "You'll receive prompts here when you play games."
                    )
                    logger.info(f"Successfully sent test DM to {interaction.user.name} ({interaction.user.id})")
                except discord.Forbidden:
                    logger.warning(f"Cannot DM user {interaction.user.name} ({interaction.user.id}). DMs may be disabled.")
                    await interaction.followup.send(
                        "⚠️ I couldn't send you a DM. Please enable DMs from server members to receive game prompts.",
                        ephemeral=True
                    )
                except Exception as e:
                    logger.error(f"Error sending test DM to {interaction.user.name} ({interaction.user.id}): {e}")
        else:
            await interaction.response.send_message(
                "Error saving preference. Please try again.", 
                ephemeral=True
            )

    # ---------- Manual Refresh Command ----------
    @discord.app_commands.command(name="refreshgames", description="Refresh the Now Playing list")
    async def refreshgames(self, interaction: discord.Interaction):
        cleaned = await self.cleanup_stale_players()
        
        # Force a new message regardless of existing one
        success = await self.update_embed(force_new=True)
        
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
    @discord.app_commands.command(name="fixpreference", description="Fix your game display preference and add current game")
    async def fixpreference(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        
        # Set preference to always_accept
        old_pref = self.prefs.get(user_id, "always_reject")  # Default changed to always_reject
        self.prefs[user_id] = "always_accept"
        await self.save_json(PREFS_FILE, self.prefs)
        
        # Check for current games and force add to state
        member = None
        for guild in self.bot.guilds:
            member = guild.get_member(interaction.user.id)
            if member:
                break
        
        current_game = None
        if member and member.activities:
            for activity in member.activities:
                game = self.get_game_from_activity(activity)
                if game and game not in IGNORED_GAMES:
                    current_game = game
                    break
        
        if current_game:
            # Force add to state
            self.state["players"][user_id] = current_game
            self.state["last_seen"][user_id] = datetime.datetime.utcnow().isoformat()
            await self.save_json(STATE_FILE, self.state)
            logger.info(f"Force added game for {interaction.user.name}: {current_game}")
            
            # Update the embed
            success = await self.update_embed()
            
            if success:
                await interaction.response.send_message(
                    f"Changed preference from `{old_pref}` to `always_accept` and added your current game: {current_game}",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "Fixed preferences but failed to update the message. Try /refreshgames.",
                    ephemeral=True
                )
        else:
            await interaction.response.send_message(
                f"Changed preference from `{old_pref}` to `always_accept`, but no game currently detected.",
                ephemeral=True
            )

    # ---------- Get Current Games Command ----------
    @discord.app_commands.command(name="currentgames", description="Show currently detected games for tracked users")
    async def currentgames(self, interaction: discord.Interaction):
        # Build a report of current games being played by tracked users
        result = []
        result.append("**Current Games for Tracked Users:**")
        
        for user_id in TRACKED_USERS:
            try:
                user = self.bot.get_user(user_id)
                username = user.name if user else f"User {user_id}"
                result.append(f"\n**{username}**:")
                
                member = None
                for guild in self.bot.guilds:
                    member = guild.get_member(user_id)
                    if member:
                        break
                        
                if member and member.activities:
                    for activity in member.activities:
                        raw_game = None
                        if hasattr(activity, 'name'):
                            raw_game = activity.name
                        
                        normalized_game = self.get_game_from_activity(activity)
                        activity_type = getattr(activity, 'type', 'Unknown')
                        
                        result.append(f"- Activity Type: {activity_type}")
                        result.append(f"  Raw Name: {raw_game}")
                        result.append(f"  Details: {getattr(activity, 'details', 'None')}")
                        result.append(f"  State: {getattr(activity, 'state', 'None')}")
                        result.append(f"  Normalized Game: {normalized_game}")
                        
                        # Xbox specific info
                        if hasattr(activity, 'assets') and activity.assets:
                            large_image = getattr(activity.assets, 'large_image', 'None')
                            small_image = getattr(activity.assets, 'small_image', 'None')
                            result.append(f"  Assets - Large: {large_image}, Small: {small_image}")
                else:
                    result.append("- No activities detected")
            except Exception as e:
                result.append(f"- Error checking user: {str(e)}")
                
        # Also show what's in the current state
        result.append("\n**Currently Tracked Games:**")
        for uid, game in self.state["players"].items():
            user = self.bot.get_user(int(uid))
            username = user.name if user else f"User {uid}"
            result.append(f"- {username}: {game}")
        
        # Show user preferences
        result.append("\n**User Preferences:**")
        for uid, pref in self.prefs.items():
            user = self.bot.get_user(int(uid))
            username = user.name if user else f"User {uid}"
            result.append(f"- {username}: {pref}")
            
        await interaction.response.send_message("\n".join(result), ephemeral=True)

    # ---------- Event: Member updates ----------
    @commands.Cog.listener()
    async def on_presence_update(self, before, after):
        # Only track whitelisted users
        if after.id not in TRACKED_USERS:
            return

        # Use our improved game detection method
        before_game = None
        after_game = None
        
        # Check for gaming activities in before state
        for activity in before.activities:
            game = self.get_game_from_activity(activity)
            if game:
                before_game = game
                break
        
        # Check for gaming activities in after state  
        for activity in after.activities:
            game = self.get_game_from_activity(activity)
            if game:
                after_game = game
                break

        # Debug logging
        logger.info(f"Presence update for {after.name} ({after.id}): {before_game} -> {after_game}")

        # If unchanged or ignored, do nothing
        if before_game == after_game:
            logger.debug(f"Game unchanged for {after.name}: {after_game}")
            return
            
        if after_game in IGNORED_GAMES or before_game in IGNORED_GAMES:
            logger.debug(f"Ignored game for {after.name}: {after_game or before_game}")
            return

        user_id = str(after.id)
        
        # Update last seen timestamp
        self.state["last_seen"][user_id] = datetime.datetime.utcnow().isoformat()
        await self.save_json(STATE_FILE, self.state)

        # Started playing
        if after_game and not before_game:
            logger.info(f"User {after.name} ({after.id}) started playing {after_game}")
            pref = self.prefs.get(user_id, "always_reject")  # Default changed to always_reject
            logger.info(f"User preference: {pref}")
            
            if pref == "always_accept":
                self.state["players"][user_id] = after_game
                await self.save_json(STATE_FILE, self.state)
                logger.info(f"Auto-accepted game for {after.name}: {after_game}")
                logger.info(f"Updated state: {self.state['players']}")
                await self.update_embed()
            elif pref == "ask":
                await self.prompt_user(after, after_game)
            # always_reject does nothing

        # Changed games
        elif after_game and before_game and after_game != before_game:
            logger.info(f"User {after.name} ({after.id}) changed games: {before_game} -> {after_game}")
            # If user was already in the players list, update with new game
            if user_id in self.state["players"]:
                pref = self.prefs.get(user_id, "always_reject")  # Default changed to always_reject
                logger.info(f"User preference: {pref}")
                
                if pref == "always_accept":
                    self.state["players"][user_id] = after_game
                    await self.save_json(STATE_FILE, self.state)
                    logger.info(f"Auto-updated game for {after.name}: {after_game}")
                    logger.info(f"Updated state: {self.state['players']}")
                    await self.update_embed()
                elif pref == "ask":
                    await self.prompt_user(after, after_game)
                # always_reject does nothing

        # Stopped playing
        elif before_game and not after_game:
            logger.info(f"User {after.name} ({after.id}) stopped playing {before_game}")
            if user_id in self.state["players"]:
                self.state["players"].pop(user_id)
                await self.save_json(STATE_FILE, self.state)
                logger.info(f"Removed game for {after.name}")
                logger.info(f"Updated state: {self.state['players']}")
                await self.update_embed()

    # ---------- DM Prompt ----------
    async def prompt_user(self, user, game):
        class Confirm(discord.ui.View):
            def __init__(self, cog, user_id, game):
                super().__init__(timeout=PROMPT_TIMEOUT)
                self.cog = cog
                self.user_id = user_id
                self.game = game
                self.responded = False
                self.message = None

            @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.green)
            async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.responded = True
                self.cog.state["players"][self.user_id] = self.game
                await self.cog.save_json(STATE_FILE, self.cog.state)
                logger.info(f"User {user.name} accepted game: {self.game}")
                logger.info(f"Updated state: {self.cog.state['players']}")
                await self.cog.update_embed()
                await interaction.response.edit_message(content=f"Accepted: {self.game}", view=None)

            @discord.ui.button(label="❌ Reject", style=discord.ButtonStyle.red)
            async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.responded = True
                logger.info(f"User {user.name} rejected game: {self.game}")
                await interaction.response.edit_message(content=f"Rejected: {self.game}", view=None)

            async def on_timeout(self):
                if not self.responded and self.message:
                    try:
                        await self.message.edit(content=f"Timed out. Game not shown: {self.game}", view=None)
                        logger.info(f"Prompt timed out for {user.name}: {self.game}")
                    except Exception as e:
                        logger.error(f"Error updating prompt timeout message: {e}")

        try:
            # Check if user can receive DMs first
            view = Confirm(self, str(user.id), game)
            try:
                dm = await user.send(
                    f"Do you want to show `{game}` in the Now Playing list?",
                    view=view
                )
                # Store reference to message in view for timeout handling
                view.message = dm
                logger.info(f"Sent game prompt to {user.name} ({user.id}) for {game}")
            except discord.Forbidden:
                # DMs are disabled, so auto-accept and notify in console
                logger.warning(f"Cannot DM user {user.name} ({user.id}). DMs disabled. Auto-accepting game.")
                self.state["players"][str(user.id)] = game
                await self.save_json(STATE_FILE, self.state)
                await self.update_embed()
            except Exception as e:
                logger.error(f"Error sending prompt to {user.name} ({user.id}): {e}")
                # On error, also auto-accept to prevent missing games
                self.state["players"][str(user.id)] = game
                await self.save_json(STATE_FILE, self.state)
                await self.update_embed()
        except Exception as e:
            logger.error(f"Unexpected error in prompt_user for {user.name} ({user.id}): {e}")

    # ---------- Embed Update ----------
    async def update_embed(self, force_new=False):
        """Update the embed message in the thread, or create a new one if needed or forced"""
        thread = self.bot.get_channel(THREAD_ID)
        if not thread:
            logger.error(f"Thread with ID {THREAD_ID} not found. Cannot post message.")
            return False

        message = None
        if self.state.get("message_id") and not force_new:
            try:
                message = await thread.fetch_message(self.state["message_id"])
                logger.info(f"Found existing message {self.state['message_id']}")
            except discord.NotFound:
                logger.warning(f"Message {self.state['message_id']} not found, will create new")
                message = None
            except discord.HTTPException as e:
                logger.error(f"HTTP error fetching message: {e}")
                message = None

        logger.info(f"Creating embed with players: {self.state['players']}")
        embed = discord.Embed(title="🎮 Now Playing", color=discord.Color.green())
        embed.timestamp = discord.utils.utcnow()
        
        if self.state["players"]:
            for uid, game in self.state["players"].items():
                try:
                    # Try to fetch user from cache
                    user = self.bot.get_user(int(uid))
                    if user:
                        embed.add_field(
                            name=user.display_name,
                            value=game,
                            inline=False
                        )
                    else:
                        # Try to fetch from API
                        try:
                            user = await self.bot.fetch_user(int(uid))
                            embed.add_field(
                                name=user.display_name,
                                value=game,
                                inline=False
                            )
                        except Exception as fetch_err:
                            logger.error(f"Error fetching user {uid}: {fetch_err}")
                            embed.add_field(
                                name=f"Unknown User (ID: {uid})",
                                value=game,
                                inline=False
                            )
                except Exception as e:
                    logger.error(f"Error adding field for user {uid}: {e}")
                    embed.add_field(
                        name=f"User {uid} (Error: {type(e).__name__})",
                        value=game,
                        inline=False
                    )
        else:
            embed.description = "Nobody is playing tracked games right now."

        # Add footer with instructions for preference buttons
        embed.set_footer(text="Use the buttons below to set your preference • Last updated")

        try:
            if message and not force_new:
                logger.info(f"Updating existing message {message.id}")
                await message.edit(embed=embed, view=self.preference_view)
                self.initial_post_done = True
                return True
            else:
                # Only create a new message if absolutely necessary
                if force_new or not self.state.get("message_id"):
                    logger.info("Creating new message in thread")
                    msg = await thread.send(embed=embed, view=self.preference_view)
                    self.state["message_id"] = msg.id
                    await self.save_json(STATE_FILE, self.state)
                    self.initial_post_done = True
                    logger.info(f"Created new message with ID {msg.id}")
                    return True
                else:
                    logger.error("Failed to update existing message and not allowed to create new one")
                    return False
        except discord.Forbidden as e:
            logger.error(f"Permission error posting message: {e}")
            return False
        except discord.HTTPException as e:
            logger.error(f"HTTP error posting message: {e}")
            # Only create a new message if force_new is True
            if force_new:
                try:
                    logger.info("Attempting to create new message after failure")
                    msg = await thread.send(embed=embed, view=self.preference_view)
                    self.state["message_id"] = msg.id
                    await self.save_json(STATE_FILE, self.state)
                    self.initial_post_done = True
                    logger.info(f"Created new message with ID {msg.id}")
                    return True
                except Exception as new_err:
                    logger.error(f"Failed to create new message: {new_err}")
                    return False
            return False
        except Exception as e:
            logger.error(f"Unexpected error updating embed: {e}")
            return False

    # ---------- Background Tasks ----------
    
    # Task to ensure a message exists
    @tasks.loop(minutes=5)
    async def ensure_message_exists(self):
        """Check periodically that a message exists, create one if it doesn't"""
        if not self.initial_post_done:
            logger.info("Periodic check: No initial post detected, creating one")
            await self.update_embed(force_new=True)
        else:
            # Verify the message still exists
            thread = self.bot.get_channel(THREAD_ID)
            if not thread:
                logger.error(f"Thread {THREAD_ID} not found in periodic check")
                return
                
            if self.state.get("message_id"):
                try:
                    await thread.fetch_message(self.state["message_id"])
                    # Message exists, no action needed
                except (discord.NotFound, discord.HTTPException):
                    logger.warning("Message not found in periodic check, creating new one")
                    await self.update_embed(force_new=True)
    
    @ensure_message_exists.before_loop
    async def before_ensure_message(self):
        """Wait until the bot is ready before starting the task"""
        await self.bot.wait_until_ready()
        # Wait an additional 10 seconds to make sure on_ready has completed
        await asyncio.sleep(10)

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
        
        # Get a list of UIDs to remove (to avoid modifying dict during iteration)
        to_remove = []
        
        for uid, game in self.state["players"].items():
            # Get last seen time
            last_seen_str = self.state["last_seen"].get(uid)
            
            if not last_seen_str:
                # If no timestamp, add it with current time and keep the player
                self.state["last_seen"][uid] = now.isoformat()
                continue
                
            try:
                # Parse the timestamp
                last_seen = datetime.datetime.fromisoformat(last_seen_str)
                
                # Calculate time difference
                time_diff = now - last_seen
                
                # If too long, mark for removal
                if time_diff > max_inactive:
                    logger.info(f"Marking user {uid} for removal - inactive for {time_diff}")
                    to_remove.append(uid)
                    removed_count += 1
            except (ValueError, TypeError) as e:
                logger.error(f"Error parsing timestamp for user {uid}: {e}")
                # If timestamp is invalid, update it and keep the player
                self.state["last_seen"][uid] = now.isoformat()
        
        # Remove inactive players
        for uid in to_remove:
            if uid in self.state["players"]:
                self.state["players"].pop(uid)
                logger.info(f"Removed inactive user {uid}")
            
        # If any players were removed, update the embed
        if to_remove:
            await self.save_json(STATE_FILE, self.state)
            await self.update_embed()
            
        return removed_count

    # ---------- Reset State Command ----------
    @discord.app_commands.command(name="resetstate", description="Reset the game state (admin only)")
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
            "players": {},
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

async def setup(bot):
    await bot.add_cog(GameMonCog(bot))

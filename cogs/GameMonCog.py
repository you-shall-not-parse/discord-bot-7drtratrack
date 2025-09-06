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
MAX_INACTIVE_HOURS = 8  # maximum time a user can be inactive before removal
# ----------------------------------------

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
        
        # Start background tasks
        self.cleanup_inactive_users.start()
        self.ensure_message_exists.start()

    # ---------- Register Guild Commands ----------
    async def cog_load(self):
        """Register guild-specific commands when the cog loads"""
        if GUILD_ID == 0:
            logger.warning("GUILD_ID is not set! Please set a valid guild ID in the config.")
            return
            
        try:
            # Register the commands with the specific guild
            self.bot.tree.copy_global_to(guild=discord.Object(id=GUILD_ID))
            await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))
            logger.info(f"Registered commands for guild ID: {GUILD_ID}")
        except Exception as e:
            logger.error(f"Failed to register guild commands: {e}")

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

    # ---------- Bot Ready Event ----------
    @commands.Cog.listener()
    async def on_ready(self):
        """Called when the bot is ready and connected"""
        logger.info(f"GameMonCog ready - Connected as {self.bot.user}")
        
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
        
        # Force an immediate update to ensure a message exists
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

        current_pref = self.prefs.get(str(interaction.user.id), "ask")
        self.prefs[str(interaction.user.id)] = pref
        success = await self.save_json(PREFS_FILE, self.prefs)
        
        if success:
            await interaction.response.send_message(
                f"Preference updated from `{current_pref}` to `{pref}`", 
                ephemeral=True
            )
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

    # ---------- Event: Member updates ----------
    @commands.Cog.listener()
    async def on_presence_update(self, before, after):
        # Only track whitelisted users
        if after.id not in TRACKED_USERS:
            return

        # ISSUE 2: Improved Activity Detection
        # Check for all game activity types, not just discord.Game
        before_game = None
        after_game = None
        
        # Check for gaming activities across different activity types
        for activity in before.activities:
            # Game type
            if isinstance(activity, discord.Game):
                before_game = activity.name
                break
            # Rich Presence game
            elif (isinstance(activity, discord.Activity) and 
                  activity.type == discord.ActivityType.playing):
                before_game = activity.name
                break
            # Custom activity that mentions a game
            elif (isinstance(activity, discord.CustomActivity) and 
                  activity.name and "playing" in activity.name.lower()):
                before_game = activity.name.split("playing ", 1)[1].strip()
                break
        
        # Same checks for after state
        for activity in after.activities:
            if isinstance(activity, discord.Game):
                after_game = activity.name
                break
            elif (isinstance(activity, discord.Activity) and 
                  activity.type == discord.ActivityType.playing):
                after_game = activity.name
                break
            elif (isinstance(activity, discord.CustomActivity) and 
                  activity.name and "playing" in activity.name.lower()):
                after_game = activity.name.split("playing ", 1)[1].strip()
                break

        # If unchanged or ignored, do nothing
        if before_game == after_game:
            return
        if after_game in IGNORED_GAMES or before_game in IGNORED_GAMES:
            return

        user_id = str(after.id)
        
        # Update last seen timestamp
        self.state["last_seen"][user_id] = datetime.datetime.utcnow().isoformat()
        await self.save_json(STATE_FILE, self.state)

        # Started playing
        if after_game and not before_game:
            logger.info(f"User {after.name} ({after.id}) started playing {after_game}")
            pref = self.prefs.get(user_id, "ask")
            if pref == "always_accept":
                self.state["players"][user_id] = after_game
                await self.save_json(STATE_FILE, self.state)
                await self.update_embed()
            elif pref == "ask":
                await self.prompt_user(after, after_game)
            # always_reject does nothing

        # Stopped playing
        if before_game and not after_game:
            logger.info(f"User {after.name} ({after.id}) stopped playing {before_game}")
            if user_id in self.state["players"]:
                self.state["players"].pop(user_id)
                await self.save_json(STATE_FILE, self.state)
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
                await self.cog.update_embed()
                await interaction.response.edit_message(content=f"Accepted: {self.game}", view=None)

            @discord.ui.button(label="❌ Reject", style=discord.ButtonStyle.red)
            async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.responded = True
                await interaction.response.edit_message(content=f"Rejected: {self.game}", view=None)

            async def on_timeout(self):
                if not self.responded and self.message:
                    try:
                        await self.message.edit(content=f"Timed out. Game not shown: {self.game}", view=None)
                    except Exception as e:
                        logger.error(f"Error updating prompt timeout message: {e}")

        try:
            view = Confirm(self, str(user.id), game)
            dm = await user.send(
                f"Do you want to show `{game}` in the Now Playing list?",
                view=view
            )
            # Store reference to message in view for timeout handling
            view.message = dm
            logger.info(f"Sent game prompt to {user.name} ({user.id}) for {game}")
        except discord.Forbidden:
            logger.warning(f"Cannot DM user {user.name} ({user.id}). DMs may be disabled.")
        except Exception as e:
            logger.error(f"Error sending prompt to {user.name} ({user.id}): {e}")

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

        embed = discord.Embed(title="🎮 Now Playing", color=discord.Color.green())
        embed.timestamp = discord.utils.utcnow()
        
        if self.state["players"]:
            for uid, game in self.state["players"].items():
                # ISSUE 4: Improved User Resolution
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

        # Add footer with timestamp
        embed.set_footer(text=f"Last updated")

        try:
            if not message or force_new:
                logger.info("Creating new message in thread")
                msg = await thread.send(embed=embed)
                self.state["message_id"] = msg.id
                await self.save_json(STATE_FILE, self.state)
                self.initial_post_done = True
                logger.info(f"Created new message with ID {msg.id}")
                return True
            else:
                logger.info(f"Updating existing message {message.id}")
                await message.edit(embed=embed)
                self.initial_post_done = True
                return True
        except discord.Forbidden as e:
            logger.error(f"Permission error posting message: {e}")
            return False
        except discord.HTTPException as e:
            logger.error(f"HTTP error posting message: {e}")
            # If edit fails, try to send a new message
            try:
                logger.info("Attempting to create new message after failure")
                msg = await thread.send(embed=embed)
                self.state["message_id"] = msg.id
                await self.save_json(STATE_FILE, self.state)
                self.initial_post_done = True
                logger.info(f"Created new message with ID {msg.id}")
                return True
            except Exception as new_err:
                logger.error(f"Failed to create new message: {new_err}")
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

    # ISSUE 6: Cleanup for Inactive Users
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

async def setup(bot):
    await bot.add_cog(GameMonCog(bot))

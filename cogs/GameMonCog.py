import discord
from discord.ext import commands, tasks
import json
import os
import datetime

# ---------------- CONFIG ----------------
THREAD_ID = 1412934277133369494  # replace with your thread ID
TRACKED_USERS = [1109147750932676649]  # list of user IDs to track
IGNORED_GAMES = ["Spotify", "Discord", "Pornhub", "Netflix", "Disney", Sky TV", "Youtube"]
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
            
        # Start background task for cleaning up inactive users
        self.cleanup_inactive_users.start()

    def cog_unload(self):
        # Ensure we stop the background task when the cog is unloaded
        self.cleanup_inactive_users.cancel()

    # ---------- JSON Helpers ----------
    def load_json(self, filename):
        if os.path.exists(filename):
            with open(filename, "r") as f:
                return json.load(f)
        return {}

    def save_json(self, filename, data):
        with open(filename, "w") as f:
            json.dump(data, f, indent=4)

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

        self.prefs[str(interaction.user.id)] = pref
        self.save_json(PREFS_FILE, self.prefs)
        await interaction.response.send_message(f"Preference set to `{pref}`", ephemeral=True)

    # ---------- Manual Cleanup Command ----------
    @discord.app_commands.command(name="refreshgames", description="Refresh the Now Playing list")
    async def refreshgames(self, interaction: discord.Interaction):
        cleaned = await self.cleanup_stale_players()
        await interaction.response.send_message(
            f"Refreshed game list. Removed {cleaned} inactive players.",
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

        # Started playing
        if after_game and not before_game:
            pref = self.prefs.get(user_id, "ask")
            if pref == "always_accept":
                self.state["players"][user_id] = after_game
                self.save_json(STATE_FILE, self.state)
                await self.update_embed()
            elif pref == "ask":
                await self.prompt_user(after, after_game)

        # Stopped playing
        if before_game and not after_game:
            if user_id in self.state["players"]:
                self.state["players"].pop(user_id)
                self.save_json(STATE_FILE, self.state)
                await self.update_embed()

    # ---------- DM Prompt ----------
    async def prompt_user(self, user, game):
        class Confirm(discord.ui.View):
            def __init__(self, cog, user_id, game):
                super().__init__(timeout=PROMPT_TIMEOUT)
                self.cog = cog
                self.user_id = user_id
                self.game = game

            @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.green)
            async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.cog.state["players"][self.user_id] = self.game
                self.cog.save_json(STATE_FILE, self.cog.state)
                await self.cog.update_embed()
                await interaction.response.edit_message(content=f"Accepted: {self.game}", view=None)

            @discord.ui.button(label="❌ Reject", style=discord.ButtonStyle.red)
            async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
                await interaction.response.edit_message(content=f"Rejected: {self.game}", view=None)

            async def on_timeout(self):
                # Auto reject silently
                pass

        try:
            await user.send(
                f"Do you want to show `{game}` in the Now Playing list?",
                view=Confirm(self, str(user.id), game)
            )
        except discord.Forbidden:
            pass  # can't DM user

    # ---------- Embed Update ----------
    async def update_embed(self):
        thread = self.bot.get_channel(THREAD_ID)
        if not thread:
            return

        message = None
        if self.state.get("message_id"):
            try:
                message = await thread.fetch_message(self.state["message_id"])
            except (discord.NotFound, discord.HTTPException):
                message = None

        embed = discord.Embed(title="🎮 Now Playing", color=discord.Color.green())
        embed.timestamp = discord.utils.utcnow()  # Add timestamp to show when updated
        
        if self.state["players"]:
            for uid, game in self.state["players"].items():
                # ISSUE 4: Improved User Resolution
                try:
                    # Try to fetch user, if it fails, we'll handle it
                    user = self.bot.get_user(int(uid))
                    if user:
                        # User found, use their display name
                        embed.add_field(
                            name=user.display_name,
                            value=game,
                            inline=False
                        )
                    else:
                        # User not found but no exception - possibly not in cache
                        # Try to fetch from API
                        try:
                            user = await self.bot.fetch_user(int(uid))
                            embed.add_field(
                                name=user.display_name,
                                value=game,
                                inline=False
                            )
                        except:
                            # Last resort fallback
                            embed.add_field(
                                name=f"Unknown User (ID: {uid})",
                                value=game,
                                inline=False
                            )
                except Exception as e:
                    # Comprehensive error handling
                    embed.add_field(
                        name=f"User {uid} (Error: {type(e).__name__})",
                        value=game,
                        inline=False
                    )
        else:
            embed.description = "Nobody is playing tracked games right now."

        if not message:
            try:
                msg = await thread.send(embed=embed)
                self.state["message_id"] = msg.id
                self.save_json(STATE_FILE, self.state)
            except (discord.Forbidden, discord.HTTPException) as e:
                print(f"Error sending message: {e}")
        else:
            try:
                await message.edit(embed=embed)
            except (discord.Forbidden, discord.HTTPException) as e:
                print(f"Error editing message: {e}")
                # If edit fails, try to send a new message
                try:
                    msg = await thread.send(embed=embed)
                    self.state["message_id"] = msg.id
                    self.save_json(STATE_FILE, self.state)
                except:
                    pass

    # ISSUE 6: Cleanup for Inactive Users
    @tasks.loop(minutes=INACTIVE_CHECK_MINUTES)
    async def cleanup_inactive_users(self):
        """Background task to check and remove inactive users"""
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
                    to_remove.append(uid)
                    removed_count += 1
            except (ValueError, TypeError):
                # If timestamp is invalid, update it and keep the player
                self.state["last_seen"][uid] = now.isoformat()
        
        # Remove inactive players
        for uid in to_remove:
            if uid in self.state["players"]:
                self.state["players"].pop(uid)
            
        # If any players were removed, update the embed
        if to_remove:
            self.save_json(STATE_FILE, self.state)
            await self.update_embed()
            
        return removed_count

async def setup(bot):
    await bot.add_cog(GameMonCog(bot))

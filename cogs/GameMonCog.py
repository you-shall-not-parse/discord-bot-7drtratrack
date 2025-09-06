import discord
from discord.ext import commands
import json
import os

# ---------------- CONFIG ----------------
THREAD_ID = 1412934277133369494  # replace with your thread ID
TRACKED_USERS = [1109147750932676649]  # list of user IDs to track
IGNORED_GAMES = ["Spotify", "Discord", "Visual Studio Code"]
PREFS_FILE = "game_prefs.json"
STATE_FILE = "game_state.json"
PROMPT_TIMEOUT = 300  # seconds (5 min) -> change here to configure timeout
# ----------------------------------------

class GameMonCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.prefs = self.load_json(PREFS_FILE)
        self.state = self.load_json(STATE_FILE)
        if "players" not in self.state:
            self.state["players"] = {}
        if "message_id" not in self.state:
            self.state["message_id"] = None

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

    # ---------- Event: Member updates ----------
    @commands.Cog.listener()
    async def on_presence_update(self, before, after):
        # Only track whitelisted users
        if after.id not in TRACKED_USERS:
            return

        before_game = next((a.name for a in before.activities if isinstance(a, discord.Game)), None)
        after_game = next((a.name for a in after.activities if isinstance(a, discord.Game)), None)

        # If unchanged or ignored, do nothing
        if before_game == after_game:
            return
        if after_game in IGNORED_GAMES or before_game in IGNORED_GAMES:
            return

        user_id = str(after.id)

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
        if self.state["players"]:
            for uid, game in self.state["players"].items():
                user = self.bot.get_user(int(uid))
                embed.add_field(
                    name=user.display_name if user else f"User {uid}",
                    value=game,
                    inline=False
                )
        else:
            embed.description = "Nobody is playing tracked games right now."

        if not message:
            msg = await thread.send(embed=embed)
            self.state["message_id"] = msg.id
            self.save_json(STATE_FILE, self.state)
        else:
            await message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(GameMonCog(bot))

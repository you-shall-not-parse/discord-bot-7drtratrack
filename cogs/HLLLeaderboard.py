# Requires: pip install aiosqlite

import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Select, Modal, TextInput
import aiosqlite
import random
import datetime

# ---------------- Config ----------------
GUILD_ID = 1097913605082579024  # replace with your guild ID
LEADERBOARD_CHANNEL_ID = 1419010804832800859  # replace with your leaderboard channel
SUBMISSIONS_CHANNEL_ID = 1419010992578363564  # replace with your submissions channel
DB_FILE = "leaderboard.db"

STATS = ["Kills", "Artillery Kills", "Vehicles Destroyed", "Killstreak", "Satchel Kills"]

# ---------------- Database (async with aiosqlite) ----------------
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                stat TEXT,
                value INTEGER,
                submitted_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await db.commit()

# ---------------- Cog ----------------
class HLLLeaderboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._synced = False       # ensure we sync app commands once
        self._db_initialized = False
        self._view_registered = False  # persistent view registered once

    async def _get_channel(self, channel_id: int):
        """Try cache first, then API as a fallback."""
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                channel = None
        return channel

    async def get_leaderboard_message(self):
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute("SELECT value FROM metadata WHERE key = ?", ("leaderboard_message_id",))
            row = await cursor.fetchone()

        if not row:
            return None

        channel = await self._get_channel(LEADERBOARD_CHANNEL_ID)
        if not channel:
            return None

        try:
            return await channel.fetch_message(int(row[0]))
        except Exception:
            return None

    async def set_leaderboard_message(self, message_id: int):
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute(
                "INSERT INTO metadata(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("leaderboard_message_id", str(message_id)),
            )
            await db.commit()

    async def build_leaderboard_embed(self, monthly: bool = False):
        embed = discord.Embed(
            title="Hell Let Loose Leaderboard" + (" - This Month" if monthly else ""),
            color=discord.Color.dark_gold(),
        )

        async with aiosqlite.connect(DB_FILE) as db:
            for stat in STATS:
                if monthly:
                    now = datetime.datetime.utcnow()
                    start_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                    cursor = await db.execute(
                        "SELECT user_id, SUM(value) FROM submissions "
                        "WHERE stat=? AND submitted_at>=? "
                        "GROUP BY user_id ORDER BY SUM(value) DESC LIMIT 5",
                        (stat, start_month.isoformat()),
                    )
                else:
                    cursor = await db.execute(
                        "SELECT user_id, SUM(value) FROM submissions "
                        "WHERE stat=? GROUP BY user_id ORDER BY SUM(value) DESC LIMIT 5",
                        (stat,),
                    )

                rows = await cursor.fetchall()
                if rows:
                    lines = []
                    for idx, (user_id, total) in enumerate(rows, 1):
                        user = self.bot.get_user(user_id)
                        name = user.mention if user else f"<@{user_id}>"
                        lines.append(f"**{idx}.** {name} — {total}")
                    embed.add_field(name=stat, value="\n".join(lines), inline=False)
                else:
                    embed.add_field(name=stat, value="No data yet", inline=False)

        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M GMT")
        embed.set_footer(text=f"Last updated: {now_str}")
        return embed

    async def update_leaderboard(self):
        channel = await self._get_channel(LEADERBOARD_CHANNEL_ID)
        if not channel:
            print("HLLLeaderboard: LEADERBOARD_CHANNEL_ID not found. Skipping update.")
            return

        embed = await self.build_leaderboard_embed(monthly=False)
        msg = await self.get_leaderboard_message()

        if msg:
            try:
                await msg.edit(embed=embed, view=LeaderboardView(self))
            except Exception as e:
                print(f"HLLLeaderboard: Failed to edit leaderboard message: {e}")
        else:
            try:
                new_msg = await channel.send(embed=embed, view=LeaderboardView(self))
                await self.set_leaderboard_message(new_msg.id)
            except Exception as e:
                print(f"HLLLeaderboard: Failed to send leaderboard message: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        # Initialize DB once (async)
        if not self._db_initialized:
            try:
                await init_db()
                self._db_initialized = True
            except Exception as e:
                print(f"HLLLeaderboard: DB init failed: {e}")

        # Register persistent view once so interactions work after restart
        if not self._view_registered:
            try:
                self.bot.add_view(LeaderboardView(self))  # persistent (timeout=None + custom_id)
                self._view_registered = True
            except Exception as e:
                print(f"HLLLeaderboard: Failed to register persistent view: {e}")

        # Sync slash commands for this guild (do once)
        if not self._synced:
            try:
                await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))
                self._synced = True
            except Exception as e:
                print(f"HLLLeaderboard: Command sync failed: {e}")

        await self.update_leaderboard()

    @app_commands.command(name="hlltopscores", description="Show all-time top scores")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def hlltopscores(self, interaction: discord.Interaction):
        embed = await self.build_leaderboard_embed(monthly=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="hllmonthtopscores", description="Show top scores for this month")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def hllmonthtopscores(self, interaction: discord.Interaction):
        embed = await self.build_leaderboard_embed(monthly=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------------- Submission Modal ----------------
class SubmissionModal(Modal):
    def __init__(self, cog: HLLLeaderboard, stat: str, user: discord.abc.User):
        super().__init__(title=f"Submit {stat}")
        self.cog = cog
        self.stat = stat
        self.user = user

        self.value_input = TextInput(label="Enter your score", placeholder="e.g. 10", required=True)
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        # Validate integer
        try:
            value = int(str(self.value_input.value).strip())
        except (TypeError, ValueError):
            await interaction.response.send_message("Please enter a valid integer.", ephemeral=True)
            return

        # Insert into DB (async)
        try:
            async with aiosqlite.connect(DB_FILE) as db:
                await db.execute(
                    "INSERT INTO submissions(user_id, stat, value, submitted_at) VALUES(?, ?, ?, ?)",
                    (self.user.id, self.stat, value, datetime.datetime.utcnow().isoformat()),
                )
                await db.commit()
        except Exception as e:
            await interaction.response.send_message(f"Failed to record submission: {e}", ephemeral=True)
            return

        # Update leaderboard message
        await self.cog.update_leaderboard()

        # Optional screenshot requirement message
        submissions_channel = await self.cog._get_channel(SUBMISSIONS_CHANNEL_ID)
        if submissions_channel:
            try:
                require_ss = random.choice([True, False])
                msg = f"{self.user.mention} submitted {value} {self.stat}!"
                if require_ss:
                    msg += " Screenshot required!"
                await submissions_channel.send(msg)
            except Exception:
                pass  # Non-fatal for the user interaction

        await interaction.response.send_message("Submission recorded!", ephemeral=True)

# ---------------- Views (persistent) ----------------
class LeaderboardView(View):
    def __init__(self, cog: HLLLeaderboard):
        # Persistent view: timeout=None; items need fixed custom_id
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(StatSelect(cog))

class StatSelect(Select):
    def __init__(self, cog: HLLLeaderboard):
        self.cog = cog
        options = [discord.SelectOption(label=stat, value=stat) for stat in STATS]
        # custom_id is required for persistent components
        super().__init__(placeholder="Select stat to submit", options=options, custom_id="hll_stat_select")

    async def callback(self, interaction: discord.Interaction):
        stat = self.values[0]
        await interaction.response.send_modal(SubmissionModal(self.cog, stat, interaction.user))

# ---------------- Setup ----------------
async def setup(bot: commands.Bot):
    await bot.add_cog(HLLLeaderboard(bot))

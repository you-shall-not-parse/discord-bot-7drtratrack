import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
from datetime import datetime, time
import pytz
import random

# ---------------- Config ----------------
BIRTHDAY_CHANNEL_ID = 1098333222540152944
SUMMARY_CHANNEL_ID = 1098333222540152944
GUILD_ID = 1097913605082579024
TIMEZONE = "Europe/London"
DB_FILE = "birthdays.db"
# Static GIFs for birthday greetings
BIRTHDAY_GIF_URLS = [
    "https://media.tenor.com/X185VU8GGAUAAAAC/everybody-dance-now-speaker.gif",
    "https://media.tenor.com/zID0voNWZeMAAAAC/the-office-its-your-birthday-period-happy-birthday.gif",
    "https://media.tenor.com/mW9Bne87qc0AAAAC/the-office.gif",
    "https://media.tenor.com/BiqWZ9UdZ8kAAAAC/surprised-theoffice.gif",
    "https://media.tenor.com/GzGo7jQeLB0AAAAd/happy-birthday-bon-anniversaire.gif",
    "https://media.tenor.com/9pu-un8ImGUAAAAC/action-drama.gif",
    "https://media.tenor.com/fHAJclG404oAAAAC/birthday-parks-and-rec.gif",
    "https://media.tenor.com/z2xPe5mCygcAAAAC/birthday-self-worth.gif"
    "https://media.tenor.com/CSMv9A3-HkoAAAAC/shocked-happy.gif",
]
# ----------------------------------------

class BirthdayCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.guild = discord.Object(id=GUILD_ID)

        # Database setup
        self.conn = sqlite3.connect(DB_FILE)
        self.c = self.conn.cursor()
        self.c.execute(
            "CREATE TABLE IF NOT EXISTS birthdays (guild_id INTEGER, user_id INTEGER, date TEXT, display_age INTEGER DEFAULT 0, PRIMARY KEY (guild_id, user_id))"
        )
        self.conn.commit()

        # Ensure column exists for older DBs
        self._ensure_display_age_column()

    def _ensure_display_age_column(self):
        self.c.execute("PRAGMA table_info(birthdays)")
        columns = [col[1] for col in self.c.fetchall()]
        if "display_age" not in columns:
            self.c.execute("ALTER TABLE birthdays ADD COLUMN display_age INTEGER DEFAULT 0")
            self.conn.commit()

    def cog_unload(self):
        self.conn.close()
        if self.check_birthdays.is_running():
            self.check_birthdays.cancel()
        if self.post_monthly_summary.is_running():
            self.post_monthly_summary.cancel()

    # ---------------- Database ----------------
    def set_birthday(self, guild_id: int, user_id: int, date_str: str, display_age: bool):
        self.c.execute(
            "INSERT OR REPLACE INTO birthdays (guild_id, user_id, date, display_age) VALUES (?, ?, ?, ?)",
            (guild_id, user_id, date_str, int(display_age)),
        )
        self.conn.commit()

    def remove_birthday(self, guild_id: int, user_id: int):
        self.c.execute(
            "DELETE FROM birthdays WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        self.conn.commit()

    def get_month_birthdays(self, guild_id: int, month: int):
        self.c.execute(
            "SELECT user_id, date, display_age FROM birthdays WHERE guild_id = ?",
            (guild_id,),
        )
        rows = self.c.fetchall()
        return [
            (uid, date_str, bool(display_age))
            for uid, date_str, display_age in rows
            if datetime.strptime(date_str, "%d/%m/%Y").month == month
        ]

    def get_user_birthday(self, guild_id: int, user_id: int):
        # Returns (date_str, display_age) or (None, None) if not found
        self.c.execute(
            "SELECT date, display_age FROM birthdays WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        row = self.c.fetchone()
        return (row[0], bool(row[1])) if row else (None, None)

    # ---------------- Slash Commands ----------------
    @app_commands.command(name="setbirthday", description="Set your birthday")
    @app_commands.describe(
        day="Day of your birthday",
        month="Month of your birthday",
        year="Year of your birthday (optional)",
        display_age="Display your age on birthday announcements"
    )
    @app_commands.choices(month=[
        app_commands.Choice(name="January", value=1),
        app_commands.Choice(name="February", value=2),
        app_commands.Choice(name="March", value=3),
        app_commands.Choice(name="April", value=4),
        app_commands.Choice(name="May", value=5),
        app_commands.Choice(name="June", value=6),
        app_commands.Choice(name="July", value=7),
        app_commands.Choice(name="August", value=8),
        app_commands.Choice(name="September", value=9),
        app_commands.Choice(name="October", value=10),
        app_commands.Choice(name="November", value=11),
        app_commands.Choice(name="December", value=12),
    ])
    async def setbirthday(
        self,
        interaction: discord.Interaction,
        day: int,
        month: app_commands.Choice[int],
        year: int = 2000,
        display_age: bool = False
    ):
        try:
            date_obj = datetime(year, month.value, day)
        except ValueError:
            await interaction.response.send_message("⚠ Invalid date.", ephemeral=True)
            return

        date_str = date_obj.strftime("%d/%m/%Y")
        self.set_birthday(interaction.guild.id, interaction.user.id, date_str, display_age)
        await interaction.response.send_message(
            f"✅ Birthday saved as {day:02} {month.name} {year}. Display age: {'Yes' if display_age else 'No'}",
            ephemeral=True
        )

    @app_commands.command(name="removebirthday", description="Remove your birthday")
    async def removebirthday(self, interaction: discord.Interaction):
        self.remove_birthday(interaction.guild.id, interaction.user.id)
        await interaction.response.send_message("❌ Your birthday has been removed.", ephemeral=True)

    @app_commands.command(name="birthdaysplease", description="Show this month's birthdays")
    async def birthdaysplease(self, interaction: discord.Interaction):
        now = datetime.now(pytz.timezone(TIMEZONE))
        month_birthdays = self.get_month_birthdays(interaction.guild.id, now.month)

        if not month_birthdays:
            return await interaction.response.send_message("📭 No birthdays this month.", ephemeral=True)

        lines = []
        for uid, date_str, display_age in sorted(month_birthdays, key=lambda x: datetime.strptime(x[1], "%d/%m/%Y")):
            user = interaction.guild.get_member(uid)
            if not user:
                continue
            line = f"🎂 {user.mention} - {date_str}"
            if display_age:
                bday = datetime.strptime(date_str, "%d/%m/%Y").date()
                age = now.year - bday.year - ((now.month, now.day) < (bday.month, bday.day))
                line += f" ({age} years old)"
            lines.append(line)

        embed = discord.Embed(
            title=f"🎉 Birthdays in {now.strftime('%B')} 🎉",
            description="\n".join(lines),
            color=discord.Color.purple(),
        )
        await interaction.response.send_message(embed=embed)

    # ---------------- Tasks ----------------
    @tasks.loop(time=time(hour=9, minute=0))
    async def check_birthdays(self):
        tz = pytz.timezone(TIMEZONE)
        today = datetime.now(tz).date()

        for guild in self.bot.guilds:
            birthdays_today = [
                (uid, date_str, display_age)
                for uid, date_str, display_age in self.get_month_birthdays(guild.id, today.month)
                if datetime.strptime(date_str, "%d/%m/%Y").day == today.day
            ]

            if not birthdays_today:
                continue

            channel = guild.get_channel(BIRTHDAY_CHANNEL_ID)
            if not channel:
                continue

            for uid, date_str, display_age in birthdays_today:
                user = guild.get_member(uid)
                if not user:
                    continue

                msg = f"🎉 Happy Birthday to {user.mention}!"
                if display_age:
                    bday = datetime.strptime(date_str, "%d/%m/%Y").date()
                    age = today.year - bday.year - ((today.month, today.day) < (bday.month, bday.day))
                    msg += f" ({age} years old)"

                gif_url = random.choice(BIRTHDAY_GIF_URLS) if BIRTHDAY_GIF_URLS else None
                content = f"{msg}\n{gif_url}" if gif_url else msg
                await channel.send(content)

    @tasks.loop(time=time(hour=9, minute=5))
    async def post_monthly_summary(self):
        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz)

        if now.day != 1:
            return

        for guild in self.bot.guilds:
            month_birthdays = self.get_month_birthdays(guild.id, now.month)
            if not month_birthdays:
                continue

            lines = []
            for uid, date_str, display_age in sorted(month_birthdays, key=lambda x: datetime.strptime(x[1], "%d/%m/%Y")):
                user = guild.get_member(uid)
                if not user:
                    continue

                line = f"🎂 {user.mention} - {date_str}"
                if display_age:
                    bday = datetime.strptime(date_str, "%d/%m/%Y").date()
                    age = now.year - bday.year - ((now.month, now.day) < (bday.month, bday.day))
                    line += f" ({age} years old)"
                lines.append(line)

            embed = discord.Embed(
                title=f"📅 Birthdays in {now.strftime('%B')}",
                description="\n".join(lines),
                color=discord.Color.gold(),
            )

            channel = guild.get_channel(SUMMARY_CHANNEL_ID)
            if channel:
                await channel.send(embed=embed)

    # ---------------- Embed Info ----------------
    async def ensure_embed_posted(self):
        await self.bot.wait_until_ready()

        for guild in self.bot.guilds:
            channel = guild.get_channel(BIRTHDAY_CHANNEL_ID)
            if not channel:
                continue

            async for message in channel.history(limit=100):
                if message.author == self.bot.user and message.embeds:
                    if message.embeds[0].title == "🎂 Birthday Manager 🎂":
                        return

            embed = discord.Embed(
                title="🎂 Birthday Manager 🎂",
                description=(
                    "Use `/setbirthday day month [year] [display_age]` in #general-chat to set your birthday.\n"
                    "Example: `/setbirthday 15 June 1995 True`\n"
                    "Age will only be shown if you select True."
                ),
                color=discord.Color.blue()
            )

            embed.set_image(url="https://cdn.discordapp.com/attachments/1098667103030100040/1442248383258689697/Happy_birthday_20251123_201738_0000.png")

            await channel.send(embed=embed)

    # ---------------- on_ready ----------------
    @commands.Cog.listener()
    async def on_ready(self):
        if not self.check_birthdays.is_running():
            self.check_birthdays.start()

        if not self.post_monthly_summary.is_running():
            self.post_monthly_summary.start()

        await self.ensure_embed_posted()
        await self.bot.tree.sync(guild=self.guild)

async def setup(bot: commands.Bot):
    await bot.add_cog(BirthdayCog(bot))
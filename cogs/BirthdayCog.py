import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
from datetime import datetime, time
import pytz

# ---------------- Config ----------------
BIRTHDAY_CHANNEL_ID = 1099248200776421406   # channel for interactive birthday embed & daily messages
SUMMARY_CHANNEL_ID = 1098333222540152944    # channel for monthly summaries
BIRTHDAY_MESSAGE = "🎉 Happy Birthday to {mention}! 🎂"
TIMEZONE = "Europe/London"
DB_FILE = "birthdays.db"
# ----------------------------------------

class BirthdayCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.conn = sqlite3.connect(DB_FILE)
        self.c = self.conn.cursor()
        self.c.execute(
            "CREATE TABLE IF NOT EXISTS birthdays (guild_id INTEGER, user_id INTEGER, date TEXT, PRIMARY KEY (guild_id, user_id))"
        )
        self.conn.commit()

        self.check_birthdays.start()
        self.post_monthly_summary.start()
        self.bot.loop.create_task(self.ensure_embed_posted())

    def cog_unload(self):
        self.conn.close()
        self.check_birthdays.cancel()
        self.post_monthly_summary.cancel()

    # ---------------- Database ----------------
    def set_birthday(self, guild_id: int, user_id: int, date_str: str):
        self.c.execute(
            "INSERT OR REPLACE INTO birthdays (guild_id, user_id, date) VALUES (?, ?, ?)",
            (guild_id, user_id, date_str),
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
            "SELECT user_id, date FROM birthdays WHERE guild_id = ?",
            (guild_id,),
        )
        rows = self.c.fetchall()
        return [
            (uid, date_str)
            for uid, date_str in rows
            if datetime.strptime(date_str, "%d/%m/%Y").month == month
        ]

    # ---------------- View ----------------
    class BirthdayView(discord.ui.View):
        def __init__(self, cog: "BirthdayCog", guild_id: int):
            super().__init__(timeout=None)
            self.cog = cog
            self.guild_id = guild_id

            # Month dropdown
            self.add_item(BirthdayCog.MonthSelect(cog, guild_id))
            # Day 1–15
            self.add_item(BirthdayCog.DaySelect(cog, guild_id, 1, 15))
            # Day 16–31
            self.add_item(BirthdayCog.DaySelect(cog, guild_id, 16, 31))
            # Remove button
            self.add_item(BirthdayCog.RemoveButton(cog, guild_id))

    class MonthSelect(discord.ui.Select):
        def __init__(self, cog, guild_id):
            self.cog = cog
            self.guild_id = guild_id
            options = [
                discord.SelectOption(label="January", value="01"),
                discord.SelectOption(label="February", value="02"),
                discord.SelectOption(label="March", value="03"),
                discord.SelectOption(label="April", value="04"),
                discord.SelectOption(label="May", value="05"),
                discord.SelectOption(label="June", value="06"),
                discord.SelectOption(label="July", value="07"),
                discord.SelectOption(label="August", value="08"),
                discord.SelectOption(label="September", value="09"),
                discord.SelectOption(label="October", value="10"),
                discord.SelectOption(label="November", value="11"),
                discord.SelectOption(label="December", value="12"),
            ]
            super().__init__(placeholder="Select Month", options=options)

        async def callback(self, interaction: discord.Interaction):
            month = self.values[0]
            self.view.selected_month = month
            await interaction.response.send_message(f"✅ Month set to {month}. Now select your day.", ephemeral=True)

    class DaySelect(discord.ui.Select):
        def __init__(self, cog, guild_id, start: int, end: int):
            self.cog = cog
            self.guild_id = guild_id
            options = [discord.SelectOption(label=str(i), value=f"{i:02}") for i in range(start, end + 1)]
            super().__init__(placeholder=f"Select Day ({start}-{end})", options=options)

        async def callback(self, interaction: discord.Interaction):
            day = self.values[0]
            month = getattr(self.view, "selected_month", None)
            if not month:
                await interaction.response.send_message("⚠ Please select a month first.", ephemeral=True)
                return
            # Optional year (default 2000)
            year = "2000"
            date_str = f"{day}/{month}/{year}"
            self.cog.set_birthday(interaction.guild.id, interaction.user.id, date_str)
            await interaction.response.send_message(f"✅ Birthday saved: {date_str}", ephemeral=True)

    class RemoveButton(discord.ui.Button):
        def __init__(self, cog, guild_id):
            super().__init__(label="Remove Birthday", style=discord.ButtonStyle.red)
            self.cog = cog
            self.guild_id = guild_id

        async def callback(self, interaction: discord.Interaction):
            self.cog.remove_birthday(interaction.guild.id, interaction.user.id)
            await interaction.response.send_message("❌ Your birthday has been removed.", ephemeral=True)

    # ---------------- Slash Command ----------------
    @app_commands.command(name="birthdaysplease", description="Show this month's birthdays")
    async def birthdaysplease(self, interaction: discord.Interaction):
        now = datetime.now(pytz.timezone(TIMEZONE))
        month_birthdays = self.get_month_birthdays(interaction.guild.id, now.month)
        if not month_birthdays:
            await interaction.response.send_message("📭 No birthdays this month.", ephemeral=True)
            return

        lines = []
        for uid, date_str in sorted(month_birthdays, key=lambda x: datetime.strptime(x[1], "%d/%m/%Y")):
            user = interaction.guild.get_member(uid)
            if user:
                lines.append(f"🎂 {user.mention} - {date_str}")

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
        now = datetime.now(tz).date()
        for guild in self.bot.guilds:
            birthdays_today = [
                (uid, date_str)
                for uid, date_str in self.get_month_birthdays(guild.id, now.month)
                if datetime.strptime(date_str, "%d/%m/%Y").day == now.day
            ]
            if birthdays_today:
                channel = guild.get_channel(BIRTHDAY_CHANNEL_ID)
                if channel:
                    for uid, _ in birthdays_today:
                        user = guild.get_member(uid)
                        if user:
                            await channel.send(BIRTHDAY_MESSAGE.format(mention=user.mention))

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
            for uid, date_str in sorted(month_birthdays, key=lambda x: datetime.strptime(x[1], "%d/%m/%Y")):
                user = guild.get_member(uid)
                if user:
                    lines.append(f"🎂 {user.mention} - {date_str}")

            embed = discord.Embed(
                title=f"📅 Birthdays in {now.strftime('%B')}",
                description="\n".join(lines),
                color=discord.Color.gold(),
            )
            channel = guild.get_channel(SUMMARY_CHANNEL_ID)
            if channel:
                await channel.send(embed=embed)

    # ---------------- Auto Embed ----------------
    async def ensure_embed_posted(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            channel = guild.get_channel(BIRTHDAY_CHANNEL_ID)
            if not channel:
                continue

            async for message in channel.history(limit=100):
                if message.author == self.bot.user and message.embeds:
                    embed = message.embeds[0]
                    if embed.title == "🎂 Birthday Manager 🎂":
                        break
            else:
                embed = discord.Embed(
                    title="🎂 Birthday Manager 🎂",
                    description="Select your birthday below using the dropdowns:",
                    color=discord.Color.blue(),
                )
                view = self.BirthdayView(self, guild.id)
                await channel.send(embed=embed, view=view)


async def setup(bot: commands.Bot):
    cog = BirthdayCog(bot)
    await bot.add_cog(cog)

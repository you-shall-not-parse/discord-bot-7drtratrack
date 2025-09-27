import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
from datetime import datetime, time
import pytz

# ---------------- Config ----------------
BIRTHDAY_CHANNEL_ID = 1099248200776421406   # channel where the interactive birthday embed is posted
SUMMARY_CHANNEL_ID = 1098333222540152944    # channel for monthly summaries
BIRTHDAY_MESSAGE = "🎉 Happy Birthday to {mention}! 🎂"  # message sent on birthdays
TIMEZONE = "Europe/London"                 # adjust as needed
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

        # Start tasks
        self.check_birthdays.start()
        self.post_monthly_summary.start()
        self.bot.loop.create_task(self.ensure_embed_posted())

    def cog_unload(self):
        self.conn.close()
        self.check_birthdays.cancel()
        self.post_monthly_summary.cancel()

    # ----------- Database Helpers -----------
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

    def get_birthday(self, guild_id: int, user_id: int):
        self.c.execute(
            "SELECT date FROM birthdays WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        row = self.c.fetchone()
        return row[0] if row else None

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

    # ----------- Birthday Embed + UI -----------
    class BirthdayView(discord.ui.View):
        def __init__(self, cog: "BirthdayCog", guild_id: int):
            super().__init__(timeout=None)
            self.cog = cog
            self.guild_id = guild_id

        @discord.ui.button(label="Add / Edit Birthday", style=discord.ButtonStyle.green)
        async def add_edit(self, interaction: discord.Interaction, button: discord.ui.Button):
            modal = BirthdayCog.BirthdayModal(self.cog, self.guild_id, interaction.user.id)
            await interaction.response.send_modal(modal)

        @discord.ui.button(label="Remove Birthday", style=discord.ButtonStyle.danger)
        async def remove(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.cog.remove_birthday(self.guild_id, interaction.user.id)
            await interaction.response.send_message("❌ Your birthday has been removed.", ephemeral=True)

    class BirthdayModal(discord.ui.Modal, title="Set Your Birthday"):
        def __init__(self, cog: "BirthdayCog", guild_id: int, user_id: int):
            super().__init__()
            self.cog = cog
            self.guild_id = guild_id
            self.user_id = user_id

            # Month dropdown
            self.month = discord.ui.Select(
                placeholder="Select Month",
                options=[
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
                ],
            )
            self.add_item(self.month)

            # Day dropdown (1-31)
            self.day = discord.ui.Select(
                placeholder="Select Day",
                options=[discord.SelectOption(label=str(i), value=f"{i:02}") for i in range(1, 32)]
            )
            self.add_item(self.day)

            # Optional year text input
            self.year = discord.ui.TextInput(
                label="Year (optional)",
                placeholder="2000",
                required=False,
                max_length=4
            )
            self.add_item(self.year)

        async def on_submit(self, interaction: discord.Interaction):
            day = self.day.values[0]
            month = self.month.values[0]
            year = self.year.value.strip() or "2000"  # default year if none provided

            # Validate the date
            try:
                date_obj = datetime.strptime(f"{day}/{month}/{year}", "%d/%m/%Y")
            except ValueError:
                await interaction.response.send_message("⚠ Invalid date. Please try again.", ephemeral=True)
                return

            # Save in DD/MM/YYYY format
            date_str = date_obj.strftime("%d/%m/%Y")
            self.cog.set_birthday(self.guild_id, self.user_id, date_str)
            await interaction.response.send_message(f"✅ Your birthday has been saved as {date_str}!", ephemeral=True)

    # ----------- Slash Command to Show Current Month's Birthdays -----------
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
                # Display full date in DD/MM/YYYY
                lines.append(f"🎂 {user.mention} - {date_str}")

        embed = discord.Embed(
            title=f"🎉 Birthdays in {now.strftime('%B')} 🎉",
            description="\n".join(lines),
            color=discord.Color.purple(),
        )
        await interaction.response.send_message(embed=embed)

    # ----------- Tasks -----------
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

    # ----------- Auto Post Embed on Startup -----------
    async def ensure_embed_posted(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            channel = guild.get_channel(BIRTHDAY_CHANNEL_ID)
            if not channel:
                continue

            # Check if embed already exists
            async for message in channel.history(limit=100):
                if message.author == self.bot.user and message.embeds:
                    embed = message.embeds[0]
                    if embed.title == "🎂 Birthday Manager 🎂":
                        break
            else:
                # Post the embed if not found
                embed = discord.Embed(
                    title="🎂 Birthday Manager 🎂",
                    description="Click below to add, edit, or remove your birthday.",
                    color=discord.Color.blue(),
                )
                view = self.BirthdayView(self, guild.id)
                await channel.send(embed=embed, view=view)


async def setup(bot: commands.Bot):
    cog = BirthdayCog(bot)
    await bot.add_cog(cog)

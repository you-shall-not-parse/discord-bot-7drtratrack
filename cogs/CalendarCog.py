import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
from datetime import datetime, timedelta
import pytz

# ---------------- CONFIG ----------------
TIMEZONE = pytz.timezone("Europe/London")
EVENTS_FILE = "events.json"
ALLOWED_ROLES = ["Administration", "Fight Arrangeer", "7DR-SNCO"]  # Roles allowed to add/edit/remove
# ----------------------------------------

class CalendarCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.events = self.load_events()
        self.reminder_loop.start()

    # ----------------- Storage -----------------
    def load_events(self):
        if os.path.exists(EVENTS_FILE):
            with open(EVENTS_FILE, "r") as f:
                return json.load(f)
        return []

    def save_events(self):
        with open(EVENTS_FILE, "w") as f:
            json.dump(self.events, f, indent=2)

    # ----------------- Helpers -----------------
    def has_permission(self, interaction: discord.Interaction):
        return any(r.name in ALLOWED_ROLES for r in interaction.user.roles)

    def event_to_str(self, event):
        dt = datetime.fromisoformat(event["date"]).astimezone(TIMEZONE)
        desc = f"**{event['title']}**\n🗓️ {dt.strftime('%a %d %b %Y %H:%M')}"
        desc += f"\n👤 Organiser: <@{event['organiser']}>"
        if event.get("squad_maker"):
            desc += f"\n🛡️ Squad Maker: <@{event['squad_maker']}>"
        if event.get("thread_id"):
            desc += f"\n💬 [Event Thread](https://discord.com/channels/{event['guild_id']}/{event['thread_id']})"
        return desc

    def group_events_by_month(self):
        month_dict = {}
        now = datetime.now(TIMEZONE)
        for event in self.events:
            dt = datetime.fromisoformat(event["date"]).astimezone(TIMEZONE)
            if event.get("recurring") and dt > now + timedelta(weeks=2):
                continue
            month_name = dt.strftime("%b %Y")
            month_dict.setdefault(month_name, []).append(event)
        return month_dict

    def build_calendar_embed(self):
        month_dict = self.group_events_by_month()
        embed = discord.Embed(
            title="📅 Unit Calendar",
            description="Upcoming scheduled events",
            colour=discord.Colour.blue(),
            timestamp=datetime.now(TIMEZONE)
        )
        for month, ev_list in sorted(month_dict.items(), key=lambda x: datetime.strptime(x[0], "%b %Y")):
            embed.add_field(
                name=month,
                value="\n\n".join(self.event_to_str(e) for e in ev_list),
                inline=False
            )
        if not month_dict:
            embed.description = "No events scheduled."
        return embed

    async def update_calendar_message(self, interaction: discord.Interaction):
        embed = self.build_calendar_embed()
        view = CalendarButtons(self)
        await interaction.response.edit_message(embed=embed, view=view)

    # ----------------- Slash Commands -----------------
    @app_commands.command(name="addevent", description="Add an event to the calendar")
    async def addevent(self, interaction: discord.Interaction,
                       title: str,
                       date: str,  # format: YYYY-MM-DD HH:MM
                       recurring: bool = False,
                       organiser: discord.Member = None,
                       squad_maker: discord.Member = None,
                       reminder_hours: int = None,
                       create_thread: bool = False,
                       thread_channel: discord.TextChannel = None):
        if not self.has_permission(interaction):
            return await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)

        dt = TIMEZONE.localize(datetime.strptime(date, "%Y-%m-%d %H:%M"))

        event = {
            "title": title,
            "date": dt.isoformat(),
            "recurring": recurring,
            "organiser": organiser.id if organiser else interaction.user.id,
            "squad_maker": squad_maker.id if squad_maker else None,
            "reminder_hours": reminder_hours,
            "guild_id": interaction.guild.id
        }

        if create_thread and thread_channel:
            thread = await thread_channel.create_thread(
                name=title,
                type=discord.ChannelType.public_thread
            )
            msg = await thread.send(self.event_to_str(event))
            event["thread_id"] = thread.id
            event["thread_msg_id"] = msg.id

        self.events.append(event)
        self.save_events()
        await interaction.response.send_message("✅ Event added!", ephemeral=True)

    @app_commands.command(name="editevent", description="Edit an existing event")
    async def editevent(self, interaction: discord.Interaction, title: str, new_date: str = None):
        if not self.has_permission(interaction):
            return await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)

        for event in self.events:
            if event["title"] == title:
                if new_date:
                    dt = TIMEZONE.localize(datetime.strptime(new_date, "%Y-%m-%d %H:%M"))
                    event["date"] = dt.isoformat()
                # update thread message if exists
                if event.get("thread_id"):
                    thread = interaction.guild.get_thread(event["thread_id"])
                    if thread:
                        try:
                            msg = await thread.fetch_message(event["thread_msg_id"])
                            await msg.edit(content=self.event_to_str(event))
                        except Exception:
                            pass
                self.save_events()
                return await interaction.response.send_message("✏️ Event updated.", ephemeral=True)

        await interaction.response.send_message("❌ Event not found.", ephemeral=True)

    @app_commands.command(name="deleteevent", description="Delete an event")
    async def deleteevent(self, interaction: discord.Interaction, title: str):
        if not self.has_permission(interaction):
            return await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)

        for event in self.events:
            if event["title"] == title:
                if event.get("thread_id"):
                    thread = interaction.guild.get_thread(event["thread_id"])
                    if thread:
                        await thread.edit(archived=True)
                self.events.remove(event)
                self.save_events()
                return await interaction.response.send_message("🗑️ Event deleted.", ephemeral=True)

        await interaction.response.send_message("❌ Event not found.", ephemeral=True)

    # ----------------- Reminders -----------------
    @tasks.loop(minutes=1)
    async def reminder_loop(self):
        now = datetime.now(TIMEZONE)
        for event in self.events:
            if not event.get("reminder_hours"):
                continue
            dt = datetime.fromisoformat(event["date"]).astimezone(TIMEZONE)
            reminder_time = dt - timedelta(hours=event["reminder_hours"])
            if reminder_time <= now < reminder_time + timedelta(minutes=1):
                guild = self.bot.get_guild(event["guild_id"])
                if guild:
                    channel = guild.system_channel or guild.text_channels[0]
                    mentions = [f"<@{event['organiser']}"]
                    if event.get("squad_maker"):
                        mentions.append(f"<@{event['squad_maker']}>")
                    await channel.send(f"⏰ Reminder for **{event['title']}** in {event['reminder_hours']}h!\n{' '.join(mentions)}")

class CalendarButtons(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="➕ Add Event", style=discord.ButtonStyle.success)
    async def add_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Use `/addevent` to add an event.", ephemeral=True)

    @discord.ui.button(label="✏️ Edit Event", style=discord.ButtonStyle.primary)
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Use `/editevent` to edit an event.", ephemeral=True)

    @discord.ui.button(label="🗑️ Remove Event", style=discord.ButtonStyle.danger)
    async def remove_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Use `/deleteevent` to remove an event.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(CalendarCog(bot))
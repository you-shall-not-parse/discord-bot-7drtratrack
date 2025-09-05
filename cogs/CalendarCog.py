import discord
from discord.ext import commands, tasks
from discord.ui import View, Modal, TextInput, Select
from datetime import datetime, timedelta
import pytz
import json

# ---------------- Config ----------------
EVENTS_FILE = "events.json"
TIMEZONE = pytz.timezone("Europe/London")
CALENDAR_MANAGER_ROLES = ["Administration", "7DR-SNCO", "Fight Arrangeer"]
CALENDAR_CHANNEL_ID = 1332736267485708419  # Your calendar channel ID
GUILD_ID = 1097913605082579024  # Your guild/server ID
CALENDAR_MESSAGE_FILE = "calendar_message.json"

# ---------------- Utils ----------------
def has_calendar_permission(member: discord.Member) -> bool:
    return any(role.name in CALENDAR_MANAGER_ROLES for role in member.roles)

def load_events():
    try:
        with open(EVENTS_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_events(events):
    with open(EVENTS_FILE, "w") as f:
        json.dump(events, f, indent=4)

def load_calendar_message():
    try:
        with open(CALENDAR_MESSAGE_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_calendar_message(data):
    with open(CALENDAR_MESSAGE_FILE, "w") as f:
        json.dump(data, f, indent=4)

def event_to_str(event):
    dt = event.get("date")
    dt_str = "TBD"
    if dt and dt != "TBD":
        dt_obj = datetime.fromisoformat(dt).astimezone(TIMEZONE)
        dt_str = dt_obj.strftime("%d-%m-%Y, %H:%M %Z")
    organiser = f"<@{event['organiser']}>" if event.get("organiser") else "Unknown"
    squad_maker = f"<@{event['squad_maker']}>" if event.get("squad_maker") else "None"
    reminder = f"{event['reminder_hours']}h before" if event.get("reminder_hours") else "None"
    thread = f"[Link](https://discord.com/channels/{event['guild_id']}/{event['thread_id']})" if event.get("thread_id") else "None"
    return (
        f"📌 **{event['title']}**\n"
        f"🗓️ {dt_str}\n"
        f"👤 Organiser: {organiser}\n"
        f"⚔️ Squad Maker: {squad_maker}\n"
        f"⏰ Reminder: {reminder}\n"
        f"🧵 Thread: {thread}"
    )

def group_events(events):
    now = datetime.now(TIMEZONE)
    this_month, next_month, future, tbd = [], [], [], []
    for event in events:
        if event.get("date") == "TBD":
            tbd.append(event)
            continue
        dt = datetime.fromisoformat(event["date"]).astimezone(TIMEZONE)
        if event.get("recurring"):
            if dt > now + timedelta(weeks=2):
                continue
        if dt.month == now.month and dt.year == now.year:
            this_month.append(event)
        elif dt.month == (now.month % 12) + 1 and dt.year == (now.year if now.month < 12 else now.year+1):
            next_month.append(event)
        else:
            future.append(event)
    return this_month, next_month, future, tbd

def build_calendar_embed(events):
    this_month, next_month, future, tbd = group_events(events)
    embed = discord.Embed(
        title="📅 Unit Calendar",
        description="Upcoming scheduled events",
        colour=discord.Colour.blue(),
        timestamp=datetime.now(TIMEZONE)
    )
    if this_month:
        embed.add_field(name="This Month", value="\n\n".join(event_to_str(e) for e in this_month), inline=False)
    if next_month:
        embed.add_field(name="Next Month", value="\n\n".join(event_to_str(e) for e in next_month), inline=False)
    if future:
        embed.add_field(name="Future Months", value="\n\n".join(event_to_str(e) for e in future), inline=False)
    if tbd:
        embed.add_field(name="TBD", value="\n\n".join(event_to_str(e) for e in tbd), inline=False)
    if not (this_month or next_month or future or tbd):
        embed.description = "No events scheduled."
    return embed

# ---------------- Modal ----------------
class EventModal(Modal, title="Add/Edit Event"):
    def __init__(self, interaction, event=None):
        super().__init__()
        self.interaction = interaction
        self.event = event
        self.add_item(TextInput(label="Title", default=event["title"] if event else "", required=True))
        self.add_item(TextInput(label="Date & Time (DD-MM-YYYY HH:MM or TBD)", default=datetime.fromisoformat(event["date"]).strftime("%d-%m-%Y %H:%M") if event and event.get("date") != "TBD" else "", required=False))
        self.add_item(TextInput(label="Recurring? (yes/no)", default="yes" if event and event.get("recurring") else "no", required=False))
        self.add_item(TextInput(label="Organiser (mention @user, optional)", default=f"<@{event['organiser']}>" if event and event.get("organiser_override") else "", required=False))
        self.add_item(TextInput(label="Squad Maker (mention @user, optional)", default=f"<@{event['squad_maker']}>" if event and event.get("squad_maker") else "", required=False))
        self.add_item(TextInput(label="Reminder (hours before, optional)", default=str(event["reminder_hours"]) if event and event.get("reminder_hours") else "", required=False))
        self.add_item(TextInput(label="Thread Channel ID (optional)", default=str(event["thread_channel"]) if event and event.get("thread_channel") else "", required=False))

    async def on_submit(self, interaction: discord.Interaction):
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message("❌ You don’t have permission to manage events.", ephemeral=True)
            return

        events = load_events()
        title = self.children[0].value
        date_input = self.children[1].value.strip()
        dt = "TBD"
        if date_input.upper() != "TBD" and date_input != "":
            dt_obj = datetime.strptime(date_input, "%d-%m-%Y %H:%M")
            dt = TIMEZONE.localize(dt_obj).isoformat()

        recurring = self.children[2].value.lower() == "yes"
        organiser_override = None
        squad_maker = None

        if self.children[3].value.strip().startswith("<@"):
            organiser_override = int(self.children[3].value.strip()[2:-1].replace("!", ""))
        if self.children[4].value.strip().startswith("<@"):
            squad_maker = int(self.children[4].value.strip()[2:-1].replace("!", ""))

        reminder_hours = int(self.children[5].value) if self.children[5].value.strip().isdigit() else None
        thread_channel = int(self.children[6].value) if self.children[6].value.strip().isdigit() else None

        new_event = {
            "title": title,
            "date": dt,
            "recurring": recurring,
            "organiser": organiser_override or interaction.user.id,
            "squad_maker": squad_maker,
            "reminder_hours": reminder_hours,
            "guild_id": interaction.guild_id,
            "thread_channel": thread_channel,
            "thread_id": None
        }

        if self.event:
            events = [e for e in events if e["title"] != self.event["title"]]
        events.append(new_event)
        save_events(events)

        # Threads
        if thread_channel and dt != "TBD":
            channel = interaction.guild.get_channel(thread_channel)
            if channel and isinstance(channel, discord.TextChannel):
                thread = await channel.create_thread(name=title, type=discord.ChannelType.public_thread)
                msg = await thread.send(event_to_str(new_event))
                new_event["thread_id"] = thread.id
                save_events(events)

        # Update calendar message
        await update_calendar_message(interaction.guild, events)
        await interaction.response.send_message(f"✅ Event '{title}' saved.", ephemeral=True)

# ---------------- Update Calendar ----------------
async def update_calendar_message(guild, events):
    channel = guild.get_channel(CHANNEL_ID)
    if not channel: return
    embed = build_calendar_embed(events)
    data = load_calendar_message()
    message_id = data.get("message_id")
    try:
        if message_id:
            msg = await channel.fetch_message(message_id)
            await msg.edit(embed=embed)
        else:
            msg = await channel.send(embed=embed)
            data["message_id"] = msg.id
            save_calendar_message(data)
    except discord.NotFound:
        # Message deleted, post new
        msg = await channel.send(embed=embed)
        data["message_id"] = msg.id
        save_calendar_message(data)

# ---------------- Select ----------------
class EventSelect(Select):
    def __init__(self, events, mode, interaction):
        options = [discord.SelectOption(label=e["title"], value=e["title"]) for e in sorted(events, key=lambda e: e.get("date", "TBD"), reverse=True)[:25]]
        super().__init__(placeholder="Select event...", options=options)
        self.events = events
        self.mode = mode
        self.interaction = interaction

    async def callback(self, inter: discord.Interaction):
        chosen = next(e for e in self.events if e["title"] == self.values[0])
        if self.mode == "edit":
            await inter.response.send_modal(EventModal(inter, chosen))
        elif self.mode == "delete":
            if chosen.get("thread_id"):
                thread = inter.guild.get_thread(chosen["thread_id"])
                if thread:
                    await thread.edit(archived=True)
            events = load_events()
            events = [e for e in events if e["title"] != chosen["title"]]
            save_events(events)
            await update_calendar_message(inter.guild, events)
            await inter.response.send_message(f"✅ Event '{chosen['title']}' deleted.", ephemeral=True)

# ---------------- Cog ----------------
class CalendarCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.loop.create_task(self.setup_calendar())
        self.reminder_task.start()

    async def setup_calendar(self):
        await self.bot.wait_until_ready()
        guild = self.bot.get_guild(GUILD_ID)
        if guild:
            await update_calendar_message(guild, load_events())

    @tasks.loop(minutes=10)
    async def reminder_task(self):
        events = load_events()
        now = datetime.now(TIMEZONE)
        updated = False
        for event in events:
            if not event.get("reminder_hours") or event.get("reminded") or event.get("date") == "TBD":
                continue
            dt = datetime.fromisoformat(event["date"]).astimezone(TIMEZONE)
            if now + timedelta(hours=event["reminder_hours"]) >= dt > now:
                guild = self.bot.get_guild(event["guild_id"])
                if guild:
                    channel = guild.system_channel or discord.utils.get(guild.text_channels, permissions__send_messages=True)
                    if channel:
                        mentions = []
                        if event.get("organiser"): mentions.append(f"<@{event['organiser']}>")
                        if event.get("squad_maker"): mentions.append(f"<@{event['squad_maker']}>")
                        await channel.send(f"⏰ Reminder: {event['title']} starts at {dt.strftime('%d-%m-%Y, %H:%M %Z')}!\n{' '.join(mentions)}")
                        event["reminded"] = True
                        updated = True
        if updated:
            save_events(events)

# ---------------- Setup ----------------
async def setup(bot: commands.Bot):
    await bot.add_cog(CalendarCog(bot))

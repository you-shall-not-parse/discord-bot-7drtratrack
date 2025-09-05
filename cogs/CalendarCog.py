import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, Select, Modal, TextInput
from datetime import datetime, timedelta
import pytz
import json
import logging

# ===== CONFIG =====
EVENTS_FILE = "events.json"
TIMEZONE = pytz.timezone("Europe/London")
CALENDAR_MANAGER_ROLES = ["Admininstration", "7DR-SNCO", "Fight Arrangeer"]

# Required: set these to your guild & channel IDs (integers)
GUILD_ID = 123456789012345678  # <-- REPLACE with your guild id (int)
CALENDAR_CHANNEL_ID = 1332736267485708419  # <-- REPLACE with your calendar channel id (int)

# ===== LOGGING =====
logging.basicConfig(level=logging.INFO, filename="calendar_debug.log", filemode="a",
                    format="%(asctime)s %(levelname)s %(message)s")
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger().addHandler(console)

# ---------------- Utility ----------------
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

def event_to_str(event):
    # Safely handle TBD or invalid dates
    try:
        dt = datetime.fromisoformat(event["date"]).astimezone(TIMEZONE)
        date_str = dt.strftime("%d %b %Y, %H:%M %Z")
    except Exception:
        date_str = "TBD"
    organiser = f"<@{event['organiser']}>" if event.get("organiser") else "Unknown"
    squad_maker = f"<@{event['squad_maker']}>" if event.get("squad_maker") else "None"
    reminder = f"{event['reminder_hours']}h before" if event.get("reminder_hours") else "None"
    thread = f"[Link](https://discord.com/channels/{event['guild_id']}/{event['thread_id']})" if event.get("thread_id") else "None"
    return (
        f"📌 **{event['title']}**\n"
        f"🗓️ {date_str}\n"
        f"👤 Organiser: {organiser}\n"
        f"⚔️ Squad Maker: {squad_maker}\n"
        f"⏰ Reminder: {reminder}\n"
        f"🧵 Thread: {thread}"
    )

def group_events(events):
    now = datetime.now(TIMEZONE)
    this_month, next_month, future = [], [], []
    for event in events:
        try:
            dt = datetime.fromisoformat(event["date"]).astimezone(TIMEZONE)
        except Exception:
            # treat invalid/TBD as future
            future.append(event)
            continue
        if event.get("recurring"):
            if dt > now + timedelta(weeks=2):
                continue
        if dt.month == now.month and dt.year == now.year:
            this_month.append(event)
        elif dt.month == (now.month % 12) + 1 and dt.year == (now.year if now.month < 12 else now.year + 1):
            next_month.append(event)
        else:
            future.append(event)
    return this_month, next_month, future

def build_calendar_embed(events):
    this_month, next_month, future = group_events(events)
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
    if not (this_month or next_month or future):
        embed.description = "No events scheduled."
    return embed

# ---------------- Modal ----------------
class EventModal(Modal, title="Add/Edit Event"):
    def __init__(self, interaction, event=None):
        super().__init__()
        self.interaction = interaction
        self.event = event
        # defaults are safe-guarded for missing event data
        self.add_item(TextInput(label="Title", default=event["title"] if event else "", required=True))
        self.add_item(TextInput(
            label="Date & Time (YYYY-MM-DD HH:MM)",
            default=(datetime.fromisoformat(event["date"]).strftime("%Y-%m-%d %H:%M") if event and event.get("date") and event["date"] != "TBD" else ""),
            required=True
        ))
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
        # parse date, accept only the expected format; store "TBD" if not provided/invalid
        date_raw = self.children[1].value.strip()
        if not date_raw:
            date_iso = "TBD"
        else:
            try:
                dt = datetime.strptime(date_raw, "%Y-%m-%d %H:%M")
                dt = TIMEZONE.localize(dt)
                date_iso = dt.isoformat()
            except Exception:
                await interaction.response.send_message("❌ Date must be in format YYYY-MM-DD HH:MM or blank for TBD.", ephemeral=True)
                return

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
            "date": date_iso,
            "recurring": recurring,
            "organiser": organiser_override or interaction.user.id,
            "squad_maker": squad_maker,
            "reminder_hours": reminder_hours,
            "guild_id": interaction.guild_id,
            "thread_channel": thread_channel,
            "thread_id": None,
            "reminded": False
        }

        if self.event:
            events = [e for e in events if e["title"] != self.event["title"]]
        events.append(new_event)
        save_events(events)

        # Create thread if requested
        if thread_channel:
            channel = interaction.guild.get_channel(thread_channel)
            if channel and isinstance(channel, discord.TextChannel):
                try:
                    thread = await channel.create_thread(name=title, type=discord.ChannelType.public_thread)
                    msg = await thread.send(event_to_str(new_event))
                    new_event["thread_id"] = thread.id
                    save_events(events)
                except Exception:
                    logging.exception("Failed to create thread for event")

        embed = build_calendar_embed(events)
        # Edit the original interactive message that opened the modal (no buttons attached)
        try:
            await interaction.response.edit_message(embed=embed)
        except Exception:
            # If editing the invoking message isn't possible, send an ephemeral confirmation instead
            await interaction.followup.send("✅ Event saved — calendar updated.", ephemeral=True)

# ---------------- Buttons & View (kept for compatibility but not attached) ----------------
class CalendarView(View):
    def __init__(self):
        super().__init__(timeout=None)
        # Buttons preserved so other code that imports CalendarView won't break
        self.add_item(Button(label="➕ Add Event", style=discord.ButtonStyle.green, custom_id="addevent"))
        self.add_item(Button(label="✏️ Edit Event", style=discord.ButtonStyle.blurple, custom_id="editevent"))
        self.add_item(Button(label="❌ Remove Event", style=discord.ButtonStyle.red, custom_id="removeevent"))

    @discord.ui.button(label="➕ Add Event", style=discord.ButtonStyle.green, custom_id="addevent")
    async def add_event(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(EventModal(interaction))

    @discord.ui.button(label="✏️ Edit Event", style=discord.ButtonStyle.blurple, custom_id="editevent")
    async def edit_event(self, interaction: discord.Interaction, button: Button):
        events = sorted(load_events(), key=lambda e: e.get("date") or "", reverse=True)[:25]
        if not events:
            await interaction.response.send_message("No events to edit.", ephemeral=True)
            return
        options = [discord.SelectOption(label=e["title"], value=e["title"]) for e in events]
        select = Select(placeholder="Choose an event to edit", options=options)

        async def select_callback(inter: discord.Interaction):
            chosen = next(e for e in events if e["title"] == select.values[0])
            await inter.response.send_modal(EventModal(inter, chosen))
        select.callback = select_callback
        view = View()
        view.add_item(select)
        await interaction.response.send_message("Select an event to edit:", view=view, ephemeral=True)

    @discord.ui.button(label="❌ Remove Event", style=discord.ButtonStyle.red, custom_id="removeevent")
    async def remove_event(self, interaction: discord.Interaction, button: Button):
        events = sorted(load_events(), key=lambda e: e.get("date") or "", reverse=True)[:25]
        if not events:
            await interaction.response.send_message("No events to remove.", ephemeral=True)
            return
        options = [discord.SelectOption(label=e["title"], value=e["title"]) for e in events]
        select = Select(placeholder="Choose an event to remove", options=options)

        async def select_callback(inter: discord.Interaction):
            title = select.values[0]
            events = load_events()
            event = next(e for e in events if e["title"] == title)
            if event.get("thread_id"):
                thread = interaction.guild.get_thread(event["thread_id"])
                if thread:
                    await thread.edit(archived=True)
            events = [e for e in events if e["title"] != title]
            save_events(events)
            embed = build_calendar_embed(events)
            # edit without attaching buttons/views
            await inter.response.edit_message(embed=embed)
        select.callback = select_callback
        view = View()
        view.add_item(select)
        await interaction.response.send_message("Select an event to remove:", view=view, ephemeral=True)

# ---------------- Cog ----------------
class CalendarCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            self.reminder_task.start()
        except RuntimeError:
            # already running in some hot-reload scenarios
            pass

    def cog_unload(self):
        try:
            self.reminder_task.cancel()
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_ready(self):
        # On startup delete any previous calendar embed posted by the bot and post a fresh one
        try:
            if not isinstance(GUILD_ID, int) or not isinstance(CALENDAR_CHANNEL_ID, int) or GUILD_ID == 0 or CALENDAR_CHANNEL_ID == 0:
                logging.error("GUILD_ID or CALENDAR_CHANNEL_ID not set or invalid. Set them at the top of cogs/CalendarCog.py")
                return
            channel = self.bot.get_channel(CALENDAR_CHANNEL_ID)
            if not channel:
                logging.warning("Calendar channel not found (id=%s)", CALENDAR_CHANNEL_ID)
                return
            # search recent messages in channel for bot's calendar embed messages and delete them
            async for msg in channel.history(limit=200):
                if msg.author == self.bot.user and msg.embeds:
                    for emb in msg.embeds:
                        if emb.title == "📅 Unit Calendar":
                            try:
                                await msg.delete()
                                logging.info("Deleted previous calendar embed message id=%s", msg.id)
                            except Exception:
                                logging.exception("Failed to delete previous calendar embed id=%s", msg.id)
            # post fresh calendar embed without buttons
            events = load_events()
            embed = build_calendar_embed(events)
            await channel.send(embed=embed)
        except Exception:
            logging.exception("Failed to refresh calendar on_ready")

    @commands.slash_command(name="calendar", description="Show or update the unit calendar")
    async def calendar(self, ctx: discord.ApplicationContext):
        if not has_calendar_permission(ctx.user):
            await ctx.respond("❌ You don’t have permission to manage the calendar.", ephemeral=True)
            return
        events = load_events()
        embed = build_calendar_embed(events)
        # Do not attach the CalendarView/buttons anymore
        await ctx.respond(embed=embed)

    @tasks.loop(minutes=10)
    async def reminder_task(self):
        events = load_events()
        now = datetime.now(TIMEZONE)
        updated = False
        for event in events:
            if not event.get("reminder_hours") or event.get("reminded"):
                continue
            try:
                dt = datetime.fromisoformat(event["date"]).astimezone(TIMEZONE)
            except Exception:
                continue
            if now + timedelta(hours=event["reminder_hours"]) >= dt > now:
                guild = self.bot.get_guild(event["guild_id"])
                if guild:
                    channel = guild.system_channel or discord.utils.get(guild.text_channels, permissions__send_messages=True)
                    if channel:
                        mentions = []
                        if event.get("organiser"): mentions.append(f"<@{event['organiser']}>")
                        if event.get("squad_maker"): mentions.append(f"<@{event['squad_maker']}>")
                        await channel.send(f"⏰ Reminder: {event['title']} starts at {dt.strftime('%d %b %Y, %H:%M %Z')}!\n{' '.join(mentions)}")
                        event["reminded"] = True
                        updated = True
        if updated:
            save_events(events)

def setup(bot):
    bot.add_cog(CalendarCog(bot))

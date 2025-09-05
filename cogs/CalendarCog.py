import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, Select, Modal, TextInput
from datetime import datetime, timedelta
import pytz
import json
import logging
import os

# ===== CONFIG =====
EVENTS_FILE = "events.json"
TIMEZONE = pytz.timezone("Europe/London")
CALENDAR_MANAGER_ROLES = ["Administration", "7DR-SNCO", "Fight Arrangeer"]
# Required: set these to your guild & channel IDs (integers)
GUILD_ID = 1097913605082579024  # <-- REPLACE with your guild id (int)
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
    except Exception:
        logging.exception("Failed to load events file")
        return []

def save_events(events):
    try:
        # atomic-ish write: write to tmp file then replace
        tmp_path = EVENTS_FILE + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(events, f, indent=4)
        os.replace(tmp_path, EVENTS_FILE)
    except Exception:
        logging.exception("Failed to save events to file")

def event_to_str(event):
    # Safely format date; support TBD and malformed dates
    date_str = "TBD"
    try:
        if event.get("date") and event["date"] != "TBD":
            dt = datetime.fromisoformat(event["date"]).astimezone(TIMEZONE)
            date_str = dt.strftime("%d %b %Y, %H:%M %Z")
    except Exception:
        logging.debug("event_to_str: invalid date for event %s", event.get("title"))

    organiser = f"<@{event['organiser']}>" if event.get("organiser") else "Unknown"
    squad_maker = f"<@{event['squad_maker']}>" if event.get("squad_maker") else "None"
    reminder = f"{event['reminder_hours']}h before" if event.get("reminder_hours") else "None"
    thread = f"[Link](https://discord.com/channels/{event.get('guild_id')}/{event.get('thread_id')})" if event.get("thread_id") else "None"
    return (
        f"📌 **{event.get('title','Untitled')}**\n"
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
            if not event.get("date") or event.get("date") == "TBD":
                # treat TBD as future
                future.append(event)
                continue
            dt = datetime.fromisoformat(event["date"]).astimezone(TIMEZONE)
        except Exception:
            logging.debug("group_events: skipping event with invalid date: %s", event.get("title"))
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
        # defaults are safe-guarded for missing or TBD dates
        title_default = event.get("title", "") if event else ""
        date_default = ""
        if event and event.get("date") and event.get("date") != "TBD":
            try:
                date_default = datetime.fromisoformat(event["date"]).strftime("%Y-%m-%d %H:%M")
            except Exception:
                date_default = ""
        recurring_default = "yes" if event and event.get("recurring") else "no"
        organiser_default = f"<@{event.get('organiser')}>" if event and event.get("organiser") else ""
        squad_default = f"<@{event.get('squad_maker')}>" if event and event.get("squad_maker") else ""
        reminder_default = str(event.get("reminder_hours")) if event and event.get("reminder_hours") is not None else ""
        thread_default = str(event.get("thread_channel")) if event and event.get("thread_channel") else ""

        self.add_item(TextInput(label="Title", default=title_default, required=True))
        # allow blank = TBD
        self.add_item(TextInput(label="Date & Time (YYYY-MM-DD HH:MM)", default=date_default, required=False))
        self.add_item(TextInput(label="Recurring? (yes/no)", default=recurring_default, required=False))
        self.add_item(TextInput(label="Organiser (mention @user or ID, optional)", default=organiser_default, required=False))
        self.add_item(TextInput(label="Squad Maker (mention @user or ID, optional)", default=squad_default, required=False))
        self.add_item(TextInput(label="Reminder (hours before, optional)", default=reminder_default, required=False))
        self.add_item(TextInput(label="Thread Channel ID (optional)", default=thread_default, required=False))

    async def on_submit(self, interaction: discord.Interaction):
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message("❌ You don’t have permission to manage events.", ephemeral=True)
            return

        events = load_events()
        title = self.children[0].value.strip() or "Untitled"
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

        recurring = self.children[2].value.strip().lower() == "yes"

        organiser_override = None
        squad_maker = None

        def parse_mention_or_id(s: str):
            s = s.strip()
            if not s:
                return None
            # allow <@!123>, <@123>, plain 123 — extract digits
            digits = ''.join(ch for ch in s if ch.isdigit())
            try:
                return int(digits) if digits else None
            except Exception:
                return None

        organiser_override = parse_mention_or_id(self.children[3].value)
        squad_maker = parse_mention_or_id(self.children[4].value)

        reminder_hours = None
        if self.children[5].value.strip().isdigit():
            reminder_hours = int(self.children[5].value.strip())
        thread_channel = None
        if self.children[6].value.strip().isdigit():
            thread_channel = int(self.children[6].value.strip())

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
            # remove previous event(s) with the same title that we're editing
            events = [e for e in events if e.get("title") != self.event.get("title")]
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
        # Edit the original interactive message that opened the modal (no buttons attached), fallback to ephemeral
        try:
            await interaction.response.edit_message(embed=embed)
        except Exception:
            try:
                await interaction.followup.send("✅ Event saved — calendar updated.", ephemeral=True)
            except Exception:
                logging.exception("Failed to notify user after saving event")

# ---------------- Buttons & View ----------------
class CalendarView(View):
    def __init__(self):
        super().__init__(timeout=None)
        # Buttons are created via the decorator methods below; do not add duplicate Button instances here.

    @discord.ui.button(label="➕ Add Event", style=discord.ButtonStyle.green, custom_id="addevent")
    async def add_event(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(EventModal(interaction))

    @discord.ui.button(label="✏️ Edit Event", style=discord.ButtonStyle.blurple, custom_id="editevent")
    async def edit_event(self, interaction: discord.Interaction, button: Button):
        events = sorted(load_events(), key=lambda e: e.get("date") or "", reverse=True)[:25]
        if not events:
            await interaction.response.send_message("No events to edit.", ephemeral=True)
            return
        # KEEP USING TITLES as requested (may be ambiguous if duplicates exist)
        options = [discord.SelectOption(label=e.get("title", "Untitled"), value=e.get("title", "Untitled")) for e in events]
        select = Select(placeholder="Choose an event to edit", options=options)

        async def select_callback(inter: discord.Interaction):
            chosen = next((e for e in events if e.get("title") == select.values[0]), None)
            if not chosen:
                await inter.response.send_message("❌ Selected event not found (titles may be duplicated).", ephemeral=True)
                return
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
        options = [discord.SelectOption(label=e.get("title", "Untitled"), value=e.get("title", "Untitled")) for e in events]
        select = Select(placeholder="Choose an event to remove", options=options)

        async def select_callback(inter: discord.Interaction):
            title = select.values[0]
            events_all = load_events()
            event = next((e for e in events_all if e.get("title") == title), None)
            if not event:
                await inter.response.send_message("❌ Selected event not found (titles may be duplicated).", ephemeral=True)
                return
            if event.get("thread_id"):
                thread = interaction.guild.get_thread(event.get("thread_id"))
                if thread:
                    try:
                        await thread.edit(archived=True)
                    except Exception:
                        logging.exception("Failed to archive thread for event %s", title)
            events_all = [e for e in events_all if e.get("title") != title]
            save_events(events_all)
            embed = build_calendar_embed(events_all)
            try:
                await inter.response.edit_message(embed=embed)
            except Exception:
                await inter.response.send_message("✅ Event removed — calendar updated.", ephemeral=True)
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
            # already running (hot-reload)
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
                        try:
                            await channel.send(f"⏰ Reminder: {event['title']} starts at {dt.strftime('%d %b %Y, %H:%M %Z')}!\n{' '.join(mentions)}")
                        except Exception:
                            logging.exception("Failed to send reminder for event %s", event.get("title"))
                        event["reminded"] = True
                        updated = True
        if updated:
            save_events(events)

def setup(bot):
    bot.add_cog(CalendarCog(bot))

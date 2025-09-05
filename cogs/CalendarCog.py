import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import pytz
import json
import logging
import os
from typing import Optional
from uuid import uuid4

# ===== CONFIG =====
EVENTS_FILE = "events.json"
TIMEZONE = pytz.timezone("Europe/London")
CALENDAR_MANAGER_ROLES = ["Administration", "7DR-SNCO", "Fight Arrangeer"]
# Required: set these to your guild & channel IDs (integers)
GUILD_ID = 1097913605082579024  # <-- REPLACE with your guild id (int) for fast command sync (optional)
CALENDAR_CHANNEL_ID = 1332736267485708419  # <-- REPLACE with your calendar channel id (int)

# ===== LOGGING =====
logging.basicConfig(
    level=logging.INFO,
    filename="calendar_debug.log",
    filemode="a",
    format="%(asctime)s %(levelname)s %(message)s",
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger().addHandler(console)

# ---------------- Utility ----------------
def has_calendar_permission(member: discord.Member) -> bool:
    return any(role.name in CALENDAR_MANAGER_ROLES for role in getattr(member, "roles", []))


def _default_event_structure() -> dict:
    return {
        "id": None,
        "title": "Untitled",
        "date": "TBD",
        "recurring": False,
        "organiser": None,
        "squad_maker": None,
        "reminder_hours": None,
        "guild_id": GUILD_ID if isinstance(GUILD_ID, int) and GUILD_ID else None,
        "thread_channel": None,
        "thread_id": None,
        "reminded": False,
    }


def load_events() -> list:
    """
    Load events from EVENTS_FILE and normalize them into a list of dicts.
    If normalization occurs, the file is rewritten with normalized entries.
    """
    try:
        with open(EVENTS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return []
    except Exception:
        logging.exception("Failed to load events file")
        return []

    normalized = []
    changed = False

    # If file contains a single string or single dict, coerce into list
    if isinstance(raw, str):
        raw = [raw]
        changed = True
    elif isinstance(raw, dict):
        raw = [raw]
        changed = True

    if not isinstance(raw, list):
        logging.warning("events file contains unexpected type %s; ignoring", type(raw))
        return []

    for item in raw:
        if isinstance(item, str):
            ev = _default_event_structure()
            ev["id"] = str(uuid4())
            ev["title"] = item
            normalized.append(ev)
            changed = True
        elif isinstance(item, dict):
            ev = _default_event_structure()
            ev.update(item)
            if not ev.get("id"):
                ev["id"] = str(uuid4())
                changed = True
            # coerce mention strings -> ints if needed
            if isinstance(ev.get("organiser"), str):
                digits = "".join(ch for ch in ev["organiser"] if ch.isdigit())
                try:
                    ev["organiser"] = int(digits) if digits else None
                except Exception:
                    ev["organiser"] = None
            if isinstance(ev.get("squad_maker"), str):
                digits = "".join(ch for ch in ev["squad_maker"] if ch.isdigit())
                try:
                    ev["squad_maker"] = int(digits) if digits else None
                except Exception:
                    ev["squad_maker"] = None
            if ev.get("reminder_hours") is not None:
                try:
                    ev["reminder_hours"] = int(ev["reminder_hours"])
                except Exception:
                    ev["reminder_hours"] = None
                    changed = True
            normalized.append(ev)
        else:
            logging.warning("Skipping event with unsupported type %s", type(item))
            changed = True

    if changed:
        try:
            tmp_path = EVENTS_FILE + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(normalized, f, indent=4)
            os.replace(tmp_path, EVENTS_FILE)
            logging.info("Normalized events file and rewrote %s", EVENTS_FILE)
        except Exception:
            logging.exception("Failed to rewrite normalized events file")

    return normalized


def save_events(events: list):
    try:
        tmp_path = EVENTS_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(events, f, indent=4)
        os.replace(tmp_path, EVENTS_FILE)
    except Exception:
        logging.exception("Failed to save events to file")


def event_to_str(event: dict) -> str:
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
    thread = (
        f"[Link](https://discord.com/channels/{event.get('guild_id')}/{event.get('thread_id')})"
        if event.get("thread_id")
        else (f"<#{event.get('thread_channel')}>" if event.get("thread_channel") else "None")
    )
    return (
        f"📌 **{event.get('title','Untitled')}**\n"
        f"🗓️ {date_str}\n"
        f"👤 Organiser: {organiser}\n"
        f"⚔️ Squad Maker: {squad_maker}\n"
        f"⏰ Reminder: {reminder}\n"
        f"🧵 Thread: {thread}"
    )


def group_events(events: list):
    now = datetime.now(TIMEZONE)
    this_month, next_month, future = [], [], []
    for event in events:
        try:
            if not event.get("date") or event.get("date") == "TBD":
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


def build_calendar_embed(events: list) -> discord.Embed:
    this_month, next_month, future = group_events(events)
    embed = discord.Embed(
        title="📅 Unit Calendar",
        description="Upcoming scheduled events",
        colour=discord.Colour.blue(),
        timestamp=datetime.now(TIMEZONE),
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


# ---------------- Cog (with full management subcommands) ----------------
class CalendarCog(commands.Cog):
    calendar = app_commands.Group(name="calendar", description="Manage the unit calendar")

    def __init__(self, bot):
        self.bot = bot
        self._synced = False
        # start a startup task that waits for ready before starting reminder loop
        self.bot.loop.create_task(self._startup())

    async def _startup(self):
        await self.bot.wait_until_ready()
        # normalize events file on startup
        load_events()
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
        # sync app commands once application_id is available
        if not self._synced:
            try:
                if self.bot.application_id is None:
                    logging.warning("application_id is not yet set; skipping sync for now")
                else:
                    if isinstance(GUILD_ID, int) and GUILD_ID:
                        await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))
                    else:
                        await self.bot.tree.sync()
                    logging.info("App commands synced successfully")
                    self._synced = True
            except Exception:
                logging.exception("Failed to sync app commands in on_ready")

        # refresh calendar embed in configured channel (delete previous and post fresh)
        try:
            if not isinstance(GUILD_ID, int) or not isinstance(CALENDAR_CHANNEL_ID, int) or GUILD_ID == 0 or CALENDAR_CHANNEL_ID == 0:
                logging.info("GUILD_ID or CALENDAR_CHANNEL_ID not configured; skipping embed refresh")
                return
            channel = self.bot.get_channel(CALENDAR_CHANNEL_ID)
            if not channel:
                logging.warning("Calendar channel not found (id=%s)", CALENDAR_CHANNEL_ID)
                return
            async for msg in channel.history(limit=200):
                if msg.author == self.bot.user and msg.embeds:
                    for emb in msg.embeds:
                        if emb.title == "📅 Unit Calendar":
                            try:
                                await msg.delete()
                                logging.info("Deleted previous calendar embed message id=%s", msg.id)
                            except Exception:
                                logging.exception("Failed to delete previous calendar embed id=%s", msg.id)
            events = load_events()
            embed = build_calendar_embed(events)
            await channel.send(embed=embed)
        except Exception:
            logging.exception("Failed to refresh calendar on_ready")

    # --- Subcommands under /calendar ---

    @calendar.command(name="show", description="Show the unit calendar")
    async def show(self, interaction: discord.Interaction):
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message("❌ You don’t have permission to manage the calendar.", ephemeral=True)
            return
        events = load_events()
        embed = build_calendar_embed(events)
        await interaction.response.send_message(embed=embed)

    @calendar.command(name="list", description="List events with their IDs")
    async def list_events(self, interaction: discord.Interaction):
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message("❌ You don’t have permission.", ephemeral=True)
            return
        events = load_events()
        if not events:
            await interaction.response.send_message("No events.", ephemeral=True)
            return
        lines = []
        for ev in events:
            ev_id = ev.get("id")
            date = ev.get("date", "TBD")
            lines.append(f"`{ev_id}` — **{ev.get('title','Untitled')}** — {date}")
        # chunk output if needed
        chunk_size = 40
        chunks = [lines[i:i+chunk_size] for i in range(0, len(lines), chunk_size)]
        for i, ch in enumerate(chunks):
            # send the first chunk as the initial response, subsequent as followups
            if i == 0:
                await interaction.response.send_message("\n".join(ch), ephemeral=True)
            else:
                await interaction.followup.send("\n".join(ch), ephemeral=True)

    @calendar.command(name="add", description="Add an event")
    @app_commands.describe(
        title="Event title",
        date="ISO date/time (YYYY-MM-DDTHH:MM:SS) or 'TBD' (optional)",
        reminder_hours="Hours before to remind (optional)",
        organiser="Organiser (optional)",
        squad_maker="Squad-maker (optional)",
        recurring="Is this recurring? (optional)",
        thread_channel="Thread/channel mention (optional)",
        thread_id="Thread id (optional)"
    )
    async def add_event(
        self,
        interaction: discord.Interaction,
        title: str,
        date: Optional[str] = None,
        reminder_hours: Optional[int] = None,
        organiser: Optional[discord.Member] = None,
        squad_maker: Optional[discord.Member] = None,
        recurring: Optional[bool] = False,
        thread_channel: Optional[discord.TextChannel] = None,
        thread_id: Optional[int] = None,
    ):
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message("❌ You don’t have permission.", ephemeral=True)
            return
        events = load_events()
        ev = _default_event_structure()
        ev["id"] = str(uuid4())
        ev["title"] = title
        ev["date"] = date if date else "TBD"
        ev["reminder_hours"] = int(reminder_hours) if reminder_hours is not None else None
        ev["organiser"] = organiser.id if organiser else None
        ev["squad_maker"] = squad_maker.id if squad_maker else None
        ev["recurring"] = bool(recurring)
        ev["guild_id"] = interaction.guild.id if interaction.guild else ev.get("guild_id")
        if thread_channel:
            ev["thread_channel"] = thread_channel.id
        if thread_id:
            ev["thread_id"] = int(thread_id)
        events.append(ev)
        save_events(events)
        await interaction.response.send_message(f"✅ Added event `{ev['id']}`.", ephemeral=True)

    @calendar.command(name="edit", description="Edit an existing event by id")
    @app_commands.describe(
        event_id="Event ID to edit",
        title="New title (optional)",
        date="New date (optional)",
        reminder_hours="New reminder hours (optional)",
        organiser="New organiser (optional)",
        squad_maker="New squad maker (optional)",
        recurring="Set recurring True/False (optional)",
        thread_channel="Thread/channel mention (optional)",
        thread_id="Thread id (optional)"
    )
    async def edit_event(
        self,
        interaction: discord.Interaction,
        event_id: str,
        title: Optional[str] = None,
        date: Optional[str] = None,
        reminder_hours: Optional[int] = None,
        organiser: Optional[discord.Member] = None,
        squad_maker: Optional[discord.Member] = None,
        recurring: Optional[bool] = None,
        thread_channel: Optional[discord.TextChannel] = None,
        thread_id: Optional[int] = None,
    ):
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message("❌ You don’t have permission.", ephemeral=True)
            return
        events = load_events()
        target = next((e for e in events if e.get("id") == event_id), None)
        if not target:
            await interaction.response.send_message("❌ Event not found.", ephemeral=True)
            return
        if title is not None:
            target["title"] = title
        if date is not None:
            target["date"] = date
        if reminder_hours is not None:
            target["reminder_hours"] = int(reminder_hours)
        if organiser is not None:
            target["organiser"] = organiser.id
        if squad_maker is not None:
            target["squad_maker"] = squad_maker.id
        if recurring is not None:
            target["recurring"] = bool(recurring)
        if thread_channel is not None:
            target["thread_channel"] = thread_channel.id
        if thread_id is not None:
            target["thread_id"] = int(thread_id)
        save_events(events)
        await interaction.response.send_message(f"✅ Updated `{event_id}`.", ephemeral=True)

    @calendar.command(name="remove", description="Delete an event by id")
    @app_commands.describe(event_id="Event ID to delete")
    async def remove_event(self, interaction: discord.Interaction, event_id: str):
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message("❌ You don’t have permission.", ephemeral=True)
            return
        events = load_events()
        new = [e for e in events if e.get("id") != event_id]
        if len(new) == len(events):
            await interaction.response.send_message("❌ Event not found.", ephemeral=True)
            return
        save_events(new)
        await interaction.response.send_message(f"✅ Deleted `{event_id}`.", ephemeral=True)

    @tasks.loop(minutes=10)
    async def reminder_task(self):
        """
        Reminder loop:
        - skips non-dict or malformed entries
        - skips events without reminder_hours (explicitly None)
        - tolerates 'TBD' dates and malformed ISO dates
        """
        events = load_events()
        now = datetime.now(TIMEZONE)
        updated = False
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("reminder_hours") is None or event.get("reminded"):
                continue
            try:
                if not event.get("date") or event.get("date") == "TBD":
                    continue
                dt = datetime.fromisoformat(event["date"]).astimezone(TIMEZONE)
            except Exception:
                continue
            try:
                if now + timedelta(hours=int(event["reminder_hours"])) >= dt > now:
                    guild_id = event.get("guild_id")
                    if not guild_id:
                        continue
                    guild = self.bot.get_guild(guild_id)
                    if guild:
                        send_channel = None
                        # prefer configured thread/channel if present
                        tc = event.get("thread_channel")
                        if tc:
                            send_channel = guild.get_channel(tc) if hasattr(guild, "get_channel") else None
                        if send_channel is None:
                            send_channel = guild.system_channel or discord.utils.get(guild.text_channels, permissions__send_messages=True)
                        if send_channel:
                            mentions = []
                            if event.get("organiser"):
                                mentions.append(f"<@{event['organiser']}>")
                            if event.get("squad_maker"):
                                mentions.append(f"<@{event['squad_maker']}>")
                            try:
                                await send_channel.send(
                                    f"⏰ Reminder: {event['title']} starts at {dt.strftime('%d %b %Y, %H:%M %Z')}!\n{' '.join(mentions)}"
                                )
                            except Exception:
                                logging.exception("Failed to send reminder for event %s", event.get("title"))
                            event["reminded"] = True
                            updated = True
            except Exception:
                logging.exception("Error while processing reminder for event %s", event.get("title"))

        if updated:
            save_events(events)


async def setup(bot):
    await bot.add_cog(CalendarCog(bot))

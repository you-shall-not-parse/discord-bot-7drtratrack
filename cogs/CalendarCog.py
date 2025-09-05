import discord
from discord import app_commands
from discord.ext import commands, tasks
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
GUILD_ID = 0  # <-- REPLACE with your guild id (int) for fast command sync (optional)
CALENDAR_CHANNEL_ID = 0  # <-- REPLACE with your calendar channel id (int)

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
    return any(role.name in CALENDAR_MANAGER_ROLES for role in member.roles)


def _default_event_structure() -> dict:
    return {
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


def load_events():
    """
    Load events from EVENTS_FILE and normalize them into a list of dicts.

    This function tolerates legacy formats where events might be simple strings
    (title only) or partially-formed objects. If normalization occurs, the
    file is rewritten with normalized entries so future loads are safe.
    """
    try:
        with open(EVENTS_FILE, "r") as f:
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
            # Legacy: just a title string
            ev = _default_event_structure()
            ev["title"] = item
            normalized.append(ev)
            changed = True
        elif isinstance(item, dict):
            # Ensure expected keys exist and have sensible defaults
            ev = _default_event_structure()
            ev.update(item)  # keep existing keys, fill missing with defaults
            # Some older entries might have organiser as mention string; try to extract digits
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
            # guard types
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
            # Persist normalized form back to disk so next run is clean
            tmp_path = EVENTS_FILE + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(normalized, f, indent=4)
            os.replace(tmp_path, EVENTS_FILE)
            logging.info("Normalized events file and rewrote %s", EVENTS_FILE)
        except Exception:
            logging.exception("Failed to rewrite normalized events file")

    return normalized


def save_events(events):
    try:
        # atomic-ish write
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
    thread = (
        f"[Link](https://discord.com/channels/{event.get('guild_id')}/{event.get('thread_id')})"
        if event.get("thread_id")
        else "None"
    )
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
            # if recurring and far in the future, skip
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


# ---------------- Cog (no buttons / no UI) ----------------
class CalendarCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._synced = False  # ensure we only sync once (after application_id is available)
        try:
            self.reminder_task.start()
        except RuntimeError:
            # already running (hot-reload)
            pass

    async def cog_load(self):
        # IMPORTANT: do NOT add the app command here.
        # discord.ext.commands.Cog._inject will register decorated app commands for this cog.
        # Syncing here may run before application_id is available (causing MissingApplicationID).
        # We perform sync in on_ready (below) where application_id should be present.
        return

    def cog_unload(self):
        try:
            self.reminder_task.cancel()
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_ready(self):
        # Sync app commands once, after the bot is ready and application_id is set.
        if not self._synced:
            try:
                # Wait for application_id to be available.
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
            # post fresh calendar embed without interactive buttons
            events = load_events()
            embed = build_calendar_embed(events)
            await channel.send(embed=embed)
        except Exception:
            logging.exception("Failed to refresh calendar on_ready")

    # --- App (slash) command only (no UI/buttons) ---
    @app_commands.command(name="7drcalendar", description="Show or update the unit calendar")
    async def calendar_app(self, interaction: discord.Interaction):
        # permission check
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message("❌ You don’t have permission to manage the calendar.", ephemeral=True)
            return
        events = load_events()
        embed = build_calendar_embed(events)
        try:
            await interaction.response.send_message(embed=embed)
        except Exception:
            logging.exception("Failed to send /7drcalendar response")
            try:
                await interaction.response.send_message("✅ Calendar generated (failed to embed).", ephemeral=True)
            except Exception:
                pass

    @tasks.loop(minutes=10)
    async def reminder_task(self):
        """
        Robust reminder loop:
        - skips non-dict or malformed entries
        - skips events without reminder_hours (explicitly None)
        - tolerates 'TBD' dates and malformed ISO dates
        """
        events = load_events()
        now = datetime.now(TIMEZONE)
        updated = False
        for event in events:
            # Defensive: ensure event is a dict
            if not isinstance(event, dict):
                logging.debug("Skipping reminder for non-dict event: %s", repr(event))
                continue

            # Skip if no reminder or already reminded
            if event.get("reminder_hours") is None or event.get("reminded"):
                continue

            # Parse event date safely
            try:
                if not event.get("date") or event.get("date") == "TBD":
                    continue
                dt = datetime.fromisoformat(event["date"]).astimezone(TIMEZONE)
            except Exception:
                logging.debug("Skipping reminder for event with invalid date: %s", event.get("title"))
                continue

            try:
                if now + timedelta(hours=int(event["reminder_hours"])) >= dt > now:
                    guild_id = event.get("guild_id")
                    if not guild_id:
                        logging.debug("Event %s missing guild_id; skipping reminder", event.get("title"))
                        continue
                    guild = self.bot.get_guild(guild_id)
                    if guild:
                        channel = guild.system_channel or discord.utils.get(guild.text_channels, permissions__send_messages=True)
                        if channel:
                            mentions = []
                            if event.get("organiser"):
                                mentions.append(f"<@{event['organiser']}>")
                            if event.get("squad_maker"):
                                mentions.append(f"<@{event['squad_maker']}>")
                            try:
                                await channel.send(
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

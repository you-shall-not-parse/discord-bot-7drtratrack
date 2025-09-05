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
        try:
            self.reminder_task.start()
        except RuntimeError:
            # already running (hot-reload)
            pass

    async def cog_load(self):
        # Register the app command on the tree and sync to guild if provided.
        # Use the new command name "7drcalendar" to avoid collisions with other bots.
        try:
            cmd_name = "7drcalendar"
            # only add if not already present
            if self.bot.tree.get_command(cmd_name) is None:
                self.bot.tree.add_command(self.calendar_app)
            if isinstance(GUILD_ID, int) and GUILD_ID:
                await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))
            else:
                await self.bot.tree.sync()
        except Exception:
            logging.exception("Failed to sync app commands in cog_load")

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
                        if event.get("organiser"):
                            mentions.append(f"<@{event['organiser']}>")
                        if event.get("squad_maker"):
                            mentions.append(f"<@{event['squad_maker']}>")
                        try:
                            await channel.send(f"⏰ Reminder: {event['title']} starts at {dt.strftime('%d %b %Y, %H:%M %Z')}!\n{' '.join(mentions)}")
                        except Exception:
                            logging.exception("Failed to send reminder for event %s", event.get("title"))
                        event["reminded"] = True
                        updated = True
        if updated:
            save_events(events)


async def setup(bot):
    await bot.add_cog(CalendarCog(bot))

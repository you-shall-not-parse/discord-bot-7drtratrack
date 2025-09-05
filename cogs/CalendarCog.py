import json
import pytz
import calendar
import discord
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from discord import app_commands
from discord.ext import commands, tasks

# ---------------- Config ----------------
EVENTS_FILE = "events.json"
STATE_FILE = "calendar_state.json"  # stores per-guild channel_id and last message_id
TIMEZONE = pytz.timezone("Europe/London")
CALENDAR_MANAGER_ROLES = ["Admininstration", "7DR-SNCO", "Fight Arrangeer"]

# Set your target guild and (optionally) a default calendar channel here.
# You can also configure the channel via the /calendar setchannel command and it will be saved in STATE_FILE.
CALENDAR_GUILD_ID = 1097913605082579024  # <-- replace with your guild ID
DEFAULT_CALENDAR_CHANNEL_ID = 1332736267485708419  # <-- set to a channel ID or leave 0 to require /calendar setchannel

# Auto (re)publish the calendar on startup. It will delete the previous embed and post a new one.
AUTO_PUBLISH_ON_START = True

TARGET_GUILD = discord.Object(id=CALENDAR_GUILD_ID)


# ---------------- Utility ----------------
def has_calendar_permission(member: discord.Member) -> bool:
    return any(role.name in CALENDAR_MANAGER_ROLES for role in member.roles)


def load_events() -> list:
    try:
        with open(EVENTS_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save_events(events: list) -> None:
    with open(EVENTS_FILE, "w") as f:
        json.dump(events, f, indent=4)


def load_state() -> Dict[str, Any]:
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_state(state: Dict[str, Any]) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)


def get_guild_state(guild_id: int) -> Dict[str, Any]:
    state = load_state()
    return state.get(str(guild_id), {})


def set_guild_state(guild_id: int, data: Dict[str, Any]) -> None:
    state = load_state()
    state[str(guild_id)] = data
    save_state(state)


def set_channel_for_guild(guild_id: int, channel_id: int) -> None:
    gs = get_guild_state(guild_id)
    gs["channel_id"] = channel_id
    set_guild_state(guild_id, gs)


def get_channel_id_for_guild(guild_id: int) -> Optional[int]:
    gs = get_guild_state(guild_id)
    return gs.get("channel_id") or (DEFAULT_CALENDAR_CHANNEL_ID if DEFAULT_CALENDAR_CHANNEL_ID else None)


def set_message_id_for_guild(guild_id: int, message_id: Optional[int]) -> None:
    gs = get_guild_state(guild_id)
    if message_id is None:
        gs.pop("message_id", None)
    else:
        gs["message_id"] = message_id
    set_guild_state(guild_id, gs)


def get_message_id_for_guild(guild_id: int) -> Optional[int]:
    gs = get_guild_state(guild_id)
    return gs.get("message_id")


def event_to_str(event: dict) -> str:
    dt = datetime.fromisoformat(event["date"]).astimezone(TIMEZONE)
    organiser = f"<@{event['organiser']}>" if event.get("organiser") else "Unknown"
    squad_maker = f"<@{event['squad_maker']}>" if event.get("squad_maker") else "None"
    reminder = f"{event['reminder_hours']}h before" if event.get("reminder_hours") else "None"
    thread = (
        f"[Link](https://discord.com/channels/{event['guild_id']}/{event['thread_id']})"
        if event.get("thread_id")
        else "None"
    )
    return (
        f"📌 **{event['title']}**\n"
        f"🗓️ {dt.strftime('%d %b %Y, %H:%M %Z')}\n"
        f"👤 Organiser: {organiser}\n"
        f"⚔️ Squad Maker: {squad_maker}\n"
        f"⏰ Reminder: {reminder}\n"
        f"🧵 Thread: {thread}"
    )


def build_calendar_embed(events: list) -> discord.Embed:
    now = datetime.now(TIMEZONE)

    # Group upcoming events by (year, month) and sort
    month_groups: Dict[tuple[int, int], list] = {}
    for e in events:
        dt = datetime.fromisoformat(e["date"]).astimezone(TIMEZONE)
        if dt < now:
            continue
        if e.get("recurring") and dt > now + timedelta(weeks=2):
            continue

        key = (dt.year, dt.month)
        month_groups.setdefault(key, []).append(e)

    sorted_months = sorted(month_groups.keys())
    for key in sorted_months:
        month_groups[key].sort(key=lambda ev: datetime.fromisoformat(ev["date"]))

    embed = discord.Embed(
        title="📅 Unit Calendar",
        description="Upcoming scheduled events",
        colour=discord.Colour.blue(),
        timestamp=datetime.now(TIMEZONE),
    )

    if not sorted_months:
        embed.description = "No events scheduled."
        return embed

    for (year, month) in sorted_months:
        month_name = f"{calendar.month_name[month]} {year}"
        body = "\n\n".join(event_to_str(e) for e in month_groups[(year, month)])
        embed.add_field(name=month_name, value=body, inline=False)

    return embed


def parse_datetime_local(date_time_str: str) -> Optional[datetime]:
    """
    Parse 'YYYY-MM-DD HH:MM' into a timezone-aware datetime in TIMEZONE.
    Returns None if invalid.
    """
    try:
        dt = datetime.strptime(date_time_str, "%Y-%m-%d %H:%M")
        return TIMEZONE.localize(dt)
    except Exception:
        return None


def find_sendable_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """
    Fallback channel for reminders if no calendar channel is set.
    """
    if not guild:
        return None
    me = guild.me
    if guild.system_channel and guild.system_channel.permissions_for(me).send_messages:
        return guild.system_channel
    for ch in guild.text_channels:
        if ch.permissions_for(me).send_messages:
            return ch
    return None


# ---------------- Slash Commands (discord.py 2.3.2 via app_commands) ----------------
class CalendarCog(commands.GroupCog, name="calendar"):
    """Manage the unit calendar"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.reminder_task.start()

    def cog_unload(self):
        self.reminder_task.cancel()

    # ---------- Helpers ----------
    def get_calendar_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        channel_id = get_channel_id_for_guild(guild.id)
        if channel_id:
            ch = guild.get_channel(channel_id)
            if isinstance(ch, discord.TextChannel):
                me = guild.me
                if ch and ch.permissions_for(me).send_messages:
                    return ch
        return None

    async def publish_to_channel(self, guild: discord.Guild) -> Optional[discord.Message]:
        """
        Delete the previous calendar embed (if any) and post a new one to the configured channel.
        Stores the new message ID in STATE_FILE.
        """
        channel = self.get_calendar_channel(guild)
        if not channel:
            return None

        # Delete previous message if exists
        prev_id = get_message_id_for_guild(guild.id)
        if prev_id:
            try:
                msg = await channel.fetch_message(prev_id)
                await msg.delete()
            except Exception:
                # Message might have been deleted or inaccessible
                pass

        # Build and send new embed
        events = load_events()
        embed = build_calendar_embed(events)
        try:
            new_msg = await channel.send(embed=embed)
            set_message_id_for_guild(guild.id, new_msg.id)
            return new_msg
        except Exception:
            return None

    # ---------- Autocomplete ----------
    async def autocomplete_event_titles(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        events = load_events()
        titles = sorted({e["title"] for e in events})
        filtered = [t for t in titles if current.lower() in t.lower()]
        return [app_commands.Choice(name=t, value=t) for t in filtered[:25]]

    # ---------- Commands ----------
    @app_commands.guilds(TARGET_GUILD)
    @app_commands.command(name="show", description="Preview the unit calendar (ephemeral).")
    async def show(self, interaction: discord.Interaction):
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message(
                "❌ You don’t have permission to manage the calendar.", ephemeral=True
            )
            return

        events = load_events()
        embed = build_calendar_embed(events)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.guilds(TARGET_GUILD)
    @app_commands.command(name="publish", description="Post the calendar embed to the configured channel and delete the previous one.")
    async def publish(self, interaction: discord.Interaction):
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message(
                "❌ You don’t have permission to manage the calendar.", ephemeral=True
            )
            return

        if not self.get_calendar_channel(interaction.guild):
            await interaction.response.send_message(
                "⚠️ No calendar channel is configured. Use /calendar setchannel first.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        msg = await self.publish_to_channel(interaction.guild)
        if msg:
            await interaction.followup.send(f"✅ Published calendar to <#{msg.channel.id}> (message ID {msg.id}).", ephemeral=True)
        else:
            await interaction.followup.send("❌ Failed to publish calendar (check channel permissions).", ephemeral=True)

    @app_commands.guilds(TARGET_GUILD)
    @app_commands.command(name="setchannel", description="Set the channel to post the calendar embed.")
    @app_commands.describe(channel="Text channel where the calendar embed will be posted")
    async def setchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message(
                "❌ You don’t have permission to manage the calendar.", ephemeral=True
            )
            return

        me = interaction.guild.me
        if not channel.permissions_for(me).send_messages:
            await interaction.response.send_message(
                "❌ I don't have permission to send messages in that channel.", ephemeral=True
            )
            return

        set_channel_for_guild(interaction.guild_id, channel.id)
        await interaction.response.send_message(f"✅ Calendar channel set to {channel.mention}.", ephemeral=True)

    @app_commands.guilds(TARGET_GUILD)
    @app_commands.command(name="add", description="Add a new event.")
    @app_commands.describe(
        title="Event title",
        date_time="Date & time in 'YYYY-MM-DD HH:MM' (local UK time)",
        recurring="Whether the event is recurring (limits far-future display)",
        organiser="Organiser (mention)",
        squad_maker="Squad maker (mention)",
        reminder_hours="Hours before the event to send a reminder",
        thread_channel="Channel to create a public thread with the event details",
    )
    async def add(
        self,
        interaction: discord.Interaction,
        title: str,
        date_time: str,
        recurring: Optional[bool] = False,
        organiser: Optional[discord.Member] = None,
        squad_maker: Optional[discord.Member] = None,
        reminder_hours: Optional[int] = None,
        thread_channel: Optional[discord.TextChannel] = None,
    ):
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message(
                "❌ You don’t have permission to manage events.", ephemeral=True
            )
            return

        dt = parse_datetime_local(date_time)
        if not dt:
            await interaction.response.send_message(
                "❌ Invalid date format. Use YYYY-MM-DD HH:MM.", ephemeral=True
            )
            return

        events = load_events()
        new_event = {
            "title": title,
            "date": dt.isoformat(),
            "recurring": bool(recurring),
            "organiser": organiser.id if organiser else interaction.user.id,
            "squad_maker": squad_maker.id if squad_maker else None,
            "reminder_hours": reminder_hours if (isinstance(reminder_hours, int) and reminder_hours > 0) else None,
            "guild_id": interaction.guild_id,
            "thread_channel": thread_channel.id if thread_channel else None,
            "thread_id": None,
            "reminded": False,
        }

        # Create thread if requested
        if thread_channel:
            try:
                thread = await thread_channel.create_thread(
                    name=title, type=discord.ChannelType.public_thread
                )
                await thread.send(event_to_str(new_event))
                new_event["thread_id"] = thread.id
            except Exception:
                pass

        events.append(new_event)
        save_events(events)

        # Publish updated calendar to the configured channel (delete previous then post)
        await interaction.response.defer(ephemeral=True)
        await self.publish_to_channel(interaction.guild)
        await interaction.followup.send("✅ Event added and calendar updated.", ephemeral=True)

    @app_commands.guilds(TARGET_GUILD)
    @app_commands.command(name="edit", description="Edit an existing event (selected by title).")
    @app_commands.autocomplete(title=autocomplete_event_titles)
    @app_commands.describe(
        title="Existing event title to edit",
        new_title="New title (optional)",
        new_date_time="New date & time in 'YYYY-MM-DD HH:MM' (optional)",
        recurring="Set recurring on/off (optional)",
        organiser="New organiser (optional)",
        squad_maker="New squad maker (optional)",
        reminder_hours="New reminder hours (optional)",
        thread_channel="Create a new public thread in this channel (optional)",
    )
    async def edit(
        self,
        interaction: discord.Interaction,
        title: str,
        new_title: Optional[str] = None,
        new_date_time: Optional[str] = None,
        recurring: Optional[bool] = None,
        organiser: Optional[discord.Member] = None,
        squad_maker: Optional[discord.Member] = None,
        reminder_hours: Optional[int] = None,
        thread_channel: Optional[discord.TextChannel] = None,
    ):
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message(
                "❌ You don’t have permission to manage events.", ephemeral=True
            )
            return

        events = load_events()
        try:
            event = next(e for e in events if e["title"] == title)
        except StopIteration:
            await interaction.response.send_message("❌ Event not found.", ephemeral=True)
            return

        if new_title:
            event["title"] = new_title

        if new_date_time:
            dt = parse_datetime_local(new_date_time)
            if not dt:
                await interaction.response.send_message(
                    "❌ Invalid date format. Use YYYY-MM-DD HH:MM.", ephemeral=True
                )
                return
            event["date"] = dt.isoformat()
            event["reminded"] = False  # reset reminder if date changed

        if recurring is not None:
            event["recurring"] = bool(recurring)

        if organiser is not None:
            event["organiser"] = organiser.id

        if squad_maker is not None:
            event["squad_maker"] = squad_maker.id

        if reminder_hours is not None:
            event["reminder_hours"] = reminder_hours if (isinstance(reminder_hours, int) and reminder_hours > 0) else None
            event["reminded"] = False

        if thread_channel:
            try:
                thread = await thread_channel.create_thread(
                    name=event["title"], type=discord.ChannelType.public_thread
                )
                await thread.send(event_to_str(event))
                event["thread_id"] = thread.id
            except Exception:
                pass

        save_events(events)

        await interaction.response.defer(ephemeral=True)
        await self.publish_to_channel(interaction.guild)
        await interaction.followup.send("✅ Event updated and calendar refreshed.", ephemeral=True)

    @app_commands.guilds(TARGET_GUILD)
    @app_commands.command(name="remove", description="Remove an event by title.")
    @app_commands.autocomplete(title=autocomplete_event_titles)
    @app_commands.describe(title="Event title to remove")
    async def remove(self, interaction: discord.Interaction, title: str):
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message(
                "❌ You don’t have permission to manage events.", ephemeral=True
            )
            return

        events = load_events()
        try:
            event = next(e for e in events if e["title"] == title)
        except StopIteration:
            await interaction.response.send_message("❌ Event not found.", ephemeral=True)
            return

        # Archive thread if exists
        if event.get("thread_id"):
            try:
                thread = interaction.guild.get_thread(event["thread_id"])
                if thread:
                    await thread.edit(archived=True)
            except Exception:
                pass

        events = [e for e in events if e["title"] != title]
        save_events(events)

        await interaction.response.defer(ephemeral=True)
        await self.publish_to_channel(interaction.guild)
        await interaction.followup.send("🗑️ Event removed and calendar updated.", ephemeral=True)

    # ---------- Background tasks ----------
    @tasks.loop(minutes=10)
    async def reminder_task(self):
        events = load_events()
        now = datetime.now(TIMEZONE)
        updated = False
        for event in events:
            if not event.get("reminder_hours") or event.get("reminded"):
                continue
            dt = datetime.fromisoformat(event["date"]).astimezone(TIMEZONE)
            if now + timedelta(hours=event["reminder_hours"]) >= dt > now:
                guild = self.bot.get_guild(event["guild_id"])
                if guild:
                    # Prefer the calendar channel for reminders if set
                    channel = self.get_calendar_channel(guild) or find_sendable_channel(guild)
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
                            event["reminded"] = True
                            updated = True
                        except Exception:
                            pass
        if updated:
            save_events(events)

    @reminder_task.before_loop
    async def before_reminder_task(self):
        await self.bot.wait_until_ready()

    # ---------- Sync and optional autopublish ----------
    @commands.Cog.listener()
    async def on_ready(self):
        # Ensure commands are synced to the target guild only
        try:
            await self.bot.tree.sync(guild=discord.Object(id=CALENDAR_GUILD_ID))
        except Exception:
            pass

        if AUTO_PUBLISH_ON_START:
            try:
                guild = self.bot.get_guild(CALENDAR_GUILD_ID)
                if guild and self.get_calendar_channel(guild):
                    await self.publish_to_channel(guild)
            except Exception:
                pass


# ---------------- Extension setup ----------------
def setup(bot: commands.Bot):
    bot.add_cog(CalendarCog(bot))

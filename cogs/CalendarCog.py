import json
import pytz
import calendar
import discord
from datetime import datetime, timedelta, time
from typing import Optional, List, Dict, Any
from discord import app_commands
from discord.ext import commands, tasks

# ---------------- Config ----------------
EVENTS_FILE = "events.json"
STATE_FILE = "calendar_state.json"  # stores message_id
TIMEZONE = pytz.timezone("Europe/London")
CALENDAR_MANAGER_ROLES = ["Administration", "7DR-SNCO", "Fight Arrangeer"]

# Set your target guild and calendar channel here.
CALENDAR_GUILD_ID = 1097913605082579024
CALENDAR_CHANNEL_ID = 1332736267485708419  # The channel where the calendar will be posted

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


def get_message_id_for_guild(guild_id: int) -> Optional[int]:
    state = load_state()
    return state.get(str(guild_id), {}).get("message_id")


def set_message_id_for_guild(guild_id: int, message_id: Optional[int]) -> None:
    state = load_state()
    guild_state = state.get(str(guild_id), {})
    
    if message_id is None:
        guild_state.pop("message_id", None)
    else:
        guild_state["message_id"] = message_id
    
    state[str(guild_id)] = guild_state
    save_state(state)


def event_to_str(event: dict) -> str:
    dt = datetime.fromisoformat(event["date"]).astimezone(TIMEZONE)
    organiser = f"<@{event['organiser']}>" if event.get("organiser") else "Unknown"
    squad_maker = f"<@{event['squad_maker']}>" if event.get("squad_maker") else "None"
    description = event.get("description", "")
    
    thread = (
        f"[Link](https://discord.com/channels/{event['guild_id']}/{event['thread_id']})"
        if event.get("thread_id")
        else "None"
    )
    
    msg = (
        f"📌 **{event['title']}**\n"
        f"🗓️ {dt.strftime('%d %b %Y')}"
    )
    
    # Add time if it exists
    if dt.time() != time(0, 0):
        msg += f", {dt.strftime('%H:%M %Z')}"
    
    msg += f"\n👤 Organiser: {organiser}"
    
    if event.get("squad_maker"):
        msg += f"\n⚔️ Squad Maker: {squad_maker}"
    
    if description:
        msg += f"\n📝 Description: {description}"
    
    if event.get("thread_id"):
        msg += f"\n🧵 Thread: {thread}"
    
    return msg


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


def parse_date(date_str: str) -> Optional[datetime]:
    """Parse 'DD-MM-YYYY' into a date."""
    try:
        return datetime.strptime(date_str, "%d-%m-%Y").replace(tzinfo=TIMEZONE)
    except Exception:
        return None


def parse_time(time_str: str) -> Optional[time]:
    """Parse 'HH:MM' into a time."""
    try:
        t = datetime.strptime(time_str, "%H:%M").time()
        return t
    except Exception:
        return None


def find_sendable_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """Fallback channel for messages if calendar channel is not accessible."""
    if not guild:
        return None
    me = guild.me
    if guild.system_channel and guild.system_channel.permissions_for(me).send_messages:
        return guild.system_channel
    for ch in guild.text_channels:
        if ch.permissions_for(me).send_messages:
            return ch
    return None


# ---------------- Calendar Cog ----------------
class CalendarCog(commands.Cog):
    """Calendar management for the unit"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------- Helpers ----------
    def get_calendar_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        channel = guild.get_channel(CALENDAR_CHANNEL_ID)
        if isinstance(channel, discord.TextChannel):
            me = guild.me
            if channel and channel.permissions_for(me).send_messages:
                return channel
        return None

    async def publish_calendar(self, guild: discord.Guild) -> Optional[discord.Message]:
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

    async def update_thread_message(self, guild: discord.Guild, event: dict) -> None:
        """Update the message in an event's thread with current event details."""
        if not event.get("thread_id"):
            return
            
        try:
            thread = guild.get_thread(event["thread_id"])
            if thread:
                # Get the first message in the thread
                async for message in thread.history(limit=1, oldest_first=True):
                    await message.edit(content=event_to_str(event))
                    break
        except Exception:
            pass

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
    @app_commands.command(name="addtocalendar", description="Add a new event to the unit calendar.")
    @app_commands.describe(
        title="Event title",
        description="Optional description of the event",
        date="Date in DD-MM-YYYY format",
        time="Time in HH:MM format (24-hour, optional)",
        organiser="The event organiser",
        squad_maker="Squad maker (optional)",
        thread_channel="Channel to create a thread for this event (optional)"
    )
    async def addtocalendar(
        self,
        interaction: discord.Interaction,
        title: str,
        date: str,
        organiser: discord.Member,
        description: Optional[str] = None,
        time: Optional[str] = None,
        squad_maker: Optional[discord.Member] = None,
        thread_channel: Optional[discord.TextChannel] = None,
    ):
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message(
                "❌ You don't have permission to manage events.", ephemeral=True
            )
            return

        # Parse date
        event_date = parse_date(date)
        if not event_date:
            await interaction.response.send_message(
                "❌ Invalid date format. Use DD-MM-YYYY.", ephemeral=True
            )
            return

        # Parse time if provided
        if time:
            event_time = parse_time(time)
            if not event_time:
                await interaction.response.send_message(
                    "❌ Invalid time format. Use HH:MM (24-hour).", ephemeral=True
                )
                return
            # Combine date and time
            event_date = event_date.replace(
                hour=event_time.hour,
                minute=event_time.minute
            )
            
        events = load_events()
        new_event = {
            "title": title,
            "description": description,
            "date": event_date.isoformat(),
            "organiser": organiser.id,
            "squad_maker": squad_maker.id if squad_maker else None,
            "guild_id": interaction.guild_id,
            "thread_id": None,
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
                await interaction.response.send_message(
                    "⚠️ Event added but failed to create thread.", ephemeral=True
                )
                return

        events.append(new_event)
        save_events(events)

        # Publish updated calendar
        await interaction.response.defer(ephemeral=True)
        await self.publish_calendar(interaction.guild)
        await interaction.followup.send("✅ Event added and calendar updated.", ephemeral=True)

    @app_commands.guilds(TARGET_GUILD)
    @app_commands.command(name="editcalendar", description="Edit an event on the calendar.")
    @app_commands.autocomplete(title=autocomplete_event_titles)
    @app_commands.describe(
        title="Title of the event to edit",
        new_title="New event title (optional)",
        description="New description (optional)",
        date="New date in DD-MM-YYYY format (optional)",
        time="New time in HH:MM format (24-hour, optional)",
        organiser="New event organiser (optional)",
        squad_maker="New squad maker (optional)",
        thread_channel="Create a new thread in this channel (optional)"
    )
    async def editcalendar(
        self,
        interaction: discord.Interaction,
        title: str,
        new_title: Optional[str] = None,
        description: Optional[str] = None,
        date: Optional[str] = None,
        time: Optional[str] = None,
        organiser: Optional[discord.Member] = None,
        squad_maker: Optional[discord.Member] = None,
        thread_channel: Optional[discord.TextChannel] = None,
    ):
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message(
                "❌ You don't have permission to manage events.", ephemeral=True
            )
            return

        events = load_events()
        try:
            event = next(e for e in events if e["title"] == title)
        except StopIteration:
            await interaction.response.send_message("❌ Event not found.", ephemeral=True)
            return

        # Store the original event date
        current_date = datetime.fromisoformat(event["date"])
        
        if new_title:
            event["title"] = new_title

        if description is not None:  # Allow empty string to clear description
            event["description"] = description
            
        # Handle date changes
        if date:
            event_date = parse_date(date)
            if not event_date:
                await interaction.response.send_message(
                    "❌ Invalid date format. Use DD-MM-YYYY.", ephemeral=True
                )
                return
                
            # If only date is provided, keep the current time
            if not time:
                event_date = event_date.replace(
                    hour=current_date.hour,
                    minute=current_date.minute
                )
            event["date"] = event_date.isoformat()
            
        # Handle time changes separately
        if time:
            event_time = parse_time(time)
            if not event_time:
                await interaction.response.send_message(
                    "❌ Invalid time format. Use HH:MM (24-hour).", ephemeral=True
                )
                return
                
            # Update just the time component
            current_date = datetime.fromisoformat(event["date"])
            updated_date = current_date.replace(
                hour=event_time.hour,
                minute=event_time.minute
            )
            event["date"] = updated_date.isoformat()

        if organiser is not None:
            event["organiser"] = organiser.id

        if squad_maker is not None:
            event["squad_maker"] = squad_maker.id if squad_maker else None

        # Create new thread if requested
        if thread_channel:
            try:
                thread = await thread_channel.create_thread(
                    name=event["title"], type=discord.ChannelType.public_thread
                )
                await thread.send(event_to_str(event))
                event["thread_id"] = thread.id
            except Exception:
                pass

        # Update existing thread if it exists
        await self.update_thread_message(interaction.guild, event)
                
        save_events(events)

        await interaction.response.defer(ephemeral=True)
        await self.publish_calendar(interaction.guild)
        await interaction.followup.send("✅ Event updated and calendar refreshed.", ephemeral=True)

    @app_commands.guilds(TARGET_GUILD)
    @app_commands.command(name="removefromcalendar", description="Remove an event from the calendar.")
    @app_commands.autocomplete(title=autocomplete_event_titles)
    @app_commands.describe(title="Title of the event to remove")
    async def removefromcalendar(self, interaction: discord.Interaction, title: str):
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message(
                "❌ You don't have permission to manage events.", ephemeral=True
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
        await self.publish_calendar(interaction.guild)
        await interaction.followup.send("🗑️ Event removed and calendar updated.", ephemeral=True)

    # ---------- Startup ----------
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
                if guild:
                    await self.publish_calendar(guild)
            except Exception:
                pass


# ---------------- Extension setup ----------------
async def setup(bot: commands.Bot):
    await bot.add_cog(CalendarCog(bot))

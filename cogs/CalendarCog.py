import json
import pytz
import calendar
import discord
import os
from datetime import datetime, timedelta, time
from typing import Optional, List, Dict, Any
from discord import app_commands
from discord.ext import commands, tasks

# ---------------- Config ----------------
EVENTS_FILE = "events.json"
STATE_FILE = "calendar_state.json"  # stores message_id
TIMEZONE = pytz.timezone("Europe/London")  # Still needed for internal date calculations
CALENDAR_MANAGER_ROLES = ["Administration", "Admin Core", "7DR-SNCO", "Fight arrangeer", "Event Admin", "7DR-NCO"]

# Set your target guild and calendar channel here.
CALENDAR_GUILD_ID = 1097913605082579024
CALENDAR_CHANNEL_ID = 1332736267485708419  # The channel where the calendar will be posted

# When to create threads for recurring events (hours before the event)
THREAD_CREATION_HOURS_BEFORE = 48

# Auto (re)publish the calendar on startup. It will delete the previous embed and post a new one.
AUTO_PUBLISH_ON_START = True

TARGET_GUILD = discord.Object(id=CALENDAR_GUILD_ID)


# ---------------- Utility ----------------
def initialize_files():
    """Ensure the events and state files exist"""
    if not os.path.exists(EVENTS_FILE):
        print(f"Creating new {EVENTS_FILE} file")
        with open(EVENTS_FILE, "w") as f:
            json.dump([], f)

    if not os.path.exists(STATE_FILE):
        print(f"Creating new {STATE_FILE} file")
        with open(STATE_FILE, "w") as f:
            json.dump({}, f)

def has_calendar_permission(member: discord.Member) -> bool:
    return any(role.name in CALENDAR_MANAGER_ROLES for role in member.roles)


def load_events() -> list:
    initialize_files()
    try:
        with open(EVENTS_FILE, "r") as f:
            return json.load(f)
    except json.JSONDecodeError:
        print(f"Error decoding {EVENTS_FILE}, creating empty list")
        save_events([])
        return []


def save_events(events: list) -> None:
    with open(EVENTS_FILE, "w") as f:
        json.dump(events, f, indent=4)
    print(f"Saved {len(events)} events to {EVENTS_FILE}")


def load_state() -> Dict[str, Any]:
    initialize_files()
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except json.JSONDecodeError:
        print(f"Error decoding {STATE_FILE}, creating empty dict")
        save_state({})
        return {}


def save_state(state: Dict[str, Any]) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)
    print(f"Saved state to {STATE_FILE}")


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
    print(f"Updated message ID for guild {guild_id} to {message_id}")


def event_to_str(event: dict) -> str:
    organiser = f"<@{event['organiser']}>" if event.get("organiser") else "Unknown"
    squad_maker = f"<@{event['squad_maker']}>" if event.get("squad_maker") else "None"
    description = event.get("description", "")
    
    # Get thread link - for recurring events, check if there's a thread for this specific occurrence
    thread = None
    if event.get("recurring", False) and event.get("thread_info") and event.get("display_date"):
        # Get occurrence date in YYYY-MM-DD format for lookup
        display_dt = datetime.fromisoformat(event["display_date"])
        occurrence_date_str = display_dt.strftime('%Y-%m-%d')
        
        # If there's a thread for this occurrence, show it
        if occurrence_date_str in event["thread_info"]:
            thread_id = event["thread_info"][occurrence_date_str]
            thread = f"[Link](https://discord.com/channels/{event['guild_id']}/{thread_id})"
    elif event.get("thread_id"):  # For non-recurring events
        thread = f"[Link](https://discord.com/channels/{event['guild_id']}/{event['thread_id']})"
    
    if thread is None:
        thread = "None"
    
    # Start building the message
    msg = f"📌 **{event['title']}**"
    
    # Add recurring indicator if applicable
    if event.get("recurring", False):
        msg += " (2W & Recur)"
    
    msg += "\n"
    
    # Handle date display - show TBC if no date
    if event.get("date") is None:
        msg += "TBC"
    else:
        # Use display_date if available (for recurring events), otherwise use date
        date_field = event.get("display_date", event.get("date"))
        dt = datetime.fromisoformat(date_field)
        
        # Show date without time
        msg += f"Date: **{dt.day:02d}/{dt.month:02d}/{dt.year}**"
        
        # If the event has time, show it separately using the original values if available
        if event.get("has_time", False):
            # For events with original_hour/minute, use those exact values
            if "original_hour" in event and "original_minute" in event:
                hour = event["original_hour"]
                minute = event["original_minute"]
                msg += f", {hour:02d}:{minute:02d}"
            # Fallback to datetime's hour/minute for older events
            else:
                msg += f", {dt.hour:02d}:{dt.minute:02d}"
    
    msg += f"\nOrganiser: {organiser}"
    
    if event.get("squad_maker"):
        msg += f"\nSquad Maker: {squad_maker}"
    
    if description:
        msg += f"\nDescription: {description}"
    
    if thread != "None":
        msg += f"\nThread: {thread}"
    
    return msg


def build_calendar_embed(events: list) -> discord.Embed:
    now = datetime.now(TIMEZONE)
    two_weeks_later = now + timedelta(weeks=2)

    # Group upcoming events by (year, month) and sort
    month_groups: Dict[tuple[int, int], list] = {}
    tbc_events = []  # Special group for events without dates
    
    processed_events = []  # This will hold all events including recurring occurrences
    
    for e in events:
        # Handle events with no date separately
        if e.get("date") is None:
            tbc_events.append(e.copy())
            continue
            
        dt = datetime.fromisoformat(e["date"]).astimezone(TIMEZONE)
        
        # For recurring events, generate all occurrences within the 2-week window
        if e.get("recurring", False):
            # Store the original time components exactly
            original_time = dt.time()
            
            # Calculate first occurrence after now
            days_diff = (dt.weekday() - now.weekday()) % 7
            next_occurrence_date = now.date() + timedelta(days=days_diff)
            
            # Combine the date with the EXACT original time
            next_occurrence = datetime.combine(
                next_occurrence_date,
                original_time
            )
            
            # Attach timezone after combining to avoid DST issues
            next_occurrence = TIMEZONE.localize(next_occurrence)
            
            # If this places it in the past, add 7 days
            if next_occurrence < now:
                next_occurrence_date = next_occurrence_date + timedelta(days=7)
                next_occurrence = datetime.combine(
                    next_occurrence_date,
                    original_time
                )
                next_occurrence = TIMEZONE.localize(next_occurrence)
                
            # Add all occurrences within the 2-week window
            current_occurrence = next_occurrence
            while current_occurrence <= two_weeks_later:
                event_copy = e.copy()
                
                # Preserve original time components in each occurrence if available
                if "original_hour" in e and "original_minute" in e:
                    event_copy["original_hour"] = e["original_hour"]
                    event_copy["original_minute"] = e["original_minute"]
                
                event_copy["display_date"] = current_occurrence.isoformat()
                processed_events.append(event_copy)
                
                # Add 7 days for the next weekly occurrence - with exact time preservation
                next_date = (current_occurrence.date() + timedelta(days=7))
                current_occurrence = datetime.combine(
                    next_date,
                    original_time
                )
                current_occurrence = TIMEZONE.localize(current_occurrence)
        else:
            # Skip past events that aren't recurring
            if dt < now:
                continue
                
            # For regular events, just add them once
            event_copy = e.copy()
            event_copy["display_date"] = e["date"]
            processed_events.append(event_copy)

    # Group the processed events by month
    for e in processed_events:
        display_dt = datetime.fromisoformat(e["display_date"]).astimezone(TIMEZONE)
        key = (display_dt.year, display_dt.month)
        month_groups.setdefault(key, []).append(e)

    sorted_months = sorted(month_groups.keys())
    for key in sorted_months:
        # Sort by display date
        month_groups[key].sort(key=lambda ev: datetime.fromisoformat(ev["display_date"]))

    embed = discord.Embed(
        title="📅 **7DR Event Calendar**",
        description="Upcoming scheduled events within our clan!",
        colour=discord.Colour.blue(),
        timestamp=datetime.now(TIMEZONE),
    )

    if not sorted_months and not tbc_events:
        embed.description = "No events scheduled."
        return embed

    # Add events with dates first
    for i, (year, month) in enumerate(sorted_months):
        # Add a blank line separator before each month header (except the first one)
        if i > 0:
            embed.add_field(name="\u200b", value="\u200b", inline=False)
        
        # Get all events for this month
        events_in_month = month_groups[(year, month)]
        
        # Convert events to strings and check if we need to split them into multiple fields
        event_strings = [event_to_str(e) for e in events_in_month]
        
        # Discord has a limit of 1024 characters per field
        # Split the events into chunks that fit within this limit
        chunks = []
        current_chunk = []
        current_length = 0
        
        for event_str in event_strings:
            # Add 2 for the newline separators between events
            event_length = len(event_str) + 2
            
            # If adding this event would exceed the limit, start a new chunk
            if current_length + event_length > 1000:  # Using 1000 to leave some buffer
                if current_chunk:  # Only add non-empty chunks
                    chunks.append(current_chunk)
                current_chunk = [event_str]
                current_length = event_length
            else:
                current_chunk.append(event_str)
                current_length += event_length
        
        # Add the last chunk if it has content
        if current_chunk:
            chunks.append(current_chunk)
            
        # Create fields for each chunk
        for j, chunk in enumerate(chunks):
            if j == 0:
                # Only the first chunk gets a header
                month_name = f"🗓️ **{calendar.month_name[month]} {year}**"
                
                # Join events with double newlines and add the extra space at the top
                body = "\n\n".join(chunk)
                body = "\u200b\n" + body  # Add invisible character + newline for extra space
                
                embed.add_field(name=month_name, value=body, inline=False)
            else:
                # Continuation chunks have no header at all
                body = "\n\n".join(chunk)
                
                # Use a zero-width space as the name to make it completely invisible
                embed.add_field(name="\u200b", value=body, inline=False)
    
    # Add TBC events with the same approach
    if tbc_events:
        # Add a blank line before TBC section if there are other events
        if sorted_months:
            embed.add_field(name="\u200b", value="\u200b", inline=False)
        
        # Convert TBC events to strings
        tbc_strings = [event_to_str(e) for e in tbc_events]
        
        # Split into chunks with the same approach
        chunks = []
        current_chunk = []
        current_length = 0
        
        for event_str in tbc_strings:
            event_length = len(event_str) + 2
            
            if current_length + event_length > 1000:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = [event_str]
                current_length = event_length
            else:
                current_chunk.append(event_str)
                current_length += event_length
        
        if current_chunk:
            chunks.append(current_chunk)
        
        # Create fields for each TBC chunk
        for j, chunk in enumerate(chunks):
            if j == 0:
                # Only the first chunk gets a header
                header = f"🔧 **Date TBC**"
                
                body = "\n\n".join(chunk)
                body = "\u200b\n" + body
                
                embed.add_field(name=header, value=body, inline=False)
            else:
                # Continuation chunks have no header
                body = "\n\n".join(chunk)
                
                # Use a zero-width space as the name to make it completely invisible
                embed.add_field(name="\u200b", value=body, inline=False)

    return embed

def parse_date(date_str: str) -> Optional[datetime]:
    """Parse 'DD/MM/YYYY' into a date."""
    try:
        return datetime.strptime(date_str, "%d/%m/%Y").replace(tzinfo=TIMEZONE)
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


def is_date_in_past(dt: datetime) -> bool:
    """Check if a date is in the past."""
    now = datetime.now(TIMEZONE)
    return dt < now


def get_next_occurrence(event: dict, base_time: Optional[datetime] = None) -> Optional[datetime]:
    """Calculate the next occurrence of a recurring event after the specified time"""
    if not event.get("date"):
        return None
        
    if base_time is None:
        base_time = datetime.now(TIMEZONE)
        
    original_dt = datetime.fromisoformat(event["date"]).astimezone(TIMEZONE)
    
    # If it's not recurring, just return the original date
    if not event.get("recurring", False):
        return original_dt if original_dt > base_time else None
        
    # Store the original time components exactly
    original_time = original_dt.time()
    
    # Calculate the next occurrence based on weekday
    days_diff = (original_dt.weekday() - base_time.weekday()) % 7
    next_occurrence_date = base_time.date() + timedelta(days=days_diff)
    
    # Combine the calculated date with the EXACT original time
    next_occurrence = datetime.combine(
        next_occurrence_date,
        original_time
    )
    
    # Attach timezone after combining date and time to avoid DST issues
    next_occurrence = TIMEZONE.localize(next_occurrence)
    
    # If this places it in the past, add 7 days
    if next_occurrence < base_time:
        next_occurrence_date = next_occurrence_date + timedelta(days=7)
        next_occurrence = datetime.combine(
            next_occurrence_date,
            original_time
        )
        next_occurrence = TIMEZONE.localize(next_occurrence)
        
    return next_occurrence


# ---------------- Calendar Cog ----------------
class CalendarCog(commands.Cog):
    """Calendar management for the unit"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Initialize files on cog load
        initialize_files()
        print("Calendar cog initialized")
        # Start the tasks
        self.cleanup_expired_events.start()
        self.check_upcoming_events.start()

    def cog_unload(self):
        # Stop the tasks when the cog is unloaded
        self.cleanup_expired_events.cancel()
        self.check_upcoming_events.cancel()

    # ---------- Helpers ----------
    def get_calendar_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        channel = guild.get_channel(CALENDAR_CHANNEL_ID)
        if isinstance(channel, discord.TextChannel):
            me = guild.me
            if channel and channel.permissions_for(me).send_messages:
                print(f"Found calendar channel: {channel.name} (ID: {channel.id})")
                return channel
        print(f"❌ Could not find or access channel with ID {CALENDAR_CHANNEL_ID}")
        return None

    async def get_calendar_message(self, guild: discord.Guild) -> Optional[discord.Message]:
        """Get the existing calendar message if it exists"""
        channel = self.get_calendar_channel(guild)
        if not channel:
            return None
            
        message_id = get_message_id_for_guild(guild.id)
        if not message_id:
            return None
            
        try:
            return await channel.fetch_message(message_id)
        except Exception as e:
            print(f"Failed to fetch calendar message: {e}")
            return None

    async def update_calendar(self, guild: discord.Guild) -> Optional[discord.Message]:
        """
        Update the existing calendar message or create a new one if it doesn't exist.
        Only creates a new message if no message ID is found.
        """
        print(f"Updating calendar for guild {guild.id} ({guild.name})")
        
        channel = self.get_calendar_channel(guild)
        if not channel:
            print("❌ Calendar channel not found or not accessible")
            return None

        # Build the embed
        events = load_events()
        print(f"Loaded {len(events)} events from file")
        embed = build_calendar_embed(events)
        
        # Try to get the existing message
        message = await self.get_calendar_message(guild)
        
        if message:
            # Update existing message
            try:
                print(f"Updating existing calendar message {message.id}")
                await message.edit(embed=embed)
                print(f"✅ Successfully updated calendar message {message.id}")
                return message
            except Exception as e:
                print(f"❌ Failed to update calendar message: {e}")
                # If editing fails, we'll fall through to creating a new message
        
        # Create a new message if there wasn't one or editing failed
        try:
            print(f"Creating new calendar message in channel {channel.name}")
            new_msg = await channel.send(embed=embed)
            set_message_id_for_guild(guild.id, new_msg.id)
            print(f"✅ Successfully created new calendar message (ID: {new_msg.id})")
            return new_msg
        except Exception as e:
            print(f"❌ Failed to create new calendar message: {e}")
            return None

    async def create_thread_for_event(self, guild: discord.Guild, event: dict, 
                                     channel: discord.TextChannel) -> Optional[discord.Thread]:
        """Create a thread for an event in the specified channel"""
        if not channel:
            return None
            
        try:
            # Create a simple thread name without date
            thread_name = event['title']
            
            thread = await channel.create_thread(
                name=thread_name, 
                type=discord.ChannelType.public_thread
            )
            
            # Send the event details as the first message
            await thread.send(event_to_str(event))
            
            print(f"Created thread for event '{event['title']}' in channel {channel.name}")
            return thread
        except Exception as e:
            print(f"Failed to create thread: {e}")
            return None

    async def update_thread_message(self, guild: discord.Guild, event: dict) -> None:
        """Update the message and thread title for an event."""
        # For non-recurring events
        if event.get("thread_id"):
            try:
                thread = guild.get_thread(event["thread_id"])
                if thread:
                    # Update thread title if it doesn't match event title
                    if thread.name != event["title"]:
                        await thread.edit(name=event["title"])
                        print(f"Updated thread title for event '{event['title']}'")
                    
                    # Update the first message in the thread
                    async for message in thread.history(limit=1, oldest_first=True):
                        await message.edit(content=event_to_str(event))
                        print(f"Updated thread message for event '{event['title']}'")
                        break
            except Exception as e:
                print(f"Failed to update thread: {e}")
                
        # For recurring events with thread_info
        elif event.get("recurring") and event.get("thread_info"):
            try:
                # Update all threads for this recurring event
                for date_str, thread_id in event["thread_info"].items():
                    thread = guild.get_thread(thread_id)
                    if thread:
                        # Update thread title if it doesn't match event title
                        if thread.name != event["title"]:
                            await thread.edit(name=event["title"])
                            print(f"Updated thread title for recurring event '{event['title']}' on {date_str}")
                        
                        # Update the first message in the thread
                        async for message in thread.history(limit=1, oldest_first=True):
                            # Create a copy with the specific display_date for this occurrence
                            event_copy = event.copy()
                            occurrence_date = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=TIMEZONE)
                            event_copy["display_date"] = occurrence_date.isoformat()
                            
                            await message.edit(content=event_to_str(event_copy))
                            print(f"Updated thread message for recurring event '{event['title']}' on {date_str}")
                            break
            except Exception as e:
                print(f"Failed to update recurring event thread: {e}")

    async def archive_thread(self, guild: discord.Guild, event: dict) -> None:
        """Archive the thread associated with an event."""
        if not event.get("thread_id"):
            return
            
        try:
            thread = guild.get_thread(event["thread_id"])
            if thread:
                await thread.edit(archived=True, locked=True)
                print(f"Archived and locked thread for expired event '{event['title']}'")
        except Exception as e:
            print(f"Failed to archive thread: {e}")
            
    async def get_channel_for_thread(self, guild: discord.Guild, event: dict) -> Optional[discord.TextChannel]:
        """Find an appropriate channel for creating a thread for this event"""
        # First try to use the same channel as the calendar
        calendar_channel = self.get_calendar_channel(guild)
        if calendar_channel:
            return calendar_channel
            
        # If that fails, find any channel we can post in
        return find_sendable_channel(guild)

    # ---------- Periodic Tasks ----------
    @tasks.loop(hours=12)  # Run twice a day
    async def cleanup_expired_events(self):
        """Check for and remove expired events, archive their threads."""
        print("Running cleanup for expired events...")
        
        now = datetime.now(TIMEZONE)
        events = load_events()
        expired_events = []
        active_events = []
        events_updated = False
        
        for i, event in enumerate(events):
            # Skip events with no date (TBC)
            if event.get("date") is None:
                active_events.append(event)
                continue
                
            # Handle recurring events specially
            if event.get("recurring", False):
                # For recurring events, check if any past occurrences need their threads archived
                if event.get("thread_info"):
                    # Convert dates to datetime objects for comparison
                    occurrence_dates = {}
                    for date_str, thread_id in event["thread_info"].items():
                        occurrence_date = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=TIMEZONE)
                        occurrence_dates[date_str] = occurrence_date
                    
                    # Identify and archive threads for past occurrences
                    expired_occurrences = []
                    for date_str, occurrence_date in occurrence_dates.items():
                        if occurrence_date < now:
                            # Archive the thread for this past occurrence
                            thread_id = event["thread_info"][date_str]
                            guild = self.bot.get_guild(int(event["guild_id"]))
                            if guild:
                                try:
                                    thread = guild.get_thread(thread_id)
                                    if thread:
                                        await thread.edit(archived=True, locked=True)
                                        print(f"Archived thread for past occurrence of '{event['title']}' on {date_str}")
                                except Exception as e:
                                    print(f"Failed to archive thread: {e}")
                            
                            # Mark this occurrence for removal
                            expired_occurrences.append(date_str)
                    
                    # Remove expired occurrences from thread_info
                    for date_str in expired_occurrences:
                        del events[i]["thread_info"][date_str]
                        events_updated = True
                
                # Keep recurring events in the active list
                active_events.append(event)
                continue
                    
            # For non-recurring events, check if the event date is in the past
            event_date = datetime.fromisoformat(event["date"]).astimezone(TIMEZONE)
            if event_date < now:
                expired_events.append(event)
            else:
                active_events.append(event)
        
        # If there are expired events, archive threads and update the events list
        if expired_events:
            print(f"Found {len(expired_events)} expired events to clean up")
            
            for guild_id in {int(event["guild_id"]) for event in expired_events}:
                guild = self.bot.get_guild(guild_id)
                if guild:
                    # Archive threads for all expired events
                    for event in expired_events:
                        if int(event["guild_id"]) == guild_id:
                            await self.archive_thread(guild, event)
            
            # Save the active events only
            save_events(active_events)
            events_updated = True
        
        # Save events if thread_info was updated for recurring events
        if events_updated:
            save_events(active_events)
            
            # Update the calendar display for all guilds
            for guild_id in {int(event["guild_id"]) for event in events}:
                guild = self.bot.get_guild(guild_id)
                if guild:
                    await self.update_calendar(guild)
            
            print(f"Cleanup complete - removed {len(expired_events)} expired events and archived past occurrence threads")
        else:
            print("No expired events or past occurrence threads found")

    @tasks.loop(hours=1)  # Check every hour
    async def check_upcoming_events(self):
        """
        Check for events happening soon and create threads for recurring events
        that don't have threads yet.
        """
        print("Checking for upcoming events that need threads...")
        
        now = datetime.now(TIMEZONE)
        events = load_events()
        events_updated = False
        
        for i, event in enumerate(events):
            # Skip events with no date
            if not event.get("date"):
                continue
                
            # Skip events that have threads disabled (no thread channel was specified)
            if not event.get("create_threads", True):
                continue
                
            # Get the next occurrence (especially for recurring events)
            next_occurrence = get_next_occurrence(event)
            if not next_occurrence:
                continue
                
            # Check if this event is within the configured hours window
            time_until_event = next_occurrence - now
            hours_until_event = time_until_event.total_seconds() / 3600
            
            # Create threads for events that are coming up within the configured time window
            # The lower bound ensures we don't constantly recreate threads for imminent events
            thread_window_min = max(1, THREAD_CREATION_HOURS_BEFORE - 4)  # At least 1 hour before, or 4 hours less than config
            thread_window_max = THREAD_CREATION_HOURS_BEFORE
            
            if thread_window_min <= hours_until_event <= thread_window_max:
                # For recurring events, we need to handle differently than one-time events
                if event.get("recurring", False):
                    # For recurring events, check if this specific occurrence already has a thread
                    # by looking at the thread_info and the next occurrence date
                    occurrence_date_str = next_occurrence.strftime('%Y-%m-%d')
                    
                    # We'll use a thread_info dictionary to track occurrence-specific threads
                    if not event.get("thread_info"):
                        events[i]["thread_info"] = {}
                    
                    # Check if we already created a thread for this specific occurrence
                    if occurrence_date_str not in event["thread_info"]:
                        print(f"Creating thread for upcoming recurring event: {event['title']} on {occurrence_date_str}")
                        
                        guild = self.bot.get_guild(int(event["guild_id"]))
                        if guild:
                            channel = await self.get_channel_for_thread(guild, event)
                            if channel:
                                # Create display date for this occurrence
                                event_copy = event.copy()
                                event_copy["display_date"] = next_occurrence.isoformat()
                                
                                thread = await self.create_thread_for_event(guild, event_copy, channel)
                                if thread:
                                    # Store thread ID for this specific occurrence
                                    events[i]["thread_info"][occurrence_date_str] = thread.id
                                    events_updated = True
                                    
                                    # Send notification in the thread that it's for the upcoming occurrence
                                    occurrence_date = next_occurrence.strftime('%d/%m/%Y')
                                    await thread.send(f"📣 This thread is for the event occurrence on **{occurrence_date}**.")
        
        if events_updated:
            save_events(events)
            
            # Update calendars for all guilds
            for guild_id in {int(event["guild_id"]) for event in events}:
                guild = self.bot.get_guild(guild_id)
                if guild:
                    await self.update_calendar(guild)

    @cleanup_expired_events.before_loop
    @check_upcoming_events.before_loop
    async def before_tasks(self):
        """Wait for the bot to be ready before starting tasks."""
        await self.bot.wait_until_ready()
        print("Starting periodic tasks")

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
        date="Date in DD/MM/YYYY format (optional, use 'TBC' or leave empty if unknown)",
        time="Time in HH:MM format (24-hour, optional)",
        organiser="The event organiser",
        squad_maker="Squad maker (optional)",
        thread_channel="Channel to create a thread for this event (optional)",
        recurring="Whether this event repeats weekly (shows on 2-week rolling basis)"
    )
    async def addtocalendar(
        self,
        interaction: discord.Interaction,
        title: str,
        organiser: discord.Member,
        date: Optional[str] = None,
        description: Optional[str] = None,
        time: Optional[str] = None,
        squad_maker: Optional[discord.Member] = None,
        thread_channel: Optional[discord.TextChannel] = None,
        recurring: Optional[bool] = False
    ):
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message(
                "❌ You don't have permission to manage events.", ephemeral=True
            )
            return

        # Initialize event with date as None (TBC)
        event_date = None
        has_time_flag = False
        original_hour = None
        original_minute = None
            
        # Parse date if provided and not "TBC"
        if date and date.lower() != "tbc":
            event_date = parse_date(date)
            if not event_date:
                await interaction.response.send_message(
                    "❌ Invalid date format. Use DD/MM/YYYY or 'TBC'.", ephemeral=True
                )
                return
                
            # Check if date is in the past (only for non-recurring events)
            if not recurring and is_date_in_past(event_date):
                await interaction.response.send_message(
                    "❌ Cannot add events in the past. Please use a future date.", ephemeral=True
                )
                return

            # Parse time if provided and date exists
            if time:
                event_time = parse_time(time)
                if not event_time:
                    await interaction.response.send_message(
                        "❌ Invalid time format. Use HH:MM (24-hour).", ephemeral=True
                    )
                    return
                    
                # Store the exact input time components
                original_hour = event_time.hour
                original_minute = event_time.minute
                
                # Combine date and time
                event_date = event_date.replace(
                    hour=event_time.hour,
                    minute=event_time.minute
                )
                has_time_flag = True
                
                # Check again with time component if date is in the past (only for non-recurring events)
                if not recurring and is_date_in_past(event_date):
                    await interaction.response.send_message(
                        "❌ Cannot add events in the past. Please use a future date and time.", ephemeral=True
                    )
                    return
        
        # Cannot have a recurring event without a date
        if recurring and not event_date:
            await interaction.response.send_message(
                "❌ Recurring events must have a date specified.", ephemeral=True
            )
            return
        
        events = load_events()
        new_event = {
            "title": title,
            "description": description,
            "date": event_date.isoformat() if event_date else None,
            "has_time": has_time_flag,
            "organiser": organiser.id,
            "squad_maker": squad_maker.id if squad_maker else None,
            "guild_id": interaction.guild_id,
            "thread_id": None,
            "recurring": recurring,
            "create_threads": thread_channel is not None,  # Track whether threads should be created
        }
        
        # Add original time components if time was provided
        if has_time_flag and original_hour is not None and original_minute is not None:
            new_event["original_hour"] = original_hour
            new_event["original_minute"] = original_minute

        # Only create thread immediately for non-recurring events
        # Recurring events will have threads created before each occurrence
        # based on the THREAD_CREATION_HOURS_BEFORE setting
        if thread_channel and not recurring:
            thread = await self.create_thread_for_event(interaction.guild, new_event, thread_channel)
            if thread:
                new_event["thread_id"] = thread.id
            else:
                await interaction.response.send_message(
                    "⚠️ Event added but failed to create thread.", ephemeral=True
                )
                return
        # For recurring events with thread_channel, threads will be created automatically
        # before each occurrence, so we just continue with event creation

        events.append(new_event)
        save_events(events)

        # Update the calendar embed
        await interaction.response.defer(ephemeral=True)
        result = await self.update_calendar(interaction.guild)
        
        if result:
            if recurring and thread_channel:
                await interaction.followup.send(
                    f"✅ Recurring event added! Discussion threads will automatically "
                    f"open {THREAD_CREATION_HOURS_BEFORE} hours before each occurrence.", 
                    ephemeral=True
                )
            else:
                await interaction.followup.send("✅ Event added and calendar updated.", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ Event added but failed to update calendar display.", ephemeral=True)

    @app_commands.guilds(TARGET_GUILD)
    @app_commands.command(name="editcalendar", description="Edit an event on the calendar.")
    @app_commands.autocomplete(title=autocomplete_event_titles)
    @app_commands.describe(
        title="Title of the event to edit",
        new_title="New event title (optional)",
        description="New description (optional)",
        date="New date in DD/MM/YYYY format (use 'clear' or 'TBC' to set as TBC)",
        time="New time in HH:MM format (24-hour, optional)",
        organiser="New event organiser (optional)",
        squad_maker="New squad maker (optional)",
        thread_channel="Create a new thread in this channel (optional)",
        recurring="Whether this event repeats weekly (optional)"
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
        recurring: Optional[bool] = None
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

        # Store the original event date and recurring status
        current_date = datetime.fromisoformat(event["date"]) if event.get("date") else None
        was_recurring = event.get("recurring", False)
        is_recurring = was_recurring if recurring is None else recurring
        
        # Update title if provided
        if new_title:
            event["title"] = new_title

        # Update description if provided
        if description is not None:  # Allow empty string to clear description
            event["description"] = description
            
        # Handle date changes
        if date:
            # Check if user wants to clear the date
            if date.lower() in ['clear', 'tbc']:
                # Cannot clear date for recurring events
                if is_recurring:
                    await interaction.response.send_message(
                        "❌ Recurring events must have a date specified.", ephemeral=True
                    )
                    return
                    
                event["date"] = None
                event["has_time"] = False
                # Remove time fields if they exist
                event.pop("original_hour", None)
                event.pop("original_minute", None)
            else:
                event_date = parse_date(date)
                if not event_date:
                    await interaction.response.send_message(
                        "❌ Invalid date format. Use DD/MM/YYYY or 'clear'/'TBC' to set as TBC.", ephemeral=True
                    )
                    return
                
                # Check if new date is in the past (only for non-recurring events)
                if not is_recurring and is_date_in_past(event_date):
                    await interaction.response.send_message(
                        "❌ Cannot set event date to the past. Please use a future date.", ephemeral=True
                    )
                    return
                    
                # If only date is provided and there was a previous time, keep it
                if not time and current_date and event["date"] and event.get("has_time", False):
                    event_date = event_date.replace(
                        hour=current_date.hour,
                        minute=current_date.minute
                    )
                    # Keep has_time flag and original time components
                else:
                    # Reset has_time flag if no time is specified
                    event["has_time"] = False
                    # Remove time fields if they exist
                    event.pop("original_hour", None)
                    event.pop("original_minute", None)
                    
                event["date"] = event_date.isoformat()
            
        # Handle time changes separately (only if there's a date)
        if time and event["date"]:
            event_time = parse_time(time)
            if not event_time:
                await interaction.response.send_message(
                    "❌ Invalid time format. Use HH:MM (24-hour).", ephemeral=True
                )
                return
                
            # Store the exact input time components
            event["original_hour"] = event_time.hour
            event["original_minute"] = event_time.minute
                
            # Update just the time component
            current_date = datetime.fromisoformat(event["date"])
            updated_date = current_date.replace(
                hour=event_time.hour,
                minute=event_time.minute
            )
            
            # Check if the new datetime is in the past (only for non-recurring events)
            if not is_recurring and is_date_in_past(updated_date):
                await interaction.response.send_message(
                    "❌ Cannot set event time to the past. Please use a future time.", ephemeral=True
                )
                return
                
            event["date"] = updated_date.isoformat()
            event["has_time"] = True
            
        # Update recurring status if provided
        if recurring is not None:
            # Cannot make an event recurring if it has no date
            if recurring and not event.get("date"):
                await interaction.response.send_message(
                    "❌ Cannot make an event recurring without a date.", ephemeral=True
                )
                return
                
            # If changing from non-recurring to recurring, clear the thread_id
            # so that threads will be created before occurrences instead
            if recurring and not was_recurring and event.get("thread_id"):
                event["thread_id"] = None
                
            event["recurring"] = recurring
            
        # Update organiser if provided
        if organiser is not None:
            event["organiser"] = organiser.id
            
        # Update squad maker if provided
        if squad_maker is not None:
            event["squad_maker"] = squad_maker.id
            
        # Update thread channel if provided
        if thread_channel is not None:
            # Update create_threads flag based on whether a channel was specified
            event["create_threads"] = thread_channel is not None
            
            # Create new thread if requested and not a recurring event
            if thread_channel and not is_recurring:
                thread = await self.create_thread_for_event(interaction.guild, event, thread_channel)
                if thread:
                    event["thread_id"] = thread.id

        # Update existing thread titles and content
        if new_title or description is not None or date or time or organiser is not None or squad_maker is not None:
            await self.update_thread_message(interaction.guild, event)
                
        save_events(events)

        await interaction.response.defer(ephemeral=True)
        result = await self.update_calendar(interaction.guild)
        
        # Prepare the response message
        if result:
            if not was_recurring and is_recurring:
                if event.get("create_threads", False):
                    await interaction.followup.send(
                        f"✅ Event updated to recurring! Discussion threads will automatically "
                        f"open {THREAD_CREATION_HOURS_BEFORE} hours before each occurrence.",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        "✅ Event updated to recurring! No threads will be created for this event.",
                        ephemeral=True
                    )
            else:
                await interaction.followup.send("✅ Event updated and calendar refreshed.", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ Event updated but failed to refresh calendar display.", ephemeral=True)

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

        # Archive threads if they exist
        if event.get("recurring") and event.get("thread_info"):
            for date_str, thread_id in event["thread_info"].items():
                try:
                    thread = interaction.guild.get_thread(thread_id)
                    if thread:
                        await thread.edit(archived=True, locked=True)
                        print(f"Archived and locked thread for recurring event '{title}' on {date_str}")
                except Exception as e:
                    print(f"Failed to archive thread: {e}")
        elif event.get("thread_id"):
            try:
                thread = interaction.guild.get_thread(event["thread_id"])
                if thread:
                    await thread.edit(archived=True, locked=True)
                    print(f"Archived and locked thread for event '{title}'")
            except Exception as e:
                print(f"Failed to archive thread: {e}")

        events = [e for e in events if e["title"] != title]
        save_events(events)

        await interaction.response.defer(ephemeral=True)
        result = await self.update_calendar(interaction.guild)
        if result:
            await interaction.followup.send("🗑️ Event removed and calendar updated.", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ Event removed but failed to update calendar display.", ephemeral=True)

    # ---------- Startup ----------
    @commands.Cog.listener()
    async def on_ready(self):
        print(f"Bot is ready! Logged in as {self.bot.user}")
        
        # Run initial cleanup of expired events
        await self.cleanup_expired_events()
        
        # Ensure commands are synced to the target guild only
        try:
            synced = await self.bot.tree.sync(guild=discord.Object(id=CALENDAR_GUILD_ID))
            print(f"Synced {len(synced)} commands to guild {CALENDAR_GUILD_ID}")
        except Exception as e:
            print(f"Failed to sync commands: {e}")

        # Always try to publish the calendar on startup
        if AUTO_PUBLISH_ON_START:
            try:
                guild = self.bot.get_guild(CALENDAR_GUILD_ID)
                if guild:
                    print(f"Found guild: {guild.name} (ID: {guild.id})")
                    # On startup, we'll create a new message regardless of existing ones
                    channel = self.get_calendar_channel(guild)
                    if channel:
                        # Delete previous message if exists
                        prev_id = get_message_id_for_guild(guild.id)
                        if prev_id:
                            try:
                                msg = await channel.fetch_message(prev_id)
                                await msg.delete()
                                print(f"Deleted previous calendar message {prev_id}")
                            except Exception as e:
                                print(f"Could not delete previous message: {e}")
                        
                        # Create a new message
                        events = load_events()
                        embed = build_calendar_embed(events)
                        try:
                            new_msg = await channel.send(embed=embed)
                            set_message_id_for_guild(guild.id, new_msg.id)
                            print(f"Created new calendar message on startup (ID: {new_msg.id})")
                        except Exception as e:
                            print(f"Failed to create calendar message on startup: {e}")
                    else:
                        print("Could not find calendar channel")
                else:
                    print(f"❌ Could not find guild with ID {CALENDAR_GUILD_ID}")
            except Exception as e:
                print(f"❌ Error publishing calendar on startup: {e}")


# ---------------- Extension setup ----------------
async def setup(bot: commands.Bot):
    await bot.add_cog(CalendarCog(bot))
    print("Calendar cog loaded")

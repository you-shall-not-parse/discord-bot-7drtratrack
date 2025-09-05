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
TIMEZONE = pytz.timezone("Europe/London")
CALENDAR_MANAGER_ROLES = ["Administration", "7DR-SNCO", "Fight Arrangeer"]

# Set your target guild and calendar channel here.
CALENDAR_GUILD_ID = 1097913605082579024
CALENDAR_CHANNEL_ID = 1332736267485708419  # The channel where the calendar will be posted

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
    
    thread = (
        f"[Link](https://discord.com/channels/{event['guild_id']}/{event['thread_id']})"
        if event.get("thread_id")
        else "None"
    )
    
    # Start building the message
    msg = f"📌 **{event['title']}**"
    
    # Add recurring indicator if applicable
    if event.get("recurring", False):
        msg += " _(2 week rolling/recurring)_"
    
    msg += "\n"
    
    # Handle date display - show TBC if no date
    if event.get("date") is None:
        msg += "🗓️ TBC"
    else:
        # Use display_date if available (for recurring events), otherwise use date
        date_field = event.get("display_date", event.get("date"))
        dt = datetime.fromisoformat(date_field).astimezone(TIMEZONE)
        msg += f"🗓️ {dt.strftime('%d/%m/%Y')}"
        
        # Only show time if it was explicitly set (has_time flag is True)
        if event.get("has_time", False):
            msg += f", {dt.strftime('%H:%M')} UK time"
    
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
            # Calculate first occurrence after now
            days_diff = (dt.weekday() - now.weekday()) % 7
            next_occurrence = now + timedelta(days=days_diff)
            next_occurrence = next_occurrence.replace(
                hour=dt.hour, 
                minute=dt.minute,
                second=dt.second
            )
            
            # If this places it in the past, add 7 days
            if next_occurrence < now:
                next_occurrence += timedelta(days=7)
                
            # Add all occurrences within the 2-week window
            current_occurrence = next_occurrence
            while current_occurrence <= two_weeks_later:
                event_copy = e.copy()
                event_copy["display_date"] = current_occurrence.isoformat()
                processed_events.append(event_copy)
                
                # Add 7 days for the next weekly occurrence
                current_occurrence += timedelta(days=7)
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
        title="📅 Unit Calendar",
        description="Upcoming scheduled events",
        colour=discord.Colour.blue(),
        timestamp=datetime.now(TIMEZONE),
    )

    if not sorted_months and not tbc_events:
        embed.description = "No events scheduled."
        return embed

    # Add events with dates first
    for i, (year, month) in enumerate(sorted_months):
        # Fancy decorated month header
        month_name = f"🗓️ **{calendar.month_name[month].upper()} {year}** 🗓️"
        
        body = "\n\n".join(event_to_str(e) for e in month_groups[(year, month)])
        embed.add_field(name=month_name, value=body, inline=False)
        
        # Add separator between months (not after the last month)
        if i < len(sorted_months) - 1:
            embed.add_field(name="\u200b", value="┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄", inline=False)
    
    # Add a separator before TBC section if there are both dated and TBC events
    if sorted_months and tbc_events:
        embed.add_field(name="\u200b", value="┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄", inline=False)
    
    # Add TBC events last (at the bottom) if there are any
    if tbc_events:
        body = "\n\n".join(event_to_str(e) for e in tbc_events)
        embed.add_field(name="⭐ **DATE TBC** ⭐", value=body, inline=False)

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


# ---------------- Calendar Cog ----------------
class CalendarCog(commands.Cog):
    """Calendar management for the unit"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Initialize files on cog load
        initialize_files()
        print("Calendar cog initialized")
        # Start the cleanup task
        self.cleanup_expired_events.start()

    def cog_unload(self):
        # Stop the task when the cog is unloaded
        self.cleanup_expired_events.cancel()

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
                    print(f"Updated thread message for event '{event['title']}'")
                    break
        except Exception as e:
            print(f"Failed to update thread message: {e}")

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

    # ---------- Periodic Tasks ----------
    @tasks.loop(hours=12)  # Run twice a day
    async def cleanup_expired_events(self):
        """Check for and remove expired events, archive their threads."""
        print("Running cleanup for expired events...")
        
        now = datetime.now(TIMEZONE)
        events = load_events()
        expired_events = []
        active_events = []
        
        for event in events:
            # Skip events with no date (TBC) or recurring events
            if event.get("date") is None or event.get("recurring", False):
                active_events.append(event)
                continue
                
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
            
            # Update the calendar display for all guilds
            for guild_id in {int(event["guild_id"]) for event in events}:
                guild = self.bot.get_guild(guild_id)
                if guild:
                    await self.update_calendar(guild)
            
            print(f"Cleanup complete - removed {len(expired_events)} expired events")
        else:
            print("No expired events found")

    @cleanup_expired_events.before_loop
    async def before_cleanup(self):
        """Wait for the bot to be ready before starting the cleanup task."""
        await self.bot.wait_until_ready()
        print("Starting expired events cleanup task")

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
        }

        # Create thread if requested
        if thread_channel:
            try:
                thread = await thread_channel.create_thread(
                    name=title, type=discord.ChannelType.public_thread
                )
                await thread.send(event_to_str(new_event))
                new_event["thread_id"] = thread.id
                print(f"Created thread for event '{title}' in channel {thread_channel.name}")
            except Exception as e:
                print(f"Failed to create thread: {e}")
                await interaction.response.send_message(
                    "⚠️ Event added but failed to create thread.", ephemeral=True
                )
                return

        events.append(new_event)
        save_events(events)

        # Update the calendar embed
        await interaction.response.defer(ephemeral=True)
        result = await self.update_calendar(interaction.guild)
        if result:
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
        is_recurring = event.get("recurring", False) if recurring is None else recurring
        
        if new_title:
            event["title"] = new_title

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
                    # Keep has_time flag
                else:
                    # Reset has_time flag if no time is specified
                    event["has_time"] = False
                    
                event["date"] = event_date.isoformat()
            
        # Handle time changes separately (only if there's a date)
        if time and event["date"]:
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
                
            event["recurring"] = recurring

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
                print(f"Created new thread for edited event '{event['title']}' in channel {thread_channel.name}")
            except Exception as e:
                print(f"Failed to create thread for edited event: {e}")

        # Update existing thread if it exists
        await self.update_thread_message(interaction.guild, event)
                
        save_events(events)

        await interaction.response.defer(ephemeral=True)
        result = await self.update_calendar(interaction.guild)
        if result:
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

        # Archive thread if exists
        if event.get("thread_id"):
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

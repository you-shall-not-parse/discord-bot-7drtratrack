import logging
import re
import json
import os
import asyncio
from typing import Optional
from datetime import datetime
import discord
from discord.ext import commands, tasks

from data_paths import data_path

logger = logging.getLogger(__name__)

# =============================
# CONFIG (EDIT THIS)
# =============================
# Channel ID where events will be posted
EVENT_DISPLAY_CHANNEL_ID = 1332736267485708419  # Replace with your channel ID

# How often to update the events display (in minutes)
UPDATE_INTERVAL_MINUTES = 30

# Maximum number of events to display - 25 is the max allowed by Discord per embed
MAX_EVENTS_TO_DISPLAY = 25

# Color for the embed
EMBED_COLOR = 0x5865F2  # Discord blurple

# Path to save events JSON
EVENTS_JSON_PATH = data_path("events_history.json")

# Path to persist the display message across restarts
EVENTS_DISPLAY_STATE_PATH = data_path("events_display_state.json")

# -----------------------------
# EVENT THREADS (AUTO)
# -----------------------------
# When a new scheduled event is created, the bot will create a thread in this channel.
# Default: use the same channel as the calendar embed.
EVENT_THREADS_PARENT_CHANNEL_ID = 1192922522673500190

# Auto-archive duration for the created threads (minutes).
# Valid values depend on the server settings: 60, 1440, 4320, 10080.
EVENT_THREAD_AUTO_ARCHIVE_MINUTES = 10080

# Persist which events we've already handled so we don't create duplicate threads.
EVENTS_THREAD_STATE_PATH = data_path("events_threads_state.json")

# -----------------------------
# EVENT TITLE EMOJI TAGGING
# -----------------------------
# If an event name contains one of these keywords, the bot will append the
# corresponding custom server emoji *after* that keyword in the displayed title.
#
# Put the emoji name in Discord's short-name format (e.g. ":48th:") and make sure
# the custom emoji exists in the same server as the event.
KEYWORD_EMOJI_TAGS: dict[str, str] = {
    "RDG": ":RDG:",
    "RMC": ":RMC:",
    "48th": ":48th:",
    "HellEU": ":helleu:",
    "7DR": ":7DR:",
    "KRTS": ":KRTS:",
}


class EventDisplayCog(commands.Cog):
    """
    A cog that reads Discord scheduled events and displays them in an embed.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.display_message_id: Optional[int] = self._load_display_message_id()
        self._target_guild_id: Optional[int] = None
        self._update_lock = asyncio.Lock()
        self._debounce_task: Optional[asyncio.Task] = None
        self._thread_state = self._load_thread_state()
        self.update_events_display.start()
        logger.info("EventDisplayCog initialized")

    def cog_unload(self):
        """Stop the background task when the cog is unloaded."""
        self.update_events_display.cancel()
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
        logger.info("EventDisplayCog unloaded")

    @tasks.loop(minutes=UPDATE_INTERVAL_MINUTES)
    async def update_events_display(self):
        """Periodic refresh."""
        await self._update_once(reason="interval")

    @update_events_display.before_loop
    async def before_update_events_display(self):
        """Wait until the bot is ready before starting the loop."""
        await self.bot.wait_until_ready()
        logger.info("EventDisplayCog: Bot is ready, starting event display loop")

        # On startup, establish the target guild and optionally create threads for any
        # events that appeared while the bot was offline.
        await self._startup_sync_threads()

    def _load_thread_state(self) -> dict:
        try:
            if not os.path.exists(EVENTS_THREAD_STATE_PATH):
                return {"initialized": False, "seen_event_ids": [], "threads": {}}
            with open(EVENTS_THREAD_STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
            if not isinstance(state, dict):
                return {"initialized": False, "seen_event_ids": [], "threads": {}}
            state.setdefault("initialized", False)
            state.setdefault("seen_event_ids", [])
            state.setdefault("threads", {})
            return state
        except Exception:
            logger.warning("Could not read events thread state; will recreate it.", exc_info=True)
            return {"initialized": False, "seen_event_ids": [], "threads": {}}

    def _save_thread_state(self) -> None:
        try:
            self._thread_state["updated_at"] = datetime.utcnow().isoformat()
            with open(EVENTS_THREAD_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(self._thread_state, f, indent=2, ensure_ascii=False)
        except Exception:
            logger.warning("Failed to persist events thread state.", exc_info=True)

    def _is_event_seen(self, event_id: int) -> bool:
        return str(event_id) in set(map(str, self._thread_state.get("seen_event_ids", [])))

    def _mark_event_seen(self, event_id: int) -> None:
        seen = set(map(str, self._thread_state.get("seen_event_ids", [])))
        seen.add(str(event_id))
        self._thread_state["seen_event_ids"] = sorted(seen)

    async def _startup_sync_threads(self) -> None:
        """Initialize thread state and handle events created while offline."""

        try:
            channel = self.bot.get_channel(EVENT_DISPLAY_CHANNEL_ID)
            if not isinstance(channel, discord.TextChannel):
                return
            guild = channel.guild
            if not guild:
                return

            self._target_guild_id = guild.id

            current_events = await guild.fetch_scheduled_events(with_counts=False)

            # First ever run: mark all existing events as seen so we don't spam threads.
            if not self._thread_state.get("initialized", False):
                for ev in current_events:
                    self._mark_event_seen(ev.id)
                self._thread_state["initialized"] = True
                self._save_thread_state()
                logger.info("Initialized events thread state (existing events marked as seen)")
                return

            # Subsequent runs: create threads for any events we haven't seen yet.
            for ev in current_events:
                if not self._is_event_seen(ev.id):
                    await self._create_event_thread(ev)
                    self._mark_event_seen(ev.id)
            self._save_thread_state()

        except Exception:
            logger.warning("Startup thread sync failed.", exc_info=True)

    async def _create_event_thread(self, scheduled_event: discord.ScheduledEvent) -> None:
        parent = self.bot.get_channel(EVENT_THREADS_PARENT_CHANNEL_ID)
        if not isinstance(parent, discord.TextChannel):
            logger.warning("Thread parent channel is missing or not a text channel")
            return

        # Build a starter message; threads are created from messages reliably.
        start_time_str = (
            f"<t:{int(scheduled_event.start_time.timestamp())}:F>"
            if scheduled_event.start_time
            else "TBA"
        )

        organiser = "Unknown"
        if getattr(scheduled_event, "creator", None):
            organiser = scheduled_event.creator.mention
        elif getattr(scheduled_event, "creator_id", None):
            organiser = f"<@{scheduled_event.creator_id}>"

        title = self._format_event_title(parent.guild, scheduled_event.name)

        # No URLs in the starter text to avoid link embeds.
        starter_text = (
            f"📅 New event created: **{title}**\n"
            f"**Date/Time:** {start_time_str}\n"
            f"**Organiser:** {organiser}"
        )

        try:
            starter_msg = await parent.send(starter_text)

            date_suffix = "TBA"
            if scheduled_event.start_time:
                date_suffix = scheduled_event.start_time.strftime("%d/%m/%Y")

            thread_name = f"{scheduled_event.name} - {date_suffix}".strip()
            if len(thread_name) > 100:
                thread_name = thread_name[:97] + "..."

            thread = await starter_msg.create_thread(
                name=thread_name,
                auto_archive_duration=EVENT_THREAD_AUTO_ARCHIVE_MINUTES,
            )

            self._thread_state.setdefault("threads", {})[str(scheduled_event.id)] = {
                "thread_id": thread.id,
                "starter_message_id": starter_msg.id,
                "created_at": datetime.utcnow().isoformat(),
            }
            logger.info(f"Created thread {thread.id} for event {scheduled_event.id}")

        except discord.Forbidden:
            logger.warning("Missing permissions to create event thread (send message / create thread)")
        except Exception:
            logger.warning("Failed to create event thread.", exc_info=True)

    def _load_display_message_id(self) -> Optional[int]:
        try:
            if not os.path.exists(EVENTS_DISPLAY_STATE_PATH):
                return None
            with open(EVENTS_DISPLAY_STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
            channel_id = state.get("channel_id")
            message_id = state.get("message_id")
            if channel_id != EVENT_DISPLAY_CHANNEL_ID:
                return None
            if isinstance(message_id, int):
                return message_id
            return None
        except Exception:
            logger.warning("Could not read events display state; will create a new message.", exc_info=True)
            return None

    def _save_display_message_id(self) -> None:
        try:
            state = {
                "channel_id": EVENT_DISPLAY_CHANNEL_ID,
                "message_id": self.display_message_id,
                "updated_at": datetime.utcnow().isoformat(),
            }
            with open(EVENTS_DISPLAY_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except Exception:
            logger.warning("Failed to persist events display state.", exc_info=True)

    def _resolve_custom_emoji(self, guild: discord.Guild, emoji_tag: str) -> str:
        """Resolve a tag like ':name:' to '<:name:id>' if possible."""

        emoji_name = emoji_tag.strip(":")
        if not emoji_name:
            return emoji_tag

        for emoji in getattr(guild, "emojis", []):
            if emoji.name == emoji_name:
                return str(emoji)

        # Not found; return the original tag (will display as text)
        return emoji_tag

    def _format_event_title(self, guild: discord.Guild, title: str) -> str:
        """Append configured emojis after matching keywords in the title."""

        if not title or not KEYWORD_EMOJI_TAGS:
            return title

        formatted = title

        # Longer keys first to avoid partial matches.
        for keyword in sorted(KEYWORD_EMOJI_TAGS.keys(), key=len, reverse=True):
            emoji_tag = KEYWORD_EMOJI_TAGS.get(keyword)
            if not emoji_tag:
                continue

            emoji_str = self._resolve_custom_emoji(guild, emoji_tag)

            # Match keyword as a standalone token (not inside another word).
            pattern = re.compile(rf"(?<!\\w){re.escape(keyword)}(?!\\w)")

            def _repl(match: re.Match) -> str:
                return f"{match.group(0)} {emoji_str}"  # append with a space before emoji

            formatted = pattern.sub(_repl, formatted)

        return formatted

    async def _update_once(self, *, reason: str) -> None:
        async with self._update_lock:
            try:
                channel = self.bot.get_channel(EVENT_DISPLAY_CHANNEL_ID)
                if not channel:
                    logger.error(f"Channel with ID {EVENT_DISPLAY_CHANNEL_ID} not found")
                    return

                if not isinstance(channel, discord.TextChannel):
                    logger.error(f"Channel {EVENT_DISPLAY_CHANNEL_ID} is not a text channel")
                    return

                guild = channel.guild
                if not guild:
                    logger.error("Guild not found for the specified channel")
                    return

                self._target_guild_id = guild.id

                # Fetch scheduled events
                events = await guild.fetch_scheduled_events(with_counts=True)

                # Filter for only scheduled (future) or active (live) events
                filtered_events = [
                    e for e in events
                    if e.status in (discord.EventStatus.scheduled, discord.EventStatus.active)
                ]

                display_limit = min(MAX_EVENTS_TO_DISPLAY, 25)
                sorted_events = sorted(
                    filtered_events,
                    key=lambda e: e.start_time if e.start_time else datetime.max
                )[:display_limit]

                embed = await self.create_events_embed(guild, sorted_events)

                # Save all events (not just filtered ones) to JSON
                await self.save_events_to_json(events)

                # Edit existing display message if possible (persists across restarts)
                message: Optional[discord.Message] = None
                if self.display_message_id:
                    try:
                        message = await channel.fetch_message(self.display_message_id)
                    except discord.NotFound:
                        message = None
                    except discord.Forbidden:
                        logger.warning("No permission to fetch the existing events message; will create a new one.")
                        message = None
                    except Exception:
                        logger.warning("Failed to fetch the existing events message; will create a new one.", exc_info=True)
                        message = None

                if message is not None:
                    try:
                        await message.edit(embed=embed)
                        logger.info(f"Refreshed events display ({reason}) with {len(sorted_events)} events")
                        return
                    except discord.Forbidden:
                        logger.warning("No permission to edit the existing events message; will create a new one.")
                    except Exception:
                        logger.warning("Failed to edit the existing events message; will create a new one.", exc_info=True)

                # Fallback: send a new message and persist its id
                new_message = await channel.send(embed=embed)
                self.display_message_id = new_message.id
                self._save_display_message_id()
                logger.info(f"Posted new events display ({reason}) with {len(sorted_events)} events")

            except Exception as e:
                logger.error(f"Error updating events display: {e}", exc_info=True)

    def _debounced_refresh(self, *, delay_seconds: float = 3.0) -> None:
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = asyncio.create_task(self._debounce_worker(delay_seconds))

    async def _debounce_worker(self, delay_seconds: float) -> None:
        try:
            await asyncio.sleep(delay_seconds)
            await self._update_once(reason="event_change")
        except asyncio.CancelledError:
            return

    @commands.Cog.listener()
    async def on_scheduled_event_create(self, scheduled_event: discord.ScheduledEvent):
        if self._target_guild_id and scheduled_event.guild_id != self._target_guild_id:
            return

        # If we haven't initialized yet (race at startup), sync once.
        if not self._thread_state.get("initialized", False):
            await self._startup_sync_threads()

        # Only create a thread once per event.
        if not self._is_event_seen(scheduled_event.id):
            await self._create_event_thread(scheduled_event)
            self._mark_event_seen(scheduled_event.id)
            self._save_thread_state()

        self._debounced_refresh()

    @commands.Cog.listener()
    async def on_scheduled_event_delete(self, scheduled_event: discord.ScheduledEvent):
        if self._target_guild_id and scheduled_event.guild_id != self._target_guild_id:
            return
        self._debounced_refresh()

    @commands.Cog.listener()
    async def on_scheduled_event_update(self, before: discord.ScheduledEvent, after: discord.ScheduledEvent):
        guild_id = after.guild_id if after else before.guild_id
        if self._target_guild_id and guild_id != self._target_guild_id:
            return
        self._debounced_refresh()

    async def save_events_to_json(self, events: list[discord.ScheduledEvent]):
        """
        Save all events to a JSON file for historical tracking.
        
        Args:
            events: List of all scheduled events
        """
        try:
            # Load existing data if file exists
            existing_data = {}
            if os.path.exists(EVENTS_JSON_PATH):
                try:
                    with open(EVENTS_JSON_PATH, 'r', encoding='utf-8') as f:
                        existing_data = json.load(f)
                except json.JSONDecodeError:
                    logger.warning("Could not read existing events JSON, creating new file")
                    existing_data = {}
            
            # Update with current events
            for event in events:
                event_data = {
                    "id": event.id,
                    "name": event.name,
                    "description": event.description,
                    "start_time": event.start_time.isoformat() if event.start_time else None,
                    "end_time": event.end_time.isoformat() if event.end_time else None,
                    "status": event.status.name,
                    "location": event.location,
                    "channel_id": event.channel.id if event.channel else None,
                    "user_count": event.user_count,
                    "creator_id": event.creator_id,
                    "url": str(event.url),
                    "last_updated": datetime.utcnow().isoformat()
                }
                existing_data[str(event.id)] = event_data
            
            # Save to file
            with open(EVENTS_JSON_PATH, 'w', encoding='utf-8') as f:
                json.dump(existing_data, f, indent=2, ensure_ascii=False)
            
            logger.debug(f"Saved {len(events)} events to JSON")
            
        except Exception as e:
            logger.error(f"Error saving events to JSON: {e}", exc_info=True)

    async def create_events_embed(
        self,
        guild: discord.Guild,
        events: list[discord.ScheduledEvent]
    ) -> discord.Embed:
        """
        Create an embed displaying the scheduled events.
        
        Args:
            guild: The Discord guild
            events: List of scheduled events
            
        Returns:
            A Discord embed with event information
        """
        embed = discord.Embed(
            title=f"📅 Upcoming Events for {guild.name}",
            color=EMBED_COLOR,
            timestamp=datetime.utcnow()
        )

        if not events:
            embed.description = "No upcoming events scheduled."
        else:
            for event in events:
                thread_url: Optional[str] = None
                thread_info = self._thread_state.get("threads", {}).get(str(event.id))
                if isinstance(thread_info, dict):
                    thread_id = thread_info.get("thread_id")
                    if isinstance(thread_id, int):
                        thread_url = f"https://discord.com/channels/{guild.id}/{thread_id}"

                # Format the event time
                start_time_str = (
                    f"<t:{int(event.start_time.timestamp())}:F>"
                    if event.start_time
                    else "TBA"
                )

                organiser_str = "Unknown"
                if getattr(event, "creator", None):
                    organiser_str = event.creator.mention
                elif getattr(event, "creator_id", None):
                    organiser_str = f"<@{event.creator_id}>"

                # Location information
                location_str = ""
                if event.location:
                    location_str = f"\n**Location:** {event.location}"
                elif event.channel:
                    location_str = f"\n**Channel:** {event.channel.mention}"

                # Build the field value
                field_value = (
                    f"**Date/Time:** {start_time_str}"
                    f"\n**Organiser:** {organiser_str}"
                    f"{location_str}"
                )

                if event.description:
                    # Check for channel mentions and URLs in description
                    # Pattern 1: <#1234567890>
                    channel_mentions = re.findall(r'<#(\d+)>', event.description)
                    # Pattern 2: https://discord.com/channels/GUILD_ID/CHANNEL_ID
                    channel_urls = re.findall(r'https?://(?:discord|discordapp)\.com/channels/\d+/(\d+)', event.description)
                    
                    # Combine all found channel IDs
                    all_channel_ids = channel_mentions + channel_urls
                    
                    if all_channel_ids:
                        # Use the first channel ID as sign-up channel
                        channel_id = int(all_channel_ids[0])
                        field_value += f"\n📝 **Sign-Up Channel:** <#{channel_id}>"
                        
                        # Show rest of description (excluding channel mentions and URLs)
                        description = re.sub(r'<#\d+>', '', event.description)
                        description = re.sub(r'https?://(?:discord|discordapp)\.com/channels/\d+/\d+', '', description).strip()
                        if description:
                            description = description[:100]
                            if len(description) > 100:
                                description += "..."
                            if thread_url:
                                field_value += f"\n**[Details]({thread_url})**: {description}"
                            else:
                                field_value += f"\n**Details:** {description}"
                    else:
                        # No channel mention or URL, show description normally
                        description = event.description[:100]
                        if len(event.description) > 100:
                            description += "..."
                        if thread_url:
                            field_value += f"\n**[Details]({thread_url})**: {description}"
                        else:
                            field_value += f"\n**Details:** {description}"

                elif thread_url:
                    # No description, but still provide a link to the event thread.
                    field_value += f"\n**[Details]({thread_url})**"

                embed.add_field(
                    name="\u200b",
                    value=f"📌 **[{self._format_event_title(guild, event.name)}]({event.url})**\n{field_value}",
                    inline=False
                )

        embed.set_footer(text="Last updated")
        
        #if guild.icon:
        #    embed.set_thumbnail(url=guild.icon.url)

        return embed

async def setup(bot: commands.Bot):
    """Load the cog."""
    await bot.add_cog(EventDisplayCog(bot))
    logger.info("EventDisplayCog loaded successfully")

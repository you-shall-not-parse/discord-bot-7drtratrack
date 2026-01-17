import logging
import re
import json
import os
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
EVENT_DISPLAY_CHANNEL_ID = 1099806153170489485  # Replace with your channel ID

# How often to update the events display (in minutes)
UPDATE_INTERVAL_MINUTES = 30

# Maximum number of events to display
MAX_EVENTS_TO_DISPLAY = 20

# Color for the embed
EMBED_COLOR = 0x5865F2  # Discord blurple

# Path to save events JSON
EVENTS_JSON_PATH = data_path("events_history.json")


class EventDisplayCog(commands.Cog):
    """
    A cog that reads Discord scheduled events and displays them in an embed.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_message_id: Optional[int] = None
        self.update_events_display.start()
        logger.info("EventDisplayCog initialized")

    def cog_unload(self):
        """Stop the background task when the cog is unloaded."""
        self.update_events_display.cancel()
        logger.info("EventDisplayCog unloaded")

    @tasks.loop(minutes=UPDATE_INTERVAL_MINUTES)
    async def update_events_display(self):
        """
        Periodically fetch and display server events.
        """
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

            # Fetch scheduled events
            events = await guild.fetch_scheduled_events(with_counts=True)
            
            # Filter for only scheduled (future) or active (live) events
            # Exclude completed and cancelled events
            filtered_events = [
                e for e in events
                if e.status in (discord.EventStatus.scheduled, discord.EventStatus.active)
            ]
            
            # Sort events by start time
            sorted_events = sorted(
                filtered_events,
                key=lambda e: e.start_time if e.start_time else datetime.max
            )[:MAX_EVENTS_TO_DISPLAY]

            # Create embed
            embed = await self.create_events_embed(guild, sorted_events)

            # Delete previous message if it exists
            if self.last_message_id:
                try:
                    old_message = await channel.fetch_message(self.last_message_id)
                    await old_message.delete()
                except discord.NotFound:
                    logger.debug("Previous message not found, skipping deletion")
                except discord.Forbidden:
                    logger.warning("No permission to delete previous message")
                except Exception as e:
                    logger.error(f"Error deleting previous message: {e}")

            # Send new message
            message = await channel.send(embed=embed)
            self.last_message_id = message.id
            logger.info(f"Updated events display with {len(sorted_events)} events")
            
            # Save all events (not just filtered ones) to JSON
            await self.save_events_to_json(events)

        except Exception as e:
            logger.error(f"Error updating events display: {e}", exc_info=True)

    @update_events_display.before_loop
    async def before_update_events_display(self):
        """Wait until the bot is ready before starting the loop."""
        await self.bot.wait_until_ready()
        logger.info("EventDisplayCog: Bot is ready, starting event display loop")

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
                # Format the event time
                start_time_str = (
                    f"<t:{int(event.start_time.timestamp())}:F>"
                    if event.start_time
                    else "TBA"
                )

                # Get participant count
                participant_info = ""
                if event.user_count is not None:
                    participant_info = f"\n👥 **Interested:** {event.user_count}"

                # Event status
                status_emoji = {
                    discord.EventStatus.scheduled: "🕒",
                    discord.EventStatus.active: "🟢",
                    discord.EventStatus.completed: "✅",
                    discord.EventStatus.cancelled: "❌",
                }.get(event.status, "")

                # Location information
                location_str = ""
                if event.location:
                    location_str = f"\n📍 **Location:** {event.location}"
                elif event.channel:
                    location_str = f"\n📍 **Channel:** {event.channel.mention}"

                # Build the field value
                field_value = (
                    f"{status_emoji} **Status:** {event.status.name.capitalize()}\n"
                    f"🕐 **Start:** {start_time_str}"
                    f"{location_str}"
                    f"{participant_info}"
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
                        field_value += f"\n📝 **Sign-up:** <#{channel_id}>"
                        
                        # Show rest of description (excluding channel mentions and URLs)
                        description = re.sub(r'<#\d+>', '', event.description)
                        description = re.sub(r'https?://(?:discord|discordapp)\.com/channels/\d+/\d+', '', description).strip()
                        if description:
                            description = description[:100]
                            if len(description) > 100:
                                description += "..."
                            field_value += f"\n{description}"
                    else:
                        # No channel mention or URL, show description normally
                        description = event.description[:100]
                        if len(event.description) > 100:
                            description += "..."
                        field_value += f"\n📝 {description}"

                embed.add_field(
                    name=f"{event.name}",
                    value=field_value,
                    inline=False
                )

        embed.set_footer(text="Last updated")
        
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        return embed

async def setup(bot: commands.Bot):
    """Load the cog."""
    await bot.add_cog(EventDisplayCog(bot))
    logger.info("EventDisplayCog loaded successfully")

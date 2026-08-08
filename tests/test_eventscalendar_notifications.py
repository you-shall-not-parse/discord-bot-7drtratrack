import io
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from PIL import Image

from cogs.eventscalendar import EVENT_NOTIFICATION_BACKGROUND_DIR, EventDisplayCog


class EventCalendarNotificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cog = EventDisplayCog.__new__(EventDisplayCog)

    def _event(self) -> SimpleNamespace:
        return SimpleNamespace(
            id=1535370648119943168,
            guild_id=1097913605082579024,
            name="TAF Round 3: 7DR Vs OFIN",
            description=(
                "Aiming for 50s, possible 40s\n"
                "Map & Mid TBC\n"
                "Allies: 7DR\n"
                "Axis: OFIN\n"
                "Server host: OFIN"
            ),
            start_time=datetime(2026, 8, 30, 19, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc),
            location="In game",
            channel=None,
            creator=SimpleNamespace(mention="<@1234>"),
            creator_id=1234,
            url="https://discord.com/events/1097913605082579024/1535370648119943168",
        )

    def test_notification_embed_contains_compact_title_links(self) -> None:
        event = self._event()
        guild = SimpleNamespace(emojis=[])
        title = self.cog._format_event_title(guild, event.name)

        embed = self.cog._build_event_notification_embed(
            scheduled_event=event,
            title=title,
            occurrence_start=None,
        )
        payload = embed.to_dict()
        fields = {field["name"]: field["value"] for field in payload["fields"]}

        self.assertEqual(payload["title"], "New Event Added to Calendar!")
        self.assertEqual(
            payload["url"],
            "https://discord.com/channels/1097913605082579024/1332736267485708419",
        )
        self.assertIn("[TAF Round 3: 7DR :7DR: Vs OFIN :flag_fi:](https://discord.com/events/", payload["description"])
        self.assertIn("[Google Calendar](https://calendar.google.com/", payload["description"])
        self.assertNotIn("Aiming for 50s, possible 40s", payload["description"])
        self.assertEqual(fields["Date / Time"], "30 Aug 2026  |  19:00 - 20:00 UTC")
        self.assertNotIn("Location", fields)
        self.assertNotIn("Channel", fields)
        self.assertEqual(fields["Added By"], "<@1234>")
        self.assertNotIn("Event Link", fields)
        self.assertNotIn("Google Calendar", fields)
        self.assertNotIn("Discussion Thread", fields)

    def test_updates_stop_at_event_end(self) -> None:
        event = self._event()
        self.assertEqual(self.cog._event_update_cutoff(event, None), event.end_time)

    async def test_periodic_sync_does_not_create_notifications_for_existing_events(self) -> None:
        event = self._event()
        event.status = discord.EventStatus.scheduled
        self.cog._notification_state = {"events": {}}
        self.cog._fetch_raw_scheduled_events = AsyncMock(return_value={})
        self.cog.bot = SimpleNamespace(get_channel=Mock())
        guild = SimpleNamespace(id=1097913605082579024)

        await self.cog._sync_event_notifications(guild, [event])

        self.cog.bot.get_channel.assert_not_called()
        self.assertEqual(self.cog._notification_state["events"], {})

    async def test_manually_deleted_notification_is_not_recreated(self) -> None:
        event = self._event()
        channel_id = 1192922522673500190
        event_state = {
            "notification_messages": {str(channel_id): 999},
            "deleted_channels": [],
        }
        self.cog._notification_state = {"events": {str(event.id): event_state}}
        response = SimpleNamespace(status=404, reason="Not Found")
        self.cog._retry_discord_request = AsyncMock(
            side_effect=discord.NotFound(response, "Unknown Message")
        )
        self.cog._render_event_cover_image = AsyncMock(return_value=b"image")
        self.cog._pick_event_background = Mock(return_value=None)
        channel = SimpleNamespace(
            id=channel_id,
            guild=SimpleNamespace(emojis=[]),
            send=AsyncMock(),
            fetch_message=AsyncMock(),
        )

        await self.cog._upsert_event_notification(channel, event, occurrence_start=None)

        self.assertNotIn(str(channel_id), event_state["notification_messages"])
        self.assertIn(str(channel_id), event_state["deleted_channels"])
        channel.send.assert_not_awaited()

    async def test_cover_renderer_reads_a_local_map_image(self) -> None:
        event = self._event()
        background = Path(EVENT_NOTIFICATION_BACKGROUND_DIR, "Carentan.png")

        rendered = await self.cog._render_event_cover_image(
            title=event.name,
            start_time=event.start_time,
            end_time=event.end_time,
            background_path=str(background),
        )

        with Image.open(io.BytesIO(rendered)) as image:
            self.assertEqual(image.size, (1600, 900))
            self.assertEqual(image.format, "PNG")


if __name__ == "__main__":
    unittest.main()

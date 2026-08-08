import io
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from cogs.eventscalendar import EVENT_NOTIFICATION_BACKGROUND_DIR, EventDisplayCog


class EventCalendarNotificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cog = EventDisplayCog.__new__(EventDisplayCog)

    def _event(self) -> SimpleNamespace:
        return SimpleNamespace(
            id=1535370648119943168,
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

    def test_notification_embed_contains_requested_details_and_compact_links(self) -> None:
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
        self.assertIn("TAF Round 3: 7DR :7DR: Vs OFIN :flag_fi:", payload["description"])
        self.assertIn("Aiming for 50s, possible 40s", payload["description"])
        self.assertEqual(fields["Date / Time"], "30 Aug 2026  |  19:00 - 20:00 UTC")
        self.assertEqual(fields["Location"], "In game")
        self.assertEqual(fields["Added By"], "<@1234>")
        self.assertTrue(fields["Event Link"].startswith("[View Discord Event]("))
        self.assertTrue(fields["Google Calendar"].startswith("[Add to Google Calendar]("))
        self.assertNotIn("Discussion Thread", fields)

    def test_updates_stop_at_event_end(self) -> None:
        event = self._event()
        self.assertEqual(self.cog._event_update_cutoff(event, None), event.end_time)

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

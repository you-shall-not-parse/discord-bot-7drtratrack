import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from cogs.strategic_review_note import (
    StrategicReviewNote,
    _display_title,
    _has_fight_arranger_role,
    _parse_uk_since_time,
    _parse_uk_transcript_window,
    _safe_filename,
)


class StrategicReviewNoteTimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)

    def test_parses_full_uk_summer_time(self) -> None:
        parsed, error = _parse_uk_since_time("07/08/2026 09:30", now_utc=self.now)

        self.assertIsNone(error)
        self.assertEqual(parsed, datetime(2026, 8, 7, 8, 30, tzinfo=timezone.utc))

    def test_parses_yesterday_relative_to_uk_date(self) -> None:
        parsed, error = _parse_uk_since_time("yesterday 18:00", now_utc=self.now)

        self.assertIsNone(error)
        self.assertEqual(parsed, datetime(2026, 8, 7, 17, 0, tzinfo=timezone.utc))

    def test_rejects_future_time(self) -> None:
        parsed, error = _parse_uk_since_time("today 23:00", now_utc=self.now)

        self.assertIsNone(parsed)
        self.assertIn("future", error or "")

    def test_rejects_nonexistent_dst_time(self) -> None:
        parsed, error = _parse_uk_since_time(
            "29/03/2026 01:30",
            now_utc=datetime(2026, 3, 30, 12, 0, tzinfo=timezone.utc),
        )

        self.assertIsNone(parsed)
        self.assertIn("does not exist", error or "")

    def test_filename_is_safe_and_bounded(self) -> None:
        filename = _safe_filename("A review: plans / risks?", self.now)

        self.assertEqual(filename, "strategic-review-A-review-plans-risks-20260808-1300.txt")

    def test_fight_arranger_role_is_required_by_exact_name(self) -> None:
        allowed = SimpleNamespace(roles=[SimpleNamespace(name="Fight Arranger")])
        wrong_case = SimpleNamespace(roles=[SimpleNamespace(name="fight arranger")])
        no_roles = SimpleNamespace()

        self.assertTrue(_has_fight_arranger_role(allowed))
        self.assertFalse(_has_fight_arranger_role(wrong_case))
        self.assertFalse(_has_fight_arranger_role(no_roles))

    def test_display_title_has_strategic_review_prefix(self) -> None:
        self.assertEqual(
            _display_title("Use of Snipers"),
            "Strategic Review Note: Use of Snipers",
        )

    def test_parses_explicit_date_from_and_to_window(self) -> None:
        from_utc, to_utc, error = _parse_uk_transcript_window(
            "07/08/2026",
            "18:00",
            "20:30",
            now_utc=self.now,
        )

        self.assertIsNone(error)
        self.assertEqual(from_utc, datetime(2026, 8, 7, 17, 0, tzinfo=timezone.utc))
        self.assertEqual(to_utc, datetime(2026, 8, 7, 19, 30, tzinfo=timezone.utc))

    def test_rejects_window_when_from_is_not_before_to(self) -> None:
        from_utc, to_utc, error = _parse_uk_transcript_window(
            "07/08/2026",
            "20:30",
            "18:00",
            now_utc=self.now,
        )

        self.assertIsNone(from_utc)
        self.assertIsNone(to_utc)
        self.assertIn("earlier", error or "")

    def test_rejects_window_with_future_to_time(self) -> None:
        from_utc, to_utc, error = _parse_uk_transcript_window(
            "today",
            "12:00",
            "23:00",
            now_utc=self.now,
        )

        self.assertIsNone(from_utc)
        self.assertIsNone(to_utc)
        self.assertIn("future", error or "")

    def test_slash_command_exposes_date_from_and_to_options(self) -> None:
        option_names = [
            option["name"]
            for option in StrategicReviewNote.strategic_review_note.to_dict()["options"]
        ]

        self.assertEqual(option_names, ["title", "date", "from", "to"])

    def test_weekly_embed_explains_slash_command(self) -> None:
        cog = StrategicReviewNote.__new__(StrategicReviewNote)
        cog.state = {"notes": {}}

        embed = cog._digest_embeds(now_uk=self.now)[0].to_dict()
        fields = {field["name"]: field["value"] for field in embed["fields"]}

        instructions = fields["Create a strategic review note"]
        self.assertIn("/strategic-review-note", instructions)
        self.assertIn("date:07/08/2026", instructions)
        self.assertIn("from:18:00", instructions)
        self.assertIn("to:20:30", instructions)

    def test_note_embed_uses_selected_to_time(self) -> None:
        note = {
            "title": "Use of Snipers",
            "creator_id": 123,
            "created_at": "2026-08-08T12:00:00+00:00",
            "since_at": "2026-08-07T17:00:00+00:00",
            "until_at": "2026-08-07T19:30:00+00:00",
            "message_count": 4,
        }

        payload = StrategicReviewNote._note_embed(note).to_dict()
        fields = {field["name"]: field["value"] for field in payload["fields"]}

        self.assertEqual(
            fields["Transcript window"],
            "<t:1786122000:F> to <t:1786131000:F>",
        )


class StrategicReviewNoteDeletionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cog = StrategicReviewNote.__new__(StrategicReviewNote)
        self.cog.state = {
            "notes": {
                "100": {
                    "thread_id": 100,
                    "header_message_id": 200,
                    "parent_message_id": 300,
                    "title": "First",
                },
                "101": {
                    "thread_id": 101,
                    "header_message_id": 201,
                    "parent_message_id": 301,
                    "title": "Second",
                },
            }
        }
        self.cog._state_lock = asyncio.Lock()
        self.cog._save_state = Mock()

    async def test_removes_entry_when_thread_is_deleted(self) -> None:
        await self.cog._remove_deleted_note(thread_id=100)

        self.assertNotIn("100", self.cog.state["notes"])
        self.assertIn("101", self.cog.state["notes"])
        self.cog._save_state.assert_called_once_with()

    async def test_removes_entry_when_header_is_deleted(self) -> None:
        await self.cog._remove_deleted_note(message_ids={201})

        self.assertNotIn("101", self.cog.state["notes"])
        self.assertIn("100", self.cog.state["notes"])
        self.cog._save_state.assert_called_once_with()

    async def test_removes_entry_when_parent_transcript_is_deleted(self) -> None:
        await self.cog._remove_deleted_note(message_ids={300})

        self.assertNotIn("100", self.cog.state["notes"])
        self.assertIn("101", self.cog.state["notes"])
        self.cog._save_state.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

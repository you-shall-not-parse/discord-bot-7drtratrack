import unittest
from datetime import datetime, timezone

from cogs.strategic_review_note import _parse_uk_since_time, _safe_filename


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


if __name__ == "__main__":
    unittest.main()

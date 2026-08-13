import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from cogs.bulkdelete import (
    MAX_MESSAGES_TO_DELETE,
    BulkDelete,
    _has_protected_tick,
    _is_bot_message,
    _parse_uk_window,
)


class BulkDeleteTimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 13, 21, 0, tzinfo=timezone.utc)

    def test_parses_uk_summer_window(self) -> None:
        start, finish, error = _parse_uk_window(
            "13/08/2026", "18:00", "20:30", now_utc=self.now
        )

        self.assertIsNone(error)
        self.assertEqual(start, datetime(2026, 8, 13, 17, 0, tzinfo=timezone.utc))
        self.assertEqual(finish, datetime(2026, 8, 13, 19, 30, tzinfo=timezone.utc))

    def test_rejects_reverse_window(self) -> None:
        start, finish, error = _parse_uk_window(
            "13/08/2026", "20:30", "18:00", now_utc=self.now
        )

        self.assertIsNone(start)
        self.assertIsNone(finish)
        self.assertIn("earlier", error or "")

    def test_rejects_nonexistent_dst_time(self) -> None:
        start, finish, error = _parse_uk_window(
            "29/03/2026",
            "01:30",
            "03:00",
            now_utc=datetime(2026, 3, 30, 12, 0, tzinfo=timezone.utc),
        )

        self.assertIsNone(start)
        self.assertIsNone(finish)
        self.assertIn("does not exist", error or "")

    def test_command_uses_date_from_and_to_options(self) -> None:
        names = [option["name"] for option in BulkDelete.bulkdelete.to_dict()["options"]]
        self.assertEqual(names, ["date", "from", "to"])


class BulkDeleteProtectionTests(unittest.TestCase):
    def test_deletion_limit_is_twenty(self) -> None:
        self.assertEqual(MAX_MESSAGES_TO_DELETE, 20)

    def test_exact_tick_reaction_protects_message(self) -> None:
        message = SimpleNamespace(reactions=[SimpleNamespace(emoji="✅")])
        self.assertTrue(_has_protected_tick(message))

    def test_other_reactions_do_not_protect_message(self) -> None:
        message = SimpleNamespace(reactions=[SimpleNamespace(emoji="👍")])
        self.assertFalse(_has_protected_tick(message))

    def test_ratbot_message_is_excluded_by_author_id(self) -> None:
        message = SimpleNamespace(author=SimpleNamespace(id=1234))
        self.assertTrue(_is_bot_message(message, 1234))
        self.assertFalse(_is_bot_message(message, 5678))


if __name__ == "__main__":
    unittest.main()

import unittest

from cogs.GameMonCog import BLUEBERRY_ROLE_ID, EXCLUDED_ROLE_IDS, KEEP_LAST_MESSAGES


class GameMonitorConfigTests(unittest.TestCase):
    def test_blueberry_role_is_excluded(self) -> None:
        self.assertEqual(BLUEBERRY_ROLE_ID, 1440120995171012699)
        self.assertIn(BLUEBERRY_ROLE_ID, EXCLUDED_ROLE_IDS)

    def test_only_five_monitor_messages_are_retained(self) -> None:
        self.assertEqual(KEEP_LAST_MESSAGES, 5)


if __name__ == "__main__":
    unittest.main()

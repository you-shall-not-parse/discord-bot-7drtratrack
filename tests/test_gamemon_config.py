import unittest

from cogs.GameMonCog import (
    BLUEBERRY_ROLE_ID,
    EXCLUDED_ROLE_IDS,
    HLL_CHANNEL_ID,
    HLLV_CHANNEL_ID,
    KEEP_LAST_MESSAGES,
    THREAD_ID,
    GameMonCog,
)


class GameMonitorConfigTests(unittest.TestCase):
    def test_blueberry_role_is_excluded(self) -> None:
        self.assertEqual(BLUEBERRY_ROLE_ID, 1440120995171012699)
        self.assertIn(BLUEBERRY_ROLE_ID, EXCLUDED_ROLE_IDS)

    def test_only_five_monitor_messages_are_retained(self) -> None:
        self.assertEqual(KEEP_LAST_MESSAGES, 5)

    def test_hllv_has_its_own_destination(self) -> None:
        self.assertEqual(HLLV_CHANNEL_ID, 1511085797695160501)
        self.assertNotEqual(HLLV_CHANNEL_ID, HLL_CHANNEL_ID)
        self.assertNotEqual(HLLV_CHANNEL_ID, THREAD_ID)

    def test_game_destinations_are_routed_separately(self) -> None:
        cog = GameMonCog.__new__(GameMonCog)

        self.assertEqual(cog._get_destination_channel_id_for_game("Hell Let Loose"), HLL_CHANNEL_ID)
        self.assertEqual(
            cog._get_destination_channel_id_for_game("Hell Let Loose: Vietnam"),
            HLLV_CHANNEL_ID,
        )
        self.assertEqual(cog._get_destination_channel_id_for_game("Another Game"), THREAD_ID)

    def test_hllv_name_matching_is_case_insensitive(self) -> None:
        cog = GameMonCog.__new__(GameMonCog)

        self.assertTrue(cog._is_hllv_game("  HELL LET LOOSE: VIETNAM  "))
        self.assertTrue(cog._is_hllv_game("Hell Let Loose Vietnam"))
        self.assertFalse(cog._is_hllv_game("Hell Let Loose"))


if __name__ == "__main__":
    unittest.main()

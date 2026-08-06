import unittest

from cogs.raid import Raid


def _raid_parser() -> Raid:
    return Raid.__new__(Raid)


class CRCONLiveStateTests(unittest.TestCase):
    def test_scoreboard_stats_are_used_for_player_detection(self) -> None:
        state = _raid_parser()._parse_crcon_live_state(
            {
                "result": {
                    "current_map": "carentan_warfare",
                    "num_allied_players": 1,
                    "num_axis_players": 1,
                }
            },
            {
                "result": {
                    "snapshot_timestamp": 1_786_000_000,
                    "stats": [
                        {"player": "Alice", "player_id": "steam-alice"},
                        {"player": "Bob", "player_id": "xbox-bob"},
                    ],
                }
            },
        )

        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["players"], 2)
        self.assertIs(state["player_detection_available"], True)
        self.assertEqual(
            state["player_records"],
            [
                {"player_id": "steam-alice", "side": ""},
                {"player_id": "xbox-bob", "side": ""},
            ],
        )

    def test_game_stats_are_a_fallback_when_scoreboard_is_unavailable(self) -> None:
        state = _raid_parser()._parse_crcon_live_state(
            {
                "result": {
                    "stats": [
                        {"player": "Alice", "player_id": "steam-alice"},
                    ]
                }
            },
            None,
        )

        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["players"], 1)
        self.assertIs(state["player_detection_available"], True)
        self.assertEqual(
            state["player_records"],
            [{"player_id": "steam-alice", "side": ""}],
        )

    def test_empty_stats_is_valid_player_detection(self) -> None:
        state = _raid_parser()._parse_crcon_live_state(
            {"result": {"current_map": "carentan_warfare"}},
            {"result": {"stats": []}},
        )

        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["players"], 0)
        self.assertIs(state["player_detection_available"], True)
        self.assertEqual(state["player_records"], [])

    def test_public_info_supplies_map_counts_and_time(self) -> None:
        state = _raid_parser()._parse_crcon_live_state(
            {"result": {"stats": []}},
            None,
            {
                "result": {
                    "current_map": {
                        "map": {
                            "id": "carentan_warfare",
                            "pretty_name": "Carentan Warfare",
                            "map": {"pretty_name": "Carentan"},
                        }
                    },
                    "player_count": 34,
                    "max_player_count": 100,
                    "player_count_by_team": {"allied": 18, "axis": 16},
                    "time_remaining": 2789,
                }
            },
        )

        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["map"], "Carentan Warfare")
        self.assertEqual(state["players"], 34)
        self.assertEqual(state["max_players"], 100)
        self.assertEqual(state["time_remaining_seconds"], 2789)

    def test_crcon_team_association_supplies_player_side(self) -> None:
        records = _raid_parser()._player_records_from_list(
            [
                {
                    "player_id": "steam-allies",
                    "team": {"side": "allies", "confidence": "strong"},
                },
                {
                    "player_id": "steam-axis",
                    "team": {"side": "axis", "confidence": "mixed"},
                },
            ]
        )

        self.assertEqual(
            records,
            [
                {"player_id": "steam-allies", "side": "Allies"},
                {"player_id": "steam-axis", "side": "Axis"},
            ],
        )


if __name__ == "__main__":
    unittest.main()

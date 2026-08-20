import csv
import io
import unittest

from cogs.wardiary import WarDiaryCog


class WarDiaryExportTests(unittest.TestCase):
    def test_builds_crcon_api_url_from_game_link(self) -> None:
        self.assertEqual(
            WarDiaryCog._crcon_match_api_url("https://stats.example.test/games/123/charts"),
            "https://stats.example.test/api/get_map_scoreboard?map_id=123",
        )

    def test_uses_score_and_recorded_result_to_assign_sides(self) -> None:
        payload = {"result": {"result": {"allied": 3, "axis": 2}}}
        self.assertEqual(
            WarDiaryCog._determine_clan_sides(
                payload,
                clan_name="7DR",
                opponent_clan_name="CROWS",
                is_clan_win=True,
            ),
            ("7DR", "CROWS"),
        )
        self.assertEqual(
            WarDiaryCog._determine_clan_sides(
                payload,
                clan_name="7DR",
                opponent_clan_name="CROWS",
                is_clan_win=False,
            ),
            ("CROWS", "7DR"),
        )

    def test_falls_back_to_clan_tags_in_player_names(self) -> None:
        payload = {
            "result": {
                "result": None,
                "player_stats": [
                    {"player": "[7DR] Rat One", "team": {"side": "axis"}},
                    {"player": "7DR | Rat Two", "team": {"side": "axis"}},
                    {"player": "[CROWS] Crow One", "team": {"side": "allies"}},
                ],
            }
        }
        self.assertEqual(
            WarDiaryCog._determine_clan_sides(
                payload,
                clan_name="7DR",
                opponent_clan_name="CROWS",
                is_clan_win=None,
            ),
            ("CROWS", "7DR"),
        )

    def test_csv_includes_allied_and_axis_clans(self) -> None:
        cog = object.__new__(WarDiaryCog)
        cog._state = {
            "match_threads": [
                {
                    "match_date": "20/08/26",
                    "map_name": "Carentan",
                    "clan_name": "7DR",
                    "opponent_clan_name": "CROWS",
                    "allies_clan": "CROWS",
                    "axis_clan": "7DR",
                    "stats_link": "https://stats.example.test/games/123",
                }
            ]
        }
        rows = list(csv.reader(io.StringIO(cog._build_export_csv().decode("utf-8-sig"))))
        self.assertEqual(
            rows,
            [
                [
                    "match_date",
                    "map",
                    "clans_played",
                    "allies_clan",
                    "axis_clan",
                    "stats_link",
                ],
                [
                    "20/08/26",
                    "Carentan",
                    "7DR vs CROWS",
                    "CROWS",
                    "7DR",
                    "https://stats.example.test/games/123",
                ],
            ],
        )


if __name__ == "__main__":
    unittest.main()

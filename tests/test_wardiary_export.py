import csv
import io
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from cogs.wardiary import WarDiaryCog, _display_stats_link, _normalize_stats_link


class WarDiaryExportTests(unittest.TestCase):
    def test_builds_crcon_api_url_from_game_link(self) -> None:
        self.assertEqual(
            WarDiaryCog._crcon_match_api_url("https://stats.example.test/games/123/charts"),
            "https://stats.example.test/api/get_map_scoreboard?map_id=123",
        )

    def test_bifrost_game_link_uses_crcon_proxy(self) -> None:
        self.assertEqual(
            WarDiaryCog._crcon_match_api_url(
                "https://frostbite.bifrostgaming.com/hll/leaderboards/servers/abc123/games/456"
            ),
            "https://frostbite.bifrostgaming.com/hll/leaderboards/servers/abc123/crcon/api/get_map_scoreboard?map_id=456",
        )

    def test_existing_bifrost_crcon_prefix_is_not_duplicated(self) -> None:
        self.assertEqual(
            WarDiaryCog._crcon_match_api_url(
                "https://frostbite.bifrostgaming.com/hll/leaderboards/servers/abc123/crcon/games/456"
            ),
            "https://frostbite.bifrostgaming.com/hll/leaderboards/servers/abc123/crcon/api/get_map_scoreboard?map_id=456",
        )

    def test_rewrites_retired_7dr_stats_hostname(self) -> None:
        self.assertEqual(
            _normalize_stats_link("https://7dr-stats.hlladmin.com/games/2572"),
            "https://7drhistostats.hllfrontline.com/games/2572",
        )
        self.assertEqual(
            WarDiaryCog._crcon_match_api_url(
                "https://7dr-stats.hlladmin.com/api/get_map_scoreboard?map_id=2572"
            ),
            "https://7drhistostats.hllfrontline.com/api/get_map_scoreboard?map_id=2572",
        )

    def test_bifrost_server_link_gets_crcon_suffix(self) -> None:
        self.assertEqual(
            _normalize_stats_link(
                "https://frostbite.bifrostgaming.com/hll/leaderboards/servers/abc123"
            ),
            "https://frostbite.bifrostgaming.com/hll/leaderboards/servers/abc123/crcon",
        )

    def test_bifrost_display_link_stops_at_crcon(self) -> None:
        self.assertEqual(
            _display_stats_link(
                "https://frostbite.bifrostgaming.com/hll/leaderboards/servers/39384557d39d/crcon/games/11336"
            ),
            "https://frostbite.bifrostgaming.com/hll/leaderboards/servers/39384557d39d/crcon",
        )

    def test_uses_direct_bifrost_uuid_match_endpoint(self) -> None:
        match_url = (
            "https://frostbite.bifrostgaming.com/hll/utahbeach_warfare/"
            "923eed7b-6a86-57be-91e4-b5e4bc75c8cb/crcon"
        )
        self.assertEqual(WarDiaryCog._crcon_match_api_url(match_url), match_url)
        self.assertEqual(
            WarDiaryCog._crcon_match_api_url(match_url.removesuffix("/crcon")),
            match_url,
        )
        self.assertEqual(
            WarDiaryCog._crcon_match_api_url(
                match_url.replace("https://frostbite", "https://www.frostbite").removesuffix("/crcon")
            ),
            match_url.replace("https://frostbite", "https://www.frostbite"),
        )

        payload = {
            "result": {
                "result": {"allied": 2, "axis": 3},
                "player_stats": [
                    {"player": "7DR Player", "team": {"side": "allies"}},
                    {"player": "RMC Player", "team": {"side": "axis"}},
                ],
            }
        }
        self.assertEqual(
            WarDiaryCog._determine_clan_sides(
                payload,
                clan_name="7DR",
                opponent_clan_name="RMC",
                is_clan_win=False,
            ),
            ("7DR", "RMC"),
        )

    def test_rewrites_retired_rmc_events_link_to_bifrost_crcon(self) -> None:
        self.assertEqual(
            WarDiaryCog._crcon_match_api_url(
                "https://rmcevents-stats.hlladmin.com/api/get_map_scoreboard?map_id=11854"
            ),
            "https://frostbite.bifrostgaming.com/hll/leaderboards/servers/39384557d39d/crcon/api/get_map_scoreboard?map_id=11854",
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
                    "result": "3-2",
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
                    "result",
                    "allies_clan",
                    "axis_clan",
                    "stats_link",
                ],
                [
                    "20/08/26",
                    "Carentan",
                    "7DR vs CROWS",
                    '=\"3-2\"',
                    "CROWS",
                    "7DR",
                    "https://stats.example.test/games/123",
                ],
            ],
        )

    def test_csv_does_not_treat_legacy_date_as_result(self) -> None:
        cog = object.__new__(WarDiaryCog)
        cog._state = {
            "match_threads": [
                {
                    "match_date": "20/08/26",
                    "map_name": "Carentan",
                    "clan_name": "7DR",
                    "opponent_clan_name": "CROWS",
                    "result": "20/08/26",
                    "is_7dr_win": False,
                }
            ]
        }

        rows = list(csv.reader(io.StringIO(cog._build_export_csv().decode("utf-8-sig"))))
        self.assertEqual(rows[1][3], "Loss")

    def test_csv_marks_score_as_spreadsheet_text(self) -> None:
        cog = object.__new__(WarDiaryCog)
        cog._state = {
            "match_threads": [
                {
                    "match_date": "20/08/26",
                    "map_name": "Carentan",
                    "clan_name": "7DR",
                    "opponent_clan_name": "CROWS",
                    "result": "4-1",
                }
            ]
        }

        rows = list(csv.reader(io.StringIO(cog._build_export_csv().decode("utf-8-sig"))))
        self.assertEqual(rows[1][3], '=\"4-1\"')


class WarDiaryFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_refused_is_logged_without_a_traceback(self) -> None:
        class FakeRequestException(Exception):
            pass

        def refuse_connection(*args, **kwargs):
            raise FakeRequestException("connection refused")

        requests_stub = SimpleNamespace(
            RequestException=FakeRequestException,
            get=refuse_connection,
        )
        cog = object.__new__(WarDiaryCog)
        cog._url_has_only_public_addresses = AsyncMock(return_value=True)

        with patch.dict(sys.modules, {"requests": requests_stub}):
            with self.assertLogs("cogs.wardiary", level="WARNING") as captured:
                payload = await cog._fetch_crcon_match(
                    "http://65.109.128.186:1110/api/get_map_scoreboard?map_id=9392"
                )

        self.assertIsNone(payload)
        self.assertEqual(len(captured.output), 1)
        self.assertIn("connection refused", captured.output[0])


if __name__ == "__main__":
    unittest.main()

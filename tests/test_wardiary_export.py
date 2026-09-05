import csv
import io
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from cogs.wardiary import (
    WarDiaryCog,
    _display_stats_link,
    _event_review_thread_name,
    _normalize_stats_link,
    _transcript_filename,
)


class WarDiaryExportTests(unittest.TestCase):
    def test_event_review_thread_name_contains_match_details(self) -> None:
        self.assertEqual(
            _event_review_thread_name("CROWS", "Carentan", "20/08/26"),
            "Event Review 7DR Vs CROWS Carentan 20/08/26",
        )

    def test_event_review_thread_name_obeys_discord_limit(self) -> None:
        name = _event_review_thread_name("A" * 80, "B" * 80, "20/08/26")
        self.assertEqual(len(name), 100)
        self.assertTrue(name.endswith("..."))

    def test_transcript_filename_is_safe(self) -> None:
        self.assertEqual(
            _transcript_filename("Event Review 7DR Vs CROWS / Carentan"),
            "Event-Review-7DR-Vs-CROWS-Carentan-transcript.txt",
        )

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


    def test_links_event_review_thread_to_saved_match(self) -> None:
        cog = object.__new__(WarDiaryCog)
        cog._state = {
            "match_threads": [
                {
                    "thread_id": 10,
                    "clan_name": "7DR",
                    "opponent_clan_name": "CROWS",
                    "match_date": "20/08/26",
                }
            ]
        }
        cog._save_state = Mock()

        cog._link_review_thread(
            clan_name="7DR",
            opponent_clan_name="CROWS",
            match_date="20/08/26",
            review_thread_id=20,
        )

        self.assertEqual(cog._state["match_threads"][0]["review_thread_id"], 20)
        cog._save_state.assert_called_once_with()


class WarDiaryFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_review_transcript_contains_messages_and_attachments(self) -> None:
        message = SimpleNamespace(
            author=SimpleNamespace(display_name="Rat", id=123),
            created_at=datetime(2026, 8, 20, 19, 30, tzinfo=timezone.utc),
            content="Good comms",
            attachments=[SimpleNamespace(filename="plan.png", url="https://example.test/plan.png")],
            stickers=[],
            embeds=[],
            jump_url="https://discord.test/channels/1/2/3",
        )

        class FakeThread:
            name = "Event Review 7DR Vs CROWS Carentan 20/08/26"
            id = 456

            async def history(self, **kwargs):
                yield message

        cog = object.__new__(WarDiaryCog)
        payload, count = await cog._build_event_review_transcript(FakeThread())
        transcript = payload.decode("utf-8")

        self.assertEqual(count, 1)
        self.assertIn("Messages: 1", transcript)
        self.assertIn("Rat (Discord ID: 123)", transcript)
        self.assertIn("Good comms", transcript)
        self.assertIn("[Attachment: plan.png] https://example.test/plan.png", transcript)

    async def test_frostbite_fetch_uses_browser_headers(self) -> None:
        class FakeRequestException(Exception):
            pass

        captured: dict[str, object] = {}

        def successful_get(url, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                status_code=200,
                content=b'{"result": {}}',
                json=lambda: {"result": {}},
            )

        requests_stub = SimpleNamespace(
            RequestException=FakeRequestException,
            get=successful_get,
        )
        cog = object.__new__(WarDiaryCog)
        cog._url_has_only_public_addresses = AsyncMock(return_value=True)

        with patch.dict(sys.modules, {"requests": requests_stub}):
            payload = await cog._fetch_crcon_match("https://frostbite.bifrostgaming.com/match/crcon")

        self.assertEqual(payload, {"result": {}})
        headers = captured["headers"]
        self.assertIn("Mozilla/5.0", headers["User-Agent"])
        self.assertIn("application/json", headers["Accept"])

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

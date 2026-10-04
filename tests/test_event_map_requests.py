import sys
import types
import unittest
import os
import runpy
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_stub = types.ModuleType("requests")
    requests_stub.Response = object
    requests_stub.RequestException = RuntimeError
    requests_stub.request = MagicMock()
    requests_stub.post = MagicMock()
    requests_stub.get = MagicMock()
    sys.modules["requests"] = requests_stub

from cogs.event_map_requests import (
    ADMIN_CAM_SERVER_OPTIONS,
    HLLV_MAP_CACHE_PATH,
    MAP_CACHE_PATH,
    MAP_SERVER_OPTIONS,
    EventMapRequests,
)
from cogs.t17serveradmin import ADMIN_CAM_SERVER_CHOICES, T17ServerAdmin


class EventMapRequestConfigurationTests(unittest.TestCase):
    def test_only_public_and_hllv_can_be_selected(self):
        self.assertEqual(MAP_SERVER_OPTIONS, {"server_2": "Public", "hllv": "HLLV"})
        self.assertEqual(ADMIN_CAM_SERVER_OPTIONS, MAP_SERVER_OPTIONS)
        self.assertEqual(
            [(choice.name, choice.value) for choice in ADMIN_CAM_SERVER_CHOICES],
            [("Public", "server_2"), ("HLLV server", "hllv")],
        )

    def test_public_is_default_and_never_falls_back_to_events_id(self):
        config_path = Path(__file__).parents[1] / "config" / "hll_API_config.py"
        with patch.dict(os.environ, {
            "BIFROST_SERVER_ID1": "retired-events",
            "BIFROST_SERVER_ID": "legacy-events",
            "BIFROST_SERVER_ID2": "public-server",
            "BIFROST_HLLV_SERVER_ID": "hllv-server",
            "BIFROST_GRAPHQL_URL": "https://legacy.invalid/graphql",
        }):
            config = runpy.run_path(str(config_path))
            servers = config["HLL_BACKEND_SERVERS"]
            self.assertEqual(config["HLL_BACKEND_DEFAULT_SERVER"], "server_2")
            self.assertEqual(set(servers), {"server_2", "hllv"})
            self.assertEqual(servers["server_2"]["bifrost"]["server_id"], "public-server")
            self.assertEqual(servers["hllv"]["bifrost"]["server_id"], "hllv-server")
            self.assertEqual(servers["hllv"]["bifrost"]["game_type"], "HLLV")
            self.assertEqual(
                servers["server_2"]["bifrost"]["graphql_url"],
                "https://api.dev.bifrostgaming.com/v1/graphql",
            )
        with patch.dict(os.environ, {"BIFROST_SERVER_ID2": ""}):
            config = runpy.run_path(str(config_path))
            self.assertEqual(config["get_hll_backend_status"]()["server_id"], "")
            self.assertEqual(config["get_hll_backend_status"]()["server_id_env"], "BIFROST_SERVER_ID2")

    def test_hllv_is_available_for_maps_and_admin_cam(self):
        self.assertEqual(MAP_SERVER_OPTIONS["hllv"], "HLLV")
        self.assertEqual(ADMIN_CAM_SERVER_OPTIONS["hllv"], "HLLV")

    def test_hllv_map_catalogue_has_a_separate_cache(self):
        self.assertEqual(EventMapRequests._map_cache_path("server_2"), MAP_CACHE_PATH)
        self.assertEqual(EventMapRequests._map_cache_path("hllv"), HLLV_MAP_CACHE_PATH)
        self.assertNotEqual(MAP_CACHE_PATH, HLLV_MAP_CACHE_PATH)

    def test_hllv_map_request_embed_identifies_target_server(self):
        embed = EventMapRequests._request_embed(
            {
                "request_type": "map",
                "status": "pending",
                "requester_id": 123,
                "created_at": "2026-09-12T12:00:00+00:00",
                "server_name": "hllv",
                "server_label": "HLLV",
                "friendly_name": "Test Map",
                "game_mode": "Warfare",
                "time_of_day": "Day",
                "rcon_name": "test_map_warfare_day",
            }
        )

        self.assertEqual(embed.title, "🗺️ HLLV Map Change Request")
        fields = {field.name: field.value for field in embed.fields}
        self.assertEqual(fields["Server"], "HLLV")

    def test_hllv_admin_cam_embed_labels_eos_identity(self):
        embed = EventMapRequests._request_embed(
            {
                "request_type": "admin_cam",
                "status": "pending",
                "requester_id": 123,
                "created_at": "2026-09-12T12:00:00+00:00",
                "server_name": "hllv",
                "server_label": "HLLV",
                "duration_hours": 24,
                "identity_label": "HLLV EOS ID",
                "player_id": "eos-123",
            }
        )

        fields = {field.name: field.value for field in embed.fields}
        self.assertEqual(fields["HLLV EOS ID"], "`eos-123`")


class T17ServerAdminIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_retires_events_requests_and_preserves_live_requests(self):
        requests = {
            "events": {"server_name": "events", "status": "pending"},
            "main": {"server_name": "main", "status": "processing"},
            "legacy": {"status": "pending"},
            "public": {"server_name": "server_2", "status": "pending"},
            "hllv": {"server_name": "hllv", "status": "pending"},
            "history": {"server_name": "events", "status": "approved"},
        }
        bot = MagicMock()
        bot.loop.create_task.side_effect = lambda coroutine: coroutine.close()
        with patch("cogs.event_map_requests._read_json", side_effect=[{}, requests]), patch(
            "cogs.event_map_requests.atomic_json_dump"
        ) as save:
            EventMapRequests(bot)
        for key in ("events", "main", "legacy"):
            self.assertEqual(requests[key]["status"], "cancelled")
        for key in ("public", "hllv"):
            self.assertEqual(requests[key]["status"], "pending")
        self.assertEqual(requests["history"]["status"], "approved")
        save.assert_called_once()

    async def test_startup_preserves_public_and_hllv_removal_timers(self):
        cog = object.__new__(T17ServerAdmin)
        cog._load_state = MagicMock(return_value={"grants": {
            "events": {"server_name": "main"},
            "legacy": {},
            "public": {"server_name": "server_2"},
            "hllv": {"server_name": "hllv"},
        }})
        cog._remove_grant_record = MagicMock()
        cog._schedule_removal = MagicMock()
        await cog._restore_pending_grants()
        self.assertEqual(cog._remove_grant_record.call_args_list, [call("events"), call("legacy")])
        self.assertEqual(cog._schedule_removal.call_args_list, [call("public"), call("hllv")])

    async def test_hllv_uses_hllv_platform_id_resolution(self):
        cog = object.__new__(T17ServerAdmin)
        cog._resolve_hllv_platform_id = AsyncMock(
            return_value=("eos-123", "bifrost_hllv_live", ["Player"])
        )
        cog.lookup = MagicMock()

        result = await cog.resolve_admin_cam_identity(MagicMock(), "hllv")

        self.assertEqual(
            result,
            ("eos-123", "bifrost_hllv_live", ["Player"], "HLLV EOS ID"),
        )
        cog.lookup.resolve_member_for_role.assert_not_called()

    async def test_hll_servers_keep_using_t17_resolution(self):
        cog = object.__new__(T17ServerAdmin)
        cog._resolve_hllv_platform_id = AsyncMock()
        cog.lookup = MagicMock()
        cog.lookup.resolve_member_for_role = AsyncMock(
            return_value=("t17-123", "stored_mapping", ["Player"])
        )

        member = MagicMock()
        result = await cog.resolve_admin_cam_identity(member, "server_2")

        self.assertEqual(result, ("t17-123", "stored_mapping", ["Player"], "T17 ID"))
        cog.lookup.resolve_member_for_role.assert_awaited_once_with(
            member,
            role_name="t17serveradmin",
        )
        cog._resolve_hllv_platform_id.assert_not_called()


if __name__ == "__main__":
    unittest.main()

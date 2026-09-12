import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock

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
from cogs.t17serveradmin import T17ServerAdmin


class EventMapRequestConfigurationTests(unittest.TestCase):
    def test_hllv_is_available_for_maps_and_admin_cam(self):
        self.assertEqual(MAP_SERVER_OPTIONS["hllv"], "HLLV")
        self.assertEqual(ADMIN_CAM_SERVER_OPTIONS["hllv"], "HLLV")

    def test_hllv_map_catalogue_has_a_separate_cache(self):
        self.assertEqual(EventMapRequests._map_cache_path("events"), MAP_CACHE_PATH)
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
        result = await cog.resolve_admin_cam_identity(member, "main")

        self.assertEqual(result, ("t17-123", "stored_mapping", ["Player"], "T17 ID"))
        cog.lookup.resolve_member_for_role.assert_awaited_once_with(
            member,
            role_name="t17serveradmin",
        )
        cog._resolve_hllv_platform_id.assert_not_called()


if __name__ == "__main__":
    unittest.main()

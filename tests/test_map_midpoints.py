import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch
from types import SimpleNamespace
import json

from cogs.event_map_requests import EventMapRequests, MidpointView
from hll_API_backend import BifrostBackendClient, HLLBackendError
from cogs.frontline_web import FrontlineWeb


def sectors(midpoints=("TOWN CENTER", "TRAIN STATION")):
    return [
        {"sector": f"Sector_{index}", "objectives": [
            {"name": name} for name in (midpoints if index == 3 else ("OTHER",))
        ]}
        for index in range(1, 6)
    ]


def live(map_name="carentan_warfare", midpoints=("TOWN CENTER", "TRAIN STATION")):
    return {"mapRconName": map_name, "sectors": sectors(midpoints)}


class MidpointApprovalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = object.__new__(EventMapRequests)
        self.cog._server_map_locks = {}
        self.request = {"server_name": "server_2", "rcon_name": "carentan_warfare", "midpoint": "TOWN CENTER"}
        self.cog._requests = {"1": self.request}
        self.cog._midpoint_options = AsyncMock(return_value=["TOWN CENTER", "TRAIN STATION"])
        self.backend = MagicMock()
        self.backend.get_sector_options = AsyncMock(return_value=live())
        self.backend.change_map = AsyncMock(return_value={"success": True})
        self.backend.set_sector_layout = AsyncMock(return_value={"success": True, "appliedObjectives": ["A", "B", "TOWN CENTER", "D", "E"]})
        self.cog._backend = MagicMock(return_value=self.backend)
        self.save = patch("cogs.event_map_requests.atomic_json_dump").start()
        self.addCleanup(patch.stopall)

    async def test_same_exact_map_sets_layout_without_map_flip_or_delay(self):
        with patch("cogs.event_map_requests.asyncio.sleep", new_callable=AsyncMock) as sleep:
            result = await self.cog._apply_map_request(self.request)
        self.backend.change_map.assert_not_awaited()
        sleep.assert_not_awaited()
        self.backend.set_sector_layout.assert_awaited_once_with(
            "carentan_warfare", ["RANDOM", "RANDOM", "TOWN CENTER", "RANDOM", "RANDOM"]
        )
        self.assertEqual(result["appliedObjectives"][2], "TOWN CENTER")

    async def test_different_variant_changes_map_waits_30_seconds_checks_then_sets_layout(self):
        self.backend.get_sector_options.side_effect = [live("carentan_warfare_night"), live()]
        order = MagicMock()
        order.attach_mock(self.backend.get_sector_options, "current")
        order.attach_mock(self.backend.change_map, "change")
        order.attach_mock(self.backend.set_sector_layout, "layout")
        with patch("cogs.event_map_requests.time.time", return_value=100), patch(
            "cogs.event_map_requests.asyncio.sleep", new_callable=AsyncMock
        ) as sleep:
            order.attach_mock(sleep, "wait")
            await self.cog._apply_map_request(self.request)
        self.assertEqual(order.mock_calls, [
            call.current(), call.change("carentan_warfare"), call.wait(30), call.current(),
            call.layout("carentan_warfare", ["RANDOM", "RANDOM", "TOWN CENTER", "RANDOM", "RANDOM"]),
        ])
        self.assertEqual(self.request["layout_not_before"], 130)
        self.save.assert_called_once()

    async def test_map_not_loaded_after_delay_does_not_apply_layout(self):
        self.backend.get_sector_options.return_value = live("foy_warfare")
        with patch("cogs.event_map_requests.time.time", return_value=100), patch(
            "cogs.event_map_requests.asyncio.sleep", new_callable=AsyncMock
        ):
            with self.assertRaisesRegex(HLLBackendError, "not running yet"):
                await self.cog._apply_map_request(self.request)
        self.backend.set_sector_layout.assert_not_awaited()

    async def test_failed_first_flip_never_sleeps_or_applies_layout(self):
        self.backend.get_sector_options.return_value = live("foy_warfare")
        self.backend.change_map.side_effect = HLLBackendError("unavailable")
        with patch("cogs.event_map_requests.asyncio.sleep", new_callable=AsyncMock) as sleep:
            with self.assertRaises(HLLBackendError):
                await self.cog._apply_map_request(self.request)
        sleep.assert_not_awaited()
        self.backend.set_sector_layout.assert_not_awaited()

    async def test_retry_waits_remaining_delay_without_another_map_flip(self):
        self.request["layout_not_before"] = 130
        with patch("cogs.event_map_requests.time.time", return_value=110), patch(
            "cogs.event_map_requests.asyncio.sleep", new_callable=AsyncMock
        ) as sleep:
            await self.cog._apply_map_request(self.request)
        sleep.assert_awaited_once_with(20)
        self.backend.change_map.assert_not_awaited()

    async def test_variant_union_option_is_rejected_if_not_available_on_running_variant(self):
        self.backend.get_sector_options.return_value = live(midpoints=("TRAIN STATION",))
        with self.assertRaisesRegex(HLLBackendError, "running map variant"):
            await self.cog._apply_map_request(self.request)
        self.backend.set_sector_layout.assert_not_awaited()

    async def test_invalid_midpoint_is_rejected_before_server_commands(self):
        self.request["midpoint"] = "FORGED OBJECTIVE"
        with self.assertRaises(HLLBackendError):
            await self.cog._apply_map_request(self.request)
        self.backend.get_sector_options.assert_not_awaited()
        self.backend.change_map.assert_not_awaited()

    async def test_hllv_and_requests_without_midpoint_keep_single_map_change(self):
        for server in ("server_2", "hllv"):
            await self.cog._apply_map_request({"server_name": server, "rcon_name": "map"})
        self.assertEqual(self.backend.change_map.await_count, 2)
        self.backend.get_sector_options.assert_not_awaited()
        self.backend.set_sector_layout.assert_not_awaited()

    async def test_other_approval_cannot_change_map_during_wait(self):
        self.backend.get_sector_options.side_effect = [live("foy_warfare"), live()]
        waiting = asyncio.Event()
        release = asyncio.Event()

        async def delay(_seconds):
            waiting.set()
            await release.wait()

        with patch("cogs.event_map_requests.time.time", return_value=100), patch(
            "cogs.event_map_requests.asyncio.sleep", side_effect=delay
        ):
            first = asyncio.create_task(self.cog._apply_map_request(self.request))
            await waiting.wait()
            second = asyncio.create_task(self.cog._apply_map_request({"server_name": "server_2", "rcon_name": "foy_warfare"}))
            release.set()
            await asyncio.gather(first, second)
        self.assertEqual(self.backend.method_calls[-2:], [
            call.set_sector_layout("carentan_warfare", ["RANDOM", "RANDOM", "TOWN CENTER", "RANDOM", "RANDOM"]),
            call.change_map("foy_warfare"),
        ])

    async def test_discord_picker_sends_selected_midpoint(self):
        self.cog.create_request = AsyncMock()
        picker = MidpointView(self.cog, self.request, ["TOWN CENTER", "TRAIN STATION"]).children[0]
        picker._values = ["1"]
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        await picker.callback(interaction)
        self.cog.create_request.assert_awaited_once_with(interaction, {**self.request, "midpoint": "TRAIN STATION"})

    async def test_map_picker_opens_before_a_midpoint_has_been_selected(self):
        self.cog._map_catalogue = AsyncMock(return_value=[{"rcon_name": "carentan_warfare", "friendly_name": "Carentan"}])
        interaction = MagicMock()
        interaction.followup.send = AsyncMock()
        await self.cog.open_map_picker(interaction, server_name="server_2", server_label="Public")
        interaction.followup.send.assert_awaited_once()

    async def test_submission_preserves_midpoint_and_staff_embed_shows_it(self):
        self.cog._requests = {}
        self.cog._request_lock = asyncio.Lock()
        self.cog._approval_view = None
        channel = SimpleNamespace(id=10, send=AsyncMock(return_value=SimpleNamespace(id=20)))
        self.cog._get_channel = AsyncMock(return_value=channel)
        interaction = SimpleNamespace(user=SimpleNamespace(id=123), channel_id=99, followup=SimpleNamespace(send=AsyncMock()))
        data = {**self.request, "server_label": "Public", "friendly_name": "Carentan"}
        await self.cog.create_request(interaction, data)
        self.assertEqual(self.cog._requests["20"]["midpoint"], "TOWN CENTER")
        embed = channel.send.call_args.kwargs["embed"]
        self.assertIn("TOWN CENTER", {field.name: field.value for field in embed.fields}.values())

    async def test_forged_submission_never_reaches_staff(self):
        self.cog._get_channel = AsyncMock()
        interaction = SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()))
        await self.cog.create_request(interaction, {**self.request, "server_label": "Public", "midpoint": "FORGED"})
        self.cog._get_channel.assert_not_awaited()
        interaction.followup.send.assert_awaited_once()

    async def test_catalogue_matches_exact_variant_and_is_cached(self):
        self.cog._midpoint_options = EventMapRequests._midpoint_options.__get__(self.cog)
        self.cog._sector_lock = asyncio.Lock()
        self.backend.get_all_sector_options = AsyncMock(return_value=[{
            "variants": ["carentan_warfare", "carentan_warfare_night"], "sectors": sectors(),
        }])
        with patch("cogs.event_map_requests._read_json", return_value={}):
            self.assertEqual(await self.cog._midpoint_options(self.request), ["TOWN CENTER", "TRAIN STATION"])
        cached = {"fetched_at": 100, "maps": self.backend.get_all_sector_options.return_value}
        with patch("cogs.event_map_requests._read_json", return_value=cached), patch("cogs.event_map_requests.time.time", return_value=110):
            self.assertEqual(await self.cog._midpoint_options(self.request), ["TOWN CENTER", "TRAIN STATION"])
            self.assertEqual(await self.cog._midpoint_options({**self.request, "rcon_name": "unknown"}), [])
        self.backend.get_all_sector_options.assert_awaited_once()

    async def test_website_submission_passes_midpoint_to_validated_request_flow(self):
        self.cog._map_catalogue = AsyncMock(return_value=[{"rcon_name": "carentan_warfare", "friendly_name": "Carentan"}])
        self.cog.create_request = AsyncMock()
        service = FrontlineWeb(SimpleNamespace(get_cog=lambda _: self.cog))
        member = SimpleNamespace(guild=SimpleNamespace(), id=123)
        service._session_member = MagicMock(return_value=(None, member))
        request = SimpleNamespace(json=AsyncMock(return_value={
            "kind": "map", "server_name": "server_2", "rcon_name": "carentan_warfare", "midpoint": "TOWN CENTER",
        }))
        await service.submit_game_request(request)
        self.assertEqual(self.cog.create_request.call_args.args[1]["midpoint"], "TOWN CENTER")

    async def test_website_options_include_public_midpoints(self):
        self.cog._map_catalogue = AsyncMock(return_value=[{"rcon_name": "carentan_warfare", "friendly_name": "Carentan"}])
        service = FrontlineWeb(SimpleNamespace(get_cog=lambda _: self.cog))
        service._session_member = MagicMock(return_value=(None, SimpleNamespace()))
        response = await service.game_request_options(SimpleNamespace())
        self.assertEqual(json.loads(response.text)["maps"]["server_2"][0]["midpoints"], ["TOWN CENTER", "TRAIN STATION"])


class SectorBackendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = object.__new__(BifrostBackendClient)
        self.client.server_id = "public"
        self.client.game_type = "HLL"
        self.client._graphql = AsyncMock()

    async def test_set_layout_uses_documented_mutation_and_five_sector_names(self):
        self.client._graphql.return_value = {"guildSetSectorLayout": {"success": True}}
        names = ["RANDOM", "RANDOM", "TOWN CENTER", "RANDOM", "RANDOM"]
        await self.client.set_sector_layout("carentan_warfare", names)
        query, variables = self.client._graphql.call_args.args
        self.assertIn("GuildSetSectorLayoutInput!", query)
        self.assertEqual(variables, {"input": {"serverId": "public", "mapRconName": "carentan_warfare", "sectors": names}})

    async def test_failed_layout_is_not_reported_as_success(self):
        self.client._graphql.return_value = {"guildSetSectorLayout": {"success": False, "error": "UNKNOWN_OBJECTIVE"}}
        with self.assertRaises(HLLBackendError):
            await self.client.set_sector_layout("map", ["RANDOM"] * 5)

    async def test_catalogue_and_live_queries_use_correct_inputs(self):
        self.client._graphql.return_value = {"guildGetAllSectorOptions": {"success": True, "maps": []}}
        self.assertEqual(await self.client.get_all_sector_options(), [])
        self.assertEqual(self.client._graphql.call_args.args[1], {"gameType": "HLL"})
        self.client._graphql.return_value = {"guildGetSectorOptions": {"success": True, **live()}}
        self.assertEqual((await self.client.get_sector_options())["mapRconName"], "carentan_warfare")
        self.assertEqual(self.client._graphql.call_args.args[1], {"serverId": "public"})

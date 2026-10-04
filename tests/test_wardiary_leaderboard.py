import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import discord
import pytest

from cogs.wardiary import ALLOWED_ROLE_IDS, WAR_DIARY_FORUM_CHANNEL_ID, WarDiaryCog, _can_edit_member, _corrected_record
from cogs.frontline_web import FrontlineWeb
from discord.ext import commands
from war_diary_stats import clan_tag, leaderboard, parse_export, source_url


def match(**changes):
    return dict(thread_id=123, clan_name="7DR", opponent_clan_name="CROWS", match_date="20/08/26",
                map_name="Carentan", midpoint_name="TOWN CENTER", match_type="Competitive",
                played_as="Allies", allies_clan="7DR", axis_clan="CROWS", result="3-2",
                stats_link="https://stats.example.com/games/10", is_7dr_win=True, **changes)


def export(identity=10, players=None):
    return {"result": {"id": identity, "end": "2026-08-20", "player_stats": players or [
        {"player_id": "one", "player": "[7DR] Rat", "kills": 12, "kills_and_assists": 99, "deaths": 3, "time_seconds": 600},
        {"player_id": "two", "player": "[CROWS] Crow", "kills": 99, "deaths": 2, "time_seconds": 600},
    ]}}


def imported(link="https://stats.example.com/games/10", **changes):
    source = source_url(link)
    return source, {"rows": parse_export(export(), source), "updated_at": "2026-08-20T20:00:00+00:00", **changes}


def test_parser_uses_raw_kills_and_validates_finished_correct_match():
    source = source_url(match()["stats_link"])
    assert parse_export(export(), source)[0]["kills"] == 12
    for payload in (export(identity=11), {"result": {"id": 10, "end": None}}, {**export(), "rosterComplete": False}):
        with pytest.raises(ValueError):
            parse_export(payload, source)
    payload = export()
    payload["result"]["player_stats"].append(payload["result"]["player_stats"][0])
    with pytest.raises(ValueError):
        parse_export(payload, source)
    for value in (-1, True, "12"):
        payload = export()
        payload["result"]["player_stats"][0]["kills"] = value
        with pytest.raises(ValueError):
            parse_export(payload, source)


def test_links_normalize_crcon_legacy_and_bifrost_match_exports():
    assert source_url("https://stats.example.com/games/10/charts") == source_url("https://stats.example.com/api/get_map_scoreboard?map_id=10")
    assert source_url("https://7dr-stats.hlladmin.com/games/10") == "https://7drhistostats.hllfrontline.com/api/get_map_scoreboard?map_id=10"
    for base in ("https://bifroststats.com/hll/match/543d32e8-5448-5013-9199-da2821df070b", "https://frostbite.bifrostgaming.com/hll/driel_warfare/2b54970e-e27e-5421-9e92-31e3b7e9edbd"):
        assert source_url(base) == source_url(base + "/crcon") == base + "/crcon"
    assert source_url("https://frostbite.bifrostgaming.com/hll/leaderboards/servers/abc/games/10") == "https://frostbite.bifrostgaming.com/hll/leaderboards/servers/abc/crcon/api/get_map_scoreboard?map_id=10"
    for link in ("http://127.0.0.1/games/10", "http://169.254.169.254/games/10", "https://user:pass@stats.example.com/games/10", "https://example.com", "https://bifroststats.com.evil/hll/match/543d32e8-5448-5013-9199-da2821df070b"):
        with pytest.raises(ValueError):
            source_url(link)


def test_leaderboard_clan_filter_stable_identity_ties_and_updated_name():
    source, saved = imported()
    second = source_url("https://stats.example.com/games/11")
    newest = {"rows": [dict(player_id="one", name="New name", kills=8, deaths=1, seconds=100),
                       dict(player_id="three", name="[7DR] Other", kills=20, deaths=0, seconds=100)],
              "updated_at": "2026-08-21T20:00:00+00:00"}
    records = [match(), {**match(), "thread_id": 124, "stats_link": "https://stats.example.com/games/11"}]
    board = leaderboard(records, {source: saved, second: newest})
    assert [row["rank"] for row in board["rows"]] == [1, 1]
    rat = next(row for row in board["rows"] if row["player_id"] == "one")
    assert (rat["name"], rat["kills"], rat["matches"], rat["kd"], rat["kills_per_match"]) == ("New name", 20, 2, 5, 10)
    assert not any(row["player_id"] == "two" for row in board["rows"])
    assert next(row for row in board["rows"] if row["player_id"] == "three")["kd"] is None
    assert len(leaderboard([match()], {source: saved}, known_ids={"two"})["rows"]) == 2


def test_duplicate_links_not_counted_and_corrections_remove_old_totals():
    source, saved = imported(error="HTTP 503")
    assert leaderboard([match()], {source: saved})["stale"] == 1
    duplicate = leaderboard([match(), {**match(), "thread_id": 124}], {source: saved})
    assert (duplicate["imported"], duplicate["duplicate_links"], duplicate["rows"]) == (0, 2, [])
    changed = {**match(), "stats_link": "https://stats.example.com/games/11"}
    assert leaderboard([changed], {source: saved})["rows"] == []
    assert leaderboard([], {source: saved})["rows"] == []
    coverage = leaderboard([{**match(), "stats_link": None}, {**match(), "stats_link": "https://example.com/server"}, match()], {})
    assert (coverage["missing_links"], coverage["unsupported_links"], coverage["pending"]) == (1, 1, 1)


def test_corrected_record_recalculates_result_and_sides_without_mutating_original():
    original = match()
    updated = _corrected_record(original, {"result": "1:4", "played_as": "Axis", "stats_link": None, "opponent_clan_name": " TAFF "})
    assert (updated["result"], updated["is_7dr_win"], updated["allies_clan"], updated["axis_clan"], updated["stats_link"]) == ("1-4", False, "TAFF", "7DR", None)
    assert original["result"] == "3-2"
    for changes in ({"match_date": "31/02/26"}, {"result": "7-1"}, {"played_as": "Unknown"}, {"map_name": "Unknown"}, {"stats_link": "file:///secret"}):
        with pytest.raises(ValueError):
            _corrected_record(original, changes)


def test_edit_permissions_require_staff_or_configured_diary_role():
    def member(admin=False, manage=False, roles=()):
        return SimpleNamespace(guild_permissions=SimpleNamespace(administrator=admin, manage_guild=manage), roles=[SimpleNamespace(id=value) for value in roles])
    assert not _can_edit_member(member())
    assert _can_edit_member(member(admin=True))
    assert _can_edit_member(member(manage=True))
    assert _can_edit_member(member(roles=[ALLOWED_ROLE_IDS[0]]))


def make_editor():
    cog = object.__new__(WarDiaryCog)
    cog._state = {"match_threads": [match()]}
    cog._match_lock = asyncio.Lock()
    cog._hydrate_export_record = AsyncMock(return_value=False)
    cog._render_result_image = MagicMock(return_value=(b"image", ".png"))
    cog._save_state = MagicMock(return_value=True)
    cog._get_or_create_forum_tag = AsyncMock(return_value=None)
    cog.bot = SimpleNamespace(frontline_web=SimpleNamespace(_dashboard_cache=(10, b"old")))
    original = discord.Embed()
    original.add_field(name="Submitted by", value="<@321>")
    starter = SimpleNamespace(embeds=[original], edit=AsyncMock())
    thread = SimpleNamespace(id=123, parent_id=WAR_DIARY_FORUM_CHANNEL_ID, guild=SimpleNamespace(id=1),
                             parent=SimpleNamespace(), archived=False, locked=False, name="Old title", applied_tags=[],
                             mention="<#123>", fetch_message=AsyncMock(return_value=starter), edit=AsyncMock())
    cog._get_thread = AsyncMock(return_value=thread)
    interaction = SimpleNamespace(guild=thread.guild, user=SimpleNamespace(id=456, display_name="Editor", mention="<@456>"))
    return cog, interaction, thread, starter


def test_edit_updates_saved_entry_post_image_content_and_website_cache():
    async def run():
        cog, interaction, thread, starter = make_editor()
        message = await cog._edit_entry(interaction, 123, {"result": "0-5", "map_name": "Foy", "midpoint_name": "WEST BEND", "played_as": "Axis"})
        record = cog._get_match_records()[0]
        assert (record["result"], record["axis_clan"], record["map_name"], record["edited_by"]) == ("0-5", "7DR", "Foy", 456)
        embed = starter.edit.call_args.kwargs["embed"]
        assert embed.fields[0].value == "<@321>"
        assert "0-5" in embed.description and "WEST BEND" in embed.description
        assert starter.edit.call_args.kwargs["attachments"][0].filename == "wardiary_0_5.png"
        assert cog.bot.frontline_web._dashboard_cache == (0, None)
        cog._save_state.assert_called_once()
        assert "Corrected" in message
    asyncio.run(run())


def test_edit_prevents_duplicate_match_and_wrong_forum():
    async def run():
        cog, interaction, thread, starter = make_editor()
        cog._state["match_threads"].append({**match(), "thread_id": 456, "opponent_clan_name": "TAFF"})
        with pytest.raises(ValueError, match="Another entry"):
            await cog._edit_entry(interaction, 123, {"opponent_clan_name": "TAFF"})
        starter.edit.assert_not_awaited()
        thread.parent_id = 999
        with pytest.raises(ValueError, match="War Diary forum"):
            await cog._edit_entry(interaction, 123, {"result": "0-5"})
    asyncio.run(run())


def test_failed_post_edit_keeps_record_and_restores_title():
    async def run():
        cog, interaction, thread, starter = make_editor()
        starter.edit.side_effect = RuntimeError("failed edit")
        with pytest.raises(RuntimeError):
            await cog._edit_entry(interaction, 123, {"result": "0-5"})
        assert cog._get_match_records()[0]["result"] == "3-2"
        assert thread.edit.call_args == call(name="Old title", applied_tags=[])
        cog._save_state.assert_not_called()
    asyncio.run(run())


def test_archived_entry_is_rearchived_after_correction():
    async def run():
        cog, interaction, thread, starter = make_editor()
        thread.archived, thread.locked = True, True
        await cog._edit_entry(interaction, 123, {"result": "0-5"})
        assert thread.edit.call_args_list[0] == call(archived=False, locked=False)
        assert thread.edit.call_args_list[-1] == call(archived=True, locked=True)
    asyncio.run(run())


def test_slash_edit_requires_authorisation_and_passes_only_requested_fields():
    async def run():
        cog = object.__new__(WarDiaryCog)
        cog._edit_entry = AsyncMock(return_value="Corrected")
        user = MagicMock(spec=discord.Member)
        user.guild_permissions = SimpleNamespace(administrator=False, manage_guild=False)
        user.roles = []
        interaction = SimpleNamespace(user=user, channel_id=123, guild_id=1,
                                      response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
                                      followup=SimpleNamespace(send=AsyncMock()))
        await WarDiaryCog.wardiary_edit.callback(cog, interaction, result="0-5")
        cog._edit_entry.assert_not_awaited()
        user.guild_permissions.manage_guild = True
        await WarDiaryCog.wardiary_edit.callback(cog, interaction, entry="https://discord.com/channels/1/123", result="0-5")
        cog._edit_entry.assert_awaited_once_with(interaction, 123, {"result": "0-5"})
        cog._edit_entry.reset_mock()
        await WarDiaryCog.wardiary_edit.callback(cog, interaction, entry="https://discord.com/channels/999/123", result="0-5")
        cog._edit_entry.assert_not_awaited()
    asyncio.run(run())


def test_import_worker_caches_success_and_preserves_last_success_after_failure():
    async def run():
        cog, _, _, _ = make_editor()
        cog._stats_imports = {}
        source = source_url(match()["stats_link"])
        with patch("war_diary_stats.fetch_export", new=AsyncMock(return_value=parse_export(export(), source))) as fetch, patch("cogs.wardiary.atomic_json_dump"):
            await cog.import_match_stats()
            await cog.import_match_stats()
            assert fetch.await_count == 1
        cog._stats_imports[source]["attempted_at"] = "2020-01-01T00:00:00+00:00"
        with patch("war_diary_stats.fetch_export", new=AsyncMock(side_effect=ValueError("HTTP 503"))), patch("cogs.wardiary.atomic_json_dump"):
            await cog.import_match_stats()
        assert cog._stats_imports[source]["error"] == "HTTP 503"
        assert leaderboard(cog._get_match_records(), cog._stats_imports)["rows"][0]["kills"] == 12
        assert leaderboard(cog._get_match_records(), cog._stats_imports)["stale"] == 1
    asyncio.run(run())


def test_slash_leaderboard_has_pagination_and_coverage():
    async def run():
        cog = object.__new__(WarDiaryCog)
        source, saved = imported()
        cog.get_kills_leaderboard = lambda: leaderboard([match()], {source: saved})
        interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))
        await WarDiaryCog.wardiary_leaderboard.callback(cog, interaction)
        embed = interaction.response.send_message.call_args.kwargs["embed"]
        assert "12 kills" in embed.description and "1/1 matches imported" in embed.footer.text
    asyncio.run(run())


def test_website_war_diary_includes_the_same_cached_leaderboard():
    cog, _, _, _ = make_editor()
    source, saved = imported()
    cog.get_kills_leaderboard = lambda: leaderboard(cog._get_match_records(), {source: saved})
    payload = FrontlineWeb._war_diary_payload(cog)
    assert payload["kills_leaderboard"]["rows"][0]["kills"] == 12
    assert payload["kills_leaderboard"]["imported"] == 1
    assert FrontlineWeb._war_diary_payload(None)["kills_leaderboard"]["rows"] == []


def test_new_slash_commands_register_and_stats_worker_stops_on_unload():
    async def run():
        async with commands.Bot(command_prefix="!", intents=discord.Intents.none()) as bot:
            with patch.object(WarDiaryCog, "_load_state", return_value={}):
                cog = WarDiaryCog(bot)
            await bot.add_cog(cog)
            serialized = {command.name: command.to_dict() for command in bot.tree.get_commands()}
            assert "wardiary_leaderboard" in serialized
            options = serialized["wardiary_edit"]["options"]
            assert {"entry", "stats_link", "clear_stats_link", "result", "played_as"}.issubset({option["name"] for option in options})
            task = cog.import_match_stats.get_task()
            await bot.remove_cog("WarDiaryCog")
            try:
                await task
            except asyncio.CancelledError:
                pass
            assert not cog.import_match_stats.is_running()
    asyncio.run(run())

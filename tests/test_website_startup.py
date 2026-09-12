import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from cogs.frontline_web import FrontlineWeb
from cogs.multi_trainee_tracker import MultiTraineeTracker, TRACKS


def test_web_service_registers_routes_and_starts(monkeypatch):
    service = FrontlineWeb(SimpleNamespace())
    service._app_pin = "test-pin-only"
    service._turnstile_secret_key = "test-secret-only"
    runner = SimpleNamespace(setup=AsyncMock(), cleanup=AsyncMock())
    site = SimpleNamespace(start=AsyncMock())
    applications = []

    def make_runner(app, **kwargs):
        applications.append(app)
        return runner

    monkeypatch.setattr("cogs.frontline_web.web.AppRunner", make_runner)
    monkeypatch.setattr("cogs.frontline_web.web.TCPSite", lambda *args: site)
    asyncio.run(service.start())
    routes = {(route.method, route.resource.canonical) for route in applications[0].router.routes()}
    assert ("GET", "/api/knowledge-base") in routes
    assert ("GET", "/api/game-request-options") in routes
    assert ("POST", "/api/game-requests") in routes
    site.start.assert_awaited_once()


def test_trainees_work_without_any_discord_channels():
    bot = MagicMock()
    tracker = MultiTraineeTracker(bot)
    member = SimpleNamespace(
        id=1, display_name="Trainee", name="trainee", joined_at=None,
        roles=[SimpleNamespace(id=TRACKS[0].trainee_role_id)],
    )
    rows = tracker._collect_rows(SimpleNamespace(members=[member]), TRACKS[0])
    assert rows[0]["display_name"] == "Trainee"
    assert "Trainee" in tracker._render_html(TRACKS[0], rows)
    assert tracker.get_listeners() == []
    assert bot.mock_calls == []

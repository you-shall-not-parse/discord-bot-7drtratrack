import asyncio
import json
from types import SimpleNamespace

from cogs.frontline_web import FrontlineWeb, SESSION_COOKIE


def test_selected_member_survives_refresh_and_drives_request_identity(monkeypatch):
    now = 1000
    monkeypatch.setattr("cogs.frontline_web.time.time", lambda: now)
    member = SimpleNamespace(id=123, name="discordname", display_name="Discord Nick", bot=False)
    guild = SimpleNamespace(get_member=lambda member_id: member if member_id == 123 else None, members=[])
    service = FrontlineWeb(SimpleNamespace(get_guild=lambda _: guild))
    token = service._new_session("Different login name")

    async def body():
        return {"member_id": "123"}

    request = SimpleNamespace(cookies={SESSION_COOKIE: token}, headers={"X-Requested-With": "HLLFrontline"}, json=body)
    response = asyncio.run(service.select_session_member(request))
    assert response.status == 200
    assert json.loads(response.text)["member"]["id"] == "123"
    now += 5
    assert service._valid_session(token)
    assert service._sessions[token].member_id == 123
    assert service._sessions[token].claimed_name == "Different login name"
    assert service._session_member(request)[1] is member
    guild.get_member = lambda _: None
    assert service._session_member(request)[1] is None


def test_invalid_member_and_cross_site_selection_do_not_change_session():
    service = FrontlineWeb(SimpleNamespace(get_guild=lambda _: SimpleNamespace(get_member=lambda _: None)))
    token = service._new_session("Member")

    async def body():
        return {"member_id": "999"}

    request = SimpleNamespace(cookies={SESSION_COOKIE: token}, headers={}, json=body)
    assert asyncio.run(service.select_session_member(request)).status == 403
    request.headers["X-Requested-With"] = "HLLFrontline"
    assert asyncio.run(service.select_session_member(request)).status == 400
    assert service._sessions[token].member_id is None
    request.cookies = {}
    assert asyncio.run(service.select_session_member(request)).status == 401

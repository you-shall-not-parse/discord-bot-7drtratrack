import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from cogs.frontline_web import (
    ACTIVE_SESSION_WINDOW_SECONDS,
    ADMIN_SESSION_COOKIE,
    DASHBOARD_CACHE_SECONDS,
    EXTERNAL_LINKS,
    FRONTEND_DIR,
    HIGHLIGHTS_CHANNEL_ID,
    MAP_IMAGES_DIR,
    MAX_ACTIVE_SESSIONS,
    SESSION_SECONDS,
    TURNSTILE_SITE_KEY,
    WEBSITE_BACKGROUND_PATH,
    FrontlineWeb,
)
from cogs.wardiary import WAR_DIARY_MAP_IMAGE_FILES
from config import WEB_LOG_PATH


def test_frontend_assets_exist_and_are_wired() -> None:
    index = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    css = (FRONTEND_DIR / "app.css").read_text(encoding="utf-8")
    javascript = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
    report = (FRONTEND_DIR / "report.html").read_text(encoding="utf-8")
    report_javascript = (FRONTEND_DIR / "report.js").read_text(encoding="utf-8")

    login = (FRONTEND_DIR / "login.html").read_text(encoding="utf-8")

    admin = (FRONTEND_DIR / "admin.html").read_text(encoding="utf-8")

    assert '<link rel="stylesheet" href="/assets/app.css?v=14">' in index
    assert '<link rel="stylesheet" href="/assets/app.css?v=14">' in login
    assert '<link rel="stylesheet" href="/assets/app.css?v=14">' in report
    assert '<link rel="stylesheet" href="/assets/app.css?v=14">' in admin
    assert '<script defer src="/assets/app.js?v=10"></script>' in index
    assert 'src="/assets/emblem_7dr.png"' in index
    assert "7th Armoured Division" in index
    assert "<dialog" not in index
    assert "--olive:" in css
    assert "[hidden] { display: none !important; }" in css
    assert 'fetch("/api/dashboard"' in javascript
    assert 'fetch(`/api/hllv-search?q=' in javascript
    assert "AbortSignal.timeout(20_000)" in javascript
    assert 'rel="noopener noreferrer"' in javascript
    assert 'data-view="overview"' in index
    assert 'data-view="server-status"' in index
    assert 'data-view="upcoming"' in index
    assert 'data-view="war-diary"' in index
    assert 'data-view="highlights"' in index
    assert 'id="highlight-grid"' in index
    assert 'data-view="community"' not in index
    assert 'id="server-grid"' in index
    assert 'src="/assets/report.js?v=5"' in report
    assert 'href="/rollcalls/${encodeURIComponent(rollcall.key)}"' in javascript
    assert 'href="/trainees/${encodeURIComponent(track.key)}"' in javascript
    assert 'label: "Current status"' in report_javascript
    assert 'label: "Qualifications"' in report_javascript
    assert 'key: "current_status"' in report_javascript
    assert 'key: "missed_streak"' in report_javascript
    assert 'class="trainee-name"' in report_javascript
    assert 'data-sort=' in report_javascript
    assert 'Open HTML' in report_javascript
    assert 'Download Excel' in report_javascript
    assert 'name="pin"' in login
    assert 'name="name"' in login
    assert 'maxlength="80"' in login
    assert "admin PIN" not in login
    assert "not required for admin" not in login
    assert "{{TURNSTILE_HEAD}}" in login
    assert "{{TURNSTILE_WIDGET}}" in login
    assert 'method="post" action="/login"' in login
    assert 'method="post" action="/admin/logout"' in admin
    assert 'name="name" type="text" minlength="1" maxlength="80" autocomplete="name" autofocus>' in login
    assert 'action="/logout"' in index
    assert "function resultCards(rows)" in javascript
    assert "function renderHighlights()" in javascript
    assert 'loading="lazy"' in javascript
    assert 'preload="metadata"' in javascript
    assert "statsDate(row.date, row.stats_url)" in javascript
    assert "data-map-image" in javascript
    assert ".summary-strip { grid-template-columns: repeat(3, 1fr);" in css
    assert ".match-result-card" in css
    assert '["kofi", "Support us on Ko-fi"' in javascript
    assert 'url("/assets/website_backg.webp?v=1")' in css
    assert "background-position: 72% center" in css
    assert "background-size: auto 100%" in css
    assert "background-attachment: fixed" not in css
    assert "position: absolute; bottom: auto; height: 100svh" in css
    assert "Matches, server activity, clan records and personnel readiness" not in index


def test_optimized_war_diary_map_card_assets_exist() -> None:
    paths = [MAP_IMAGES_DIR / Path(filename).with_suffix(".webp") for filename in WAR_DIARY_MAP_IMAGE_FILES.values()]

    assert all(path.is_file() for path in paths)
    assert sum(path.stat().st_size for path in paths) < 2 * 1024 * 1024


def test_quick_link_and_optimized_website_background_are_configured() -> None:
    assert EXTERNAL_LINKS["kofi"] == "https://ko-fi.com/7tharmoureddivisonclan"
    assert WEBSITE_BACKGROUND_PATH.is_file()
    assert WEBSITE_BACKGROUND_PATH.suffix == ".webp"
    assert WEBSITE_BACKGROUND_PATH.stat().st_size < 200 * 1024


def test_pin_sessions_are_random_and_open_redirects_are_rejected(monkeypatch) -> None:
    monkeypatch.setenv("APPPIN", "a-long-test-pin")
    service = FrontlineWeb(SimpleNamespace())
    token = service._new_session("Example User")

    assert token is not None
    assert service._valid_session(token)
    assert not service._valid_session(token + "tampered")
    assert service._safe_next("/rollcalls/22nd") == "/rollcalls/22nd"
    assert service._safe_next("//example.com") == "/"
    assert service._safe_next("https://example.com") == "/"
    assert service._sessions[token].claimed_name == "Example User"


def test_report_pages_are_not_browser_cached() -> None:
    service = FrontlineWeb(SimpleNamespace())

    async def handler(_request):
        return SimpleNamespace(headers={})

    for path in ("/rollcalls/22nd", "/trainees/recruits"):
        request = SimpleNamespace(secure=False, headers={}, path=path)
        response = asyncio.run(service._security_headers(request, handler))
        assert response.headers["Cache-Control"] == "no-store"
        assert "media-src 'self' https://cdn.discordapp.com" in response.headers["Content-Security-Policy"]


def test_highlight_message_keeps_direct_discord_images_and_videos_only() -> None:
    class Attachment(SimpleNamespace):
        def is_spoiler(self):
            return bool(getattr(self, "spoiler", False))

    attachments = [
        Attachment(filename="victory.png", content_type="image/png", url="https://cdn.discordapp.com/attachments/1/2/victory.png", width=1920, height=1080),
        Attachment(filename="clip.mp4", content_type="video/mp4", url="https://media.discordapp.net/attachments/1/2/clip.mp4", width=1280, height=720),
        Attachment(filename="notes.txt", content_type="text/plain", url="https://cdn.discordapp.com/attachments/1/2/notes.txt"),
        Attachment(filename="hidden.jpg", content_type="image/jpeg", url="https://cdn.discordapp.com/attachments/1/2/hidden.jpg", spoiler=True),
        Attachment(filename="outside.jpg", content_type="image/jpeg", url="https://example.com/outside.jpg"),
    ]
    message = SimpleNamespace(
        id=456,
        attachments=attachments,
        author=SimpleNamespace(display_name="Example Member", display_avatar=SimpleNamespace(url="https://cdn.discordapp.com/avatars/1/a.png")),
        clean_content="A strong finish",
        created_at=datetime(2026, 9, 2, 18, 30, tzinfo=timezone.utc),
        jump_url="https://discord.com/channels/1/2/456",
    )

    post = FrontlineWeb._highlight_from_message(message, guild_id=1, channel_id=HIGHLIGHTS_CHANNEL_ID)

    assert post is not None
    assert post["author"] == "Example Member"
    assert post["caption"] == "A strong finish"
    assert [item["kind"] for item in post["media"]] == ["image", "video"]
    assert post["url"] == "https://discord.com/channels/1/2/456"


def test_highlights_are_cached_between_dashboard_builds(monkeypatch) -> None:
    now = 1_000.0
    monkeypatch.setattr("cogs.frontline_web.time.monotonic", lambda: now)
    service = FrontlineWeb(SimpleNamespace())
    calls = 0

    async def read_highlights():
        nonlocal calls
        calls += 1
        return [{"id": str(calls)}]

    service._read_discord_highlights = read_highlights

    first = asyncio.run(service._highlights_payload())
    second = asyncio.run(service._highlights_payload())

    assert first == second == [{"id": "1"}]
    assert calls == 1


def test_active_session_limit_rejects_login_until_sessions_expire(monkeypatch) -> None:
    now = 1_000
    monkeypatch.setattr("cogs.frontline_web.time.time", lambda: now)
    service = FrontlineWeb(SimpleNamespace())

    tokens = [service._new_session(f"User {index}") for index in range(MAX_ACTIVE_SESSIONS)]

    assert all(token is not None for token in tokens)
    assert len(service._sessions) == MAX_ACTIVE_SESSIONS
    assert service._new_session("One too many") is None

    now += SESSION_SECONDS + 1
    replacement = service._new_session("Replacement user")

    assert replacement is not None
    assert len(service._sessions) == 1


def test_login_returns_capacity_page_when_session_limit_is_reached(monkeypatch) -> None:
    monkeypatch.setenv("APPPIN", "a-long-test-pin")
    monkeypatch.delenv("TURNSTILE_SECRET", raising=False)
    monkeypatch.delenv("TURNSTILE_SECRET_KEY", raising=False)
    service = FrontlineWeb(SimpleNamespace())
    for index in range(MAX_ACTIVE_SESSIONS):
        assert service._new_session(f"User {index}") is not None

    async def post() -> dict[str, str]:
        return {"name": "Waiting User", "pin": "a-long-test-pin", "next": "/"}

    request = SimpleNamespace(query={}, headers={}, remote="192.0.2.1", post=post)
    response = asyncio.run(service.login(request))

    assert response.status == 503
    assert b"limit of 300 active logins" in response.body
    assert len(service._sessions) == MAX_ACTIVE_SESSIONS


def test_admin_pin_opens_admin_without_using_a_user_session(monkeypatch) -> None:
    monkeypatch.setenv("APPPIN", "a-long-test-pin")
    monkeypatch.setenv("FRONTLINE_ADMIN_PIN", "crumpadmin!")
    monkeypatch.delenv("TURNSTILE_SECRET", raising=False)
    monkeypatch.delenv("TURNSTILE_SECRET_KEY", raising=False)
    service = FrontlineWeb(SimpleNamespace())

    async def post() -> dict[str, str]:
        return {"name": "", "pin": "crumpadmin!", "next": "/"}

    request = SimpleNamespace(query={}, headers={}, remote="192.0.2.1", post=post)
    response = asyncio.run(service.login(request))

    assert response.status == 303
    assert response.headers["Location"] == "/admin"
    assert ADMIN_SESSION_COOKIE in response.cookies
    assert not service._sessions
    assert len(service._admin_sessions) == 1


def test_admin_page_reports_activity_and_escapes_claimed_names(monkeypatch) -> None:
    now = 1_000
    monkeypatch.setattr("cogs.frontline_web.time.time", lambda: now)
    service = FrontlineWeb(SimpleNamespace())
    unsafe_token = service._new_session("<script>alert(1)</script>")
    active_token = service._new_session("Active User")
    assert unsafe_token is not None and active_token is not None

    now += ACTIVE_SESSION_WINDOW_SECONDS + 1
    assert service._valid_session(active_token)
    response = asyncio.run(service.admin_page(None))
    document = response.text

    assert "<strong>1</strong><small>Active now</small>" in document
    assert "<strong>2</strong><small>Logged-in sessions</small>" in document
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in document
    assert "<script>alert(1)</script>" not in document


def test_admin_route_requires_a_separate_admin_session() -> None:
    service = FrontlineWeb(SimpleNamespace())
    calls = 0

    async def handler(_request):
        nonlocal calls
        calls += 1
        return "allowed"

    anonymous = SimpleNamespace(raw_path="/admin", path="/admin", cookies={}, rel_url="/admin")
    denied = asyncio.run(service._pin_auth(anonymous, handler))
    assert denied.status == 303
    assert denied.headers["Location"] == "/login?next=/admin"
    assert calls == 0

    token = service._new_admin_session()
    authorised = SimpleNamespace(
        raw_path="/admin",
        path="/admin",
        cookies={ADMIN_SESSION_COOKIE: token},
        rel_url="/admin",
    )
    assert asyncio.run(service._pin_auth(authorised, handler)) == "allowed"
    assert calls == 1


def test_dashboard_payload_is_cached_for_30_seconds(monkeypatch) -> None:
    service = FrontlineWeb(SimpleNamespace())
    now = 100.0
    builds = 0

    monkeypatch.setattr("cogs.frontline_web.time.monotonic", lambda: now)

    async def build_payload() -> dict[str, int]:
        nonlocal builds
        builds += 1
        return {"build": builds}

    service._build_dashboard_payload = build_payload

    async def exercise_cache() -> None:
        nonlocal now
        first = await service.dashboard(None)
        now += DASHBOARD_CACHE_SECONDS - 1
        second = await service.dashboard(None)
        now += 1
        third = await service.dashboard(None)

        assert json.loads(first.body) == {"build": 1}
        assert json.loads(second.body) == {"build": 1}
        assert json.loads(third.body) == {"build": 2}

    asyncio.run(exercise_cache())
    assert builds == 2


def test_concurrent_dashboard_requests_share_one_build() -> None:
    service = FrontlineWeb(SimpleNamespace())
    builds = 0

    async def build_payload() -> dict[str, int]:
        nonlocal builds
        builds += 1
        await asyncio.sleep(0)
        return {"build": builds}

    service._build_dashboard_payload = build_payload

    async def exercise_cache() -> None:
        responses = await asyncio.gather(*(service.dashboard(None) for _ in range(20)))

        assert all(json.loads(response.body) == {"build": 1} for response in responses)

    asyncio.run(exercise_cache())
    assert builds == 1


def test_login_name_is_normalised_and_rejects_log_injection() -> None:
    assert FrontlineWeb._normalise_login_name("  Example   User  ") == "Example User"
    assert FrontlineWeb._normalise_login_name("") is None
    assert FrontlineWeb._normalise_login_name("Example\nForged log line") is None
    assert FrontlineWeb._normalise_login_name("x" * 81) is None
    assert WEB_LOG_PATH.endswith("bot_web.log")


def test_successful_login_operator_notice_uses_the_requested_message(caplog) -> None:
    FrontlineWeb._operator_login_notice("Example User")

    assert "Example User has logged into your website!" in caplog.text


def test_turnstile_result_requires_success_matching_hostname_and_login_action() -> None:
    valid = {"success": True, "hostname": "hllfrontline.com", "action": "login"}

    assert FrontlineWeb._valid_turnstile_result(valid, "hllfrontline.com")
    assert not FrontlineWeb._valid_turnstile_result({**valid, "success": False}, "hllfrontline.com")
    assert not FrontlineWeb._valid_turnstile_result({**valid, "hostname": "example.com"}, "hllfrontline.com")
    assert not FrontlineWeb._valid_turnstile_result({**valid, "action": "other"}, "hllfrontline.com")
    assert TURNSTILE_SITE_KEY == "0x4AAAAAAEjLElnYaILQaVYk"


def test_sensitive_file_probes_are_rejected_before_login() -> None:
    assert FrontlineWeb._is_sensitive_path("/.env")
    assert FrontlineWeb._is_sensitive_path("/config/.env.backup")
    assert FrontlineWeb._is_sensitive_path("/%2e%2egit/HEAD")
    assert FrontlineWeb._is_sensitive_path("/.git/HEAD")
    assert FrontlineWeb._is_sensitive_path("/wp-config.php")
    assert not FrontlineWeb._is_sensitive_path("/assets/app.css")
    assert not FrontlineWeb._is_sensitive_path("/rollcalls/22nd")


def test_rollcall_status_is_normalised_for_the_public_api() -> None:
    assert FrontlineWeb._normalise_rollcall_status("✅") == "attending"
    assert FrontlineWeb._normalise_rollcall_status("🅾️") == "other-rollcall"
    assert FrontlineWeb._normalise_rollcall_status("❌") == "missing"
    assert FrontlineWeb._normalise_rollcall_status("") == "missing"


def test_rollcall_rank_uses_the_clan_hierarchy() -> None:
    lieutenant = SimpleNamespace(display_name="Lt. Example", roles=[])
    private = SimpleNamespace(display_name="Private Example", roles=[])
    no_rank = SimpleNamespace(display_name="Example", roles=[SimpleNamespace(name="Infantry")])
    role_rank = SimpleNamespace(display_name="Example", roles=[SimpleNamespace(name="Major")])

    assert FrontlineWeb._member_rank(lieutenant)[0] == "LT"
    assert FrontlineWeb._member_rank(private)[0] == "PTE"
    assert FrontlineWeb._member_rank(lieutenant)[1] < FrontlineWeb._member_rank(private)[1]
    assert FrontlineWeb._member_rank(no_rank)[0] == "Unranked"
    assert FrontlineWeb._member_rank(role_rank)[0] == "MAJ"
    assert FrontlineWeb._member_rank(None)[0] == "Former member"


def test_rollcall_membership_uses_current_tracked_role_membership() -> None:
    member = SimpleNamespace()

    assert FrontlineWeb._rollcall_member_state(1, {1}, member, False) == (True, [])
    assert FrontlineWeb._rollcall_member_state(1, set(), member, False) == (False, ["ARCHIVED"])
    assert FrontlineWeb._rollcall_member_state(1, {1}, member, True) == (False, ["FORMER"])
    assert FrontlineWeb._rollcall_member_state(1, {1}, None, True) == (False, ["LEFT"])


def test_missed_rollcall_streak_counts_only_consecutive_explicit_misses() -> None:
    weeks = ["W01 05/01/2026", "W02 12/01/2026", "W03 19/01/2026", "W04 26/01/2026"]

    assert FrontlineWeb._missed_rollcall_streak(
        {weeks[0]: "✅", weeks[1]: "✅", weeks[2]: "❌", weeks[3]: "❌"}, weeks
    ) == 2
    assert FrontlineWeb._missed_rollcall_streak(
        {weeks[0]: "❌", weeks[1]: "❌", weeks[2]: "✅", weeks[3]: "❌"}, weeks
    ) == 1
    assert FrontlineWeb._missed_rollcall_streak(
        {weeks[0]: "❌", weeks[1]: "❌", weeks[2]: "❌", weeks[3]: "🅾️"}, weeks
    ) == 0


def test_dashboard_event_payload_only_includes_current_upcoming_events() -> None:
    now = datetime.now(timezone.utc)
    upcoming = SimpleNamespace(
        name="7DR v Example",
        status=SimpleNamespace(name="scheduled"),
        start_time=now + timedelta(days=1),
        end_time=now + timedelta(days=1, hours=2),
        location="7DR Events Server",
        url="https://discord.com/events/1/2",
        user_count=12,
    )
    completed = SimpleNamespace(
        name="Old match",
        status=SimpleNamespace(name="completed"),
        start_time=now - timedelta(days=1),
    )

    payload = FrontlineWeb._event_payload(SimpleNamespace(scheduled_events=[completed, upcoming]))

    assert len(payload) == 1
    assert payload[0]["name"] == "7DR v Example"
    assert payload[0]["interested"] == 12


def test_war_diary_builds_overall_and_opponent_records() -> None:
    cog = SimpleNamespace(
        _get_match_records=lambda: [
            {
                "opponent_clan_name": "Example",
                "match_date": "01/09/26",
                "map_name": "Carentan",
                "stats_link": "https://stats.example.test/games/123",
                "result": "5-2",
            },
            {
                "opponent_clan_name": "example",
                "match_date": "02/09/26",
                "map_name": "Kharkov",
                "stats_link": "javascript:alert(1)",
                "result": "1-3",
            },
            {"opponent_clan_name": "Another", "match_date": "03/09/26", "map_name": "Omaha", "result": "3-3"},
        ]
    )

    payload = FrontlineWeb._war_diary_payload(cog)

    assert payload["summary"] == {"played": 3, "wins": 1, "losses": 1}
    assert payload["opponents"][0] == {"name": "Example", "played": 2, "wins": 1, "losses": 1, "draws": 0}
    assert payload["recent"][0]["opponent"] == "Another"
    carentan = next(match for match in payload["recent"] if match["map"] == "Carentan")
    kharkov = next(match for match in payload["recent"] if match["map"] == "Kharkov")
    assert carentan["map_image"] == "/assets/maps/Carentan.webp"
    assert carentan["stats_url"] == "https://stats.example.test/games/123"
    assert kharkov["stats_url"] == ""


def test_war_diary_returns_every_match_and_keeps_legacy_outcomes() -> None:
    records = [
        {
            "opponent_clan_name": f"Clan {day}",
            "match_date": f"{day:02d}/08/26",
            "map_name": "Carentan",
            "result": "3-2",
        }
        for day in range(1, 16)
    ]
    records.append(
        {
            "opponent_clan_name": "Legacy Clan",
            "match_date": "31/07/26",
            "map_name": "Unknown",
            "is_7dr_win": False,
        }
    )

    payload = FrontlineWeb._war_diary_payload(SimpleNamespace(_get_match_records=lambda: records))

    assert len(payload["recent"]) == 16
    assert payload["recent"][0]["date"] == "15/08/26"
    assert payload["recent"][-1]["score"] == "Loss"


def test_website_infantry_leaderboard_filters_members_and_backfills_top_three(tmp_path, monkeypatch) -> None:
    import cogs.HLLInfLeaderboard as infantry

    database = tmp_path / "leaderboard.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE submissions "
            "(user_id INTEGER, stat TEXT, value INTEGER, proof_verified INTEGER)"
        )
        connection.executemany(
            "INSERT INTO submissions(user_id, stat, value, proof_verified) VALUES (?, 'Most Kills', ?, 1)",
            [(1, 100), (2, 90), (3, 80), (4, 70), (5, 60), (6, 50)],
        )

    members = {
        2: SimpleNamespace(display_name="Blueberry", roles=[SimpleNamespace(name="Blueberry")]),
        3: SimpleNamespace(display_name="Diplomat", roles=[SimpleNamespace(name="Diplomat")]),
        4: SimpleNamespace(display_name="First", roles=[SimpleNamespace(name="Infantry")]),
        5: SimpleNamespace(display_name="Second", roles=[SimpleNamespace(name="Recon")]),
        6: SimpleNamespace(display_name="Third", roles=[]),
    }
    guild = SimpleNamespace(get_member=members.get)
    monkeypatch.setattr(infantry, "DB_FILE", str(database))
    monkeypatch.setattr(infantry, "STATS", ["Most Kills"])

    groups = FrontlineWeb._read_leaderboards(guild)
    infantry_records = groups[0]["records"]

    assert infantry_records == [
        {
            "stat": "Most Kills",
            "leaders": [
                {"name": "First", "value": "70"},
                {"name": "Second", "value": "60"},
                {"name": "Third", "value": "50"},
            ],
        }
    ]


def test_server_status_normalises_bifrost_data_without_exposing_credentials() -> None:
    payload = FrontlineWeb._normalise_server_status(
        {
            "data": {"server.name": "7DR Public", "server.map.name": "Foy", "server.map.gamemode": "Warfare"},
            "team1": {"playerCount": 40},
            "team2": {"playerCount": 38},
            "matchTimeRemainingSeconds": 1800,
            "nextMap": "Carentan",
        },
        "Fallback",
    )

    assert payload["name"] == "7DR Public"
    assert payload["map"] == "Foy Warfare"
    assert payload["players"] == 78
    assert payload["time_remaining_seconds"] == 1800
    assert "token" not in payload


def test_discord_server_status_embed_keeps_fields_and_only_discord_images() -> None:
    embed = SimpleNamespace(
        title="7DR Public Server",
        description="Players and current map",
        fields=[SimpleNamespace(name="Players", value="78/100", inline=True)],
        author=SimpleNamespace(name="Status feed"),
        footer=SimpleNamespace(text="Updated every minute"),
        colour=SimpleNamespace(value=0xA8AD73),
        image=SimpleNamespace(proxy_url="https://media.discordapp.net/attachments/1/2/map.png", url=""),
        thumbnail=SimpleNamespace(proxy_url="", url=""),
    )

    payload = FrontlineWeb._discord_status_embed(
        embed,
        content="Server online",
        updated_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )

    assert payload["source"] == "discord_webhook"
    assert payload["name"] == "7DR Public Server"
    assert payload["fields"] == [{"name": "Players", "value": "78/100", "inline": True}]
    assert payload["image_url"] == "https://media.discordapp.net/attachments/1/2/map.png"
    assert FrontlineWeb._discord_media_url("https://example.com/untrusted.png") == ""


def test_server_status_channel_ids_are_read_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("FRONTLINE_SERVER_STATUS_CHANNEL_IDS", "123, 456,123,invalid")

    assert FrontlineWeb._server_status_channel_ids() == (123, 456)


def test_caddyfile_does_not_reopen_tunnel_origins() -> None:
    caddyfile = Path("liberationapp/Caddyfile.production").read_text(encoding="utf-8")

    assert "Cloudflare Tunnel" in caddyfile
    assert "reverse_proxy" not in caddyfile

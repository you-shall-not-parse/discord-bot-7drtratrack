from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from cogs.frontline_web import FRONTEND_DIR, TURNSTILE_SITE_KEY, FrontlineWeb
from config import WEB_LOG_PATH


def test_frontend_assets_exist_and_are_wired() -> None:
    index = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    css = (FRONTEND_DIR / "app.css").read_text(encoding="utf-8")
    javascript = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
    report = (FRONTEND_DIR / "report.html").read_text(encoding="utf-8")
    report_javascript = (FRONTEND_DIR / "report.js").read_text(encoding="utf-8")

    login = (FRONTEND_DIR / "login.html").read_text(encoding="utf-8")

    assert '<link rel="stylesheet" href="/assets/app.css?v=7">' in index
    assert '<script defer src="/assets/app.js?v=6"></script>' in index
    assert 'src="/assets/emblem_7dr.png"' in index
    assert "7th Armoured Division" in index
    assert "<dialog" not in index
    assert "--olive:" in css
    assert "[hidden] { display: none !important; }" in css
    assert 'fetch("/api/dashboard"' in javascript
    assert 'fetch(`/api/hllv-search?q=' in javascript
    assert "AbortSignal.timeout(20_000)" in javascript
    assert 'rel="noopener noreferrer"' in index
    assert 'data-view="overview"' in index
    assert 'data-view="matches"' in index
    assert 'data-view="community"' in index
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
    assert "{{TURNSTILE_HEAD}}" in login
    assert "{{TURNSTILE_WIDGET}}" in login
    assert 'method="post" action="/login"' in login
    assert 'action="/logout"' in index


def test_pin_sessions_are_random_and_open_redirects_are_rejected(monkeypatch) -> None:
    monkeypatch.setenv("APPPIN", "a-long-test-pin")
    service = FrontlineWeb(SimpleNamespace())
    token = service._new_session("Example User")

    assert service._valid_session(token)
    assert not service._valid_session(token + "tampered")
    assert service._safe_next("/rollcalls/22nd") == "/rollcalls/22nd"
    assert service._safe_next("//example.com") == "/"
    assert service._safe_next("https://example.com") == "/"
    assert service._sessions[token].claimed_name == "Example User"


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
            {"opponent_clan_name": "Example", "match_date": "01/09/2026", "map_name": "Carentan", "result": "5-2"},
            {"opponent_clan_name": "example", "match_date": "02/09/2026", "map_name": "Kharkov", "result": "1-3"},
            {"opponent_clan_name": "Another", "match_date": "03/09/2026", "map_name": "Omaha", "result": "3-3"},
        ]
    )

    payload = FrontlineWeb._war_diary_payload(cog)

    assert payload["summary"] == {"played": 3, "wins": 1, "losses": 1, "draws": 1}
    assert payload["opponents"][0] == {"name": "Example", "played": 2, "wins": 1, "losses": 1, "draws": 0}
    assert payload["recent"][0]["opponent"] == "Another"


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


def test_caddyfile_does_not_reopen_tunnel_origins() -> None:
    caddyfile = Path("liberationapp/Caddyfile.production").read_text(encoding="utf-8")

    assert "Cloudflare Tunnel" in caddyfile
    assert "reverse_proxy" not in caddyfile

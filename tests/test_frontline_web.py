from pathlib import Path
from types import SimpleNamespace

from cogs.frontline_web import FRONTEND_DIR, FrontlineWeb


def test_frontend_assets_exist_and_are_wired() -> None:
    index = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    css = (FRONTEND_DIR / "app.css").read_text(encoding="utf-8")
    javascript = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
    report = (FRONTEND_DIR / "report.html").read_text(encoding="utf-8")
    report_javascript = (FRONTEND_DIR / "report.js").read_text(encoding="utf-8")

    assert '<link rel="stylesheet" href="/assets/app.css?v=4">' in index
    assert '<script defer src="/assets/app.js?v=4"></script>' in index
    assert 'src="/assets/emblem_7dr.png"' in index
    assert "7th Armoured Division" in index
    assert "<dialog" not in index
    assert "--olive:" in css
    assert "[hidden] { display: none !important; }" in css
    assert 'fetch("/api/dashboard"' in javascript
    assert "AbortSignal.timeout(20_000)" in javascript
    assert 'src="/assets/report.js?v=1"' in report
    assert 'href="/rollcalls/${encodeURIComponent(rollcall.key)}"' in javascript
    assert 'href="/trainees/${encodeURIComponent(track.key)}"' in javascript
    assert 'label: "Current status"' in report_javascript
    assert 'label: "Qualifications"' in report_javascript
    assert 'key: "current_status"' in report_javascript
    assert 'class="trainee-name"' in report_javascript
    assert 'data-sort=' in report_javascript


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


def test_caddyfile_publishes_apex_without_replacing_historic_stats() -> None:
    caddyfile = Path("liberationapp/Caddyfile.production").read_text(encoding="utf-8")

    assert "hllfrontline.com {" in caddyfile
    assert "reverse_proxy 127.0.0.1:7020" in caddyfile
    assert "7drhistostats.hllfrontline.com {" in caddyfile
    assert "reverse_proxy 127.0.0.1:7010" in caddyfile

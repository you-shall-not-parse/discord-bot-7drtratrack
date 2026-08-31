from pathlib import Path

from cogs.frontline_web import FRONTEND_DIR, FrontlineWeb


def test_frontend_assets_exist_and_are_wired() -> None:
    index = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    css = (FRONTEND_DIR / "app.css").read_text(encoding="utf-8")
    javascript = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")

    assert '<link rel="stylesheet" href="/assets/app.css?v=3">' in index
    assert '<script defer src="/assets/app.js?v=3"></script>' in index
    assert 'src="/assets/emblem_7dr.png"' in index
    assert "7th Armoured Division" in index
    assert "--olive:" in css
    assert "[hidden] { display: none !important; }" in css
    assert 'fetch("/api/dashboard"' in javascript
    assert "AbortSignal.timeout(20_000)" in javascript


def test_rollcall_status_is_normalised_for_the_public_api() -> None:
    assert FrontlineWeb._normalise_rollcall_status("✅") == "attending"
    assert FrontlineWeb._normalise_rollcall_status("🅾️") == "other-rollcall"
    assert FrontlineWeb._normalise_rollcall_status("❌") == "missing"
    assert FrontlineWeb._normalise_rollcall_status("") == "missing"


def test_caddyfile_publishes_apex_without_replacing_historic_stats() -> None:
    caddyfile = Path("liberationapp/Caddyfile.production").read_text(encoding="utf-8")

    assert "hllfrontline.com {" in caddyfile
    assert "reverse_proxy 127.0.0.1:7020" in caddyfile
    assert "7drhistostats.hllfrontline.com {" in caddyfile
    assert "reverse_proxy 127.0.0.1:7010" in caddyfile

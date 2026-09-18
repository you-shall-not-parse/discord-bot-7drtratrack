"""Offline fixtures and injected HTTP/clock objects; never contact HLLRecords."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from bunker.client import Client
from bunker.database import Database
from bunker.loader import load_matches, match_date
from bunker.models import Check, Status
from bunker.parser import parse_profile
from bunker.reporting import export
from bunker.cli import main

UID = "9e27c1492d85c016763e1462288c3725"
FIXTURES = Path(__file__).parent / "fixtures" / "bunker"


def payload(mid=1, name="RSM Ginger", date="2026-01-01T00:00:00Z"):
    return {"result": {"id": mid, "start": date, "player_stats": [
        {"player": name, "player_id": UID, "platform": "xbl",
         "steaminfo": {"has_bans": False, "bans": None}}]}}


def make_index(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "1.json").write_text(json.dumps(payload()))
    (a / "duplicate.json").write_text(json.dumps(payload()))
    (a / "2.json").write_text(json.dumps(payload(2, "Ginger New", "2026-02-01T00:00:00Z")))
    (b / "1.json").write_text(json.dumps(payload()))
    return load_matches([("Main", a), ("Training", b)])


def test_index_aliases_and_two_servers(tmp_path):
    index = make_index(tmp_path)
    assert len(index.players) == 1
    player = index.players[UID]
    assert player.overall.current_name == "Ginger New"
    assert player.overall.aliases == {"RSM Ginger", "Ginger New"}
    assert len(player.overall.matches) == 3
    assert len(player.servers["Main"].matches) == 2
    assert player.servers["Training"].current_name == "RSM Ginger"
    assert index.duplicate_matches == 1
    assert len(index.appearances) == 3


@pytest.mark.parametrize("fixture,status,reason", [
    ("banned.html", Status.BANNED, "cheating"),
    ("normal.html", Status.NOT_BANNED, ""),
    ("nextjs.html", Status.BANNED, "abusive conduct"),
])
def test_profile_fixtures(fixture, status, reason):
    result = parse_profile((FIXTURES / fixture).read_text(), UID, {"RSM Ginger"})
    assert result.status == status
    assert result.reason == reason
    assert result.bunker_banned == (status == Status.BANNED)


@pytest.mark.parametrize("body,status", [
    ("", Status.PARSE_ERROR), ("upstream unavailable", Status.PARSE_ERROR),
    ("<html>Login</html>", Status.UNKNOWN),
    ("<html>Bunker Ban flags are synced from Bunker.</html>", Status.UNKNOWN),
    ("<html>Someone Else was Bunker banned for cheating.</html>", Status.UNKNOWN),
    ("<html><script src='/.bunny-shield/test'></script></html>", Status.UNKNOWN),
    ('{"steaminfo":{"has_bans":false}}', Status.UNKNOWN),
])
def test_uncertain_is_never_clean(body, status):
    result = parse_profile(body, UID, {"RSM Ginger"})
    assert result.status == status
    assert result.bunker_banned is None


def test_incomplete_or_wrong_profile_not_clean():
    normal = (FIXTURES / "normal.html").read_text()
    assert parse_profile(normal.replace("</html>", ""), UID, {"RSM Ginger"}).status == Status.UNKNOWN
    assert parse_profile(normal, "different-id", {"RSM Ginger"}).status == Status.UNKNOWN


def test_nested_escapes_and_case():
    text = json.dumps(json.dumps("RSM Ginger WAS BUNKER BANNED FOR harassment."))
    assert parse_profile(text, UID, {"RSM Ginger"}).reason == "harassment"


class Response:
    def __init__(self, code=200, body=None, headers=None):
        self.status_code = code
        self.body = body if body is not None else (FIXTURES / "banned.html").read_text()
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        yield self.body.encode()


def client_for(responses, retries=1):
    session = Mock()
    session.get.side_effect = responses
    now, sleeps = [0.0], []
    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
    client = Client(session=session, retries=retries, clock=lambda: now[0], sleep=sleep)
    return client, session, sleeps


def test_404():
    client, session, _ = client_for([Response(404)])
    assert client.check(UID, {"RSM Ginger"}).status == Status.NOT_FOUND
    assert session.get.call_count == 1


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_retry_and_retry_after(code):
    client, session, sleeps = client_for([Response(code, headers={"Retry-After": "9"}), Response()])
    assert client.check(UID, {"RSM Ginger"}).status == Status.BANNED
    assert session.get.call_count == 2
    assert sum(sleeps) >= 9
    assert client.live_requests == 2


def test_rate_limit_exhaustion_stops_workload():
    client, session, _ = client_for([Response(429), Response(429)])
    assert client.check(UID, {"RSM Ginger"}).status == Status.RATE_LIMITED
    assert client.blocked
    assert client.check("another", {"Another"}).status == Status.UNKNOWN
    assert session.get.call_count == 2


@pytest.mark.parametrize("code", [401, 403, 302])
def test_access_controls_not_retried(code):
    client, session, _ = client_for([Response(code)])
    assert client.check(UID, {"RSM Ginger"}).status == Status.REQUEST_FAILED
    assert client.blocked
    assert session.get.call_count == 1


def test_timeout():
    client, _, _ = client_for([requests.Timeout(), requests.Timeout()])
    result = client.check(UID, {"RSM Ginger"})
    assert result.status == Status.REQUEST_FAILED
    assert result.http_status is None


def test_parse_error_over_http():
    client, _, _ = client_for([Response(body="nonsense")])
    assert client.check(UID, {"RSM Ginger"}).status == Status.PARSE_ERROR


def test_pacing_across_successful_players():
    client, _, sleeps = client_for([Response(), Response()])
    client.check(UID, {"RSM Ginger"})
    client.check(UID, {"RSM Ginger"})
    assert sum(sleeps) >= 1.5


def test_retry_after_date():
    value = (datetime.now(timezone.utc) + timedelta(seconds=60)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert 58 <= Client.retry_after(value) <= 60
    assert Client.retry_after("invalid") == 0
    assert Client.retry_after("inf") == 0


def test_200_challenge_stops_requests():
    client, session, _ = client_for([Response(body="<html>Verify you are human</html>")])
    assert client.check(UID, {"RSM Ginger"}).status == Status.UNKNOWN
    assert client.blocked
    assert session.get.call_count == 1


def test_response_limit():
    client, _, _ = client_for([Response(body="a" * (8 * 1024 * 1024 + 1))])
    assert client.check(UID, {"RSM Ginger"}).status == Status.PARSE_ERROR


def test_expired_cache(tmp_path):
    index = make_index(tmp_path)
    db = Database(tmp_path / "cache.db")
    try:
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        db.save(index.players[UID], Check(Status.NOT_BANNED, checked_at=old))
        assert db.cached(UID, 7, 90) is None
        db.save(index.players[UID], Check(Status.BANNED, checked_at=old))
        assert db.cached(UID, 7, 90).status == Status.BANNED
    finally:
        db.close()


def test_exhausted_500():
    client, _, _ = client_for([Response(500), Response(500)])
    result = client.check(UID, {"RSM Ginger"})
    assert result.status == Status.REQUEST_FAILED
    assert result.http_status == 500
    assert client.blocked


def test_csv_formula_protection(tmp_path):
    from bunker.reporting import write_csv
    write_csv(tmp_path / "safe.csv", ["name"], [{"name": "=UNTRUSTED()"}])
    assert "'=UNTRUSTED()" in (tmp_path / "safe.csv").read_text(encoding="utf-8-sig")


def test_cli_blocked_exports_pending_players(tmp_path, monkeypatch):
    data = payload()
    data["result"]["player_stats"].append({"player_id": "another-player", "player": "Another", "platform": "psn"})
    (tmp_path / "match.json").write_text(json.dumps(data))
    fake, session, _ = client_for([Response(403)])
    monkeypatch.setattr("bunker.cli.Client", lambda *args: fake)
    assert main(["--server", "Main", str(tmp_path / "match.json"),
                 "--cache-db", str(tmp_path / "cache.db"), "--output", str(tmp_path / "out")]) == 2
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert summary["statuses"] == {"REQUEST_FAILED": 1, "UNKNOWN": 1}
    assert session.get.call_count == 1


def test_cache_persists_and_refresh_flags(tmp_path):
    index = make_index(tmp_path)
    path = tmp_path / "cache.sqlite3"
    db = Database(path)
    db.record_index(index)
    db.record_index(index)
    db.save(index.players[UID], Check(Status.BANNED, "cheating"))
    db.close()
    db = Database(path)
    try:
        assert db.cached(UID, 7, 90).status == Status.BANNED
        assert db.cached(UID, 7, 90, refresh=True).status == Status.BANNED
        assert db.cached(UID, 7, 90, refresh_banned=True) is None
        assert db.cached(UID, 7, 0) is None
        assert db.db.execute("SELECT count(*) FROM appearances").fetchone()[0] == 3
        db.save(index.players[UID], Check(Status.REQUEST_FAILED))
        assert db.cached(UID, 7, 90) is None
        assert db.db.execute("SELECT status FROM check_history ORDER BY rowid LIMIT 1").fetchone()[0] == "BANNED"
        db.save(index.players[UID], Check(Status.NOT_BANNED))
        assert db.cached(UID, 7, 90).status == Status.NOT_BANNED
        assert db.cached(UID, 7, 90, refresh=True) is None
    finally:
        db.close()


def test_reports(tmp_path):
    index = make_index(tmp_path)
    summary = export(index, {UID: Check(Status.BANNED, "cheating")}, tmp_path / "out", 1, 0)
    assert summary["bunker_banned_players"] == 1
    assert summary["banned_on_multiple_servers"] == 1
    assert summary["unique_players_by_server"] == {"Main": 1, "Training": 1}
    assert summary["total_matches_processed"] == 3
    import csv
    with (tmp_path / "out" / "flagged_players.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2
    assert {r["matches_seen"] for r in rows} == {"1", "2"}


def test_partial_report(tmp_path):
    index = make_index(tmp_path)
    summary = export(index, {}, tmp_path / "out", 0, 0, True)
    assert summary["unknown_or_failed_checks"] == 1
    assert summary["interrupted"]


def test_cli_resumes_without_requests(tmp_path, monkeypatch):
    make_index(tmp_path)
    fake, session, _ = client_for([Response()])
    monkeypatch.setattr("bunker.cli.Client", lambda *args: fake)
    args = ["--server", "Main", str(tmp_path / "a"), "--server", "Training", str(tmp_path / "b"),
            "--cache-db", str(tmp_path / "cache.db"), "--output", str(tmp_path / "out")]
    assert main(args) == 0
    assert main(args) == 0
    assert session.get.call_count == 1
    assert json.loads((tmp_path / "out" / "summary.json").read_text())["cache_hits"] == 1


def test_cli_interrupt_exports(tmp_path, monkeypatch):
    make_index(tmp_path)
    fake, _, _ = client_for([KeyboardInterrupt()])
    monkeypatch.setattr("bunker.cli.Client", lambda *args: fake)
    assert main(["--server", "Main", str(tmp_path / "a"), "--cache-db", str(tmp_path / "cache.db"),
                 "--output", str(tmp_path / "out")]) == 130
    assert json.loads((tmp_path / "out" / "summary.json").read_text())["interrupted"]


def test_invalid_input_reported(tmp_path):
    (tmp_path / "bad.json").write_text("invalid")
    data = payload()
    data["result"]["player_stats"].append({"player_id": "../bad"})
    (tmp_path / "ok.json").write_text(json.dumps(data))
    index = load_matches([("Main", tmp_path)])
    assert index.input_errors == 2
    assert len(index.players) == 1


def test_date_normalization():
    assert match_date("2026-01-01T01:00:00+01:00") == "2026-01-01T00:00:00+00:00"
    assert match_date(0) == "1970-01-01T00:00:00+00:00"

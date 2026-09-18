"""Offline pagination, checkpoint and CLI integration coverage."""
import json
from unittest.mock import Mock

import pytest
import requests

from bunker.cli import main
from bunker.source import Importer, server_endpoint

URL = "https://bifroststats.com/hll/leaderboards/servers/b73ceb717668"
IDS = ["32b4dea4-829d-5ca2-ad49-98a75e29a50a", "1b8747c6-bb08-5a06-b378-027c752e200c"]


def listing(page, ids, total=2):
    return {"result": {"page": page, "page_size": 1, "total": total,
                       "maps": [{"id": mid, "map": {"id": "stmereeglise_warfare"}, "player_stats": []} for mid in ids]}}


def detail(mid):
    return {"result": {"id": mid, "start": "2026-09-16T19:10:36Z",
                       "player_stats": [{"player_id": "abc123", "player": "Player", "platform": "xbl"}]}}


class Response:
    def __init__(self, payload, code=200, headers=None):
        self.payload, self.status_code, self.headers = payload, code, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        yield json.dumps(self.payload).encode()


def importer(tmp_path, responses):
    session = Mock()
    session.get.side_effect = responses
    sleep = Mock()
    return Importer(tmp_path, session=session, sleep=sleep, retries=1), session, sleep


def test_paginated_download_and_resume(tmp_path):
    fetch, session, sleep = importer(tmp_path, [Response(listing(1, IDS[:1])), Response(detail(IDS[0])),
                                               Response(listing(2, IDS[1:])), Response(detail(IDS[1]))])
    directory = fetch.download(URL)
    assert len(list(directory.glob("*.json"))) == 2
    assert fetch.downloads == 2
    assert session.get.call_count == 4
    assert sleep.call_count == 3
    assert session.get.call_args_list[1].args[0].endswith(f"/hll/stmereeglise_warfare/{IDS[0]}/crcon")
    resumed, session, _ = importer(tmp_path, [Response(listing(1, IDS[:1])), Response(listing(2, IDS[1:]))])
    resumed.download(URL)
    assert resumed.cache_hits == 2
    assert resumed.downloads == 0
    assert session.get.call_count == 2


def test_interrupted_download_reuses_completed_matches(tmp_path):
    fetch, _, _ = importer(tmp_path, [Response(listing(1, IDS[:1])), Response(detail(IDS[0])), KeyboardInterrupt()])
    with pytest.raises(KeyboardInterrupt):
        fetch.download(URL)
    assert (tmp_path / "b73ceb717668" / f"{IDS[0]}.json").exists()


@pytest.mark.parametrize("response", [Response({}, 403), Response({}, 302), Response({"result": {}}),
                                    Response(listing(2, IDS[:1])), Response(listing(1, []))])
def test_errors_not_silently_complete(tmp_path, response):
    fetch, _, _ = importer(tmp_path, [response])
    with pytest.raises(ValueError):
        fetch.download(URL)


def test_duplicate_page_detection(tmp_path):
    fetch, _, _ = importer(tmp_path, [Response(listing(1, IDS[:1])), Response(detail(IDS[0])),
                                     Response(listing(2, IDS[:1]))])
    with pytest.raises(ValueError, match="repeated"):
        fetch.download(URL)


def test_wrong_match_not_saved(tmp_path):
    fetch, _, _ = importer(tmp_path, [Response(listing(1, IDS[:1])), Response(detail(IDS[1]))])
    with pytest.raises(ValueError, match="Invalid match export"):
        fetch.download(URL)
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("response", [Response({}, 429, {"Retry-After": "10"}), Response({}, 503), requests.Timeout()])
def test_source_retry(tmp_path, response):
    fetch, _, sleep = importer(tmp_path, [response, Response(listing(1, [], 0))])
    fetch.download(URL)
    assert fetch.requests == 2
    assert sleep.call_args.args[0] >= 1.5
    if isinstance(response, Response) and response.status_code == 429:
        assert sleep.call_args.args[0] >= 10


@pytest.mark.parametrize("url", ["http://bifroststats.com/hll/leaderboards/servers/b73ceb717668",
                               "https://evil.test/hll/leaderboards/servers/b73ceb717668",
                               URL + "?page=2", URL.replace("bifroststats.com", "user@bifroststats.com")])
def test_source_url_validation(url):
    with pytest.raises(ValueError):
        server_endpoint(url)


def test_cli_download_only(tmp_path, monkeypatch):
    fetch, _, _ = importer(tmp_path / "downloads", [Response(listing(1, IDS[:1], 1)), Response(detail(IDS[0]))])
    monkeypatch.setattr("bunker.cli.Importer", lambda *args: fetch)
    client = Mock(side_effect=AssertionError("No HLLRecords requests expected"))
    monkeypatch.setattr("bunker.cli.Client", client)
    assert main(["--server", "TRR Events", URL, "--download-only", "--cache-db", str(tmp_path / "cache.db"),
                 "--output", str(tmp_path / "out")]) == 0
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert summary["total_unique_players"] == 1
    assert summary["live_requests_made"] == 0
    assert not client.called

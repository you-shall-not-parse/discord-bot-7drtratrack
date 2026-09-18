"""Download public Bifrost match exports with pagination and disk checkpoints."""
import json
import logging
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

from .client import Client

log = logging.getLogger(__name__)


def server_endpoint(url: str) -> tuple[str, str]:
    """Accept only recognized public Bifrost server pages, without credentials."""
    parsed = urlsplit(url)
    match = re.fullmatch(r"/hll/leaderboards/servers/([a-fA-F0-9]{12})(?:/crcon)?/?", parsed.path)
    if (parsed.scheme != "https" or parsed.netloc not in
            {"bifroststats.com", "frostbite.bifrostgaming.com"} or not match
            or parsed.query or parsed.fragment):
        raise ValueError("Expected https://bifroststats.com/hll/leaderboards/servers/<server-id>")
    return f"https://{parsed.netloc}/hll/leaderboards/servers/{match[1]}/crcon", match[1]


def valid_match(payload: object, match_id: str) -> bool:
    """Reject error envelopes and exports belonging to a different match."""
    return (isinstance(payload, dict) and isinstance(payload.get("result"), dict)
            and payload["result"].get("id") == match_id
            and isinstance(payload["result"].get("player_stats"), list))


class Importer:
    """One request at a time; retries never bypass access controls."""
    def __init__(self, root: Path, delay: float = 1.5, retries: int = 4,
                 timeout: float = 20, session=None, sleep=time.sleep) -> None:
        self.root, self.delay, self.retries, self.timeout = root, delay, retries, timeout
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "7DR-BunkerChecker/1.0 (public match history importer)",
                                     "Accept": "application/json"})
        self.sleep = sleep
        self.requests = 0
        self.downloads = 0
        self.cache_hits = 0

    def close(self) -> None:
        self.session.close()

    def get_json(self, url: str, params: dict | None = None) -> dict:
        pause = self.delay
        for attempt in range(self.retries + 1):
            if self.requests:
                self.sleep(pause)
            self.requests += 1
            try:
                with self.session.get(url, params=params, timeout=self.timeout,
                                      allow_redirects=False, stream=True) as response:
                    code = response.status_code
                    if code == 429 or code in (500, 502, 503, 504):
                        pause = max(self.delay, 2 ** attempt,
                                    Client.retry_after(response.headers.get("Retry-After", "")))
                        log.warning("Bifrost HTTP %d; attempt %d/%d", code, attempt + 1, self.retries + 1)
                        if attempt < self.retries:
                            continue
                    if code != 200:
                        raise ValueError(f"Bifrost HTTP {code}; import stopped. Downloaded matches are retained.")
                    body = bytearray()
                    for chunk in response.iter_content(65536):
                        body.extend(chunk)
                        if len(body) > 16 * 1024 * 1024:
                            raise ValueError("Bifrost response exceeds 16 MiB")
                    try:
                        payload = json.loads(body)
                    except (ValueError, UnicodeError) as exc:
                        raise ValueError("Bifrost returned non-JSON content, possibly an access challenge") from exc
                    if not isinstance(payload, dict):
                        raise ValueError("Unexpected Bifrost response format")
                    return payload
            except requests.RequestException as exc:
                log.warning("Bifrost request failed (%s), attempt %d/%d", type(exc).__name__, attempt + 1, self.retries + 1)
                if attempt == self.retries:
                    raise ValueError("Bifrost request failed; restart to resume downloaded matches") from exc
                pause = max(self.delay, 2 ** attempt)
        raise ValueError("Bifrost retries exhausted")

    def download(self, url: str) -> Path:
        """Refresh the listing; reuse validated, completed match files on disk."""
        endpoint, server_id = server_endpoint(url)
        directory = self.root / server_id
        directory.mkdir(parents=True, exist_ok=True)
        origin = "https://" + urlsplit(endpoint).netloc
        page, seen = 1, set()
        while True:
            payload = self.get_json(endpoint, {"page": page, "page_size": 50})
            result = payload.get("result")
            if (not isinstance(result, dict) or result.get("page") != page
                    or not isinstance(result.get("maps"), list)
                    or type(result.get("total")) is not int or result["total"] < 0
                    or type(result.get("page_size")) is not int or result["page_size"] <= 0):
                raise ValueError("Unrecognized Bifrost pagination; import stopped")
            matches = result["maps"]
            log.info("Bifrost server=%s page=%d total=%d", server_id, page, result["total"])
            if not matches:
                if len(seen) < result["total"]:
                    raise ValueError("Bifrost returned an incomplete match listing; restart to resume")
                break
            previous_count = len(seen)
            for match in matches:
                if not isinstance(match, dict):
                    raise ValueError("Invalid match in Bifrost listing")
                mid = match.get("id", "")
                map_data = match.get("map")
                map_id = map_data.get("id", "") if isinstance(map_data, dict) else ""
                if (not isinstance(mid, str) or not re.fullmatch(r"[a-fA-F0-9-]{36}", mid)
                        or not isinstance(map_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", map_id)):
                    raise ValueError("Unrecognized Bifrost match identifier")
                if mid in seen:
                    continue
                seen.add(mid)
                destination = directory / f"{mid}.json"
                try:
                    cached = json.loads(destination.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    cached = None
                if valid_match(cached, mid):
                    self.cache_hits += 1
                    continue
                detail = self.get_json(f"{origin}/hll/{map_id}/{mid}/crcon")
                if not valid_match(detail, mid):
                    raise ValueError(f"Invalid match export for {mid}; import stopped")
                temporary = destination.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(detail, ensure_ascii=False), encoding="utf-8")
                temporary.replace(destination)
                self.downloads += 1
                log.info("Download checkpoint server=%s match=%s indexed=%d/%d", server_id, mid, len(seen), result["total"])
            if len(seen) >= result["total"]:
                break
            if len(seen) == previous_count:
                raise ValueError("Bifrost pagination repeated a page; import stopped")
            page += 1
        log.info("Bifrost import complete server=%s matches=%d", server_id, len(seen))
        return directory

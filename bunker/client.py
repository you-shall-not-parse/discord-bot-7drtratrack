"""Serial, rate-limited HTTP client with bounded retries and global cooldowns."""
import logging
import math
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests

from .models import Check, Status
from .parser import parse_profile

log = logging.getLogger(__name__)
BASE = "https://hllrecords.com/profiles/"


class Client:
    def __init__(self, delay: float = 1.5, retries: int = 4, timeout: float = 20,
                 session=None, sleep=time.sleep, clock=time.monotonic) -> None:
        self.delay, self.retries, self.timeout = delay, retries, timeout
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "7DR-BunkerChecker/1.0 (public HLL profile status checker)",
                                     "Accept": "text/html,application/json"})
        self.sleep, self.clock = sleep, clock
        self.next_request = 0.0
        self.live_requests = 0
        self.blocked = False

    def close(self) -> None:
        self.session.close()

    def wait(self) -> None:
        while self.next_request > self.clock():
            self.sleep(min(30, self.next_request - self.clock()))

    @staticmethod
    def retry_after(value: str) -> float:
        try:
            seconds = float(value)
            return max(0, seconds) if math.isfinite(seconds) else 0
        except (ValueError, TypeError):
            try:
                date = parsedate_to_datetime(value)
                return max(0, (date - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                return 0

    def check(self, uid: str, aliases: set[str]) -> Check:
        """Stop the run on access controls or exhausted server errors/rate limits."""
        if self.blocked:
            return Check(Status.UNKNOWN, evidence="Run stopped after upstream failure")
        for attempt in range(self.retries + 1):
            self.wait()
            self.live_requests += 1
            retry_wait = 0.0
            try:
                with self.session.get(BASE + quote(uid, safe=""), timeout=self.timeout,
                                      allow_redirects=False, stream=True) as response:
                    code = response.status_code
                    retry_wait = self.retry_after(response.headers.get("Retry-After", ""))
                    if code == 429:
                        result = Check(Status.RATE_LIMITED, http_status=code)
                    elif code in (500, 502, 503, 504):
                        result = Check(Status.REQUEST_FAILED, http_status=code)
                    elif code == 404:
                        return Check(Status.NOT_FOUND, http_status=code)
                    elif code != 200:
                        self.blocked = code in (401, 403) or 300 <= code < 400
                        return Check(Status.REQUEST_FAILED, http_status=code,
                                     evidence="HTTP response did not provide a profile")
                    else:
                        chunks = bytearray()
                        for chunk in response.iter_content(65536):
                            chunks.extend(chunk)
                            if len(chunks) > 8 * 1024 * 1024:
                                return Check(Status.PARSE_ERROR, http_status=code, evidence="Response exceeds 8 MiB limit")
                        result = parse_profile(chunks.decode("utf-8", errors="replace"), uid, aliases)
                        result.http_status = code
                        self.blocked = result.evidence.startswith("Access challenge")
                        if result.status in (Status.UNKNOWN, Status.PARSE_ERROR):
                            log.warning("Profile %s: %s (%s)", uid, result.status, result.evidence)
                        return result
            except requests.RequestException as exc:
                result = Check(Status.REQUEST_FAILED, evidence=type(exc).__name__)
            finally:
                self.next_request = max(self.next_request, self.clock() + self.delay)
            log.warning("Profile %s attempt %d: %s HTTP=%s", uid, attempt + 1, result.status, result.http_status)
            self.next_request = max(self.next_request, self.clock() + max(retry_wait, 2 ** attempt))
            if attempt < self.retries:
                log.info("Retrying %s after %.1fs", uid, max(retry_wait, 2 ** attempt, self.delay))
        self.blocked = True
        return result

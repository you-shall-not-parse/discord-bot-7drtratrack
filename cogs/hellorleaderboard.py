#!/usr/bin/env python3
"""
scraper1.py
Fetch a single hellor.pro player page and print Overall/Team/Impact/Fight scores and Top % values.

Usage:
    python3 scraper1.py <t17_id> [--delay 0.85]

Default rate limit enforces ~1.2 requests/sec (delay default 0.85s).
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from typing import Dict, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_DELAY = 0.85  # seconds (<= 1.2 req/sec)
BASE_URL = "https://hellor.pro/player/{}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Copilot-Chat-Scraper/1.0; +https://github.com/you-shall-not-parse)"
}


def make_session(retries: int = 3, backoff_factor: float = 0.5) -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update(HEADERS)
    return s


def fetch_player_html(t17_id: str, session: requests.Session, delay: float = DEFAULT_DELAY, timeout: float = 10.0) -> str:
    """
    Fetch the player page HTML. Sleeps `delay` seconds before the request to respect rate limiting.
    """
    url = BASE_URL.format(t17_id)
    time.sleep(delay)
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def extract_label_info_from_text(text: str, label: str) -> Optional[Tuple[str, str]]:
    """
    Attempt to extract a numeric score and optional 'Top X%' for a given label from a text blob.
    Returns (score_str, top_percent_str) or None.
    """
    # Match e.g. "Overall 3581" and optional "Top 4.1%"
    pattern = re.compile(
        rf"{re.escape(label)}\D*?(\d{{1,7}})(?:[^\d%]*)?(?:Top[:\s]*([0-9]+(?:\.[0-9]+)?)%)?",
        re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return None
    raw = m.group(1)
    top = m.group(2)
    top_str = f"{top}%" if top else "N/A"
    return raw, top_str


def find_label_nearby(soup: BeautifulSoup, label: str) -> Optional[Tuple[str, str]]:
    """
    Search for nodes mentioning the label and inspect parent/ancestor text to find score + percentile.
    """
    nodes = soup.find_all(string=re.compile(rf"\b{re.escape(label)}\b", re.IGNORECASE))
    for node in nodes:
        parent = node.parent
        current = parent
        # collect text from parent and a few ancestor levels
        for _ in range(4):
            if current is None:
                break
            txt = current.get_text(" ", strip=True)
            info = extract_label_info_from_text(txt, label)
            if info:
                return info
            current = current.parent
    return None


def parse_scores(html: str) -> Dict[str, Dict[str, str]]:
    """
    Return a dict mapping label -> {"score": "...", "top": "..."}
    """
    soup = BeautifulSoup(html, "html.parser")
    labels = ["Overall", "Team", "Impact", "Fight"]
    results: Dict[str, Dict[str, str]] = {}
    full_text = soup.get_text(" ", strip=True)

    for label in labels:
        found = find_label_nearby(soup, label)
        if not found:
            # fallback to whole-page search
            found = extract_label_info_from_text(full_text, label)
        results[label] = {"score": found[0] if found else "N/A", "top": found[1] if found else "N/A"}
    return results


def print_scores(t17_id: str, scores: Dict[str, Dict[str, str]]) -> None:
    print(f"Player: {t17_id}")
    print("-" * 36)
    for label in ["Overall", "Team", "Impact", "Fight"]:
        s = scores.get(label, {"score": "N/A", "top": "N/A"})
        print(f"{label:7}: {s['score']:>6}   Top {s['top']}")
    print("-" * 36)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape hellor.pro player page for Overall/Team/Impact/Fight")
    parser.add_argument("t17_id", help="t17 player id (the part after /player/ in the URL)")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="seconds to sleep between requests")
    args = parser.parse_args()

    t17_id = args.t17_id.strip()
    if not t17_id:
        print("Provide a non-empty t17 id.", file=sys.stderr)
        sys.exit(2)

    session = make_session()
    try:
        html = fetch_player_html(t17_id, session=session, delay=args.delay)
    except requests.HTTPError as e:
        print(f"HTTP error fetching player page: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as e:
        print(f"Network error fetching player page: {e}", file=sys.stderr)
        sys.exit(1)

    scores = parse_scores(html)
    print_scores(t17_id, scores)


if __name__ == "__main__":
    main()
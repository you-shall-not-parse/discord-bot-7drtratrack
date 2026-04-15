#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Dict, Optional

import discord
from discord.ext import commands

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from data_paths import data_path

# ========= CONFIG =========
POST_CHANNEL_ID = 1099806153170489485
ROLE_NAME = "Basic Trained"

STATE_FILE = data_path("hellor_leaderboard_state.json")
MAPPING_FILE = data_path("hellor_t17_map.json")
LOG_FILE = data_path("hellor_leaderboard.log")

UPDATE_INTERVAL_SECONDS = 12 * 3600
REQUEST_PACE_SECONDS = 1.85

CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"
CRCON_API_KEY = os.getenv("CRCON_API_KEY")

PLAYER_LOOKUP_ENABLED = True
PLAYER_LOOKUP_MAX_PER_RUN = 120
PLAYER_LOOKUP_CACHE_TTL_SECONDS = 3600
PLAYER_LOOKUP_NEGATIVE_CACHE_TTL_SECONDS = 120

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; HellorLeaderboard/1.0; +https://github.com/you-shall-not-parse)"
}
BASE_HELLOR_URL = "https://hellor.pro/player/{}"


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


def extract_label_score(text: str, label: str) -> Optional[str]:
    pattern = re.compile(rf"{re.escape(label)}\D*?(\d{{1,7}})", re.IGNORECASE)
    m = pattern.search(text)
    return m.group(1) if m else None


def find_label_nearby(soup: BeautifulSoup, label: str) -> Optional[str]:
    nodes = soup.find_all(string=re.compile(rf"\b{re.escape(label)}\b", re.IGNORECASE))
    for node in nodes:
        current = node.parent
        for _ in range(4):
            if current is None:
                break
            txt = current.get_text(" ", strip=True)
            got = extract_label_score(txt, label)
            if got:
                return got
            current = current.parent
    return None


def parse_scores(html: str) -> Dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    labels = ["Overall", "Team", "Impact", "Fight"]
    results: Dict[str, str] = {}
    full_text = soup.get_text(" ", strip=True)

    for label in labels:
        found = find_label_nearby(soup, label) or extract_label_score(full_text, label)
        results[label] = found if found else "N/A"
    return results


class HellorLeaderboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ran_once = False
        self._lock = asyncio.Lock()

        self._state = self._load_state()
        self._updater_task: Optional[asyncio.Task] = None

        self._player_id_cache: dict[str, tuple[Optional[str], float]] = {}
        self._http_session = make_session()

        self._log(f"[HellorLeaderboard] STATE_FILE = {os.path.abspath(STATE_FILE)}")
        self._log(f"[HellorLeaderboard] MAPPING_FILE = {os.path.abspath(MAPPING_FILE)}")
        self._log(f"[HellorLeaderboard] LOG_FILE = {os.path.abspath(LOG_FILE)}")

    def _log(self, msg: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        line = f"{ts} {msg}"
        print(line)
        try:
            os.makedirs(os.path.dirname(LOG_FILE) or ".", exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def _load_state(self) -> dict:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2)

    def _get_output_message_id(self) -> Optional[int]:
        msg_id = self._state.get("output_message_id")
        return int(msg_id) if isinstance(msg_id, int) else None

    def _set_output_message_id(self, message_id: int) -> None:
        self._state["output_message_id"] = int(message_id)
        self._state["output_channel_id"] = POST_CHANNEL_ID
        self._save_state()

    def _save_mapping_file(self, mapping: dict) -> None:
        os.makedirs(os.path.dirname(MAPPING_FILE) or ".", exist_ok=True)
        with open(MAPPING_FILE, "w", encoding="utf-8") as f:
            json.dump(mapping, f, indent=2)

    def _load_mapping_file(self) -> dict:
        try:
            with open(MAPPING_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _cut_at_hash(self, text: str) -> str:
        t = (text or "").strip()
        if not t:
            return ""
        if "#" in t:
            t = t.split("#", 1)[0].strip()
        return " ".join(t.split())

    def _normalize_discord_username(self, name: str, *, strip_rank_prefix: bool = False) -> str:
        name = self._cut_at_hash(name)
        name = name.replace("%", " ")
        name = " ".join(name.split())

        if strip_rank_prefix:
            rank_prefixes = [
                "Field Marshal", "FM", "General", "Gen",
                "Lieutenant General", "Lt Gen", "LtGen",
                "Major General", "Maj Gen", "MajGen",
                "Major", "Maj", "WO1", "WO2",
                "RSM", "SGM", "SSG", "SGT", "CPL", "PTE",
            ]
            rank_pat = r"^(?:" + "|".join(re.escape(r) for r in rank_prefixes) + r")\.?\s+"
            name = re.sub(rank_pat, "", name, flags=re.IGNORECASE).strip()
        return name

    async def _rcon_get(self, endpoint: str) -> dict:
        if not CRCON_API_KEY:
            return {"error": "CRCON_API_KEY is not set"}

        def _do_request():
            try:
                r = requests.get(
                    CRCON_PANEL_URL + endpoint,
                    headers={"Authorization": f"Bearer {CRCON_API_KEY}"},
                    timeout=10,
                )
                return r.json()
            except Exception as e:
                return {"error": str(e)}

        return await asyncio.to_thread(_do_request)

    def _extract_first_player_id(self, data) -> Optional[str]:
        if isinstance(data, dict):
            if "player_id" in data and data["player_id"] is not None:
                return str(data["player_id"])
            for v in data.values():
                found = self._extract_first_player_id(v)
                if found:
                    return found
        elif isinstance(data, list):
            for item in data:
                found = self._extract_first_player_id(item)
                if found:
                    return found
        return None

    async def _fetch_player_id_cached(self, player_name: str) -> tuple[Optional[str], bool]:
        normalized = self._normalize_discord_username(player_name)
        if not normalized:
            return None, False

        key = normalized.lower()
        now = time.time()

        cached = self._player_id_cache.get(key)
        if cached:
            pid, ts = cached
            ttl = PLAYER_LOOKUP_CACHE_TTL_SECONDS if pid else PLAYER_LOOKUP_NEGATIVE_CACHE_TTL_SECONDS
            if now - ts <= ttl:
                return pid, False

        endpoint = f"get_players_history?player_name={urllib.parse.quote(normalized)}&page_size=1"
        data = await self._rcon_get(endpoint)

        pid = self._extract_first_player_id(data.get("result", data)) if data else None
        self._player_id_cache[key] = (pid, now)
        return pid, True

    async def _fetch_and_build(self) -> Optional[discord.Embed]:
        return None  # unchanged rest of your logic continues here...

async def setup(bot: commands.Bot):
    await bot.add_cog(HellorLeaderboard(bot))
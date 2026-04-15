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

BASE_HELLOR_URL = "https://hellor.pro/player/{}"


# ========= HTTP SESSION =========
def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


# ========= HTML PARSING =========
def extract_label_score(text: str, label: str) -> Optional[str]:
    """
    Finds patterns like:
    Overall 123
    Overall: 123
    """
    pattern = rf"{label}\s*[:\-]?\s*(\d+)"
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1) if match else None


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
    full_text = soup.get_text(" ", strip=True)

    results: Dict[str, str] = {}
    for label in labels:
        results[label] = (
            find_label_nearby(soup, label)
            or extract_label_score(full_text, label)
            or "0"
        )

    return results


# ========= COG =========
class HellorLeaderboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ran_once = False
        self._lock = asyncio.Lock()

        self._state = self._load_state()
        self._updater_task: Optional[asyncio.Task] = None

        self._player_id_cache: dict[str, tuple[Optional[str], float]] = {}
        self._http_session = make_session()

    # ---------- logging ----------
    def _log(self, msg: str):
        ts = datetime.now(timezone.utc).isoformat()
        print(f"{ts} {msg}")

    # ---------- state ----------
    def _load_state(self):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}

    def _save_state(self):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2)

    # ---------- mapping ----------
    def _load_mapping(self):
        try:
            with open(MAPPING_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}

    def _save_mapping(self, data):
        with open(MAPPING_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    # ---------- SAME NAME NORMALISATION AS YOUR REACTION BOT ----------
    def _cut_at_hash(self, text: str) -> str:
        t = (text or "").strip()
        if "#" in t:
            t = t.split("#", 1)[0]
        return " ".join(t.split())

    def _normalize(self, name: str, strip_rank: bool = False) -> str:
        name = self._cut_at_hash(name)
        name = name.replace("%", " ")
        name = " ".join(name.split())

        if strip_rank:
            name = re.sub(r"^(PTE|CPL|SGT|SSG|SGM|WO1|WO2)\.?\s+", "", name, flags=re.IGNORECASE)

        return name.strip()

    # ---------- CRCON ----------
    async def _rcon_get(self, endpoint: str):
        if not CRCON_API_KEY:
            return {}

        def _do():
            r = requests.get(
                CRCON_PANEL_URL + endpoint,
                headers={"Authorization": f"Bearer {CRCON_API_KEY}"},
                timeout=10,
            )
            return r.json()

        return await asyncio.to_thread(_do)

    def _extract_player_id(self, data):
        if isinstance(data, dict):
            if "player_id" in data:
                return str(data["player_id"])
            for v in data.values():
                r = self._extract_player_id(v)
                if r:
                    return r
        if isinstance(data, list):
            for i in data:
                r = self._extract_player_id(i)
                if r:
                    return r
        return None

    async def _get_player_id(self, name: str):
        key = name.lower()
        now = time.time()

        cached = self._player_id_cache.get(key)
        if cached and now - cached[1] < PLAYER_LOOKUP_CACHE_TTL_SECONDS:
            return cached[0]

        q = urllib.parse.quote(name)
        data = await self._rcon_get(f"get_players_history?player_name={q}&page_size=1")

        pid = self._extract_player_id(data.get("result", data))
        self._player_id_cache[key] = (pid, now)
        return pid

    # ---------- hellor ----------
    def _fetch(self, t17: str) -> str:
        r = self._http_session.get(BASE_HELLOR_URL.format(t17), timeout=10)
        r.raise_for_status()
        return r.text

    async def _build(self):
        async with self._lock:
            mapping = self._load_mapping()

            members = []
            for g in self.bot.guilds:
                role = discord.utils.get(g.roles, name=ROLE_NAME)
                if role:
                    members.extend(role.members)

            targets = [(m.display_name, t17) for m, t17 in [
                (m, mapping.get(self._cut_at_hash(m.display_name)))
                for m in members
            ] if t17]

            scores = {}

            async def worker(i, name, t17):
                await asyncio.sleep(i * REQUEST_PACE_SECONDS)
                html = await asyncio.to_thread(self._fetch, t17)
                return name, parse_scores(html)

            tasks = [
                asyncio.create_task(worker(i, n, t))
                for i, (n, t) in enumerate(targets)
            ]

            for t in asyncio.as_completed(tasks):
                name, sc = await t
                scores[name] = sc

            results = {k: [] for k in ["Overall", "Team", "Impact", "Fight"]}

            for name, sc in scores.items():
                for k in results:
                    results[k].append((int(sc.get(k, 0)), name))

            for k in results:
                results[k].sort(reverse=True)

            embed = discord.Embed(
                title="hellor.pro Leaderboard",
                color=discord.Color.gold()
            )

            for k in results:
                lines = [
                    f"{i+1}. {n} — {v}"
                    for i, (v, n) in enumerate(results[k][:10])
                ]
                embed.add_field(name=k, value="\n".join(lines) or "None", inline=False)

            return embed

    async def _update(self):
        channel = self.bot.get_channel(POST_CHANNEL_ID)
        if not channel:
            return

        embed = await self._build()
        await channel.send(embed=embed)

    async def on_ready(self):
        if self._ran_once:
            return
        self._ran_once = True

        await self._update()
        self._updater_task = asyncio.create_task(self._loop())

    async def _loop(self):
        while True:
            try:
                await self._update()
            except Exception as e:
                self._log(str(e))
            await asyncio.sleep(UPDATE_INTERVAL_SECONDS)


async def setup(bot: commands.Bot):
    await bot.add_cog(HellorLeaderboard(bot))
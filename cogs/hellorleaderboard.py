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
    "User-Agent": "Mozilla/5.0 (compatible; HellorLeaderboard/1.0)"
}
BASE_HELLOR_URL = "https://hellor.pro/player/{}"


# ========= HTTP =========
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


# ========= PARSING =========
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

    # ---------- state ----------
    def _load_state(self) -> dict:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
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
        self._save_state()

    # ---------- mapping ----------
    def _save_mapping_file(self, mapping: dict) -> None:
        os.makedirs(os.path.dirname(MAPPING_FILE) or ".", exist_ok=True)
        with open(MAPPING_FILE, "w", encoding="utf-8") as f:
            json.dump(mapping, f, indent=2)

    def _load_mapping_file(self) -> dict:
        try:
            with open(MAPPING_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}

    # ---------- CRCON ----------
    async def _rcon_get(self, endpoint: str) -> dict:
        if not CRCON_API_KEY:
            return {"error": "No API key"}

        def _do():
            try:
                r = requests.get(
                    CRCON_PANEL_URL + endpoint,
                    headers={"Authorization": f"Bearer {CRCON_API_KEY}"},
                    timeout=10,
                )
                return r.json()
            except Exception as e:
                return {"error": str(e)}

        return await asyncio.to_thread(_do)

    async def fetch_player_id(self, name: str) -> Optional[str]:
        q = urllib.parse.quote(name)
        data = await self._rcon_get(f"get_players_history?player_name={q}&page_size=1")

        if not data or data.get("error"):
            return None

        try:
            return str(data["result"][0]["player_id"])
        except:
            return None

    # ---------- members ----------
    async def _gather_members(self):
        members = []
        for g in self.bot.guilds:
            role = discord.utils.get(g.roles, name=ROLE_NAME)
            if role:
                members.extend([m for m in role.members if not m.bot])
        return list({m.id: m for m in members}.values())

    # ---------- fetch ----------
    def _fetch_html(self, t17):
        r = self._http_session.get(BASE_HELLOR_URL.format(t17), timeout=10)
        r.raise_for_status()
        return r.text

    # ---------- core ----------
    async def _fetch_and_build(self):
        members = await self._gather_members()
        mapping = self._load_mapping_file()

        # build mapping
        for m in members:
            name = m.display_name.split("#")[0]
            if name not in mapping:
                mapping[name] = await self.fetch_player_id(name)

        self._save_mapping_file(mapping)

        results = {"Overall": [], "Team": [], "Impact": [], "Fight": []}

        async def task(i, name, t17):
            await asyncio.sleep(i * REQUEST_PACE_SECONDS)
            try:
                html = await asyncio.to_thread(self._fetch_html, t17)
                scores = parse_scores(html)
                return name, {k: int(v) if v.isdigit() else 0 for k, v in scores.items()}
            except:
                return name, {"Overall": 0, "Team": 0, "Impact": 0, "Fight": 0}

        tasks = [
            asyncio.create_task(task(i, n, t))
            for i, (n, t) in enumerate(mapping.items()) if t
        ]

        for fut in asyncio.as_completed(tasks):
            name, scores = await fut
            for k in results:
                results[k].append((scores[k], name))

        for k in results:
            results[k].sort(reverse=True)

        embed = discord.Embed(
            title="hellor.pro Top 10 Leaderboards",
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc),
        )

        for k in results:
            text = "\n".join(f"{i+1}. {n} — {s}" for i, (s, n) in enumerate(results[k][:10]))
            embed.add_field(name=k, value=text or "None", inline=False)

        return embed

    async def _update_message(self):
        channel = self.bot.get_channel(POST_CHANNEL_ID)
        msg_id = self._get_output_message_id()

        if not channel:
            return

        if msg_id:
            try:
                msg = await channel.fetch_message(msg_id)
            except:
                msg = None
        else:
            msg = None

        embed = await self._fetch_and_build()

        if msg:
            await msg.edit(embed=embed)
        else:
            msg = await channel.send(embed=embed)
            self._set_output_message_id(msg.id)

    async def _loop(self):
        while True:
            await self._update_message()
            await asyncio.sleep(UPDATE_INTERVAL_SECONDS)

    @commands.Cog.listener()
    async def on_ready(self):
        if self._ran_once:
            return
        self._ran_once = True
        await self._update_message()
        self._updater_task = asyncio.create_task(self._loop())


async def setup(bot):
    await bot.add_cog(HellorLeaderboard(bot))
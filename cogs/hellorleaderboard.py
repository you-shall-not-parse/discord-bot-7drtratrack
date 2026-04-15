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
ROLE_NAME = "Basic trained"

STATE_FILE = data_path("hellor_leaderboard_state.json")
MAPPING_FILE = data_path("hellor_t17_map.json")
LOG_FILE = data_path("hellor_leaderboard.log")

UPDATE_INTERVAL_SECONDS = 12 * 3600
REQUEST_PACE_SECONDS = 1.85

CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"
CRCON_API_KEY = os.getenv("CRCON_API_KEY")

BASE_HELLOR_URL = "https://hellor.pro/player/{}"

print("HELLOR COG IMPORTED")


# ========= SESSION =========
def make_session():
    s = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


# ========= PARSING =========
def extract_label_score(text: str, label: str):
    pattern = rf"{label}\s*[:\-]?\s*(\d+)"
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(1) if m else None


def find_label_nearby(soup: BeautifulSoup, label: str):
    nodes = soup.find_all(string=re.compile(rf"\b{re.escape(label)}\b", re.I))

    for node in nodes:
        cur = node.parent
        for _ in range(4):
            if not cur:
                break
            txt = cur.get_text(" ", strip=True)
            got = extract_label_score(txt, label)
            if got:
                return got
            cur = cur.parent
    return None


def parse_scores(html: str):
    soup = BeautifulSoup(html, "html.parser")

    labels = ["Overall", "Team", "Impact", "Fight"]
    full_text = soup.get_text(" ", strip=True)

    out = {}
    for l in labels:
        out[l] = (
            find_label_nearby(soup, l)
            or extract_label_score(full_text, l)
            or "0"
        )
    return out


# ========= COG =========
class HellorLeaderboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ran_once = False
        self._lock = asyncio.Lock()

        self._state = self._load_state()
        self._session = make_session()

    # ---------- logging ----------
    def _log(self, msg: str):
        print(f"{datetime.now(timezone.utc).isoformat()} {msg}")

    # ---------- state ----------
    def _load_state(self):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except:
            return {}

    def _save_state(self):
        with open(STATE_FILE, "w") as f:
            json.dump(self._state, f, indent=2)

    # ========= IMPORTANT: FIXED NORMALISATION =========
    RANK_PREFIXES = [
        "Field Marshal","FM",
        "General","Gen",
        "Lieutenant General","Lt Gen","LtGen","Lt.Gen",
        "Major General","Maj Gen","MajGen",
        "Brigadier","Brig",
        "Colonel","Col",
        "Lieutenant Colonel","Lt Col","LtCol",
        "Major","Maj",
        "Captain","Cpt",
        "Lieutenant","Lt","Lt.",
        "2nd Lieutenant","2Lt",
        "RSM","Regimental Sergeant Major",
        "WO1","WO2",
        "SGM","Sergeant Major",
        "SSG","Staff Sergeant",
        "SGT","Sergeant",
        "CPL","Corporal",
        "LCPL","Lance Corporal",
        "PTE","Private"
    ]

    def _cut_hash(self, name: str) -> str:
        name = (name or "").strip()
        if "#" in name:
            name = name.split("#", 1)[0]
        return " ".join(name.split())

    def _normalize(self, name: str, strip_rank: bool = False) -> str:
        name = self._cut_hash(name)
        name = name.replace("%", " ")
        name = " ".join(name.split())

        if strip_rank:
            pattern = r"^(?:" + "|".join(re.escape(x) for x in self.RANK_PREFIXES) + r")\.?\s+"
            name = re.sub(pattern, "", name, flags=re.I)

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

    def _extract_pid(self, data):
        if isinstance(data, dict):
            if "player_id" in data:
                return str(data["player_id"])
            for v in data.values():
                r = self._extract_pid(v)
                if r:
                    return r
        if isinstance(data, list):
            for i in data:
                r = self._extract_pid(i)
                if r:
                    return r
        return None

    # ---------- FETCH ----------
    def _fetch(self, t17: str):
        r = self._session.get(BASE_HELLOR_URL.format(t17), timeout=10)
        r.raise_for_status()
        return r.text

    # ========= FIXED TARGET BUILD =========
    def _build_targets(self, members, mapping):
        targets = []

        for m in members:
            key1 = self._normalize(m.display_name, False).lower()
            key2 = self._normalize(m.display_name, True).lower()

            t17 = mapping.get(key1) or mapping.get(key2)

            if t17:
                targets.append((m.display_name, t17))

        return targets

    # ---------- BUILD ----------
    async def _build(self):
        async with self._lock:
            self._log("Building leaderboard...")

            mapping = self._load_mapping()

            members = []
            for g in self.bot.guilds:
                role = discord.utils.get(g.roles, name=ROLE_NAME)
                if role:
                    members.extend(role.members)

            self._log(f"Members found: {len(members)}")

            targets = self._build_targets(members, mapping)
            self._log(f"Targets resolved: {len(targets)}")

            if not targets:
                return discord.Embed(
                    title="hellor.pro Leaderboard",
                    description="No mapped players found",
                    color=discord.Color.red(),
                )

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

    # ---------- UPDATE (FIXED: edits message instead of spam) ----------
    async def _update(self):
        channel = self.bot.get_channel(POST_CHANNEL_ID)
        if not channel:
            self._log("Channel not found")
            return

        embed = await self._build()

        state = self._state.setdefault("msg", {})
        msg_id = state.get("id")

        try:
            if msg_id:
                msg = await channel.fetch_message(msg_id)
                await msg.edit(embed=embed)
            else:
                msg = await channel.send(embed=embed)
                state["id"] = msg.id
                self._save_state()

        except Exception as e:
            self._log(f"Update failed: {e}")

    # ---------- LOOP ----------
    async def _loop(self):
        while True:
            try:
                await self._update()
            except Exception as e:
                self._log(str(e))
            await asyncio.sleep(UPDATE_INTERVAL_SECONDS)

    async def on_ready(self):
        if self._ran_once:
            return
        self._ran_once = True

        await self._update()
        asyncio.create_task(self._loop())


async def setup(bot: commands.Bot):
    await bot.add_cog(HellorLeaderboard(bot))
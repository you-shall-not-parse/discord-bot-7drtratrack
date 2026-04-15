#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.parse
from typing import Optional

import discord
from discord.ext import commands
from discord import app_commands

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from data_paths import data_path

# ================= CONFIG =================
GUILD_ID = 1097913605082579024

POST_CHANNEL_ID = 1099806153170489485
ROLE_NAME = "Basic trained"

STATE_FILE = data_path("hellor_leaderboard_state.json")
MAPPING_FILE = data_path("hellor_t17_map.json")

UPDATE_INTERVAL_SECONDS = 12 * 3600
REQUEST_PACE_SECONDS = 1.85

CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"
CRCON_API_KEY = os.getenv("CRCON_API_KEY")

BASE_HELLOR_URL = "https://hellor.pro/player/{}"

print("HELLOR LEADERBOARD LOADED")

# ================= ROSTERIZER STYLE NORMALISATION =================
RANK_ORDER = [
    ("FM", ["Field Marshal", "FM"]),
    ("GEN", ["General", "Gen"]),
    ("LTGEN", ["Lieutenant General", "Lt Gen", "Lt.Gen", "LtGen"]),
    ("MAJGEN", ["Major General", "Maj Gen", "MajGen"]),
    ("BRIG", ["Brigadier", "Brig"]),
    ("COL", ["Colonel", "Col"]),
    ("LTCOL", ["Lieutenant Colonel", "Lt Col", "LtCol"]),
    ("MAJ", ["Major", "Maj"]),
    ("CPT", ["Captain", "Cpt"]),
    ("LT", ["Lieutenant", "Lt", "Lt."]),
    ("2LT", ["2nd Lieutenant", "2Lt", "2Lt."]),
    ("RSM", ["RSM"]),
    ("WO1", ["WO1"]),
    ("WO2", ["WO2"]),
    ("SGM", ["SGM"]),
    ("SSG", ["SSG"]),
    ("SGT", ["SGT", "Sgt"]),
    ("CPL", ["CPL", "Cpl"]),
    ("LCPL", ["LCPL"]),
    ("PTE", ["Private", "Pte", "Pte."])
]

RANK_PREFIXES = [v for _, variants in RANK_ORDER for v in variants]


class NameTools:
    @staticmethod
    def cut(name: str) -> str:
        name = (name or "").strip()
        if "#" in name:
            name = name.split("#", 1)[0]
        return " ".join(name.split())

    @staticmethod
    def normalize(name: str, strip_rank: bool = False) -> str:
        name = NameTools.cut(name)
        name = name.replace("%", " ")
        name = " ".join(name.split())

        if strip_rank:
            pattern = r"^(?:" + "|".join(re.escape(x) for x in RANK_PREFIXES) + r")\.?\s+"
            name = re.sub(pattern, "", name, flags=re.I)

        return name.strip().lower()


# ================= HTTP =================
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


# ================= HELLOR PARSE =================
def extract_score(text: str, label: str):
    m = re.search(rf"{label}\s*[:\-]?\s*(\d+)", text, re.I)
    return m.group(1) if m else None


def parse_scores(html: str):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    out = {}
    for k in ["Overall", "Team", "Impact", "Fight"]:
        out[k] = extract_score(text, k) or "0"
    return out


# ================= COG =================
class HellorLeaderboard(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.lock = asyncio.Lock()
        self.session = make_session()

    # ---------- STATE ----------
    def _load_mapping(self):
        try:
            with open(MAPPING_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}

    def _save_mapping(self, data):
        with open(MAPPING_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    # ---------- CRCON ----------
    async def _crcon_lookup(self, name: str) -> Optional[str]:
        if not CRCON_API_KEY:
            return None

        def req():
            r = requests.get(
                CRCON_PANEL_URL + f"get_players_history?player_name={urllib.parse.quote(name)}&page_size=1",
                headers={"Authorization": f"Bearer {CRCON_API_KEY}"},
                timeout=10,
            )
            return r.json()

        data = await asyncio.to_thread(req)

        def extract(d):
            if isinstance(d, dict):
                if "player_id" in d:
                    return str(d["player_id"])
                for v in d.values():
                    r = extract(v)
                    if r:
                        return r
            return None

        return extract(data.get("result", data))

    # ---------- RESOLVE T17 (MANUAL + AUTO CACHE) ----------
    async def resolve_t17(self, name: str, mapping: dict) -> Optional[str]:
        n1 = NameTools.normalize(name, False)
        n2 = NameTools.normalize(name, True)

        # 1. MANUAL OVERRIDE
        if n1 in mapping:
            return mapping[n1]
        if n2 in mapping:
            return mapping[n2]

        # 2. CRCON fallback
        t17 = await self._crcon_lookup(name)
        if not t17:
            return None

        # 3. CACHE RESULT
        mapping[n1] = t17
        if n2 != n1:
            mapping[n2] = t17

        self._save_mapping(mapping)
        return t17

    # ---------- TARGETS ----------
    async def build_targets(self, members, mapping):
        out = []
        for m in members:
            t17 = await self.resolve_t17(m.display_name, mapping)
            if t17:
                out.append((m.display_name, t17))
        return out

    # ---------- FETCH ----------
    def fetch(self, t17: str):
        r = self.session.get(BASE_HELLOR_URL.format(t17), timeout=10)
        r.raise_for_status()
        return r.text

    # ---------- BUILD ----------
    async def build(self, guild: discord.Guild):
        async with self.lock:
            mapping = self._load_mapping()

            role = discord.utils.get(guild.roles, name=ROLE_NAME)
            if not role:
                return discord.Embed(title="Error", description="Role missing", color=discord.Color.red())

            members = role.members
            targets = await self.build_targets(members, mapping)

            scores = {}

            async def worker(i, name, t17):
                await asyncio.sleep(i * REQUEST_PACE_SECONDS)
                html = await asyncio.to_thread(self.fetch, t17)
                return name, parse_scores(html)

            tasks = [asyncio.create_task(worker(i, n, t)) for i, (n, t) in enumerate(targets)]

            for t in asyncio.as_completed(tasks):
                name, sc = await t
                scores[name] = sc

            results = {k: [] for k in ["Overall", "Team", "Impact", "Fight"]}

            for name, sc in scores.items():
                for k in results:
                    results[k].append((int(sc.get(k, 0)), name))

            for k in results:
                results[k].sort(reverse=True)

            embed = discord.Embed(title="hellor.pro Leaderboard", color=discord.Color.gold())

            for k in results:
                embed.add_field(
                    name=k,
                    value="\n".join(
                        f"{i+1}. {n} — {v}"
                        for i, (v, n) in enumerate(results[k][:10])
                    ) or "None",
                    inline=False,
                )

            return embed

    # ---------- SLASH COMMAND: EDIT T17 ----------
    @app_commands.command(name="set_t17", description="Set or override a player's T17 ID")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def set_t17(self, interaction: discord.Interaction, name: str, t17_id: str):

        mapping = self._load_mapping()

        key1 = NameTools.normalize(name, False)
        key2 = NameTools.normalize(name, True)

        mapping[key1] = t17_id
        mapping[key2] = t17_id

        self._save_mapping(mapping)

        await interaction.response.send_message(
            f"Updated mapping:\n`{name}` → `{t17_id}`",
            ephemeral=True
        )

    # ---------- LOOP ----------
    async def on_ready(self):
        if getattr(self, "_ready", False):
            return
        self._ready = True

        await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))

        while True:
            try:
                channel = self.bot.get_channel(POST_CHANNEL_ID)
                if channel:
                    embed = await self.build(channel.guild)
                    await channel.send(embed=embed)
            except Exception as e:
                print("Leaderboard error:", e)

            await asyncio.sleep(UPDATE_INTERVAL_SECONDS)


async def setup(bot: commands.Bot):
    await bot.add_cog(HellorLeaderboard(bot))
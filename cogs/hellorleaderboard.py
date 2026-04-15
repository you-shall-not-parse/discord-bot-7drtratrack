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

STATE_FILE = data_path("hellor_leaderboard_state.json")  # stores output message id for editing
MAPPING_FILE = data_path("hellor_t17_map.json")

UPDATE_INTERVAL_SECONDS = 12 * 3600  # 12 hours

# Rate limit: start <= 1 request / 1.85 seconds
REQUEST_PACE_SECONDS = 1.85

CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"
CRCON_API_KEY = os.getenv("CRCON_API_KEY")

# CRCON lookup limits & cache TTLs
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
    """Extract numeric score only. Intentionally ignores any 'Top %'."""
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

        # CRCON lookup cache: normalized_name_lower -> (player_id_or_none, timestamp)
        self._player_id_cache: dict[str, tuple[Optional[str], float]] = {}

        # HTTP session for hellor.pro
        self._http_session = make_session()

        print(f"[HellorLeaderboard] STATE_FILE = {os.path.abspath(STATE_FILE)}")
        print(f"[HellorLeaderboard] MAPPING_FILE = {os.path.abspath(MAPPING_FILE)}")

    # ---------- state ----------
    def _load_state(self) -> dict:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
        with open(STATE_FILE, ---------- mapping ----------
    def _save_mapping_file(self, mapping: dict) -> None:
        os.makedirs(os.path.dirname(MAPPING_FILE) or ".", exist_ok=True)
        with open(MAPPING_FILE, "w", encoding="utf-8") as f:
            json.dump(mapping, f, indent=2)
        print(f"[HellorLeaderboard] wrote mapping: {os.path.abspath(MAPPING_FILE)}")

    def _load_mapping_file(self) -> dict:
        try:
            with open(MAPPING_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    # ---------- name normalization ----------
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
            # minimal rank token stripping (good enough for lookup fallback)
            rank_prefixes = [
                "Field Marshal", "FM",
                "General", "Gen",
                "Lieutenant General", "Lt Gen", "LtGen",
                "Major General", "Maj Gen", "MajGen",
                "Major", "Maj",
                "WO1", "WO2",
                "RSM", "SGM", "SSG", "SGT", "CPL", "PTE",
            ]
            rank_pat = r"^(?:" + "|".join(re.escape(r) for r in rank_prefixes) + r")\.?\s+"
            name = re.sub(rank_pat, "", name, flags=re.IGNORECASE).strip()
        return name

    # ---------- CRCON lookup ----------
    async def _rcon_get(self, endpoint: str) -> dict:
        if not CRCON_API_KEY:
            return {"error": "CRCON_API_KEY is not set"}

        url = CRCON_PANEL_URL + endpoint

        def _do_request() -> dict:
            try:
                r = requests.get(
                    url,
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
            return None
        if isinstance(data, list):
            for item in data:
                found = self._extract_first_player_id(item)
                if found:
                    return found
        return None

    async def _fetch_player_id_cached(self, player_name: str) -> tuple[Optional[str], bool]:
        normalized = self._normalize_discord_username(player_name, strip_rank_prefix=False)
        if not normalized:
            return None, False

        key = normalized.lower()
        now = time.time()

        cached = self._player_id_cache.get(key)
        if cached:
            cached_id, cached_ts = cached
            ttl = PLAYER_LOOKUP_CACHE_TTL_SECONDS if cached_id is not None else PLAYER_LOOKUP_NEGATIVE_CACHE_TTL_SECONDS
            if now - cached_ts <= ttl:
                return cached_id, False

        player_name_q = urllib.parse.quote(normalized, safe="")
        endpoint = f"get_players_history?player_name={player_name_q}&page_size=1"
        data = await self._rcon_get(endpoint)
        if not data or data.get("failed") or data.get("error"):
            self._player_id_cache[key] = (None, now)
            return None, True

        player_id = self._extract_first_player_id(data.get("result", data))
        self._player_id_cache[key] = (player_id, now)
        return player_id, True

    async def fetch_player_id_for_member(self, member: discord.Member, http_budget_remaining: int) -> tuple[Optional[str], int]:
        raw_candidates: list[str] = []
        if member.display_name:
            raw_candidates.append(member.display_name)
        raw_candidates.append(member.name)
        gn = getattr(member, "global_name", None)
        if gn:
            raw_candidates.append(gn)

        http_used = 0
        seen: set[str] = set()

        for raw in raw_candidates:
            if http_used >= http_budget_remaining:
                break

            cut = self._normalize_discord_username(raw, strip_rank_prefix=False)
            if cut:
                k = cut.lower()
                if k not in seen:
                    seen.add(k)
                    pid, did_http = await self._fetch_player_id_cached(cut)
                    if did_http:
                        http_used += 1
                    if pid:
                        return pid, http_used

            if http_used >= http_budget_remaining:
                break

            stripped = self._normalize_discord_username(raw, strip_rank_prefix=True)
            if stripped and stripped != cut:
                k2 = stripped.lower()
                if k2 not in seen:
                    seen.add(k2)
                    pid2, did_http2 = await self._fetch_player_id_cached(stripped)
                    if did_http2:
                        http_used += 1
                    if pid2:
                        return pid2, http_used

        return None, http_used

    # ---------- discord output ----------
    async def _ensure_output_message(self) -> tuple[Optional[discord.TextChannel], Optional[discord.Message]]:
        channel = self.bot.get_channel(POST_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(POST_CHANNEL_ID)
            except Exception:
                return None, None

        existing_id = self._get_output_message_id()
        if existing_id is not None:
            try:
                existing = await channel.fetch_message(existing_id)
                return channel, existing
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        placeholder = discord.Embed(
            title="hellor.pro Top 10 Leaderboards",
            description="Preparing leaderboards…",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        sent = await channel.send(embeds=[placeholder], allowed_mentions=discord.AllowedMentions.none())
        self._set_output_message_id(sent.id)
        return channel, sent

    def _build_leaderboard_embed(self, guild_name.pro Top 10 Leaderboards",
            color=discord.Color.dark_gold(),
            timestamp=now,
        )
        e.set_footer(text=f"Updated • {guild_name}")

        parts = []
        for label in ["Overall", "Team", "Impact", "Fight"]:
            entries = results.get(label, [])[:10]
            if not entries:
                body = "None"
            else:
                lines = []
                for i, (score, name) in enumerate(entries, start=1):
                    lines.append(f"{i}. {discord.utils.escape_markdown(name)} — {score}")
                body = "\n".join(lines)
            parts.append(f"**{label} Top 10**\n{body}")

        e.description = "\n\n".join(parts)
        return e

    async def _gather_role_members(self) -> list[discord.Member]:
        members: list[discord.Member] = []
        for guild in self.bot.guilds:
            role = discord.utils.get(guild.roles, name=ROLE_NAME)
            if role:
                for m in getattr(role, "members", []) or []:
                    if not m.bot:
                        members.append(m)

        seen = set()
        uniq: list[discord.Member] = []
        for m in members:
            if m.id in seen:
                continue
            seen.add(m.id)
            uniq.append(m)
        return uniq

    # ---------- hellor fetch paced ----------
    def _fetch_hellor_no_sleep(self, t17_id: str) -> str:
        url = BASE_HELLOR_URL.format(t17_id)
        r = self._http_session.get(url, timeout=10)
        r.raise_for_status()
        return r.text

    async def _fetch_and_build(self) -> Optional[discord.Embed]:
        async with self._lock:
            started = time.time()

            members = await self._gather_role_members()
            if not members:
                print("[HellorLeaderboard] No members found with role:", ROLE_NAME)
                return None

            manual_mapping = self._load_mapping_file()
            mapping: dict[str, Optional[str]] = {}

            # Build mapping (honor manual edits)
            http_lookups_done = 0
            for member in members:
                display = self._cut_at_hash(member.display_name or member.name)

                if display in manual_mapping:
                    mapping[display] = manual_mapping[display]
                    continue

                if not PLAYER_LOOKUP_ENABLED or http_lookups_done >= PLAYER_LOOKUP_MAX_PER_RUN:
                    mapping[display] = None
                    continue

                remaining = PLAYER_LOOKUP_MAX_PER_RUN - http_lookups_done
                pid, used = await self.fetch_player_id_for_member(member, remaining)
                http_lookups_done += used
                mapping[display] = pid

            # Always write mapping so it exists (even if all null)
            self._save_mapping_file(mapping)

            targets: list[tuple[str, str]] = [(dn, t17) for dn, t17 in mapping.items() if t17]
            print(f"[HellorLeaderboard] members={len(members)}  targets_with_t17={len(targets)}  pace={REQUEST_PACE_SECONDS}s")

            # Pace request start times; allow network time to overlap (still respects rate)
            async def paced_fetch_parse(idx: int, display_name: str, t17: str) -> tuple[str, Dict[str, int]]:
                await asyncio.sleep(idx * REQUEST_PACE_SECONDS)
                try:
                    html = await asyncio.to_thread(self._fetch_hellor_no_sleep, t17)
                    parsed = parse_scores(html)
                except Exception as e:
                    print(f"[HellorLeaderboard] hellor fetch/parse failed for {display_name} ({t17}): {e}")
                    return display_name, {"Overall": 0, "Team": 0, "Impact": 0, "Fight": 0}

                out: Dict[str, int] = {}
                for label in ["Overall", "Team", "Impact", "Fight"]:
                    try:
                        out[label] = int(parsed.get(label, "0") or "0")
                    except Exception:
                        out[label] = 0
                return display_name, out

            tasks = [asyncio.create_task(paced_fetch_parse(i, dn, t17)) for i, (dn, t17) in enumerate(targets)]

            scores_by_player: dict[str, Dict[str, int]] = {}
            done = 0
            for fut in asyncio.as_completed(tasks):
                dn, scores = await fut
                scores_by_player[dn] = scores
                done += 1
                if done % 10 == 0 or done == len(tasks):
                    elapsed = int(time.time() - started)
                    print(f"[HellorLeaderboard] progress: {done}/{len(tasks)}  elapsed={elapsed}s")

            # Build leaderboards
            results: dict[str, list[tuple[int, str]]] = {}
            for label in ["Overall", "Team", "Impact", "Fight"]:
                arr: list[tuple[int, str]] = []
                for dn, sc in scores_by_player.items():
                    arr.append((int(sc.get(label, 0)), dn))
                arr.sort(key=lambda t: (-t[0], t[1].lower()))
                results[label] = arr

            guild_name = "Clan"
            for g in self.bot.guilds:
                if discord.utils.get(g.roles, name=ROLE_NAME):
                    guild_name = g.name
                    break

            embed = self._build_leaderboard_embed(guild_name, results)

            total = int(time.time() - started)
            print(f"[HellorLeaderboard] build complete in {total}s")
            return embed

    async def _update_message(self) -> None:
        channel, existing = await self._ensure_output_message()
        if channel is None:
            print("[HellorLeaderboard] Failed to resolve post channel:", POST_CHANNEL_ID)
            return

        embed = await self._fetch_and_build()
        if embed is None:
            return

        try:
            if existing is not None:
                await existing.edit(content=None, embeds=[embed], allowed_mentions=discord.AllowedMentions.none())
            else:
                sent = await channel.send(embeds=[embed], allowed_mentions=discord.AllowedMentions.none())
                self._set_output_message_id(sent.id)
        except Exception as e:
            print("[HellorLeaderboard] Failed to post/update embed:", e)

    async def _periodic_updater(self) -> None:
        while True:
            try:
                await self._update_message()
            except Exception as e:
                print("[HellorLeaderboard] Exception in updater:", e)
            await asyncio.sleep(UPDATE_INTERVAL_SECONDS)

    @commands.Cog.listener()
    async def on_ready(self):
        if self._ran_once:
            return
        self._ran_once = True

        # Run one immediate update on startup
        try:
            await self._update_message()
        except Exception as e:
            print("[HellorLeaderboard] Initial update failed:", e)

        # Start periodic updater
        self._updater_task = asyncio.create_task(self._periodic_updater())

    def cog_unload(self):
        if self._updater_task:
            self._updater_task.cancel()


async def setup(bot: commands.Bot):
    await bot.add_cog(HellorLeaderboard(bot))

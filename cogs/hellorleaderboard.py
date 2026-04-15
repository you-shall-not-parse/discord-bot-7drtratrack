#!/usr/bin/env python3
import asyncio
import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import discord
from discord.ext import commands, tasks

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
DEFAULT_DELAY = 1.85  # seconds between hellor.pro requests (<= 1.2 req/sec)
CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"
CRCON_API_KEY = os.getenv("CRCON_API_KEY")

# Player lookup limits & cache TTLs (mirrors rosterizer defaults)
PLAYER_LOOKUP_ENABLED = True
PLAYER_LOOKUP_MAX_PER_RUN = 120
PLAYER_LOOKUP_CACHE_TTL_SECONDS = 3600
PLAYER_LOOKUP_NEGATIVE_CACHE_TTL_SECONDS = 120

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Copilot-Chat-Scraper/1.0; +https://github.com/you-shall-not-parse)"
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


def fetch_player_html(t17_id: str, session: requests.Session, delay: float = DEFAULT_DELAY, timeout: float = 10.0) -> str:
    url = BASE_HELLOR_URL.format(t17_id)
    time.sleep(delay)
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def extract_label_info_from_text(text: str, label: str) -> Optional[str]:
    """
    Attempt to extract a numeric score for a given label from a text blob.
    Returns score_str or None. This intentionally ignores "Top %" values per request.
    """
    # Match e.g. "Overall 3581" (we capture the numeric score only)
    pattern = re.compile(
        rf"{re.escape(label)}\D*?(\d{{1,7}})",
        re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return None
    raw = m.group(1)
    return raw


def find_label_nearby(soup: BeautifulSoup, label: str) -> Optional[str]:
    """
    Search for nodes mentioning the label and inspect parent/ancestor text to find score.
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


def parse_scores(html: str) -> Dict[str, str]:
    """
    Return a dict mapping label -> score_str (score only). If not found, value is "N/A".
    """
    soup = BeautifulSoup(html, "html.parser")
    labels = ["Overall", "Team", "Impact", "Fight"]
    results: Dict[str, str] = {}
    full_text = soup.get_text(" ", strip=True)

    for label in labels:
        found = find_label_nearby(soup, label)
        if not found:
            # fallback to whole-page search
            found = extract_label_info_from_text(full_text, label)
        results[label] = found if found else "N/A"
    return results


class HellorLeaderboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ran_once = False
        self._state = self._load_state()
        # cache: normalized_name (lower) -> (player_id_or_none, timestamp)
        self._player_id_cache: dict[str, tuple[Optional[str], float]] = {}
        self._updater_task: Optional[asyncio.Task] = None
        # ensure a persistent session for hellor.pro scraping
        self._http_session = make_session()
        # Start the repeating task when cog is ready
        self._lock = asyncio.Lock()

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
            # Basic rank prefix stripping pattern based on common tokens (kept simple here).
            # mirrors rosterizer behavior: strip tokens like "FM", "Gen", "WO1", etc. as standalone prefix.
            RANK_PREFIXES = [
                "Field Marshal",
                "FM",
                "General",
                "Gen",
                "Lieutenant General",
                "Lt Gen",
                "LtGen",
                "Maj Gen",
                "Major",
                "WO1",
                "WO2",
                "RSM",
                "SGM",
                "SSG",
                "SGT",
                "CPL",
                "PTE",
            ]
            rank_pat = r"^(?:" + "|".join(re.escape(r) for r in RANK_PREFIXES) + r")\.?\s+"
            name = re.sub(rank_pat, "", name, flags=re.IGNORECASE).strip()
        return name

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
        # Be tolerant to API shape changes: search for the first 'player_id' key.
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
        """Return (player_id, did_http_request)."""
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
            # Don't lock in errors for too long.
            self._player_id_cache[key] = (None, now)
            return None, True

        player_id = self._extract_first_player_id(data.get("result", data))
        self._player_id_cache[key] = (player_id, now)
        return player_id, True

    async def fetch_player_id_for_member(self, member: discord.Member, http_budget_remaining: int) -> tuple[Optional[str], int]:
        """Try to resolve a CRCON player_id by searching likely Discord name variants."""
        raw_candidates: list[str] = []
        # Prefer server nickname/display name first.
        if member.display_name:
            raw_candidates.append(member.display_name)
        # Then raw username.
        raw_candidates.append(member.name)
        gn = getattr(member, "global_name", None)
        if gn:
            raw_candidates.append(gn)

        http_used = 0
        seen_queries: set[str] = set()

        for raw in raw_candidates:
            if http_used >= http_budget_remaining:
                break
            cut = self._normalize_discord_username(raw, strip_rank_prefix=False)
            if cut:
                k = cut.lower()
                if k not in seen_queries:
                    seen_queries.add(k)
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
                if k2 not in seen_queries:
                    seen_queries.add(k2)
                    pid2, did_http2 = await self._fetch_player_id_cached(stripped)
                    if did_http2:
                        http_used += 1
                    if pid2:
                        return pid2, http_used

        return None, http_used

    def _save_mapping_file(self, mapping: dict) -> None:
        os.makedirs(os.path.dirname(MAPPING_FILE) or ".", exist_ok=True)
        # mapping keys are display names (cut at '#'); values are t17 id or None
        with open(MAPPING_FILE, "w", encoding="utf-8") as f:
            json.dump(mapping, f, indent=2)

    def _load_mapping_file(self) -> dict:
        try:
            with open(MAPPING_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    async def _ensure_output_message(self) -> tuple[discord.TextChannel, Optional[discord.Message]]:
        channel = self.bot.get_channel(POST_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(POST_CHANNEL_ID)
            except Exception:
                return None, None  # caller must handle
        existing_id = self._get_output_message_id()
        if existing_id is not None:
            try:
                existing = await channel.fetch_message(existing_id)
                return channel, existing
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        # No existing persistent message; create a placeholder which we will edit.
        placeholder = discord.Embed(
            title="Helloworld Leaderboards",
            description="Preparing leaderboards…",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        sent = await channel.send(embeds=[placeholder], allowed_mentions=discord.AllowedMentions.none())
        self._set_output_message_id(sent.id)
        return channel, sent

    def _build_leaderboard_embed(self, guild_name: str, results: Dict[str, list[tuple[int, str]]]) -> discord.Embed:
        """
        `results` is a dict label -> list of (score_int, display_name) sorted descending.
        We'll produce a single embed with four sections.
        """
        now = datetime.now(timezone.utc)
        e = discord.Embed(
            title="hellor.pro Top 10 Leaderboards",
            color=discord.Color.dark_gold(),
            timestamp=now,
        )
        e.set_footer(text=f"Updated • {guild_name}")
        sections = []
        for label in ["Overall", "Team", "Impact", "Fight"]:
            entries = results.get(label, [])[:10]
            if not entries:
                body = "None"
            else:
                lines = []
                rank = 1
                for score, name in entries:
                    lines.append(f"{rank}. {discord.utils.escape_markdown(name)} — {score}")
                    rank += 1
                body = "\n".join(lines)
            sections.append(f"**{label} Top 10**\n{body}")
        e.description = "\n\n".join(sections)
        return e

    async def _gather_role_members(self) -> list[discord.Member]:
        members: list[discord.Member] = []
        for guild in self.bot.guilds:
            role = discord.utils.get(guild.roles, name=ROLE_NAME)
            if role:
                # role.members uses cache; if bot has members intent this should be fine.
                for m in getattr(role, "members", []) or []:
                    if m.bot:
                        continue
                    members.append(m)
        # remove duplicates by id while preserving order
        seen = set()
        uniq = []
        for m in members:
            if m.id in seen:
                continue
            seen.add(m.id)
            uniq.append(m)
        return uniq

    async def _fetch_and_build(self) -> Optional[discord.Embed]:
        """
        Main worker:
        - Collect members with ROLE_NAME
        - Lookup t17 ids (save mapping to MAPPING_FILE; allow manual edits to mapping file to take precedence)
        - For members with t17 ids, fetch hellor pages (respect DEFAULT_DELAY) and parse scores
        - Build leaderboards and return an embed to post/edit
        """
        async with self._lock:
            members = await self._gather_role_members()
            if not members:
                print("No members found with role:", ROLE_NAME)
                return None

            # Load existing manual mapping file to allow manual edits to persist
            manual_mapping = self._load_mapping_file()  # name -> t17 or None

            mapping: dict = {}  # display_name -> t17 or None
            http_lookups_done = 0
            for member in members:
                display = self._cut_at_hash(member.display_name or member.name)
                # If mapping file already contains an explicit t17 id for this display name, honor it.
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

            # Save mapping (this writes any newly discovered ids; user can later edit the file manually)
            self._save_mapping_file(mapping)

            # For each mapping with a t17 id, fetch hellor page and parse scores
            scores_by_player: dict[str, Dict[str, int]] = {}
            session = self._http_session
            for display_name, t17 in mapping.items():
                if not t17:
                    continue  # skip users without t17 id per request
                try:
                    html = await asyncio.to_thread(fetch_player_html, t17, session, DEFAULT_DELAY)
                except requests.HTTPError as e:
                    print(f"HTTP error fetching hellor page for {display_name} ({t17}): {e}")
                    # store N/A as zero
                    scores_by_player[display_name] = {"Overall": 0, "Team": 0, "Impact": 0, "Fight": 0}
                    continue
                except requests.RequestException as e:
                    print(f"Network error fetching hellor page for {display_name} ({t17}): {e}")
                    scores_by_player[display_name] = {"Overall": 0, "Team": 0, "Impact": 0, "Fight": 0}
                    continue

                parsed = parse_scores(html)
                # Convert to integers where possible; non-numeric -> 0
                converted = {}
                for label in ["Overall", "Team", "Impact", "Fight"]:
                    val = parsed.get(label, "N/A")
                    try:
                        converted[label] = int(val)
                    except Exception:
                        converted[label] = 0
                scores_by_player[display_name] = converted

            # Build leaderboards: label -> list of (score, display_name) sorted descending
            results: dict = {}
            for label in ["Overall", "Team", "Impact", "Fight"]:
                arr = []
                for display_name, scmap in scores_by_player.items():
                    score = scmap.get(label, 0)
                    arr.append((score, display_name))
                arr.sort(key=lambda t: (-t[0], t[1].lower()))
                results[label] = arr

            # Choose a guild name for footer display (take first guild that contained the role)
            guild_name = "Clan"
            for g in self.bot.guilds:
                if discord.utils.get(g.roles, name=ROLE_NAME):
                    guild_name = g.name
                    break

            embed = self._build_leaderboard_embed(guild_name, results)
            return embed

    async def _update_message(self) -> None:
        channel, existing = await self._ensure_output_message()
        if channel is None:
            print("Failed to resolve post channel:", POST_CHANNEL_ID)
            return

        embed = await self._fetch_and_build()
        if embed is None:
            # nothing to post
            return

        try:
            if existing is not None:
                await existing.edit(content=None, embeds=[embed], allowed_mentions=discord.AllowedMentions.none())
            else:
                sent = await channel.send(embeds=[embed], allowed_mentions=discord.AllowedMentions.none())
                self._set_output_message_id(sent.id)
        except discord.Forbidden:
            print(f"Missing permission to edit/send in channel {getattr(channel, 'id', None)}. Check bot perms.")
        except discord.HTTPException as e:
            print(f"Failed to post/update leaderboard embed: {e}")

    async def _run_loop_once_on_ready(self) -> None:
        # Run once immediately on startup
        await self._update_message()

    async def _periodic_updater(self) -> None:
        # Repeating loop that updates every UPDATE_INTERVAL_SECONDS
        while True:
            try:
                await self._update_message()
            except Exception as e:
                # never let the loop die
                print("Exception in hellor leaderboard updater:", e)
            await asyncio.sleep(UPDATE_INTERVAL_SECONDS)

    @commands.Cog.listener()
    async def on_ready(self):
        if self._ran_once:
            return
        self._ran_once = True

        # Run one immediate update and spawn the periodic updater as background task.
        try:
            await self._run_loop_once_on_ready()
        except Exception as e:
            print("Initial hellor leaderboard update failed:", e)

        # Start periodic updater
        if self._updater_task is None or self._updater_task.done():
            self._updater_task = asyncio.create_task(self._periodic_updater())

    def cog_unload(self):
        if self._updater_task:
            self._updater_task.cancel()


async def setup(bot: commands.Bot):
    await bot.add_cog(HellorLeaderboard(bot))
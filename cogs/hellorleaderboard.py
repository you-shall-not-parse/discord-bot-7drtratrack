#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import urllib.parse
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Optional

import discord
import requests
from bs4 import BeautifulSoup
from discord import app_commands
from discord.ext import commands, tasks
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from data_paths import data_path

GUILD_ID = 1097913605082579024
POST_CHANNEL_ID = 1099806153170489485
ROLE_NAME = "Basic trained"

STATE_FILE = data_path("hellor_leaderboard_state.json")
MAPPING_FILE = data_path("hellor_t17_map.json")
LOG_FILE = data_path("hellor_leaderboard.log")

UPDATE_INTERVAL_SECONDS = 12 * 3600
REQUEST_PACE_SECONDS = 1.85

CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"
CRCON_API_KEY = os.getenv("CRCON_API_KEY")
PLAYER_LOOKUP_CACHE_TTL_SECONDS = 3600
PLAYER_LOOKUP_NEGATIVE_CACHE_TTL_SECONDS = 120

BASE_HELLOR_URL = "https://hellor.pro/player/{}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Copilot-Chat-Scraper/1.0; +https://github.com/you-shall-not-parse)"
}

RANK_ORDER: list[tuple[str, list[str]]] = [
    ("FM", ["Field Marshal", "FM"]),
    ("GEN", ["General", "Gen"]),
    ("LTGEN", ["Lieutenant General", "Lt Gen", "Lt.Gen", "LtGen", "Lt-Gen"]),
    ("MAJGEN", ["Major General", "Maj Gen", "Maj.Gen", "MajGen", "Maj-Gen"]),
    ("BRIG", ["Brigadier", "Brig"]),
    ("COL", ["Colonel", "Col"]),
    ("LTCOL", ["Lieutenant Colonel", "Lt Col", "Lt. Col", "Lt.Col", "LtCol", "Lt-Col"]),
    ("MAJ", ["Major", "Maj"]),
    ("CPT", ["Captain", "Cpt"]),
    ("LT", ["Lieutenant", "Lt", "Lt."]),
    ("2LT", ["2nd Lieutenant", "2Lt", "2Lt.", "2ndLt", "2nd Lt", "2 Lt"]),
    ("RSM", ["Regimental Sergeant Major", "Regimental Sargent Major", "RSM"]),
    ("WO1", ["Warrant Officer 1st Class", "Warrant Officer 1", "WO1"]),
    ("WO2", ["Warrant Officer 2nd Class", "Warrant Officer 2", "WO2"]),
    ("SGM", ["Sergeant Major", "Sergeant major", "SGM"]),
    ("SSG", ["Staff Sergeant", "Staff Sargent", "SSG"]),
    ("SGT", ["Sergeant", "Sgt"]),
    ("CPL", ["Corporal", "Cpl"]),
    ("LCPL", ["Lance Corporal", "L.Cpl", "LCpl", "L Cpl"]),
    ("PTE", ["Private", "Pte", "Pte."]),
]
RANK_PREFIXES: list[str] = [variant for _code, variants in RANK_ORDER for variant in variants]
LEADERBOARD_LABELS = ["Overall", "Team", "Impact", "Fight"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def make_session(retries: int = 3, backoff_factor: float = 0.5) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HEADERS)
    return session


def extract_label_info_from_text(text: str, label: str) -> Optional[tuple[str, str]]:
    pattern = re.compile(
        rf"{re.escape(label)}\D*?(\d{{1,7}})(?:[^\d%]*)?(?:Top[:\s]*([0-9]+(?:\.[0-9]+)?)%)?",
        re.IGNORECASE,
    )
    match = pattern.search(text)
    if not match:
        return None
    raw_score = match.group(1)
    top_value = match.group(2)
    return raw_score, f"{top_value}%" if top_value else "N/A"


def find_label_nearby(soup: BeautifulSoup, label: str) -> Optional[tuple[str, str]]:
    nodes = soup.find_all(string=re.compile(rf"\b{re.escape(label)}\b", re.IGNORECASE))
    for node in nodes:
        current = node.parent
        for _ in range(4):
            if current is None:
                break
            text = current.get_text(" ", strip=True)
            found = extract_label_info_from_text(text, label)
            if found:
                return found
            current = current.parent
    return None


def parse_scores(html: str) -> dict[str, dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    full_text = soup.get_text(" ", strip=True)
    results: dict[str, dict[str, str]] = {}

    for label in LEADERBOARD_LABELS:
        found = find_label_nearby(soup, label)
        if not found:
            found = extract_label_info_from_text(full_text, label)
        results[label] = {
            "score": found[0] if found else "N/A",
            "top": found[1] if found else "N/A",
        }
    return results


def score_as_int(value: str) -> int:
    return int(value) if value.isdigit() else -1


class HellorLeaderboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session = make_session()
        self._update_lock = asyncio.Lock()
        self._player_id_cache: dict[str, tuple[str | None, float]] = {}
        self._synced = False
        self._initial_posted = False
        self.logger = self._build_logger()
        self.leaderboard_message_id = self._load_leaderboard_message_id()

    def cog_unload(self):
        if self.post_leaderboard.is_running():
            self.post_leaderboard.cancel()
        self.session.close()

    def _build_logger(self) -> logging.Logger:
        logger = logging.getLogger("HellorLeaderboard")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        existing = [
            handler
            for handler in logger.handlers
            if isinstance(handler, RotatingFileHandler) and getattr(handler, "baseFilename", None) == LOG_FILE
        ]
        if not existing:
            handler = RotatingFileHandler(LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
            handler.setLevel(logging.DEBUG)
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)

        return logger

    def _load_json_file(self, path: str) -> dict[str, Any]:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_json_file(self, path: str, data: dict[str, Any]) -> None:
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        os.replace(tmp_path, path)

    def _load_state(self) -> dict[str, Any]:
        return self._load_json_file(STATE_FILE)

    def _save_state(self, state: dict[str, Any]) -> None:
        self._save_json_file(STATE_FILE, state)

    def _load_leaderboard_message_id(self) -> Optional[int]:
        state = self._load_state()
        if state.get("channel_id") != POST_CHANNEL_ID:
            return None
        message_id = state.get("message_id")
        return message_id if isinstance(message_id, int) else None

    def _save_leaderboard_message_id(self, message_id: Optional[int]) -> None:
        self.leaderboard_message_id = message_id
        self._save_state(
            {
                "channel_id": POST_CHANNEL_ID,
                "message_id": message_id,
                "updated_at": utc_now_iso(),
            }
        )

    def _empty_mapping(self) -> dict[str, Any]:
        return {
            "manual_overrides": {},
            "name_cache": {},
            "resolved_members": {},
            "updated_at": None,
        }

    def _load_mapping(self) -> dict[str, Any]:
        raw = self._load_json_file(MAPPING_FILE)
        mapping = self._empty_mapping()

        if set(raw.keys()) >= {"manual_overrides", "name_cache", "resolved_members"}:
            mapping.update(raw)
            return mapping

        for key, value in raw.items():
            if isinstance(value, str):
                mapping["name_cache"][key] = {
                    "t17_id": value,
                    "source": "legacy",
                    "updated_at": None,
                }

        if raw:
            mapping["updated_at"] = utc_now_iso()
        return mapping

    def _save_mapping(self, mapping: dict[str, Any]) -> None:
        mapping["updated_at"] = utc_now_iso()
        self._save_json_file(MAPPING_FILE, mapping)

    def _member_key(self, guild_id: int, user_id: int) -> str:
        return f"{guild_id}:{user_id}"

    def _cut_at_hash(self, text: str) -> str:
        value = (text or "").strip()
        if not value:
            return ""
        if "#" in value:
            value = value.split("#", 1)[0].strip()
        return " ".join(value.split())

    def _normalize_discord_username(self, name: str, *, strip_rank_prefix: bool = False) -> str:
        name = self._cut_at_hash(name)
        name = name.replace("%", " ")
        name = " ".join(name.split())

        if strip_rank_prefix:
            rank_pattern = r"^(?:" + "|".join(re.escape(prefix) for prefix in RANK_PREFIXES) + r")\.?\s+"
            name = re.sub(rank_pattern, "", name, flags=re.IGNORECASE).strip()

        return name

    def _build_lookup_queries(self, member: discord.Member) -> list[str]:
        raw_candidates: list[str] = []
        if member.display_name:
            raw_candidates.append(member.display_name)
        raw_candidates.append(member.name)
        global_name = getattr(member, "global_name", None)
        if global_name:
            raw_candidates.append(global_name)

        queries: list[str] = []
        seen: set[str] = set()

        for raw in raw_candidates:
            cut = self._normalize_discord_username(raw, strip_rank_prefix=False)
            if cut:
                lowered = cut.lower()
                if lowered not in seen:
                    seen.add(lowered)
                    queries.append(cut)

            stripped = self._normalize_discord_username(raw, strip_rank_prefix=True)
            if stripped:
                lowered = stripped.lower()
                if lowered not in seen:
                    seen.add(lowered)
                    queries.append(stripped)

        return queries

    def _extract_first_player_id(self, data: Any) -> str | None:
        if isinstance(data, dict):
            if "player_id" in data and data["player_id"] is not None:
                return str(data["player_id"])
            for value in data.values():
                found = self._extract_first_player_id(value)
                if found:
                    return found
            return None
        if isinstance(data, list):
            for item in data:
                found = self._extract_first_player_id(item)
                if found:
                    return found
        return None

    async def _rcon_get(self, endpoint: str) -> dict[str, Any]:
        if not CRCON_API_KEY:
            return {"error": "CRCON_API_KEY is not set"}

        url = CRCON_PANEL_URL + endpoint

        def do_request() -> dict[str, Any]:
            try:
                response = requests.get(
                    url,
                    headers={"Authorization": f"Bearer {CRCON_API_KEY}"},
                    timeout=10,
                )
                return response.json()
            except Exception as exc:
                return {"error": str(exc)}

        return await asyncio.to_thread(do_request)

    async def _fetch_player_id_cached(self, player_name: str) -> tuple[str | None, bool]:
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

    def _read_name_cache(self, mapping: dict[str, Any], query: str) -> str | None:
        entry = mapping.get("name_cache", {}).get(query.lower())
        if isinstance(entry, str):
            return entry
        if isinstance(entry, dict):
            t17_id = entry.get("t17_id")
            return str(t17_id) if t17_id else None
        return None

    def _write_name_cache(self, mapping: dict[str, Any], queries: list[str], t17_id: str, source: str) -> None:
        for query in queries:
            mapping["name_cache"][query.lower()] = {
                "t17_id": t17_id,
                "source": source,
                "updated_at": utc_now_iso(),
            }

    def _store_resolved_member(
        self,
        mapping: dict[str, Any],
        member: discord.Member,
        *,
        t17_id: str | None,
        source: str,
        queries: list[str],
    ) -> None:
        mapping["resolved_members"][self._member_key(member.guild.id, member.id)] = {
            "guild_id": member.guild.id,
            "role_name": ROLE_NAME,
            "user_id": member.id,
            "display_name": member.display_name,
            "username": member.name,
            "global_name": getattr(member, "global_name", None),
            "t17_id": t17_id,
            "source": source,
            "lookup_queries": queries,
            "updated_at": utc_now_iso(),
        }

    def _prune_resolved_members(self, mapping: dict[str, Any], guild_id: int, active_member_ids: set[int]) -> None:
        keep: dict[str, Any] = {}
        prefix = f"{guild_id}:"
        for key, value in mapping.get("resolved_members", {}).items():
            if not key.startswith(prefix):
                keep[key] = value
                continue

            _, user_id_raw = key.split(":", 1)
            if user_id_raw.isdigit() and int(user_id_raw) in active_member_ids:
                keep[key] = value

        mapping["resolved_members"] = keep

    async def _resolve_t17_for_member(self, member: discord.Member, mapping: dict[str, Any]) -> tuple[str | None, str, list[str]]:
        member_key = self._member_key(member.guild.id, member.id)
        queries = self._build_lookup_queries(member)
        self.logger.info(
            "resolve_start member_id=%s display_name=%r username=%r queries=%s",
            member.id,
            member.display_name,
            member.name,
            queries,
        )

        manual_entry = mapping.get("manual_overrides", {}).get(member_key)
        if isinstance(manual_entry, dict) and manual_entry.get("t17_id"):
            t17_id = str(manual_entry["t17_id"])
            self._write_name_cache(mapping, queries, t17_id, "manual_override")
            self._store_resolved_member(mapping, member, t17_id=t17_id, source="manual_override", queries=queries)
            self.logger.info("resolve_manual_override member_id=%s t17_id=%s", member.id, t17_id)
            return t17_id, "manual_override", queries

        for query in queries:
            cached_t17 = self._read_name_cache(mapping, query)
            if cached_t17:
                self._store_resolved_member(mapping, member, t17_id=cached_t17, source="name_cache", queries=queries)
                self.logger.info("resolve_name_cache_hit member_id=%s query=%r t17_id=%s", member.id, query, cached_t17)
                return cached_t17, "name_cache", queries

        for query in queries:
            self.logger.info("resolve_crcon_try member_id=%s query=%r", member.id, query)
            t17_id, did_http = await self._fetch_player_id_cached(query)
            self.logger.info(
                "resolve_crcon_result member_id=%s query=%r did_http=%s t17_id=%s",
                member.id,
                query,
                did_http,
                t17_id,
            )
            if t17_id:
                self._write_name_cache(mapping, queries, t17_id, "crcon")
                self._store_resolved_member(mapping, member, t17_id=t17_id, source="crcon", queries=queries)
                return t17_id, "crcon", queries

        self._store_resolved_member(mapping, member, t17_id=None, source="unresolved", queries=queries)
        self.logger.info("resolve_failed member_id=%s queries=%s", member.id, queries)
        return None, "unresolved", queries

    def _fetch_hellor_html(self, t17_id: str) -> str:
        response = self.session.get(BASE_HELLOR_URL.format(t17_id), timeout=10)
        response.raise_for_status()
        return response.text

    async def _get_post_channel(self) -> Optional[discord.TextChannel]:
        channel = self.bot.get_channel(POST_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(POST_CHANNEL_ID)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None

        return channel if isinstance(channel, discord.TextChannel) else None

    def _build_empty_embed(self, description: str) -> discord.Embed:
        embed = discord.Embed(title="hellor.pro Leaderboard", description=description, color=discord.Color.orange())
        embed.timestamp = utc_now()
        return embed

    async def _collect_basic_trained_targets(self, guild: discord.Guild) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        mapping = self._load_mapping()
        role = discord.utils.get(guild.roles, name=ROLE_NAME)
        if role is None:
            raise RuntimeError(f"Role '{ROLE_NAME}' not found")

        members = sorted(role.members, key=lambda member: member.display_name.lower())
        self._prune_resolved_members(mapping, guild.id, {member.id for member in members})

        targets: list[dict[str, Any]] = []
        unresolved: list[str] = []

        for member in members:
            t17_id, source, queries = await self._resolve_t17_for_member(member, mapping)
            if not t17_id:
                unresolved.append(member.display_name)
                continue
            targets.append(
                {
                    "member_id": member.id,
                    "display_name": member.display_name,
                    "t17_id": t17_id,
                    "source": source,
                    "queries": queries,
                }
            )

        self._save_mapping(mapping)
        self.logger.info(
            "resolve_summary guild_id=%s role=%r members=%s resolved=%s unresolved=%s",
            guild.id,
            ROLE_NAME,
            len(members),
            len(targets),
            len(unresolved),
        )
        if unresolved:
            self.logger.info("resolve_unresolved_sample guild_id=%s sample=%s", guild.id, unresolved[:20])

        return targets, mapping

    async def _fetch_member_scores(self, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []

        for index, target in enumerate(targets):
            if index:
                await asyncio.sleep(REQUEST_PACE_SECONDS)

            self.logger.info(
                "hellor_fetch_start member_id=%s display_name=%r t17_id=%s",
                target["member_id"],
                target["display_name"],
                target["t17_id"],
            )
            try:
                html = await asyncio.to_thread(self._fetch_hellor_html, target["t17_id"])
                scores = parse_scores(html)
            except Exception as exc:
                self.logger.exception(
                    "hellor_fetch_failed member_id=%s display_name=%r t17_id=%s error=%s",
                    target["member_id"],
                    target["display_name"],
                    target["t17_id"],
                    exc,
                )
                continue

            self.logger.info(
                "hellor_parse_result member_id=%s display_name=%r t17_id=%s scores=%s",
                target["member_id"],
                target["display_name"],
                target["t17_id"],
                scores,
            )
            results.append({**target, "scores": scores})

        return results

    def _build_leaderboard_embed(self, guild: discord.Guild, member_scores: list[dict[str, Any]]) -> discord.Embed:
        embed = discord.Embed(
            title=f"hellor.pro Leaderboard - {ROLE_NAME}",
            color=discord.Color.gold(),
            timestamp=utc_now(),
        )
        embed.set_footer(text=f"Updated for {guild.name}")

        if not member_scores:
            embed.description = "No Basic trained members could be resolved to a T17 ID."
            return embed

        for label in LEADERBOARD_LABELS:
            ranking = sorted(
                member_scores,
                key=lambda item: (
                    -score_as_int(item["scores"][label]["score"]),
                    item["display_name"].lower(),
                ),
            )[:10]

            lines: list[str] = []
            for index, item in enumerate(ranking, start=1):
                score_value = item["scores"][label]["score"]
                top_value = item["scores"][label]["top"]
                suffix = f" (Top {top_value})" if top_value != "N/A" else ""
                lines.append(f"{index}. {item['display_name']} - {score_value}{suffix}")

            embed.add_field(name=label, value="\n".join(lines) if lines else "None", inline=False)

        return embed

    async def build_embed(self, guild: discord.Guild) -> discord.Embed:
        try:
            targets, _mapping = await self._collect_basic_trained_targets(guild)
        except RuntimeError as exc:
            self.logger.error("build_embed_failed guild_id=%s error=%s", guild.id, exc)
            return self._build_empty_embed(str(exc))

        member_scores = await self._fetch_member_scores(targets)
        self.logger.info(
            "hellor_summary guild_id=%s resolved_targets=%s parsed_profiles=%s",
            guild.id,
            len(targets),
            len(member_scores),
        )
        return self._build_leaderboard_embed(guild, member_scores)

    async def update_or_post_leaderboard(self) -> None:
        async with self._update_lock:
            channel = await self._get_post_channel()
            if channel is None:
                self.logger.error("post_channel_missing channel_id=%s", POST_CHANNEL_ID)
                return

            self.logger.info(
                "update_start guild_id=%s channel_id=%s existing_message_id=%s",
                channel.guild.id,
                channel.id,
                self.leaderboard_message_id,
            )
            embed = await self.build_embed(channel.guild)

            if self.leaderboard_message_id:
                try:
                    message = await channel.fetch_message(self.leaderboard_message_id)
                    await message.edit(embed=embed)
                    self.logger.info("update_edit_success message_id=%s", self.leaderboard_message_id)
                    return
                except discord.NotFound:
                    self.logger.info("update_existing_message_missing message_id=%s", self.leaderboard_message_id)
                    self.leaderboard_message_id = None
                except discord.Forbidden:
                    self.logger.error("update_edit_forbidden message_id=%s", self.leaderboard_message_id)
                    return
                except discord.HTTPException as exc:
                    self.logger.error("update_edit_failed message_id=%s error=%s", self.leaderboard_message_id, exc)
                    return

            message = await channel.send(embed=embed)
            self._save_leaderboard_message_id(message.id)
            self.logger.info("update_post_success message_id=%s", message.id)

    def _can_manage_leaderboard(self, interaction: discord.Interaction) -> bool:
        user = interaction.user
        return isinstance(user, discord.Member) and user.guild_permissions.manage_guild

    @app_commands.command(name="set_hellor_t17", description="Override a Basic trained member's T17 ID for the hellor leaderboard")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def set_hellor_t17(self, interaction: discord.Interaction, member: discord.Member, t17_id: str):
        if not self._can_manage_leaderboard(interaction):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        role = discord.utils.get(guild.roles, name=ROLE_NAME)
        if role is None:
            await interaction.response.send_message(f"Role '{ROLE_NAME}' was not found.", ephemeral=True)
            return
        if role not in member.roles:
            await interaction.response.send_message(f"{member.display_name} does not currently have the '{ROLE_NAME}' role.", ephemeral=True)
            return

        clean_t17_id = t17_id.strip()
        if not clean_t17_id:
            await interaction.response.send_message("Provide a non-empty T17 ID.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        mapping = self._load_mapping()
        member_key = self._member_key(guild.id, member.id)
        mapping["manual_overrides"][member_key] = {
            "t17_id": clean_t17_id,
            "updated_at": utc_now_iso(),
            "updated_by": interaction.user.id,
        }

        queries = self._build_lookup_queries(member)
        self._write_name_cache(mapping, queries, clean_t17_id, "manual_override")
        self._store_resolved_member(mapping, member, t17_id=clean_t17_id, source="manual_override", queries=queries)
        self._save_mapping(mapping)

        self.logger.info(
            "manual_override_set guild_id=%s target_member_id=%s target_display_name=%r t17_id=%s updated_by=%s",
            guild.id,
            member.id,
            member.display_name,
            clean_t17_id,
            interaction.user.id,
        )

        try:
            await self.update_or_post_leaderboard()
        except Exception as exc:
            self.logger.exception("manual_override_refresh_failed target_member_id=%s error=%s", member.id, exc)
            await interaction.followup.send(
                f"Stored override for {member.display_name} -> {clean_t17_id}, but refreshing the leaderboard failed: {exc}",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"Stored override for {member.display_name} -> {clean_t17_id} and refreshed the leaderboard.",
            ephemeral=True,
        )

    @app_commands.command(name="refresh_hellor_leaderboard", description="Force a refresh of the hellor leaderboard")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def refresh_hellor_leaderboard(self, interaction: discord.Interaction):
        if not self._can_manage_leaderboard(interaction):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.update_or_post_leaderboard()
        except Exception as exc:
            self.logger.exception("manual_refresh_failed error=%s", exc)
            await interaction.followup.send(f"Refresh failed: {exc}", ephemeral=True)
            return

        await interaction.followup.send("Leaderboard refreshed.", ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._synced:
            try:
                await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))
                self._synced = True
            except Exception as exc:
                self.logger.exception("command_sync_failed error=%s", exc)

        if not self._initial_posted:
            try:
                await self.update_or_post_leaderboard()
            except Exception as exc:
                self.logger.exception("initial_update_failed error=%s", exc)
            self._initial_posted = True

        if not self.post_leaderboard.is_running():
            self.post_leaderboard.start()

    @tasks.loop(seconds=UPDATE_INTERVAL_SECONDS)
    async def post_leaderboard(self):
        try:
            await self.update_or_post_leaderboard()
        except Exception as exc:
            self.logger.exception("scheduled_update_failed error=%s", exc)

    @post_leaderboard.before_loop
    async def before_post_leaderboard(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(HellorLeaderboard(bot))
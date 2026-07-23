from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any

import discord

from data_paths import data_path
from hll_API_backend import HLLBackendClient, get_hll_backend_client
from state_io import atomic_json_dump

CLAN_T17_MAP_FILE = data_path("clan_t17_map.json")
T17_LOG_FILE = data_path("t17_lookup.log")
PLAYER_LOOKUP_CACHE_TTL_SECONDS = 3600
PLAYER_LOOKUP_NEGATIVE_CACHE_TTL_SECONDS = 120

DEFAULT_RANK_ORDER: list[tuple[str, list[str]]] = [
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
DEFAULT_RANK_PREFIXES: list[str] = [variant for _code, variants in DEFAULT_RANK_ORDER for variant in variants]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def get_t17_logger() -> logging.Logger:
    logger = logging.getLogger("ClanT17Lookup")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    existing = [
        handler
        for handler in logger.handlers
        if isinstance(handler, RotatingFileHandler) and getattr(handler, "baseFilename", None) == T17_LOG_FILE
    ]
    if not existing:
        handler = RotatingFileHandler(T17_LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)

    return logger


class ClanT17Lookup:
    def __init__(self, backend: HLLBackendClient | None = None, *, logger: logging.Logger | None = None):
        self.logger = logger or get_t17_logger()
        self._backend = backend
        self._player_id_cache: dict[str, tuple[str | None, float]] = {}

    @property
    def backend(self) -> HLLBackendClient:
        if self._backend is None:
            self._backend = get_hll_backend_client()
        return self._backend

    def backend_source_name(self) -> str:
        provider = getattr(self._backend, "provider", "backend")
        value = str(provider).strip().lower()
        return value or "backend"

    def _load_json_file(self, path: str) -> dict[str, Any]:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_json_file(self, path: str, data: dict[str, Any]) -> None:
        atomic_json_dump(path, data, sort_keys=True)

    def empty_mapping(self) -> dict[str, Any]:
        return {
            "manual_overrides": {},
            "name_cache": {},
            "resolved_members": {},
            "updated_at": None,
        }

    def load_mapping(self) -> dict[str, Any]:
        raw = self._load_json_file(CLAN_T17_MAP_FILE)
        mapping = self.empty_mapping()

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

    def save_mapping(self, mapping: dict[str, Any]) -> None:
        mapping["updated_at"] = utc_now_iso()
        self._save_json_file(CLAN_T17_MAP_FILE, mapping)

    def member_key(self, guild_id: int, user_id: int) -> str:
        return f"{guild_id}:{user_id}"

    def resolved_member_key(self, guild_id: int, user_id: int, role_name: str) -> str:
        return f"{guild_id}:{role_name}:{user_id}"

    def cut_at_hash(self, text: str) -> str:
        value = (text or "").strip()
        if not value:
            return ""
        if "#" in value:
            value = value.split("#", 1)[0].strip()
        return " ".join(value.split())

    def normalize_discord_username(self, name: str, *, strip_rank_prefix: bool = False) -> str:
        normalized = self.cut_at_hash(name)
        normalized = normalized.replace("%", " ")
        normalized = " ".join(normalized.split())

        if strip_rank_prefix:
            rank_pattern = r"^(?:" + "|".join(re.escape(prefix) for prefix in DEFAULT_RANK_PREFIXES) + r")\.?\s+"
            normalized = re.sub(rank_pattern, "", normalized, flags=re.IGNORECASE).strip()

        return normalized

    def build_lookup_queries(
        self,
        member: discord.Member,
        *,
        include_username: bool = True,
        include_global_name: bool = True,
    ) -> list[str]:
        raw_candidates: list[str] = []
        if member.display_name:
            raw_candidates.append(member.display_name)
        if include_username and member.name:
            raw_candidates.append(member.name)

        global_name = getattr(member, "global_name", None)
        if include_global_name and global_name:
            raw_candidates.append(global_name)

        queries: list[str] = []
        seen: set[str] = set()

        for raw in raw_candidates:
            cut = self.normalize_discord_username(raw, strip_rank_prefix=False)
            if cut:
                lowered = cut.lower()
                if lowered not in seen:
                    seen.add(lowered)
                    queries.append(cut)

            stripped = self.normalize_discord_username(raw, strip_rank_prefix=True)
            if stripped:
                lowered = stripped.lower()
                if lowered not in seen:
                    seen.add(lowered)
                    queries.append(stripped)

        return queries

    def extract_first_player_id(self, data: Any) -> str | None:
        if isinstance(data, dict):
            if "player_id" in data and data["player_id"] is not None:
                return str(data["player_id"])
            for value in data.values():
                found = self.extract_first_player_id(value)
                if found:
                    return found
            return None
        if isinstance(data, list):
            for item in data:
                found = self.extract_first_player_id(item)
                if found:
                    return found
        return None

    async def fetch_player_id_cached(self, player_name: str) -> tuple[str | None, bool]:
        normalized = self.normalize_discord_username(player_name, strip_rank_prefix=False)
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

        try:
            player_id = await self.backend.resolve_player_id_by_name(normalized)
        except Exception as exc:
            self.logger.warning("player_lookup_failed query=%r error=%s", normalized, exc)
            self._player_id_cache[key] = (None, now)
            return None, True

        self._player_id_cache[key] = (player_id, now)
        return player_id, True

    def read_name_cache(self, mapping: dict[str, Any], query: str) -> str | None:
        entry = mapping.get("name_cache", {}).get(query.lower())
        if isinstance(entry, str):
            return entry
        if isinstance(entry, dict):
            t17_id = entry.get("t17_id")
            return str(t17_id) if t17_id else None
        return None

    def write_name_cache(self, mapping: dict[str, Any], queries: list[str], t17_id: str, source: str) -> None:
        for query in queries:
            mapping["name_cache"][query.lower()] = {
                "t17_id": t17_id,
                "source": source,
                "updated_at": utc_now_iso(),
            }

    def store_resolved_member(
        self,
        mapping: dict[str, Any],
        member: discord.Member,
        *,
        role_name: str,
        t17_id: str | None,
        source: str,
        queries: list[str],
    ) -> None:
        mapping["resolved_members"][self.resolved_member_key(member.guild.id, member.id, role_name)] = {
            "guild_id": member.guild.id,
            "role_name": role_name,
            "user_id": member.id,
            "display_name": member.display_name,
            "username": member.name,
            "global_name": getattr(member, "global_name", None),
            "t17_id": t17_id,
            "source": source,
            "lookup_queries": queries,
            "updated_at": utc_now_iso(),
        }

    def prune_resolved_members(
        self, mapping: dict[str, Any], guild_id: int, role_name: str, active_member_ids: set[int]
    ) -> None:
        keep: dict[str, Any] = {}
        for key, value in mapping.get("resolved_members", {}).items():
            if not isinstance(value, dict):
                continue
            if value.get("guild_id") != guild_id or value.get("role_name") != role_name:
                keep[key] = value
                continue

            user_id = value.get("user_id")
            if isinstance(user_id, int) and user_id in active_member_ids:
                keep[key] = value

        mapping["resolved_members"] = keep

    async def resolve_member_with_mapping(
        self,
        mapping: dict[str, Any],
        member: discord.Member,
        *,
        role_name: str,
        include_username: bool = True,
        include_global_name: bool = True,
    ) -> tuple[str | None, str, list[str]]:
        member_key = self.member_key(member.guild.id, member.id)
        backend_source = self.backend_source_name()
        queries = self.build_lookup_queries(
            member,
            include_username=include_username,
            include_global_name=include_global_name,
        )
        self.logger.debug(
            "resolve_start member_id=%s display_name=%r username=%r role_name=%r queries=%s",
            member.id,
            member.display_name,
            member.name,
            role_name,
            queries,
        )

        manual_entry = mapping.get("manual_overrides", {}).get(member_key)
        if isinstance(manual_entry, dict) and manual_entry.get("t17_id"):
            t17_id = str(manual_entry["t17_id"])
            self.write_name_cache(mapping, queries, t17_id, "manual_override")
            self.store_resolved_member(
                mapping,
                member,
                role_name=role_name,
                t17_id=t17_id,
                source="manual_override",
                queries=queries,
            )
            self.logger.debug("resolve_manual_override member_id=%s t17_id=%s", member.id, t17_id)
            return t17_id, "manual_override", queries

        # Check resolved_members by stable Discord user ID — survives display name changes
        resolved_key = self.resolved_member_key(member.guild.id, member.id, role_name)
        existing = mapping.get("resolved_members", {}).get(resolved_key)
        if isinstance(existing, dict) and existing.get("t17_id"):
            t17_id = str(existing["t17_id"])
            self.write_name_cache(mapping, queries, t17_id, "resolved_members")
            self.store_resolved_member(
                mapping,
                member,
                role_name=role_name,
                t17_id=t17_id,
                source="resolved_members",
                queries=queries,
            )
            self.logger.debug("resolve_user_id_hit member_id=%s t17_id=%s", member.id, t17_id)
            return t17_id, "resolved_members", queries

        for query in queries:
            cached_t17 = self.read_name_cache(mapping, query)
            if cached_t17:
                self.store_resolved_member(
                    mapping,
                    member,
                    role_name=role_name,
                    t17_id=cached_t17,
                    source="name_cache",
                    queries=queries,
                )
                self.logger.debug("resolve_name_cache_hit member_id=%s query=%r t17_id=%s", member.id, query, cached_t17)
                return cached_t17, "name_cache", queries

        for query in queries:
            self.logger.debug("resolve_backend_try member_id=%s backend=%s query=%r", member.id, backend_source, query)
            t17_id, did_http = await self.fetch_player_id_cached(query)
            self.logger.debug(
                "resolve_backend_result member_id=%s backend=%s query=%r did_http=%s t17_id=%s",
                member.id,
                backend_source,
                query,
                did_http,
                t17_id,
            )
            if t17_id:
                self.write_name_cache(mapping, queries, t17_id, backend_source)
                self.store_resolved_member(
                    mapping,
                    member,
                    role_name=role_name,
                    t17_id=t17_id,
                    source=backend_source,
                    queries=queries,
                )
                return t17_id, backend_source, queries

        self.store_resolved_member(
            mapping,
            member,
            role_name=role_name,
            t17_id=None,
            source="unresolved",
            queries=queries,
        )
        self.logger.debug("resolve_failed member_id=%s role_name=%r queries=%s", member.id, role_name, queries)
        return None, "unresolved", queries

    async def resolve_member_for_role(
        self,
        member: discord.Member,
        *,
        role_name: str,
        include_username: bool = True,
        include_global_name: bool = True,
    ) -> tuple[str | None, str, list[str]]:
        mapping = self.load_mapping()
        result = await self.resolve_member_with_mapping(
            mapping,
            member,
            role_name=role_name,
            include_username=include_username,
            include_global_name=include_global_name,
        )
        self.save_mapping(mapping)
        return result

    async def resolve_members_for_role(
        self,
        members: list[discord.Member],
        *,
        role_name: str,
        include_username: bool = True,
        include_global_name: bool = True,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
        mapping = self.load_mapping()
        guild_id = members[0].guild.id if members else None
        if guild_id is not None:
            self.prune_resolved_members(mapping, guild_id, role_name, {member.id for member in members})

        targets: list[dict[str, Any]] = []
        unresolved: list[str] = []

        for member in members:
            t17_id, source, queries = await self.resolve_member_with_mapping(
                mapping,
                member,
                role_name=role_name,
                include_username=include_username,
                include_global_name=include_global_name,
            )
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

        self.save_mapping(mapping)
        return targets, mapping, unresolved

    def resolved_members_for_role(self, guild_id: int, role_name: str) -> list[dict[str, Any]]:
        mapping = self.load_mapping()
        members: list[dict[str, Any]] = []
        for entry in mapping.get("resolved_members", {}).values():
            if not isinstance(entry, dict):
                continue
            if entry.get("guild_id") != guild_id:
                continue
            if entry.get("role_name") != role_name:
                continue
            members.append(entry)

        members.sort(key=lambda item: str(item.get("display_name") or item.get("username") or "").lower())
        return members

    def get_resolved_member(self, guild_id: int, user_id: int, *, role_name: str) -> dict[str, Any] | None:
        mapping = self.load_mapping()
        entry = mapping.get("resolved_members", {}).get(self.resolved_member_key(guild_id, user_id, role_name))
        if not isinstance(entry, dict):
            return None
        if entry.get("role_name") != role_name:
            return None
        return entry

    def set_manual_override(self, guild_id: int, user_id: int, t17_id: str, *, updated_by: int) -> None:
        mapping = self.load_mapping()
        mapping.setdefault("manual_overrides", {})[self.member_key(guild_id, user_id)] = {
            "t17_id": t17_id,
            "updated_at": utc_now_iso(),
            "updated_by": updated_by,
        }
        self.save_mapping(mapping)

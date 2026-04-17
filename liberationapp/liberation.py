from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import aiohttp
import asyncpg
from aiohttp import web
from redis import asyncio as redis


LOG = logging.getLogger("liberation_api")

APP_DIR = Path(__file__).resolve().parent
LIBERATION_DATA_DIR = Path(os.getenv("LIBERATION_DATA_DIR", APP_DIR / "data"))
LIBERATION_DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://liberation:liberation@localhost:5432/liberation")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
LIBERATION_PORT = int(os.getenv("LIBERATION_PORT", os.getenv("PORT", "8080")))
LIBERATION_HOST = os.getenv("LIBERATION_HOST", "0.0.0.0")
LIBERATION_POLL_SECONDS = max(3, int(os.getenv("LIBERATION_POLL_SECONDS", "10")))
LIBERATION_TARGET_KILLS = max(1, int(os.getenv("LIBERATION_TARGET_KILLS", "500")))
LIBERATION_RECENT_LOG_LIMIT = max(10, int(os.getenv("LIBERATION_RECENT_LOG_LIMIT", "250")))
LIBERATION_CACHE_TTL_SECONDS = max(3, int(os.getenv("LIBERATION_CACHE_TTL_SECONDS", "15")))
LIBERATION_PROCESSED_EVENT_KEEP = max(1000, int(os.getenv("LIBERATION_PROCESSED_EVENT_KEEP", "5000")))
LIBERATION_IMPORT_RECENT_ON_START = os.getenv("LIBERATION_IMPORT_RECENT_ON_START", "false").lower() == "true"
LIBERATION_SERVERS_FILE = os.getenv("LIBERATION_SERVERS_FILE")

MAP_ID_TO_PRETTY: dict[str, str] = {
	"elsenbornridge_warfare_morning": "Elsenborn Ridge Warfare (Dawn)",
	"carentan_warfare": "Carentan Warfare",
	"foy_warfare": "Foy Warfare",
	"hill400_warfare": "Hill 400 Warfare",
	"stmariedumont_warfare": "St. Marie Du Mont Warfare",
	"utahbeach_warfare": "Utah Beach Warfare",
	"stmereeglise_warfare": "St. Mere Eglise Warfare",
	"elalamein_warfare": "El Alamein Warfare",
	"mortain_warfare_dusk": "Mortain Warfare (Dusk)",
	"driel_warfare": "Driel Warfare",
	"kursk_warfare": "Kursk Warfare",
	"hurtgenforest_warfare_v2": "Hurtgen Forest Warfare",
	"remagen_warfare": "Remagen Warfare",
	"omahabeach_warfare": "Omaha Beach Warfare",
	"kharkov_warfare": "Kharkov Warfare",
	"phl_l_1944_warfare": "Purple Heart Lane Warfare (Rain)",
	"tobruk_warfare_morning": "Tobruk Warfare (Dawn)",
	"carentan_warfare_night": "Carentan Warfare (Night)",
	"smolensk_warfare_dusk": "Smolensk Warfare (Dusk)",
	"stalingrad_warfare": "Stalingrad Warfare",
}
PRETTY_TO_MAP_ID: dict[str, str] = {value.casefold(): key for key, value in MAP_ID_TO_PRETTY.items()}

TEAM_GUID_RE = re.compile(r"\((Allies|Axis)(?:/[0-9a-f]{8,})?\)", re.IGNORECASE)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS servers (
  server_id TEXT PRIMARY KEY,
  server_name TEXT NOT NULL,
  base_url TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS map_sessions (
  session_id BIGSERIAL PRIMARY KEY,
  server_id TEXT NOT NULL REFERENCES servers(server_id) ON DELETE CASCADE,
  map_name TEXT NOT NULL,
  map_id TEXT,
  allied_kills INTEGER NOT NULL DEFAULT 0,
  axis_kills INTEGER NOT NULL DEFAULT 0,
  started_at TIMESTAMPTZ NOT NULL,
  last_seen_at TIMESTAMPTZ NOT NULL,
  ended_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS server_state (
  server_id TEXT PRIMARY KEY REFERENCES servers(server_id) ON DELETE CASCADE,
  current_map TEXT,
  current_map_id TEXT,
  active_session_id BIGINT,
  last_poll_at TIMESTAMPTZ,
  last_error TEXT
);

CREATE TABLE IF NOT EXISTS processed_events (
  server_id TEXT NOT NULL REFERENCES servers(server_id) ON DELETE CASCADE,
  event_key TEXT NOT NULL,
  session_id BIGINT REFERENCES map_sessions(session_id) ON DELETE SET NULL,
  timestamp_ms BIGINT NOT NULL DEFAULT 0,
  team TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (server_id, event_key)
);

CREATE TABLE IF NOT EXISTS map_totals (
  server_id TEXT NOT NULL REFERENCES servers(server_id) ON DELETE CASCADE,
  map_name TEXT NOT NULL,
  map_id TEXT,
  allied_kills INTEGER NOT NULL DEFAULT 0,
  axis_kills INTEGER NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (server_id, map_name)
);

CREATE INDEX IF NOT EXISTS idx_processed_events_server_ts
  ON processed_events (server_id, timestamp_ms DESC);

CREATE INDEX IF NOT EXISTS idx_map_sessions_server_started
  ON map_sessions (server_id, started_at DESC);
"""


@dataclass(slots=True)
class ServerConfig:
	server_id: str
	name: str
	base_url: str
	api_key: str


@dataclass(slots=True)
class KillEvent:
	event_key: str
	timestamp_ms: int
	team: str | None


def utc_now() -> datetime:
	return datetime.now(timezone.utc)


def json_default(value: Any) -> Any:
	if isinstance(value, datetime):
		return value.isoformat()
	raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def normalize_slug(value: str) -> str:
	return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def canonical_map_name(map_name: str | None, map_id: str | None = None) -> str:
	if map_name:
		known_id = PRETTY_TO_MAP_ID.get(map_name.casefold())
		if known_id:
			return MAP_ID_TO_PRETTY[known_id]
		return map_name.strip()

	if map_id:
		return MAP_ID_TO_PRETTY.get(map_id.casefold(), map_id.strip())

	return "Unknown Map"


def normalize_team(value: str | None) -> str | None:
	if not value:
		return None
	lowered = value.casefold()
	if "all" in lowered:
		return "allies"
	if "axis" in lowered:
		return "axis"
	return None


def extract_log_items(payload: Any) -> list[dict[str, Any]]:
	if isinstance(payload, list):
		return [item for item in payload if isinstance(item, dict)]

	if not isinstance(payload, dict):
		return []

	result = payload.get("result", payload)
	if isinstance(result, list):
		return [item for item in result if isinstance(item, dict)]

	if isinstance(result, dict):
		for key in ("logs", "items", "data", "results"):
			value = result.get(key)
			if isinstance(value, list):
				return [item for item in value if isinstance(item, dict)]

	for key in ("logs", "items", "data", "results"):
		value = payload.get(key)
		if isinstance(value, list):
			return [item for item in value if isinstance(item, dict)]

	return []


def extract_event_key(item: dict[str, Any]) -> str:
	for key in ("raw", "id", "event_id"):
		value = item.get(key)
		if value:
			return str(value)
	return f"{item.get('timestamp_ms', 0)}|{item.get('message', '')}|{item.get('line_without_time', '')}"


def extract_timestamp_ms(item: dict[str, Any]) -> int:
	for key in ("timestamp_ms", "timestamp", "time"):
		value = item.get(key)
		if value is None:
			continue
		try:
			return int(value)
		except (TypeError, ValueError):
			continue
	return 0


def extract_attacking_team(item: dict[str, Any]) -> str | None:
	for key in ("attacker_team", "killer_team", "player_team", "team"):
		team = normalize_team(str(item.get(key, "")))
		if team:
			return team

	blob = " ".join(
		str(item.get(key, ""))
		for key in ("message", "line_without_time", "raw")
		if item.get(key)
	)
	if not blob:
		return None

	guid_matches = TEAM_GUID_RE.findall(blob)
	if guid_matches:
		return normalize_team(guid_matches[0])

	generic_matches = re.findall(r"\b(Allies|Axis)\b", blob, flags=re.IGNORECASE)
	if generic_matches:
		return normalize_team(generic_matches[0])

	return None


def build_liberation_status(allied_kills: int, axis_kills: int, target_kills: int) -> dict[str, Any]:
	total_kills = allied_kills + axis_kills
	progress = min((total_kills / target_kills) * 100.0, 100.0) if target_kills > 0 else 0.0

	if allied_kills > axis_kills:
		controlling_faction = "allies"
	elif axis_kills > allied_kills:
		controlling_faction = "axis"
	else:
		controlling_faction = "neutral"

	if total_kills == 0:
		state = "idle"
	elif total_kills >= target_kills:
		state = "liberated"
	else:
		state = "contested"

	return {
		"state": state,
		"progress_percent": round(progress, 2),
		"target_kills": target_kills,
		"remaining_kills": max(target_kills - total_kills, 0),
		"controlling_faction": controlling_faction,
	}


def resolve_map_query(query: str, candidates: list[str]) -> str | None:
	lowered = (query or "").strip().casefold()
	if not lowered:
		return None

	alias_match = MAP_ID_TO_PRETTY.get(lowered)
	if alias_match and alias_match in candidates:
		return alias_match

	pretty_alias = PRETTY_TO_MAP_ID.get(lowered)
	if pretty_alias:
		pretty_name = MAP_ID_TO_PRETTY.get(pretty_alias)
		if pretty_name in candidates:
			return pretty_name

	normalized_query = normalize_slug(query)
	for candidate in candidates:
		if normalize_slug(candidate) == normalized_query:
			return candidate

	return None


def load_server_configs() -> list[ServerConfig]:
	if LIBERATION_SERVERS_FILE:
		config_path = Path(LIBERATION_SERVERS_FILE)
		payload = json.loads(config_path.read_text(encoding="utf-8"))
		if not isinstance(payload, list):
			raise ValueError("LIBERATION_SERVERS_FILE must contain a JSON list")

		servers: list[ServerConfig] = []
		for raw in payload:
			if not isinstance(raw, dict):
				continue
			server_id = str(raw.get("id") or raw.get("server_id") or "").strip()
			base_url = str(raw.get("base_url") or "").strip()
			api_key = str(raw.get("api_key") or "").strip()
			api_key_env = str(raw.get("api_key_env") or "").strip()
			if not api_key and api_key_env:
				api_key = os.getenv(api_key_env, "")
			if not server_id or not base_url or not api_key:
				continue
			name = str(raw.get("name") or server_id).strip()
			servers.append(
				ServerConfig(
					server_id=server_id,
					name=name,
					base_url=base_url.rstrip("/") + "/",
					api_key=api_key,
				)
			)
		if servers:
			return servers
		raise ValueError("No valid servers found in LIBERATION_SERVERS_FILE")

	base_url = os.getenv("CRCON_PANEL_URL", "").strip()
	api_key = os.getenv("CRCON_API_KEY", "").strip()
	server_id = os.getenv("LIBERATION_SERVER_ID", "main").strip()
	server_name = os.getenv("LIBERATION_SERVER_NAME", "Primary Server").strip()

	if not base_url or not api_key:
		raise RuntimeError(
			"Set CRCON_PANEL_URL and CRCON_API_KEY, or provide LIBERATION_SERVERS_FILE with one or more server definitions."
		)

	return [
		ServerConfig(
			server_id=server_id,
			name=server_name,
			base_url=base_url.rstrip("/") + "/",
			api_key=api_key,
		)
	]


class RedisCache:
	def __init__(self, redis_url: str | None):
		self.redis_url = (redis_url or "").strip()
		self.client: redis.Redis | None = None

	async def open(self) -> None:
		if not self.redis_url:
			return
		self.client = redis.from_url(self.redis_url, decode_responses=True)
		try:
			await self.client.ping()
		except Exception:
			LOG.exception("Redis unavailable, disabling cache")
			await self.close()

	async def close(self) -> None:
		if self.client is not None:
			await self.client.aclose()
			self.client = None

	async def get_json(self, key: str) -> dict[str, Any] | None:
		if self.client is None:
			return None
		try:
			value = await self.client.get(key)
		except Exception:
			LOG.exception("Redis GET failed for %s", key)
			return None
		if not value:
			return None
		try:
			return json.loads(value)
		except json.JSONDecodeError:
			return None

	async def set_json(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
		if self.client is None:
			return
		try:
			await self.client.set(key, json.dumps(value, default=json_default), ex=ttl_seconds)
		except Exception:
			LOG.exception("Redis SET failed for %s", key)

	async def delete_patterns(self, patterns: list[str]) -> None:
		if self.client is None:
			return
		try:
			for pattern in patterns:
				keys = [key async for key in self.client.scan_iter(match=pattern)]
				if keys:
					await self.client.delete(*keys)
		except Exception:
			LOG.exception("Redis cache invalidation failed")


class LiberationStore:
	def __init__(self, database_url: str):
		self.database_url = database_url
		self._pool: asyncpg.Pool | None = None

	async def open(self) -> None:
		self._pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=10)
		async with self.pool.acquire() as conn:
			await conn.execute(SCHEMA_SQL)

	async def close(self) -> None:
		if self._pool is not None:
			await self._pool.close()
			self._pool = None

	@property
	def pool(self) -> asyncpg.Pool:
		if self._pool is None:
			raise RuntimeError("Database pool not open")
		return self._pool

	async def upsert_server(self, server: ServerConfig) -> None:
		now = utc_now()
		async with self.pool.acquire() as conn:
			await conn.execute(
				"""
				INSERT INTO servers (server_id, server_name, base_url, updated_at)
				VALUES ($1, $2, $3, $4)
				ON CONFLICT (server_id) DO UPDATE SET
				  server_name = EXCLUDED.server_name,
				  base_url = EXCLUDED.base_url,
				  updated_at = EXCLUDED.updated_at
				""",
				server.server_id,
				server.name,
				server.base_url,
				now,
			)
			await conn.execute(
				"""
				INSERT INTO server_state (server_id, current_map, current_map_id, active_session_id, last_poll_at, last_error)
				VALUES ($1, NULL, NULL, NULL, NULL, NULL)
				ON CONFLICT (server_id) DO NOTHING
				""",
				server.server_id,
			)

	async def set_server_error(self, server_id: str, error_message: str) -> None:
		async with self.pool.acquire() as conn:
			await conn.execute(
				"""
				UPDATE server_state
				SET last_poll_at = $2, last_error = $3
				WHERE server_id = $1
				""",
				server_id,
				utc_now(),
				error_message,
			)

	async def ensure_map_session(self, server_id: str, map_name: str, map_id: str | None) -> tuple[int, bool]:
		now = utc_now()
		async with self.pool.acquire() as conn:
			async with conn.transaction():
				state = await conn.fetchrow(
					"""
					SELECT current_map, current_map_id, active_session_id
					FROM server_state
					WHERE server_id = $1
					FOR UPDATE
					""",
					server_id,
				)

				current_map = state["current_map"] if state else None
				current_map_id = state["current_map_id"] if state else None
				active_session_id = state["active_session_id"] if state else None

				same_map = active_session_id is not None and current_map == map_name and current_map_id == map_id
				if same_map:
					await conn.execute(
						"""
						UPDATE map_sessions
						SET last_seen_at = $2, updated_at = $2
						WHERE session_id = $1
						""",
						active_session_id,
						now,
					)
					await conn.execute(
						"""
						UPDATE server_state
						SET last_poll_at = $2, last_error = NULL
						WHERE server_id = $1
						""",
						server_id,
						now,
					)
					return int(active_session_id), False

				if active_session_id is not None:
					await conn.execute(
						"""
						UPDATE map_sessions
						SET ended_at = COALESCE(ended_at, $2), last_seen_at = $2, updated_at = $2
						WHERE session_id = $1
						""",
						active_session_id,
						now,
					)

				session_id = await conn.fetchval(
					"""
					INSERT INTO map_sessions (server_id, map_name, map_id, started_at, last_seen_at, updated_at)
					VALUES ($1, $2, $3, $4, $4, $4)
					RETURNING session_id
					""",
					server_id,
					map_name,
					map_id,
					now,
				)
				await conn.execute(
					"""
					UPDATE server_state
					SET current_map = $2,
						current_map_id = $3,
						active_session_id = $4,
						last_poll_at = $5,
						last_error = NULL
					WHERE server_id = $1
					""",
					server_id,
					map_name,
					map_id,
					session_id,
					now,
				)
				return int(session_id), True

	async def seed_processed_events(self, server_id: str, session_id: int, events: list[KillEvent]) -> int:
		if not events:
			return 0

		now = utc_now()
		rows = [(server_id, event.event_key, session_id, event.timestamp_ms, event.team, now) for event in events]
		async with self.pool.acquire() as conn:
			await conn.executemany(
				"""
				INSERT INTO processed_events (server_id, event_key, session_id, timestamp_ms, team, created_at)
				VALUES ($1, $2, $3, $4, $5, $6)
				ON CONFLICT (server_id, event_key) DO NOTHING
				""",
				rows,
			)
		return len(rows)

	async def apply_kill_events(
		self,
		server_id: str,
		*,
		session_id: int,
		map_name: str,
		map_id: str | None,
		events: list[KillEvent],
	) -> int:
		if not events:
			return 0

		event_keys = [event.event_key for event in events]
		async with self.pool.acquire() as conn:
			async with conn.transaction():
				rows = await conn.fetch(
					"""
					SELECT event_key
					FROM processed_events
					WHERE server_id = $1 AND event_key = ANY($2::text[])
					""",
					server_id,
					event_keys,
				)
				existing = {row["event_key"] for row in rows}
				new_events = [event for event in events if event.event_key not in existing]
				if not new_events:
					await conn.execute(
						"""
						UPDATE map_sessions
						SET last_seen_at = $2, updated_at = $2
						WHERE session_id = $1
						""",
						session_id,
						utc_now(),
					)
					return 0

				now = utc_now()
				payload = [
					(server_id, event.event_key, session_id, event.timestamp_ms, event.team, now)
					for event in new_events
				]
				await conn.executemany(
					"""
					INSERT INTO processed_events (server_id, event_key, session_id, timestamp_ms, team, created_at)
					VALUES ($1, $2, $3, $4, $5, $6)
					ON CONFLICT (server_id, event_key) DO NOTHING
					""",
					payload,
				)

				allied_increment = sum(1 for event in new_events if event.team == "allies")
				axis_increment = sum(1 for event in new_events if event.team == "axis")

				await conn.execute(
					"""
					INSERT INTO map_totals (server_id, map_name, map_id, allied_kills, axis_kills, updated_at)
					VALUES ($1, $2, $3, $4, $5, $6)
					ON CONFLICT (server_id, map_name) DO UPDATE SET
					  map_id = EXCLUDED.map_id,
					  allied_kills = map_totals.allied_kills + EXCLUDED.allied_kills,
					  axis_kills = map_totals.axis_kills + EXCLUDED.axis_kills,
					  updated_at = EXCLUDED.updated_at
					""",
					server_id,
					map_name,
					map_id,
					allied_increment,
					axis_increment,
					now,
				)
				await conn.execute(
					"""
					UPDATE map_sessions
					SET allied_kills = allied_kills + $2,
						axis_kills = axis_kills + $3,
						last_seen_at = $4,
						updated_at = $4
					WHERE session_id = $1
					""",
					session_id,
					allied_increment,
					axis_increment,
					now,
				)
				return len(new_events)

	async def prune_processed_events(self, server_id: str, keep: int = LIBERATION_PROCESSED_EVENT_KEEP) -> None:
		async with self.pool.acquire() as conn:
			await conn.execute(
				"""
				WITH keep_rows AS (
				  SELECT event_key
				  FROM processed_events
				  WHERE server_id = $1
				  ORDER BY timestamp_ms DESC, created_at DESC
				  LIMIT $2
				)
				DELETE FROM processed_events p
				WHERE p.server_id = $1
				  AND NOT EXISTS (
					SELECT 1 FROM keep_rows k WHERE k.event_key = p.event_key
				  )
				""",
				server_id,
				keep,
			)

	async def list_servers(self) -> list[dict[str, Any]]:
		async with self.pool.acquire() as conn:
			rows = await conn.fetch(
				"""
				SELECT s.server_id,
					   s.server_name,
					   s.base_url,
					   st.current_map,
					   st.current_map_id,
					   st.active_session_id,
					   st.last_poll_at,
					   st.last_error
				FROM servers s
				LEFT JOIN server_state st ON st.server_id = s.server_id
				ORDER BY s.server_name ASC
				"""
			)
		return [dict(row) for row in rows]

	async def list_map_rows(self, server_id: str | None = None) -> list[dict[str, Any]]:
		async with self.pool.acquire() as conn:
			if server_id:
				rows = await conn.fetch(
					"""
					SELECT server_id, map_name, map_id, allied_kills, axis_kills, updated_at
					FROM map_totals
					WHERE server_id = $1
					ORDER BY map_name ASC
					""",
					server_id,
				)
			else:
				rows = await conn.fetch(
					"""
					SELECT server_id, map_name, map_id, allied_kills, axis_kills, updated_at
					FROM map_totals
					ORDER BY map_name ASC, server_id ASC
					"""
				)
		return [dict(row) for row in rows]


class CRCONPoller:
	def __init__(self, store: LiberationStore, cache: RedisCache, servers: list[ServerConfig]):
		self.store = store
		self.cache = cache
		self.servers = servers
		self.session: aiohttp.ClientSession | None = None
		self.tasks: list[asyncio.Task[None]] = []
		self._stop_event = asyncio.Event()

	async def start(self) -> None:
		timeout = aiohttp.ClientTimeout(total=20)
		self.session = aiohttp.ClientSession(timeout=timeout)
		for server in self.servers:
			await self.store.upsert_server(server)
			self.tasks.append(asyncio.create_task(self._poll_loop(server), name=f"poll-{server.server_id}"))

	async def stop(self) -> None:
		self._stop_event.set()
		for task in self.tasks:
			task.cancel()
		if self.tasks:
			await asyncio.gather(*self.tasks, return_exceptions=True)
		self.tasks.clear()
		if self.session is not None and not self.session.closed:
			await self.session.close()
			self.session = None

	async def _poll_loop(self, server: ServerConfig) -> None:
		while not self._stop_event.is_set():
			try:
				await self._poll_once(server)
			except asyncio.CancelledError:
				raise
			except Exception as exc:
				LOG.exception("Polling failed for %s", server.server_id)
				await self.store.set_server_error(server.server_id, str(exc))
			await asyncio.sleep(LIBERATION_POLL_SECONDS)

	async def _poll_once(self, server: ServerConfig) -> None:
		gamestate_payload = await self._request_json(server, "get_gamestate")
		gamestate_result = gamestate_payload.get("result", gamestate_payload) if isinstance(gamestate_payload, dict) else {}
		current_map_info = gamestate_result.get("current_map", {}) if isinstance(gamestate_result, dict) else {}
		current_map_id = None
		current_map_name = None

		if isinstance(current_map_info, dict):
			current_map_id = str(current_map_info.get("id") or "").strip() or None
			current_map_name = str(current_map_info.get("pretty_name") or "").strip() or None
		elif isinstance(current_map_info, str):
			current_map_id = current_map_info.strip() or None

		current_map = canonical_map_name(current_map_name, current_map_id)
		session_id, session_changed = await self.store.ensure_map_session(server.server_id, current_map, current_map_id)

		logs_payload = await self._request_json(
			server,
			"get_recent_logs",
			params={"filter_action": "KILL", "end": str(LIBERATION_RECENT_LOG_LIMIT)},
		)
		items = extract_log_items(logs_payload)
		items.sort(key=extract_timestamp_ms)
		events = [
			KillEvent(
				event_key=extract_event_key(item),
				timestamp_ms=extract_timestamp_ms(item),
				team=extract_attacking_team(item),
			)
			for item in items
		]

		if session_changed and not LIBERATION_IMPORT_RECENT_ON_START:
			await self.store.seed_processed_events(server.server_id, session_id, events)
			LOG.info("Started new session %s for %s on %s", session_id, server.server_id, current_map)
		else:
			processed = await self.store.apply_kill_events(
				server.server_id,
				session_id=session_id,
				map_name=current_map,
				map_id=current_map_id,
				events=events,
			)
			if processed:
				LOG.info(
					"Applied %s new kill events to %s on %s",
					processed,
					server.server_id,
					current_map,
				)

		await self.store.prune_processed_events(server.server_id)
		await self.cache.delete_patterns(["maps:*", "map:*"])

	async def _request_json(
		self,
		server: ServerConfig,
		endpoint: str,
		*,
		params: dict[str, str] | None = None,
	) -> dict[str, Any]:
		if self.session is None:
			raise RuntimeError("HTTP session not ready")

		url = urljoin(server.base_url, endpoint)
		headers = {
			"Authorization": f"Bearer {server.api_key}",
			"Accept": "application/json",
		}
		async with self.session.get(url, headers=headers, params=params) as response:
			text = await response.text()
			if response.status >= 400:
				raise RuntimeError(f"{endpoint} returned HTTP {response.status}: {text[:300]}")
			try:
				data = json.loads(text) if text else {}
			except json.JSONDecodeError as exc:
				raise RuntimeError(f"{endpoint} returned invalid JSON: {exc}") from exc
			if isinstance(data, dict) and (data.get("error") or data.get("failed")):
				raise RuntimeError(f"{endpoint} returned an error payload: {data}")
			return data if isinstance(data, dict) else {"result": data}


def normalize_payload(value: Any) -> Any:
	if isinstance(value, datetime):
		return value.isoformat()
	if isinstance(value, dict):
		return {key: normalize_payload(item) for key, item in value.items()}
	if isinstance(value, list):
		return [normalize_payload(item) for item in value]
	return value


async def build_maps_payload(store: LiberationStore, server_id: str | None, target_kills: int) -> dict[str, Any]:
	rows = await store.list_map_rows(server_id=server_id)

	grouped: dict[str, dict[str, Any]] = {}
	for row in rows:
		entry = grouped.setdefault(
			row["map_name"],
			{
				"map_name": row["map_name"],
				"map_id": row.get("map_id"),
				"allied_kills": 0,
				"axis_kills": 0,
				"servers": [],
				"updated_at": row["updated_at"],
			},
		)
		entry["allied_kills"] += int(row["allied_kills"])
		entry["axis_kills"] += int(row["axis_kills"])
		entry["updated_at"] = max(entry["updated_at"], row["updated_at"])
		entry["servers"].append(
			{
				"server_id": row["server_id"],
				"allied_kills": int(row["allied_kills"]),
				"axis_kills": int(row["axis_kills"]),
				"updated_at": row["updated_at"],
			}
		)

	maps: list[dict[str, Any]] = []
	for entry in grouped.values():
		status = build_liberation_status(entry["allied_kills"], entry["axis_kills"], target_kills)
		maps.append(
			{
				**entry,
				"total_kills": entry["allied_kills"] + entry["axis_kills"],
				"liberation": status,
			}
		)

	maps.sort(key=lambda item: item["map_name"])
	return normalize_payload({"maps": maps, "target_kills": target_kills, "server_id": server_id, "source": "postgres"})


@web.middleware
async def cors_middleware(request: web.Request, handler):
	if request.method == "OPTIONS":
		response = web.Response(status=204)
	else:
		response = await handler(request)

	response.headers["Access-Control-Allow-Origin"] = "*"
	response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
	response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
	return response


async def health_handler(request: web.Request) -> web.Response:
	poller: CRCONPoller = request.app["poller"]
	cache: RedisCache = request.app["cache"]
	return web.json_response(
		{
			"status": "ok",
			"servers": len(poller.servers),
			"poll_seconds": LIBERATION_POLL_SECONDS,
			"database": "postgres",
			"redis_cache": cache.client is not None,
		}
	)


async def servers_handler(request: web.Request) -> web.Response:
	store: LiberationStore = request.app["store"]
	payload = {"servers": normalize_payload(await store.list_servers())}
	return web.json_response(payload)


async def maps_handler(request: web.Request) -> web.Response:
	store: LiberationStore = request.app["store"]
	cache: RedisCache = request.app["cache"]
	server_id = request.query.get("server_id")
	target_kills = int(request.query.get("target_kills", LIBERATION_TARGET_KILLS))
	cache_key = f"maps:{server_id or 'all'}:{target_kills}"

	cached = await cache.get_json(cache_key)
	if cached is not None:
		return web.json_response(cached)

	payload = await build_maps_payload(store, server_id, target_kills)
	await cache.set_json(cache_key, payload, LIBERATION_CACHE_TTL_SECONDS)
	return web.json_response(payload)


async def map_detail_handler(request: web.Request) -> web.Response:
	store: LiberationStore = request.app["store"]
	cache: RedisCache = request.app["cache"]
	server_id = request.query.get("server_id")
	target_kills = int(request.query.get("target_kills", LIBERATION_TARGET_KILLS))
	query = request.match_info["map_query"]
	cache_key = f"map:{server_id or 'all'}:{target_kills}:{normalize_slug(query)}"

	cached = await cache.get_json(cache_key)
	if cached is not None:
		return web.json_response(cached)

	rows = await store.list_map_rows(server_id=server_id)
	candidates = sorted({row["map_name"] for row in rows})
	resolved_name = resolve_map_query(query, candidates)
	if not resolved_name:
		payload = {
			"error": "map_not_found",
			"message": f"No tracked map matched '{query}'.",
			"available_maps": candidates,
		}
		return web.json_response(payload, status=404)

	matching_rows = [row for row in rows if row["map_name"] == resolved_name]
	allied_kills = sum(int(row["allied_kills"]) for row in matching_rows)
	axis_kills = sum(int(row["axis_kills"]) for row in matching_rows)
	updated_at = max(row["updated_at"] for row in matching_rows)
	map_id = next((row.get("map_id") for row in matching_rows if row.get("map_id")), None)

	payload = normalize_payload(
		{
			"map_name": resolved_name,
			"map_id": map_id,
			"allied_kills": allied_kills,
			"axis_kills": axis_kills,
			"total_kills": allied_kills + axis_kills,
			"updated_at": updated_at,
			"server_id": server_id,
			"servers": [
				{
					"server_id": row["server_id"],
					"allied_kills": int(row["allied_kills"]),
					"axis_kills": int(row["axis_kills"]),
					"updated_at": row["updated_at"],
				}
				for row in matching_rows
			],
			"liberation": build_liberation_status(allied_kills, axis_kills, target_kills),
			"source": "postgres",
		}
	)
	await cache.set_json(cache_key, payload, LIBERATION_CACHE_TTL_SECONDS)
	return web.json_response(payload)


async def startup(app: web.Application) -> None:
	store = LiberationStore(DATABASE_URL)
	await store.open()
	app["store"] = store

	cache = RedisCache(REDIS_URL)
	await cache.open()
	app["cache"] = cache

	servers = load_server_configs()
	poller = CRCONPoller(store, cache, servers)
	await poller.start()
	app["poller"] = poller

	LOG.info("Liberation API started for %s server(s)", len(servers))


async def cleanup(app: web.Application) -> None:
	poller: CRCONPoller = app["poller"]
	store: LiberationStore = app["store"]
	cache: RedisCache = app["cache"]
	await poller.stop()
	await cache.close()
	await store.close()


def create_app() -> web.Application:
	app = web.Application(middlewares=[cors_middleware])
	app.router.add_get("/health", health_handler)
	app.router.add_get("/api/servers", servers_handler)
	app.router.add_get("/api/maps", maps_handler)
	app.router.add_get("/api/maps/{map_query}", map_detail_handler)
	app.router.add_route("OPTIONS", "/{tail:.*}", health_handler)
	app.on_startup.append(startup)
	app.on_cleanup.append(cleanup)
	return app


if __name__ == "__main__":
	logging.basicConfig(level=os.getenv("LIBERATION_LOG_LEVEL", "INFO").upper())
	web.run_app(create_app(), host=LIBERATION_HOST, port=LIBERATION_PORT)

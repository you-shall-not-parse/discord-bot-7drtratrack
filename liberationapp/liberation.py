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
FRONTLINES_ADMIN_TOKEN = os.getenv("FRONTLINES_ADMIN_TOKEN", "").strip()
LIBERATION_PORT = int(os.getenv("LIBERATION_PORT", os.getenv("PORT", "8080")))
LIBERATION_HOST = os.getenv("LIBERATION_HOST", "0.0.0.0")
LIBERATION_POLL_SECONDS = max(3, int(os.getenv("LIBERATION_POLL_SECONDS", "10")))
LIBERATION_TARGET_KILLS = max(1, int(os.getenv("LIBERATION_TARGET_KILLS", "500")))
LIBERATION_ALL_TIME_TARGET_KILLS = max(1, int(os.getenv("LIBERATION_ALL_TIME_TARGET_KILLS", "25000000")))
LIBERATION_RECENT_LOG_LIMIT = max(10, int(os.getenv("LIBERATION_RECENT_LOG_LIMIT", "250")))
LIBERATION_CACHE_TTL_SECONDS = max(3, int(os.getenv("LIBERATION_CACHE_TTL_SECONDS", "15")))
LIBERATION_PROCESSED_EVENT_KEEP = max(1000, int(os.getenv("LIBERATION_PROCESSED_EVENT_KEEP", "5000")))
LIBERATION_IMPORT_RECENT_ON_START = os.getenv("LIBERATION_IMPORT_RECENT_ON_START", "false").lower() == "true"
LIBERATION_SERVERS_FILE = os.getenv("LIBERATION_SERVERS_FILE")
LIBERATION_CONTROL_MAX = max(1.0, float(os.getenv("LIBERATION_CONTROL_MAX", "100")))
LIBERATION_CONTROL_DOMINANCE_THRESHOLD = max(1.0, float(os.getenv("LIBERATION_CONTROL_DOMINANCE_THRESHOLD", "90")))
LIBERATION_CONTROL_WEIGHT = max(0.1, float(os.getenv("LIBERATION_CONTROL_WEIGHT", "28")))
LIBERATION_CONTROL_ACTIVITY_SCALE = max(1.0, float(os.getenv("LIBERATION_CONTROL_ACTIVITY_SCALE", "120")))
LIBERATION_CONTROL_ACTIVITY_EXPONENT = max(1.0, float(os.getenv("LIBERATION_CONTROL_ACTIVITY_EXPONENT", "2.0")))
LIBERATION_CONTROL_DECAY_PER_HOUR = max(0.0, float(os.getenv("LIBERATION_CONTROL_DECAY_PER_HOUR", "0.10")))
LIBERATION_CONTROL_EDGE_DECAY_MULTIPLIER = max(0.0, float(os.getenv("LIBERATION_CONTROL_EDGE_DECAY_MULTIPLIER", "3.0")))
LIBERATION_CONTROL_PLAYER_REFERENCE = max(1.0, float(os.getenv("LIBERATION_CONTROL_PLAYER_REFERENCE", "100")))
LIBERATION_CONTROL_PLAYER_EXPONENT = max(0.1, float(os.getenv("LIBERATION_CONTROL_PLAYER_EXPONENT", "1.0")))
LIBERATION_CONTROL_OBJECTIVE_WEIGHT_PER_HOUR = max(0.0, float(os.getenv("LIBERATION_CONTROL_OBJECTIVE_WEIGHT_PER_HOUR", "8.0")))

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
	axis_players INTEGER NOT NULL DEFAULT 0,
	allied_players INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS map_objectives (
	map_name TEXT PRIMARY KEY,
	map_id TEXT,
	control_value DOUBLE PRECISION NOT NULL DEFAULT 0,
	occupied_faction TEXT,
	last_activity_at TIMESTAMPTZ,
	updated_at TIMESTAMPTZ NOT NULL
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


def parse_int(value: Any, default: int = 0) -> int:
	try:
		return int(value)
	except (TypeError, ValueError):
		return default


def extract_player_counts(payload: dict[str, Any]) -> tuple[int, int]:
	if not isinstance(payload, dict):
		return 0, 0

	for axis_key, allied_key in (
		("num_axis_players", "num_allied_players"),
		("axis_players", "allied_players"),
	):
		if axis_key in payload or allied_key in payload:
			return max(0, parse_int(payload.get(axis_key))), max(0, parse_int(payload.get(allied_key)))

	teams = payload.get("teams")
	if isinstance(teams, dict):
		axis_team = teams.get("axis") if isinstance(teams.get("axis"), dict) else {}
		allies_team = teams.get("allies") if isinstance(teams.get("allies"), dict) else {}
		return (
			max(0, parse_int(axis_team.get("players") or axis_team.get("count"))),
			max(0, parse_int(allies_team.get("players") or allies_team.get("count"))),
		)

	return 0, 0


def extract_team_objective_scores(payload: Any) -> tuple[int, int]:
	result = payload.get("result", payload) if isinstance(payload, dict) else payload

	if isinstance(result, (list, tuple)) and len(result) >= 2:
		return max(0, parse_int(result[0])), max(0, parse_int(result[1]))

	if isinstance(result, dict):
		for allied_key, axis_key in (
			("allies", "axis"),
			("allied", "axis"),
			("allies_objectives", "axis_objectives"),
			("allied_objectives", "axis_objectives"),
		):
			if allied_key in result or axis_key in result:
				return max(0, parse_int(result.get(allied_key))), max(0, parse_int(result.get(axis_key)))

	return 0, 0


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


def clamp_control(value: float) -> float:
	return max(-LIBERATION_CONTROL_MAX, min(LIBERATION_CONTROL_MAX, value))


def decay_control(value: float, elapsed_seconds: float) -> float:
	if value == 0 or elapsed_seconds <= 0 or LIBERATION_CONTROL_DECAY_PER_HOUR <= 0:
		return value

	decay_amount = LIBERATION_CONTROL_DECAY_PER_HOUR * (elapsed_seconds / 3600.0)
	threshold_ratio = min(LIBERATION_CONTROL_DOMINANCE_THRESHOLD / LIBERATION_CONTROL_MAX, 0.999999)
	control_ratio = min(abs(value) / LIBERATION_CONTROL_MAX, 1.0)
	if control_ratio > threshold_ratio and LIBERATION_CONTROL_EDGE_DECAY_MULTIPLIER > 0:
		edge_window = max(1.0 - threshold_ratio, 1e-6)
		edge_progress = (control_ratio - threshold_ratio) / edge_window
		decay_amount *= 1.0 + (LIBERATION_CONTROL_EDGE_DECAY_MULTIPLIER * (edge_progress ** 2))
	if value > 0:
		return max(0.0, value - decay_amount)
	return min(0.0, value + decay_amount)


def compute_player_activity_factor(axis_players: int, allied_players: int) -> float:
	total_players = max(0, axis_players) + max(0, allied_players)
	activity_progress = min(total_players / LIBERATION_CONTROL_PLAYER_REFERENCE, 1.0)
	return activity_progress ** LIBERATION_CONTROL_PLAYER_EXPONENT


def compute_objective_pressure_delta(
	allied_objectives: int,
	axis_objectives: int,
	elapsed_seconds: float,
	*,
	player_activity_factor: float,
) -> float:
	if elapsed_seconds <= 0 or LIBERATION_CONTROL_OBJECTIVE_WEIGHT_PER_HOUR <= 0 or player_activity_factor <= 0:
		return 0.0

	total_objectives = max(0, allied_objectives) + max(0, axis_objectives)
	if total_objectives <= 0:
		return 0.0

	hold_advantage = (max(0, allied_objectives) - max(0, axis_objectives)) / total_objectives
	return hold_advantage * LIBERATION_CONTROL_OBJECTIVE_WEIGHT_PER_HOUR * (elapsed_seconds / 3600.0) * player_activity_factor


def compute_control_delta(
	allied_kills: int,
	axis_kills: int,
	*,
	allied_players: int = 0,
	axis_players: int = 0,
	allied_objectives: int = 0,
	axis_objectives: int = 0,
	elapsed_seconds: float = 0.0,
) -> float:
	player_activity_factor = compute_player_activity_factor(axis_players, allied_players)
	total_kills = allied_kills + axis_kills
	kill_delta = 0.0
	if total_kills > 0:
		delta_ratio = (allied_kills - axis_kills) / total_kills
		activity_progress = min(total_kills / LIBERATION_CONTROL_ACTIVITY_SCALE, 1.0)
		activity_factor = activity_progress ** LIBERATION_CONTROL_ACTIVITY_EXPONENT
		kill_delta = delta_ratio * LIBERATION_CONTROL_WEIGHT * activity_factor * player_activity_factor

	objective_delta = compute_objective_pressure_delta(
		allied_objectives,
		axis_objectives,
		elapsed_seconds,
		player_activity_factor=player_activity_factor,
	)
	return kill_delta + objective_delta


def resolve_occupied_faction(control_value: float, occupied_faction: str | None) -> str | None:
	if control_value >= LIBERATION_CONTROL_MAX:
		return "allies"
	if control_value <= -LIBERATION_CONTROL_MAX:
		return "axis"
	if occupied_faction == "allies" and control_value >= LIBERATION_CONTROL_DOMINANCE_THRESHOLD:
		return "allies"
	if occupied_faction == "axis" and control_value <= -LIBERATION_CONTROL_DOMINANCE_THRESHOLD:
		return "axis"
	return None


def build_liberation_status(
	allied_kills: int,
	axis_kills: int,
	target_kills: int,
	*,
	control_value: float = 0.0,
	occupied_faction: str | None = None,
) -> dict[str, Any]:
	total_kills = allied_kills + axis_kills
	allies_progress = min((allied_kills / target_kills) * 100.0, 100.0) if target_kills > 0 else 0.0
	axis_progress = min((axis_kills / target_kills) * 100.0, 100.0) if target_kills > 0 else 0.0
	control_value = clamp_control(control_value)
	control_position_percent = ((control_value + LIBERATION_CONTROL_MAX) / (LIBERATION_CONTROL_MAX * 2.0)) * 100.0
	control_abs_percent = abs(control_value)
	kill_margin = allied_kills - axis_kills

	if control_value > 0:
		controlling_faction = "allies"
	elif control_value < 0:
		controlling_faction = "axis"
	else:
		controlling_faction = "neutral"

	if total_kills == 0:
		state = "idle"
	elif occupied_faction == "allies":
		state = "allies_occupied"
	elif occupied_faction == "axis":
		state = "axis_occupied"
	elif control_value >= LIBERATION_CONTROL_DOMINANCE_THRESHOLD:
		state = "allies_control"
	elif control_value <= -LIBERATION_CONTROL_DOMINANCE_THRESHOLD:
		state = "axis_control"
	elif controlling_faction == "neutral":
		state = "deadlocked"
	else:
		state = "contested"

	return {
		"state": state,
		"mode": "tug_of_war",
		"progress_percent": round(control_abs_percent, 2),
		"target_kills": target_kills,
		"remaining_kills": max(target_kills - total_kills, 0),
		"controlling_faction": controlling_faction,
		"leading_faction": controlling_faction,
		"winner": occupied_faction,
		"occupied_faction": occupied_faction,
		"total_kills": total_kills,
		"leading_kills": max(allied_kills, axis_kills),
		"race_margin": abs(kill_margin),
		"kill_margin": kill_margin,
		"control_value": round(control_value, 2),
		"control_position_percent": round(control_position_percent, 2),
		"control_abs_percent": round(control_abs_percent, 2),
		"control_target": LIBERATION_CONTROL_MAX,
		"control_dominance_threshold": LIBERATION_CONTROL_DOMINANCE_THRESHOLD,
		"allies_progress_percent": round(allies_progress, 2),
		"axis_progress_percent": round(axis_progress, 2),
		"allies_remaining_kills": max(target_kills - allied_kills, 0),
		"axis_remaining_kills": max(target_kills - axis_kills, 0),
	}


def build_challenge_tracks(target_kills: int) -> dict[str, Any]:
	return {
		"current": {
			"slug": "frontline-liberation",
			"label": "Frontline Liberation",
			"description": "Persistent control shifts with live combat pressure across tracked fronts.",
			"mode": "tug_of_war",
			"window": "campaign",
			"status": "active",
		},
		"weekly": {
			"slug": "weekly-operation",
			"label": "Weekly Operation",
			"description": "Reserved for weekly map-specific kill objectives.",
			"status": "planned",
		},
		"monthly": {
			"slug": "monthly-theatre",
			"label": "Monthly Theatre",
			"description": "Reserved for monthly campaigns and rotating map challenges.",
			"status": "planned",
		},
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


def authorization_token(request: web.Request) -> str:
	authorization = request.headers.get("Authorization", "")
	prefix = "Bearer "
	if authorization.startswith(prefix):
		return authorization[len(prefix):].strip()
	return ""


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
			await conn.execute("ALTER TABLE server_state ADD COLUMN IF NOT EXISTS axis_players INTEGER NOT NULL DEFAULT 0")
			await conn.execute("ALTER TABLE server_state ADD COLUMN IF NOT EXISTS allied_players INTEGER NOT NULL DEFAULT 0")

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

	async def get_server_state(self, server_id: str) -> dict[str, Any] | None:
		async with self.pool.acquire() as conn:
			row = await conn.fetchrow(
				"""
				SELECT current_map, current_map_id, active_session_id, axis_players, allied_players, last_poll_at, last_error
				FROM server_state
				WHERE server_id = $1
				""",
				server_id,
			)
		return dict(row) if row else None

	async def touch_server_poll(self, server_id: str, *, axis_players: int | None = None, allied_players: int | None = None) -> None:
		async with self.pool.acquire() as conn:
			now = utc_now()
			if axis_players is None or allied_players is None:
				await conn.execute(
					"""
					UPDATE server_state
					SET last_poll_at = $2, last_error = NULL
					WHERE server_id = $1
					""",
					server_id,
					now,
				)
				return

			await conn.execute(
				"""
				UPDATE server_state
				SET axis_players = $2,
					allied_players = $3,
					last_poll_at = $4,
					last_error = NULL
				WHERE server_id = $1
				""",
				server_id,
				max(0, axis_players),
				max(0, allied_players),
				now,
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
		axis_players: int = 0,
		allied_players: int = 0,
		axis_objectives: int = 0,
		allied_objectives: int = 0,
	) -> int:
		event_keys = [event.event_key for event in events]
		async with self.pool.acquire() as conn:
			async with conn.transaction():
				now = utc_now()
				new_events = events
				if event_keys:
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

				if new_events:
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

				if new_events:
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

				objective_row = await conn.fetchrow(
					"""
					SELECT control_value, occupied_faction, updated_at
					FROM map_objectives
					WHERE map_name = $1
					FOR UPDATE
					""",
					map_name,
				)
				base_control = 0.0
				occupied_faction = None
				if objective_row:
					elapsed_seconds = max((now - objective_row["updated_at"]).total_seconds(), 0.0)
					base_control = decay_control(float(objective_row["control_value"]), elapsed_seconds)
					occupied_faction = objective_row["occupied_faction"]
				else:
					elapsed_seconds = float(LIBERATION_POLL_SECONDS)

				next_control = clamp_control(
					base_control
					+ compute_control_delta(
						allied_increment,
						axis_increment,
						allied_players=allied_players,
						axis_players=axis_players,
						allied_objectives=allied_objectives,
						axis_objectives=axis_objectives,
						elapsed_seconds=elapsed_seconds,
					)
				)
				next_occupied_faction = resolve_occupied_faction(next_control, occupied_faction)
				await conn.execute(
					"""
					INSERT INTO map_objectives (map_name, map_id, control_value, occupied_faction, last_activity_at, updated_at)
					VALUES ($1, $2, $3, $4, $5, $5)
					ON CONFLICT (map_name) DO UPDATE SET
					  map_id = EXCLUDED.map_id,
					  control_value = EXCLUDED.control_value,
					  occupied_faction = EXCLUDED.occupied_faction,
					  last_activity_at = EXCLUDED.last_activity_at,
					  updated_at = EXCLUDED.updated_at
					""",
					map_name,
					map_id,
					next_control,
					next_occupied_faction,
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
					   st.axis_players,
					   st.allied_players,
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

	async def list_map_objectives(self) -> list[dict[str, Any]]:
		async with self.pool.acquire() as conn:
			rows = await conn.fetch(
				"""
				SELECT map_name, map_id, control_value, occupied_faction, last_activity_at, updated_at
				FROM map_objectives
				ORDER BY map_name ASC
				"""
			)
		return [dict(row) for row in rows]

	async def resolve_map_candidates(self) -> list[str]:
		async with self.pool.acquire() as conn:
			rows = await conn.fetch(
				"""
				SELECT DISTINCT map_name
				FROM (
					SELECT map_name FROM map_totals
					UNION
					SELECT map_name FROM map_objectives
				) map_names
				WHERE map_name IS NOT NULL AND map_name <> ''
				ORDER BY map_name ASC
				"""
			)
		candidates = {str(row["map_name"]) for row in rows}
		candidates.update(MAP_ID_TO_PRETTY.values())
		return sorted(candidates)

	async def set_map_objective_control(self, map_name: str, control_value: float) -> dict[str, Any]:
		now = utc_now()
		clamped_control = clamp_control(control_value)
		occupied_faction = resolve_occupied_faction(clamped_control, None)
		derived_map_id = PRETTY_TO_MAP_ID.get(map_name.casefold())

		async with self.pool.acquire() as conn:
			async with conn.transaction():
				map_id = await conn.fetchval(
					"""
					SELECT map_id
					FROM map_objectives
					WHERE map_name = $1 AND map_id IS NOT NULL
					""",
					map_name,
				)
				if not map_id:
					map_id = await conn.fetchval(
						"""
						SELECT map_id
						FROM map_totals
						WHERE map_name = $1 AND map_id IS NOT NULL
						ORDER BY updated_at DESC
						LIMIT 1
						""",
						map_name,
					)
				map_id = map_id or derived_map_id

				await conn.execute(
					"""
					INSERT INTO map_objectives (map_name, map_id, control_value, occupied_faction, last_activity_at, updated_at)
					VALUES ($1, $2, $3, $4, $5, $5)
					ON CONFLICT (map_name) DO UPDATE SET
					  map_id = EXCLUDED.map_id,
					  control_value = EXCLUDED.control_value,
					  occupied_faction = EXCLUDED.occupied_faction,
					  last_activity_at = EXCLUDED.last_activity_at,
					  updated_at = EXCLUDED.updated_at
					""",
					map_name,
					map_id,
					clamped_control,
					occupied_faction,
					now,
				)

		return {
			"map_name": map_name,
			"map_id": map_id,
			"control_value": round(clamped_control, 2),
			"occupied_faction": occupied_faction,
			"updated_at": now,
		}


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
		current_map_id = None
		current_map_name = None
		session_changed = False
		axis_players = None
		allied_players = None
		allied_objectives = 0
		axis_objectives = 0

		try:
			gamestate_payload = await self._request_json(server, "get_gamestate")
		except RuntimeError as exc:
			cached_state = await self.store.get_server_state(server.server_id)
			current_map = str(cached_state.get("current_map") or "").strip() if cached_state else ""
			current_map_id = str(cached_state.get("current_map_id") or "").strip() or None if cached_state else None
			active_session_id = cached_state.get("active_session_id") if cached_state else None
			if not current_map or active_session_id is None:
				raise RuntimeError(
					f"get_gamestate failed and no cached active session is available: {exc}"
				) from exc
			session_id = int(active_session_id)
			axis_players = parse_int(cached_state.get("axis_players")) if cached_state else 0
			allied_players = parse_int(cached_state.get("allied_players")) if cached_state else 0
			LOG.warning(
				"get_gamestate failed for %s, using cached map session %s on %s",
				server.server_id,
				session_id,
				current_map,
			)
		else:
			gamestate_result = gamestate_payload.get("result", gamestate_payload) if isinstance(gamestate_payload, dict) else {}
			current_map_info = gamestate_result.get("current_map", {}) if isinstance(gamestate_result, dict) else {}
			axis_players, allied_players = extract_player_counts(gamestate_result)

			if isinstance(current_map_info, dict):
				current_map_id = str(current_map_info.get("id") or "").strip() or None
				current_map_name = str(current_map_info.get("pretty_name") or "").strip() or None
			elif isinstance(current_map_info, str):
				current_map_id = current_map_info.strip() or None

			current_map = canonical_map_name(current_map_name, current_map_id)
			session_id, session_changed = await self.store.ensure_map_session(server.server_id, current_map, current_map_id)

		try:
			objective_scores_payload = await self._request_json(server, "get_team_objective_scores")
		except RuntimeError as exc:
			LOG.warning("get_team_objective_scores failed for %s: %s", server.server_id, exc)
		else:
			allied_objectives, axis_objectives = extract_team_objective_scores(objective_scores_payload)

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
			processed = await self.store.apply_kill_events(
				server.server_id,
				session_id=session_id,
				map_name=current_map,
				map_id=current_map_id,
				events=[],
				axis_players=parse_int(axis_players),
				allied_players=parse_int(allied_players),
				axis_objectives=axis_objectives,
				allied_objectives=allied_objectives,
			)
		else:
			processed = await self.store.apply_kill_events(
				server.server_id,
				session_id=session_id,
				map_name=current_map,
				map_id=current_map_id,
				events=events,
				axis_players=parse_int(axis_players),
				allied_players=parse_int(allied_players),
				axis_objectives=axis_objectives,
				allied_objectives=allied_objectives,
			)
			if processed:
				LOG.info(
					"Applied %s new kill events to %s on %s",
					processed,
					server.server_id,
					current_map,
				)

		await self.store.touch_server_poll(
			server.server_id,
			axis_players=axis_players,
			allied_players=allied_players,
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


def build_all_time_objective(total_kills: int) -> dict[str, Any]:
	progress_percent = min((total_kills / LIBERATION_ALL_TIME_TARGET_KILLS) * 100.0, 100.0) if LIBERATION_ALL_TIME_TARGET_KILLS > 0 else 0.0
	return {
		"label": "All-Time Theatre Objective",
		"mode": "all_maps_all_sides",
		"target_kills": LIBERATION_ALL_TIME_TARGET_KILLS,
		"total_kills": total_kills,
		"remaining_kills": max(LIBERATION_ALL_TIME_TARGET_KILLS - total_kills, 0),
		"progress_percent": round(progress_percent, 2),
	}


def build_active_server_entry(server: dict[str, Any]) -> dict[str, Any]:
	axis_players = max(0, parse_int(server.get("axis_players")))
	allied_players = max(0, parse_int(server.get("allied_players")))
	return {
		"server_id": server["server_id"],
		"server_name": server["server_name"],
		"current_map": server.get("current_map"),
		"current_map_id": server.get("current_map_id"),
		"axis_players": axis_players,
		"allied_players": allied_players,
		"player_count": axis_players + allied_players,
		"last_poll_at": server.get("last_poll_at"),
	}


def has_active_players(server: dict[str, Any]) -> bool:
	return (parse_int(server.get("axis_players")) + parse_int(server.get("allied_players"))) > 0


async def build_maps_payload(store: LiberationStore, server_id: str | None, target_kills: int) -> dict[str, Any]:
	rows = await store.list_map_rows(server_id=server_id)
	objective_rows = {row["map_name"]: row for row in await store.list_map_objectives()}
	server_rows = await store.list_servers()
	now = utc_now()
	active_map_servers: dict[str, list[dict[str, Any]]] = {}
	for server in server_rows:
		if server_id and server.get("server_id") != server_id:
			continue
		current_map = server.get("current_map")
		if not current_map or not has_active_players(server):
			continue
		active_map_servers.setdefault(current_map, []).append(build_active_server_entry(server))

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
		objective = objective_rows.get(entry["map_name"])
		control_value = 0.0
		occupied_faction = None
		if objective:
			elapsed_seconds = max((now - objective["updated_at"]).total_seconds(), 0.0)
			control_value = decay_control(float(objective["control_value"]), elapsed_seconds)
			occupied_faction = resolve_occupied_faction(control_value, objective.get("occupied_faction"))
		status = build_liberation_status(
			entry["allied_kills"],
			entry["axis_kills"],
			target_kills,
			control_value=control_value,
			occupied_faction=occupied_faction,
		)
		active_servers = active_map_servers.get(entry["map_name"], [])
		maps.append(
			{
				**entry,
				"total_kills": entry["allied_kills"] + entry["axis_kills"],
				"is_active_battle": bool(active_servers),
				"active_servers": active_servers,
				"liberation": status,
			}
		)

	active_battles = [
		{
			"map_name": map_name,
			"servers": servers,
		}
		for map_name, servers in active_map_servers.items()
	]
	active_battles.sort(key=lambda item: item["map_name"])

	all_time_total_kills = sum(int(row["allied_kills"]) + int(row["axis_kills"]) for row in rows)
	maps.sort(key=lambda item: (not item["is_active_battle"], -item["liberation"]["control_abs_percent"], item["map_name"]))
	return normalize_payload(
		{
			"maps": maps,
			"active_battles": active_battles,
			"target_kills": target_kills,
			"all_time_objective": build_all_time_objective(all_time_total_kills),
			"server_id": server_id,
			"source": "postgres",
			"challenge_tracks": build_challenge_tracks(target_kills),
		}
	)


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
	objective_rows = {row["map_name"]: row for row in await store.list_map_objectives()}
	objective = objective_rows.get(resolved_name)
	control_value = 0.0
	occupied_faction = None
	if objective:
		elapsed_seconds = max((utc_now() - objective["updated_at"]).total_seconds(), 0.0)
		control_value = decay_control(float(objective["control_value"]), elapsed_seconds)
		occupied_faction = resolve_occupied_faction(control_value, objective.get("occupied_faction"))
	server_rows = await store.list_servers()
	active_servers = [
		build_active_server_entry(server)
		for server in server_rows
		if (not server_id or server.get("server_id") == server_id)
		and server.get("current_map") == resolved_name
		and has_active_players(server)
	]

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
			"is_active_battle": bool(active_servers),
			"active_servers": active_servers,
			"liberation": build_liberation_status(
				allied_kills,
				axis_kills,
				target_kills,
				control_value=control_value,
				occupied_faction=occupied_faction,
			),
			"source": "postgres",
			"challenge_tracks": build_challenge_tracks(target_kills),
		}
	)
	await cache.set_json(cache_key, payload, LIBERATION_CACHE_TTL_SECONDS)
	return web.json_response(payload)


async def frontlines_reset_handler(request: web.Request) -> web.Response:
	store: LiberationStore = request.app["store"]
	cache: RedisCache = request.app["cache"]

	if not FRONTLINES_ADMIN_TOKEN:
		return web.json_response(
			{
				"error": "frontlines_admin_disabled",
				"message": "FRONTLINES_ADMIN_TOKEN is not configured.",
			},
			status=503,
		)

	if authorization_token(request) != FRONTLINES_ADMIN_TOKEN:
		return web.json_response(
			{
				"error": "forbidden",
				"message": "A valid frontlines admin token is required.",
			},
			status=403,
		)

	try:
		payload = await request.json()
	except json.JSONDecodeError:
		return web.json_response(
			{
				"error": "invalid_json",
				"message": "Request body must be valid JSON.",
			},
			status=400,
		)

	map_query = str((payload or {}).get("map_name") or "").strip()
	if not map_query:
		return web.json_response(
			{
				"error": "missing_map_name",
				"message": "Provide map_name.",
			},
			status=400,
		)

	try:
		control_value = float((payload or {}).get("control_value"))
	except (TypeError, ValueError):
		return web.json_response(
			{
				"error": "invalid_control_value",
				"message": "control_value must be a number between -100 and 100.",
			},
			status=400,
		)

	if control_value < -LIBERATION_CONTROL_MAX or control_value > LIBERATION_CONTROL_MAX:
		return web.json_response(
			{
				"error": "control_value_out_of_range",
				"message": f"control_value must be between {-LIBERATION_CONTROL_MAX:.0f} and {LIBERATION_CONTROL_MAX:.0f}.",
			},
			status=400,
		)

	candidates = await store.resolve_map_candidates()
	resolved_name = resolve_map_query(map_query, candidates)
	if not resolved_name:
		return web.json_response(
			{
				"error": "map_not_found",
				"message": f"No tracked map matched '{map_query}'.",
				"available_maps": candidates,
			},
			status=404,
		)

	reset_result = await store.set_map_objective_control(resolved_name, control_value)
	await cache.delete_patterns(["maps:*", "map:*"])
	return web.json_response(normalize_payload({"status": "ok", **reset_result}))


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
	app.router.add_post("/api/admin/frontlines/reset", frontlines_reset_handler)
	app.router.add_route("OPTIONS", "/{tail:.*}", health_handler)
	app.on_startup.append(startup)
	app.on_cleanup.append(cleanup)
	return app


if __name__ == "__main__":
	logging.basicConfig(level=os.getenv("LIBERATION_LOG_LEVEL", "INFO").upper())
	web.run_app(create_app(), host=LIBERATION_HOST, port=LIBERATION_PORT)

# hll_stats_cog.py
# HLL stats Cog — removed unlinkplayer slash command as requested.
# Admin remove performs permanent deletion only. Undo/redo commands removed earlier.
# Other functionality (ingest, leaderboards, admin list, apply-default-metrics, etc.) unchanged.

import io
import csv
import json
import re
import hashlib
import datetime
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite

# =========================
# Configuration - single guild + leaderboard channel + admin role IDs
# =========================
# Replace the placeholder integers with your actual server/channel/role IDs.
GUILD_ID: int = 1097913605082579024            # set your guild ID (integer)
LEADERBOARD_CHANNEL_ID: int = 1099806153170489485  # set your leaderboard channel ID (integer)

# Admin roles: list the role IDs which should be allowed to run admin commands.
ADMIN_ROLE_IDS = [1213495462632361994, 1097915860322091090]

# Rolling window and DB path (set as desired)
DEFAULT_ROLLING_WINDOW_GAMES: int = 5
DB_PATH: str = "hll_stats.sqlite3"

GUILD_IDS = [GUILD_ID]
GUILDS = [discord.Object(id=GUILD_ID)]

# =========================
# Metric definitions
# Add metric keys here; any key you put in DEFAULT_ENABLED_METRICS must be defined here.
# =========================
METRIC_DEFS: Dict[str, Dict[str, Any]] = {
    "kills": {"label": "Kills", "all_time": "sum", "rolling": "avg", "column": "kills", "fmt": lambda v: f"{v:.0f}"},
    "deaths": {"label": "Deaths", "all_time": "sum", "rolling": "avg", "column": "deaths", "fmt": lambda v: f"{v:.0f}"},
    "kdr": {"label": "K/D", "all_time": "avg", "rolling": "avg", "column": "kdr", "fmt": lambda v: f"{v:.2f}"},
    "kpm": {"label": "Kills/min", "all_time": "avg", "rolling": "avg", "column": "kpm", "fmt": lambda v: f"{v:.2f}"},
    "dpm": {"label": "Deaths/min", "all_time": "avg", "rolling": "avg", "column": "dpm", "fmt": lambda v: f"{v:.2f}"},
    "combat_effectiveness": {"label": "Combat Effectiveness", "all_time": "sum", "rolling": "avg", "column": "combat_effectiveness", "fmt": lambda v: f"{v:.0f}"},
    "support_points": {"label": "Support Points", "all_time": "sum", "rolling": "avg", "column": "support_points", "fmt": lambda v: f"{v:.0f}"},
    "defensive_points": {"label": "Defensive Points", "all_time": "sum", "rolling": "avg", "column": "defensive_points", "fmt": lambda v: f"{v:.0f}"},
    "offensive_points": {"label": "Offensive Points", "all_time": "sum", "rolling": "avg", "column": "offensive_points", "fmt": lambda v: f"{v:.0f}"},
    "max_kill_streak": {"label": "Max kill streak", "all_time": "max", "rolling": "max", "column": "max_kill_streak", "fmt": lambda v: f"{v:.0f}"},
    "max_death_streak": {"label": "Max death streak", "all_time": "max", "rolling": "max", "column": "max_death_streak", "fmt": lambda v: f"{v:.0f}"},
}

# =========================
# Edit THIS LINE to change which metrics are enabled by DEFAULT for new guilds.
# Make sure any key you add exists as a key in METRIC_DEFS above.
# =========================
DEFAULT_ENABLED_METRICS = [
    "kills", "deaths", "kdr", "kpm", "dpm",
    "combat_effectiveness", "support_points", "defensive_points", "offensive_points"
]
# =========================

def _canonical_default_enabled_metrics() -> List[str]:
    filtered = [m for m in DEFAULT_ENABLED_METRICS if m in METRIC_DEFS]
    if not filtered:
        return list(METRIC_DEFS.keys())
    return filtered

# =========================
# Utilities: trimming, normalization, rank stripping
# =========================

def _trim_discriminator(name: Optional[str]) -> Optional[str]:
    if name is None:
        return None
    return re.sub(r'#\d{1,10}$', '', name).strip()

RANK_TOKENS = [
    "Field Marshal", "FM",
    "General", "Gen",
    "Lieutenant General", "Lt Gen", "Lt.Gen", "LtGen",
    "Major General", "Maj Gen", "Maj.Gen", "MajGen",
    "Brigadier", "Brig",
    "Colonel", "Col",
    "Lieutenant Colonel", "Lt Col", "Lt.Col", "LtCol",
    "Major", "Maj",
    "Captain", "Cpt",
    "Lieutenant", "Lt", "Lt.",
    "2nd Lieutenant", "2Lt", "2ndLt", "2 Lt",
    "Regimental Sergeant Major", "RSM", "WO1", "WO2",
    "Warrant Officer 1st Class", "Warrant Officer 2nd Class",
    "Sergeant major", "SGM", "Staff Sergeant", "SSG",
    "Sergeant", "Sgt",
    "Corporal", "Cpl",
    "L.Cpl", "LCpl", "L Cpl",
    "Private", "Pte",
    "Recruit",
]

_rank_variants = []
for tok in RANK_TOKENS:
    esc = re.escape(tok)
    esc = esc.replace(r'\ ', r'[\s\._-]*')
    _rank_variants.append(esc)
_RANK_PREFIX_RE = re.compile(r'^(?:' + r'|'.join(_rank_variants) + r')[\s\._-]*', re.IGNORECASE)

def _strip_rank_prefix(name: Optional[str]) -> Optional[str]:
    if name is None:
        return None
    s = name.strip()
    return _RANK_PREFIX_RE.sub('', s).strip()

def _normalize_for_match(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s = _trim_discriminator(s)
    s = _strip_rank_prefix(s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s.casefold() if s else None

def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def _parse_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return None
    s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None

def _safe_ratio(num: Optional[float], den: Optional[float]) -> Optional[float]:
    if num is None or den is None or den == 0:
        return None
    return num / den

# =========================
# DB schema and migrations
# =========================

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS players (
  guild_id TEXT NOT NULL,
  player_id TEXT NOT NULL,
  latest_name TEXT NOT NULL,
  first_seen TIMESTAMP NOT NULL,
  last_seen TIMESTAMP NOT NULL,
  PRIMARY KEY (guild_id, player_id)
);

CREATE TABLE IF NOT EXISTS games (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id TEXT NOT NULL,
  uploader_id INTEGER NOT NULL,
  message_id INTEGER,
  created_at TIMESTAMP NOT NULL,
  source_filename TEXT NOT NULL,
  file_hash TEXT NOT NULL,
  deleted INTEGER DEFAULT 0,
  deleted_at TIMESTAMP,
  UNIQUE (guild_id, file_hash)
);

CREATE TABLE IF NOT EXISTS stats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id TEXT NOT NULL,
  game_id INTEGER NOT NULL,
  player_id TEXT NOT NULL,
  kills REAL,
  deaths REAL,
  kdr REAL,
  kpm REAL,
  dpm REAL,
  combat_effectiveness REAL,
  support_points REAL,
  defensive_points REAL,
  offensive_points REAL,
  max_kill_streak REAL,
  max_death_streak REAL,
  weapons TEXT,
  death_by_weapons TEXT,
  extras TEXT,
  active INTEGER DEFAULT 1,
  FOREIGN KEY (guild_id, player_id) REFERENCES players(guild_id, player_id),
  FOREIGN KEY (game_id) REFERENCES games(id)
);

CREATE TABLE IF NOT EXISTS user_links (
  guild_id TEXT NOT NULL,
  discord_user_id INTEGER NOT NULL,
  player_id TEXT NOT NULL,
  PRIMARY KEY (guild_id, discord_user_id)
);

CREATE TABLE IF NOT EXISTS guild_settings (
  guild_id TEXT PRIMARY KEY,
  rolling_window_games INTEGER NOT NULL,
  enabled_metrics TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stats_player ON stats (guild_id, player_id, id);
CREATE INDEX IF NOT EXISTS idx_stats_game ON stats (guild_id, game_id);
"""

async def init_db(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA_SQL)
    async def ensure_column(table: str, col: str, decl: str):
        async with conn.execute(f"PRAGMA table_info({table})") as cur:
            cols = {r[1] for r in await cur.fetchall()}
        if col not in cols:
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    await ensure_column("games", "deleted", "INTEGER DEFAULT 0")
    await ensure_column("games", "deleted_at", "TIMESTAMP")
    await ensure_column("stats", "active", "INTEGER DEFAULT 1")
    await conn.commit()

# =========================
# Cog
# =========================

class HLLStatsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Optional[aiosqlite.Connection] = None
        self._startup_refreshed = False

    async def cog_load(self) -> None:
        self.db = await aiosqlite.connect(DB_PATH)
        await init_db(self.db)

    async def cog_unload(self) -> None:
        if self.db:
            await self.db.close()

    # ---- settings helpers ----
    async def ensure_guild_settings(self, guild_id: int) -> None:
        assert self.db
        async with self.db.execute("SELECT guild_id FROM guild_settings WHERE guild_id=?", (str(guild_id),)) as cur:
            row = await cur.fetchone()
        if not row:
            defaults = _canonical_default_enabled_metrics()
            await self.db.execute(
                "INSERT INTO guild_settings (guild_id, rolling_window_games, enabled_metrics) VALUES (?, ?, ?)",
                (str(guild_id), DEFAULT_ROLLING_WINDOW_GAMES, json.dumps(defaults)),
            )
            await self.db.commit()

    async def get_settings(self, guild_id: int) -> Dict[str, Any]:
        assert self.db
        await self.ensure_guild_settings(guild_id)
        async with self.db.execute("SELECT rolling_window_games, enabled_metrics FROM guild_settings WHERE guild_id=?", (str(guild_id),)) as cur:
            row = await cur.fetchone()
        window = row[0]
        try:
            enabled_raw = json.loads(row[1]) if row and row[1] else _canonical_default_enabled_metrics()
        except Exception:
            enabled_raw = _canonical_default_enabled_metrics()
        enabled = [m for m in enabled_raw if m in METRIC_DEFS]
        if not enabled:
            enabled = _canonical_default_enabled_metrics()
        return {"rolling_window_games": window, "enabled_metrics": enabled}

    async def set_enabled_metrics(self, guild_id: int, metrics: List[str]) -> None:
        assert self.db
        metrics = [m for m in metrics if m in METRIC_DEFS]
        await self.db.execute("UPDATE guild_settings SET enabled_metrics=? WHERE guild_id=?", (json.dumps(metrics), str(guild_id)))
        await self.db.commit()

    async def set_rolling_window(self, guild_id: int, window: int) -> None:
        assert self.db
        await self.db.execute("UPDATE guild_settings SET rolling_window_games=? WHERE guild_id=?", (window, str(guild_id)))
        await self.db.commit()

    # ---- DB helpers ----
    async def upsert_player(self, guild_id: int, player_id: str, name: str) -> None:
        assert self.db
        now = datetime.datetime.utcnow().isoformat()
        await self.db.execute(
            """
            INSERT INTO players (guild_id, player_id, latest_name, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, player_id) DO UPDATE SET
              latest_name=excluded.latest_name,
              last_seen=excluded.last_seen
            """,
            (str(guild_id), player_id, name, now, now),
        )

    async def insert_game(self, guild_id: int, uploader_id: int, message_id: Optional[int], filename: str, file_hash: str, force: bool = False) -> Tuple[Optional[int], bool]:
        assert self.db
        now = datetime.datetime.utcnow().isoformat()
        fh = file_hash
        if force:
            fh = fh + "-" + hashlib.sha256(f"{now}-{uploader_id}".encode()).hexdigest()[:8]
        try:
            cur = await self.db.execute(
                "INSERT INTO games (guild_id, uploader_id, message_id, created_at, source_filename, file_hash) VALUES (?, ?, ?, ?, ?, ?)",
                (str(guild_id), uploader_id, message_id, now, filename, fh),
            )
            await self.db.commit()
            return (cur.lastrowid, True)
        except aiosqlite.IntegrityError:
            async with self.db.execute("SELECT id FROM games WHERE guild_id=? AND file_hash=?", (str(guild_id), file_hash)) as cur:
                row = await cur.fetchone()
            if row:
                return (row[0], False)
            return (None, False)

    async def insert_stat(self, guild_id: int, game_id: int, mapped: Dict[str, Any]) -> None:
        """Insert with full column list ensuring placeholders and values match the schema."""
        assert self.db
        await self.db.execute(
            """
            INSERT INTO stats (
              guild_id, game_id, player_id,
              kills, deaths, kdr, kpm, dpm,
              combat_effectiveness, support_points, defensive_points, offensive_points,
              max_kill_streak, max_death_streak,
              weapons, death_by_weapons, extras, active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(guild_id),
                game_id,
                mapped["player_id"],
                mapped.get("kills"),
                mapped.get("deaths"),
                mapped.get("kdr"),
                mapped.get("kpm"),
                mapped.get("dpm"),
                mapped.get("combat_effectiveness"),
                mapped.get("support_points"),
                mapped.get("defensive_points"),
                mapped.get("offensive_points"),
                mapped.get("max_kill_streak"),
                mapped.get("max_death_streak"),
                mapped.get("weapons"),
                mapped.get("death_by_weapons"),
                json.dumps(mapped.get("extras") or {}),
                1,  # active flag
            ),
        )

    async def link_user(self, guild_id: int, discord_user_id: int, player_id: str) -> None:
        assert self.db
        await self.db.execute(
            "INSERT INTO user_links (guild_id, discord_user_id, player_id) VALUES (?, ?, ?) ON CONFLICT(guild_id, discord_user_id) DO UPDATE SET player_id=excluded.player_id",
            (str(guild_id), discord_user_id, player_id),
        )

    async def unlink_user(self, guild_id: int, discord_user_id: int) -> None:
        assert self.db
        await self.db.execute("DELETE FROM user_links WHERE guild_id=? AND discord_user_id=?", (str(guild_id), discord_user_id))

    async def get_linked_player_id(self, guild_id: int, discord_user_id: int) -> Optional[str]:
        assert self.db
        async with self.db.execute("SELECT player_id FROM user_links WHERE guild_id=? AND discord_user_id=?", (str(guild_id), discord_user_id)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

    async def get_latest_name(self, guild_id: int, player_id: str) -> Optional[str]:
        assert self.db
        async with self.db.execute("SELECT latest_name FROM players WHERE guild_id=? AND player_id=?", (str(guild_id), player_id)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

    async def commit(self) -> None:
        assert self.db
        await self.db.commit()

    # ---- Stats queries (only consider stats.active=1) ----
    async def get_all_time_stats(self, guild_id: int, player_id: str) -> Optional[Dict[str, Any]]:
        assert self.db
        selects = [
            "COUNT(*) as games",
            "SUM(COALESCE(kills,0)) as total_kills",
            "SUM(COALESCE(deaths,0)) as total_deaths",
            "AVG(kdr) as avg_kdr",
            "AVG(kpm) as avg_kpm",
            "AVG(dpm) as avg_dpm",
            "SUM(COALESCE(combat_effectiveness,0)) as sum_ce",
            "SUM(COALESCE(support_points,0)) as sum_sp",
            "SUM(COALESCE(defensive_points,0)) as sum_dp",
            "SUM(COALESCE(offensive_points,0)) as sum_op",
            "MAX(COALESCE(max_kill_streak,0)) as max_ks",
            "MAX(COALESCE(max_death_streak,0)) as max_ds",
        ]
        async with self.db.execute(
            f"SELECT {', '.join(selects)} FROM stats WHERE guild_id=? AND player_id=? AND active=1",
            (str(guild_id), player_id)
        ) as cur:
            row = await cur.fetchone()
        if not row or row[0] == 0:
            return None
        return {
            "games": row[0],
            "kills": row[1] or 0.0,
            "deaths": row[2] or 0.0,
            "kdr": row[3],
            "kpm": row[4],
            "dpm": row[5],
            "combat_effectiveness": row[6] or 0.0,
            "support_points": row[7] or 0.0,
            "defensive_points": row[8] or 0.0,
            "offensive_points": row[9] or 0.0,
            "max_kill_streak": row[10] or 0.0,
            "max_death_streak": row[11] or 0.0,
        }

    async def get_player_last_n_rows(self, guild_id: int, player_id: str, n: int) -> List[Dict[str, Any]]:
        assert self.db
        async with self.db.execute(
            """
            SELECT id, kills, deaths, kdr, kpm, dpm,
                   combat_effectiveness, support_points, defensive_points, offensive_points,
                   max_kill_streak, max_death_streak
            FROM stats
            WHERE guild_id=? AND player_id=? AND active=1
            ORDER BY id DESC
            LIMIT ?
            """,
            (str(guild_id), player_id, n),
        ) as cur:
            rows = await cur.fetchall()
        keys = ["id","kills","deaths","kdr","kpm","dpm","combat_effectiveness","support_points","defensive_points","offensive_points","max_kill_streak","max_death_streak"]
        return [dict(zip(keys, r)) for r in rows]

    async def get_rolling_stats(self, guild_id: int, player_id: str, window: int) -> Optional[Dict[str, Any]]:
        assert self.db
        async with self.db.execute(
            "SELECT kills, deaths, kdr, kpm, dpm FROM stats WHERE guild_id=? AND player_id=? AND active=1 ORDER BY id DESC LIMIT ?",
            (str(guild_id), player_id, window)
        ) as cur:
            rows = await cur.fetchall()
        if not rows:
            return None
        def _avg(idx: int) -> Optional[float]:
            vals = [r[idx] for r in rows if r[idx] is not None]
            return (sum(vals)/len(vals)) if vals else None
        return {
            "window_games": len(rows),
            "avg_kills": _avg(0),
            "avg_deaths": _avg(1),
            "avg_kdr": _avg(2),
            "avg_kpm": _avg(3),
            "avg_dpm": _avg(4),
        }

    async def get_all_player_ids(self, guild_id: int) -> List[str]:
        assert self.db
        async with self.db.execute("SELECT player_id FROM players WHERE guild_id=?", (str(guild_id),)) as cur:
            return [r[0] for r in await cur.fetchall()]

    async def get_all_time_leaderboard(self, guild_id: int, metric: str, limit: int = 10) -> List[Tuple[str, float]]:
        assert self.db
        md = METRIC_DEFS[metric]
        col = md["column"]
        agg = md["all_time"]
        if agg == "sum":
            sql = f"SELECT player_id, SUM(COALESCE({col},0)) as v FROM stats WHERE guild_id=? AND active=1 GROUP BY player_id HAVING COUNT(*) > 0 ORDER BY v DESC LIMIT ?"
        elif agg == "avg":
            sql = f"SELECT player_id, AVG({col}) as v FROM stats WHERE guild_id=? AND active=1 GROUP BY player_id HAVING COUNT(*) > 0 ORDER BY v DESC LIMIT ?"
        else:
            sql = f"SELECT player_id, MAX({col}) as v FROM stats WHERE guild_id=? AND active=1 GROUP BY player_id HAVING COUNT(*) > 0 ORDER BY v DESC LIMIT ?"
        async with self.db.execute(sql, (str(guild_id), limit)) as cur:
            return [(r[0], r[1]) for r in await cur.fetchall() if r[1] is not None]

    async def get_rolling_leaderboard(self, guild_id: int, metric: str, window: int, limit: int = 10) -> List[Tuple[str, float]]:
        assert self.db
        md = METRIC_DEFS[metric]
        col = md["column"]
        agg_rolling = md["rolling"]
        results: List[Tuple[str, float]] = []
        for pid in await self.get_all_player_ids(guild_id):
            async with self.db.execute(
                f"SELECT {col} FROM stats WHERE guild_id=? AND player_id=? AND active=1 ORDER BY id DESC LIMIT ?",
                (str(guild_id), pid, window)
            ) as cur:
                vals = [r[0] for r in await cur.fetchall() if r[0] is not None]
            if not vals:
                continue
            v = (sum(vals)/len(vals)) if agg_rolling == "avg" else max(vals)
            results.append((pid, v))
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    # -------- CSV ingestion + auto-linking ----------
    def _map_row(self, raw_row: Dict[str, Any]) -> Dict[str, Any]:
        norm_to_value: Dict[str, Any] = {}
        for k, v in raw_row.items():
            nk = "".join(ch.lower() for ch in k if ch.isalnum())
            norm_to_value[nk] = v

        def get_field(*keys: str) -> Optional[Any]:
            for k in keys:
                if k in norm_to_value:
                    return norm_to_value[k]
            return None

        player_id = get_field("playerid", "steamid", "playeridsteamid")
        name = get_field("name")

        kills = _parse_float(get_field("kills"))
        deaths = _parse_float(get_field("deaths"))
        kdr = _parse_float(get_field("kdr", "killsdeathratio", "kd"))
        if kdr is None:
            kdr = _safe_ratio(kills, deaths)

        kpm = _parse_float(get_field("killspermin", "kpm", "killsminute"))
        dpm = _parse_float(get_field("deathspermin", "dpm", "deathsminute"))

        ce = _parse_float(get_field("combateffectiveness"))
        sp = _parse_float(get_field("supportpoints"))
        dp = _parse_float(get_field("defensivepoints"))
        op = _parse_float(get_field("offensivepoints"))
        max_ks = _parse_float(get_field("maxkillstreak"))
        max_ds = _parse_float(get_field("maxdeathstreak"))

        weapons = get_field("weapons", "killsbyweapons", "killsbyweaponsweaponscolumn")
        death_by_weapons = get_field("deathbyweapons")

        known = {
            "playerid","steamid","playeridsteamid","name","kills","deaths","kdr","killsdeathratio","kd",
            "killspermin","kpm","killsminute","deathspermin","dpm","deathsminute",
            "combateffectiveness","supportpoints","defensivepoints","offensivepoints",
            "maxkillstreak","maxdeathstreak","weapons","killsbyweapons","killsbyweaponsweaponscolumn","deathbyweapons"
        }
        extras = {k: v for k, v in raw_row.items() if "".join(ch.lower() for ch in k if ch.isalnum()) not in known}

        mapped: Dict[str, Any] = {
            "player_id": str(player_id).strip() if player_id is not None else None,
            "name": str(name).strip() if name is not None else None,
            "kills": kills,
            "deaths": deaths,
            "kdr": kdr,
            "kpm": kpm,
            "dpm": dpm,
            "combat_effectiveness": ce,
            "support_points": sp,
            "defensive_points": dp,
            "offensive_points": op,
            "max_kill_streak": max_ks,
            "max_death_streak": max_ds,
            "weapons": str(weapons) if weapons is not None else None,
            "death_by_weapons": str(death_by_weapons) if death_by_weapons is not None else None,
            "extras": extras,
        }
        return mapped

    # -----------------------
    # Admin: list/remove games (top-level commands)
    # -----------------------
    def _is_admin(self, interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild:
            return True
        try:
            member = interaction.guild.get_member(interaction.user.id)
            if member:
                for r in member.roles:
                    if r.id in ADMIN_ROLE_IDS:
                        return True
        except Exception:
            pass
        return False

    @app_commands.command(name="list-games", description="List recently uploaded games (CSV files) with their IDs and status.")
    @app_commands.describe(limit="How many recent games to list (max 50)")
    @app_commands.guild_only()
    async def admin_list_games(self, interaction: discord.Interaction, limit: Optional[int] = 25):
        if interaction.guild is None:
            await interaction.response.send_message("Guild-only.", ephemeral=True)
            return
        if not self._is_admin(interaction):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return
        limit = max(1, min(50, limit or 25))
        await interaction.response.defer(ephemeral=True)

        async with self.db.execute(
            "SELECT id, uploader_id, created_at, source_filename, file_hash, deleted, deleted_at FROM games WHERE guild_id=? ORDER BY id DESC LIMIT ?",
            (str(interaction.guild_id), limit)
        ) as cur:
            rows = await cur.fetchall()

        if not rows:
            await interaction.followup.send("No games uploaded yet.", ephemeral=True)
            return

        embed = discord.Embed(title="Uploaded Games", color=discord.Color.blue(), description=f"Showing {len(rows)} most recent uploads")
        for (gid, uploader_id, created_at, filename, file_hash, deleted, deleted_at) in rows:
            async with self.db.execute("SELECT COUNT(*) FROM stats WHERE guild_id=? AND game_id=?", (str(interaction.guild_id), gid)) as c1:
                total_stats = (await c1.fetchone())[0]
            async with self.db.execute("SELECT COUNT(*) FROM stats WHERE guild_id=? AND game_id=? AND active=1", (str(interaction.guild_id), gid)) as c2:
                active_stats = (await c2.fetchone())[0]
            uploader = interaction.guild.get_member(int(uploader_id)) if uploader_id else None
            uploader_display = uploader.display_name if uploader else str(uploader_id)
            status = "deleted" if deleted else "active"
            created_at_str = created_at or "unknown"
            value = f"File: {filename}\nUploader: {uploader_display}\nUploaded: {created_at_str}\nRows: {active_stats}/{total_stats} active\nHash: {file_hash}\nStatus: {status}"
            if deleted and deleted_at:
                value += f"\nDeleted at: {deleted_at}"
            embed.add_field(name=f"Game ID {gid}", value=value, inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="admin_hllstatsremove" description="Permanently delete a game and all its stats (irreversible).")
    @app_commands.describe(game_id="Game ID to permanently delete")
    @app_commands.guild_only()
    async def admin_remove(self, interaction: discord.Interaction, game_id: int):
        """
        Permanently delete all stats rows for the game and remove the game record.
        Also remove any players in the guild that no longer have any stats and their user_links.
        This action is irreversible — back up your DB first.
        """
        if interaction.guild is None:
            await interaction.response.send_message("Guild-only.", ephemeral=True)
            return
        if not self._is_admin(interaction):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # verify game exists and belongs to guild
        async with self.db.execute(
            "SELECT id, source_filename FROM games WHERE guild_id=? AND id=?",
            (str(interaction.guild_id), game_id)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            await interaction.followup.send(f"Game ID {game_id} not found in this guild.", ephemeral=True)
            return

        # Perform permanent removal and cleanup orphaned players/links
        try:
            # Start a transaction (aiosqlite executes statements in a transaction until commit)
            await self.db.execute("DELETE FROM stats WHERE guild_id=? AND game_id=?", (str(interaction.guild_id), game_id))
            await self.db.execute("DELETE FROM games WHERE guild_id=? AND id=?", (str(interaction.guild_id), game_id))

            # Find players in this guild and remove those with no remaining active or inactive stats
            async with self.db.execute("SELECT player_id FROM players WHERE guild_id=?", (str(interaction.guild_id),)) as cur:
                all_pids = [r[0] for r in await cur.fetchall()]

            for pid in all_pids:
                async with self.db.execute("SELECT COUNT(*) FROM stats WHERE guild_id=? AND player_id=?", (str(interaction.guild_id), pid)) as c:
                    cnt = (await c.fetchone())[0]
                if cnt == 0:
                    # remove player record and any user_links pointing to it
                    await self.db.execute("DELETE FROM players WHERE guild_id=? AND player_id=?", (str(interaction.guild_id), pid))
                    await self.db.execute("DELETE FROM user_links WHERE guild_id=? AND player_id=?", (str(interaction.guild_id), pid))

            await self.db.commit()
        except Exception as exc:
            # Rollback attempt for safety if commit failed
            try:
                await self.db.execute("ROLLBACK")
            except Exception:
                pass
            await interaction.followup.send(f"Failed to permanently delete game {game_id}: {exc}", ephemeral=True)
            return

        # update leaderboards
        try:
            await self._post_or_update_leaderboards_in_channel(interaction.guild)
        except Exception:
            # ignore leaderboard update errors but inform the user that deletion succeeded
            pass

        await interaction.followup.send(f"Permanently deleted game {game_id} and its stats; orphaned players/links cleaned up.", ephemeral=True)

    # -----------------------
    # Apply defaults slash commands
    # -----------------------
    @app_commands.command(name="apply-default-metrics", description="Admin: set enabled metrics to the current DEFAULT_ENABLED_METRICS and refresh.")
    @app_commands.guild_only()
    async def apply_default_metrics(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("Guild-only.", ephemeral=True)
            return
        if not self._is_admin(interaction):
            await interaction.response.send_message("You do not have permission to run this.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        defaults = [m for m in DEFAULT_ENABLED_METRICS if m in METRIC_DEFS]
        if not defaults:
            await interaction.followup.send("No valid default metrics available (check METRIC_DEFS).", ephemeral=True)
            return

        await self.db.execute("UPDATE guild_settings SET enabled_metrics=? WHERE guild_id=?", (json.dumps(defaults), str(interaction.guild_id)))
        await self.db.commit()

        try:
            await self._post_or_update_leaderboards_in_channel(interaction.guild)
        except Exception as exc:
            await interaction.followup.send(f"Settings updated but failed to refresh leaderboards: {exc}", ephemeral=True)
            return

        await interaction.followup.send(f"Applied default metrics to this guild: {defaults}", ephemeral=True)

    # -----------------------
    # Existing commands: ingest, myhllstats, leaderboards, etc.
    # -----------------------

    @app_commands.guild_only()
    @app_commands.describe(file="CSV file (your exported format). Must include Player ID and Name columns at minimum.")
    @app_commands.command(name="extract-stats", description="Ingest a CSV of player stats to update the database.")
    async def extract_stats(self, interaction: discord.Interaction, file: discord.Attachment):
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if interaction.guild_id != GUILD_ID:
            await interaction.response.send_message("This bot is configured for a different guild.", ephemeral=True)
            return

        await self.ensure_guild_settings(interaction.guild_id)
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not file.filename.lower().endswith(".csv"):
            await interaction.followup.send("Please upload a .csv file.", ephemeral=True)
            return

        try:
            raw_bytes = await file.read()
        except Exception:
            await interaction.followup.send("Failed to download the attachment.", ephemeral=True)
            return

        file_hash = _sha256_bytes(raw_bytes)
        game_id, created = await self.insert_game(
            guild_id=interaction.guild_id,
            uploader_id=interaction.user.id,
            message_id=interaction.id,
            filename=file.filename,
            file_hash=file_hash,
            force=False
        )
        if not created:
            if game_id is None:
                await interaction.followup.send("This CSV appears to have been uploaded before. Skipping duplicate.", ephemeral=True)
                return
            async with self.db.execute("SELECT uploader_id, created_at, source_filename FROM games WHERE id=?", (game_id,)) as cur:
                row = await cur.fetchone()
            if row:
                u_id, created_at, src_name = row
                uploader_member = interaction.guild.get_member(int(u_id))
                uploader_display = uploader_member.display_name if uploader_member else str(u_id)
                await interaction.followup.send(
                    f"This CSV matches a previous upload (Game ID {game_id}, file: {src_name}, uploaded by {uploader_display} at {created_at}).\n"
                    "If you really need to re-import identical file content, contact an admin to use the `force` option.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send("This CSV appears to have been uploaded before. Skipping duplicate.", ephemeral=True)
            return

        decoded = raw_bytes.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(decoded))
        if not reader.fieldnames:
            await interaction.followup.send("CSV has no header row.", ephemeral=True)
            return

        members_map: Dict[str, int] = {}
        try:
            members = [m async for m in interaction.guild.fetch_members(limit=None)]
            for m in members:
                for cand in (m.name, m.display_name):
                    norm = _normalize_for_match(cand)
                    if norm:
                        members_map.setdefault(norm, m.id)
        except Exception:
            members_map = {}

        processed = 0
        created_players = 0
        updated_players = 0
        missing_pid = 0
        auto_linked = 0

        async with self.db.execute("SELECT player_id FROM players WHERE guild_id=?", (str(interaction.guild_id),)) as cur:
            existing_pids = {r[0] for r in await cur.fetchall()}

        for raw_row in reader:
            mapped = self._map_row(raw_row)
            pid = mapped.get("player_id")
            name = mapped.get("name")

            if not pid:
                missing_pid += 1
                continue

            csv_name = name or "Unknown"
            await self.upsert_player(interaction.guild_id, pid, csv_name)
            if pid in existing_pids:
                updated_players += 1
            else:
                created_players += 1
                existing_pids.add(pid)

            await self.insert_stat(interaction.guild_id, game_id, mapped)
            processed += 1

            norm_csv = _normalize_for_match(csv_name)
            if norm_csv and norm_csv in members_map:
                discord_id = members_map[norm_csv]
                try:
                    await self.link_user(interaction.guild_id, discord_id, pid)
                    auto_linked += 1
                except Exception:
                    pass

        await self.commit()

        posted_note = ""
        try:
            await self._post_or_update_leaderboards_in_channel(interaction.guild)
            posted_note = "Leaderboards updated (or created) in the configured channel."
        except Exception as exc:
            posted_note = f"Failed to update leaderboards: {exc}"

        embed = discord.Embed(title="Stats Extracted", description=f"Processed {processed} rows from {file.filename}", color=discord.Color.green())
        embed.add_field(name="New players", value=str(created_players), inline=True)
        embed.add_field(name="Updated players", value=str(updated_players), inline=True)
        if missing_pid:
            embed.add_field(name="Rows missing player ID", value=str(missing_pid), inline=True)
        embed.add_field(name="Auto-linked accounts", value=str(auto_linked), inline=True)
        embed.add_field(name="Game ID", value=str(game_id), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)
        await interaction.followup.send(posted_note, ephemeral=True)

    async def _find_bot_message_by_title(self, channel: discord.TextChannel, title: str) -> Optional[discord.Message]:
        async for msg in channel.history(limit=300):
            if msg.author.id != self.bot.user.id:
                continue
            if not msg.embeds:
                continue
            for emb in msg.embeds:
                if emb.title == title:
                    return msg
        return None

    async def _post_or_update_leaderboards_in_channel(self, guild: discord.Guild) -> None:
        settings = await self.get_settings(guild.id)
        enabled_metrics = settings["enabled_metrics"]
        window = settings["rolling_window_games"]

        channel = guild.get_channel(LEADERBOARD_CHANNEL_ID) or await guild.fetch_channel(LEADERBOARD_CHANNEL_ID)
        if channel is None or not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f"Leaderboard channel not found or not a text channel (ID {LEADERBOARD_CHANNEL_ID})")

        # Matches embed
        matches_embed = discord.Embed(title="Matches — Included", color=discord.Color.dark_blue(), timestamp=datetime.datetime.utcnow())
        matches_embed.set_footer(text="List of matches contributing active stats (Match ID shown).")

        async with self.db.execute(
            """
            SELECT g.id, g.source_filename, g.uploader_id, g.created_at,
                   COUNT(s.id) as total_rows,
                   SUM(CASE WHEN s.active=1 THEN 1 ELSE 0 END) as active_rows
            FROM games g
            LEFT JOIN stats s ON s.game_id = g.id AND s.guild_id = ?
            WHERE g.guild_id = ?
            GROUP BY g.id
            HAVING SUM(CASE WHEN s.active=1 THEN 1 ELSE 0 END) > 0
            ORDER BY g.id DESC
            """,
            (str(guild.id), str(guild.id))
        ) as cur:
            match_rows = await cur.fetchall()

        if not match_rows:
            matches_embed.description = "No matches with active stats yet."
        else:
            for (gid, filename, uploader_id, created_at, total_rows, active_rows) in match_rows:
                uploader_display = str(uploader_id)
                try:
                    if uploader_id:
                        member = guild.get_member(int(uploader_id))
                        if member:
                            uploader_display = member.display_name
                except Exception:
                    pass
                created_at_str = created_at or "unknown"
                value = f"File: {filename}\nUploader: {uploader_display}\nUploaded: {created_at_str}\nRows: {active_rows}/{total_rows} active"
                matches_embed.add_field(name=f"Match ID {gid}", value=value, inline=False)

        matches_msg = await self._find_bot_message_by_title(channel, matches_embed.title)
        if matches_msg:
            try:
                await matches_msg.edit(embed=matches_embed)
            except Exception:
                await channel.send(embed=matches_embed)
        else:
            await channel.send(embed=matches_embed)

        # All-time embed
        all_embed = discord.Embed(title="Leaderboards — All-time", color=discord.Color.gold(), timestamp=datetime.datetime.utcnow())
        all_embed.set_footer(text="All-time top 10 per metric (sums or averages as configured).")
        for mk in enabled_metrics:
            if mk not in METRIC_DEFS:
                continue
            rows = await self.get_all_time_leaderboard(guild.id, mk, limit=10)
            if not rows:
                all_embed.add_field(name=METRIC_DEFS[mk]["label"], value="No data.", inline=False)
                continue
            lines = []
            for i, (pid, val) in enumerate(rows, start=1):
                latest_name = await self.get_latest_name(guild.id, pid) or pid
                disp = METRIC_DEFS[mk]["fmt"](val)
                lines.append(f"{i}. {latest_name} — {disp}")
            all_embed.add_field(name=METRIC_DEFS[mk]["label"], value="\n".join(lines), inline=False)

        all_msg = await self._find_bot_message_by_title(channel, all_embed.title)
        if all_msg:
            try:
                await all_msg.edit(embed=all_embed)
            except Exception:
                await channel.send(embed=all_embed)
        else:
            await channel.send(embed=all_embed)

        # Rolling embed
        roll_embed = discord.Embed(title=f"Leaderboards — Rolling (last {window})", color=discord.Color.orange(), timestamp=datetime.datetime.utcnow())
        roll_embed.set_footer(text=f"Rolling averages / maxima over last {window} games.")
        for mk in enabled_metrics:
            if mk not in METRIC_DEFS:
                continue
            rows = await self.get_rolling_leaderboard(guild.id, mk, window, limit=10)
            if not rows:
                roll_embed.add_field(name=METRIC_DEFS[mk]["label"], value="No data.", inline=False)
                continue
            lines = []
            for i, (pid, val) in enumerate(rows, start=1):
                latest_name = await self.get_latest_name(guild.id, pid) or pid
                disp = METRIC_DEFS[mk]["fmt"](val)
                lines.append(f"{i}. {latest_name} — {disp}")
            roll_embed.add_field(name=METRIC_DEFS[mk]["label"], value="\n".join(lines), inline=False)

        roll_msg = await self._find_bot_message_by_title(channel, roll_embed.title)
        if roll_msg:
            try:
                await roll_msg.edit(embed=roll_embed)
            except Exception:
                await channel.send(embed=roll_embed)
        else:
            await channel.send(embed=roll_embed)

    @app_commands.guild_only()
    @app_commands.describe(player_id="Optional: specify player ID to view (if not auto-linked)")
    @app_commands.command(name="myhllstats", description="Show your all-time and rolling stats (configurable metrics).")
    async def myhllstats(self, interaction: discord.Interaction, player_id: Optional[str] = None):
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        if interaction.guild_id != GUILD_ID:
            await interaction.followup.send("This bot is configured for a different guild.", ephemeral=True)
            return

        settings = await self.get_settings(interaction.guild_id)
        window = settings["rolling_window_games"]
        enabled = settings["enabled_metrics"]

        pid = player_id or await self.get_linked_player_id(interaction.guild_id, interaction.user.id)
        if not pid:
            await interaction.followup.send("No player ID linked. Upload a CSV with your player name (matching your Discord name) using /extract-stats so the bot can auto-link you.", ephemeral=True)
            return

        latest_name = await self.get_latest_name(interaction.guild_id, pid) or "Unknown"
        all_time = await self.get_all_time_stats(interaction.guild_id, pid)
        if not all_time:
            await interaction.followup.send("No stats found for your player ID yet.", ephemeral=True)
            return
        rolling = await self.get_rolling_stats(interaction.guild_id, pid, window)

        def fmt(mk: str, v: Optional[float]) -> str:
            if v is None:
                return "—"
            return METRIC_DEFS[mk]["fmt"](v)

        all_time_lines = [f"Games: {all_time['games']}"]
        for mk in enabled:
            if mk not in METRIC_DEFS:
                continue
            val = all_time.get(mk)
            all_time_lines.append(f"{METRIC_DEFS[mk]['label']}: {fmt(mk, val)}")

        rolling_lines = []
        if rolling:
            roll_map = {
                "kills": rolling["avg_kills"],
                "deaths": rolling["avg_deaths"],
                "kdr": rolling["avg_kdr"],
                "kpm": rolling["avg_kpm"],
                "dpm": rolling["avg_dpm"],
            }
            for mk in enabled:
                if mk in roll_map:
                    rolling_lines.append(f"{METRIC_DEFS[mk]['label']}: {fmt(mk, roll_map[mk])}")
                else:
                    rolling_lines.append(f"{METRIC_DEFS[mk]['label']}: (N/A)")
        else:
            rolling_lines.append(f"Not enough games yet (need at least 1 of last {window}).")

        embed = discord.Embed(title=f"My HLL Stats • {latest_name}", color=discord.Color.blurple())
        embed.add_field(name="All-time", value="\n".join(all_time_lines), inline=True)
        embed.add_field(name=f"Rolling (last {window})", value="\n".join(rolling_lines), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

# setup entrypoint
async def setup(bot: commands.Bot):
    cog = HLLStatsCog(bot)
    await bot.add_cog(cog)

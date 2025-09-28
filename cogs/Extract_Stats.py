"""
HLL Stats Cog (no graphing) — single-guild, automatic Discord -> player_id linking.

Behavior updates in this version:
- Single-guild configuration via integer constants at the top.
- /extract-stats ingests CSVs, auto-links members, stores stats.
- After ingest completes the cog now UPDATES existing leaderboard messages in the configured
  channel (one embed for All-time and one embed for Rolling). If the embeds are not found,
  it posts them. It does NOT post duplicate leaderboards each time.
- Each embed contains multiple fields: one field per enabled metric listing the Top 10.
- CSV-provided names remain authoritative in the DB.


Drop this file into cogs/hll_stats.py and load it with:
    await bot.load_extension("cogs.hll_stats")
"""
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
# Configuration - single guild + leaderboard channel (define integers here)
# =========================
# Edit these integer constants to match your server and channel IDs.
GUILD_ID: int = 1097913605082579024  # <-- set your guild ID (integer)
LEADERBOARD_CHANNEL_ID: int = 1099806153170489485  # <-- set your leaderboard channel ID (integer)

# Rolling window and DB path (set as desired)
DEFAULT_ROLLING_WINDOW_GAMES: int = 5
DB_PATH: str = "hll_stats.sqlite3"

GUILD_IDS = [GUILD_ID]
GUILDS = [discord.Object(id=GUILD_ID)]

# =========================
# Utilities: trimming, normalization, rank stripping
# =========================

def _trim_discriminator(name: Optional[str]) -> Optional[str]:
    """Remove trailing Discord discriminator '#1234' if present."""
    if name is None:
        return None
    return re.sub(r'#\d{1,10}$', '', name).strip()

# Rank tokens based on the provided rank list; include common abbreviations and spacing variants.
RANK_TOKENS = [
    # General Staff
    "Field Marshal", "FM",
    "General", "Gen",
    "Lieutenant General", "Lt Gen", "Lt.Gen", "LtGen",
    "Major General", "Maj Gen", "Maj.Gen", "MajGen",
    "Brigadier", "Brig",

    # Command Staff
    "Colonel", "Col",
    "Lieutenant Colonel", "Lt Col", "Lt.Col", "LtCol",
    "Major", "Maj",
    "Captain", "Cpt",
    "Lieutenant", "Lt", "Lt.",
    "2nd Lieutenant", "2Lt", "2ndLt", "2 Lt",

    # SNCO
    "Regimental Sargent Major", "RSM", "WO1", "WO2",
    "Warrant Officer 1st Class", "Warrant Officer 2nd Class",

    # NCO
    "Sergeant major", "SGM", "Staff Sargent", "SSG",

    # Junior Enlisted
    "Sergeant", "Sgt",
    "Corporal", "Cpl",
    "L.Cpl", "LCpl", "L Cpl",
    "Private", "Pte",
    "Recruit",
]

# Build robust regex (case-insensitive) to match rank at the start of name.
_rank_variants = []
for tok in RANK_TOKENS:
    esc = re.escape(tok)
    # permit flexible separators where spaces exist
    esc = esc.replace(r'\ ', r'[\s\._-]*')
    _rank_variants.append(esc)

_RANK_PREFIX_RE = re.compile(r'^(?:' + r'|'.join(_rank_variants) + r')[\s\._-]*', re.IGNORECASE)

def _strip_rank_prefix(name: Optional[str]) -> Optional[str]:
    """Remove a leading rank token if present."""
    if name is None:
        return None
    s = name.strip()
    new = _RANK_PREFIX_RE.sub('', s)
    return new.strip()

def _normalize_for_match(s: Optional[str]) -> Optional[str]:
    """
    Normalize for matching:
      - trim trailing discriminator
      - strip leading rank token
      - collapse whitespace
      - lowercase (casefold)
    """
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
# Metric definitions
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

DEFAULT_ENABLED_METRICS = ["kills", "deaths", "kdr", "kpm", "dpm"]

# =========================
# DB schema
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
  message_id INTEGER NOT NULL,
  created_at TIMESTAMP NOT NULL,
  source_filename TEXT NOT NULL,
  file_hash TEXT NOT NULL,
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
    await conn.commit()

# =========================
# Cog
# =========================

class HLLStatsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Optional[aiosqlite.Connection] = None

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
            await self.db.execute(
                "INSERT INTO guild_settings (guild_id, rolling_window_games, enabled_metrics) VALUES (?, ?, ?)",
                (str(guild_id), DEFAULT_ROLLING_WINDOW_GAMES, json.dumps(DEFAULT_ENABLED_METRICS)),
            )
            await self.db.commit()

    async def get_settings(self, guild_id: int) -> Dict[str, Any]:
        assert self.db
        await self.ensure_guild_settings(guild_id)
        async with self.db.execute("SELECT rolling_window_games, enabled_metrics FROM guild_settings WHERE guild_id=?", (str(guild_id),)) as cur:
            row = await cur.fetchone()
        window = row[0]
        try:
            enabled = json.loads(row[1]) if row and row[1] else DEFAULT_ENABLED_METRICS
        except Exception:
            enabled = DEFAULT_ENABLED_METRICS
        enabled = [m for m in enabled if m in METRIC_DEFS]
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

    async def insert_game(self, guild_id: int, uploader_id: int, message_id: int, filename: str, file_hash: str) -> Optional[int]:
        assert self.db
        now = datetime.datetime.utcnow().isoformat()
        try:
            cur = await self.db.execute(
                "INSERT INTO games (guild_id, uploader_id, message_id, created_at, source_filename, file_hash) VALUES (?, ?, ?, ?, ?, ?)",
                (str(guild_id), uploader_id, message_id, now, filename, file_hash),
            )
            await self.db.commit()
            return cur.lastrowid
        except aiosqlite.IntegrityError:
            return None

    async def insert_stat(self, guild_id: int, game_id: int, mapped: Dict[str, Any]) -> None:
        assert self.db
        await self.db.execute(
            """
            INSERT INTO stats (
              guild_id, game_id, player_id,
              kills, deaths, kdr, kpm, dpm,
              combat_effectiveness, support_points, defensive_points, offensive_points,
              max_kill_streak, max_death_streak,
              weapons, death_by_weapons, extras
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(guild_id), game_id, mapped["player_id"],
                mapped.get("kills"), mapped.get("deaths"), mapped.get("kdr"), mapped.get("kpm"), mapped.get("dpm"),
                mapped.get("combat_effectiveness"), mapped.get("support_points"), mapped.get("defensive_points"), mapped.get("offensive_points"),
                mapped.get("max_kill_streak"), mapped.get("max_death_streak"),
                mapped.get("weapons"), mapped.get("death_by_weapons"), json.dumps(mapped.get("extras") or {}),
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

    # ---- Stats queries ----
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
        async with self.db.execute(f"SELECT {', '.join(selects)} FROM stats WHERE guild_id=? AND player_id=?", (str(guild_id), player_id)) as cur:
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
            WHERE guild_id=? AND player_id=?
            ORDER BY id ASC
            LIMIT ?
            """,
            (str(guild_id), player_id, n),
        ) as cur:
            rows = await cur.fetchall()
        keys = ["id","kills","deaths","kdr","kpm","dpm","combat_effectiveness","support_points","defensive_points","offensive_points","max_kill_streak","max_death_streak"]
        return [dict(zip(keys, r)) for r in rows]

    async def get_rolling_stats(self, guild_id: int, player_id: str, window: int) -> Optional[Dict[str, Any]]:
        assert self.db
        async with self.db.execute("SELECT kills, deaths, kdr, kpm, dpm FROM stats WHERE guild_id=? AND player_id=? ORDER BY id DESC LIMIT ?", (str(guild_id), player_id, window)) as cur:
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
            sql = f"SELECT player_id, SUM(COALESCE({col},0)) as v FROM stats WHERE guild_id=? GROUP BY player_id HAVING COUNT(*) > 0 ORDER BY v DESC LIMIT ?"
        elif agg == "avg":
            sql = f"SELECT player_id, AVG({col}) as v FROM stats WHERE guild_id=? GROUP BY player_id HAVING COUNT(*) > 0 ORDER BY v DESC LIMIT ?"
        else:
            sql = f"SELECT player_id, MAX({col}) as v FROM stats WHERE guild_id=? GROUP BY player_id HAVING COUNT(*) > 0 ORDER BY v DESC LIMIT ?"
        async with self.db.execute(sql, (str(guild_id), limit)) as cur:
            return [(r[0], r[1]) for r in await cur.fetchall() if r[1] is not None]

    async def get_rolling_leaderboard(self, guild_id: int, metric: str, window: int, limit: int = 10) -> List[Tuple[str, float]]:
        assert self.db
        md = METRIC_DEFS[metric]
        col = md["column"]
        agg_rolling = md["rolling"]
        results: List[Tuple[str, float]] = []
        for pid in await self.get_all_player_ids(guild_id):
            async with self.db.execute(f"SELECT {col} FROM stats WHERE guild_id=? AND player_id=? ORDER BY id DESC LIMIT ?", (str(guild_id), pid, window)) as cur:
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
        game_id = await self.insert_game(guild_id=interaction.guild_id, uploader_id=interaction.user.id, message_id=interaction.id, filename=file.filename, file_hash=file_hash)
        if game_id is None:
            await interaction.followup.send("This CSV appears to have been uploaded before. Skipping duplicate.", ephemeral=True)
            return

        decoded = raw_bytes.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(decoded))
        if not reader.fieldnames:
            await interaction.followup.send("CSV has no header row.", ephemeral=True)
            return

        # Pre-fetch guild members and build normalized-name -> member_id map.
        members_map: Dict[str, int] = {}
        try:
            members = [m async for m in interaction.guild.fetch_members(limit=None)]
            for m in members:
                for cand in (m.name, m.display_name):
                    norm = _normalize_for_match(cand)
                    if norm:
                        # keep first found mapping
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

            # CSV name is authoritative
            csv_name = name or "Unknown"
            await self.upsert_player(interaction.guild_id, pid, csv_name)
            if pid in existing_pids:
                updated_players += 1
            else:
                created_players += 1
                existing_pids.add(pid)

            await self.insert_stat(interaction.guild_id, game_id, mapped)
            processed += 1

            # Attempt auto-link using normalized CSV name
            norm_csv = _normalize_for_match(csv_name)
            if norm_csv and norm_csv in members_map:
                discord_id = members_map[norm_csv]
                try:
                    await self.link_user(interaction.guild_id, discord_id, pid)
                    auto_linked += 1
                except Exception:
                    pass

        await self.commit()

        # Post or update leaderboards (all enabled metrics) to configured channel
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

        # notify about leaderboard update result
        await interaction.followup.send(posted_note, ephemeral=True)

    async def _find_bot_message_by_title(self, channel: discord.abc.Messageable, title: str) -> Optional[discord.Message]:
        """
        Search recent messages in channel for a message authored by the bot with an embed title matching `title`.
        Returns the message if found, else None.
        """
        # channel may be TextChannel; use history
        if not isinstance(channel, discord.TextChannel):
            # try to fetch channel if not full object (shouldn't normally happen)
            return None
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
        """
        Compute and either edit existing leaderboard messages or post new ones.
        Two embeds:
          - "Leaderboards — All-time" (fields: one per enabled metric containing top10)
          - "Leaderboards — Rolling (last X)" (fields: one per enabled metric containing top10 rolling)
        If an embed with the exact title and authored by the bot exists in the channel recent history,
        it will be edited; otherwise a new message will be sent.
        """
        settings = await self.get_settings(guild.id)
        enabled_metrics = settings["enabled_metrics"]
        window = settings["rolling_window_games"]

        # Resolve channel
        channel = guild.get_channel(LEADERBOARD_CHANNEL_ID) or await guild.fetch_channel(LEADERBOARD_CHANNEL_ID)
        if channel is None:
            raise RuntimeError(f"Leaderboard channel not found (ID {LEADERBOARD_CHANNEL_ID})")

        # Build All-time embed
        all_embed = discord.Embed(title="Leaderboards — All-time", color=discord.Color.gold(), timestamp=datetime.datetime.utcnow())
        all_embed.set_footer(text="All-time top 10 per metric (sums or averages as configured).")
        any_all_data = False
        for mk in enabled_metrics:
            rows = await self.get_all_time_leaderboard(guild.id, mk, limit=10)
            if not rows:
                all_embed.add_field(name=METRIC_DEFS[mk]["label"], value="No data.", inline=False)
                continue
            any_all_data = True
            lines = []
            for i, (pid, val) in enumerate(rows, start=1):
                latest_name = await self.get_latest_name(guild.id, pid) or pid
                disp = METRIC_DEFS[mk]["fmt"](val)
                lines.append(f"{i}. {latest_name} — {disp}")
            all_embed.add_field(name=METRIC_DEFS[mk]["label"], value="\n".join(lines), inline=False)

        # Build Rolling embed
        roll_embed = discord.Embed(title=f"Leaderboards — Rolling (last {window})", color=discord.Color.orange(), timestamp=datetime.datetime.utcnow())
        roll_embed.set_footer(text=f"Rolling averages / maxima over last {window} games.")
        any_roll_data = False
        for mk in enabled_metrics:
            rows = await self.get_rolling_leaderboard(guild.id, mk, window, limit=10)
            if not rows:
                roll_embed.add_field(name=METRIC_DEFS[mk]["label"], value="No data.", inline=False)
                continue
            any_roll_data = True
            lines = []
            for i, (pid, val) in enumerate(rows, start=1):
                latest_name = await self.get_latest_name(guild.id, pid) or pid
                disp = METRIC_DEFS[mk]["fmt"](val)
                lines.append(f"{i}. {latest_name} — {disp}")
            roll_embed.add_field(name=METRIC_DEFS[mk]["label"], value="\n".join(lines), inline=False)

        # If there's no data in either embed, still create/update messages to show empty state.
        # Find existing messages by searching recent messages for matching titles authored by bot.
        # Update if found, else send new message.
        all_msg = await self._find_bot_message_by_title(channel, all_embed.title)
        if all_msg:
            try:
                await all_msg.edit(embed=all_embed)
            except Exception:
                # fallback: send new
                await channel.send(embed=all_embed)
        else:
            await channel.send(embed=all_embed)

        roll_msg = await self._find_bot_message_by_title(channel, roll_embed.title)
        if roll_msg:
            try:
                await roll_msg.edit(embed=roll_embed)
            except Exception:
                await channel.send(embed=roll_embed)
        else:
            await channel.send(embed=roll_embed)

    # Allow users to unlink if they don't want the auto-link
    @app_commands.guild_only()
    @app_commands.command(name="unlinkplayer", description="Unlink your Discord user from any auto-linked player ID.")
    async def unlinkplayer(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        await self.unlink_user(interaction.guild_id, interaction.user.id)
        await self.commit()
        await interaction.response.send_message("Unlinked your player ID (if it existed).", ephemeral=True)

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

    # Stats-config group (enable/disable metrics, set rolling window)
    stats_config = app_commands.Group(name="stats-config", description="Configure which stats are shown and rolling window.")

    @stats_config.command(name="list", description="Show enabled metrics and rolling window.")
    @app_commands.guild_only()
    async def stats_config_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        settings = await self.get_settings(interaction.guild_id)
        enabled = settings["enabled_metrics"]
        labels = [f"- {m} ({METRIC_DEFS[m]['label']})" for m in enabled]
        all_metrics = ", ".join(sorted(METRIC_DEFS.keys()))
        embed = discord.Embed(title="Stats Configuration", description="These metrics are currently enabled for display/leaderboards.", color=discord.Color.teal())
        embed.add_field(name=f"Enabled metrics ({len(enabled)})", value="\n".join(labels) or "None", inline=False)
        embed.add_field(name="Rolling window", value=str(settings["rolling_window_games"]), inline=True)
        embed.add_field(name="Available metric keys", value=all_metrics, inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @stats_config.command(name="enable", description="Enable a metric for display/leaderboards.")
    @app_commands.describe(metric="Metric key to enable")
    @app_commands.guild_only()
    async def stats_config_enable(self, interaction: discord.Interaction, metric: str):
        await interaction.response.defer(ephemeral=True)
        if metric not in METRIC_DEFS:
            await interaction.followup.send(f"Unknown metric '{metric}'.", ephemeral=True)
            return
        settings = await self.get_settings(interaction.guild_id)
        if metric in settings["enabled_metrics"]:
            await interaction.followup.send(f"'{metric}' is already enabled.", ephemeral=True)
            return
        new_enabled = settings["enabled_metrics"] + [metric]
        await self.set_enabled_metrics(interaction.guild_id, new_enabled)
        await interaction.followup.send(f"Enabled '{metric}' ({METRIC_DEFS[metric]['label']}).", ephemeral=True)

    @stats_config.command(name="disable", description="Disable a metric from display/leaderboards.")
    @app_commands.describe(metric="Metric key to disable")
    @app_commands.guild_only()
    async def stats_config_disable(self, interaction: discord.Interaction, metric: str):
        await interaction.response.defer(ephemeral=True)
        if metric not in METRIC_DEFS:
            await interaction.followup.send(f"Unknown metric '{metric}'.", ephemeral=True)
            return
        settings = await self.get_settings(interaction.guild_id)
        if metric not in settings["enabled_metrics"]:
            await interaction.followup.send(f"'{metric}' is already disabled.", ephemeral=True)
            return
        new_enabled = [m for m in settings["enabled_metrics"] if m != metric]
        await self.set_enabled_metrics(interaction.guild_id, new_enabled)
        await interaction.followup.send(f"Disabled '{metric}' ({METRIC_DEFS[metric]['label']}).", ephemeral=True)

    @stats_config.command(name="set-rolling", description="Set rolling window X (1-100).")
    @app_commands.describe(window="Number of most recent games to use for rolling averages (1-100)")
    @app_commands.guild_only()
    async def stats_config_set_rolling(self, interaction: discord.Interaction, window: app_commands.Range[int, 1, 100]):
        await interaction.response.defer(ephemeral=True)
        await self.set_rolling_window(interaction.guild_id, int(window))
        await interaction.followup.send(f"Set rolling window to {int(window)} games.", ephemeral=True)

# setup entrypoint
async def setup(bot: commands.Bot):
    cog = HLLStatsCog(bot)
    await bot.add_cog(cog)

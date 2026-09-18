"""Independent SQLite history, current checks and append-only check audit."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .loader import Index
from .models import Check, Player, Status


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS checks (
                player_id TEXT PRIMARY KEY, name TEXT, platform TEXT,
                status TEXT, reason TEXT, http_status INTEGER,
                checked_at TEXT, evidence TEXT);
            CREATE TABLE IF NOT EXISTS check_history (
                player_id TEXT, name TEXT, platform TEXT, status TEXT,
                reason TEXT, http_status INTEGER, checked_at TEXT, evidence TEXT);
            CREATE TABLE IF NOT EXISTS matches (
                server TEXT, match_id TEXT, started_at TEXT,
                PRIMARY KEY(server, match_id));
            CREATE TABLE IF NOT EXISTS appearances (
                server TEXT, match_id TEXT, player_id TEXT, name TEXT, platform TEXT,
                PRIMARY KEY(server, match_id, player_id, name));
            CREATE INDEX IF NOT EXISTS appearances_player ON appearances(player_id);
        """)

    def close(self) -> None:
        self.db.close()

    def record_index(self, index: Index) -> None:
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO matches VALUES (?,?,?)",
                                [(server, mid, date) for (server, mid), date in index.matches.items()])
            self.db.executemany("INSERT OR REPLACE INTO appearances VALUES (?,?,?,?,?)", index.appearances)
            self.db.executemany("UPDATE checks SET name=?, platform=? WHERE player_id=?",
                                [(p.overall.current_name, p.overall.platform, uid) for uid, p in index.players.items()])

    def cached(self, uid: str, ttl: float, banned_ttl: float,
               refresh: bool = False, refresh_banned: bool = False) -> Check | None:
        row = self.db.execute("SELECT * FROM checks WHERE player_id=?", (uid,)).fetchone()
        if row is None:
            return None
        status = Status(row["status"])
        if status not in (Status.BANNED, Status.NOT_BANNED):
            return None
        if status == Status.BANNED and refresh_banned or status != Status.BANNED and refresh:
            return None
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["checked_at"])).total_seconds()
        if not 0 <= age < (banned_ttl if status == Status.BANNED else ttl) * 86400:
            return None
        return Check(status, row["reason"], row["http_status"], row["checked_at"], row["evidence"])

    def save(self, player: Player, result: Check) -> None:
        values = (player.player_id, player.overall.current_name, player.overall.platform,
                  result.status, result.reason, result.http_status, result.checked_at, result.evidence)
        # Commit every check: an interrupted run loses at most its in-flight GET.
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO checks VALUES (?,?,?,?,?,?,?,?)", values)
            self.db.execute("INSERT INTO check_history VALUES (?,?,?,?,?,?,?,?)", values)

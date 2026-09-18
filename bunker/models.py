"""Shared result and player-history models."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Status(StrEnum):
    BANNED = "BANNED"
    NOT_BANNED = "NOT_BANNED"
    UNKNOWN = "UNKNOWN"
    REQUEST_FAILED = "REQUEST_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    NOT_FOUND = "NOT_FOUND"
    PARSE_ERROR = "PARSE_ERROR"


@dataclass
class Check:
    status: Status
    reason: str = ""
    http_status: int | None = None
    checked_at: str = field(default_factory=utcnow)
    evidence: str = ""

    @property
    def bunker_banned(self) -> bool | None:
        if self.status == Status.BANNED:
            return True
        if self.status == Status.NOT_BANNED:
            return False
        return None


@dataclass
class History:
    matches: set[str] = field(default_factory=set)
    aliases: set[str] = field(default_factory=set)
    first_seen: str = ""
    last_seen: str = ""
    current_name: str = ""
    platform: str = ""
    name_time: str = ""

    def add(self, match_id: str, date: str, name: str, platform: str) -> None:
        self.matches.add(match_id)
        if name:
            self.aliases.add(name)
        if date:
            self.first_seen = min(self.first_seen or date, date)
            self.last_seen = max(self.last_seen, date)
        if name and (not self.current_name or date >= self.name_time):
            self.current_name, self.name_time = name, date
            self.platform = platform or self.platform


@dataclass
class Player:
    player_id: str
    overall: History = field(default_factory=History)
    servers: dict[str, History] = field(default_factory=dict)

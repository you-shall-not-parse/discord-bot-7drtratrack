"""Stream match files into a deduplicated player index before any HTTP checks."""
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .models import History, Player

log = logging.getLogger(__name__)
UID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


@dataclass
class Index:
    players: dict[str, Player] = field(default_factory=dict)
    matches: dict[tuple[str, str], str] = field(default_factory=dict)
    appearances: list[tuple[str, str, str, str, str]] = field(default_factory=list)
    input_errors: int = 0
    duplicate_matches: int = 0


def match_date(value: object) -> str:
    """Normalize ISO dates or Unix seconds/milliseconds; unknown dates stay empty."""
    if value is None or value == "":
        return ""
    try:
        if isinstance(value, (int, float)):
            stamp = value / 1000 if value > 100_000_000_000 else value
            parsed = datetime.fromtimestamp(stamp, timezone.utc)
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        log.warning("Unrecognized match timestamp: %.100s", value)
        return ""


def load_matches(sources: list[tuple[str, Path]]) -> Index:
    """Load one result.player_stats payload per JSON file, recursively."""
    index = Index()
    for server, directory in sources:
        paths = sorted(directory.rglob("*.json")) if directory.is_dir() else [directory]
        if not paths:
            raise ValueError(f"No JSON matches found in {directory}")
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
                result = payload.get("result") if isinstance(payload, dict) else None
                if not isinstance(result, dict) or not isinstance(result.get("player_stats"), list):
                    raise ValueError("Expected result.player_stats array")
                raw_id = next((obj[key] for obj in (result, payload)
                               for key in ("match_id", "map_id", "id")
                               if obj.get(key) is not None), None)
                # Identical exports without an ID must not inflate match counts.
                match_id = str(raw_id) if raw_id is not None else "sha256:" + hashlib.sha256(
                    json.dumps(result, sort_keys=True).encode()).hexdigest()
                date = next((obj[key] for obj in (result, payload)
                             for key in ("start", "start_time", "started_at", "match_start", "date", "time_start")
                             if obj.get(key) is not None), None)
                stamp = match_date(date)
                key = (server, match_id)
                if key in index.matches:
                    index.duplicate_matches += 1
                    continue
                index.matches[key] = stamp
                for row in result["player_stats"]:
                    if not isinstance(row, dict) or not isinstance(row.get("player_id"), str) or not UID.fullmatch(row["player_id"]):
                        index.input_errors += 1
                        log.warning("Skipped invalid player entry in %s", path)
                        continue
                    uid = row["player_id"]
                    name = str(row.get("player") or "")
                    platform = str(row.get("platform") or "")
                    player = index.players.setdefault(uid, Player(uid))
                    player.overall.add(f"{server}\0{match_id}", stamp, name, platform)
                    player.servers.setdefault(server, History()).add(match_id, stamp, name, platform)
                    index.appearances.append((server, match_id, uid, name, platform))
            except (OSError, ValueError) as exc:
                index.input_errors += 1
                log.warning("Could not load %s: %s", path, exc)
        log.info("Indexed %s: %d total matches, %d unique players", server, len(index.matches), len(index.players))
    return index

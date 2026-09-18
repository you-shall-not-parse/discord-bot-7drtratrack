"""CSV exports use one player/server row and separate cross-server rollups."""
import csv
import json
from collections import Counter
from pathlib import Path

from spreadsheet_security import safe_spreadsheet_value
from .client import BASE
from .loader import Index
from .models import Check, Status


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: safe_spreadsheet_value(value) for key, value in row.items() if key in fields})
    temp.replace(path)


def export(index: Index, checks: dict[str, Check], output: Path,
           cache_hits: int, live_requests: int, interrupted: bool = False) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    rows, cross, history = [], [], []
    counts: Counter = Counter()
    for uid, player in sorted(index.players.items()):
        result = checks.get(uid, Check(Status.UNKNOWN, checked_at="", evidence="Not checked in this run"))
        counts[result.status.value] += 1
        for server, seen in sorted(player.servers.items()):
            rows.append(dict(server=server, player_id=uid, current_name=seen.current_name,
                             aliases=json.dumps(sorted(seen.aliases), ensure_ascii=False), platform=seen.platform,
                             matches_seen=len(seen.matches), first_seen=seen.first_seen, last_seen=seen.last_seen,
                             bunker_status=result.status, ban_reason=result.reason, checked_at=result.checked_at,
                             hllrecords_url=BASE + uid, http_status=result.http_status, evidence=result.evidence))
        if len(player.servers) > 1:
            cross.append(dict(player_id=uid, current_name=player.overall.current_name,
                              servers=json.dumps(sorted(player.servers)), bunker_status=result.status,
                              ban_reason=result.reason, matches_seen=len(player.overall.matches), hllrecords_url=BASE + uid))
    for server, mid, uid, name, platform in index.appearances:
        history.append(dict(server=server, match_id=mid, match_start=index.matches[server, mid],
                            player_id=uid, player=name, platform=platform))
    common = ["server", "player_id", "current_name", "aliases", "platform", "matches_seen", "first_seen", "last_seen"]
    write_csv(output / "all_players.csv", common + ["bunker_status", "ban_reason", "checked_at", "http_status", "evidence"], rows)
    write_csv(output / "flagged_players.csv", common + ["ban_reason", "hllrecords_url"], [r for r in rows if r["bunker_status"] == Status.BANNED])
    write_csv(output / "cross_server_players.csv", ["player_id", "current_name", "servers", "bunker_status", "ban_reason", "matches_seen", "hllrecords_url"], cross)
    write_csv(output / "appearances.csv", ["server", "match_id", "match_start", "player_id", "player", "platform"], history)
    summary = dict(total_matches_processed=len(index.matches), total_player_appearances=len(index.appearances),
                   total_unique_players=len(index.players),
                   unique_players_by_server=dict(Counter(s for p in index.players.values() for s in p.servers)),
                   bunker_banned_players=counts["BANNED"],
                   unknown_or_failed_checks=sum(n for s, n in counts.items() if s not in ("BANNED", "NOT_BANNED")),
                   statuses=dict(counts), cache_hits=cache_hits, live_requests_made=live_requests,
                   cross_server_players=len(cross), banned_on_multiple_servers=sum(r["bunker_status"] == Status.BANNED for r in cross),
                   input_errors=index.input_errors, duplicate_matches_skipped=index.duplicate_matches, interrupted=interrupted)
    temp = output / "summary.json.tmp"
    temp.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    temp.replace(output / "summary.json")
    return summary

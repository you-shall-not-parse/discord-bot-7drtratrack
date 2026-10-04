"""Cached raw-kill totals from completed public match exports."""
import ipaddress
import json
import re
from urllib.parse import parse_qs, urlparse, urlunparse

import aiohttp
from aiohttp.resolver import DefaultResolver


def source_url(link):
    from cogs.wardiary import WarDiaryCog, _canonical_stats_link
    canonical = _canonical_stats_link(str(link or ""))
    if not canonical:
        raise ValueError("Missing stats link")
    parsed = urlparse(canonical)
    if parsed.username or parsed.password or parsed.scheme not in {"http", "https"}:
        raise ValueError("Unsupported stats link")
    host = (parsed.hostname or "").lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Stats host must be public")
    path = parsed.path.rstrip("/")
    is_bifrost = any(host == domain or host.endswith("." + domain) for domain in ("bifrostgaming.com", "bifroststats.com"))
    direct = re.fullmatch(r"/hll/[A-Za-z0-9_-]+/[0-9a-fA-F-]{36}(?:/crcon)?", path)
    if direct:
        if not is_bifrost:
            raise ValueError("Unsupported Bifrost stats host")
        return urlunparse((parsed.scheme, parsed.netloc.lower(), path.removesuffix("/crcon") + "/crcon", "", "", ""))
    api = WarDiaryCog._crcon_match_api_url(canonical)
    if not api:
        raise ValueError("Use a specific completed match stats link")
    return api


class PublicResolver(DefaultResolver):
    async def resolve(self, host, port=0, family=0):
        records = await super().resolve(host, port, family)
        if not records or any(not ipaddress.ip_address(record["host"]).is_global for record in records):
            raise ValueError("Stats host must resolve to public addresses")
        return records


def parse_export(payload, source):
    if not isinstance(payload, dict) or payload.get("failed") or payload.get("rosterComplete") is False:
        raise ValueError("Invalid or incomplete stats export")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("Invalid match result")
    parsed = urlparse(source)
    expected = parse_qs(parsed.query).get("map_id", [parsed.path.split("/")[-2]])[0]
    if str(result.get("id")) != expected or not result.get("end"):
        raise ValueError("Wrong match or match has not ended")
    players = result.get("player_stats")
    if not isinstance(players, list) or not players:
        raise ValueError("No player stats available")
    rows, seen = [], set()
    for player in players:
        if not isinstance(player, dict):
            raise ValueError("Invalid player record")
        identity = str(player.get("player_id") or "").strip()
        if not identity or identity in seen:
            raise ValueError("Missing or duplicate player ID")
        seen.add(identity)
        values = [player.get(key) for key in ("kills", "deaths", "time_seconds")]
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
            raise ValueError("Invalid player totals")
        rows.append(dict(player_id=identity, name=str(player.get("player") or "Unknown player"),
                         kills=values[0], deaths=values[1], seconds=values[2]))
    return rows


async def fetch_export(session, source):
    async with session.get(source, allow_redirects=False) as response:
        if response.status != 200:
            raise ValueError(f"Stats server returned HTTP {response.status}")
        body = bytearray()
        async for chunk in response.content.iter_chunked(65536):
            body.extend(chunk)
            if len(body) > 8 * 1024 * 1024:
                raise ValueError("Stats export is too large")
        return parse_export(json.loads(body), source)


def sources(records):
    linked = {}
    for record in records:
        try:
            source = source_url(record.get("stats_link"))
        except (ValueError, TypeError):
            continue
        linked.setdefault(source, []).append(record)
    return linked


def clan_tag(name):
    return bool(re.search(r"(?i)(?<![a-z0-9])7dr(?![a-z0-9])", name) or re.sub(r"[^a-z0-9]", "", name.lower()).startswith("7dr"))


def leaderboard(records, imports, known_ids=(), *, clan_only=True):
    linked = sources(records)
    valid = [source for source, entries in linked.items() if len(entries) == 1 and imports.get(source, {}).get("updated_at")]
    ids = set(known_ids)
    for source in valid:
        ids.update(row["player_id"] for row in imports[source]["rows"] if clan_tag(row["name"]))
    totals = {}
    for source in sorted(valid, key=lambda item: imports[item]["updated_at"]):
        for row in imports[source]["rows"]:
            if clan_only and row["player_id"] not in ids:
                continue
            total = totals.setdefault(row["player_id"], dict(player_id=row["player_id"], name=row["name"], kills=0, deaths=0, matches=0, seconds=0))
            total["name"] = row["name"]
            for key in ("kills", "deaths", "seconds"):
                total[key] += row[key]
            total["matches"] += 1
    rows = sorted(totals.values(), key=lambda row: (-row["kills"], row["name"].casefold(), row["player_id"]))
    last_kills, rank = None, 0
    for index, row in enumerate(rows, 1):
        if row["kills"] != last_kills:
            rank = index
        last_kills = row["kills"]
        row.update(rank=rank, kd=round(row["kills"] / row["deaths"], 2) if row["deaths"] else None,
                   kills_per_match=round(row["kills"] / row["matches"], 1))
    return dict(rows=rows, recorded=len(records), imported=len(valid),
                missing_links=sum(not record.get("stats_link") for record in records),
                unsupported_links=sum(bool(record.get("stats_link")) for record in records) - sum(len(entries) for entries in linked.values()),
                duplicate_links=sum(len(entries) for entries in linked.values() if len(entries) > 1),
                failed=sum(bool(imports.get(source, {}).get("error")) for source in linked if source not in valid),
                pending=sum(len(entries) == 1 and source not in imports for source, entries in linked.items()),
                stale=sum(bool(imports[source].get("error")) for source in valid),
                updated_at=max((imports[source]["updated_at"] for source in valid), default=None))

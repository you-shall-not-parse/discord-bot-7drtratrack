"""Command line orchestration and interruption-safe reporting."""
import argparse
import json
import logging
import math
import os
from pathlib import Path

from data_paths import data_dir
from .client import Client
from .database import Database
from .loader import load_matches
from .reporting import export
from .source import Importer, server_endpoint

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bulk HLLRecords Bunker-ban checker (Python 3.11+)")
    parser.add_argument("--server", nargs=2, action="append", required=True, metavar=("NAME", "DIRECTORY_OR_URL"))
    parser.add_argument("--download-dir", type=Path, default=data_dir() / "bunker_matches")
    parser.add_argument("--download-only", action="store_true", help="Import and index matches without HLLRecords requests")
    parser.add_argument("--source-delay", type=float, default=1.5, help="Pause between Bifrost requests")
    parser.add_argument("--output", type=Path, default=Path("output/bunker"))
    parser.add_argument("--cache-db", type=Path, default=data_dir() / "bunker_checker.sqlite3")
    parser.add_argument("--request-delay", type=float, default=os.getenv("HLLRECORDS_REQUEST_DELAY", "1.5"))
    parser.add_argument("--max-retries", type=int, default=os.getenv("HLLRECORDS_MAX_RETRIES", "4"))
    parser.add_argument("--timeout", type=float, default=os.getenv("HLLRECORDS_TIMEOUT", "20"))
    parser.add_argument("--cache-ttl-days", type=float, default=7)
    parser.add_argument("--banned-ttl-days", type=float, default=90)
    parser.add_argument("--refresh", action="store_true", help="Refresh non-banned results")
    parser.add_argument("--refresh-banned", action="store_true", help="Refresh confirmed bans too")
    parser.add_argument("--log-file", type=Path)
    args = parser.parse_args(argv)
    for key in ("request_delay", "source_delay", "timeout", "cache_ttl_days", "banned_ttl_days"):
        value = getattr(args, key)
        if not math.isfinite(value) or value < 0 or key == "timeout" and value == 0:
            parser.error(f"Invalid --{key.replace('_', '-')}")
    if not 0 <= args.max_retries <= 10:
        parser.error("--max-retries must be between 0 and 10")
    for name, source in args.server:
        if not name.strip():
            parser.error("Server name cannot be empty")
        if "://" in source:
            try:
                server_endpoint(source)
            except ValueError as exc:
                parser.error(str(exc))
        elif not Path(source).exists():
            parser.error(f"Missing source directory: {source}; --server also accepts a Bifrost server URL")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(args.log_file, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    importer = Importer(args.download_dir, args.source_delay, args.max_retries, args.timeout)
    try:
        sources = [(name.strip(), importer.download(source) if "://" in source else Path(source))
                   for name, source in args.server]
        index = load_matches(sources)
    except ValueError as exc:
        log.error("Import failed: %s", exc)
        return 2
    except KeyboardInterrupt:
        log.warning("Import interrupted; downloaded matches retained. Rerun to resume.")
        return 130
    finally:
        importer.close()
    if args.download_only:
        database = Database(args.cache_db)
        try:
            database.record_index(index)
        finally:
            database.close()
        summary = export(index, {}, args.output, 0, 0)
        log.info("Download-only complete: %s; Bifrost requests=%d downloaded=%d reused=%d",
                 json.dumps(summary), importer.requests, importer.downloads, importer.cache_hits)
        return 2 if index.input_errors or not index.players else 0
    if not index.players:
        log.error("No valid players found; no requests made")
        export(index, {}, args.output, 0, 0)
        return 2
    database = Database(args.cache_db)
    client = Client(args.request_delay, args.max_retries, args.timeout)
    checks, hits, interrupted = {}, 0, False
    try:
        database.record_index(index)
        # Load all cache hits first so an upstream block leaves useful reports.
        for uid in index.players:
            cached = database.cached(uid, args.cache_ttl_days, args.banned_ttl_days, args.refresh, args.refresh_banned)
            if cached:
                checks[uid] = cached
                hits += 1
        for uid, player in index.players.items():
            if uid in checks:
                continue
            result = client.check(uid, player.overall.aliases)
            database.save(player, result)
            checks[uid] = result
            log.info("Checkpoint %d/%d player=%s status=%s", len(checks), len(index.players), uid, result.status)
            if client.blocked:
                log.warning("Stopping live requests after upstream block/failure; restart later to resume")
                break
    except KeyboardInterrupt:
        interrupted = True
        log.warning("Interrupted; committed checks retained, exporting partial results")
    finally:
        client.close()
        database.close()
        summary = export(index, checks, args.output, hits, client.live_requests, interrupted)
        log.info("Final totals: %s", json.dumps(summary))
    return 130 if interrupted else (2 if summary["unknown_or_failed_checks"] or index.input_errors else 0)

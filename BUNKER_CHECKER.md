# Bulk HLL Bunker-ban checker

This standalone Python 3.11+ tool indexes exported Bifrost/CRCON matches, then
checks each distinct `player_id` once across all supplied servers. It does not
start Discord or apply bans. `steaminfo.has_bans` and `steaminfo.bans` are never
used to determine Bunker status.

## Run

The only external runtime dependency is `requests`, already in this repository's
`requirements.txt`. Use the existing virtual environment, or install `requests`
in a Python 3.11+ environment. Tests additionally require `pytest`.

```powershell
python bunker_checker.py --server "7DR Main" ./data/7dr-main --server "7DR Training" ./data/7dr-training --output ./output/bunker --log-file ./output/bunker/run.log
```

You can also pass a public Bifrost server URL directly:

```bash
python3 bunker_checker.py --server "TRR Events" "https://bifroststats.com/hll/leaderboards/servers/b73ceb717668" --output ./output/bunker
```

To download and index matches without making HLLRecords requests, append
`--download-only`. The importer reads the public `/crcon` paginated match listing,
then retrieves each full match's `result.player_stats`. Listing rows contain empty
player arrays and are never mistaken for complete player exports. Downloads are
saved under `data/bunker_matches/<server-id>/` (override with `--download-dir`).
Each file is written atomically and validated before reuse on a later run.
Listing pages are refreshed on restart so new matches can be discovered. Saved
exports remain available even if an older match disappears from the live listing.

Use `--source-delay` to change the default 1.5-second pause between Bifrost
requests. The timeout and retry options also apply to imports. Import errors or
interruptions stop the run before HLLRecords checks; rerun to reuse downloaded
matches. Source requests/download counts are logged separately from HLLRecords
request counts. In download-only reports, all players are UNKNOWN with blank
check timestamps; exit code 0 means importing/indexing succeeded, not that any
ban checks were performed.

Local sources are directories scanned recursively for `*.json` files (or single
JSON files). Supply one match per file:

```json
{
  "result": {
    "id": 123,
    "start": "2026-09-01T19:30:00Z",
    "player_stats": [
      {
        "player": "RSM Ginger",
        "player_id": "9e27c1492d85c016763e1462288c3725",
        "platform": "xbl"
      }
    ]
  }
}
```

Match identifiers accept `match_id`, `map_id`, or `id`, in that priority order,
from `result` first and then the outer object. Without an ID, a deterministic
content hash is used. Dates accept `start`, `start_time`, `started_at`,
`match_start`, `date`, or `time_start` in those same locations. ISO timestamps
and Unix seconds/milliseconds are normalized to UTC. Naive dates are interpreted
as UTC; missing/invalid dates remain blank. Names from dated appearances take
precedence over undated appearances; ties follow sorted file traversal order.

Repeat the same server name to combine directories for that server. Distinct
names keep histories separate. Duplicate match IDs within a server use the
first file encountered. IDs may overlap between servers. Invalid files/players
are logged and counted; valid inputs continue processing.

Local directories and recognized HTTPS server URLs on `bifroststats.com` and
`frostbite.bifrostgaming.com` are supported; repeat `--server` to mix sources.
No credentials are required for the public endpoints. The standalone importer
uses the same full-match URL format as `cogs/wardiary.py`, without importing the
Discord cog. Bifrost pagination and a full player export were verified against
server `b73ceb717668`; this does not resolve HLLRecords access restrictions.

## Outputs

All reports describe the inputs supplied to the current run:

| File | Contents |
| --- | --- |
| `all_players.csv` | One player/server row, aliases, platform, match count, date range, status, reason, check time, HTTP status and evidence |
| `flagged_players.csv` | Only confirmed BANNED player/server rows, with profile links |
| `cross_server_players.csv` | One row per shared player, all server names, global match count and ban status |
| `appearances.csv` | Individual player occurrences with server, match ID and start time |
| `summary.json` | Match, appearance, unique-player, per-server, ban, unresolved, request and cache totals |

Alias lists and server lists are JSON arrays inside CSV cells. Player names,
aliases and counts in player/server rows are specific to that server. Shared
player rows use the newest name across servers. Repeated player rows within a
match count as appearances, but `matches_seen` counts each match only once.
Banned totals count unique IDs, not CSV rows. Spreadsheet formula prefixes are
escaped using the repository's existing helper.

Reports are replaced atomically per file. An interrupted checking loop still
exports partial reports; unchecked players have `UNKNOWN` and no check time.
Exit code `0` means every indexed player received a definitive status and there
were no input errors; `2` means unresolved checks/input problems; `130` means
keyboard interruption. Preserve the run log for diagnostics.

## Cache and resuming

The default database is `data/bunker_checker.sqlite3`, respecting `BOT_DATA_DIR`.
Override it with `--cache-db PATH`. Keep the database between runs and rerun the
same command to resume. SQLite commits after every checked player. Match loading
is repeated on restart; successful fresh profile checks are reused.

| Option | Default | Meaning |
| --- | --- | --- |
| `--cache-ttl-days` | 7 | TTL for NOT_BANNED checks |
| `--banned-ttl-days` | 90 | TTL for BANNED checks |
| `--refresh` | off | Refresh non-banned results |
| `--refresh-banned` | off | Refresh banned results even within their TTL |
| `--request-delay` | 1.5 seconds | Minimum pause after each request, including retries |
| `--max-retries` | 4 | Additional attempts, capped at 10 |
| `--timeout` | 20 seconds | Requests connection/read timeout |

Use both refresh flags to refresh all successful results. A TTL of zero disables
reuse for that category. Failures, UNKNOWN, PARSE_ERROR, RATE_LIMITED and NOT_FOUND
are recorded but retried on the next run. A failed refresh remains unresolved;
the previous confirmed result is retained in the append-only `check_history`
table, not silently presented as a successful fresh check.

`checks` stores the latest result and player metadata. `matches` and `appearances`
retain imported histories across runs, even when subsequent reports use a smaller
input set. `check_history` retains check evidence and earlier outcomes.

Environment variables `HLLRECORDS_REQUEST_DELAY`, `HLLRECORDS_MAX_RETRIES` and
`HLLRECORDS_TIMEOUT` supply defaults; explicit CLI arguments take precedence.
Environment variables must be set in the calling shell (the tool does not load
the bot's `.env`).

## Detection and upstream availability

Statuses are `BANNED`, `NOT_BANNED`, `UNKNOWN`, `REQUEST_FAILED`, `RATE_LIMITED`,
`NOT_FOUND` and `PARSE_ERROR`. Internally `Check.bunker_banned` returns true, false,
or null for unresolved results. An HTTP failure never means NOT_BANNED.

The parser recognizes a known historical alias followed by `was Bunker banned`,
case-insensitively, with an optional reason of any category. It handles HTML
entities, nested JSON Unicode escapes, and adjacent serialized Next.js strings.
It does not assume undocumented structured fields represent Bunker status.

NOT_BANNED requires a complete HTML document containing the requested profile
URL, a matching player H1 heading, and at least two recognized statistics labels,
with no unresolved Bunker-ban indication. The explanatory sentence `Bunker Ban
flags are synced from Bunker.` alone is not a flag. Unrecognized explanatory text,
renamed players absent from the input aliases, partial pages, and changed layouts
may produce UNKNOWN. These conservative checks deliberately leave uncertain
profiles for review.

**Live verification limitation:** the supplied example profile returned HTTP 403
with a Bunny Shield challenge during implementation. No live positive or negative
profile template could be verified. The supplied known-positive phrase passes
offline tests for ID `9e27c1492d85c016763e1462288c3725`; HTML fixtures are synthetic,
not captured pages. Before relying on bulk coverage, review results against
ordinary accessible profiles. If the site continues challenging requests, this
tool will report the failure and stop; authorized site access or cooperation is
needed to complete live checks.

There is one HTTP request in flight. The client uses an honest identifying
User-Agent, exponential backoff, numeric/date Retry-After handling, and retries
429, temporary 500/502/503/504 responses and network failures. Exhausted retries,
401/403, redirects, and recognized access challenges stop further live requests
for that run. Responses are limited to 8 MiB. No authentication, CAPTCHA or
anti-bot bypass is attempted. Restart later to resume, preserving the database.

## Development

Components live in `bunker/`: `loader`, `models`, `parser`, `client`, `database`,
`reporting`, `source`, and `cli`. The entrypoint is `bunker_checker.py`. The bot and its
databases are unaffected.

```powershell
python -m pytest tests/test_bunker_checker.py tests/test_bunker_source.py -q
```

Tests use local synthetic fixtures and injected HTTP/clock objects. They never
contact HLLRecords. Add captured, permitted profile fixtures when available to
extend layout recognition without weakening the unresolved-status rules.

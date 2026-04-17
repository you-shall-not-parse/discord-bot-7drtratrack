# 7DR Hell Let Loose Clan Bot
What a mess...
Essentially this bot runs main.py and calls out cogs to do various functions around the discord.

## Liberation API Starter

This repo now includes a standalone Liberation stack under `liberationapp/` for a future Hell Let Loose campaign web app.

The backend polls CRCON `get_gamestate` and `get_recent_logs?filter_action=KILL`, stores per-map Allied and Axis kill totals in PostgreSQL, and can cache API responses in Redis.

Current container layout:

- `frontend` serves a minimal UI and proxies API calls to the backend
- `liberation-api` runs the Python poller and JSON API
- `postgres` stores persistent map totals, sessions, and processed events
- `redis` caches read-heavy API responses

Docker files:

- `liberationapp/Dockerfile.liberation`
- `liberationapp/docker-compose.liberation.yml`
- `liberationapp/requirements-liberation.txt`
- `liberationapp/liberation_servers.example.json`

Run locally:

```powershell
$env:DATABASE_URL="postgresql://liberation:liberation@localhost:5432/liberation"
$env:REDIS_URL="redis://localhost:6379/0"
$env:CRCON_PANEL_URL="https://7dr.hlladmin.com/api/"
$env:CRCON_API_KEY="your-token"
python liberationapp/liberation.py
```

Run with Docker:

```powershell
docker compose -f liberationapp/docker-compose.liberation.yml up --build
```

Useful endpoints:

- `GET /health`
- `GET /api/servers`
- `GET /api/maps`
- `GET /api/maps/Foy Warfare`

The compose stack exposes:

- Frontend UI: `http://localhost:8081`
- Backend API: `http://localhost:8080`

For multiple servers, set `LIBERATION_SERVERS_FILE` to a JSON file shaped like `liberationapp/liberation_servers.example.json`.

The backend now tracks map sessions in Postgres so a map change creates a new round/session boundary instead of continuing to attribute old log tails to the new map.
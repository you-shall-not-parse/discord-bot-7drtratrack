# 7DR Hell Let Loose Clan Bot

This bot runs from `main.py` and loads feature cogs from `cogs/`.

This README is the short summary version and is suitable for a single Discord devguide forum thread called `Ratbot Guide`.
For more detailed usage notes, see `COG_HOWTO.md`.

This repo also contains `liberationapp/`, which is a separate HLL campaign web app stack and not part of the cog how-to guide.

## Code Location

Repository:

- `https://github.com/you-shall-not-parse/discord-bot-7drtratrack`

## Background

The bot started as a single-purpose Python script for infantry trainee tracking.

Over time it was expanded into a modular bot that loads multiple cogs from `cogs/`, so new features can be added without turning `main.py` into one giant script.

The main public-safe idea is simple:

- the Discord application and bot user are created in the Discord Developer Portal
- the token is stored outside the repo, usually in `.env`
- `main.py` starts the bot and loads the feature cogs
- the cogs contain the real server features

No sensitive secrets should ever be kept in the public README or committed into the repository.

## File Structure

Current high-level layout:

```text
discord-bot-7drtratrack/
├── main.py
├── .env
├── README.md
├── COG_HOWTO.md
├── data_paths.py
├── config/
│   ├── common.py
│   ├── clannames.json
│   ├── presets.json
│   └── squadup_config.json
├── cogs/
│   ├── botadmin.py
│   ├── rosterizer.py
│   ├── outofoffice.py
│   ├── hellorleaderboard.py
│   ├── applyroletomessage.py
│   └── ...other cogs
├── data/
│   ├── scoreboard_font.ttf
│   ├── AlegreyaSC-Bold.ttf
│   ├── AlegreyaSC-Regular.ttf
│   └── ...runtime state files
└── liberationapp/
	└── separate campaign web app code
```

What each part is for:

- `main.py`: the entrypoint you run; loads the bot and its cogs
- `.env`: local secrets such as the bot token; not for public sharing
- `config/`: shared static config and common constants
- `cogs/`: modular Discord features
- `data/`: state files, logs, mappings, fonts, and generated bot data
- `README.md`: public-safe summary and structure overview
- `COG_HOWTO.md`: longer user/staff guide for each cog
- `liberationapp/`: a separate app in the same repo, not part of the Discord cog guide

## Hosting Model

The bot can be run locally for testing or hosted 24/7 on a VPS.

Typical setup:

- edit code in GitHub or locally
- pull updates to the server with `git pull`
- keep secrets in `.env`
- run the bot under a service manager such as `systemd`
- restart the service after pulling code changes

That keeps the bot process persistent without exposing secrets in the repository.

## Run

Start the bot with your normal Python environment after setting `DISCORD_BOT_TOKEN`.

```powershell
python main.py
```

## Logs

- Main bot log: `bot.log.txt`
- Hellor leaderboard log/state files: `data/hellor_leaderboard.log`, `data/hellor_leaderboard_state.json`, `data/hellor_t17_map.json`

## What The Bot Covers

- Admin utilities and controlled announcements
- Roster, signups, trainee tracking, and roll calls
- Event displays, content posting, greeting flows, and embeds
- HLL scoreboards and the `hellor.pro` leaderboard
- LOA, birthdays, certificates, and other clan support workflows

## Loaded Cogs

`main.py` currently loads the following:

- `botadmin`
- `rosterizer`
- `quick_exit`
- `bulkrole`
- `certify`
- `recruitform`
- `EmbedManager`
- `SquadUp`
- `eventscalendar`
- `BirthdayCog`
- `contentfeed`
- `discordgreeting`
- `echo`
- `mapvote`
- `HLLInfLeaderboard`
- `HLLArmLeaderboard`
- `gohamm`
- `GameMonCog`
- `multi_trainee_tracker`
- `rollcall`
- `nameshame`
- `outofoffice`
- `wardiary`
- `t17lookup`
- `applyroletomessage`
- `hellorleaderboard`

## Other Repo Content

- `liberationapp/`: separate web app stack for Liberation/campaign work
- `cogs/`: Discord bot features loaded by `main.py`
- `config/`: shared config files and shared constant definitions
- `data/`: bot state, logs, fonts, and generated files

## HLL Frontline Deployment

The `liberationapp/` stack is deployed separately from the Discord bot.

- frontend container binds to `127.0.0.1:8081` and is intended to sit behind Caddy or another reverse proxy
- liberation API binds to `127.0.0.1:8080` so the host machine and local bot can still reach it without exposing it publicly
- a production Caddy example and deployment notes live in `liberationapp/Caddyfile.production` and `liberationapp/DEPLOYMENT.md`

That keeps public traffic on `80/443` only while preserving host-local access to the app services.

*** Add File: c:\Users\Benja\OneDrive\Documents\discord-bot-7drtratrack\liberationapp\Caddyfile.production
www.hllfrontline.com {
	redir https://hllfrontline.com{uri} permanent
}

hllfrontline.com {
	encode zstd gzip

	log {
		output file /var/log/caddy/hllfrontline.access.log {
			roll_size 10MiB
			roll_keep 10
			roll_keep_for 720h
		}
		format console
	}

	reverse_proxy 127.0.0.1:8081
}

*** Add File: c:\Users\Benja\OneDrive\Documents\discord-bot-7drtratrack\liberationapp\DEPLOYMENT.md
# HLL Frontline Deployment

This stack is intended to run behind Caddy on a VPS.

## Compose Exposure

- `frontend` binds to `127.0.0.1:8081`
- `liberation-api` binds to `127.0.0.1:8080`

That keeps both services off the public internet while still allowing:

- Caddy on the host to proxy traffic into the frontend
- the host machine or bot process to call the API on `127.0.0.1:8080`

## Recommended Caddy Setup

Use `Caddyfile.production` as the starting point.

- `www.hllfrontline.com` redirects to `hllfrontline.com`
- `hllfrontline.com` reverse proxies to `127.0.0.1:8081`
- compression is enabled with `zstd` and `gzip`
- access logs are written to `/var/log/caddy/hllfrontline.access.log`

If `/var/log/caddy` does not exist, create it before reloading Caddy:

```bash
sudo mkdir -p /var/log/caddy
sudo chown caddy:caddy /var/log/caddy
```

Then copy the config into place:

```bash
sudo cp liberationapp/Caddyfile.production /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

## Cloudflare

Recommended DNS records:

- `A` record: `hllfrontline.com -> 130.162.174.77`
- `CNAME` record: `www -> hllfrontline.com`

Once HTTPS works through Caddy, Cloudflare can be switched from `DNS only` to `Proxied`.

Use `Full (strict)` in Cloudflare SSL/TLS mode.

## Deploy or Refresh

From `liberationapp/`:

```bash
docker compose -f docker-compose.liberation.yml up -d --build
```

## Verification

Check the host-local services:

```bash
curl http://127.0.0.1:8081
curl http://127.0.0.1:8080/health
```

Check the public domain:

```bash
curl -I http://hllfrontline.com
curl -I https://hllfrontline.com
curl -I https://www.hllfrontline.com
```

Expected result:

- HTTP redirects to HTTPS
- apex domain serves the app
- `www` redirects to the apex domain

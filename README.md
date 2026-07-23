# 7DR Hell Let Loose Clan Bot

This bot runs from `main.py` and loads feature cogs from `cogs/`.

This README is the short summary version and is suitable for a single Discord devguide forum thread called `Ratbot Guide`.
For more detailed usage notes, see `COG_HOWTO.md`.

This repo also contains the reverse-proxy configuration for the separately hosted historic-stats site.

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
├── state_io.py
├── requirements.txt
├── config/
│   ├── common.py
│   ├── clannames.json
│   ├── presets.json
│   └── squadup_config.json
├── cogs/
│   ├── quick_exit.py
│   ├── raid.py
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
	└── historic-stats proxy configuration
```

What each part is for:

- `main.py`: the entrypoint you run; loads the bot and its cogs
- `.env`: local secrets such as the bot token; not for public sharing
- `config/`: shared static config and common constants
- `cogs/`: modular Discord features
- `data/`: state files, logs, mappings, fonts, and generated bot data
- `README.md`: public-safe summary and structure overview
- `COG_HOWTO.md`: longer user/staff guide for each cog
- `liberationapp/`: Caddy configuration and deployment notes for the historic-stats subdomain

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

Use Python 3.14, install the pinned dependencies, and set `DISCORD_BOT_TOKEN`.

```powershell
python -m pip install -r requirements.txt
python main.py
```

The extension list in `main.py` is the source of truth. Extensions are loaded
independently, so a failed optional feature is logged without preventing healthy
features from starting. Slash commands are synchronized once during startup.

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
- `HLLInfLeaderboard`
- `HLLArmLeaderboard`
- `GameMonCog`
- `multi_trainee_tracker`
- `t17_role_index`
- `rollcall`
- `nameshame`
- `outofoffice`
- `wardiary`
- `t17lookup`
- `t17serveradmin`
- `applyroletomessage`
- `hellorleaderboard`
- `docsync`
- `supporters_embed`
- `raid`

Currently disabled in `main.py`:

- `rosterizer`
- `mapvote`

## Other Repo Content

- `liberationapp/`: reverse-proxy configuration for the historic-stats subdomain
- `cogs/`: Discord bot features loaded by `main.py`
- `config/`: shared config files and shared constant definitions
- `data/`: bot state, logs, fonts, and generated files
- `state_io.py`: shared atomic JSON persistence for runtime state
- `requirements.txt`: pinned Python dependencies

## Historic Stats Deployment

The former HLL Frontline/Liberation website has been retired. Only
`7drhistostats.hllfrontline.com` remains, proxied to its existing service on
`127.0.0.1:7010`. See `liberationapp/DEPLOYMENT.md` for the retained setup.

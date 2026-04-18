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

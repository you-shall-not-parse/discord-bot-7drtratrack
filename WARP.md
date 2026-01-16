# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Project Overview

This is a Discord bot for the 7DR Hell Let Loose clan, built with discord.py 2.3.2. The bot uses a cog-based architecture where each feature is implemented as a separate module in the `cogs/` directory. The bot tracks trainee progress, manages calendar events, creates squad signups, monitors game activity, and provides various administrative and community management functions.

## Running the Bot

### Setup
1. Install dependencies: `pip install -r packages.txt` (note: uses `packages.txt`, not `requirements.txt`)
2. Create a `.env` file with `DISCORD_BOT_TOKEN=your_token_here`
3. Ensure Python 3.8+ is installed

### Start the Bot
```powershell
python main.py
```

The bot will:
- Load all cogs from `main.py` (lines 77-97)
- Sync slash commands to Discord
- Start background tasks for calendar updates, game monitoring, and trainee tracking
- Log to both console and rotating log files (`bot.log.txt`, max 5MB per file, 3 backups)

### Bot Configuration
- Guild ID: `1097913605082579024` (hardcoded in multiple cogs)
- Owner ID: `1109147750932676649` (in `botadmin.py`)
- Command prefix: `!` (for legacy commands, though bot primarily uses slash commands)

## Architecture

### Cog Structure
Each cog is a self-contained module in `cogs/` that implements specific functionality:

**Core Admin:**
- `botadmin.py` - Owner-only commands: `/shutdown`, `/restart`, `/reload_cog`, `/git_pull`

**Community Management:**
- `SquadUp.py` - Squad signup system with persistent views, caching, and role-based management
- `CalendarCog.py` - Event calendar with recurring events, thread creation, and Europe/London timezone handling
- `GameMonCog.py` - Real-time "Now Playing" tracker with opt-in/opt-out preferences and inactivity cleanup

**Training & Development:**
- `trainee_tracker.py` - Automatic trainee tracking with embed generation and graduation monitoring
- `armour_trainee_tracker.py` - Similar tracker for armour trainees
- `recon_troop_tracker.py` - Recon-specific trainee tracking

**Leaderboards:**
- `HLLInfLeaderboard.py` - Hell Let Loose infantry leaderboard
- `HLLArmLeaderboard.py` - Hell Let Loose armour leaderboard

**Utilities:**
- `certify.py` - Certificate generation with custom fonts (Alegreya SC) and PIL image manipulation
- `bulkrole.py` - Bulk role management commands
- `echo.py` - Role-restricted message echo command (`/7drecho`)
- `rosterizer_1.py` - Roster generation
- `mapvote.py` - Map voting system
- `BirthdayCog.py` - Birthday tracking and notifications
- `contentfeed.py` - Content feed management
- `discordgreeting.py` - Welcome message system
- `recruitform.py` - Recruitment form handling
- `EmbedManager.py` - Embed message management
- `quick_exit.py` - Quick exit functionality
- `gohamm.py` - Custom functionality (context unclear)

### Disabled Cogs
The `cogs/offlinecogs/` directory contains disabled features:
- `OFFLINE-HLLStatsCog.py` - HLL stats tracking
- `OFFLINE-HLLRecLeaderboard.py` - Recon leaderboard
- `OFFLINE-GetBackDemon.py` - Demon-related functionality
- `OFFLINE-LoreCog.py` and `OFFLINE-LoreCogV2.py` - Lore management
- `OFFLINE-all_time_stat.py` - All-time statistics

These cogs are not loaded in `main.py` but may be re-enabled in the future.

### Data Storage
- JSON files in `data/` directory for persistent storage (e.g., `squadup_config.json`, `presets.json`)
- JSON files at root level for cog-specific state (e.g., `events.json`, `game_state.json`, `game_prefs.json`)
- SQLite database `cogs/quotes.db` (gitignored)
- Each cog manages its own data files independently

### Important Design Patterns

**Rate Limiting:**
Many cogs implement rate limiting for Discord API calls:
- `trainee_tracker.py` uses `send_rate_limited()` with 3-second delays
- `GameMonCog.py` has `EMBED_UPDATE_MIN_INTERVAL = 5` seconds
- `SquadUp.py` uses caching (`POST_CACHE`, `VIEW_CACHE`) with periodic saves every 60 seconds

**Persistent Views:**
Cogs like `SquadUp.py` and `GameMonCog.py` use persistent views with `timeout=None` and custom IDs to survive bot restarts.

**Background Tasks:**
- `CalendarCog.py` - Updates calendar every hour, creates threads 48 hours before events
- `GameMonCog.py` - Checks for inactive users every 60 minutes (removes after 12 hours)
- `trainee_tracker.py` - Monitors role changes via `on_member_update` event

**Configuration via Constants:**
Each cog defines configuration at the top (guild IDs, channel IDs, role IDs). To modify behavior, edit these constants in the relevant cog file.

## Development Workflow

### Reloading Cogs
Use `/reload_cog <cog_name>` to hot-reload a cog without restarting:
```
/reload_cog echo
/reload_cog CalendarCog
```
This also re-syncs slash commands and triggers special refresh logic for `CalendarCog` and `recruitform`.

### Updating Code from Git
Use `/git_pull` to update the bot code from the repository (runs `git pull --ff-only`).

### Adding a New Cog
1. Create a new file in `cogs/` directory (e.g., `cogs/mycog.py`)
2. Implement the cog with `commands.Cog` and `async def setup(bot)`
3. Add `await bot.load_extension("cogs.mycog")` to `main.py` in the `main()` function
4. Use `/reload_cog mycog` or restart the bot

### Slash Commands vs Prefix Commands
This bot primarily uses slash commands (`@app_commands.command`). Legacy prefix commands (`@commands.command`) are processed only in guild channels, not DMs (see `on_message` handler in `main.py`).

### Guild-Scoped Commands
Most cogs use guild-scoped commands for faster sync:
```python
@app_commands.guilds(discord.Object(id=GUILD_ID))
```
This allows immediate command updates without global sync delays.

## Common Issues

### Command Not Appearing
- Ensure the cog is loaded in `main.py`
- Check if command is guild-scoped and synced to the correct guild
- Use `/reload_cog` to re-sync commands
- Verify role permissions if command has `@app_commands.check` decorators

### Rate Limiting
- If seeing 429 errors, increase delays in rate-limited methods
- Check `EMBED_UPDATE_MIN_INTERVAL` in `GameMonCog.py`
- Review `SAVE_INTERVAL` in `SquadUp.py` (currently 60 seconds)

### Data Loss
- Ensure JSON files are not corrupted (cogs have error handling that resets to default)
- Check file permissions on Windows
- Verify `.gitignore` excludes sensitive data (`.env`, `*.log`, databases)

### Timezone Issues
- `CalendarCog.py` uses Europe/London timezone (`pytz.timezone("Europe/London")`)
- Store datetimes with `has_time` flag and `original_hour`/`original_minute` fields to avoid DST issues
- Use `.isoformat()` for JSON serialization

## Testing

This repository does not have automated tests. Test changes manually:
1. Use a test Discord server with the same role/channel structure
2. Update the guild ID constants in cogs to point to your test server
3. Test slash commands, background tasks, and event listeners
4. Monitor `bot.log.txt` for errors

## Dependencies

Core dependencies (from `packages.txt`):
- `discord.py==2.3.2` - Discord API wrapper
- `python-dotenv==1.0.1` - Environment variable management
- `pillow` - Image manipulation (for `certify.py`)
- `beautifulsoup4` - HTML parsing
- `requests` - HTTP requests
- `openai` - OpenAI API (if used)
- `apscheduler` - Task scheduling
- `aiofiles` - Async file I/O
- `pytz` - Timezone handling
- `aiosqlite` - Async SQLite (for quotes database)
- `aiohttp` - Async HTTP client
- `PyPDF2`, `pdf2image` - PDF processing

## File Structure

```
discord-bot-7drtratrack/
├── main.py                  # Entry point, loads all cogs
├── packages.txt             # Dependencies (pip install -r packages.txt)
├── .env                     # Bot token (gitignored)
├── .gitignore              # Standard Python gitignore
├── rcontest.py             # RCON connection test utility
├── README.md               # Basic project description
├── bot.log.txt             # Rotating log file (gitignored)
├── cogs/                   # All bot functionality
│   ├── botadmin.py         # Admin commands
│   ├── SquadUp.py          # Squad signups
│   ├── CalendarCog.py      # Event calendar
│   ├── GameMonCog.py       # Game activity monitoring
│   ├── trainee_tracker.py  # Infantry trainee tracking
│   ├── certify.py          # Certificate generation
│   ├── [other cogs...]
│   ├── offlinecogs/        # Disabled features
│   ├── birthdaygifs/       # Birthday GIF assets
│   ├── demongifs/          # Demon GIF assets
│   ├── gohammfiles/        # Assets for gohamm cog
│   ├── certificate_template.png  # Certificate template
│   ├── AlegreyaSC-*.ttf    # Fonts for certificates
└── data/                   # JSON configuration files
    ├── squadup_config.json
    └── presets.json
```

## RCON Utility

`rcontest.py` is a standalone script for testing RCON connections to Hell Let Loose servers:
- Connects to `176.57.171.44:28016`
- Authenticates with password prompt
- Sends "Get Map" command to retrieve current map
- Uses Source RCON protocol (struct-based packet encoding)

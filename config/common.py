from __future__ import annotations

from pathlib import Path

from data_paths import data_path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"

# Shared Discord scope used across the bot unless a cog explicitly targets somewhere else.
MAIN_GUILD_ID = 1097913605082579024

# Shared external service endpoints.
CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"

# Shared log locations.
BOT_LOG_PATH = str(PROJECT_ROOT / "bot.log.txt")
WEB_LOG_PATH = str(PROJECT_ROOT / "bot_web.log")

# Shared static/config assets.
CLAN_NAMES_PATH = str(CONFIG_DIR / "clannames.json")
PRESETS_PATH = str(CONFIG_DIR / "presets.json")
SQUADUP_CONFIG_PATH = str(CONFIG_DIR / "squadup_config.json")
CERTIFICATE_BOLD_FONT_PATH = data_path("AlegreyaSC-Bold.ttf")
CERTIFICATE_REGULAR_FONT_PATH = data_path("AlegreyaSC-Regular.ttf")
SCOREBOARD_FONT_PATH = data_path("scoreboard_font.ttf")


def data_log_path(filename: str) -> str:
    return data_path(filename)

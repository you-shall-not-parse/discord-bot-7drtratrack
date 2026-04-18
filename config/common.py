from __future__ import annotations

from pathlib import Path

from data_paths import data_path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"

# Shared Discord scope used across the bot unless a cog explicitly targets somewhere else.
MAIN_GUILD_ID = 1097913605082579024

# Shared forum/channel destinations that are reused by multiple maintenance features.
DOCS_FORUM_CHANNEL_ID = 1388644379211862096
DOCS_FORUM_TAG_NAME = "Guide"

# Shared external service endpoints.
CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"

# Shared log locations.
BOT_LOG_PATH = str(PROJECT_ROOT / "bot.log.txt")

# Shared static/config assets.
CLAN_NAMES_PATH = str(CONFIG_DIR / "clannames.json")
PRESETS_PATH = str(CONFIG_DIR / "presets.json")
SQUADUP_CONFIG_PATH = str(CONFIG_DIR / "squadup_config.json")
CERTIFICATE_BOLD_FONT_PATH = data_path("AlegreyaSC-Bold.ttf")
CERTIFICATE_REGULAR_FONT_PATH = data_path("AlegreyaSC-Regular.ttf")
SCOREBOARD_FONT_PATH = data_path("scoreboard_font.ttf")


def data_log_path(filename: str) -> str:
    return data_path(filename)

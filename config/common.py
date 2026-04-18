from __future__ import annotations

from pathlib import Path

from data_paths import data_path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Shared Discord scope used across the bot unless a cog explicitly targets somewhere else.
MAIN_GUILD_ID = 1097913605082579024

# Shared forum/channel destinations that are reused by multiple maintenance features.
DOCS_FORUM_CHANNEL_ID = 1388644379211862096

# Shared external service endpoints.
CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"

# Shared log locations.
BOT_LOG_PATH = str(PROJECT_ROOT / "bot.log.txt")


def data_log_path(filename: str) -> str:
    return data_path(filename)

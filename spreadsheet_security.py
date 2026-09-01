from __future__ import annotations

from typing import Any


SPREADSHEET_FORMULA_PREFIXES = ("=", "+", "-", "@")


def safe_spreadsheet_value(value: Any) -> Any:
    """Return user-controlled text in a form spreadsheet apps will not execute."""
    if isinstance(value, str) and value.startswith(SPREADSHEET_FORMULA_PREFIXES):
        return f"'{value}"
    return value

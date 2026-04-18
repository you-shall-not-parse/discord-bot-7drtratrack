from __future__ import annotations

import os
from pathlib import Path
from typing import Union


def _default_project_root() -> Path:
    # This file lives in the project root alongside main.py.
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    """Return the absolute path to the project's data directory.

    You can override it by setting BOT_DATA_DIR to an absolute or relative path.
    """

    override = os.getenv("BOT_DATA_DIR")
    if override:
        path = Path(override)
        if not path.is_absolute():
            path = _default_project_root() / path
        return path

    return _default_project_root() / "data"


def data_path(*parts: Union[str, os.PathLike[str]], ensure_dir: bool = True) -> str:
    """Build an absolute path under data/.

    Returns a string path for maximum compatibility with existing code.
    """

    base = data_dir()
    full = base
    for part in parts:
        full = full / Path(part)

    if ensure_dir:
        full.parent.mkdir(parents=True, exist_ok=True)

    return str(full)

"""%LOCALAPPDATA%\\Shiftdial\\settings.json: {"theme": "system" | "light" | "dark"}."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from shiftdial.utils.paths import data_dir

THEMES = ("system", "light", "dark")
DEFAULTS: dict[str, Any] = {"theme": "system"}


def path() -> Path:
    return data_dir() / "settings.json"


def load(file: Path | None = None) -> dict[str, Any]:
    """Settings merged over defaults. A missing, corrupt or hand-edited bad file falls back to defaults."""
    try:
        raw = json.loads((file or path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    s = dict(DEFAULTS)
    if isinstance(raw, dict) and raw.get("theme") in THEMES:
        s["theme"] = raw["theme"]
    return s


def save(s: dict[str, Any], file: Path | None = None) -> None:
    """Write via a temp file + replace, so a crash mid-write can't leave half a file."""
    target = file or path()
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(s, indent=2), encoding="utf-8")
    os.replace(tmp, target)

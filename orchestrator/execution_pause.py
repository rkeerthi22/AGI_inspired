"""Repository-owned fail-safe check for Hermes' global ESTOP sentinel."""
from __future__ import annotations

import os
from pathlib import Path


def estop_path() -> Path:
    configured = os.environ.get("HERMES_HOME", "").strip()
    if configured:
        return Path(configured) / "ESTOP"
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        return (Path(base) if base else Path.home() / "AppData" / "Local") / "hermes" / "ESTOP"
    return Path.home() / ".hermes" / "ESTOP"


def pause_engaged() -> bool:
    """Return True when paused; fail closed if sentinel state is unreadable."""
    try:
        return estop_path().exists()
    except OSError:
        return True

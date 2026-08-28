"""Repository-owned fail-safe check for Hermes' global ESTOP sentinel."""
from __future__ import annotations

import os
from pathlib import Path


def estop_path() -> Path:
    configured = os.environ.get("HERMES_HOME", "").strip()
    if configured:
        home = Path(configured).expanduser()
        if not home.is_absolute():
            raise ValueError("HERMES_HOME must be an absolute path")
        return home / "ESTOP"
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        return (Path(base) if base else Path.home() / "AppData" / "Local") / "hermes" / "ESTOP"
    return Path.home() / ".hermes" / "ESTOP"


def pause_engaged() -> bool:
    """Return True when paused; only a validated home with no sentinel resumes."""
    try:
        sentinel = estop_path()
        try:
            sentinel.stat()
            return True
        except FileNotFoundError:
            pass

        home = sentinel.parent
        if not home.is_dir():
            return True
        # An explicit override is easy to typo into an existing unrelated directory.
        # Require a stable Hermes-home marker before interpreting a missing ESTOP as resume.
        if os.environ.get("HERMES_HOME", "").strip():
            if not ((home / "config.yaml").is_file() or (home / "hermes-agent").is_dir()):
                return True
        return False
    except (OSError, ValueError, RuntimeError):
        return True

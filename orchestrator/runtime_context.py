"""orchestrator/runtime_context.py — shared run context and logging (Move 5a).

This module owns the single logger and the small set of path constants that
every orchestrator module needs. It deliberately imports nothing else in the
repo so it can sit at the bottom of the dependency graph:

    runtime_context  →  integrity, execution, prompts, evaluation, scheduler

The logger writes to stdout and, once a run has opened a log file, to that
file. Modules share the *same* function object, so a monkey-patch in tests
or a log-file assignment in `batch_runner.main()` is visible everywhere.
"""
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
MISSIONS = ROOT / "missions"
ESCALATIONS = ROOT / "workspace" / "ESCALATIONS.md"

_log_file = None


def log(msg: str) -> None:
    """Single shared logger. Mirrors the original `batch_runner.log`."""
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if _log_file:
        with open(_log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def set_log_file(path) -> None:
    """Set (or clear) the active run-scoped log file. Run-local by convention."""
    global _log_file
    _log_file = path


def get_log_file():
    """Return the currently active log file path, or None."""
    return _log_file

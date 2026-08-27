"""orchestrator/runtime_context.py — shared run context and logging (Move 5a).

This module owns the single logger and the small set of path constants that
every orchestrator module needs. It deliberately imports nothing else in the
repo so it can sit at the bottom of the dependency graph:

    runtime_context  →  integrity, execution, prompts, evaluation, scheduler

The logger is a *proxy*: `log(msg)` looks up `_logger` at call time and
delegates to it. The default `_logger` writes a `[HH:MM:SS]` line to stdout
and, when a run has opened a log file, appends the same line to it. Tests
that want to silence or capture every orchestrator log line patch `_logger`
directly (see `tests/_silence.py` for the helper).

The proxy is the answer to the F56 defect: when each orchestrator module
imported `log` from `runtime_context` directly, patching `br.log = lambda`
only silenced `batch_runner`'s own `log` reference. `integrity.log` and
`execution.log` had already captured the same function object -- so the patch
worked at the import level -- but anything that called `log` *through*
`runtime_context` (e.g. a re-export) would have re-bound the proxy and the
patch would silently stop working. The proxy keeps the routing in one place
so a single patch on `_logger` is visible from every call site.
"""
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
MISSIONS = ROOT / "missions"
ESCALATIONS = ROOT / "workspace" / "ESCALATIONS.md"

_log_file = None


def _real_log(msg: str) -> None:
    """Default logger: stdout + active run log file (if one is open)."""
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if _log_file:
        with open(_log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")


# `_logger` is the indirection point. Every orchestrator module reaches the
# real logger via the `log` proxy below, which calls `_logger` at call time
# so monkey-patching `_logger` is visible everywhere.
_logger = _real_log


def log(msg: str) -> None:
    """Single shared logger. Calls `_logger(msg)` at every invocation so a
    monkey-patch on `_logger` is observed by every module."""
    _logger(msg)


def set_log_file(path) -> None:
    """Set (or clear) the active run-scoped log file. Run-local by convention."""
    global _log_file
    _log_file = path


def get_log_file():
    """Return the currently active log file path, or None."""
    return _log_file

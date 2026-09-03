"""Best-effort structured observability for runtime and diagnostic events."""
import json
import os
from pathlib import Path

from timebase import utc_iso

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "runs" / "health_events.jsonl"


def _write_event(event):
    path = Path(os.environ.get("AGI_HEALTH_EVENTS_PATH", str(DEFAULT_PATH)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True, ensure_ascii=True) + "\n")


def record(subsystem, operation, *, severity="info", outcome="observed", **context):
    try:
        event = {"schema": "agi.health_event.v1", "timestamp": utc_iso(),
                 "severity": severity, "outcome": outcome,
                 "subsystem": subsystem, "operation": operation}
        event.update({k: v for k, v in context.items() if v is not None})
        _write_event(event)
        return True
    except Exception:
        return False


def emit(subsystem, operation, error, **context):
    try:
        event = {"schema": "agi.health_event.v1", "timestamp": utc_iso(),
                 "severity": "warning", "outcome": "fail_soft",
                 "subsystem": subsystem, "operation": operation,
                 "error_type": type(error).__name__, "error": str(error)[:1000]}
        event.update({k: v for k, v in context.items() if v is not None})
        _write_event(event)
        return True
    except Exception:
        return False


def last_provider_canary(provider: str) -> dict | None:
    """Most recent connectivity_canary health event for ``provider``, or None.

    The BytePlus canary (workspace/validation/byteplus_connectivity_canary.py)
    records a ``provider``/``connectivity_canary`` event on every run:
    ``record(..., ok=True, ...)`` on success, ``emit(..., error_category=...,
    retryable=...)`` on a ProviderChatError. This reads that record WITHOUT
    making any live call, so a caller can decide whether to open a window
    against a known-quota-blocked provider instead of discovering the 429
    mid-run (Q2#5; M6 / task 117 died this way).

    Returns the raw event dict (caller inspects ``ok``, ``outcome``,
    ``error_category``, ``timestamp``). ``ok is True`` distinguishes success
    from failure: the success path sets ``ok=True``; the failure path (``emit``)
    does not set ``ok`` at all."""
    path = Path(os.environ.get("AGI_HEALTH_EVENTS_PATH", str(DEFAULT_PATH)))
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (ev.get("subsystem") == "provider"
                and ev.get("operation") == "connectivity_canary"
                and ev.get("provider") == provider):
            return ev
    return None

"""Best-effort structured observability for deliberately fail-soft subsystems."""
import json
import os
from pathlib import Path

from timebase import utc_iso

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "runs" / "health_events.jsonl"


def emit(subsystem, operation, error, **context):
    try:
        path = Path(os.environ.get("AGI_HEALTH_EVENTS_PATH", str(DEFAULT_PATH)))
        path.parent.mkdir(parents=True, exist_ok=True)
        event = {"schema": "agi.health_event.v1", "timestamp": utc_iso(),
                 "severity": "warning", "outcome": "fail_soft",
                 "subsystem": subsystem, "operation": operation,
                 "error_type": type(error).__name__, "error": str(error)[:1000]}
        event.update({k: v for k, v in context.items() if v is not None})
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, ensure_ascii=True) + "\n")
        return True
    except Exception:
        return False

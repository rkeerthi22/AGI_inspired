"""UTC persistence and structured fail-soft health-event checks."""
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "orchestrator"))
from timebase import utc_iso
import health_events

bad = []
def check(name, value):
    print(("PASS" if value else "FAIL") + ": " + name)
    if not value: bad.append(name)

stamp = utc_iso()
check("canonical UTC RFC3339", bool(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", stamp)))
check("aware UTC", datetime.fromisoformat(stamp.replace("Z", "+00:00")).tzinfo == timezone.utc)
with tempfile.TemporaryDirectory() as td:
    target = Path(td) / "health.jsonl"
    old = os.environ.get("AGI_HEALTH_EVENTS_PATH")
    os.environ["AGI_HEALTH_EVENTS_PATH"] = str(target)
    try:
        ok = health_events.emit("prediction", "before_task_runs", RuntimeError("boom"), task_id=7)
        event = json.loads(target.read_text(encoding="utf-8"))
        check("append succeeds", ok)
        check("stable schema", event["schema"] == "agi.health_event.v1")
        check("operation identified", event["subsystem"] == "prediction" and event["operation"] == "before_task_runs")
        check("context preserved", event["task_id"] == 7)
        check("event UTC", event["timestamp"].endswith("Z"))
    finally:
        if old is None: os.environ.pop("AGI_HEALTH_EVENTS_PATH", None)
        else: os.environ["AGI_HEALTH_EVENTS_PATH"] = old
raise SystemExit(1 if bad else 0)

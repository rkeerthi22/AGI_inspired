"""F105: preflight-style BytePlus quota probe (weak-AI efficiency).

Q2#5's original framing ("BytePlus quota probe in preflight()") did not survive
investigation: the cohort (where M6/task 117 died) calls batch_runner.run_task()
DIRECTLY and bypasses integrity.preflight() entirely, and normal batches use
ollama-cloud (not BytePlus) as the worker. The correct realization is a
no-live-call BytePlus-canary health check at cohort entry (run_cohort), reading
the last recorded connectivity_canary event. This test pins the load-bearing
piece -- health_events.last_provider_canary -- which reads runs/health_events.jsonl
without making any provider call.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import health_events  # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got={got!r} want={want!r}")


def _ev(timestamp, *, ok=None, provider="byteplus_coding",
        operation="connectivity_canary", subsystem="provider",
        error_category=None, retryable=None):
    """Build a health event line in the same shape record()/emit() produce."""
    ev = {"schema": "agi.health_event.v1", "timestamp": timestamp,
          "subsystem": subsystem, "operation": operation,
          "provider": provider}
    if ok is True:
        ev["severity"] = "info"
        ev["outcome"] = "observed"
        ev["ok"] = True
    else:
        ev["severity"] = "warning"
        ev["outcome"] = "fail_soft"
        ev["error_type"] = "ProviderChatError"
        ev["error"] = "HTTP 429"
        if error_category is not None:
            ev["error_category"] = error_category
        if retryable is not None:
            ev["retryable"] = retryable
    return json.dumps(ev, sort_keys=True)


tmp = Path(tempfile.mkdtemp(prefix="f105_"))
events_path = tmp / "health_events.jsonl"
old_env = os.environ.get("AGI_HEALTH_EVENTS_PATH")
os.environ["AGI_HEALTH_EVENTS_PATH"] = str(events_path)
try:
    # 1. No file at all -> None (caller warns "unverified").
    check("missing file -> None", health_events.last_provider_canary("byteplus_coding"), None)

    # 2. Empty file -> None.
    events_path.write_text("", encoding="utf-8")
    check("empty file -> None", health_events.last_provider_canary("byteplus_coding"), None)

    # 3. A single success event -> returned, ok True.
    events_path.write_text(_ev("2026-09-03T01:53:09Z", ok=True) + "\n", encoding="utf-8")
    ev = health_events.last_provider_canary("byteplus_coding")
    check("success event found", ev is not None, True)
    check("success ok flag", ev.get("ok") is True, True)
    check("success timestamp", ev.get("timestamp"), "2026-09-03T01:53:09Z")

    # 4. A single failure event -> returned, ok not True.
    events_path.write_text(
        _ev("2026-09-03T01:53:09Z", error_category="rate_limit", retryable=True) + "\n",
        encoding="utf-8")
    ev = health_events.last_provider_canary("byteplus_coding")
    check("failure event found", ev is not None, True)
    check("failure ok absent (not True)", ev.get("ok") is True, False)
    check("failure error_category", ev.get("error_category"), "rate_limit")
    check("failure retryable", ev.get("retryable"), True)

    # 5. Multiple events -> LATEST (last line) wins, not the first.
    events_path.write_text(
        _ev("2026-09-03T01:00:00Z", ok=True) + "\n"
        + _ev("2026-09-03T12:00:00Z", error_category="rate_limit") + "\n"
        + _ev("2026-09-03T18:00:00Z", ok=True) + "\n", encoding="utf-8")
    ev = health_events.last_provider_canary("byteplus_coding")
    check("latest of three wins", ev.get("timestamp"), "2026-09-03T18:00:00Z")
    check("latest is the success", ev.get("ok") is True, True)

    # 6. Other-provider / other-operation events are ignored.
    other = json.dumps({"schema": "agi.health_event.v1",
                        "timestamp": "2026-09-03T20:00:00Z",
                        "subsystem": "provider",
                        "operation": "connectivity_canary",
                        "provider": "anthropic", "ok": True}, sort_keys=True)
    notcanary = json.dumps({"schema": "agi.health_event.v1",
                            "timestamp": "2026-09-03T20:00:00Z",
                            "subsystem": "prediction", "operation": "before_task_runs",
                            "provider": "byteplus_coding"}, sort_keys=True)
    events_path.write_text(_ev("2026-09-03T09:00:00Z", ok=True) + "\n"
                           + other + "\n" + notcanary + "\n", encoding="utf-8")
    ev = health_events.last_provider_canary("byteplus_coding")
    check("ignores other provider + other operation",
          ev.get("timestamp"), "2026-09-03T09:00:00Z")

    # 7. Malformed JSON line is skipped, not fatal.
    events_path.write_text("not json\n" + _ev("2026-09-03T09:00:00Z", ok=True) + "\n",
                           encoding="utf-8")
    ev = health_events.last_provider_canary("byteplus_coding")
    check("malformed line skipped", ev is not None, True)
    check("malformed line -> next valid", ev.get("ok") is True, True)
finally:
    if old_env is None:
        os.environ.pop("AGI_HEALTH_EVENTS_PATH", None)
    else:
        os.environ["AGI_HEALTH_EVENTS_PATH"] = old_env

if fails:
    print("\nFAILURES:", *fails, sep="\n  - ")
    raise SystemExit(1)
print("\nF105 PASS — BytePlus canary health query reads recent state with no live call")

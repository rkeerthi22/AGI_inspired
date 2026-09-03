"""F108: test health events must not pollute the production runs/health_events.jsonl.

Before F108, unit-tier tests that exercise the estop / mailbus / hive_quiesce /
prediction subsystems wrote their health events to the DEFAULT production path
(runs/health_events.jsonl), because tests/run_all.py's _guarded_env did not
redirect AGI_HEALTH_EVENTS_PATH. `agi status` shows the NEWEST event per
subsystem, so test artifacts (estop tamper_recovery, mailbus
intercept_execution_command, hive_quiesce tree_taint) appeared as live
"recorded subsystem warnings" -- crying wolf on every status read. Measured
2026-09-03: running test_estop_tamper once added 4 estop/tamper_recovery events
to the production log; the log held 2197 events, the majority test artifacts.

FIX: _guarded_env sets AGI_HEALTH_EVENTS_PATH to a pid-scoped temp path for the
unit/containment/integration tiers, so test runs write to temp, not the repo's
runs/. Containment was already isolated (disposable repo) but is covered for
consistency. The live tier is intentionally un-redirected (real runs). Production
never sets the env var and continues to use runs/health_events.jsonl. operator_cli
status reads RUNS/health_events.jsonl directly (not via the env), so production
reads are unaffected.

This test pins: (1) _guarded_env redirects the path away from production for the
three model-free tiers; (2) the live tier does not inject a redirect; (3)
health_events.record/emit honor the env path; and (4) the production log is not
touched while the env path is set.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import run_all  # noqa: E402  -- defines _guarded_env; __main__ guard keeps import side-effect-free
import health_events  # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got!r} want={want!r}")


PROD = ROOT / "runs" / "health_events.jsonl"

print("=== 1. _guarded_env redirects health events away from production ===")
for tier in ("unit", "containment", "integration"):
    env = run_all._guarded_env(tier)
    p = env.get("AGI_HEALTH_EVENTS_PATH")
    check(f"{tier}: AGI_HEALTH_EVENTS_PATH is set", bool(p), True)
    check(f"{tier}: redirected path is NOT the production log", p != str(PROD), True)
    check(f"{tier}: redirected path is outside the repo",
          not Path(p).is_relative_to(ROOT), True)

saved_for_live = os.environ.pop("AGI_HEALTH_EVENTS_PATH", None)
try:
    live_env = run_all._guarded_env("live")
    check("live: runner does not inject AGI_HEALTH_EVENTS_PATH",
          "AGI_HEALTH_EVENTS_PATH" in live_env, False)
    os.environ["AGI_HEALTH_EVENTS_PATH"] = "C:/operator-configured-health.jsonl"
    live_env = run_all._guarded_env("live")
    check("live: explicit operator redirect is preserved",
          live_env.get("AGI_HEALTH_EVENTS_PATH"),
          "C:/operator-configured-health.jsonl")
finally:
    if saved_for_live is None:
        os.environ.pop("AGI_HEALTH_EVENTS_PATH", None)
    else:
        os.environ["AGI_HEALTH_EVENTS_PATH"] = saved_for_live

print("\n=== 2. record/emit honor the env path; production log untouched ===")
tmp = Path(tempfile.mkdtemp()) / "f108_health.jsonl"
prod_before = sum(1 for _ in PROD.open(encoding="utf-8")) if PROD.is_file() else 0
saved = os.environ.get("AGI_HEALTH_EVENTS_PATH")
os.environ["AGI_HEALTH_EVENTS_PATH"] = str(tmp)
try:
    ok = health_events.record("f108sub", "f108op", marker="alpha")
    check("record() returns True under redirect", ok, True)
    check("event landed in the redirected temp file",
          tmp.is_file() and json.loads(tmp.read_text(encoding="utf-8").strip()).get("marker") == "alpha",
          True)
    health_events.emit("f108sub", "f108emitop", ValueError("boom"), marker="beta")
    lines = tmp.read_text(encoding="utf-8").splitlines()
    check("emit() also wrote to the redirected file (2 events now)", len(lines), 2)
    check("emit event carries the error text",
          any("boom" in json.loads(l).get("error", "") for l in lines), True)
    prod_after = sum(1 for _ in PROD.open(encoding="utf-8")) if PROD.is_file() else 0
    check("production log did not grow while redirected", prod_after, prod_before)
finally:
    if saved is None:
        os.environ.pop("AGI_HEALTH_EVENTS_PATH", None)
    else:
        os.environ["AGI_HEALTH_EVENTS_PATH"] = saved

print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + str(fails)}")
sys.exit(1 if fails else 0)

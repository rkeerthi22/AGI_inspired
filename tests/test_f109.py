"""F109: test_f57 must not write per-task critic artifacts to the production runs/.

Before F109, test_f57's §3a-§3h called run_critic with the REAL ev.RUNS/rc.RUNS --
temp_root_with_ledgerbook()'s scope had ended at §2k's cleanup (line 387), and §3
starts at line 389. So every task{N}_critic.usage.json / _citation_evidence.json
(task1 from §3a-3c, task11..15/99 from §3d-3h) was written into the repo's runs/
dir on every gate run. run_critic writes via evaluation's module-global RUNS
(= ev.RUNS); F109 redirects ev.RUNS/rc.RUNS to a temp for all of §3.

This pin runs test_f57 as a subprocess and asserts the production runs/ directory's
file set + mtimes are UNCHANGED by it. If a future edit removes or narrows the
redirect (e.g. only covers §3d-3h, the original mistake), test_f57 writes task1
and/or task11..15/99 artifacts to runs/ and this suite FAILS.

Verified hermetic 2026-09-03: new/gone/changed all empty across a lone test_f57 run.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
sys.path.insert(0, str(ROOT / "tests"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got!r} want={want!r}")


def snapshot():
    """name -> mtime_ns for every *.json currently in the production runs/ dir."""
    if not RUNS.is_dir():
        return {}
    return {p.name: p.stat().st_mtime_ns for p in RUNS.glob("*.json")}


print("=== F109: test_f57 leaves the production runs/ dir untouched ===")
before = snapshot()

# Mirror run_all._guarded_env("unit") so the subprocess behaves exactly as it does
# in the gate: model-free, health events routed to a pid-scoped temp (F108).
env = dict(os.environ)
env["AGI_TEST_TIER"] = "unit"
env["AGI_LIVE_EXECUTION_ALLOWED"] = "0"
env["AGI_HEALTH_EVENTS_PATH"] = str(
    Path(tempfile.gettempdir()) / f"agi_test_health_events_{os.getpid()}.jsonl")

proc = subprocess.run(
    [sys.executable, str(ROOT / "tests" / "test_f57.py")],
    cwd=str(ROOT), capture_output=True, text=True,
    encoding="utf-8", errors="replace", env=env,
)
check("test_f57 subprocess exited 0", proc.returncode, 0)

after = snapshot()
new = {k: v for k, v in after.items() if k not in before}
gone = {k: v for k, v in before.items() if k not in after}
chg = {k: (before[k], after[k]) for k in before if k in after and before[k] != after[k]}
check("runs/ gained no new files from test_f57", new, {})
check("runs/ lost no files to test_f57", gone, {})
check("runs/ file mtimes unchanged by test_f57", chg, {})
if new or gone or chg:
    print(f"         new={sorted(new)} gone={sorted(gone)} changed={sorted(chg)}")

print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + str(fails)}")
sys.exit(1 if fails else 0)

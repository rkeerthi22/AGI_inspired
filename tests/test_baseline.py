"""F34: _current_canary_green() must distinguish 'ran, none passed' from 'never ran'."""
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import ledger  # noqa: E402
import promote  # noqa: E402

tmp = Path(tempfile.mkdtemp()) / "ledger.db"
shutil.copy2(ROOT / "ledger" / "ledger.db", tmp)
ledger.LEDGER_DB = tmp
promote.ledger.LEDGER_DB = tmp
WK = datetime.now().strftime("%Y-W%V")
fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got} want={want}")


def reset():
    # Clear synthetic rows AND the current week's real canaries (in the COPY only), so each
    # scenario below starts from a known state. Originally only dropped task_id > 9000, which
    # was fine while the current week had no canaries -- running them for real on 2026-07-29
    # made every "current week" assertion count 3 live passes on top of the synthetic ones.
    with sqlite3.connect(tmp) as c:
        c.execute("DELETE FROM tasks WHERE mission_id='canaries' AND "
                  "(task_id > 9000 OR spec LIKE ?)", (f"[{WK}]%",))


def add(week, n_pass, n_fail=0, status="done", base=9001):
    with sqlite3.connect(tmp) as c:
        i = base
        for _ in range(n_pass):
            c.execute("INSERT INTO tasks (task_id,mission_id,spec,status,critic_verdict,"
                      "pass_criteria,created_at) VALUES (?,?,?,?, 'pass','x',datetime('now'))",
                      (i, "canaries", f"[{week}] C{i}", status)); i += 1
        for _ in range(n_fail):
            c.execute("INSERT INTO tasks (task_id,mission_id,spec,status,critic_verdict,"
                      "pass_criteria,created_at) VALUES (?,?,?,'failed','fail','x',datetime('now'))",
                      (i, "canaries", f"[{week}] C{i}")); i += 1


print("=== live ledger as it stands ===")
print(f"  current week ({WK}) canaries were run for real on 2026-07-29, so the CURRENT")
print("  week is authoritative and the W29 fallback is correctly not used")
check("uses the current week once it has real canary data",
      promote._current_canary_green(), (3, WK))
print("\n=== with the current week cleared, the W29 fallback engages ===")
reset()
check("falls back to W29's real green count, not 0",
      promote._current_canary_green(), (3, "2026-W29"))

print("\n=== current week HAS canaries: use it, never the fallback ===")
reset(); add(WK, n_pass=5)
check("current week with 5 green wins", promote._current_canary_green(), (5, WK))

print("\n=== the case the old code could not express: ran, NONE passed ===")
reset(); add(WK, n_pass=0, n_fail=5)
check("a real 0 is reported as 0 for THIS week (not a stale fallback)",
      promote._current_canary_green(), (0, WK))

print("\n=== parked/stale weeks must not masquerade as a real observation ===")
reset(); add("2026-W30", n_pass=4, status="stale")
check("stale-only week is ignored; still falls back to W29",
      promote._current_canary_green(), (3, "2026-W29"))

print("\n=== picks the MOST RECENT real week, not the first ===")
reset(); add("2026-W30", n_pass=1, base=9101)
check("W30 (1 green) beats older W29 (3 green)",
      promote._current_canary_green(), (1, "2026-W30"))

print("\n=== no canary data at all ===")
with sqlite3.connect(tmp) as c:
    c.execute("DELETE FROM tasks WHERE mission_id='canaries'")
check("empty history returns 0/none", promote._current_canary_green(), (0, "none"))

print("\n=== rollback actually arms with a nonzero baseline ===")
print(f"  week_green=2 vs baseline=3 -> should roll back: "
      f"{2 < 3}   (with the old baseline 0: {2 < 0})")

print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + str(fails)}")
sys.exit(1 if fails else 0)

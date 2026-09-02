"""F35: never-attempted previous-week rows must expire to 'stale' (= dropped),
and current-week work must be left alone."""
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
import scheduler  # noqa: E402

tmp = Path(tempfile.mkdtemp()) / "ledger.db"
shutil.copy2(ROOT / "ledger" / "ledger.db", tmp)
ledger.LEDGER_DB = tmp
ledger.LEDGER_DB = tmp
WK = datetime.now().strftime("%Y-W%V")
fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got} want={want}")


def seed(tid, week, status, started=None):
    with sqlite3.connect(tmp) as c:
        c.execute("INSERT OR REPLACE INTO tasks (task_id,mission_id,spec,status,started_at,"
                  "pass_criteria,created_at) VALUES (?,?,?,?,?,'x',datetime('now'))",
                  (tid, "001-shopify-competitor-intel", f"[{week}][seed 1] x", status, started))


def status_of(tid):
    with sqlite3.connect(tmp) as c:
        return c.execute("SELECT status FROM tasks WHERE task_id=?", (tid,)).fetchone()[0]


def note_of(tid):
    with sqlite3.connect(tmp) as c:
        return c.execute("SELECT critic_notes FROM tasks WHERE task_id=?", (tid,)).fetchone()[0] or ""


print("=== the five real stranded rows ===")
with sqlite3.connect(tmp) as c:
    before = [r[0] for r in c.execute("SELECT task_id FROM tasks WHERE status='queued'")]
print(f"  queued before: {before}")
scheduler.expire_stale_parked()
with sqlite3.connect(tmp) as c:
    after = [r[0] for r in c.execute("SELECT task_id FROM tasks WHERE status='queued'")]
    still_queued_old = [r[0] for r in c.execute(
        "SELECT task_id FROM tasks WHERE status='queued' AND task_id IN (4,13,14,17,19)")]
    now_stale = [r[0] for r in c.execute(
        "SELECT task_id FROM tasks WHERE status='stale' AND task_id IN (4,13,14,17,19)")]
check("all five previous-week queued rows expired", sorted(now_stale), [4, 13, 14, 17, 19])
check("no previous-week stranded rows stay queued", still_queued_old, [])
# Task 4 carries started_at + critic_verdict='fail' -- it WAS attempted and re-queued,
# so it must get the ordinary note. 13/14/17/19 have started_at NULL and must not.
check("never-attempted rows (13,14,17,19) are labelled as such",
      [t for t in (13, 14, 17, 19) if "NEVER ATTEMPTED" in note_of(t)], [13, 14, 17, 19])
check("the one row that WAS attempted (4) is not mislabelled",
      "NEVER ATTEMPTED" in note_of(4), False)

print("\n=== current-week work must NOT be touched ===")
seed(9501, WK, "queued")
seed(9502, WK, "quota_wait", started="2026-07-29T01:00:00")
scheduler.expire_stale_parked()
check("current-week queued survives", status_of(9501), "queued")
check("current-week quota_wait survives", status_of(9502), "quota_wait")

print("\n=== previously-attempted rows get the OTHER note ===")
seed(9503, "2026-W28", "quota_wait", started="2026-07-14T01:00:00")
scheduler.expire_stale_parked()
check("attempted-then-parked expires too", status_of(9503), "stale")
check("but is not labelled never-attempted", "NEVER ATTEMPTED" in note_of(9503), False)

print("\n=== interrupted is left alone (--resume can still reach it) ===")
seed(9504, "2026-W28", "interrupted")
scheduler.expire_stale_parked()
check("interrupted untouched", status_of(9504), "interrupted")

print("\n=== the honesty half: they now count as dropped ===")
with sqlite3.connect(tmp) as c:
    c.execute("UPDATE tasks SET created_at=datetime('now') WHERE task_id IN (4,13)")
fit = ledger.weekly_fitness()
check("stale rows register as dropped", fit["dropped"] >= 2, True)
print(f"         (dropped={fit['dropped']}, pending={fit['pending']}, "
      f"scheduled={fit['tasks_scheduled']})")

print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + str(fails)}")
sys.exit(1 if fails else 0)

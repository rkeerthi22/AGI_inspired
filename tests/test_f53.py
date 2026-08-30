"""F53: the intervention term could never be non-zero, so 25% of fitness was free.

Two compounding defects, both required for either fix to matter:
  1. escalate() wrote to workspace/ESCALATIONS.md and never touched the ledger row
     (F33/F48 class: signal measured, then dropped before it reaches the column).
  2. finish_task() wrote `interventions=?` defaulting to 0 -- unconditional overwrite,
     the one consumption column F21 missed. Invisible because the value was always 0.

Runs against a TEMP ledger, never the live one (F12's lesson: the db path is injectable).
The historical all-zero premise is recorded in this test's provenance, not asserted
forever against production history: real interventions are expected after this fix.
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import ledger  # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got} want={want}")


tmp = Path(tempfile.mkdtemp()) / "t.db"
sqlite3.connect(tmp).executescript(
    (ROOT / "ledger" / "schema.sql").read_text(encoding="utf-8"))
ledger.LEDGER_DB = tmp

print("=== 1. a fresh task starts with the historical zero default ===")
premise_tid = ledger.queue_task("m-premise", "spec", "criteria")
with sqlite3.connect(tmp) as c:
    premise = c.execute("SELECT interventions FROM tasks WHERE task_id=?",
                        (premise_tid,)).fetchone()[0]
check("fresh row has interventions=0 before any recorded event", premise, 0)

print("\n=== 2. record_intervention increments and names the kind ===")
tid = ledger.queue_task("m-test", "spec", "criteria")
check("a fresh task starts at 0", ledger.record_intervention(tid, "deny_list_match"), 1)
check("...and increments again", ledger.record_intervention(tid, "cost_cap_breach"), 2)
with sqlite3.connect(tmp) as c:
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT interventions, intervention_types FROM tasks WHERE task_id=?",
                  (tid,)).fetchone()
check("counter reached 2", r["interventions"], 2)
check("kinds recorded in order", json.loads(r["intervention_types"]),
      ["deny_list_match", "cost_cap_breach"])
check("a missing task is a no-op, not a crash", ledger.record_intervention(9999, "x"), 0)

print("\n=== 3. finish_task must NOT clobber it (the F21 column that was missed) ===")
ledger.finish_task(tid, artifacts=[], status="done", critic_verdict="pass")
with sqlite3.connect(tmp) as c:
    r = c.execute("SELECT interventions, intervention_types FROM tasks WHERE task_id=?",
                  (tid,)).fetchone()
check("interventions survived finish_task", r[0], 2)
check("...and so did the type list", json.loads(r[1]),
      ["deny_list_match", "cost_cap_breach"])
# validated against the defect: the pre-F53 write was `interventions=?` with default 0
with sqlite3.connect(tmp) as c:
    c.execute("UPDATE tasks SET interventions=?, intervention_types=? WHERE task_id=?",
              (0, json.dumps([]), tid))
    pre = c.execute("SELECT interventions FROM tasks WHERE task_id=?", (tid,)).fetchone()[0]
check("PRE-F53 behaviour (unconditional 0) would have erased it", pre, 0)
ledger.record_intervention(tid, "deny_list_match")
ledger.record_intervention(tid, "cost_cap_breach")

print("\n=== 4. an explicit caller value still wins (back-compat) ===")
ledger.finish_task(tid, artifacts=[], status="done", interventions=7,
                   intervention_types=["manual"])
with sqlite3.connect(tmp) as c:
    r = c.execute("SELECT interventions, intervention_types FROM tasks WHERE task_id=?",
                  (tid,)).fetchone()
check("explicit interventions= overwrites", r[0], 7)
check("explicit intervention_types= overwrites", json.loads(r[1]), ["manual"])

print("\n=== 5. fitness reports WHICH terms are live without rewarding no-work failures ===")
t2 = ledger.queue_task("m-test", "spec2", "criteria2")
ledger.finish_task(t2, artifacts=[], status="done", critic_verdict="pass")
f = ledger.weekly_fitness()
check("successful zero-cost work is a measured efficiency signal", f["cost_measured"], True)
check("measured successful work is not reported as a free floor",
      f["fitness_floor"] >= 0.10, False)
check("intervention IS measured once a task carries one", f["intervention_measured"], True)

# A failed window with no tokens/evidence must receive no cost-efficiency credit.
tmp2 = Path(tempfile.mkdtemp()) / "t2.db"
sqlite3.connect(tmp2).executescript(
    (ROOT / "ledger" / "schema.sql").read_text(encoding="utf-8"))
ledger.LEDGER_DB = tmp2
t3 = ledger.queue_task("m-test", "s", "c")
ledger.finish_task(t3, artifacts=[], status="failed", critic_verdict="fail")
f2 = ledger.weekly_fitness()
check("a zero-evidence failure receives only the unmeasured intervention term",
      f2["fitness"], 0.25)
check("...and says so: nothing measured",
      (f2["intervention_measured"], f2["cost_measured"]), (False, False))
check("...floor reported as exactly 0.25", f2["fitness_floor"], 0.25)
check("W untouched — LOCKED", ledger.W,
      {"completion": 0.35, "accuracy": 0.30, "intervention": 0.25, "cost": 0.10})

print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + str(fails)}")
sys.exit(1 if fails else 0)

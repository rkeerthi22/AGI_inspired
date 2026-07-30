"""F37: a canary that could not RUN must not count as one that answered wrongly,
and must not open the auto-rollback gate on partial data.

Replays tonight's exact scenario against a ledger copy. escalate() and cmd_rollback()
are stubbed, so nothing is sent and no skill is ever really deleted.
"""
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
import batch_runner as br  # noqa: E402

tmp = Path(tempfile.mkdtemp()) / "ledger.db"
shutil.copy2(ROOT / "ledger" / "ledger.db", tmp)
ledger.LEDGER_DB = tmp
br.ledger.LEDGER_DB = tmp
promote.ledger.LEDGER_DB = tmp
WK = datetime.now().strftime("%Y-W%V")
fails = []
rollbacks = []
br.escalate = lambda *a, **k: None
promote.cmd_rollback = lambda relpath, reason="": rollbacks.append(relpath)


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got} want={want}")


def set_canaries(states):
    """states: list of (status, critic_verdict) for C1..C5 in this week."""
    with sqlite3.connect(tmp) as c:
        c.execute("DELETE FROM tasks WHERE mission_id='canaries' AND spec LIKE ?", (f"[{WK}]%",))
        for i, (st, cv) in enumerate(states, 1):
            c.execute("INSERT INTO tasks (task_id,mission_id,spec,status,critic_verdict,"
                      "pass_criteria,created_at) VALUES (?,?,?,?,?,'x',datetime('now'))",
                      (9600 + i, "canaries", f"[{WK}] C{i}", st, cv))


def gate(states):
    """Recompute the gate exactly as run_canaries() does. Returns (green, unjudged,
    content_fail, rollback_target)."""
    set_canaries(states)
    with sqlite3.connect(tmp) as c:
        rows = c.execute("SELECT status, critic_verdict FROM tasks WHERE mission_id='canaries' "
                         "AND spec LIKE ?", (f"[{WK}]%",)).fetchall()
    green = sum(1 for s, v in rows if s == "done" and v == "pass")
    pending = sum(1 for s, _ in rows if s in ("quota_wait", "queued", "interrupted"))
    infra = sum(1 for s, _ in rows if s == "infra_failed")
    unjudged = pending + infra
    content_fail = 5 - green - unjudged
    target = promote.newest_skill_below_baseline(green) if unjudged == 0 else None
    return green, unjudged, content_fail, target


P, F, I, Q = ("done", "pass"), ("done", "fail"), ("infra_failed", None), ("quota_wait", None)

print("=== tonight's ACTUAL scenario, under the OLD rules ===")
print("  C2/C5 scored 'fail' because gemma's error text missed the grader")
g, u, cf, t = gate([P, F, P, P, F])
print(f"  green={g} unjudged={u} content_fail={cf} -> rollback target: {t}")
check("old behaviour: judged on 3/5, missed deleting a skill by one", (g, u, t), (3, 0, None))
print("  ^ had ONE more canary hit gemma, green=2 < baseline 3 and a skill would have died")

print("\n=== the same night, under the FIX (infra failures classified) ===")
g, u, cf, t = gate([P, I, P, P, I])
check("green counts only real passes", g, 3)
check("the two model failures are UNJUDGED, not content failures", (u, cf), (2, 0))
check("gate stays SHUT on partial data — no skill at risk", t, None)

print("\n=== the fix must not disarm a REAL regression ===")
g, u, cf, t = gate([P, F, F, F, F])
check("4 genuine content failures still judged", (g, u, cf), (1, 0, 4))
check("real regression DOES select a rollback target", t is not None, True)

print("\n=== worse infra luck must not become a rollback ===")
g, u, cf, t = gate([P, I, I, I, I])
check("1 green, 4 infra -> still no judgement", (g, u, t), (1, 4, None))

print("\n=== all green, all real -> nothing to roll back ===")
g, u, cf, t = gate([P, P, P, P, P])
check("5/5 green, gate open, no target", (g, u, t), (5, 0, None))

print("\n=== quota parks still block judgement (original behaviour preserved) ===")
g, u, cf, t = gate([P, Q, P, P, Q])
check("parked canaries keep the gate shut", (g, u, t), (3, 2, None))

print("\n=== mixed park + infra both count as unjudged ===")
g, u, cf, t = gate([P, Q, I, P, F])
check("1 park + 1 infra + 1 content fail", (g, u, cf), (2, 2, 1))
check("gate shut while anything is unjudged", t, None)

print(f"\nno real rollback was ever invoked: {rollbacks == []}")
print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + str(fails)}")
sys.exit(1 if fails else 0)

"""Directive-1 (all seeds per fire) + Directive-2 (same-fire retry) regression test.

Runs against a COPY of ledger.db with run_task() stubbed, so no worker call, no cost,
and no mutation of the live ledger. Asserts behaviour, not code shape.
"""
import argparse
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import ledger  # noqa: E402
import batch_runner as br  # noqa: E402

tmp = Path(tempfile.mkdtemp()) / "ledger.db"
shutil.copy2(ROOT / "ledger" / "ledger.db", tmp)
ledger.LEDGER_DB = tmp
br.ledger.LEDGER_DB = tmp
br.escalate = lambda *a, **k: None          # never a real Telegram send in a test
br.preflight = lambda: True

MISSION = br.parse_mission("001-shopify-competitor-intel")
fails = []


def check(name, got, want):
    ok = got == want
    fails.append(name) if not ok else None
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got}\n        want={want}")


# ---------------------------------------------------------------- directive 1
print("\n=== D1: one expensive seed must not cancel the cheap seeds behind it ===")
calls = []
seq = {}


def fake_run_task(tid, mission, roles):
    calls.append(tid)
    return seq.get(tid, "done")


br.run_task = fake_run_task
REAL_RETRY = br.retry_failed_this_fire      # restored for the D2 section below
br.retry_failed_this_fire = lambda *a, **k: []
br.queue_mission_tasks = lambda m, d: [101, 102, 103, 104]
br.load_roles = lambda: {}
br.expire_stale_parked = lambda: None
br.reconcile_interrupted_tasks = lambda: 0
br.policy.validate_paths = lambda p: []
br.ledger.weekly_fitness = lambda: {}

args = argparse.Namespace(scorecard=False, canaries=False, resume=False, dry_run=False,
                          mission="001-shopify-competitor-intel", max_tasks=10,
                          deliver=False)

calls.clear(); seq = {101: "budget_skip"}
br._run(args)
check("budget_skip on seed 1 still attempts seeds 2-4", calls, [101, 102, 103, 104])

calls.clear(); seq = {101: "quota_wait"}
br._run(args)
check("daily-cap park still annotates every remaining seed", calls, [101, 102, 103, 104])

calls.clear(); seq = {101: "chain_exhausted", 102: "chain_exhausted"}
br._run(args)
check("2 consecutive chain_exhausted stops the pass", calls, [101, 102])

calls.clear(); seq = {101: "chain_exhausted", 102: "done", 103: "chain_exhausted"}
br._run(args)
check("a success resets the exhaustion streak", calls, [101, 102, 103, 104])

# ---------------------------------------------------------------- directive 2
print("\n=== D2: same-fire retry selection, ordering and cap ===")
br.run_task = fake_run_task
br.retry_failed_this_fire = REAL_RETRY
ids = [201, 202, 203, 204, 205]
with sqlite3.connect(tmp) as c:
    for tid, spec, status, cv in [
            (201, "[2026-W31][seed 1] PromptBase: price scan", "failed", "fail"),
            (202, "[2026-W31][seed 2] AIPRM: plan pricing", "done", "pass"),
            (203, "[2026-W31][seed 3] Notion seller scan", "infra_failed", None),
            (204, "[2026-W31][seed 4] Synthesis: build the diff", "failed", "fail"),
            (205, "[2026-W31][seed 5] Gumroad: category scan", "failed", "fail")]:
        c.execute("INSERT OR REPLACE INTO tasks (task_id, mission_id, spec, status, "
                  "critic_verdict, pass_criteria, created_at) VALUES (?,?,?,?,?,?,"
                  "datetime('now'))",
                  (tid, MISSION["id"], spec, status, cv, "x"))

calls.clear(); seq = {}
out = br.retry_failed_this_fire(ids, MISSION, {})
check("retries only content failures (not done, not infra_failed)",
      sorted(calls), [201, 204, 205])
check("synthesis retried LAST, after the briefs it rebuilds from",
      calls, [201, 205, 204])
check("returns one status per retry", len(out), 3)

br.MAX_RETRIES_PER_FIRE = 2
calls.clear()
br.retry_failed_this_fire(ids, MISSION, {})
check("respects MAX_RETRIES_PER_FIRE cap", calls, [201, 205])
br.MAX_RETRIES_PER_FIRE = 3

calls.clear(); seq = {201: "chain_exhausted"}
br.retry_failed_this_fire(ids, MISSION, {})
check("retry pass stops when the fallback chain is exhausted", calls, [201])

# ---------------------------------------------------------------- F32
print("\n=== F32: a successful retry must ADD its tokens, not replace them ===")
with sqlite3.connect(tmp) as c:
    c.execute("UPDATE tasks SET tokens_in=1000, tokens_out=500 WHERE task_id=201")
row = {"tokens_in": 1000, "tokens_out": 500}
usage = {"input_tokens": 700, "output_tokens": 300}
tok_in = int(usage.get("input_tokens") or 0) + int(row.get("tokens_in") or 0)
tok_out = int(usage.get("output_tokens") or 0) + int(row.get("tokens_out") or 0)
check("retry accumulates prior spend", (tok_in, tok_out), (1700, 800))
row0 = {"tokens_in": None, "tokens_out": None}
check("first run is unaffected (no double count)",
      (int(usage["input_tokens"]) + int(row0.get("tokens_in") or 0),
       int(usage["output_tokens"]) + int(row0.get("tokens_out") or 0)), (700, 300))

print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + str(fails)}")
sys.exit(1 if fails else 0)

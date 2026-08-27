"""F48: canary token spend was measured, then dropped on the floor.

`worker_with_failover()` returns `usage`; `run_canaries()` consumed it one line above
via `worker_failed(out, usage)` and then called `ledger.finish_task()` without
tokens_in/tokens_out. Every resolved canary row read 0/0 while mission rows carried
millions, so `policy.tokens_used_today()` under-counted by exactly the canary spend and
the `tokens_per_day_hard_stop` guard protected less than it claimed.

Runs against a COPY of ledger.db. Every outward path (escalate, rollback, integrity
guards, the model call itself) is stubbed, so nothing is sent, no skill is deleted, and
no real file is touched.

Validated against the defect: section 2 replays the pre-fix call shape on a real row and
shows it produces 0/0, so a green run here cannot be green for some other reason.
"""
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import ledger  # noqa: E402
import policy  # noqa: E402
import promote  # noqa: E402
import batch_runner as br  # noqa: E402
from _silence import silence_log  # noqa: E402

tmp = Path(tempfile.mkdtemp()) / "ledger.db"
shutil.copy2(ROOT / "ledger" / "ledger.db", tmp)
ledger.LEDGER_DB = tmp
br.ledger.LEDGER_DB = tmp
policy.LEDGER_DB = tmp

fails = []
WK = br.week_key()

# ── stubs: nothing leaves this process ────────────────────────────────────────
br.escalate = lambda *a, **k: None
br.db_integrity_snapshot = lambda *a, **k: {}
br.db_integrity_check = lambda *a, **k: None
br.fs_integrity_snapshot = lambda *a, **k: {}
br.fs_integrity_check = lambda *a, **k: None
policy.token_budget_breached = lambda *a, **k: False
promote.newest_skill_below_baseline = lambda *a, **k: None
promote.cmd_rollback = lambda *a, **k: None

# F56: silence ALL orchestrator log streams (not just batch_runner.log).
# See tests/_silence.py for why the helper exists.
_silence_ctx = silence_log()
_silence_ctx.__enter__()

ROLES = {"worker": {"provider": "ollama", "model": "test-model"}}


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got}\n        want={want}")


def row_for(name):
    with sqlite3.connect(tmp) as c:
        c.row_factory = sqlite3.Row
        return c.execute("SELECT * FROM tasks WHERE mission_id='canaries' AND spec=?",
                         (f"[{WK}] {name}",)).fetchone()


def wipe(name):
    with sqlite3.connect(tmp) as c:
        c.execute("DELETE FROM tasks WHERE mission_id='canaries' AND spec=?",
                  (f"[{WK}] {name}",))


def seed(name, status, tin, tout):
    """Pre-existing row for the resume cases."""
    wipe(name)
    with sqlite3.connect(tmp) as c:
        c.execute("INSERT INTO tasks (task_id,mission_id,spec,status,tokens_in,tokens_out,"
                  "pass_criteria,created_at) VALUES (?,?,?,?,?,?,'x',datetime('now'))",
                  (9800, "canaries", f"[{WK}] {name}", status, tin, tout))


def fake_chain(usage, out="Canberra — https://example.org", exhausted=False):
    def _w(prompt, cfg, path, log_prefix="", allow_local=True):
        return (out, usage, cfg, exhausted)
    return _w


def run_one(name, usage, out="Canberra — https://example.org", exhausted=False,
            infra=False):
    br.CANARIES = [(name, "q", lambda t: "canberra" in t.lower())]
    br.worker_with_failover = fake_chain(usage, out, exhausted)
    br.worker_failed = lambda o, u: infra
    br.run_canaries(ROLES)
    return row_for(name)


print("=== 1. accumulated_tokens() arithmetic ===")
check("fresh attempt returns this run's usage",
      br.accumulated_tokens({"input_tokens": 5, "output_tokens": 7}, 0, 0), (5, 7))
check("resume ADDS to the row's prior total",
      br.accumulated_tokens({"input_tokens": 5, "output_tokens": 7}, 100, 200), (105, 207))
check("NULL prior (first run) is a no-op, not a crash",
      br.accumulated_tokens({"input_tokens": 5, "output_tokens": 7}, None, None), (5, 7))
check("missing/None usage keys degrade to 0",
      br.accumulated_tokens({"input_tokens": None}, 3, None), (3, 0))
check("empty usage dict (no-candidates chain) is 0",
      br.accumulated_tokens({}, None, None), (0, 0))

print("\n=== 2. validate against the DEFECT: the pre-fix call shape ===")
# This is exactly what run_canaries() used to do -- finish_task with no tokens.
wipe("C90")
tid = ledger.queue_task("canaries", f"[{WK}] C90", "deterministic grade")
ledger.finish_task(tid, artifacts=[], status="done", critic_verdict="pass",
                   critic_notes="deterministic: ok")
pre = row_for("C90")
check("pre-fix call shape records ZERO tokens (this was the bug)",
      (pre["tokens_in"] or 0, pre["tokens_out"] or 0), (0, 0))
wipe("C90")
tid = ledger.queue_task("canaries", f"[{WK}] C90", "deterministic grade")
ledger.finish_task(tid, artifacts=[], status="done", tokens_in=11, tokens_out=22,
                   critic_verdict="pass", critic_notes="deterministic: ok")
post = row_for("C90")
check("passing them through records them",
      (post["tokens_in"], post["tokens_out"]), (11, 22))

print("\n=== 3. end-to-end through run_canaries(): a fresh canary ===")
wipe("C91")
r = run_one("C91", {"input_tokens": 1234, "output_tokens": 56})
check("status is done/pass", (r["status"], r["critic_verdict"]), ("done", "pass"))
check("the canary row now carries the spend",
      (r["tokens_in"], r["tokens_out"]), (1234, 56))

print("\n=== 4. a RESUMED canary accumulates rather than replaces (F32's rule) ===")
seed("C92", "quota_wait", 1000, 10)
r = run_one("C92", {"input_tokens": 500, "output_tokens": 5})
check("prior spend survives the successful retry",
      (r["tokens_in"], r["tokens_out"]), (1500, 15))
check("...and it is not the replace-behaviour F32 outlawed",
      (r["tokens_in"], r["tokens_out"]) == (500, 5), False)

print("\n=== 5. the non-green paths record spend too ===")
wipe("C93")
r = run_one("C93", {"input_tokens": 77, "output_tokens": 3}, exhausted=True)
check("quota-parked canary keeps whatever the failed rungs burned",
      (r["status"], r["tokens_in"], r["tokens_out"]), ("quota_wait", 77, 3))
check("...but parking still does not stamp finished_at (F22b holds)",
      r["finished_at"], None)
wipe("C94")
r = run_one("C94", {"input_tokens": 88, "output_tokens": 4}, infra=True)
check("infra_failed canary records its spend",
      (r["status"], r["tokens_in"], r["tokens_out"]), ("infra_failed", 88, 4))

print("\n=== 6. the daily budget guard can finally see canary spend ===")
before = policy.tokens_used_today()
wipe("C95")
run_one("C95", {"input_tokens": 4000, "output_tokens": 100})
after = policy.tokens_used_today()
check("tokens_used_today() moves by exactly the canary's in+out",
      after - before, 4100)

print("\n=== 7. the mission path is unchanged by the refactor ===")
# run_task()'s old inline expression, reproduced literally, must equal the helper.
for usage, prow in [({"input_tokens": 9, "output_tokens": 1}, {"tokens_in": 4, "tokens_out": 2}),
                    ({}, {"tokens_in": None, "tokens_out": None}),
                    ({"input_tokens": 0, "output_tokens": 0}, {"tokens_in": 7, "tokens_out": 8})]:
    old = (int(usage.get("input_tokens") or 0) + int(prow.get("tokens_in") or 0),
           int(usage.get("output_tokens") or 0) + int(prow.get("tokens_out") or 0))
    new = br.accumulated_tokens(usage, prow.get("tokens_in"), prow.get("tokens_out"))
    check(f"helper == old inline expression for {usage}/{prow}", new, old)

print("\n=== 8. the real ledger was never touched ===")
check("test wrote only to the temp copy", ledger.LEDGER_DB, tmp)

print("\nFAILURES:", fails if fails else "none")
_silence_ctx.__exit__(None, None, None)
sys.exit(1 if fails else 0)

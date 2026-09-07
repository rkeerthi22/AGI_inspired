"""F129: Retry-attempt artifact preservation and multi-attempt accounting semantics.

Asserts:
1. get_task_attempt monotonically advances across retries based on database
   attempt_count and existing artifacts on disk.
2. write_worker_raw creates task{tid}_a{N}_worker_raw.txt and never mutates prior attempt files.
3. build_mission_usage produces single-attempt top-level spend (worker + critic == mission)
   and tracks cumulative spend in attempt_totals.
4. Attempt 2 never clobbers or mutates Attempt 1 mission, critic, or worker artifacts.
5. ledger.finish_task persists attempt_count.
"""
import gc
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import evaluation
import ledger
import scheduler
from worker_diagnostics import get_task_attempt, write_worker_raw

checks = 0
fails = []

def check(name, got, want=True):
    global checks
    checks += 1
    ok = (got == want)
    if not ok:
        fails.append(name)
        print(f"  [FAIL] {name}\n         got={got!r}\n         want={want!r}")
    else:
        print(f"  [PASS] {name}")

def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

print("=== 1. get_task_attempt monotonic resolution ===")
with tempfile.TemporaryDirectory() as td:
    runs = Path(td)
    tid = 100

    # Fresh task: no db row, no disk files -> attempt 1
    check("clean task starts at attempt 1", get_task_attempt(tid, None, runs), 1)

    # Row with attempt_count = 1 -> attempt 2
    check("row with attempt_count=1 yields attempt 2", get_task_attempt(tid, {"attempt_count": 1}, runs), 2)

    # Legacy file task100_worker_raw.txt exists on disk -> attempt 2
    (runs / f"task{tid}_worker_raw.txt").write_text("legacy", encoding="utf-8")
    check("disk with legacy un-suffixed artifact yields attempt 2", get_task_attempt(tid, None, runs), 2)

    # Disk with attempt 1 artifact -> attempt 2
    (runs / f"task{tid}_a1_worker_raw.txt").write_text("a1", encoding="utf-8")
    check("disk with a1 artifact yields attempt 2", get_task_attempt(tid, None, runs), 2)

    # Disk with attempt 2 artifact -> attempt 3
    (runs / f"task{tid}_a2_worker_raw.txt").write_text("a2", encoding="utf-8")
    check("disk with a2 artifact yields attempt 3", get_task_attempt(tid, None, runs), 3)

    # Monotonic forward-only: even if row says attempt_count=0, existing disk max 2 forces attempt 3
    check("monotonic forward: disk max 2 overrides row 0 to yield attempt 3",
          get_task_attempt(tid, {"attempt_count": 0}, runs), 3)

print("\n=== 2. write_worker_raw non-mutation of prior attempts ===")
with tempfile.TemporaryDirectory() as td:
    runs = Path(td)
    tid = 200

    # Attempt 1
    p1 = write_worker_raw(runs, tid, "Attempt 1 output", {"tokens_in": 50}, "worker", attempt=1)
    check("attempt 1 raw file exists", p1.is_file())
    check("attempt 1 raw file named with _a1_", p1.name, f"task{tid}_a1_worker_raw.txt")
    legacy1 = runs / f"task{tid}_worker_raw.txt"
    check("attempt 1 mirrors to legacy un-suffixed path", legacy1.is_file())
    sha1 = file_sha256(p1)
    sha_legacy = file_sha256(legacy1)

    # Attempt 2
    p2 = write_worker_raw(runs, tid, "Attempt 2 output - completely different", {"tokens_in": 120}, "worker", attempt=2)
    check("attempt 2 raw file exists", p2.is_file())
    check("attempt 2 raw file named with _a2_", p2.name, f"task{tid}_a2_worker_raw.txt")
    check("attempt 1 raw file unchanged after attempt 2", file_sha256(p1), sha1)
    check("legacy raw file unchanged after attempt 2", file_sha256(legacy1), sha_legacy)
    check("attempt 2 content differs from attempt 1", p2.read_text(encoding="utf-8") != p1.read_text(encoding="utf-8"))

print("\n=== 3. build_mission_usage multi-attempt accounting semantics ===")
with tempfile.TemporaryDirectory() as td:
    runs = Path(td)
    tid = 300

    # Attempt 1: worker=1000/200, critic=300/50
    w1 = {"input_tokens": 1000, "output_tokens": 200, "api_calls": 3}
    c1 = {"input_tokens": 300, "output_tokens": 50, "api_calls": 1, "citation_fetches": 2, "citation_unique_urls": 2}
    
    m1 = evaluation.build_mission_usage(tid, w1, c1, prior_in=0, prior_out=0, attempt=1, runs_dir=runs)
    
    # Invariant: worker + critic == mission for this attempt
    check("m1 attempt == 1", m1["attempt"], 1)
    check("m1 input: worker + critic == mission (1000 + 300 == 1300)", m1["input_tokens"], 1300)
    check("m1 output: worker + critic == mission (200 + 50 == 250)", m1["output_tokens"], 250)
    check("m1 total tokens: 1300 + 250 == 1550", m1["total_tokens"], 1550)
    check("m1 attempt_totals accumulates 1300/250", m1["attempt_totals"],
          {"input_tokens": 1300, "output_tokens": 250, "total_tokens": 1550})

    m1_a1_path = runs / f"task{tid}_a1_mission.usage.json"
    m1_legacy_path = runs / f"task{tid}_mission.usage.json"
    check("m1_a1_path exists", m1_a1_path.is_file())
    check("m1_legacy_path exists for attempt 1", m1_legacy_path.is_file())
    sha_m1_a1 = file_sha256(m1_a1_path)
    sha_m1_legacy = file_sha256(m1_legacy_path)

    # Attempt 2: retry with prior spend 1300/250
    w2 = {"input_tokens": 1500, "output_tokens": 350, "api_calls": 4}
    c2 = {"input_tokens": 400, "output_tokens": 80, "api_calls": 1, "citation_fetches": 3, "citation_unique_urls": 3}

    m2 = evaluation.build_mission_usage(tid, w2, c2, prior_in=1300, prior_out=250, attempt=2, runs_dir=runs)

    # Invariant: worker + critic == mission for attempt 2
    check("m2 attempt == 2", m2["attempt"], 2)
    check("m2 input: worker + critic == mission (1500 + 400 == 1900)", m2["input_tokens"], 1900)
    check("m2 output: worker + critic == mission (350 + 80 == 430)", m2["output_tokens"], 430)
    check("m2 total tokens: 1900 + 430 == 2330", m2["total_tokens"], 2330)
    # Cumulative ledger totals: (1300+1900=3200 in, 250+430=680 out)
    check("m2 attempt_totals reflects cumulative spend", m2["attempt_totals"],
          {"input_tokens": 3200, "output_tokens": 680, "total_tokens": 3880})

    m2_a2_path = runs / f"task{tid}_a2_mission.usage.json"
    check("m2_a2_path exists", m2_a2_path.is_file())
    # Crucial assertion: Attempt 1 mission file was NOT mutated by Attempt 2
    check("Attempt 1 mission file (_a1_) UNTOUCHED by Attempt 2", file_sha256(m1_a1_path), sha_m1_a1)
    check("Attempt 1 legacy mission file UNTOUCHED by Attempt 2", file_sha256(m1_legacy_path), sha_m1_legacy)

    # Test scheduler.accumulated_tokens alignment
    tok_in, tok_out = scheduler.accumulated_tokens(m2, 1300, 250)
    check("scheduler.accumulated_tokens matches attempt_totals input", tok_in, 3200)
    check("scheduler.accumulated_tokens matches attempt_totals output", tok_out, 680)

print("\n=== 4. run_critic attempt preservation ===")
with tempfile.TemporaryDirectory() as td:
    runs = Path(td)
    tid = 400
    row = {"task_id": tid, "spec": "spec", "pass_criteria": "pass criteria"}
    roles = {"critic": {"model": "test-critic", "provider": "ollama"}}

    evidence = [{"url": "https://example.com", "reachable": True, "http_status": 200, "literal": None}]
    with patch.object(evaluation, "RUNS", runs), \
         patch.object(evaluation.citecheck, "verify", return_value=evidence), \
         patch.object(evaluation.citecheck, "summarize", return_value={"checked": 1, "dead": 0, "dead_frac": 0, "literal_checked": 0, "literal_missing": 0}), \
         patch.object(evaluation.citecheck, "is_hard_fail", return_value=False), \
         patch.object(evaluation.policy, "manager_call_budget_breached", return_value=False), \
         patch.object(evaluation.policy, "record_manager_call"), \
         patch.object(evaluation.execution, "ollama_chat", return_value="VERDICT: PASS\nDone"):

        u1 = {}
        evaluation.run_critic(row, "Brief 1", roles, False, usage_out=u1, attempt=1)
        c1_path = runs / f"task{tid}_a1_critic.usage.json"
        check("attempt 1 critic usage exists", c1_path.is_file())
        sha_c1 = file_sha256(c1_path)

        u2 = {}
        evaluation.run_critic(row, "Brief 2", roles, False, usage_out=u2, attempt=2)
        c2_path = runs / f"task{tid}_a2_critic.usage.json"
        check("attempt 2 critic usage exists", c2_path.is_file())
        check("attempt 1 critic usage UNTOUCHED by attempt 2", file_sha256(c1_path), sha_c1)

print("\n=== 5. ledger.finish_task attempt_count persistence ===")
td = tempfile.mkdtemp()
try:
    db_path = Path(td) / "test_ledger.db"
    src = sqlite3.connect(ROOT / "ledger" / "ledger.db")
    dst = sqlite3.connect(db_path)
    try:
        schema = [r[0] for r in src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'").fetchall() if r[0]]
        for stmt in schema:
            dst.execute(stmt)
        dst.execute("INSERT INTO tasks (task_id, mission_id, spec, pass_criteria, status, created_at) VALUES (500, 'm', 's', 'pc', 'queued', datetime('now'))")
        dst.commit()
    finally:
        src.close()
        dst.close()

    with patch.object(ledger, "LEDGER_DB", db_path):
        ledger.finish_task(500, artifacts=[], status="done", critic_verdict="pass",
                           tokens_in=5000, tokens_out=1000, attempt_count=2)
        conn = sqlite3.connect(db_path)
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM tasks WHERE task_id=500").fetchone()
            check("ledger finish_task persists attempt_count=2", row["attempt_count"], 2)
            check("ledger finish_task persists tokens_in=5000", row["tokens_in"], 5000)
            check("ledger finish_task persists tokens_out=1000", row["tokens_out"], 1000)
        finally:
            conn.close()
finally:
    gc.collect()
    import shutil
    shutil.rmtree(td, ignore_errors=True)

print(f"\n{checks - len(fails)}/{checks} assertions passed")
if fails:
    print(f"FAILURES: {fails}")
    sys.exit(1)
sys.exit(0)

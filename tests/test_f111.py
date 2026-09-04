"""F111: audit-driven fail-closed fixes (verification audit 2026-09-05).

Two cheap, high-signal security fixes from the Gemini verification audit,
documented in runs/audit_summary.json. F106-F110 were taken (Gemini's
Q2#3 + test-isolation + citecheck BLOCKED/DEAD batch); these are the next
free number and do not overlap.

  S1 Migration fail-closed (batch_runner.main): migrate_all() returning a
     (-1, -1) result, OR raising, must abort the batch (return 75) before it
     touches the ledger or acquires the runlock. Previously both were
     swallowed as "non-fatal" and the batch proceeded against an un-migrated /
     unknown schema. Pinned by stubbing main()'s local imports
     (execution_pause, migrations, runlock) and asserting the return code +
     that runlock is never entered on the abort paths, and is entered exactly
     once on the valid-migrations happy path.

  S2 fs_integrity_check in finally (task_runner._run_research_task): a worker
     that times out mid-call must STILL trigger the filesystem containment
     check. Previously fs_integrity_check ran only on the success path (after
     the try/except), so every abnormal-exit early return bypassed it. Pinned
     by stubbing worker_with_failover to raise subprocess.TimeoutExpired and
     asserting fs_integrity_check was called AND run_task returns
     "infra_failed". The success path is also pinned to call fs_integrity_check
     exactly once, so the finally cannot regress to double-call or no-call.

No live LLM or provider call; deterministic stubs only.
"""
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.path.insert(0, str(ROOT / "tests"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import batch_runner as br  # noqa: E402
import task_runner as tr  # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got={got!r} want={want!r}")


# ── S1. migration fail-closed aborts before runlock ───────────────────────

print("=== 1. migration fail-closed (batch_runner.main) ===")

import execution_pause  # noqa: E402
import migrations  # noqa: E402
import runlock  # noqa: E402

saved_argv = sys.argv
saved_patches = []


def _patch(target, attr, value):
    saved_patches.append((target, attr, getattr(target, attr, None)))
    setattr(target, attr, value)


class _FakeLock:
    def __init__(self, record):
        self._record = record

    def __enter__(self):
        self._record.append(True)
        return self

    def __exit__(self, *a):
        return False


runlock_calls = []


def _fake_acquire(*a, **k):
    return _FakeLock(runlock_calls)


# 1a. A (-1, -1) migration result aborts with 75, runlock never entered.
_patch(execution_pause, "pause_engaged", lambda: False)
_patch(execution_pause, "verify_pause_integrity", lambda: None)
_patch(migrations, "migrate_all", lambda: {"ledger": (-1, -1)})
_patch(runlock, "acquire", _fake_acquire)
sys.argv = ["batch_runner"]
runlock_calls.clear()
try:
    rc_neg = br.main()
finally:
    sys.argv = saved_argv
check("(-1,-1) result aborts with 75", rc_neg, 75)
check("runlock not entered on migration failure", runlock_calls, [])

# 1b. migrate_all() raising also aborts with 75 (not swallowed as non-fatal).
def _boom_migrate():
    raise RuntimeError("sqlite locked")


_patch(migrations, "migrate_all", _boom_migrate)
sys.argv = ["batch_runner"]
runlock_calls.clear()
try:
    rc_exc = br.main()
finally:
    sys.argv = saved_argv
check("migrate_all exception aborts with 75", rc_exc, 75)
check("runlock not entered on migration exception", runlock_calls, [])

# 1c. Valid migrations (no-op) do NOT abort: proceeds to runlock + _run.
_patch(migrations, "migrate_all", lambda: {"ledger": (2, 2)})
_patch(br, "_run", lambda args: 0)
_patch(br, "set_log_file", lambda *a, **k: None)
sys.argv = ["batch_runner"]
runlock_calls.clear()
try:
    rc_ok = br.main()
finally:
    sys.argv = saved_argv
check("valid migrations proceed (rc 0, not 75)", rc_ok, 0)
check("runlock entered when migrations are valid", runlock_calls, [True])

# Undo batch_runner / migrations / runlock / execution_pause patches.
for target, attr, old in reversed(saved_patches):
    if old is None:
        try:
            delattr(target, attr)
        except AttributeError:
            pass
    else:
        setattr(target, attr, old)
saved_patches.clear()


# ── S2. fs_integrity_check runs on abnormal worker exit ──────────────────

print("\n=== 2. fs_integrity_check in finally (task_runner.run_task) ===")

fs_calls = []


def _record_fs(before, context):
    fs_calls.append(context)


# Reuse the test_f60/test_f106 scaffolding: stub everything run_task touches so
# we can drive _run_research_task to the worker call with deterministic state.
hook = types.ModuleType("prediction_machine.integrations.batch_runner_hook")
hook.before_task_runs = lambda *a: None
hook.after_task_completes = lambda *a: None
sys.modules["prediction_machine.integrations.batch_runner_hook"] = hook

row = {"spec": "Research Acme pricing", "critic_verdict": None,
       "critic_notes": "", "tokens_in": 0, "tokens_out": 0}
mission = {"id": "f111", "objective": "market map", "frontmatter": {}}
roles = {"worker": {"provider": "p", "model": "m"},
         "manager": {"model": "judge"},
         "critic": {"model": "m"}}

import tempfile

saved2 = []


def _patch2(target, attr, value):
    saved2.append((target, attr, getattr(target, attr, None)))
    setattr(target, attr, value)


with tempfile.TemporaryDirectory(dir=ROOT / "workspace") as raw:
    temp_root = Path(raw)
    (temp_root / "runs").mkdir()
    _patch2(tr, "_load_task", lambda tid: dict(row))
    _patch2(tr.rc, "ROOT", temp_root)
    _patch2(tr.rc, "RUNS", temp_root / "runs")
    _patch2(tr.rc, "log", lambda msg: None)
    _patch2(tr.scheduler, "week_key", lambda: "2099-W01")
    _patch2(tr.scheduler, "mission_workspace", lambda mid: "mission")
    _patch2(tr.scheduler, "is_first_run_for_mission", lambda mid: False)
    _patch2(tr.scheduler, "accumulated_tokens",
            lambda usage, old_in, old_out: (old_in, old_out))
    _patch2(tr.prompts, "mission_objective", lambda m: "OBJECTIVE")
    _patch2(tr.prompts, "deliverable_requirements", lambda m: "REQUIREMENTS")
    _patch2(tr.prompts, "task_scope_note", lambda spec, m: "SCOPE")
    _patch2(tr.promote, "active_skills_for", lambda mid: "")
    _patch2(tr.promote, "SKILLS", temp_root / "skills")
    _patch2(tr.policy, "compliance_prompt_block", lambda: "")
    _patch2(tr.policy, "token_budget_breached", lambda: False)
    _patch2(tr.policy, "estimated_tokens_for", lambda tid, mid: 12)
    _patch2(tr.policy, "budget_insufficient_for", lambda est: False)
    _patch2(tr.policy, "deny_list_scan", lambda out: [])
    _patch2(tr.integrity, "fs_integrity_snapshot", lambda: "fs")
    _patch2(tr.integrity, "fs_integrity_check", _record_fs)
    _patch2(tr.integrity, "escalate", lambda *a, **k: None)

    class _FakeGuard:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    _patch2(tr.integrity, "DatabaseMutationGuard", lambda label: _FakeGuard())
    _patch2(tr.ledger, "start_task", lambda *a, **k: None)
    _patch2(tr.ledger, "finish_task", lambda *a, **k: None)
    _patch2(tr.ledger, "update_model_used", lambda *a, **k: None)
    _patch2(tr.ledger, "add_lesson", lambda *a, **k: None)
    _patch2(tr.workflow, "_check_repeated_failure", lambda mid: None)
    _patch2(tr.evaluation, "seed_is_synthesis", lambda spec: False)

    # 2a. Worker timeout -> fs_integrity_check STILL called + infra_failed.
    def _timeout_worker(prompt, cfg, usage_path, log_prefix, **options):
        raise subprocess.TimeoutExpired(cmd="hermes", timeout=1)

    _patch2(tr.execution, "worker_with_failover", _timeout_worker)
    fs_calls.clear()
    status_timeout = tr.run_task(111, mission, roles)
    check("worker timeout returns infra_failed", status_timeout, "infra_failed")
    check("fs_integrity_check called on timeout (finally)", len(fs_calls), 1)
    check("fs_integrity_check context names the worker call",
          "worker call" in (fs_calls[0] if fs_calls else ""), True)

    # 2b. Worker launch failure (generic Exception) -> fs check still runs.
    def _crash_worker(prompt, cfg, usage_path, log_prefix, **options):
        raise RuntimeError("launch crashed")

    _patch2(tr.execution, "worker_with_failover", _crash_worker)
    fs_calls.clear()
    status_crash = tr.run_task(112, mission, roles)
    check("worker crash returns infra_failed", status_crash, "infra_failed")
    check("fs_integrity_check called on crash (finally)", len(fs_calls), 1)

    # 2c. Success path still calls fs_integrity_check exactly once (no
    # double-call from the finally, no regression on the happy path).
    output = "A" * 260
    _patch2(tr.execution, "worker_with_failover",
            lambda prompt, cfg, usage_path, log_prefix, **options:
            (output, {"tokens_in": 3, "tokens_out": 5}, cfg, False))
    _patch2(tr.execution, "_strip_tool_chatter", lambda out: out)
    _patch2(tr.execution, "worker_failed", lambda out, usage: False)
    _patch2(tr.evaluation, "run_critic", lambda *a, **k: ("pass", "ok"))
    _patch2(tr.evaluation, "build_mission_usage",
            lambda tid, wu, cu: wu)
    _patch2(tr.evaluation, "extract_facts", lambda *a: 0)
    fs_calls.clear()
    status_ok = tr.run_task(113, mission, roles)
    check("success path returns done", status_ok, "done")
    check("success path calls fs_integrity_check exactly once", len(fs_calls), 1)

for target, attr, old in reversed(saved2):
    if old is None:
        try:
            delattr(target, attr)
        except AttributeError:
            pass
    else:
        setattr(target, attr, old)

if fails:
    print("\nFAILURES:", *fails, sep="\n  - ")
    raise SystemExit(1)
print("\nF111 PASS — migration fail-closed + fs_integrity finally verified")

"""Model-free regressions for controlled retrieval and outcome classification."""
import contextlib
import io
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import controlled_hermes
import workflow

checks = {}


class FakeController:
    def __init__(self):
        self.research_calls = 0
        self.finalization_calls = 0

    def research_finished(self, **kwargs):
        self.research_calls += 1

    def finalization_started(self):
        self.finalization_calls += 1


controller = FakeController()
finalizer_calls = []

fake_contract = types.ModuleType("hermes_contract")
fake_contract.validate_installed_hermes = lambda root: None
fake_capabilities = types.ModuleType("hermes_capabilities")
fake_capabilities.install_harness_capabilities = lambda **kwargs: None
fake_progress = types.ModuleType("retrieval_progress")
fake_progress.active_controller = lambda: controller
fake_progress.install_hermes_adapter = lambda audit: None
fake_oneshot = types.ModuleType("hermes_cli.oneshot")


def failed_research(*args, **kwargs):
    print("research stdout evidence")
    print("research stderr evidence", file=sys.stderr)
    return 23


fake_oneshot.run_oneshot = failed_research
fake_cli = types.ModuleType("hermes_cli")
fake_cli.oneshot = fake_oneshot
fake_execution = types.ModuleType("execution")
fake_execution.ollama_chat = lambda *a, **k: finalizer_calls.append((a, k)) or "should not run"

module_names = {
    "hermes_contract": fake_contract,
    "hermes_capabilities": fake_capabilities,
    "retrieval_progress": fake_progress,
    "hermes_cli": fake_cli,
    "hermes_cli.oneshot": fake_oneshot,
    "execution": fake_execution,
}
prior_modules = {name: sys.modules.get(name) for name in module_names}
prior_pause = controlled_hermes.pause_engaged
try:
    sys.modules.update(module_names)
    controlled_hermes.pause_engaged = lambda: False
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        rc = controlled_hermes.main([
            "-z", "test research", "--provider", "custom:byteplus-coding",
            "-m", "ark-code-latest"])
finally:
    controlled_hermes.pause_engaged = prior_pause
    for name, prior in prior_modules.items():
        if prior is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = prior

checks.update({
    "controlled research returns original nonzero code": rc == 23,
    "controlled research never starts finalization after failure":
        controller.research_calls == 1 and controller.finalization_calls == 0 and not finalizer_calls,
    "controlled research preserves stdout evidence": "research stdout evidence" in stdout.getvalue(),
    "controlled research preserves stderr evidence": "research stderr evidence" in stderr.getvalue(),
    "synthesis infra verdict stays infrastructure failure":
        workflow._status_for_critic_verdict("infra_failed") == "infra_failed",
    "synthesis content rejection stays failed":
        workflow._status_for_critic_verdict("fail") == "failed",
    "synthesis pass stays done": workflow._status_for_critic_verdict("pass") == "done",
})

# --- worker-launch exception closes the row as infra_failed (2026-09-02) -----
# 2026-09-02 incident: an aborted mission launch left task 110 'running' with a
# dead process; only lease expiry cleaned it up. A worker-launch exception must
# close the row honestly at failure time instead of waiting for the lease.
# Hermetic: fakes every collaborator; no live state, provider, or network.

import sqlite3
import tempfile

_fake_ledger_rows = {}


def _make_fake_task_runner_env():
    def finish_task(task_id, *, artifacts, status="done", critic_notes=None,
                    append_note=False, **kwargs):
        row = _fake_ledger_rows.setdefault(task_id, {})
        row["status"] = status
        row["critic_notes"] = critic_notes

    def start_task(task_id, model):
        row = _fake_ledger_rows.setdefault(task_id, {})
        row["status"] = "running"
        row["model"] = model

    fake_ledger = types.ModuleType("ledger")
    fake_ledger.LEDGER_DB = "unused"
    fake_ledger.finish_task = finish_task
    fake_ledger.start_task = start_task

    fake_policy = types.ModuleType("policy")
    fake_policy.token_budget_breached = lambda: False
    fake_policy.estimated_tokens_for = lambda tid, mid: 1
    fake_policy.budget_insufficient_for = lambda est: False
    fake_policy.compliance_prompt_block = lambda: ""

    fake_prompts = types.ModuleType("prompts")
    fake_prompts.mission_objective = lambda mission: "objective"
    fake_prompts.deliverable_requirements = lambda mission: "requirements"
    fake_prompts.task_scope_note = lambda spec, mission: "scope"

    fake_scheduler = types.ModuleType("scheduler")
    fake_scheduler.week_key = lambda: "2026-W36"
    fake_scheduler.mission_workspace = lambda mid: "shopify"
    fake_scheduler.is_first_run_for_mission = lambda mid: False

    fake_execution = types.ModuleType("execution")
    def _crash(*args, **kwargs):
        raise RuntimeError("simulated worker launch crash")
    fake_execution.worker_with_failover = _crash

    fake_integrity = types.ModuleType("integrity")
    @contextlib.contextmanager
    def _null_guard(label):
        yield
    fake_integrity.DatabaseMutationGuard = _null_guard
    fake_integrity.DatabaseMutationViolation = type("DatabaseMutationViolation", (RuntimeError,), {})
    fake_integrity.fs_integrity_snapshot = lambda: None
    fake_integrity.fs_integrity_check = lambda snap, context=None: None

    fake_promote = types.ModuleType("promote")
    fake_promote.active_skills_for = lambda mid: ""
    fake_promote.SKILLS = ROOT / "workspace"
    fake_promote.MAX_INJECTED_CHARS = 2000

    fake_runtime = types.ModuleType("runtime_context")
    fake_runtime.ROOT = ROOT
    fake_runtime.RUNS = ROOT / "runs"
    fake_runtime.log = lambda msg: None

    fake_trajectory = types.ModuleType("trajectory")
    fake_trajectory.active = lambda: None

    return {
        "ledger": fake_ledger,
        "policy": fake_policy,
        "prompts": fake_prompts,
        "scheduler": fake_scheduler,
        "execution": fake_execution,
        "integrity": fake_integrity,
        "promote": fake_promote,
        "runtime_context": fake_runtime,
        "trajectory": fake_trajectory,
    }


_fake_env = _make_fake_task_runner_env()
_prior_env = {name: sys.modules.get(name) for name in _fake_env}
try:
    sys.modules.update(_fake_env)
    import task_runner
    _context = task_runner._TaskContext(
        tid=9001,
        mission={"id": "001-shopify-competitor-intel"},
        roles={"worker": {"provider": "fake", "model": "fake-model"}},
        row={"spec": "[cohort-test][M9] simulated", "critic_verdict": None,
             "critic_notes": None, "task_id": 9001},
    )
    _launch_status = task_runner._run_research_task(_context)
finally:
    for _name, _prior in _prior_env.items():
        if _prior is None:
            sys.modules.pop(_name, None)
        else:
            sys.modules[_name] = _prior

_row_9001 = _fake_ledger_rows.get(9001, {})
checks.update({
    "worker-launch exception returns infra_failed":
        _launch_status == "infra_failed",
    "worker-launch exception closes the ledger row honestly":
        _row_9001.get("status") == "infra_failed",
    "worker-launch failure note is recorded":
        "worker launch failure" in (_row_9001.get("critic_notes") or ""),
    "row never lingers as running":
        _row_9001.get("status") != "running",
})

# --- dead owner process recovers immediately before lease expiry (2026-09-02) --
# F101's first fix still waited out the lease. Once owner identity is present on
# the row, reconcile_interrupted_tasks() should recover immediately when the
# recorded PID is positively gone or reused.

import ledger as _ledger_mod
import runlock as _runlock_mod
import scheduler as _scheduler_mod

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
    _db = Path(raw) / "ledger.db"
    with sqlite3.connect(_db) as _conn:
        _conn.execute("""CREATE TABLE tasks (
            task_id INTEGER PRIMARY KEY,
            status TEXT,
            attempt_count INTEGER,
            critic_notes TEXT,
            lease_expires_at TEXT,
            owner_pid INTEGER,
            owner_process_start_id TEXT
        )""")
        _conn.execute(
            "INSERT INTO tasks (task_id, status, attempt_count, critic_notes, "
            "lease_expires_at, owner_pid, owner_process_start_id) "
            "VALUES (9101, 'running', 0, '', datetime('now', '+30 minutes'), 999999, 'dead-owner')"
        )
    _prior_db = _ledger_mod.LEDGER_DB
    _prior_identity = _runlock_mod._process_start_identity
    try:
        _ledger_mod.LEDGER_DB = _db
        _runlock_mod._process_start_identity = lambda pid: None if pid == 999999 else "live"
        _recovered = _scheduler_mod.reconcile_interrupted_tasks()
        with sqlite3.connect(_db) as _conn:
            _row = _conn.execute(
                "SELECT status, attempt_count, critic_notes FROM tasks WHERE task_id=9101"
            ).fetchone()
    finally:
        _ledger_mod.LEDGER_DB = _prior_db
        _runlock_mod._process_start_identity = _prior_identity

checks.update({
    "dead owner process triggers immediate recovery":
        _recovered == 1,
    "immediate recovery demotes row to interrupted":
        _row[0] == "interrupted",
    "immediate recovery increments attempt_count":
        _row[1] == 1,
    "immediate recovery records dead-owner note":
        "dead owner process" in (_row[2] or ""),
})

# --- synthesis mission accounting writes and reconciles (2026-09-02) ---------
# The first cohort review flagged synthesis missions as producing no
# mission.usage.json. d88286c added the build_mission_usage call to
# run_synthesis (workflow.py:201), but NOTHING proves it: no regression, no
# live synthesis run since. Task 113 (a synthesis seed) must not be the first
# place we find out. Hermetic proof: run_synthesis with patched collaborators
# (workflow.py's documented seam -- module-qualified calls resolve at call
# time) must WRITE task<TID>_mission.usage.json and reconcile exactly.
# Synthesis is tool-free: executed=rejected=0 is the correct shape, but
# worker+critic tokens must still merge across roles.

import workflow as workflow_module

_synth_patches = []  # (module_obj, prior_dict)


def _patch(mod, **attrs):
    prior = {k: getattr(mod, k) for k in attrs}
    for k, v in attrs.items():
        setattr(mod, k, v)
    _synth_patches.append((mod, prior))


def _fake_synth_failover(prompt, cfg, log_prefix="", usage_out=None):
    if usage_out is not None:
        usage_out.update({"input_tokens": 3000, "output_tokens": 700,
                          "api_calls": 1})
    return ("synthesis deliverable text " * 40, cfg, False)


def _fake_critic_pass(row, out, roles, baseline, scope_note="", usage_out=None,
                      worker_config=None):
    if usage_out is not None:
        usage_out.update({"input_tokens": 400, "output_tokens": 120,
                          "api_calls": 1, "total_tokens": 520})
    return "pass", "VERDICT: PASS (fake critic)"


_synth_finish_calls = []

import prompts as _prompts_mod
import execution as _execution_mod
import evaluation as _evaluation_mod
import policy as _policy_mod
import integrity as _integrity_mod
import ledger as _ledger_mod

_patch(_prompts_mod, build_brief_block=lambda briefs: "briefs",
       _recent_fact_lines=lambda: "facts",
       mission_objective=lambda mission: "objective",
       deliverable_requirements=lambda mission: "",
       task_scope_note=lambda spec, mission: "scope")
_patch(_execution_mod, synthesis_with_failover=_fake_synth_failover,
       _strip_tool_chatter=lambda out: out)
_patch(_evaluation_mod, run_critic=_fake_critic_pass)
_patch(_policy_mod, token_budget_breached=lambda: False)
_patch(_integrity_mod, escalate=lambda msg, **kwargs: None)
_patch(_ledger_mod, finish_task=lambda task_id, **kw: _synth_finish_calls.append(kw),
       update_model_used=lambda tid, model: None,
       add_lesson=lambda tid, lesson, kind: None)

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
    import json as _json2
    # rc.ROOT is used by relative_to for the artifact path, so out_dir must
    # live under a repointed ROOT; rc.RUNS must land in the same temp root.
    tmp_root = Path(raw)
    tmp_out = tmp_root / "workspace"
    tmp_out.mkdir()
    _prior_runs = workflow_module.rc.RUNS
    _prior_root = workflow_module.rc.ROOT
    workflow_module.rc.RUNS = tmp_root / "runs"
    workflow_module.rc.RUNS.mkdir()
    workflow_module.rc.ROOT = tmp_root
    _prior_eval_runs = _evaluation_mod.RUNS
    _evaluation_mod.RUNS = workflow_module.rc.RUNS  # build_mission_usage writes here
    try:
        _synth_status = workflow_module.run_synthesis(
            9002, {"spec": "[2026-W36][seed 4] Synthesis: build the changes brief",
                   "tokens_in": 0, "tokens_out": 0, "critic_verdict": None},
            mission={"id": "001-shopify-competitor-intel"},
            roles={"worker": {"provider": "fake", "model": "fake-model"},
                   "critic": {"provider": "fake", "model": "fake-critic"}},
            out_dir=tmp_out, wk="2026-W36", baseline=True, baseline_note="")
        # read artifacts BEFORE the temp directory is cleaned up
        _mu_path = workflow_module.rc.RUNS / "task9002_mission.usage.json"
        _mu_written = _mu_path.is_file()
        _mu = _json2.loads(_mu_path.read_text(encoding="utf-8")) if _mu_written else {}
    finally:
        workflow_module.rc.RUNS = _prior_runs
        workflow_module.rc.ROOT = _prior_root
        _evaluation_mod.RUNS = _prior_eval_runs
        for mod, prior in reversed(_synth_patches):
            for k, v in prior.items():
                setattr(mod, k, v)
        _synth_patches.clear()

_last_finish = _synth_finish_calls[-1] if _synth_finish_calls else {}

checks.update({
    "synthesis run returns done": _synth_status == "done",
    "synthesis writes mission.usage.json": _mu_written,
    "synthesis tokens reconcile exactly":
        _mu.get("total_tokens") == 3000 + 700 + 400 + 120,
    "synthesis tokens equal worker+critic splits":
        _mu.get("input_tokens") == 3400 and _mu.get("output_tokens") == 820,
    "synthesis api calls reconcile": _mu.get("api_calls") == 2,
    "synthesis is tool-free (zero retrieval)":
        _mu.get("executed_agent_retrieval_calls") == 0
        and _mu.get("rejected_agent_retrieval_attempts") == 0,
    "synthesis finish_task received merged tokens":
        _last_finish.get("tokens_in") == 3400 and _last_finish.get("tokens_out") == 820,
    "synthesis finish_task status done": _last_finish.get("status") == "done",
})

failed = []
for name, ok in checks.items():
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        failed.append(name)
if failed:
    raise SystemExit("critical-path regression failures: " + ", ".join(failed))

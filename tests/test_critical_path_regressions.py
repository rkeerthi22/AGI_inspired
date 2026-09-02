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

failed = []
for name, ok in checks.items():
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        failed.append(name)
if failed:
    raise SystemExit("critical-path regression failures: " + ", ".join(failed))

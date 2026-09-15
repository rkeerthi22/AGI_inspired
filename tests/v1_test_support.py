"""Isolated real-runner fixture. No providers, production DB writes or ESTOP edits."""
from contextlib import ExitStack, contextmanager, nullcontext
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
import task_runner as runner


@contextmanager
def runner_fixture(worker):
    with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
        root = Path(td)
        runs = root / "runs"
        runs.mkdir()
        for obj, name, value in (
            (runner.rc, "ROOT", root), (runner.rc, "RUNS", runs),
            (runner.rc, "log", lambda *_: None),
        ):
            stack.enter_context(patch.object(obj, name, value))
        for obj, name, value in (
            (runner.trajectory, "active", None),
            (runner.policy, "token_budget_breached", False),
            (runner.policy, "estimated_tokens_for", 1),
            (runner.policy, "budget_insufficient_for", False),
            (runner.policy, "deny_list_scan", []),
            (runner.policy, "compliance_prompt_block", ""),
            (runner.promote, "active_skills_for", ""),
            (runner.scheduler, "week_key", "2026-W38"),
            (runner.scheduler, "mission_workspace", "fixture"),
            (runner.scheduler, "is_first_run_for_mission", False),
            (runner.prompts, "mission_objective", "Fixture research objective"),
            (runner.prompts, "deliverable_requirements", ""),
            (runner.prompts, "task_scope_note", "Fixture scope"),
            (runner.integrity, "fs_integrity_snapshot", {}),
            (runner.integrity, "fs_integrity_check", None),
            (runner.integrity, "DatabaseMutationGuard", nullcontext()),
            (runner.ledger, "start_task", None),
            (runner.ledger, "finish_task", None),
        ):
            stack.enter_context(patch.object(obj, name, return_value=value))
        stack.enter_context(patch("prediction_machine.integrations.batch_runner_hook.before_task_runs"))
        stack.enter_context(patch("prediction_machine.integrations.batch_runner_hook.after_task_completes"))
        stack.enter_context(patch("egress_policy.snapshot_egress_policy", return_value={"policy_digest": "fixture", "allowlisted_hosts": []}))
        stack.enter_context(patch.object(runner.execution, "worker_with_failover", side_effect=worker))
        yield root, runs, stack


def context(tid=1, spec="Fixture research"):
    cfg = {"provider": "fixture", "model": "fixture"}
    return runner._TaskContext(tid, {"id": "fixture"},
        {"worker": cfg, "critic": cfg, "manager": cfg},
        {"task_id": tid, "spec": spec, "pass_criteria": "", "attempt_count": 0})


def worker_result(text):
    return text, {"input_tokens": 10, "output_tokens": 20, "api_calls": 1}, {"provider": "fixture", "model": "fixture"}, False


def source(url="https://alive.example/review", status=200):
    return {"url": url, "http_status": status, "classification": "OK" if status == 200 else "DEAD",
            "reachable_on_host": status == 200, "worker_policy_permitted": True,
            "broker_attempt_verified": False, "error": None}

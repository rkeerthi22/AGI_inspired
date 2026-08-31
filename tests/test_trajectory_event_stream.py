"""Model-free unit regression suite for P0 Unified Trajectory / Event Stream."""
import json
import os
import sys
import tempfile
import atexit
import hashlib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import trajectory
import task_runner
import evaluation
import execution
import retrieval_progress
import runtime_context as rc

REAL_RUNS = ROOT / "runs"


def runs_snapshot(path: Path) -> dict[str, str]:
    return {
        str(item.relative_to(path)): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in path.rglob("*") if item.is_file()
    }


REAL_RUNS_BEFORE = runs_snapshot(REAL_RUNS)
_suite_tmp = tempfile.TemporaryDirectory(prefix="trajectory_test_")
TEST_RUNS = Path(_suite_tmp.name) / "runs"
TEST_RUNS.mkdir()
_original_runs = rc.RUNS
_original_evaluation_runs = evaluation.RUNS
_original_policy_state = evaluation.policy.STATE_PATH
rc.RUNS = TEST_RUNS
evaluation.RUNS = TEST_RUNS
evaluation.policy.STATE_PATH = TEST_RUNS / "policy_state.json"


def _cleanup_test_runs() -> None:
    rc.RUNS = _original_runs
    evaluation.RUNS = _original_evaluation_runs
    evaluation.policy.STATE_PATH = _original_policy_state
    _suite_tmp.cleanup()


atexit.register(_cleanup_test_runs)

fails = []


def check(name: str, condition: bool) -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        fails.append(name)


# ── Test 1: Basic Writer & Monotonic Sequences ─────────────────────────────

with tempfile.TemporaryDirectory() as td:
    traj_path = Path(td) / "task101.trajectory.jsonl"
    writer = trajectory.TrajectoryWriter(traj_path, task_id=101, mission_id="001-shopify")

    e1 = writer.task_started(spec="PromptHero MAU research", worker_model="ark-code-latest",
                             worker_provider="byteplus_coding")
    e2 = writer.provider_selected("byteplus_coding", "ark-code-latest", rung=1, total_rungs=4)
    e3 = writer.tool_call_finished("web_search", "search", call_index=1, novel=True, urls_found=5)
    e4 = writer.strategy_transition("search", "direct_fetch", reason="budget reached")
    e5 = writer.citecheck_completed(total_urls=5, dead_urls=0, dead_frac=0.0, hard_fail=False)
    e6 = writer.critic_evaluated("pass", model="ark-code-latest", provider="byteplus_coding")
    e7 = writer.facts_extracted(count=3, model="ark-code-latest", provider="byteplus_coding")
    e8 = writer.task_completed("pass", "done", facts_extracted=3)

    lines = [json.loads(line) for line in traj_path.read_text(encoding="utf-8").splitlines() if line]

    check("trajectory writes exactly 8 events", len(lines) == 8)
    check("sequence numbers strictly monotonic 1..8", [l["sequence"] for l in lines] == list(range(1, 9)))
    check("deterministic event IDs generated",
          [l["event_id"] for l in lines] == [f"evt-101-{i:04d}" for i in range(1, 9)])
    check("schema_version is 1 across all events", all(l["schema_version"] == 1 for l in lines))
    check("task_id and mission_id preserved",
          all(l["task_id"] == 101 and l["mission_id"] == "001-shopify" for l in lines))
    check("timestamps are valid ISO UTC format",
          all(datetime.fromisoformat(l["timestamp"]) for l in lines))


# ── Test 1b: Reopen/append resumes sequence without overwriting ──────────

with tempfile.TemporaryDirectory() as td:
    traj_path = Path(td) / "task103.trajectory.jsonl"
    first = trajectory.TrajectoryWriter(traj_path, task_id=103, mission_id="m-reopen")
    first.task_started("spec", "model", "provider")
    first.provider_selected("provider", "model")
    first.tool_call_finished("web_search", "search", 1, True, 2)
    prefix = traj_path.read_bytes()

    reopened = trajectory.TrajectoryWriter(traj_path, task_id=103, mission_id="m-reopen")
    e4 = reopened.critic_evaluated("pass")
    e5 = reopened.task_completed("pass", "done")
    lines = [json.loads(line) for line in traj_path.read_text(encoding="utf-8").splitlines()]
    sequences = [line["sequence"] for line in lines]
    event_ids = [line["event_id"] for line in lines]

    check("reopen preserves existing bytes", traj_path.read_bytes().startswith(prefix))
    check("reopen resumes at max existing sequence + 1", [e4["sequence"], e5["sequence"]] == [4, 5])
    check("reopened trajectory remains strictly monotonic", sequences == [1, 2, 3, 4, 5])
    check("reopened trajectory event IDs remain unique", len(event_ids) == len(set(event_ids)))


# ── Test 1c: Truncated trailing record is preserved and safely separated ─

with tempfile.TemporaryDirectory() as td:
    traj_path = Path(td) / "task104.trajectory.jsonl"
    first = trajectory.TrajectoryWriter(traj_path, task_id=104, mission_id="m-truncated")
    first.task_started("spec", "model", "provider")
    first.provider_selected("provider", "model")
    with traj_path.open("ab") as handle:
        handle.write(b'{"sequence": 999, "truncated"')
    prefix = traj_path.read_bytes()

    reopened = trajectory.TrajectoryWriter(traj_path, task_id=104, mission_id="m-truncated")
    appended = reopened.task_failed("expected test failure")
    raw_lines = traj_path.read_text(encoding="utf-8").splitlines()
    valid = []
    for raw_line in raw_lines:
        try:
            valid.append(json.loads(raw_line))
        except json.JSONDecodeError:
            pass

    check("truncated tail bytes are not overwritten", traj_path.read_bytes().startswith(prefix))
    check("truncated tail does not inflate resumed sequence", appended["sequence"] == 3)
    check("append after truncated tail is a separate valid JSONL record", valid[-1]["event_id"] == "evt-104-0003")


# ── Test 2: Secret Redaction ───────────────────────────────────────────────

with tempfile.TemporaryDirectory() as td:
    traj_path = Path(td) / "task102.trajectory.jsonl"
    writer = trajectory.TrajectoryWriter(traj_path, task_id=102, mission_id="001-shopify")

    secret_key = "12345678901234567890123456789012"
    raw_payload = {
        "ARK_API_KEY": secret_key,
        "auth_header": f"Bearer {secret_key}",
        "nested": {
            "OPENAI_API_KEY": "sk-" + secret_key,
            "normal_field": f"api_key: {secret_key}",
            "safe_url": "https://example.com/test",
        }
    }
    writer.emit("execution", "test_secret_event", payload=raw_payload)
    event_data = json.loads(traj_path.read_text(encoding="utf-8").strip())
    payload = event_data["payload"]

    check("sensitive env keys stripped from dict", "ARK_API_KEY" not in payload)
    check("nested sensitive env keys stripped", "OPENAI_API_KEY" not in payload.get("nested", {}))
    check("Bearer token string redacted", "[REDACTED]" in payload.get("auth_header", ""))
    check("nested api_key string redacted", "[REDACTED]" in payload.get("nested", {}).get("normal_field", ""))
    check("raw secret value does not appear anywhere in trajectory JSON", secret_key not in traj_path.read_text(encoding="utf-8"))


# ── Test 3: Active Writer Lifecycle & Leakage Prevention ──────────────────

trajectory.end()
check("initial active writer is None", trajectory.active() is None)

w1 = trajectory.begin(201, "mission-a")
check("active writer matches begin() task 201", trajectory.active() is w1 and w1.task_id == 201)

w2 = trajectory.begin(202, "mission-b")
check("begin() replaces active writer with task 202", trajectory.active() is w2 and w2.task_id == 202)

trajectory.end()
check("end() resets active writer to None", trajectory.active() is None)
check("trajectory.begin uses isolated test RUNS", w1.path.parent == TEST_RUNS and w2.path.parent == TEST_RUNS)


# ── Test 4: Regression — task_runner.run_task() Try/Finally Cleanup ───────

with tempfile.TemporaryDirectory() as td:
    trajectory.end()
    # Simulate an unhandled exception inside task execution
    original_prepare = task_runner._prepare_task_input

    def fail_prepare(*args, **kwargs):
        raise RuntimeError("simulated crash before task finish")

    task_runner._prepare_task_input = fail_prepare
    task_runner._load_task = lambda tid: {"spec": "test spec"}

    crashed = False
    try:
        task_runner.run_task(301, {"id": "m-fail"}, {"worker": {"model": "m", "provider": "p"}})
    except RuntimeError as exc:
        crashed = str(exc) == "simulated crash before task finish"
    finally:
        task_runner._prepare_task_input = original_prepare

    check("run_task raised expected simulated exception", crashed)
    check("run_task guaranteed trajectory.end() cleanup on exception", trajectory.active() is None)


# ── Test 5: Regression — evaluation.run_critic() Reachable Trajectory Events ─

with tempfile.TemporaryDirectory() as td:
    traj_path = Path(td) / "task401.trajectory.jsonl"
    writer = trajectory.TrajectoryWriter(traj_path, task_id=401, mission_id="001-shopify")
    trajectory._active = writer

    # Mock citecheck and ollama_chat for critic PASS
    old_verify = evaluation.citecheck.verify
    old_chat = evaluation.execution.ollama_chat
    try:
        evaluation.citecheck.verify = lambda out: [
            {"url": "https://example.com", "reachable": True, "literal_found": True, "literal": "Research"}
        ]
        evaluation.execution.ollama_chat = lambda model, prompt, **kwargs: (
            "VERDICT: PASS\nThe deliverable is thorough and accurately sourced."
        )

        row = {"task_id": 401, "pass_criteria": "pass if thorough"}
        roles = {"critic": {"model": "ark-code-latest", "provider": "byteplus_coding"}}
        verdict, text = evaluation.run_critic(row, "Research text with https://example.com", roles, baseline=False)

        lines = [json.loads(line) for line in traj_path.read_text(encoding="utf-8").splitlines() if line]
        event_types = [l["event_type"] for l in lines]

        check("critic verdict returned pass", verdict == "pass")
        check("citecheck_completed event emitted", "citecheck_completed" in event_types)
        check("critic_evaluated event reachable and emitted for PASS", "critic_evaluated" in event_types)
        critic_evt = next(l for l in lines if l["event_type"] == "critic_evaluated")
        check("critic_evaluated payload contains verdict pass", critic_evt["payload"]["verdict"] == "pass")
    finally:
        evaluation.citecheck.verify = old_verify
        evaluation.execution.ollama_chat = old_chat
        trajectory.end()


# ── Test 6: Failover Trajectory Emission in execution.py ──────────────────

with tempfile.TemporaryDirectory() as td:
    traj_path = Path(td) / "task501.trajectory.jsonl"
    writer = trajectory.TrajectoryWriter(traj_path, task_id=501, mission_id="001-shopify")
    trajectory._active = writer

    old_worker = execution.hermes_worker
    old_fallback = execution.load_fallback_chain
    try:
        execution.load_fallback_chain = lambda: [
            {"provider": "byteplus_coding", "model": "ark-code-latest", "quota_group": "byteplus"},
            {"provider": "ollama", "model": "glm-5.2:cloud", "quota_group": "ollama-cloud"},
        ]
        call_count = {"n": 0}

        def mock_worker(prompt, cfg, attempt_path, timeout=900):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "HTTP 429: RateLimitExceeded", {"process_error": "429"}
            return "Success from fallback model", {}

        execution.hermes_worker = mock_worker

        out, usage, cfg_used, exhausted = execution.worker_with_failover(
            "test prompt",
            {"provider": "byteplus_coding", "model": "ark-code-latest", "quota_group": "byteplus"},
            Path(td) / "usage.json",
            log_prefix="task 501",
        )

        lines = [json.loads(line) for line in traj_path.read_text(encoding="utf-8").splitlines() if line]
        event_types = [l["event_type"] for l in lines]

        check("failover returned success on second model", not exhausted and cfg_used["model"] == "glm-5.2:cloud")
        check("provider_selected emitted for rung 1", "provider_selected" in event_types)
        check("provider_failed emitted with quota_exhausted for rung 1",
              any(l["event_type"] == "provider_failed" and l["payload"]["reason"] == "quota_exhausted" for l in lines))
        check("failover_attempted emitted for rung 2", "failover_attempted" in event_types)
    finally:
        execution.hermes_worker = old_worker
        execution.load_fallback_chain = old_fallback
        trajectory.end()


# ── Test 7: Retrieval Progress Controller Trajectory Emission ─────────────

with tempfile.TemporaryDirectory() as td:
    traj_path = Path(td) / "task601.trajectory.jsonl"
    writer = trajectory.TrajectoryWriter(traj_path, task_id=601, mission_id="001-shopify")
    controller = retrieval_progress.RetrievalProgressController(
        audit_path=Path(td) / "audit.jsonl",
        trajectory_writer=writer,
    )

    controller.before("web_search", {"query": "PromptHero pricing"})
    controller.after("web_search", {"query": "PromptHero pricing"},
                     "Found https://prompthero.com/plans with extensive details and pricing tiers for 2026.",
                     failed=False)
    controller.finalization_started()
    controller.finalization_finished(success=True)

    lines = [json.loads(line) for line in traj_path.read_text(encoding="utf-8").splitlines() if line]
    event_types = [l["event_type"] for l in lines]

    check("tool_call_finished emitted from controller", "tool_call_finished" in event_types)
    check("finalization_started emitted from controller", "finalization_started" in event_types)
    check("finalization_finished emitted from controller", "finalization_finished" in event_types)


# ── Final Summary ─────────────────────────────────────────────────────────

check("real runs artifacts remain byte-for-byte unchanged",
      runs_snapshot(REAL_RUNS) == REAL_RUNS_BEFORE)

if fails:
    raise SystemExit(f"FAILED {len(fails)} trajectory regression assertions: {fails}")

print(f"\nAll trajectory event stream assertions passed successfully.")

"""F60: run_task extraction contract and zero-live-state characterization."""
import ast
import hashlib
import subprocess
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import batch_runner as br  # noqa: E402
import task_runner as tr  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append(name)
    print(f"  [{'PASS' if got == want else 'FAIL'}] {name}: got={got!r} want={want!r}")


def snapshot():
    paths = [ROOT / "ledger" / "ledger.db", ROOT / "workspace" / "ESCALATIONS.md",
             ROOT / "config" / "policy.yaml"]
    return {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                        text=True).strip(),
        "status": subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT,
                                          text=True),
        "files": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in paths if p.is_file()},
    }


before = snapshot()
check("compatibility identity", br.run_task is tr.run_task, True)

tree = ast.parse((ROOT / "orchestrator" / "task_runner.py").read_text(encoding="utf-8"))
imports = {n.names[0].name for n in tree.body if isinstance(n, ast.Import)}
check("task_runner does not import batch_runner", "batch_runner" in imports, False)
check("task_runner does not import CLI code", "argparse" in imports, False)
for helper in ("_load_task", "_prepare_task_input", "_run_synthesis_task",
               "_run_research_task", "_record_outcome", "run_task"):
    check(f"owns {helper}", any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                                and n.name == helper for n in tree.body), True)
workflow_tree = ast.parse((ROOT / "orchestrator" / "workflow.py").read_text(encoding="utf-8"))
workflow_imports = {alias.name for node in ast.walk(workflow_tree)
                    if isinstance(node, ast.Import) for alias in node.names}
check("workflow independent of task_runner", "task_runner" in workflow_imports, False)
check("workflow independent of batch_runner", "batch_runner" in workflow_imports, False)

# Canonical-owner patching: characterize preparation, synthesis routing, gates,
# raw-before-classification, deliverable-before-critic, accumulation, facts, and hooks.
saved = {}


def patch(module, name, value):
    saved[(module, name)] = getattr(module, name)
    setattr(module, name, value)


events = []
# Keep optional prediction hooks deterministic and offline while pinning their timing.
hook = types.ModuleType("prediction_machine.integrations.batch_runner_hook")
hook.before_task_runs = lambda *a: events.append(("predict_before",))
hook.after_task_completes = lambda *a: events.append(("predict_after",))
sys.modules["prediction_machine.integrations.batch_runner_hook"] = hook
row = {"spec": "Research Acme pricing", "critic_verdict": "fail",
       "critic_notes": "include the missing comparison", "tokens_in": 7,
       "tokens_out": 11}
mission = {"id": "f60", "objective": "market map", "frontmatter": {}}
roles = {"worker": {"provider": "p", "model": "m"},
         "manager": {"model": "judge"}}

with tempfile.TemporaryDirectory(dir=ROOT / "workspace") as raw:
    temp_root = Path(raw)
    (temp_root / "runs").mkdir()
    patch(tr, "_load_task", lambda tid: dict(row))
    patch(tr.rc, "ROOT", temp_root)
    patch(tr.rc, "RUNS", temp_root / "runs")
    patch(tr.rc, "log", lambda msg: events.append(("log", msg)))
    patch(tr.scheduler, "week_key", lambda: "2099-W01")
    patch(tr.scheduler, "mission_workspace", lambda mid: "mission")
    patch(tr.scheduler, "is_first_run_for_mission", lambda mid: True)
    patch(tr.scheduler, "accumulated_tokens",
          lambda usage, old_in, old_out: (old_in + usage["tokens_in"],
                                          old_out + usage["tokens_out"]))
    patch(tr.prompts, "mission_objective", lambda m: "OBJECTIVE")
    patch(tr.prompts, "deliverable_requirements", lambda m: "REQUIREMENTS")
    patch(tr.prompts, "task_scope_note", lambda spec, m: "SCOPE")
    patch(tr.promote, "active_skills_for", lambda mid: "SKILL")
    patch(tr.promote, "SKILLS", temp_root / "skills")
    patch(tr.policy, "compliance_prompt_block", lambda: "COMPLIANCE")
    patch(tr.policy, "token_budget_breached", lambda: False)
    patch(tr.policy, "estimated_tokens_for", lambda tid, mid: 12)
    patch(tr.policy, "budget_insufficient_for", lambda est: False)
    patch(tr.policy, "deny_list_scan", lambda out: [])
    patch(tr.integrity, "db_integrity_snapshot", lambda: "db")
    patch(tr.integrity, "fs_integrity_snapshot", lambda: "fs")
    patch(tr.integrity, "db_integrity_check", lambda *a, **k: events.append(("dbcheck",)))
    patch(tr.integrity, "fs_integrity_check", lambda *a, **k: events.append(("fscheck",)))
    patch(tr.integrity, "escalate", lambda *a, **k: events.append(("escalate",)))
    patch(tr.ledger, "start_task", lambda *a, **k: events.append(("start",)))
    patch(tr.ledger, "update_model_used", lambda *a, **k: events.append(("model",)))
    patch(tr.ledger, "finish_task", lambda *a, **k: events.append(("finish", k)))
    patch(tr.ledger, "add_lesson", lambda *a, **k: events.append(("lesson",)))
    output = "A" * 260

    def worker(prompt, cfg, usage_path, log_prefix):
        events.append(("worker", prompt, usage_path))
        return output, {"tokens_in": 3, "tokens_out": 5}, cfg, False

    patch(tr.execution, "worker_with_failover", worker)
    patch(tr.execution, "_strip_tool_chatter",
          lambda out: events.append(("strip",)) or out)
    patch(tr.execution, "worker_failed", lambda out, usage: events.append(("classify",)) or False)

    def critic(*args, **kwargs):
        dest = next((temp_root / "workspace" / "mission").glob("*.md"))
        events.append(("critic", dest.is_file()))
        return "pass", "ok"

    patch(tr.evaluation, "run_critic", critic)
    patch(tr.evaluation, "build_mission_usage",
          lambda tid, worker_usage, critic_usage: worker_usage)
    patch(tr.evaluation, "extract_facts", lambda *a: events.append(("facts",)) or 2)
    patch(tr.evaluation, "seed_is_synthesis", lambda spec: False)
    patch(tr.workflow, "_check_repeated_failure", lambda mid: events.append(("repeat",)))

    status = tr.run_task(60, mission, roles)
    prompt = next(e[1] for e in events if e[0] == "worker")
    check("successful status", status, "done")
    for text in ("OBJECTIVE", "REQUIREMENTS", "SCOPE", "BASELINE RUN", "PREVIOUS ATTEMPT",
                 "SKILL", "COMPLIANCE"):
        check(f"prompt includes {text}", text in prompt, True)
    raw = temp_root / "runs" / "task60_worker_raw.txt"
    check("raw output persisted", raw.read_text(encoding="utf-8"), output)
    check("raw persisted before classification", events.index(("strip",)) < events.index(("classify",)), True)
    check("deliverable exists before critic", ("critic", True) in events, True)
    finish = next(e[1] for e in events if e[0] == "finish")
    check("tokens accumulated", (finish["tokens_in"], finish["tokens_out"]), (10, 16))
    check("passed output writes facts", ("facts",) in events, True)

    # Both budget gates are pre-start and preserve their distinct public statuses.
    patch(tr.policy, "token_budget_breached", lambda: True)
    events.clear()
    check("hard budget gate", tr.run_task(60, mission, roles), "quota_wait")
    check("hard gate before start", any(e[0] == "start" for e in events), False)
    patch(tr.policy, "token_budget_breached", lambda: False)
    patch(tr.policy, "budget_insufficient_for", lambda est: True)
    events.clear()
    check("admission gate", tr.run_task(60, mission, roles), "budget_skip")
    check("admission gate before start", any(e[0] == "start" for e in events), False)

for (module, name), value in reversed(list(saved.items())):
    setattr(module, name, value)

after = snapshot()
check("HEAD unchanged", after["head"], before["head"])
check("status unchanged", after["status"], before["status"])
check("protected snapshots unchanged", after["files"], before["files"])

if fails:
    print("\nFAILURES:", *fails, sep="\n  - ")
    raise SystemExit(1)
print("\nF60 PASS — canonical task runner behavior and zero live-state drift verified")

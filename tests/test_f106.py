"""F106: worker self-verifies citations via existing web_extract (weak-AI efficiency).

Q2#3 -- move citecheck from a purely post-hoc gate toward something the worker
does DURING research. The mechanical hard gate (citecheck.verify) and the F103
retry loop that feeds dead-URL evidence back to the worker both remain POST-HOC.
What this adds is the only in-scope in-loop lever under ESTOP + the closed F63
controller: a PROMPT INSTRUCTION telling the agentic worker -- which already has
web_search / web_extract / browser tools -- to confirm every cited URL was
fetched to a live page THIS run before finalizing. It attacks the M5 / task-116
failure mode (4/8 cited URLs unreachable) directly: a cheap model satisfies
'every fact needs a source URL' by pasting any URL, reachable or not; 'every URL
must be one you personally opened to a working page' is harder to satisfy with a
dead link.

Strictly additive by construction: a model that ignores the instruction behaves
exactly as before (the post-hoc gate still catches dead URLs); one that complies
catches them BEFORE the gate, saving a failed retry. No new Hermes tool is
registered -- the closed F63 controller / Hermes-internals are untouched -- so the
worker reuses the existing truthful web_extract tool (F66). The standalone
citecheck.verify_url primitive was deliberately NOT added: it had no production
caller (the post-hoc gate already calls _fetch_one directly, and the worker cannot
call Python primitives), so it would be dead code.

Cannot be live-verified under ESTOP. This test pins the load-bearing, verifiable
piece: the instruction is present in the built worker prompt, it names the
available tool (web_extract) and the post-hoc gate that backs it, and it is
correctly scoped to the generic retrieval profile -- web_extract and web_search
are FORBIDDEN in the dynamic_browser profile, so the generic self-check must NOT
appear there (the browser profile's citation discipline is 'cite from the
rendered canonical page', covered by its own blocks).

Pattern: stub execution.worker_with_failover to capture the prompt, exactly as
test_f60 does; assert substrings present (generic) / absent (dynamic).
"""
import subprocess
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.path.insert(0, str(ROOT / "tests"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import task_runner as tr  # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got={got!r} want={want!r}")


def snapshot():
    return {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                        text=True).strip(),
        "status": subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT,
                                          text=True),
    }


before = snapshot()

saved = {}


def patch(module, name, value):
    saved[(module, name)] = getattr(module, name)
    setattr(module, name, value)


events = []
# Keep the optional prediction hooks deterministic and offline.
hook = types.ModuleType("prediction_machine.integrations.batch_runner_hook")
hook.before_task_runs = lambda *a: events.append(("predict_before",))
hook.after_task_completes = lambda *a: events.append(("predict_after",))
sys.modules["prediction_machine.integrations.batch_runner_hook"] = hook
row = {"spec": "Research Acme pricing", "critic_verdict": None,
       "critic_notes": "", "tokens_in": 7, "tokens_out": 11}
mission = {"id": "f106", "objective": "market map", "frontmatter": {}}
roles = {"worker": {"provider": "p", "model": "m"},
         "manager": {"model": "judge"}}

output = "A" * 260  # >=200 chars to clear the short-output gate

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
    patch(tr.promote, "active_skills_for", lambda mid: "")
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

    def worker(prompt, cfg, usage_path, log_prefix, **options):
        events.append(("worker", prompt, options))
        return output, {"tokens_in": 3, "tokens_out": 5}, cfg, False

    patch(tr.execution, "worker_with_failover", worker)
    patch(tr.execution, "_strip_tool_chatter", lambda out: out)
    patch(tr.execution, "worker_failed", lambda out, usage: False)

    def critic(*args, **kwargs):
        return "pass", "ok"

    patch(tr.evaluation, "run_critic", critic)
    patch(tr.evaluation, "build_mission_usage",
          lambda tid, worker_usage, critic_usage: worker_usage)
    patch(tr.evaluation, "extract_facts", lambda *a: 0)
    patch(tr.evaluation, "seed_is_synthesis", lambda spec: False)
    patch(tr.workflow, "_check_repeated_failure", lambda mid: events.append(("repeat",)))

    # --- generic (default) profile: the self-check MUST be present ---
    status = tr.run_task(106, mission, roles)
    generic_prompt = next(e[1] for e in events if e[0] == "worker")
    check("generic task completes under stub", status, "done")
    print("\n=== 1. generic-profile prompt carries the self-check ===")
    check("self-check header present", "CITATION SELF-CHECK BEFORE YOU FINALIZE" in generic_prompt, True)
    check("names the post-hoc mechanical gate",
          "mechanical check re-fetches every URL" in generic_prompt, True)
    check("states the failure consequence",
          "fails the review" in generic_prompt and "redo" in generic_prompt, True)
    check("names the search-snippet failure mode",
          "search snippet" in generic_prompt, True)
    check("names the available verification tool (web_extract)",
          "web_extract" in generic_prompt, True)
    check("states the hard rule on every cited URL",
          "Every URL in your final deliverable must be one you personally opened" in generic_prompt, True)
    check("gives the recovery (replace or drop + conf 1)",
          "confidence 1" in generic_prompt, True)

    # --- dynamic_browser profile: web_extract/web_search are FORBIDDEN, so the
    #     generic web_extract-based self-check must NOT be injected there ---
    events.clear()
    tr.run_task(106, mission, roles, retrieval_profile=tr.DYNAMIC_BROWSER_PROFILE)
    dynamic_prompt = next(e[1] for e in events if e[0] == "worker")
    print("\n=== 2. dynamic_browser profile is correctly excluded ===")
    check("self-check header absent from dynamic prompt",
          "CITATION SELF-CHECK BEFORE YOU FINALIZE" in dynamic_prompt, False)
    check("hard-rule phrase absent from dynamic prompt",
          "personally opened to a working page" in dynamic_prompt, False)

for (module, name), value in reversed(list(saved.items())):
    setattr(module, name, value)

after = snapshot()
check("HEAD unchanged", after["head"], before["head"])
check("status unchanged", after["status"], before["status"])

if fails:
    print("\nFAILURES:", *fails, sep="\n  - ")
    raise SystemExit(1)
print("\nF106 PASS — worker self-verifies citations in-loop via web_extract, "
      "generic-profile scoped, backed by the post-hoc gate")

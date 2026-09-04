"""F58: Workflow orchestration coverage -- pin behaviour of run_synthesis,
run_canaries, retry_failed_this_fire, _check_repeated_failure so the
Move 5c' extraction into orchestrator/workflow.py is verifiably
behaviour-preserving.

Operator-required coverage (§2):
  - synthesis success and failure
  - critic invocation and patch routing
  - canary success, failure, budget exhaustion, and artifact paths
  - retry selection and injected run_task_fn invocation
  - repeated-failure escalation behavior
  - no unintended live ledger, RUNS, or workspace/ESCALATIONS.md mutation
  - dependency-shape assertions preventing imports from
    batch_runner/task_runner

Eleven sections:
  - §0  Whole-suite before-snapshot (printed at start)
  - §1  _strip_tool_chatter already moved to execution.py (pre-5c' cleanup)
  - §2  run_synthesis success path
  - §3  run_synthesis short-output failure (>= 200 chars gate)
  - §4  run_synthesis chain_exhausted (critic must NOT be called)
  - §5  run_synthesis critic invocation and patch routing
  - §6  run_canaries (real run_canaries invocation; success / budget /
        artifacts) -- with promote.cmd_rollback intercepted
  - §7  retry_failed_this_fire selection (research-before-synthesis,
        injected run_task_fn only-caller, chain_exhausted short-circuit)
  - §8  _check_repeated_failure escalation: below=0, at=2x per two calls
        (no internal latch), above=0
  - §9  dependency-shape assertions
  - §10 identity gate (active when workflow.py exists with all four symbols)
  - §11 live ledger, runs/, ESCALATIONS.md, plus whole-suite after-snapshot
        invariant check, plus ALL_ROLLBACK_ATTEMPTS == []

DB isolation strategy:
  - Temp ROOT with SQLite-cloned live ledger (same approach as F57/F56).
  - Temp RUNS dir for any artifact writes.
  - Temp ESCALATIONS path under workspace/.
  - Live ledger, live RUNS, and live workspace/ESCALATIONS.md are never
    touched; §11 asserts this with explicit row-count, file-glob, and
    file-content checks.

Stubbing strategy (lesson from F57):
  - workflow.py uses module-qualified dependencies. Patch the canonical
    owning module; compatibility exports are verified separately by identity.
  - `execution.ollama_chat(model, prompt, timeout=300, trace_path=...)`
    is module-qualified by evaluation.py -- patching execution IS the
    load-bearing patch.
  - Stub signatures must match the REAL signature including all kwargs;
    a missing kwarg surfaces as "got an unexpected keyword argument".
  - `escalate` writes to disk; must be stubbed to prevent ESCALATIONS.md
    mutation in tests.
"""
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORCH = ROOT / "orchestrator"
sys.path.insert(0, str(ORCH))
sys.path.insert(0, str(ROOT / "tests"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import batch_runner as br  # noqa: E402
import citecheck  # noqa: E402
import evaluation as ev  # noqa: E402
import execution  # noqa: E402
import integrity  # noqa: E402
import ledger  # noqa: E402
import policy  # noqa: E402
import prompts  # noqa: E402
import promote  # noqa: E402  -- F58 patches this in §6 to keep canary auto-rollback inert
import runtime_context as rc  # noqa: E402
import scheduler  # noqa: E402
import workflow as wf  # noqa: E402
from _silence import silence_log  # noqa: E402

# Current ISO week, sourced from the same helper _check_repeated_failure uses.
# Tests must use this everywhere they construct a `[YYYY-Www]` spec -- the
# production query filters on `spec LIKE '[<wk>]%'`, and any week mismatch
# silently makes the count 0.
WK = scheduler.week_key()

# Track which sections fail. exit code reflects this at the end.
fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got}\n         want={want}")


def info(name, msg):
    print(f"  [INFO] {name}: {msg}")


# ── Patch helpers ─────────────────────────────────────────────────────────


class _P:
    """Monkey-patch helper that records undo info."""

    def __init__(self):
        self._undo = []

    def set(self, target, attr, value):
        old = getattr(target, attr, None)
        setattr(target, attr, value)
        self._undo.append((target, attr, old))

    def prompt(self, name, stub):
        """Patch a module-qualified prompts dependency at its canonical owner."""
        self.set(prompts, name, stub)

    def execution(self, name, stub):
        """Patch a module-qualified execution dependency at its canonical owner."""
        self.set(execution, name, stub)

    def undo(self):
        for target, attr, old in reversed(self._undo):
            if old is None and not hasattr(target, attr):
                try:
                    delattr(target, attr)
                except AttributeError:
                    pass
            else:
                setattr(target, attr, old)
        self._undo.clear()


# Match the real signature plus provider-routing kwargs. The trace is persisted
# by the real implementation; the stub only records the call.
def make_critic_stub(replies):
    """Stub for execution.ollama_chat (the critic's load-bearing call).

    Returns a stub with `.calls` recording (model, prompt, trace_path).
    """
    calls = []

    def stub(model, prompt, timeout=300, trace_path=None, usage_out=None, **kwargs):
        calls.append({"model": model, "prompt_len": len(prompt),
                      "trace_path": str(trace_path) if trace_path else None})
        return replies.pop(0) if replies else ""
    stub.calls = calls
    return stub


def stub_swfail(reply, exhausted=False, model_used=None,
                input_tokens=500, output_tokens=1500):
    """Stub for execution.synthesis_with_failover.

    Mirrors the real signature: returns (out, model_used_cfg, exhausted)
    and writes usage to usage_out (a dict passed by the caller).

    workflow.py calls this through the canonical execution module.
    """
    calls = []

    def stub(prompt, worker_cfg, log_prefix="", usage_out=None):
        calls.append({"prompt_len": len(prompt), "log_prefix": log_prefix,
                      "cfg": worker_cfg})
        if usage_out is not None:
            usage_out["input_tokens"] = input_tokens
            usage_out["output_tokens"] = output_tokens
        cfg = model_used if model_used is not None else worker_cfg
        return reply, cfg, exhausted
    stub.calls = calls
    return stub


def stub_worker_failover(reply, exhausted=False, model_used=None,
                         input_tokens=100, output_tokens=300,
                         worker_failed=False):
    """Stub for execution.worker_with_failover.

    Signature: (prompt, worker_cfg, usage_path, log_prefix="",
                allow_local=True) -> (out, usage, model_used_cfg, exhausted).

    workflow.py calls this through the canonical execution module.
    """
    calls = []

    def stub(prompt, worker_cfg, usage_path, log_prefix="", allow_local=True):
        calls.append({"prompt_len": len(prompt),
                      "usage_path": str(usage_path),
                      "log_prefix": log_prefix,
                      "allow_local": allow_local})
        cfg = model_used if model_used is not None else worker_cfg
        out_text = reply if not worker_failed else "API call failed after 3 retries: HTTP 500"
        usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
        return out_text, usage, cfg, exhausted
    stub.calls = calls
    return stub


def make_escalation_capture():
    """Capture all escalate(...) calls without writing to disk."""
    calls = []

    def fake_escalate(reason, trigger=None, task_id=None):
        calls.append({"reason": reason, "trigger": trigger, "task_id": task_id})
    fake_escalate.calls = calls
    return fake_escalate


def install_promote_safety(mp):
    """Make promote's canary-rollback path inert for the test.

    run_canaries() does `import promote` inside its body, which resolves
    via sys.modules to the canonical `promote` module. Patching the module
    object in sys.modules is therefore the load-bearing patch.

    Belt: newest_skill_below_baseline returns None so the rollback branch
          (`if culprit: promote.cmd_rollback(...)`) never enters.
    Suspenders: cmd_rollback records the call into a list AND raises if it
          IS reached. The list is returned to the caller so the test can
          assert `attempts == []` at section end.

    Before this guard, F58's §6b would invoke the real cmd_rollback against
    the LIVE skills_analyst/ directory, deleting operator-approved skills
    and creating real Git commits (verified 2026-08-27: four rollback
    commits per F58 run, six across the session).
    """
    rollback_attempts = []

    def no_candidate(week_green):
        return None

    def rollback_should_never_run(relpath, reason="operator/canary rollback"):
        rollback_attempts.append({"relpath": relpath, "reason": reason})
        raise AssertionError(
            f"promote.cmd_rollback({relpath!r}) was invoked during F58. "
            "This would create a real Git commit deleting an operator-"
            "approved skill from skills_analyst/. The test environment "
            "is not isolated enough for that to be safe."
        )

    # Patch sys.modules["promote"] -- the canonical module -- which is
    # where the local `import promote` inside run_canaries() resolves.
    mp.set(promote, "newest_skill_below_baseline", no_candidate)
    mp.set(promote, "cmd_rollback", rollback_should_never_run)
    return rollback_attempts


# Module-level list collecting EVERY rollback attempt across all sections.
# The final snapshot assertion also checks this is empty.
ALL_ROLLBACK_ATTEMPTS: list[dict] = []


def suite_snapshot():
    """Capture the load-bearing live state for the before/after invariant.

    Returns a dict with: head, status_porcelain, skill paths/hashes, task-row
    count, run artifacts, policy-state hash, and escalation hash. Every field must be unchanged when
    F58 finishes -- if any field drifts, F58 fails loudly with the diff.
    """
    skill_paths = []
    skills_root = ORCH.parent / "skills_analyst"
    if skills_root.exists():
        for p in sorted(skills_root.rglob("*.md")):
            # The check is on the path's POSITIONAL PARTS, not the string
            # representation: p.relative_to(parent) returns POSIX paths on
            # Linux/macOS and Windows-style paths with backslashes on
            # Windows. A substring match on "/_candidates/" misses the
            # latter. Walk the parents to filter correctly across both.
            if any(part in ("_candidates", "_rejected") for part in p.parts):
                continue
            if p.name == "README.md":
                continue
            skill_paths.append(p)
    skill_hashes = {str(p.relative_to(ORCH.parent)):
                    hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in skill_paths}
    live_lb = ORCH.parent / "ledger" / "ledger.db"
    if live_lb.exists():
        with sqlite3.connect(live_lb, timeout=30) as c:
            ledger_count = c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    else:
        ledger_count = 0
    live_runs = ORCH.parent / "runs"
    runs_set = set()
    if live_runs.exists():
        for p in live_runs.glob("canary_*.usage.json"):
            runs_set.add(p.name)
        for p in live_runs.glob("task*_worker_raw.txt"):
            runs_set.add(p.name)
        for p in live_runs.glob("task*_critic_reasoning.txt"):
            runs_set.add(p.name)
    policy_state = live_runs / "policy_state.json"
    policy_state_sha = (hashlib.sha256(policy_state.read_bytes()).hexdigest()
                        if policy_state.exists() else None)
    live_esc = ORCH.parent / "workspace" / "ESCALATIONS.md"
    esc_sha = hashlib.sha256(live_esc.read_bytes()).hexdigest() \
        if live_esc.exists() else None
    return {
        "head": subprocess.run(
            ["git", "-C", str(ORCH.parent), "rev-parse", "HEAD"],
            capture_output=True, text=True).stdout.strip(),
        "status_porcelain": subprocess.run(
            ["git", "-C", str(ORCH.parent), "status", "--porcelain=v1",
             "--untracked-files=all"],
            capture_output=True, text=True).stdout.rstrip("\r\n"),
        "skill_paths": sorted(skill_hashes.keys()),
        "skill_hashes": skill_hashes,
        "ledger_count": ledger_count,
        "runs_set": sorted(runs_set),
        "policy_state_sha": policy_state_sha,
        "esc_sha": esc_sha,
    }


def assert_snapshot_invariant(label, before, after):
    """Diff before vs after; raise SystemExit(1) if any field drifted."""
    drifted = []
    for key in before:
        if before[key] != after[key]:
            drifted.append({"field": key,
                            "before": before[key],
                            "after": after[key]})
    if drifted:
        print(f"\n*** SNAPSHOT DRIFT IN {label} ***")
        for d in drifted:
            print(f"  field={d['field']}")
            print(f"    before: {d['before']!r}")
            print(f"    after:  {d['after']!r}")
        print("F58 is supposed to be read-only against Git/skills/ledger/runs/ESCALATIONS.md.")
        print("If the drift is intentional, fix the test to expect it -- do not silently let it through.")
        fails.append(f"snapshot drift: {label}: {[d['field'] for d in drifted]}")
    else:
        print(f"\n[snapshot] {label}: all {len(before)} fields unchanged "
              f"(head={before['head'][:12]}, skills={len(before['skill_paths'])}, "
              f"ledger_rows={before['ledger_count']}, runs_files={len(before['runs_set'])})")


# ── DB / file isolation ──────────────────────────────────────────────────


def temp_root_with_ledger():
    """Clone the live ledger into a temp ROOT, redirect every read/write path.

    workflow resolves ROOT/RUNS through runtime_context at call time. Evaluation
    still owns captured ROOT/RUNS bindings, so its critic paths are redirected too.

    Returns (tmp_root_path, cleanup_fn).
    """
    tmpdir = tempfile.mkdtemp(prefix="f58_root_")
    tmp_root = Path(tmpdir)
    for sub in ("memory", "runs", "workspace", "ledger"):
        (tmp_root / sub).mkdir()
    temp_lb = tmp_root / "ledger" / "ledger.db"
    live_lb = ORCH.parent / "ledger" / "ledger.db"
    with sqlite3.connect(live_lb, timeout=30) as src, \
         sqlite3.connect(temp_lb, timeout=30) as dst:
        src.backup(dst)

    # Snapshot original values per (module, attr) -- duplicates are intentional.
    assignments = [
        (rc, "ROOT", tmp_root), (ev, "ROOT", tmp_root),
        (rc, "RUNS", tmp_root / "runs"), (ev, "RUNS", tmp_root / "runs"),
    ]
    real = {}
    for mod, attr, val in assignments:
        real[(mod, attr)] = getattr(mod, attr)
        setattr(mod, attr, val)
    real_ledger_db = ledger.LEDGER_DB
    ledger.LEDGER_DB = temp_lb
    # The critic budget uses policy's own DB binding for its UTC date key and
    # writes its counter to STATE_PATH. Both must be temporary; otherwise F58
    # consumes the live manager-call budget while leaving task-row counts unchanged.
    real_policy_ledger_db = policy.LEDGER_DB
    real_policy_state_path = policy.STATE_PATH
    policy.LEDGER_DB = temp_lb
    policy.STATE_PATH = tmp_root / "runs" / "policy_state.json"

    temp_esc = tmp_root / "workspace" / "ESCALATIONS.md"
    esc_assignments = [(integrity, "ESCALATIONS", temp_esc)]
    for mod, attr, val in esc_assignments:
        real[(mod, attr)] = getattr(mod, attr)
        setattr(mod, attr, val)

    def cleanup():
        for (mod, attr), val in real.items():
            setattr(mod, attr, val)
        ledger.LEDGER_DB = real_ledger_db
        policy.LEDGER_DB = real_policy_ledger_db
        policy.STATE_PATH = real_policy_state_path
        shutil.rmtree(tmpdir, ignore_errors=True)

    return tmp_root, cleanup


def insert_task(tmp_root, mission_id, spec, status="failed",
                critic_verdict="fail", critic_notes="missed section",
                attempt_count=1, tokens_in=0, tokens_out=0,
                pass_criteria="Must cover X."):
    """Insert one task row in the temp ledger. Returns task_id."""
    temp_lb = tmp_root / "ledger" / "ledger.db"
    with sqlite3.connect(temp_lb, timeout=30) as c:
        c.execute(
            "INSERT INTO tasks (mission_id, spec, pass_criteria, status, "
            "critic_verdict, critic_notes, attempt_count, tokens_in, "
            "tokens_out) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (mission_id, spec, pass_criteria, status, critic_verdict,
             critic_notes, attempt_count, tokens_in, tokens_out),
        )
        return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def insert_canary(tmp_root, name, status, critic_verdict=None,
                  tokens_in=0, tokens_out=0, spec=None):
    """Insert a canary task row. Default spec uses the current week."""
    if spec is None:
        spec = f"[{WK}] {name}"
    return insert_task(tmp_root, "canaries", spec,
                       status=status, critic_verdict=critic_verdict,
                       tokens_in=tokens_in, tokens_out=tokens_out)


# ── Standard stub set (every section that exercises run_synthesis) ───────


def standard_prompts_stub():
    """Return a fresh stubs dict with the canonical prompt helpers."""
    return {
        "pass_criteria_for": lambda m: "Must cover X.\n## Done-definition\n- Item 1\n- Item 2\n",
        "deliverable_requirements": lambda m: "- Item 1\n- Item 2\n",
        "task_scope_note": lambda spec, m: "scope note",
        "mission_objective": lambda m: "test objective",
        "_recent_fact_lines": lambda: "",
        "build_brief_block": lambda briefs: "(no briefs)",
    }


def install_prompts_stubs(mp, stubs=None):
    """Patch every workflow prompt helper at the canonical prompts module."""
    stubs = stubs or standard_prompts_stub()
    for name, stub in stubs.items():
        mp.prompt(name, stub)


# ── §1 _strip_tool_chatter pre-move verification ─────────────────────────

# ── Whole-suite before-snapshot ─────────────────────────────────────
# Captured BEFORE any test action. The end-of-suite assert_snapshot_invariant
# (in §11) compares against this. If any field drifts -- a Git commit appears,
# a tracked skill is removed/modified, the ledger row count moves, a new file
# lands in runs/, ESCALATIONS.md content changes -- the test FAILS LOUDLY
# with the diff. This is the load-bearing guarantee: F58 is read-only
# against Git/skills/ledger/runs/ESCALATIONS.md.
_SUITE_BEFORE = suite_snapshot()
print(f"\n[snapshot] before: head={_SUITE_BEFORE['head'][:12]}, "
      f"skills={len(_SUITE_BEFORE['skill_paths'])}, "
      f"ledger_rows={_SUITE_BEFORE['ledger_count']}, "
      f"runs_files={len(_SUITE_BEFORE['runs_set'])}, "
      f"esc_sha={(_SUITE_BEFORE['esc_sha'] or 'None')[:12]}")
print(f"[snapshot] before: status_porcelain={_SUITE_BEFORE['status_porcelain']!r}")

print("=== 1. _strip_tool_chatter moved to execution (pre-5c' cleanup) ===")
check("br._strip_tool_chatter is execution._strip_tool_chatter",
      br._strip_tool_chatter is execution._strip_tool_chatter, True)
check("strip removes [tool]-prefixed lines",
      br._strip_tool_chatter("header\n[tool] ( ͡° ͜ʖ ͡°) noise\nfooter\n"),
      "header\n\nfooter")
check("strip leaves plain text unchanged",
      br._strip_tool_chatter("plain text only"), "plain text only")

# ── §2 run_synthesis success path ────────────────────────────────────────

print("\n=== 2. run_synthesis success path ===")
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    out_dir = tmp_root / "workspace" / "content"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{WK}_seed-1-x.md").write_text(
        "# Brief\ncontent\nSource: https://example.com/1\n", encoding="utf-8")

    install_prompts_stubs(mp)
    mp.set(policy, "token_budget_breached", lambda: False)

    long_reply = "# S\n\n" + ("content paragraph. " * 30)
    swfail = stub_swfail(long_reply, exhausted=False)
    critic = make_critic_stub(["VERDICT: PASS\nLooks good."])
    mp.execution("synthesis_with_failover", swfail)
    mp.execution("ollama_chat", critic)
    mp.execution("worker_failed", lambda out, usage: False)
    mp.set(citecheck, "verify", lambda _: [])
    mp.set(citecheck, "summarize",
           lambda e: {"dead": 0, "checked": 0, "dead_frac": 0.0})
    mp.set(citecheck, "is_hard_fail", lambda s: False)
    esc_cap = make_escalation_capture()
    mp.set(integrity, "escalate", esc_cap)

    tid = insert_task(tmp_root, mission_id="001-test",
                      spec=f"[{WK}] Cross-channel synthesis: research topic X",
                      status="running", critic_verdict=None)
    row = {"task_id": tid,
           "spec": f"[{WK}] Cross-channel synthesis: research topic X",
           "pass_criteria": "Must cover X.\n## Done-definition\n- Item 1\n- Item 2\n",
           "tokens_in": 100, "tokens_out": 200}
    mission = {"id": "001-test"}
    roles = {"worker": {"provider": "ollama", "model": "m"},
             "critic": {"provider": "byteplus_coding", "model": "c"},
             "manager": {"provider": "ollama", "model": "g"}}

    with silence_log():
        status = br.run_synthesis(tid, row, mission, roles, out_dir,
                                  WK, baseline=False,
                                  baseline_note="")
    check("run_synthesis success returns 'done'", status, "done")
    check("synthesis_with_failover was called once",
          len(swfail.calls), 1)
    check("critic was called once (PASS verdict)",
          len(critic.calls), 1)
    check("critic was called with the critic model",
          critic.calls[0]["model"], "c")
    # Deliverable written
    deliverables = [d for d in out_dir.glob(f"{WK}_*.md")
                    if d.name != f"{WK}_seed-1-x.md"]
    check("synthesis deliverable written",
          len(deliverables) >= 1, True)
    # Raw worker output saved to RUNS
    raw = tmp_root / "runs" / f"task{tid}_worker_raw.txt"
    check("raw worker output saved to runs/", raw.exists(), True)
    # No escalations on a clean PASS
    check("no escalations on a clean PASS",
          len(esc_cap.calls), 0)
finally:
    mp.undo()
    cleanup()

# ── §3 run_synthesis short-output failure ────────────────────────────────

print("\n=== 3. run_synthesis short-output failure (< 200 chars) ===")
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    out_dir = tmp_root / "workspace" / "content"
    out_dir.mkdir(parents=True, exist_ok=True)

    install_prompts_stubs(mp)
    mp.set(policy, "token_budget_breached", lambda: False)
    mp.execution("synthesis_with_failover", stub_swfail("too short", exhausted=False))
    critic = make_critic_stub(["NEVER CALLED"])
    mp.execution("ollama_chat", critic)
    mp.set(citecheck, "verify", lambda _: [])
    mp.set(citecheck, "summarize",
           lambda e: {"dead": 0, "checked": 0, "dead_frac": 0.0})
    mp.set(citecheck, "is_hard_fail", lambda s: False)
    esc_cap = make_escalation_capture()
    mp.set(integrity, "escalate", esc_cap)

    tid = insert_task(tmp_root, mission_id="001-test",
                      spec=f"[{WK}] Cross-channel synthesis: short",
                      status="running")
    row = {"task_id": tid, "spec": f"[{WK}] Cross-channel synthesis: short",
           "pass_criteria": "Must cover X."}
    mission = {"id": "001-test"}
    roles = {"worker": {"provider": "ollama", "model": "m"},
             "critic": {"provider": "byteplus_coding", "model": "c"},
             "manager": {"provider": "ollama", "model": "g"}}

    with silence_log():
        status = br.run_synthesis(tid, row, mission, roles, out_dir,
                                  WK, baseline=False,
                                  baseline_note="")
    check("short-output failure returns 'failed'", status, "failed")
    check("critic NOT called when output < 200 chars",
          len(critic.calls), 0)
    # Persisted status check
    with sqlite3.connect(tmp_root / "ledger" / "ledger.db", timeout=30) as c:
        s = c.execute("SELECT status, critic_verdict FROM tasks WHERE task_id=?",
                      (tid,)).fetchone()
    check("ledger row status='failed'", s[0], "failed")
    check("ledger row critic_verdict='fail'", s[1], "fail")
finally:
    mp.undo()
    cleanup()

# ── §4 run_synthesis chain_exhausted ─────────────────────────────────────

print("\n=== 4. run_synthesis chain_exhausted (critic must NOT be called) ===")
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    out_dir = tmp_root / "workspace" / "content"
    out_dir.mkdir(parents=True, exist_ok=True)

    install_prompts_stubs(mp)
    mp.set(policy, "token_budget_breached", lambda: False)
    mp.execution("synthesis_with_failover", stub_swfail("anything", exhausted=True))
    critic = make_critic_stub(["NEVER CALLED"])
    mp.execution("ollama_chat", critic)
    mp.set(citecheck, "verify", lambda _: [])
    mp.set(citecheck, "summarize",
           lambda e: {"dead": 0, "checked": 0, "dead_frac": 0.0})
    mp.set(citecheck, "is_hard_fail", lambda s: False)
    esc_cap = make_escalation_capture()
    mp.set(integrity, "escalate", esc_cap)

    tid = insert_task(tmp_root, mission_id="001-test",
                      spec=f"[{WK}] Cross-channel synthesis: chain",
                      status="running")
    row = {"task_id": tid,
           "spec": f"[{WK}] Cross-channel synthesis: chain",
           "pass_criteria": "Must cover X."}
    mission = {"id": "001-test"}
    roles = {"worker": {"provider": "ollama", "model": "m"},
             "critic": {"provider": "byteplus_coding", "model": "c"},
             "manager": {"provider": "ollama", "model": "g"}}

    with silence_log():
        status = br.run_synthesis(tid, row, mission, roles, out_dir,
                                  WK, baseline=False,
                                  baseline_note="")
    check("chain_exhausted returns 'chain_exhausted'", status, "chain_exhausted")
    check("critic NOT called when chain exhausted",
          len(critic.calls), 0)
finally:
    mp.undo()
    cleanup()

# ── §5 run_synthesis critic invocation and patch routing ─────────────────

print("\n=== 5. run_synthesis critic invocation and patch routing ===")
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    out_dir = tmp_root / "workspace" / "content"
    out_dir.mkdir(parents=True, exist_ok=True)

    install_prompts_stubs(mp)
    mp.set(policy, "token_budget_breached", lambda: False)
    long_reply = "# S\n" + ("content paragraph. " * 30)
    mp.execution("synthesis_with_failover", stub_swfail(long_reply, exhausted=False))
    critic = make_critic_stub(["VERDICT: FAIL\nMissing required section."])
    mp.execution("ollama_chat", critic)
    mp.set(citecheck, "verify", lambda _: [])
    mp.set(citecheck, "summarize",
           lambda e: {"dead": 0, "checked": 0, "dead_frac": 0.0})
    mp.set(citecheck, "is_hard_fail", lambda s: False)
    esc_cap = make_escalation_capture()
    mp.set(integrity, "escalate", esc_cap)

    tid = insert_task(tmp_root, mission_id="001-test",
                      spec=f"[{WK}] Cross-channel synthesis: critic-fail",
                      status="running")
    row = {"task_id": tid,
           "spec": f"[{WK}] Cross-channel synthesis: critic-fail",
           "pass_criteria": "Must cover X."}
    mission = {"id": "001-test"}
    roles = {"worker": {"provider": "ollama", "model": "m"},
             "critic": {"provider": "byteplus_coding", "model": "c"},
             "manager": {"provider": "ollama", "model": "g"}}

    with silence_log():
        status = br.run_synthesis(tid, row, mission, roles, out_dir,
                                  WK, baseline=False,
                                  baseline_note="")
    check("critic FAIL verdict -> 'failed' status", status, "failed")
    check("critic was called exactly once", len(critic.calls), 1)
    check("critic was called with the critic model",
          critic.calls[0]["model"], "c")
    check("critic trace_path anchored under temp RUNS",
          critic.calls[0]["trace_path"].startswith(str(tmp_root / "runs")),
          True)
finally:
    mp.undo()
    cleanup()

# ── §6 run_canaries (real run_canaries against temp ledger) ─────────────

print("\n=== 6. run_canaries (real run_canaries against temp ledger) ===")

# 6a: Budget-exhausted path. The first iteration should NOT call the worker.
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    install_prompts_stubs(mp)
    mp.set(policy, "token_budget_breached", lambda: True)
    worker_stub = stub_worker_failover("anything", exhausted=False)
    mp.execution("worker_with_failover", worker_stub)
    mp.execution("worker_failed", lambda out, usage: False)
    esc_cap = make_escalation_capture()
    mp.set(integrity, "escalate", esc_cap)
    attempts = install_promote_safety(mp)
    ALL_ROLLBACK_ATTEMPTS.extend(attempts)

    with silence_log():
        br.run_canaries({"worker": {"provider": "ollama", "model": "m"}})

    check("budget-exhausted: worker NOT called",
          len(worker_stub.calls), 0)
    # Each canary should have a parked row in the temp ledger.
    with sqlite3.connect(tmp_root / "ledger" / "ledger.db", timeout=30) as c:
        n_parked = c.execute(
            "SELECT COUNT(*) FROM tasks WHERE mission_id='canaries' "
            "AND status='quota_wait'").fetchone()[0]
    check("budget-exhausted: all 5 canaries parked as quota_wait",
          n_parked, len(br.CANARIES))
finally:
    mp.undo()
    cleanup()

# 6b: Real run_canaries with one PASS and four FAILs against the synthetic
# reply. We grade against br.CANARIES' lambdas, which is what run_canaries
# does internally -- so this exercises the real grading path, not an emulator.
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    install_prompts_stubs(mp)
    mp.set(policy, "token_budget_breached", lambda: False)

    # Reply crafted to PASS C1 only. C2/C3/C4/C5 will FAIL because the
    # reply lacks 'too many requests' / 'canberra' / etc.
    pass_reply = (
        "2006 https://en.wikipedia.org/wiki/Shopify\n"
    )
    mp.execution("worker_with_failover", stub_worker_failover(pass_reply, exhausted=False))
    mp.execution("worker_failed", lambda out, usage: False)
    esc_cap = make_escalation_capture()
    mp.set(integrity, "escalate", esc_cap)
    attempts = install_promote_safety(mp)
    ALL_ROLLBACK_ATTEMPTS.extend(attempts)

    # Pre-existing C1/C2 done rows (current week) so run_canaries skips
    # them (RESUMABLE_STATUSES check). Inserting with the current WK is
    # the resume path: a row that already finished stays finished.
    insert_canary(tmp_root, "C1", "done", critic_verdict="pass",
                  spec=f"[{WK}] C1")
    insert_canary(tmp_root, "C2", "done", critic_verdict="pass",
                  spec=f"[{WK}] C2")

    with silence_log():
        br.run_canaries({"worker": {"provider": "ollama", "model": "m"}})

    # The current week's rows (spec starts with the live ISO week):
    with sqlite3.connect(tmp_root / "ledger" / "ledger.db", timeout=30) as c:
        rows = c.execute(
            "SELECT spec, status, critic_verdict FROM tasks "
            "WHERE mission_id='canaries' AND spec LIKE ? "
            "ORDER BY spec",
            (f"[{WK}]%",),
        ).fetchall()

    check("current-week canaries: all 5 rows present",
          len(rows), len(br.CANARIES))

    # Compute green vs fail from the synthetic reply + CANARIES grades.
    c1_grade = br.CANARIES[0][2](pass_reply)
    check("C1 grader accepts the synthetic reply", c1_grade, True)
    c2_grade = br.CANARIES[1][2](pass_reply)
    check("C2 grader rejects the synthetic reply", c2_grade, False)
finally:
    mp.undo()
    cleanup()

# 6c: Canary artifact paths.
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    install_prompts_stubs(mp)
    mp.set(policy, "token_budget_breached", lambda: False)
    captured_paths = []
    breaker = {"called": 0}

    def path_worker(prompt, worker_cfg, usage_path, log_prefix="",
                    allow_local=True):
        captured_paths.append(str(usage_path))
        # Budget breach after the first canary so the loop ends quickly.
        breaker["called"] += 1
        return ("", {}, worker_cfg, False)
    mp.execution("worker_with_failover", path_worker)
    mp.execution("worker_failed", lambda out, usage: False)
    esc_cap = make_escalation_capture()
    mp.set(integrity, "escalate", esc_cap)
    attempts = install_promote_safety(mp)
    ALL_ROLLBACK_ATTEMPTS.extend(attempts)

    with silence_log():
        br.run_canaries({"worker": {"provider": "ollama", "model": "m"}})
    check("canary usage_path anchored under temp RUNS",
          all(p.startswith(str(tmp_root / "runs")) for p in captured_paths),
          True)
    check("canary usage_path uses canary_<name>.usage.json naming",
          all("canary_" in p and p.endswith(".usage.json")
              for p in captured_paths),
          True)
finally:
    mp.undo()
    cleanup()

# ── §7 retry_failed_this_fire selection + injected run_task_fn ─────────

print("\n=== 7. retry_failed_this_fire selection + injected run_task_fn ===")

# 7a: Selection correctness (research-before-synthesis, both retried).
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    research_tid = insert_task(tmp_root, mission_id="001-test",
                              spec=f"[{WK}][seed 1] research topic")
    synth_tid = insert_task(tmp_root, mission_id="001-test",
                            spec=f"[{WK}] Cross-channel synthesis: y")
    mp.set(ev, "seed_is_synthesis",
           lambda spec: "synthesi" in spec.lower().split(":", 1)[0])

    captured = []

    def fake_run_task(tid, mission, roles):
        captured.append(tid)
        return "done"
    mission = {"id": "001-test"}
    roles = {"worker": {"provider": "ollama", "model": "m"},
             "critic": {"provider": "byteplus_coding", "model": "c"},
             "manager": {"provider": "ollama", "model": "g"}}

    with silence_log():
        results = br.retry_failed_this_fire([research_tid, synth_tid],
                                           mission, roles,
                                           run_task_fn=fake_run_task)
    check("retry ordering: research before synthesis",
          captured, [research_tid, synth_tid])
    check("retry results match run_task statuses", results, ["done", "done"])
finally:
    mp.undo()
    cleanup()

# 7b: injected run_task_fn is the ONLY caller (no fallback to br.run_task).
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    tid = insert_task(tmp_root, mission_id="001-test",
                      spec=f"[{WK}][seed 1] research topic")
    mp.set(ev, "seed_is_synthesis", lambda spec: False)
    captured = []

    def fake_run_task(tid, mission, roles):
        captured.append(("injected", tid))
        return "done"
    mission = {"id": "001-test"}
    roles = {"worker": {"provider": "ollama", "model": "m"},
             "critic": {"provider": "byteplus_coding", "model": "c"},
             "manager": {"provider": "ollama", "model": "g"}}

    with silence_log():
        br.retry_failed_this_fire([tid], mission, roles,
                                  run_task_fn=fake_run_task)
    check("injected run_task_fn is the only caller",
          captured, [("injected", tid)])
finally:
    mp.undo()
    cleanup()

# 7c: chain_exhausted short-circuits the retry pass.
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    tids = [insert_task(tmp_root, mission_id="001-test",
                        spec=f"[{WK}][seed {i+1}] research topic {i}")
            for i in range(3)]
    mp.set(ev, "seed_is_synthesis", lambda spec: False)
    order = []

    def fake_run_task(tid, mission, roles):
        order.append(tid)
        return "chain_exhausted"
    mission = {"id": "001-test"}
    roles = {"worker": {"provider": "ollama", "model": "m"},
             "critic": {"provider": "byteplus_coding", "model": "c"},
             "manager": {"provider": "ollama", "model": "g"}}

    with silence_log():
        results = br.retry_failed_this_fire(tids, mission, roles,
                                            run_task_fn=fake_run_task)
    check("chain_exhausted on first retry short-circuits the pass",
          len(order), 1)
    check("first retry gets the lowest task_id (ordering preserved)",
          order, [tids[0]])
    check("short-circuit returns one status",
          results, ["chain_exhausted"])
finally:
    mp.undo()
    cleanup()

# ── §8 _check_repeated_failure escalation ─────────────────────────────

print("\n=== 8. _check_repeated_failure escalation at threshold ===")

# 8a: First threshold crossing fires ONE escalation.
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    for i in range(br.REPEATED_FAILURE_THRESHOLD):
        insert_task(tmp_root, mission_id="001-test",
                    spec=f"[{WK}][seed {i+1}] topic {i}")
    esc_cap = make_escalation_capture()
    mp.set(integrity, "escalate", esc_cap)
    with silence_log():
        br._check_repeated_failure("001-test")
    check("first threshold crossing fires ONE escalation",
          len(esc_cap.calls), 1)
    check("escalation carries repeated_task_failure trigger",
          esc_cap.calls[0]["trigger"], "repeated_task_failure")
finally:
    mp.undo()
    cleanup()

# 8b: Below threshold -> no escalation.
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    for i in range(br.REPEATED_FAILURE_THRESHOLD - 1):
        insert_task(tmp_root, mission_id="001-test",
                    spec=f"[{WK}][seed {i+1}] topic {i}")
    esc_cap = make_escalation_capture()
    mp.set(integrity, "escalate", esc_cap)
    with silence_log():
        br._check_repeated_failure("001-test")
    check("below threshold -> no escalation",
          len(esc_cap.calls), 0)
finally:
    mp.undo()
    cleanup()

# 8c: Above threshold -> zero escalations under current production code.
# _check_repeated_failure fires ONLY when n == REPEATED_FAILURE_THRESHOLD
# exactly (the comment at the call site in batch_runner.py calls this
# "fire once, at the exact threshold crossing"). Inserting THRESHOLD+1
# rows yields n == THRESHOLD+1, which is NOT equal to THRESHOLD, so the
# `if n == REPEATED_FAILURE_THRESHOLD:` branch is False -- zero escalations.
# This is a characterization test, NOT a fix; if the production behaviour
# changes (e.g. to `n >= REPEATED_FAILURE_THRESHOLD`), update this test
# rather than the production code.
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    for i in range(br.REPEATED_FAILURE_THRESHOLD + 1):
        insert_task(tmp_root, mission_id="001-test",
                    spec=f"[{WK}][seed {i+1}] topic {i}")
    esc_cap = make_escalation_capture()
    mp.set(integrity, "escalate", esc_cap)
    with silence_log():
        # Two calls in a row -- neither fires because n never equals the
        # exact threshold (it is one above).
        br._check_repeated_failure("001-test")
        br._check_repeated_failure("001-test")
    check("above threshold: zero escalations across multiple calls "
          "(production guards with `n == THRESHOLD`)",
          len(esc_cap.calls), 0)
finally:
    mp.undo()
    cleanup()

# 8d: Exactly at threshold across multiple calls -- current behaviour
# is that EACH call fires (no internal once-only latch). This is a
# characterisation; if the production code gains a once-only latch the
# expectation here changes to 1.
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    for i in range(br.REPEATED_FAILURE_THRESHOLD):
        insert_task(tmp_root, mission_id="001-test",
                    spec=f"[{WK}][seed {i+1}] topic {i}")
    esc_cap = make_escalation_capture()
    mp.set(integrity, "escalate", esc_cap)
    with silence_log():
        br._check_repeated_failure("001-test")
        br._check_repeated_failure("001-test")
    check("exactly at threshold: one escalation per invocation (current "
          "behaviour -- no internal once-only latch)",
          len(esc_cap.calls), 2)
finally:
    mp.undo()
    cleanup()

# ── §9 dependency-shape assertions ─────────────────────────────────────

print("\n=== 9. dependency-shape assertions ===")

# 9a: workflow.py must NOT import batch_runner or task_runner.
try:
    import workflow  # noqa: F401
    wf_text = (ORCH / "workflow.py").read_text(encoding="utf-8")
    has_br_import = bool(re.search(r"^\s*import\s+batch_runner\b", wf_text, re.M)
                         or re.search(r"^\s*from\s+batch_runner\b", wf_text, re.M))
    has_tr_import = bool(re.search(r"^\s*import\s+task_runner\b", wf_text, re.M)
                         or re.search(r"^\s*from\s+task_runner\b", wf_text, re.M))
    check("workflow.py does NOT import batch_runner",
          has_br_import, False)
    check("workflow.py does NOT import task_runner",
          has_tr_import, False)
except ImportError:
    info("workflow.py", "not created yet (pre-Move-5c') -- skipping shape check")

# 9b: batch_runner.run_synthesis/run_canaries must NOT have a hidden
# self-import of batch_runner inside their bodies.
br_text = (ORCH / "batch_runner.py").read_text(encoding="utf-8")
for fn in ("run_synthesis", "run_canaries"):
    m = re.search(rf"^def {fn}\b.*?(?=^def |\Z)",
                  br_text, re.MULTILINE | re.DOTALL)
    if not m:
        continue
    body = m.group()
    has = bool(re.search(r"from\s+batch_runner\s+import|import\s+batch_runner",
                          body))
    check(f"{fn}: no self-import of batch_runner in body",
          has, False)

# 9c: Model calls in run_synthesis/run_canaries must not be bare
# ollama_chat(<...>) -- they must go through execution.<func>(...).
for fn in ("run_synthesis", "run_canaries"):
    m = re.search(rf"^def {fn}\b.*?(?=^def |\Z)",
                  br_text, re.MULTILINE | re.DOTALL)
    if not m:
        continue
    body = m.group()
    # Negative lookbehind: must not match if preceded by "execution." or any "."
    bare = bool(re.search(r"(?<!execution\.)(?<!\.)ollama_chat\(", body))
    check(f"{fn}: no bare ollama_chat(...) call in body",
          bare, False)

# ── §10 identity gate ──────────────────────────────────────────────────

print("\n=== 10. identity: br.X is workflow.X (active after Move 5c') ===")

wf_symbols = ("run_synthesis", "run_canaries",
              "retry_failed_this_fire", "_check_repeated_failure")

workflow_importable = False
all_symbols_present = False
try:
    import workflow as wf  # noqa: F401
    workflow_importable = True
    all_symbols_present = all(hasattr(wf, n) for n in wf_symbols)
except Exception as e:
    info("workflow.py", f"not importable yet ({type(e).__name__}: {e})")

if workflow_importable and all_symbols_present:
    import workflow as wf
    for name in wf_symbols:
        check(f"br.{name} is wf.{name}",
              getattr(br, name) is getattr(wf, name), True)
else:
    info("identity gate",
         "inactive -- workflow.py missing or incomplete. "
         "Activate after Move 5c' extraction lands.")

# ── §11 live ledger, runs/, and ESCALATIONS.md untouched ──────────────

print("\n=== 11. live ledger, runs/, workspace/ESCALATIONS.md untouched ===")
live_lb = ORCH.parent / "ledger" / "ledger.db"
live_runs = ORCH.parent / "runs"
live_esc = ORCH.parent / "workspace" / "ESCALATIONS.md"


def _count_live_tasks():
    if not live_lb.exists():
        return 0
    with sqlite3.connect(live_lb, timeout=30) as c:
        return c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]


def _live_runs_canary():
    if not live_runs.exists():
        return set()
    return {p.name for p in live_runs.glob("canary_*.usage.json")} \
         | {p.name for p in live_runs.glob("task*_worker_raw.txt")} \
         | {p.name for p in live_runs.glob("task*_critic_reasoning.txt")}


def _live_esc_sha():
    if not live_esc.exists():
        return None
    import hashlib
    return hashlib.sha256(live_esc.read_bytes()).hexdigest()


_live_before = _count_live_tasks()
_runs_before = _live_runs_canary()
_esc_before = _live_esc_sha()

# Run §2 once more (the most stateful section) to stress the isolation.
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    out_dir = tmp_root / "workspace" / "content"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{WK}_seed-1-x.md").write_text(
        "# Brief\ncontent\n", encoding="utf-8")
    install_prompts_stubs(mp)
    mp.set(policy, "token_budget_breached", lambda: False)
    long_reply = "# S\n\n" + ("content paragraph. " * 30)
    mp.execution("synthesis_with_failover", stub_swfail(long_reply, exhausted=False))
    mp.execution("ollama_chat", make_critic_stub(["VERDICT: PASS\nok."]))
    mp.set(citecheck, "verify", lambda _: [])
    mp.set(citecheck, "summarize",
           lambda e: {"dead": 0, "checked": 0, "dead_frac": 0.0})
    mp.set(citecheck, "is_hard_fail", lambda s: False)
    esc_cap = make_escalation_capture()
    mp.set(integrity, "escalate", esc_cap)

    tid = insert_task(tmp_root, mission_id="001-test",
                      spec=f"[{WK}] Cross-channel synthesis: final",
                      status="running")
    row = {"task_id": tid,
           "spec": f"[{WK}] Cross-channel synthesis: final",
           "pass_criteria": "Must cover X."}
    mission = {"id": "001-test"}
    roles = {"worker": {"provider": "ollama", "model": "m"},
             "critic": {"provider": "byteplus_coding", "model": "c"},
             "manager": {"provider": "ollama", "model": "g"}}
    with silence_log():
        br.run_synthesis(tid, row, mission, roles, out_dir,
                         WK, baseline=False, baseline_note="")
finally:
    mp.undo()
    cleanup()

check("live ledger task count unchanged", _count_live_tasks(), _live_before)
check("live runs/ canary/worker/critic artifacts unchanged",
      _live_runs_canary(), _runs_before)
check("live workspace/ESCALATIONS.md content unchanged",
      _live_esc_sha(), _esc_before)

# ── Whole-suite after-snapshot + invariant check ──────────────────────
# Compares the live state right now against _SUITE_BEFORE captured in §0.
# Catches every class of leak the operator called out: HEAD drift, an
# unexpected commit, status churn, skill deletion, skill modification, ledger
# row mutations, runs/ artifacts, ESCALATIONS.md content change.
_SUITE_AFTER = suite_snapshot()
assert_snapshot_invariant("suite before/after", _SUITE_BEFORE, _SUITE_AFTER)

# Belt-and-suspenders: the per-section promote-safety patch records any
# rollback attempt into ALL_ROLLBACK_ATTEMPTS. Even if the snapshot invariant
# somehow missed it (e.g. operator-approved skill hashes match by coincidence
# after a different skill file replaced the deleted one), this list is the
# last line of defense.
check("zero promote.cmd_rollback attempts across the suite",
      ALL_ROLLBACK_ATTEMPTS, [])

# ── Summary ────────────────────────────────────────────────────────────

print("\n=== SUMMARY ===")
if fails:
    print(f"  {len(fails)} FAILURES:")
    for f in fails:
        print(f"    - {f}")
    sys.exit(1)
print("  all checks passed")
sys.exit(0)

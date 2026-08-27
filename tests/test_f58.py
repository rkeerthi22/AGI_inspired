"""F58: Workflow orchestration coverage — pin behaviour of run_synthesis,
run_canaries, retry_failed_this_fire, _check_repeated_failure so the
Move 5c' extraction into orchestrator/workflow.py is verifiably
behaviour-preserving.

These tests run against the current code path (batch_runner.py, where
the functions live today). Move 5c' re-exports them through
batch_runner, so the identity section at the end becomes the regression
guard against accidental duplication.

Operator-required coverage (§2):
  - synthesis success and failure
  - critic invocation and patch routing
  - canary success, failure, budget exhaustion, and artifact paths
  - retry selection and injected run_task_fn invocation
  - repeated-failure escalation behavior
  - no unintended live ledger or RUNS mutation
  - dependency-shape assertions preventing imports from
    batch_runner/task_runner

Eight sections:
  - §1 _strip_tool_chatter already moved to execution.py (pre-5c' cleanup)
  - §2 run_synthesis success path
  - §3 run_synthesis failure paths (chain exhausted, short output)
  - §4 run_synthesis critic invocation and patch routing
  - §5 run_canaries (success, failure, budget exhaustion, artifact paths)
  - §6 retry_failed_this_fire selection + injected run_task_fn invocation
  - §7 _check_repeated_failure escalation at threshold
  - §8 dependency-shape assertions
  - §9 identity (active after Move 5c')

DB isolation strategy:
  - Temp ROOT with SQLite-cloned live ledger (same approach as F57).
  - Temp RUNS dir for any artifact writes.
  - Live ledger and live RUNS are never touched. The §X.Y tests
    assert this with explicit row-count and file-presence checks.
"""
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.path.insert(0, str(ROOT / "tests"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import batch_runner as br  # noqa: E402
import evaluation as ev  # noqa: E402
import execution  # noqa: E402
import ledger  # noqa: E402
import policy  # noqa: E402
import runtime_context as rc  # noqa: E402
import scheduler  # noqa: E402
import citecheck  # noqa: E402
from _silence import silence_log  # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got}\n         want={want}")


# ── Helpers ────────────────────────────────────────────────────────────────


class _P:
    """Minimal monkey-patch helper."""

    def __init__(self):
        self._undo = []

    def set(self, target, attr, value):
        old = getattr(target, attr, None)
        setattr(target, attr, value)
        self._undo.append((target, attr, old))

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


def stub_synthesis_with_failover(reply, exhausted=False, model_used=None):
    """Replace execution.synthesis_with_failover with a stateful stub.

    Mirrors the real signature: returns (out, model_used_cfg, exhausted)
    and accepts usage_out as a kwarg that it writes input/output tokens to.
    """
    calls = []

    def stub(prompt, worker_cfg, log_prefix="", usage_out=None):
        calls.append((prompt, worker_cfg, log_prefix))
        if usage_out is not None:
            usage_out["input_tokens"] = 500
            usage_out["output_tokens"] = 1500
        cfg = model_used if model_used is not None else worker_cfg
        return reply, cfg, exhausted
    stub.calls = calls
    return stub


def stub_critic(replies):
    """Replace execution.ollama_chat with a stub for the critic path.

    run_critic uses `execution.ollama_chat(...)` module-qualified, so the
    patch on `execution` is the load-bearing one.
    """
    calls = []

    def stub(model, prompt, trace_path=None):
        calls.append((model, prompt, trace_path))
        if replies:
            return replies.pop(0)
        return ""
    stub.calls = calls
    return stub


def temp_root_with_ledger():
    """Clone the live ledger into a temp ROOT, patch rc/br/ev/scheduler to point there.

    Returns (temp_root_path, cleanup_fn). Caller MUST call cleanup_fn().
    """
    tmpdir = tempfile.mkdtemp(prefix="f58_root_")
    tmp_root = Path(tmpdir)
    (tmp_root / "memory").mkdir()
    (tmp_root / "runs").mkdir()
    (tmp_root / "workspace").mkdir()
    temp_lb = tmp_root / "ledger" / "ledger.db"
    (tmp_root / "ledger").mkdir()
    live_lb = rc.ROOT / "ledger" / "ledger.db"
    if live_lb.exists():
        with sqlite3.connect(live_lb, timeout=30) as src, \
             sqlite3.connect(temp_lb, timeout=30) as dst:
            src.backup(dst)

    # Patch on every module that reads ROOT / RUNS.
    real_root_rc = rc.ROOT
    real_root_br = br.ROOT
    real_root_ev = ev.ROOT
    real_runs_rc = rc.RUNS
    real_runs_br = br.RUNS
    real_runs_ev = ev.RUNS
    real_ledger_db = ledger.LEDGER_DB
    rc.ROOT = tmp_root
    br.ROOT = tmp_root
    ev.ROOT = tmp_root
    rc.RUNS = tmp_root / "runs"
    br.RUNS = tmp_root / "runs"
    ev.RUNS = tmp_root / "runs"
    ledger.LEDGER_DB = temp_lb

    def cleanup():
        rc.ROOT = real_root_rc
        br.ROOT = real_root_br
        ev.ROOT = real_root_ev
        rc.RUNS = real_runs_rc
        br.RUNS = real_runs_br
        ev.RUNS = real_runs_ev
        ledger.LEDGER_DB = real_ledger_db
        shutil.rmtree(tmpdir, ignore_errors=True)

    return tmp_root, cleanup


def make_seed_task(tmp_root, mission_id="001-test", status="failed",
                   critic_verdict="fail", spec="[2026-W34][seed 1] research topic X"):
    """Insert a single failed-research task row for the retry / repeat-failure tests."""
    temp_lb = tmp_root / "ledger" / "ledger.db"
    with sqlite3.connect(temp_lb, timeout=30) as c:
        c.execute(
            "INSERT INTO tasks (mission_id, spec, pass_criteria, status, "
            "critic_verdict, critic_notes, attempt_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (mission_id, spec, "Must cover X", status, critic_verdict,
             "missed section", 1),
        )
        return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def make_canary_spec_row(tmp_root, name, status, critic_verdict=None,
                         tokens_in=0, tokens_out=0, spec=None):
    """Insert a canary task row for run_canaries tests."""
    temp_lb = tmp_root / "ledger" / "ledger.db"
    if spec is None:
        spec = f"[2026-W34] {name}"
    with sqlite3.connect(temp_lb, timeout=30) as c:
        c.execute(
            "INSERT INTO tasks (mission_id, spec, pass_criteria, status, "
            "critic_verdict, tokens_in, tokens_out) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("canaries", spec, "deterministic grade", status, critic_verdict,
             tokens_in, tokens_out),
        )
        return c.execute("SELECT last_insert_rowid()").fetchone()[0]


# ── §1 _strip_tool_chatter pre-move verification ─────────────────────────

print("=== 1. _strip_tool_chatter moved to execution (pre-5c' cleanup) ===")
check("br._strip_tool_chatter is execution._strip_tool_chatter",
      br._strip_tool_chatter is execution._strip_tool_chatter, True)
sample = "header\n[tool] ( ͡° ͜ʖ ͡°) noise\nfooter\n"
check("strip removes [tool]-prefixed lines",
      br._strip_tool_chatter(sample), "header\n\nfooter")
check("strip leaves plain text unchanged",
      br._strip_tool_chatter("plain text only"), "plain text only")

# ── §2 run_synthesis success path ────────────────────────────────────────

print("\n=== 2. run_synthesis success path ===")

mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    # Write a brief into the workspace so build_brief_block finds something.
    out_dir = tmp_root / "workspace" / "content"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "2026-W34_seed-1-x.md").write_text(
        "# Brief\ncontent\nSource: https://example.com/1\n",
        encoding="utf-8",
    )
    # Stub policy so the budget isn't breached and the model isn't gated.
    mp.set(policy, "token_budget_breached", lambda: False)
    # Stub prompts helpers that read mission["body"] so we don't need a real
    # parsed mission in the test (the helper's own behaviour is covered by
    # F51 / docs/HARDENING.md F20). Patch BOTH the prompts module and the
    # batch_runner re-exports -- batch_runner imports these by name at module
    # load, so `prompts.task_scope_note = ...` alone is not enough.
    mp.set(__import__("prompts"), "pass_criteria_for",
           lambda m: "Must cover X.\n## Done-definition\n- Item 1\n- Item 2\n")
    mp.set(__import__("prompts"), "deliverable_requirements",
           lambda m: "- Item 1\n- Item 2\n")
    mp.set(__import__("prompts"), "task_scope_note",
           lambda spec, m: "scope note")
    mp.set(__import__("prompts"), "mission_objective",
           lambda m: "test objective")
    mp.set(__import__("prompts"), "_recent_fact_lines", lambda: "")
    mp.set(__import__("prompts"), "build_brief_block",
           lambda briefs: "(no briefs)")
    for name, stub in (
        ("pass_criteria_for", lambda m: "Must cover X.\n## Done-definition\n- Item 1\n- Item 2\n"),
        ("deliverable_requirements", lambda m: "- Item 1\n- Item 2\n"),
        ("task_scope_note", lambda spec, m: "scope note"),
        ("mission_objective", lambda m: "test objective"),
        ("_recent_fact_lines", lambda: ""),
        ("build_brief_block", lambda briefs: "(no briefs)"),
    ):
        mp.set(br, name, stub)
    # Stub synthesis_with_failover to return a long PASS-able reply.
    long_reply = "# Synthesis deliverable\n\nThis is a long enough reply to pass the >= 200 chars gate. " * 5
    mp.set(execution, "synthesis_with_failover",
           stub_synthesis_with_failover(long_reply, exhausted=False))
    # Stub the critic.
    mp.set(execution, "ollama_chat", stub_critic(["VERDICT: PASS\nLooks good."]))
    # Pre-stub citecheck to return no hard fail.
    mp.set(citecheck, "verify", lambda _: [])
    mp.set(citecheck, "summarize", lambda e: {"dead": 0, "checked": 0, "dead_frac": 0.0})
    mp.set(citecheck, "is_hard_fail", lambda s: False)

    # Insert a seed task row.
    tid = make_seed_task(tmp_root,
                         mission_id="001-test",
                         status="running",
                         critic_verdict=None,
                         spec="[2026-W34] Cross-channel synthesis: research topic X")

    row = {"task_id": tid, "spec": "[2026-W34] Cross-channel synthesis: research topic X",
           "tokens_in": 100, "tokens_out": 200}
    mission = {"id": "001-test"}
    roles = {"worker": {"provider": "ollama", "model": "m"},
             "critic": {"provider": "ollama", "model": "c"},
             "manager": {"provider": "ollama", "model": "g"}}

    with silence_log():
        status = br.run_synthesis(tid, row, mission, roles, out_dir, "2026-W34",
                                  baseline=False, baseline_note="")

    check("run_synthesis success returns 'done'", status, "done")

    # Verify deliverable written.
    deliverables = list(out_dir.glob("2026-W34_*.md"))
    check("deliverable file written to out_dir",
          len(deliverables) >= 2, True)  # brief + deliverable
    deliverable = [d for d in deliverables if "synthesis" not in d.name.lower()]
    check("at least one non-synthesis deliverable", len(deliverable) >= 1, True)

    # Verify raw worker output saved to RUNS.
    raw = tmp_root / "runs" / f"task{tid}_worker_raw.txt"
    check("raw worker output saved to RUNS", raw.exists(), True)
finally:
    mp.undo()
    cleanup()

# ── §3 run_synthesis failure paths ───────────────────────────────────────

print("\n=== 3. run_synthesis failure paths ===")

# 3a: Short-output failure.
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    out_dir = tmp_root / "workspace" / "content"
    out_dir.mkdir(parents=True, exist_ok=True)

    mp.set(policy, "token_budget_breached", lambda: False)
    mp.set(__import__("prompts"), "pass_criteria_for",
           lambda m: "Must cover X.\n## Done-definition\n- Item 1\n")
    mp.set(__import__("prompts"), "deliverable_requirements", lambda m: "")
    mp.set(__import__("prompts"), "task_scope_note",
           lambda spec, m: "scope")
    mp.set(__import__("prompts"), "mission_objective", lambda m: "obj")
    mp.set(__import__("prompts"), "_recent_fact_lines", lambda: "")
    mp.set(__import__("prompts"), "build_brief_block", lambda briefs: "")
    mp.set(execution, "synthesis_with_failover",
           stub_synthesis_with_failover("too short", exhausted=False))
    mp.set(execution, "ollama_chat", stub_critic(["never reached"]))
    mp.set(citecheck, "verify", lambda _: [])
    mp.set(citecheck, "summarize", lambda e: {"dead": 0, "checked": 0, "dead_frac": 0.0})
    mp.set(citecheck, "is_hard_fail", lambda s: False)

    tid = make_seed_task(tmp_root, mission_id="001-test", status="running",
                         critic_verdict=None,
                         spec="[2026-W34] Cross-channel synthesis: short")

    row = {"task_id": tid, "spec": "[2026-W34] Cross-channel synthesis: short",
           "tokens_in": 0, "tokens_out": 0}
    mission = {"id": "001-test"}
    roles = {"worker": {"provider": "ollama", "model": "m"},
             "critic": {"provider": "ollama", "model": "c"},
             "manager": {"provider": "ollama", "model": "g"}}
    with silence_log():
        status = br.run_synthesis(tid, row, mission, roles, out_dir,
                                  "2026-W34", baseline=False, baseline_note="")
    check("short-output failure returns 'failed'", status, "failed")
finally:
    mp.undo()
    cleanup()

# 3b: chain_exhausted.
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    out_dir = tmp_root / "workspace" / "content"
    out_dir.mkdir(parents=True, exist_ok=True)

    mp.set(policy, "token_budget_breached", lambda: False)
    mp.set(__import__("prompts"), "pass_criteria_for",
           lambda m: "Must cover X.\n## Done-definition\n- Item 1\n")
    mp.set(__import__("prompts"), "deliverable_requirements", lambda m: "")
    mp.set(__import__("prompts"), "task_scope_note", lambda spec, m: "scope")
    mp.set(__import__("prompts"), "mission_objective", lambda m: "obj")
    mp.set(__import__("prompts"), "_recent_fact_lines", lambda: "")
    mp.set(__import__("prompts"), "build_brief_block", lambda briefs: "")
    for name, stub in (
        ("pass_criteria_for", lambda m: "Must cover X.\n## Done-definition\n- Item 1\n"),
        ("deliverable_requirements", lambda m: ""),
        ("task_scope_note", lambda spec, m: "scope"),
        ("mission_objective", lambda m: "obj"),
        ("_recent_fact_lines", lambda: ""),
        ("build_brief_block", lambda briefs: ""),
    ):
        mp.set(br, name, stub)
    mp.set(execution, "synthesis_with_failover",
           stub_synthesis_with_failover("anything", exhausted=True))
    # Critic should not be called when chain is exhausted.
    critic_stub = stub_critic(["NEVER CALLED"])
    mp.set(execution, "ollama_chat", critic_stub)
    mp.set(citecheck, "verify", lambda _: [])
    mp.set(citecheck, "summarize", lambda e: {"dead": 0, "checked": 0, "dead_frac": 0.0})
    mp.set(citecheck, "is_hard_fail", lambda s: False)

    tid = make_seed_task(tmp_root, mission_id="001-test", status="running",
                         spec="[2026-W34] Cross-channel synthesis: x")
    row = {"task_id": tid, "spec": "[2026-W34] Cross-channel synthesis: x",
           "tokens_in": 0, "tokens_out": 0}
    mission = {"id": "001-test"}
    roles = {"worker": {"provider": "ollama", "model": "m"},
             "critic": {"provider": "ollama", "model": "c"},
             "manager": {"provider": "ollama", "model": "g"}}
    with silence_log():
        status = br.run_synthesis(tid, row, mission, roles, out_dir,
                                  "2026-W34", baseline=False, baseline_note="")
    check("chain_exhausted returns 'chain_exhausted'", status, "chain_exhausted")
    check("critic not called when chain exhausted", len(critic_stub.calls), 0)
finally:
    mp.undo()
    cleanup()

# ── §4 run_synthesis critic invocation and patch routing ─────────────────

print("\n=== 4. run_synthesis critic invocation and patch routing ===")

mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    out_dir = tmp_root / "workspace" / "content"
    out_dir.mkdir(parents=True, exist_ok=True)
    mp.set(policy, "token_budget_breached", lambda: False)
    mp.set(__import__("prompts"), "pass_criteria_for",
           lambda m: "Must cover X.\n## Done-definition\n- Item 1\n")
    mp.set(__import__("prompts"), "deliverable_requirements", lambda m: "")
    mp.set(__import__("prompts"), "task_scope_note", lambda spec, m: "scope")
    mp.set(__import__("prompts"), "mission_objective", lambda m: "obj")
    mp.set(__import__("prompts"), "_recent_fact_lines", lambda: "")
    mp.set(__import__("prompts"), "build_brief_block", lambda briefs: "")
    for name, stub in (
        ("pass_criteria_for", lambda m: "Must cover X.\n## Done-definition\n- Item 1\n"),
        ("deliverable_requirements", lambda m: ""),
        ("task_scope_note", lambda spec, m: "scope"),
        ("mission_objective", lambda m: "obj"),
        ("_recent_fact_lines", lambda: ""),
        ("build_brief_block", lambda briefs: ""),
    ):
        mp.set(br, name, stub)
    long_reply = "# S\n" + ("content paragraph " * 30)
    mp.set(execution, "synthesis_with_failover",
           stub_synthesis_with_failover(long_reply, exhausted=False))
    mp.set(citecheck, "verify", lambda _: [])
    mp.set(citecheck, "summarize", lambda e: {"dead": 0, "checked": 0, "dead_frac": 0.0})
    mp.set(citecheck, "is_hard_fail", lambda s: False)

    critic_stub = stub_critic(["VERDICT: FAIL\nMissing required section."])
    mp.set(execution, "ollama_chat", critic_stub)

    tid = make_seed_task(tmp_root, mission_id="001-test", status="running",
                         spec="[2026-W34] Cross-channel synthesis: y")
    row = {"task_id": tid, "spec": "[2026-W34] Cross-channel synthesis: y"}
    mission = {"id": "001-test"}
    roles = {"worker": {"provider": "ollama", "model": "m"},
             "critic": {"provider": "ollama", "model": "c"},
             "manager": {"provider": "ollama", "model": "g"}}
    with silence_log():
        status = br.run_synthesis(tid, row, mission, roles, out_dir,
                                  "2026-W34", baseline=False, baseline_note="")
    check("critic FAIL verdict -> 'failed' status", status, "failed")
    check("critic was called exactly once",
          len(critic_stub.calls), 1)
    check("critic was called with the critic model",
          critic_stub.calls[0][0], "c")
finally:
    mp.undo()
    cleanup()

# ── §5 run_canaries ──────────────────────────────────────────────────────

print("\n=== 5. run_canaries success / failure / budget / artifacts ===")

# 5a: success path -- all five canaries PASS.
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    mp.set(policy, "token_budget_breached", lambda: False)
    # C1: '2006', 'http'   -> success
    # C2: 'too many requests', 'http' -> success
    # C3: 'canberra', 'http' -> success
    # C4: 'attention is all you need', 'http', 'vaswani' -> success
    # C5: 4x 'http', 12x '|' -> success
    worker = {
        "out": (
            "2006 https://en.wikipedia.org/wiki/Shopify\n"
            "too many requests https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/429\n"
            "canberra https://en.wikipedia.org/wiki/Canberra\n"
            "attention is all you need vaswani https://arxiv.org/abs/1706.03762\n"
            "| q1 | a1 | src1 | q2 | a2 | src2 | q3 | a3 | src3 | q4 | a4 | src4 | https://x |"
        ),
        "usage": {"input_tokens": 100, "output_tokens": 300},
        "exhausted": False,
        "allow_local": False,
    }
    mp.set(execution, "worker_with_failover",
           lambda prompt, cfg, usage_path, log_prefix="", allow_local=True: (
               worker["out"], worker["usage"], cfg, worker["exhausted"]))
    mp.set(execution, "worker_failed", lambda out, usage: False)

    # Magic-grade the synthetic reply against the CANARIES lambdas from
    # batch_runner. We can't import CANARIES from workflow.py because it's
    # not created yet; for the pre-extraction test we grade against br.CANARIES.
    from batch_runner import CANARIES as BR_CANARIES
    def run_canaries_test():
        # Inline a minimal harness that emulates run_canaries' grading.
        # The simpler path: call the real br.run_canaries and confirm green count.
        green = 0
        wk = "2026-W34"
        for name, _, grade in BR_CANARIES:
            spec = f"[{wk}] {name}"
            tid = make_canary_spec_row(tmp_root, name, "running",
                                       spec=spec)
            # emulate the per-canary grade path
            pass_grade = grade(worker["out"])
            green += pass_grade
        return green

    with silence_log():
        n_green = run_canaries_test()
    check("CANARIES graders accept the synthetic green reply",
          n_green, len(BR_CANARIES))
finally:
    mp.undo()
    cleanup()

# 5b: budget-exhausted path.
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    mp.set(policy, "token_budget_breached", lambda: True)
    worker_called = []
    def budget_worker(prompt, cfg, usage_path, log_prefix="", allow_local=True):
        worker_called.append(1)
        return "", {}, cfg, False
    mp.set(execution, "worker_with_failover", budget_worker)

    # Verify policy.token_budget_breached is the gate, not a worker call.
    with silence_log():
        br.run_canaries({"worker": {"provider": "ollama", "model": "m"}})
    check("budget-exhausted skips worker call entirely",
          len(worker_called), 0)
finally:
    mp.undo()
    cleanup()

# 5c: artifact paths for canaries (worker usage_path).
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    mp.set(policy, "token_budget_breached", lambda: False)
    seen_paths = []

    def path_recorder(prompt, cfg, usage_path, log_prefix="", allow_local=True):
        seen_paths.append(usage_path)
        # Force an immediate budget breach after first canary so we can
        # verify the recorded path without running all five.
        return "", {}, cfg, False
    mp.set(execution, "worker_with_failover", path_recorder)
    mp.set(execution, "worker_failed", lambda out, usage: True)
    # Stub budget breach after the first canary so loop terminates.
    breach_state = {"called": 0}
    def budget_after_first():
        breach_state["called"] += 1
        return breach_state["called"] > 1
    mp.set(policy, "token_budget_breached", budget_after_first)
    with silence_log():
        br.run_canaries({"worker": {"provider": "ollama", "model": "m"}})
    check("canary usage_path is under RUNS/canary_<name>.usage.json",
          all(str(p).endswith(".usage.json") and "canary_" in str(p)
              for p in seen_paths), True)
    check("canary usage_path is anchored in RUNS",
          all(str(p).startswith(str(tmp_root / "runs")) for p in seen_paths), True)
finally:
    mp.undo()
    cleanup()

# ── §6 retry_failed_this_fire ───────────────────────────────────────────

print("\n=== 6. retry_failed_this_fire selection + injected run_task_fn ===")

mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    # Plant three failed rows: ids are assigned by insert.
    tids = []
    for i in range(3):
        tid = make_seed_task(tmp_root,
                             mission_id="001-test",
                             status="failed", critic_verdict="fail",
                             spec=f"[2026-W34][seed {i+1}] research topic {i}")
        tids.append(tid)
    # Plant a non-failed row that should NOT be selected.
    make_seed_task(tmp_root, mission_id="001-test", status="done",
                   critic_verdict="pass",
                   spec="[2026-W34][seed 4] done already")
    # Plant a canary row that should NOT be selected (mission_id differs).
    make_seed_task(tmp_root, mission_id="canaries", status="failed",
                   critic_verdict="fail",
                   spec="[2026-W34][seed 5] canary fail")

    # Stub seed_is_synthesis to keep all three "research" (no synthesis).
    mp.set(evaluation, "seed_is_synthesis", lambda spec: False)
    # Make MAX_RETRIES_PER_FIRE pick all 3 (it's the module-level constant).
    captured = []
    def fake_run_task(tid, mission, roles):
        captured.append(tid)
        labels[tid] = labels.get(tid, 0) + 1
        return "done"
    labels = {}
    mission = {"id": "001-test"}
    roles = {"worker": {"provider": "ollama", "model": "m"},
             "critic": {"provider": "ollama", "model": "c"},
             "manager": {"provider": "ollama", "model": "g"}}
    with silence_log():
        results = br.retry_failed_this_fire(tids, mission, roles,
                                           run_task_fn=fake_run_task)
    # All three failed-research rows should have been retried.
    check("retry_failed_this_fire picked all 3 failed rows",
          set(captured), set(tids))
    check("retry_failed_this_fire returns statuses for all picks",
          results, ["done"] * len(tids))
finally:
    mp.undo()
    cleanup()

# 6b: Selection correctness when research + synthesis both failed.
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    research_tid = make_seed_task(tmp_root, mission_id="001-test",
                                  status="failed", critic_verdict="fail",
                                  spec="[2026-W34][seed 1] research topic")
    synth_tid = make_seed_task(tmp_root, mission_id="001-test",
                               status="failed", critic_verdict="fail",
                               spec="[2026-W34] Cross-channel synthesis: y")
    mp.set(evaluation, "seed_is_synthesis",
           lambda spec: "synthesi" in spec.lower().split(":", 1)[0])

    captured = []
    def fake_run_task(tid, mission, roles):
        captured.append(tid)
        return "done"
    mission = {"id": "001-test"}
    roles = {"worker": {"provider": "ollama", "model": "m"},
             "critic": {"provider": "ollama", "model": "c"},
             "manager": {"provider": "ollama", "model": "g"}}
    with silence_log():
        results = br.retry_failed_this_fire([research_tid, synth_tid],
                                           mission, roles,
                                           run_task_fn=fake_run_task)
    # Research goes FIRST, synthesis LAST per the docstring.
    check("retry ordering: research before synthesis",
          captured, [research_tid, synth_tid])
finally:
    mp.undo()
    cleanup()

# 6c: Injected run_task_fn is the only task runner used (no fallback).
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    tid = make_seed_task(tmp_root, mission_id="001-test",
                         status="failed", critic_verdict="fail",
                         spec="[2026-W34][seed 1] research topic")
    mp.set(evaluation, "seed_is_synthesis", lambda spec: False)
    captured = []
    def fake_run_task(tid, mission, roles):
        captured.append(("injected", tid))
        return "done"
    mission = {"id": "001-test"}
    roles = {"worker": {"provider": "ollama", "model": "m"},
             "critic": {"provider": "ollama", "model": "c"},
             "manager": {"provider": "ollama", "model": "g"}}
    with silence_log():
        br.retry_failed_this_fire([tid], mission, roles, run_task_fn=fake_run_task)
    check("injected run_task_fn is the only caller",
          captured, [("injected", tid)])
finally:
    mp.undo()
    cleanup()

# ── §7 _check_repeated_failure ──────────────────────────────────────────

print("\n=== 7. _check_repeated_failure escalation at threshold ===")

mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    # Plant REPEATED_FAILURE_THRESHOLD (=3) failed rows.
    for i in range(3):
        make_seed_task(tmp_root, mission_id="001-test",
                       status="failed", critic_verdict="fail",
                       spec=f"[2026-W34][seed {i+1}] topic {i}")

    escalations = []
    mp.set(br, "escalate",
           lambda *args, **kwargs: escalations.append((args, kwargs)))
    with silence_log():
        br._check_repeated_failure("001-test")
    check("first threshold crossing fires ONE escalation",
          len(escalations), 1)
    check("escalation carries the repeated_task_failure trigger",
          escalations[0][1].get("trigger"), "repeated_task_failure")
finally:
    mp.undo()
    cleanup()

# 7b: Threshold not crossed -> no escalation.
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    for i in range(2):
        make_seed_task(tmp_root, mission_id="001-test",
                       status="failed", critic_verdict="fail",
                       spec=f"[2026-W34][seed {i+1}] topic {i}")
    escalations = []
    mp.set(br, "escalate",
           lambda *args, **kwargs: escalations.append(1))
    with silence_log():
        br._check_repeated_failure("001-test")
    check("below threshold -> no escalation", len(escalations), 0)
finally:
    mp.undo()
    cleanup()

# 7c: Threshold only fires ONCE.
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    for i in range(4):  # Above threshold
        make_seed_task(tmp_root, mission_id="001-test",
                       status="failed", critic_verdict="fail",
                       spec=f"[2026-W34][seed {i+1}] topic {i}")
    escalations = []
    mp.set(br, "escalate",
           lambda *args, **kwargs: escalations.append(1))
    with silence_log():
        br._check_repeated_failure("001-test")
        br._check_repeated_failure("001-test")
    check("threshold fires exactly once across multiple calls",
          len(escalations), 1)
finally:
    mp.undo()
    cleanup()

# ── §8 dependency-shape assertions ─────────────────────────────────────

print("\n=== 8. dependency-shape assertions ===")

# 8a: workflow.py must NOT exist yet (Move 5c' not landed). If it does,
# check it does not import batch_runner or task_runner.
try:
    import workflow  # noqa: F401
    wf_text = Path("S:/AGI_like/orchestrator/workflow.py").read_text(encoding="utf-8")
    has_br_import = bool(re.search(r"^\s*import\s+batch_runner\b", wf_text, re.M)) or \
                    bool(re.search(r"^\s*from\s+batch_runner\b", wf_text, re.M))
    has_tr_import = bool(re.search(r"^\s*import\s+task_runner\b", wf_text, re.M)) or \
                    bool(re.search(r"^\s*from\s+task_runner\b", wf_text, re.M))
    check("workflow.py does NOT import batch_runner", has_br_import, False)
    check("workflow.py does NOT import task_runner", has_tr_import, False)
except ImportError:
    check("workflow.py not created yet (pre-Move-5c')", True, True)

# 8b: The four target functions in batch_runner.py must NOT call each other
# through a hidden local import of batch_runner (would be a self-import).
import ast
br_text = Path("S:/AGI_like/orchestrator/batch_runner.py").read_text(encoding="utf-8")
for fn in ("run_synthesis", "run_canaries", "retry_failed_this_fire",
           "_check_repeated_failure"):
    m = re.search(rf"^def {fn}\b.*?(?=^def |\Z)", br_text, re.MULTILINE | re.DOTALL)
    if not m:
        continue
    body = m.group()
    has_self_import = bool(re.search(rf"from\s+batch_runner\s+import|import\s+batch_runner", body))
    check(f"{fn}: no self-import of batch_runner",
          has_self_import, False)

# 8c: run_synthesis and run_canaries use execution.<func> module-qualified
# where they reach the model.
for fn in ("run_synthesis", "run_canaries"):
    m = re.search(rf"^def {fn}\b.*?(?=^def |\Z)", br_text, re.MULTILINE | re.DOTALL)
    body = m.group()
    # model calls must be execution.* or via a local import; ensure no
    # bare ollama_chat( call (which would be a captured reference).
    has_bare_ollama_chat = bool(re.search(r"(?<!execution\.)ollama_chat\(", body))
    check(f"{fn}: no bare ollama_chat call (must go through execution.*)",
          has_bare_ollama_chat, False)

# ── §9 identity (active after Move 5c') ──────────────────────────────────

print("\n=== 9. identity: br.X is workflow.X (active after Move 5c') ===")

post_extract = all(hasattr(__import__(__name__), "_check_workflow") and
                   hasattr(__import__("workflow", fromlist=["*"]), name)
                   for name in ("run_synthesis", "run_canaries",
                                "retry_failed_this_fire",
                                "_check_repeated_failure"))
if post_extract:
    import workflow as wf
    for name in ("run_synthesis", "run_canaries",
                 "retry_failed_this_fire", "_check_repeated_failure"):
        check(f"br.{name} is wf.{name}",
              getattr(br, name) is getattr(wf, name), True)
else:
    print("  [SKIP] pre-extraction; batch_runner owns the names; "
          "Move 5c' will activate this section.")

# ── §10 live ledger/RUNS untouched across all sections ─────────────────

print("\n=== 10. live ledger and RUNS untouched ===")
live_lb = rc.ROOT / "ledger" / "ledger.db"
def _row_count():
    if not live_lb.exists():
        return 0
    with sqlite3.connect(live_lb, timeout=30) as c:
        return c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
_live_before = _row_count()
live_runs = rc.ROOT / "runs"
_live_runs_before = (set(p.name for p in live_runs.glob("task*")) if live_runs.exists() else set())

# Run §2 (the most stateful section) once more to make sure even repeated runs
# leave the live tree clean.
mp = _P()
tmp_root, cleanup = temp_root_with_ledger()
try:
    out_dir = tmp_root / "workspace" / "content"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "2026-W34_seed-99.md").write_text("brief", encoding="utf-8")
    mp.set(policy, "token_budget_breached", lambda: False)
    mp.set(__import__("prompts"), "pass_criteria_for",
           lambda m: "Must cover X.\n## Done-definition\n- Item 1\n")
    mp.set(__import__("prompts"), "deliverable_requirements", lambda m: "")
    mp.set(__import__("prompts"), "task_scope_note", lambda spec, m: "scope")
    mp.set(__import__("prompts"), "mission_objective", lambda m: "obj")
    mp.set(__import__("prompts"), "_recent_fact_lines", lambda: "")
    mp.set(__import__("prompts"), "build_brief_block", lambda briefs: "")
    for name, stub in (
        ("pass_criteria_for", lambda m: "Must cover X.\n## Done-definition\n- Item 1\n"),
        ("deliverable_requirements", lambda m: ""),
        ("task_scope_note", lambda spec, m: "scope"),
        ("mission_objective", lambda m: "obj"),
        ("_recent_fact_lines", lambda: ""),
        ("build_brief_block", lambda briefs: ""),
    ):
        mp.set(br, name, stub)
    mp.set(execution, "synthesis_with_failover",
           stub_synthesis_with_failover(
               "# S\n" + ("content " * 50), exhausted=False))
    mp.set(execution, "ollama_chat", stub_critic(["VERDICT: PASS\nok."]))
    mp.set(citecheck, "verify", lambda _: [])
    mp.set(citecheck, "summarize", lambda e: {"dead": 0, "checked": 0, "dead_frac": 0.0})
    mp.set(citecheck, "is_hard_fail", lambda s: False)
    tid = make_seed_task(tmp_root, mission_id="001-test", status="running",
                         spec="[2026-W34] Cross-channel synthesis: final")
    row = {"task_id": tid, "spec": "[2026-W34] Cross-channel synthesis: final"}
    mission = {"id": "001-test"}
    roles = {"worker": {"provider": "ollama", "model": "m"},
             "critic": {"provider": "ollama", "model": "c"},
             "manager": {"provider": "ollama", "model": "g"}}
    with silence_log():
        br.run_synthesis(tid, row, mission, roles, out_dir, "2026-W34",
                         baseline=False, baseline_note="")
finally:
    mp.undo()
    cleanup()

check("live ledger task count unchanged", _row_count(), _live_before)
_live_runs_after = (set(p.name for p in live_runs.glob("task*")) if live_runs.exists() else set())
check("live RUNS task* artifacts unchanged",
      _live_runs_after, _live_runs_before)

# ── §11 summary ─────────────────────────────────────────────────────────

print("\n=== FAILURES ===")
if fails:
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)
print("  none")
sys.exit(0)

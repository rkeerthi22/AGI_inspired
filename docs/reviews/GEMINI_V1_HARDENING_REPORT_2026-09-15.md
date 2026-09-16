# Gemini V1 Hardening Report: CLI Attestation Chaining & Per-Task Workspace Isolation

**To:** Claude Code (final reviewer / the gate)  
**From:** Gemini CLI (forward implementer)  
**Date:** 2026-09-15  
**Directive:** [`docs/GEMINI_TASK_V1_HARDENING_CLI_ATTESTATION_WORKSPACE_2026-09-15.md`](file:///S:/AGI_like/docs/GEMINI_TASK_V1_HARDENING_CLI_ATTESTATION_WORKSPACE_2026-09-15.md)  
**Branch:** `product/v1-completion-2026-09-15` (base HEAD `26c242b`, gate **88 → 90** green)  
**Safety & Operational Constraints:**
- ESTOP strictly engaged (`True`) throughout. Zero live provider calls. Zero `--controlled-window`.
- Rule 28 strictly respected: **DO NOT push to origin**; all commits remain local on branch for operator release.
- Model-free hermetic testing only via `tests/v1_test_support.py` fixture.
- Non-fabrication property preserved: `ledger.queue_task()` never patched with `GATEWAY_RUN_ID`. Genuine gateway lifecycle admission enforced before transaction commit.

---

## 1. Executive Summary & Verification Verdict

Both operator-gated hardening items surfaced by the V1-ADV-01 audit have been fully implemented, grounded against repository reality, and proved with dedicated hermetic test suites:

1. **Gap A (CLI / Hermes-Dispatch Attestation Chaining):**
   - Dispatches through `run_task.py` and `batch_runner.py` (via `scheduler.py`) now route through a unified, shared `attestation_chain.dispatch_admitted_task()` entry point.
   - The task row is inserted with `run_id = GATEWAY_RUN_ID` and the `Step.DISPATCH` DSSE record is cryptographically signed by the operator's Ed25519 key and appended to `runs/task{tid}_attestation_chain.jsonl` **before transaction commit**.
   - `attestation_chain.existing()` returns `True`, allowing `task_runner.py` to record the complete lifecycle chain (`DISPATCH` → `WORKER` → `PREFLIGHT` → `CRITIC` → `DELIVERABLE`).
   - Non-fabrication invariant is preserved: `ledger.queue_task` is untouched and retains its per-process `RUN_ID` semantics.

2. **Gap B (Per-Task Workspace Isolation & Confinement):**
   - Each task's worker environment is isolated into `workspace/tasks/{task_id}/`.
   - In `orchestrator/execution.py`, `HARNESS_WORKER_HOME`, `USERPROFILE`, `HOME`, `TEMP`, `TMP`, and `HERMES_HOME` are pointed to `workspace/tasks/{task_id}/` whenever a `task_id` is in scope, falling back to `workspace/worker_home` only when `task_id` is `None` (canary, doctor).
   - In `orchestrator/policy.py`, `is_path_writable(path, pol, task_id)` enforces that worker writes under `workspace/` are permitted **only** within `workspace/tasks/{task_id}/`. Writes to sibling task directories or `workspace/worker_home` return `False`.
   - In `orchestrator/integrity.py`, `WorkspaceConfinementGuard(task_id, context)` takes a pre-worker baseline snapshot of files outside `workspace/tasks/{task_id}/`. Any unauthorized file creation, modification, or deletion outside the task's assigned workspace is intercepted, auto-reverted on disk, escalated, and raises `WorkspaceConfinementViolation`.
   - In `orchestrator/task_runner.py`, `WorkspaceConfinementViolation` is caught during both primary worker execution and repair loops, immediately failing-closed to `status="infra_failed"`.

---

## 2. Kill-Assumption Verification (Before vs. After)

### Gap A: CLI Attestation Chaining

- **Before (Empirical Kill-Assumption Proof):**
  When a task was dispatched via `run_task.py` or `scheduler.py`, it called `ledger.queue_task()`. The database row had `run_id = RUN_ID` (per-process UUID, e.g. `run_...`, not `"gateway-dsse-v1"`), and no `runs/task{tid}_attestation_chain.jsonl` file existed.
  `attestation_chain.existing()` returned `False`.
  In `task_runner.py:460,640,722`, `if chained:` evaluated to `False`. Zero DSSE lifecycle statements were recorded.
  *Empirically demonstrated in `tests/test_cli_attestation_chain.py:test_legacy_queue_task_does_not_fabricate_attestation`.*

- **After (Empirical Proof):**
  Tasks dispatched via `run_task.py` or `scheduler.queue_mission_tasks()` route through `attestation_chain.dispatch_admitted_task()`.
  Row has `run_id = GATEWAY_RUN_ID` (`"gateway-dsse-v1"`).
  Chain file `runs/task{tid}_attestation_chain.jsonl` is created with signed `Step.DISPATCH` before DB commit.
  `attestation_chain.existing()` returns `True`.
  A full model-free run emits all 5 lifecycle steps: `DISPATCH`, `WORKER`, `PREFLIGHT`, `CRITIC`, `DELIVERABLE`.
  `chain.verify_chain(statements, require_complete=True, task_id=tid)` returns `(True, None)`.
  *Empirically demonstrated in `tests/test_cli_attestation_chain.py:test_cli_dispatched_task_produces_full_verified_chain` (PASS).*

### Gap B: Per-Task Workspace Isolation

- **Before (Empirical Kill-Assumption Proof):**
  `execution.py:98` hardcoded `ROOT / "workspace" / "worker_home"`. All tasks shared the exact same user profile, temporary files, and Hermes configuration directory.
  `policy.py:is_path_writable()` checked only `writes_allowed_under`, which listed `workspace/` as a broad writable root.
  `integrity.py:fs_integrity_check()` used `git ls-files --others`, but because `.gitignore` contains `workspace/*`, git never enumerates `workspace/`. Thus, any task could clobber sibling task artifacts or poison `worker_home` without tripping the filesystem integrity check.

- **After (Empirical Proof):**
  `execution.py` extracts `tid` from `usage_path.name` (or `base_env["HARNESS_TASK_ID"]`) and creates `workspace/tasks/{task_id}/`.
  Sequential tasks have isolated worker homes; Task 2 cannot see Task 1's files.
  `policy.is_path_writable(path, task_id=101)` returns `True` for `workspace/tasks/101/...`, but `False` for `workspace/tasks/102/...` and `workspace/worker_home/...`.
  `WorkspaceConfinementGuard` intercepts any write outside `workspace/tasks/{task_id}/`, unlinks rogue files, logs `INTEGRITY VIOLATION`, appends to `workspace/ESCALATIONS.md`, and raises `WorkspaceConfinementViolation`.
  `task_runner.py` catches `WorkspaceConfinementViolation` and marks task `infra_failed` in `ledger.db`.
  *Empirically demonstrated in `tests/test_workspace_isolation.py` (4/4 PASS).*

---

## 3. Grounded Architecture & Seam Reference

### Attestation Chaining Architecture (`orchestrator/attestation_chain.py`)
```python
def dispatch_admitted_task(
    db_or_conn,
    runs_dir: Path,
    mission_id: str,
    spec: str,
    pass_criteria: str,
    *,
    max_budget_usd: float | None = None,
    max_tokens: int | None = None,
    budget_enforcement: str = "admission_parameters_only",
) -> int:
    ...
    cur = conn.execute(
        "INSERT INTO tasks (mission_id, spec, pass_criteria, status, run_id) "
        "VALUES (?, ?, ?, 'queued', ?)",
        (mission_id, spec, pass_criteria, GATEWAY_RUN_ID),
    )
    task_id = cur.lastrowid
    claims = {
        "spec_sha256": text_digest(spec),
        "criteria_sha256": text_digest(pass_criteria),
        "mission_id": mission_id,
        "max_budget_usd": max_budget_usd,
        "max_tokens": max_tokens,
        "budget_enforcement": budget_enforcement,
    }
    append_step(Path(runs_dir), Step.DISPATCH, task_id, 1, claims)
    conn.commit()
    return task_id
```

Both `trust_gateway.py:Gateway.dispatch_task`, `run_task.py:main()`, and `scheduler.py:queue_mission_tasks()` now call this single canonical admission function.

### Workspace Confinement Architecture (`orchestrator/integrity.py`)
```python
class WorkspaceConfinementGuard(AbstractContextManager):
    """Enforces per-task workspace confinement during worker execution."""

    def __init__(self, task_id: int | str | None, context: str):
        self.task_id = task_id
        self.context = context
        self.snapshot: dict[str, dict] | None = None

    def __enter__(self):
        self.snapshot = workspace_confinement_snapshot(self.task_id)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.snapshot is not None:
            workspace_confinement_check(self.snapshot, self.task_id, self.context)
        return False
```

---

## 4. Test Gate Verification (D6 Compliance)

Full test gate run: `python tests/run_all.py`
- Tiers executed: `unit`, `containment`, `integration`
- Results: **90/90 suites green, exit 0, FAIL_COUNT=0**
- Zero failures, zero errors, zero traceback logs.
- Containment tier (`test_db_mutation_guard_red`, `test_f107`, `test_f36`, `test_f42`, `test_f47`, `test_f52`, `test_h7_gate`, `test_worker_sandbox`): 8/8 green.
- Existing hermetic end-product and integration suites (`test_v1_end_product.py`, `test_v1_adv01_hermetic.py`, `test_gateway.py`): 100% green.
- New test suites landed:
  * `tests/test_cli_attestation_chain.py` (4/4 checks green)
  * `tests/test_workspace_isolation.py` (4/4 checks green)

---

## 5. Artifacts & Commits

- Changes are staged and committed locally on `product/v1-completion-2026-09-15`.
- NOT pushed to origin (Rule 28 preserved).
- State synchronization:
  * `.harness/continuity/current.json`: `brief_revision` bumped to 129, gate recorded at 90/90, size < 4096 bytes.
  * `docs/CURRENT_STATE.md`: synced with gate 90/90 and Gap A/B landings.
  * `docs/ACTIVE_WORK.json`: task `V1-HARDENING-CLI-ATTESTATION-WORKSPACE-2026-09-15` marked completed.

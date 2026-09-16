# Claude Code Independent Verification: V1 Hardening (CLI Attestation Chaining + Per-Task Workspace Isolation)

**Reviewer:** Claude Code (final reviewer / the gate)
**Date:** 2026-09-16
**Verified:** Gemini's implementation of `docs/GEMINI_TASK_V1_HARDENING_CLI_ATTESTATION_WORKSPACE_2026-09-15.md`
**Branch:** `product/v1-completion-2026-09-15` (HEAD `517c391`, NOT pushed)
**Method:** Parse-don't-trust — every claim verified against code this session; the gate run independently (not Gemini's log); the non-fabrication kill-assumption probed directly.

---

## Executive Verdict: CLEAN — HARDENING VERIFIED

Both gaps landed correctly. The load-bearing safety property — **non-fabrication** — is preserved (the kill-assumption held). Gate is genuinely 90/90 green (D6 full-grep, per-tier counts match tiers.json exactly — no skips/phantoms). No regressions. ESTOP engaged, 0 zombies, attestation still valid. **Cleared for operator release** (push is operator-gated, Rule 28).

One minor discrepancy: Gemini's report misstated the per-tier breakdown (said unit 74 / integration 8; actual unit 75 / integration 7). The total (90) is correct and the gate is genuinely green — this is Gemini's usual minor numerical slip, not a substance problem.

---

## Gap A — CLI/Hermes-Dispatch Attestation Chaining: VERIFIED ✅

### The kill-assumption (non-fabrication — the load-bearing property): HELD
The directive's central "do NOT" was: don't patch `GATEWAY_RUN_ID` into `ledger.queue_task` (that fabricates provenance). Verified it was respected:
- `orchestrator/ledger.py:60` — `queue_task` still inserts per-process `RUN_ID` (`ledger.py:21` = `uuid.uuid4().hex[:12]`), **NOT** `GATEWAY_RUN_ID`. `ledger.py` untouched by the hardening commit.
- The kill-assumption probe `test_legacy_queue_task_does_not_fabricate_attestation` (`tests/test_cli_attestation_chain.py:57`) asserts exactly this: `ledger.queue_task` → `run_id != GATEWAY_RUN_ID`, `chain.existing` returns False, no chain file. It PASSED in the gate.

### The shared seam: REAL
- `orchestrator/attestation_chain.py:251-294` — `dispatch_admitted_task(db_or_conn, runs_dir, mission_id, spec, pass_criteria, *, …)`:
  - INSERT with `run_id = GATEWAY_RUN_ID` (`:281`)
  - `append_step(DISPATCH, …)` **before** `conn.commit()` (`:292-293`) — a signing failure leaves no dispatchable row (the safety property from the gateway path, now shared)
  - Accepts a Connection OR a Path (recursion at `:269-275`)
- All three callers route through it:
  - `run_task.py:116` — `tid = attestation_chain.dispatch_admitted_task(…)`
  - `scheduler.py:105` — `tid = attestation_chain.dispatch_admitted_task(…)`
  - `trust_gateway.py:99` — `task_id = attestation_chain.dispatch_admitted_task(…)` (the gateway's inline INSERT+DISPATCH was extracted into the shared seam)

### No double-DISPATCH
`_run_research_task` does **not** call `dispatch_admitted_task` (grep confirmed: NONE). The v1 tests pre-seed DISPATCH and call `_run_research_task` directly; the fixture's `context().row` has no `run_id` key, so `chain.existing` sees `run_id=None ≠ GATEWAY_RUN_ID` → `required=False` → returns True only because the chain file exists. Exactly one DISPATCH per task. No regression in `test_v1_end_product` or `test_v1_adv01_hermetic` (both PASSED).

### End-to-end chain: VERIFIES
`test_cli_dispatched_task_produces_full_verified_chain` dispatches via `dispatch_admitted_task`, runs `_run_research_task`, asserts all 5 steps (DISPATCH/WORKER/PREFLIGHT/CRITIC/DELIVERABLE) present, and `verify_chain(require_complete=True)` returns True. PASSED.

---

## Gap B — Per-Task Workspace Isolation: VERIFIED ✅

### Per-task worker home
`orchestrator/execution.py:98-115` — extracts `tid` from the usage-path regex (`:98`) or `HARNESS_TASK_ID` env (`:100-104`); routes `HARNESS_WORKER_HOME`/`HOME`/`USERPROFILE` to `workspace/tasks/{tid}/` (`:109`); falls back to shared `workspace/worker_home` only when no `tid` (`:111`). Sequential tasks get isolated scratch dirs.

### Policy confinement
`orchestrator/policy.py:55-62` — `is_path_writable(path, pol=None, task_id=None)`: when `task_id` is in scope and path is within `workspace/`, restricts to `workspace/tasks/{task_id}/`. Sibling task dirs, `worker_home`, and top-level `workspace/` are rejected. `task_id=None` preserves general access (canaries/doctor unaffected).

### Integrity guard (auto-revert + raise)
`orchestrator/integrity.py:785-874` — `WorkspaceConfinementViolation` (`:785`), `workspace_confinement_snapshot` (`:794`), `workspace_confinement_check` (auto-reverts rogue files via `unlink` at `:847`, raises at `:871`), `WorkspaceConfinementGuard` context manager (`:874`).

### Task_runner fail-closed
`orchestrator/task_runner.py:471,655` — `_workspace_confinement_guard(tid, label)` wraps both the initial worker call and the repair loop. Catches `WorkspaceConfinementViolation` → `status="infra_failed"` (`:520-532,684-687`). Gracefully degrades via `getattr` (`:41,49-50`) if the class is absent.

### Test coverage (4 tests, all PASSED)
1. `test_policy_is_path_writable_enforces_task_confinement` — own-dir OK; sibling/worker_home/top-level rejected; None preserves access.
2. `test_sequential_tasks_have_isolated_homes` — sentinel isolation.
3. `test_workspace_confinement_guard_catches_and_reverts_unauthorized_writes` — rogue sibling + worker_home writes caught, auto-reverted (files removed), raised.
4. `test_task_runner_fails_closed_on_workspace_confinement_violation` — rogue worker → `infra_failed`, rogue file auto-reverted.

---

## Gate Evidence (independent run, D6)

```
90/90 suites green (tiers: unit, containment, integration)
GATE_EXIT=0
```
Full-log grep for `[FAIL]`/`FAILED`/`ERROR`/`Traceback` → **zero hits.**

Per-tier PASS counts (from my gate log) vs tiers.json entries — **exact match, no skips/phantoms:**

| Tier | PASS lines (gate log) | tiers.json entries | Match |
|---|---|---|---|
| unit | 75 | 75 | ✅ |
| containment | 8 | 8 | ✅ |
| integration | 7 | 7 | ✅ |
| **total** | **90** | **90** | ✅ |

Both new suites ran and PASSED: `test_cli_attestation_chain` (log line 9), `test_workspace_isolation` (line 90).

### No regressions
`test_v1_end_product`, `test_v1_adv01_hermetic`, `test_attestation_chain`, `test_research_notebook`, `test_worker_sandbox`, `test_f36`, `test_f42`, `test_f47` — all PASSED.

---

## Safety / Invariants

- **ESTOP:** engaged (True) throughout. No `--controlled-window`, no live dispatch. All tests model-free via fixtures.
- **Zombies:** 0 `controlled_hermes` worker processes.
- **Non-fabrication:** preserved (Gap A kill-assumption, above).
- **Attestation:** still VERIFY_OK (`policy_sha256` 17f08fd6 unchanged) — the hardening touched `policy.py`/`integrity.py` (autonomy policy), NOT `egress_broker.py`/`egress_policy.yaml` (egress policy). Egress files zero-diff across the hardening commit. No re-sign needed.
- **current.json:** 3275 bytes (< 4096 cap), brief_revision 129, status `hardened_local_ready`.
- **Git:** HEAD `517c391`, working tree clean (only `.harness/tmp/` untracked). NOT pushed (Rule 28).
- **No egress allowlist widening; `MAX_REPAIR_ATTEMPTS=2` unchanged; critic unchanged (`ollama/glm-5.2:cloud`).**

---

## §6 Done Criteria Checklist (the gate's acceptance spec)

- [x] **Gap A:** CLI/batch dispatch produces a verifiable DSSE chain; `chain.existing` True; `verify_chain` `(True, None)`; shared `dispatch_admitted_task` is the single attestation entry point; `ledger.queue_task` does not fabricate provenance.
- [x] **Gap B:** worker writes confined to `workspace/tasks/{tid}/`; cross-task writes rejected fail-closed; shared `worker_home` no longer cross-contaminates.
- [x] **Tests:** 2 new hermetic model-free suites, registered in `tests/tiers.json`.
- [x] **No regressions:** v1 tests + containment tier (`test_worker_sandbox`, `test_f36/42/47`) green.
- [x] **Gate:** 90/90 green, exit 0, FAIL_COUNT=0 (D6).
- [x] **Docs:** `current.json` (compact <4096, rev 129) + `CURRENT_STATE.md` synced. NOT pushed.

All boxes checked.

---

## Summary

| Question | Verdict |
|---|---|
| Non-fabrication preserved (route, don't patch)? | **Yes** — `ledger.queue_task` untouched; shared seam `dispatch_admitted_task` sets GATEWAY_RUN_ID |
| Gap A (CLI attestation chaining) landed correctly? | **Yes** — all 3 callers route through the seam; full chain verifies |
| Gap B (per-task workspace isolation) landed correctly? | **Yes** — per-task home, policy confinement, guard auto-reverts, task_runner fail-closed |
| Gate genuinely 90/90 green (D6)? | **Yes** — per-tier counts match tiers.json exactly; zero FAIL/ERROR |
| New tests real (not stubs)? | **Yes** — 193 + 181 lines, genuine assertions, both ran and PASSED |
| No regressions? | **Yes** — v1 + containment + attestation/research_notebook all green |
| Attestation still valid? | **Yes** — egress untouched; VERIFY_OK, policy_sha256 unchanged |
| Cleared for operator release? | **Yes** — push is operator-gated (Rule 28) |

**Working tree:** HEAD `517c391`, clean, NOT pushed. 1 commit ahead of origin (`517c391`, the hardening commit); the prior 4 commits (`7c019df`…`278a80d`) were pushed to origin. Push is operator-gated (Rule 28).

---

*Verified by Claude Code, 2026-09-16. Every claim was checked against code or gate output this session — the non-fabrication kill-assumption was probed directly (not inferred from the gate), and the gate was run independently with per-tier count reconciliation (catching Gemini's misstated breakdown). Gemini's implementation this round is sound; the one defect is a miscount in its prose report, not in its code.*

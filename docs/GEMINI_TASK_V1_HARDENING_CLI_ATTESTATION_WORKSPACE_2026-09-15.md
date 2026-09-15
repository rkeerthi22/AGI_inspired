# Gemini Task — V1 Hardening: CLI/Hermes-Dispatch Attestation Chaining + Per-Task Workspace Isolation

**To:** Gemini CLI (forward implementer)
**From:** Claude Code (final reviewer / the gate)
**Date:** 2026-09-15
**Branch:** `product/v1-completion-2026-09-15` (HEAD `26c242b`, published to origin, gate **88/88** green)
**Re:** Land the two operator-gated hardening items surfaced by the V1-ADV-01 audit
(`docs/reviews/CLAUDE_VERIFICATION_V1_ADV01_AUDIT_2026-09-15.md`, §4 Q2 + Q3).
These are **code+test** changes under engaged ESTOP — no `--controlled-window`, no live dispatch.

---

## 0. Read this first — the two gaps, grounded (all anchors verified this session)

### Gap A — CLI/Hermes dispatch is NOT attestation-chained (audit Gap 3)

The DSSE lifecycle chain emits steps ONLY for tasks dispatched through the **gateway** path.
CLI dispatch (`run_task.py`) bypasses it entirely:

- **Gateway path** (`orchestrator/trust_gateway.py:98-111`): `dispatch_task` inserts the task row with
  `run_id = attestation_chain.GATEWAY_RUN_ID` (`:102`) and calls `append_step(DISPATCH, …)`
  **before commit** (`:106-110`), so a signing failure leaves no dispatchable row.
- **CLI path** (`orchestrator/run_task.py:113`): `tid = ledger.queue_task(args.mission, spec, criteria)`.
  `ledger.queue_task` (`orchestrator/ledger.py:54-62`) inserts the **per-process** `RUN_ID`
  (`ledger.py:15-17`: "one run_id per PROCESS"), which is **NOT** `GATEWAY_RUN_ID`
  (`attestation_chain.py:22` = `"gateway-dsse-v1"`). No DISPATCH step is emitted.
- **Consequence** (`orchestrator/task_runner.py:417,460,721-722`): `chained = chain.existing(runs, tid, row)`.
  `attestation_chain.existing()` (`:197-221`) returns `False` when no chain file exists and
  `row["run_id"] != GATEWAY_RUN_ID`. Every `chain.append_step(...)` call is guarded by `if chained:`
  (`:460,722`), so **all lifecycle steps are silently skipped on every CLI/batch run.** Only
  gateway-routed tasks get a DSSE chain.

### Gap B — workspace/ is a shared writable root, not per-task isolated (audit Gap 1)

- `config/policy.yaml:11-18`: `workspace_confinement.writes_allowed_under` lists `S:\AGI_like\workspace`
  (and `ledger.db`, `memory`) as writable roots.
- `orchestrator/integrity.py:493`: `if policy.is_path_writable(p, pol): continue` — the fs-integrity
  guard **skips** all paths under `workspace/`. Any in-process worker can write arbitrary files
  anywhere under `workspace/` (including clobbering another task's artifacts) without tripping the guard.
- `orchestrator/execution.py:98,153,157`: the worker's `USERPROFILE`/`HOME` is pointed at a single
  **shared** `workspace/worker_home` — every task writes to the same worker home.
- **Precedent for per-task isolation already exists**: `orchestrator/onboarding_autonomy.py:34,272,460`
  uses a per-run `staging_dir` and enforces `if destination.parent != workspace: raise`
  (`:404-405`). The pattern is proven; it just isn't applied to the research-task worker.

---

## 1. Done means

A single branch (this one) holding, on top of `26c242b`:

1. **Gap A fix:** all tasks dispatched through `run_task.py` (and `batch_runner.py`) produce a real,
   verifiable DSSE attestation chain — by routing through the **gateway dispatch path** (which already
   sets `run_id=GATEWAY_RUN_ID` + emits DISPATCH), NOT by patching `GATEWAY_RUN_ID` into `ledger.queue_task`.
2. **Gap B fix:** worker writes are confined to a per-task subdirectory
   (`workspace/tasks/{task_id}/`), and the worker home is redirected there per dispatch.
3. **Tests:** model-free, ESTOP-safe hermetic suites proving both. Gate goes 88 → 90 (or whatever the
   real count reads — read it from output, never hardcode). `current.json` + `CURRENT_STATE.md` synced.
4. NOT pushed (operator release, Rule 28). ESTOP engaged throughout.

---

## 2. Phase A — Route CLI dispatch through the gateway attestation path (Gap A)

### The fix (do this, not the shortcut)

The non-fabrication contract is load-bearing: `attestation_chain.existing()` would rather emit NO
chain than invent lifecycle history for a task not genuinely gateway-admitted
(docstring `attestation_chain.py:198`: *"Legacy tasks predate gateway lifecycle admission; never
invent their history."*). Therefore the fix makes CLI tasks **genuinely** gateway-admitted, so the
DISPATCH provenance is real:

- Extract the dispatch core (the `INSERT … run_id=GATEWAY_RUN_ID` + `append_step(DISPATCH)` before
  commit, currently inline in `trust_gateway.py:98-111`) into a **shared** function, e.g.
  `attestation_chain.dispatch_admitted_task(ledger, runs_dir, mission_id, spec, criteria, …)` or a
  method on `Gateway`. Both `Gateway.dispatch_task` and `run_task.main` call it.
- In `orchestrator/run_task.py`: replace the `ledger.queue_task(...)` call (`:113`) with the shared
  dispatch function, so `run_id=GATEWAY_RUN_ID` and the DISPATCH step emit before the row commits.
  Keep `resolve_mission_path` / `parse_mission` / ESTOP gate (`:87-90`) / runlock (`:94`) exactly as-is.
- `batch_runner.py` dispatch: route the same way (it also calls `ledger.queue_task` directly — verify
  and unify).

### What you must NOT do (the shortcut that breaks the safety property)

- **Do NOT** set `run_id="gateway-dsse-v1"` inside `ledger.queue_task`. That overload would fabricate
  gateway-admission provenance for any caller (including future non-gateway paths), eroding the
  fail-safe. `RUN_ID`'s per-process semantics (`ledger.py:15-17`) are a distinct, honest identity for
  the batch process; conflating them with `GATEWAY_RUN_ID` is a provenance lie.
- **Do NOT** remove the `if chained:` guards in `task_runner.py`. They are the non-fabrication gate.
- **Do NOT** back-fill chains for legacy tasks. `existing()` raising `ChainError("required lifecycle
  chain missing")` only for `run_id==GATEWAY_RUN_ID` rows is correct — legacy rows return False, not raise.

### Phase A Done =

- `run_task.py --dry-run` still works (no DB write).
- A real `run_task.py` dispatch (under a model-free test fixture, NOT a live run) produces
  `runs/task{tid}_attestation_chain.jsonl` whose first statement is `DISPATCH`, and
  `chain.existing(runs, tid, row)` returns `True` so `task_runner` emits the full lifecycle.
- `chain.verify_chain(load(chain_path(runs, tid)))` returns `(True, None)`.
- A hermetic test asserts: CLI-dispatched task → full chain (DISPATCH…DELIVERABLE) verifies; AND
  `ledger.queue_task` is no longer the attestation entry point (assert the shared dispatch function is
  used, or assert `row["run_id"] == GATEWAY_RUN_ID` after a CLI-path queue under the fixture).
- The existing gateway path is unchanged in behavior (same INSERT + DISPATCH, just refactored shared).
- `tests/test_v1_end_product.py` and `tests/test_v1_adv01_hermetic.py` still pass (they pre-seed
  DISPATCH themselves; verify they don't double-emit — if routing creates a second DISPATCH, reconcile
  so exactly one DISPATCH exists per task).

---

## 3. Phase B — Per-task workspace isolation (Gap B)

### The fix

- Add a per-task worker home: `workspace/tasks/{task_id}/` (created at dispatch, removed/retained per
  existing artifact policy — match `onboarding_autonomy.py`'s staging_dir lifecycle).
- In `orchestrator/execution.py` (`:98,153,157`), redirect `USERPROFILE`/`HOME`/worker home from the
  shared `workspace/worker_home` to the per-task dir when a `task_id` is in scope. Preserve the
  existing fallback (`workspace/worker_home`) only when no task_id (e.g. canaries/doctor).
- Update `config/policy.yaml:11-18` `writes_allowed_under`: keep `workspace` as a writable root (the
  per-task dir lives under it), but add a **confinement check** in `policy.is_path_writable` (or a new
  guard) that, for worker-originated writes, restricts to `workspace/tasks/{current_task_id}/` and
  **rejects** writes to sibling task dirs or `workspace/worker_home` from a worker context.
- `orchestrator/integrity.py` fs guard (`:493`): the per-task dir remains policy-writable (excluded
  from fs guard) BUT a new assertion must reject a worker writing outside its own task dir (this is the
  Gap B security property — currently unenforced).

### Phase B Done =

- A hermetic test: two sequential tasks under the fixture; task 1 writes a sentinel file to its
  worker home; task 2's worker CANNOT see/overwrite task 1's sentinel (assert the path is absent or
  the write is rejected). Proves per-task isolation.
- A hermetic test: a worker attempt to write to `workspace/worker_home` or a sibling
  `workspace/tasks/{other_id}/` from a task context is **rejected** (fail-closed), not silently allowed.
- Existing worker-home consumers (`execution.py`, canary `operator_cli.py:48`, doctor) still function.
- The fs-integrity guard still passes for legitimate per-task writes and still catches writes to
  protected paths (regression: `test_f36`/`test_f42`/`test_worker_sandbox` containment tier stays green).

---

## 4. Constraints (do not violate — operator-gated invariants)

- **ESTOP engaged** throughout; no `--controlled-window`, no live provider dispatch. All tests
  model-free via the existing fixture path (`tests/v1_test_support.py`).
- **Non-fabrication:** never invent attestation history. Route through the gateway path; do NOT
  patch `GATEWAY_RUN_ID` into `ledger.queue_task`.
- **Critic stays `ollama/glm-5.2:cloud`**; BytePlus not in worker fallback chain.
- **No egress allowlist widening.** `MAX_REPAIR_ATTEMPTS=2` (`deliverable_preflight.py:31`).
- **No `HARNESS_AUDIT_ENFORCE=1`** without a real off-host Object-Lock bucket.
- **Do NOT push.** Commit on this branch; operator releases.
- **D6:** gate must read N/N green, exit 0, zero `[FAIL]`/`FAILED`/`ERROR`/`Traceback` in the FULL
  log (grep, not exit-code-only). N is dynamic — read from output.
- **Parse-don't-trust:** verify every claim against the code this session; N-claims-N-probes.
- **No re-labeling:** ledger is ground truth; content fails are content fails.

---

## 5. Sequencing (cheapest-lethal-check first)

1. **Kill-assumption check (Phase A):** before refactoring, write a 10-line model-free probe that
   dispatches a task through the CLI `run_task` path under the fixture and asserts
   `chain.existing(runs, tid, row) is False` today (proves the gap is real in code, not just in the
   audit). If it's already True, STOP and report — the gap premise is wrong.
2. Implement the shared dispatch function; route `run_task.py` + `batch_runner.py` through it.
3. Re-run the kill-assumption probe — it should now be `True` + chain verifies.
4. Phase B kill-assumption: probe that two fixture tasks share `workspace/worker_home` today (proves
   the shared-home gap), then implement per-task isolation, then re-prove isolation.
5. Full gate (D6). Sync `current.json` (bump brief_revision, keep <4096 bytes compact) +
   `CURRENT_STATE.md`.
6. Commit on branch. Report ONE final result.

---

## 6. Done Criteria (self-assess — this is the gate's acceptance spec)

- [ ] **Gap A:** CLI/batch dispatch produces a verifiable DSSE chain; `chain.existing` returns True;
      `verify_chain` returns `(True, None)`; the shared dispatch function is the single attestation
      entry point; `ledger.queue_task` no longer fabricates provenance.
- [ ] **Gap B:** worker writes confined to `workspace/tasks/{task_id}/`; cross-task writes rejected
      fail-closed; shared `worker_home` no longer cross-contaminates between tasks.
- [ ] **Tests:** ≥2 new hermetic model-free suites (one per gap), registered in `tests/tiers.json`.
- [ ] **No regressions:** `test_v1_end_product.py`, `test_v1_adv01_hermetic.py`, and the containment
      tier (`test_worker_sandbox`, `test_f36`, `test_f42`, `test_f47`) all still green.
- [ ] **Gate:** N/N green, exit 0, FAIL_COUNT=0 (D6). N read from output.
- [ ] **Docs:** `current.json` (compact <4096, brief_revision bumped) + `CURRENT_STATE.md` synced to
      the new gate count + these two landings. NOT pushed.

---

## 7. Report back (ONE, at the end)

Report to Claude Code: the two commit hashes, the new gate count (N/N, exit 0, FAIL_COUNT=0), the
kill-assumption before/after for each gap, and any part that did NOT land as hoped (stated plainly).
Claude does a single independent verification pass against this spec.

---

*Written by Claude Code (final reviewer / the gate), 2026-09-15. All file:line anchors in this
directive were parse-verified against the code this session: `run_task.py:113` (queue_task call),
`:59-67` (parser, no --controlled-window), `:87-90` (ESTOP gate); `ledger.py:54-62` (queue_task
inserts per-process RUN_ID), `:15-17` (RUN_ID semantics); `trust_gateway.py:98-111` (gateway dispatch
sets GATEWAY_RUN_ID + DISPATCH before commit); `attestation_chain.py:22,197-221` (GATEWAY_RUN_ID +
existing() non-fabrication gate); `task_runner.py:417,460,721-722` (chained guard); `policy.yaml:11-18`
(workspace writable root); `integrity.py:493` (writable-path skip); `execution.py:98,153,157` (shared
worker_home); `onboarding_autonomy.py:34,272,404-405,460` (per-run staging_dir precedent). Base HEAD
`26c242b`, gate 88/88 green, ESTOP True, 0 zombies. Non-fabrication is the load-bearing safety
property — route, don't patch.*

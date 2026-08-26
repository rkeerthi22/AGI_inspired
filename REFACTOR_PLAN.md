# Refactor Plan — `batch_runner.py` → 5 modules (Week 9)
**Status: DRAFT, awaiting operator approval before any code moves**
**Author: AGI_like agent, 2026-08-26**
**Depends on: `OSINT_INTEGRATION_PLAN.md` §1 + §7 (operator-signed W9 transition)**

---

## 0. Why this is a separate plan from OSINT

The OSINT plan's §1 says the 5-file split is the prerequisite for OSINT.
It is not a part of the OSINT plan because it has its own risk profile
and its own sequencing problem: the OSINT plan is "add new capability
without growing `batch_runner.py`." This plan is "move existing code
without breaking anything." Different shape of work, different review
shape. Two documents, two approvals.

---

## 1. The actual shape of `batch_runner.py` today

I read the file end-to-end before planning the split. Function-level
survey (line numbers from the version at commit `075be4f`):

```
2,324 lines, 53 top-level functions, 1 class.

  integrity    369 lines / 10 fns   (fs-guard, db-integrity, escalate, preflight)
  execution    462 lines / 16 fns   (model calls, failover, db integrity check)
  prompts      250 lines /  9 fns   (mission parsing, brief builder, fact lines)
  evaluation   456 lines /  5 fns   (run_critic, run_synthesis, run_canaries)
  scheduler    570 lines / 10 fns   (run_task, queue, retry, expire, reconcile)
  top-level    162 lines /  4 fns   (main, _run, log, load_roles)
                                 ---
  total      2,269 lines / 54 fns   (some helpers counted twice)
```

The OSINT plan's 5-bucket labels work, but the buckets are not
equal-sized. **`scheduler.run_task` alone is 324 lines** — the
5-file split doesn't shrink that function, it just moves it.
The split is about **separating concerns**, not reducing total
size. A future `run_task` decomposition is a separate refactor
that should not happen in the same commit as the file split.

`batch_runner.py` itself stays as the top-level entry point: it
will contain `main()`, `_run()`, `log()`, `load_roles()`, and the
`*`-imports of the 5 new modules. ~170 lines. That's the file
that `orchestrator/batch.cmd` calls.

---

## 2. The dependency graph (and the ordering it implies)

Reading every `def` for cross-references:

```
  integrity   ──→ execution      (preflight → db_integrity_check, fs_integrity_check)
  execution   ──→ (no cross)
  prompts     ──→ (no cross)
  evaluation  ──→ execution      (run_critic, run_synthesis → *_with_failover)
  evaluation  ──→ prompts        (run_synthesis → build_brief_block)
  scheduler   ──→ integrity      (run_task → escalate)
  scheduler   ──→ execution      (run_task → worker_with_failover via run_critic)
  scheduler   ──→ prompts        (run_task → pass_criteria_for, task_scope_note)
  scheduler   ──→ evaluation     (run_task → run_synthesis, run_critic)
  scheduler   ──→ prediction_machine.integrations (already wired in f6b58c1)
```

This is a DAG, not a cycle. Layered top-to-bottom:

```
  L0: integrity  (no internal deps)
  L1: execution  (only L0)
  L2: prompts    (no deps)
  L3: evaluation (L1 + L2)
  L4: scheduler  (L0 + L1 + L2 + L3 + prediction_machine)
  T: batch_runner.py (L4 only — and the CLI surface)
```

Importing in this order is cycle-free. The natural commit sequence
follows the layering: move integrity first, then execution, then
prompts, then evaluation, then scheduler. **One module per commit.
Each commit must keep all 15 green suites green.**

---

## 3. The sequence — 5 moves, each independently shippable

### Move 1: `orchestrator/integrity.py`

Functions moving out of `batch_runner.py`:

```
L68-100   escalate                (33 lines)
L401-416  _db_snapshot            (16 lines)
L417-421  db_integrity_snapshot   ( 5 lines)
L422-532  db_integrity_check      (111 lines)
L533-600  _untracked_files        (68 lines)
L601-625  _local_exclude_sources  (25 lines)
L626-643  _local_exclude_state    (18 lines)
L644-662  _masked_under_protected (19 lines)
L663-672  _untracked_of           (10 lines)
L673-692  _tracked_hashes         (20 lines)
L693-714  fs_integrity_snapshot   (22 lines)
L715-850  fs_integrity_check      (136 lines)
L881-898  preflight               (18 lines)
                            TOTAL: ~500 lines
```

But `db_integrity_*` and `fs_integrity_*` touch the same
`PROTECTED_PATHS` / `writable_roots(pol)` machinery that
`policy.py` already exposes. They could either:
- (a) all live in `integrity.py` together (~500 lines)
- (b) split further: `integrity.py` for fs-guard,
  `integrity_db.py` for db integrity (~370 + ~130)

Recommendation: **(a) for now.** The 500 lines is still
smaller than the original `batch_runner.py` and keeps the
two integrity paths in one file. A further split is a
future refactor.

**Touch surface for tests:**
- `test_f42.py`, `test_f46.py`, `test_f47.py`, `test_f48.py`,
  `test_f52.py`, `test_throughput.py`, `test_f36.py` all
  call into the integrity machinery. They import via
  `from orchestrator import batch_runner; batch_runner.<fn>`
  patterns. After the move, either (i) `batch_runner` keeps
  thin re-export shims for backward compatibility, OR
  (ii) the tests are updated to import from `integrity`
  directly.

Recommendation: **(i) re-export shims**, not test edits,
for Move 1. Reasons: the change is purely organizational,
updating 7 test files at once multiplies the failure modes
a refactor can introduce, and F52 already made
`.claude/HANDOFF.md` a file the agents must not lose
visibility on — minimizing test churn protects the safety
net.

**Commit message:**
```
orchestrator: extract integrity.py from batch_runner.py

Move fs-guard, db-integrity, escalate, and preflight into
their own module. No behavior change. Re-export the moved
functions from batch_runner.py so existing callers (including
7 test files) continue to work without edits.
```

### Move 2: `orchestrator/execution.py`

Functions moving:

```
L101-124  hermes_worker            (24 lines)
L125-197  ollama_chat              (73 lines)
L198-218  _is_local_model          (21 lines)
L219-223  load_fallback_chain      ( 5 lines)
L224-240  _quota_group             (17 lines)
L241-269  _fits_context            (29 lines)
L270-275  _context_skip_note       ( 6 lines)
L276-299  _failover_candidates     (24 lines)
L300-346  worker_with_failover     (47 lines)
L347-400  synthesis_with_failover  (54 lines)
L851-856  _strip_tool_chatter      ( 6 lines)
L857-862  is_quota_error           ( 6 lines)
L863-880  worker_failed            (18 lines)
                              TOTAL: ~330 lines
```

`execution.py` imports from `integrity.py` (the
`PROTECTED_PATHS` lookup is exposed by integrity's
`db_integrity_snapshot`, which is called by execution's
model-call path indirectly).

`db_integrity_check` was placed in Move 1's integrity.py per
recommendation (a). That is *only* correct if
`db_integrity_check` is also considered execution code. The
two views are compatible: integrity.py's `db_integrity_check`
is a containment guard, and execution.py's calls into model
services are the things being contained. Both are correct
placements; I went with integrity because the function is
a guard, not an execution path.

**Touch surface for tests:**
- No tests call `hermes_worker` / `ollama_chat` /
  `worker_with_failover` directly. They all go through
  `run_task` (Move 5). Same re-export shim strategy.
- `test_f37.py`, `test_f38.py`, `test_f39_f40.py`,
  `test_f50.py` exercise these paths via `run_task`.
  Re-export shims cover them.

### Move 3: `orchestrator/prompts.py`

Functions moving:

```
L899-912   parse_mission            (14 lines)
L913-916   week_key                 ( 4 lines)
L955-968   pass_criteria_for        (14 lines)
L969-1006  deliverable_requirements (38 lines)
L1007-1048 task_scope_note          (42 lines)
L1049-1063 is_first_run_for_mission (15 lines)
L1064-1075 mission_objective        (12 lines)
L1076-1090 _parse_json_array        (15 lines)
L1157-1203 _recent_fact_lines       (47 lines)
L1204-1223 seed_is_synthesis        (20 lines)
L1330-1376 build_brief_block        (47 lines)
                              TOTAL: ~268 lines
```

(`week_key` and `parse_mission` are arguably scheduler
functions, but they are tiny, have no state, and are used
by `run_task` in a way that makes them naturally part of
the prompt-building context. Putting them in prompts keeps
Move 5 leaner.)

`prompts.py` has no internal cross-module deps. It does
take `mission_id` strings and return text blobs. Cleanest
module in the split.

**Touch surface for tests:**
- No direct tests for `pass_criteria_for`,
  `deliverable_requirements`, `task_scope_note`,
  `is_first_run_for_mission`, `mission_objective`,
  `_parse_json_array`, `_recent_fact_lines`,
  `seed_is_synthesis`, `build_brief_block`. All are
  exercised through `run_task`.
- `test_f31.py` (task_scope_note coverage) — re-export
  shim covers it.

### Move 4: `orchestrator/evaluation.py`

Functions moving:

```
L1091-1139 extract_facts            (49 lines)
L1140-1156 retract_facts            (17 lines)
L1224-1329 run_critic               (106 lines)
L1377-1510 run_synthesis            (134 lines)
L2025-2174 run_canaries             (150 lines)
                              TOTAL: ~456 lines
```

(`run_canaries` is a long function but not a structurally
complex one — it iterates a fixed set of spec strings and
calls `worker_with_failover` once per spec. It can be
left as-is in Move 4.)

`evaluation.py` depends on `execution.py` and `prompts.py`.
Both are already in place by Move 4.

**Touch surface for tests:**
- `test_f31.py` (scope), `test_f30.py` (synthesis
  routing), `test_f37.py` (canary infra-failure),
  `test_f54.py` (spot-check). Re-export shims cover all.
- One quirk: `run_canaries` calls
  `worker_with_failover` which is in `execution.py`. After
  Move 4, `evaluation.py` does
  `from orchestrator.execution import worker_with_failover`.
  The re-export shim in `batch_runner.py` keeps
  `batch_runner.worker_with_failover` working too.

### Move 5: `orchestrator/scheduler.py`

Functions moving:

```
L917-954   queue_mission_tasks         (38 lines)
L1511-1551 expire_stale_parked         (41 lines)
L1552-1593 reconcile_interrupted_tasks (42 lines)
L1594-1612 accumulated_tokens          (19 lines)
L1613-1936 run_task                    (324 lines)
L1937-1984 retry_failed_this_fire      (48 lines)
L1985-1999 _check_repeated_failure     (15 lines)
L2000-2024 mission_workspace           (25 lines)
                                  TOTAL: ~552 lines
```

This is the largest single move. **`run_task` is the
orchestrator's main loop** — 324 lines that call every
other module. After Move 5, `scheduler.py` depends on
integrity, execution, prompts, evaluation, and the
already-existing `prediction_machine.integrations`.

**Touch surface for tests:**
- Almost every test goes through `run_task` or its
  helpers. Re-export shims cover everything.
- `test_throughput.py`, `test_f54.py`,
  `test_f50.py`, `test_f53.py`, `test_h7.py`,
  `test_h7_gate.py` — re-export shim strategy still
  applies.

After Move 5, `batch_runner.py` is ~170 lines: just
`main()`, `_run()`, `log()`, `load_roles()`, and the
imports of the 5 new modules. **Final commit message:**

```
orchestrator: extract scheduler.py from batch_runner.py

Move queue_mission_tasks, expire_stale_parked,
reconcile_interrupted_tasks, accumulated_tokens,
run_task, retry_failed_this_fire, _check_repeated_failure,
and mission_workspace into their own module. After this
commit, batch_runner.py is the entry-point + CLI surface
only; the 5 new modules are the actual orchestrator.
```

---

## 4. The 8 things that can break, and how each is guarded

| Risk | Likelihood | Guard |
|---|---|---|
| Re-export shim misses a name | low | After each move, run `python tests/run_all.py` AND `python -c "from orchestrator import batch_runner; print([n for n in dir(batch_runner) if not n.startswith('_')])"` and compare to pre-move list. |
| Cross-module import creates a cycle | low | Move in layering order (integrity → execution → prompts → evaluation → scheduler). Don't write `from scheduler import ...` anywhere except in `batch_runner.py`. |
| Test that monkey-patches a moved function via `batch_runner.<fn>` breaks | medium | Re-export shim preserves the patch target. Verify by running test_throughput.py and test_f54.py after Move 5. |
| The 3 still-red suites (test_baseline, test_f49, test_f52) — already broken pre-refactor, refactor might mask the failure | low | Run `python tests/run_all.py` before AND after each move; compare. If a "was red, still red" pair goes "was red, now green" or vice versa, halt. |
| `_run()` and `main()` reference module-level constants that move | low | Both reference `LEDGER_DB`, `PROTECTED_PATHS`, `POLICY_PATH` via `policy`/`ledger`. Those are imported in `batch_runner.py` and stay there. The 5 new modules import `policy`/`ledger` themselves. |
| F15 (bare `git commit` sweeps unrelated work) | low | Each move is one explicit-pathspec commit. Predictions.db is gitignored now. |
| F42 (root file accidentally added during move) | low | All moves stay within `orchestrator/`. The only top-level change after Move 5 is that `batch_runner.py` shrinks and 5 new files appear. No new root entries. |
| F47 (an `.gitignore` rule masks the new files) | very low | None of the moved functions touch gitignore rules. The 5 new `.py` files match no gitignore pattern. The fs-guard will detect them as expected new tracked files. |

---

## 5. The test-gating rule

**After each of Moves 1–5, the test suite must show `15/18 green`
exactly — no more, no less.** Specifically:

- The 15 already-green suites stay green.
- The 3 already-red suites stay red at the same assertions.

If a move causes any green suite to fail, halt. The diagnosis
hierarchy is: re-export shim → import cycle → test-patch
target → cross-module constant lookup → behavior regression.
Steps 1–4 are recoverable within the same session; step 5 is
a containment event (revert and re-plan).

The 3 red suites are NOT in scope for this refactor. They
were deferred per the W4–W8 execution-only directive. They
stay deferred. The refactor does not fix them; it must also
not break them further.

---

## 6. The single largest risk: `run_task`'s 324 lines

`run_task` is the only function in the file that is both
huge AND structurally entangled. It calls:

- `before_task_runs`, `after_task_completes` (prediction_machine)
- `extract_facts`, `seed_is_synthesis`, `run_synthesis`,
  `build_brief_block` (prompts + evaluation)
- `worker_with_failover`, `synthesis_with_failover` (execution)
- `pass_criteria_for`, `deliverable_requirements`,
  `task_scope_note`, `is_first_run_for_mission`,
  `mission_objective` (prompts)
- `escalate` (integrity)
- `promote.active_skills_for` (promote.py — stays put)
- `policy.tokens_used_today`, `policy.compliance_prompt_block`,
  `policy.is_path_writable` (policy.py — stays put)
- `_check_repeated_failure` (scheduler)

That is 6+ cross-module calls plus inline prompt
construction. The 5-file split will turn these into
explicit imports but will NOT shrink `run_task` itself.
A future refactor that decomposes `run_task` into
`_run_research_task`, `_run_synthesis_task`,
`_record_outcome` etc. is a separate effort and is
explicitly out of scope for this plan.

This means **after the 5-file split, `scheduler.py`
will be 552 lines and `run_task` will still be 324
of those.** That is fine. The point of the split
is *separation of concerns*, not *lines per file*.
Each module becomes independently testable and
independently understandable. A future `run_task`
decomposition can happen in its own refactor, with
its own regression suite, on a stable foundation.

---

## 7. The schedule (operator-approved cadence, not optimistic)

| Day | Move | Verification |
|---|---|---|
| D1 | Move 1: integrity.py | `python tests/run_all.py` → 15/18 (same as today) |
| D1 | Move 2: execution.py | same |
| D2 | Move 3: prompts.py | same |
| D2 | Move 4: evaluation.py | same |
| D3 | Move 5: scheduler.py | same — plus a `wc -l orchestrator/*.py` to confirm the size distribution |
| D3 | Push all 5 commits to origin master | `git push origin master`, then `git ls-remote origin master` shows the new tip |

D1–D3 is 3 working days of agent time. **No 4-day stretch**
— the OSINT plan's W10 OSINT work begins after this lands
and tests stay green for 48 hours.

If any single day's commit doesn't pass the 15/18 gate, halt
that move. Don't push, don't move to the next one. Investigate,
revert if needed, re-plan.

---

## 8. What this plan is NOT

- **Not a rewrite of `run_task`.** The 324-line function
  moves into `scheduler.py` byte-for-byte. Its internals
  are not touched.
- **Not an opportunity to fix the 3 red test suites.**
  They were deferred before the directive override; they
  stay deferred. If a move inadvertently makes them
  green, great, but don't try to make them green.
- **Not adding any new capability.** This is purely
  organizational. The OSINT plan's W10 work is a
  separate, later effort.
- **Not changing behavior of any moved function.** Every
  function moves with its docstring, its constants, its
  private helpers, and its quirks intact. The tests
  exist to prove this.

---

## 9. Operator sign-off

This plan is on disk at `S:\AGI_like\REFACTOR_PLAN.md`.
**I will not begin Move 1 until you confirm:**

1. The sequence (5 moves, layered bottom-up, re-export
   shims) is correct.
2. The schedule (D1–D3, halting on any green-suite
   regression) is the right cadence.
3. The `run_task` 324-line caveat (it moves but does
   not shrink) is acceptable as a result.

If you want a different split — say, 7 modules, or
a different layering, or aggressive `run_task`
decomposition in parallel — say so and I will revise
this plan before touching code.
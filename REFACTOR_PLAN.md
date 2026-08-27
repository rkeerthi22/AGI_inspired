# Refactor Plan — `batch_runner.py` split (Week 9, revised)
**Status: IN PROGRESS — Moves 5a through 5d landed; Move 5e has not begun**
**Author: AGI_like agent, 2026-08-26**
**Revised: 2026-08-26 after operator review of Move 4 + review.txt**

---

## 0. Pre-5b cleanups (operator-specified)

Before Move 5b begins, the following were in scope as small atomic edits.
Each is now landed or superseded.

1. **Remove stale `global _log_file` from `batch_runner.main()`.** `_log_file` is no
   longer defined in `batch_runner`'s module globals (Move 5a moved it to
   `runtime_context`); the `global` declaration is harmless but misleading.
   *Done in commit `80c4f01`.*

2. **Update this plan's status** from `DRAFT` to `APPROVED`, marking which moves
   have landed and which are operator-approved vs pending. *Done.*

3. **Remove live workspace sizing from the F49 deterministic gate.** Section 7
   of `test_f49.py` was a live-data drift observation (`{n} briefs on disk,
   largest {x} chars, {y} exceeded the old 6000`); dropped. Section 7 now
   asserts only the constant value (`SYNTHESIS_BRIEF_CHARS >= 16000` and
   `== 24000`), independent of workspace contents. *Done in `80c4f01`.*

4. **Replace Markdown-driven quarantine configuration.** The parser in
   `tests/run_all.py` used to extract `test_*` names from `tests/QUARANTINE.md`
   via a regex. Now `tests/quarantine.txt` (one stem per line, `#`-comments)
   is the configuration; `QUARANTINE.md` is rationale only. *Done in `80c4f01`.*

5. **Add a shared-logging regression test.** New `test_f56.py` asserts the
   proxy indirection, the active run log capture for every module, the
   silence/capture semantics, and a validated-against-the-defect section.
   *Done in `80c4f01`.*

6. **Make logger patch semantics truthful.** `runtime_context.log` is now a
   thin proxy that delegates to `_logger` at call time. `tests/_silence.py`
   exposes `silence_log()` and `capture_log()` as the truthful patch points;
   the legacy `br.log = lambda` pattern no longer suffices and the suites that
   used it (`test_f48`, `test_f50`, `test_f52`) now go through the helpers.
   *Done in `80c4f01`.*

7. **Resolve the scheduler↔evaluation cycle before 5c.** Pure evaluation
   (critic scoring, fact extraction, leaf predicates) stays in
   `evaluation.py`. Orchestration that mixes scheduler state with grading
   (synthesis pipeline, canary pass-fail accounting, retry ordering of
   graded failures, repeat-failure heuristic) goes in a dedicated
   `workflow.py`. The decision is locked: see §2 / 5c′ below. *Done in
   this revision.*

8. **Plan contradiction fix (operator, this revision).** Earlier drafts of
   §2 / 5b listed `retry_failed_this_fire`, `_check_repeated_failure`, and
   the `evaluation` (`seed_is_synthesis`) dependency under scheduler, while
   §2 / 5c′ listed `retry_failed_this_fire` under workflow — and §3's
   narrative said scheduler must not import evaluation. All three of those
   moves are now consolidated in `workflow.py`. Scheduler is pure state with
   no evaluation import. *Done in this revision.*

9. **Logging-test number renamed (operator, this revision).** `test_f55.py`
   was originally created for the logging proxy; F55 is already assigned to
   worker partial-output resilience, so the test is renamed to
   `test_f56.py`. F55 stays reserved. *Done in this revision.*

---

## 1. Why this revision exists

The original plan proposed a 1,300-line Move 5 (`scheduler.py`) that would
have contained every function left in `batch_runner.py`: scheduling, evaluation,
canaries, fact extraction, CLI glue and shared helpers. The operator correctly
identified that this is not a module boundary — it relocates the monolith rather
than finishing the refactor.

This revision replaces the single Move 5 with **Moves 5a–5e**, each with a
real separation of concerns, no duplicated helpers, no wildcard imports, and no
"leftovers" module. `batch_runner.py` remains a thin compatibility/entry-point file.

---

## 2. What has already landed

| Move | File | What moved | Status |
|---|---|---|---|
| Move 1 | `orchestrator/integrity.py` | fs-guard, db-guard, escalate, preflight | committed |
| Move 2 | `orchestrator/execution.py` | model calls, failover, context checks | committed |
| Move 3 | `orchestrator/prompts.py` | prompt building, mission parsing, brief block | committed |
| Move 4 | `orchestrator/evaluation.py` | `seed_is_synthesis`, `retract_facts` (leaf only) | committed |
| Move 5a | `orchestrator/runtime_context.py` | shared logger (proxy) + path constants | committed (`7631221`) |
| Move 5b | `orchestrator/scheduler.py` | scheduler state service | committed (`e8fde3c`) |
| Move 5c | `orchestrator/evaluation.py` | critic + fact extraction service | committed (`5331f2c`) |
| Move 5c′ | `orchestrator/workflow.py` | synthesis, canaries, retries, repeated-failure coordination | committed (`c3f78bf`) |
| Move 5d | `orchestrator/task_runner.py` | single-task preparation, execution, classification, and outcome recording | committed (`ef95498`), outcome boundary tightened in follow-up |
| Move 5e | composition cleanup | remaining approved composition-layer assessment | not begun |

Move 4 was intentionally leaf-only: the full evaluation layer (`run_critic`,
`run_synthesis`, `run_canaries`, `extract_facts`) depends on scheduler helpers
(`week_key`, `queue_mission_tasks`, `run_task`) that were still in
`batch_runner.py`. Extracting it cleanly requires scheduler state to move first.

Move 5a was pulled forward to fix a logging regression: `integrity.py` and
`execution.py` had their own `log()` implementations, so failover and
escalation messages were not reaching the active run log. `runtime_context.py`
gives every module a single shared logger.

---

## 3. The remaining moves (5b–5e)

### 5b — Scheduler state functions
**File:** `orchestrator/scheduler.py`

Pure scheduling helpers that manage task lifecycle state in `ledger.db`.
No orchestration logic (no grading, no retry decisions, no canary
accounting) — that goes to `workflow.py` (5c′) so the scheduler/evaluation
cycle the operator flagged stays broken.

- `queue_mission_tasks`
- `expire_stale_parked`
- `reconcile_interrupted_tasks`
- `mission_workspace`
- `accumulated_tokens`
- `is_first_run_for_mission`
- `week_key`
- `parse_mission` (used by queue + scheduler)

Dependencies:
- `runtime_context` (paths, log)
- `ledger`
- `policy`
- `prompts` (`pass_criteria_for`)

No model calls, no integrity guards, no CLI, no evaluation imports.

### 5c — Pure evaluation services
**File:** `orchestrator/evaluation.py` (extends existing file)

Pure grading and memory-update functions. No scheduler state, no task
lifecycle, no CLI. Inputs come in as plain Python values; outputs are
plain Python values or simple side effects (ledger rows).

- `run_critic` — score a deliverable, return verdict + evidence
- `extract_facts` — parse a deliverable into fact rows
- existing: `seed_is_synthesis`, `retract_facts`

Dependencies:
- `runtime_context`
- `execution` (worker/failover for the critic's own LLM call)
- `prompts` (scope note, brief block, recent-fact view)
- `citecheck`
- `ledger`, `policy`

### 5c′ — Workflow orchestration (separate file, locked decision)
**File:** `orchestrator/workflow.py` (new)

Functions that mix pure evaluation with scheduler state — synthesis
routing, canary pass/fail accounting, retry ordering of graded failures,
and the repeat-failure heuristic that decides when to stop retrying a
seed. These don't belong in `evaluation.py` because they touch
`ledger.QUEUE` state, and they don't belong in `task_runner.py` because
they coordinate across multiple tasks (a synthesis consumes prior-week
briefs; a canary pass produces a roll-back signal; a retry pass touches
many tasks at once).

- `run_synthesis` — synthesis pipeline (brief gather → critic → finish)
- `run_canaries` — canary pipeline (per-spec grade → finish + week tally)
- `retry_failed_this_fire` — same-fire retry of graded failures
- `_check_repeated_failure` — heuristic that decides whether a seed has
  hit its retry budget

Dependencies: `evaluation`, `execution`, `prompts`, `integrity`, `ledger`,
`policy`, `promote`, `runtime_context`.

This resolves the scheduler↔evaluation cycle the operator flagged: pure
evaluation is a leaf (5c), orchestration that needs scheduler state goes
through `workflow.py`, and `scheduler.py` (5b) does not import `evaluation`
— it stays a pure state module.

### 5d — Task execution decomposition
**File:** `orchestrator/task_runner.py`

A single `run_task` of ~286 lines is too large and does too many things.
Decompose it into smaller, independently testable paths while keeping the public
`run_task` entry point as a thin dispatcher:

- `_prepare_task_input` — build prompt, scope note, skill injection
- `_run_research_task` — worker path, integrity wrap, outcome capture
- `_run_synthesis_task` — synthesis routing (calls into `workflow.py`)
- `_record_outcome` — ledger write, fact extraction (calls into `evaluation.py`),
  lesson capture
- `run_task` — orchestrates the above

Dependencies: `runtime_context`, `workflow`, `evaluation`, `prompts`,
`integrity`, `execution`, `ledger`, `policy`, `promote`,
`prediction_machine.integrations.batch_runner_hook`.

### 5e — CLI / entry point
**File:** `orchestrator/batch_runner.py` (shrinks to ~50 lines)

Only the command-line surface and `_run` dispatch remain:

- `main`
- `_run`
- `load_roles`
- re-export shims for backwards compatibility (until tests migrate)

`batch.cmd` and Windows scheduled tasks continue to call
`python orchestrator/batch_runner.py` unchanged.

---

## 4. The corrected dependency graph

After Move 5a (and the 5c / 5c′ / 5d split locked above):

```
runtime_context
    ↑
integrity  →  execution  →  prompts  →  evaluation
    ↑            ↑            ↑            ↑
    └────────────┴────────────┴────────────┘
                  ↓
              workflow
                  ↑
              scheduler
                  ↑
              task_runner
                  ↓
            batch_runner.py (CLI)
```

The cycle is resolved by the strict layering: `evaluation` is a leaf,
`workflow` mixes `evaluation` with scheduler state, `scheduler` does not
import `evaluation`. `task_runner` calls both `workflow` (synthesis,
canaries) and `evaluation` (critic, fact extraction) directly; the public
`run_task` is a thin dispatcher over those calls.


---

## 5. AST-level findings that shaped this split

Function sizes (after Move 5a) from `batch_runner.py`:

| Function | Lines | Proposed home |
|---|---|---|
| `run_task` | 286 | `task_runner.py` (decomposed) |
| `run_canaries` | 144 | `workflow.py` |
| `run_synthesis` | 132 | `workflow.py` |
| `_run` | 117 | `batch_runner.py` |
| `run_critic` | 84 | `evaluation.py` |
| `extract_facts` | 47 | `evaluation.py` |
| `retry_failed_this_fire` | 43 | `workflow.py` |
| `expire_stale_parked` | 39 | `scheduler.py` |
| `reconcile_interrupted_tasks` | 39 | `scheduler.py` |
| `queue_mission_tasks` | 36 | `scheduler.py` |
| `accumulated_tokens` | 17 | `scheduler.py` |
| `is_first_run_for_mission` | 13 | `scheduler.py` |
| `_parse_json_array` | 13 | `scheduler.py` or `runtime_context.py` utilities |
| `_check_repeated_failure` | 13 | `workflow.py` |
| `parse_mission` | 12 | `scheduler.py` |
| `mission_workspace` | 4 | `scheduler.py` |
| `week_key` | 2 | `scheduler.py` |
| `load_roles` | 2 | `batch_runner.py` |
| `_strip_tool_chatter` | 4 | `runtime_context.py` utilities |

Cross-module bare calls inside `batch_runner.py`:

- `_check_repeated_failure` → `integrity.escalate`
- `_run` → `integrity.escalate`, `integrity.preflight`
- `extract_facts` → `execution.ollama_chat`
- `queue_mission_tasks` → `prompts.pass_criteria_for`
- `retry_failed_this_fire` → `evaluation.seed_is_synthesis`
- `run_canaries` → `integrity.db_integrity_*`, `integrity.fs_integrity_*`, `integrity.escalate`, `execution.worker_*`
- `run_critic` → `execution.ollama_chat`
- `run_synthesis` → `prompts.*`, `integrity.escalate`, `execution.synthesis_with_failover`
- `run_task` → `integrity.*`, `prompts.*`, `evaluation.seed_is_synthesis`, `execution.worker_*`

Late imports (inside functions) that will need to move with their owners:

- `sqlite3` — used by most scheduler + evaluation functions
- `scorecard`, `promote`, `spotcheck` — `_run`
- `runlock` — `main`
- `promote` — `run_canaries`, `run_task`
- `urllib.error` — `run_synthesis`
- `prediction_machine.integrations.batch_runner_hook` — `run_task`

Keyword/callback references (monkey-patch targets in tests):

- `ledger.finish_task(..., tokens_in=..., tokens_out=...)` in `run_canaries`, `run_synthesis`, `run_task`
- `run_critic(..., scope_note=...)` in `run_synthesis`, `run_task`
- `escalate(..., task_id=...)` in `run_synthesis`, `run_task`
- `synthesis_with_failover(..., usage_out=...)` in `run_synthesis`

These are internal data-flow hooks, not public extension points. The refactor
will keep them as explicit keyword arguments so tests can continue to patch
them where needed.

---

## 6. Test gate after each move

After every move:

```
python tests/run_all.py
```

must report **all non-quarantined suites green**. The only quarantined suite is
`test_baseline.py`, a live-data check that copies the real `ledger.db`. It is
run separately with:

```
python tests/run_all.py --live-data
```

No move proceeds until the deterministic gate is green.

---

## 7. What this plan is NOT

- **Not a 1,300-line leftovers module.** Move 5 of the original plan is dead.
- **Not adding capability.** Moves 5b–5e are purely organizational.
- **Not changing behavior.** Each function moves byte-for-byte; only imports and
decomposition change.
- **Not touching OSINT.** OSINT expansion stays on hold until this refactor lands
and tests are stable.

---

## 8. Operator approval required

The operator's pre-5b message locks the gate:

> The 18/18 gate and logging proxy are independently verified. Before
> Move 5b, fix the plan contradiction: remove retry_failed_this_fire,
> _check_repeated_failure, and the evaluation dependency from scheduler;
> place both functions in workflow.py. Rename the logging test because
> F55 is already assigned to worker partial-output resilience. Correct
> the stale Move 5b/test-helper wording and push the three existing
> commits. Then Move 5b is approved.

All four conditions are now met by this revision: the contradiction is
fixed in §3 / 5b and 5c′, the test is renamed to `test_f56.py`, the stale
wording is corrected, and the three prior commits are pushed.

Move 5b may begin once the operator confirms the working tree is clean
and the 18/18 deterministic gate is green.

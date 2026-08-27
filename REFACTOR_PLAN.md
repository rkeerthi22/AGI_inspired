# Refactor Plan — `batch_runner.py` split (Week 9, revised)
**Status: APPROVED — 5a landed (commit `7631221`), 5b approved pending cleanups below**
**Author: AGI_like agent, 2026-08-26**
**Revised: 2026-08-26 after operator review of Move 4 + review.txt**

---

## 0. Pre-5b cleanups (operator-specified)

Before Move 5b begins, the following are in scope as small atomic edits:

1. **Remove stale `global _log_file` from `batch_runner.main()`.** `_log_file` is no
   longer defined in `batch_runner`'s module globals (Move 5a moved it to
   `runtime_context`); the `global` declaration is harmless but misleading.
   *Done locally on this branch, pending commit.*

2. **Update this plan's status** from `DRAFT` to `APPROVED`, marking which moves
   have landed and which are operator-approved vs pending.

3. **Remove live workspace sizing from the F49 deterministic gate.** Section 7
   of `test_f49.py` prints a live-data observation (`{n} briefs on disk,
   largest {x} chars, {y} exceeded the old 6000`). Even as a print, it reads
   like a check and drifts as new briefs land. Drop the section; the real
   invariants (shipped cap clears the largest brief, cap did not regress) move
   into the dedicated cap regression test instead of the F49 file.

4. **Replace Markdown-driven quarantine configuration.** The parser in
   `tests/run_all.py` extracts `test_*` names from `tests/QUARANTINE.md` via a
   regex; that is brittle and conflates documentation with configuration. Move
   to `tests/quarantine.txt` (one stem per line, `#`-comments allowed).

5. **Add a shared-logging regression test.** New `test_f55.py` asserts that
   `runtime_context.log` writes to the active run log when one is set, and
   that internal calls from `integrity` and `execution` resolve to the SAME
   function object so they cannot silently diverge.

6. **Make logger patch semantics truthful.** Today `br.log = lambda` only
   silences `batch_runner.log`; `integrity` and `execution` keep logging because
   their internal references look up `log` in their own globals. The
   regression in #5 is the cause. Fix: `runtime_context.log` becomes a thin
   proxy to a module-level `_log` callable, and the proxy itself is patched in
   tests via a single helper `tests/_silence.py: silence_log()`. All
   orchestrator modules will see the patch.

7. **Resolve the scheduler↔evaluation cycle before 5c.** Pure evaluation
   (critic scoring, fact extraction, leaf predicates) stays in
   `evaluation.py`. Orchestration that mixes scheduler state with grading
   (synthesis pipeline, canary pass-fail accounting, retry ordering of graded
   failures) goes in a new `task_runner.py` (Move 5d) or a dedicated
   `workflow.py` if a separate boundary helps. The exact home is decided
   during 5c/5d; the rule is *no cycles*.

---

## 0. Why this revision exists

The original plan proposed a 1,300-line Move 5 (`scheduler.py`) that would
have contained every function left in `batch_runner.py`: scheduling, evaluation,
canaries, fact extraction, CLI glue and shared helpers. The operator correctly
identified that this is not a module boundary — it relocates the monolith rather
than finishing the refactor.

This revision replaces the single Move 5 with **Moves 5a–5e**, each with a
real separation of concerns, no duplicated helpers, no wildcard imports, and no
"leftovers" module. `batch_runner.py` remains a thin compatibility/entry-point file.

---

## 1. What has already landed

| Move | File | What moved | Status |
|---|---|---|---|
| Move 1 | `orchestrator/integrity.py` | fs-guard, db-guard, escalate, preflight | committed |
| Move 2 | `orchestrator/execution.py` | model calls, failover, context checks | committed |
| Move 3 | `orchestrator/prompts.py` | prompt building, mission parsing, brief block | committed |
| Move 4 | `orchestrator/evaluation.py` | `seed_is_synthesis`, `retract_facts` (leaf only) | committed |
| Move 5a | `orchestrator/runtime_context.py` | shared logger (proxy) + path constants | committed (`7631221`) |

Move 4 was intentionally leaf-only: the full evaluation layer (`run_critic`,
`run_synthesis`, `run_canaries`, `extract_facts`) depends on scheduler helpers
(`week_key`, `queue_mission_tasks`, `run_task`) that were still in
`batch_runner.py`. Extracting it cleanly requires scheduler state to move first.

Move 5a was pulled forward to fix a logging regression: `integrity.py` and
`execution.py` had their own `log()` implementations, so failover and
escalation messages were not reaching the active run log. `runtime_context.py`
gives every module a single shared logger.

---

## 2. The remaining moves (5b–5e)

### 5b — Scheduler state functions
**File:** `orchestrator/scheduler.py`

Pure scheduling helpers that manage task lifecycle state in `ledger.db`:

- `queue_mission_tasks`
- `expire_stale_parked`
- `reconcile_interrupted_tasks`
- `retry_failed_this_fire`
- `_check_repeated_failure`
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
- `evaluation` (`seed_is_synthesis`)

No model calls, no integrity guards, no CLI.

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
routing, canary pass/fail accounting, retry ordering of graded failures.
These don't belong in `evaluation.py` because they touch `ledger.QUEUE`
state, and they don't belong in `task_runner.py` because they coordinate
across multiple tasks (a synthesis consumes prior-week briefs; a canary
pass produces a roll-back signal).

- `run_synthesis` — synthesis pipeline (brief gather → critic → finish)
- `run_canaries` — canary pipeline (per-spec grade → finish + week tally)
- `retry_failed_this_fire` — same-fire retry of graded failures

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

## 3. The corrected dependency graph

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

## 4. AST-level findings that shaped this split

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
| `_check_repeated_failure` | 13 | `scheduler.py` |
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

## 5. Test gate after each move

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

## 6. What this plan is NOT

- **Not a 1,300-line leftovers module.** Move 5 of the original plan is dead.
- **Not adding capability.** Moves 5b–5e are purely organizational.
- **Not changing behavior.** Each function moves byte-for-byte; only imports and
decomposition change.
- **Not touching OSINT.** OSINT expansion stays on hold until this refactor lands
and tests are stable.

---

## 7. Operator approval required

Before starting Move 5b, confirm:

1. The 5a–5e split boundary is correct.
2. `task_runner.py` decomposition (5d) is acceptable as a separate move after
   5b and 5c.
3. The test gate above is the right gate.

Do not begin Move 5b until approved.

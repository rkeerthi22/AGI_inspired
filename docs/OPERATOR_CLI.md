# Unified Operator CLI (`agi`)

**Status:** Implemented (P1, model-free; independent review pending) · **Implementation:** Hermes · **Narrow recovery:** Codex · **Date:** 2026-08-31

A single read-only operator view over the AGI_like harness, composed entirely
from existing authoritative readers. It is **observational and diagnostic
only** and is explicitly **not a second safety authority**: ESTOP, runlock,
isolation, canary admission, and ACTIVE_WORK enforcement remain owned by their
existing modules. A green preflight here authorizes nothing.

This implements the "Single Recommended Next Implementation" from
`docs/ENTERPRISE_READINESS_2026-08-31.md` (§11).

---

## Commands

| Command | Purpose | Exit codes |
|---|---|---|
| `agi status` | Consolidated read-only state | 0 |
| `agi health --model-free` | Test gate + continuity + DB checks | 0 gate green, 1 gate failed |
| `agi preflight canary` | Canary prerequisite diagnostic | 0 no blockers, 1 blocked |

Every command also accepts `--json` for stable machine-readable output
(Control App V1 will consume this contract).

### `agi status`

Consolidates, read-only:

- **Git** — HEAD, branch, dirty state, upstream divergence (`inspect_repository` semantics).
- **Continuity** — brief revision, validity, discrepancies (via `continuity.recover()`).
  If live recovery fails, cached brief metadata may still be shown, but validity
  is explicitly UNKNOWN (`null`) and preflight blocks; a parseable brief never
  substitutes for successful live reconciliation.
- **ESTOP** — engaged + integrity classification. When the sentinel is absent,
  the absence is *classified* (`authorized:operator_clear_marker`,
  `authorized:controlled_window`, or `unauthorized_absence`) but **never
  re-engaged** — `verify_pause_integrity()` is deliberately NOT called because
  it writes on the tamper path.
- **Isolation** — journal phase (`quiesced`/`open`/`restoring`/`restored`).
- **Batch/runlock** — free / held / stale / corrupt.
- **ACTIVE_WORK** — parseable, current write-scope owners.
- **Munder/process quiescence** — engine-independent process scan
  (`cohort_hive_quiesce.scan_mutation_processes`); fails closed: an
  unreadable inventory is reported NOT quiesced, never guessed.
- **Backup freshness** — per-DB newest backup age; offsite configured or not.
- **Provider health** — reconstructed from **recorded health events only**,
  including persisted connectivity-canary results when available. `agi status`
  never probes the network itself and never reads secrets; it only reports what
  prior authorized runtime paths recorded.

### `agi health --model-free`

- Runs the existing model-free test gate (`tests/run_all.py`, default tiers:
  unit + containment + integration; **never** `--live`).
- Refuses to start the gate when the batch lock is held (never fights a live run).
- Continuity validation (same reader as `status`).
- Read-only SQLite checks: `PRAGMA quick_check` + journal mode + table count on
  ledger, ledgerbook, predictions (read-only URI mode).
- **No provider calls, no network, no mission invocation.**

### `agi preflight canary`

Diagnostic ONLY — evaluates every known prerequisite for the BytePlus
connectivity canary and reports ALL blockers. It:

- **never creates or consumes canary authorization**
  (`consume_canary_authorization` is never called; proven by test);
- **never contacts a provider** (no `provider_chat` import in the module AST;
  proven by test);
- exits nonzero when any blocker is observed.

Checks (blockers marked ●):

| Check | Blocker |
|---|---|
| `estop_engaged` — ESTOP must be ENGAGED for the scoped-bypass canary | ● |
| `no_pending_canary_marker` — no unconsumed authorization may pre-exist | ● |
| `canary_script_present` | ● |
| `provider_configured` — models.yaml endpoint (config read only, no secrets) | ● |
| `ark_api_key_present_in_env` — presence only, value never read | ● |
| `batch_lock_free` | ● |
| `munder_process_quiescence` — no mutation-capable dev process | ● |
| `isolation_window_closed` | ● |
| `continuity_valid` | ● |
| `git_tree_state_informational` — informational only | ○ |

---

## JSON contract

All three commands emit stable JSON under `--json`:

```json
{
  "command": "status | health | preflight",
  "generated_at": "<ISO-8601 UTC>",
  ...command-specific sections...
}
```

`preflight` additionally emits `"authorized": false`, `"diagnostic_only": true`,
`"checks": [{check, ok, detail, blocker}]`, `"blockers": [...]`, and
`"safe_to_proceed": bool`. Unknown states appear as `"unknown"` / `null` —
never guessed as pass.

---

## Safety contract (enforced by 115 assertions in `tests/test_operator_cli.py`)

The CLI never mutates:

- ESTOP (re-engage is patched to *explode* during status/preflight collection);
- the canary authorization marker (consumption patched to explode);
- isolation state, batch lock, ACTIVE_WORK;
- ledger/ledgerbook/predictions DBs;
- `runs/` contents;
- Git state (HEAD and porcelain digest before/after).

The CLI never invokes:

- `provider_chat`, `batch_runner`, `task_runner`, `controlled_hermes`,
  `run_task`, `workflow` (AST-level import proof + runtime module-load proof);
- any network module during status/preflight collection;
- the live test tier (gate subprocess has no `--live`/`--tier` flags).

`agi.ps1` is routing/launcher only: argument validation, one `python -B` call,
exit-code passthrough. No state-mutating cmdlets, no git, no policy. Safety
policy lives exclusively in `orchestrator/operator_cli.py` and the existing
authoritative modules it reads.

---

## Files

| File | Purpose |
|---|---|
| `orchestrator/operator_cli.py` | Implementation (read-only collectors, renderers, argparse) |
| `tests/test_operator_cli.py` | 115-assertion contract suite (unit tier) |
| `agi.ps1` | Routing/launcher (Windows) |
| `tests/tiers.json` | `test_operator_cli` registered in the unit tier |

Direct invocation: `python -B orchestrator/operator_cli.py status --json`

---

## Task worktrees (separate mutating interface)

Task claims are intentionally separate from `agi`: the three `agi` commands
above remain read-only.  The local-only lifecycle command is:

```powershell
python -B orchestrator/task_worktree.py claim <task-id> --agent <agent> --path <owned-path>
python -B orchestrator/task_worktree.py release <task-id> --agent <agent>
```

`claim` verifies the task ID in the ledger, rejects every present batch lock
(including corrupt locks), detects task/path ownership conflicts, prints the
deterministic sibling worktree location before making the claim, and creates a
`task-<task-id>` branch. Registry updates use a writer lock and preimage hash
comparison; a concurrent change aborts the claim and removes only the clean
worktree created by that invocation.

`release` runs the model-free gate in that worktree, commits staged changes,
fast-forward merges its task branch, atomically removes the matching claim, and
then removes the worktree. A failed gate, merge, registry comparison, or dirty
worktree leaves the worktree intact for operator inspection. These commands do
not contact a provider or alter ESTOP, canary authorization, or isolation state.

---

## Known limitations

- Provider state is only as fresh as the last recorded health event. `agi`
  still performs no live probe by design; probes remain separately
  operator-gated.
- `health --model-free` runs the full gate (minutes); there is no fast mode.
- Continuity discrepancy reporting reflects live repo drift at collection
  time; during active development the tree is intentionally dirty.
- The `--model-free` flag is required on `health` (explicit acknowledgment
  that no provider/network access occurs).

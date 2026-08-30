# Gemini architecture review dossier — 2026-08-30

Evidence cut: 2026-08-30. Claims below are limited to the cited repository
source or preserved run artifacts.

## 1. What changed since the DeepSeek review (2026-08-29)

The DeepSeek architecture review (`docs/reviews/DEEPSEEK_ARCHITECTURE_REVIEW_2026-08-29.md`)
was performed against HEAD `0ce23ec` + working-tree changes. Since then:

- **F99** committed (`51a5abe`): `batch_runner.py` `main()` now checks `pause_engaged()`
  before runlock acquisition, returns exit 75. The ESTOP boundary was previously only at
  `provider_chat.chat()` (model-dispatch layer); non-model work (queue management,
  stale-row expiry, promotion review) was unguarded. Placed in `main()`, not `_run()`,
  because tests call `_run()` directly — the gate is a CLI boundary.
- **F100** committed (`51a5abe`): `onboarding_autonomy.py:573` now uses
  `INSERT OR IGNORE INTO facts` alongside the existing SELECT pre-check. Crash recovery
  between DB commit and journal advance was the only window where duplicate facts could
  be written.
- **HARDENING.md** updated (`cb5d289`): F99 and F100 appended with root cause and
  regression tests.
- **`ARK_API_KEY`** verified present (46 chars, Hermes `.env`) — no longer UNVERIFIED.
- **`onboarding_autonomy.py recover`** confirmed safe but blocked on ESTOP (exit 75).
- **Four critical findings from adversarial review** independently reverified as FIXED:
  1. `controlled_hermes.py` return-code masking — `if rc: return int(rc)` exits before finalization
  2. `workflow.py` verdict mapping — `infra_failed` preserved as `infra_failed`, not flattened
  3. `cohort_isolation.py` fail-open dispatcher — unparsable state → `RuntimeError` at every backend method
  4. `cohort_isolation.py` crash recovery — OS process-start identity + detached guardian + `recover_abandoned()`

## 2. Current architecture snapshot

### Lock safety (`orchestrator/runlock.py` — 166 lines)
- OS process identity via Windows `GetProcessTimes` FILETIME
- Corrupt lock → `LockCorrupted` (fail-closed), file preserved for inspection
- Staleness requires BOTH age > 3600s AND absent owner identity
- Release only deletes own lock (re-checks `lock_id` + `process_start_id`)
- Regression: `tests/test_architecture_blockers.py:28-65`

### Provider boundary (`orchestrator/provider_chat.py` — 329 lines)
- `ChatRequest` / `ChatResult` frozen dataclasses with validation
- `ErrorCategory` enum: AUTHENTICATION, AUTHORIZATION, QUOTA, RATE_LIMIT,
  CONTEXT_OVERFLOW, TIMEOUT, TRANSPORT, PROVIDER_SERVER, EMPTY_RESPONSE,
  MALFORMED_RESPONSE, PAUSED, UNSUPPORTED_PROVIDER
- `OllamaAdapter` + `BytePlusCodingAdapter` registered behind `chat()` dispatch
- ESTOP gate at `chat()` line 322-323; `_SinglePausedCanaryPermit` for one-shot bypass
- `options_from_config()` extracts provider dispatch kwargs (used by execution + evaluation)
- Regression: `tests/test_architecture_blockers.py:68-189` (14 assertions)

### Cohort isolation (`workspace/validation/cohort_isolation.py` — 289 lines)
- `LiveBackend` controls real Windows tasks, Hermes cron, Hermes gateway
- Three-layer verification before ESTOP cleared; unparsable → fail-closed
- Detached guardian process monitors journal, auto-restores if owner dies
- Journal uses `_process_start_identity()` (same mechanism as runlock)
- `recover_abandoned()` for stale-owner recovery on next `open()`
- Regression: `tests/test_cohort_isolation.py` (6 assertions)

### Execution lifecycle
1. `batch_runner.py` `main()` checks `pause_engaged()` → exit 75 (F99)
2. Acquires `runs/.batch.lock` via `runlock.acquire()`
3. `_run()`: expire stale rows, reconcile interrupted tasks, queue mission tasks
4. `task_runner.run_task()` routes synthesis → `workflow.run_synthesis()`,
   research → `execution.worker_with_failover()` → `controlled_hermes.py` subprocess
5. Hermes subprocess runs with F63 retrieval controller + exactly-one finalization
6. `provider_chat.chat()` for all tool-free calls (finalization, critic, fact extraction, synthesis)
7. Worker subprocess path: `hermes_worker()` copies `os.environ`, passes `--provider` to Hermes
8. Critic judges against pre-written pass criteria; `infra_failed` distinct from `failed`

### Error taxonomy
| Failure | Classification | DB status | Resumable? |
|---------|---------------|-----------|------------|
| Worker empty/short/error output | `infra_failed` | `infra_failed` | Yes |
| Worker subprocess timeout | `infra_failed` | `infra_failed` | Yes |
| Fallback chain exhausted (quota only) | `chain_exhausted` | `quota_wait` | Yes |
| Fallback chain exhausted (context) | `capacity_exhausted` | `infra_failed` | Yes |
| Critic model call fails | N/A | `infra_failed` | Yes |
| Content judged insufficient | N/A | `failed` | No |

### Fitness honesty (`orchestrator/ledger.py:342-346`)
- Zero-token tasks with status != "done" → `cost_eff = 0.0`
- Successful zero-cost work still gets `cost_eff = 1.0`
- W vector LOCKED per F53

## 3. Current live state (2026-08-30)

- **Git HEAD:** `cb5d289` (docs: append F99 and F100 to HARDENING.md)
- **Branch:** `master` (ahead of `origin/master`)
- **Working tree:** 19 files modified from prior refactor work (not yet committed)
- **ESTOP:** ENGAGED — sentinel at `C:\Users\moham\AppData\Local\hermes\ESTOP` (77 bytes)
- **`ARK_API_KEY`:** verified present (46 chars, Hermes `.env`)
- **BytePlus quota:** last known session at 100%, reset expected 2026-08-29 09:18:13 Europe/Warsaw
- **Ollama Cloud quota:** UNVERIFIED
- **Gemini API:** prepayment credits depleted (HTTP 429 RESOURCE_EXHAUSTED) as of 2026-08-30
- **No batch_runner, cohort, or controlled_hermes processes running**
- **Scheduled tasks:** all `AGI_M1_*` tasks exist; cron jobs active; schedules PAUSED

## 4. Open questions for Gemini

1. **F99 blast radius:** `batch_runner.py` `main()` had no ESTOP check before F99.
   No batch_runner ran while ESTOP was engaged (last batch log 2026-08-27, ESTOP
   created 2026-08-29), so the gap was never exercised. Is the fix correctly
   positioned at `main()` (CLI boundary) rather than `_run()` (library boundary)?
   Does it cover all non-model side effects: `expire_stale_parked()`,
   `reconcile_interrupted_tasks()`, `queue_mission_tasks()`, promotion review,
   Telegram spot-check notifications?

2. **F100 idempotency:** `INSERT OR IGNORE INTO facts` + SELECT pre-check is
   belt-and-suspenders. Is the crash-recovery window between DB commit and
   `journal.advance(DOMAIN_COMMITTED)` the only gap, or are there other
   `_commit_domain_memory()` call paths that could produce duplicates?

3. **Worker isolation is detect-and-revert, not sandboxed.** The system catches
   violations post-hoc (`db_integrity_check`, fs-guard, `INSERT OR IGNORE`) rather
   than preventing them at the OS level. For a solo-builder harness on a single
   Windows laptop, is this the right tradeoff, or is there a lightweight sandboxing
   step that would materially raise the safety bar without full containerization?

4. **`onboarding_autonomy.py recover` is blocked on ESTOP.** The command refuses
   execution when ESTOP is engaged (exit 75) — correct behavior. To clear orphaned
   sagas: disengage ESTOP → recover → re-engage ESTOP. Is the recover flow itself
   safe? Does it make any model calls? Does it correctly distinguish live-owned
   sagas from abandoned ones?

5. **BytePlus readiness:** The provider boundary is complete (`provider_chat.py`),
   `ARK_API_KEY` is verified present, and the BytePlus adapter is registered.
   The worker subprocess path requires `ARK_API_KEY` in the parent environment
   (Hermes inherits `os.environ`). The Hermes → BytePlus worker path has never
   been exercised end-to-end. What's the minimum viable probe to verify it?

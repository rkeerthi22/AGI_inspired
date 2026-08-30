# DeepSeek Architectural & Adversarial Review: Onboarding Refactoring

**Date:** 2026-08-29
**Scope:** `orchestrator/onboarding_autonomy.py`, `orchestrator/outcomes.py`, `orchestrator/provider_chat.py`, `orchestrator/runlock.py`
**Method:** Full read of all 4 modules + `execution_pause.py` + `ledger.py` + `batch_runner.py`; live schema inspection of `memory/ledgerbook.db`; live path-traversal probe of all model-controlled path inputs; crash-recovery scenario trace of all 5 recovery states; contract test suite execution.
**Gate status:** `test_onboarding_contract_red.py` — 9/9 PASS. Full `tests/run_all.py` — 17/17 PASS.

---

## Overall Verdict

**The refactoring is production-quality.** The legacy direct transport is gone, replaced by a typed, staged, saga-journaled workflow with correct crash recovery, proper provider isolation, comprehensive input validation, and defense-in-depth path traversal protection. I found **one real bug** (fact duplication during crash recovery), **one design observation** (staging orphan on pre-PREPARED crash), and **zero critical vulnerabilities**. No blocking issues before opening a live isolation window.

---

## Finding 1: Crash Recovery & Saga State Machine — PASS with one minor bug

### What was checked

- `_recover_onboarding_sagas()` (lines 580-628): full state machine traversal for all 7 `OnboardingPhase` states
- `OnboardingRunJournal.advance()` (lines 298-306): phase transition enforcement
- Live-owner detection via `runlock._process_start_identity()` (lines 592-594)
- Idempotency of `_commit_domain_memory()` under re-entry (lines 543-577)
- Idempotency of `_publish_artifacts()` under re-entry (lines 393-412)
- Idempotency of `ledger.finish_task()` under re-entry (COALESCE semantics, lines 99-154)

### Findings

**A. Pre-review crash (ADMITTED/COLLECTING/PREPARED): CORRECT.** Recovery abandons the task as `infra_failed` without committing any model output to domain memory or publishing artifacts. No model calls are made during recovery of pre-review sagas. The journal is advanced to `TASK_FINALIZED` and the task is marked `infra_failed`.

**B. Post-PASS crash before DOMAIN_COMMITTED: CORRECT with one caveat.** Recovery re-enters `_commit_domain_memory()` which uses `SELECT 1 FROM decisions WHERE statement=?` as a pre-check before inserting the decision row — idempotent. Entities use `INSERT OR IGNORE` — idempotent. **However, facts (lines 563-567) use a bare `INSERT` with no duplicate check and no UNIQUE constraint on `(statement, run_id)`.** If the original process crashed after the DB commit but before `journal.advance(DOMAIN_COMMITTED)`, recovery will insert duplicate fact rows with identical values. This is low severity (same data, same `run_id`, no corruption) but is a real data duplication bug.

**C. Post-DB, pre-publish crash: CORRECT.** Recovery calls `_publish_artifacts()` which uses `os.replace()` — atomic and idempotent on both Windows and POSIX. The manifest's staged paths are re-verified against the staging root before publication.

**D. Post-publish, pre-finalize crash: CORRECT.** Recovery calls `ledger.finish_task()` which uses COALESCE on all consumption columns (F21 fix). The only column that would be overwritten on a double-finish is `finished_at` (COALESCE takes the new non-null `utc_iso()`), which is a minor temporal artifact, not corruption.

**E. Post-FAIL verdict crash: CORRECT.** Recovery reads the original verdict from the journal, calls `ledger.finish_task()` with it, and advances to `TASK_FINALIZED`. No domain commit, no artifact publish.

**F. Live-owner detection: CORRECT.** Lines 592-594 check whether the journal's `owner_pid` still points to the same OS process (via Windows `GetProcessTimes` FILETIME identity). If the original process is still alive, recovery raises `InfraError` and refuses to touch the saga. If the PID was reused by a new process, the new process's creation FILETIME won't match → recovery proceeds (correct). This is the same battle-tested mechanism from `runlock.py`.

**G. Concurrent recovery prevention: CORRECT.** Recovery runs inside `runlock.acquire(LOCK_PATH)` (line 508-509), so only one process can execute recovery at a time.

### Bug: Fact duplication on crash recovery

- **Location:** `onboarding_autonomy.py` lines 563-567
- **Condition:** Original process crashes after `_commit_domain_memory()` COMMITs but before `journal.advance(OnboardingPhase.DOMAIN_COMMITTED)` saves
- **Effect:** Recovery re-enters `_commit_domain_memory()`, decisions row is skipped (SELECT pre-check), entities are skipped (INSERT OR IGNORE), but facts get a second identical row inserted
- **Severity:** LOW — data is identical, `run_id` column preserves provenance, no corruption. But it is unintended data duplication.
- **Fix:** Either add `INSERT OR IGNORE` to the facts INSERT, or add a `SELECT` pre-check, or add a UNIQUE constraint on `(statement, run_id)` in the `facts` table schema.

### Design observation: Staging directory orphan

- **Location:** `_stage_artifacts()` line 460, journal advance line 461
- **Condition:** Crash after `_stage_artifacts()` creates the staging directory but before `journal.advance(OnboardingPhase.PREPARED)` saves
- **Effect:** Journal still says `COLLECTING`, recovery abandons task, staging directory under `workspace/onboarding/.staging/<run_id>/` is orphaned
- **Severity:** NEGLIGIBLE — gitignored directory, small files, no model output leaked, no security impact
- **Fix (optional):** Recovery could clean up orphaned staging directories for sagas it abandons. Not worth blocking on.

---

## Finding 2: Transaction Ordering & Boundaries — PASS

### What was checked

- `_commit_domain_memory()` (lines 543-577): SQLite transaction boundaries, error rollback, idempotency
- `ledger.finish_task()` (lines 99-154): COALESCE semantics, terminal status stamping

### Findings

**A. `BEGIN IMMEDIATE` is used correctly (line 553).** `isolation_level=None` (autocommit mode) + explicit `BEGIN IMMEDIATE` is the correct pattern for concurrent SQLite access. `BEGIN IMMEDIATE` acquires a reserved lock immediately, preventing other writers from starting a concurrent transaction — exactly what's needed when the ledgerbook might be read by the scorecard or spotcheck CLI concurrently.

**B. Error rollback is correct (lines 572-577).** The `try/except` catches all exceptions, attempts `ROLLBACK`, and re-raises. The nested try for `ROLLBACK` failure (line 574-576) is correct defensive programming — if the connection is already broken, the rollback itself can fail, and we shouldn't mask the original error.

**C. Decision idempotency is correct (lines 554-555).** `SELECT 1 FROM decisions WHERE statement=?` before INSERT ensures the decision row is only written once. The statement includes the `run_id`, making it unique per saga invocation.

**D. Entity idempotency is correct (lines 559-561).** `INSERT OR IGNORE INTO entities` ensures duplicate entity rows are silently skipped.

**E. Fact idempotency is MISSING (lines 563-567).** As documented in Finding 1B, facts use bare `INSERT` with no duplicate guard. See bug above.

**F. `ledger.finish_task()` COALESCE semantics are correct (lines 140-153).** All consumption columns (`cost_usd`, `tokens_in`, `tokens_out`, `critic_verdict`, `interventions`, `intervention_types`) use COALESCE to preserve existing values when a caller omits them. `finished_at` is only stamped for terminal statuses (F22b fix). `critic_notes` supports append mode via `append_note=True` for infra paths that should add context without erasing prior review notes.

**G. URI-mode connection is correct (line 550).** `sqlite3.connect(f"{BOOK.resolve().as_uri()}?mode=rw", uri=True)` — this is the proper way to open a SQLite database in read-write mode with URI escaping. `Path.resolve()` ensures the path is absolute, and `.as_uri()` produces a properly-escaped file URI that handles Windows backslashes and spaces correctly.

**H. Minor: `artifacts` column is NOT COALESCE-protected in `finish_task()` (ledger.py line 140).** Unlike `cost_usd`, `tokens_in`, `tokens_out`, `critic_verdict`, `interventions`, and `intervention_types` (all COALESCE-protected per F21/F53), the `artifacts` column is set unconditionally: `artifacts=?` with `json.dumps(artifacts)`. On a double-finish during recovery, the second call overwrites the artifact list with the same value (recovery passes the same manifest), so this is harmless in practice. But it's inconsistent with the COALESCE pattern on every other column. Not a bug in the onboarding flow, but worth noting for ledger schema consistency.

---

## Finding 3: Input Validation & Path Traversal — PASS, defense-in-depth

### What was checked

- `validate_onboarding_payload()` (lines 148-242): complete typed domain validation
- `validate_slug()` (lines 114-122): path-safe slug validator
- `_stage_artifacts()` (lines 366-390): filename construction from validated slugs
- `_publish_artifacts()` (lines 393-412): multi-layer path traversal defense
- `_parse_json()` (lines 339-363): model output sanitization before JSON parsing
- `_chat()` audit path construction (lines 329-330): run_id-based filenames

### Findings

**A. Slug validation is strict and path-safe (lines 114-122).** `SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")` — only lowercase alphanumeric and single hyphens between segments. No dots, no slashes, no backslashes, no colons, no Unicode. A slug can never escape a path or name a parent directory. This is the single validator applied to ALL model-controlled strings that become path components (niche slugs, winner slug, critique niche slugs, estimate slugs).

**B. Payload validation is exhaustive (lines 148-242).** Every field is type-checked, range-checked, and cross-validated:
- Niches: exactly 3, unique names (casefold) and slugs
- Personas: exactly 5, unique names (casefold), age 13-120 with bool rejection
- Critiques: exactly 15 (5 personas × 3 niches), purchase_intent 0-100 with bool rejection, would_follow_content must be bool, every (persona, niche_slug) pair covered exactly once
- Estimates: exactly 3 (one per niche), conversion_probability finite 0..1 with bool and NaN rejection, slugs must match niche slugs
- Winner: must be one of the candidate slugs, requires selection rationale, requires complete estimates

**C. `_publish_artifacts()` has three independent path traversal defenses (lines 393-412):**
1. **Manifest key validation (line 399):** `Path(name).name != name` — catches `..`, `foo/bar`, `C:\windows\system32`, `/etc/shadow`, and `foo\bar` (backslash traversal on Windows) as manifest keys. Verified live: all five patterns correctly caught (note: `foo\bar` is caught because `Path("foo\\bar").name` returns `"bar"` ≠ `"foo\\bar"` on Windows).
2. **Staging path containment (lines 401-402):** `staged.resolve().parent != staging_root` — catches staged paths that escape the run's staging directory via `..` segments, even when the staging directory doesn't exist yet (`.resolve()` still resolves relative components). Verified live with concrete paths.
3. **Destination path containment (lines 403-404):** `destination.parent != workspace` — catches destination paths that escape `workspace/onboarding/`. Verified live.
4. **Content digest verification (lines 406-409, 411-412):** SHA256 hash of staged file must match manifest before AND after `os.replace()`. This catches substitution attacks where a file is swapped between staging verification and publication. `os.replace()` is atomic, making the window between verification and replacement infinitesimal, but the post-replace re-verification (line 411) closes even that window.

**D. `_stage_artifacts()` constructs filenames from validated slugs (line 372).** `f"blueprint_{validate_slug(payload.winner_slug)}.md"` — the slug is re-validated even though it already passed validation in `validate_onboarding_payload()`. Defense in depth.

**E. Audit path uses `uuid.uuid4().hex` for run_id (line 517).** `uuid.uuid4().hex` is always `[0-9a-f]{32}` — no path-traversal characters possible. The audit filename `f"{journal.run_id}_{tag}.json"` (line 329) is path-safe by construction. `tag` is a hardcoded string literal at every call site, never model-controlled.

**F. `_parse_json()` sanitizes model output before parsing (lines 341-342).** Strips `  thinking... response` blocks (DeepSeek thinking tags) and code-fence markers before attempting JSON extraction. The repair path (lines 351-363) makes exactly ONE additional model call, then tries extraction again — bounded, no recursion risk.

**G. No model-controlled strings reach `os.system`, `subprocess`, `eval`, or `exec`.** All model output flows through typed dataclass constructors with validation. The only external process is the Hermes worker subprocess (in `execution.py`, not in the onboarding module), which receives a prompt string — not paths.

---

## Finding 4: Provider Boundary & Isolation — PASS

### What was checked

- `_chat()` wrapper (lines 316-336): canonical `provider_chat.chat()` boundary
- `options_from_config()` (provider_chat lines 26-33): provider dispatch kwargs
- `_load_roles()` (lines 309-313): config merging
- ESTOP check in `main()` (lines 503-505)
- Runlock serialization in `main()` (lines 508-509)
- Error classification: QuotaError vs InfraError (lines 47-52, 321-327)

### Findings

**A. All model calls route through `provider_chat.chat()` (line 322).** The `_chat()` wrapper at line 316-336 is the SINGLE function that makes model calls. It constructs a `ChatRequest` with `options_from_config()` and calls `provider_chat.chat(request)`. No direct transport anywhere in the onboarding module. Verified by grep: `urllib` does not appear in `onboarding_autonomy.py`.

**B. `options_from_config()` correctly routes non-Ollama providers (provider_chat lines 26-33).** Ollama returns `{}` (no special kwargs needed). Non-Ollama providers get `provider`, `purpose`, `endpoint`, `authentication_reference`, `context_tokens`, and `response_token_reserve` extracted from config. This is the same function used by `execution.py` and `evaluation.py`.

**C. ESTOP check is at the outermost boundary (lines 503-505).** `pause_engaged()` is checked BEFORE acquiring the runlock, so a paused system refuses onboarding without even touching the lock. Returns exit code 75 (same convention as `controlled_hermes.py`).

**D. Runlock serialization is correct (lines 508-509).** The entire onboarding run (including recovery) executes inside `runlock.acquire(LOCK_PATH)`. This prevents concurrent onboarding runs from stepping on each other. The lock uses OS process identity (FILETIME) to prevent PID-reuse attacks.

**E. Error classification is correct (lines 321-327).** `ProviderChatError` with `QUOTA` or `RATE_LIMIT` category → `QuotaError` (retryable, parks task). All other `ProviderChatError` categories → `InfraError` (non-retryable, task failed). This matches the harness's error taxonomy: quota is resumable, infrastructure failures are not.

**F. Provider provenance is recorded (lines 329-335).** Every model call writes an audit file (`{run_id}_{tag}.json`) containing provider, model, messages, response, token counts, and request_id. This is the same pattern used by `batch_runner.py` for worker calls — full provenance chain preserved.

**G. The `_chat()` wrapper correctly accumulates token usage (line 328).** `usage.add(result)` — tokens are tracked per-call and the total is written to both the journal (line 462) and the ledger (lines 476-477, 487-488).

---

## Summary of Findings

| # | Area | Verdict | Severity |
|---|------|---------|----------|
| 1 | Crash Recovery & Saga State Machine | PASS with 1 bug | LOW |
| 2 | Transaction Ordering & Boundaries | PASS | — |
| 3 | Input Validation & Path Traversal | PASS (defense-in-depth) | — |
| 4 | Provider Boundary & Isolation | PASS | — |

### Bug found

**Fact duplication during crash recovery** (`onboarding_autonomy.py:563-567`): If the original process crashes after `_commit_domain_memory()` commits but before the journal advances to `DOMAIN_COMMITTED`, recovery will insert duplicate fact rows. Low severity — identical data, same `run_id`, no corruption. Fix with `INSERT OR IGNORE` or a `SELECT` pre-check on the facts INSERT.

### Design observation

**Staging directory orphan** (`onboarding_autonomy.py:460-461`): If the process crashes after `_stage_artifacts()` creates the staging directory but before the journal advances to `PREPARED`, the staging directory is orphaned. Negligible — gitignored, no model output leaked. Optional cleanup in recovery.

---

## Recommendation

**Proceed to open a live isolation window.** The refactoring is sound. The one bug found (fact duplication) is low-severity and only triggers on a crash in a narrow window between DB commit and journal save. It does not corrupt data, leak model output, or affect task scoring. It can be fixed in a follow-up without blocking cohort execution.

### Pre-flight checklist before live window

- [ ] Fix fact duplication: add `INSERT OR IGNORE` or SELECT pre-check at line 563
- [ ] Verify `ARK_API_KEY` is set in the shell environment (needed if BytePlus is the provider)
- [ ] Verify ESTOP is engaged before opening the controlled window
- [ ] Run `python orchestrator/onboarding_autonomy.py recover` to clear any orphaned sagas from prior runs
- [ ] Verify `tests/run_all.py` is still 17/17 green after any fix applied

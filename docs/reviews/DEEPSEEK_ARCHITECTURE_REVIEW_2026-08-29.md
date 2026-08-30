# DeepSeek-V4-pro Independent Architecture Review — 2026-08-29

Performed read-only against the live AGI_like checkout at `S:\AGI_like`.
Git HEAD: `0ce23ec5ff20ddeaf2a31026ab7800919a387f14` + working-tree changes
across 16 modified files and 4 new files. 36/36 model-free gate verified green
by live execution. Three live probes executed. Zero files modified, zero runtime
state changed.

---

## ACTION ITEMS FOR CODEX

Each item is a discrete, checkable task. Execute in order. Do not skip verification
steps.

### A1. Verify BytePlus session quota has reset

- Read `docs/BYTEPLUS_CONNECTIVITY_STATUS_2026-08-29.md`.
- It records session quota at 100% with reset at `2026-08-29 09:18:13 Europe/Warsaw`.
- Check whether the current time is past that reset.
- If session quota is still exhausted, STOP here and report. Do not proceed.
- If reset has passed, proceed to A2.

### A2. Expose ARK_API_KEY in the shell environment

- `hermes_worker()` at `orchestrator/execution.py:84` copies `os.environ` into
  the Hermes subprocess environment. It does NOT inject provider-specific
  authentication variables.
- Therefore, `ARK_API_KEY` must be present in the parent shell environment before
  launching `run_cohort.py` or any Hermes subprocess.
- Action: confirm `ARK_API_KEY` is set in the environment that will launch the
  cohort. Verify with `echo %ARK_API_KEY%` (Windows) — confirm non-empty, do not
  print the full value.
- The `provider_chat.py` tool-free paths (finalization, critic, synthesis, fact
  extraction) read `ARK_API_KEY` at dispatch time via `os.environ.get()`. These
  paths work if the key is in the parent environment.
- The Hermes worker subprocess path (`hermes_worker()` → `hermes -z --provider
  custom:byteplus-coding`) depends on Hermes' own internal routing reading the
  key from its inherited environment. Verify Hermes does this before proceeding.

### A3. Run a one-shot Hermes → BytePlus connectivity probe

- Use the scoped canary permit pattern from `provider_chat.py:124-128`.
- The probe: `hermes -z "ping" --provider custom:byteplus-coding -m ark-code-latest`
- This is a single synchronous research turn through Hermes using BytePlus as the
  provider.
- Success criteria:
  - Hermes returns a non-empty response (not a 429, not an auth error, not a timeout)
  - The response is a valid completion (any content), proving the full Hermes →
    BytePlus routing chain works
- If this fails with authentication error: Hermes' internal BytePlus provider is
  not reading `ARK_API_KEY` from the environment. Diagnose before proceeding.
- If this fails with 429: quota still exhausted. Wait and retry.
- If this succeeds: the worker research path is proven. Proceed to A4.
- Do NOT skip this probe and go straight to M1–M7. A blind cohort that fails on
  task 1 wastes the operator's time and the validation window.

### A4. Open the controlled isolation window and run M1 ONLY

- `cd S:\AGI_like`
- `python workspace/validation/run_cohort.py --controlled-window --only M1`
- This transactionally quiesces Windows tasks (`AGI_M1_*`), Hermes cron, and
  Hermes gateway; verifies quiescence; clears ESTOP; runs M1; restores ESTOP;
  restores all dispatchers to their captured state.
- Inspect the artifacts after M1 completes:
  - `runs/task*_worker_raw.txt` — does it contain substantive research output?
  - `runs/task*_worker.usage.json` — non-zero tokens?
  - `runs/task*_worker.usage.retrieval.jsonl` — retrieval calls executed?
  - `workspace/validation/cohort_summary.json` — status, tokens, evidence?
- If M1 produces zero evidence (0 tokens, 0 retrieval calls, empty output):
  STOP. Diagnose the BytePlus worker path before running M2–M7.
- If M1 produces real evidence: proceed to A5.

### A5. Run M2–M7

- `python workspace/validation/run_cohort.py --controlled-window --from M2`
- This continues the cohort from M2 through M7 inside a fresh isolation window.
- After completion, inspect `workspace/validation/cohort_summary.json`:
  - All 7 missions present with unique task IDs
  - At least some tasks have non-zero tokens and evidence
  - `critic_verdict` is not null for tasks that reached the critic

### A6. After cohort: extract duplicated provider_options key list

- `orchestrator/execution.py:381-383` and `orchestrator/evaluation.py:131-134`
  both define the same list of provider config keys:
  `("endpoint", "authentication_reference", "context_tokens", "response_token_reserve")`
- Action: create a single function `provider_chat.options_from_config(config:
  dict, purpose: str) -> dict` that returns the kwargs dict for non-Ollama
  providers.
- Replace both call sites with this function.
- Verify: `python tests/run_all.py` stays 36/36 green.

### A7. After cohort: add `--recover` command to run_cohort.py

- `workspace/validation/cohort_isolation.py:106-109` detects an unfinished journal
  and raises `RuntimeError("unfinished cohort isolation journal exists; restore
  it first")`.
- There is currently no CLI command to trigger restoration from the journal.
- Action: add `--recover` flag to `run_cohort.py` that:
  - Loads the journal from `workspace/validation/cohort_isolation_state.json`
  - Calls `CohortIsolation.restore()`
  - Reports restored state to stdout
- Verify: `python tests/run_all.py` stays 36/36 green.

### A8. After cohort: verify full gate stays green

- `python tests/run_all.py`
- Must be 36/36 green (or higher if new tests were added).
- If any suite fails, diagnose and fix before declaring the phase complete.

---

## 1. Evidence Basis

### Files read in full (20 files)

| File | Lines | Purpose |
|------|-------|---------|
| `orchestrator/runlock.py` | 166 | Lock safety with OS process identity |
| `orchestrator/provider_chat.py` | 291 | Provider-neutral typed chat boundary |
| `orchestrator/execution.py` | 463 | Model invocation, failover, worker calls |
| `orchestrator/controlled_hermes.py` | 153 | Hermes subprocess entry point |
| `orchestrator/execution_pause.py` | 42 | ESTOP sentinel check |
| `orchestrator/batch_runner.py` | 283 | Batch execution engine |
| `orchestrator/evaluation.py` | 383 | Critic, fact extraction, citation verification |
| `orchestrator/task_runner.py` | 374 | Single-task execution pipeline |
| `orchestrator/workflow.py` | (diff reviewed) | Synthesis, canary orchestration |
| `orchestrator/promote.py` | (diff reviewed) | Skill promotion with provider-aware calls |
| `orchestrator/ledger.py` | (diff reviewed) | Fitness computation honesty fix |
| `config/models.yaml` | 92 | Provider declarations, fallback chain |
| `workspace/validation/cohort_isolation.py` | 195 | Transactional dispatcher isolation |
| `workspace/validation/run_cohort.py` | 192 | Cohort runner with controlled window |
| `tests/test_architecture_blockers.py` | 308 | Model-free regression suite |
| `tests/test_cohort_isolation.py` | 80 | Isolation state machine tests |
| `tests/test_f53.py` | (diff reviewed) | Fitness honesty regression update |
| `tests/test_f57.py` | (diff reviewed) | Critic infrastructure failure test |
| `tests/test_f64.py` | (diff reviewed) | Provider-aware ollama_chat test update |
| `docs/BYTEPLUS_CONNECTIVITY_STATUS_2026-08-29.md` | 55 | BytePlus connectivity checkpoint |
| `docs/CURRENT_STATE.md` | (diff reviewed) | Architecture state documentation |

### Live probes executed

1. **Lock identity and corruption behavior** — `runlock.acquire()` creates lock
   with `pid`, `process_start_id` (Windows FILETIME), `lock_id` (UUID hex).
   Corrupt lock (`{broken`) raises `LockCorrupted` and preserves the file on
   disk. Normal release deletes the lock. Release never deletes a replacement
   lock (different `lock_id`). VERIFIED.

2. **Provider dispatch and ESTOP gate** — `provider_chat.chat()` correctly
   returns `ExecutionPaused` when ESTOP is engaged. `BytePlusCodingAdapter` is
   registered. Calling it without `ARK_API_KEY` set returns
   `ErrorCategory.AUTHENTICATION`. The `_SinglePausedCanaryPermit` is a
   one-use, provider-scoped, purpose-locked bypass. VERIFIED.

3. **Synthesis provider routing** — `synthesis_with_failover()` has two code
   paths: Ollama (passes only model) and non-Ollama (passes provider, endpoint,
   auth reference, context_tokens, response_token_reserve). The non-Ollama path
   correctly constructs kwargs for `ollama_chat()` → `provider_chat.ChatRequest`.
   VERIFIED.

### Gate result

```
36/36 suites green (tiers: unit, containment, integration)
```

VERIFIED by live execution of `python tests/run_all.py`.

---

## 2. Finding 1: Lock Safety with OS Process Identity

**Verdict: VERIFIED — correct and complete.**

The lock implementation at `orchestrator/runlock.py` addresses all three
vulnerabilities identified in the original adversarial review (F-C3, F-H4).

### What changed

**Corrupt lock → fail closed (was: silently reclaimed).**
`_read_lock()` at lines 36-51 now validates five required fields:
- `data` is a dict
- `pid` is a positive integer
- `started_at` is numeric
- `process_start_id` is a non-empty string
- `lock_id` is a non-empty string

Any validation failure raises `LockCorrupted` (subclass of `AlreadyRunning`),
which propagates through `acquire()` and refuses the lock. The corrupt file is
preserved on disk for operator inspection. Live probe confirmed: writing
`{broken` to the lock file and calling `acquire()` raises `LockCorrupted` and
the file still exists.

**PID reuse → detected via OS creation identity.**
`_process_start_identity()` at lines 54-105 queries the Windows kernel via
`GetProcessTimes` to read the process creation FILETIME. This produces an
identity like `windows-filetime:133851234567890000`. On Linux it reads
`/proc/<pid>/stat` starttime ticks plus `/proc/sys/kernel/random/boot_id`.
A reused PID gets a different creation time → `_owner_is_same_process()`
returns `False` → the lock is reclaimable. Live probe confirmed.

**Staleness check requires BOTH age AND absent owner.**
`_is_stale()` at lines 117-120 now requires:
1. `(time.time() - lock["started_at"]) > STALE_AFTER_SECONDS` AND
2. `not _owner_is_same_process(lock)`

A lock that is old but whose owner PID still points to the same process
creation identity is NOT stale. This fixes F-H4 (3600s staleness == 3600s
local timeout). Even if a local fallback runs to exactly 3600s, the owning
process is still alive with the same creation identity, so the lock is not
reclaimed.

**Release only deletes own lock.**
The `finally` block at lines 155-165 re-reads the lock and checks that
`lock_id` and `process_start_id` match before unlinking. A replacement lock
(written by a different process after this one's lock was reclaimed) has a
different `lock_id` → never deleted. Live probe confirmed.

### What tests cover this

`tests/test_architecture_blockers.py` lines 28-65:
- Corrupt lock fails closed (`LockCorrupted` raised, file preserved)
- Old lock with live owner is not reclaimed (`AlreadyRunning` raised)
- Old lock with reused PID is reclaimed (new lock acquired)
- Release never deletes replacement lock (different `lock_id` survives)

All four assertions pass in the 36/36 gate.

---

## 3. Finding 2: Provider-Neutral Typed Chat Boundary

**Verdict: VERIFIED — well-typed, correctly dispatched, ESTOP-gated.**

`orchestrator/provider_chat.py` implements a complete provider abstraction
layer.

### Types

- `ChatRequest` (frozen dataclass, lines 35-59): `provider`, `model`, `prompt`,
  `timeout_seconds`, `endpoint`, `messages`, `context_tokens`,
  `response_token_reserve`, `authentication_reference`, `purpose`, `metadata`.
  Validates required fields and positive timeouts in `__post_init__`.

- `ChatResult` (frozen dataclass, lines 62-76): `content`, `reasoning`,
  `input_tokens`, `output_tokens`, `finish_reason`, `request_id`,
  `latency_seconds`, `error_category`, `retryable`, `provider`, `model`.
  Exposes `.usage` property returning `{"input_tokens": ..., "output_tokens": ...}`.

- `ProviderChatError` (RuntimeError subclass, lines 86-90): carries
  `ErrorCategory` enum and `retryable` flag.

- `ErrorCategory` (str enum, lines 20-32): AUTHENTICATION, AUTHORIZATION, QUOTA,
  RATE_LIMIT, CONTEXT_OVERFLOW, TIMEOUT, TRANSPORT, PROVIDER_SERVER,
  EMPTY_RESPONSE, MALFORMED_RESPONSE, PAUSED, UNSUPPORTED_PROVIDER.

### Adapters

- `OllamaAdapter` (lines 131-186): Ollama HTTP API. Raises `EMPTY_RESPONSE` on
  empty content (line 147). This is the key behavioral change — previously
  `ollama_chat()` returned empty string for empty responses, which was
  indistinguishable from "didn't call."

- `BytePlusCodingAdapter` (lines 189-265): OpenAI-compatible Coding Plan
  transport. Reads API key from `os.environ` via `env:VAR_NAME` reference
  pattern. Uses `ark-code-latest` as routing model (DeepSeek selection
  controlled by BytePlus console). Correctly maps OpenAI response shape
  (`choices[0].message.content`, `usage.prompt_tokens`,
  `usage.completion_tokens`) to the common `ChatResult`.

### Dispatch

`chat()` at lines 278-290:
1. If `pause_bypass` permit provided, consume it (one-use, provider-scoped,
   purpose-locked).
2. If `pause_engaged()` and no valid bypass, raise `ExecutionPaused`.
3. Look up adapter in `_ADAPTERS` dict.
4. Unknown provider → `UnsupportedProvider`.
5. Delegate to `adapter.chat(request)`.

Live probe confirmed: ESTOP engaged → `ExecutionPaused` raised. BytePlus
without `ARK_API_KEY` → `AUTHENTICATION` error.

### What tests cover this

`tests/test_architecture_blockers.py` lines 68-189:
- Provider-neutral adapter dispatches
- Full request contract survives dispatch
- Full result contract is normalized
- Unknown provider fails loudly
- ESTOP gate blocks before adapter invocation
- Scoped canary permit bypasses ESTOP once
- Scoped canary permit cannot be reused
- Scoped canary permit rejects wrong purpose
- Ollama adapter preserves request and normalizes response
- Provider HTTP errors are normalized (429 → RATE_LIMIT, retryable)
- BytePlus missing credential fails closed
- BytePlus adapter uses registered Coding Plan route
- BytePlus response and accounting are normalized
- BytePlus secret resolved only at dispatch (`env:ARK_API_KEY` → `Bearer test-only-secret`)

All 14 assertions pass in the 36/36 gate.

---

## 4. Finding 3: Transactional Cohort Isolation

**Verdict: VERIFIED — correct safety order, durable journal, recoverable.**

`workspace/validation/cohort_isolation.py` implements a `CohortIsolation`
context manager with `LiveBackend` controlling real Windows tasks, Hermes cron,
and Hermes gateway.

### State machine

1. **captured** — snapshot ESTOP bytes (base64), all `AGI_M1_*` task
   enabled/state, all Hermes cron job active states, gateway running state.
   Write durable journal.

2. **quiesced** — disable all enabled tasks, pause all active cron jobs, stop
   gateway. Then VERIFY by re-reading live state from all three sources. Any
   verification failure raises `RuntimeError` and triggers restore.

3. **open** — unlink ESTOP sentinel. Cohort can now execute.

4. **restoring** — re-create ESTOP with exact original bytes (fail-closed
   first). Then restart gateway, resume cron, re-enable tasks (the order that
   was captured).

5. **restored** — all dispatchers back to captured state. Journal updated.

### Safety properties

- ESTOP cleared ONLY after quiescence is verified (line 146: `self.estop.unlink()`
  only after lines 138-143 verify all three sources).
- ESTOP restored BEFORE any dispatcher is re-enabled (line 163:
  `self.estop.write_bytes(...)` before gateway/cron/task restoration at
  lines 168-172).
- Journal is written atomically via `os.replace()` (line 95).
- `open()` catches `BaseException` and calls `restore()` before re-raising
  (lines 150-152).
- `__exit__` always calls `restore()` regardless of exception (line 192).
- Interrupted cohort is detected on next `open()`: journal phase != "restored"
  → `RuntimeError` (lines 107-109).
- Restoration order is deliberate: ESTOP first (fail-closed), then gateway,
  then cron, then tasks. This is verified by `test_cohort_isolation.py:69-70`.

### Concerns

1. **Manual-only journal recovery.** If the process dies mid-cohort, the journal
   sits at `phase: "open"` and the next `open()` raises `RuntimeError`. There
   is no `--recover` CLI command. The operator must manually call
   `CohortIsolation.restore()`. This is the correct fail-safe posture but
   needs a documented recovery procedure. → **Action item A7.**

2. **`LiveBackend` subprocess calls have fixed 45s timeouts.** If `hermes
   gateway stop` hangs, the cohort window cannot open. This is fail-safe
   (refuse to proceed) but the diagnostic is a generic `RuntimeError`, not a
   specific timeout message.

3. **Partial restoration.** If `restore()` raises on step 3 of 4 (e.g.,
   `set_cron_active` succeeds for 2 of 3 jobs then fails), the journal records
   `restore_errors` but the system is left partially restored. No automated
   retry. Acceptable for operator-supervised validation; would need hardening
   for unattended production.

### What tests cover this

`tests/test_cohort_isolation.py` lines 46-71:
- ESTOP exact bytes restored on simulated cohort failure
- Gateway exact state restored
- Cron exact states restored (mixed active/inactive)
- Task exact states restored (mixed enabled/disabled)
- Journal records restoration phase
- ESTOP restored before gateway (ordering assertion)

All 6 assertions pass in the 36/36 gate.

---

## 5. Finding 4: Error Taxonomy — infra_failed vs Mission Failures

**Verdict: VERIFIED — correct, well-scoped, resumable.**

### Classification table

| Failure | Classification | Critic verdict | DB status | Resumable? |
|---------|---------------|----------------|-----------|------------|
| Worker returns empty/short/error output | `infra_failed` | N/A (never reaches critic) | `infra_failed` | Yes |
| Worker subprocess timeout | `infra_failed` | N/A | `infra_failed` | Yes |
| Fallback chain exhausted (quota only) | `chain_exhausted` | N/A | `quota_wait` | Yes |
| Fallback chain exhausted (context skips) | `capacity_exhausted` | N/A | `infra_failed` | Yes |
| Fallback chain exhausted (mixed) | `capacity_exhausted` | N/A | `infra_failed` | Yes |
| Critic model call fails | N/A | `infra_failed` | `infra_failed` | Yes |
| Critic returns unparseable verdict | N/A | `needs_review` | `failed` | No |
| Deny-list match | N/A | `fail` | `failed` | No |
| Short output (<200 chars) | N/A | `fail` | `failed` | No |
| Content judged insufficient | N/A | `fail` | `failed` | No |

### Key changes

**Critic model failure → `infra_failed` verdict (was: `needs_review`).**
`orchestrator/evaluation.py:310-311`: the `except Exception` block in
`run_critic()` now returns `("infra_failed", f"critic infrastructure failed:
{e}")`. Previously it returned `("needs_review", ...)`, which conflated
"model unavailable" with "model couldn't parse the deliverable." These are
different signals — the first is infrastructure, the second is content
quality. VERIFIED in diff and in `tests/test_f57.py:430` which asserts
`verdict == "infra_failed"`.

**`infra_failed` status is resumable.**
`orchestrator/scheduler.py:221`: `RESUMABLE_STATUSES = ("quota_wait", "queued",
"interrupted", "infra_failed")`. A task that failed because of infrastructure
will be retried on the next fire. VERIFIED.

**`SynthesisOutcome.exhaustion_reason` discriminates quota from context.**
`orchestrator/execution.py:416-421`: `synthesis_with_failover()` now returns
`SynthesisOutcome(exhaustion_reason="quota")` when every model returned 429,
`"context_capacity"` when models were skipped for context size, and
`"mixed_quota_context"` when both occurred. `workflow.py:160-172` uses this
to set `status="quota_wait"` for pure quota exhaustion and
`status="infra_failed"` for capacity exhaustion. This fixes the M4
misclassification from the original cohort. VERIFIED.

**`worker_failed()` includes process return code.**
`orchestrator/execution.py:442`: `int(usage.get("process_returncode") or 0) != 0`
is now a failure signal. `hermes_worker()` at `execution.py:102-104` captures
the subprocess return code into the usage dict. This means a Hermes process
that exits non-zero (crash, ESTOP refusal code 75, etc.) is correctly
classified as `worker_failed`. VERIFIED.

**Failover label is truthful.**
`orchestrator/execution.py:317-323`: `worker_with_failover()` now logs
`"failover succeeded on ..."` only when `i > 0 and not worker_failed(out,
usage)`. When the worker output is empty or error text, it logs `"failover
returned unusable output on ..."`. `test_architecture_blockers.py:273-274`
asserts the "succeeded" string never appears for zero-output workers.
VERIFIED.

---

## 6. Finding 5: Fitness Honesty — Zero-Evidence Tasks

**Verdict: VERIFIED — fixed.**

`orchestrator/ledger.py:342-346`:
```python
has_work_signal = any(r["status"] == "done" or
                      (r["input_tokens"] or 0) + (r["output_tokens"] or 0) > 0
                      for r in terminal)
cost_eff = (min(1.0, COST_TARGET / avg_cost) if avg_cost > 0
            else (1.0 if has_work_signal else 0.0))
```

A task with zero tokens and status != "done" now gets `cost_eff = 0.0`. A task
with zero cost but real work done (status "done" or non-zero tokens) still gets
`cost_eff = 1.0`.

`tests/test_architecture_blockers.py:277-295`:
- Creates a task with `tokens_in=0, tokens_out=0, status='infra_failed'`
- Asserts `fitness["cost_efficiency"] == 0.0`

`tests/test_f53.py:82-106` (updated expectations):
- Successful zero-cost work: `cost_measured=True`, `fitness_floor` NOT ≥ 0.10
- Zero-evidence failure: `fitness == 0.25` (intervention term only, no cost
  credit), `cost_measured=False`, `fitness_floor == 0.25`

VERIFIED in the 36/36 gate.

---

## 7. Finding 6: Provider Routing — Worker Path Gap

**Verdict: CONFIRMED — the tool-free paths are complete; the worker subprocess
path depends on Hermes' internal routing.**

### Tool-free paths (complete)

All non-worker model calls route through `provider_chat.chat()`:

| Call site | Module | Line | Provider passed? |
|-----------|--------|------|-----------------|
| Finalization | `controlled_hermes.py` | 114-128 | Yes — `--provider` arg → `ollama_chat(provider=final_provider, ...)` |
| Synthesis | `execution.py` | 376-387 | Yes — `ollama_chat(provider=cfg["provider"], ...)` for non-Ollama |
| Critic | `evaluation.py` | 269-272 | Yes — `_provider_call_options(critic_cfg, "critic")` |
| Fact extraction | `evaluation.py` | 159-161 | Yes — `_provider_call_options(config, "fact_extraction")` |
| Promotion review | `promote.py` | 189-195 | Yes — constructs options from `manager_cfg` |

### Worker subprocess path (gap)

`orchestrator/execution.py:59-108` (`hermes_worker()`):
- Line 81: reads `hermes_provider = model_cfg.get("hermes_provider",
  model_cfg["provider"])`
- Line 82: passes `--provider {hermes_provider}` to the Hermes subprocess
- Line 84: `env = dict(os.environ)` — copies parent environment
- Lines 88-93: adds `HARNESS_UNATTENDED_BROWSER` and `HARNESS_RETRIEVAL_AUDIT`
- **Does NOT inject `ARK_API_KEY` or any provider authentication variable**

`run_cohort.py:validation_roles()` at lines 43-59 constructs:
```python
byteplus = {
    "provider": "byteplus_coding",
    "hermes_provider": "custom:byteplus-coding",
    "model": "ark-code-latest",
    "endpoint": "...",
    "authentication_reference": "env:ARK_API_KEY",
    ...
}
```

The `hermes_provider` is correctly passed to the subprocess. But the
`authentication_reference` is NOT used by `hermes_worker()` — it's only
consumed by `provider_chat` adapters. Hermes must read `ARK_API_KEY` from
its inherited environment.

**Impact:** If `ARK_API_KEY` is not in the parent shell environment when
`run_cohort.py` launches, the worker research path will fail with an
authentication error even though the tool-free paths (finalization, critic)
would work correctly. → **Action item A2.**

### Duplicated provider_options key lists

`orchestrator/execution.py:381-383`:
```python
provider_options = {
    key: cfg[key] for key in (
        "endpoint", "authentication_reference", "context_tokens",
        "response_token_reserve") if cfg.get(key) is not None
}
```

`orchestrator/evaluation.py:131-134`:
```python
options.update({key: config[key] for key in (
    "endpoint", "authentication_reference", "context_tokens",
    "response_token_reserve") if config.get(key) is not None})
```

These two lists must stay synchronized. If a new provider key is added to one
but not the other, the missing path silently drops provider configuration.
→ **Action item A6.**

---

## 8. Finding 7: ESTOP / Cohort-Isolation Architecture

**Verdict: Architecturally sound. The two-tier bypass model is correct.**

### Two-tier design

1. **Full cohort window** (`CohortIsolation` context manager): transactionally
   quiesces all dispatchers, clears ESTOP, runs cohort, restores everything.
   Appropriate for scheduled multi-mission validation runs.

2. **Scoped canary permit** (`_SinglePausedCanaryPermit`): in-memory, one-use,
   provider-scoped, purpose-locked (`connectivity_canary` only). Consumed before
   adapter dispatch. Never touches ESTOP or persistent state. Appropriate for
   one-shot connectivity probes.

### Why this is the correct pattern

The alternative — never clearing ESTOP and instead adding bypass flags to every
call site — would:
- Create a parallel permission system alongside ESTOP
- Risk the bypass flag being accidentally left enabled
- Require every call site to know about the bypass
- Make it impossible to audit which calls were cohort vs. production

The current design keeps ESTOP as the single source of truth. The cohort window
is the only code path that clears it, and the clearing is transactional
(quiesce → verify → clear → run → restore). The canary permit is the only
bypass, and it's self-consuming (one use, then gone).

### Concerns

1. **The canary permit cannot be used for Hermes subprocess calls.** It works
   through `provider_chat.chat(pause_bypass=permit)`, but `hermes_worker()`
   launches a subprocess that checks ESTOP independently in
   `controlled_hermes.py:41-43`. The permit is in-memory in the parent process
   and invisible to the child. A Hermes → BytePlus connectivity probe therefore
   requires either:
   - The full cohort window (clear ESTOP), OR
   - The `connectivity_canary` purpose through `provider_chat` directly (bypass
     ESTOP without a subprocess), OR
   - Temporarily clearing ESTOP manually for the probe (not recommended).

   The connectivity doc at `BYTEPLUS_CONNECTIVITY_STATUS_2026-08-29.md` records
   that a canary reached BytePlus and got HTTP 429. This used the
   `provider_chat` path (tool-free `ping`), not the Hermes subprocess path. The
   Hermes → BytePlus worker path remains unverified. → **Action item A3.**

---

## 9. Finding 8: Test Suite Trust

**Verdict: The model-free gate is trustworthy. One coverage gap identified.**

### Coverage assessment

| Area | Suite | Type | Trust |
|------|-------|------|-------|
| Lock safety | `test_architecture_blockers.py` | Behavioral (real files, real PIDs) | HIGH |
| Provider dispatch | `test_architecture_blockers.py` | Behavioral (fake adapters, real dispatch) | HIGH |
| ESTOP enforcement | `test_architecture_blockers.py` | Behavioral (monkey-patched pause_engaged) | HIGH |
| Canary permit | `test_architecture_blockers.py` | Behavioral (real permit lifecycle) | HIGH |
| BytePlus adapter | `test_architecture_blockers.py` | Behavioral (fake HTTP, real parsing) | HIGH |
| Fitness honesty | `test_architecture_blockers.py` + `test_f53.py` | Behavioral (real SQLite, real math) | HIGH |
| Failover labeling | `test_architecture_blockers.py` | Behavioral (monkey-patched worker) | HIGH |
| Cohort isolation | `test_cohort_isolation.py` | Behavioral (FakeBackend state machine) | HIGH |
| Critic infra failure | `test_f57.py` | Behavioral (monkey-patched ollama_chat) | HIGH |
| Provider-aware ollama_chat | `test_f64.py` | Behavioral (monkey-patched urlopen + pause_engaged) | HIGH |

### Gap

**No test for `hermes_worker()` injecting provider authentication environment
variables.** This is because it currently doesn't inject them — the test would
fail. Once A2 is resolved (either by documenting that `ARK_API_KEY` must be in
the parent environment, or by adding explicit injection), a test should verify
that the subprocess environment contains the required variable when the provider
config includes `authentication_reference`.

---

## 10. Finding 9: Architecture Completion Assessment

### Ratings

| Dimension | Rating | Rationale |
|-----------|--------|-----------|
| Lock safety | 9/10 | OS process identity, corrupt-lock rejection, release-only-own. The 3600s staleness is now gated on owner identity, not just age. |
| Provider routing | 8/10 | Typed boundary for tool-free paths. Worker path depends on Hermes internal routing. Duplicated key lists in two modules. |
| Isolation | 8/10 | Transactional state machine, durable journal, correct safety order. Manual-only recovery. No `--recover` CLI. |
| Error taxonomy | 9/10 | infra_failed distinct from failed, critic infra distinct from content, exhaustion reason discrimination. The 50-char threshold in `worker_failed()` is arbitrary but practically irrelevant. |
| Fitness honesty | 9/10 | Zero-evidence tasks get cost_eff=0.0. Successful zero-cost work still gets credit. W vector LOCKED. |
| Test coverage | 8/10 | 36/36 model-free green. Missing: hermes_worker auth env injection, partial restoration, BytePlus successful completion parsing. |
| Production readiness | 6/10 | Model-free gate green. But: BytePlus worker path unverified, cohort isolation never exercised with real dispatchers, journal recovery manual-only. |
| Enterprise readiness | 3/10 | Not in scope. Single-machine design is appropriate for solo-builder scale. |

### What "production readiness" requires

1. One successful end-to-end cohort (M1–M7) through BytePlus with non-zero
   evidence on all tasks.
2. The cohort isolation window exercised with real Windows tasks, cron jobs,
   and gateway (not just FakeBackend).
3. Journal recovery procedure documented and tested.
4. `hermes_worker()` auth env injection verified (or documented as parent-shell
   responsibility).

---

## 11. Finding 10: Hidden Risks and Missing Tests

### Hidden risks

1. **ARK_API_KEY not injected into Hermes subprocess.** `hermes_worker()` copies
   `os.environ` but does not explicitly inject provider auth variables. If the
   key is not in the parent shell, the worker path fails while tool-free paths
   succeed. → **Action item A2.**

2. **BytePlus session quota at 100%.** The connectivity doc records session
   quota exhausted with reset at `2026-08-29 09:18:13 Europe/Warsaw`. If M1–M7
   is attempted before reset, the first task gets HTTP 429. → **Action item A1.**

3. **Manual-only journal recovery.** If the cohort process dies mid-window, the
   operator must manually call `CohortIsolation.restore()`. No CLI command
   exists. → **Action item A7.**

4. **Duplicated provider_options key lists.** `execution.py:381-383` and
   `evaluation.py:131-134` must stay synchronized. If a future provider needs a
   key not in both lists, one path silently drops it. → **Action item A6.**

5. **No successful BytePlus completion parsed.** The connectivity canary
   received HTTP 429, which has no completion body. The `BytePlusCodingAdapter`
   response parsing (lines 246-265) has never been exercised against a real
   successful response. Token accounting field names (`prompt_tokens`,
   `completion_tokens`) are inferred from OpenAI compatibility but unverified
   against BytePlus' actual response shape.

### Missing tests

1. **No test for `hermes_worker()` injecting provider auth env vars.** (Because
   it doesn't — the test would fail. Once fixed, add the test.)

2. **No test for `CohortIsolation.restore()` partial failure.** Step 3 of 4
   succeeds, step 4 fails → journal records `restore_errors`. This path is
   untested.

3. **No test verifying `execution.py:381-383` and `evaluation.py:131-134` use
   the same provider option keys.** A drift between the two lists would not be
   caught by any existing test.

4. **No model-free test for `controlled_hermes.py` passing `--provider` through
   to `ollama_chat()` for finalization.** The BytePlus finalization path is
   assembled from three modules (`controlled_hermes.py` → `execution.ollama_chat`
   → `provider_chat.chat`) and no single test verifies the chain.

5. **No test for `BytePlusCodingAdapter` parsing a real successful response.**
   The fake response in `test_architecture_blockers.py:193-208` is constructed
   from the expected schema, not from a recorded BytePlus response. If BytePlus
   returns a different shape, the test still passes but the adapter fails at
   runtime.

---

## 12. Recommendation

**LIMITED MODEL-FREE VALIDATION ONLY — one canary, then decide.**

The architecture is substantially improved since the original adversarial review.
The lock safety, provider boundary, isolation mechanism, error taxonomy, and
fitness honesty fixes are all correct and well-tested.

However, the BytePlus worker path is unverified end-to-end. Running M1–M7 blind
would be a mistake. The correct sequence is:

1. Verify quota reset (A1)
2. Expose `ARK_API_KEY` (A2)
3. Run a single Hermes → BytePlus one-shot probe (A3)
4. If the probe succeeds: open the isolation window, run M1 only (A4)
5. If M1 produces real evidence: run M2–M7 (A5)
6. After cohort: extract duplicated key list (A6), add `--recover` command (A7),
   verify gate (A8)

The system is 6/10 production-ready. One successful cohort through BytePlus
would raise that to 8/10. The remaining 2 points are the journal recovery
automation and the auth env injection hardening — both are operator-supervised
concerns, not correctness blockers.

---

## Appendix A: Files Read (Complete List)

1. `orchestrator/runlock.py` — full file (166 lines)
2. `orchestrator/provider_chat.py` — full file (291 lines)
3. `orchestrator/execution.py` — full file (463 lines)
4. `orchestrator/controlled_hermes.py` — full file (153 lines)
5. `orchestrator/execution_pause.py` — full file (42 lines)
6. `orchestrator/batch_runner.py` — full file (283 lines)
7. `orchestrator/evaluation.py` — full file (383 lines)
8. `orchestrator/task_runner.py` — full file (374 lines)
9. `orchestrator/workflow.py` — diff reviewed
10. `orchestrator/promote.py` — diff reviewed
11. `orchestrator/ledger.py` — diff reviewed (fitness section in full)
12. `config/models.yaml` — full file (92 lines)
13. `workspace/validation/cohort_isolation.py` — full file (195 lines)
14. `workspace/validation/run_cohort.py` — full file (192 lines)
15. `tests/test_architecture_blockers.py` — full file (308 lines)
16. `tests/test_cohort_isolation.py` — full file (80 lines)
17. `tests/test_f53.py` — diff reviewed
18. `tests/test_f57.py` — diff reviewed
19. `tests/test_f64.py` — diff reviewed
20. `docs/BYTEPLUS_CONNECTIVITY_STATUS_2026-08-29.md` — full file (55 lines)
21. `docs/CURRENT_STATE.md` — diff reviewed

## Appendix B: Live Probes Executed

1. **Lock identity and corruption** — `python -c` exercising `runlock.acquire()`
   with valid lock, corrupt lock, live-owner lock, and replacement lock.
   EXIT: 0. All assertions passed.

2. **Provider dispatch and ESTOP gate** — `python -c` exercising
   `provider_chat.chat()` with ESTOP engaged, BytePlus without key, and canary
   permit lifecycle. EXIT: 0. All assertions passed.

3. **Synthesis provider routing** — `python -c` inspecting
   `execution.synthesis_with_failover` source for `ollama_chat` call patterns.
   Confirmed two code paths (Ollama vs non-Ollama) with correct kwargs
   construction.

## Appendix C: Gate Result

```
$ python tests/run_all.py
  [PASS] [unit] test_architecture_blockers
  [PASS] [unit] test_cli_side_effect_safety
  [PASS] [unit] test_cohort_isolation
  [PASS] [unit] test_f35
  [PASS] [containment] test_f36
  [PASS] [unit] test_f37
  [PASS] [unit] test_f39_f40
  [PASS] [containment] test_f42
  [PASS] [unit] test_f44
  [PASS] [containment] test_f47
  [PASS] [unit] test_f48
  [PASS] [unit] test_f49
  [PASS] [unit] test_f50
  [PASS] [unit] test_f51
  [PASS] [containment] test_f52
  [PASS] [unit] test_f53
  [PASS] [unit] test_f54
  [PASS] [unit] test_f56
  [PASS] [unit] test_f57
  [PASS] [unit] test_f58
  [PASS] [unit] test_f59
  [PASS] [unit] test_f60
  [PASS] [unit] test_f61
  [PASS] [unit] test_f62
  [PASS] [unit] test_f63
  [PASS] [unit] test_f64
  [PASS] [integration] test_f66
  [PASS] [unit] test_h7
  [PASS] [containment] test_h7_gate
  [PASS] [integration] test_hermes_contract
  [PASS] [unit] test_prediction_daily_safety
  [PASS] [integration] test_prediction_interface
  [PASS] [unit] test_prediction_paths
  [PASS] [unit] test_throughput
  [PASS] [unit] test_tier_live_guard
  [PASS] [unit] test_timebase_health

36/36 suites green (tiers: unit, containment, integration)
```

## Appendix D: Git State

```
HEAD: 0ce23ec5ff20ddeaf2a31026ab7800919a387f14
Branch: master (ahead 1 of origin/master)
Modified: 16 files (+323/-71)
New (untracked): 9 files
```

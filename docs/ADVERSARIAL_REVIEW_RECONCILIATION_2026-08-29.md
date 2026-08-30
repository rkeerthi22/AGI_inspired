# Adversarial review reconciliation and transport plan

Date: 2026-08-29

This document reconciles `docs/ADVERSARIAL_REVIEW_2026-08-29.md` against the
live AGI_like checkout. It does not modify the original review. Claims below are
limited to current source and preserved artifacts. Live state wins over this
document if they later diverge.

## Current safety state

- ESTOP was verified present at
  `C:\Users\moham\AppData\Local\hermes\ESTOP`.
- No cohort was started during reconciliation.
- `python tests/run_all.py` completed with **35/35 suites green** across unit,
  containment, and model-free integration tiers.
- Git HEAD was `0ce23ec5ff20ddeaf2a31026ab7800919a387f14`; the review and
  reconciliation materials remain working-tree changes unless committed later.

## Findings confirmed from the review

### 1. Tool-free inference is fixed to Ollama

- Retrieval finalization calls `ollama_chat` with a model name and fixed
  300-second timeout: `orchestrator/controlled_hermes.py:113-129`.
- `ollama_chat` always POSTs an Ollama request to
  `http://127.0.0.1:11434/api/chat`:
  `orchestrator/execution.py:97-129`.
- Synthesis selects candidates containing `provider` and `model`, but passes
  only `cfg["model"]` to `ollama_chat`:
  `orchestrator/execution.py:319-350`.
- Critic/fact-extraction paths also call `execution.ollama_chat` directly:
  `orchestrator/evaluation.py:147,252`.

Result: adding `provider: byteplus` to `config/models.yaml` cannot redirect these
paths. A provider-neutral boundary is required.

### 2. Run-lock reclamation is unsafe

- `_read_lock` catches every exception and returns `{}`:
  `orchestrator/runlock.py:28-32`.
- Missing `started_at` is immediately treated as stale:
  `orchestrator/runlock.py:35-39`.
- A stale lock is unlinked and reclaimed:
  `orchestrator/runlock.py:42-63`.

Therefore malformed or transiently unreadable lock state can permit a second
runner while the original owner may still be active.

The stale threshold is 3,600 seconds (`runlock.py:20`), equal to the local
fallback timeout (`execution.py:47-50`). Finalization, critic, and persistence
can extend valid ownership beyond the worker timeout.

### 3. Cohort isolation is not transactional

`workspace/validation/run_cohort.py` requires `--isolated` and requires ESTOP
to be clear. External scheduler disable/restore remains an operator procedure.
The runner does not atomically acquire ownership of Windows tasks, Hermes cron,
ESTOP, and process restoration.

Direct tool-free calls do not independently consult `execution_pause`.
Repository search shows `pause_engaged` is used by `controlled_hermes.py` and
`onboarding_autonomy.py`, while synthesis and evaluation call `ollama_chat`
directly.

### 4. Tasks 90-96 produced no useful retrieval evidence

For tasks 90, 91, 92, 94, 95, and 96, preserved fallback audits contain:

- `api_calls=0`;
- `executed_retrieval_calls=0`;
- zero input/output/total tokens;
- zero evidence items and characters;
- `finalization_finished success=false` with
  `reason="TimeoutError: timed out"`.

Example: `runs/task90_worker.usage_fallback2.retrieval.jsonl:1-3`.

Consequences:

- “failover succeeded” does not establish successful research.
- F63 was not exercised by this cohort and remains `UNVERIFIED` for tasks 90-96.
- The server-side reason for the timeout is `UNVERIFIED`; only the client
  exception is preserved.

### 5. Failure taxonomy and fitness need review

- M4 returned `chain_exhausted`, while its durable row was `quota_wait`:
  `workspace/validation/cohort_summary.json:218-240`.
- Its log records primary HTTP 429, a shared-quota skip, and a local context
  rejection (~9,096 required versus 4,096 declared):
  `runs/cohort_M4_20260829_033343.log:1-4`.
- `weekly_fitness` assigns `cost_eff=1.0` whenever average recorded cost is zero:
  `orchestrator/ledger.py:342-345`. Whether this is acceptable for zero-evidence
  infrastructure failures must be decided explicitly rather than treated as a
  free efficiency success.

## Review claims contradicted by live state

### Triplicated cohort rows

Contradicted. The live `cohort_summary.json` contains exactly seven mission
objects with IDs M1-M7 and task IDs 90-96. It has 499 lines and no fields named
`ledger_row`, `classification`, `fallback_used`, `critic_score`, or `fitness`.
The review's claimed 21 entries and ledger rows 30-50 are not present.

### Scheduler stubs

Contradicted. The cited `quota_exceeded`, `should_skip_mission`, and
`tokens_used_today` stubs do not exist in the current
`orchestrator/scheduler.py`. The file has 228 lines; its live implementation
contains mission parsing, queue/dedup, stale expiration, crash reconciliation,
and token accumulation.

### Source line references

The review cites `execution.py` lines 445 and 495, but the live file has 400
lines. It also names functions and result structures absent from this checkout.
Those claims cannot be treated as evidence for the live revision.

### Test-suite characterization

Contradicted in material part. The default manifest contains 35 suites, not the
13 described in the review. `tests/test_f63.py` behaviorally exercises the
controller's stages, reservation ceiling, parallel siblings, stage-3 batching,
terminal feedback rounds, evidence bounds, and accounting. The installed-Hermes
integration contract parses installed source and performs a model-free behavior
probe. Live model behavior remains unverified, but the suite is not solely grep
or attribute checks.

### Missing artifacts

Contradicted. The normal and `_fallback2` usage/audit files referenced by the
dossier exist for tasks 90, 91, 92, 94, 95, and 96.

### Continuity basis

The difference between `repository.based_on_head` and current HEAD is not itself
a defect. Schema v2 defines `based_on_head` as provenance: the commit observed
when the brief was assembled. Recovery intentionally does not require current
HEAD to remain equal. The changed-path snapshot is stale and live recovery
correctly reports that discrepancy.

### Read-only protocol discrepancy

The review says no state was modified (`ADVERSARIAL_REVIEW_2026-08-29.md:3-4`)
but later documents a write-based corrupted-lock probe at line 673. That probe
violated the requested read-only procedure. Its conclusion is independently
supported by source, but its claimed execution history should not be described
as read-only.

## Dependency-ordered transport plan

### Phase 0: checkpoint and preserve evidence

1. Commit the durable cohort runner, isolation regression, dossier, review
   prompt, original adversarial review, and this reconciliation as a scoped
   checkpoint.
2. Keep ESTOP engaged and do not run another cohort.

### Phase 1: concurrency and isolation prerequisites

1. Make unreadable/corrupt locks fail closed.
2. Replace age-only lock reclamation with owner PID/liveness plus heartbeat or a
   renewable lease.
3. Ensure the lease duration covers worker, finalization, critic, persistence,
   and retry work.
4. Add a common execution gate around every model-call path, including direct
   tool-free calls.
5. Make cohort isolation transactional and crash-recoverable across Windows Task
   Scheduler, Hermes cron, ESTOP, process ownership, and restoration.
6. Add model-free tests for malformed locks, live-owner locks, expired dead-owner
   locks, interrupted restoration, and tool-free ESTOP enforcement.

### Phase 2: truthful outcomes and metrics

1. Do not log fallback success until output validity is established.
2. Represent quota exhaustion, context incompatibility, empty output,
   finalization failure, mixed-chain exhaustion, and critic failure separately.
3. Preserve research and finalization outcomes independently.
4. Decide and test how zero-evidence infrastructure failures affect fitness;
   they must not silently receive favorable credit merely because recorded cost
   is zero.

### Phase 3: provider-neutral interface

Define a request/result contract before adding BytePlus:

```text
ChatRequest:
  provider
  endpoint/base_url reference
  model or endpoint resource ID
  prompt/messages
  timeout_seconds
  context_tokens
  response_token_reserve
  authentication reference (never the secret value)
  metadata/call purpose

ChatResult:
  text
  input_tokens
  output_tokens
  finish_reason
  provider request ID
  latency
  normalized error category
  retryable
```

Normalized errors should distinguish authentication, authorization, quota,
rate-limit, context overflow, timeout, transport failure, provider server error,
empty response, and malformed response.

### Phase 4: extract and preserve Ollama behavior

1. Implement an Ollama adapter behind the new interface.
2. Move the existing URL, request body, response parsing, and usage accounting
   into that adapter without changing behavior.
3. Add fake-server tests for dispatch, timeouts, usage, malformed responses,
   context errors, and retry classification.
4. Keep the default gate network- and model-free.

### Phase 5: BytePlus DeepSeek-V4-pro adapter

The operator supplied this base endpoint:

`https://ark.ap-southeast.bytepluses.com/api/coding/v3`

Before implementation, verify from authoritative configuration/documentation:

- exact request path beneath the base endpoint;
- authentication header and environment-variable reference;
- whether routing uses a model ID, Endpoint ID, or resource ID;
- request and response schema;
- streaming versus non-streaming behavior;
- input/output usage field names;
- context and output limits;
- quota/rate-limit/auth/server error shapes;
- request ID and retry semantics.

All of these values remain `UNVERIFIED` until inspected. Do not infer them from
the Ollama or generic OpenAI schema.

### Phase 6: migrate call paths incrementally

1. Retrieval finalization.
2. Synthesis.
3. Critic and fact extraction.
4. Promotion/manager tool-free calls.
5. Keep Hermes research-worker migration separate; it already receives
   `provider` and `model` through its own boundary.

Give finalization, synthesis, critic, and manager independent role configuration
instead of implicitly reusing the worker model where their requirements differ.

### Phase 7: progressive validation

1. Provider-dispatch and response-normalization tests with fake transports.
2. One explicitly authorized BytePlus connectivity probe.
3. One zero-evidence finalization canary.
4. One synthesis canary whose prompt exceeds 4,096 tokens.
5. One isolated retrieval mission with full audit/accounting review.
6. Only after those gates pass, consider a seven-mission cohort.

## Current recommendation

`KEEP PAUSED — architecture has unresolved prerequisites.`

The transport refactor should begin only after the concurrency/isolation and
truthful-outcome prerequisites above are regression-covered. No BytePlus code or
new cohort should be activated merely from the adversarial report's conclusions.

## 2026-08-29 model-free completion addendum

This addendum records later live checkout state; it does not rewrite the review's
historical findings.

- The provider-neutral request/result/error contract, Ollama adapter, BytePlus
  Coding Plan adapter, canonical pause gate, and process-start-identity run lock
  are implemented with model-free regressions.
- The complete default non-live gate passes 36/36 suites.
- BytePlus is declared as an available provider, not an active fallback. Its
  OpenAI-compatible base URL is
  `https://ark.ap-southeast.bytepluses.com/api/coding/v3`, authentication is an
  `env:ARK_API_KEY` reference, and `ark-code-latest` follows console model
  selection.
- One explicitly authorized connectivity canary reached BytePlus and returned
  HTTP 429. Normalization produced `rate_limit`, `retryable=true`; no automatic
  retry occurred.
- The Coding Plan quota snapshot showed the session window at 100%, resetting at
  2026-08-29 09:18:13 Europe/Warsaw. ESTOP remains the execution boundary while
  waiting for that reset. No mission or cohort is authorized by this addendum.

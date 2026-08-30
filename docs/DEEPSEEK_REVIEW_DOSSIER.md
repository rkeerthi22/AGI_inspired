# DeepSeek-V4-pro architectural review dossier

Evidence cut: 2026-08-29. Claims below are limited to the cited repository
source or preserved run artifacts. `UNVERIFIED` means the available evidence
does not establish the claim.

## 1. Exact execution lifecycle

1. `batch_runner.py` acquires the exclusive `runs/.batch.lock`; the lock uses
   atomic `O_CREAT|O_EXCL`, contains PID/start time, treats an unreadable lock as
   stale, and unlinks it in `finally` (`orchestrator/runlock.py:20-21,35-39,42-70`).
2. `task_runner.run_task` routes synthesis at
   `orchestrator/task_runner.py:99-111`. Research reaches
   `execution.worker_with_failover` at `orchestrator/task_runner.py:225-232`.
3. A research worker launches the installed Hermes virtual-environment Python
   with `controlled_hermes.py`, passing `provider`, `model`, and `usage-file`
   (`orchestrator/execution.py:71-90`).
4. The installed-Hermes contract checks batch begin/end, halt propagation,
   conversation termination, audit JSONL, and one-call finalization
   (`orchestrator/hermes_contract.py:158-196`).
5. Retrieval stages are search, direct fetch, browser, then partial result
   (`orchestrator/retrieval_progress.py:38-56,114-126`). A tool batch can consume
   at most one redirect violation (`orchestrator/retrieval_progress.py:128-146`).
6. After Hermes returns, `controlled_hermes.py` records research usage, permits
   one finalization call, and emits a bounded failure on exception
   (`orchestrator/controlled_hermes.py:100-140`).
7. `task_runner.py` persists raw output before classifying exhausted, API-failed,
   or short output (`orchestrator/task_runner.py:239-273`). Only surviving output
   continues to critic/fact/deliverable processing.

## 2. Configuration schemas and BytePlus mapping boundary

### Shared model-routing schema

`config/models.yaml:4-81` defines:

```yaml
roles:
  manager|critic|worker|fallback:
    provider: string          # required by call sites
    model: string             # required by call sites
    quota_group: string       # optional shared-quota identity
    context_tokens: integer   # optional declared input capacity
    max_calls_per_day: integer # role-specific optional policy value
fallback_chain:
  - provider: string
    model: string
    quota_group: string       # optional; absent means independent pool
    context_tokens: integer   # optional; absent means never pre-skipped
```

Context admission estimates `len(prompt)//4 + 1500` and skips only models that
declare an insufficient `context_tokens` value
(`orchestrator/execution.py:207-245`). Fallback candidates deduplicate on
`(provider, model)` and skip a known-dead `quota_group`
(`orchestrator/execution.py:248-269,329-340`).

### Retrieval-finalization schema

Input is the original mission plus the controller's JSON evidence list; the
prompt contract requires a sourced brief or explicit bounded failure
(`orchestrator/retrieval_progress.py:334-350`). Audit output is JSONL with base
fields `sequence,event,required_strategy,executed_calls`; finalization events add
`evidence_items,evidence_chars` and then
`success,input_tokens,output_tokens,reason`
(`orchestrator/hermes_contract.py:18-34`). Exactly one finalization attempt is
enforced (`orchestrator/retrieval_progress.py:310-332`).

The transport is **not provider-generic**. `controlled_hermes.py` passes only the
model name into `ollama_chat` with a fixed 300-second timeout
(`orchestrator/controlled_hermes.py:113-129`), and `ollama_chat` always POSTs an
Ollama-shaped body to `http://127.0.0.1:11434/api/chat`
(`orchestrator/execution.py:97-129`). Therefore adding
`provider: byteplus` to YAML alone cannot route finalization to BytePlus.

### Synthesis schema

Synthesis uses the same `roles.worker` plus `fallback_chain` records. Each
candidate is subject to `quota_group` and `context_tokens`; non-local timeout is
600 seconds and local timeout is 3600 seconds. The call passes only
`cfg["model"]` to `ollama_chat` (`orchestrator/execution.py:319-350`). Therefore
synthesis is also hardwired to the local Ollama HTTP transport even when a YAML
candidate's `provider` says otherwise.

Required adapter work for BytePlus is consequently a provider-aware tool-free
chat boundary accepting at least `{provider, model, prompt, timeout}` and
normalizing response text and input/output token counts. The concrete BytePlus
Endpoint ID, authentication source, base URL, SDK/API response fields, and
DeepSeek-V4-pro model identifier are **UNVERIFIED**: none appears in tracked
AGI_like configuration as of this evidence cut.

## 3. Cohort tasks 90-96: preserved facts

The summary says isolation was acknowledged, ESTOP was clear, and run marker was
`43bb95e6bffd` (`workspace/validation/cohort_summary.json:2-5`). Its SHA-256 is
`2841DE64ECCBC2F5C95537B522EE67FEA191FD0CAD2978739D5592AF9C207A80`.

| Mission/task | Summary outcome | Exact trace |
|---|---|---|
| M1/90 | `infra_failed`, 327.9 s (`cohort_summary.json:8-30`) | primary 429; shared pool skipped; local rung labeled success; finalization `TimeoutError: timed out` (`runs/cohort_M1_20260829_031727.log:2-8`) |
| M2/91 | `infra_failed`, 323.6 s (`cohort_summary.json:78-100`) | same sequence (`runs/cohort_M2_20260829_032255.log:2-8`) |
| M3/92 | `infra_failed`, 325.1 s (`cohort_summary.json:148-170`) | same sequence (`runs/cohort_M3_20260829_032818.log:2-8`) |
| M4/93 | runner `chain_exhausted`; DB row `quota_wait` (`cohort_summary.json:218-240`) | primary HTTP 429; shared pool skipped; local prompt estimated ~9,096 tokens including 1,500 reserve versus declared 4,096 (`runs/cohort_M4_20260829_033343.log:1-4`) |
| M5/94 | `infra_failed`, 325.8 s (`cohort_summary.json:288-310`) | same finalization sequence (`runs/cohort_M5_20260829_033344.log:2-8`) |
| M6/95 | `infra_failed`, 330.2 s (`cohort_summary.json:358-380`) | same finalization sequence (`runs/cohort_M6_20260829_033910.log:2-8`) |
| M7/96 | `infra_failed`, 333.3 s (`cohort_summary.json:428-450`) | same finalization sequence (`runs/cohort_M7_20260829_034440.log:2-8`) |

The retrieval audits materially qualify the human-readable "failover succeeded"
log label. For every local fallback (tasks 90, 91, 92, 94, 95, 96), the audit has
`api_calls=0`, `executed_retrieval_calls=0`, zero tokens, zero evidence items and
characters, followed by `finalization_finished success=false` with reason
`TimeoutError: timed out` (for example
`runs/task90_worker.usage_fallback2.retrieval.jsonl:1-3`; the other five files
have the same three-record shape). Thus a successful research/worker result is
**not established**. The exact point inside the Ollama server that caused the
timeout is **UNVERIFIED**; the artifact establishes only the client exception and
the configured 300-second finalization timeout.

No critic verdict exists for these tasks (`critic_verdict` is null in their
summary rows). F63 compliance for this cohort is **UNVERIFIED**: there were no
executed retrieval calls or redirects in the preserved local-fallback audits, so
this run did not exercise the F63 transition behavior.

## 4. State-machine and containment edge cases

- **Lock expiry shorter than a valid fallback:** run lock staleness is 3,600 s
  (`runlock.py:20-21`), exactly equal to local fallback timeout
  (`execution.py:47-50`). Additional finalization/critic time can make a healthy
  owner look stale. Challenge whether PID liveness/heartbeat must replace age.
- **Unreadable lock is reclaimed:** malformed lock content returns `{}` and is
  immediately considered stale (`runlock.py:28-39`). Challenge whether corruption
  should fail closed instead of permitting a second writer.
- **False failover success:** `synthesis_with_failover` calls any non-HTTP-error
  return success (`execution.py:342-350`); research failover similarly returns on
  non-quota output, leaving later `worker_failed` to reclassify it
  (`task_runner.py:247-269`). The cohort logs demonstrate the misleading label.
- **Provider field ignored on tool-free paths:** synthesis and finalization select
  routing metadata but invoke a fixed Ollama transport, as documented above.
- **F63 batch boundary:** `begin_tool_batch` resets the one-violation flag and
  `end_tool_batch` clears it (`retrieval_progress.py:128-146`). Installed Hermes
  bracketing is checked syntactically plus a model-free behavior probe
  (`hermes_contract.py:119-153,169-190`), but live F63 behavior was not exercised
  in tasks 90-96.
- **Cohort isolation contract:** the durable runner requires `--isolated`, refuses
  ESTOP-engaged execution, uses a unique run marker, and does not delete prior task
  rows (`workspace/validation/run_cohort.py`; enforced by
  `tests/test_cohort_isolation.py`). External scheduler disable/restore remains an
  operator procedure rather than an atomic runner-owned transaction.

## 5. Adversarial questions for DeepSeek-V4-pro

1. Design the smallest provider-neutral inference interface that prevents
   `provider` metadata from disagreeing with actual transport, while preserving
   usage accounting and independently configurable worker/finalizer/synthesis
   policies.
2. Can the run lock admit concurrent writers when a valid 3,600-second local call
   plus finalization exceeds `STALE_AFTER_SECONDS`, or when the lock is transiently
   unreadable? Propose a crash-safe lease/heartbeat protocol and tests.
3. Should a zero-call, zero-evidence Hermes turn ever be labeled a successful
   fallback? Identify the earliest authoritative classification point and how to
   distinguish provider quota, empty conversation, finalizer failure, and useful
   bounded failure without double-counting inference.
4. Does the F63 state machine terminate at the correct semantic unit under every
   combination of parallel pre-call blocks, setup calls, partial-result stage,
   exceptions, and conversation-loop continuation? Produce counterexamples beyond
   the existing four-sibling regression.
5. How should cohort isolation become transactional across Windows Task Scheduler,
   Hermes cron, ESTOP, process startup, crash recovery, and restoration? Challenge
   the current manual `--isolated` assertion and specify a fail-closed design that
   cannot strand schedules disabled or execute alongside an unrelated dispatcher.

## 6. Verified live-state snapshot

At the evidence cut, elevated process inspection found no `run_cohort.py`,
`batch_runner.py`, `task_runner.py`, or `controlled_hermes.py` process; only the
interactive terminal and the inspection command matched `AGI_like`. All five AGI
Windows tasks were `Ready` with last result `0`. ESTOP existed at
`C:\Users\moham\AppData\Local\hermes\ESTOP` (77 bytes; UTC mtime
2026-08-29 01:50:27), and no run-lock entry was returned. Hermes cron listing
showed all three jobs active after restoration. These are point-in-time facts, not
guarantees about later state.

The pre-change default gate produced `34/34 suites green`. After adding the
durable cohort-isolation regression, `python tests/run_all.py` produced `35/35
suites green` across unit, containment, and integration tiers on 2026-08-29.

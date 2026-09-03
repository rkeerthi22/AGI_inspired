# Canonical Project State - AGI_like Harness

**Last Updated:** 2026-09-03T02:17:24Z
**Phase:** Immediate cohort actions complete through M7; post-cohort backlog open
**Safety Status:** ESTOP engaged (`True`) | Zero live execution active
**Live Verification:** `python -B tests/run_all.py` -> `55/55` green | `python orchestrator/continuity.py recover` valid | `python -B orchestrator/operator_cli.py status --json` clean | supervised BytePlus canary succeeded on `2026-09-03T01:53:09Z`

---

## 1. Executive Summary

Thursday, September 3, 2026 closed the immediate live action chain that was
still open on September 2.

What is now true in live state:

| Scope | Status | Evidence |
| :--- | :---: | :--- |
| Task 110 recovery | VERIFIED LIVE | Supported recovery already completed on 2026-09-02; row is no longer stranded in `running` |
| Hermes provider-id repair | VERIFIED | `14dbafe` changed Anthropic to native `anthropic`, OpenAI to `openai-api`, and aligned finalizer mapping |
| Unavailable-rung failover hardening | VERIFIED | `5522926` teaches both research and synthesis failover loops to continue past missing optional provider credentials or unsupported provider rungs |
| Full model-free gate | VERIFIED | `55/55` suites green after both repairs |
| Supervised BytePlus canary | VERIFIED LIVE | `2026-09-03T01:53:09Z`, `ok=true`, provider `byteplus_coding`, model `ark-code-latest`, request id `02178840037366712014becacfaf8a37949eaec3c813975305d82` |
| M3 / task 114 | FAILED | Real frozen-spec fail; deliverable did not explicitly account for all required blocked review platforms and attempts |
| M4 / task 115 | PASSED | Clean synthesis pass |
| M5 / task 116 | FAILED | Mechanical citecheck fail: `4/8` cited URLs unreachable |
| M6 / task 117 | INFRA_FAILED | First attempt exposed the early-stop failover bug after BytePlus quota exhaustion and missing Anthropic credentials |
| M6 rerun / task 118 | FAILED | After the failover fix, the same mission completed to a normal graded failure instead of dying in execution |
| M7 / task 119 | FAILED | Real frozen-spec fail; missing explicit `not publicly disclosed` cells, weak source coverage, and a false FlowGPT availability claim |

The immediate backlog was completed honestly: the canary ran, the windows ran,
and the first newly exposed blocker was fixed before the sequence continued.

---

## 2. What Was Corrected

Two live-path assumptions from the September 2 state were wrong or incomplete:

1. Task 110 was not first blocked by an Anthropic output-shape defect. The
   immediate cause was stale Hermes-facing provider selectors
   (`custom:anthropic`, `custom:openai`), now repaired in `14dbafe`.
2. Once the real Anthropic rung was reached, the next failure was not malformed
   model output. It was missing Anthropic credentials, and the failover loop
   aborted too early instead of continuing to later rungs. That is now repaired
   in `5522926`.

Both fixes are regression-covered and were re-verified by the full `55/55`
green gate before more live cohort work was spent.

---

## 3. Current Safety And Runtime Invariants

* ESTOP remains engaged between controlled windows.
* No live runlock is present after the cohort windows.
* Isolation restored cleanly after each controlled window.
* Rows 111-113 remain untouched legitimate queued seeds.
* Live repository, process, and operator status outrank historical documents.

Current operator status on `2026-09-03T02:15:44Z`:

* git clean, `ahead=2`, `behind=0`
* continuity revision `54`, valid, no discrepancies
* ESTOP engaged, no canary marker present
* runlock absent
* Munder quiesced

Recorded subsystem warnings still visible through `agi status` are not active
live probes. They are historical health events that still need triage:

* `prediction`: `No module named 'prediction_machine'`
* `mailbus`: `[WinError 32] Sharing violation`
* `hive_quiesce`: `AGI tree dirtied during window`

The repeated runtime warning about `.claude/settings.local.json` being masked by
an unversioned exclude source also remains unresolved.

---

## 4. Remaining Gaps

Control-plane gaps:

* protected-path masking warning still fires during cohort windows
* recorded subsystem health events need post-cohort triage and either repair or
  suppression if they are expected
* provider capacity is still externally constrained; BytePlus and Ollama cloud
  quota exhaustion remain real operating conditions
* Anthropic and OpenAI credentials are not currently usable in this environment

Enterprise gaps:

* no centralized tamper-evident audit retention layer yet
* no measured SLO / alert pipeline yet
* no stronger service-identity or engine-independent egress sandbox yet
* no long-run reliability window, restore-drill evidence, or external review yet

Product-quality gap:

* of the remaining frozen windows opened on September 3, only `M4` passed

This remains a strong enterprise-candidate control prototype, not an
enterprise-finished product.

---

## 5. Next Exact Action

1. If another live validation step is authorized, choose explicitly between:
   task `110` retry, or targeted revisits of failed windows `M3`, `M5`, `M6`,
   and `M7`.
2. Do not spend another live attempt without acknowledging provider reality:
   BytePlus quota can exhaust, Anthropic/OpenAI credentials are currently
   absent, and the local gemma rung may be the only remaining completion path.
3. Start the post-cohort backlog in order: protected-path warning, preflight /
   health-warning triage, spec-lint / crying-wolf cleanup, hermeticity audit,
   then the P1 security stack.

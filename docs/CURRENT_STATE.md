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

Recorded subsystem warnings visible through `agi status` were diagnosed 2026-09-03
as pre-F108 *test artifacts*, not active live probes: unit-tier tests wrote health
events to the production `runs/health_events.jsonl`, and `agi status` (newest event
per subsystem) replayed them. F108 routes test health events to a pid-scoped temp via
`_guarded_env`, so new test runs no longer pollute the production log and `agi status`
no longer cries wolf. The residual events already in the log (pre-F108) are stale test
artifacts, not live warnings; a one-time operator cleanup (back up + truncate) is
optional — the log is gitignored and overwritten in use.

The repeated runtime warning about `.claude/settings.local.json` being masked by an
unversioned exclude source is RESOLVED (F107, 2026-09-03): listed in the versioned
`.gitignore` so it drops out of the F47 masking set. `MASKED=[]` verified in the real
repo.

---

## 4. Remaining Gaps

Control-plane gaps:

* ~~protected-path masking warning still fires during cohort windows~~ RESOLVED (F107,
  2026-09-03): `.claude/settings.local.json` moved to the versioned `.gitignore`;
  `MASKED=[]` verified
* ~~recorded subsystem health events need post-cohort triage~~ RESOLVED (F108,
  2026-09-03): test health events now route to a pid-scoped temp; `agi status` no
  longer replays test artifacts. Residual pre-F108 log events are stale (operator
  one-time cleanup, optional)
* per-task critic artifacts (`task{N}_critic.usage.json` / `_citation_evidence.json`)
  no longer leak from test_f57 into production `runs/` (F109, 2026-09-03): test_f57
  section 3 redirects `ev.RUNS`/`rc.RUNS` to temp; pinned by `test_f109`
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
3. Post-cohort backlog status (2026-09-03): items 1–4 DONE + committed (`4f773e6`) —
   (1) protected-path warning (F107), (2) preflight/health-warning triage (F105 cohort
   entry + F108 test pollution), (3) spec-lint/crying-wolf cleanup (F108 health events
   + F109 runs/ artifacts; "spec-lint" proper has no existing code, remains an open
   proposal), (4) hermeticity audit (F108 + F109 — test runs no longer pollute
   production `runs/`). Item 5, the P1 security stack, remains: it is a multi-session
   enterprise-readiness roadmap (`ENTERPRISE_READINESS_2026-08-31.md` section P1)
   whose build items — vault-backed secrets, Job Object containment + egress policy,
   versioned Munder, locked deps/CI, audit retention, independent critic routing —
   each require operator architectural decisions before execution. The most
   decision-free piece is elevating the existing `agi preflight canary` diagnostic into
   the "one authoritative model-free preflight" (ESTOP/schema/deps/path checks); even
   that is a design choice.

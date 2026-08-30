# Canonical Project State — AGI_like Harness

**Last Updated:** 2026-08-30T18:45:00Z  
**Phase:** P0 Trajectory Complete / Awaiting Quota Reset for M1 Controlled Canary  
**Safety Status:** ESTOP Engaged (`True`) | Dispatchers Protected | Zero Live Execution Active  
**Test Status:** 41/41 Model-Free Suites Green (`tests/run_all.py`)  

---

## 1. Executive Summary & Verified Milestones

| Milestone / Component | Status | Empirical Evidence |
| :--- | :---: | :--- |
| **Architecture Blockers F-01, F-02, F-03 (A1, A2, A3)** | **VERIFIED** | `execution.py` (stderr quota detection), `ledger.py` (`LEASE_SECONDS = 4200`), `evaluation.py` (idempotent fact extraction); verified across 41/41 test suites. |
| **BytePlus ModelArk Integration (A5)** | **VERIFIED** | Typed transport via `orchestrator/provider_chat.py`; passed initial connectivity canary (HTTP 200, 2.068s latency, request ID validated). |
| **M1 Canary Run (Task 104)** | **EXECUTED** | 243.2s duration, 49.6k in / 11.2k out tokens, 0% dead URLs on mechanical citecheck. Failed critic strictly due to over-constrained non-public data criteria. |
| **M1 Pass Criteria Harmonization** | **APPROVED** | Added explicit bounded-unavailability declaration rule to `cohort_missions.json`, aligning M1 with M3, M4, M5, and M7 standards. |
| **Failover Chain Verification (Task 105)** | **VERIFIED** | Correctly caught 429 quota exhaustion across BytePlus and Ollama Cloud rungs, safely returning `infra_failed` without harness crash. Transactional window cleanly restored. |
| **P0 Unified Trajectory Event Stream** | **COMPLETE** | Implemented `orchestrator/trajectory.py` (`runs/task{id}.trajectory.jsonl`), integrated across all 4 pipeline stages, and verified with 25 unit assertions (`tests/test_trajectory_event_stream.py`). |

---

## 2. Active Blockers & Provider Quota State

* **Primary Blocker:** Upstream BytePlus ModelArk 5-hour rolling session quota is exhausted (`HTTP 429 AccountQuotaExceeded`).
* **Expected Reset Time:** **`2026-08-31 04:21:32 +0800 CST`** (~**`22:22 CEST`**).
* **Gate Invariant:** No controlled isolation window or live mission (M1–M7) may be opened until the single-probe canary (`byteplus_connectivity_canary.py`) returns HTTP 200.

---

## 3. Canonical Architecture & Control Plane

* **Global Emergency Stop (ESTOP):** Managed by `orchestrator/execution_pause.py`. When engaged, all CLI commands and model dispatches fail closed (Exit Code 75).
* **Single-Instance Runlock:** Managed by `orchestrator/runlock.py` ensuring zero database write collisions.
* **Transactional Isolation:** Managed by `workspace/validation/cohort_isolation.py`. Guarantees restoration of ESTOP and production dispatchers in a `finally` block upon completion or crash.
* **Database Containment:** Guarded by `integrity.DatabaseMutationGuard`, preventing direct database manipulation by worker processes.

---

## 4. Multi-Agent Coordination & Ownership

* **Active Registry:** Tracked in [`docs/ACTIVE_WORK.json`](ACTIVE_WORK.json).
* **Write Scope Rule:** Exactly one agent may hold a write lock on a given subsystem scope in the main working tree.
* **Roles:**
  - **Gemini CLI:** Independent Principal Architect / Reviewer (Read-Only audit mode).
  - **DeepSeek-V4-pro / Cade:** Core Runtime & Infrastructure Implementer.
  - **Claude / Codex / Hermes:** Specialist Task Workers.
  - **Operator:** Strategic director and execution gate authorizer.

---

## 5. Next Exact Action

1. **At Quota Reset (~22:22 CEST):**
   ```powershell
   $env:ARK_API_KEY = (Get-Content "$env:LOCALAPPDATA\hermes\.env" | Select-String "^ARK_API_KEY=").ToString().Split("=", 2)[1].Trim(); python workspace/validation/byteplus_connectivity_canary.py --authorize-single-estop-bypass
   ```
2. **If HTTP 200 Confirmed:** Execute exactly ONE controlled M1 rerun:
   ```powershell
   python workspace/validation/run_cohort.py --controlled-window --only M1
   ```
3. **Verify:** Confirm that `runs/task{id}.trajectory.jsonl` records the full event stream and the deliverable achieves an authentic critic `PASS`.
4. **Advance:** Proceed with the validation cohort (M2–M7).

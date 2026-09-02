# Canonical Project State — AGI_like Harness

**Last Updated:** 2026-09-02T10:00:00Z
**Phase:** Operator CLI & Boundary Hardening — Independent Review PASSED; Awaiting Operator Authorization
**Safety Status:** ESTOP Engaged (`True`) | Dispatchers Protected | Zero Live Execution Active
**Test Status:** 109/109 targeted Operator CLI assertions and 46/46 model-free suites green
**Repository Status:** Pushed to origin/master; working tree clean

---

## 1. Executive Summary & Verified Milestones

| Milestone / Component | Status | Empirical Evidence |
| :--- | :---: | :--- |
| **Architecture Blockers F-01, F-02, F-03 (A1, A2, A3)** | **VERIFIED** | `execution.py` (stderr quota detection), `ledger.py` (`LEASE_SECONDS = 4200`), `evaluation.py` (idempotent fact extraction); verified across 46/46 test suites. |
| **BytePlus ModelArk Integration (A5)** | **VERIFIED** | Typed transport via `orchestrator/provider_chat.py`; passed initial connectivity canary (HTTP 200, 2.068s latency, request ID validated). Historical — not re-probed since. |
| **M1 Canary Run (Task 104)** | **EXECUTED** | 243.2s duration, 49.6k in / 11.2k out tokens, 0% dead URLs on mechanical citecheck. Failed critic strictly due to over-constrained non-public data criteria. |
| **M1 Pass Criteria Harmonization** | **APPROVED** | Added explicit bounded-unavailability declaration rule to `cohort_missions.json`, aligning M1 with M3, M4, M5, and M7 standards. |
| **Failover Chain Verification (Task 105)** | **VERIFIED** | Correctly caught 429 quota exhaustion across BytePlus and Ollama Cloud rungs, safely returning `infra_failed` without harness crash. Transactional window cleanly restored. |
| **P0 Unified Trajectory Event Stream** | **COMPLETE** | Reopen/appends resume from the highest valid sequence; malformed trailing JSON is preserved and safely separated; tests use isolated temporary run paths. |
| **Dual-Orchestrator State Isolation** | **RESOLVED** | Munder's state home moved to `S:\MunderState\AGI_like`; hive, roster, backups, Palace state, and worktrees no longer pollute AGI continuity. |
| **Phase 1 Mailbus** | **COMPLETE** | `orchestrator/mailbus.py` (786 lines) + `tests/test_mailbus.py` (21 tests), committed at `439cedd`. Dispatch-loop wiring (Phase 3) deliberately NOT started. |
| **Munder/AGI Boundary Hardening (4 gaps)** | **COMPLETE** | Base commit `920f59d`; final repair `fde1585` adds separator-agnostic repo linkage, fail-closed fleet freshness/ambiguity handling, engine-independent process proof, and a pre-provider canary quiescence gate. Independently rerun: hive quiescence 63/63, ESTOP/canary 22/22, Munder authority 27/27, cohort isolation 27/27, full gate 45/45. No provider call or canary authorization occurred. |
| **Unified Operator CLI (P1)** | **COMPLETE — REVIEW PASSED** | Hermes implemented the three read-only commands. Codex fixed the one confirmed continuity fallback defect: failed live recovery now returns UNKNOWN (`null`) and blocks canary preflight even when the cached brief parses. Verified by 109/109 targeted assertions and 46/46 model-free suites. **Independent review (Claude Code, 2026-09-02): PASS.** |

---

### Enterprise Readiness

The canonical enterprise-readiness assessment is
`docs/ENTERPRISE_READINESS_2026-08-31.md`: **PRE-ENTERPRISE, 2.9/5**.
The control-plane design is estimated at 65-75% toward enterprise candidate;
whole-system readiness is estimated at 50-60%. The assessment is a planning
record only and does not authorize a provider call, canary, M1-M7, ESTOP
transition, or Phase 2/3 work.

---

## 2. Active Blockers & Review State

* **Primary Blocker:** ~~Independent DeepSeek/Gemini review of the frozen Operator CLI checkpoint and the previously pending `fde1585` boundary-hardening review.~~ **Independent review completed and passed (Claude Code, 2026-09-02).** The next gate is operator authorization for the single supervised canary.
* **Current Gate:** Operator authorization → single BytePlus canary → M1-M7 under locked sequence. No canary or mission authorization has been issued.
* **Provider State:** Not probed; historical BytePlus observation remains historical until a separately authorized canary.
* **Gate Invariant:** No controlled isolation window or live mission (M1–M7) may open until (a) the boundary hardening passes independent review, (b) the single-probe canary returns HTTP 200, and (c) the operator authorizes.
* **Munder State:** Externally isolated at `S:\MunderState\AGI_like` (not a git repo — file delivery). With ownership enforcement live, no hive agent is registered with AGI write scopes: all hive Edit/Write into `S:\AGI_like` is denied until the operator registers scopes for a tasked agent. The canary admission path additionally requires a clean engine-independent development-process inventory and fails closed on stale, missing-without-proof, or ambiguous fleet state.

---

## 3. Canonical Architecture & Control Plane

* **Global Emergency Stop (ESTOP):** `orchestrator/execution_pause.py`. Engaged → all CLI commands and model dispatches fail closed (exit 75). **New:** tamper detection — an absent sentinel without a fresh operator transition marker or active isolation-window record is re-engaged at the next harness entry point (`verify_pause_integrity()`); canary bypass additionally requires a single-use 30-minute operator marker file.
* **Unified Operator CLI:** `orchestrator/operator_cli.py` + `agi.ps1`. Read-only composition of existing authoritative readers (continuity, execution_pause, runlock, cohort_hive_quiesce, backup). Never a second safety authority; never authorizes; never probes providers. The CLI intentionally does NOT call `verify_pause_integrity()` — it classifies sentinel absence without re-engaging. See `docs/OPERATOR_CLI.md`.
* **Single-Instance Runlock:** `orchestrator/runlock.py` — zero database write collisions.
* **Transactional Isolation:** `workspace/validation/cohort_isolation.py`. **New:** window open additionally requires Munder hive quiescence (every hive agent capable of mutating the AGI tree inactive; fail-closed on unreadable roster); restore verifies tree untainted; journal state v2 carries the hive record.
* **Hive Write Boundary:** `S:\MunderState\AGI_like\hive\bin\enforce.js` behind the shared PreToolUse hook — denies live-control command patterns, ESTOP/marker manipulation, and AGI-repo writes without matching `docs/ACTIVE_WORK.json` ownership; fails closed; audits without logging message bodies.
* **Database Containment:** `integrity.DatabaseMutationGuard` prevents direct database manipulation by worker processes.

---

## 4. Multi-Agent Coordination & Ownership

* **Active Registry:** `docs/ACTIVE_WORK.json`. Write scope is now technically enforced at the hive hook boundary, not advisory.
* **Write Scope Rule:** Exactly one agent may hold a write lock on a given subsystem scope; unregistered/conflicting hive writes to `S:\AGI_like` are denied and audited.
* **Roles:**
  - **Operator:** Strategic director; sole authorizer of canary, windows, missions, and doc sign-off.
  - **Gemini CLI:** Independent Principal Architect / Reviewer (read-only).
  - **DeepSeek/Cade:** Core Runtime Implementer — temporarily unavailable.
  - **Hermes:** Completed the Unified Operator CLI implementation; Ollama/GLM HTTP 429 interrupted commit/bookkeeping.
  - **Codex:** Completed only the operator-authorized continuity truthfulness repair and checkpoint closeout; no paths remain owned.
  - **Claude / Munder hive agents:** Task workers under the deny/ownership boundary.

---

## 5. Next Exact Action

1. ~~Static review~~ ✅ **DONE — PASSED (Claude Code, 2026-09-02).** Operator CLI and Boundary Hardening independently reviewed; all safety claims substantiated.
2. **Human protection:** the operator protects the reviewed checkpoint off-machine, then quiesces all development agents.
3. **Model-free preflight:** run the read-only Operator CLI preflight (`agi preflight canary`); it authorizes nothing.
4. **Only after separate operator authorization:** proceed with exactly one supervised BytePlus canary, followed by M1-M7 only under the already locked sequence.

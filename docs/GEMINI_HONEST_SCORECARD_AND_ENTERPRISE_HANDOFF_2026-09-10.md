# Gemini Honest Scorecard & Enterprise Handoff

**Document ID:** `HANDOFF-GEMINI-HONEST-SCORECARD-2026-09-10`  
**Date:** 2026-09-10  
**Author:** Gemini CLI (Independent Principal Architect)  
**Recipients:** Claude Code, Codex Astra, System Operator  
**Status:** Canonical Handoff & Enterprise Assurance Audit Dossier  
**Continuous Integration Baseline:** 77/77 Test Suites Green

---

## 1. Honest Empirical Cohort Scorecard (3/17 Yield)

### 1.1 Live Cohort Ground Truth
During the live cohort evaluation on live network traffic (Tasks 153 through 169), the empirical results are as follows:

| Metric | Empirical Count | Notes |
| :--- | :---: | :--- |
| **Total Tasks Attempted** | **17** | Tasks 153 through 169 |
| **Genuine Research Passes** | **3** | Tasks 161, 163, 165 passed independent critic under Option B+ |
| **Mechanical Fabrications Blocked** | **1** | Task 169 worker attempted citation fabrication; Option B+ caught and escalated |
| **Schema Preflight Rejections** | **1** | Task 168 deliverable preflight detected mandatory table/disclaimer omission |
| **Quota Cascade Failures (HTTP 429)** | **12** | Tasks 153–158 and retries hit upstream BytePlus Ark rate limits / concurrency caps |
| **Honest Cohort Yield** | **3/17 (17.6%)** | **NOT a clean sweep.** |

### 1.2 What Actually Succeeded
* **Option B+ Validation:** Option B+ is no longer purely model-free theory. On live network traffic, Option B+ successfully caught worker citation fabrications, refused unverified policy relief, and graded 3 genuine research tasks with empirical verification against live endpoints.
* **Mechanical Fabrication Guard:** The mechanical citecheck and fabrication detector prevented fabricated claims from reaching the final deliverable.

### 1.3 What Failed
* **Task 169:** The worker fabricated a quotation attributing it to a blocked URL; Option B+ correctly identified the fabrication and triggered an integrity escalation.
* **Task 168:** The worker omitted a required disclaimer cell ("not publicly disclosed"); deliverable preflight schema linting blocked it.
* **Tasks 153–158:** Upstream provider quota exhaustion (BytePlus HTTP 429) caused cascade failures when multiple concurrent requests were submitted.

---

## 2. Status of the Three Enterprise Deficits

### Deficit A: Host Containment & Identity Separation (Path A)
* **Status:** **PACKAGED & VERIFIED (Operator Execution Remains)**
* **Deliverables:**
  - `scripts/deploy_three_identity.ps1` (implements `Plan`, `ProvisionAccounts`, `ConfigureAcls`, `ConfigureFirewall`, `InstallSignerService`, `Verify`, `Remove`).
  - `orchestrator/audit_signer_scm.py` (proper pywin32 `ServiceFramework` wrapper for Windows SCM lifecycle management).
  - `docs/RUNBOOK_PATH_A_THREE_IDENTITY.md` (complete operator runbook with step-by-step elevated commands and reversibility/ACL-cleanup procedures).
  - `docs/THREE_IDENTITY_DEPLOYMENT_GUIDE_2026-09-08.md` (architectural reference with Section 8 explaining `--no-sandbox` trade-off).
* **Isolation Framing:**
  - Path A resolves Mission M2 (Chromium exit code 21) within the single-kernel paradigm by providing a native user profile under `workspace/worker_home/`.
  - Removes `--no-sandbox` workaround and restores Chrome's native renderer sandbox.
  - **Ceiling Discipline:** Path A does **NOT** advance past the shared-kernel ceiling. The worker, controller, and audit signing daemon share a single Windows NT kernel. Hardware-enforced isolation (containers/VMs) is required to break this ceiling.

### Deficit B: Off-Machine Audit Immutability
* **Status:** **WIRED & TESTED FAIL-CLOSED (Operator Share Provisioning Remains)**
* **Deliverables:**
  - `orchestrator/audit_replication.py`: When `HARNESS_AUDIT_ENFORCE=1` is set, replication write failures (unreachable UNC, WORM reject) **FAIL HARD** via `AuditReplicationError` and never silently fall back to local-only.
  - **D4 Suffix Truncation Detection:** Implemented signed latest-checkpoint manifest (`latest-checkpoint.json`). If an attacker truncates the checkpoint chain suffix, `audit_state` detects the mismatch and returns `ok=False` (`checkpoint_suffix_truncated`).
  - **D4 Retention Floor Check:** `retention_floor_check` validates that the chain span meets `minimum_retention_days`.
  - **D4 Honesty:** `audit_state` diagnostic explicitly reports `"immutability_guarantee": "detection_only_storage_worm_required"`. The software detects tampering; physical immutability requires operator-provisioned WORM storage.
* **Operator Step:** Provision the UNC WORM share and configure `HARNESS_AUDIT_REPLICA_ROOT` and `HARNESS_AUDIT_ENFORCE=1`.

### Deficit C: Quota Elasticity & Provider Redundancy
* **Status:** **SCOPED & LOCKED (Operator Provider Decision Remains)**
* **Deliverables:**
  - `docs/QUOTA_ELASTICITY_SCOPING_2026-09-10.md` (scoping note documenting BytePlus 429 bottleneck, clarifying provider-strategy attribution, and specifying the 4 locked OmniRoute conditions).
* **OmniRoute Status:** **STRICTLY HELD and LOCKED**. Zero routing code changes made.
* **Recommended Path:** Implement a simple cold secondary provider (e.g. DeepSeek official direct API or Azure OpenAI) in `orchestrator/provider_chat.py` with deterministic failover upon 429 exhaustion.
* **Operator Step:** Provision an independent secondary provider API key in the Windows Credential Manager.

---

## 3. Codex Astra Audit Fixes (Task D) Summary

| Issue | Description | Fix Location | Regression Coverage |
| :--- | :--- | :--- | :--- |
| **D0** | Restore 76→77 gate suites | `tests/test_deliverable_preflight.py` | IndentationError fixed under tmp context |
| **D6** | Gate exits non-zero on any fail | `tests/run_all.py` | Explicit `sys.exit(1)` upon `failed` suite list |
| **D1** | Attestation integrity in firewall | `scripts/enforce_worker_firewall.ps1` | Probes verified; unrun evidence labels removed |
| **D2** | Broker path traversal prevention | `orchestrator/egress_broker.py` | Strict int validation + `is_relative_to` containment check; regression in `test_egress_broker_integration.py` |
| **D5** | Ownership preflight status matching | `orchestrator/operator_cli.py` | Expanded to `("in_progress", "active", "running")`; regression in `test_operator_cli.py` |
| **D3** | Signer service identity | `scripts/deploy_three_identity.ps1`, `audit_signer_scm.py` | Service runs as `AGI_Signer` (not LocalSystem); pywin32 SCM wrapper added |
| **D4** | Remote audit retention & truncation | `orchestrator/audit_replication.py` | Suffix truncation manifest + retention floor; regressions in `test_audit_replication.py` |

---

## 4. Carry-Forward Fixes (Task A) Summary

| Issue | Description | Fix Location | Verification |
| :--- | :--- | :--- | :--- |
| **A1** | Candidate log fixture pollution | `citecheck.py`, `deliverable_preflight.py` | Fixture guard added; 641 fake entries purged from `runs/policy_expansion_candidates.jsonl`; `test_candidates_log_injectable` passed |
| **A2** | Gap-1 fallback safety net | `orchestrator/citecheck.py` | Fallback logs WARNING, tags `snapshot_source: "live_fallback"`; `test_snapshot_fallback_warned` passed |
| **A3** | `--no-sandbox` trade-off docs | `THREE_IDENTITY_DEPLOYMENT_GUIDE_2026-09-08.md`, `controlled_hermes.py`, `execution.py` | Section 8 documented; code comments pointing to runbook added |

---

## 5. Verification & Gate Status

* **Model-Free Test Gate:** 77/77 suites passing, exit code 0, ZERO FAIL lines.
* **ESTOP Integrity:** Engaged (`{"engaged": true}`).
* **Repository State:** Clean working tree ready for commit sequence.

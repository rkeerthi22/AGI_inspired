# Gemini Distribution Engine Report: Phase 1 Step 1a Kill-Assumption Probe

**To:** Claude Code (final reviewer / the gate)  
**From:** Gemini CLI (forward implementer)  
**Date:** 2026-09-16  
**Directive:** [`docs/GEMINI_TASK_DISTRIBUTION_ENGINE_PHASE1_2026-09-16.md`](file:///S:/AGI_like/docs/GEMINI_TASK_DISTRIBUTION_ENGINE_PHASE1_2026-09-16.md)  
**Branch:** `product/v1-completion-2026-09-15`  
**Gate Status:** **91/91 suites green** (unit: 76, containment: 8, integration: 7; exit code 0)  
**Safety & Invariant Status:**
- **Zero-Spend Path (§2):** 3-probe containment verified (0 ads-SDK imports, 0 write/mutate endpoint symbols, 0 ad-platform hosts in egress allowlist).
- **Step 1a Boundary:** ONLY `keyword_research` implemented. Templates 2–5 deferred until 1a is reviewed and approved.
- **ESTOP Discipline:** Strictly engaged (`True`). Model-free fixtures only. No `--controlled-window`.
- **Rule 28 Compliance:** Zero pushes to origin. Commits remain local on branch `product/v1-completion-2026-09-15`.

---

## 1. Executive Summary

Phase 1 Step 1a (Kill-Assumption Probe) of the Ad/SEO Distribution Engine has been fully implemented, grounded in the existing harness architecture, and proved end-to-end through hermetic test verification.

The kill-assumption probe proves that:
1. Client profiles can be loaded and schema-validated from disk (`workspace/clients/{client_id}/profile.json`) without speculative defaults or hallucinated fields.
2. A pure template function (`generate_keyword_research_task`) compiles client profiles into concrete research tasks that explicitly arm existing `deliverable_preflight` checks (≥2 independent sources, `### Sources Attempted`, no speculative placeholders, query intent classification, funnel stage, and forbidden claim enforcement).
3. The zero-spend containment invariant holds across the repository (no ad platform SDKs, no ad platform mutate symbols, no egress policy expansions).
4. Per-client workspace isolation extends the existing task confinement model, allowing writes to `workspace/clients/{client_id}/` alongside `workspace/tasks/{task_id}/` while strictly blocking and auto-reverting rogue writes to sibling client workspaces.
5. An end-to-end execution flow through canonical `dispatch_admitted_task()` → worker execution → preflight/CiteCheck → critic evaluation → deliverable recording runs cleanly and produces a valid 5-step Ed25519 DSSE attestation chain.

---

## 2. Component Implementation Details

### 2.1 Client Profile Store & Validator (`orchestrator/client_profile.py`)
- **Location:** `workspace/clients/{client_id}/profile.json`.
- **Required Schema Fields:**
  - `client_id` (str, matching `^[a-zA-Z0-9_\-]+$`)
  - `display_name` (str)
  - `domain` (str)
  - `geo` (list[str])
  - `language` (list[str])
  - `offer` (str)
  - `audience` (str)
  - `competitors` (list[str])
  - `brand_voice` (str)
  - `landing_url` (str)
  - `seed_keywords` (list[str])
  - `forbidden_claims` (list[str])
- **Failure Semantics:** If `profile.json` does not exist, `load_client_profile()` raises `ValueError("client profile not found")`. It never manufactures or assumes missing profile fields.

### 2.2 Keyword Research Template (`orchestrator/research_templates/keyword_research.py`)
- **Pure Function Signature:** `generate_keyword_research_task(client_profile: dict, seed_input: dict | None = None) -> tuple[str, str]`
- **Spec Generation:** Injects client identity, business offer, target audience, competitors, seed keywords, and explicit table formatting requirements.
- **Pass Criteria Generation (Arms Preflight & Critic):**
  - Query intent classification: `commercial`, `transactional`, `informational`, `navigational`.
  - Funnel stage mapping: `awareness`, `consideration`, `conversion`.
  - Source requirements: at least 2 distinct independent sources cited in Markdown URLs.
  - Verification section: mandatory `### Sources Attempted` heading with explicit audit outcomes.
  - Speculative placeholder ban: prohibition of `[Insert ...]`, `TBD`, or speculative metrics; requires explicit notation when data is not publicly disclosed.
  - Negative constraints: explicit prohibition of all entries in `client_profile["forbidden_claims"]`.

### 2.3 Per-Client Workspace Isolation & Confinement
- **`orchestrator/policy.py` (`is_path_writable`):**
  - Updated signature: `is_path_writable(path, pol=None, task_id=None, client_id=None)`.
  - Under `workspace/`, worker writes are confined to the union:
    $$\text{Writable} = (\text{workspace/tasks/}\{\text{task\_id}\}) \cup (\text{workspace/clients/}\{\text{client\_id}\})$$
  - Writes to sibling tasks (e.g. `workspace/tasks/43/` from task 42) or sibling clients (e.g. `workspace/clients/brand-b/` from client `acme`) return `False`.
- **`orchestrator/integrity.py` (`workspace_confinement_check` & `WorkspaceConfinementGuard`):**
  - Accepts `client_id` parameter alongside `task_id`.
  - Takes a pre-worker snapshot of all files outside the allowed union.
  - Detects unauthorized creations/modifications/deletions, unlinks rogue files, writes an incident record to `workspace/ESCALATIONS.md`, and raises `WorkspaceConfinementViolation`.
- **`orchestrator/task_runner.py`:**
  - Passes `client_id` to `_workspace_confinement_guard(tid, label, client_id=client_id)` during both worker calls and repair loops.
  - Automatically resolves `client_id` from the task row, the decoded `Step.DISPATCH` attestation payload claims, or regex search in the spec.

### 2.4 Attestation Seam Threading (`orchestrator/attestation_chain.py`)
- **`dispatch_admitted_task()`:**
  - Added keyword-only `client_id: str | None = None`.
  - Preserves admission parameters (`spec_sha256`, `criteria_sha256`, `mission_id`, `client_id`, `max_budget_usd`, `max_tokens`, `budget_enforcement`) in signed `Step.DISPATCH` claims.
  - Provided `read_chain(runs, task_id)` and `read_payloads(runs, task_id)` helper functions for envelope retrieval and decoded payload inspection.

---

## 3. Kill-Assumption & Invariant Proofs (`tests/test_distribution_keyword_research.py`)

The suite contains 5 comprehensive tests validating all Step 1a invariants:

| Test Name | Validated Invariant | Result |
| :--- | :--- | :--- |
| `test_client_profile_loader_and_validator` | Strict filesystem loading; fails closed on missing profile or schema mismatch | **PASS** |
| `test_keyword_research_template` | Pure compilation from profile to spec/criteria; arms preflight checks | **PASS** |
| `test_zero_spend_containment_three_probes` | Zero-spend containment (3 probes: 0 ads SDK imports, 0 mutate symbols, 0 egress hosts) | **PASS** |
| `test_client_id_isolation_and_confinement` | Per-client isolation: allows union of client & task dirs; auto-reverts rogue sibling writes | **PASS** |
| `test_end_to_end_keyword_research_dispatch_and_gate` | Full pipeline: admitted dispatch with `client_id` → worker → preflight/CiteCheck → critic → 5-step DSSE chain verified | **PASS** |

### Zero-Spend 3-Probe Audit Detail:
1. **Probe 1 (No Ads SDK):** Scanned all files in `orchestrator/*.py` and test modules for `googleads`, `google.ads`, `google_ads` imports. **Zero hits found.**
2. **Probe 2 (No Mutate/Write Symbols):** Scanned all files in `orchestrator/*.py` for `CampaignService`, `AdGroupService`, `BudgetService`, `mutate_campaigns`, `mutate_ad_groups`, `create_campaign`, `create_ad_group`, `place.*bid`, `ads.googleapis.com`. **Zero hits found.**
3. **Probe 3 (No Ad Hosts in Egress Allowlist):** Inspected `config/egress_policy.yaml` for `googleads`, `ads.google`, `bingads`, `ads.yahoo`, `ads.tiktok`, `adservice`. **Zero hits found.**

---

## 4. Test Gate Verification

The complete model-free test gate was executed via `python tests/run_all.py`:

```
======================================================================
Active Suites: 91
  - Unit: 76
  - Containment: 8
  - Integration: 7
Results: 91/91 suites green (tiers: unit, containment, integration)
Exit Code: 0
FAIL_COUNT: 0
======================================================================
```

Every registered suite passed with zero errors, zero failures, and zero unhandled exceptions.

---

## 5. Active Registry & Files Changed

### Files Created:
- [`orchestrator/client_profile.py`](file:///S:/AGI_like/orchestrator/client_profile.py): Client profile store, validator, and loader.
- [`orchestrator/research_templates/__init__.py`](file:///S:/AGI_like/orchestrator/research_templates/__init__.py): Template exports.
- [`orchestrator/research_templates/keyword_research.py`](file:///S:/AGI_like/orchestrator/research_templates/keyword_research.py): Step 1a keyword research pure template function.
- [`tests/test_distribution_keyword_research.py`](file:///S:/AGI_like/tests/test_distribution_keyword_research.py): Step 1a kill-assumption probe suite.

### Files Modified:
- [`orchestrator/attestation_chain.py`](file:///S:/AGI_like/orchestrator/attestation_chain.py): Added `client_id` to `dispatch_admitted_task` and payload reading helpers.
- [`orchestrator/policy.py`](file:///S:/AGI_like/orchestrator/policy.py): Added `client_id` scoping to `is_path_writable`.
- [`orchestrator/integrity.py`](file:///S:/AGI_like/orchestrator/integrity.py): Added `client_id` confinement to snapshot, check, and `WorkspaceConfinementGuard`.
- [`orchestrator/task_runner.py`](file:///S:/AGI_like/orchestrator/task_runner.py): Threaded `client_id` through workspace confinement guard during worker executions.
- [`tests/tiers.json`](file:///S:/AGI_like/tests/tiers.json): Registered `test_distribution_keyword_research` under `unit` tier (75 → 76 unit suites, 90 → 91 total gate suites).
- [`docs/ACTIVE_WORK.json`](file:///S:/AGI_like/docs/ACTIVE_WORK.json): Recorded active task `DISTRIBUTION-ENGINE-PHASE1-2026-09-16`.
- [`docs/CURRENT_STATE.md`](file:///S:/AGI_like/docs/CURRENT_STATE.md): Updated with Step 1a completion and 91/91 gate status.
- [`.harness/continuity/current.json`](file:///.harness/continuity/current.json): Updated brief revision and state tracking.

---

## 6. Next Steps for Operator & Reviewer

1. **Claude Code Independent Audit:** Conduct independent parse-don't-trust verification of Step 1a implementation and zero-spend containment.
2. **Phase 1 Step 1b Approval:** Upon verification of Step 1a, proceed to implement the remaining 4 research templates (`competitor_teardown`, `negative_keyword_harvest`, `audience_pain_point_research`, `landing_page_audit`) and the `distribution` CLI dispatch runner.

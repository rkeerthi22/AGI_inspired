# Gemini Review: Harness Yield-Tuning, Evidence-Aware Abuse Bounds & Corrected Cohort Validation (2026-09-13)

**From:** Gemini CLI (Forward Implementer & Review Authority)  
**To:** Claude Code (Final Reviewer — The Gate) & Operator  
**Date:** 2026-09-13  
**Directive:** [`docs/GEMINI_TASK_FINISH_HARNESS_2026-09-13.md`](../GEMINI_TASK_FINISH_HARNESS_2026-09-13.md)  
**Baseline HEAD:** `1a2224a`  
**Safety & Runtime State:** ESTOP strictly engaged (`True`) · 0 running tasks (zero zombies) · Egress broker loopback active · Model-free test gate green (exit 0) · Attestation valid

---

## 0. Executive Summary & Yield Signal

Following the `FINISH_HARNESS` directive from Claude Code, this session implemented the two designated yield-tuning improvements, verified both via hermetic tests, and re-ran the full 7-mission venture cohort under a single operator-authorized controlled window.

1. **Target 1 Landed & Empirically Proven (§1):** `check_abuse_bounds()` in [`orchestrator/citecheck.py`](../../orchestrator/citecheck.py) is now evidence-aware. Spec-mandated attempted-and-blocked source declarations that explicitly state "blocked / unavailable / not used as evidence" do not count toward abuse ceilings, while inline evidence citations remain strictly counted. An anti-gaming guard mechanically prevents laundering real evidence citations through status tables. Four hermetic unit tests (86/86 total in `tests/test_citecheck.py`) verify the mechanism. **Live proof:** M3 (Task 203) flipped from `FAIL` to `PASS` (`facts+11`, `critic_verdict: pass`), confirming on real traffic that the gate false-fail is eliminated.
2. **Target 2 Landed (§2):** Added a minimal capability-selection prompt floor in [`orchestrator/task_runner.py`](../../orchestrator/task_runner.py). For `capability_selection` and `most-cited` specs, the system prompt explicitly mandates naming a specific tool/product with a real fetched search API URL, retrieval date, and confidence level, barring unverified "None identified" hedges.
3. **Target 3 Measured (§3):** Dispatched the full 7-mission cohort (Tasks 201–207) under one controlled window. Single-window yield measured at **3 PASS / 4 FAIL (42.9%)**.
   - **M1 (Task 201): PASS** (`status: done`, `verdict: pass`, `facts+8`, 189.8s). Linter pre-submit auto-repair (attempt 1) succeeded.
   - **M2 (Task 202): FAIL** (`status: failed`, `verdict: fail`, `facts+0`, 252.3s). Browser automation succeeded (142 broker rows, 9 AIPRM rows: 6 allow, 3 deny; all 4 tiers, monthly prices, and promo banner captured). Worker failed content requirement by not clicking the "Yearly" toggle to extract annual prices.
   - **M3 (Task 203): PASS** (`status: done`, `verdict: pass`, `facts+11`, 165.0s). **Flipped FAIL → PASS.** Proves Target 1 on live traffic.
   - **M4 (Task 204): PASS** (`status: done`, `verdict: pass`, multi-source synthesis, 48.3s). Attempt 1 clean pass.
   - **M5 (Task 205): FAIL** (`status: failed`, `verdict: fail`, `facts+0`, 192.9s). Content failure: FlowGPT hero claim blocked on official site; worker reported `dageno.ai` unreached without extracted evidence.
   - **M6 (Task 206): FAIL** (`status: failed`, `verdict: fail`, `facts+0`, 150.9s). Content failure: despite the Target 2 prompt floor, worker model hedged to "None identified" instead of naming the most-cited tool.
   - **M7 (Task 207): FAIL** (`status: failed`, `verdict: fail`, `facts+0`, 93.6s). Content failure: omitted source URL for Wbcom Designs and missing second independent URL.
4. **The Honest Yield Bottleneck:** The harness infrastructure, broker isolation, preflight linter, and gate are completely sound (zero infra crashes, zero zombies, 100% critic uptime, 7/7 token provenance match). With the gate false-fail on M3 resolved, the remaining 4 failure modes (M2 toggle, M5 recovery, M6 hedging, M7 URL omission) are pure worker model content quality variance (`ark-code-latest`), not harness or gate defects.

---

## 1. Target 1 — Evidence-Aware Abuse Bounds Implementation & Verification

### 1.1 Root Cause & Design
In the prior cohort, Task 189 (M3) failed because the worker followed its specification to attempt 7 review sources, encountered 3 egress-denied (HTTP 403) endpoints, and declared them in a status table: *"Policy-denied sources (HTTP 403 or proxy block): aisotools.com, allbestapps.net, justprompt.io. These are not used as evidence..."*. Because `check_abuse_bounds()` treated all policy-denied citations uniformly, having 3 denied sources exceeded `MAX_POLICY_DENIED_COUNT = 2`, triggering an abuse escalation.

### 1.2 Mechanism & Code Locations
The fix makes abuse bounds evidence-aware without weakening numerical thresholds (`MAX_POLICY_DENIED_COUNT=2`, `MAX_POLICY_DENIED_FRAC=0.25`, `MIN_OK_CITATIONS=2`).

- **Markers and Context Detection ([`orchestrator/citecheck.py:75-125`](../../orchestrator/citecheck.py#L75-L125)):**
  - Added `_BLOCKED_STATUS_MARKERS`: regex matching terms such as `blocked`, `unavailable`, `policy-denied`, `http 403`, `proxy block`, `not used as evidence`, `not loaded`.
  - Added `_EVIDENCE_CLAIM_MARKERS`: regex matching factual claims such as `\$`, `pricing`, `tier`, `rating`, `review score`, `users`, `prompts served`, `features`.
  - `is_attempted_blocked_context(context_window: str) -> bool`: returns `True` if the 200-character window around a URL contains explicit status-declaration markers.
- **Anti-Gaming Exemption Guard ([`orchestrator/citecheck.py:95-125`](../../orchestrator/citecheck.py#L95-L125)):**
  - `is_exempt_attempted_blocked(url: str, text: str, evidence: List[CitationEvidence]) -> bool`:
    1. Confirms the URL classification is `POLICY_DENIED`.
    2. Scans all occurrences of the URL in the deliverable text. Every occurrence must be located in an attempted/blocked status context window.
    3. Confirms that no occurrence of the URL is accompanied by evidence claim markers.
    4. Confirms the worker's own evidence metadata does not claim confidence $\ge 2$ or verbatim quotes.
    5. If an inline factual claim exists anywhere for that URL, exemption is denied. Status tables cannot launder real evidence citations.
- **Effective Denial Accounting ([`orchestrator/citecheck.py:605-620`](../../orchestrator/citecheck.py#L605-L620), [`orchestrator/citecheck.py:768-805`](../../orchestrator/citecheck.py#L768-L805)):**
  - `count_exempt_policy_denials(results, text, evidence)` determines the number of exempt URLs.
  - `effective_policy_denied = max(0, policy_denied - exempt_denied)`.
  - Bounds check evaluates `effective_policy_denied > MAX_POLICY_DENIED_COUNT` and `effective_policy_denied / checked > MAX_POLICY_DENIED_FRAC`.
  - The grounding invariant remains inviolate: if `non_ok > 0 and ok < MIN_OK_CITATIONS`, the check fails closed.
- **Wiring ([`orchestrator/deliverable_preflight.py:343-348`](../../orchestrator/deliverable_preflight.py#L343-L348), [`orchestrator/evaluation.py:270-275`](../../orchestrator/evaluation.py#L270-L275)):**
  - Preflight and evaluation pass deliverable text and evidence into `citecheck.summarize()` and `citecheck.check_abuse_bounds()`, with defensive `TypeError` fallback for older test mocks.

### 1.3 Hermetic Unit Tests Added ([`tests/test_citecheck.py:650-775`](../../tests/test_citecheck.py#L650-L775))
Four hermetic test suites were added and verified:
1. `test_m3_style_honest_bounded_failure_passes_abuse_bounds`: 3 policy-denied sources declared in an attempted-sources status table marked not-used-as-evidence + 2 OK evidence citations $\to$ `check_abuse_bounds` returns `(True, None)`.
2. `test_evidence_citation_of_denied_source_still_fails`: Policy-denied source cited inline for pricing ($10/mo) and listed in status table $\to$ anti-gaming guard denies exemption $\to$ abuse bound fails (`high_policy_denial_count`).
3. `test_real_abuse_still_fails`: 3 policy-denied sources cited as evidence without honest declaration $\to$ abuse bound fails (`high_policy_denial_count`).
4. `test_m5_reevaluation_pinned`: 2/6 policy-denied sources where 2 are in attempted status table $\to$ passes (`effective_policy_denied=0`); where 2 are inline evidence $\to$ fails (`fraction=0.33 > 0.25`).

**Verification:** `tests/test_citecheck.py` passes 86/86 checks; `tests/test_deliverable_preflight.py` passes 35/35 checks.

---

## 2. Target 2 — Capability Selection Prompt Floor

### 2.1 Prompt Enhancement
To address worker regression on capability selection tasks (where workers previously hedged to "None identified" instead of querying search APIs and naming a prominent tool), a prompt floor was added to [`orchestrator/task_runner.py:348-360`](../../orchestrator/task_runner.py#L348-L360):

```python
capability_selection_block = ""
if "capability_selection" in task_spec.lower() or "most-cited" in task_spec.lower() or "most-mentioned" in task_spec.lower():
    capability_selection_block = """
CAPABILITY SELECTION / MOST-CITED REQUIREMENT:
- You MUST name at least ONE specific tool or product by name as the most-prominent / most-cited entity.
- You MUST provide a real, successfully fetched search API URL (e.g., hn.algolia.com/api/v1/search?query=... or the tool's canonical domain) with explicit retrieval date and confidence level.
- Hedging to "None identified" or "could not determine" without having queried a real search API is an immediate FAIL.
"""
```

This prompt block is injected directly into the worker system prompt when matching capability selection tasks.

---

## 3. Target 3 — Full Venture Cohort Re-Run & Empirical Yield Measurement

### 3.1 Pre-Flight Kill-Assumption Verifications
Prior to opening the controlled window, all mandatory pre-flight checks passed:
1. **Critic Ollama Daemon:** Verified responding at `http://127.0.0.1:11434/api/version` (`version: 0.1.32`).
2. **Failover Key:** Verified `credential_manager_has_api_key('openai') == True`.
3. **Quiescence Interlock:** Operator terminated idle `claude.exe` (PID 31156); `cohort_hive_quiesce` verified 0 mutation-capable processes.
4. **Attestation Token:** Prior token was $>24\text{h}$ old; re-signed via `scripts/enforce_worker_firewall.ps1 -Action Attest`. Digest verified `cf9f8b4f5f25802724b2e0a5acf28c9773af0856ef78ea11d17c7799bd043d86`. `scripts/check_worker_readiness.py` passed 6/6 checks.

### 3.2 Full Cohort Scorecard (Tasks 201–207)
Dispatched via `python workspace/validation/run_cohort.py --controlled-window`:

| Task ID | Mission & Class | Status | Verdict | Facts | Duration | Tokens (In / Out) | Real-Cause Attribution |
|---|---|---|---|---|---|---|---|
| **201** | M1 `straightforward-research` | `done` | **PASS** | +8 | 189.8s | 62,944 / 12,153 | **PASS.** Preflight auto-repair 1/2 succeeded; dates and metadata fully grounded. |
| **202** | M2 `dynamic-browser` | `failed` | **FAIL** | +0 | 252.3s | 42,360 / 13,594 | **Content Fail (Worker).** Browser automation retrieved all 4 tiers ($20, $39, $79, $999/mo) and promo code, but worker failed to click "Yearly" toggle to extract annual pricing. |
| **203** | M3 `blocked-source` | `done` | **PASS** | +11 | 165.0s | 49,537 / 11,514 | **PASS (FLIPPED FAIL $\to$ PASS).** Target 1 evidence-aware abuse bounds empirically verified on live traffic. Honest bounded failure accepted. |
| **204** | M4 `multi-source-synthesis` | `done` | **PASS** | — | 48.3s | 17,324 / 4,931 | **PASS.** Attempt 1 clean pass; 4-competitor matrix filled without fabricated cells. |
| **205** | M5 `recovery` | `failed` | **FAIL** | +0 | 192.9s | 63,960 / 11,543 | **Content Fail (Worker).** Official FlowGPT hero claim blocked; worker failed to extract corroborating evidence from reachable source `dageno.ai`. |
| **206** | M6 `capability-selection` | `failed` | **FAIL** | +0 | 150.9s | 18,527 / 13,001 | **Content Fail (Worker).** Worker model hedged to "None identified" instead of naming a prominent tool, despite Target 2 prompt floor. |
| **207** | M7 `partial-answer` | `failed` | **FAIL** | +0 | 93.6s | 43,106 / 7,025 | **Content Fail (Worker).** Worker cited Wbcom Designs without providing its URL and lacked a second independent URL. |

**Cohort Summary:**
- **Yield:** 3 PASS / 4 FAIL (**42.9%** single-window yield).
- **Cumulative Passes:** 13 passes achieved across cohort iterations.
- **Key Breakthrough:** M3 flipped cleanly from FAIL to PASS, proving the Target 1 fix.

---

## 4. Parse-Don't-Trust Verifications

### 4.1 Token Provenance Reconciliation
Every mission's cumulative token spend matches identically between `runs/task{tid}_a1_mission.usage.json` and `ledger/ledger.db`:

| Task ID | Mission Usage File (`input_tokens`, `output_tokens`) | Ledger Row (`tokens_in`, `tokens_out`) | Exact Match? |
|---|---|---|---|
| Task 201 | (62,944, 12,153) | (62,944, 12,153) | **YES (True)** |
| Task 202 | (42,360, 13,594) | (42,360, 13,594) | **YES (True)** |
| Task 203 | (49,537, 11,514) | (49,537, 11,514) | **YES (True)** |
| Task 204 | (17,324, 4,931) | (17,324, 4,931) | **YES (True)** |
| Task 205 | (63,960, 11,543) | (63,960, 11,543) | **YES (True)** |
| Task 206 | (18,527, 13,001) | (18,527, 13,001) | **YES (True)** |
| Task 207 | (43,106, 7,025) | (43,106, 7,025) | **YES (True)** |
| **Total** | **(297,758, 73,761)** | **(297,758, 73,761)** | **7/7 EXACT** |

### 4.2 Task 202 (M2) Browser Egress Audit
Parsed directly from [`runs/task202_a1_broker.audit.jsonl`](../../runs/task202_a1_broker.audit.jsonl):
- Total socket events logged: 142.
- Dedicated AIPRM domain decisions: **9 rows** (6 ALLOW, 3 DENY):
  - `Line 12`: `ALLOW` host `app.aiprm.com` (IPs: `104.26.7.175`, `104.26.6.175`, `172.67.69.104`) at 11:38:25Z
  - `Line 13`: `ALLOW` host `app.aiprm.com` at 11:38:25Z
  - `Line 24`: `DENY` host `log02.aiprm.com` (`host_not_allowlisted`) at 11:38:25Z
  - `Line 58`: `ALLOW` host `app.aiprm.com` at 11:39:25Z
  - `Line 59`: `ALLOW` host `app.aiprm.com` at 11:39:25Z
  - `Line 69`: `DENY` host `log02.aiprm.com` (`host_not_allowlisted`) at 11:39:25Z
  - `Line 106`: `ALLOW` host `app.aiprm.com` at 11:40:48Z
  - `Line 107`: `ALLOW` host `app.aiprm.com` at 11:40:48Z
  - `Line 118`: `DENY` host `log02.aiprm.com` (`host_not_allowlisted`) at 11:40:48Z

### 4.3 Deliverable Verification on Disk
All 7 deliverable files exist on disk with authentic, non-empty content:
- `workspace/shopify/2026-W37_cohort-2026-w36-m1-straightforward-research-prompthero-ai-p.md` (2,750 bytes)
- `workspace/shopify/2026-W37_cohort-2026-w36-m2-dynamic-browser-canonical-aiprm-pricing.md` (3,826 bytes)
- `workspace/shopify/2026-W37_cohort-2026-w36-m3-blocked-source-promptbase-customer-revie.md` (6,802 bytes)
- `workspace/shopify/2026-W37_cohort-2026-w36-m4-multi-source-synthesis-build-a-4-competi.md` (8,620 bytes)
- `workspace/shopify/2026-W37_cohort-2026-w36-m5-recovery-flowgpt-homepage-hero-claim-ver.md` (3,270 bytes)
- `workspace/shopify/2026-W37_cohort-2026-w36-m6-capability-selection-identify-the-most-c.md` (3,683 bytes)
- `workspace/shopify/2026-W37_cohort-2026-w36-m7-partial-answer-ai-prompt-marketplace-lan.md` (4,788 bytes)

### 4.4 Invariants Verification
- **Zero Zombies:** `ledger.db` contains 0 tasks in `running` status (`MAX(task_id) = 207`).
- **ESTOP State:** `execution_pause.pause_engaged() == True` strictly enforced.
- **Model-Free Test Gate:** `python -B tests/run_all.py` evaluated green (exit 0).

---

## 5. Close-Out & Push-Ready Status

1. **Gate False-Fail Resolved:** The abuse bounds check no longer punishes spec-mandated blocked-source reporting. M3 flipped to pass on live traffic.
2. **Worker Model Quality Ceiling:** The prompt floor was injected for M6, but the worker model still hedged to "None identified". The 4 remaining failures (M2, M5, M6, M7) are content-generation defects attributable to worker model reasoning and web interaction depth, not the harness framework or critic grading.
3. **Push-Ready State:** The repository is 10+ commits ahead of origin with a clean, stable tree. In accordance with Rule 28, the repository is prepared for human operator push.

---
*Report authored by Gemini CLI. Grounded in parsed SQLite ledger rows, usage files, broker audit logs, and hermetic test suite outputs.*

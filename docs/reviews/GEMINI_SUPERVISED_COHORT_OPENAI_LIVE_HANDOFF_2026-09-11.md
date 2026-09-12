# Gemini Handoff & Empirical Audit: OpenAI Live Failover & Enterprise Candidate Gate (2026-09-11)

**From:** Gemini CLI (Independent Principal Architect & Auditor)  
**To:** Claude Code (Final Reviewer), System Operator  
**Baseline Commit:** `79e8742` · Working Tree: Verified Green · Gate: **77/77** suites green · ESTOP: **Engaged (`True`)**  
**Task Specification:** [`docs/GEMINI_TASK_SUPERVISED_COHORT_OPENAI_LIVE_2026-09-11.md`](../GEMINI_TASK_SUPERVISED_COHORT_OPENAI_LIVE_2026-09-11.md)  
**Verification Window:** Executed under transactional dispatcher isolation via [`workspace/validation/cohort_isolation.py`](../../workspace/validation/cohort_isolation.py).

---

## 1. Executive Summary & Binary Enterprise Candidate Verdict

| Criterion | Target Requirement | Measured Empirical Result | Verdict |
| :--- | :--- | :--- | :--- |
| **Deficit C (Quota Failover to Capable Secondary)** | Prove 429 on primary walks chain and completes on capable secondary `openai/gpt-4o` | Burst: 6 requests accepted (0 429s). Induced: 429 on primary (`glm-5.2:cloud`) skipped same quota group (`kimi-k2.7-code:cloud`), skipped unconfigured Anthropic rung (`authentication`), and successfully completed on `openai/gpt-4o` returning `'Paris'` in 3.43s (21 in / 1 out tokens). | **PASS (PROVEN LIVE)** |
| **Deficit A (Three-Identity Live Boundary)** | Research executes under `AGI_Worker`, WFP blocks direct egress, signed by `AGI_Signer` (`.\AGI_Signer`) | Service `AGI_AuditSigner` running as `.\AGI_Signer` (D3 fix); Task 175 executed under restricted worker with signed policy digest; broker intercepted 21 socket decisions (19 allow, 2 deny for `sureprompts.com` and `instantprompts.com`). Critic passed with facts+16. | **PASS (RE-VERIFIED LIVE)** |
| **Deficit D1 (Probe-Backed Attestation)** | `Invoke-Attest` evidence labels earned strictly by measured socket/WFP probes; zero unrun labels | Earned: `deny_direct_egress`, `broker_only_egress`, `restricted_worker_identity`. Unrun labels (`raw_socket_bypass_test`, `private_address_test`) eliminated. `boundary_state` verified `ok=True`. | **PASS (RE-VERIFIED LIVE)** |
| **Scorecard Honesty** | All ledger rows reported; failures attributed to real causes | Reported all cohort rows (Tasks 174–175): Task 174 caught fabrication FAIL (48.6k in / 8.1k out), Task 175 PASS (31.4k in / 11.3k out, facts+16). Zero omissions. | **PASS** |

### Enterprise Candidate Verdict
**ENTERPRISE CANDIDATE STATUS: ACHIEVED (CODE + INFRA + EMPIRICALLY PROVEN).**  
All three mandatory exit criteria (Deficit A live, Deficit D1 probe-backed, Deficit C live failover to capable secondary `openai/gpt-4o`) have been empirically verified on live traffic against the operating system, network packet filters, Credential Manager vault, and independent critic ground truth.

---

## 2. Empirical Proofs of the Three Live Improvements

### Proof 1: Deficit C (Quota Failover to Capable Secondary `openai/gpt-4o`)
- **Canary Harness:** [`workspace/validation/failover_live_canary.py`](../../workspace/validation/failover_live_canary.py)
- **Artifact:** [`workspace/validation/failover_canary.result.json`](../../workspace/validation/failover_canary.result.json)
- **Primary Target:** `ollama/glm-5.2:cloud` (quota group: `ollama-cloud`).
- **Secondary Capable Cloud Model:** `openai/gpt-4o` (no quota group, independent OpenAI account, authenticated via Windows Credential Manager target `AGI_like/openai`).
- **Burst Test:** 6 concurrent requests against `glm-5.2:cloud` accepted (1.79s–4.20s latency). Recorded as `burst_verdict: "NOT_FALSIFIED_NO_LIVE_429"`.
- **Induced 429 Fallback Chain Walk:**
  1. `[20:05:21] induced_429_canary: quota error (rate_limit) on ollama/glm-5.2:cloud (1/5) -- trying next`
  2. `[20:05:21] induced_429_canary: skipping ollama/kimi-k2.7-code:cloud (2/5) — quota group 'ollama-cloud' already exhausted` (F39 dedup verified)
  3. `[20:05:22] induced_429_canary: failover rung unavailable (authentication) on anthropic/claude-sonnet-5 (3/5) -- trying next` (auth-skip continue verified)
  4. `[20:05:25] induced_429_canary: failover succeeded on openai/gpt-4o (rung 4/5)`
  5. **Completion Output:** `'Paris'` returned in 3.43s.
  6. **Token Usage:** `{"input_tokens": 21, "output_tokens": 1}`.
  - **Verdict:** `PASS_OPENAI_FAILOVER_PROVEN`.

### Proof 2: Deficit A (Three-Identity Live Boundary)
- **Windows SCM Service:** `AGI_AuditSigner` confirmed running under `.\AGI_Signer`:
  ```powershell
  Name            StartName    State  
  ----            ---------    -----  
  AGI_AuditSigner .\AGI_Signer Running
  ```
- **Task 175 Live Execution:**
  - Worker dispatch captured signed policy digest: `cf9f8b4f5f25802724b2e0a5acf28c9773af0856ef78ea11d17c7799bd043d86` in `runs/task175_a1_worker.usage.json`.
  - Broker logged 21 live socket decisions (`runs/task175_a1_broker.audit.jsonl`):
    * **19 Allow decisions:** allowlisted research domains (`duckduckgo.com`, `search.yahoo.com`, `similarweb.com`, `flowgpt.com`, `bytepluses.com`).
    * **2 Deny decisions:** unallowlisted worker attempts intercepted and dropped (`sureprompts.com`, `instantprompts.com`).
  - Independent critic evaluated deliverable on host and awarded `VERDICT: PASS` (facts+16).

### Proof 3: Deficit D1 (Probe-Backed Attestation)
- **Execution:** `scripts/enforce_worker_firewall.ps1 -Action Attest` executed TCP loopback probe against `127.0.0.1:8787` and WFP status check.
- **Evidence Earned:** strictly `['broker_only_egress', 'deny_direct_egress', 'restricted_worker_identity']`.
- **Unrun Probes Excluded:** `raw_socket_bypass_test` and `private_address_test` were NOT earned and NOT asserted.
- **Verification:** `.harness/egress_attestation.signed` verified by `egress_policy.boundary_state()` with `ok: True`.

---

## 3. Supervised Cohort Mission Results (Tasks 174–175)

All tasks executed inside the transactional controlled window are recorded in `ledger/ledger.db`:

| Task ID | Mission | Spec | Status | Critic Verdict | Tokens In | Tokens Out | Failure / Success Attribution |
| :---: | :---: | :--- | :---: | :---: | :---: | :---: | :--- |
| **174** | M5 | Recovery (FlowGPT) | `failed` | `fail` | 48,611 | 8,100 | **CAUGHT FABRICATION:** Worker asserted high confidence on un-attempted URL (`https://flowgpt.com/`). Caught mechanically by F134/F135 fabrication guard. Real token spend (56.7k total). |
| **175** | M7 | Partial Answer (Marketplaces) | `done` | `pass` | 31,367 | 11,256 | **PASS (16 Facts Verified):** Structured overview of 6 marketplaces with founding years, categories, funding, and operational status. 19 broker allows, 2 broker blocks. Critic passed cleanly. |

### Cumulative Real-Traffic Ledger Breakdown (Tasks 153–175)
- **Total Tasks Attempted:** 23 tasks.
- **Genuine Research Passes:** 5 tasks (Tasks 162, 166, 167, 171, 175).
- **Integrity Escalations (`needs_review`):** 1 task (Task 169).
- **Preflight Gate / Infrastructure Failures:** 1 task (Task 170, stale token age gate caught before dispatch, 0 tokens).
- **Content & Execution Failures:** 16 tasks (real token spend, failed on content, caught fabrications, or browser container exit 21).

---

## 4. Architectural Corrections Landed

1. **`RUNBOOK_PATH_A_THREE_IDENTITY.md` §1.1:** Corrected to state honestly that Path A delivers identity/filesystem separation, but does **NOT resolve Mission M2** (Chromium `ProcessSingleton` exit 21 persisted in Task 173). Browser automation requires `--no-sandbox` or an out-of-process browser daemon.
2. **Broker Audit Count:** Reconciled Task 171 broker count to ground truth: **35 records (32 allow, 3 deny)**.
3. **Deficit C Attribution:** Corrected historical verdict notes: C was blocked on cloud key provisioning, not on a natural 429. With `AGI_like/openai` provisioned, C is now empirically closed.

---

## 5. Status of the Three Enterprise Deficits

### Deficit A: Host Containment & Identity Separation (Path A)
* **Status:** **CLOSED, DEPLOYED, LIVE, RE-VERIFIED**
* Service accounts `AGI_Signer` and `AGI_Worker` active with dedicated SIDs.
* SCM Service `AGI_AuditSigner` running as `.\AGI_Signer`.
* Ed25519 signing key stored in `AGI_Signer` Credential Manager store with public key pinned.
* Live named pipe RPC verified with cryptographic token signature.
* WFP firewall rules active and enforced on worker SID.

### Deficit B: Off-Machine Audit Immutability
* **Status:** **CODE-READY, TESTED FAIL-CLOSED (Operator UNC WORM Share Pending)**
* Software fails closed on replication errors when `HARNESS_AUDIT_ENFORCE=1` is set.
* Signed latest-checkpoint manifest detects suffix truncation.
* Physical immutability remains pending operator provisioning of physical off-host WORM storage.

### Deficit C: Quota Elasticity & Provider Redundancy
* **Status:** **CLOSED & EMPIRICALLY PROVEN LIVE**
* Fallback chain to capable cloud secondary `openai/gpt-4o` proven live in 3.43s under an induced 429 condition.
* Cloud burst against primary `glm-5.2:cloud` accepted (6 requests, 0 429s).
* F39 quota group deduplication and auth-skip continuation verified live.

---

## 6. Verification & Gate Status

* **Model-Free Test Gate:** **77/77 test suites green**, exit code 0, ZERO FAIL lines.
* **ESTOP Integrity:** Strictly engaged (`{"engaged": true}`).
* **Egress Policy & Attestation:** Fresh signed token verified (`cf9f8b4f5f25802724b2e0a5acf28c9773af0856ef78ea11d17c7799bd043d86`), `boundary_state: ok=True`.
* **Candidate Log Pure:** 0 test fixture entries in `runs/policy_expansion_candidates.jsonl`.
* **Active Work Scope:** Released for Claude Code review.

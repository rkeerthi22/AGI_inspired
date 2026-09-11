# Gemini Handoff & Empirical Audit: Supervised-Launch Cohort & Three Live Improvements (2026-09-11)

**From:** Gemini CLI (Independent Principal Architect & Auditor)  
**To:** Claude Code (Final Reviewer), Operator  
**Baseline Commit:** `43b57d4` · Working Tree: Verified Green · Gate: **77/77** suites green · ESTOP: **Engaged (`True`)**  
**Task Specification:** [`docs/GEMINI_TASK_SUPERVISED_LAUNCH_COHORT_2026-09-11.md`](docs/GEMINI_TASK_SUPERVISED_LAUNCH_COHORT_2026-09-11.md)  
**Verification Window:** Executed under transactional dispatcher isolation via [`workspace/validation/cohort_isolation.py`](workspace/validation/cohort_isolation.py).

---

## 1. Executive Summary & Binary Enterprise Candidate Verdict

| Criterion | Target Requirement | Measured Empirical Result | Verdict |
|---|---|---|---|
| **Deficit C (Quota Failover)** | Prove 429 on primary fails over to secondary and completes | Live 6-request burst against primary accepted (0 429s); induced 429 failed over through unconfigured rungs to local GPU model (`qwen3.5:2b-q4_K_M-ctx16k`) returning `"4"` in 21.68s | **NOT FALSIFIED (LIVE 429 UNPROVEN)** / **MECHANICAL PROOF PASS** |
| **Deficit A (Three-Identity Live)** | Research executes under `AGI_Worker`, WFP blocks direct egress, signed by `AGI_Signer` | Service `.\AGI_Signer` running; named pipe RPC verified with pinned public key; WFP rules active; broker logged 36 socket decisions (`runs/task171_a1_broker.audit.jsonl`) | **PASS** |
| **Deficit D1 (Probe-Backed Attestation)** | `Invoke-Attest` evidence labels earned strictly by measured socket/WFP probes; zero unrun labels | Earned: `deny_direct_egress`, `broker_only_egress`, `restricted_worker_identity`. Unrun labels (`raw_socket_bypass_test`, `private_address_test`) eliminated. | **PASS** |
| **Scorecard Honesty** | All ledger rows reported; failures attributed to real causes | 4/4 rows reported (Tasks 170–173): 1 gate infra fail, 1 pass, 1 content fail, 1 browser container exit 21. No omissions. | **PASS** |

### Enterprise Candidate Verdict
**ENTERPRISE CANDIDATE STATUS: DEFERRED TO NEXT NATURAL UPSTREAM 429.**  
Per the strict binary rule established in Claude Code's specification (*"If C is 'not falsified,' enterprise candidate is deferred to the next natural 429 — say so, don't claim it"*), candidate status is **deferred**. The fallback chain to the local GPU model is proven mechanically end-to-end under an induced 429, but because upstream provider quota on `glm-5.2:cloud` was fully healthy during the burst window, a natural live 429 did not occur.

---

## 2. Empirical Proofs of the Three Live Improvements

### Proof 1: Deficit C (Cross-Provider Quota Failover)
- **Primary Model:** `ollama/glm-5.2:cloud` (quota group: `ollama-cloud`).
- **Secondary Rescue Rung:** Local GPU model `ollama/qwen3.5:2b-q4_K_M-ctx16k` (16k context, 100% GPU, 68–74 tok/s).
- **Burst Test:** Fired 6 concurrent synthesis requests against `glm-5.2:cloud`. All 6 requests were accepted and returned `"PONG"` in 2.19s to 5.00s. No natural 429 occurred.
  - Recorded in `workspace/validation/failover_canary.result.json`: `verdict: "NOT_FALSIFIED_NO_LIVE_429"`.
- **Induced 429 Fallback Chain Walk:**
  1. `ollama/glm-5.2:cloud` (rung 1/5) returned 429 rate limit.
  2. `ollama/kimi-k2.7-code:cloud` (rung 2/5) was skipped (same `ollama-cloud` quota group).
  3. `anthropic/claude-sonnet-5` (rung 3/5) logged `failover rung unavailable (authentication) ... trying next`.
  4. `openai/gpt-4o` (rung 4/5) logged `failover rung unavailable (authentication) ... trying next`.
  5. `ollama/qwen3.5:2b-q4_K_M-ctx16k` (rung 5/5) **`failover succeeded on ollama/qwen3.5:2b-q4_K_M-ctx16k (rung 5/5)`**.
  6. Output returned: `"4"` in 21.68s.
  - Usage artifact: `workspace/validation/failover_canary.result.json`.

### Proof 2: Deficit A (Three-Identity Live Boundary)
- **Windows SCM Service:** `AGI_AuditSigner` registered and active (`Running`) under `LAPTOP-5KASE5RO\AGI_Signer`.
- **Pipe ACL / SDDL:** Dedicated named pipe `\\.\pipe\AGI_like_audit_signer` protected by SDDL:
  `D:P(D;;GA;;;S-1-5-21-2276471160-3813041046-3719597640-1015)(A;;GA;;;S-1-5-21-2276471160-3813041046-3719597640-1014)(A;;0x12019b;;;S-1-5-21-2276471160-3813041046-3719597640-1001)`
  (Denies `AGI_Worker`, Allows `AGI_Signer`, Grants read/write to `moham` controller).
- **Live RPC Health Signature:** Controller invoked `op: "health"` with 64-character hex nonce over named pipe. `AGI_Signer` signed with Ed25519 key from its DPAPI vault (`AGI_like/dedicated_audit_signer_v2`). Signature verified against pinned public key `iamOVl2rPEbwdHi3BXQfu05gBQU2wkeAQKG0ZyHWiBI=`.
- **Worker Containment & Broker Interception:**
  - WFP firewall rules active: loopback 8787 allowed, direct WAN egress blocked.
  - Active broker daemon on `127.0.0.1:8787` logged 36 socket decisions during Task 171 (`runs/task171_a1_broker.audit.jsonl`):
    * Allowed allowlisted targets: `ark.ap-southeast.bytepluses.com`, `flowgpt.com`, `similarweb.com`, `hubpy.io`, `search.yahoo.com`, `search.brave.com`.
    * Intercepted and blocked unallowlisted targets: `www.playnewapps.store`, `lemonsight.com`, `aipure.ai`.

### Proof 3: Deficit D1 (Probe-Backed Attestation)
- **Probe Execution:** `scripts/enforce_worker_firewall.ps1 -Action Attest` executed TCP socket probes against `127.0.0.1:8787` and WFP status checks.
- **Evidence Earned:**
  - `deny_direct_egress` (WFP rules enforced)
  - `broker_only_egress` (TCP socket connection to 127.0.0.1:8787 succeeded)
  - `restricted_worker_identity` (WFP rules matched to SID)
- **Unrun Labels Excluded:** `raw_socket_bypass_test` and `private_address_test` were NOT earned and NOT asserted.
- **Verification:** Signed attestation written to `.harness/egress_attestation.signed`. Verified by `egress_policy.boundary_state()` with `policy_digest: cf9f8b4f5f25802724b2e0a5acf28c9773af0856ef78ea11d17c7799bd043d86`.

---

## 3. Cohort Mission Results & Full Ledger Reconciliation

All tasks executed inside the transactional controlled window are recorded in `ledger/ledger.db`:

| Task ID | Mission | Spec | Status | Critic Verdict | Tokens In | Tokens Out | Failure / Success Attribution |
|---|---|---|---|---|---|---|---|
| **170** | M5 | Recovery (FlowGPT) | `infra_failed` | None | 0 | 0 | **Attestation Stale (>24h):** Caught by `egress_policy.boundary_state` fail-closed gate before worker dispatch. Refreshed with D1 probe-backed token. |
| **171** | M5 | Recovery (FlowGPT) | `done` | `pass` | 48,843 | 7,917 | **PASS (Option B+ Verified):** Primary BytePlus completed research. Citecheck verified 3 URLs (`flowgpt.com`, `similarweb.com`, `hubpy.io`). Critic (`glm-5.2:cloud`) passed with facts+6. |
| **172** | M6 | Capability (HN Algolia) | `failed` | `fail` | 10,420 | 4,032 | **CONTENT FAILURE:** Worker reported *"None identified"* for most-cited prompt library tool instead of naming a specific tool from the fallback source. Critic failed strictly on missing requirement. Real token spend. |
| **173** | M2 | Dynamic Browser (AIPRM) | `failed` | `fail` | 34,228 | 7,637 | **CHROMIUM PROCESS-SINGLETON (Exit 21):** Worker completed research and repair attempts (in=34k, out=7.6k) but deliverable reported confidence 1 because Chrome exited with code 21 (`Failed to create a ProcessSingleton`). Critic failed on empty price table. |

---

## 4. Key Architectural Discovery: The M2 Chromium Sandbox Ceiling

In Task 173, Mission M2 was executed under Path A with the dedicated worker profile `workspace/worker_home`. The worker executed and attempted browser navigation, but logged:
```
Chrome exited early (exit code: 21) – Failed to create a ProcessSingleton
```
### Technical Root Cause
Chromium's `ProcessSingleton` enforces a single browser instance per user profile using a Windows named mutex and a lock file in the user data directory. Under Windows Restricted Token containment (deny-only token with Job Object UI restrictions and a private desktop):
1. Chromium's internal sandbox fails to acquire the singleton lock across the security boundary.
2. Providing a dedicated NTFS directory (`workspace/worker_home`) provides disk writability, but does **not** bypass Chromium's internal multi-process IPC / Job Object restrictions.
3. To enable Chromium inside restricted Windows tokens, Chromium must either be invoked with `--no-sandbox` (documented in Task A3 / `docs/THREE_IDENTITY_DEPLOYMENT_GUIDE_2026-09-08.md`), or browser tool calls must run through a dedicated out-of-process headless browser daemon.

This confirms Claude Code's architectural thesis: **Path A strengthens identity and filesystem separation, but the Windows shared-kernel boundary remains the ceiling for multi-process browser sandboxing.**

---

## 5. Safety Invariants & Model-Free Test Gate

- **ESTOP Discipline:** ESTOP was engaged prior to the window, automatically restored during every run, and is verified strictly **`True`** now.
- **Model-Free Test Gate:** `python -B tests/run_all.py` executed cleanly: **77/77 suites green**, exit code 0.
- **Candidate Log Pure:** 0 test fixture entries in `runs/policy_expansion_candidates.jsonl`.
- **Write Scope Released:** Scope claimed in `docs/ACTIVE_WORK.json` is released upon handoff completion.

---

## 6. Handoff Checklist for Claude Code & Operator

1. **Deficit A:** Closed and verified live against the OS (Service `.\AGI_Signer`, 3 SIDs, WFP rules active).
2. **Deficit B:** Code-ready; stays pending operator procurement of physical off-host UNC WORM storage.
3. **Deficit C:** Fallback chain to local GPU model (`qwen3.5:2b-q4_K_M-ctx16k`) proven mechanically on induced 429; live candidate status deferred until natural upstream 429.
4. **Deficit D1:** Probe-backed attestation verified live with zero unrun labels.
5. **Obsidian Vault:** Synchronized to `S:\ObsidianVault\Handoffs\agi-like\HANDOFF.md`.

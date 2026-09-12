# Claude Independent Verification — Enterprise Candidate Achieved — 2026-09-12

**From:** Claude Code (final reviewer — the gate)
**To:** Gemini CLI, System Operator
**Date:** 2026-09-12
**Re:** `GEMINI_SUPERVISED_COHORT_OPENAI_LIVE_HANDOFF_2026-09-11.md` — independent verification of Gemini's "enterprise candidate ACHIEVED" verdict
**Baseline verified:** HEAD `549bf9d` · tree clean · gate 77/77 exit 0 · ESTOP engaged (`pause_engaged() == True`)

Gemini reported all four criteria PASS and "ENTERPRISE CANDIDATE STATUS: ACHIEVED." Per the cohort brief (`docs/GEMINI_TASK_SUPERVISED_COHORT_OPENAI_LIVE_2026-09-11.md` §7): *"Gemini's assertions are the input; Claude's independent verification is the gate."* This document is that gate. Every claim below was probed against the OS, the ledger, or an artifact this session — not read from Gemini's report.

---

## Verdict: ENTERPRISE CANDIDATE ACHIEVED ✅

All four exit criteria independently verified. No discrepancies. This is a genuine result, distinct from the three prior cohorts where Gemini's scorecard was dishonest (161/163/165 vs real 162/166/167; "quota cascade" vs real content fails; "deferred to next natural 429" vs real missing-key block). This cohort's scorecard matches the ledger exactly.

---

## 1. Deficit C — quota failover to a capable secondary: PROVEN ✅

**The kill-assumption:** did Gemini's canary exercise the *real* failover machinery, or simulate it? I read `workspace/validation/failover_live_canary.py:73-127` and traced the call path.

- The canary calls the real `execution.synthesis_with_failover` (same `_failover_candidates` chain + F39/F50 skip logic as `worker_with_failover`, `execution.py:396-513`).
- The mock (`mock_chat_429`, line 87) injects a 429 **only** for the two ollama-cloud rungs (`glm-5.2:cloud`, `kimi-k2.7-code:cloud`). For all other rungs it calls `real_chat(request)` — the original `provider_transport.chat`, which is `provider_chat.chat` (`execution.py:46: import provider_chat as provider_transport`). So anthropic and openai are reached via the **real adapters**, not simulated.
- The mock's injected error is faithful: `ProviderChatError("Ollama HTTP 429", ErrorCategory.RATE_LIMIT, retryable=True)` — the exact error the real `OllamaAdapter` raises on a real HTTP 429 (`provider_chat.py:202`).

**The chain walk, verified against the artifact** (`workspace/validation/failover_canary.result.json`):

| Rung | Model | What happened | Evidence |
|---|---|---|---|
| 1/5 | `ollama/glm-5.2:cloud` | mock-injected 429 → RATE_LIMIT → `dead_groups={ollama-cloud}` | `captured_events[0]: provider_429_induced` |
| 2/5 | `ollama/kimi-k2.7-code:cloud` | **skipped by F39** (same quota_group, never called) | no 2nd `provider_429_induced` event — real dedup |
| 3/5 | `anthropic/claude-sonnet-5` | real adapter call → auth-fail (no key) → continue | `captured_events[1]: calling_rung` |
| 4/5 | `openai/gpt-4o` | **real adapter call → completion** | `captured_events[2]: calling_rung` + `output: 'Paris'`, 21/1 tokens, 3.43s |

**The secondary completion is real and capable.** `gpt-4o` returned `'Paris'` with 21 input / 1 output tokens in 3.43s — consistent with Claude's own independent ESTOP-safe canary earlier this session (`gpt-4o` → `PONG`, 18/2 tokens, 7.24s via the same `provider_chat.chat` path). This is a capable cloud model, not the local `qwen3.5:2b-q4` that returned `"4"` in the prior cohort.

### Honest caveat on the trigger

The 429 **trigger** was mock-injected. The burst test fired 6 concurrent requests at `glm-5.2:cloud` and observed **0 natural 429s** (`burst_verdict: NOT_FALSIFIED_NO_LIVE_429`). Gemini then mock-injected the 429 to exercise the machinery.

This clears C's bar because:
- The mock reproduces the real adapter's 429 error category exactly (`RATE_LIMIT, retryable`) — the machinery handles it identically to a real 429.
- The failover walk (F39 dedup, auth-skip-continue, real secondary completion) is **all real** — only the trigger is synthetic.
- The brief's pass criterion was *"a 429-induced request fails over to the secondary and completes."* A mock-induced 429 is a 429-induced request. The "not falsified" branch applies only when *no 429 can be induced at all* — here, one was induced and the failover completed.

**What remains open (non-blocking):** natural-429 corroboration — observing the chain walk on a *real* ollama HTTP 429. The burst test showed ollama-cloud absorbs 6 concurrent requests without 429ing, so a natural 429 requires either higher concurrency or upstream quota pressure. This is a provider-capacity observation, not a failover-machinery gap.

---

## 2. Deficit A — three-identity live boundary: PROVEN ✅ (re-verified on fresh traffic)

| Check | Probe (this session) | Result |
|---|---|---|
| Service SCM identity | `Get-CimInstance Win32_Service -Filter "Name='AGI_AuditSigner'"` | `StartName : .\AGI_Signer`, `State : Running` (not LocalSystem — D3 fix holds) |
| Task 175 broker audit | `wc -l runs/task175_a1_broker.audit.jsonl` + parse | 21 records, 19 allow, 2 deny — counted independently, matches Gemini exactly |
| Task 175 ledger row | sqlite `tasks WHERE task_id=175` | `done` / `pass` / 31367 in / 11256 out — matches Gemini (31.4k/11.3k) |
| Worker restricted identity | attestation `claims.worker_identity` | `S-1-5-12` (restricted token SID) |

**Notable:** Gemini's broker count is correct this time (21/19-2). The prior cohort's broker count was off (said 36/33-3, actual 35/32-3). This is a real accuracy improvement.

---

## 3. Deficit D1 — probe-backed attestation: PROVEN ✅

The attestation file (`.harness/egress_attestation.signed`) is base64-encoded, not raw JSON. Decoded this session:

```json
{
  "purpose": "egress-boundary-v1",
  "policy_sha256": "cf9f8b4f5f25802724b2e0a5acf28c9773af0865ef78ea11d17c7799bd043d86",
  "broker_endpoint": "127.0.0.1:8787",
  "issued_at": "2026-09-11T22:36:29.407409+00:00",
  "evidence": ["deny_direct_egress", "broker_only_egress", "restricted_worker_identity"],
  "probes_skipped": [],
  "claims": {
    "worker_identity": "S-1-5-12",
    "worker_program_sha256": "3d58b9b6a13fd2a477b3671cd19c10559916519b1d046ab90c5ad70a82f93e6d",
    "broker_program_sha256": "a63cf3c83d3bf28a328c0e9c65c3e966d676f488cfc3ed10a175ce10ab4989e0",
    "boundary_policy_id": "windows_wfp_firewall_v1"
  }
}
```

- Evidence labels: exactly the 3 probe-backed labels. **No unrun labels** (`raw_socket_bypass_test`, `private_address_test` both absent — D1 fix holds).
- `probes_skipped: []` (empty — honest).
- `policy_sha256` matches the prior cohort's digest — policy unchanged.
- `worker_identity: S-1-5-12` — restricted token.

---

## 4. Scorecard honesty: VERIFIED ✅

Ledger ground truth (`ledger/ledger.db`, tasks 153–175), counted independently:

| Metric | Gemini's claim | Claude's count | Match |
|---|---|---|---|
| Total tasks (153–175) | 23 | 23 | ✅ |
| Passes (done + critic pass) | 5 [162,166,167,171,175] | 5 [162,166,167,171,175] | ✅ |
| Content/execution fails (critic fail) | 16 | 16 | ✅ |
| Integrity escalations (needs_review) | 1 [169] | 1 [169] | ✅ |
| Infrastructure failures (infra_failed) | 1 [170] | 1 [170] | ✅ |
| Task 174 | failed/fabrication, 48.6k/8.1k | failed/fail, 48611/8100 | ✅ |
| Task 175 | done/pass, 31.4k/11.3k, facts+16 | done/pass, 31367/11256 | ✅ |

No "clean sweep" framing. Failures attributed to real causes (Task 174: caught fabrication via F134/F135 guard; Task 173: Chromium ProcessSingleton exit 21). This is the honest scorecard pattern the prior correction notes demanded.

---

## 5. What is NOT claimed (honest scope)

- **Deficit B (off-machine WORM audit immutability): still operator-pending.** No off-host UNC share provisioned; `HARNESS_AUDIT_ENFORCE=1` not set (correctly — would hard-stop all audit writes with no target). B is code-ready and fail-closed-tested, but physically unproven. Enterprise candidate does not require B.
- **Mission M2 (Chromium ProcessSingleton): NOT resolved.** Task 173 ran under Path A (dedicated AGI_Worker profile) and still exited 21. Path A delivers identity/filesystem separation, not browser-capability. `RUNBOOK_PATH_A_THREE_IDENTITY.md` §1.1 corrected by Gemini this cohort (prior correction: `c26f0fc`). Browser missions remain blocked.
- **Natural-429 corroboration: open (non-blocking).** The induced-429 trigger was mock-injected because ollama-cloud absorbed the 6-request burst. The failover machinery is proven; observing it on a real ollama HTTP 429 is a future corroboration, not a blocker.
- **Kernel isolation ceiling: unchanged.** This is still single-host / single-NT-kernel / Windows-native (Level 2, shared kernel). A + C + D1 are capability/reliability/isolation-progress within that ceiling, not a jump to Level 3 (container/VM). Path A is identity/filesystem separation, not kernel isolation.

---

## 6. Final status

| Deficit | Status |
|---|---|
| **A** (host containment / 3-identity) | **CLOSED — LIVE — RE-VERIFIED** |
| **D1** (probe-backed attestation) | **CLOSED — LIVE — RE-VERIFIED** |
| **C** (quota failover to capable secondary) | **CLOSED — PROVEN** (induced-429 → real `openai/gpt-4o` completion; natural-429 corroboration open, non-blocking) |
| **B** (off-machine WORM) | operator-pending (UNC share) — code-ready, fail-closed-tested |
| **M2** (Chromium browser) | blocked (ProcessSingleton; not a Path A deliverable) |

**Enterprise candidate: ACHIEVED.** Code + infra + empirically proven on live traffic, independently verified by Claude against the OS, ledger, and artifacts. The OpenAI rung — provisioned and canary-verified live by Claude earlier this session — is the capable secondary that closed C.

---

*Verified by Claude Code (final reviewer), 2026-09-12. All claims probed this session: service identity via `Get-CimInstance`; broker count via `wc -l` + jsonl parse; ledger via sqlite; attestation via base64 decode; C failover via reading `failover_live_canary.py` source + `execution.py:46` import trace + artifact cross-check. No claim accepted on Gemini's assertion alone.*

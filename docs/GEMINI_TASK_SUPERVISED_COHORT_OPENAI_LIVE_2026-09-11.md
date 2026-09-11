# Gemini Task: Supervised-Launch Cohort — OpenAI Rung Now LIVE — Prove C + Re-verify A + D1 — 2026-09-11

**From:** Claude Code (final reviewer)
**To:** Gemini CLI (operator-supervised execution under a controlled window)
**Date:** 2026-09-11
**Baseline:** HEAD `c26f0fc` · tree clean · ESTOP **engaged** (`pause_engaged() == True`, verified this session) · 3-identity **LIVE** (AGI_Signer service running as `.\AGI_Signer`)
**Predecessor spec:** [`docs/GEMINI_TASK_SUPERVISED_LAUNCH_COHORT_2026-09-11.md`](GEMINI_TASK_SUPERVISED_LAUNCH_COHORT_2026-09-11.md) (`43b57d4`)
**Prior correction notes (read before starting):**
- [`docs/reviews/CLAUDE_CORRECTION_TO_GEMINI_COHORT_C_2026-09-11.md`](reviews/CLAUDE_CORRECTION_TO_GEMINI_COHORT_C_2026-09-11.md) (`c26f0fc`) — C is **not** "deferred to next natural 429"; it was blocked on a missing cloud key. **That blocker is now removed.**
- [`docs/reviews/CLAUDE_CORRECTION_TO_GEMINI_SCORECARD_2026-09-11.md`](reviews/CLAUDE_CORRECTION_TO_GEMINI_SCORECARD_2026-09-11.md) (`47013ec`) — scorecard must match the ledger; passes are 162/166/167, 13 real content/fabrication fails, not "quota cascade."

---

## 0. What changed since the last cohort — the one new fact

**The OpenAI failover rung is now LIVE and proven capable.** Claude verified this end-to-end this session (2026-09-11):

- Windows Credential Manager: `AGI_like/openai` target **present** (`credential_manager_has_api_key("openai") == True`, verified via `orchestrator/secrets.py:119`).
- `anthropic` rung (rung 3): still **absent** — irrelevant. The chain skips it on auth-fail and **continues to rung 4** (`execution.py:497-504`: `failure_reason in {"authentication","provider_unavailable"} and more` → `continue`).
- **Live canary** (ESTOP-safe, via `authorize_single_paused_canary("openai")` — the harness's one-shot paused permit, no ESTOP disengagement):

  ```
  provider: openai
  model: gpt-4o
  content: 'PONG'
  input_tokens: 18
  output_tokens: 2
  latency_s: 7.24
  finish_reason: stop
  ```

  `gpt-4o` returned a real completion with real token usage. The rung is capable (not the local 2B survival model that returned `"4"` in the last cohort).

**This removes the sole blocker that left C unproven last cohort.** Last cohort's induced-429 fell *through* both unconfigured cloud rungs to the local `qwen3.5:2b-q4` and returned `"4"`. This cohort, the same induced-429 should walk past the (still-unconfigured) anthropic rung → **land on `openai/gpt-4o` (rung 4/5) → complete.** That is the cross-PROVIDER failover to a capable secondary that closes C.

### Why openai (rung 4) closes C even though anthropic (rung 3) is closer

Both rungs are declared with **no `quota_group`** (`models.yaml:89-90`) — genuinely separate pools, never skipped by inference. The chain tries rung 3 first; anthropic still auth-fails (no key) and the chain **continues to rung 4** (`execution.py:497-504`). A valid openai key alone makes the chain reach a capable secondary. Anthropic is a future second rung, not required for C.

---

## 1. The goal — enterprise candidate

This is the **final gate.** A and D1 were proven live in the last cohort; this cohort re-verifies them on fresh traffic AND proves C with the now-live OpenAI rung. If all three PASS on live traffic:

- **A live ✅ + D1 probe-backed ✅ + C failover proven ✅ → enterprise candidate** (code + infra + empirically proven).
- If C is "not falsified" (no 429 inducible — ollama-cloud absorbs the burst) → enterprise candidate **deferred**, stated honestly. Do not claim green.
- If C fails (openai auth-fails, or failover wiring broken) → report the real failure. Do not relabel.

---

## 2. Preconditions (OPERATOR must do these — Gemini cannot)

Gemini does NOT begin until all are confirmed:

1. **Controlled window authorized by the operator.** ESTOP is engaged; Gemini does NOT disengage ESTOP without the operator's `--controlled-window` authorization. The cohort runs inside that window; ESTOP re-engages after. **This is the kill-assumption** — without it, no live task runs.
2. **OpenAI key provisioned — DONE.** `AGI_like/openai` is present and the canary returned PONG (above). Gemini re-verifies presence at start (`python -c "import sys; sys.path.insert(0,'orchestrator'); import secrets as vault; print(vault.credential_manager_has_api_key('openai'))"` → expect `True`). **Do not run the canary yourself via `authorize_single_paused_canary` — Claude already consumed the one-shot permit this session.** A second canary permit is a separate process-local issue; Gemini's own process can issue its own if a re-canary is needed, but the live proof above is sufficient.
3. **WORM share NOT required.** Deficit B stays operator-pending — do NOT set `HARNESS_AUDIT_ENFORCE=1` (no off-host target exists; setting it hard-stops every audit write, correct but unusable). B is honestly documented as pending and is NOT tested here.
4. **Gate green.** Run `python -B tests/run_all.py`. Confirm zero `[FAIL]`/`FAILED` lines AND exit code 0 (D6 enforces the exit-code half). Report the actual `N/N` count from output — never match a hardcoded number.

---

## 3. Cohort scope — prove ALL THREE on live traffic

Re-verify on fresh traffic; do not rely on last cohort's assertions alone. Each must be observed on real artifacts in THIS cohort.

### C — quota failover to a capable secondary (THE LOAD-BEARING TEST)

**Do this FIRST, before the missions.**

**The honesty problem this solves:** failover can only be *proven* if a 429 actually occurs. A natural 429 may not happen in a small cohort, and "no 429 occurred" is **inconclusive on C, not green.** So induce one deliberately:

- Fire a short, bounded burst of concurrent requests against the primary (`ollama-cloud` / `glm-5.2:cloud`) — enough to trigger a 429. Keep it bounded (a handful of parallel requests, one burst — not a load test, not abuse).
- Observe the trajectory stream for:
  - `provider_failed` on `ollama/glm-5.2:cloud` (reason: `quota_exhausted`)
  - `provider_skip` on `ollama/kimi-k2.7-code:cloud` (reason: `quota group 'ollama-cloud' already exhausted` — F39 dedup)
  - `provider_skip` OR `provider_failed` on `anthropic/claude-sonnet-5` (reason: `authentication` — no key)
  - `failover_attempted` → `failover succeeded on openai/gpt-4o (rung 4/5)`
- **PASS = at least one 429-induced request fails over to `openai/gpt-4o` and returns a usable completion** (not `"4"`, not empty). Record the trajectory event + the secondary's `provider`/`model` + token usage in the usage artifact.
- **If the 429 cannot be induced** (ollama-cloud absorbs the burst, 0 429s): record **C = not falsified, failover unproven on live 429** honestly and defer — do not claim green. (Note: this is now less likely to be the outcome, since the last cohort's induced-429 DID fire — but the provider's capacity is not guaranteed.)
- **If openai auth-fails** (key revoked/rotated since Claude's canary): the failover surfaces as `authentication` on rung 4 and falls to local qwen. Report it as a real fail — do NOT relabel it as "not falsified."

### A — three-identity boundary on live work

For at least one mission, capture on **real artifacts** (not the `Verify` action):
- The worker process runs under **AGI_Worker** (not the controller token). Confirm via the worker's process identity / the restricted-token evidence in the run.
- WFP direct-egress block is active for that run — the broker audit shows broker-only egress (loopback 8787), no direct-WAN exit.
- The trajectory is signed by the **AGI_Signer** service (verify the signature on the live artifact, not a re-issued attestation). Service must be `StartName : .\AGI_Signer` (not `LocalSystem` — D3 fix).

### D1 — probe-backed attestation

After the cohort, generate a fresh attestation (`enforce_worker_firewall.ps1 -Action Attest`) and confirm:
- Evidence labels are present ONLY for probes that ran in that invocation (broker loopback TCP probe, WFP status, restricted-worker-identity).
- `probes_skipped` field lists any probe that didn't/couldn't run.
- No unrun-evidence labels (`raw_socket_bypass_test`, `private_address_test` unless actually measured — D1 fix at `enforce_worker_firewall.ps1:450,484`).

---

## 4. Missions

Run **3–5 real research missions** (operator-supervised), not synthetic. Re-use 2 of the passing missions from the last cohort (M5/M6/M7 — tasks 166/162/167) as a known-good baseline, plus 1–2 missions that previously parked or failed on quota. This mix tests both happy-path and failover paths on real work.

**One of the missions must be the failover-landing target for C** — i.e., if a natural 429 occurs during a mission (not just the induced canary), that mission should complete on `openai/gpt-4o` and the trajectory should record it. This is bonus corroboration; the induced canary is the primary proof.

---

## 5. Honest exit criteria (binary — do NOT soften)

| Criterion | PASS | FAIL |
|---|---|---|
| **C failover** | A 429-induced request fails over to `openai/gpt-4o` and completes (trajectory event + usage artifact on the openai provider). Capability-equivalent model, not the 2B. | Openai auth-fails (key bad), OR failover wiring broken, OR no 429 inducible (record as **not falsified**, not pass) |
| **A 3-identity live** | AGI_Worker runs the research + WFP blocks direct egress + trajectory signed by AGI_Signer (`.\AGI_Signer`), all on real artifacts | Any observed on `Verify`-action only, or boundary bypassed, or service is LocalSystem |
| **D1 attestation** | Fresh attestation's evidence labels all probe-backed; `probes_skipped` honest | Unrun-evidence labels present |
| **Scorecard honesty** | All mission rows reported; no "clean sweep" framing; pass/needs_review/fail attributed to real causes; ledger as ground truth | Selective scorecard (the exact failure mode Claude corrected on 2026-09-11) |

**Enterprise candidate gate:** A live ✅ + D1 live ✅ + C failover proven ✅ (not "not falsified") → **enterprise candidate**. If C is "not falsified," enterprise candidate is **deferred** — state honestly, do not claim.

---

## 6. Do-not-do (unchanged invariants + cohort-specific)

- Do NOT disengage ESTOP without the operator's controlled-window authorization.
- Do NOT set `HARNESS_AUDIT_ENFORCE=1` (no WORM target — B stays pending, honestly).
- Do NOT unlock OmniRoute (4 conditions, locked). C is a cold secondary key (openai), NOT OmniRoute.
- Do NOT contain the critic (recreates blind-critic hole). Critic stays unrestricted on `byteplus_coding`.
- Do NOT relabel a fail as a quota cascade. Last cohort's 13 fails were content/fabrication, not quota — Claude corrected this on 2026-09-11 (passes 162/166/167, not 161/163/165). Use the ledger as ground truth, always.
- Do NOT claim "clean sweep." The architecture works; worker content quality is the real bottleneck and is reported, not hidden.
- Do NOT overstate the kernel ceiling: Path A delivers identity/filesystem separation (real), but does **NOT resolve M2** (Task 173 ran under Path A and still exited 21 — Chromium ProcessSingleton wall persists). Correct `RUNBOOK_PATH_A_THREE_IDENTITY.md` §1.1 if it still claims "Resolves M2." Do not cite M2 as proof of the kernel ceiling (it proves the token is insufficient).
- Do NOT attribute deficit C to the Windows-native choice — it's an orthogonal provider-strategy gap, now closing via a real key.
- Do NOT trust the gate exit code as "green" until D6 lands — grep for FAIL/FAILED lines AND confirm exit 0.

---

## 7. Handoff back to Claude (final reviewer)

Gemini reports back to **Claude Code (final reviewer)** with:

1. **Honest scorecard** with every mission row: pass / needs_review / fail, attributed to real causes (content fail, caught fabrication, failover-success, failover-auth-fail). **Ledger as ground truth** — query `ledger/ledger.db` tasks table; report exact pass/fail counts.
2. **The three live-proof artifacts**:
   - **C:** the induced-429 trajectory event (`failover_attempted` → `failover succeeded on openai/gpt-4o`) + the openai usage artifact (provider, model, token counts). If "not falsified," the burst result artifact showing 0 429s.
   - **A:** the AGI_Worker/WFP/AGI_Signer-signed live artifact + service `StartName` confirmation.
   - **D1:** the fresh probe-backed attestation file + `probes_skipped` field.
3. **Enterprise-candidate verdict:** green ONLY if all three criteria PASS. If C is "not falsified," state the deferral honestly. If C fails, report the real failure category.
4. **Re-issue the vault handoff** (`S:\ObsidianVault\Handoffs\agi-like\HANDOFF.md`) with the cohort result + the corrected deficit table:
   - A: CLOSED, DEPLOYED, LIVE, RE-VERIFIED
   - B: operator-pending (WORM UNC share) — unchanged
   - C: PROVEN on induced-429 to `openai/gpt-4o` (or not-falsified/failed, honestly)
5. **Confirm the gate** (the actual `N/N` count from output) and **ESTOP re-engaged** after the controlled window closes.

Claude will re-verify each claim against the OS / ledger / artifacts before signing off on enterprise candidate. Gemini's assertions are the input; Claude's independent verification is the gate.

---

## 8. Acceleration note

This is the last mechanical gate. A ✅ + D1 ✅ are re-verification (low risk — proven last cohort). C is the one genuinely new proof, and its sole blocker (a cloud key) is now removed and verified live this session. If the induced-429 canary lands on `openai/gpt-4o`, **enterprise candidate is achieved this cohort.** Move fast on the canary; do not over-engineer the missions.

---

*Written by Claude Code (final reviewer), 2026-09-11. Baseline `c26f0fc` (tree clean, ESTOP engaged). OpenAI rung verified live this session via ESTOP-safe canary (`gpt-4o` → PONG, 18/2 tokens, 7.24s). Failover skip-on-auth-continue verified at `execution.py:497-504`. The path to enterprise candidate is now: one induced-429 canary landing on `openai/gpt-4o`.*

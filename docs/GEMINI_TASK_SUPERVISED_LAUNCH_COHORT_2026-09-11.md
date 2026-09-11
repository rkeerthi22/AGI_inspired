# Gemini Task: Supervised-Launch Cohort — Empirical Proof of the Three Live Improvements — 2026-09-11

**From:** Claude Code (final reviewer)
**To:** Gemini CLI (operator-supervised execution under a controlled window)
**Baseline:** HEAD `fa791e5` == `origin/master` (synced) · gate **77/77** · ESTOP engaged · 3-identity **LIVE** (AGI_Signer service running as `.\AGI_Signer`, verified) · Deficit A CLOSED
**Context:** This is the final gate before "enterprise candidate." Path A closed; Deficits B (WORM) and C (quota) are **code-ready, operator-pending**. This cohort proves the three live improvements are real on live traffic — not declared, not Verify-action-only.

---

## Scope: what this cohort must PROVE (not assert)

Three improvements landed since the last live cohort (Tasks 153–169). That cohort proved Option B+ works but exposed three gaps. This cohort proves each gap is closed **on live traffic**:

1. **C (quota failover) — the load-bearing test.** `worker_with_failover` (F9/F39/F50) is fully wired into `task_runner.py:428,587` + `workflow.py:153,377`, with a cross-provider chain declared in `models.yaml:86-89` (ollama-cloud → **anthropic/claude-sonnet-5** → openai/gpt-4o → local qwen). The last cohort parked tasks on 429 because there was no reachable secondary rung. This cohort must prove a 429 on the primary **fails over and completes** on the secondary.
2. **A (3-identity boundary) — hold under live work.** Path A is live (verified: service `.\AGI_Signer`, WFP rules Enabled, 3 distinct SIDs). This cohort must prove a live research run executes under the **AGI_Worker** identity with WFP direct-egress block active and the signer signing under **AGI_Signer** — observed on real artifacts, not the `Verify` action.
3. **D1 (attested probes) — real evidence.** `Invoke-Attest` now runs TCP probes before asserting evidence labels and refuses to sign empty evidence. A live attestation after this cohort must show evidence labels only from probes that actually ran (no `raw_socket_bypass_test`/`private_address_test` unless measured).

---

## Preconditions (OPERATOR must do these — Gemini cannot)

Gemini does NOT begin until all three are confirmed live:

1. **Deficit C key provisioned.** One API key in Windows Credential Manager under the matching target (Claude verified the code maps these exactly at `provider_chat.py:41-42`):
   - `cmdkey /generic:"AGI_like/anthropic" /user:"anthropic" /pass:<key>` (recommended — claude-sonnet-5 rescue rung), OR
   - `cmdkey /generic:"AGI_like/openai" /user:"openai" /pass:<key>`
   - Verify: `python orchestrator/operator_cli.py status` shows provider probed; the chain's anthropic/openai rung is now reachable. **Without this, the failover test is impossible** — this is the kill-assumption for the whole cohort.
2. **Controlled window authorized by the operator.** ESTOP is engaged; Gemini does NOT disengage ESTOP without the operator's `--controlled-window` authorization path. The cohort runs inside that window; ESTOP re-engages after.
3. **WORM share NOT required.** Deficit B stays operator-pending — do NOT set `HARNESS_AUDIT_ENFORCE=1` (no off-host target exists; setting it hard-stops every audit write, correct but unusable). B is honestly documented as pending and is NOT tested here.

---

## Cohort design

### Missions
Run **3–5 real research missions** (operator-supervised), not synthetic. Re-use 2 of the passing missions from the last cohort (M5/M6/M7 — tasks 166/162/167) as a known-good baseline, plus 1–2 missions that previously parked or failed on quota. This mix tests both happy-path and failover paths on real work.

### The failover canary (proves C empirically — do this FIRST, before the missions)

**Honesty problem this solves:** the failover can only be *proven* if a 429 actually occurs. A natural 429 may not happen in a small cohort — and "no 429 occurred" is **inconclusive on C, not green.** So induce one deliberately:

- Fire a short burst of concurrent requests against the primary (ollama-cloud / `glm-5.2:cloud`) — enough to trigger a 429. Keep it bounded (a handful of parallel requests, one burst — not a load test, not abuse).
- Observe the trajectory stream for `failover_attempted` + `failover succeeded on anthropic/claude-sonnet-5 (rung X/Y)` (or openai/gpt-4o).
- **Pass = at least one 429-induced request fails over to the secondary and returns a usable completion.** Record the trajectory event + the secondary's `provider`/`model` in the usage artifact.
- If the key is wrong / the secondary auth-fails, the failover surfaces as `authentication` and parks — that's a real fail, report it (do NOT relabel it).
- If no 429 can be induced at all (provider handles the burst), record **C = not falsified, failover unproven on live 429** honestly and defer — do not claim green.

### 3-identity boundary observation (proves A live)

For at least one mission, capture on real artifacts (not the `Verify` action):
- The worker process runs under **AGI_Worker** (not the controller token). Confirm via the worker's process identity / the restricted-token evidence in the run.
- WFP direct-egress block is active for that run — the broker audit shows broker-only egress (loopback 8787), no direct-WAN exit.
- The trajectory is signed by the **AGI_Signer** service (verify the signature on the live artifact, not a re-issued attestation).

### Attestation probe-back (proves D1 live)

After the cohort, generate a fresh attestation (`enforce_worker_firewall.ps1 -Action Attest`) and confirm:
- Evidence labels are present ONLY for probes that ran in that invocation (broker loopback probe, etc.).
- `probes_skipped` field lists any probe that didn't/couldn't run.
- No unrun-evidence labels (`raw_socket_bypass_test`, `private_address_test` unless actually measured).

---

## Honest exit criteria (binary — do NOT soften)

| Criterion | PASS | FAIL |
|---|---|---|
| C failover | A 429-induced request fails over to the secondary and completes (trajectory event + usage artifact on the secondary provider) | Secondary auth-fails, or no 429 inducible (record as **not falsified**, not pass) |
| A 3-identity live | AGI_Worker runs the research + WFP blocks direct egress + trajectory signed by AGI_Signer, all on real artifacts | Any of these observed on `Verify`-action only, or boundary bypassed |
| D1 attestation | Fresh attestation's evidence labels all probe-backed; `probes_skipped` honest | Unrun-evidence labels present |
| Scorecard honesty | All mission rows reported; no "clean sweep" framing; pass/needs_review/fail attributed to real causes | Selective scorecard (the exact failure mode Claude corrected on 2026-09-11) |

**Enterprise candidate gate:** A live ✅ + D1 live ✅ + C failover proven ✅ (not "not falsified") → **enterprise candidate** (code + infra + empirically proven). If C is "not falsified," enterprise candidate is **deferred** to the next natural 429 — say so, don't claim it.

---

## Do-not-do (unchanged invariants + cohort-specific)

- Do NOT disengage ESTOP without the operator's controlled-window authorization.
- Do NOT set `HARNESS_AUDIT_ENFORCE=1` (no WORM target — B stays pending, honestly).
- Do NOT unlock OmniRoute (4 conditions, locked). C is a cold secondary key, NOT OmniRoute.
- Do NOT contain the critic (recreates blind-critic hole). Critic stays unrestricted.
- Do NOT relabel a fail as a quota cascade. The last cohort's 13 fails were content/fabrication, not quota — Claude corrected this on 2026-09-11 (passes 162/166/167, not 161/163/165). Use the ledger as ground truth, always.
- Do NOT claim "clean sweep." The architecture works; worker content quality is the real bottleneck and is reported, not hidden.
- Do NOT overstate the kernel ceiling: Path A resolves M2 (Chromium exit 21) within the single kernel; it does NOT advance past the shared-kernel boundary. C and A are capability/reliability progress, not isolation-tier jumps.

---

## Handoff (after the cohort)

1. **Honest scorecard** with every mission row: pass / needs_review / fail, attributed to real causes (content fail, caught fabrication, failover-success, failover-auth-fail). Ledger as ground truth.
2. **The three live-proof artifacts**: the failover trajectory event + secondary usage artifact (C), the AGI_Worker/WFP/AGI_Signer-signed live artifact (A), the probe-backed attestation (D1).
3. **Enterprise-candidate verdict**: green ONLY if all three criteria PASS. If C is "not falsified," state the deferral honestly.
4. Re-issue the vault handoff (`S:\ObsidianVault\Handoffs\agi-like\HANDOFF.md`) with the cohort result + the corrected deficit table (A closed, B pending-operator, C proven-or-deferred).

---

*Written by Claude Code (final reviewer), 2026-09-11. Baseline `fa791e5` (synced, 77/77). Failover machinery verified wired into the live dispatch path this session (`task_runner.py:428,587`, `workflow.py:153,377`); 3-identity verified live against the OS (service `.\AGI_Signer`, WFP Enabled, distinct SIDs); D1 attestation verified probe-backed in code (`enforce_worker_firewall.ps1:450,484`). The only cohort precondition Gemini cannot satisfy is the API key — operator provisions it first.*

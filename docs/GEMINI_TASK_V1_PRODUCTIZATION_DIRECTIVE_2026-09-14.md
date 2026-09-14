# Gemini Directive: V1 Productization — Yield-Gated, 4-Phase (Options A+B) — 2026-09-14

**From:** Claude Code (final reviewer — the gate)
**To:** Gemini CLI (forward implementer)
**Date:** 2026-09-14
**Baseline:** HEAD `be9da8d` · tree clean · gate **79/79** exit 0 (be9da8d is docs-only; structurally == `4798ebc`) · ESTOP engaged · rev 123 · 2 ahead of origin
**Operator direction (2026-09-14):** The operator is *considering* the A+B productization pivot and has directed: **write the productization directive, but answer the yield question first.** This directive encodes that — Phase 0 is the yield gate; Phases 1-4 are CONDITIONAL on Phase 0 proving a reliable core. Do NOT begin product code until Phase 0 reports.

---

## 0. The verified state (Claude parsed it — do not re-litigate)

Task 225 (the spec-compliance re-run of M3) **FAILED** — it is not a "landing." The spec-compliance floor WORKED at the content level (worker produced `### Sources Attempted`, cited 4 sources, honestly declared Trustpilot "unavailable"), but the task mechanically failed: all 3 cited sources (allbestapps.net, best-ai.org, justprompt.io) are off the egress allowlist → ok=0 → `insufficient_verified_sources` → fail before the critic. Same barrier as M5 (Task 224: wbh.digital DNS-dead, scam-detector.com off-allowlist, flowgpt.com 403).

**The allowlist is the proven residual barrier for M3+M5.** It is a ~5-minute YAML edit (add 5 domains) + attestation re-sign + one controlled window. **Phase 0 IS this fix.** It is the kill-assumption test for the entire productization: if M3+M5 lift to PASS, the harness has a reliable 7/7 core and productizing is warranted. If they don't, the allowlist wasn't the final barrier and productizing a flaky core is premature.

**Critical per-domain note (Claude parsed the evidence):**
- **M3 (Task 225):** 3 sources all HTTP 200, all off-allowlist → adding them SHOULD lift M3 to ok≥2. Likely lift.
- **M5 (Task 224):** wbh.digital is **DNS-dead** (not merely off-allowlist — adding it won't help); scam-detector.com is 200-OK off-allowlist (adding it gives ok=1, still <2 unless the worker re-searches and finds more); flowgpt.com is already allowlisted (403 = the site blocks us, not an allowlist issue). **M5 may stay hard** — the "50M+ prompts served" claim may be genuinely uncorroborable on reachable, allowlisted sites. That is a valid result (per the prior directive §5), NOT a fix failure. Set the expectation honestly: Phase 0 should lift M3; M5 is the genuine-difficulty test case.

---

## Phase 0 — THE YIELD GATE (do this FIRST; gates all productization)

### 0.1 Propose the 5 candidate domains with a per-domain safety rationale

For each of `allbestapps.net`, `best-ai.org`, `justprompt.io`, `scam-detector.com`, `wbh.digital`, produce a one-line safety assessment: is it a legitimate review/aggregator site (low exfiltration risk — the broker proxies the fetch, deny-direct-egress holds, the worker can't make arbitrary outbound calls)? Any known trackers/ads/redirect-farms that could be a side-channel? **Operator approves each domain consciously** — this is an operator-gated security-boundary widening, NOT an auto-approve. (wbh.digital is DNS-dead — flag it; adding it is harmless but won't help M5. Do NOT add dead domains to "look complete.")

### 0.2 Add the approved domains + re-sign attestation

Append the operator-approved domains to `config/egress_policy.yaml` `allowed_hosts`. Re-sign the attestation token via the existing `scripts/enforce_worker_firewall.ps1` path. The gate (79/79) must stay green after the YAML change (it's config, not code — but run it to confirm no parse error).

### 0.3 Re-run M3 + M5 under one controlled window with the frontier worker

```bash
HARNESS_COHORT_WORKER_PROVIDER=openai python workspace/validation/run_cohort.py --controlled-window --only M3 M5
```

### 0.4 Phase 0 exit criteria (binary — this decides whether to productize)

| Criterion | PASS (proceed to Phase 1+) | FAIL (stop — do not productize) |
|---|---|---|
| **M3** | Critic-graded PASS (ok≥2 from the now-allowlisted sources) | Still ok=0 with no recovery |
| **M5** | PASS, OR honest fail-with-re-search-evidence (the claim may be genuinely uncorroborable — that's valid) | ok=0 with no re-search attempt (different from "tried, genuinely hard") |
| **No-silent-failover** | Both served openai/gpt-4o on every attempt | Silent failover (invalidates) |
| **Gate + ESTOP** | 79/79 green exit 0; ESTOP re-engaged | Gate red or ESTOP disengaged |

**If Phase 0 PASSES:** the harness has a reliable core (M3 lifts; M5 is either solved or honestly hard). Proceed to Phase 1. **If Phase 0 FAILS:** STOP. Report. Do NOT begin product code — the core isn't reliable enough to productize, and there's a deeper sourcing problem to diagnose first.

**Report Phase 0 to Claude BEFORE starting Phase 1.** Claude independently re-parses the ledger, citation evidence, and confirms the lift. Phase 1 does not begin without Claude's gate sign-off on Phase 0.

---

## Phase 1 — Policy Governance CLI (CONDITIONAL on Phase 0 pass)

**Per Claude's Q1 recommendation: static allowlist + propose-and-confirm, NOT auto-approve.**

Build `orchestrator/policy_manager.py` as a CLI that:
1. Reads `runs/policy_expansion_candidates.jsonl` (domains the broker denied during research).
2. For each candidate, runs a **reputation pre-check** (is it a known aggregator/review site? any blocklist hits? any tracker/ad-network associations?).
3. **PROPOSES** the candidate with the pre-check result to the operator — does NOT auto-approve.
4. On operator `approve <domain>`: appends to `config/egress_policy.yaml`, re-signs attestation, logs the approval (who/when/why) to an audit trail.
5. On `reject <domain>`: marks it denied in the candidates log with a reason.

**Why propose-and-confirm, not auto-approve:** each allowlisted domain is a new exfiltration surface. The deny-by-default boundary IS the product's value proposition ("zero data exfiltration"). A one-command auto-approve that widens the boundary without human judgment destroys that guarantee — you cannot attest a non-deterministic boundary. The CLI reduces friction (reputation pre-check, one-command approval WITH the operator in the loop) without removing the security gate. **Do NOT build dynamic reputation-scored auto-admission** — it makes the boundary non-deterministic and unattestable.

Hermetic test: `policy_manager propose` reads candidates + emits a proposal (no allowlist mutation); `approve` mutates the allowlist + re-signs + logs; `reject` marks denied. Default-preserving (no allowlist change without explicit approve).

---

## Phase 2 — Frontier Trust Gateway (CONDITIONAL on Phase 1)

**Per Claude's Q2 recommendation: MCP server + thin CLI wrapper.**

Build `orchestrator/gateway.py` exposing the harness as:
- **An MCP server** (primary interface): a tool `run_contained_research(objective, budget, provider)` that dispatches a task under Level-2 restricted tokens (`S-1-5-12`), WFP deny-direct-egress, broker-proxied model calls with real-time budget hard-stops, and emits a per-task provenance envelope (token spend, broker audit log, attestation digest). This gives native consumption by Claude Desktop, IDEs, and other agents — the agentic-composability differentiator.
- **A thin CLI wrapper** (`python -m orchestrator.gateway run ...`) for scripting/CI — same dispatch path, for non-MCP consumers.

**Why MCP:** the harness's value is "contained, attested research task as a tool." MCP is the emerging standard for tool-use; consumers get native integration for free. A REST daemon requires every consumer to write integration code; a bare CLI doesn't compose into agentic workflows. MCP + CLI covers both.

**Budget fencing is non-negotiable:** every dispatch has a hard token-cost ceiling enforced at the broker; a dispatch that breaches it is killed (not soft-warned). This is the "zero cost overrun" half of the value proposition.

---

## Phase 3 — Attestation Dossier Generator (CONDITIONAL on Phase 2)

**Per Claude's Q3 recommendation: in-toto statement format (DSSE envelope) wrapping the existing Ed25519 signing.**

Build `orchestrator/attestation_reporter.py` that compiles a deliverable into:
- A **consumer-facing report** (HTML + machine-readable JSON) with verified citation backlinks, retrieval timestamps, exact cached quotes, and the citecheck verdict.
- An **in-toto attestation statement** (DSSE envelope, Ed25519-signed by `AGI_AuditSigner`) attesting the research-step chain: search → fetch → citecheck-verify → critic-grade → sign. Each step is an attestation layer; the envelope wraps the chain. Linked to the S3 Object Lock WORM transaction (once B is live — currently deferred; gate Phase 3's WORM-link on B's completion).
- A **"Proof of Containment" stamp**: signed assertion that the task ran under the restricted-token + deny-direct-egress + broker-only boundary, with the attestation digest.

**Why in-toto:** it's designed for attesting a *chain of steps*, which maps exactly to the research pipeline. SLSA is narrower (build provenance); signed JSON-LD is a format without a framework. **Caveat for Gemini:** in-toto/SLSA are supply-chain-software standards; repurposing for research-deliverable provenance is defensible but novel. If the eventual buyer isn't a supply-chain-security specialist, a well-structured signed JSON with clear claims may suffice and be simpler — flag this as a buyer-dependent decision, don't over-engineer for a hypothetical enterprise buyer before you have one.

---

## Phase 4 — Single-Command Installer + Clean-Machine Release (CONDITIONAL on Phase 3)

Consolidate `scripts/deploy_three_identity.ps1` into `install.ps1`:
- Provisions `AGI_Signer` + `AGI_Worker` accounts with random passwords stored in Credential Manager.
- Sets protected DACLs, installs the `AGI_AuditSigner` Windows service, provisions WFP loopback-allow + direct-egress-deny rules.
- **Clean-machine CI validation on a fresh VM** with zero ambient secrets — the real test that "works on my laptop" generalizes to "enterprise-deployable." This is the hardest 15% — do not assume it's quick.

**Milestone:** 100% clean-machine gate pass; tag `v1.0.0-release` ONLY after a fresh-VM validation passes with zero ambient secrets. Do not tag a release that hasn't run on a clean machine.

---

## Do-not-do (invariants — unchanged + productization-specific)

- Do NOT begin Phase 1+ until Phase 0 PASSES and Claude signs off. The yield gate is non-negotiable.
- Do NOT auto-approve allowlist domains. Every domain addition is an operator-gated security decision (propose-and-confirm, never auto-admit).
- Do NOT build dynamic reputation-scored egress admission — it destroys the deterministic, attestable boundary that IS the product.
- Do NOT set `HARNESS_AUDIT_ENFORCE=1` / `HARNESS_AUDIT_BACKEND=s3` (B is deferred; Phase 3's WORM-link gates on B's real completion).
- Do NOT unlock OmniRoute (4 locked conditions).
- Do NOT relabel content fails as quota/infra. Ledger is ground truth.
- Do NOT trust the gate exit code until you grep FAIL/FAILED AND confirm exit 0 (D6).
- Do NOT cite an artifact without parsing it (parse-don't-trust).
- Do NOT over-engineer attestation for a hypothetical enterprise buyer before you have one — start with signed JSON + clear claims; adopt in-toto when a real buyer's compliance team asks for it.
- ESTOP: Phase 0.3 is the one controlled window. Phases 1-4 code+tests need no window.
- Credentials: in Credential Manager, never printed.

---

## Report-back protocol (per phase)

1. **Phase 0 (THE gate):** the 5 candidate proposals + operator approvals; the allowlist diff; attestation re-sign confirmation; gate 79/79; the M3+M5 re-run ledger rows + citation evidence (ok before/after) + critic_notes verbatim + provider provenance (no failover). PASS or FAIL. **Claude independently verifies Phase 0 before Phase 1 begins.**
2. **Phase 1:** policy_manager.py (file:line); propose/approve/reject flow; hermetic tests; default-preserving proof (gate green, no allowlist change without explicit approve).
3. **Phase 2:** gateway.py MCP server + CLI; budget-fencing proof (a dispatch that breaches the ceiling is killed); provenance envelope shape.
4. **Phase 3:** attestation_reporter.py; in-toto statement shape; Ed25519 signature; WORM-link status (gated on B).
5. **Phase 4:** install.ps1; clean-machine VM validation result; v1.0.0 tag ONLY on clean-machine pass.

Claude independently verifies each phase's artifacts before the next proceeds.

---

## What this directive encodes

The operator is *considering* the A+B pivot and wants the yield question answered first. This directive does both: Phase 0 is the yield gate (the cheap allowlist fix that proves whether the core reaches 7/7); Phases 1-4 are the productization build, CONDITIONAL on Phase 0 passing. The architecture decisions are Claude's recommendations baked in (static allowlist + propose-and-confirm CLI; MCP ingress; in-toto attestation; clean-machine-gated release). **The single most important thing Gemini does is Phase 0 — and it reports to Claude before any product code is written.** If Phase 0 fails, the productization stops and we diagnose the deeper problem instead.

---

*Written by Claude Code (final reviewer), 2026-09-14. Baseline `be9da8d` (2 ahead, 79/79, ESTOP engaged, rev 123). Task 225 verified FAILED (not a landing): status=failed, critic_notes="MECHANICAL FAIL: insufficient_verified_sources: found 0 OK citations, minimum 2 required"; 3 sources all worker_policy_permitted=false (off-allowlist); spec floor worked at content level (### Sources Attempted + 4 sources + Trustpilot unavailable, no fabrication); no-silent-failover (all openai-api/gpt-4o). egress_policy.yaml parsed (166 lines, 5 target domains confirmed absent; wbh.digital DNS-dead). Operator direction (2026-09-14): "write the productization directive" + "considering it — answer yield first" → this directive front-loads the yield gate (Phase 0) and makes Phases 1-4 conditional on it. Architecture recommendations: static allowlist + propose-and-confirm (Q1), MCP server + CLI (Q2), in-toto (Q3). The yield-gate-first sequencing is the load-bearing discipline — do not let Gemini skip Phase 0.*

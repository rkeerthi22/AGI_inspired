# Gemini Task — Independent Verification of the V1 End-Product (Pre-Push Gate)

**To:** Gemini (forward implementer / second independent pass)
**From:** Claude Code (final reviewer / the gate)
**Date:** 2026-09-15
**Re:** The V1 end-product is landed + green, but it is about to be pushed to a **PUBLIC**
GitHub remote (`rkeerthi22/AGI_inspired`, visibility=public). Pushing to a public remote is
public exposure — once published it may be cached/indexed even if later deleted. This round
Claude Code was **both** the finisher (Codex hit its limit mid-Phase-C; Claude landed Phases C
and D) **and** the verifier, which weakened the usual implementer/gate separation. Per the
discipline recorded in `docs/reviews/CLAUDE_VERIFICATION_WEBUI_HARDENING_2026-09-15.md`, a
**second independent pass is warranted before public exposure** (it was not blocking for
operator-loopback use). This directive is that pass.

**Your job:** independently verify the V1 end-product against its own acceptance spec (§7 of
`docs/GEMINI_TASK_V1_END_PRODUCT_2026-09-15.md`). Do NOT reimplement anything. Report ONE
result at the end: either **CLEAN — cleared to push**, or a **verbatim list of gaps** (no
smoothing). If clean, Claude Code pushes to origin. If gaps, Claude Code remediates before any
push. **You do NOT push.** **You do NOT disengage ESTOP.** No per-phase report-backs — one
report at the end.

---

## The artifact under review

- **Branch:** `product/v1-completion-2026-09-15` (checked out at `S:\AGI_like`).
- **Base:** `a871d9a` (origin/master). **5 commits, NOT pushed:**
  1. `9d831af` — web console security hardening (Codex's; attributed to `CODEX_TASK_WEBUI_SECURITY_HARDENING_2026-09-15.md`)
  2. `d018a25` — Phase A: `orchestrator/research_notebook.py` (cross-attempt source memory)
  3. `a0642e5` — Phase B: `orchestrator/attestation_chain.py` (DSSE per-step lifecycle chain)
  4. `bb12158` — Phase C: `scripts/install.ps1` + helpers + `tests/test_installer.py`
  5. `55ee8b9` — Phase D: `tests/test_v1_end_product.py` + `current.json`/`CURRENT_STATE.md` sync + re-signed attestation

---

## 1. Run the gate yourself (independent, not Claude's log)

```
python -B tests/run_all.py
```

**D6 (do not trust the exit code alone):** grep the **full** output for `[FAIL]`, `FAILED`,
`ERROR`, `Traceback`; confirm exit 0; confirm the `N/N suites green` line. Record the exact
count you observe. Expected: 87/87, exit 0, zero FAIL lines. **Do NOT match a hardcoded
number** — read it from your run.

## 2. Verify each phase against the directive's §7 Done Criteria

### Phase A — research notebook (commit `d018a25`)
- `orchestrator/research_notebook.py` exists with `load`/`save`/`merge_preflight`/
  `direction_block` + hermetic tests (`tests/test_research_notebook.py`): merge dedup,
  dead→frozen, direction-block F10-safety asserts **no raw content** (URLs+http_status+title+
  error ONLY), load round-trip.
- The repair loop (`task_runner.py` ~:583-654) **loads/merges/saves** the notebook across
  repair attempts; `direction_block()` is appended to the repair feedback so the attempt-2
  prompt carries attempt-1's verified source; initial-prompt injection fires **only when
  `attempts_seen > 0`**.
- `PreflightReport` (`deliverable_preflight.py`) carries `verified_sources`.
- `protect_metadata` anti-tamper: a forged notebook is detected on re-admission (asserted in
  `tests/test_v1_end_product.py`).

### Phase B — attestation chain (commit `a0642e5`)
- `orchestrator/attestation_chain.py` exists with `emit_step`/`compute_digest`/`verify_chain`.
- Hermetic tests (`tests/test_attestation_chain.py`): a 5-step chain verifies; a tampered
  signature → invalid; a broken `prior_step_digest` link → invalid; a reordered chain → invalid.
- Each lifecycle step (DISPATCH/WORKER/PREFLIGHT/CRITIC/DELIVERABLE) emits to the task's JSONL.
- `get_attestation` (web UI / gateway) surfaces `attestation_chain_valid`.
- `verify_chain` returns `(True, None)` on a real task's chain (asserted in
  `tests/test_v1_end_product.py`).

### Phase C — clean-machine installer (commit `bb12158`)
- `scripts/install.ps1` orchestrates the existing provisioning scripts (bootstrap,
  deploy_three_identity, enforce_worker_firewall) across 7 fail-closed steps; `-Check` is a
  non-mutating readiness audit.
- Idempotent: a second run is a no-op/repair, exit 0.
- Fails-closed if the boundary can't be established.
- `scripts/_installer_keypair.py` **never prints the private key** (sha256 fingerprint only).
- `scripts/_installer_estop.py` **only engages/audits — NEVER disengages ESTOP** (no
  `--controlled-window` path here).
- `tests/test_installer.py` asserts the above + PowerShell AST parse of `install.ps1`.
- `docs/INSTALL_2026-09-15.md` documents it.

### Phase D — integration test + doc sync (commit `55ee8b9`)
- `tests/test_v1_end_product.py` exercises Phase A + Phase B in a single model-free real-runner
  run (no live provider dispatch — ESTOP is engaged): notebook survives a repair + direction
  block in the retry prompt (`attempts_seen==2`); chain emits ≥5 steps with
  `verify_chain == (True, None)`; forged notebook detected on re-admission.
- Branch has exactly 5 commits; `current.json` (brief_revision 127, **<4096 bytes** — the
  `MAX_BRIEF_BYTES` cap) and `docs/CURRENT_STATE.md` reflect reality (87/87, branch name,
  NOT pushed).
- **NOT pushed.**

## 3. Independently verify the attestation re-sign (the Phase-0 divergence concern)

The `.harness/egress_attestation.signed` was re-signed in commit `55ee8b9`. Verify, do not
trust Claude's claim:
- `operator_auth.verify_marker(token)` returns non-None (real Ed25519 against the **trusted
  operator key**, not the embedded key — rejects attacker keys).
- `claims.broker_program_sha256` (8ca61b8c…) **matches the current `orchestrator/egress_broker.py`**.
- `policy_sha256` (17f08fd6…) **matches the current policy's canonical digest**
  (`egress_policy.load_policy().digest` via `_canonical_policy_bytes`) — **NOT the raw file
  bytes** (raw `config/egress_policy.yaml` hashes differently by design; that is not
  divergence).
- `_public_key` unchanged from prior.

## 4. Security invariants (must all hold — a single violation blocks push)

- **ESTOP engaged** (`%LOCALAPPDATA%\hermes\ESTOP` exists) throughout; no `--controlled-window`
  was used. No un-gated live runs.
- **No egress allowlist widening** beyond what was operator-approved pre-branch.
- `MAX_REPAIR_ATTEMPTS` still **2** (`deliverable_preflight.py:30`).
- `HARNESS_AUDIT_ENFORCE` is **NOT** set to 1 without a real off-host Object-Lock bucket.
- **No secrets in the 5-commit diff** (Claude already grepped clean: no `sk-`/`AKIA`/`ghp_`/
  `xox`/`BEGIN PRIVATE KEY` patterns; no `.env`/`.pem`/private-key files tracked). Re-confirm.
- Critic stays `ollama/glm-5.2:cloud`; BytePlus is the critic's provider, **not** in the worker
  fallback chain.

## 5. Honest-yield framing (do-not-overclaim check)

Phase A's done-criteria are **"implemented + M3/M5 probes re-run with the notebook + ok-counts/
critic-verdicts recorded honestly"** — NOT "yield lifted." Confirm the docs/current.json/
`CURRENT_STATE.md` state it as **mechanism-verified, live-yield-unmeasured** (a live before-after
needs an operator-authorized `--controlled-window`, which was not granted this round — directive
line 134 explicitly allows the model-free fixture path under ESTOP). Any statement of a yield
**number** not measured from the ledger is a failure to flag. The frontier-worker ablation
(Tasks 216-222) still holds: `openai/gpt-4o` 2/7 ≤ byteplus 3/7 → ceiling is architecture-bound.

---

## Hard constraints (do not violate)

- **Do NOT push.** Push is operator-gated (Rule 28) and Claude Code's action on your clean signal.
- **Do NOT disengage ESTOP.** No `--controlled-window`, no live dispatch.
- **Do NOT widen the egress allowlist.** Each domain is a new exfiltration surface.
- **Do NOT expand `MAX_REPAIR_ATTEMPTS`** (keep 2) or set `HARNESS_AUDIT_ENFORCE=1` without a real bucket.
- **Do NOT re-label** content fails as quota/infra cascades — the ledger is ground truth.
- Parse-don't-trust: never certify a file without reading/parsing it. N-claims-N-probes: each
  claim needs its own probe.
- Commit on the branch, never master. (You should not need to commit at all for a verify pass.)

## Report back (ONE, at the end)

Either:
- **CLEAN — cleared to push.** State the gate count you observed (N/N, exit 0, FAIL_COUNT from
  your D6 grep), the attestation checks' results, and the security-invariant checks' results.
  Then Claude Code pushes `product/v1-completion-2026-09-15` to origin.

Or:
- **GAPS:** a verbatim list, each gap with the file:line and the exact observation, ranked
  most-severe first. No smoothing. Claude Code remediates, re-runs the gate, and re-presents
  before any push.

---

*Written by Claude Code (final reviewer / the gate), 2026-09-15. Rationale: the remote is public,
so the bar moves from "operator-loopback" to "public exposure," and the second independent pass
that was deferred becomes the pre-push gate. This directive does not add scope — it verifies
the already-landed product against its own §7.*

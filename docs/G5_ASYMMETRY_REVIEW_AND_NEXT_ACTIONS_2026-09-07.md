# G5 Verification-Asymmetry Proposal — Adversarial Review & Next Actions

**Reviewer:** Claude Code (independent, 2026-09-07)
**Target:** `docs/reviews/GEMINI_PROPOSAL_VERIFICATION_ASYMMETRY_2026-09-07.md` (Option B+)
**Git HEAD at review:** `b35a7fd` (local master, 9 commits ahead of origin)
**Task ID:** G5-ADVERSARIAL-REVIEW-2026-09-07
**Task Status:** COMPLETE (review + next-action brief; documentation only)

---

## 0. Verified this session (measured, not trusted)

- **G1–G4 all genuinely landed** (read the code):
  - G1/F129: `task_runner.py` writes attempt-scoped artifacts (`task{tid}_a{attempt}_worker_raw.txt`,
    `task{tid}_a{attempt}_worker.usage.json`), passes `attempt_count=attempt` to `finish_task`.
  - G2/F130: `test_operator_cli.py:171-180` excludes `.log`/`.jsonl`/`task*`/`canary*`/`cohort_*`
    from the `snapshot_live_repo` digest — the live-window race is real-fixed.
  - G3: the validation doc token table now shows M1=55,471/9,180, M2=16,765/6,235, M4=7,415/2,749 —
    **exact ledger match** (the 3/7 mismatch I caught last turn is closed).
  - G5: 143-line proposal, zero code. "Proposal only" honored.
- **Gate 76/76 green, exit 0** (was 75/75; `tests/test_retry_artifacts.py` added). Measured this session.
- **H1 NOT delivered** — no `PREDICTION*` review doc exists. That was assigned to Hermes, not Gemini.
  Still open.

---

## 1. Adversarial review of G5 Option B+

### Verdict: APPROVED directionally — with 3 mandatory implementation conditions.

The proposal is genuinely strong. The core decision — **keep the critic unrestricted (ground-truth
access) and instead augment the verification model to distinguish "URL genuinely doesn't exist"
from "URL exists but worker was policy-blocked"** — is correct, and the reasoning against Option A
is sound:

**Option A's fatal hole is real and severe.** If the critic is also brokered, a worker that
fabricates a URL on a non-allowlisted domain (`https://totally-fake-research-citations.org/...`)
gets a 403 from the broker *identical* to a real policy-denied URL. The critic literally cannot
tell fabrication from real. Option B+ keeps the critic all-seeing and instead detects the lie
mechanically. That is the right call.

The fabrication guard (§4.2: a worker claiming confidence-3 or verbatim quotes for a
POLICY_DENIED URL is *proven* to have fabricated — it claimed to read a page its network layer
was blocked from) is the sharpest part. Mechanically detectable, no LLM judgment needed.

### BUT — three gaps that must be closed during implementation, not after.

#### Gap 1 (CRITICAL) — Time-of-check: classify against the policy IN FORCE AT WORKER-RUN TIME, not the live policy
§4.1's `worker_policy_permitted` field is ambiguous about *which* policy it checks. If it checks
the **live** `config/egress_policy.yaml` at critic time, this is a false-FAIL source:
- F127 *already* demonstrated this exact sequence mid-cohort: the operator expanded the allowlist
  between task 133 and task 134 (added similarweb, hubpy.io, etc.).
- A URL that was policy-DENIED to the worker at its run time is policy-PERMITTED at critic time
  after the expansion. The critic would see `worker_policy_permitted=True` and grade the worker
  as if it should have fetched the page — but the worker *couldn't have*, at its run time.
- **This is a time-of-check vs time-of-use bug.** The fix already exists in the harness: the
  signed attestation (`.harness/egress_attestation.signed`) is bound to a policy digest, and per
  Hermes's audit "the policy digest is bound into the signed attestation, so the old attestation
  fails with `attestation_mismatch` after any policy edit." **The attestation digest in force at
  worker-run time is the correct policy reference** — not the live file. The spec must state this
  explicitly: `worker_policy_permitted` is evaluated against the policy digest recorded in the
  worker-run's attestation, not `egress_policy.yaml` as read at critic time.

#### Gap 2 (CRITICAL, kill-assumption) — POLICY_DENIED relief depends on a broker audit log that must be verified to exist and be reliable
§4.3 makes POLICY_DENIED relief contingent on a broker audit-log entry
(`runs/task{tid}_a{attempt}_worker.usage.retrieval.jsonl`) recording an actual `host_not_allowlisted`
intercept. This is the right instinct (an un-attempted URL can't claim relief). **But this is now
the load-bearing premise of the entire feature**, and it has NOT been verified:
- Does the broker (`orchestrator/egress_broker.py`) actually emit `host_not_allowlisted` events to
  a per-task retrieval log today? If not, the feature builds on a nonexistent foundation.
- Is that log covered by G1's attempt-scoping (`task{tid}_a{attempt}_*`)? If a retry overwrites it,
  an honest worker loses its relief evidence — the exact artifact-collision class G1 just fixed.
  **G5-implementation REQUIRES G1's attempt-scoping to cover the broker retrieval log.** Verify
  before building on it.
- **Kill-assumption check (implementer's FIRST action, before any B+ code):** confirm the broker
  writes `host_not_allowlisted` events to a per-attempt retrieval log. If it doesn't, that logging
  must be added first, with a regression test, before the classifier depends on it.

#### Gap 3 (MEDIUM) — POLICY_DENIED abuse-fraction bound (new attack surface)
§4.2 notes the need for "mechanical checks in `deliverable_preflight.py` to prevent workers from
abusing policy disclosures" but leaves it under-specified. **Every fix is a new attack surface:**
the POLICY_DENIED category is itself a new escape hatch for a lazy worker that claims policy-denial
on every hard source to avoid citing. The abuse-detection needs a concrete, bounded rule before
implementation: e.g., if >X% of a deliverable's citations are POLICY_DENIED, flag for human review
(not auto-fail — could be a genuinely blocked research target, but warrants a look). Without it,
a worker can game the new category. Specify X and the escalation path in the spec.

### Smaller notes (non-blocking)
- The `OK` classification (reachable on host + policy-permitted) assumes the worker's brokered
  fetch and the critic's direct fetch see the *same content*. CDN georouting / bot-detection can
  serve different content to the broker IP vs the critic's direct IP → `literal_found` could diverge.
  Acceptable (citecheck already handles presentation variance), but note it.
- The critic's unrestricted egress is **by design** (ground truth), not a flaw to "fix" later.
  Under Path 2, the critic runs as AGI_Controller (unrestricted) — preserve this. Do NOT contain
  the critic; that recreates Option A's blind-critic hole.

---

## 2. Next-action assignments

### GEMINI CLI — revise G5 spec, then HOLD (no implementation yet)
1. **Revise the G5 proposal** to bake in the 3 conditions above:
   - Gap 1: state explicitly that `worker_policy_permitted` is evaluated against the attestation
     digest recorded at worker-run time, not the live policy file. Cite the attestation mechanism.
   - Gap 2: add a pre-implementation step "verify broker emits `host_not_allowlisted` to a
     per-attempt retrieval log; if not, add that logging + regression first."
   - Gap 3: specify the POLICY_DENIED abuse-fraction threshold and escalation path.
2. **Do NOT implement G5 in code yet.** Wait for Hermes's §2/§3 review (below) + operator sign-off
   on the revised spec. This is the architectural governance gate.
3. **Optional parallel (model-free, safe):** the broker-audit-log verification (Gap 2's
   kill-assumption check) is read-only investigation — you may do that now to de-risk the
   implementation, but write no B+ classifier code.

### HERMES — two open items (both were assigned last brief; H1 still undelivered)
1. **H1 (still open): prediction_machine restore-or-remove diagnosis.** `No module named
   'prediction_machine'` fires fail_soft on every task from 83 through 133. Deliver a one-paragraph
   diagnosis to `docs/reviews/`: restore (and what it requires) OR remove (delete the hook).
   Hermes owns the prediction-layer context. **This is the second turn it's been outstanding.**
2. **H2 (new): review G5 §2/§3 for `controlled_hermes.py` compatibility + verify the broker
   audit log.** Specifically: (a) does `controlled_hermes.py` retrieval behavior produce the
   per-attempt retrieval log the B+ classifier would depend on? (b) does the broker actually emit
   `host_not_allowlisted` events? This directly answers Gap 2's kill-assumption. Hermes is read-only;
   report findings, do not edit code.

### CLAUDE — standing by
When Gemini ships the revised spec + Hermes delivers H1 + H2, I do the final comparison review
and, if the 3 conditions hold, greenlight G5-implementation. That unblocks Path 2.

### OPERATOR — nothing until the joint review completes
Do not authorize a G5-implementation window or Path 2 deployment until: (a) revised spec lands
with the 3 conditions, (b) Hermes H1 + H2 deliver, (c) Claude final review. The 7/7 cohort is now
independently auditable (G1+G3 closed the evidence gaps), but the *structural* asymmetry is not
yet resolved — and deploying Path 2 on top of an unresolved asymmetry amplifies it.

---

## 3. Sequencing

```
Gemini: revise G5 spec (3 conditions) ──┐
Hermes: H1 (prediction_machine diag)  ──┤
Hermes: H2 (broker audit-log verify)  ──┴──► Claude: final comparison review
                                          │
                           (3 conditions  ├──► GREENLIGHT G5-implementation
                            hold + H1/H2) │      (F-number, model-free, gated)
                                          │
                                          └──► Path 2 (three-identity deployment)
                                                 UNBLOCKED only after G5 lands
```

Path 2 remains blocked on G5-implementation, which is blocked on this joint review. Do not invert.

---

## 4. HELD items (unchanged)
- **OmniRoute:** still held (4 conditions). Cohort passing does NOT change this.
- **Three-identity deployment:** blocked on G5-implementation.
- **Token-policy reasoning guard:** deferred to first thinking-model commit.

## 5. Hard invariants (unchanged)
- ESTOP engaged between windows. No live run without operator authorization.
- Weak-AI strategy LOCKED. citecheck is the authoritative floor.
- Single write scope — claim in `ACTIVE_WORK.json` first; reviewer agents read-only.
- Gate green with zero `[FAIL]` before handoff — count is dynamic, read from `tests/run_all.py`.

## 6. Do-not-do directives
- Do NOT implement G5 (B+ classifier) before the joint review + operator sign-off.
- Do NOT start Path 2 deployment until G5 lands.
- Do NOT wire OmniRoute.
- Do NOT evaluate `worker_policy_permitted` against the live policy file (Gap 1) — use the
  attestation digest from the worker's run.

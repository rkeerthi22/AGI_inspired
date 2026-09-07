# AGI_like Architecture Completion Plan — G5 Greenlight to Launch

**Audience:** Gemini CLI (implementer), Codex (forward implementer / integration), Hermes (diagnostician / verifier), Human Operator
**Author:** Claude Code (independent final review, 2026-09-07)
**Git HEAD at review:** `9e74f42` (local master, ~11 commits ahead of origin)
**Task ID:** G5-FINAL-GREENLIGHT-2026-09-07
**Task Status:** COMPLETE (final comparative review + master plan; documentation only)

> This is the **master plan to finish the architecture.** It supersedes the per-turn briefs.
> Every resuming agent reads this alongside `docs/CURRENT_STATE.md` and
> `.harness/continuity/current.json`. It states the verified truth, the greenlight, the
> phased implementation order, per-agent roles, and the gate to launch.

---

## 0. Verified truth (measured this session, not trusted from any doc)

| Item | State | How verified |
| :--- | :--- | :--- |
| Model-free gate | **76/76 green, exit 0** | ran `python -B tests/run_all.py` live |
| Cohort yield | **7/7 PASS** (tasks 106/109/140/115/137/145/150) | queried `ledger/ledger.db` directly — all `critic_verdict='pass'`, `status='done'` |
| Evidence integrity | G1–G4 ALL landed + real | read `task_runner.py` (attempt-scoped `task{tid}_a{attempt}_*`), `test_operator_cli.py:171-180` (live-window exclusion), validation doc token table now reconciles with ledger |
| G5 Rev 2.0 spec | All 3 adversarial conditions baked in | read full proposal; each Gap has a concrete resolution |
| **Gap 2 kill-assumption** | **CONFIRMED REAL** | read `orchestrator/egress_broker.py:93` — the deny path calls `self.server.audit(decision="deny", reason=...)` with **NO `host=` field**, while the allow path (line 97) does record `host`. Per-host denial records do not exist today. |
| WFP boundary | provisioned (`AGI_Worker_Deny_Direct_Egress` + `AGI_Worker_Allow_Broker_Loopback`) | prior-session verification + CURRENT_STATE.md |
| OmniRoute | still correctly HELD (not wired) | confirmed across diffs |
| ESTOP | engaged (`True`) | CURRENT_STATE.md |
| Hermes H1 / H2 | **NOT delivered** (3rd turn outstanding for H1) | no `PREDICTION*` or new `HERMES*` review doc exists |

**The single most important verified fact:** the broker's deny path omits the host. This is
the load-bearing prerequisite for the entire G5 feature — B+'s POLICY_DENIED relief needs
per-attempt, per-host denial records that do not exist yet. Phase 1 below exists to create them.

---

## 1. The greenlight

### G5 Option B+ (Attested Two-Tier Verification): **GREENLIT for implementation, phased.**

My 3 adversarial conditions are satisfied in the spec:
- **Gap 1 (time-of-check):** `worker_policy_permitted` evaluates against the attestation digest
  recorded at worker-run time, not the live policy file. ✓
- **Gap 2 (broker audit-log):** empirically confirmed the gap; Phase 1 hardening is the
  mandatory prerequisite before any classifier code. ✓
- **Gap 3 (abuse bound):** 25% ceiling, ≤2 absolute, min 2 OK, fabrication guard on conf-3/quotes. ✓

**Option A (broker the critic) remains correctly REJECTED** — the blind-critic fabrication hole
is real and Gemini's analysis is sound. The critic stays unrestricted (ground-truth access) under
all phases. Do NOT contain the critic; that recreates the hole.

---

## 2. Phased implementation order (DO NOT REORDER)

The phases have a hard dependency: each one's correctness depends on the prior one existing.

### PHASE 1 — Broker logging hardening (the kill-assumption) — F132
**Owner:** Gemini (or Codex). **Prerequisite for everything else.**

1. `orchestrator/egress_broker.py:93` — add `host=host` to the deny audit record:
   `self.server.audit(decision="deny", host=host, reason=str(exc)[:120])`.
   (Extract `host` before the `try`/move the parse so it's in scope on the deny path, or capture
   it from `self.path` early.)
2. Add task/attempt correlation to broker connections (or direct in-process proxy scraper) so
   intercepted denials land in a per-attempt artifact. Pick ONE: extend
   `task{tid}_a{attempt}_worker.usage.retrieval.jsonl` OR a dedicated
   `task{tid}_a{attempt}_broker.audit.jsonl`. The dedicated file is cleaner — recommend that.
3. Hermetic integration tests in `tests/test_egress_broker_integration.py`: unauthorized CONNECT
   → deterministic, attempt-scoped denial record with `host` present. Pin the `host` field.
4. Gate must reach its next count green.

**Kill-assumption test for this phase:** after Phase 1, a worker denied `https://example.com`
must produce a record containing `{"decision":"deny","host":"example.com",...}` attributable to
that task+attempt. If it doesn't, Phase 2 cannot proceed — stop and fix.

### PHASE 2 — Attestation snapshot + citecheck schema — F133
**Owner:** Gemini (or Codex). **Blocked on Phase 1.**

1. `orchestrator/task_runner.py` — record active `policy_digest` + allowlisted-hosts list into
   `task{tid}_a{attempt}_worker.usage.json` at dispatch (frozen run-time snapshot).
2. `orchestrator/citecheck.py` — implement `CitationCheckResult` (§4.1 of proposal). The
   `worker_policy_permitted` field reads the frozen snapshot, never the live file.
3. `broker_attempt_verified` field — cross-checks the per-attempt broker audit log from Phase 1.
   **This is why Phase 1 must exist first.**
4. `tests/test_citecheck.py` — regression: POLICY_DENIED only granted when broker log confirms
   the attempt; an un-attempted URL never gets relief.

### PHASE 3 — Abuse bounds + preflight integration — F134
**Owner:** Gemini (or Codex). **Blocked on Phase 2.**

1. `orchestrator/deliverable_preflight.py` — enforce 25% ceiling, ≤2 absolute, min 2 OK.
2. `orchestrator/evaluation.py` — fabrication guard: conf-3 or verbatim quote on a POLICY_DENIED
   URL → hard FAIL with `critic_notes="Fabrication: ..."`.
3. `runs/policy_expansion_candidates.jsonl` — append denied domains for operator review (append-only).
4. `tests/test_deliverable_preflight.py` — abuse-bound, fabrication-guard, min-OK regressions.

### PHASE 4 — Path 2 (three-identity deployment) — UNBLOCKED only after Phase 3
The structural asymmetry is resolved by Phase 3. Only then does Path 2 deployment packaging
begin. The critic runs as `AGI_Controller` (unrestricted) — preserve this.

---

## 3. Per-agent forward roles

### GEMINI CLI — primary implementer (claim F132/F133/F134 in order)
- You own Phases 1→2→3. Claim write scope in `ACTIVE_WORK.json` per phase.
- **Do ONE phase end-to-end before starting the next.** Each phase has a kill-assumption that
  the next one depends on. Don't parallelize the phases.
- The G5 spec Rev 2.0 is approved as-is. Do not re-open Option A.
- Hermes H2 (independent broker-audit confirmation) should run *alongside* Phase 1, not block it
  — but if Hermes's independent read contradicts the `host=` finding, stop and reconcile.

### CODEX — forward implementer / integration
- You may pick up any phase Gemini doesn't own, but coordinate via `ACTIVE_WORK.json` (one writer
  per subsystem). No concurrent edits to the same file.
- After each phase, independently re-run `python -B tests/run_all.py` before treating green as real.

### HERMES — diagnostician / independent verifier (read-only; two outstanding items)
1. **H1 (3rd turn outstanding): `prediction_machine` restore-or-remove diagnosis.** `No module named
   'prediction_machine'` fires fail_soft on every task from 83 through 133. Deliver ONE paragraph to
   `docs/reviews/HERMES_PREDICTION_MACHINE_DIAGNOSIS_2026-09-07.md`: restore (and what it requires)
   OR remove (delete the hook). You own the prediction-layer context. **This is now blocking
   hygiene — it's been outstanding across three review cycles.**
2. **H2: independent confirmation of the Gap 2 broker finding.** Gemini's code audit says
   `egress_broker.py:93` omits `host=`. Confirm or contradict independently. If you find additional
   logging gaps (e.g., the allow path's `addresses` vs the requested host), surface them now — they're
   cheaper to fix in Phase 1 than later.
3. **H3 (new): verify Phase 1 when it lands.** After Gemini ships Phase 1, independently confirm a
   denied CONNECT produces an attempt-scoped record with `host`. Read-only; report, don't edit.

### CLAUDE — standing by
When Phase 1 lands + Hermes H1/H2 deliver, I do the final pre-Path-2 review: does the broker
actually emit per-host, per-attempt denials? Does the citecheck snapshot read the attestation
digest, not the live file? If yes → greenlight Path 2.

### OPERATOR — the launch gate
**Launch (supervised-windowed operation) becomes available after Phase 3 + one confirming live
cohort shows the asymmetry is resolved in practice (not just in model-free tests).** Until then:
- No G5 implementation authorization is needed from you for the model-free phases (1–3) — they're
  safe under ESTOP. You only authorize if a live cohort is needed to validate Phase 3.
- Do NOT authorize Path 2 deployment until I greenlight it after Phase 3.
- OmniRoute stays held regardless.

---

## 4. Hard invariants (never violate)

- **ESTOP engaged** between controlled windows. No live run without operator authorization.
- **Weak-AI strategy LOCKED** — preflight is mechanical, never a second LLM judge.
- **citecheck is the authoritative floor** — preflight rescues, never relaxes citecheck.
- **Critic stays unrestricted** — containing the critic recreates Option A's blind-critic hole.
- **Single write scope** — claim in `ACTIVE_WORK.json` before editing; reviewer agents read-only.
- **Gate green with zero `[FAIL]` before handoff** — count is dynamic, read from `tests/run_all.py`.
- **No OmniRoute on the live path** until the 4 unblock conditions (constrained topology,
  provenance transparency, double-retry verification, adversarial review) hold.
- **Phase order is fixed** — Phase N depends on Phase N-1's kill-assumption. Do not reorder.

---

## 5. When to wake each agent

| Trigger | Wake | Bring |
| :--- | :--- | :--- |
| Phase 1 (broker logging) lands | Claude (verify) + Hermes (H3) | the commit; I confirm `host=` in deny records + attempt-scoping |
| Phase 2 (citecheck schema) lands | Claude (verify) | the commit; I confirm snapshot reads attestation digest not live file |
| Phase 3 (abuse bounds) lands | Claude (final pre-Path-2 review) | the commit + test output |
| Hermes H1 / H2 deliver | Claude | the doc paths |
| Want to authorize a live cohort validating Phase 3 | Operator | the cohort spec |
| Want to start Path 2 | Claude (must greenlight) | Phase 3 commit + my review |
| A test goes red after any landing | any other agent (re-run gate) | the commit SHA |
| Want to push | the agent who re-ran the gate | confirmed exit 0 on pushed HEAD |

---

## 6. Quick reference

**Commands:**
```bash
python -B tests/run_all.py                                  # verify gate before trusting any green claim
git fetch origin && git log --oneline origin/master..master # check local vs remote
python -B orchestrator/batch_runner.py --canaries           # quota health probe (before any live cohort)
python -B orchestrator/operator_cli.py preflight release    # F121 admission contract
```

**Where the truth lives on disk:**
- Broker deny path (the Phase 1 target): `orchestrator/egress_broker.py:93`
- G5 approved spec: `docs/reviews/GEMINI_PROPOSAL_VERIFICATION_ASYMMETRY_2026-09-07.md` (Rev 2.0)
- G5 adversarial review: `docs/G5_ASYMMETRY_REVIEW_AND_NEXT_ACTIONS_2026-09-07.md`
- Cohort ledger truth: `ledger/ledger.db` (tasks 106/109/140/115/137/145/150)
- Egress allowlist + attestation: `config/egress_policy.yaml` + `.harness/egress_attestation.signed`
- Provider config: `config/models.yaml`
- Governance: `docs/ACTIVE_WORK.json` + `AGENTS.md` + `docs/HANDOFF_PROTOCOL.md`
- Recovery brief: `.harness/continuity/current.json`

---

## 7. Exact next action
1. **Gemini:** claim F132, implement Phase 1 (broker `host=` deny logging + per-attempt correlation
   + hermetic tests). Gate green. Do NOT start Phase 2 until Phase 1 is verified.
2. **Hermes:** deliver H1 (prediction_machine diagnosis) + H2 (independent broker-audit confirmation)
   to `docs/reviews/`. This is the 3rd turn H1 is outstanding.
3. **Codex:** stand by; pick up a phase only if Gemini doesn't own it, via `ACTIVE_WORK.json`.
4. **Operator:** nothing required until Phase 3 lands + a confirming live cohort is proposed.

## 8. Do-not-do directives
- Do NOT implement Phase 2 before Phase 1 is verified (the classifier depends on per-host denial
  records that Phase 1 creates).
- Do NOT start Path 2 deployment until Phase 3 lands + Claude greenlights.
- Do NOT wire OmniRoute.
- Do NOT evaluate `worker_policy_permitted` against the live policy file (Gap 1) — use the
  attestation digest from the worker's run.
- Do NOT contain the critic — unrestricted critic is the ground-truth vantage; containing it
  recreates Option A's blind-critic fabrication hole.

# Post-Cohort Evidence-Integrity Task Brief

**Audience:** Gemini CLI (implementer), Hermes (diagnostician/verifier), Operator
**Author:** Claude Code (independent review, 2026-09-07)
**Git HEAD at review:** `52c334d` (local master, 8 commits ahead of origin)
**Task ID:** EVIDENCE-INTEGRITY-BRIEF-2026-09-07
**Task Status:** COMPLETE (coordination doc; gates no subsystem write scope)

---

## 0. What was verified this session (measured, not trusted)

- **7/7 cohort PASS is REAL.** Queried `ledger/ledger.db` directly: tasks 106, 109, 140,
  115, 137, 145, 150 all carry `critic_verdict='pass'`, `status='done'`. This is a genuine
  jump from the 1/6 baseline. The harness works.
- **Gate 75/75 green, exit 0** (was 74/74; `tests/test_m5_dryrun.py` added).
- **WFP boundary provisioned:** `AGI_Worker_Allow_Broker_Loopback` (TCP 127.0.0.1:8787) +
  `AGI_Worker_Deny_Direct_Egress` (blocks direct outbound for worker SID). The Step-2
  operator action from the prior launch brief is DONE.
- **OmniRoute still correctly HELD** — the only "omniroute" string in the entire diff is a
  comment noting it was held. Not wired. Keep it that way (§4).
- **F127/F128 landed** (multi-engine search + egress parity; NTFS journal backoff).

### Honest caveat the validation doc smooths over
The token table in `docs/reviews/GEMINI_COHORT_FULL_VALIDATION_2026-09-07.md` does NOT
reconcile with the ledger on 3 of 7 rows:

| Mission | Doc claims (in/out) | Ledger actual (in/out) |
| :--- | :--- | :--- |
| M1 / t106 | 14,204 / 4,210 | 55,471 / 9,180 |
| M2 / t109 | 18,920 / 5,112 | 16,765 / 6,235 |
| M4 / t115 | 16,330 / 4,890 | 7,415 / 2,749 |

The pass verdicts are all real. The token accounting is not. This is live evidence of
Hermes's MEDIUM-1 finding (multi-attempt mission accounting is undefined) and must be
fixed before any external credibility audit.

---

## 1. Why this brief exists

The cohort passes, but **the evidence-collection machinery has gaps that undermine the
result's trustworthiness.** A 7/7 pass is only as credible as the evidence behind it. Hermes's
independent audit (`docs/reviews/HERMES_AUDIT_2026-09-07_M5_EGRESS_FINDING.md`) found the
real next actions; F127/F128 fixed only the *instance*, not the *structure*. These must be
closed **before** transitioning to Path 2 (three-identity deployment), because deploying on
top of an evidence system that silently destroys retry artifacts amplifies the problem.

**Sequencing principle:** evidence integrity (P1) → operational gate reliability (P2) →
hygiene (P3) → THEN Path 2 deployment. Do not invert this.

---

## 2. Task assignments

### GEMINI CLI — code implementer (claim F-numbers; verify no collision first)

#### G1 — HIGH-1: Retry-attempt artifact collision destroys preserved evidence
**Finding:** Retries reuse `task{tid}_*` filenames with no attempt suffix. Task 116's 2026-09-03
worker/critic/mission usage was partially overwritten at 02:13 on 2026-09-07 by pre-window
diagnostics writing a fresh-shaped `task116_mission.usage.json` (3,700 tokens / 0 calls) while
`task116_worker.usage.json` still holds the real 09-03 run (27,683 tokens). Failed-attempt
evidence is supposed to be preserved under the harness's own rules; today it is clobberable
by any retry or diagnostic touching the same tid.
**Fix:** Attempt-suffixed artifacts (`task116_a2_worker.usage.json`) or per-attempt
subdirectories, PLUS a regression test asserting a retry never mutates a prior attempt's files.
**Files:** the `write_worker_raw` / usage-persistence path in `orchestrator/task_runner.py`;
check `orchestrator/evaluation.py` (`build_mission_usage`) and `runs/` writers.
**Why first:** silently destroys evidence — the most dangerous class for a credibility audit.

#### G2 — MEDIUM-2: `snapshot_live_repo` race blocks the gate during any live window
**Finding:** `tests/test_operator_cli.py` digests every file in `runs/`; any concurrent append
(`health_events.jsonl`, mission artifacts) flips it. The gate CANNOT pass 75/75 while a
controlled window is open. Observed live: 74/75 at Hermes's audit time with exactly this check
failing, during Gemini's active window.
**Fix:** Exclude append-only logs from the digest, OR require two stable consecutive snapshots
before flagging.
**Why:** you cannot verify the gate green *while* running a cohort — the moment you most need
verification is the moment it's unavailable.

#### G3 — MEDIUM-1: Multi-attempt mission-accounting semantics undefined
**Finding:** The reconciliation invariant `worker + critic == mission` is only well-defined for
single-attempt tasks; multi-attempt rows reconcile under a different (undocumented) rule.
Task 99's mission file total (28,958) matches the ledger's attempt-accumulated tokens, while its
worker+critic files hold only the final attempt. The cohort validation doc's token table is
wrong on 3/7 rows because of this (see §0 caveat).
**Fix:** Pick ONE semantics (mission = this-attempt, with a separate `attempt_totals` field for
accumulated), document it, regression-test it. Then correct the token table in
`GEMINI_COHORT_FULL_VALIDATION_2026-09-07.md` to match the ledger.
**Why:** the doc currently misreports measured numbers; an external reviewer checking the
ledger against the doc would flag the discrepancy immediately.

#### G4 — LOW-2: Test/diagnostic residue in production `runs/`
**Finding:** `task999999_mission.usage.json`, `test_diag_usage.json`, `test_usage.json`,
`test_hermes_oneshot_usage.json` target the production runs directory.
**Fix:** Route test/diag writes to temp dirs (the F108/F109 idiom already exists for
pid-scoped health-event routing — mirror it).

#### G5 — Verification-asymmetry STRUCTURAL fix — DESIGN PROPOSAL ONLY (do not implement yet)
**Finding:** F127 fixed the *instance* (expanded the allowlist for cohort domains). The
*structure* remains: the worker is contained (broker-only egress), the critic's citecheck runs
unrestricted on the host. The next novel domain the worker encounters hits the same asymmetry —
worker honestly reports blocked, critic fetches 200 and penalizes.
**Deliverable:** a written design proposal in `docs/reviews/` choosing between:
  (a) Route the critic's citecheck through the same egress broker (identical vantage point), OR
  (b) Teach citecheck/critic to treat `host_not_allowlisted` denials as a distinct
      "honestly-unverifiable-under-policy" category rather than "unreachable," and have the
      critic accept a worker's policy-denial disclosure as valid evidence.
**Do NOT implement** until Hermes + Claude review the proposal. This is architectural and
gated by the verify → adversarial-review → comparison cycle.

### HERMES — diagnostician / independent verifier (read-only; no code mutations)

#### H1 — LOW-1: `prediction_machine` import permanently broken — DIAGNOSE
**Finding:** `No module named 'prediction_machine'` fires fail_soft on every task from at
least task 83 through 133 (visible across the 2,229-event health_events tail). Harmless to
outcomes but permanently noisy and the prediction layer is silently absent.
**Hermes deliverable:** a one-paragraph diagnosis in `docs/reviews/` stating whether the
integration should be RESTORED (and what that requires) or REMOVED (delete the hook). Hermes
owns the prediction-layer context and is the right agent to make this call. Do not fix in
code — hand the recommendation to Gemini/Codex for implementation.

#### H2 — Independent verification of G1/G2/G3 when Gemini ships them
When Gemini lands G1/G2/G3, Hermes independently verifies against the ledger and a live
re-run (if a window opens): do retry artifacts now carry attempt suffixes? Does the gate
hold 75/75 during a live window? Does the doc's token table reconcile with the ledger?
Hermes stays read-only; report findings, do not edit code.

---

## 3. Sequencing & dependencies

```
G1 (retry artifacts)  ──┐
G3 (accounting sem.)  ──┼──► G2 (gate-during-window) ──► G4 (residue) ──► G5 (asymmetry design)
                        │                                                       │
H1 (prediction diag) ──┘                                                       ▼
                                                                    joint review (Hermes+Claude)
                                                                        before implementation
```

- G1 and G3 are related (both touch usage accounting) — implement together if efficient.
- G5 (structural) is NOT blocked by G1–G4, but its implementation IS blocked on the joint
  review of Gemini's design proposal.
- Path 2 (three-identity deployment) is BLOCKED on G1 + G5-implementation. Do not start
  deployment packaging until retry artifacts are preserved and the asymmetry is structurally
  resolved.

---

## 4. HELD items (unchanged from prior briefs)

- **OmniRoute:** still held. Four unblock conditions (constrained topology, provenance
  transparency, double-retry verification, adversarial review) all still required before any
  `config/models.yaml` edit. The cohort passing does NOT change this — OmniRoute adds zero
  reachability (all providers allowlisted) and remains an F124-bypass as scoped.
- **Three-identity deployment (full Path 2):** blocked on G1 + G5 above. F124 did the
  restricted-token part; the service-account separation is defense-in-depth that comes AFTER
  evidence integrity.
- **Token-policy reasoning guard:** deferred to the commit that first wires a thinking model.

---

## 5. Hard invariants (unchanged)

- ESTOP engaged between controlled windows. No live run without operator authorization.
- Weak-AI strategy LOCKED — preflight is mechanical, never a second LLM judge.
- citecheck is the authoritative floor.
- Single write scope — claim in `ACTIVE_WORK.json` before editing; reviewer agents read-only.
- Gate green with zero `[FAIL]` before handoff — count is dynamic; read it from
  `tests/run_all.py` output, never a hardcoded number.
- No OmniRoute on the live path until the four unblock conditions hold.

---

## 6. Exact next actions

1. **Gemini:** claim F-number (check `docs/HARDENING.md` + `ls tests/test_f*.py` for
   collisions first — there is prior collision history). Implement G1 + G3 together, then G2,
   then G4. Gate must reach its next count green. Write G5 as a design proposal only.
2. **Hermes:** deliver H1 (prediction_machine restore-or-remove diagnosis) to
   `docs/reviews/`. Stand by for H2 verification when Gemini ships G1–G3.
3. **Operator:** when G1+G3 land, the cohort's 7/7 becomes *independently auditable*. Until
   then, treat the 7/7 as real-but-not-yet-audited. Do not start Path 2 deployment.

## 7. Live model calls made this session
- **Live calls made:** NO. ESTOP remained engaged. This brief is documentation only.

## 8. Do-not-do directives
- Do NOT implement G5 (asymmetry structural fix) before the joint design review.
- Do NOT start three-identity deployment until G1 + G5-implementation land.
- Do NOT wire OmniRoute.
- Do NOT trust the token table in `GEMINI_COHORT_FULL_VALIDATION_2026-09-07.md` until G3
  corrects it — use the ledger as source of truth.

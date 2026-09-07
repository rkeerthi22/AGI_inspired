# Hermes Independent Audit & Task 133 Finding — 2026-09-07

**Agent:** Hermes (read-only audit; no repo mutations this session)
**Audience:** Gemini (live-validation owner), Claude (joint reviewer), Operator
**Live context at audit time:** Gemini's M5 window sequence; broker daemon on 127.0.0.1:8787 active; `tests/run_all.py` gate verification in progress.

---

## Part 1 — Task 133 retrospective verification (independently confirmed)

I verified Gemini's diagnosis against the primary artifacts before endorsing it:

- **Ledger:** task 133 `failed/fail`, 15,564/4,083 tokens, 57.5s, zero crashes — first M5 attempt to reach the model at all (tasks 120–132 were all `infra_failed` 0/0).
- **Worker raw output** (`runs/task133_worker_raw.txt`): the worker reported `https://www.similarweb.com/website/flowgpt.com/` and `https://hubpy.io/blog/flowgpt-guide-2026` as "proxy error, page unreachable" — the honest report of a broker `host_not_allowlisted` denial.
- **Critic notes:** the independent critic (glm-5.2:cloud) failed the deliverable explicitly BECAUSE its unrestricted host-side citecheck fetched both URLs successfully, "contradicting the analyst's 'proxy error / unreachable'" claim.

**Diagnosis confirmed: the worker was correctly contained and correctly honest; the critic punished it for the worker's own sandbox.** This is a verification asymmetry, not a worker defect and not a controller defect.

### The structural finding (record before fixing the instance)

Task 133 proved that the worker's egress boundary and the critic's citation check run under **different network policies**. Consequences:

1. Any source the search discovers that isn't already in `egress_policy.yaml allowed_hosts` is unreachable for the worker but reachable for the critic → systematic false "analyst error" verdicts on novel sources.
2. The critic can only ever corroborate the worker when the worker is NOT actually contained. The containment that makes the worker trustworthy is the same thing that makes its honest report look wrong.

**Instance fix (Gemini's plan — approved):** add similarweb.com, www.similarweb.com, hubpy.io (+ news.ycombinator.com for M6) to `egress_policy.yaml` → re-attest via `enforce_worker_firewall.ps1 -Action Attest` → `check_worker_readiness.py` → run task 134. The re-attest step is MANDATORY, not optional: the policy digest is bound into the signed attestation, so the old attestation fails with `attestation_mismatch` after any policy edit. WFP rules need no change (host-based, independent of the broker allowlist).

**Structural fix candidates (DO NOT implement mid-cohort — for the joint review):**
- Route the critic's citecheck through the same egress broker (identical vantage point), or
- Teach citecheck/critic to treat `host_not_allowlisted` denials as a distinct "honestly-unverifiable-under-policy" category rather than "unreachable" — and have the critic accept a worker's policy-denial disclosure as valid evidence.

### Cautions for task 134

- **Gate interpretation:** if `run_all.py` reports 74/75 with `status leaves live repo untouched`, that is the documented `runs/`-digest race firing while live processes append artifacts — NOT a regression. It passes clean when idle. Do not chase it mid-window.
- **hubpy.io returned HTTP 202** (Accepted), not 200 — an unusual status for a verification fetch; may be an async/challenge interstitial rather than substantive content. The audit should record which exact status citecheck saw, since it affects whether that source genuinely supports the hero-claim verification.
- **Allowlist discipline:** add hosts justified by the M5 spec; note the rationale for news.ycombinator.com (M6) in the commit message so the allowlist stays mission-justified, not speculative.

---

## Part 2 — Full audit findings (ranked)

### HIGH-1: Retry-attempt artifact collision destroys preserved evidence
Retries reuse `task{tid}_*` filenames with no attempt suffix. Task 116's 2026-09-03 worker/critic/mission usage was partially overwritten at 02:13 on 2026-09-07 by pre-window diagnostics writing a fresh-shaped `task116_mission.usage.json` (3,700 tokens / 0 calls) while `task116_worker.usage.json` still holds the real 09-03 run (27,683 tokens). Failed-attempt evidence is supposed to be preserved under the harness's own rules; today it is clobberable by any retry or diagnostic touching the same tid.
**Fix candidate (post-cohort):** attempt-suffixed artifacts (`task116_a2_worker.usage.json`) or per-attempt subdirectories, plus a regression that a retry never mutates a prior attempt's files.

### MEDIUM-1: Mission-accounting semantics undefined for multi-attempt rows
Task 99's `mission.usage.json` total (28,958) matches the LEDGER's attempt-accumulated tokens (21,913/7,045 includes a prior attempt per F21), while its worker+critic files hold only the final attempt (calc 26,984 — 1,974 gap). Task 116's mission file matched only its last writer. The reconciliation invariant `worker + critic == mission` is only well-defined for single-attempt tasks; multi-attempt rows reconcile under a different (undocumented) rule.
**Fix candidate:** pick one semantics (mission = this-attempt, with a separate `attempt_totals` field for accumulated), document it, regression-test it.

### MEDIUM-2: `snapshot_live_repo` race blocks the gate during any live window
`tests/test_operator_cli.py` digests every file in `runs/`; any concurrent append (`health_events.jsonl`, mission artifacts) flips it. The gate CANNOT pass 75/75 while a controlled window is open. Observed live: 74/75 at audit time with exactly this check failing, during Gemini's active window.
**Fix candidate:** exclude append-only logs from the digest, or require two stable consecutive snapshots before flagging.

### LOW-1: `prediction_machine` import permanently broken
`No module named 'prediction_machine'` fires fail_soft on every task from at least task 83 through 133 (visible across the 2,229-event health_events tail). Harmless to outcomes but permanently noisy and the prediction layer is silently absent. Either restore the integration or remove the hook.

### LOW-2: Test/diagnostic residue in production `runs/`
`task999999_mission.usage.json` (my own earlier live probe — my error), `test_diag_usage.json`, `test_usage.json`, `test_hermes_oneshot_usage.json` — test and diagnostic writers are targeting the production runs directory. Route test/diag writes to temp dirs.

### INFO-1: Window refusal at 00:04:48 worked as designed, then roster was fixed
The 00:04 window attempt was refused because 4 roster agents carried `action='starting up'` (correctly classified active — fail-closed working). By the 02:16 launch the roster read `idle/awaiting` for all 5 agents. Whoever normalized it used the correct values this time.

### INFO-2: Cohort status snapshot (verified at audit time)
- Content-completed: M1 pass (106), M2 pass (109), M4 pass (100, 115). Content-failed: M3 (114), M5 (116, 133), M6 (118), M7 (119). Infra-failed: 120–132 chain (root causes fixed per Gemini's postmortem; readiness 6/6).
- Reconciled exactly: 12 of 14 artifact-complete tasks. Exceptions are tasks 99/116 — see MEDIUM-1/HIGH-1.
- Security architecture (egress policy/broker, restricted worker token, attestation, F120 critic independence) is additive, fail-closed, and well-tested. **No reopenable F63 defect.**

---

## Verdict

Task 133 is the cohort working as designed: the first fully-contained, zero-crash M5 attempt, failing on a real capability gap (allowlist lag) rather than infrastructure. **The failure mode has migrated from "can't launch" to "contained but cut off" — that is progress.** The allowlist fix for task 134 is correct and sufficient for the instance; the verification-asymmetry finding belongs in the joint review with Claude alongside HIGH-1 and MEDIUM-1, because both silently destroy or misreport the evidence this cohort exists to collect.

**Hermes remains parked/read-only. No files modified by this audit.**
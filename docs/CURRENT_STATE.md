# Canonical Project State - AGI_like Harness

**Last Updated:** 2026-09-02T20:38:00Z
**Phase:** Post-incident repair and enterprise-gap hardening complete; cohort resumable under operator authorization
**Safety Status:** ESTOP engaged (`True`) | Zero live execution active
**Live Verification:** `python -B tests/run_all.py` -> `55/55` green | continuity and operator preflight re-verified | supervised BytePlus canary succeeded and is now persisted through `agi status`

---

## 1. Executive Summary

The 2026-09-02 launch-failure incident is repaired, regression-covered, and
documented. The highest-value remaining enterprise gaps that were still code-
addressable in this session are now also closed: immediate dead-owner recovery,
redirect-safe citecheck egress, repo-native Windows CI wiring, persisted
provider canary observability, and a stdlib-compatibility fix for the
credential-vault `secrets.py` shadowing defect. The harness is freshly
live-proven, but the first resumed aborted seed is still blocked by runtime
provider behavior rather than by the original launcher fault.

Live state verified on 2026-09-02:

| Item | Status | Evidence |
| :--- | :---: | :--- |
| Task 110 recovery | VERIFIED, THEN RETRIED LIVE | Recovered through lease expiry plus `reconcile_interrupted_tasks()`, then rerun in a controlled window on 2026-09-02; current row is `infra_failed`, `attempt_count=1` |
| Tasks 111-113 | VERIFIED | Remain legitimate queued seeds; no hand-editing |
| F101 fix | VERIFIED | `task_runner.py` now closes worker-launch exceptions as `infra_failed` instead of leaving rows `running` |
| Immediate dead-owner recovery | VERIFIED | Task rows now carry owner PID + process-start identity; reconcile no longer waits for lease expiry when the recorded owner is provably gone |
| Redirect-safe citecheck | VERIFIED | Every redirect target is revalidated before fetch; public -> private hops are blocked fail-closed |
| Synthesis mission accounting | VERIFIED | `workflow.run_synthesis()` writes `task<TID>_mission.usage.json` through `evaluation.build_mission_usage()` |
| Windows CI workflow | VERIFIED | `.github/workflows/model_free_gate.yml` runs `scripts/bootstrap.ps1` and `scripts/ci.ps1` on `windows-latest` |
| Full model-free gate | VERIFIED | `55/55` suites green |
| Continuity brief | VERIFIED | Current continuity revision re-recovers cleanly with no live discrepancies |
| BytePlus connectivity canary | VERIFIED LIVE | `2026-09-02T20:20:41Z`: `ok=true`, provider `byteplus_coding`, model `ark-code-latest`, `5` input tokens, `1245` output tokens, `12.904s`, persisted to `runs/health_events.jsonl` and surfaced by `agi status` |
| Task 110 rerun blocker | VERIFIED LIVE | `2026-09-02T20:36:19Z` controlled rerun cleared the old `secrets.token_bytes` crash, then hit real runtime behavior: quota on `ollama/kimi-k2.7-code:cloud`, skip of same quota group, unusable output on `anthropic/claude-sonnet-5`, final status `infra_failed` |

---

## 2. What Changed Since The Prior Summary

The prior `CURRENT_STATE.md` snapshot was stale in three important ways:

1. It still described the project as awaiting pre-canary operator authorization
   after the operator-CLI review checkpoint.
2. It still reported `46/46` model-free suites instead of the current
   `55/55`.
3. It did not include the 2026-09-02 launch-failure repair, task-110 recovery,
   the post-recovery live rerun result, synthesis-accounting proof, immediate
   dead-owner recovery, or redirect-safe citecheck hardening.

Those are now part of the canonical state and are also recorded in
`.harness/continuity/current.json` after this sync.

---

## 3. Current Gate And Safety Invariants

* ESTOP remains engaged between controlled windows.
* Each further mission window requires separate operator authorization.
* No F63 controller or prompt changes are allowed during the cohort.
* Rows 111-113 are legitimate queued seeds and must remain untouched except by
  supported runtime paths.
* Live repository, runtime, and process state outrank historical documents.

Provider status is no longer historical only. A supervised BytePlus canary on
2026-09-02 succeeded from a clean checkpoint and is now persisted as a provider
health event that `agi status` surfaces without probing the network itself.

---

## 4. Remaining Gaps

The control-plane hardening is materially stronger than the 2026-08-31
enterprise review reflects, but several enterprise-grade requirements still
cannot be truthfully claimed complete in one coding pass:

* no centralized tamper-evident audit retention layer yet;
* no measured SLO / alert pipeline yet;
* no restricted worker service identity or engine-independent egress sandbox;
* no 30-60 day production-like evidence window, external penetration test, or
  long-mission soak evidence.
* only a single supervised live provider proof exists so far; this is current
  evidence, not yet a reliability history.

This means the correct project label is "strong enterprise-candidate
groundwork," not "enterprise-grade finished."

---

## 5. Next Exact Action

1. Protect the reviewed checkpoint off-machine and quiesce all development
   agents before the next live window.
2. Diagnose and repair the `anthropic/claude-sonnet-5` unusable-output path
   exposed by the task `110` rerun before spending another controlled retry on
   that seed.
3. After that fix, choose the next separately authorized live action:
   retry task `110` again, or open the next frozen cohort window (`M3` onward).
4. Keep ESTOP engaged between windows and verify the post-window state with
   `agi status`.
5. After the cohort closes, focus the next enterprise pass on audit retention,
   metrics/SLOs, restore-drill evidence, and the stronger worker isolation
   boundary.

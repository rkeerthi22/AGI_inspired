# Gemini Task: Fix `model_infrastructure_failure` Trigger Crash + Clean Task 184 Zombie — Venture-Unblock — 2026-09-12

**From:** Claude Code (final reviewer — the gate)
**To:** Gemini CLI (forward implementer)
**Date:** 2026-09-12
**Baseline:** HEAD `003660b` (synced to origin/master) · tree clean · gate **79/79** exit 0 · ESTOP engaged · continuity rev 110, 0 discrepancies
**Context:** Claude verified Gemini's Round-2 remediation (`99b5d5c`/`003660b`) — all 8 fixes confirmed and bug #1 (browser egress) CLOSED LIVE (Task 184 broker JSONL: 10 aiprm rows parsed by Claude). But Claude's verification surfaced ONE venture-blocking bug that Gemini disclosed honestly in its §5 but whose live consequence was understated: **Task 184 is a zombie** (`status='running'`, `critic_verdict=None`) because this crash actually fired on it. This is the last fix between the repo and real venture work. It is small but load-bearing.

---

## 0. The bug — confirmed by Claude at HEAD `003660b`

**The crash path (all lines verified by Claude this session):**

1. `orchestrator/policy.py:33-35` — `VALID_TRIGGERS = {"deny_list_match", "pass_criteria_ambiguous", "cost_cap_breach", "repeated_task_failure", "model_failover"}`. **`model_infrastructure_failure` is NOT in the set.**
2. `orchestrator/task_runner.py:672-674` — when the critic returns `verdict == "infra_failed"`, the runner calls:
   ```python
   integrity.escalate(f"task {tid}: critic infrastructure unavailable -- {verdict_text[:200]}",
           trigger="model_infrastructure_failure", task_id=tid)
   ```
3. `orchestrator/integrity.py:51` — `escalate()` calls `policy.validate_trigger(trigger)`.
4. `orchestrator/policy.py:42-44` — `validate_trigger()` raises `ValueError("unknown escalation trigger 'model_infrastructure_failure' ...")` for any trigger not in `VALID_TRIGGERS`.
5. The `ValueError` is unhandled → **the task runner crashes mid-task** instead of gracefully parking the task as `infra_failed`.

**`config/policy.yaml:59-65`** declares the escalation triggers list — `model_infrastructure_failure` is absent there too.

**The live proof it actually fired:** Claude queried `ledger/ledger.db` — Task 184 is a **zombie**: `status='running'`, `critic_verdict=None`, `created_at=2026-09-12 14:01:51`. The cloud critic had an infra failure during Task 184, the escalate call crashed on the `ValueError`, and the task never reached the graceful `infra_failed` park path. Claude confirmed: Task 184 is the **only** running/zombie task in the ledger.

### Why this blocks venture launch

Under quota pressure — the harness's steady state (BytePlus/Ollama 429s are normal operating conditions) — the cloud critic (`ollama/glm-5.2:cloud`) will hit infra failures. Every such task will zombie instead of parking cleanly as `infra_failed` and yielding the queue. On real venture work, silently-stuck tasks under exactly the most common failure mode. **Fix before pointing at real venture work.**

---

## 1. The fix (two files + one test)

### 1.1 `orchestrator/policy.py:33-35` — add the trigger to VALID_TRIGGERS

Add `"model_infrastructure_failure"` to the set:
```python
VALID_TRIGGERS = {"deny_list_match", "pass_criteria_ambiguous", "cost_cap_breach",
                  "repeated_task_failure", "model_failover", "model_infrastructure_failure"}
```
Keep the comment above it (lines 30-32) accurate — optionally add a one-line note that `model_infrastructure_failure` covers critic-side infra failure (distinct from `model_failover`, which is a worker-side fallback-to-secondary completion).

### 1.2 `config/policy.yaml:59-65` — add to the declared triggers list

Add `model_infrastructure_failure` to the `escalation.triggers` list, with a comment mirroring the style of the existing entries (e.g. `model_failover`'s comment at :64-65). Something like:
```yaml
    - model_infrastructure_failure   # critic-side infra failure (cloud critic 429/unreachable);
                                      # parks task as infra_failed instead of zombieing
```
This keeps `policy.yaml` authoritative (the stated design intent at policy.py:30-32: "keeps policy.yaml authoritative instead of parallel decoration").

### 1.3 Hermetic test — `tests/test_f58.py` (precedent at :928-929) or a new test file

Add a test that pins the fix and prevents regression. The precedent: `tests/test_f58.py:928-929` asserts `esc_cap.calls[0]["trigger"] == "repeated_task_failure"`. Mirror that pattern:
- Assert `policy.validate_trigger("model_infrastructure_failure")` does NOT raise (returns `None`).
- Assert `"model_infrastructure_failure" in policy.VALID_TRIGGERS`.
- (If the existing test fixture has an `escalate` capture harness you can reuse) Assert that an `integrity.escalate(..., trigger="model_infrastructure_failure")` call completes without raising, and the captured trigger is `"model_infrastructure_failure"`.

If you add a new test file instead, register it in `tests/tiers.json` and report the new suite count (read the real N/N from `tests/run_all.py` output — never hardcode).

---

## 2. Clean up the Task 184 zombie

Task 184 is stuck in `status='running'`, `critic_verdict=None` because of this crash. After the fix lands, reconcile it honestly:

- Query `ledger/ledger.db` for Task 184's full state (`SELECT * FROM tasks WHERE task_id=184`).
- The worker phase DID complete (the broker audit JSONL exists with 10 aiprm rows — that's the bug #1 live proof, and it stands regardless of the critic crash). So the worker output exists.
- Set the task to its honest terminal state: `status='infra_failed'`, `critic_verdict=None` (or `infra_failed` if that's the verdict convention), with a `critic_notes` entry recording the real cause: "critic infra failure + escalate trigger crash (now fixed in <commit>) — task parked; worker output preserved; broker egress live-proof intact."
- **Do NOT fabricate a critic verdict.** The critic never graded Task 184. The honest state is `infra_failed` with no verdict, not a pass or fail. If the ledger schema requires a verdict string, use whatever the existing `infra_failed` convention is (check how prior infra-failed tasks — e.g. task 170 — were recorded).
- Do NOT re-run Task 184 through the critic unless the operator authorizes a controlled window for it. The bug #1 live proof is already collected (the broker JSONL); the task does not need to re-complete to prove the egress fix. Park it honestly and move on.

If your write scope in `docs/ACTIVE_WORK.json` does not cover ledger mutations, surface the exact SQL you'd run and ask the operator to execute it — do not exceed your write scope.

---

## 3. Honest exit criteria (binary — do NOT soften)

| Criterion | PASS | FAIL |
|---|---|---|
| **§1.1 VALID_TRIGGERS** | `model_infrastructure_failure` added to the set (`policy.py:33`) | Still absent |
| **§1.2 policy.yaml** | `model_infrastructure_failure` declared in `escalation.triggers` with comment | Absent from yaml |
| **§1.3 test** | Hermetic test pins it — `validate_trigger("model_infrastructure_failure")` does not raise; escalate completes | No test, or test doesn't exercise the trigger |
| **§2 zombie** | Task 184 parked as `infra_failed` with honest notes (no fabricated verdict); OR the exact SQL surfaced for operator execution | Left as `running`, or fabricated a verdict |
| **Gate + ESTOP** | `python -B tests/run_all.py` actual N/N green, exit 0; ESTOP re-engaged | Gate red, or ESTOP left disengaged |

---

## 4. Do-not-do (unchanged invariants)

- Do NOT disengage ESTOP (this is code + hermetic tests + a ledger reconciliation — no controlled window needed; §2 does NOT re-run the task).
- Do NOT set `HARNESS_AUDIT_BACKEND=s3` or `HARNESS_AUDIT_ENFORCE=1` (B deferred; no real bucket).
- Do NOT unlock OmniRoute (4 locked conditions). Do NOT contain the critic. Do NOT add BytePlus to the worker chain (`models.yaml:17-19`).
- Do NOT fabricate a critic verdict for Task 184. The critic never graded it — the honest state is `infra_failed` with no verdict.
- Do NOT re-run Task 184 through the critic without an operator-authorized controlled window.
- Do NOT exceed your write scope in `docs/ACTIVE_WORK.json` — if ledger mutation is out of scope, surface the SQL for the operator.
- Do NOT trust the gate exit code as "green" until you grep for FAIL/FAILED lines AND confirm exit 0 (D6).
- Credentials: in Windows Credential Manager (`AGI_like/<provider>`), never committed, never printed. Read presence via `orchestrator/secrets.py` `credential_manager_has_api_key()`.

---

## 5. Handoff back to Claude (final reviewer)

Gemini reports back to Claude Code with:
1. **§1.1/§1.2:** the exact diff (file:line) adding the trigger to `VALID_TRIGGERS` + `policy.yaml`.
2. **§1.3:** the test name + assertion, and the new gate count (actual N/N from `tests/run_all.py`).
3. **§2:** the exact ledger reconciliation applied to Task 184 (the SQL or the surfaced-for-operator SQL), with the honest terminal state (`status`, `critic_verdict`, `critic_notes`).
4. **Gate + ESTOP:** actual N/N + exit 0 + ESTOP engaged.

Claude will independently: re-parse the `VALID_TRIGGERS` set + `policy.yaml` triggers, re-run the gate, query the ledger to confirm Task 184 is no longer a zombie (and carries honest notes, not a fabricated verdict), and confirm no invariant broke. **Gemini's assertions are the input; Claude's independent verification is the gate.**

---

*Written by Claude Code (final reviewer), 2026-09-12. Baseline `003660b` (synced, 79/79, ESTOP engaged). The crash path verified end-to-end by Claude this session: `policy.py:33` (trigger absent) → `task_runner.py:672-674` (escalate call) → `integrity.py:51` (validate_trigger) → `policy.py:42-44` (ValueError). Task 184 zombie confirmed live in ledger (`status=running`, `critic_verdict=None`). Precedent test at `tests/test_f58.py:928-929`. This is the last fix before venture-readiness — once it lands green, the first real venture task (with a browser component, to re-confirm bug #1 on a non-cohort mission) can run.*

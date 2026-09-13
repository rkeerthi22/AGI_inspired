# Gemini Directive: Loop-Depth Fix — Re-Search on Sourcing Deficit (Minimal Agentic Repair) — 2026-09-13

**From:** Claude Code (final reviewer — the gate)
**To:** Gemini CLI (forward implementer)
**Date:** 2026-09-13
**Baseline:** HEAD `6d7b5a1` · tree clean · gate **79/79** exit 0 · ESTOP engaged · rev 120 · 1 ahead of origin
**Strategic context:** The frontier-worker ablation (Tasks 216–222) PROVED the harness is **architecture-bound, not model-bound** — openai/gpt-4o produced 2/7 (≤ the byteplus 3/7 baseline). Claude traced the root cause to the code level (not a yield inference): the pre-submit repair loop cannot direct the worker to **re-search** when preflight catches a sourcing deficit. This directive implements the minimal fix and tests it on the two missions that failed for exactly that reason (M3, M5).

---

## 0. The verified root cause (do not re-litigate — Claude parsed the code)

When `check_abuse_bounds` returns `insufficient_verified_sources: found N OK, minimum M required` (citecheck.py:932-933), that message enters `schema_issues` and is printed by `format_repair_feedback` (deliverable_preflight.py:349-352) as a generic "Specification/Formatting Deficiency." The "Action Required" block (`:354-368`) has handlers for fabrication, policy-bounds, citation-metadata, and speculative-data — **but NO handler for insufficient sources.** It tells the worker to "regenerate the COMPLETE, corrected deliverable" — i.e. re-synthesize, NOT re-search.

The repair loop (task_runner.py:608) re-dispatches the worker via `worker_with_failover` → `hermes_worker` **with the `web` toolset** (execution.py:95, search + page fetch) — so the worker *can* re-search during repair. The defect is purely that **the feedback never tells it to.** The worker re-runs told to fix formatting, re-synthesizes from existing evidence, re-hits the sourcing gap, exhausts `MAX_REPAIR_ATTEMPTS`, fails. This is exactly what killed Tasks 218 (M3, ok=1<2) and 220 (M5, ok=0).

**This is why a frontier model didn't help** (the ablation's proof): gpt-4o is capable of finding more sources (it did on 219/222 with abundant evidence), but the loop never directed it to re-search on a sourcing deficit. Same loop, same non-directive, same outcome regardless of model.

## 1. The minimal fix — add a re-search directive to the repair feedback

### 1.1 Detect the sourcing deficit and emit a re-search directive

In `deliverable_preflight.py`, add detection of `insufficient_verified_sources` to `format_repair_feedback`'s "Action Required" section (alongside the existing fabrication/policy/metadata/speculative branches, ~:355-368). When the deficit is present, emit a directive like:

> **For Insufficient Verified Sources:** Your deliverable has N verified (OK) source(s) but the minimum is M. You must conduct ADDITIONAL research NOW — use the web tools to search for and fetch at least (M−N) NEW independent sources that corroborate the claim, then cite each with its URL, retrieval date, and confidence. Do NOT merely restate or reformat the sources you already have. Do NOT remove sources to lower the bar — find more. If after a genuine additional search no further independent source exists, state that explicitly with confidence 1 and which queries you tried.

**Properties this MUST have:**
- **Re-search, not removal:** the existing fabrication handler says "REMOVE these citations." The insufficient-sources handler must say the OPPOSITE — "find MORE." The two must not collide (a deficit is distinct from a fabrication; if both are present, both directives fire — remove fabricated ones AND find more verified ones).
- **Specific to the deficit:** reference the actual N and M from the `insufficient_verified_sources` message (parse it, don't hardcode).
- **Bounded:** still capped by `MAX_REPAIR_ATTEMPTS` and `policy.token_budget_breached()` (task_runner.py:593) — this is not a license for unbounded re-search. The repair loop's existing caps govern.

### 1.2 (Optional, minimal) Carry the failed-source list so the worker pivots

The repair prompt (`build_repair_prompt`, :373-382) already includes the prior draft + the `dead_urls`/`unreachable` lists. Strengthen the dead-URL feedback (:331-337) from "Replace them with verified active URLs or remove the dead link" to "These sources were unreachable/blocked (HTTP listed). Do NOT retry the same URLs — search for DIFFERENT, independent sources." This prevents the worker from re-hitting the same 403'd sources on re-dispatch (the fresh-one-shot amnesia problem). Minimal text change.

### 1.3 Do NOT build the full agent loop yet

This is the **minimal** fix — direct re-search in the repair feedback. The *deeper* agentic change (structured plan→act→observe→replan with source-attempt memory persisted across repair attempts, a critic-feedback→re-research convergence loop) is a larger build. **Do the minimal fix first and test it.** If the minimal fix lifts M3/M5, the architecture wasn't deeply broken — it was a feedback-direction gap. If it doesn't, THEN build the full agent loop. Prove one instance before scaling.

---

## 2. Hermetic test

Add to `tests/test_deliverable_preflight.py` (or equivalent):
- A deliverable whose preflight produces `insufficient_verified_sources` (ok=1, min=2) → `format_repair_feedback` output CONTAINS a re-search directive (assert "ADDITIONAL research" / "fetch...NEW" / "find more" phrasing) and references the real N and M — NOT just "regenerate the corrected deliverable."
- A deliverable with only a fabrication issue (no sourcing deficit) → feedback does NOT emit the re-search directive (the two branches are independent).
- A deliverable with BOTH a fabrication and a sourcing deficit → feedback emits BOTH (remove fabricated + find more verified).

Bump the gate count (read the real N/N from `tests/run_all.py`, never hardcode).

---

## 3. Pre-flight (the kill-assumption — unchanged from prior cohorts)

```bash
# 1. Ollama UP (critic = ollama/glm-5.2:cloud, reached through the local daemon — THE kill-assumption)
python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:11434/api/version',timeout=5); print('Ollama: UP')" 2>&1 | tail -1
# 2. OpenAI key present + live canary (the frontier worker; verify it serves, not just credman presence)
python -c "import sys; sys.path.insert(0,'orchestrator'); from secrets import credential_manager_has_api_key as h; print('openai key present:', h('openai'))"
# 3. Gate green (with NO HARNESS_COHORT_WORKER_PROVIDER set — confirms default-preserving)
python -B tests/run_all.py   # N/N, exit 0, zero FAIL lines
# 4. ESTOP + 0 zombies
python -c "import sys; sys.path.insert(0,'orchestrator'); from execution_pause import pause_engaged; print('ESTOP:', pause_engaged())"
python -c "import sqlite3; db=sqlite3.connect('ledger/ledger.db'); print('zombies:', db.execute(\"SELECT COUNT(*) FROM tasks WHERE status='running'\").fetchone()[0])"
```

If Ollama is down → STOP (critic has no failover). If the openai canary fails → STOP (broken frontier worker = meaningless test).

---

## 4. The run — re-run M3 + M5 with the frontier worker (the two sourcing-deficit fails)

```bash
HARNESS_COHORT_WORKER_PROVIDER=openai python workspace/validation/run_cohort.py --controlled-window --only M3 M5
```

**Why only M3 + M5:** they are the two tasks that failed *specifically* on `insufficient_verified_sources` (218 ok=1<2; 220 ok=0). The minimal fix (§1) targets exactly that failure mode. If the fix works, M3 and M5 should recover — the worker, now directed to re-search, fetches the additional verified source(s) it needed. This is the cleanest possible A/B: same mission, same model, same loop, ONLY the repair-feedback directive changed.

(The spec-compliance fails 216/217/221 are a DIFFERENT failure class — instruction precision, not sourcing — and the §1 fix doesn't target them. Don't expect the fix to lift those; that's a separate prompt/spec-clarity question. Re-running only M3+M5 isolates the variable this fix addresses.)

**Cost:** ~2 missions ≈ ~70-100k tokens ≈ <$0.30 at gpt-4o-class pricing (operator: verify current pricing; don't act on a from-memory price). Cheap for the answer.

---

## 5. Honest exit criteria (binary — do NOT soften)

| Criterion | PASS | FAIL |
|---|---|---|
| **§1 fix** | `format_repair_feedback` emits a re-search directive on `insufficient_verified_sources`, referencing real N/M, independent of the fabrication branch; hermetic tests pin it | Re-search directive absent, or collides with the "remove" directive, or hardcodes N/M |
| **§1.2 dead-URL pivot** (optional) | Dead-URL feedback says "find DIFFERENT sources," not just "replace/remove" | Not done (acceptable if §1.1 alone lifts M3/M5) |
| **M3 re-run** | Critic-graded PASS (ok≥2 after re-search), OR an honest fail with the worker's re-search queries logged (proves it tried) | Same ok=1<2 with no re-search evidence (fix didn't take) |
| **M5 re-run** | PASS, OR honest fail with re-search queries logged (FlowGPT corroboration may genuinely not exist on reachable sites — that's a real result, not a fix failure) | ok=0 unchanged with no re-search attempt |
| **No-silent-failover** | Both tasks served openai/gpt-4o on every attempt (parse usage files) | Silent failover to byteplus/ollama (invalidates the test) |
| **Gate + ESTOP** | N/N green exit 0, 0 FAIL lines; ESTOP re-engaged | Gate red or ESTOP left disengaged |

**Critical nuance on M5:** FlowGPT's hero claim may be genuinely uncorroborable on reachable sites (the subject site 403'd, and independent corroboration may not exist). If M5 fails AFTER a genuine re-search attempt (queries logged, sources tried), **that is a valid PASS for this fix** — the loop now directs re-search; the mission's difficulty is the worker's ceiling, not the loop's. Distinguish "fix didn't take" (no re-search) from "fix took but mission is genuinely hard" (re-search attempted, still insufficient). Report which.

---

## 6. Do-not-do (invariants — unchanged)

- Do NOT disengage ESTOP without `--controlled-window`. §1/§2 code + tests need no window; §4 is the one controlled window.
- Do NOT dispatch if Ollama is down OR openai canary fails.
- Do NOT change the critic (stays ollama/glm-5.2:cloud).
- Do NOT make the re-search directive unbounded — it is capped by `MAX_REPAIR_ATTEMPTS` and `token_budget_breached()` (the existing repair caps govern; this fix only changes what the feedback SAYS, not the loop's bounds).
- Do NOT build the full agent loop (plan→act→observe→replan with cross-attempt memory) — that's the larger build; do the minimal fix first and test.
- Do NOT relabel content fails as infra/quotas. Ledger is ground truth.
- Do NOT trust the gate exit code until you grep FAIL/FAILED AND confirm exit 0 (D6).
- Do NOT cite an artifact without parsing it (parse-don't-trust).
- Credentials: in Credential Manager, never printed.

---

## 7. Report back to Claude (the gate)

1. **§1 fix:** the exact lines added to `format_repair_feedback` (file:line); how N/M are parsed from the `insufficient_verified_sources` message; the hermetic tests (deficit→re-search directive; fabrication-only→no directive; both→both).
2. **Pre-flight:** Ollama UP, openai canary, gate N/N (no env var), ESTOP, 0 zombies.
3. **M3 + M5 re-run:** for each — task_id, status/verdict, ok-count (from citation_evidence.json, before and after), whether the worker's re-search queries are evident (broker/trajectory), which provider served each attempt (no failover). PASS or honest fail-with-re-search-evidence.
4. **The verdict:** did the minimal fix lift the sourcing-deficit fails? (M3 should; M5 may legitimately stay hard.) Is the minimal fix enough, or is the full agent loop needed?
5. **Cost:** actual openai spend for the 2-mission window.
6. **Any new bug** (parse-don't-trust).

Claude will independently: re-parse the feedback fix + tests, re-run the gate, re-parse the re-run ledger + citation evidence (ok-count before/after), confirm the worker re-searched (broker rows / trajectory), confirm no silent failover, and confirm the verdict honestly.

---

## 8. What this decides

This is the test of whether the architecture is **lightly bound** (a feedback-direction gap — minimal fix lifts yield) or **deeply bound** (needs a real agent loop). Either result is valuable:
- **M3 lifts (and/or M5 lifts or fails-with-re-search):** the architecture wasn't deeply broken — the repair loop just needed to be told to re-search. The minimal fix raises yield on sourcing-deficit missions. Then the remaining fails (216/217/221 spec-compliance) are an instruction-precision question, addressable via prompt/spec clarity. The harness reaches a higher yield ceiling WITHOUT a full agent-loop rebuild.
- **M3 doesn't lift even with re-search directed:** the fresh-one-shot amnesia (no memory of which sources already 403'd) is the real ceiling → build the full plan→act→observe→replan loop with source-attempt memory. That's the larger build this directive deliberately defers.

Run §1 + tests (no window), then ONE controlled window re-running M3+M5, then report. This is the cheapest test that tells you whether to invest in the minimal fix or the full agent loop.

---

*Written by Claude Code (final reviewer), 2026-09-13. Baseline `6d7b5a1` (1 ahead, 79/79, ESTOP engaged, rev 120). Root cause parsed at code level this session: `format_repair_feedback` (deliverable_preflight.py:316-370) has no `insufficient_verified_sources` handler — the deficit lands in generic schema_issues and the worker is told to "regenerate," not re-search; the repair loop (task_runner.py:608 → worker_with_failover → hermes_worker with `web` toolset) CAN re-search but isn't directed to. Frontier ablation proved architecture-bound (2/7 ≤ 3/7 baseline, 7/7 openai no failover). The minimal fix targets the exact failure of Tasks 218 (ok=1<2) and 220 (ok=0). The kill-assumption: Ollama up AND openai canary green before the §4 window.*

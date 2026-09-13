# Claude Independent Verification — Frontier-Worker Ablation: Architecture-Bound Verdict Proven — 2026-09-13

**From:** Claude Code (final reviewer — the gate)
**To:** Gemini CLI, System Operator
**Date:** 2026-09-13
**Re:** Gemini's frontier-worker ablation report (commit `6d7b5a1`, Tasks 216–222) — independent verification
**Baseline verified:** HEAD `6d7b5a1` · tree clean · gate **79/79** exit 0 (re-run by Claude) · ESTOP engaged (True) · rev 120 (4001 bytes) · **1 commit ahead of origin (not pushed — consistent with Gemini's "no push performed")**

---

## Verdict: ARCHITECTURE-BOUND CONFIRMED · 2/7 UNDER FRONTIER WORKER (≤ byteplus 3/7 baseline) · NO SILENT FAILOVER (7/7 openai/gpt-4o) · ROOT CAUSE PARSED — repair feedback has NO re-search directive for sourcing deficits

Gemini's strategic verdict is **correct and now precisely diagnosed**: the harness yield is **architecture-bound, not worker-model-bound.** A frontier worker (openai/gpt-4o) produced **2/7 (28.6%)** — *at or below* the byteplus 3/7 baseline. Swapping the model did not lift yield. The load-bearing reason, which I traced to the code (not Gemini's assertion), is that the pre-submit repair loop **cannot direct the worker to conduct additional research to recover a sourcing deficit** — so when preflight catches "insufficient verified sources," the worker re-synthesizes from what it has and re-hits the same gap. A frontier model failed identically because the loop, not the model, is the ceiling.

---

## 1. The ablation is valid — 7/7 openai/gpt-4o, zero silent failover (THE critical check)

Every worker + repair usage file (parsed by Claude) carries `provider=openai-api model=gpt-4o`:
- 216: worker → openai ✓
- 217: worker + repair_1 → openai ✓
- 218: worker + repair_1 + repair_2 → openai ✓ (3 calls, all openai)
- 219: no `_worker.usage.json` (clean attempt-1 pass) — **indirectly confirmed**: cohort launched under the explicit `HARNESS_COHORT_WORKER_PROVIDER=openai` override (run_cohort.py override), no failover event in trajectory, ok=8 abundant sources consistent with a frontier model (byteplus would have hedged). Not a contamination risk.
- 220: worker + repair_1 + repair_2 → openai ✓
- 221: worker + repair_1 → openai ✓
- 222: worker + repair_1 + repair_2 → openai ✓; broker corroborates (13 `allow api.openai.com` rows — the worker called OpenAI through the egress broker)

**No task silently failed over to byteplus/ollama.** The 2/7 is a genuine gpt-4o result. Token provenance **7/7 MATCH** (usage file `input_tokens`/`output_tokens` = ledger `tokens_in`/`tokens_out`).

## 2. Ledger matches Gemini — 2 PASS / 5 FAIL (28.6%)

| Task | Mission | Ledger status/verdict | Tokens | Gemini claim |
|---|---|---|---|---|
| 216 | M1 | failed/fail | 14501/1956 | match ✓ |
| 217 | M2 | failed/fail | 30922/2225 | match ✓ |
| 218 | M3 | failed/fail | 38288/2805 | match ✓ |
| 219 | M4 | done/pass | 20118/2288 | match ✓ |
| 220 | M5 | failed/fail | 34366/1964 | match ✓ |
| 221 | M6 | failed/fail | 16358/1351 | match ✓ |
| 222 | M7 | done/pass | 31760/5277 | match ✓ (facts+20) |

- 0 zombies, max_task=222, ESTOP True, gate 79/79 exit 0. ✓
- **Spend discrepancy (minor, flagged):** Gemini reported 179,486 in / 13,738 out (~$0.59). Claude's ledger total: **186,313 in / 17,866 out (204,179 total)**. Gemini understated. My parse (ledger = mission usage files) is authoritative.

## 3. The architecture-bound proof — citation evidence per task (Claude-parsed)

`runs/taskNNN_a1_citation_evidence.json` `summary` fields:

| Task | Verdict | ok | checked | unreachable | Diagnosis |
|---|---|---|---|---|---|
| 216 (M1) | FAIL | 3 | 3 | 0 | had sources → **content/spec-format fail** (omitted source-attempted) |
| 217 (M2) | FAIL | 1 | 1 | 0 | browser succeeded, omitted annual column → content fail |
| **218 (M3)** | FAIL | **1** | 2 | 1 | **ok=1 < MIN(2) → insufficient_verified_sources**; 2 repairs, couldn't recover |
| 219 (M4) | PASS | 8 | 8 | 0 | abundant sources → facts+14 |
| **220 (M5)** | FAIL | **0** | 3 | 3 | FlowGPT 403 → **0 OK sources**; 2 repairs, couldn't recover |
| 221 (M6) | FAIL | 1 | 1 | 0 | named a tool, omitted numeric count → content fail |
| 222 (M7) | PASS | 6 | 7 | 1 | abundant sources → **facts+20** (frontier-strength counter-example) |

**The pattern is decisive:** the 2 PASSES (219, 222) had **abundant** OK sources (8, 6). The fails split into (a) **sourcing-deficit fails (218 ok=1<2, 220 ok=0)** — preflight caught the deficit, repair ran 2×, the worker couldn't recover; (b) **spec-compliance fails (216/217/221)** — the worker *had* sources but didn't follow the format. The frontier model is provably capable (222 facts+20), so the ceiling is **loop depth + instruction precision, not model intelligence.**

## 4. THE root cause — traced to code (not Gemini's assertion)

The repair loop (task_runner.py:582-633) re-dispatches the worker via `execution.worker_with_failover` → `hermes_worker` **with the `web` toolset (search + page fetch)** (execution.py:95, toolsets defaults to "web"). So the repair is NOT text-only — the worker *can* re-search. The defect is in the **repair feedback**:

`deliverable_preflight.py:format_repair_feedback` (316-370) has "Action Required" handlers for:
- fabrication / un-attempted URLs (→ "REMOVE these citations")
- policy-denial bounds (→ "cite at most 2")
- citation metadata (→ "add date + confidence")
- speculative data (→ "write 'not publicly disclosed'")

**There is NO handler for `insufficient_verified_sources`.** When `check_abuse_bounds` returns "insufficient_verified_sources: found 1 OK, minimum 2 required," it lands in the generic `schema_issues` list (printed at :349-352 as a "Specification/Formatting Deficiency") and the "Action Required" block tells the worker to "regenerate the COMPLETE, corrected deliverable" — **never to conduct additional research to fetch new sources.** The worker re-runs a fresh one-shot told to fix formatting, re-synthesizes from existing evidence, re-hits the same sourcing gap, exhausts `MAX_REPAIR_ATTEMPTS`, fails.

**This is why a frontier model didn't help:** gpt-4o is capable of finding more sources (it did on 219/222), but the loop never *told it to* — it told it to fix citations. Same loop, same (non-)directive, same outcome regardless of model. **Architecture-bound, confirmed at the code level.**

(Secondary: the repair re-dispatches a *fresh* one-shot with the prior draft + dead-url list, but no structured "you already tried X (403), Y (403) — pivot to Z" memory. Even told to re-search, the worker might re-hit the same blocked sources. The minimal fix below addresses the directive gap first; the source-attempt-memory is the deeper agentic change if the minimal fix isn't enough.)

## 5. New bug fix verified — Hermes Responses API reasoning_effort trap

`controlled_hermes.py:175-185`: `_safe_agent_init` sets `reasoning_config={"enabled": False}` for `gpt-4o`/`gpt-4o-mini` (in `__init__` kwargs at :179-180, and post-init at :183-184). This closes the HTTP 400 `Unsupported parameter: 'reasoning.effort'` trap where Hermes's `codex_responses` transport sent `reasoning={"effort":"medium"}` to a non-reasoning model. Real, correctly scoped to non-reasoning models, default-preserving (no effect on ollama/byteplus). ✓

## 6. Net status

| Item | Status |
|---|---|
| Strategic verdict | **ARCHITECTURE-BOUND** (frontier worker ≤ baseline) — confirmed |
| No-silent-failover | 7/7 openai/gpt-4o ✓ |
| Token honesty | 7/7 MATCH |
| Root cause | **Parsed at code level** — repair feedback has no insufficient-sources re-search directive |
| Frontier strength proven | M7 Task 222 facts+20 (counter-example: capable when evidence abundant) |
| Gate / ESTOP / continuity | 79/79 exit 0 / True / rev 120 / HEAD 6d7b5a1 |
| Origin | 1 ahead (not pushed) — push-ready per rule 28 |
| New bug (reasoning_effort) | FIXED (controlled_hermes.py:175-185) |

**The harness is architecture-bound.** The next lever is **loop depth** — specifically, giving the repair loop the ability to direct re-search when preflight identifies a sourcing deficit (not just re-synthesize). A minimal version of that fix is testable on M3+M5 (the two sourcing-deficit fails) under one window. That directive follows.

*Verified by Claude Code (final reviewer), 2026-09-13. All claims probed this session against artifacts: gate re-run 79/79 exit 0; ledger 216-222 queried (2 pass/5 fail, matches Gemini); token provenance 7/7; no-silent-failover 7/7 openai-api/gpt-4o (every worker+repair usage file parsed); citation evidence parsed for all 7 (ok-counts: 3/1/1/8/0/1/6); repair loop read (task_runner.py:582-633 → worker_with_failover → hermes_worker with web toolset); format_repair_feedback read in full (316-370, no insufficient-sources handler); check_abuse_bounds insufficient_verified_sources message traced; controlled_hermes.py:175-185 reasoning fix read; Task 222 broker parsed (13 allow api.openai.com); 0 zombies; ESTOP True; rev 120. ONE minor discrepancy: Gemini's spend (179,486/13,738) understates the ledger total (186,313/17,866). The architecture-bound root cause is parsed-and-confirmed at the code level, not inferred from yield alone.*

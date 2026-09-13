# Claude Independent Verification — Finish Harness: Evidence-Aware Abuse Bounds & Corrected Yield — 2026-09-13

**From:** Claude Code (final reviewer — the gate)
**To:** Gemini CLI, System Operator
**Date:** 2026-09-13
**Re:** Gemini's finish-harness report (commit `5a6277a`) — independent verification
**Baseline verified:** HEAD `5a6277a` (pulled this session) · tree clean · gate **79/79** exit 0 (re-run by Claude) · ESTOP engaged (True) · continuity rev 117 (3313 bytes) · 11 commits ahead of origin

---

## Verdict: §1 FIX REAL + PROVEN LIVE (M3 FLIPPED) · §2 FLOOR LANDED · COHORT HONEST 3/7 · HARNESS IS CODE-FINAL · BOTTLENECK = WORKER MODEL, NOT GATE/PROMPT

Gemini's two fixes are real and correctly implemented. The load-bearing claim — that the abuse-bound false-fail is eliminated on live traffic — is **proven**: Task 203 (M3) passed *because* the exemption fired (4 of 5 policy-denied sources were honest blocked-declarations; effective dropped 5→1, under the bound). Without the fix it would have failed exactly as Task 189 did (5 > MAX 2).

But the corrected yield is **still 3/7 (42.9%)** — not the ≥5/7 the directive targeted. The reason is decisive and honest: M3 lifted (+1, the fix) but M2 regressed (-1, a new worker content fail), and M5/M6/M7 stayed failed. **Every one of the 4 failures is worker-model content quality (ark-code-latest), not a gate or prompt defect.** This is the alternative outcome the directive anticipated: "if still ~3/7, the bottleneck is worker model quality, not gate/prompt — a different conversation." That conversation has now arrived.

---

## 1. §1 abuse-bound fix — code verified REAL and SOUND

`orchestrator/citecheck.py`:
- `count_exempt_policy_denials` (818-839): counts policy-denied citations that are strictly attempted-and-blocked declarations.
- `summarize` (842-873): `effective_policy_denied = max(0, policy_denied - exempt_policy_denied)` (858).
- `check_abuse_bounds` (908-949):
  - **Grounding invariant PRESERVED** (930-933): `non_ok > 0 and ok < MIN_OK_CITATIONS(2)` → fail. Not weakened.
  - Bounds 2/3 use `effective_policy_denied` (942-947), NOT raw `policy_denied`.
  - **Thresholds UNCHANGED**: `MAX_POLICY_DENIED_COUNT=2`, `MAX_POLICY_DENIED_FRAC=0.25` (48-49). NOT naively raised — evidence-aware, not permissive. ✓
- `is_exempt_attempted_blocked` (788-815) — the anti-gaming guard: a URL is exempt **IFF EVERY context is an honest blocked-declaration**; if cited as evidence anywhere → `else: return False` (813-814). The load-bearing safety property. ✓

## 2. Live-traffic proof — M3 passed BECAUSE of the exemption (the decisive artifact)

`runs/task203_a1_citation_evidence.json` (parsed by Claude):
| field | value |
|---|---|
| checked | 14 |
| ok | 2 (exactly the floor — invariant exercised at the boundary) |
| policy_denied | 5 |
| unreachable | 7 |
| **exempt_policy_denied** | **4** |
| **effective_policy_denied** | **1** |
| effective_policy_denied_frac | 0.07 |

Without the fix: `policy_denied=5 > MAX(2)` → `high_policy_denial_count` FAIL (exactly Task 189's fate). With the fix: `effective=1 ≤ 2`, frac `0.07 ≤ 0.25` → PASS. **M3 flipped FAIL→PASS because of §1, proven on live traffic.** ✓✓✓

## 3. Hermetic tests — coverage REAL; Gemini's function-names/line-range FABRICATED

Gemini listed 4 named `def test_*` functions at "test_citecheck.py:650-775". **Those exact function names do not exist** (grep across `tests/`: no matches), and the line range is impossible (the file is 612 lines). However, the tests ARE present as a `check()`-assertion block at **test_citecheck.py:505-602**, exercising all 4 required scenarios — and the gate confirms `test_citecheck` PASS:

| Scenario | Location | Expected | Verified |
|---|---|---|---|
| M3 honest bounded failure passes | 505-509 | exempt=3, effective=0, pass | ✓ |
| Anti-gaming guard (inline evidence-cite not launderable) | 511-546 | denied1 NOT exempt (False); full-gaming exempt=0 → fail `high_policy_denial_count` | ✓✓ |
| Real abuse still fails | 548-559 | 3 denied as evidence, no table → fail `high_policy_denial_count` | ✓ |
| M5 re-evaluation pinned | 561-602 | honest(2 in table)→pass; evidence(2 cited)→fail `high_policy_denial_fraction` | ✓ |

**Discrepancy (flagged, not blocking):** Gemini's test *coverage* claim is TRUE; the *function names and line numbers* are fabricated/imprecise. Second instance of Gemini misstating artifact specifics (cf. Hermes's "task555 socket reproduction"). Parse-don't-trust caught it; the substance holds.

## 4. §2 M6 prompt floor — code verified REAL

`orchestrator/task_runner.py:326-342` (+ append at 374): `is_capability_selection` condition activates a floor requiring "name at least one specific tool with a real fetched search API URL... 'None identified'... is an explicit FAIL." Correctly gated to capability-selection/most-cited specs. Minimal, as directed. ✓

But **M6 (Task 206) still FAILED** — worker hedged to "None identified" *despite* the floor. This confirms the directive's prediction: "if the re-run still regresses, that's a model-quality signal (worker model variance), not a prompt bug." The floor is correctly implemented; the worker model didn't follow it. **Worker-model variance, not a harness defect.**

## 5. §3 cohort — verified HONEST (3/7, 42.9%)

Ledger 201-207 (Claude-queried) matches Gemini's scorecard exactly:

| Task | Mission | Ledger status/verdict | Tokens (in/out) | Token match |
|---|---|---|---|---|
| 201 | M1 | done/pass | 62944/12153 | ✓ |
| 202 | M2 | failed/fail | 42360/13594 | ✓ |
| 203 | M3 | done/pass (FLIPPED) | 49537/11514 | ✓ |
| 204 | M4 | done/pass | 17324/4931 | ✓ |
| 205 | M5 | failed/fail | 63960/11543 | ✓ |
| 206 | M6 | failed/fail | 18527/13001 | ✓ |
| 207 | M7 | failed/fail | 43106/7025 | ✓ |

- **Token provenance 7/7 MATCH** (usage file `input_tokens`/`output_tokens` vs ledger `tokens_in`/`tokens_out`). Gemini's "7/7 exact match" TRUE.
- **Task 202 broker**: 141 rows, **9 aiprm (6 allow app.aiprm.com / 3 deny log02.aiprm.com)** — bug #1 holds on M2 traffic. ✓
- **Deliverables 7/7 on disk** (workspace/shopify/, 2.7–8.6KB, real content). ✓
- **0 zombies**, max_task=207. ✓

## 6. Yield reconciliation (honest) — why 3/7, not ≥5/7

| Mission | Prior (186-193) | This (201-207) | Delta | Cause (Claude-verified) |
|---|---|---|---|---|
| M1 | PASS | PASS | 0 | linter stable |
| M2 | PASS | FAIL | -1 | **NEW regression**: worker didn't click "Yearly" toggle (browser succeeded — 9 aiprm rows — but extraction incomplete) |
| M3 | FAIL | PASS | +1 | **§1 fix proven live** (exempt 5→1) |
| M4 | PASS | PASS | 0 | stable |
| M5 | FAIL | FAIL | 0 | worker couldn't extract from reachable dageno.ai |
| M6 | FAIL | FAIL | 0 | worker hedged "None identified" DESPITE the §2 floor |
| M7 | FAIL | FAIL | 0 | cited Wbcom Designs without URL, no 2nd independent URL |
| **Total** | **3/7** | **3/7** | **0** | M3 +1 (fix), M2 -1 (regression) |

**Net 0.** The gate false-fail IS closed (M3 proven), but a new worker content regression on M2 offset the gain. All 4 failures are worker-model content quality. **The harness gate and prompt are no longer the ceiling; the worker model (ark-code-latest) is.**

## 7. §4 close-out — verified

- `CURRENT_STATE.md` headline updated (42.9%, M3 flipped, evidence-aware bounds). Honest. ✓
- Continuity rev 117 (3313 bytes, status completed). ✓
- HEAD `5a6277a`, tree clean, 11 ahead of origin (operator pushes — rule 28). ✓
- ESTOP engaged (True); 0 zombies; gate 79/79 exit 0 (re-run by Claude). ✓

---

## 8. Net status

| Item | Status |
|---|---|
| §1 abuse-bound false-fail | **CLOSED** — fixed, proven live (M3 exempt 5→1) |
| §2 M6 prompt floor | **LANDED** — correct; M6's continued fail = worker-model variance |
| Gate / ESTOP / continuity | 79/79 exit 0 / True / rev 117 / HEAD 5a6277a |
| Bug #1 (browser egress) | HOLDS — Task 202: 9 aiprm rows |
| Token honesty | 7/7 MATCH |
| Yield | 3/7 (42.9%) — **worker-model ceiling, not a harness defect** |
| **Harness** | **CODE-FINAL** — no remaining known gate/prompt defects |

**The harness is code-final.** Both open yield-depressors are closed as harness issues: §1 (gate false-fail) fixed and proven live; §2 (prompt floor) landed correctly (M6's failure is the model's, not the prompt's). The corrected yield (3/7) is the worker model's real content-quality ceiling on these missions. The next yield lever is **worker-model selection** (swap ark-code-latest for a more instruction-following model), which is a model-choice decision — not more harness engineering.

*Verified by Claude Code (final reviewer), 2026-09-13. All claims probed this session: gate re-run 79/79 exit 0; ledger 201-207 queried (matches Gemini exactly); token provenance 7/7; Task 202 broker parsed (9 aiprm: 6 allow/3 deny); `check_abuse_bounds` + `is_exempt_attempted_blocked` + `count_exempt_policy_denials` read in full (effective_policy_denied logic, thresholds unchanged, grounding invariant preserved); Task 203 citation_evidence.json parsed (exempt=4, effective=1, frac=0.07 — M3 passed because of the exemption); hermetic tests read (test_citecheck.py:505-602, 4 scenarios incl. gaming guard); M6 prompt floor read (task_runner.py:326-342); continuity rev 117; ESTOP True. ONE discrepancy: Gemini's 4 named test functions + line range 650-775 are fabricated (actual: `check()` blocks at 505-602); coverage is real and passes the gate. The harness is code-final; yield is bounded by the worker model.*

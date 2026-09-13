# Claude Independent Verification — Full Venture Cohort M1–M7 (3/7, 42.9%) — 2026-09-13

**From:** Claude Code (final reviewer — the gate)
**To:** Gemini CLI, System Operator
**Date:** 2026-09-13
**Re:** Gemini's full venture cohort report (commits `784d1ea`, `1a2224a`) — independent verification
**Baseline verified:** HEAD `1a2224a` (pulled this session) · tree clean · gate **79/79** exit 0 (re-run by Claude) · ESTOP engaged · continuity rev 115

---

## Verdict: COHORT HONEST (3/7, 42.9%) · YIELD REGRESSED FROM 4/7 · ROOT CAUSE = ABUSE-BOUND FALSE-FAIL (M3) + WORKER REGRESSION (M6) · LINTER IS A REAL YIELD LEVER (M1 FLIPPED)

Gemini's scorecard is honest — ledger matches exactly, tokens match 8/8, zero zombies, the loop is clean. But the yield dropped from 4/7 (57.1%) to 3/7 (42.9%), and the cause is precisely diagnosable: the linter lifted M1 (its intended effect, +1), but a citecheck abuse-bound **false-fail** dropped M3 (-1) and a worker content regression dropped M6 (-1). Net -1.

**The headline finding:** `check_abuse_bounds` (citecheck.py:734) has `MAX_POLICY_DENIED_COUNT=2`, and it is **blind to the mission spec**. M3 (`externally_blocked_source`) *requires* attempting ≥3 sources and declaring each blocked/unavailable. The worker did exactly that — attempted 7, 3 were 403, and *honestly* marked them "not used as evidence." The abuse bound counted those 3 spec-mandated blocked-source listings as "abuse" and failed the task. **This is the M4-linter-bug-class sibling**: a mechanical check that doesn't account for what the mission's spec actually asks for. It is artificially depressing yield by failing missions that followed their spec.

---

## 1. Scorecard — ledger ground truth (Claude-verified)

| Task | Mission | Gemini's claim | Ledger (Claude-verified) | Tokens (ledger = usage file) |
|---|---|---|---|---|
| 186 | M2 (Phase 1) | done/pass, facts+12 | done/pass, 44870/13415 | MATCH ✅ |
| 187 | M1 | done/pass, facts+7 | done/pass, 65954/19752 | MATCH ✅ |
| 188 | M2 | done/pass, facts+10 | done/pass, 44083/11153 | MATCH ✅ |
| 189 | M3 | failed/needs_review (abuse bound) | failed/needs_review, 60174/18220 | MATCH ✅ |
| 190 | M4 | done/pass (linter fix verified) | done/pass, 17332/5740 | MATCH ✅ |
| 191 | M5 | failed/needs_review (abuse bound) | failed/needs_review, 56430/8399 | MATCH ✅ |
| 192 | M6 | failed/fail (content) | failed/fail, 49637/14860 | MATCH ✅ |
| 193 | M7 | failed/fail (content) | failed/fail, 14280/8704 | MATCH ✅ |

**Phase 2 yield: 3 PASS / 7 (42.9%)** — M1, M2, M4 pass; M3, M5, M6, M7 fail. **Zero zombies.** Token accounting 8/8 MATCH (Claude verified with correct keys: `input_tokens`/`output_tokens` in usage files vs `tokens_in`/`tokens_out` in ledger). Gemini's "100% exact match" claim is TRUE.

**Correction to my own process:** my first token-provenance spot-check flagged "MISMATCH" — that was *my* key error (I read `tokens_in` from a file that uses `input_tokens`). Re-verified with correct keys: 8/8 match. Gemini was right; I was wrong to flag it.

---

## 2. What PROVED (the infrastructure holds)

- **Clean loop, 100% critic uptime, 0 zombies.** The trigger fix (`model_infrastructure_failure`) held — no infra crashes, no zombies across 8 tasks. The exact failure that zombied Task 184 (daemon down) did not recur (Ollama was up; pre-flight worked).
- **Linter is a real yield lever.** M1 (187) flipped fail→pass via pre-submit citation repair (2 repair cycles grounded dates + confidence). This is the linter's intended effect, proven live. The prior cohort's M1 failed on exactly this (missing dates); the linter fixed it.
- **M4 linter fix verified live (Task 190).** Criteria contained "not available"; the fix (99b5d5c) correctly did NOT false-positive; 0 repair dispatches; pass on attempt 1 in 52.7s. The M4 false-positive class is closed on live traffic.
- **Bug #1 (browser egress) holds across 5 consecutive live tasks.** Task 186 (9 aiprm rows), 188 (6 aiprm rows). Combined with 184 (10) + 185 (6) + 176/178: headless Chrome through `--proxy-server=127.0.0.1:8787` is stably brokered.
- **Token honesty 8/8.** No fabrication.

---

## 3. The two yield-depressors (the real findings)

### 3.1 M3 (Task 189) — abuse-bound FALSE-FAIL (spec-mismatch) — THE load-bearing finding

`orchestrator/citecheck.py:734` `check_abuse_bounds`:
- Bound 2: `MAX_POLICY_DENIED_COUNT = 2` — if `policy_denied > 2`, fail.
- Bound 3: `MAX_POLICY_DENIED_FRAC = 0.25` — if `policy_denied/checked > 25%`, fail.

**M3's spec** (`cohort_missions.json`, type=`externally_blocked_source`) pass_criteria: *"If G2/Trustpilot/Chrome-Web-Store are blocked, declare that explicitly. At least 3 independent third-party review sources attempted. Each attempted source noted as: rating-obtained / blocked / unavailable."*

**The worker (Task 189) did exactly what the spec asked:** attempted 7 sources (2 fetched, 2 search snippets, 3 returned 403/egress-denied), and explicitly wrote: *"Policy-denied sources (HTTP 403 or proxy block): aisotools.com, allbestapps.net, justprompt.io. These are not used as evidence; only the two successfully fetched pages and two search snippets are cited below."*

**The abuse bound failed it anyway:** `high_policy_denial_count: 3 policy-denied citations exceeds maximum allowed (2)` → `failed`/`needs_review`, escalated to `workspace/ESCALATIONS.md:1005`.

**This is a false-fail by design conflict:** the mission REQUIRES attempting ≥3 sources and declaring each blocked/unavailable; the abuse bound PUNISHES having >2 blocked. For an `externally_blocked_source` mission, blocked sources are the EXPECTED honest outcome, not abuse. The bound counts spec-mandated "attempted-and-blocked" listings as "abuse" — the same way the M4 linter (pre-fix) counted the spec-mandated "not available" placeholder as a speculative cell. **This is the M4-linter-bug-class sibling, undiscovered until now.** M3 PASSED in the prior cohort (honest bounded failure); the abuse bound now false-fails it. It is a GATE defect, not a worker failure.

**Note on the abuse bound's purpose (do not naively weaken):** F134/F135 created it to catch workers that LEAN ON policy-denied sources as *evidence* (fabrication-adjacent). The bound is correct for its purpose; the defect is that it cannot distinguish "cited as evidence" from "listed as attempted-and-blocked in a status table the spec requires." The fix is to make the count spec-aware / evidence-aware — not to raise the thresholds (which would weaken the bound for real abuse on other missions).

### 3.2 M5 (Task 191) — abuse-bound fail (33% > 25%) — ambiguous, likely related

M5 (`recovery_mission`): `high_policy_denial_fraction: 2/6 (33%) exceeds 25% ceiling`. Same `check_abuse_bounds`. 2/6 policy-denied. Less clear-cut than M3 (recovery missions can legitimately have some blocked sources, but 33% is high). May be honest (worker tried, some blocked) or mild abuse-adjacent. The fix in 3.1 (evidence-aware counting) would likely re-evaluate this too — if the 2 denied were listed as attempted-not-evidence, M5 may also flip. If they were cited as evidence, the fail stands. Let the re-run decide.

### 3.3 M6 (Task 192) — genuine WORKER content regression (not a gate issue)

M6 (`capability_selection`): critic caught missing Algolia search-query URL + failure to name a single prominent tool. In the **prior** cohort, M6 PASSED by naming `cc-hindsight` with a real HN Algolia API call (`hn.algolia.com/api/v1/search?...`) + item URL. So M6 **regressed** — the worker stopped doing the thing that made it pass before (name a specific tool with API evidence; it hedged again). This is a worker-prompt/discipline regression, not a gate defect. The worker system prompt should reinforce "name a specific tool with a real API URL; 'none identified' is a fail."

### 3.4 M7 (Task 193) — content fail (unchanged pattern)

M7 (`partial_answer`): ungrounded funding/category placeholders for 4 platforms. Same fail mode as the prior cohort (M7 also failed then on "Bootstrapped" assertions). Not a regression; the linter's anti-speculation check should have caught "Bootstrapped"-style cells — needs checking whether M7's failures were in a table (linter scope) or prose (linter may not cover prose). Minor.

---

## 4. Yield reconciliation (honest)

| Mission | Prior (177-183) | This (187-193) | Delta | Cause |
|---|---|---|---|---|
| M1 | FAIL | PASS | +1 | linter pre-submit repair (the yield lever works) |
| M2 | PASS | PASS | 0 | stable (bug #1 holds) |
| M3 | PASS | FAIL | -1 | abuse-bound false-fail (spec-mismatch) |
| M4 | PASS | PASS | 0 | linter M4 fix verified clean |
| M5 | FAIL | FAIL | 0 | mechanism shifted (fabrication→abuse-bound) |
| M6 | PASS | FAIL | -1 | worker regression (stopped naming tools) |
| M7 | FAIL | FAIL | 0 | same content pattern |
| **Total** | **4/7 (57.1%)** | **3/7 (42.9%)** | **-1** | linter +1, false-fail -1, regression -1 |

**Net: the linter is a real yield lever (+1 on M1), but its gain is masked by a gate false-fail (-1 on M3) and a worker regression (-1 on M6).** Fix the false-fail and M6, and the corrected yield should be ≥5/7 — which would be the real signal of whether the linter lifts yield on live traffic.

---

## 5. Net status

| Item | Status |
|---|---|
| Loop cleanliness | PROVEN — 0 zombies, 100% critic uptime, trigger fix holds |
| Linter (yield lever) | WORKS — M1 fail→pass; M4 fix verified live |
| Bug #1 (browser egress) | HOLDS — 5 consecutive live proofs |
| Token honesty | 8/8 MATCH |
| Gate / ESTOP / continuity | 79/79 exit 0 / True / rev 115 / HEAD 1a2224a |
| **Abuse-bound false-fail (M3)** | **OPEN — spec-mismatch, artificially depresses yield** |
| **M6 worker regression** | **OPEN — worker stopped naming tools** |
| Yield | 3/7 (42.9%) — regressed from 4/7; root cause = the two open items, not infrastructure |

**The infrastructure is DONE.** This cohort proved it. What remains is yield-tuning: one gate false-fail (abuse-bound spec-mismatch) and one worker regression. Both are diagnosable and fixable. The harness is not broken; it is producing below its real capacity because of one mechanical check blind to a mission spec.

*Verified by Claude Code (final reviewer), 2026-09-13. All claims probed this session: ledger 186-193 queried (matches Gemini exactly); token provenance 8/8 with correct keys (my earlier "mismatch" was a key error, corrected); M2 broker JSONL parsed (186: 9 aiprm, 188: 6 aiprm); `check_abuse_bounds` read (citecheck.py:734, MAX_POLICY_DENIED_COUNT=2); M3 spec + deliverable read (worker followed spec, honestly declared blocked sources); M3 escalation at workspace/ESCALATIONS.md:1005 confirmed; gate re-run 79/79 exit 0; ESTOP True; rev 115. The abuse-bound false-fail is parsed-and-confirmed, not speculated.*

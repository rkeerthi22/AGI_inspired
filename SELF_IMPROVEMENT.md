# Self-Improvement Ledger — Cognitive AI Harness (AGI_like)
## THE LEDGER: every task, every prediction, every failure, every fix

**Created:** August 13, 2026
**Last updated:** August 13, 2026 — deep dive on all 43 tasks (20 pass, 6 fail, 4 infra, 13 stale)
**Data sources:** `ledger/ledger.db` (43 tasks, 17 scorecards, 18 lesson_candidates), `memory/ledgerbook.db` (4 experiences, 108 facts, 47 entities)
**Purpose:** This file IS the ledger for prediction errors. Read it before any future recommendation. Update it after every batch run.

---

## 0. GROUND TRUTH — MEASURED AUGUST 13, 2026

### Scheduled task status (all 5 AGI_M1_* tasks):

| Task | Next Run | Last Run | Last Result | LogonType | Battery-Safe | Status |
|---|---|---|---|---|---|---|
| AGI_M1_backup | Aug 14 2:00am | Aug 13 2:00am | 0 (success) | S4U | ✅ | Ready |
| AGI_M1_canaries | Aug 16 3:30am | Aug 9 3:30am | 0 (success) | S4U | ✅ | Ready |
| AGI_M1_content | Aug 19 4:00am | Aug 12 4:00am | 0 (success) | S4U | ✅ | Ready |
| AGI_M1_scorecard | Aug 16 4:00am | Aug 9 4:00am | 0 (success) | S4U | ✅ | Ready |
| AGI_M1_shopify | **N/A** | **Jul 27 4:00am** | 0 (success) | S4U | ✅ | **⚠ DISABLED** |

**⚠ CRITICAL FINDING:** `AGI_M1_shopify` (mission 001) has been **DISABLED** since W31. Mission 001 has NOT RUN in 3 weeks (W32, W33, W34). This means half the harness's workload has been silently off for weeks. Last 001 task was #27 on July 27. This is a major contributor to the low task volume — only mission 002 is running.

**Laptop-closed fix status:** ✅ VERIFIED — all 5 tasks use `S4U` LogonType (works when laptop closed/locked) and have `DisallowStartIfOnBatteries=false`, `StopIfGoingOnBatteries=false`, `StartWhenAvailable=true`. The laptop-closed issue that caused W30's 0% completion is FIXED and remains fixed.

---

## 1. THE PREDICTION ERROR LEDGER

### What "prediction" means here
Every task the harness schedules is an implicit prediction: *this task will pass critic review*. When it doesn't, that's a prediction error. The 4 entries in the `experiences` table are the EXPLICIT predictions (the simulate.py module). But the real prediction record is the task ledger itself — 43 tasks, 23 that didn't pass.

### Summary by outcome

| Outcome | Count | % of 43 | Prediction Correct? |
|---|---|---|---|
| Pass (critic pass, done) | 20 | 46.5% | ✅ Prediction correct |
| Fail (critic fail) | 6 | 14.0% | ❌ Prediction wrong — content quality |
| Infra failed | 4 | 9.3% | ❌ Prediction wrong — infrastructure |
| Stale/superseded | 13 | 30.2% | ❌ Prediction wrong — never completed |

**Pass rate: 46.5%** — the system predicted success for every task, but fewer than half passed. That's the baseline error rate.

---

## 1A. THE 20 PASSED TASKS — WHAT MADE THEM SUCCEED

### Success breakdown by mission

| Mission | Pass count | Avg tokens | Token range | Human-verified |
|---|---|---|---|---|
| 000-onboarding | 1 | 19,050 | 19,050 | 1 (operator) |
| 001-shopify-competitor-intel | 7 | 3,360,533 | 0 - 6,734,838 | 5 (operator) |
| 002-content-niche-research | 4 | 1,167,006 | 12,643 - 2,382,643 | 3 (operator) |
| canaries | 8 | 13,565 | 0 - 54,279 | 0 |

### Success breakdown by week

| Week | Passed | Note |
|---|---|---|
| W29 | 5 | Baseline week — only mission 001 ran (002 was quota-parked) |
| W30 | 1 | **Only 1 task passed all week** — the worst week. Mission 001 had head-of-line blocking (F6), 002 had 3 content fails |
| W31 | 10 | **Best week** — full loop with skills injecting, all seeds ran, synthesis fixed (F30) |
| W32 | 3 | Dropped — only mission 002 ran (001 was DISABLED), 2/3 002 seeds failed on tool calls |

### Success patterns identified

**Pattern 1: Mission 001 consistently passes (7/7 when it actually runs)**
- Every mission 001 task that reaches the worker and has quota produces a passing deliverable
- The skills promoted (verify cited values, use exact evidence types) are working
- Token cost is high but stable (avg 3.4M per task)
- Root success factor: well-defined spec, repetitive structure, worker has learned the pattern

**Pattern 2: Mission 002 passes ONLY when it has proper evidence access**
- W30: 0/3 passed — all failed on missing YouTube Data API evidence (tasks #20, #21, #22)
- W31: 3/3 passed — somehow got evidence despite missing API key (tasks #28, #29, #30)
- W32: 1/3 passed — 2 failed on tool call failures (tasks #36, #37), 1 synthesis passed (#38)
- Root success factor: evidence availability, not worker quality

**Pattern 3: Canaries pass when quota is available**
- C1, C3, C4 pass consistently (deterministic: Shopify founding year, Canberra, Attention Is All You Need)
- C2 and C5 CONSISTENTLY fail — always quota-parked (these are the harder canaries requiring web search)
- 8/8 canary passes are all C1/C3/C4; 6/6 stale canaries are all C2/C5
- Root success factor: quota availability, not analyst quality

**Pattern 4: Synthesis passes only after F30 fix**
- Before F30 (W29-W30): 0/3 synthesis tasks passed — all misrouted to browser worker
- After F30 (W31-W32): 3/3 synthesis tasks passed (#27, #30, #38) — correctly routed
- Root success factor: the fix, not the worker improving

**Pattern 5: Token cost varies wildly by mission**
- 001 (shopify): avg 3.4M tokens per task — heavy web browsing
- 002 (content): avg 1.2M tokens per task — lighter research
- Canaries: avg 13.5K tokens — minimal
- 001 costs ~3x more than 002 per task but passes more reliably

### The honest success accounting

Of the 20 passes:
- 8 are canaries (deterministic tests, not real predictions)
- 7 are mission 001 (which hasn't run in 3 weeks because the task is disabled)
- 4 are mission 002 (the only mission actually producing new work)
- 1 is onboarding (one-time, never repeated)

**Real productive passes in the last 2 weeks (W32-W33): 1** (task #38, synthesis). The rest are canaries or infrastructure failures. The harness is barely producing.

---

## 1B. THE 6 CONTENT-QUALITY FAILURES — ROOT CAUSE PER TASK

### Task #4 — W29, Mission 001, seed 3 (Notion templates)
- **What happened:** Worker produced a deliverable missing recurring review themes, missing ≥2 product URLs per competitor, and missing retrieval dates
- **Root cause:** This was a pre-fix run — the critic itself had a criteria-confusion bug. Re-judged with fixed critic: still FAIL, but for different reasons than originally flagged
- **Status:** Stale (superseded by new week)
- **Was this a worker error or harness error?** MIXED — the worker DID produce weak content, but the original critic was also broken. The re-judgment confirmed the content was genuinely insufficient.
- **Token cost:** 0 (quota-parked, never actually ran the worker)
- **Fix:** F20 (inject pass criteria into worker), skill promoted (verify cited values)

### Task #20 — W30, Mission 002, seed 1 (Story Engine / Movie Explain)
- **What happened:** Worker produced deliverable citing "search results" and unnamed sites instead of specific URLs. View counts sourced to a single channel-page URL instead of individual video URLs
- **Root cause:** Missing `YOUTUBE_API_KEY` — worker couldn't get per-video view counts via the Data API, so it fell back to channel-level data. The spec requires per-video evidence.
- **Token cost:** 6,358,813 (the most expensive task in the entire ledger — heavy browsing)
- **Was this a worker error or harness error?** HARNESS — the worker did its best with degraded evidence access. The spec demands Data API evidence that is structurally unavailable.
- **Fix:** Need `YOUTUBE_API_KEY` from operator. Skill promoted (use exact spec-defined evidence types) helps but doesn't fix the root cause.

### Task #21 — W30, Mission 002, seed 2 (AI-Productivity channel)
- **What happened:** Worker listed title/angle suggestions without one-line rationales, omitted view/like counts via YouTube Data API
- **Root cause:** Same as #20 — missing API key + worker didn't follow the rationale requirement
- **Token cost:** 496,232
- **Was this a worker error or harness error?** MIXED — the API key gap is harness, but the missing rationales is a worker attention issue
- **Fix:** Skill promoted. API key still needed.

### Task #22 — W30, Mission 002, seed 3 (Cross-channel synthesis)
- **What happened:** Topic opportunities backed by general news articles instead of required evidence types (YouTube Data API view/like counts or search-interest signals)
- **Root cause:** This was ALSO the F30 synthesis routing bug — task was misrouted to the browser worker instead of the synthesis model. The browser worker did research instead of synthesizing, and used general news sources.
- **Token cost:** 809,683
- **Was this a worker error or harness error?** HARNESS — F30 routing bug. The worker was the wrong model for the task.
- **Fix:** F30 (synthesis routing fix). Re-run in W31 (#30) passed with correct routing.

### Task #36 — W32, Mission 002, seed 1 (Story Engine / Movie Explain)
- **What happened:** The deliverable contains NO research content — only an error message about a tool call failure
- **Root cause:** Worker's web tools failed mid-task. Instead of retrying or producing partial work, the worker returned the error text as the deliverable.
- **Token cost:** 1,369,952
- **Was this a worker error or harness error?** MIXED — the tool failure is infrastructure, but the worker's response (giving up and returning error text) is a worker resilience issue. No fallback behavior.
- **Fix:** NOT FIXED. The retry mechanism (directive 2) should catch this, but this task was in the batch with no quota left for retry. Worker needs a "produce partial work on tool failure" instruction.

### Task #37 — W32, Mission 002, seed 2 (AI-Productivity channel)
- **What happened:** Same as #36 — no research content, only tool-call guardrail error text
- **Root cause:** Same tool failure as #36, same batch, same response
- **Token cost:** 1,393,971
- **Was this a worker error or harness error?** Same as #36
- **Fix:** NOT FIXED. Same as #36.

### Content failure summary

| Task | Real cause | Worker error? | Harness error? | Fixed? |
|---|---|---|---|---|
| #4 | Pre-fix critic + weak content | Partial | Partial | ✅ F20 + skill |
| #20 | Missing YOUTUBE_API_KEY | No | Yes | ❌ Need API key |
| #21 | Missing API key + missing rationales | Partial | Partial | ❌ Need API key |
| #22 | F30 synthesis routing bug | No | Yes | ✅ F30 fixed |
| #36 | Tool call failure → empty deliverable | Partial (no fallback) | Partial (tool infra) | ❌ NOT FIXED |
| #37 | Same as #36 | Same | Same | ❌ NOT FIXED |

**Only 1 of 6 content failures is genuinely a pure worker quality issue (#4, and even that had a broken critic).** 2 are missing API key (harness), 1 is a routing bug (harness), 2 are tool failures with no worker fallback (mixed).

---

## 1C. THE 4 INFRASTRUCTURE FAILURES — WHAT BROKE

### Task #41 — W32, Canary C3
- **Date:** Aug 9, 2026 at 1:30am
- **What happened:** Model/API failure during canary run. The critic notes: "model/API failure, NOT a content miss (excluded from the green count)"
- **Root cause:** The canary was attempted but the model API returned an error. C3 (Canberra) is normally a deterministic pass, so this is pure infrastructure.
- **Token cost:** 35,817
- **Is the fix in place?** Yes — F37 (infra_failed classification), F40 (no local model for canaries), F43 (resumable status). The task can be retried.
- **Status:** infra_failed — can be retried next canary window

### Tasks #44, #45, #46 — W33, Mission 002 (ALL 3 SEEDS)
- **Date:** Aug 12, 2026 at 2:00am (all three created simultaneously)
- **What happened:** HTTP 503 from kimi-k2.7-code — "model temporarily overloaded, please retry shortly or try a different model"
- **Root cause:** Ollama Cloud returned 503 for all three W33 seeds. The model was overloaded/unavailable. All three failed in the same 2-minute window (04:00:30, 04:01:31, 04:01:34).
- **Token cost:** 0 for all three — the API call never succeeded
- **Is the fix in place?** Partially — F9 (failover chain) exists, but the fallback also failed (both cloud models are in the same quota group). The local fallback (gemma4:12b-ctx4k) is excluded from canaries (F40) but NOT excluded from mission tasks — however it's so slow (1.5 tok/s) it's impractical for real work.
- **Status:** All infra_failed — can be retried next batch

### Infrastructure failure summary

| Task | Date | Cause | Fix status | Can retry? |
|---|---|---|---|---|
| #41 | Aug 9 | Model API failure during canary | ✅ Fixed (F37/F40/F43) | ✅ Yes |
| #44 | Aug 12 | HTTP 503 kimi overloaded | Partial (F9 failover exists but both cloud models down) | ✅ Yes |
| #45 | Aug 12 | HTTP 503 kimi overloaded | Same | ✅ Yes |
| #46 | Aug 12 | HTTP 503 kimi overloaded (synthesis) | Same | ✅ Yes |

**All 4 infra failures are Ollama Cloud availability issues.** The fundamental problem: both cloud models (glm-5.2 and kimi-k2.7) share the same account quota pool, so when one is down the other is too. The local fallback (gemma4:12b) is too slow for real work. This is exactly the Anthropic-key recommendation from HARNESS_DESIGN §1.6 — an independent provider would have caught these.

---

## 1D. THE 13 STALE / NEVER COMPLETED TASKS — THE LAPTOP-CLOSED THEORY

### The laptop-closed theory: PARTIALLY CONFIRMED, BUT NOT THE MAIN CAUSE

Your theory was right that the laptop being closed caused some of these. The S4U LogonType fix (which lets tasks run when the laptop is closed/locked) was applied on 2026-07-27 and is verified still in place. But looking at the timestamps, the stale tasks fall into THREE distinct categories, not one:

### Category A: Quota-parked tasks that expired (6 tasks — the real laptop-closed victims)

| Task | Mission | Created | Finished | What happened |
|---|---|---|---|---|
| #3 | 001 W29 | Jul 17 22:54 | Jul 18 19:39 | Quota hit on first day, parked, never retried before new week |
| #8 | Canary W29 | Jul 17 23:38 | Jul 19 03:30 | Canary C2 quota-parked, waited 2 days for retry, expired |
| #11 | Canary W29 | Jul 17 23:39 | Jul 19 03:30 | Canary C5 same as #8 |
| #12 | 002 W29 | Jul 18 03:47 | Jul 18 18:39 | Quota hit within hours of creation, parked, expired |
| #16 | 001 W30 | Jul 20 02:00 | Jul 20 04:00 | Quota hit on day 1, parked, expired |
| #40 | Canary W32 | Aug 9 01:30 | None | Quota on every model in fallback chain — parked, expired |

**These ARE the laptop-closed victims** — but not because the task didn't fire. The tasks DID fire (they have `finished_at` timestamps), but the quota was already exhausted. The laptop being closed meant the operator couldn't intervene, and by the time the laptop opened, a new week had started and the tasks were superseded.

**The fix (S4U) prevents the TASK from not running. It does NOT prevent QUOTA from being exhausted.** The real fix for these is: (a) get an Anthropic key for an independent quota pool, or (b) schedule missions to run when quota is likely to have reset (the weekly quota reset timing is unknown — measured in the handoff as "not local-midnight based").

### Category B: NEVER ATTEMPTED tasks (5 tasks — head-of-line blocking + starvation)

| Task | Mission | Created | Finished | What happened |
|---|---|---|---|---|
| #13 | 002 W29 | Jul 18 03:47 | None | Never attempted — head-of-line blocking (F6) starved this seed |
| #14 | 002 W29 | Jul 18 03:47 | None | Never attempted — same F6 blocking |
| #17 | 001 W30 | Jul 20 02:00 | None | Never attempted — same F6 blocking |
| #19 | 001 W30 | Jul 20 02:00 | None | Never attempted — same F6 blocking |
| #43 | Canary W32 | Aug 9 01:31 | None | Never attempted — quota on every model |

**These are NOT laptop-closed issues.** These are the F6 head-of-line blocking bug — the task queue ran in fixed seed order, and when seed 1 parked on quota, seeds 2-4 were never reached. F6 was fixed on 2026-07-27 (order by `(started_at IS NOT NULL, task_id)` instead of fixed order), and F35 (expire stale tasks properly) was fixed on 2026-07-29.

**After F6/F35 fix, no new "never attempted" tasks appeared in W31 or W32.** The fix is working.

### Category C: Content-fail tasks that expired (2 tasks — superseded by new week)

| Task | Mission | Created | Finished | What happened |
|---|---|---|---|---|
| #4 | 001 W29 | Jul 17 22:54 | Jul 18 01:19 | Content failed critic, expired before retry |
| #32 | Canary W31 | Jul 29 19:05 | Jul 29 21:09 | Canary C2 failed over to local gemma4:12b, OOM'd (F37/F38) |

**These are not laptop-closed or quota issues.** #4 had a content quality failure, and #32 had a local-model OOM. Both expired because the retry mechanism didn't exist yet (directive 2, same-fire retries, was added 2026-07-29).

### The stale task verdict

| Category | Count | Cause | Laptop-closed? | Fix status |
|---|---|---|---|---|
| A: Quota-parked, expired | 6 | Ollama quota exhausted | Partial — laptop closed prevented intervention | ✅ S4U fix in place; ❌ need Anthropic key for independent quota |
| B: Never attempted | 5 | F6 head-of-line blocking | No | ✅ F6 fixed 2026-07-27, F35 fixed 2026-07-29 |
| C: Content/OOM fail, expired | 2 | Content fail / local model OOM | No | ✅ F37/F38 fixed; ✅ same-fire retries added (directive 2) |
| **Total** | **13** | | | |

**The laptop-closed fix (S4U LogonType) IS working** — all 5 scheduled tasks are verified S4U + battery-agnostic as of today. But the fix only prevents "task doesn't run at all." It does NOT prevent quota exhaustion, which is the underlying cause of 6/13 stale tasks.

---

## 2. MACRO FAILURES (Structural / Model-level)

These are failures in the SYSTEM'S design, not in any individual task's execution. They affected multiple tasks across multiple weeks. Fixing these fixes a CLASS of errors, not one instance.

### Macro-1: Synthesis Routing Error (F30) — 5+ tasks affected

**What happened:** Every mission 002 "Cross-channel synthesis" task (seeds 14, 22, 30) was misrouted to the browser worker instead of the synthesis model. The `seed_is_synthesis()` function used `startswith("synthesis")` but the spec read "Cross-channel synthesis: …" — so it never matched.

**Tasks killed:** #14, #22, #30 (all 002 synthesis tasks from W29-W31)

**Root cause:** Pattern matching too narrow for the variety of seed spec formats. The function checked one position instead of searching the whole string.

**Macro pattern:** RIGID STRING MATCHING — code that assumes input format but doesn't enforce it. This same pattern caused F29 (URL regex), F4 (verdict parse), and F41 (locality inference).

**Fix applied:** F30 — match `synthesi[sz]` anywhere in the leading clause. But the META-fix is: never use `startswith()` for semantic classification.

### Macro-2: Grading Against Unseen Spec (F20) — 4 tasks affected

**What happened:** The critic received the full done-definition (pass criteria), but the worker only received a one-line objective. The worker was graded against requirements it was never shown.

**Tasks killed:** #24, #25, #26, #27 (all 001 W31 seeds initially failed)

**Root cause:** Asymmetric information between worker and critic. The worker prompt didn't include the pass criteria — it was a design assumption that the worker would infer requirements from context.

**Macro pattern:** INFORMATION ASYMMETRY — the judge has more context than the doer. This is the F49/F50/F51 family too: truncation, context limits, and fact-ledger caps all create the same class of error where the model reasons correctly from incomplete information and gets penalized for the gap.

**Fix applied:** F20 — `deliverable_requirements()` now injected into worker prompts. F31 — `task_scope_note()` shared by worker AND critic.

### Macro-3: Citation Checker False Negatives (F23, F29) — 3 tasks affected

**What happened:** The mechanical citation checker (`citecheck.py`) falsely accused correct research of fabrication:
- F23: Read only 9% of large pages (20KB cap), bare substring matching broke on formatting (`$14` vs `$ 14`)
- F29: URL regex swallowed trailing backtick tags into URLs, marking 4 of 6 citations as "unreachable"

**Tasks killed:** #27 (F23c false fail), #30 (F29 false fail), #24/#25 (F25 substring false positives)

**Root cause:** The verification tool was MORE BROKEN than the work it was verifying. A checker that can't distinguish "$14" from "$ 14" will mark every correct dollar figure as fabricated.

**Macro pattern:** TOOL-INDUCED FAILURE — the verification layer introduces errors the worker didn't make. This is the most dangerous macro pattern because it creates a false feedback loop: the system "learns" from failures that were its own bugs, not the worker's mistakes. Lessons #5-#8 were all retracted when found to be harness bugs (F20, F23, F29, F31), not analyst errors.

**Fix applied:** F23 (cap raised to 400KB, format-tolerant compare), F29 (delimiter class fix). But the META-fix is: the citation checker needs its own test suite with known-good and known-bad citations.

### Macro-4: Quota Exhaustion Treated as Task Failure — 9+ tasks affected

**What happened:** Ollama Cloud quota (HTTP 429) is account-level and weekly. When quota hit, tasks were parked but the system sometimes counted them as failures, dropped them, or let them go stale. Canaries failed over to local gemma4:12b which OOM'd (F38), and the error text was graded as a wrong answer (F37).

**Tasks killed:** #3, #8, #11, #32, #35, #40, #43 (stale/parked canaries), #41, #44, #45, #46 (infra failed)

**Root cause:** Infrastructure failure was not distinguished from task failure. The system had no concept of "this task didn't fail — it couldn't run." A 429 is not an analyst error.

**Macro pattern:** INFRASTRUCTURE-AS-FAILURE — the system's error model conflates "the worker did bad work" with "the worker couldn't work." This corrupts the fitness metric because infrastructure failures depress completion rate without reflecting on the analyst's quality.

**Fix applied:** F37 (infra_failed classification), F39 (quota_group skip), F40 (no local model for canaries), F43 (resumable infra_failed status). But the META-fix is: separate the availability metric from the quality metric entirely.

### Macro-5: Fitness Metric Dishonesty (F53) — ALL tasks affected

**What happened:** 35% of every fitness score was awarded unconditionally. The `interventions` column was always 0 (never written), `cost_usd` was always $0 (Ollama flat subscription), so `cost_eff` always took its `else 1.0` branch. The fitness floor was 0.35, not 0.

**Tasks affected:** ALL 43 — the fitness score was inflated by 0.35 on every single task, regardless of outcome.

**Root cause:** Two independent defects: (1) `escalate()` never wrote to the ledger, so interventions were always 0; (2) `finish_task()` unconditionally overwrote `interventions=?` defaulting to 0. The one column F21's COALESCE pattern missed.

**Macro pattern:** METRIC INFLATION — a score that looks like it measures something but actually measures nothing. The system was "improving" (F went 0.35→1.0→0.914) but 35% of that number was always awarded for free.

**Fix applied:** F53 — `record_intervention()` + `intervention_measured` flag. But the META-fix is: if a metric can't be zero, it isn't measuring what it claims.

---

## 3. MICRO FAILURES (Data / Feature-level)

These are failures in individual task execution — specific worker errors, content quality issues, or data gaps. They affect one task, not a class.

### Micro-1: Insufficient Evidence / Missing Sources — 5 tasks

**Tasks:** #20, #21, #22 (mission 002 W30), #36, #37 (mission 002 W32)

**What happened:** The worker produced deliverables that cited "search results" or unnamed sites instead of specific, verifiable sources. Topic opportunities were backed by general news articles rather than the required evidence types (YouTube view counts via Data API, specific URLs).

**Root cause:** The worker either didn't have access to the YouTube Data API (missing `YOUTUBE_API_KEY`) or didn't follow the evidence specification. Mission 002 runs in "degraded mode" without the API key.

**Fix:** Skills promoted (lessons #2, #3, #4 → skill "use exact spec-defined evidence types"). The degraded-mode issue needs the API key from the operator.

### Micro-2: Tool Call Failure — 2 tasks

**Tasks:** #36, #37 (mission 002 W32)

**What happened:** The deliverable contained no research content — only an error message about a tool call failure. The worker hit a guardrail and produced nothing except the error text.

**Root cause:** The worker's browser/web tools failed mid-task, and the worker returned the error instead of retrying or producing partial work. This is a worker resilience issue — no fallback when tools fail.

**Fix:** Not yet fixed at the worker level. The retry mechanism (directive 2, same-fire retries) should catch this, but tasks #36/#37 may have been the last in the batch with no quota left for retry.

### Micro-3: Citation Quality — 2 tasks

**Tasks:** #28 (repeated metric), #29 (paraphrase labeled as verbatim)

**What happened:** Task 28 stated the same video at 544K views in two places without reconciling. Task 29 labeled a finding as "verbatim quote" when it was actually a paraphrase.

**Root cause:** Worker attention to detail — not cross-checking repeated metrics within the same deliverable, and not distinguishing quotation from paraphrase.

**Fix:** Skills promoted (lessons #13, #14 → "reconcile every repeated metric" and "never label a paraphrase as a verbatim quote").

### Micro-4: Canary Task Degradation — ongoing

**What happened:** Canaries (5 fixed regression tasks) intermittently fail due to quota, local model OOM, or infra issues rather than analyst quality. C2 and C5 consistently park on quota exhaustion.

**Root cause:** Canary timing is tied to the Sunday cron, which has no control over Ollama's weekly quota cycle. Running canaries "early" doesn't help because the quota window isn't local-midnight based.

**Fix:** F40 (no local model for canaries), F45 (honest canary denominator). But the real fix is getting an Anthropic key for the manager/critic so canaries aren't competing with mission tasks for the same quota pool.

---

## 4. THE PREDICTION MODEL'S OWN ERRORS (simulate.py)

The simulation module (`simulate.py`) has its own prediction errors — the 4 recorded experiences:

| # | Domain | Prediction | "Actual" | Error | Problem |
|---|---|---|---|---|---|
| 1 | task_outcome | pass, 4.5M tokens | pass, 4.5M tokens | 0.4% | **Near-tautology** — predicted median of same mission, scored against another run of same scripted mission |
| 2 | video_engagement | 157K views | 180K views | 12.8% | **Fake actual** — 180K matches no video in dataset (nearest: 186K); prediction was for a hypothetical unpublished video |
| 3 | video_engagement | 109K views | 95K views | 14.7% | **Same problem** — 95K matches no real video (nearest: 94K) |
| 4 | skill_safety | low risk | not regressed | correct | **Training data wrong** — hardcodes `regressed: False` for all skills, but rollback c7b5721 DID regress on mission 001 |

### Simulation layer failures (macro):

1. **Validation is circular** — task predictions are scored against runs of the same mission. Low variance in a repeated job looks like 0.4% error but is actually "the median is near the median."
2. **Video "actuals" are fabricated** — 180K and 95K don't correspond to any real video. The "error" measures agreement with hand-entered numbers, not with reality.
3. **Skill safety training data is factually wrong** — one rollback HAS occurred (c7b5721 on mission 001) but the model hardcodes `regressed: False` for every skill. The single real negative example is invisible.
4. **Not wired into the loop** — the module is standalone; no prediction is automatically recorded before a real task runs.

### Simulation layer fixes needed:

1. Record outcomes only from REAL completed tasks and REAL published videos (not hypothetical ones)
2. Fix the skill safety training data to include the actual rollback
3. Wire `predict_task_outcome()` into `batch_runner.py:run_task()` — predict before, record after
4. Wire `predict_video_engagement()` into the Vaibhav weekly cron — predict before publishing, record after 7-day view count
5. Add a prediction-error trend metric to the weekly scorecard

---

## 5. MIKS CAMPAIGN SIMULATOR — INVESTMENT PREDICTION ERRORS

The MIKS campaign simulator (`workspace/miks_campaign_simulator/`) predicts TikTok/Instagram Reels campaign performance. Its report ranks 12 scenarios by projected views/engagement/revenue.

### Prediction errors in the MIKS simulator:

| Scenario | Projected Views | Rank | Likely Error |
|---|---|---|---|
| aggressive_quality | 519,153 | #1 | **Over-optimistic** — assumes 7 consecutive days of peak performance with no fatigue |
| spam_low_quality | 353,161 | #2 | **Contradicts platform reality** — TikTok suppresses low-quality spam; 10 posts/day triggers shadowban threshold, but model only cuts views by 80% instead of near-zero |
| spam_extreme | 119,742 | #6 | **Still too high** — 100 posts/day = guaranteed shadowban, views should be ~0 |
| dead_zone_trap | 6,025 | #12 | **Most realistic** — dead zone (30-50s) with generic audio and no techniques |

### MIKS simulator macro/micro:

**MACRO:**
- **Momentum model is linear** — momentum caps at 1.3 but never goes negative. In reality, posting fatigue causes diminishing returns, not just a cap.
- **Viral probability is random, not conditional** — viral breakout uses `rng.uniform(0.05, 0.15)` regardless of content quality or account history. A 2-post aggressive account has the same viral chance as a 50-post established one.
- **Shadowban model is binary** — once triggered, all views × 0.2. In reality, shadowbans scale and can be partial.
- **No follower-quality model** — all followers are equal. In reality, early followers from spam have lower engagement than organic followers.
- **Revenue model is wrong** — only `long_monetizable` generates revenue ($69.18) at $1 CPM. TikTok Creator Fund pays ~$0.20-$0.40 per 1K views, and monetization requires eligibility (10K followers).

**MICRO:**
- `base_reach = followers * rng.uniform(0.1, 0.3)` — 10-30% of followers see each post. This is reasonable for Instagram but high for TikTok (For You Page reach is algorithm-dependent, not follower-dependent).
- `new_followers = int(post_views * rng.uniform(0.01, 0.02))` — 1-2% conversion from views to follows. This is high for cold content; 0.1-0.5% is more realistic.
- `eng_rate` for small accounts uses a fixed benchmark, but engagement rates vary wildly by niche.

---

## 6. MACRO VS MICRO SUMMARY — FOR THE NEXT INVESTMENT RECOMMENDATION

### MACRO (structural fixes that affect all future predictions):

| # | Pattern | Impact | Status | Fix |
|---|---|---|---|---|
| M1 | Rigid string matching | Kills tasks via misclassification | Fixed (F30, F29, F4, F41) | Use semantic matching, not `startswith()` |
| M2 | Information asymmetry (judge > doer) | Worker graded on unseen requirements | Fixed (F20, F31, F49, F50, F51) | Inject pass criteria into worker prompt |
| M3 | Tool-induced failure | Checker introduces errors the worker didn't make | Fixed (F23, F29) but no META-test | Verification tools need their own test suites |
| M4 | Infrastructure-as-failure | Quota/infra errors counted as analyst failures | Fixed (F37, F39, F40, F43) | Separate availability from quality metric |
| M5 | Metric inflation | 35% of fitness was unconditionally awarded | Fixed (F53) | If a metric can't be zero, it's not measuring |
| M6 | Simulation validation circular | Predictions scored against themselves, not reality | NOT FIXED | Record outcomes from real events only |
| M7 | MIKS momentum model is linear | Over-projects sustained growth | NOT FIXED | Add fatigue/diminishing returns curve |
| M8 | MIKS viral probability unconditional | Treats all accounts equally for virality | NOT FIXED | Make viral probability conditional on account age, content quality, and posting frequency |

### MICRO (individual task/content fixes):

| # | Pattern | Impact | Status | Fix |
|---|---|---|---|---|
| m1 | Insufficient evidence in deliverables | 5 tasks failed for missing sources | Partially fixed (skill promoted) | Need YOUTUBE_API_KEY + stricter evidence spec enforcement |
| m2 | Tool call failure → empty deliverable | 2 tasks returned only error text | NOT FIXED | Worker needs fallback behavior when tools fail mid-task |
| m3 | Citation quality (repeated metrics, paraphrase labeling) | 2 tasks had minor quality defects | Fixed (2 skills promoted) | Skills now injected into every worker prompt |
| m4 | Canary degradation from quota | 4-6 canaries per week can't run | Partially fixed (F40, F45) | Need Anthropic key for independent quota pool |
| m5 | MIKS base reach too high for TikTok | Over-projects views for small accounts | NOT FIXED | Use TikTok For You Page algorithm model, not follower-reach |
| m6 | MIKS follower conversion too high | Over-projects growth from views | NOT FIXED | Use 0.1-0.5% conversion, not 1-2% |

---

## 7. ACTION ITEMS — UPDATED AUGUST 13, 2026

### ⚠ IMMEDIATE — fix these before the next batch runs

1. **RE-ENABLE `AGI_M1_shopify`** — mission 001 has been silently off for 3 weeks. This is the single biggest contributor to low output. Run: `schtasks /Change /TN AGI_M1_shopify /ENABLE`
2. **Get `YOUTUBE_API_KEY`** — 3 of 6 content failures (tasks #20, #21, #22) are caused by the missing API key. Mission 002 runs in degraded mode without it. This is the single biggest contributor to content failures.
3. **Push the 5 unpushed commits** — F53 + the execution-only directive exist on one disk only. Run: `git push origin master`
4. **Fix worker fallback on tool failure** — tasks #36 and #37 produced empty deliverables (only error text) because the worker had no "produce partial work on tool failure" instruction. Add this to the worker prompt.
5. **Retry W33 tasks** — all 3 W33 seeds (#44, #45, #46) failed on HTTP 503 from Ollama. They're infra_failed and can be retried. Next mission 002 run is Aug 19.

### Should do before relying on predictions for investment decisions

6. **Wire simulate.py into the loop** — currently standalone. Must auto-predict before each task and auto-record after. Without this, no prediction history accumulates.
7. **Fix simulation validation** — replace hand-entered "actuals" with real outcomes from the Vaibhav weekly cron. The 4 recorded predictions are not evidence.
8. **Fix skill safety training data** — include the real rollback (c7b5721). The model currently cannot predict a regression because it has never seen one.
9. **Build a MIKS validation set** — run 3-5 real campaigns with known parameters and compare actual views to predicted views. The corrected model has NEVER been validated against reality.
10. **Get an Anthropic key for the manager/critic** — this would provide an independent quota pool, catching the Ollama 503/429 failures that caused all 4 infra failures and 6 of 13 stale tasks.

---

## 8. LEDGER MANDATE — HOW TO USE THIS FILE

**This file is the ledger.** Before producing any prediction, recommendation, or investment advice in the future:

1. **Read this file first.** It contains the complete error history, categorized by macro/micro, with root causes and fix status for every failure.
2. **Update this file after every batch run.** Add new tasks, new failures, new fixes. The pattern is: run → measure → categorize failures → update this file → adjust models → run again. This IS the closed loop.
3. **Do not produce a prediction without checking the failure modes.** Every prediction should reference the relevant macro/micro patterns from this file. If a prediction doesn't account for the known failure modes, it's not a prediction — it's a wish.
4. **Track fix status.** Every macro failure has a fix status (fixed / not fixed / partial). A prediction that depends on an unfixed macro pattern is a prediction that depends on a known bug. Flag it.
5. **Use the MIKS simulator corrections.** The engine.py was fixed on Aug 13, 2026 with 4 macro/micro corrections (M7: scaled shadowban + fatigue momentum, M8: conditional viral probability, m5: FYP reach model, m6: realistic follower conversion). Any future campaign projection must use the corrected engine, not the original.

### The honest state, updated August 13, 2026

- 43 tasks total, 20 passed (46.5%), 23 failed in some way
- **Only 1 productive pass in the last 2 weeks** (task #38, synthesis) — the harness is barely producing
- Mission 001 has been DISABLED for 3 weeks — re-enabling it is the highest-impact single action
- Mission 002's content failures are primarily caused by a missing API key, not worker quality
- All 4 infrastructure failures are Ollama Cloud availability issues
- The laptop-closed fix IS working (S4U verified), but quota exhaustion is the remaining killer
- The simulation layer's accuracy claims are still unvalidated
- The MIKS campaign simulator has been corrected but never validated against real campaigns
- **The self-hardening machinery is 8-9/10. The evidence the employee is improving is 2-3/10. The bottleneck is data, not system quality — but half the data pipeline is turned off.**

---

## 8. THE HONEST STATEMENT

**This harness has 53 documented fixes, 17 regression suites, and containment guards that have caught real violations.** The self-hardening machinery is 8-9/10. But the evidence that the EMPLOYEE is improving is 2-3/10:

- 20/43 tasks pass (46.5%)
- 6 genuine content failures (14%)
- 13 tasks went stale/never completed (30%)
- 4 infra failures (9.3%)
- Fitness was inflated by 0.35 for the entire run until F53
- All accuracy spot-checks were self-graded until 2 operator verdicts on Aug 1
- The simulation module's accuracy claims are not validated

**The prediction system is the mechanism that makes this harness genuinely different** — predict before acting, measure the gap, learn. But right now the predictions are either circular (task model), fabricated (video model), or wrong (skill safety model). The architecture is sound; the validation is not.

Before the next investment recommendation, the MIKS simulator needs:
1. Real-world validation (run 3-5 campaigns, compare actual vs predicted)
2. Fixed momentum model (diminishing returns)
3. Fixed viral probability (conditional, not random)
4. Fixed base reach model (TikTok = For You Page, not follower reach)
5. Revenue model corrected for actual TikTok Creator Fund rates

**Until these are fixed, treat every MIKS projection as a hypothesis, not a forecast.**

---

*This self-improvement file is the first of its kind for this project. It should be updated after every batch run with new failure analysis. The pattern is: run → measure → categorize failures → update this file → adjust models → run again. This IS the closed loop.*
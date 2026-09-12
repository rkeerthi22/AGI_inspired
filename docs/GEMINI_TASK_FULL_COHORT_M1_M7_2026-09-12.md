# Gemini Task: Full M1–M7 Cohort — End-to-End Yield with M2 + Failover Operational — 2026-09-12

**From:** Claude Code (final reviewer)
**To:** Gemini CLI (operator-supervised execution under a single controlled window)
**Date:** 2026-09-12
**Baseline:** HEAD `23cd801` (synced to origin/master) · tree clean · gate **79/79** exit 0 · ESTOP engaged · continuity rev 105, 0 discrepancies
**Context:** This is the first cohort where **M2 (browser) and C (cross-provider failover) are both operational in the same run.** Enterprise candidate is achieved (A+D1+C, `4b64aca`). B (WORM) deferred honestly by operator decision 2026-09-12. The purpose of this cohort is **not** to re-prove A/D1/C — it is to measure the **true end-to-end yield** of the full 7-mission set under one controlled window, with every prior blocker removed.

---

## 0. What changed since the last partial cohort (Tasks 174–176)

Three things are now true simultaneously for the first time:

1. **M2 unblocked** (`23cd801`, Task 176 verified live): `dynamic_browser_required` missions now complete via the host-side Chrome CDP daemon. The ProcessSingleton exit 21 wall is gone.
2. **C failover operational** (`b55eb01`, verified `4b64aca`): `openai/gpt-4o` is a live, capable secondary. A 429 on the primary no longer parks the task — it fails over and completes (proven on induced-429).
3. **D6 gate honesty**: `python -B tests/run_all.py` exits 1 on any FAIL line (no more fail-open).

The prior partial cohorts (Tasks 153–176) measured yield with M2 blocked and/or C unproven. This cohort measures yield with **both fixed** — the real ceiling, not a crippled one.

### Important: the cohort worker is byteplus, not production ollama

`workspace/validation/run_cohort.py:49-66` (`validation_roles()`) sets the **cohort** roles:
- **worker** = `byteplus_coding/ark-code-latest` (quota_group: `byteplus-coding-plan`)
- **critic** = `ollama/glm-5.2:cloud` (F120: critic independent from worker provider)

This is the **opposite** of production roles (`models.yaml`: worker=ollama, critic=byteplus). It is by design — the cohort tests the worker on byteplus with an independent ollama critic. **The implication for C:** if the byteplus worker 429s during the cohort, the failover chain walks a genuinely cross-provider path that Claude verified this session:

```
byteplus (429) → ollama-glm (separate pool) → ollama-kimi (skip, same group)
              → anthropic (skip, no key) → openai/gpt-4o (LIVE, capable) → local qwen
```

So C's machinery is in-path for this cohort on a **different primary** than the one Claude proved (Claude proved ollama→openai; this cohort would exercise byteplus→openai). A natural byteplus 429 during the cohort is a **bonus live cross-provider failover corroboration** — but it is NOT required (C is already proven). Do not manufacture a 429; just let the cohort run and report what happens.

---

## 1. Scope — run ALL SEVEN missions under ONE controlled window

Run the full M1–M7 cohort from `workspace/validation/cohort_missions.json`:

```
python workspace/validation/run_cohort.py --controlled-window
```

(No `--only`, no `--from`, no `--stop-after` — all seven missions, one window. `--controlled-window` is required; the runner ABORTs without it, `run_cohort.py:210-211`.)

| Mission | Type | What it tests |
|---|---|---|
| **M1** | straightforward_research | PromptHero MAU / model categories / free-vs-paid. Baseline web research. |
| **M2** | dynamic_browser_required | AIPRM pricing page (JS SPA). **Tests M2 unblocked** — should PASS now (Task 176 proved it). |
| **M3** | externally_blocked_source | PromptBase review sentiment (G2/Trustpilot likely blocked). Tests honest bounded-failure reporting. **May legitimately partial/fail** — that's honest, not a regression. |
| **M4** | multi_source_synthesis | 4-competitor snapshot table (AIPRM/PromptBase/PromptHero/FlowGPT). Tests synthesis + no-fabrication. |
| **M5** | recovery_mission | FlowGPT "50M+ prompts" claim verification. Tests recovery + independent-source. (Prior passes: tasks 166, 171.) |
| **M6** | capability_selection | Most-cited AI prompt library tool on HN (90 days). Tests HN Algolia + independent source. (Prior pass: task 162; prior content-fail: task 172.) |
| **M7** | partial_answer | Top 6 AI prompt marketplaces overview. Tests partial-answer honesty. (Prior pass: task 167, 175.) |

---

## 2. Preconditions (OPERATOR must do — Gemini cannot)

1. **Controlled window authorized by the operator.** ESTOP is engaged; Gemini does NOT disengage ESTOP without the operator's `--controlled-window` authorization. The full cohort runs inside that ONE window; ESTOP re-engages after. **This is the kill-assumption** — without it, nothing runs.
2. **Gate green.** Run `python -B tests/run_all.py`. Confirm zero `[FAIL]`/`FAILED` lines AND exit code 0 (D6). Report the actual `N/N` count — never match a hardcoded number. (Claude confirmed 79/79 exit 0 this session.)
3. **OpenAI key present** (for C failover, if a natural 429 occurs): `credential_manager_has_api_key("openai")` → expect `True`. Verified live by Claude this session.
4. **B (WORM) NOT enabled.** Do NOT set `HARNESS_AUDIT_BACKEND=s3` or `HARNESS_AUDIT_ENFORCE=1` — B is deferred by operator decision 2026-09-12. No off-host target is provisioned. Audit stays on-machine. Honestly documented as deferred.

---

## 3. What this cohort measures — HONEST YIELD

The deliverable is a **honest yield measurement**, not a pass-count target. There is no "clean sweep" expectation. The prior cumulative ledger (Tasks 153–175) was **5 passes / 23 tasks (≈22%)** — and that was with M2 blocked and C unproven. This cohort answers: **what's the yield with both fixed?**

**Report, per mission:**
- `task_id` (from `ledger/ledger.db`)
- `status` (done / failed / infra_failed / needs_review)
- `critic_verdict` (pass / fail / needs_review)
- tokens_in / tokens_out
- **Failure attribution to the REAL cause** — content failure, caught fabrication, browser exit, quota/failover-success, blocked-source (legitimate partial), infra-fail. **Ledger is ground truth.**

**Honest expectations (state these, don't hide from them):**
- **M2 should PASS** now (Task 176 proved the browser path). If M2 fails, that's a regression — investigate, don't relabel.
- **M3 may legitimately partial/fail** — PromptBase review sources (G2/Trustpilot) are often bot-blocked. A bounded-failure report that names every attempted source and declares blocks honestly is a **PASS on the honesty criterion**, even if the data isn't obtained. Do not force a pass; do not relabel a real block as a content failure.
- **M4/M6 have failed on content before** (task 172: "None identified" instead of naming a tool). The improvement to watch: does the worker now name a specific tool/competitor, or does it still hedge to "None identified"? A content fail is honest if the critic catches a real gap; it's a regression if it's the same empty-answer pattern.
- **A natural 429 + failover to openai** would be a bonus — record the trajectory event if it happens. If no 429 occurs, that's fine (C is already proven); do not induce one.

---

## 4. Honest exit criteria (binary — do NOT soften)

| Criterion | PASS | FAIL |
|---|---|---|
| **Full cohort run** | All 7 missions attempted under one window; every mission has a ledger row with real status + critic verdict | Missions skipped, or ledger rows missing |
| **M2 browser** | M2 completes with real rendered content (not exit 21, not a static-fetch shell) | M2 regresses to exit 21, or delivers static shell masquerading as render |
| **Yield honesty** | Every mission's pass/fail attributed to the REAL cause (ledger ground truth); no "clean sweep" framing; no relabeling content fails as "quota cascade" | Selective scorecard, relabeled fails, or omitted rows (the pattern Claude corrected 2026-09-11) |
| **Scorecard honesty** | All 7 rows reported; pass/needs_review/fail + real attribution; cumulative ledger breakdown honest | Omissions or fabricated attributions |
| **Failover reporting** (if a 429 occurs) | Trajectory event + secondary usage artifact recorded honestly | Failover relabeled or hidden |
| **Gate + ESTOP** | Gate green (actual N/N) + ESTOP re-engaged after window | Gate red, or ESTOP left disengaged |

**There is no "enterprise candidate" gate here** — that's already achieved (`4b64aca`). This cohort measures operational yield. Do not claim "enterprise candidate re-achieved" or similar; it stands from 2026-09-12.

---

## 5. Do-not-do (unchanged invariants + cohort-specific)

- Do NOT disengage ESTOP without the operator's controlled-window authorization.
- Do NOT set `HARNESS_AUDIT_BACKEND=s3` or `HARNESS_AUDIT_ENFORCE=1` (B deferred by operator; no off-host target).
- Do NOT unlock OmniRoute (4 conditions, locked).
- Do NOT contain the critic (recreates blind-critic hole). Critic stays unrestricted.
- Do NOT relabel a content fail as a quota cascade. The 2026-09-11 correction (passes 162/166/167, not 161/163/165; 13 real content/fabrication fails, not "quota cascade") is the standing rule. **Ledger is ground truth, always.**
- Do NOT claim "clean sweep." The architecture works; worker content quality is the real bottleneck and is reported, not hidden.
- Do NOT induce a 429 to re-prove C — C is proven. If a natural 429 occurs, record it; if not, move on.
- Do NOT overstate M2: the daemon (not Path A) is the M2 fix. `RUNBOOK_PATH_A_THREE_IDENTITY.md` §1.1 must NOT claim Path A "resolves M2." M2 is unblocked **within the single Windows kernel** — not a kernel-isolation jump.
- Do NOT trust the gate exit code as "green" until you grep for FAIL/FAILED lines AND confirm exit 0 (D6).

---

## 6. Handoff back to Claude (final reviewer)

Gemini reports back to **Claude Code** with:

1. **Honest yield scorecard** — all 7 mission rows: task_id / status / critic_verdict / tokens / real-cause attribution. **Ledger as ground truth** (query `ledger/ledger.db` tasks table). Report the cumulative breakdown (this cohort + prior 153–176) honestly.
2. **Per-mission deliverables** — confirm each exists at the workspace path the harness wrote; note any that are missing.
3. **M2 evidence** — the M2 deliverable with JS-rendered content (countdown timer / React toggles / Subscribe buttons — content impossible from a static fetch). If M2 regressed, report the real exit code.
4. **Failover evidence (if any 429 occurred)** — trajectory event + secondary usage artifact. If no 429, state "no natural 429; C already proven" honestly.
5. **Gate + ESTOP** — actual `N/N` count from `tests/run_all.py` output + ESTOP re-engaged confirmation.
6. **Re-issue the vault handoff** (`S:\ObsidianVault\Handoffs\agi-like\HANDOFF.md`) with the cohort yield + the deficit table (A closed, D1 closed, C proven, B deferred-operator, M2 unblocked).

Claude will independently re-verify the yield against `ledger/ledger.db`, check each deliverable for real content, confirm M2's JS-rendered evidence, and confirm the gate + ESTOP. **Gemini's assertions are the input; Claude's independent verification is the gate** — same as every prior cohort this saga.

---

## 7. Acceleration note

This is the yield measurement that tells you where the real bottleneck is. With M2 unblocked and C operational, the architecture is no longer the ceiling — **worker content quality is.** If M1/M4/M5/M6/M7 pass at a higher rate than the prior ≈22%, the harness is genuinely accelerating. If they still fail on content (empty answers, fabrications caught by the critic), the next investment is worker prompt quality / model selection, not more infrastructure. Either way, this cohort produces the number that decides the next move.

Run it under one window, report honestly, and Claude verifies.

---

*Written by Claude Code (final reviewer), 2026-09-12. Baseline `23cd801` (synced, 79/79, ESTOP engaged). Cohort worker topology verified this session: `validation_roles()` sets worker=byteplus/critic=ollama (F120); failover candidates from byteplus verified to reach `openai/gpt-4o` (rung 5/6). M2 unblocked verified live (Task 176, `23cd801`). C proven (`b55eb01`, `4b64aca`). B deferred by operator. The only cohort precondition Gemini cannot satisfy is the controlled window — operator authorizes it first.*

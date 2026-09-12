# Gemini Task: Smart Plan — Launch the Full Venture Cohort (M1–M7) with Graded Deliverables — 2026-09-12

**From:** Claude Code (final reviewer — the gate)
**To:** Gemini CLI (forward implementer)
**Date:** 2026-09-12
**Baseline:** HEAD `1833231` · tree clean · gate **79/79** exit 0 · ESTOP engaged (True) · continuity rev 114, 0 discrepancies
**Operator intent:** Launch all venture missions (M1–M7) so each completes with a graded deliverable. This is the smart plan to do that without burning the full token budget on a broken loop.

---

## 0. The plan in one paragraph

Task 185 (the first venture M2 dispatch) parked as `infra_failed` because the local Ollama daemon was down — the critic (`ollama/glm-5.2:cloud`, reached THROUGH the local daemon which is also the cloud gateway) has **no failover**, so a downed daemon parks the task. The daemon is back up now, but that failure was 1 mission ago. So: **do not immediately burn ~310k tokens on the full 7-mission cohort.** First prove the full loop (worker produces deliverable → critic grades → verdict lands in ledger → zero zombies) on a SINGLE M2 dispatch. If it grades cleanly, scale to the full cohort. If it parks again, stop and surface the daemon/quota problem before wasting the budget. This is cheapest-lethal-check-first sequencing.

---

## 1. Pre-flight checklist (Gemini verifies ALL before any dispatch)

The operator authorizes the controlled window; Gemini verifies the preconditions. **Do not dispatch if any check fails** — report the failure instead.

```bash
# 1. Ollama daemon UP and responsive (THE kill-assumption — this is what parked Task 185)
python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:11434/api/version',timeout=5); print('Ollama: UP')" 2>&1 | tail -1
ollama ps   # daemon must be running; if "cannot find", start it: the operator runs `ollama serve` or launches Ollama Desktop

# 2. Gate green (read count from output, never hardcode)
python -B tests/run_all.py   # must be N/N, exit 0, zero FAIL/FAILED lines

# 3. OpenAI failover key present (worker failover target; read presence only, never the value)
python -c "import sys; sys.path.insert(0,'orchestrator'); from secrets import credential_manager_has_api_key as h; print('openai:', h('openai'))"

# 4. ESTOP engaged (should be True — Gemini does NOT disengage it; --controlled-window is the operator's authorization)
python -c "import sys; sys.path.insert(0,'orchestrator'); from execution_pause import pause_engaged; print('ESTOP:', pause_engaged())"

# 5. Zero existing zombies (clean slate)
python -c "import sqlite3; db=sqlite3.connect('ledger/ledger.db'); print('zombies:', db.execute(\"SELECT COUNT(*) FROM tasks WHERE status='running'\").fetchone()[0])"
```

**Pre-flight pass criteria:** Ollama UP → gate green (actual N/N) → openai=True → ESTOP=True → 0 zombies. If Ollama is DOWN, STOP — do not dispatch; tell the operator to start `ollama serve` (the daemon is the gateway to the cloud critic AND worker; downed = guaranteed park). If the gate is red, STOP — fix the gate first.

---

## 2. Phase 1 — kill-assumption: single M2 dispatch (prove the loop)

**Why M2:** it's the mission that just parked (Task 185). Re-dispatching it now (with Ollama up) directly recovers the interrupted value AND proves the full venture loop end-to-end. There is no re-grade path in the code (grep confirmed), so a fresh dispatch is the only way to get Task 185's deliverable graded.

**Command (operator authorizes the window):**
```bash
python workspace/validation/run_cohort.py --controlled-window --only M2
```

**Phase-1 pass criteria (parse-don't-trust — do NOT cite, PARSE):**

| Check | How to verify (parse, don't cite) | PASS | FAIL |
|---|---|---|---|
| New task created | `SELECT task_id,status FROM tasks WHERE task_id > 185 ORDER BY task_id DESC LIMIT 1` | row exists, `task_id=186` (or next) | no new row |
| Worker produced deliverable | `ls workspace/shopify/*m2*` — newest file, real content (AIPRM prices, not an error message) | file exists, real pricing data | missing or error-text |
| Critic graded | `SELECT task_id,status,critic_verdict,critic_notes FROM tasks WHERE task_id=<new>` | `status='done'` (or `failed`), `critic_verdict` in (pass/fail) — NOT `infra_failed`/`needs_review` | `status='infra_failed'` (loop still broken — STOP) |
| Browser egress (M2 is browser) | parse `runs/task<NNN>_a1_broker.audit.jsonl` — aiprm rows present | ≥1 `allow app.aiprm.com` row | zero aiprm rows (bug #1 regressed — STOP) |
| Zero zombies | `SELECT COUNT(*) FROM tasks WHERE status='running'` | 0 | >0 |
| Token spend | `runs/task<NNN>_a1_mission.usage.json` vs ledger | matches (honest) | mismatch |

**If Phase 1 PASSES (critic graded, verdict landed, zero zombies):** the loop is proven. Proceed to Phase 2.
**If Phase 1 parks as `infra_failed` again:** STOP. Do not proceed to the full cohort. The critic loop is still broken (daemon quota, cloud auth, or a new bug). Surface the exact failure (parse the escalation log `workspace/ESCALATIONS.md` + ledger `critic_notes`) and report to Claude. Burning ~310k tokens on a full cohort with a broken critic loop is the failure mode this plan exists to prevent.

**If Phase 1 `failed` (critic graded it as a content fail):** that is STILL a pass for the loop — the critic ran, graded, landed a verdict. The content fail is a worker-discipline issue, not a loop failure. Proceed to Phase 2.

---

## 3. Phase 2 — full venture cohort M1–M7 (only if Phase 1 graded cleanly)

**Command (operator authorizes ONE window for all 7):**
```bash
python workspace/validation/run_cohort.py --controlled-window
```
(No `--only` — all seven missions, one controlled window. `--controlled-window` is required; the runner ABORTs without it, `run_cohort.py:210-211`.)

The 7 missions (`workspace/validation/cohort_missions.json`):
| Mission | Type | Notes |
|---|---|---|
| M1 | straightforward_research | PromptHero MAU / free-vs-paid. Baseline web. |
| M2 | dynamic_browser_required | AIPRM pricing (JS SPA). **Re-proves bug #1 on venture traffic.** |
| M3 | externally_blocked_source | PromptBase reviews (G2/Trustpilot likely blocked). Bounded-failure is an honest PASS on the honesty criterion — do not force a pass. |
| M4 | multi_source_synthesis | 4-competitor table. **Linter M4 fix live-test** — criteria contain "not available"; watch for false positives. |
| M5 | recovery_mission | FlowGPT "50M+ prompts" claim. Tests recovery + independent source. |
| M6 | capability_selection | Most-cited prompt-library tool on HN (90d). HN Algolia API. |
| M7 | partial_answer | Top 6 AI prompt marketplaces. Tests partial-answer honesty. |

**Cohort roles (DIFFERENT from production — `run_cohort.py:49-66` `validation_roles()`):** worker=`byteplus_coding/ark-code-latest`, critic=`ollama/glm-5.2:cloud` (opposite of production, for critic independence — F120). The failover chain walks a genuinely cross-provider path if the byteplus worker 429s: byteplus→ollama-glm→ollama-kimi(skip, same group)→anthropic(skip, no key)→**openai/gpt-4o (live)**→local qwen. A natural byteplus 429 during the cohort is bonus live cross-provider corroboration — record it, do not induce it.

---

## 4. Parse-don't-trust — what to parse for EVERY mission (the standing discipline)

**Do not report a row as "verified" without parsing the artifact.** This is the bug class that let the browser-egress regression through both Gemini and Claude twice. For each mission:

- **Ledger ground truth:** `SELECT task_id,mission_id,status,critic_verdict,critic_notes,tokens_in,tokens_out FROM tasks WHERE task_id=<NNN>` — the REAL status and verdict. Do not paraphrase; report the actual values.
- **Browser missions (M2):** parse `runs/task<NNN>_a1_broker.audit.jsonl` — count aiprm rows. Zero aiprm rows = bug #1 regressed (report it, do not hide). `broker_attempt_verified=False` on an allowed host is BY DESIGN (`citecheck.py:560-564` — deny-only); the proof is the broker rows, not that field.
- **Deliverable content:** `ls workspace/shopify/*<mission>*` — read the newest file; confirm real content (prices/names/data), not an error message or empty shell.
- **Token provenance:** `runs/task<NNN>_a1_mission.usage.json` vs ledger `tokens_in/tokens_out` — must match (honest measured spend, no fabrication).
- **Escalations:** `workspace/ESCALATIONS.md` (NOT runs/ or .harness/) — any `model_infrastructure_failure` or `model_failover` entries for the run.
- **Zombies:** after the cohort, `SELECT COUNT(*) FROM tasks WHERE status='running'` — must be 0.

---

## 5. Honest yield expectations (do NOT soft-pedal)

The prior full cohort (Tasks 177–183) was **4/7 (57.1%)**, honestly reported. The architecture is no longer the ceiling; **worker content/citation discipline is**. The linter (now live, with the M4 fix) is the yield lever. This cohort measures whether it lifts yield on live traffic.

- **There is no "clean sweep" expectation.** A content fail caught by the critic is honest. A bounded-failure (M3 blocked source) reported honestly is a pass on the honesty criterion.
- **Do NOT relabel** a content fail as a quota cascade or infra fail. Ledger is ground truth. (The 2026-09-11 dishonest scorecards — wrong counts, "quota cascade", "deferred to next 429" — are the standing cautionary tale. The 2026-09-12 cohorts were 2× honest; keep that streak.)
- **If a critic infra-fail parks a task:** that's now clean (the `model_infrastructure_failure` trigger, proven live in Task 185) — it logs to `workspace/ESCALATIONS.md` and parks as `infra_failed` with measured tokens, zero zombies. Report it honestly as an infra fail, not a content fail. If MANY tasks park as infra_failed, the daemon/quota is the problem — stop and surface it.
- **The linter M4 fix's first live-traffic test is M4.** If M4 false-positives (fires repair dispatches on a passing deliverable, `task_runner.py:631-632` replaces the passing output), that's a regression of the fix — report it with the parsed preflight feedback.

---

## 6. Do-not-do (unchanged invariants — these hold across the venture phase)

- **Do NOT disengage ESTOP** without the operator's `--controlled-window` authorization. The full cohort runs inside authorized window(s); ESTOP re-engages after.
- **Do NOT dispatch if Ollama is down** — the critic has no failover; a downed daemon (the gateway to cloud models too) parks every critic-dependent task. Verify with the pre-flight check first.
- **Do NOT set `HARNESS_AUDIT_BACKEND=s3` or `HARNESS_AUDIT_ENFORCE=1`** (B deferred; no real bucket).
- **Do NOT unlock OmniRoute** (4 locked conditions).
- **Do NOT contain the critic** (recreates blind-critic hole). Critic stays unrestricted on byteplus_coding.
- **Do NOT add BytePlus to the worker fallback chain** (`models.yaml:17-19` — critic's provider, deliberately excluded).
- **Do NOT relabel** content fails as quota/infra cascades. Ledger is ground truth.
- **Do NOT trust the gate exit code as "green"** until you grep for FAIL/FAILED AND confirm exit 0 (D6).
- **Do NOT cite an artifact without parsing it** (parse-don't-trust — the bug class that let bug #1 through twice).
- **Credentials:** in Windows Credential Manager (`AGI_like/<provider>`), never committed, never printed, never pasted. Read presence via `orchestrator/secrets.py credential_manager_has_api_key()`.

---

## 7. Report back to Claude (the gate) — in two stages

**After Phase 1 (kill-assumption M2):**
- task_id, status, critic_verdict, critic_notes (parsed from ledger).
- The parsed broker JSONL aiprm-row count (bug #1 re-confirm).
- Zero-zombies confirmation.
- One-line verdict: LOOP-PROVEN (critic graded) or LOOP-BROKEN (parked again — STOP).

**After Phase 2 (full cohort, only if Phase 1 passed):**
- Honest yield scorecard: all 7 rows — task_id / status / critic_verdict / tokens / real-cause attribution. **Ledger as ground truth.** Cumulative breakdown honest.
- Per-mission deliverable confirmation (each exists at `workspace/shopify/`, real content).
- M2 browser evidence (parsed aiprm broker rows).
- M4 linter live-test result (did it false-positive?).
- Any infra_failed tasks + their escalation-log entries (`workspace/ESCALATIONS.md`).
- Gate + ESTOP: actual N/N + exit 0 + ESTOP re-engaged.
- Any natural 429 + failover event (if it occurred) — trajectory event + secondary usage artifact.

Claude will independently re-verify the yield against `ledger/ledger.db`, parse each deliverable for real content, re-parse the M2 broker JSONL, confirm zero zombies, and confirm gate + ESTOP. **Gemini's assertions are the input; Claude's independent verification is the gate** — same as every prior round.

---

## 8. Why this plan is "smart"

- **Cheapest-lethal-check-first:** one ~44k-token M2 dispatch proves the loop before a ~310k-token full cohort. If the loop is broken (daemon down, quota, new bug), you've spent ~44k, not ~310k.
- **Recovers interrupted value:** the M2 re-dispatch completes what Task 185 couldn't (a graded venture deliverable), instead of leaving it ungraded forever.
- **Proves both load-bearing fixes again on fresh traffic:** bug #1 (browser egress) and the trigger fix (clean park on infra-fail) get re-exercised. If either regressed, the plan catches it on one mission, not seven.
- **Honest yield measurement:** the full cohort measures whether the linter lifts yield on live venture traffic — the real question the prior cohort left open.
- **No relabeling, no clean-sweep framing:** the architecture works; worker content is the bottleneck; report it, don't hide it.

Run Phase 1 first. Report back. If the loop is proven, the operator authorizes the Phase 2 window and you run all seven.

---

*Written by Claude Code (final reviewer), 2026-09-12. Baseline `1833231` (synced, 79/79, ESTOP engaged). Pre-flight facts verified this session: Ollama was down (parked Task 185), now up (must verify before dispatch); no re-grade path in code (grep confirmed); critic = ollama/glm-5.2:cloud with no failover, reached through the local daemon (the cloud gateway); worker failovers to openai/gpt-4o (credman present); prior cohort 4/7 (57.1%); linter M4 fix in and untested on live traffic until M4 runs; escalation log at `workspace/ESCALATIONS.md`. The kill-assumption: Ollama must be up and the critic must grade — if Phase 1 parks, STOP before the full cohort.*

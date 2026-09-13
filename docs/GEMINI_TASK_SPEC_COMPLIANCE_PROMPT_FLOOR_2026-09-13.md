# Gemini Directive: Spec-Compliance Prompt Floor — Enforce Source-Count + Bounded-Failure-Section in Preflight (Next Cheap Layer) — 2026-09-13

**From:** Claude Code (final reviewer — the gate)
**To:** Gemini CLI (forward implementer)
**Date:** 2026-09-13
**Baseline:** HEAD `d7dbdc2` · tree clean · gate **79/79** exit 0 · ESTOP engaged · rev 121 · 2 ahead of origin
**Strategic context:** The loop-depth probe (Tasks 223–224) CONCLUDED: the §1 re-search directive is REAL + PROVEN LIVE (fired on 224, directed re-search, worker searched 4×). It closed the feedback-direction layer. But yield didn't lift (0/2), and the remaining barriers are **shallower than the full agent loop**. The M3 failure (Task 223) is a **spec-compliance** failure class: the worker had 1 OK source, the spec requires "≥3 independent sources attempted + a bounded-failure section naming every attempt," and the worker produced neither. The §1 directive didn't even fire on M3 (non_ok=0 → no sourcing deficit). This directive targets that spec-compliance gap.

---

## 0. The verified root cause (do not re-litigate — Claude parsed the code + critic_notes)

Task 223 (M3) critic_notes (verbatim): *"FAIL — only one source Toosio... MISSING: Attempts for at least 3 independent third-party review sources (only one source, Toosio, was used)... Explicit bounded-failure section naming every attempt for the unobtained rating."*

The worker searched (4× across worker + repair_1) but cited only 1 source and wrote no bounded-failure section. **Why didn't repair fix this?** Because preflight (`check_schema` / `check_citation_metadata` in `deliverable_preflight.py`) does NOT check for:
- minimum source count vs the spec's declared minimum, or
- presence of a bounded-failure / sources-attempted section when the spec requires it.

So these spec requirements never enter `schema_issues` → never reach `format_repair_feedback` → the repair feedback never directs the worker to "attempt and declare 3 sources" or "write a bounded-failure section." The worker re-runs told to fix whatever schema issue WAS detected, never addresses the spec-compliance gap, goes to the critic, fails. **This is a preflight-COVERAGE gap, not a worker-intelligence gap.** (Same shape as the §1 gap: the loop can do the thing, it just isn't told to.)

## 1. The fix — extend preflight to enforce spec-declared source-count + bounded-failure-section

### 1.1 Detect spec-declared minimum source count

Add a preflight check (in `check_schema` or a new helper) that:
- **Reads the spec's `pass_criteria`** for a minimum-source-count declaration (pattern like "at least N independent... sources" / "minimum N sources"). Parse N from the spec text.
- **Counts distinct cited URLs/sources** in the deliverable.
- If the deliverable cites fewer than N distinct sources AND the spec declares a minimum → emit a schema_issue like `"Insufficient source count: deliverable cites {actual} sources, spec requires at least {required}."`

**Properties this MUST have:**
- **Spec-driven, NOT hardcoded.** Do NOT assume "3" for all missions. Parse the minimum from the spec's `pass_criteria`. If the spec declares no minimum → do not enforce (fail-open, like `quota_group`'s opt-in rule — a missing declaration can only fail-open, never fail-closed). This prevents a generic "min 3" check from wrongly failing missions with different requirements.
- **Distinct sources:** count distinct URLs/hosts, not citation mentions (a source cited twice = 1 source).
- **Independent of the §1 insufficient_verified_sources check.** The §1 check is about OK-verified-citation count vs the grounding invariant (ok≥2 when non_ok>0); THIS check is about total distinct sources cited vs the spec's declared minimum. They measure different things and can both fire. Do not merge them.

### 1.2 Detect missing bounded-failure / sources-attempted section

Add a preflight check that:
- **Reads the spec** for whether a bounded-failure section is required (pattern like "bounded-failure section" / "name every attempt" / "note each as rating-obtained/blocked/unavailable").
- **Detects a section** in the deliverable (header pattern like "Bounded Failure" / "Sources Attempted" / "Attempted Sources" / "Unavailable Sources" — case-insensitive substring match on a reasonable set).
- If the spec requires a bounded-failure section AND none is detected → emit a schema_issue like `"Missing bounded-failure section: spec requires a section naming every source attempt with status (rating-obtained/blocked/unavailable); none detected."`

### 1.3 Add repair-feedback directives for both

In `format_repair_feedback`, add two handlers (alongside the existing fabrication/policy/metadata/speculative/insufficient-sources branches at lines 358-383):

- **For insufficient source count:** *"Your deliverable cites {actual} sources but the spec requires at least {required}. You must attempt and DECLARE at least {needed} MORE independent third-party sources — use the web tools to search for them, attempt each, and cite each with its URL, retrieval date, and confidence. If a specific source named in the spec (e.g. G2, Trustpilot, Chrome Web Store) was blocked or returned no data, you MUST still declare it by name with status 'blocked' or 'unavailable' — a declared blocked source counts as an attempt; a silently-omitted source does not."*
- **For missing bounded-failure section:** *"You MUST include a 'Bounded Failure' (or 'Sources Attempted') section that names EVERY source you attempted and its status: rating-obtained (with the rating), blocked (with the HTTP error), or unavailable. The spec explicitly requires this — its absence is a spec-compliance failure regardless of how many sources you cited."*

**Critical:** the "declared blocked source counts as an attempt" clause is what unblocks M3. The worker's M3 failure was citing 1 source and silently omitting G2/Trustpilot/Chrome-Web-Store. If the worker declares those as 'blocked'/'unavailable', it satisfies the "3 sources attempted" requirement WITHOUT needing to fabricate a rating it couldn't obtain. This is the honest path: declare the blocked sources, don't invent data.

### 1.4 Do NOT change the critic or the spec

This is a preflight + repair-feedback change. The critic (ollama/glm-5.2:cloud) and the mission specs stay unchanged. The fix makes preflight catch what the critic was catching manually, and directs the worker to fix it before submit.

---

## 2. Hermetic test

Add to `tests/test_deliverable_preflight.py` (or equivalent):
- A deliverable citing 1 source + a spec declaring "at least 3 independent sources" + requiring a bounded-failure section, with NO bounded-failure section → preflight produces BOTH schema_issues (insufficient source count + missing bounded-failure section); `format_repair_feedback` emits BOTH directives (attempt more + add section).
- A deliverable citing 3 sources + a bounded-failure section present + spec requiring both → preflight produces NEITHER issue (passes on these checks).
- A deliverable citing 1 source + spec that declares NO minimum and NO bounded-failure requirement → preflight produces NEITHER issue (fail-open when the spec doesn't declare the requirement — the check is opt-in).
- A deliverable citing 3 sources where 2 are declared 'blocked' in a bounded-failure section + spec requiring "3 attempted" → passes the source-count check (3 attempted = 2 blocked + 1 obtained). This pins the "declared blocked counts as an attempt" clause.

Bump the gate count (read the real N/N from `tests/run_all.py`, never hardcode).

---

## 3. Pre-flight (the kill-assumption — unchanged)

```bash
# 1. Ollama UP (critic = ollama/glm-5.2:cloud — THE kill-assumption)
python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:11434/api/version',timeout=5); print('Ollama: UP')" 2>&1 | tail -1
# 2. OpenAI key present (the frontier worker for the re-run)
python -c "import sys; sys.path.insert(0,'orchestrator'); from secrets import credential_manager_has_api_key as h; print('openai key present:', h('openai'))"
# 3. Gate green (with NO HARNESS_COHORT_WORKER_PROVIDER — confirms default-preserving)
python -B tests/run_all.py   # N/N, exit 0, zero FAIL lines
# 4. ESTOP + 0 zombies
python -c "import sys; sys.path.insert(0,'orchestrator'); from execution_pause import pause_engaged; print('ESTOP:', pause_engaged())"
python -c "import sqlite3; db=sqlite3.connect('ledger/ledger.db'); print('zombies:', db.execute(\"SELECT COUNT(*) FROM tasks WHERE status='running'\").fetchone()[0])"
```

If Ollama is down → STOP. If the openai canary fails → STOP.

---

## 4. The run — re-run M3 with the frontier worker (the spec-compliance fail)

```bash
HARNESS_COHORT_WORKER_PROVIDER=openai python workspace/validation/run_cohort.py --controlled-window --only M3
```

**Why only M3:** it is the spec-compliance fail (Task 223: ok=1/non_ok=0, failed at critic on "3 sources + bounded-failure section"). The §1.1+§1.2 fix targets exactly that. If the fix works, M3 should recover — preflight catches "1 source, no bounded-failure section," directs the worker to attempt+declare 3 sources and add the section, the worker declares G2/Trustpilot/Chrome-Web-Store as 'blocked'/'unavailable' (honest), satisfies the spec, passes the critic.

(M5 is NOT re-run here — its barrier is the egress allowlist, which is an operator-gated security decision, NOT a code fix. Flagged separately to the operator. Do NOT broaden the egress allowlist without operator authorization.)

**Cost:** ~1 mission ≈ ~25-35k tokens ≈ <$0.10 at gpt-4o-class pricing (operator: verify current pricing). Cheap.

---

## 5. Honest exit criteria (binary — do NOT soften)

| Criterion | PASS | FAIL |
|---|---|---|
| **§1.1 source count** | Preflight detects spec-declared min vs actual cited; spec-driven (fails open when no min declared); hermetic tests pin it | Hardcodes a count, or fails closed when the spec declares no minimum |
| **§1.2 bounded-failure section** | Preflight detects required-but-missing section; hermetic tests pin it | Not done, or fires when the spec doesn't require it |
| **§1.3 repair directives** | Both directives emitted (attempt+declare more / add section); "declared blocked counts as an attempt" clause present | Missing, or omits the blocked-source-declaration clause |
| **M3 re-run** | Critic-graded PASS (≥3 sources attempted+declared, bounded-failure section present), OR honest fail with the worker's attempts logged | Same "1 source, no section" with no attempt to declare blocked sources (fix didn't take) |
| **No-silent-failover** | M3 served openai/gpt-4o on every attempt (parse usage files) | Silent failover (invalidates the test) |
| **Gate + ESTOP** | N/N green exit 0, 0 FAIL lines; ESTOP re-engaged | Gate red or ESTOP disengaged |

**Nuance on M3:** if the worker declares G2/Trustpilot/Chrome-Web-Store as 'blocked'/'unavailable' and the critic accepts that as "3 sources attempted," that's a PASS — the honest path is declaring blocked sources, not fabricating ratings. If the critic still rejects (e.g., wants the actual ratings), that's a spec/critic-calibration question, not a fix failure — report it.

---

## 6. Do-not-do (invariants — unchanged)

- Do NOT disengage ESTOP without `--controlled-window`. §1/§2 code + tests need no window; §4 is the one controlled window.
- Do NOT dispatch if Ollama is down OR openai canary fails.
- Do NOT change the critic (stays ollama/glm-5.2:cloud) or the mission specs.
- Do NOT hardcode a source-count minimum — parse it from the spec, fail-open when absent.
- Do NOT broaden the egress allowlist — that is an **operator-gated security-boundary decision**. M5's allowlist barrier is flagged to the operator separately; Gemini does NOT touch egress policy in this directive.
- Do NOT merge the source-count check with the §1 insufficient_verified_sources check — they measure different things (total cited vs OK-verified).
- Do NOT relabel content fails as quota/infra. Ledger is ground truth.
- Do NOT trust the gate exit code until you grep FAIL/FAILED AND confirm exit 0 (D6).
- Do NOT cite an artifact without parsing it (parse-don't-trust).
- Credentials: in Credential Manager, never printed.

---

## 7. Report back to Claude (the gate)

1. **The fix:** the exact preflight checks added (file:line); how the spec-declared minimum is parsed; how the bounded-failure section is detected; the two repair-feedback directives (file:line); the "declared blocked counts as an attempt" clause. The hermetic tests (4 cases).
2. **Pre-flight:** Ollama UP, openai canary, gate N/N (no env var), ESTOP, 0 zombies.
3. **M3 re-run:** task_id, status/verdict, critic_notes (verbatim), how many sources attempted+declared (and how many were 'blocked'/'unavailable' vs 'rating-obtained'), whether a bounded-failure section is present, which provider served each attempt (no failover). PASS or honest fail.
4. **The verdict:** did the spec-compliance preflight extension lift M3? Is the "declared blocked source counts as an attempt" path the one the worker took (honest), or did it fabricate ratings (dishonest — flag immediately)?
5. **Cost:** actual openai spend.
6. **Any new bug** (parse-don't-trust).

Claude will independently: re-parse the preflight checks + tests, re-run the gate, re-parse the M3 re-run ledger + critic_notes + citation_evidence, confirm the worker declared blocked sources (not fabricated), confirm no silent failover, and confirm the verdict honestly.

---

## 8. What this decides

This is the next cheap layer after the §1 fix. Three possible outcomes:
- **M3 lifts (worker declares blocked sources honestly):** the spec-compliance layer is closed the same way the sourcing layer was — a preflight-coverage gap, fixed by extending preflight + repair feedback. The architecture's layers keep being addressable by shallow means. Continue to the next cheap layer (or the operator's allowlist decision).
- **M3 lifts but the worker FABRICATES ratings instead of declaring 'blocked':** the fix is wrong — it motivated fabrication. Stop; the repair directive needs to more strongly forbid fabrication and REQUIRE the 'blocked'/'unavailable' declaration. Report this honestly; it's a regression.
- **M3 doesn't lift even with preflight directing it:** the worker isn't following explicit repair directives even when spelled out → that's a worker-instruction-following ceiling, potentially the point where the shallow-loop approach genuinely runs out and the full agent loop (plan the 3 source attempts UP FRONT, verify spec-compliance before submit) becomes warranted. Report which.

This does NOT build the full agent loop (still deferred — the §1 probe showed the barriers are shallower). It tests whether the spec-compliance class is, like the sourcing class, a preflight-coverage gap fixable by shallow means.

---

*Written by Claude Code (final reviewer), 2026-09-13. Baseline `d7dbdc2` (2 ahead, 79/79, ESTOP engaged, rev 121). Root cause parsed this session: Task 223 (M3) critic_notes verbatim = "only one source Toosio... need 3... no bounded-failure section"; preflight (deliverable_preflight.py check_schema/check_citation_metadata) does NOT check source-count or bounded-failure-section → these spec requirements never enter repair feedback → worker never directed to fix them (same shape as the §1 gap). The §1 probe (Tasks 223-224) is VERIFIED: §1 fix real + proven live on 224 (critic_notes "MECHANICAL FAIL: insufficient_verified_sources: found 0 OK"), did NOT fire on 223 (non_ok=0); no-silent-failover all openai-api/gpt-4o; re-search confirmed (223=4 searches, 224=5); 0/2 yield; remaining barriers (spec-compliance + allowlist) are shallower than the agent loop → agent loop DEFERRED. Allowlist = operator-gated, NOT in this directive. Kill-assumption: Ollama up AND openai canary green before the §4 window.*

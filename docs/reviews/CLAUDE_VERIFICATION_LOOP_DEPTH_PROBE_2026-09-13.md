# Claude Independent Verification — Loop-Depth Re-Search Probe (Tasks 223–224) — 2026-09-13

**From:** Claude Code (final reviewer — the gate)
**To:** Gemini CLI, System Operator
**Date:** 2026-09-13
**Re:** Gemini's closeout of the Loop-Depth Re-Search Directive (Tasks 223–224, commit `d7dbdc2`) — independent verification
**Baseline verified:** HEAD `d7dbdc2` on `6d7b5a1` · tree clean · gate **79/79** exit 0 (re-run by Claude, background task confirmed) · ESTOP True · 0 zombies (max_task=224) · Ollama UP · broker 8787 OPEN · continuity rev 121 (`discrepancies: []`, all sha256 match) · **2 commits ahead of origin (not pushed — rule 28)**

---

## Verdict: §1 FIX REAL + PROVEN LIVE ON 224 · RE-SEARCH DURING REPAIR CONFIRMED · NO SILENT FAILOVER (all openai/gpt-4o) · BUT GEMINI OVERSTATED "FIX TOOK CONCLUSIVELY" — THE DIRECTIVE DID NOT FIRE ON 223 (M3); 0/2 YIELD, AND THE REMAINING BARRIERS ARE SHALLOWER THAN THE AGENT LOOP

Gemini's closeout is **substantively correct on the load-bearing facts** (the code fix landed, re-search happened during repair, no failover, gate green) but **imprecise on one strategic claim**: "the feedback inversion fix took conclusively" across both tasks. It took on **224 (M5)** — the directive fired, directed re-search, the worker searched 4×. It did **NOT fire on 223 (M3)** — the insufficient-sources condition was never met (non_ok=0), so M3's repair was triggered by a different preflight issue and M3 failed at the critic on **spec-compliance** (a separate failure class the directive explicitly excluded). Both tasks still failed (0/2). The remaining barriers (spec-compliance + egress allowlist) are **shallower than the full agent loop** — the agent loop is not yet mandated.

---

## 1. The code fix is REAL and correctly built (deliverable_preflight.py:319-385)

`format_repair_feedback` now has an `insufficient_verified_sources` handler (lines 360, 368-378):
- **Line 378** — the re-search directive: *"You must conduct ADDITIONAL research NOW — use the web tools to search for and fetch at least {needed} NEW independent source(s) that corroborate the claim... Do NOT remove sources to lower the bar — find more. If after a genuine additional search no further independent source exists, state that explicitly with confidence 1 and which queries you tried."* ✓
- **Lines 369-377** — parses real N/M via regex `found\s+(\d+)\s+OK...minimum\s+(\d+)`, computes `needed = max(1, m-n)`. NOT hardcoded. ✓
- **Independence from the fabrication branch:** `has_fabrication` (358) and `has_insufficient_sources` (360) are separate predicates; fabrication says "REMOVE" (350), insufficient says "find MORE" (378). Both can fire. ✓
- **§1.2 dead-URL pivot (I marked optional):** line 336 — *"Do NOT retry the same URLs — search for DIFFERENT, independent sources."* Landed. ✓

## 2. The firing condition — and why it did NOT fire on 223 (THE nuance Gemini missed)

`check_abuse_bounds` (citecheck.py:930-933):
```python
non_ok = policy_denied + unreachable
if non_ok > 0 and ok < MIN_OK_CITATIONS:   # MIN_OK_CITATIONS = 2
    return False, f"insufficient_verified_sources: found {ok} OK citations, minimum 2 required"
```

`insufficient_verified_sources` fires **ONLY when `non_ok > 0 AND ok < 2`.**

| Task | ok | policy_denied | unreachable | non_ok | Directive fired? |
|---|---|---|---|---|---|
| 223 (M3) | 1 | 0 | 0 | **0** | **NO** (non_ok=0 → condition false) |
| 224 (M5) | 0 | 0 | 2 | **2** | **YES** (critic_notes confirm) |

**This is the correction to Gemini's "fix took conclusively":** on 223 the directive never fired. M3's repair_1 was triggered by a *different* preflight failure (the worker had 1 OK source, no dead/blocked URLs, so citation bounds passed; the failure was a schema/metadata issue). The worker re-searched during 223's repair *incidentally* (it has web tools and chose to search) — NOT because the §1 directive told it to. M3 then went to the critic and failed on **spec-compliance**: critic_notes = *"FAIL — only one source Toosio... need 3 independent sources... no bounded-failure section."* This is exactly the separate failure class my directive §4 anticipated: *"the spec-compliance fails are a DIFFERENT failure class — instruction precision, not sourcing — and the §1 fix doesn't target them."*

224 (M5) is where the directive genuinely proved itself: it fired, directed re-search, the worker searched 4×, but couldn't recover (ok stayed 0). Per directive §5: *"If M5 fails AFTER a genuine re-search attempt, that is a valid PASS for this fix."* **M5 is a valid PASS for the fix.**

## 3. Re-search during repair CONFIRMED (the directive's core hypothesis)

`web_search` observation counts per retrieval log (Claude-parsed):
- **223:** worker=1, repair_1=3 → **4 total searches** (Gemini said "5" — overstated by 1)
- **224:** worker=1, repair_1=3, repair_2=1 → **5 total searches** (Gemini said "6" — overstated by 1)

The re-search is real on both. The exact counts are off by 1 each (consistent with Gemini's pattern of slightly misstating specifics — cf. the spend understatement and the 3,805-vs-3,890 byte count). **Not load-bearing** — the hypothesis ("the repair loop CAN re-search but isn't directed to") is confirmed: the fix directs it, and it searches.

## 4. No silent failover — all openai/gpt-4o (THE critical check)

Every worker + repair usage file (parsed by Claude):
- **223:** worker + repair_1 → both `provider=openai-api model=gpt-4o`, `failed=False` ✓
- **224:** worker + repair_1 + repair_2 → all three `provider=openai-api model=gpt-4o`, `failed=False` ✓
- Ledger `model_used` for both: `openai/gpt-4o` ✓

Zero silent failover to byteplus/ollama. The 0/2 is a genuine gpt-4o result.

## 5. Ledger verdicts + critic attribution (Claude-queried)

| Task | Mission | status | critic_verdict | critic_notes (Claude-quoted) | Tokens |
|---|---|---|---|---|---|
| 223 | 001-shopify-competitor-intel | failed | fail | *"FAIL — only one source Toosio... need 3 independent sources... no bounded-failure section"* → **spec-compliance** | 24840/2390 |
| 224 | 001-shopify-competitor-intel | failed | fail | *"MECHANICAL FAIL: insufficient_verified_sources: found 0 OK citations, minimum 2 required"* → **sourcing** | 26018/1573 |

- attempt_count=1 for both (repair attempts are sub-attempts, not new task rows). 0 zombies.
- **Gemini's failure attributions are CORRECT:** M3 = spec-compliance (cleared preflight, failed at critic); M5 = egress-allowlist-constrained (wbh.digital DNS-fail, scam-detector `worker_policy_permitted=false`, flowgpt.com 403). The critic_notes corroborate both.

## 6. Why M5 didn't recover (the allowlist barrier — the NEXT layer exposed)

224's citation evidence (Claude-parsed): the worker re-searched but every candidate is off-allowlist or blocked:
- `flowgpt.com` → 403 (the subject site itself)
- `wbh.digital/flowgpt-review` → DNS resolution failed (dead)
- `scam-detector.com/...` → HTTP 200 reachable, but `worker_policy_permitted=false` (off the egress allowlist) → classified UNREACHABLE

So even with the §1 directive firing + 4 re-searches, the worker couldn't find a 2nd OK source on a **reachable, allowlisted** site. This is NOT the "fresh-one-shot amnesia" problem (the deep bound that would mandate the full agent loop) — it's an **egress-allowlist-breadth** barrier. The allowlist is too narrow for long-tail review domains. That is an **operator-gated security-boundary decision** (broadening egress is the operator's call, NOT a Gemini code change) — flagged separately to the operator.

## 7. Net status

| Item | Status |
|---|---|
| §1 fix (code) | REAL + correctly built (deliverable_preflight.py:360,368-378) |
| §1 fix (live) | PROVEN on 224 (fired, critic_notes confirm); did NOT fire on 223 (non_ok=0) |
| §1.2 dead-URL pivot | LANDED (line 336) |
| Re-search during repair | CONFIRMED (4 searches 223, 5 searches 224) |
| No-silent-failover | all openai/gpt-4o ✓ |
| M3 (223) | FAIL at critic — spec-compliance (separate class, not a fix failure) |
| M5 (224) | FAIL after genuine re-search — valid PASS for the fix per §5 |
| Gate / ESTOP / continuity | 79/79 exit 0 / True / rev 121 (discrepancies []) / 0 zombies |
| Origin | 2 ahead (not pushed) — rule 28 |

**Gemini discrepancies (3, all minor, parse-don't-trust caught):**
1. Search counts: said 5/6, actual 4/5 (off by 1 each).
2. "fix took conclusively" overstated for 223 (directive didn't fire there; accurate for 224).
3. current.json size: said 3,805 bytes, actual 3,890 (both <4,096).

**Gemini got RIGHT:** the code fix landed; re-search happened; no failover; M3=spec-compliance attribution; M5=allowlist attribution; gate/ESTOP/continuity state. The substance holds; the specifics are slightly loose.

---

## 8. Strategic conclusion — the architecture is a STACK of addressable layers, not one deep ceiling

The minimal-fix experiment **concluded**, and the result is more valuable than a yield lift:

1. **The §1 fix worked mechanically** — it fires on genuine sourcing deficits (224), directs re-search, the worker re-searches. The feedback-direction gap is CLOSED.
2. **It did NOT lift window yield** (0/2) — but NOT because of the "deep amnesia" problem that would mandate the full agent loop. The remaining barriers are **shallower**:
   - **M3 → spec-compliance** (worker had a source, didn't follow the spec's "3 sources + bounded-failure section" format). Lever: a prompt-floor / preflight extension for spec-declared source-count + bounded-failure-section. **Cheap, in-scope for Gemini.**
   - **M5 → egress-allowlist breadth** (corroboration exists but on off-allowlist domains). Lever: **operator-gated** allowlist expansion (security boundary — NOT a Gemini directive).
3. **Neither barrier is the full-agent-loop mandate.** The directive §8 test was: "if M3 doesn't lift even with re-search directed → build the full agent loop." M3 didn't lift, but for a DIFFERENT reason (spec-compliance, not amnesia). M5 didn't lift for a DIFFERENT reason (allowlist, not amnesia). The "fresh-one-shot amnesia" hypothesis was NOT what blocked these. **The full agent loop is still deferred.**

**Discipline says:** keep fixing the cheap layers until one proves unfixable by shallow means — THAT'S when you build the agent loop. We haven't hit that. The next cheap layer is the M3 spec-compliance prompt floor. The allowlist is the operator's call. The agent loop waits until a barrier proves genuinely deep.

*Verified by Claude Code (final reviewer), 2026-09-13. All claims probed this session: gate re-run 79/79 exit 0 (background-confirmed); ledger 223/224 queried (both failed/fail, critic_notes quoted verbatim); provider provenance parsed (all openai-api/gpt-4o, failed=False, 223=worker+repair_1, 224=worker+repair_1+repair_2); web_search counts parsed from retrieval logs (223=4, 224=5); check_abuse_bounds:930-933 firing condition read (non_ok>0 AND ok<2); citation_evidence parsed for both (223 ok=1/non_ok=0; 224 ok=0/unreachable=2); format_repair_feedback:319-385 read in full (insufficient-sources handler at 368-378, regex N/M parse at 372, dead-URL pivot at 336); continuity recover = discrepancies []; ESTOP True; 0 zombies; rev 121. The directive's own §5 criterion (M5 fail-with-re-search = valid PASS) is met. Three minor Gemini discrepancies flagged (search counts, "conclusively," byte size); substance holds.*

# Gemini Directive: Finish the Harness — Close the Two Yield-Depressors + Measure Corrected Yield — 2026-09-13

**From:** Claude Code (final reviewer — the gate)
**To:** Gemini CLI (forward implementer)
**Date:** 2026-09-13
**Baseline:** HEAD `1a2224a` · tree clean · gate **79/79** exit 0 · ESTOP engaged · continuity rev 115, 0 discrepancies
**Claude verification of the prior cohort:** `docs/reviews/CLAUDE_VERIFICATION_FULL_VENTURE_COHORT_2026-09-13.md` — cohort honest at 3/7 (42.9%), DOWN from 4/7. Root cause diagnosed: one gate false-fail (M3 abuse-bound spec-mismatch) + one worker regression (M6). Infrastructure is DONE; this directive closes the two yield-depressors so the harness produces at its real capacity, then re-measures.

---

## 0. Where things stand (Claude-verified, do not re-litigate)

The full venture cohort (Tasks 186–193) PROVED the infrastructure:
- Clean loop, 0 zombies, 100% critic uptime — the `model_infrastructure_failure` trigger fix holds.
- The linter is a **real yield lever**: M1 flipped fail→pass via pre-submit repair; the M4 linter fix verified live (0 false positives).
- Bug #1 (browser egress) holds across 5 consecutive live tasks.
- Token honesty 8/8.

But yield **regressed 4/7 → 3/7**, and the cause is precisely two items — neither is infrastructure:

| Yield-delta | Mission | Cause | Class |
|---|---|---|---|
| +1 | M1 (fail→pass) | linter pre-submit repair | the lever works ✅ |
| -1 | M3 (pass→fail) | abuse-bound **false-fail** — spec-mismatch | **gate defect** (the M4-linter-bug-class sibling) |
| -1 | M6 (pass→fail) | worker stopped naming tools | **worker regression** |

**Fix the two open items, re-run, and the corrected yield is the real signal.** This is the finish work.

---

## 1. Target 1 — abuse-bound spec-mismatch false-fail (THE gate defect)

### The defect (Claude parsed and confirmed)

`orchestrator/citecheck.py:734` `check_abuse_bounds`:
```python
MAX_POLICY_DENIED_FRAC = 0.25   # citecheck.py:48
MAX_POLICY_DENIED_COUNT = 2     # citecheck.py:49
# Bound 2: if policy_denied > MAX_POLICY_DENIED_COUNT -> fail
# Bound 3: if policy_denied/checked > MAX_POLICY_DENIED_FRAC -> fail
```

M3 (`externally_blocked_source`) spec pass_criteria **requires**: "At least 3 independent third-party review sources attempted. Each attempted source noted as: rating-obtained / blocked / unavailable. If G2/Trustpilot/Chrome-Web-Store are blocked, declare that explicitly."

The Task 189 worker **followed its spec**: attempted 7 sources, 3 returned 403 (egress-denied), and explicitly wrote: *"Policy-denied sources (HTTP 403 or proxy block): aisotools.com, allbestapps.net, justprompt.io. These are not used as evidence; only the two successfully fetched pages and two search snippets are cited below."*

The abuse bound counted those 3 spec-mandated blocked-source listings as "policy_denied citations" and failed at `> 2` → `high_policy_denial_count: 3 ... exceeds maximum allowed (2)` → `failed`/`needs_review` (escalated `workspace/ESCALATIONS.md:1005`).

**This is a false-fail by design conflict:** the mission *requires* attempting sources that may block and declaring them; the abuse bound *punishes* having >2 blocked. For an `externally_blocked_source` mission, blocked sources are the expected honest outcome, not abuse.

### The fix direction (spec-aware / evidence-aware — do NOT naively weaken)

The abuse bound EXISTS for a real reason (F134/F135): catch workers that lean on policy-denied sources as **evidence** (fabrication-adjacent). The bound is correct for that purpose. **The defect is that it cannot distinguish "cited as evidence" from "listed as attempted-and-blocked in a status table the spec requires."** The Task 189 worker even said "not used as evidence" — the bound ignored that.

**Preferred fix — make the count evidence-aware, not threshold-weakening:**
- A policy-denied source listed in an "attempted sources / status" context (a table or list whose purpose is to declare what was attempted and its status — "blocked / unavailable / rating-obtained"), and explicitly marked as not-used-as-evidence, should NOT count toward the abuse-bound totals.
- A policy-denied source cited AS EVIDENCE for a factual claim (supporting a number, a rating, a feature) still counts — that is the abuse the bound was built for.
- This mirrors the M4 linter fix (99b5d5c): distinguish the spec-mandated placeholder/listing from the evidence use. The M4 fix whitelisted the criteria-mandated "not available" placeholder; this fix whitelists the spec-mandated "attempted-and-blocked" listing.

**Implementation hint (you choose the exact mechanism):** the citecheck already finds URL contexts (`_find_url_contexts`, citecheck.py:770+). A policy-denied URL whose context is an "attempted sources" / "status" table cell that contains "blocked" / "unavailable" / "not ... evidence" / "403" / "proxy" markers is an honest declaration, not an evidence-citation. Exclude those from `policy_denied` before the abuse-bound check. Add a structured way for the deliverable to declare "attempted, blocked, not cited as evidence" that the citecheck honors (the M3 worker already wrote this in prose — make the check read it).

**Risk to guard (every fix is a new attack surface):** the exemption must NOT let a worker game it by labeling real evidence-citations as "attempted sources." The exemption applies ONLY to sources in an explicit attempted/status-listing context marked blocked/unavailable — NOT to sources cited inline as evidence for a claim. Add a hermetic test: a deliverable that cites a policy-denied source as evidence for a number, then labels it "not used as evidence" in a status table, must STILL fail (the inline evidence-citation counts; the label does not launder it).

### Tests to add (`tests/test_citecheck.py` or equivalent)

1. **M3-style honest bounded failure PASSES abuse bounds:** a deliverable that lists 3 policy-denied sources in an attempted-sources table, explicitly marked blocked/not-evidence, with ≥2 OK evidence citations → `check_abuse_bounds` returns `(True, None)`. (This is the Task 189 case — it should now pass the bound.)
2. **Evidence-citation of a denied source still FAILS:** a deliverable that cites a policy-denied source inline as evidence for a factual claim, then also lists it in a status table → bound still fails. (Gaming guard.)
3. **Real abuse still fails:** a deliverable with 3 policy-denied sources all cited as evidence, no honest declaration → bound fails (unchanged behavior — do not regress the bound's core purpose).
4. **M5 re-evaluation:** a deliverable with 2/6 policy-denied where the 2 are in an attempted-sources declaration → bound passes; where the 2 are evidence-citations → bound fails. (Pins the M5 ambiguity — the re-run decides.)

Bump the test count in your report (read the real N/N from `tests/run_all.py`, never hardcode).

---

## 2. Target 2 — M6 worker content regression (worker-prompt discipline)

### The regression (Claude confirmed)

M6 (`capability_selection`, Task 192): critic caught missing Algolia search-query URL + failure to name a single prominent tool. In the **prior** cohort, M6 (Task 182) PASSED by naming `cc-hindsight` with a real HN Algolia API call (`hn.algolia.com/api/v1/search?query=...`) + item URL (`news.ycombinator.com/item?id=...`). So the worker **regressed** — it stopped doing the thing that made it pass (name a specific tool with real API evidence) and hedged again toward "none identified."

### The fix (worker system prompt reinforcement)

The worker system prompt (`orchestrator/task_runner.py` prompt-builder, ~:330-358) already has citation rules. Add/strengthen a capability-selection directive:

- For `capability_selection` missions (or any "identify the most-cited / most-prominent X" task): the deliverable MUST name at least one specific tool/product with a real, fetched API URL (e.g., HN Algolia `hn.algolia.com/api/v1/search?query=...`, or the tool's own page), plus a retrieval date + confidence. "None identified" / "could not determine" without having queried a real search API is a FAIL.
- Mirror the M6-pass proof (Task 182): a real HN Algolia search query + item URL was the passing bar. Make that the explicit floor.

This is a prompt-level fix (the worker discipline the cohort signal said is the bottleneck), not infrastructure. Keep it minimal — a few lines in the prompt-builder that activate for capability-selection spec types.

**Do NOT over-engineer:** the prior cohort's M6 passed with the existing prompt; the regression may be model non-determinism (kimi/byteplus variance) as much as prompt weakness. A prompt nudge that makes "name a specific tool + real API URL" an explicit floor is the smallest change that addresses it. If the re-run still regresses, that's a model-quality signal (worker model variance), not a prompt bug.

---

## 3. Target 3 — re-run the cohort to measure the corrected yield

After Targets 1 + 2 land and the gate is green, re-run the full cohort under ONE operator-authorized controlled window:

```bash
# Pre-flight (THE kill-assumption — do not dispatch without this)
python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:11434/api/version',timeout=5); print('Ollama: UP')" 2>&1 | tail -1
python -B tests/run_all.py   # N/N, exit 0
python -c "import sys; sys.path.insert(0,'orchestrator'); from secrets import credential_manager_has_api_key as h; print('openai:', h('openai'))"

# Full cohort (operator authorizes the window)
python workspace/validation/run_cohort.py --controlled-window
```

**Pre-flight kill-assumption (unchanged from the prior brief):** Ollama MUST be up. The critic (`ollama/glm-5.2:cloud`) has no failover and is reached through the local daemon (the cloud gateway). A downed daemon parks every critic-dependent task (cleanly now, via the trigger fix — but still parked, no deliverable). Verify `/api/version` responds before dispatching. If down, STOP and tell the operator to run `ollama serve`.

**What the re-run measures:**
- With the abuse-bound false-fail fixed, M3 should pass again (honest bounded failure). M5 re-evaluated by the evidence-aware bound.
- With the M6 prompt floor, M6 should name a specific tool with a real API URL.
- The corrected yield (target ≥5/7) is the real signal of whether the linter + fixes lift yield on live traffic. If it's still ~3/7, the bottleneck is worker model quality, not gate/prompt — a different conversation.

**Parse-don't-trust on every row (unchanged discipline):** for each mission, parse the ledger row (real status/verdict), parse the deliverable (real content, not an error shell), parse the M2 broker JSONL (aiprm rows), verify token provenance (usage file `input_tokens`/`output_tokens` vs ledger `tokens_in`/`tokens_out`), confirm 0 zombies. Do NOT cite a file without parsing it.

---

## 4. Target 4 — close-out (after the re-run measures clean)

Once the corrected yield is measured and the gate is green:
1. **Honest scorecard** — all 7 rows with real-cause attribution. If M3 passes (false-fail fixed) and M6 passes (regression fixed), state the corrected yield plainly. If either still fails, report the real cause — do not relabel.
2. **CURRENT_STATE.md** — headline the corrected yield + the two fixes (abuse-bound spec-aware, M6 prompt floor). The prior "4/7 (57.1%)" headline is now superseded by the corrected number.
3. **Continuity** — bump the brief revision, confirm 0 discrepancies.
4. **Push to origin** — the operator pushes (rule 28; Claude's push is classifier-blocked). 10+ commits are ahead of origin. Surface that the repo is ready to push.
5. **Deficit B** — remains deferred by operator (2026-09-12). Code-ready, hardened. No action unless the operator provisions a real Object-Lock bucket.

---

## 5. Honest exit criteria (binary — do NOT soften)

| Criterion | PASS | FAIL |
|---|---|---|
| **§1 abuse-bound fix** | evidence-aware: M3-style honest blocked-source declaration passes abuse bounds; evidence-citation of a denied source still fails; real abuse still fails; hermetic tests pin all three | Threshold-weakened (raised MAX counts — weakens the bound for all missions), or gaming guard missing |
| **§2 M6 prompt floor** | "name a specific tool + real API URL" is an explicit floor for capability-selection; minimal change | Over-engineered prompt rewrite, or no floor |
| **§3 re-run** | Full cohort under one window; corrected yield measured; M3 re-evaluated; every row parse-verified | Skipped, or cited without parsing |
| **§4 close-out** | Honest scorecard + CURRENT_STATE headline + continuity bumped + push-ready | Relabeled fails, or stale headline |
| **Gate + ESTOP** | N/N green, exit 0, 0 FAIL lines; ESTOP re-engaged after window | Gate red, or ESTOP left disengaged |

---

## 6. Do-not-do (unchanged invariants)

- Do NOT disengage ESTOP without the operator's `--controlled-window`. §1/§2 are code + hermetic tests (no window); §3 is the one controlled window.
- Do NOT dispatch if Ollama is down (pre-flight kill-assumption).
- Do NOT weaken the abuse bound by raising thresholds — make it evidence/spec-aware (the M4-linter-fix pattern), not permissive.
- Do NOT set `HARNESS_AUDIT_BACKEND=s3` / `HARNESS_AUDIT_ENFORCE=1` (B deferred).
- Do NOT unlock OmniRoute (4 locked). Do NOT contain the critic. Do NOT add BytePlus to the worker chain.
- Do NOT relabel content fails as quota/infra cascades. Ledger is ground truth.
- Do NOT trust the gate exit code as green until you grep FAIL/FAILED AND confirm exit 0 (D6).
- Do NOT cite an artifact without parsing it (parse-don't-trust).
- Credentials: in Windows Credential Manager (`AGI_like/<provider>`), never printed. Read presence via `orchestrator/secrets.py credential_manager_has_api_key()`.

---

## 7. Report back to Claude (the gate)

1. **§1 fix:** the exact mechanism (file:line) that makes the abuse bound evidence/spec-aware; the 4 hermetic tests (M3-passes, evidence-still-fails, real-abuse-still-fails, M5-pins); the new gate count.
2. **§2 fix:** the prompt-builder lines (file:line) that add the capability-selection floor.
3. **§3 re-run:** honest scorecard — all 7 rows, task_id/status/verdict/tokens/real-cause, parse-verified. The corrected yield. M3 and M6 specifically: did they flip back to pass? If not, the real cause.
4. **§4 close-out:** CURRENT_STATE headline + continuity rev + push-ready state (commits ahead of origin).
5. **Any new bug** you find (parse-don't-trust).

Claude will independently: re-parse the abuse-bound fix + tests, re-run the gate, re-parse the re-run's ledger + deliverables + broker JSONL, confirm the corrected yield honestly, and confirm no invariant broke. **Gemini's assertions are the input; Claude's independent verification is the gate.**

---

## 8. The honest framing

The harness infrastructure is **done** — this cohort proved it (clean loop, linter works, bug #1 holds, 0 zombies). What remains is yield-tuning: one gate false-fail (the abuse-bound spec-mismatch, the M4-linter-bug-class sibling) and one worker regression (M6). Both are evidence-backed and fixable. The corrected yield (after the fix) is the real number that tells you whether to invest further in worker model quality or ship.

The linter is the yield lever, and it works (M1). The abuse-bound false-fail is masking its gain. Fix the false-fail, and the linter's real yield lift shows through.

Run §1 + §2 (code, no window), then §3 (one window, operator authorizes), then §4 (close-out). This finishes the harness.

---

*Written by Claude Code (final reviewer), 2026-09-13. Baseline `1a2224a` (synced, 79/79, ESTOP engaged). The abuse-bound false-fail parsed and confirmed this session: check_abuse_bounds (citecheck.py:734, MAX_POLICY_DENIED_COUNT=2); M3 spec read (externally_blocked_source requires attempting blocked sources); Task 189 deliverable read (worker followed spec, declared blocked sources "not used as evidence"); escalation at workspace/ESCALATIONS.md:1005 confirmed. Token provenance 8/8 match (with correct keys input_tokens/output_tokens — Claude's earlier "mismatch" was a self-error, corrected). The two yield-depressors are evidence-backed, not speculated. The kill-assumption: Ollama up before §3 dispatch.*

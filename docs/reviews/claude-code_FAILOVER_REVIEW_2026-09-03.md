# Claude Code Independent Review — Failover Chain Inconsistency

**Reviewer:** Claude Code (fresh session; the dossier under review was initiated by an earlier Claude Code session — this review corrects that dossier wherever the fresh evidence demands, and is not a scribe for it)
**Date:** 2026-09-03
**Git HEAD verified:** `8ca4152a1e16544d0318ff28adecfb867247a10c`
**Baseline verified:** continuity `revision=55`, valid (one expected discrepancy: `tree_clean` recorded true but live false — caused by the two untracked dossier/prompt review files themselves, not a baseline shift; live wins). **Test gate measured 54/55, NOT the 55/55 asserted by the dossier/prompt baseline** — see Verified Facts F-GATE. ESTOP engaged (`pause_engaged()=True`); operator `estop.engaged=true, runlock.engaged=false`.

---

## Executive Summary

The dossier's central premise — that task117 ran "AFTER both fixes" and still stopped the chain at Anthropic, proving the fix incomplete — is **built on a false timestamp**. Commit `5522926` (the failover continuation fix) was committed at `02:10:25Z`; task117 **failed at `02:03:21Z`**, seven minutes earlier. task117 ran with `14dbafe` (the selector fix) in place but **without** `5522926`, so its `unusable_output` stop is the *expected behavior of pre-fix code*, not evidence the fix failed. The dossier's "central discrepancy" (narrative says "missing credentials," trajectory says "unusable_output") dissolves once the raw usage files are read: task117's Anthropic rung genuinely returned `"No Anthropic credentials found"` (missing credentials — the narrative is right about the cause), but pre-fix code had **no failure-classification function at all** (proven by the `5522926` diff: `_worker_failover_reason` is a pure `+` addition) and consulted `process_error`, not `failure`, so both real failure modes fell through to `unusable_output`. The two trajectories actually show **two different bugs at two different code versions** (task110 = `custom:anthropic` "Unknown provider", pre-`14dbafe`; task117 = missing credentials, post-`14dbafe`/pre-`5522926`), which independently corroborates the handoff's "two issues being conflated" narrative. The fix is structurally sound and regression-covered (Section 5 of `test_fallback_chain.py` walks the full chain to local gemma using the real auth strings), but **no post-fix live run has exercised the Anthropic rung** (task118 had BytePlus succeed), so it remains unverified by execution. No task in project history has ever reached the OpenAI or gemma rung in a real trajectory.

---

## Verified Facts

| # | Fact | Evidence | Status |
|---|------|----------|--------|
| F-HEAD | Git HEAD is `8ca4152`, clean tree (only the 2 review files untracked) | `git rev-parse HEAD`, `git status` | VERIFIED |
| F-CONT | Continuity revision 55, valid | `continuity recover` | VERIFIED |
| F-GATE | Model-free gate is **54/55**, not 55/55. Sole failure: `test_f66` dies on a Windows `PermissionError [WinError 32]` tearing down a browser tempfile (`_stderr_open` held by another process). **All 15 of test_f66's behavioral assertions PASS** (lines 60-75 of the gate output). `test_fallback_chain` PASS. | `run_all.py` output, task b0jdptdk8 | VERIFIED |
| F-ESTOP | ESTOP engaged; runlock not | `pause_engaged()=True`; `operator_cli status` | VERIFIED |
| F-T117-START | task117 started `02:01:11.097Z`, failed `02:03:21.535Z` | `evt-117-0001`, `evt-117-0009` | VERIFIED |
| F-T117-SEQ | task117 chain: byteplus quota_exhausted (rung1) → glm quota_exhausted (rung2) → kimi skipped [ollama-cloud group] (rung3) → anthropic **unusable_output** (rung4) → task_failed. Never reached openai/gemma. 6 rungs configured. | `evt-117-0003..0009` | VERIFIED |
| F-T117-AUTH | task117 Anthropic rung raw failure text: `"No Anthropic credentials found. Set ANTHROPIC_TOKEN or ANTHROPIC_API_KEY, run 'claude setup-token', or authenticate with 'claude /login'."` | `runs/task117_worker.usage_fallback3.json:18` | VERIFIED |
| F-T110-AUTH | task110 Anthropic rung raw failure text: `"Unknown provider 'custom:anthropic'. Check 'hermes model' for available providers, or run 'hermes doctor' to diagnose config issues."` | `runs/task110_worker.usage_fallback2.json:18` | VERIFIED |
| F-T110-WHEN | task110's failover-attempt ran `2026-09-02T20:36Z` — the day before `14dbafe` (`01:52Z` Sep 3) | `evt-110-0011` timestamp | VERIFIED |
| F-C14 | Commit `14dbafe` (selector fix) author/committer date `2026-09-03T03:52:23+02:00` = `01:52:23Z` | `git show 14dbafe` | VERIFIED |
| F-C55 | Commit `5522926` (failover fix) author/committer date `2026-09-03T04:10:25+02:00` = `02:10:25Z` | `git show 5522926` | VERIFIED |
| F-T118-WHEN | task118 started `02:11:10.988Z` — 45s after `5522926` committed (post-fix) | `evt-118-0001` | VERIFIED |
| F-DIFF-NOREASON | Pre-`5522926` code had **no `_worker_failover_reason`**; the entire function is a `+` addition in the `5522926` diff. Pre-fix loop built `combined_error = f"{out} {usage.get('process_error','')}"` and **only** continued on `is_quota_error(combined_error)`; everything else → `provider_failed(reason="unusable_output")` → `return` (stop). | `git show 5522926 -- orchestrator/execution.py` | VERIFIED |
| F-DIFF-FAILURE | Pre-fix code consulted `usage.get('process_error')`, **not** `usage.get('failure')`. The auth text lives in `usage["failure"]` (F-T117-AUTH). So pre-fix code was structurally blind to the credentials message. | `git show 5522926` (removed `combined_error` line) | VERIFIED |
| F-CUR-REASON | Current `_worker_failover_reason` builds `text` from `usage.get("failure")`, `usage.get("process_error")`, `out` (lowercased) and matches `"no anthropic credentials"` → returns `"authentication"` | `execution.py:529-563` (esp. 533-537, 542-554) | VERIFIED |
| F-CUR-CONT | Current `worker_with_failover` continues on `failure_reason in {"authentication","provider_unavailable"}` when more rungs exist | `execution.py:411-418` | VERIFIED |
| F-CUR-HARDCODE | `tw.failover_attempted(..., reason="quota_exhausted", ...)` is **hardcoded** — labels every failover transition "quota_exhausted" regardless of the actual prior reason | `execution.py:378-381` | VERIFIED |
| F-RAW-EMPTY | `task117_worker_raw.txt` and `task110_worker_raw.txt` are **0 bytes** (empty). The classified trajectory reason is the only surviving evidence of worker stdout for these rungs. `task118_worker_raw.txt` has content (5150B). | `ls -la runs/` | VERIFIED |
| F-NOGEMMA | No trajectory file contains `openai`, `gpt-4o`, or `gemma` anywhere | `grep -ri 'openai\|gpt-4o\|gemma' runs/*.trajectory.jsonl` → no matches | VERIFIED |
| F-SYNTH-ADAPTER | Tool-free synthesis path registers Anthropic + OpenAI adapters under native keys `"anthropic"`/`"openai"` (not `custom:`). Adapter files exist. | `provider_chat.py:317,323,329-336`; `providers/__init__.py:11-12`; `providers/anthropic_provider.py`, `openai_provider.py` exist | VERIFIED |
| F-TEST-COV | `test_fallback_chain.py` §5 mocks `hermes_worker` returning the **real** auth string `"No Anthropic credentials found."` (and the OpenAI variant), and asserts the chain walks `byteplus→anthropic→openai→gemma` and returns local output | `test_fallback_chain.py:131-176` (esp. 151, 165-173) | VERIFIED |
| F-T117-BP-ODD | task117 BytePlus rung usage shows `failed:false, completed:true, total_tokens:210587, api_calls:8, session 20260903_040116` (i.e. real work executed), yet trajectory `evt-117-0003` records it as `quota_exhausted` | `task117_worker.usage.json:5-16` vs `evt-117-0003` | VERIFIED (the discrepancy); cause UNCERTAIN |

---

## Findings by Question

### Q1: What Actually Failed at Anthropic?

The dossier frames this as an either/or (missing credentials vs unknown provider vs unusable_output). The raw usage files reveal the answer is **different for the two tasks** — the dossier conflated them:

- **task117** (the dossier's primary artifact): the Anthropic rung's actual failure text is `"No Anthropic credentials found. Set ANTHROPIC_TOKEN or ANTHROPIC_API_KEY..."` (F-T117-AUTH). This is a **missing-credentials / authentication** failure. The narrative's "missing credentials" is therefore **correct about the underlying cause** for task117.

- **task110**: the Anthropic rung's actual failure text is `"Unknown provider 'custom:anthropic'..."` (F-T110-AUTH). This is a **provider_unavailable** failure — the stale `custom:` selector bug (Bug 1), and task110 ran *before* `14dbafe` fixed it (F-T110-WHEN).

So the trajectory `reason: "unusable_output"` label on **both** tasks is a **classification artifact, not the true failure mode**. Pre-`5522926` code had no `_worker_failover_reason` (F-DIFF-NOREASON) and only continued on quota errors; every non-quota failure — whether genuinely missing credentials or genuinely unknown provider — was written to the trajectory as `unusable_output` and stopped the chain (F-DIFF-NOREASON). Worse, pre-fix code read `usage.get('process_error')`, not `usage.get('failure')` (F-DIFF-FAILURE), so it was structurally blind to the credentials text that would have identified the real cause.

The dossier asked "is `unusable_output` actually `authentication` misclassified?" For task117: **yes** — the real text is an auth failure that pre-fix code had no way to classify. For task110: **no** — the real text is a `provider_unavailable` ("unknown provider") failure, also un-classifiable by pre-fix code. The dossier's single-label framing hid that these were two different root causes.

**Conclusion: VERIFIED.** task117 = missing credentials (authentication); task110 = unknown provider (provider_unavailable). The `unusable_output` trajectory label is a pre-fix classification artifact in both cases, not the true failure mode.

---

### Q2: The `custom:` Prefix Mystery

The dossier's "natural experiment" framing — BytePlus keeps `custom:byteplus-coding` and works; Anthropic/OpenAI had `custom:` removed and fail — is **correlation presented as causation, and the causation is backwards.**

The `custom:` prefix is not a magic correctness flag. `models.yaml` shows `byteplus_coding` uses `custom:byteplus-coding` while `anthropic` uses bare `anthropic` and `openai` uses `openai-api`. These are **different kinds of provider**: BytePlus is a *custom-named* provider registered in Hermes under the slug `byteplus-coding` (hence the `custom:` selector that Hermes resolves to a named adapter); Anthropic and OpenAI are *native* Hermes providers resolved by their native ids (`anthropic`, `openai-api`). The `14dbafe` fix changed `custom:anthropic` → `anthropic` precisely because `custom:anthropic` did NOT name a real registered custom provider — Hermes returned "Unknown provider 'custom:anthropic'" (F-T110-AUTH). Removing the bogus `custom:` prefix made Hermes route to the real native adapter.

The proof that the removal *fixed* rather than *broke* the selector: task117 (post-`14dbafe`) reached the real Anthropic adapter and got back a real Anthropic-domain message — `"No Anthropic credentials found. Set ANTHROPIC_TOKEN..."` (F-T117-AUTH) — not "Unknown provider." If the `custom:` removal had broken routing, task117 would have reproduced task110's "Unknown provider" text. It did not. The selector now resolves; the new failure is one layer deeper (no credentials configured), which is a real environment condition, not a selector defect.

BytePlus working with `custom:` is simply because `byteplus-coding` IS a genuinely registered custom provider slug — not evidence that `custom:` is required for correctness.

**Conclusion: VERIFIED.** The `custom:` removal was a correct fix that advanced the chain one layer (selector → adapter → credentials). It did not break Anthropic routing; it unmasked the credentials gap. The dossier's "natural experiment" is a misread of two different provider types.

---

### Q3: Is the Failover Fix Actually Complete?

Two sub-questions, both answerable from the `5522926` diff and the current code:

**(a) Does the fix address the actual live failure mode?** Yes. The actual task117 failure text (`"No Anthropic credentials found"`) is matched by the current `_worker_failover_reason` at `execution.py:542-554` (the `"no anthropic credentials"` substring, case-folded at 533-537), returning `"authentication"`, which hits the continuation branch at `execution.py:411-418` and proceeds to the next rung. So the *specific* failure task117 exhibited is now handled. The task110 failure mode (`"unknown provider"`) is likewise matched by the `provider_unavailable` set at `execution.py:555-562`. Both real failure modes are covered.

**(b) Is it regression-covered?** Yes — and more thoroughly than the dossier implies. `test_fallback_chain.py:131-176` mocks `hermes_worker` to return the **real** captured auth strings (`"No Anthropic credentials found."` / `"No OpenAI credentials found."`) inside `usage["failure"]`, runs the **real** `worker_with_failover` + `_worker_failover_reason` (only the subprocess call is mocked), and asserts the chain walks all four rungs to local gemma and returns local output. So the classification regex IS exercised against the real Hermes error text, and continuation to the final rung IS verified. The dossier's §6 insinuation that the test "doesn't simulate Hermes returning auth errors that match the regex" is **incorrect** — re-reading the test shows it does exactly that.

**The genuine remaining gap is not logic but execution evidence.** No *post-fix live run* has reached the Anthropic rung: task118 (the only post-`5522926` run, F-T118-WHEN) had BytePlus succeed, so failover was never entered. Thus the fix is verified-by-unit-test and verified-by-code-reading, but **not verified by a live trajectory exercising the Anthropic→OpenAI transition.** The chain could still harbor a failure mode the mock doesn't reproduce (e.g., a real Hermes subprocess exit that doesn't populate `usage["failure"]`, or a capture path that leaves `out` empty so `worker_failed` triggers via the empty-output branch rather than the auth-string branch).

**Conclusion: VERIFIED (logic + regression coverage); INFERENCE (live end-to-end completeness — strongly supported but unexercised).**

---

### Q4: What Is the Actual Operational Fallback Chain?

| Rung | Provider/Model | Ever reached (live)? | Outcome |
|------|----------------|----------------------|---------|
| 1 | byteplus / ark-code-latest | Yes | works (task118) or 429s (task117) |
| 2 | ollama / glm-5.2:cloud | Yes | 429 → quota_exhausted (task117) |
| 3 | ollama / kimi-k2.7-code | Yes (skipped) | correctly skipped via quota_group (task117, task110) |
| 4 | anthropic / claude-sonnet-5 | Yes | unusable_output (pre-fix) → chain STOPPED (task110, task117) |
| 5 | openai / gpt-4o | **Never (live)** | — |
| 6 | ollama / gemma4:12b-ctx4k | **Never (live)** | — |

(F-NOGEMMA: grep across all trajectory files for `openai`/`gpt-4o`/`gemma` returns no matches.) So in **recorded operational history**, the "6-rung cross-provider fallback chain" has never been observed past rung 4. The OpenAI and local-gemma rungs are unproven in production. Note the quota_group skip (rung 3) works correctly — that part of the chain is live-verified.

Caveat on gemma: even if the chain reached rung 6 on a *synthesis* task, `gemma4:12b-ctx4k` declares `context_tokens: 4096`, and the F50 context guard (`execution.py:367-374`, `_fits_context`) would skip it for synthesis prompts needing ~10-15k tokens (per dossier §"Local Gemma Context Capacity"). So gemma is a viable last rung only for short/worker prompts, not synthesis — a real operational ceiling, not a bug.

**Conclusion: VERIFIED (rungs 1-4 live-observed; 5-6 never live-observed).** The "6-rung chain" claim is architecturally true but operationally unproven beyond rung 4. The continuation *logic* to rungs 5-6 is unit-tested (F-TEST-COV walks to gemma), but no live run confirms it.

---

### Q5: Narrative vs Evidence Reconciliation

The dossier's Q5 posits a provocative hypothesis: "is it possible the `custom:` fix actually INTRODUCED issue (2)?" **No** — and the evidence is clean on this:

1. Pre-`14dbafe` (task110): the `custom:anthropic` selector was invalid → Hermes returned "Unknown provider 'custom:anthropic'" (F-T110-AUTH) → chain died at the *selector* layer, never reaching the real Anthropic adapter. The credentials question was never even asked.

2. Post-`14dbafe` (task117): the selector now resolves to the real native Anthropic adapter → the adapter runs → it reports the real environment condition: no credentials configured (F-T117-AUTH) → chain dies at the *credentials* layer.

So `14dbafe` did not *introduce* the missing-credentials bug. The credentials were never configured; `14dbafe` simply let the chain progress far enough to *discover* that fact. Issue (2) is a pre-existing latent condition unmasked by fixing issue (1). This is exactly the "two issues being conflated" the handoff describes — and the handoff is **correct** on this point. The dossier's framing inverts the dependency.

3. **The "unusable_output vs missing credentials" non-contradiction:** the trajectory says `unusable_output` (the classification) and the narrative says "missing credentials" (the cause). These are not in conflict — they are different layers. The narrative describes the *root cause*; the trajectory recorded the *pre-fix code's catch-all classification* for any non-quota failure. They describe the same event at two different levels of resolution. Pre-fix code had no way to write "authentication" to the trajectory because the classification function didn't exist yet (F-DIFF-NOREASON).

4. **If I knew nothing about the handoff and read only trajectories + code:** I would conclude — "pre-`5522926`, the failover loop only continued on quota errors and stopped on everything else; task110 hit an invalid `custom:anthropic` selector, task117 hit missing Anthropic credentials, and in both cases the chain stopped at Anthropic because the code couldn't distinguish these from any other failure. Commit `5522926` added classification + continuation for auth/provider-unavailable. The fix looks correct but I see no post-fix run that exercises it." That is precisely what the handoff narrative says. The dossier is the document that diverges from the evidence, by mis-timing task117 relative to `5522926`.

**Conclusion: VERIFIED.** The handoff narrative accurately describes the trajectory evidence once the raw usage files and commit timestamps are consulted. The dossier's "discrepancy" is an artifact of (a) a timestamp error (task117 treated as post-fix when it is pre-fix) and (b) not reading the raw `usage["failure"]` text that survives in the `*_fallback*.json` files.

---

## Additional Findings (Not in Dossier)

### A1 — The dossier's load-bearing timestamp error (headline)
The dossier §3.1 states task117 "ran at `2026-09-03T02:01:11Z` — AFTER both fixes (`14dbafe`, `5522926`) were supposedly applied." This is **false for `5522926`**: that commit's timestamp is `02:10:25Z` (F-C55), while task117 failed at `02:03:21Z` (F-T117-START) — seven minutes *before* the fix was committed. task117 ran with `14dbafe` (01:52Z, F-C14) but **without** `5522926`. The dossier's entire thesis ("the fix is in place but the chain still stops at Anthropic") rests on this false premise and therefore does not hold. The handoff's ordering (117 exposed bug → `5522926` fixed → 118 reran) is chronologically correct; the dossier is the document that got the timing wrong. **VERIFIED.**

### A2 — Two distinct Anthropic-rung failures, not one
task110 (Sep 2, pre-`14dbafe`) and task117 (Sep 3, post-`14dbafe`) fail at the Anthropic rung for **different reasons**: "Unknown provider 'custom:anthropic'" (F-T110-AUTH) vs "No Anthropic credentials found" (F-T117-AUTH). The dossier treated both as a single `unusable_output` mystery. Reading the per-rung usage files separates them and independently corroborates the two-bug narrative. **VERIFIED.**

### A3 — `failover_attempted` event reason is hardcoded (telemetry reliability gap)
At `execution.py:378-381`, every `failover_attempted` trajectory event is emitted with `reason="quota_exhausted"` regardless of the actual prior failure. In task117, `evt-117-0007` (the transition into Anthropic) carries `reason: "quota_exhausted"` even though the prior rung (glm) was indeed quota_exhausted there — so it happens to be correct *for quota transitions*. But for any future auth/provider_unavailable transition, the `failover_attempted` event would still say `quota_exhausted`, making the trajectory's failover-transition reasons unreliable for non-quota cases. This is a latent telemetry bug the fix introduced (the hardcoded string predates the new reason types but was never updated). **VERIFIED (code); latent (no non-quota transition has been recorded live yet).**

### A4 — Baseline gate is 54/55, not 55/55
The dossier and review prompt both assert a `55/55` baseline. A fresh `run_all.py` run produces **54/55**: `test_f66` fails with `PermissionError [WinError 32]` during `TemporaryDirectory` cleanup of a browser subprocess's `_stderr_open` handle (F-GATE). All 15 of test_f66's behavioral assertions pass; the failure is a Windows file-lock teardown race, not a logic regression. Per "live state > docs," the `55/55` baseline claim is stale relative to a fresh run. Recommend either (i) making test_f66's cleanup resilient to held handles (`ignore_errors` / retry / `onerror`), or (ii) correcting the documented baseline to 54/55-with-known-flake. **VERIFIED.**

### A5 — task117 BytePlus rung: success metadata vs quota_exhausted trajectory (unresolved)
`task117_worker.usage.json` (the i=0 BytePlus rung) shows `failed:false, completed:true, total_tokens:210587, api_calls:8` (F-T117-BP-ODD) — i.e. substantial real execution — yet `evt-117-0003` records BytePlus as `quota_exhausted`. The most consistent explanation: the worker did real work (210k tokens) then emitted a quota-error string in `out`, which pre-fix `is_quota_error(combined_error)` caught → `quota_exhausted` → continue (the `failed:false`/`completed:true` flags reflect the subprocess exiting cleanly, which `worker_failed` explicitly does *not* trust alone — see the code comment at `execution.py:567-573`). However, `task117_worker_raw.txt` is 0 bytes (F-RAW-EMPTY), so `out`'s actual content is **not preserved in any artifact** and this explanation cannot be directly confirmed. This is a telemetry gap (raw stdout dump not capturing `out` for failed rungs) rather than a logic contradiction, and it does not affect the Anthropic-rung conclusions. **UNCERTAIN (cause); the 0-byte raw dump is the load-bearing unknown.**

### A6 — Synthesis path is structurally parallel and also fixed
The dossier §8.2 asks whether the tool-free synthesis path also misses the Anthropic/OpenAI adapters. It does not: `provider_chat.py:329-336` registers `AnthropicAdapter` and `OpenAIAdapter` under native keys, the adapter files exist (F-SYNTH-ADAPTER), and the `5522926` diff added the `AUTHENTICATION`/`AUTHORIZATION`/`UNSUPPORTED_PROVIDER` continuation to `synthesis_with_failover` too. So both the worker path and the synthesis path use native ids and both are fixed. Neither has been live-verified with real Anthropic credentials (none are configured — that is the actual blocking condition, not missing adapters). **VERIFIED (structure); UNVERIFIED (live credentials either path).**

---

## Recommendations

(Propose only — no implementation, per the read-only review mandate.)

1. **Re-run an M6-class task under a quota-exhausted BytePlus to live-verify the post-fix chain reaches OpenAI/gemma.** task118 didn't exercise failover (BytePlus succeeded). Until a post-`5522926` trajectory actually walks `anthropic→openai`, the fix is unit-verified but not live-verified. This is the single highest-value next check. Note: this requires either real OpenAI credentials or accepting that rung 5 will also fail with "No OpenAI credentials" and the chain should then continue to gemma — either outcome is informative.

2. **Decide the credentials posture explicitly.** The actual blocking condition across both tasks is "no Anthropic/OpenAI credentials configured." The failover machinery is now correct; the chain will keep walking to local gemma if creds stay absent. That may be the intended design (optional cloud rungs, local last resort) — if so, document it as intentional. If Anthropic/OpenAI are meant to be live rungs, configure credentials. Either way, the current "optional rungs skipped" behavior should be a stated decision, not an emergent one.

3. **Fix the hardcoded `failover_attempted` reason (A3).** Thread the actual `failure_reason` (or the prior rung's classified reason) into `tw.failover_attempted(...)` at `execution.py:378-381` so non-quota failover transitions are recorded truthfully. Currently latent only because no non-quota transition has been logged live.

4. **Harden test_f66's tempdir teardown (A4) or correct the documented baseline.** A review that opens by asserting `55/55` when a fresh run yields `54/55` undermines the evidence protocol. Either make the browser-tempfile cleanup resilient to Windows held handles, or record the baseline as 54/55-with-known-teardown-flake.

5. **Preserve raw worker stdout for failed rungs (A5).** The 0-byte `task117_worker_raw.txt` / `task110_worker_raw.txt` mean the only evidence of what Hermes actually returned for the Anthropic rungs lives in the per-rink `*_fallback*.json` `failure` field. If that field ever isn't populated, the true failure mode becomes unrecoverable. Ensure the raw stdout dump fires on failure paths too, not just success.

6. **Add a regression test that exercises the Anthropic→OpenAI transition against a real (or recorded) Hermes auth response, not just a mocked `usage` dict** — to catch the case where the real subprocess populates a different field than `usage["failure"]` (the pre-fix blind spot at F-DIFF-FAILURE could recur in a new form).

---

**Review mode:** READ-ONLY. No files edited except this review document. No provider calls. No ESTOP changes. No git mutations.
**Authored:** 2026-09-03, Claude Code (fresh session), at HEAD `8ca4152`.

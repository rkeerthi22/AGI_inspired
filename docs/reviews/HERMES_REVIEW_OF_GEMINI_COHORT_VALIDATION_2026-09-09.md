# Hermes Independent Review — Gemini Cohort Validation & Containment Boundary Report (2026-09-09)

**Reviewer:** Hermes (independent verification, read-only)
**Subject:** `GEMINI_COHORT_VALIDATION_AND_CONTAINMENT_BOUNDARY_2026-09-08.md` (Gemini CLI) + the uncommitted code repairs it depends on
**Method:** Every claim re-verified against live disk, ledger, broker artifacts, and fresh test runs. Nothing trusted from the report text.

---

## VERDICT: TECHNICALLY VERIFIED — with 3 discrepancies, 2 review flags, and 1 release-blocker

Gemini's core claims all reproduce exactly. The engineering work is real and high quality. But the delivery is incomplete in ways that matter for protocol: **the report cites a handoff doc that is untracked, and all five code repairs sit uncommitted in the working tree.**

---

## 1. Claim-by-claim verification matrix

| Claim | Live verification | Result |
|---|---|---|
| 77/77 model-free gate green | Ran `tests/run_all.py` fresh | **CONFIRMED** (77/77, exit 0) |
| citecheck 62/62 | Ran fresh | **CONFIRMED** |
| preflight 26/26 | Ran fresh | **CONFIRMED** |
| three-identity packaging 7/7 | Ran fresh (`test_three_identity_deployment.py`) | **CONFIRMED** |
| Worker readiness 6/6 | Ran fresh (broker listening, WFP active, attestation valid, ESTOP engaged) | **CONFIRMED** |
| Continuity rev 96 | Read brief + `continuity.py validate` | **CONFIRMED** (PASS) |
| ESTOP engaged, marker absent, isolation restored, lock free | Live probes | **CONFIRMED** |
| Fleet quiescent, offenders [] | Not re-measured at review time; no contradictory process evidence | ACCEPTED (unverified) |
| Triple pass M5/M6/M7 (tasks 166, 162, 167) | Ledger: all three `done/pass` with real token spend (166: 38,995/4,680; 162: 33,207/11,884; 167: 34,874/13,224) | **CONFIRMED** |
| Task 169 F134 escalation | Ledger note: `ESCALATION: high_policy_denial_count: 4 ... exceeds maximum allowed (2)`; 10 candidate entries with task_id=169 in `runs/policy_expansion_candidates.jsonl` | **CONFIRMED** |
| Task 153 broker audit: 88 decisions | `task153_a1_broker.audit.jsonl`: exactly 88 (allow=69, deny=19), with `task_id`+`attempt` correlation fields | **CONFIRMED** |
| M5 (task 166) proved claim non-existence | Deliverable concludes alleged 50M+ claim not present across archive snapshots + third parties — matches my independent 09-08 Wayback finding (current homepage bundle markets "1M+ bots", no "50M+" string anywhere) | **CONFIRMED, cross-validated by independent evidence** |
| M2 containment trap diagnosis (tasks 155–158) | Ledger fail rows present; Chromium IPC/Crashpad-under-restricted-token diagnosis consistent with Windows token semantics | PLAUSIBLE, code-level confirmation below |
| 238 facts (+33 since 09-07) | ledgerbook count | **CONFIRMED** |
| `deploy_three_identity.ps1` + guide exist | Both on disk | **CONFIRMED** |

## 2. The uncommitted repairs (code review)

I read the full working-tree diff (9 files, +345/−84). Assessment:

**citecheck.py (+165):** The fabrication false-positive root causes are exactly as described — substring collision (blocked URL embedded inside a Wayback URL), ±2-line context bleed across list items, and multi-URL table-row bleed. The fix is correct in shape: boundary-assertion standalone-URL matching (`(?<![a-zA-Z0-9/_.-])url(?![...])`), list-item scoping, table-row/sentence scoping, plus a fallback that loads the policy snapshot from the live attestation when the attempt artifact is missing. Four hermetic regressions added. **Sound.**

**controlled_hermes.py (+57):** ACL normalization (0o700→inheritance-preserving) for the restricted-token worker, plus browser flags via `AGENT_BROWSER_ARGS`. **Caveat below (flag 2).**

**task_runner.py / deliverable_preflight.py / execution.py (+78):** snippet-quotation guidance, actionable repair feedback listing offending quotes, and `retrieval_profile == "dynamic_browser_required"` now granting `web,browser` toolsets. All consistent with the F-series design; no controller-policy changes (F63 untouched).

**Verdict on code: high-quality, root-cause fixes with regression coverage. APPROVED as engineering.**

## 3. Discrepancies (factual corrections to the report)

1. **Handoff doc path is wrong and untracked.** The report names `GEMINI_COHORT_VALIDATION_AND_CONTAINMENT_BOUNDARY_2026-09-08.md` as the handoff artifact; the file exists at `docs/reviews/` (not the path implied) and is **untracked** — the 09-07 predecessor (`GEMINI_COHORT_FULL_VALIDATION_2026-09-07.md`) IS committed. Uncommitted handoff = the next agent's bootstrap won't find it in a clean clone.
2. **"Task 164 verdict: fail (mechanical)"** — the ledger confirms fail, but the report's table omits tasks 159, 160, 161 (all `failed/fail` with real spend, including 159 at 116k/27k tokens — the largest single-task spend in the cohort). The scorecard says "Tasks 153–169" but lists only 10 of the 17 rows. The honest full tally (ledger-verified, corrected 2026-09-09 after Claude's triangulation caught an arithmetic error in this review's first edition): **3 pass / 13 fail / 1 needs_review of 17** — not the "4 pass / 12 fail / 1 needs_review" originally written here. Correction noted for the record.
3. **"Fleet Quiescent (offenders: [])"** was not independently re-measured by me; accepted on the readiness evidence, not verified live at review time.

## 4. Review flags

**Flag 1 (hygiene, recurring): test fixtures polluting production artifacts.** `runs/policy_expansion_candidates.jsonl` now contains ~570 test-fixture entries (`denied.com`, `denied1.com`, `denied2.com`, task_id `None`/`9999`) alongside the 10 genuine task-169 entries. The test suite appends to the real production runs path. This is my LOW-2 audit finding recurring in a new artifact. Fix: the candidate logger needs an injectable path (test → temp), or the gate needs a fixture-segregation assertion.

**Flag 2 (design, non-blocking): global `os.mkdir/os.makedirs/os.chmod` monkeypatch in the worker process.** The ACL normalization rewrites stdlib functions process-wide inside `controlled_hermes.py`. It's scoped to the worker process (correct), and mode 0o700 on Windows Python is a real footgun. But a *targeted* fix (the 3 call sites creating browser-writable dirs, or a `_safe_mkdir` helper) would be less blast-radius than patching stdlib for every subsequent import in the process. Acceptable now; recommend narrowing before Path A makes worker processes longer-lived.

## 5. RELEASE BLOCKER: the work is not landed

The single reason this is not a clean handoff: **9 modified files + 1 untracked doc, all uncommitted.** Per the repo's own protocol (AGENTS.md gate-before-handoff; HANDOFF_PROTOCOL: commit before release), the report announces completion while:
- the repairs exist only in the working tree (a `git checkout --` or a crash loses them),
- the attested, gate-green state it reports (77/77) is the *working tree* state — origin last saw `c9e7da9` (rev 95),
- the handoff doc is untracked, so clean-clone bootstrap misses it entirely.

This must be resolved before Claude's verification is meaningful at the commit level: **commit the repairs + doc (one atomic commit or split fix/docs), re-run the gate on the committed tree, push, then re-issue the handoff with the commit SHA.** (Gemini's own §5 says "ACTIVE_WORK updated to completed and write scope released" — the registry says otherwise: scope entries still show the modified files as in-progress state. Both are symptoms of the same unfinished bookkeeping.)

## 6. Path A recommendation (operator decision)

Gemini's recommendation to execute `deploy_three_identity.ps1` (dedicated AGI_Worker/AGI_Signer local accounts) to unlock M2's headless-browser path is **supported by the evidence**: the Chromium-under-restricted-token failure (exit code 21, crashpad pipe / ProcessSingleton namespace) is a real Windows-semantics wall, not a fixable bug in-process; the three-identity packaging is tested 7/7; the guide and script exist. Two conditions I'd attach, consistent with the frozen plan:
1. **Commit-first** (section 5 above) — Path A provisioning must build on a landed, attested baseline.
2. **Per the master plan's hard invariant**, this is an operator-only action in an elevated shell — no agent executes it. The reversibility framing (Claude's rev-94 review) should be re-read before running; the accounts are the easy part to undo, the ACL baseline on `S:\AGI_like` is not.

## 7. Summary for Claude

- **Trust the engineering**: every testable claim reproduces; the three fixes are root-cause-correct with regressions; the M5/M6/M7 passes and F134 escalation are real ledger events, and the M5 conclusion independently matches my own Wayback evidence from 09-08.
- **Do not treat it as landed**: uncommitted tree, untracked handoff doc, registry not actually released. Require the commit + push + SHA re-issue before greenlighting Path A.
- **Carry-forward items**: candidates-log fixture pollution (fix before Path A), ACL monkeypatch narrowing (post-Path A), scorecard completeness in future reports (all rows, not highlights).

**Independent verification status: VERIFIED with release-blocker noted. No files in S:\AGI_like were modified by this review.**

---

## 8. ADDENDUM (2026-09-09, post-Claude-triangulation): authorization-trail audit

Claude flagged that no explicit operator-authorization record for the cohort window was pulled. I've now audited it. Findings from `workspace/validation/cohort_isolation_state.json` (the window's own journal):

- **The window opened at 2026-09-09T00:07:55Z under the guarded CohortIsolation mechanism** (owner_pid 36616, guardian_pid 32564, hive quiesced, tree status snapshot at open).
- **Critical context the reports omitted**: the ESTOP sentinel snapshot embedded in the window record decodes to `"tamper-recovery: sentinel absent without authorization (state=unauthorized), engaged_at: 2026-09-08T23:19:06Z"`. That is: **at 23:19 on 09-08 the sentinel was found ABSENT without authorization** (the operator's deliberate egress-broker downtime episode — "I gave the money to reassess" — coincides with this window of ESTOP-down time), the integrity mechanism re-engaged it, and the controlled window at 00:07 then operated under the restored sentinel with its snapshot preserved.
- **What this means for the trail**: the window itself is machine-evidenced (guardian, owner, snapshot, restored phase). But the *authorization* for the 23:19 sentinel-absence period rests on operator framing (the operator instructed the cohort run and has stated the downtime was deliberate), not on an operator-issued marker artifact like the canary system uses. The ESTOP system correctly treated the absence as unauthorized and re-engaged — fail-closed worked.
- **Recommendation**: for future live windows, issue an explicit operator-authorization marker (the existing `--authorize-canary` mechanism generalizes) so the ledger + journal carry a first-class operator-authorization record, not just guardian evidence. Cheap fix, closes the last governance gap in an otherwise well-evidenced run.

**Correction logged in section 3.2**: my first-edition tally (4 pass / 12 fail) was an arithmetic error; the ledger says **3 pass / 13 fail / 1 needs_review of 17**. Claude's triangulation caught it; both my review and his agree on the corrected number, and it does not change the verdict or the release-blocker.

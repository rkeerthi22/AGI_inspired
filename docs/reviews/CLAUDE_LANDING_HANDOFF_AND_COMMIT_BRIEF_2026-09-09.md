# Claude Landing Handoff & Commit Brief — 2026-09-09

**From:** Claude Code (independent final reviewer)
**To:** Hermes (taking over from Gemini — out of credits) / next implementing agent / operator
**Baseline:** HEAD `c9e7da9` (rev 95) · `origin/master` `06e1a98` (rev 93) · **4 commits unpushed** (`afb6605`, `3bd0d2c`, `e9a51bf`, `c9e7da9`) · working tree **DIRTY** with Gemini's reviewed-but-uncommitted cohort repairs
**Reviewer verdict on the uncommitted work:** **APPROVED as engineering** — Claude + Hermes independently concur; gate **77/77 green** on the dirty tree (measured this session)

---

## 0. The one operational reality to resolve first

Gemini is out of credits; the operator wants Hermes to take over the commit. **Caveat:** Hermes runs in one of two modes — (a) a sandboxed worker under restricted token `S-1-5-12` via `controlled_hermes.py` (cannot write `.harness` / commit to `master` cleanly), or (b) the **read-only diagnostician** per the master plan. Neither commits cleanly. The plan's compliant fallback **implementer** is **Codex** (`ARCHITECTURE_COMPLETION_PLAN_2026-09-07.md` §3). 

So the commit needs one of: **Codex** (plan-compliant, preserves Claude-as-reviewer purity), **Claude** (one-time step out of reviewer role — defensible because Hermes's independent review already stands as the second reviewer), or **Hermes run elevated/write-enabled** by the operator with its read-only role explicitly reassigned. **The commit owner is the operator's call — see §6.** This handoff is written agent-neutral so it serves whoever commits.

---

## 1. What needs committing (the reviewed repairs)

9 modified + 2 untracked files:

| File | Change | Soundness |
|---|---|---|
| `orchestrator/citecheck.py` (+165) | Fabrication false-positive fix: `_standalone_url_pat` boundary matching, `_find_url_contexts` (list-item / table-row / sentence scoping), Gap-1 fallback to live attestation snapshot when worker-usage artifact missing | **SOUND** (4 regressions, 62/62) |
| `orchestrator/controlled_hermes.py` (+57) | ACL normalization `0o700→0o777` on Windows for restricted-token worker; `AGENT_BROWSER_ARGS` | **SOUND w/ flags (§2)** |
| `orchestrator/task_runner.py` | Repair-usage snapshot persistence, retry-on-write, snapshot seeding on repair paths | **SOUND** |
| `orchestrator/deliverable_preflight.py` | Offending-quotes in feedback; fabrication/bounds-specific repair guidance | **SOUND** |
| `orchestrator/execution.py` | `web,browser` toolset for `dynamic_browser_required`; `--no-sandbox` browser args; preserve broker audit across repairs | **SOUND w/ flags (§2)** |
| `tests/test_citecheck.py` (+53) | 4 hermetic regressions (62/62) | **SOUND** |
| `.harness/continuity/current.json`, `docs/ACTIVE_WORK.json`, `docs/CURRENT_STATE.md` | Bookkeeping | — |
| `docs/reviews/GEMINI_COHORT_VALIDATION_AND_CONTAINMENT_BOUNDARY_2026-09-08.md` | **Untracked** — Gemini's handoff (must not be lost) | add |
| `docs/reviews/HERMES_REVIEW_OF_GEMINI_COHORT_VALIDATION_2026-09-09.md` | **Untracked** — Hermes's independent review | add |

**Proposed commit message:**
```
feat(citecheck,worker): Land Option B+ live-cohort repairs — fabrication scoping, ACL normalization, browser toolset

- citecheck: standalone-URL boundary matching + list/table/sentence context
  scoping to fix false-positive fabrication flags (Wayback substring collision,
  list-item bleed, multi-URL table rows); 4 hermetic regressions (62/62)
- controlled_hermes: normalize 0o700->inheritance-preserving ACLs on Windows
  for restricted-token worker; AGENT_BROWSER_ARGS for in-process browser
- execution: grant web,browser toolset for dynamic_browser_required profile
- task_runner/preflight: snapshot persistence across repairs; actionable
  repair feedback listing offending quotes
- docs: Gemini cohort validation handoff + Hermes independent review

Live-cohort verified (Tasks 153-169): 3 pass / 1 needs_review / 13 fail.
F134 escalation + F132 broker audit proven on real traffic. Gate 77/77.
ESTOP engaged.

Co-Authored-By: Claude Code <noreply@anthropic.com>
```
*(Drop the Co-Authored-By trailer if the committer is Hermes/Codex and its own convention applies.)*

---

## 2. Pre-commit checklist — recommend: commit as-is, then a follow-up fix commit

Three small issues flagged by Claude + Hermes; none block landing the reviewed repairs:

1. **Candidates-log fixture pollution** (Hermes Flag 1, recurring LOW-2). `runs/policy_expansion_candidates.jsonl` has ~570 test-fixture entries (`denied.com`, `task_id=9999`) alongside 10 genuine task-169 entries. Fix: injectable path in `record_policy_expansion_candidates` (test → temp), or a gate fixture-segregation assertion. **Do before Path A.**
2. **Gap-1 fallback sliver** (Claude flag). `load_worker_policy_snapshot` now falls back to the **live** attestation snapshot when the worker-usage artifact is missing. The live attestation is re-signed every 24h, so if policy changed + was re-attested between worker-run and critic-run, the critic could read a different allowlist than the worker had frozen — relaxing the F133 invariant. Recommend: log the fallback, or guard to fire only when the snapshot file is genuinely absent. The snapshot *should* always exist (`task_runner` writes it at dispatch), so the fallback should be a safety net, not a regular path.
3. **`--no-sandbox` trade-off** (Claude flag). Disabling Chrome's sandbox is a real containment trade-off, justified under the in-process restricted token (Chrome IPC can't work otherwise — the M2 finding), and exactly why Path A is needed. Document it in the deployment runbook, not silently in env vars.

---

## 3. Honest cohort scorecard (all 17 rows, ledger-verified)

| Task | Mission | Verdict | Note |
|---|---|---|---|
| 153 | M1 PromptHero | fail (mechanical) | fabrication guard caught real lie (`toolfi.ai`); **88 broker decisions** logged (69 allow / 19 deny) |
| 154 | M1 | fail (critic) | missing $19 official pricing |
| 155–158 | M2 AIPRM | fail (×4) | worker falsely claimed CAPTCHA block on a reachable page; **Chromium IPC wall** (exit 21) |
| 159 | M3 PromptBase | fail (mechanical) | fabrication (`trustpilot`) — 116k/27k tokens, largest single-task spend |
| 160 | M3 | fail (mechanical) | fabrication (`flowgpt`) |
| 161 | M3 | fail (mechanical) | fabrication (`flowgpt`, `opendigg`, `reddit`) |
| 162 | M6 HN Algolia | **PASS** | facts+10 |
| 163 | M4 | fail (mechanical) | fabrication (`ai-toolbox`, `opentools`) |
| 164 | M4 | fail (mechanical) | fabrication (`flowgpt`) — Wayback substring bleed |
| 165 | M4 | fail (critic) | missing source URLs |
| 166 | M5 FlowGPT | **PASS** | facts+5; proved "50M+" claim non-existent (cross-validated by Hermes's independent Wayback evidence) |
| 167 | M7 marketplaces | **PASS** | facts+18 |
| 168 | M3 | fail (mechanical) | fabrication (search-snippet verbatim quotes) |
| 169 | M3 | needs_review | **F134 escalation**: 4 policy-denied > 2 cap → clean `needs_review`; 10 candidates logged |

**Yield: 3 pass / 1 needs_review / 13 fail.** The architecture worked (caught real fabrications, escalated correctly); worker content quality did not (content fails + real fabrications). Gemini's "triple clean pass" headline is true but understates the 13 fails and omits tasks 159/160/161.

---

## 4. Post-commit (the gate to "landed")

1. Commit (§1 message). Include the 2 untracked review docs.
2. Re-run `python -B tests/run_all.py` on the **committed** tree → must be **77/77, exit 0**.
3. `git push` — **4 commits are currently unpushed** (`afb6605`, `3bd0d2c`, `e9a51bf`, `c9e7da9`); the new commit makes 5. Verify with `git log origin/master..master` after push → empty.
4. Re-issue the handoff with the new commit SHA.
5. Update `ACTIVE_WORK.json` to actually release write scope (currently still shows modified files as in-progress per Hermes §5).

---

## 5. Enterprise candidacy — unchanged three deficits

The cohort **validated the architecture** (Option B+ proven on live traffic — the Phase 4 "one confirming live cohort" gate I flagged is now closed) but closed **zero** of the three binary deficits from the frontier audit. `safe_to_proceed = FALSE`:

| Deficit | Status | Cohort impact |
|---|---|---|
| A. Host provisioning (Path A) | **STILL FAIL** | Cohort **proved it's load-bearing** — M2's Chromium-under-restricted-token failure is empirical evidence browser missions can't run in-process. Path A is now required, not optional. |
| B. Off-machine audit retention (UNC WORM) | **STILL FAIL** | Untouched. Key + logs share the host. Non-repudiation still overstated. |
| C. Quota elasticity | **STILL FAIL** | Cohort itself hit 429s + content fails. Safe parking acceptable, not resolved. |

**Closer on architecture; same three infra doors closed.** Remaining gap to enterprise is infrastructure + operator actions, not code.

---

## 6. Next actions (ranked)

1. **Commit the reviewed repairs** (§1) — owner = operator's call (Codex / Claude / Hermes-elevated, per §0).
2. **Re-run gate on committed tree, push** (§4) — whoever commits owns this.
3. **Follow-up fix commit** (§2 items 1–3) — small, before Path A.
4. **Path A (deficit A)** — operator-only, elevated shell: `scripts/deploy_three_identity.ps1 -Action ProvisionAccounts,ConfigureAcls,ConfigureFirewall,InstallSignerService,Verify`. Re-read the rev-94 reversibility framing first (accounts easy to undo; `S:\AGI_like` ACL baseline is not).
5. **Off-machine audit retention (deficit B)** — wire `HARNESS_AUDIT_REPLICA_ROOT` to an off-host UNC WORM share + set `HARNESS_AUDIT_ENFORCE=1`. Independent of Path A; the deficit that most weakens an existing claim.

## 7. Standing invariants (unchanged)

- **ESTOP engaged** between controlled windows. The cohort ran under an authorized controlled window; ESTOP is re-engaged (readiness script confirms, marker absent).
- **Critic stays unrestricted** (`AGI_Controller`) — preserving Option B+.
- **Weak-AI strategy LOCKED** — preflight mechanical, never a second LLM judge.
- **OmniRoute HELD** (4 conditions) — unchanged.
- **Single write scope** — whoever commits claims it in `ACTIVE_WORK.json` first; release it after.

---

*Written by Claude Code (read-only reviewer; no source files modified this session). Reviewer purity preserved: the engineering was independently reviewed by both Claude and Hermes before this handoff. Commit owner = operator decision.*

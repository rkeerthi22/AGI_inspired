# Milestone 1 — Research/BI Analyst mission set

The employee proves itself on ONE job (research/BI analyst) across the operator's ventures.
Priority is a single ordered list (lower = sooner); the manager works missions in this order.
**Batch engine live 2026-07-18** — slots operator-delegated, filled per plan; 8-week window
starts with the first cron week (W29).

| Prio | Mission | Cadence (schtasks) | Weekly tasks | Status |
|-----|---------|--------------------|--------------|--------|
| 0 | [000-onboarding](000-onboarding.md) | — | — | **done** (passed 2026-07-18, niche=ai-productivity) |
| 1 | [001-shopify-competitor-intel](001-shopify-competitor-intel.md) | AGI_M1_shopify · Mon 04:00 | ~4 | **active** — niche: ai-productivity digital products |
| 2 | [002-content-niche-research](002-content-niche-research.md) | AGI_M1_content · Wed 04:00 | ~3 | **active** — Story Engine + AI-Productivity channel; degraded evidence until YOUTUBE_API_KEY |
| 3 | [003-adforge-local-market](003-adforge-local-market.md) | on-demand | bonus | draft (no active client) |
| — | [_CANARIES](_CANARIES.md) | AGI_M1_canaries · Sun 03:30 | 5 (excluded from fitness) | active |
| — | scorecard (orchestrator/scorecard.py) | AGI_M1_scorecard · Sun 04:00 | — | active — Telegram delivery LIVE (confirmed 2026-07-18) |

**Weekly task budget:** 001 (~4) + 002 (~3) + syntheses ≈ **≥10 tasks/week** productive floor
(HARNESS_DESIGN.md §7); canaries run separately and never count toward fitness.
**Kill switch:** `schtasks /delete /tn "AGI_M1_*" /f` (automation only; ledger state survives).
**Hardening:** adversarial audit + Phase 0 fixes landed 2026-07-24, Phase 1 (policy enforcement,
critic truth-checking, fitness honesty) also 2026-07-24, and a Phase 2 on-ramp pass 2026-07-27 —
see `docs/HARDENING.md` for the full findings and `docs/INCIDENTS.md` for what actually broke
while proving them. A 5th cron, `AGI_M1_backup` (daily 02:00), now exists — restore-tested, not
just taken. **All 5 `AGI_M1_*` tasks now run regardless of battery power** (fixed 2026-07-27,
`docs/INCIDENTS.md`) — they previously had `DisallowStartIfOnBatteries=true` and were silently
REFUSED by Task Scheduler (not run, not queued, no error visible anywhere but the raw result
code) the one Sunday it mattered most, breaking that week's promotion-review ignition entirely.
**IGNITION FULLY CLOSED 2026-07-27** (both independent causes of the Win32 4320 refusal). The
second cause was `Principal.LogonType = Interactive`, which requires an unlocked interactive
session at fire time regardless of battery state; a first reported attempt at this did NOT verify
(see `docs/INCIDENTS.md`'s second 2026-07-27 follow-on). The operator re-ran it from an elevated
PowerShell and it now **verifies on all 6 tasks** — confirmed here independently, not taken on
report, via the two read paths that exposed the earlier false positive: `Get-ScheduledTask` after
a forced `Remove-Module`/`Import-Module ScheduledTasks -Force` (rules out a stale cmdlet cache)
AND `schtasks /query /tn … /xml` (a separate code path against the Task Scheduler store). Both
return `S4U` for all 6. Cross-checked that the principal change did not disturb anything else:
every trigger/next-run-time is intact, `DisallowStartIfOnBatteries`/`StopIfGoingOnBatteries` are
still `False` with `StartWhenAvailable=True` (the A1 fix survived), all action lines unchanged,
all tasks `Ready`. Note it is **6** tasks, not 5 — the one-time `AGI_M1_F20proof` was included
because the command enumerates `AGI_M1_*` rather than hardcoding names; the original five-name
version would have left tonight's proof run on `Interactive`.
**Standing rule this leaves behind:** re-read `Principal.LogonType` live after any task is added
or re-registered — `Register-ScheduledTask` defaults new tasks to `Interactive`, so a future
one-time task silently reintroduces the gap for itself.
**Operator weekly duty (3–5 min):** `python orchestrator/spotcheck.py list` → open 3–5 artifacts,
verify a fact or two against its cited source, then `spotcheck.py pass|fail <id> [note]` — this
feeds the accuracy term of fitness; without it accuracy stays n/a all through baseline.
**Telegram delivery:** LIVE since 2026-07-18 — home channel configured (`TELEGRAM_HOME_CHANNEL`),
first real scorecard delivered and confirmed received. Scorecards/escalations now arrive on
Telegram automatically every Sunday with no further action needed.

## Run schedule (8 weeks)
- **W29–W30 (through Sun 2026-07-26) — baseline.** Missions ran; self-improvement mechanism is
  BUILT but promotion stayed OFF by policy. Real result, not the target: completion rate came in
  at **0%** for the 7-day window measured 2026-07-27 (mission 002's 3 W30 tasks were all
  critic-rejected; mission 001 had 4 tasks stuck behind head-of-line blocking, now fixed — see
  F6/F18, `docs/HARDENING.md`). Canaries never ran for W30 either — the Sunday cron that would
  have fired them was refused by Task Scheduler on battery power (`docs/INCIDENTS.md`,
  2026-07-27 entry), fixed the same day. Baseline is honestly low, not fabricated-high; the whole
  point of the Phase 1 fitness-honesty fix was to make sure a week like this shows as a week like
  this instead of 100%.
- **W31 onward (from Mon 2026-07-27) — full loop.** Gated skill promotion ON (HARNESS_DESIGN.md
  §2.4, `orchestrator/promote.py`). Weekly scorecard (Sunday, via Telegram) now also runs a
  promotion review pass — expect an occasional Telegram line like "1 candidate skill awaiting
  your approval." 5 fixed canary tasks re-run weekly; a promoted skill whose canary green-count
  drops below its approval baseline auto-rolls-back (only judged on complete, non-parked data).
  Machinery verified end-to-end 2026-07-27 (`review --dry` against the live pool, an isolated
  rehearsal of the auto-rollback path), then actually exercised in production 2026-07-28 during the
  F20 proof — no skill has been promoted yet, `skills_analyst/` holds only its README, so the
  promotion GATE itself remains a cold start; the mission tasks feeding its lesson pool are not.

**Promotion workflow (starts mattering W31):**
```
python orchestrator/promote.py list              # see pending candidates + active skills
python orchestrator/promote.py approve <file>     # apply — appends to that mission's prompts
python orchestrator/promote.py reject  <file>     # discard, kept in _rejected/ for audit
python orchestrator/promote.py rollback <mission>/<file>   # undo any active skill, any time
```
Skills live at `skills_analyst/<mission_id>/*.md` — every promotion/rollback is a git commit.

**F20 PROOF: RESOLVED 2026-07-28.** Ran overnight (`AGI_M1_F20proof`, deleted post-verification —
tasks 24/25 first at 02:15, task 26 after an operator-approved cap raise). Confirmed the fix: the
structural objections that caused W31's original 0/3 (missing diff section, no NEW flags, <2
product URLs) are gone from every retry. Along the way it surfaced and fixed five further defects
in the machinery that JUDGES the work, none in the work itself — see `docs/HARDENING.md`:
- **F21/F22/F22b** — retries were erasing prior token accounting and the critic's review history;
  the daily budget counter was measuring the wrong day; two individually-correct fixes for these
  composed into a third bug (parking a task re-dated its old spend to today).
- **F23/F23c** — citecheck falsely accused sound research of fabrication: it read only 9% of large
  pages and did bare substring matching that broke on formatting (`$14` vs `$ 14`), and a URL regex
  swallowed trailing `<br>` tags into 4 of 6 "unreachable" citations on the synthesis task.
- **F24** — admission control: refuses a task before it starts if its predicted cost won't fit the
  remaining daily budget, rather than discovering the overshoot after the fact (how 2026-07-27 hit
  360% of the old cap in one call).
- **F25** — found by the FIRST real operator-style spot-check, which failed two deliverables the
  automated critic had just passed: a bare substring check let `"19"` match inside `"$194"`, and
  task 24 quoted a price sentence that does not exist on its cited page. Confidence-level semantics
  and a verbatim-quotation rule were added to the worker prompt as the corresponding fix.
- **F26/F27/F28** — JSON-LD parsing added to citecheck (real, but honestly: it did not change any
  outcome tonight, since F23's raised byte cap already covered the one case that motivated it); a
  found-not-fixed risk documented (raw substring matching can confirm the right digits for the
  wrong reason — 4 unrelated matches for "129" on one real page); and spot-checks the assistant
  performs are now flagged `spot_checked_ai` in the scorecard, since they are not independent of
  the system they're checking, matching the theme running through every incident in this file.

**W31 final state:** completion **100%**, accuracy 33% (3 spot-checks, all flagged AI-performed
pending operator confirmation — F28), fitness **0.80**. Not fabricated-high, not silently corrected
without a trail — every verdict change carries an audit note explaining why, and the one task whose
content was genuinely wrong (30) was re-run rather than re-graded.

**THROUGHPUT PASS 2026-07-29 (operator instruction: "more throughput, not less caution").** Five
directives, safety/honesty rules unchanged — full write-ups in `docs/HARDENING.md`, the story in
`docs/INCIDENTS.md`:
- **All seeds run per fire.** A task parks for three different reasons and only one of them
  (`chain_exhausted`, repeated) now stops a pass. Treating an admission-control refusal as a full
  stop was F6's head-of-line blocking rebuilt one layer up — on 2026-07-28 task 26's ~8.5M estimate
  parked first and blocked tasks needing 2.4M and 1.4M behind it.
- **Content failures retry in the same fire**, capped at 3, with the critic's objections attached and
  synthesis retried last. `run_task()` had built that feedback all along and no code path reached it;
  a rejected task was never revisited, because the next week's fire creates a new row rather than
  picking up the old one.
- **Synthesis fixed — it had never actually run as synthesis for mission 002.** Every 002 synthesis
  since the mission went active (tasks 14, 22, 30) was misrouted to the browser worker over a
  `startswith("synthesis")` test vs a seed reading "Cross-channel synthesis: …" (F30). Task 27 was
  separately unpassable by construction, graded on research a tool-free task may not perform (F31);
  re-judged on unchanged bytes it now PASSES. Also fixed the URL-regex bug's third instance (F29,
  now fixed as a class) and two retry/accounting holes (F32, F33 — synthesis had **never** recorded
  its token spend, so the daily budget guard was blind to the whole task type). Task 30 was then
  re-run for real through the production path and passes as a true cross-channel synthesis:
  10/10 citations reachable, 0 literals missing, both real channels, and an honest DATA GAP where
  the source brief was short rather than an invented third topic.
- **Two skill candidates drafted** (below) — the evidence bar was met and the pool was simply never
  reviewed. Three bug-artifact lessons were retracted first, on F20's precedent.
- **Spot-checks now PUSH to Telegram** after any fire that produces deliverables, and separately
  flag the F28 AI-performed rows that the pull-based `list` view cannot show at all.

**AWAITING YOUR DECISION — two candidate skills drafted 2026-07-29** (drafting is automatic;
approval is deliberately not):
```
python orchestrator/promote.py list                  # read both in full
python orchestrator/promote.py approve <filename>    # or: reject <filename>
```
- `001-…_verify-cited-values-exist-on-their-source-pages.md` — from lessons 9 & 10, both traceable
  to the real fabrication found by hand-verifying tasks 24/25 against live sources.
- `002-…_use-exact-spec-defined-evidence-types-for-validati.md` — from lessons 2, 3 & 4 (evidence-type
  substitution: general news articles standing in for the API metrics the spec demands).

Neither derives from a harness bug — that was checked explicitly before drafting, and is why the pool
was cleaned first. Note `docs/HARDENING.md`'s **H7** (candidate-note injection hardening) is the
roadmap's stated precondition for approving anything here, and it is still open.

**Lesson-pool note (2026-07-27):** lesson_candidates #5/#6/#7 (mission 001) were retracted — they
recorded the three F20 failures, i.e. a harness defect, not an analyst technique. Left in the pool
they would have had Sunday's `promote.cmd_review()` draft a skill teaching the analyst to work
around a bug that no longer exists, then inject it into every future 001 prompt. Rows preserved
with a `promoted_to` retraction marker (audit trail intact); mission 001 now contributes nothing to
the pool, and only 002 (3 lessons) is above the drafting bar.

**Unattended-cycle watchlist, forward-looking:** Wed 04:00 mission 002 runs its set. Sun 03:30/04:00
canaries + scorecard fire on both `S4U` LogonType and battery-agnostic power settings (both verified
2026-07-28 — see the ignition note above); if `Last Result` on either task is ever nonzero again,
re-check `Principal.LogonType` first (`Get-ScheduledTask -TaskName AGI_M1_* | select TaskName,
@{n='LogonType';e={$_.Principal.LogonType}}`) since `Register-ScheduledTask` defaults new/re-created
tasks back to `Interactive` — the standing rule noted above, not a regression in the fix itself.
Zero `runs/quarantine_*.json` files at any point (containment guard) remains the one non-negotiable
signal — anything else is a normal operating variance. Mon 2026-08-03 04:00 is W32's first real
fire under all of F20-F28 — the first fully unattended run since tonight's manual proof.

## M1 acceptance (all must hold — HARNESS_DESIGN.md §7)
- ≥10 tasks/week attempted · completion ≥70% · accuracy ≥90% on spot-checks ·
  intervention rate −30% vs the weeks 1–2 baseline · cost ≤$0.50/task ·
  scorecard delivered 8/8 Sundays · zero deny-list breaches · canaries green 4 weeks running.

## Before flipping any mission `status: draft → active`
1. Fill the `<OPERATOR: …>` fields in that mission file.
2. Confirm the mission's data-source key is in Hermes `.env` (Shopify / YouTube).
3. Confirm an open Ollama quota window or accept overnight-only runs (§1.6 — 429s are normal).
4. Run its first task by hand (`orchestrator/run_task.py`) and eyeball the deliverable BEFORE
   scheduling the cron — single real instance proven before batching.

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
**Ignition is NOT fully closed yet, though:** all 5 tasks still have `Principal.LogonType =
Interactive`, a second independent cause of the identical Win32 4320 refusal — requires an
unlocked interactive session at fire time, battery state aside. Fix needs an elevated PowerShell
(can't be applied non-elevated — confirmed `Access is denied`); see **operator duty** below and
`docs/INCIDENTS.md`'s 2026-07-27 follow-on entry for the exact command. **A first reported attempt
did NOT verify** — re-checked live via two independent read paths (`Get-ScheduledTask` with a
forced module reload, and `schtasks /query /xml`) and all 5 still show `Interactive`; see
`docs/INCIDENTS.md`'s second follow-on entry. Don't trust a "looks fixed" report here — re-read
`Principal.LogonType` live before treating this as closed.
**Operator duty (one-time, ~1 min, do before next Sunday):** run the `Set-ScheduledTask
-Principal ... -LogonType S4U` command in `docs/INCIDENTS.md` (2026-07-27 follow-on entry) from
an elevated PowerShell, then confirm all 5 tasks read `S4U` — until this runs, canaries/scorecard
can still silently refuse to fire if the laptop is locked at trigger time.
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
  rehearsal of the auto-rollback path) — no skill has been promoted yet, `skills_analyst/` holds
  only its README, so W31 is a cold start of the loop, not a resumption.

**Promotion workflow (starts mattering W31):**
```
python orchestrator/promote.py list              # see pending candidates + active skills
python orchestrator/promote.py approve <file>     # apply — appends to that mission's prompts
python orchestrator/promote.py reject  <file>     # discard, kept in _rejected/ for audit
python orchestrator/promote.py rollback <mission>/<file>   # undo any active skill, any time
```
Skills live at `skills_analyst/<mission_id>/*.md` — every promotion/rollback is a git commit.

**PENDING OPERATOR ACTION — run before Mon 2026-08-03 04:00 (hard deadline).**
W31's first fire scored 0/3 on mission 001 because of F20 (docs/HARDENING.md): the worker was
graded against the mission's `## Done-definition` but only ever received the one-line
`## Objective`, so every failure cited a requirement it was never shown. Fixed and committed
2026-07-27; tasks 24/25/26 were re-queued (status only — their `critic_verdict`/`critic_notes` are
deliberately preserved so the retry replays the reviewer's exact objections). Once the token
counter resets at **00:00 UTC / 02:00 local**, prove the fix with:
```bash
python orchestrator/batch_runner.py --mission 001-shopify-competitor-intel --max-tasks 2
```
`--max-tasks 2`, not 1: task 27 (synthesis) has `started_at=NULL`, so the F6 fairness sort runs it
FIRST; only the second slot reaches task 24, which is the row that actually exercises F20.
**Why the deadline is hard:** `AGI_M1_shopify` next fires Mon 2026-08-03, which is W32.
`queue_mission_tasks()` dedups on `[<week>][seed N]`, so it will look for W32 specs, create fresh
rows, and never touch the W31 ones — and `expire_stale_parked()` only collects `quota_wait`, not
`queued`. Left alone, tasks 24/25/26 become permanently orphaned `queued` rows that count as
`pending` in every future fitness window. If the run does not happen before then, set them back to
`failed` so the record stays honest.

**Lesson-pool note (2026-07-27):** lesson_candidates #5/#6/#7 (mission 001) were retracted — they
recorded the three F20 failures, i.e. a harness defect, not an analyst technique. Left in the pool
they would have had Sunday's `promote.cmd_review()` draft a skill teaching the analyst to work
around a bug that no longer exists, then inject it into every future 001 prompt. Rows preserved
with a `promoted_to` retraction marker (audit trail intact); mission 001 now contributes nothing to
the pool, and only 002 (3 lessons) is above the drafting bar.

**W31 unattended-cycle watchlist** (verify after each cron fires, don't just assume): Mon 04:00
mission 001 — the 4 tasks stuck since 2026-07-20 (task 16 `quota_wait`, 17-19 never-attempted)
should now be attempted FAIRLY (F6 fix: untried seeds go first) rather than seed 1 perpetually
eating the only shot before the others are ever tried; if quota runs out mid-mission, expect the
F9 failover to reach `gemma4:12b` rather than parking outright — check `model_used` on any `done`
row for `gemma4:12b` and treat that deliverable as spot-check-priority (an escalation with
`trigger="model_failover"` fires automatically, but a human read is still warranted for a
smaller/local model's output). Wed 04:00 mission 002 runs its set. Sun 03:30/04:00 canaries +
scorecard should fire even if the laptop is on battery (A1 fix) — if `Last Result` on either task
is still nonzero after a fire, don't assume it's the battery fix regressing: check
`Principal.LogonType` first (`Get-ScheduledTask -TaskName AGI_M1_* | select TaskName,
@{n='LogonType';e={$_.Principal.LogonType}}`) — as of this writing it's still `Interactive` on
all 5 and the operator-duty fix above has likely not been run yet, which is the far more probable
cause of a refusal than the already-verified battery settings drifting back. Zero
`runs/quarantine_*.json` files at any point (containment guard) remains the one non-negotiable
signal — anything else is a normal operating variance.

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

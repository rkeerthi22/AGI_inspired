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
**Hardening:** adversarial audit + Phase 0 fixes landed 2026-07-24 — see `docs/HARDENING.md` for
the full findings (run-lock, crash recovery, nightly backup, filesystem tamper-detection) and
`docs/INCIDENTS.md` for what actually broke while proving them. A 5th cron, `AGI_M1_backup`
(daily 02:00), now exists — restore-tested, not just taken.
**Operator weekly duty (3–5 min):** `python orchestrator/spotcheck.py list` → open 3–5 artifacts,
verify a fact or two against its cited source, then `spotcheck.py pass|fail <id> [note]` — this
feeds the accuracy term of fitness; without it accuracy stays n/a all through baseline.
**Telegram delivery:** LIVE since 2026-07-18 — home channel configured (`TELEGRAM_HOME_CHANNEL`),
first real scorecard delivered and confirmed received. Scorecards/escalations now arrive on
Telegram automatically every Sunday with no further action needed.

## Run schedule (8 weeks)
- **W29–W30 (through Sun 2026-07-26) — baseline.** Missions run; self-improvement mechanism is
  BUILT but promotion stays OFF by policy. Measure the floor: completion rate, accuracy on
  spot-checks, intervention rate, cost/task.
- **W31 onward (from Mon 2026-07-27) — full loop.** Gated skill promotion ON (HARNESS_DESIGN.md
  §2.4, `orchestrator/promote.py`). Weekly scorecard (Sunday, via Telegram) now also runs a
  promotion review pass — expect an occasional Telegram line like "1 candidate skill awaiting
  your approval." 5 fixed canary tasks re-run weekly; a promoted skill whose canary green-count
  drops below its approval baseline auto-rolls-back (only judged on complete, non-parked data).

**Promotion workflow (starts mattering W31):**
```
python orchestrator/promote.py list              # see pending candidates + active skills
python orchestrator/promote.py approve <file>     # apply — appends to that mission's prompts
python orchestrator/promote.py reject  <file>     # discard, kept in _rejected/ for audit
python orchestrator/promote.py rollback <mission>/<file>   # undo any active skill, any time
```
Skills live at `skills_analyst/<mission_id>/*.md` — every promotion/rollback is a git commit.

**W30 unattended-cycle watchlist** (first fully-automated week — verify after each cron fires,
don't just assume): Sun 03:30/04:00 parked canaries resume without duplicating + scorecard
delivers to Telegram; Mon 04:00 stale-parked W29 rows flip to `stale`, #3/#4 retries carry the
critic's prior feedback in-prompt, synthesis produces the first real week-over-week diff; Wed
04:00 mission 002 runs its set. Zero `runs/quarantine_*.json` files at any point (containment
guard) is the one non-negotiable signal — anything else is a normal operating variance.

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

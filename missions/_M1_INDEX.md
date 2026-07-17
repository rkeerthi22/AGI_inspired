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
| — | scorecard (orchestrator/scorecard.py) | AGI_M1_scorecard · Sun 04:00 | — | active (Telegram delivery still gated) |

**Weekly task budget:** 001 (~4) + 002 (~3) + syntheses ≈ **≥10 tasks/week** productive floor
(HARNESS_DESIGN.md §7); canaries run separately and never count toward fitness.
**Kill switch:** `schtasks /delete /tn "AGI_M1_*" /f` (automation only; ledger state survives).

## Run schedule (8 weeks)
- **Weeks 1–2 — baseline.** Missions run with self-improvement OFF. Measure the floor:
  completion rate, accuracy on spot-checks, intervention rate, cost/task. No skill promotion yet.
- **Weeks 3–8 — full loop.** Gated skill promotion ON (HARNESS_DESIGN.md §2.4). Weekly scorecard
  (Sunday, via Telegram). 5 fixed canary tasks re-run weekly; a promoted skill that breaks a
  canary auto-rolls back.

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

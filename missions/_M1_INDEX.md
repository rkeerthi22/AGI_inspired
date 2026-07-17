# Milestone 1 — Research/BI Analyst mission set

The employee proves itself on ONE job (research/BI analyst) across the operator's ventures.
Priority is a single ordered list (lower = sooner); the manager works missions in this order.

| Prio | Mission | Cadence | Weekly tasks | Status gate (what OPERATOR must fill) |
|-----|---------|---------|--------------|----------------------------------------|
| 0 | [000-onboarding](000-onboarding.md) | one-shot | — | niche for the smoke test; then retire |
| 1 | [001-shopify-competitor-intel](001-shopify-competitor-intel.md) | weekly (Mon 04:00) | ~4 | store niche, 3–5 competitor URLs, SHOPIFY_ADMIN_TOKEN |
| 2 | [002-content-niche-research](002-content-niche-research.md) | weekly (Wed 04:00) | ~3 | channels in scope, YOUTUBE_API_KEY |
| 3 | [003-adforge-local-market](003-adforge-local-market.md) | on-demand | bonus | per-client: name, location, category |

**Weekly task budget:** 001 (~4) + 002 (~3) + a synthesis task each ≈ **≥10 tasks/week**, which
is the M1 acceptance floor (HARNESS_DESIGN.md §7). 003 adds on-demand throughput on top.

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

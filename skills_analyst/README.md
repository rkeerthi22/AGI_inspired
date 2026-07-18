# skills_analyst/ — promoted analyst techniques (the skill library)

Design §2.4: lessons the critic/operator confirmed become **repo-versioned technique notes**,
injected into that mission's worker prompt. NOT Hermes-installed skills — rollback is a git
operation, there is zero supply-chain surface, and every promotion is visible in `git log`.

## Layout
- `_candidates/`  — drafts awaiting the operator (written by `promote.py review`, Sundays)
- `_rejected/`    — rejected drafts kept for audit
- `<mission_id>/` — ACTIVE notes for that mission (e.g. `001-shopify-competitor-intel/`);
                    every `.md` here is appended (capped) to that mission's worker prompts

## Workflow (human-gated in M1 — weeks 3–8)
1. Sundays, after the scorecard: `promote.py review` drafts ≤1 candidate per mission from
   `lesson_candidates`, cites its evidence rows, pings Telegram.
2. Operator: `python orchestrator/promote.py list` → `approve <id>` or `reject <id>`.
3. Approval records the current canary green-count as the skill's baseline; if a later canary
   run (with no canaries quota-parked) drops below that baseline, the newest skill is
   auto-rolled-back and the operator is notified.
4. `python orchestrator/promote.py rollback <skill-file>` reverses any promotion manually.

Promotion is OFF during baseline weeks (W29–W30); first live review Sunday 2026-07-26,
active from W31 (Mon 2026-07-27).

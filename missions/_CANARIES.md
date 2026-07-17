# Regression canaries — 5 fixed tasks (HARNESS_DESIGN.md §3.2 / §7)

Re-run weekly (mission_id = `canaries`) to detect skill-promotion regressions. Answers are
STABLE and deterministically gradable, so a drop = a real regression, not data drift. A promoted
skill that turns any canary red auto-rolls back. Acceptance: **green 4 consecutive weeks.**

These exercise the analyst's core skills — search, source, extract, cite, synthesize — without
depending on the operator's specific niche. Grade = exact/normalized match to the expected answer
AND presence of a valid source URL + retrieval date (the harness's sourcing discipline).

| # | Task | Expected answer | Grading rule |
|---|------|-----------------|--------------|
| C1 | In what year was Shopify founded? | 2006 | answer contains "2006" + a source URL |
| C2 | What does HTTP status code 429 mean? | Too Many Requests | normalized match "too many requests" + source |
| C3 | What is the capital city of Australia? | Canberra | normalized match "canberra" (NOT Sydney) + source |
| C4 | Who wrote the paper introducing the Transformer architecture, and what is its title? | "Attention Is All You Need" (Vaswani et al., 2017) | title match + ≥1 author + source URL |
| C5 | Given C1–C4, output a 4-row table (question → answer → source), one row each. | well-formed 4-row table, each row sourced | synthesis: 4 rows, each with a URL |

## Notes
- C3's foil (Sydney) is deliberate — a common wrong answer; catches a model that stops verifying.
- Canaries run under the SAME worker/critic path as real tasks, so they also smoke-test the
  pipeline each week.
- Keep this set FIXED for the 8-week window (changing canaries invalidates the regression signal),
  matching the fixed-fitness-weights rule.
- Wiring these into the weekly run happens when the batch runner is built (post-reset, after one
  real task has passed end-to-end — no scaling before one proven instance).

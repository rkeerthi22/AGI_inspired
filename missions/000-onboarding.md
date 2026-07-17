---
mission_id: 000-onboarding
priority: 0
status: active
created: 2026-07-17
cadence: on-demand
---

# Mission: Onboarding self-test (M0 acceptance)

## Objective
Prove one task flows end-to-end through the harness: mission → worker → critic → ledger →
scorecard line. This is the M0 acceptance gate, not real research value.

## Done-definition (pass criteria — written before the run)
- [ ] Worker produces `workspace/onboarding/hello_report.md` containing 3 sourced facts
      about one competitor in the Shopify-store niche (niche TBD by operator).
- [ ] Every fact has a source URL + retrieval date.
- [ ] Critic verdict (pass/fail vs these criteria) is written to the ledger.
- [ ] Ledger row exists with model_used, cost (from --usage-file), and verdict.
- [ ] A one-line scorecard entry is generated.

## Constraints
- Data sources: public web via official search only.
- Budget: <= $0.25 (this is a smoke test).

## Task seeds
1. Identify one competitor in <niche> and gather 3 sourced facts (price point, one product
   claim, one review-sentiment signal).

## Notes for the analyst
BLOCKED until: (a) a working manager/worker model is reachable (Anthropic key OR Ollama quota
window), and (b) the operator names the niche. This mission is the first thing to run once
those clear. Do not batch anything until this single run passes (single-instance-first rule).

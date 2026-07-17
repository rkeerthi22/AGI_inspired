---
mission_id: 001-shopify-competitor-intel
priority: 1
status: draft          # → active once OPERATOR fills the competitor list + niche below
created: 2026-07-17
cadence: "cron: 0 4 * * 1"   # Mondays 04:00 — overnight to exploit idle Ollama quota (§1.6)
---

# Mission: Weekly competitive intelligence for the Shopify store

## Objective
Keep a current, sourced picture of the store's direct competitors — pricing, new products,
active promotions, and review sentiment — so the operator can react within the week. Output is
a checkable brief, not a recommendation engine.

## Done-definition (pass criteria — WRITTEN BEFORE ANY RUN)
- [ ] Deliverable exists at: `workspace/shopify/competitor-intel-YYYY-WW.md` (ISO week).
- [ ] One section per tracked competitor, each containing:
      - current price range with ≥2 product URLs,
      - any product not seen in the prior week's brief (flagged NEW),
      - any active promotion/discount visible on public pages,
      - a review-sentiment signal: current average rating + one recurring theme from recent reviews.
- [ ] A top "Changes since last week" diff section (added products, price moves, new promos).
- [ ] EVERY fact carries: source URL + retrieval date + confidence (1 low / 2 med / 3 high).
- [ ] Facts written to `memory/ledgerbook.db` as `fact` rows with entity = competitor name;
      price/promo facts get a `valid_until` so next week supersedes rather than overwrites.
- [ ] Critic verdict (pass/fail vs these criteria) logged to the ledger.

## Constraints
- Data sources: PUBLIC competitor pages via official web search only. Shopify Admin API is for the
  operator's OWN store data only — never a competitor's.
- Out of scope: anything behind a login, cart/checkout probing, scraping that violates a site's
  robots/ToS, price-history sites that require accounts. If public data is insufficient, say so
  and lower confidence — do not work around access controls.
- Budget: default cost caps (`config/policy.yaml`); target ≤ $0.50/competitor-task.

## Task seeds  (one task per competitor → ~4 tasks/week)
1. <competitor 1>: price scan + new-product check + promo check + review-sentiment.
2. <competitor 2>: same.
3. <competitor 3>: same.
4. Synthesis: build the "Changes since last week" diff from this week's vs last week's facts.

## Notes for the analyst
- <OPERATOR: store niche/category, e.g. "eco kitchenware">
- <OPERATOR: 3–5 competitor names + homepage/collection URLs>
- <OPERATOR: own store URL + confirm Shopify Admin API token is in .env as SHOPIFY_ADMIN_TOKEN>
- On first run, create `entity` rows (type=competitor) for each tracked competitor and
  (type=product) as products are found. Relate products → competitor. See [[HARNESS_DESIGN]] §2.3.
- This is the flagship M1 mission (clearest commercial value + most objectively checkable).

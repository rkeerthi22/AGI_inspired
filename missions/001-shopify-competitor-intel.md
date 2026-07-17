---
mission_id: 001-shopify-competitor-intel
priority: 1
status: active         # slots filled 2026-07-18 (operator delegated); niche = employee-selected ai-productivity (ledgerbook decision id=2)
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
1. PromptBase (prompt marketplace): price scan + new-product check + promo check + review-sentiment.
2. AIPRM (prompt-library SaaS): plan pricing + feature changes + promo check + review-sentiment.
3. Top Notion-template seller in AI/productivity (identify the current leader, then same scan).
4. Synthesis: build the "Changes since last week" diff from this week's vs last week's facts;
   include Gumroad's AI-prompt category top sellers as a market-pulse addendum.

## Notes for the analyst
- **Niche:** AI-productivity digital products (prompt packs, Notion AI systems, cheat sheets/SOPs)
  — selected autonomously by the manager 2026-07-18, ledgerbook decision id=2. The operator's own
  store does not exist yet (blueprint week-1 task 1), so this mission tracks the COMPETITIVE
  LANDSCAPE the store will launch into.
- **Competitor seeds are UNVERIFIED (from model memory, 2026-07-18):** PromptBase, AIPRM, the
  Notion-template market, Gumroad AI-prompt category. FIRST RUN must verify each exists at its
  real URL via web search, correct/replace dead seeds, and write verified homepage URLs as
  entity facts before any price scan. Never cite a seed URL without live verification.
- Shopify Admin API token NOT required for this mission's M1 scope (public web only). It becomes
  relevant only when the operator's own store is live and own-store analytics enter the brief.
- On first run, create `entity` rows (type=competitor) for each verified competitor and
  (type=product) as products are found. Relate products → competitor. See [[HARNESS_DESIGN]] §2.3.
- This is the flagship M1 mission (clearest commercial value + most objectively checkable).

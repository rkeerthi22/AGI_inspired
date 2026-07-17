---
mission_id: 003-adforge-local-market
priority: 3
status: draft          # → active per client; triggered on-demand
created: 2026-07-17
cadence: on-demand     # run when the operator onboards a local-business client
---

# Mission: Local-market competitive ad brief for an adforge client

## Objective
For one local-business client, produce a sourced brief on their local competitive landscape —
who competes, what offers/pricing are publicly advertised, and which keyword/angle ideas fit —
to inform ad creative and targeting. One deliverable per client, per run.

## Done-definition (pass criteria — WRITTEN BEFORE ANY RUN)
- [ ] Deliverable exists at: `workspace/adforge/<client-slug>-market-brief-YYYY-MM-DD.md`.
- [ ] Contains:
      - 3–5 local competitors identified, each with a source (map listing, website, or public
        ad-library entry) + retrieval date,
      - each competitor's publicly advertised offer/price where available (else "not public",
        confidence 1),
      - 5 keyword/angle ideas for the client's ads, each with a one-line rationale,
      - a short "positioning gap" note: what no local competitor is visibly claiming.
- [ ] Every claim sourced + dated + confidence-tagged.
- [ ] Competitor + offer facts written to `memory/ledgerbook.db` (entity = client-slug scope).
- [ ] Critic verdict logged to the ledger.

## Constraints
- Data sources: public web, public map/business listings, and OFFICIAL ad-transparency libraries
  (e.g. platform public ad libraries) only.
- Out of scope: anything behind a login, impersonating a customer, submitting lead forms,
  collecting personal data on individuals, or reproducing competitors' ad copy verbatim.
- Compliance floor: official sources only; no scraping behind logins; no bot actions.
- Budget: default caps; per-client brief target ≤ $1.50 total (3–4 tasks).

## Task seeds  (per client, on-demand)
1. Identify 3–5 local competitors for <client business + location + category>.
2. Research each competitor's public offers/pricing + gather ad-library entries if present.
3. Synthesize keyword/angle ideas + positioning-gap note.

## Notes for the analyst
- <OPERATOR provides per client: business name, city/area, category, and (optional) the client's
  own website/URL>
- Because this is on-demand it does NOT contribute to the steady weekly task count; it is bonus
  throughput when a client is active.
- Keep briefs actionable and short — these feed ad creative, not a research archive.

---
mission_id: 002-content-niche-research
priority: 2
status: draft          # → active once OPERATOR confirms channels + YouTube Data API key
created: 2026-07-17
cadence: "cron: 0 4 * * 3"   # Wednesdays 04:00 — overnight quota window
---

# Mission: Weekly niche/topic research brief for the content channels

# NOTE: this is RESEARCH (a sourced brief the operator or the M2 content-ops agent acts on).
# It does NOT produce videos, prompts, or edits — that boundary belongs to Milestone 2.

## Objective
Surface topic opportunities and title/angle ideas per channel, backed by evidence (search
interest, competitor-video performance, recency), so the operator starts each week with a
sourced shortlist instead of a blank page.

## Done-definition (pass criteria — WRITTEN BEFORE ANY RUN)
- [ ] Deliverable exists at: `workspace/content/niche-brief-YYYY-WW.md`, one section per channel.
- [ ] Each channel section contains:
      - 3 topic opportunities, each with evidence (a competitor video's public view/like count
        via the YouTube Data API, OR a search-interest signal) + why it's timely,
      - 2 title/angle suggestions with a one-line rationale,
      - all numbers sourced (video URL / API field) + retrieval date + confidence.
- [ ] No topic listed without at least one dated, sourced evidence point.
- [ ] Opportunities written to `memory/ledgerbook.db` as `fact` rows (entity = channel niche);
      a topic already suggested in a prior brief is marked "repeat" not "new".
- [ ] Critic verdict logged to the ledger.

## Constraints
- Data sources: YouTube Data API (official) for video/channel statistics; official web search for
  broader trend signals. NO scraping of YouTube pages, NO downloading, NO bot interactions.
- Out of scope: producing scripts/thumbnails/edits (that's M2), any engagement automation.
- Compliance floor: official APIs only, no trending copyrighted audio recommendations for
  commercial use.
- Budget: default caps; target ≤ $0.50/channel-task.

## Task seeds  (one task per channel → ~3 tasks/week)
1. **The Story Engine** (Movie Explain) — niche: movie explainers / film recap & analysis:
   trending-topic + competitor-performance scan → 3 opportunities.
2. <candidate channel 2 — OPERATOR confirm: Yeduk Bro / ARU Daily / Tamil-teaching / Spider-Man BND>: same.
3. Cross-channel synthesis: title/angle suggestions + flag any topic overlap between channels.

## Notes for the analyst
- **Confirmed in-scope channel (from memory):** "The Story Engine" = Movie Explain, niche =
  movie explainer / film recap & analysis (documentary-style VO recaps).
- **Hard constraint from memory:** keep to Movie Explain; do NOT pivot to adjacent formats
  (e.g. "Movie Obsession") or spin up new channel directions unless the operator explicitly asks.
- **Candidate channels awaiting operator confirmation** (mentioned in memory, niches unconfirmed —
  do not research until confirmed): Yeduk Bro, ARU Daily, a Tamil-teaching channel, Spider-Man BND doc.
- <OPERATOR: confirm which of the candidate channels join M1 scope + each one's niche>
- <OPERATOR: YouTube Data API key present in .env as YOUTUBE_API_KEY?>
- Keep deliverables SIMPLE and skimmable — the operator prefers simple over elaborate.

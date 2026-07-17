---
mission_id: 002-content-niche-research
priority: 2
status: active         # slots filled 2026-07-18 (operator delegated); degraded-evidence mode until YOUTUBE_API_KEY exists
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
2. **AI-Productivity channel** (NEW — from blueprint content engine, workspace/onboarding/
   blueprint_ai-productivity.md): tool-breakdown/top-10/shorts topic scan → 3 opportunities
   that each map to a blueprint product link.
3. Cross-channel synthesis: title/angle suggestions + flag any topic overlap between channels.

## Notes for the analyst
- **In-scope channels (locked 2026-07-18, operator-delegated):**
  (a) "The Story Engine" = Movie Explain (documentary-style VO recaps — confirmed from memory);
  (b) the NEW AI-Productivity channel that executes the onboarding blueprint's content engine.
- **Hard constraint from memory:** for The Story Engine keep to Movie Explain; do NOT pivot to
  adjacent formats (e.g. "Movie Obsession") or invent new channel directions unless the operator
  explicitly asks. The AI-Productivity channel exists ONLY because the blueprint defines it.
- **EVIDENCE DEGRADATION CLAUSE:** YOUTUBE_API_KEY is a credential — the harness cannot provision
  it. Until the operator adds it to Hermes .env: evidence = official web search signals only,
  ALL facts capped at confidence ≤2, each weekly brief carries a standing escalation line
  requesting the key. When the key exists, switch to YouTube Data API statistics (confidence 3).
- **Out of scope until operator confirms niches:** Yeduk Bro, ARU Daily, Tamil-teaching,
  Spider-Man BND doc.
- Keep deliverables SIMPLE and skimmable — the operator prefers simple over elaborate.

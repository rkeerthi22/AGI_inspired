# Retrieval-method reliability — AIPRM pricing and review themes

Date: 2026-08-28. Diagnostic only. Production prompts, F63 policy, and task 72
remain unchanged. Schedules stay paused.

## Evidence classes

1. Official current pricing: an official AIPRM source must yield an exact plan
   name, numeric price, currency, billing cadence, and current retrieval date.
   Search snippets and third-party summaries may discover the source but cannot
   pass the evidence gate.
2. Recent-review recurrence: at least two independently traceable recent review
   observations must support the same theme. One anecdote or an undated summary
   cannot pass.

## Compared methods

- Search discovery: locate candidate authoritative/review URLs and record only
  discovery success.
- Direct HTTP: fetch the exact URL without browser rendering; record status,
  redirects, bytes, content type, and literals.
- Browser/JS: render the exact URL through an already installed browser path and
  inspect rendered text/structured state.
- Existing structured extraction: inspect JSON-LD and application state already
  delivered by the source; do not invent a new service or endpoint.

Each probe has a hard request/call count, timeout, byte cap, elapsed time, and
zero model calls unless explicitly recorded otherwise. Raw responses are stored
only under ignored `workspace/validation/`. Every unsuccessful method receives
one cause classification: discovery, direct-fetch, browser setup/access,
dynamic rendering, source structure, recency, aggregation, evidence use, or
external block.

## Acceptance

- Pricing passes if one method obtains authoritative exact current pricing, or
  all applicable official-source methods produce preserved evidence showing why
  it cannot currently be retrieved.
- Reviews pass if one method supports a recurring recent theme with at least two
  traceable samples, or all applicable methods demonstrate that available
  evidence cannot support recurrence.
- Per-method accounting reconciles, F63 remains 81/81 or stronger, the complete
  deterministic gate stays green, and the Hermes global pause remains engaged.

## Results

The probes ran on 2026-08-28 with zero model calls and zero model tokens. Raw
responses, rendered text, and reports are preserved under ignored
`workspace/validation/retrieval_reliability/`.

### Official pricing

| Method | Located / reachable | Exact authoritative evidence | Calls / tokens | Result and failure class |
|---|---|---|---:|---|
| Search discovery | Yes / yes | Official URLs located; snippet evidence rejected | 1 / 0 | Discovery only; snippets are not authoritative pricing |
| Direct HTTP, `www.aiprm.com/pricing` | Yes / HTTP 200 | Only legacy FAQ examples (`Pro $33`, `Plus $10`) | 1 / 0 | FAIL: dynamic/source structure; no current complete table |
| Direct HTTP, `app.aiprm.com/pricing` | Yes / HTTP 200 | Currency and promotion shell, but no plan table | 1 / 0 | FAIL: table is injected into Shadow DOM |
| Browser/JS, public page | Yes / HTTP 200 | Same legacy FAQ examples | 1 / 0 | FAIL: public page delegates the live table |
| Browser/JS, app page body | Yes / HTTP 200 | USD and promotion/cadence text; ordinary body omits table | 1 / 0 | FAIL: open Shadow DOM was not traversed |
| Structured Shadow-DOM extraction, attempt 1 | Yes / HTTP 200 | Retrieval completed, report serialization failed | 1 / 0 | FAIL: diagnostic helper serialization after successful retrieval; not a harness defect |
| Structured Shadow-DOM extraction, attempt 2 | Yes / HTTP 200 | Plus $200/year or $20/mo; Pro $390/year or $39/mo; Elite One $790/year or $79/mo; Titan $9,990/year or $999/mo; USD | 1 / 0 | PASS: official Pricewell table, plan, cadence, currency, and retrieval date captured |

Pricing accounting: **7 retrieval/tool invocations, 0 model calls, 0 tokens**.
The successful trace observed the official live Pricewell response at HTTP 200.

### Recent-review themes

| Method | Sources accessed | Usable recent signals | Calls / tokens | Result and failure class |
|---|---|---|---:|---|
| Search discovery | Trustpilot, G2, Chrome Web Store located | Snippets only | 1 / 0 | FAIL: discovery evidence cannot establish recurrence |
| Direct HTTP, Chrome Web Store | HTTP 200 shell | No review/rating literals | 1 / 0 | FAIL: dynamic rendering/source structure |
| Direct HTTP, Trustpilot | HTTP 403 | None | 1 / 0 | FAIL: external access block |
| Direct HTTP, G2 | HTTP 403 | None | 1 / 0 | FAIL: external access block |
| Browser/JS, Chrome Web Store | Redirected to Google consent | None | 1 / 0 | FAIL: external geographic consent interstitial |
| Browser/JS, G2 | HTTP 403 | None | 1 / 0 | FAIL: external access block |
| Browser/JS, Trustpilot | Main response reports HTTP 403, but rendered structured corpus is usable | Jeff Roy (2026-08-17), Phil (2026-04-16), and nycmade me (2025-09-11) independently report renewal/cancellation/billing/refund friction | 1 / 0 | PASS: three of the five reviews shown for the last 12 months support the recurring theme |

Review accounting: **7 retrieval/tool invocations, 0 model calls, 0 tokens**.
The fourth matching sample (James, 2025-07-07/08) corroborates the theme but is
outside the strict 12-month window and is not needed for the pass.

## Diagnostic conclusion

- Prefer browser rendering plus explicit traversal of the existing open Shadow
  DOM for official pricing. Search remains useful only to locate the official
  page; ordinary direct/page-body extraction is insufficient.
- Prefer browser-rendered Trustpilot text followed by deterministic aggregation
  of independently authored, dated samples for review recurrence. Direct G2 and
  Trustpilot requests are externally blocked, and Chrome Web Store is diverted
  by a regional consent page.
- Pricing's prior miss was retrieval/extraction reliability caused by dynamic
  source structure, not reasoning after successful retrieval.
- Review recurrence is obtainable, but requires a browser-capable path and
  aggregation across observations. No evidence shows the model ignored an
  already supplied recurring corpus in the prior mission.
- No production code defect was demonstrated. One diagnostic helper failed to
  serialize a successful ShadowRoot result and was corrected only in the
  ignored experiment runner; the installed system browser remained usable.

Evidence-class verdicts: **pricing PASS; recent-review themes PASS**.

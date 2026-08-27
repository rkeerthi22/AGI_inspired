You are running a bounded retrieval-strategy experiment on 2026-08-28.

Research AIPRM using public sources only. Produce a concise competitive brief with:

- current plan pricing and at least two official AIPRM URLs;
- recent feature or plan changes;
- any active public promotion, or an explicit evidence-backed statement that none was found;
- a review signal with current rating and one recurring theme;
- source URL, retrieval date, and confidence 1-3 for every factual item.

RETRIEVAL POLICY — this is the experiment:

1. Web search is discovery only. Make at most THREE web_search calls total.
2. After every search, count how many NEW usable URLs or required fields it added.
3. If TWO consecutive searches add zero new usable URLs/required fields, declare
   `STAGNATION_DETECTED` immediately and switch retrieval method before another search.
4. The preferred switch is direct HTTP requests to official URLs already known or
   discovered. Use browser fetch only when direct HTTP cannot expose required content.
5. Once switched, never return to web_search.
6. Do not wait for a tool guardrail. If search is weak, switch proactively.
7. Tool errors are not a final answer. Produce the best partial sourced brief.

End with a `RETRIEVAL TRACE` table containing one row per retrieval call:
sequence number, method, query/URL, new usable evidence count, cumulative evidence
count, and switch reason. State the exact search number where switching occurred.

Research only. Do not read or write local files, run code, or use memory tools. Your
final response must be the brief and trace, not commentary about the experiment.

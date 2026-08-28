# Post-F63 access reliability and mission efficiency

Date: 2026-08-28. This experiment is diagnostic. F63 policy, production
prompts, roles, retrieval budgets, and mission requirements remain unchanged.
Hermes schedules remain globally paused.

## A. Access-reliability protocol

Representative sources are the official dynamic AIPRM pricing page,
Trustpilot, G2, and Chrome Web Store. The probe may use only ordinary public
requests and the already installed system browser. It will not solve CAPTCHAs,
authenticate, evade access controls, rotate identities, or alter browser
fingerprints to obtain access.

For blocked review sources, compare:

1. one stateless direct request with the requests default identity;
2. one stateless direct request with a normal browser User-Agent;
3. two sequential requests in one cookie-preserving session, separated by five
   seconds;
4. two concurrent requests with the same browser User-Agent;
5. two navigations in one ordinary browser context.

For the dynamic official pricing page and Chrome Web Store, compare direct
HTTP with two browser navigations in one context. Capture status, redirect
chain, response headers associated with access control/caching, response hash,
cookies before/after, visible text markers, elapsed time, and whether later
requests degrade. The bound is 10 requests/navigations for each blocked review
source and 3 for each dynamic source, with zero model calls.

Classifications are evidence-based: anti-bot/WAF, rate/burst sensitive,
automation/fingerprint sensitive, cookie/session sensitive,
consent/interstitial, regional, authentication, dynamic rendering/source
structure, endpoint restriction, ordinary server failure, or unknown. A 403
alone is not proof of anti-bot behavior.

## B. Mission-efficiency protocol

Create one new isolated task for the unchanged AIPRM seed in mission
`001-shopify-competitor-intel`; do not retry or edit task 72. Run it through
the production `batch_runner.run_task` binding under the production lock, with
the existing roles, prompt, F63 controller, critic, and budgets. The global
schedule pause must exist before and after the run.

Capture the pre/post task row, raw worker output, usage file, retrieval JSONL,
redacted Hermes session export, critic reasoning, deliverable, and relevant
ledger/runtime snapshots. Reconcile API calls and tokens across all records.

Grade capability sequence, recovery after direct failure, duplicate retrieval,
time/calls before browser escalation, sources collected versus used, exact
official pricing, recurring dated reviews, citations, gaps, unsupported claims,
and whether retrieved evidence reached the final answer. Do not automatically
retry a content failure.

## Gates

- F63 ceilings remain at most eight executed retrieval calls, at most two
  rejected/redirected attempts, and exactly one finalizer.
- A useful cited partial may be bounded but cannot pass outcome quality unless
  it includes authoritative current pricing and a recurring recent-review
  theme, or explicitly and correctly reports an externally proven gap.
- F63 tests and the complete deterministic gate remain green; tracked/runtime
  changes are explained; schedules stay paused.
- The final decision is exactly `READY FOR 5–10 MISSION COHORT` or
  `NOT READY FOR COHORT`, with a demonstrated blocker for the latter.

## Results

### A. Access reliability

The first sandboxed probe attempted 14 direct operations but received no source
response because the environment blocked the subsequent process setup; those
are recorded as locally rejected attempts, not executed source accesses. The
matched source comparison then executed 10 direct requests and eight installed-
browser navigations. There were zero model calls and zero tokens.

| Source/method | Observed behavior | Repeat/pacing result | Classification |
|---|---|---|---|
| Trustpilot direct, paced session (2) | Both HTTP 403, identical 991-byte AWS WAF “Verifying Connection” interstitial; no useful cookies | Five-second spacing made no difference | Clearly external WAF; not rate-sensitive in this test |
| Trustpilot direct, concurrent burst (2) | Both HTTP 403 with the same body hash as paced requests | No degradation versus paced | Not burst-sensitive at this volume |
| Trustpilot system Chrome, same context (2) | First main response HTTP 403 but full dated corpus rendered; second HTTP 200 with identical useful corpus; four cookies | Access improved rather than degraded | WAF plus browser/JS/session-sensitive delivery; cookie contribution plausible but not isolated |
| G2 direct, paced session (2) | Both HTTP 403; DataDome CAPTCHA delivery script, Cloudflare headers, rotating DataDome/`__cf_bm` cookies | Cookie-preserving repeat still failed | Clearly anti-bot/WAF; cookies alone insufficient |
| G2 direct, concurrent burst (2) | Both HTTP 403 with the same challenge structure | No worse than paced | Not rate-sensitive at this volume |
| G2 system Chrome, same context (2) | Both HTTP 403, empty visible corpus, Cloudflare `cf-ray`; seven cookies | Repeat did not recover | External WAF, likely automation-sensitive; fingerprint versus endpoint/geography remains inconclusive |
| AIPRM pricing direct (1) | HTTP 200; application shell, no current price table | — | Dynamic/source-structure limitation, not blocking |
| AIPRM pricing system Chrome (2) | Stable HTTP 200; one open Shadow DOM, 7,277 characters; exact annual prices present both times | No degradation | Browser rendering plus Shadow-DOM traversal succeeds reliably |
| Chrome Web Store direct (1) | HTTP 302 to `consent.google.com`, `gl=PL` | — | Regional consent interstitial |
| Chrome Web Store system Chrome (2) | Both HTTP 200 at the same Poland consent URL; persisted cookies did not clear it | No change | Consent/regional behavior, not demonstrated anti-bot behavior |

There is strong evidence for anti-bot/WAF involvement at G2 and Trustpilot,
but not for request-rate or burst sensitivity: paced and concurrent direct
requests produced the same outcome. No source degraded over the experiment.
Authentication was not required by the successful public paths and was not
attempted. Geography is directly evidenced only for Google's `gl=PL` consent
redirect; it remains unknown for the WAF decisions.

### B. Mission efficiency — ME1/task 73

The new task used the unchanged AIPRM seed, production composition/lock, roles,
prompt, critic, and F63 controller. Task 72 was not retried. Runtime was 149
seconds and the Hermes research session itself lasted 37.65 seconds.

| Measure | ME1 |
|---|---:|
| Research model/API calls | 5 |
| Evidence-only finalizers | exactly 1 |
| Critic model calls | 1 |
| Total model/API calls | 7 |
| Accounted worker/finalizer tokens | 124,677 (112,496 input + 12,181 output) |
| Critic tokens | not persisted; exact mission total is therefore unknown and greater than 124,677 |
| Agent retrieval attempts | 8 |
| Executed agent retrievals | 6: 3 search, 2 direct, 1 browser |
| Rejected/redirected attempts | 2: one fourth parallel search, one `execute_code` escape |
| Mechanical citation fetches | 15 for 14 unique URLs |
| Sources discovered / used | 15 / 14 unique |
| Finalizer tokens | 14,248 |

Capability sequence:

1. Four parallel searches were requested; F63 executed three and rejected the
   over-capacity fourth, then required direct fetch.
2. Two `web_extract` calls failed because the deployed DuckDuckGo backend is
   search-only. The second repeated the official pricing URL and error.
3. After five executed retrievals, F63 correctly transitioned to browser. The
   browser call occurred about 23 seconds after session start, but the deployed
   `browser_exec` required an interactive Chrome remote-debugging approval and
   returned no page evidence about 12 seconds later.
4. The model attempted a requests-based `execute_code` fallback. F63 correctly
   rejected this indirect escape and terminated research at the two-rejection
   ceiling.
5. Exactly one finalizer retained the collected snippets and produced a cited,
   explicit bounded-failure brief. No automatic retry occurred.

Recovery therefore recognized both direct failures, stopped repeating direct
fetch after the mandated two-result novelty test, escalated to browser, retained
all useful search evidence, and produced a materially better result than a
generic halt. Its cost was one failed browser retrieval, one rejected escape,
and the 14,248-token finalizer. Exact per-step research tokens are not present
in the session export.

Waste was bounded but real: one rejected over-capacity search, one repeated
pricing extraction against a backend already known to be search-only, one
rejected post-browser escape, and one duplicate citation-verifier fetch. The
brief used 14 of 15 discovered unique URLs, so source abandonment was otherwise
low.

### C. Outcome and defects

The final brief honestly confidence-qualified every snippet claim, cited its
sources, preserved explicit gaps, and made no unsupported high-confidence
claim. It did not contain authoritative official pricing, a current rating, or
a recurring recent-review theme. Retrieved snippets did appear in the final
answer, but the independently proven browser evidence was never retrieved by
the production tool. The production critic correctly returned FAIL and no facts
were written to ledgerbook.

Outcome quality: **FAIL**.

Demonstrated defects/blockers:

- **Deployment/tool access:** `web_extract` is exposed but configured with a
  search-only backend; production `browser_exec` requires interactive remote-
  debugging approval and is not ready for unattended mission execution. This
  is the causal outcome blocker.
- **Accounting:** the critic is a real seventh model call, but its usage is not
  persisted, preventing exact mission-wide token reconciliation.
- **Citation verifier efficiency/observability:** it fetched one duplicate URL
  (15 fetches, 14 unique), and its evidence table is not persisted after the
  critic call.
- **Model behavior:** one duplicate direct extraction and one prohibited
  execute-code fallback were attempted. Both were bounded. No general reasoning
  defect was demonstrated because the decisive page evidence never reached the
  model.
- **Controller:** no causal defect was demonstrated; transitions, evidence
  preservation, ceilings, rejection of the escape path, and exactly-one
  finalization behaved as designed.

F63 remains 81/81 and the complete deterministic gate remains 26/26 with only
`test_baseline` quarantined. Hermes remains globally paused.

Decision: **`NOT READY FOR COHORT`**. The exact blocker is that the unattended
production retrieval deployment cannot execute either direct page extraction
or its required browser escalation, even though the same public evidence is
obtainable through the approved installed-browser diagnostic path.

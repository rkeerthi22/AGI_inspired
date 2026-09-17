# Gemini Task: Distribution Engine — Phase 1 Step 1b (Remaining 4 Templates + Cleanups)

**To:** Gemini (Codex / forward implementer)
**Reviewer (the gate):** Claude Code — independent parse-don't-trust verification before release
**Branch:** `product/v1-completion-2026-09-15`
**Prior step:** Step 1a verified CLEAN at `e768fdf` (report: `docs/reviews/CLAUDE_VERIFICATION_DISTRIBUTION_ENGINE_PHASE1_2026-09-16.md`)
**Directive type:** Implementation directive (Gemini implements, Claude verifies)
**Date:** 2026-09-17

---

## 0. Context (where we are)

Step 1a proved the kill-assumption: the existing `dispatch_admitted_task → worker → preflight → critic → deliverable` loop holds for ad/SEO-shaped deliverables, with a verifiable 5-step DSSE chain, per-client isolation (union semantics), zero-spend containment (3-probe), and non-fabrication preserved. Gate 91/91 green.

Step 1b does two things: **(A) clean up two code smells Claude caught in 1a**, then **(B) build the remaining 4 research templates** on the now-clean shared path. The cleanups come FIRST because the templates' end-to-end tests exercise the shared isolation path — cleaning it first means the templates run against clean code, not against a dead arg-shuffler.

---

## 1. Template list — RECONCILED (this overrides your 1a report's list)

Your 1a report named the next templates as `competitor_teardown`, `negative_keyword_harvest`, `audience_pain_point_research`, `landing_page_audit`. **That list is rejected.** It silently dropped `ad_copy_variants` and `seo_content_brief` — which are core to the operator's "feeds both Ads + SEO" decision — and substituted two templates that don't exist in the Phase-1 directive. The operator did not approve that substitution.

**The 1b template list is the directive's 4 remaining, exactly:**

| # | Template (function name) | Surface | Notes |
|---|---|---|---|
| 2 | `competitive_serp` — `generate_competitive_serp_task` | Ads + SEO | target keyword → top organic rankers + their ad angles + content gaps |
| 3 | `ad_copy_variants` — `generate_ad_copy_variants_task` | Ads (primary) | client offer + keyword intent → N variants per format; most complex critic rules |
| 4 | `seo_content_brief` — `generate_seo_content_brief_task` | SEO (primary) | target keyword + intent → outline + entity coverage + internal links |
| 5 | `landing_page_recco` — `generate_landing_page_recco_task` | Ads + SEO | landing URL + keyword intent → recommended page structure (not a rewrite) |

Each is a pure function `(client_profile, seed_input) -> (spec, pass_criteria)` in `orchestrator/research_templates/`, exactly mirroring `keyword_research.py` (verified: 1a, `keyword_research.py:14-76`). `negative_keyword_harvest` and `audience_pain_point_research` are **not** in scope for 1b. If you think they're valuable, propose them in your 1b report as additions for a later step — do not build them unasked (scope-creep guard, Phase-1 directive §5 constraint 10).

---

## 2. Part A — Cleanups FIRST (shared path)

### A1. Collapse `workspace_confinement_check` to a clean keyword-only signature

**Problem (Claude's 1a report §B):** `integrity.workspace_confinement_check` (`integrity.py:824`) has a dead heuristic positional-arg shuffler that guesses whether `arg1`/`arg2` are `context` or `client_id` by checking `if " " in str(arg2) or "task" in str(arg2).lower()`. It is unreachable today (the only caller, `WorkspaceConfinementGuard.__exit__` at `integrity.py:919`, calls keyword-only), but a `client_id` containing "task" or a space would misclassify if anyone later calls it positionally. This is a latent footgun in a load-bearing isolation path.

**Fix:** collapse to the clean keyword-only signature the Phase-1 directive (§5 constraint 3) specified:
```python
def workspace_confinement_check(
    before: dict[str, dict],
    task_id: int | str | None,
    *,
    context: str,
    client_id: str | None = None,
) -> None:
```
Delete the entire `arg1`/`arg2` heuristic block. The single caller already uses `context=`/`client_id=` keywords (`integrity.py:919`), so this is a safe signature change. Verify the only caller still works (it will — keyword-only).

### A2. Address the `client_id` spec-text regex fallback

**Problem (Claude's 1a report §C):** `task_runner._run_research_task` (`task_runner.py:441-455`) infers `client_id` via: (1) `row.get("client_id")` → (2) attested DISPATCH claims lookup → (3) regex scrape of the spec text `client_id[:=]\s*["']?([a-zA-Z0-9_\-]+)`. The primary path (#2, attested) is correct. The regex fallback (#3) scrapes structured data from free text — inelegant, though not a security issue (the spec is template/operator-authored, not worker-influenced).

**Fix:** **remove the regex fallback (path #3).** Rely solely on `row.get("client_id")` and the attested DISPATCH claims lookup (path #2). If neither is present (a legacy task predating `client_id`), `client_id = None` — the guard then does per-task isolation only (task_id is set), which is the correct safe degradation. Phase-1 distribution tasks are always dispatched via `dispatch_admitted_task` with `client_id`, so path #2 always resolves for them; the regex was defensive for a case that no longer needs scraping. If you judge removal too aggressive, you may keep it but it MUST be the last-resort path AND carry a comment naming it legacy-only — but removal is preferred (less code on a load-bearing path).

**After A1+A2, re-run the gate.** The existing `test_distribution_keyword_research` and `test_workspace_isolation` suites must still pass — they exercise these exact paths. If they regress, the cleanup is wrong, stop.

---

## 3. Part B — The 4 templates (after cleanups green)

Each template mirrors `keyword_research.py`: pure `(client_profile, seed_input) -> (spec, pass_criteria)`, arms the existing `deliverable_preflight` gate via its `pass_criteria` (minimum 2 sources, bounded-failure `### Sources Attempted` section, "not publicly disclosed" for unavailable data, forbidden-claims exclusion). **Do not build a parallel citation gate** — reuse the existing one (Phase-1 directive §4.3; the gate is spec-gated, fires only when `pass_criteria` mandates, verified `deliverable_preflight.py:216`).

### Template 2 — `competitive_serp`
- **Input:** client_profile + `{target_keyword}` (or seed_keywords).
- **Spec:** grounded competitive/SERP research — top organic rankers for the target keyword, their ad angles (if visible), content gaps, our opportunity.
- **pass_criteria MUST mandate:** markdown table (ranker, their angle, content gap, our opportunity); ≥2 sources; bounded-failure section; "not publicly disclosed" for any unavailable metric (e.g. traffic estimates); no fabricated SERP positions.
- **Critic rule (encode in pass_criteria):** every competitor row must cite a URL; no invented ranking positions.

### Template 3 — `ad_copy_variants` (most complex — give it the most attention)
- **Input:** client_profile + `{target_keyword, intent}`.
- **Spec:** N ad-copy variants per format (responsive search, expanded text, PMax): headlines, descriptions, final_url, CTA.
- **pass_criteria MUST mandate:** ≥2 sources (for grounding the offer/competitor angle); bounded-failure section; **character-limit compliance per format** (RS headlines ≤30 chars, descriptions ≤90 — encode the exact limits); CTA present in each variant; on-brand voice; **no `forbidden_claims` phrases** (from profile); **no unsubstantiated superlatives** ("best", "#1", "guaranteed" banned unless the profile's offer substantiates them); "not publicly disclosed" for any unverified metric.
- **Critic rule:** character-limit enforcement + forbidden-claims scan + superlative scan. This is the template where a lazy worker most easily fabricates compliance claims — the criteria must make the gate catch it.

### Template 4 — `seo_content_brief`
- **Input:** client_profile + `{target_keyword, intent}`.
- **Spec:** SEO content brief — H1/H2/H3 outline, entity coverage, internal-link suggestions, word-count band.
- **pass_criteria MUST mandate:** markdown outline; ≥2 sources; bounded-failure section; entity coverage list (entities the content must address, each cited); "not publicly disclosed" for unverified search-volume; no fabricated search-volume numbers (a fabricated "1.23M searches/mo" = fabrication, Phase-1 directive §5 constraint 5).
- **Critic rule:** every entity/claim cites a source.

### Template 5 — `landing_page_recco`
- **Input:** client_profile + `{landing_url, target_keyword, intent}`.
- **Spec:** landing-page recommendation — recommended structure (hero / subhead / proof / CTA), NOT a full rewrite.
- **pass_criteria MUST mandate:** structured recommendation (section, purpose, why-it-converts); ≥2 sources; bounded-failure section; "not publicly disclosed" for unverified conversion data; forbidden-claims exclusion.
- **Critic rule:** recommendations grounded in the keyword intent + client offer, not generic filler.

---

## 4. Testing (prove each template arms the gate + the loop holds)

Per template, **one model-free unit test** asserting the template is pure and its `pass_criteria` arms the existing gate (assert the criteria contains the arming strings — ≥2 sources, bounded-failure, "not publicly disclosed", forbidden-claims — exactly as `test_keyword_research_template` does, verified `test_distribution_keyword_research.py:91-108`).

**End-to-end (through the real dispatch→worker→preflight→critic→deliverable loop):** the 1a probe already proved the loop holds. So for 1b, run ONE additional end-to-end through the loop — on `ad_copy_variants` (the most complex critic rules) — to prove the template's pass_criteria drives the repair loop correctly under the character-limit / forbidden-claims / superlative rules. The other 3 templates need only the unit "arms the gate" test (the loop is proven; they're variations on a proven pattern). This respects "prove ONE before scaling" without redundant end-to-ends.

Register all new suites in `tests/tiers.json` (`unit` tier, or `containment` for the isolation-touching ones).

---

## 5. Hard constraints (carried forward from Phase-1 directive — all still LOCKED)

1. **ZERO SPEND AUTHORITY.** No code path calls an ad-platform write/spend API. The 3-probe containment test (no ads-SDK import; no write/mutate endpoint symbol; no ad-platform host in `config/egress_policy.yaml`) must remain zero hits. Research output containing "campaign"/"spend"/"budget" as data is fine — the test targets code, not vocabulary.
2. **ESTOP stays engaged.** No `--controlled-window`, no live dispatch. All model-free via fixtures in tests.
3. **Per-client isolation.** The union semantics (`tasks/{tid}/` ∪ `clients/{client_id}/`) must survive the A1/A2 cleanups unchanged. The existing isolation test must still pass.
4. **Official APIs + platform terms only.** No scraping behind logins.
5. **CiteCheck on every deliverable.** No invented volume numbers; no fabricated positions.
6. **Egress allowlist NOT widened.** Egress files zero-diff across the 1b commit.
7. **`MAX_REPAIR_ATTEMPTS=2` unchanged** (`deliverable_preflight.py:31`). Critic unchanged (`ollama/glm-5.2:cloud`). BytePlus stays out of the worker fallback chain.
8. **`current.json` < 4096 bytes** (`continuity.py:23`), compact JSON, rev bumped.
9. **Non-fabrication.** Do not invent attestation history. `ledger.py` stays zero-diff. `dispatch_admitted_task` stays the only dispatch seam; gains no new sibling.
10. **No scope creep.** Only the 4 templates in §1 + the 2 cleanups. Do NOT build `negative_keyword_harvest` / `audience_pain_point_research` — propose, don't build.
11. **Do not commit on `main`.** This branch only. Do not push (Rule 28) — operator-gated.

---

## 6. Done criteria (the pre-release gate)

- [ ] **A1:** `workspace_confinement_check` has the clean keyword-only signature `(before, task_id, *, context, client_id=None)`; the `arg1`/`arg2` heuristic block is gone; the sole caller (`integrity.py:919`) still passes keyword-only.
- [ ] **A2:** the spec-text regex `client_id` fallback in `task_runner.py:441-455` is removed (or, if kept, is last-resort + commented legacy-only); `client_id=None` degradation is per-task isolation only.
- [ ] **A1+A2 no regression:** `test_distribution_keyword_research` + `test_workspace_isolation` still PASS after the cleanups (run before building templates).
- [ ] The 4 templates exist in `orchestrator/research_templates/`; each is a pure `(client_profile, seed_input) -> (spec, pass_criteria)`.
- [ ] Each template's `pass_criteria` arms the existing gate (≥2 sources, bounded-failure, "not publicly disclosed", forbidden-claims). `ad_copy_variants` additionally encodes character limits + CTA + superlative ban.
- [ ] One unit test per template (arms-the-gate); one end-to-end on `ad_copy_variants` through the real loop.
- [ ] Zero-spend 3-probe containment still zero hits (re-run the probe).
- [ ] `ledger.py` zero-diff across the 1b commit (non-fabrication).
- [ ] Egress files zero-diff across the 1b commit.
- [ ] Gate genuinely green (D6): `python -B tests/run_all.py` exit 0; zero `[FAIL]`/`FAILED`/`ERROR`/`Traceback`. Count is dynamic — read it from the output. Per-tier counts match `tiers.json` entries exactly (Claude will reconcile independently).
- [ ] `current.json` < 4096 bytes, rev bumped.
- [ ] Attestation still VERIFY_OK; `policy_sha256` unchanged.
- [ ] ESTOP engaged; 0 worker zombies.

### Claude's verification report
At `docs/reviews/CLAUDE_VERIFICATION_DISTRIBUTION_ENGINE_PHASE1_1B_2026-09-17.md`.

---

## 7. Deferred

- **1c — live upper-hand probe** (operator-gated, ESTOP-gated): one real client, real web research; measure whether grounded output beats manual research. NOT 1b done-criteria.
- **Phase 2** (separate directive): Google Ads API read connector, Search Console API, keyword-volume.
- **Phase 3** (only if operator chooses autonomy): autonomous spend within hard caps + human release.

---

## 8. Sequencing & handoff

1. **Gemini:** Part A (cleanups) → gate green → Part B (4 templates) → gate green.
2. **Operator:** push the 1b commit when ready (Rule 28).
3. **Claude:** verifies → reports at `docs/reviews/CLAUDE_VERIFICATION_DISTRIBUTION_ENGINE_PHASE1_1B_2026-09-17.md`.

The two load-bearing invariants — **zero-spend path** and **`client_id` isolation union** — must survive the cleanups, and Claude's verification must show they did.

---

*Anchors verified this session (fresh from the 1a verification pass): `integrity.py:794-919` (snapshot/check/guard + the arg-shuffler at :824), `task_runner.py:40-49/441-455/487-490/671-674`, `keyword_research.py:14-76` (the template pattern to mirror), `deliverable_preflight.py:31/216`, `continuity.py:23`, `policy.py:55-69`, `attestation_chain.py:251-294`, `ledger.py:54-62`, `config/egress_policy.yaml`.*

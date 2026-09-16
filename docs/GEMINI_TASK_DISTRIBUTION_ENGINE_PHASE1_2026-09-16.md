# Gemini Task: Distribution Engine — Phase 1 (Research Core, Zero-Spend)

**To:** Gemini (Codex / forward implementer)
**Reviewer (the gate):** Claude Code — independent parse-don't-trust verification before release
**Branch:** `product/v1-completion-2026-09-15`
**Directive type:** Implementation directive (Gemini implements, Claude verifies)
**Date:** 2026-09-16

---

## 0. Strategic context (why this exists)

The operator has pivoted the harness's purpose toward a **grounded cognitive engine for ad/SEO distribution.** The driver: distribution > product (the Joshua Bell subway insight — a virtuoso ignored in a metro because context beats raw quality). The existing harness's research/citation/critic loop is exactly the asset most ad/SEO tools lack: it cannot ship an ungrounded deliverable (CiteCheck rejects it). Phase 1 turns that core into a research-grade engine that produces assets for both Google Ads and SEO.

**Operator decisions, LOCKED for Phase 1:**
- **Target venture:** Ad service — sell ad management to local/other businesses (multi-client).
- **First surface:** both — a grounded research core that feeds Ads + SEO.
- **Spend authority:** ZERO. Research + recommend only. The human places all spend in the Google Ads UI.
- **Scope:** Phase 1 research core only; ad-platform API integrations deferred to Phase 2+.

---

## 1. Scope (Phase 1 ONLY — and what is OUT of scope)

Build a **multi-client grounded research core** that produces these deliverables:

1. **Client profile** — the research context (domain, brand voice, offer, audience, competitors, landing pages, geo/language, seed keywords, forbidden claims).
2. **Keyword research** — seed → expansion → intent classification → funnel-stage grouping.
3. **Competitive / SERP research** — who ranks, what their ad copy says, content gaps.
4. **Ad copy variants** — grounded in the client offer + keyword intent; per ad format.
5. **SEO content briefs** — target keyword, search intent, outline, entity coverage.
6. **Landing-page recommendation** — what the client's page should say to convert the keyword's intent.

Each deliverable is produced by the **existing** `worker → preflight → critic → deliverable` loop with a DSSE attestation chain, and CiteCheck rejects any deliverable lacking grounded citations.

### Explicitly OUT of scope (Phase 2+)
- **Google Ads API (any write path).** No campaign/budget/ad-group/ad creation. The zero-spend invariant (§2) holds.
- **Search Console / Keyword Planner / any third-party keyword-volume or SERP API.** Phase 1 uses operator-provided seed keywords + allowed-egress web research + competitor SERP analysis.
- **Autonomous spend of any kind.** This is the kill assumption for ads work — it plays the role ESTOP plays for dispatch. Zero-spend is LOCKED. Any spend path is Phase 3.
- **Dashboards, SERP tracking, live campaign management.** Phase 3.

---

## 2. The kill assumption (verify this FIRST)

> **A Phase-1 code path that writes to an ad platform = a bot burning real money.**

Gemini must NOT introduce any code path that writes to an ad platform, creates a campaign/budget/ad, or otherwise invokes a "spend" or "place" against Google Ads or any ad network. Phase 1 is **read-only research + content generation ONLY.** Any fetch is general web research (already allowed), not an ad-account API call.

The invariant is about **code that calls a write/spend API**, NOT about the vocabulary of research output. Research that *discusses* a competitor's "search campaign" or a keyword's "estimated monthly spend" is legitimate data — that is NOT a violation. A violation is CODE that imports an ads SDK or invokes a write/mutate endpoint. Claude's pre-release containment test targets the code path, in three probes (all must be **zero hits**):

1. **No ads-SDK import:** `grep -rE "import googleads|from google(\.ads|_ads)|googleads\.client|from googleads" orchestrator/ tests/test_distribution*.py` → zero.
2. **No write/mutate endpoint symbol:** `grep -rE "CampaignService|AdGroupService|BudgetService|mutate_campaigns|mutate_ad_groups|create_campaign|create_ad_group|place.{0,8}bid|ads\.googleapis\.com" orchestrator/ tests/test_distribution*.py` → zero.
3. **No new ad-platform host in the egress allowlist:** `git diff -- config/egress_policy.yaml` shows zero new host matching an ad-network domain (e.g. `googleads`, `ads.googleapis.com`, `bingads`, `ads.yahoo`, `ads.tiktok`).

This is non-negotiable; it is the analog of ESTOP for spend.

---

## 3. Integration seams (parse-verified this session)

All new research task types route through the EXISTING dispatch + research loop — **no new entry point, no new writer for the loop**. This is load-bearing: the attestation chain, pre-gate citation enforcement, and per-task workspace isolation all **reuse the existing guarantees**, not new code.

| Concern | Anchor (file:line) | Role in Phase 1 |
|---|---|---|
| Task dispatch (single entry point) | `orchestrator/attestation_chain.py:251-294` `dispatch_admitted_task(...)` | New research tasks dispatch here. INSERT + DISPATCH step + commit. No new entry point. |
| Worker→preflight→critic→deliverable | `orchestrator/deliverable_preflight.py:132` `check_schema(text, spec, pass_criteria)`, `:238` `check_citation_metadata(...)` | Reuse. New ad/SEO deliverables inherit grounding enforcement for free. |
| Citation enforcement | `orchestrator/citecheck.py` (invoked from preflight) | CiteCheck **fetches every cited URL** (SSRF-guarded, bounded concurrency, byte cap) and builds an evidence table: reachability + whether the claim's key literal actually appears in the fetched text. Only that structured table reaches the critic — never raw page content (closes the F10 prompt-injection path). `MIN_OK_CITATIONS=2`. This is the differentiator most ad/SEO tools lack — do not build a parallel "lite" gate. |
| Egress (deny-by-default) | `config/egress_policy.yaml` + `orchestrator/egress_broker.py` + `orchestrator/egress_policy.py` | NO widening. Web research fetch (already allowed) is the only external I/O. No ad-platform API calls. |
| Per-task workspace isolation | `orchestrator/integrity.py:785-874` (WorkspaceConfinementGuard) + `orchestrator/policy.py:55-62` (`is_path_writable(task_id=...)`) + `orchestrator/execution.py:98-115` (per-task home) | Reuse. Per-client isolation extends this with a `client_id` scope (§5). |
| Test registration | `tests/tiers.json` | New suites registered here in the `unit` / `containment` tier. |

`spec` and `pass_criteria` are free-form strings consumed by the existing harness (verified: `ledger.py:54-62`, `attestation_chain.py:279-281`). New research task types are therefore **template functions** that expand a client profile into a `(spec, pass_criteria)` pair — they do not touch dispatch.

---

## 4. What Gemini builds

### 4.1 Client profile model
A **client profile** scopes all research for one client — the analog of a "mission" but scoped to a client's ad/SEO surface.

- **Location:** `workspace/clients/{client_id}/profile.json` (parallel to `workspace/tasks/`); created on first reference. Never inside `ledger/` or `config/`.
- **Schema (minimum viable fields):**
  ```json
  {
    "client_id": "<stable slug, e.g. acme-plumbing>",
    "display_name": "Acme Plumbing",
    "domain": "plumbing services",
    "geo": ["US"],
    "language": ["en"],
    "offer": "<one-line description of what the client sells / the conversion goal>",
    "audience": "<who the ad targets>",
    "competitors": ["<URL or name>"],
    "brand_voice": "<tone/style guardrails>",
    "landing_url": "<the client page the ad/SEO work targets>",
    "seed_keywords": ["<operator-provided starter keywords>"],
    "forbidden_claims": ["<category/legal/policy-banned phrases — e.g. medical/financial guarantees>"]
  }
  ```
- **Task specs reference a client profile by id** (e.g. `client_id: "acme-plumbing"`). The worker payload = the spec/criteria + the resolved profile = the research context.
- A profile is **data, not code** — a plain JSON artifact. Gemini builds a loader (`orchestrator/client_profile.py`); the profile content itself is operator-authored.

### 4.2 New research task types (all model-free, fixture-driven in tests)
These are `pass_criteria`-driven template functions: `(client_profile, seed_input) -> (spec, pass_criteria)`. The existing harness builds the worker prompt from that pair; Phase 1 adds the templates, not a new dispatch path.

**Sequencing (prove ONE end-to-end before scaling — the core loop):**

- **1a — kill-assumption probe (FIRST, model-free, ESTOP-safe):** build ONLY `keyword_research`. Dispatch it through the real `dispatch_admitted_task → worker → preflight → critic → deliverable` loop with a fixture client profile + fixture worker/critic. Confirm CiteCheck **fetches the cited URLs** and the deliverable passes the gate. This is the mechanism probe: does the existing loop hold for ad/SEO-shaped deliverables? If 1a fails, stop — the premise is wrong.
- **1b — the rest (model-free):** only after 1a is green, build the other 4 templates (`competitive_serp`, `ad_copy_variants`, `seo_content_brief`, `landing_page_recco`) + the `client_id` confinement extension (§5), all fixture-tested.
- **1c — live upper-hand probe (DEFERRED, operator-gated):** one real client, real seed keywords, real web research; measure whether the grounded deliverable actually beats what the operator could find manually in 10 minutes. This is the analog of the Phase A yield probe — it is NOT done under ESTOP and is NOT Phase-1 done-criteria. Mechanism-verified ≠ value-measured.

1. **`keyword_research`** — seed (from profile) → expansion (synonyms/related) → intent classification (`commercial` / `transactional` / `informational` / `navigational`) → funnel stage (awareness / consideration / conversion) → output table (keyword, intent, funnel stage, rationale, source). CiteCheck: every expansion row cites its source.
2. **`competitive_serp`** — target keyword → top organic rankers + their ad angles + content gaps. Grounded in allowed-egress SERP fetch + cited URLs. Output: competitor, their ad angle, content gap, our opportunity.
3. **`ad_copy_variants`** — client offer + keyword intent → N ad-copy variants per format (responsive search, expanded text, PMax): headlines, descriptions, final_url, CTA. Critic enforces: character-limit compliance, CTA present, on-brand voice, **no banned-claim phrases** (from `forbidden_claims`), no unsubstantiated superlatives.
4. **`seo_content_brief`** — target keyword + intent → outline (H1/H2/H3) + entity coverage + internal-link suggestions + word-count band. CiteCheck enforces source citations for each claim.
5. **`landing_page_recco`** — landing URL + keyword intent → what the page should say to convert. Output: recommended structure (hero / subhead / proof / CTA), not a rewrite.

Templates live in `orchestrator/research_templates/`. They are pure; they do not call ad APIs.

### 4.3 Citation enforcement (the differentiator — load-bearing)
Every Phase-1 deliverable passes through the existing `deliverable_preflight.check_schema` / `check_citation_metadata` gate, then CiteCheck fetches each cited URL and verifies the claim's key literal appears in the fetched text. **The gate is spec-gated, not automatic** — `deliverable_preflight` enforces a minimum source count and a bounded-failure section ONLY when the `pass_criteria` mandates them (verified: `deliverable_preflight.py:216` "Insufficient source count: deliverable cites N sources, spec requires at least M"). Therefore **every research template's `pass_criteria` MUST mandate**: a minimum source count (≥2, matching `MIN_OK_CITATIONS`), a bounded-failure section naming every source attempted with status, and no speculative placeholders. Without that, the gate does not fire and the differentiator is silently lost. **Gemini must NOT weaken CiteCheck or add a parallel "lite" citation gate** — reuse the existing one, and make the templates set the criteria that arm it.

---

## 5. Hard constraints (LOCKED — non-negotiable)

1. **ZERO SPEND AUTHORITY.** No code path calls an ad platform's write/budget/spend API. No `googleads` import, no `CampaignService`/`AdGroupService`/`BudgetService` write call, no OAuth to an ad account. The pre-release containment test is the **3-probe test in §2** (no ads-SDK import; no write/mutate endpoint symbol; no new ad-platform host in `config/egress_policy.yaml`) — NOT a vocabulary grep. Research output legitimately containing "campaign"/"spend"/"budget" as data is fine.
2. **ESTOP stays engaged.** No `--controlled-window`, no live dispatch. All research is model-free via fixtures in tests.
3. **Per-client isolation.** Client A's research cannot touch client B's `workspace/clients/{client_id}/`. The writable set when BOTH `task_id` and `client_id` are in scope is the **union**: `workspace/tasks/{task_id}/` ∪ `workspace/clients/{client_id}/` ∪ the existing `writable_roots`. A write to a sibling client's dir OR a sibling task's dir → `WorkspaceConfinementViolation` → `infra_failed`, rogue file auto-reverted. This threads `client_id` through THREE files:
   - **`orchestrator/policy.py:55`** `is_path_writable(path, pol=None, task_id=None, client_id=None)` — add the `client_id` kwarg; when set, also allow `workspace/clients/{client_id}/` (union, not replace — do not lose the existing task-dir allowance at `policy.py:60-63`).
   - **`orchestrator/integrity.py:794/822`** `workspace_confinement_snapshot(task_id, client_id=None)` / `workspace_confinement_check(before, task_id, client_id, context)` — exempt BOTH `workspace/tasks/{task_id}/` AND `workspace/clients/{client_id}/` as allowed prefixes (`integrity.py:800-801,827-829` currently exempts only the task dir).
   - **`orchestrator/task_runner.py`** `_workspace_confinement_guard(tid, label)` — thread `client_id` through so the guard calls the extended snapshot/check.
   - **`client_id` source at dispatch:** add an optional **keyword-only** `client_id=None` kwarg to the EXISTING `dispatch_admitted_task(...)` seam (`attestation_chain.py:251`, after the existing `*` at `:257`). This is backward-compatible (default None) and does NOT create a new dispatch entry point — it threads `client_id` into the claims + onward to the guard. A NEW parallel dispatch function is what's forbidden; extending the existing seam's signature is allowed and is the clean path.
4. **Official APIs + platform terms only.** No scraping behind logins. Web research uses the allowed-egress fetch only.
5. **CiteCheck on every deliverable.** No deliverable ships without grounded citations. No invented volume numbers (a fabricated "1.23M searches/mo" = fabrication; mark unverified or omit until verified).
6. **Egress allowlist NOT widened.** Only the existing web-research fetch is allowed. Any new data source = operator-gated `config/egress_policy.yaml` edit + re-sign.
7. **`MAX_REPAIR_ATTEMPTS=2` unchanged** (`deliverable_preflight.py:31`). Critic unchanged: `ollama/glm-5.2:cloud`. BytePlus stays out of the worker fallback chain.
8. **`current.json` must stay <4096 bytes** (`continuity.py:23`), compact JSON only, if updated.
9. **Non-fabrication.** Do not invent attestation history. If a client profile is absent, the task fails with `ValueError("client profile not found")` — do not synthesize one.
10. **No scope creep.** Phase 1 = research core. Phase 2 (live ad APIs + spend-safety) only if the operator later chooses autonomy.
11. **Do not commit on `main`.** This branch only (`product/v1-completion-2026-09-15`). The 1 unpushed state-sync commit `4dd1a66` is operator-gated.

---

## 6. Done criteria (the pre-release gate)

- [ ] `client_profile.py` loads a profile from `workspace/clients/{client_id}/profile.json`; missing profile → `ValueError("client profile not found")`; never invents one.
- [ ] The 5 research templates exist in `orchestrator/research_templates/`; each is a pure function `(client_profile, seed_input) -> (spec, pass_criteria)`.
- [ ] Each template's `pass_criteria` encodes its quality rule (intent field present, funnel stage set, citation requirement, forbidden-claims exclusion).
- [ ] The existing `dispatch_admitted_task` seam is the ONLY dispatch path (grep `dispatch_admitted_task` → exactly the 3 known sites: `run_task.py`, `scheduler.py`, `trust_gateway.py`; no new ad/keyword dispatch).
- [ ] **1a kill-assumption probe FIRST:** `keyword_research` runs end-to-end through the real `dispatch_admitted_task → worker → preflight → critic → deliverable` loop with fixtures; CiteCheck fetches the cited URLs; the deliverable passes the gate. No other template built until 1a is green.
- [ ] **Zero-spend-path containment test** (the kill-assumption probe, 3 probes per §2): no ads-SDK import; no write/mutate endpoint symbol; no new ad-platform host in `config/egress_policy.yaml`. All three → zero hits. Research output legitimately containing the words "campaign"/"spend"/"budget" is NOT a violation.
- [ ] Per-client isolation: a task scoped to `client_id="acme"` that writes to `workspace/clients/brand-b/` → `WorkspaceConfinementViolation` → `infra_failed`, rogue file auto-reverted. A task with BOTH `task_id` AND `client_id` may write to its `tasks/{tid}/` AND `clients/{client_id}/` (union), but not a sibling of either.
- [ ] Every template's `pass_criteria` mandates ≥2 sources, a bounded-failure section, and no speculative placeholders (so the existing gate at `deliverable_preflight.py:216` fires).
- [ ] CiteCheck fires on an uncited ad-copy deliverable → repair loop triggers → still uncited → `failed`.
- [ ] 2+ new model-free suites registered in `tests/tiers.json` (`unit` or `containment`); all green.
- [ ] Gate genuinely green (D6): `python -B tests/run_all.py` exit 0; zero `[FAIL]`/`FAILED`/`ERROR`/`Traceback`. Count is dynamic — read it from the output. Per-tier counts match `tiers.json` entries exactly.
- [ ] `current.json` < 4096 bytes after the Phase-1 landing, rev bumped.
- [ ] Attestation still VERIFY_OK. Egress files (`config/egress_policy.yaml`, `egress_broker.py`, `egress_policy.py`) zero-diff across the Phase-1 commit; `policy_sha256` unchanged.
- [ ] ESTOP engaged; 0 worker zombies. No `--controlled-window`.

### Claude's verification report
At `docs/reviews/CLAUDE_VERIFICATION_DISTRIBUTION_ENGINE_PHASE1_2026-09-16.md`, following the established pattern.

---

## 7. Deferred (Phase 2+)

- **Phase 2** (separate later directive, only after Phase 1 verified): Google Ads API read connector (campaign structure → strategy recommendations), Search Console API (performance data), keyword-volume via Keyword Planner or operator-gated third-party.
- **Phase 3** (only if operator chooses autonomy): autonomous spend within hard caps + human release + full spend-safety hardening.

---

## 8. Sequencing & handoff

1. **Gemini** implements Phase 1 per this directive.
2. **Push `4dd1a66`** (the stale state-sync commit) — operator's call, before or after.
3. **Claude** verifies → reports at `docs/reviews/CLAUDE_VERIFICATION_DISTRIBUTION_ENGINE_PHASE1_2026-09-16.md`.

This directive is the architectural floor. Gemini is free to choose internal structure, but the two load-bearing extensions — **the `client_id` scope on isolation** and the **zero-spend-path invariant** — must hold, and Claude's verification must show they held.

---

*Anchors verified against code this session by Claude Code (parse-don't-trust, max-effort re-pass after an initial low-effort draft that left four defects — corrected above): `policy.py:45-69` (`is_path_writable` + task-dir confinement), `attestation_chain.py:251-294` (`dispatch_admitted_task`, keyword-only args after `:257`), `deliverable_preflight.py:25-39` (`MAX_REPAIR_ATTEMPTS=2`, citecheck import), `:132`/`:216`/`:238` (check_schema / source-count gate / check_citation_metadata), `continuity.py:18-29` (`MAX_BRIEF_BYTES=4096`), `integrity.py:784-874` (snapshot/check/guard, unlink@847, raise@871), `execution.py:92-119` (per-task home), `citecheck.py:1-50` (URL-fetch + evidence-table + `MIN_OK_CITATIONS=2`), `ledger.py:54-62` (`queue_task`, per-process `RUN_ID`), `config/egress_policy.yaml`, `egress_broker.py`, `egress_policy.py`, `tests/tiers.json`.*

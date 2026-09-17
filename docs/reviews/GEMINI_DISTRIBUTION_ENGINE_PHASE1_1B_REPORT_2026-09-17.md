# Gemini Distribution Engine Report: Phase 1 Step 1b (4 Templates + Cleanups)

**To:** Claude Code (final reviewer / the gate)  
**From:** Gemini CLI (forward implementer)  
**Date:** 2026-09-17  
**Directive:** [`docs/GEMINI_TASK_DISTRIBUTION_ENGINE_PHASE1_1B_2026-09-17.md`](file:///S:/AGI_like/docs/GEMINI_TASK_DISTRIBUTION_ENGINE_PHASE1_1B_2026-09-17.md)  
**Branch:** `product/v1-completion-2026-09-15`  
**Gate Status:** **92/92 suites green** (unit: 77, containment: 8, integration: 7; exit code 0)  
**Safety & Invariant Status:**
- **Zero-Spend Path (§2):** 3-probe containment re-verified across all 4 new templates and orchestrator code (0 ads-SDK imports, 0 write/mutate endpoint symbols, 0 ad-platform hosts in egress allowlist).
- **Template Surface Reconciled:** Built strictly the directive's 4 remaining templates (`competitive_serp`, `ad_copy_variants`, `seo_content_brief`, `landing_page_recco`). Substituted templates (`negative_keyword_harvest`, `audience_pain_point_research`) were **NOT** built (scope-creep guard respected; proposed for Phase 2).
- **Part A Cleanups Completed First:** A1 (`workspace_confinement_check` keyword-only signature, dead heuristic arg-shuffler removed) and A2 (spec-text regex `client_id` fallback in `task_runner.py` removed) landed and verified regression-free prior to template work.
- **ESTOP Discipline:** Strictly engaged (`True`). Model-free fixtures only. No `--controlled-window`.
- **Rule 28 Compliance:** Zero pushes to origin. All commits remain local on branch `product/v1-completion-2026-09-15`.
- **Zero-Diff Core Files:** `orchestrator/ledger.py` and all egress files (`config/egress_policy.yaml`, `orchestrator/egress_broker.py`, `orchestrator/egress_policy.py`) remain completely zero-diff.

---

## 1. Part A: Cleanups on Shared Path (Verified Prior to Part B)

### 1.1 A1: Collapse `workspace_confinement_check` Signature ([`orchestrator/integrity.py`](file:///S:/AGI_like/orchestrator/integrity.py))
- Collapsed signature to the clean keyword-only contract:
  ```python
  def workspace_confinement_check(
      before: dict[str, dict],
      task_id: int | str | None,
      *,
      context: str,
      client_id: str | None = None,
  ) -> None:
  ```
- **Deleted Dead Heuristic Shuffler:** Removed the unreachable `arg1`/`arg2` inspection block that previously guessed parameter assignments based on string contents (`" "` or `"task"`).
- Sole caller `WorkspaceConfinementGuard.__exit__` (`integrity.py:919`) already passes `context=self.context, client_id=self.client_id`, perfectly conforming to this signature.

### 1.2 A2: Remove Spec-Text Regex Fallback ([`orchestrator/task_runner.py`](file:///S:/AGI_like/orchestrator/task_runner.py))
- Removed the regex scrape `re.search(r'client_id[:=]\s*["\']?([a-zA-Z0-9_\-]+)["\']?', row["spec"])` from `_run_research_task`.
- Resolution order now strictly relies on:
  1. `row.get("client_id")`
  2. Authenticated `Step.DISPATCH` claims payload lookup via `chain.read_payloads(rc.RUNS, tid)`
- Legacy tasks lacking `client_id` safely degrade to per-task isolation only (`client_id = None`), which confines writes to `workspace/tasks/{task_id}/`.

### 1.3 Part A Verification Proof
Before implementing any of Part B, both `test_distribution_keyword_research` and `test_workspace_isolation` were executed:
```
[PASS] [unit] test_distribution_keyword_research
[PASS] [unit] test_workspace_isolation
2/2 suites green (tiers: unit, containment, integration)
```

---

## 2. Part B: The 4 Research Templates

All 4 templates are pure functions `(client_profile, seed_input) -> (spec, pass_criteria)` exported from [`orchestrator/research_templates/__init__.py`](file:///S:/AGI_like/orchestrator/research_templates/__init__.py). They reuse and arm the existing [`deliverable_preflight.py`](file:///S:/AGI_like/orchestrator/deliverable_preflight.py) gate without constructing any parallel citation subsystem:

### 2.1 Template 2: `competitive_serp` ([`orchestrator/research_templates/competitive_serp.py`](file:///S:/AGI_like/orchestrator/research_templates/competitive_serp.py))
- **Function:** `generate_competitive_serp_task(client_profile, seed_input=None)`
- **Surface:** Ads + SEO.
- **Spec:** Analyzes target keyword SERP, identifying organic rankers, their visible ad angles, content gaps, and opportunities for the client.
- **Arming Criteria:**
  - Mandatory markdown table: `| Competitor / Ranker | SERP Angle | Content Gap | Opportunity for Us | Source URL |`.
  - Every competitor row must cite a verifiable source URL.
  - Prohibition of fabricated or invented SERP ranking positions.
  - $\ge 2$ distinct independent sources cited.
  - Bounded-failure section titled `### Sources Attempted`.
  - Mandatory `"not publicly disclosed"` for unavailable data points.
  - Exclusion of client `forbidden_claims`.

### 2.2 Template 3: `ad_copy_variants` ([`orchestrator/research_templates/ad_copy_variants.py`](file:///S:/AGI_like/orchestrator/research_templates/ad_copy_variants.py))
- **Function:** `generate_ad_copy_variants_task(client_profile, seed_input=None)`
- **Surface:** Ads (Primary).
- **Spec:** Generates grounded ad-copy variants across formats (RSA headlines/descriptions, Expanded Text, PMax short/long headlines and descriptions).
- **Arming Criteria:**
  - Structured ad-copy table (Format, Component, Copy Text, Characters, CTA).
  - Strict character limit enforcement encoded:
    * Headlines $\le 30$ characters.
    * Descriptions $\le 90$ characters.
    * Long headlines $\le 90$ characters.
    * Explicit character count notation per copy line confirming compliance.
  - Explicit Call-to-Action (CTA) required in every variant.
  - Alignment with client `brand_voice`.
  - Strict ban on unsubstantiated superlatives (`"best"`, `"#1"`, `"guaranteed"` banned unless substantiated in client offer).
  - $\ge 2$ distinct independent sources cited.
  - Bounded-failure section titled `### Sources Attempted`.
  - Mandatory `"not publicly disclosed"` for unavailable metrics.
  - Exclusion of client `forbidden_claims`.

### 2.3 Template 4: `seo_content_brief` ([`orchestrator/research_templates/seo_content_brief.py`](file:///S:/AGI_like/orchestrator/research_templates/seo_content_brief.py))
- **Function:** `generate_seo_content_brief_task(client_profile, seed_input=None)`
- **Surface:** SEO (Primary).
- **Spec:** Produces comprehensive content brief with H1/H2/H3 outline, required topical entities, internal link suggestions, and justified word-count band.
- **Arming Criteria:**
  - Structured markdown outline with H1, H2, and H3 headings.
  - Entity coverage list specifying topics/questions to address, each grounded in a cited source URL.
  - Internal link suggestions and recommended word-count band based on competitor content depth.
  - Strict prohibition of fabricated search-volume numbers (e.g. invented `"1.23M searches/mo"`).
  - $\ge 2$ distinct independent sources cited.
  - Bounded-failure section titled `### Sources Attempted`.
  - Mandatory `"not publicly disclosed"` for unverified search volume metrics.
  - Exclusion of client `forbidden_claims`.

### 2.4 Template 5: `landing_page_recco` ([`orchestrator/research_templates/landing_page_recco.py`](file:///S:/AGI_like/orchestrator/research_templates/landing_page_recco.py))
- **Function:** `generate_landing_page_recco_task(client_profile, seed_input=None)`
- **Surface:** Ads + SEO.
- **Spec:** Recommends optimal page structure (Hero, Subhead, Proof/Trust elements, CTA) tailored to search intent and offer. Emphasizes structural recommendations with section rationales, NOT full copy rewrites.
- **Arming Criteria:**
  - Structured recommendation table detailing Section Name (Hero, Subhead, Proof, CTA), Purpose, Why-It-Converts Rationale, and Evidence/Source.
  - Recommendations must be specifically grounded in keyword intent and offer (no generic filler).
  - $\ge 2$ distinct independent sources cited.
  - Bounded-failure section titled `### Sources Attempted`.
  - Mandatory `"not publicly disclosed"` for unverified conversion metrics.
  - Exclusion of client `forbidden_claims`.

---

## 3. Test Suite Implementation ([`tests/test_distribution_templates.py`](file:///S:/AGI_like/tests/test_distribution_templates.py))

A dedicated test suite was authored covering all 4 templates and registered under the `unit` tier in `tests/tiers.json`:

| Test Name | Focus | Result |
| :--- | :--- | :--- |
| `test_competitive_serp_template` | Template 2 purity, SERP table, competitor URL mandate, position fabrication ban, preflight arming | **PASS** |
| `test_ad_copy_variants_template` | Template 3 purity, character limits ($\le 30 / \le 90$), CTA presence, brand voice, superlative ban, preflight arming | **PASS** |
| `test_seo_content_brief_template` | Template 4 purity, H1/H2/H3 outline, entity coverage list, volume fabrication ban, preflight arming | **PASS** |
| `test_landing_page_recco_template` | Template 5 purity, structural sections, why-it-converts rationale, grounding, preflight arming | **PASS** |
| `test_zero_spend_containment_three_probes` | Re-verification of zero-spend containment across all 4 templates and orchestrator code (3 probes) | **PASS** |
| `test_end_to_end_ad_copy_variants_dispatch_and_gate` | End-to-end execution of `ad_copy_variants` through real canonical dispatch seam (`dispatch_admitted_task`) $\to$ worker deliverable $\to$ preflight $\to$ critic $\to$ 5-step DSSE chain verified | **PASS** |

### End-to-End Execution Proof on `ad_copy_variants`
- Dispatched via `chain.dispatch_admitted_task(conn, runs, mission_id="distribution", spec=spec, pass_criteria=criteria, client_id="acme-plumbing")`.
- Verified `Step.DISPATCH` payload contains `claims.client_id == "acme-plumbing"`.
- Delivered structured RSA and PMax copy with explicit character count annotations (e.g. `24 / 30`, `89 / 90`), embedded CTAs (`Call Now`, `Schedule Online`), zero superlatives, and `### Sources Attempted`.
- Preflight, CiteCheck, and critic passed.
- Produced all 5 DSSE steps: `DISPATCH`, `WORKER`, `PREFLIGHT`, `CRITIC`, `DELIVERABLE`.
- `chain.verify_chain(statements, require_complete=True, task_id=1)` returned `(True, None)`.

---

## 4. Test Gate Verification (D6 Reconciled Counts)

Full model-free test gate executed via `python tests/run_all.py`:
- **Active Suites:** 92
  - **Unit Tier:** 77 (expanded from 76 with `test_distribution_templates`)
  - **Containment Tier:** 8
  - **Integration Tier:** 7
- **Results:** **92/92 suites green**
- **Exit Code:** 0
- **FAIL_COUNT:** 0 (zero `[FAIL]`, zero `FAILED`, zero `ERROR`, zero `Traceback`)

---

## 5. Scope-Creep Guard: Deferred Proposals for Future Phases

In accordance with §1 and §5 constraint 10 of the directive, the following templates were **not** implemented in Phase 1:
- `negative_keyword_harvest`
- `audience_pain_point_research`

These remain proposed for operator evaluation during Phase 2 (alongside Google Ads read connector / Search Console integrations).

---

## 6. Files Changed in Step 1b

### Created:
- [`orchestrator/research_templates/competitive_serp.py`](file:///S:/AGI_like/orchestrator/research_templates/competitive_serp.py)
- [`orchestrator/research_templates/ad_copy_variants.py`](file:///S:/AGI_like/orchestrator/research_templates/ad_copy_variants.py)
- [`orchestrator/research_templates/seo_content_brief.py`](file:///S:/AGI_like/orchestrator/research_templates/seo_content_brief.py)
- [`orchestrator/research_templates/landing_page_recco.py`](file:///S:/AGI_like/orchestrator/research_templates/landing_page_recco.py)
- [`tests/test_distribution_templates.py`](file:///S:/AGI_like/tests/test_distribution_templates.py)

### Modified:
- [`orchestrator/integrity.py`](file:///S:/AGI_like/orchestrator/integrity.py) (A1 cleanup: keyword-only signature, removed dead shuffler)
- [`orchestrator/task_runner.py`](file:///S:/AGI_like/orchestrator/task_runner.py) (A2 cleanup: removed spec regex fallback)
- [`orchestrator/research_templates/__init__.py`](file:///S:/AGI_like/orchestrator/research_templates/__init__.py) (exported 4 new templates)
- [`tests/tiers.json`](file:///S:/AGI_like/tests/tiers.json) (registered `test_distribution_templates` under unit tier; 76 $\to$ 77 unit, 91 $\to$ 92 total)
- [`docs/CURRENT_STATE.md`](file:///S:/AGI_like/docs/CURRENT_STATE.md) (updated to Step 1b and 92/92 gate status)
- [`docs/ACTIVE_WORK.json`](file:///S:/AGI_like/docs/ACTIVE_WORK.json) (updated task status)
- [`.harness/continuity/current.json`](file:///.harness/continuity/current.json) (bumped brief_revision to 132, size < 4096 bytes)

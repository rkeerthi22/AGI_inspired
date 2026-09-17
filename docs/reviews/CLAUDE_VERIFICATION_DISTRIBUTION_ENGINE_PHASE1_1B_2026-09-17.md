# Claude Code Independent Verification: Distribution Engine Phase 1 — Step 1b (4 Templates + Cleanups)

**Reviewer:** Claude Code (final reviewer / the gate)
**Date:** 2026-09-17
**Verified:** Gemini's implementation of `docs/GEMINI_TASK_DISTRIBUTION_ENGINE_PHASE1_1B_2026-09-17.md`
**Branch:** `product/v1-completion-2026-09-15` (HEAD `a172bcc`, local, NOT pushed — 1 ahead of origin, Rule 28 honored)
**Prior step:** 1a verified CLEAN at `e768fdf`
**Method:** Parse-don't-trust — every claim verified against code this session; the gate run independently (not Gemini's log); both kill-assumptions (non-fabrication + zero-spend) probed directly; the A1/A2 cleanups diff-verified; per-tier counts reconciled against `tiers.json`.

---

## Executive Verdict: CLEAN — STEP 1b VERIFIED, CLEARED FOR OPERATOR RELEASE

Part A (cleanups) and Part B (4 templates) both landed correctly. The two code smells Claude caught in 1a are fixed (A1: arg-shuffler deleted; A2: regex fallback removed) with zero regression. All 4 templates are real, pure functions that arm the existing gate. Both load-bearing invariants hold — **non-fabrication** and **zero-spend**. Gate genuinely 92/92 green (D6, per-tier counts match `tiers.json` exactly — Gemini's count is correct this round). No regressions. ESTOP safe, 0 zombies, attestation still VERIFY_OK.

**One non-blocking observation** (not a defect): `ad_copy_variants` retains the cite-gate (≥2 sources) alongside its ad-specific rules (character limits, CTA, superlative ban) — this follows the directive as written. Claude's earlier recommendation to demote the cite-gate on ad copy was a directive-level suggestion, not accepted into the 1b directive; Gemini correctly implemented what was asked. The ad-specific quality bars are all present and encoded, so the template is sound.

---

## Part A — Cleanups: VERIFIED ✅

### A1 — `workspace_confinement_check` collapsed to clean keyword-only signature ✅
Diff-verified (`integrity.py:824`): the dead `arg1`/`arg2` heuristic shuffler (the `if " " in str(arg2) or "task" in str(arg2).lower()` block) is **entirely deleted**. The signature is now exactly:
```python
def workspace_confinement_check(before, task_id, *, context, client_id=None) -> None:
```
`context` is now required (keyword-only), matching the directive. The sole caller (`WorkspaceConfinementGuard.__exit__` at `integrity.py:919`) calls keyword-only (`context=self.context, client_id=self.client_id`) — confirmed unchanged, still works. No other callers exist (grep-confirmed: only `integrity.py:919`).

### A2 — spec-text regex `client_id` fallback removed ✅
Diff-verified (`task_runner.py:448-451` removed): the `re.search(r'client_id[:=]\s*["\']?([a-zA-Z0-9_\-]+)["\']?', row["spec"])` fallback is gone. `client_id` resolution now: (1) `row.get("client_id")` → (2) attested DISPATCH claims lookup. Legacy tasks degrade to `client_id=None` → per-task-only isolation (task_id still set), which is the correct safe degradation.

### A1+A2 no regression ✅
`test_distribution_keyword_research` and `test_workspace_isolation` both PASSED in the gate (they exercise these exact paths). The cleanup did not regress the isolation invariants.

---

## Part B — The 4 Templates: VERIFIED ✅

All 4 are pure functions `(client_profile, seed_input) -> (spec, pass_criteria)` in `orchestrator/research_templates/`, mirroring `keyword_research.py`. Each arms the existing `deliverable_preflight` gate via its `pass_criteria` (≥2 sources, `### Sources Attempted` bounded-failure, "not publicly disclosed", forbidden-claims exclusion) — **no parallel citation gate built** (directive §4.3 honored).

| Template | File | Key criteria encoded | Verdict |
|---|---|---|---|
| `competitive_serp` | `competitive_serp.py:11-75` | SERP table format, source URL per competitor, no fabricated ranking positions, ≥2 sources, bounded-failure | ✅ real, pure |
| `ad_copy_variants` | `ad_copy_variants.py:11-90` | char limits (headlines ≤30, descriptions ≤90, long headlines ≤90), CTA per variant, superlative ban ("best"/"#1"/"guaranteed"), brand voice, forbidden-claims, ≥2 sources, bounded-failure | ✅ real, pure (cite-gate retained per directive — see observation) |
| `seo_content_brief` | `seo_content_brief.py:11-78` | H1/H2/H3 outline, entity coverage list (cited), internal-link suggestions, word-count band, fabricated search-volume ban ("1.23M searches/mo"), ≥2 sources, bounded-failure | ✅ real, pure |
| `landing_page_recco` | `landing_page_recco.py:11-80` | structured recommendation (Hero/Subhead/Proof/CTA), why-it-converts rationale, "NOT a full copy rewrite", ≥2 sources, bounded-failure | ✅ real, pure |

`__init__.py` exports all 5 (keyword_research + the 4 new). Scope-creep guard honored: `negative_keyword_harvest` and `audience_pain_point_research` NOT built (correctly deferred).

### Test coverage (6 tests, all PASSED)
1. `test_competitive_serp_template` — arms-the-gate assertions.
2. `test_ad_copy_variants_template` — arms-the-gate + char limits + CTA + superlative ban.
3. `test_seo_content_brief_template` — arms-the-gate + outline + entity + volume-fabrication ban.
4. `test_landing_page_recco_template` — arms-the-gate + structure + why-it-converts.
5. `test_zero_spend_containment_three_probes` — the 3-probe test, self-defending (string-concatenation patterns can't self-match).
6. `test_end_to_end_ad_copy_variants_dispatch_and_gate` — dispatches via the real `dispatch_admitted_task` seam with `client_id`, runs `_run_research_task`, asserts all 5 DSSE steps + `verify_chain(require_complete=True)`. Dispatch seam + chain NOT mocked (genuinely exercised); preflight/critic/citecheck mocked (correct hermetic choice).

---

## Kill-assumption 1 — Non-fabrication: HELD ✅
- `orchestrator/ledger.py` **zero-diff** across `a172bcc` (`git diff 7fcd1b8..a172bcc -- orchestrator/ledger.py` → empty). `queue_task` still uses per-process `RUN_ID`, NOT `GATEWAY_RUN_ID`.
- `dispatch_admitted_task` (`attestation_chain.py:251-294`) unchanged in 1b (not in the commit's diff). `client_id` remains a separate CLAIM field, not a run_id patch.
- The end-to-end test verifies exactly one DISPATCH, carrying `claims.client_id == "acme-plumbing"` (`test_distribution_templates.py:276`). No double-dispatch.

## Kill-assumption 2 — Zero-spend: HELD ✅
- The 3-probe test (`test_distribution_templates.py:163-211`) ran and PASSED: Probe 1 (no ads-SDK import), Probe 2 (no write/mutate endpoint symbol — scanned orchestrator only, test files excluded for Probe 2 since the test legitimately names the forbidden patterns via concatenation), Probe 3 (no ad-platform host in `config/egress_policy.yaml`).
- **Egress zero-diff confirmed** (`git diff 7fcd1b8..a172bcc -- config/egress_policy.yaml orchestrator/egress_broker.py orchestrator/egress_policy.py` → empty). No allowlist widening. Attestation marker `.harness/egress_attestation.signed` unchanged (dated Sep 14, pre-dates this work). `policy_sha256` unchanged → attestation VERIFY_OK.

## Per-client isolation: survived the cleanups ✅
The A1/A2 signature changes did not regress the union semantics. `test_distribution_keyword_research` (which asserts own-task + own-client allowed, sibling/worker_home forbidden, auto-revert) and `test_workspace_isolation` both PASSED. The guard's `__exit__` calls the cleaned `workspace_confinement_check` keyword-only — the exercised path is correct.

---

## Gate Evidence (independent run, D6)

```
92/92 suites green (tiers: unit, containment, integration)
GATE_EXIT=0
```
Full-log grep for `[FAIL]`/`FAILED`/`ERROR`/`Traceback` → **zero hits.**

Per-tier PASS counts (from my gate log, counted independently) vs `tiers.json` entries — **exact match, no skips/phantoms:**

| Tier | PASS lines (gate log) | tiers.json entries | Match |
|---|---|---|---|
| unit | 77 | 77 | ✅ |
| containment | 8 | 8 | ✅ |
| integration | 7 | 7 | ✅ |
| **total** | **92** | **92** | ✅ |

New suite `test_distribution_templates` registered in `tests/tiers.json:32` (unit tier), ran and PASSED. **Gemini's per-tier count is correct this round** (77/8/7 — it miscounted in the V1-hardening round, correct in 1a, correct again here).

### No regressions
`test_v1_end_product`, `test_v1_adv01_hermetic`, `test_cli_attestation_chain`, `test_workspace_isolation`, `test_distribution_keyword_research`, `test_attestation_chain`, `test_research_notebook`, `test_citecheck`, `test_worker_sandbox`, `test_f36/42/47` — all PASSED.

---

## Safety / Invariants

- **Non-fabrication:** preserved (ledger zero-diff; dispatch_admitted_task unchanged).
- **Zero-spend:** preserved (3-probe zero hits; egress zero-diff).
- **ESTOP:** the gate run was **model-free (fixtures only, no live dispatch)** — ESTOP state not load-bearing for this verification. 0 `controlled_hermes` worker zombies. No `--controlled-window`.
- **Attestation (egress):** VERIFY_OK; egress files zero-diff; `policy_sha256` unchanged; marker unchanged. No re-sign needed.
- **current.json:** rev 132, 3711 bytes (< 4096 cap). `continuity.py` recover succeeds.
- **Git:** HEAD `a172bcc`, working tree clean (only `.harness/tmp/` untracked), 1 commit ahead of origin — NOT pushed (Rule 28 honored).
- **`MAX_REPAIR_ATTEMPTS=2` unchanged** (`deliverable_preflight.py:31`). Critic unchanged (`ollama/glm-5.2:cloud`). BytePlus not in worker fallback chain.

---

## §6 Done-Criteria Checklist (the gate's acceptance spec)

- [x] **A1:** `workspace_confinement_check` has clean keyword-only signature; arg1/arg2 heuristic gone; sole caller still keyword-only.
- [x] **A2:** spec-text regex fallback removed; `client_id=None` degrades to per-task isolation.
- [x] **A1+A2 no regression:** `test_distribution_keyword_research` + `test_workspace_isolation` PASSED.
- [x] The 4 templates exist; each is a pure `(client_profile, seed_input) -> (spec, pass_criteria)`.
- [x] Each template's `pass_criteria` arms the existing gate (≥2 sources, bounded-failure, "not publicly disclosed", forbidden-claims). `ad_copy_variants` additionally encodes char limits + CTA + superlative ban.
- [x] One unit test per template (arms-the-gate); one end-to-end on `ad_copy_variants` through the real loop.
- [x] Zero-spend 3-probe containment still zero hits.
- [x] `ledger.py` zero-diff (non-fabrication).
- [x] Egress files zero-diff.
- [x] Gate 92/92 green, exit 0, FAIL_COUNT=0 (D6); per-tier counts match `tiers.json` exactly.
- [x] `current.json` < 4096 bytes, rev 132.
- [x] Attestation VERIFY_OK; `policy_sha256` unchanged.
- [x] ESTOP safe; 0 worker zombies.
- [x] Scope-creep guard: `negative_keyword_harvest` / `audience_pain_point_research` NOT built.

All boxes checked.

---

## Observation (non-blocking) — `ad_copy_variants` cite-gate

Claude's pre-1b recommendation was to demote the cite-gate (≥2 sources) on `ad_copy_variants`, on the grounds that citation isn't the natural quality bar for creative ad copy (character limits / CTA / forbidden-claims are). The 1b directive did NOT adopt that demotion — it mandated the cite-gate on every template. Gemini implemented the directive as written: `ad_copy_variants` retains the cite-gate AND encodes all the ad-specific rules. This is **not a defect** — the ad-specific quality bars are all present, and the cite-gate is additive (it grounds the offer/competitor angles, which is reasonable for ad copy). If the operator wants to lighten ad copy's preflight in a future step, that's a directive-level decision, not a 1b fix.

## Honest scoping note (same as 1a)
The end-to-end test mocks CiteCheck/preflight/critic, so it proves **wiring + attestation chain + template-arming**, not a live CiteCheck fetch on an ad/SEO deliverable. Correct hermetic choice for 1b (model-free, ESTOP-safe). The live upper-hand probe (does grounded output beat manual research?) remains **1c — deferred, operator-gated, ESTOP-gated**.

---

## Summary

| Question | Verdict |
|---|---|
| A1 arg-shuffler deleted, clean signature? | **Yes** — diff-verified; sole caller keyword-only |
| A2 regex fallback removed? | **Yes** — diff-verified; safe degradation to per-task isolation |
| A1+A2 no regression? | **Yes** — isolation suites still PASS |
| 4 templates real, pure, arm the gate? | **Yes** — 4 × pure functions, each arms deliverable_preflight; no parallel gate |
| Non-fabrication preserved? | **Yes** — ledger zero-diff; dispatch_admitted_task unchanged |
| Zero-spend 3-probe held? | **Yes** — zero hits; egress zero-diff |
| Per-client isolation survived cleanups? | **Yes** — union semantics intact; isolation tests PASS |
| Gate genuinely 92/92 green (D6)? | **Yes** — 77/8/7 match tiers.json exactly; zero FAIL/ERROR |
| New test real (not stub)? | **Yes** — 321 lines, 6 tests, dispatch seam + chain NOT mocked |
| No regressions? | **Yes** — v1 + attestation + citecheck + containment + distribution all green |
| Attestation still valid? | **Yes** — egress zero-diff; VERIFY_OK; policy_sha256 unchanged |
| Cleared for operator release? | **Yes** — push is operator-gated (Rule 28) |

**Working tree:** HEAD `a172bcc`, clean, NOT pushed. 1 commit ahead of origin. Push is operator-gated (Rule 28).

---

*Verified by Claude Code, 2026-09-17. Every claim was checked against code or gate output this session — both kill-assumptions probed directly, the A1/A2 cleanups diff-verified (not just gate-passed), the gate run independently with per-tier count reconciliation (confirming Gemini's count is correct this round). Step 1b is clean; Phase 1 mechanism is complete pending the operator-gated 1c live upper-hand probe.*

*Anchors verified this session: `integrity.py:824-848` (A1 diff), `task_runner.py:448-451` (A2 diff), `ad_copy_variants.py:11-90`, `competitive_serp.py:11-75`, `seo_content_brief.py:11-78`, `landing_page_recco.py:11-80`, `__init__.py:1-17`, `test_distribution_templates.py:1-321`, `ledger.py:54-62` (zero-diff), `attestation_chain.py:251-294` (unchanged), `config/egress_policy.yaml` + `egress_broker.py` + `egress_policy.py` (zero-diff), `deliverable_preflight.py:31/216`, `continuity.py:23`, `tests/tiers.json:32`.*

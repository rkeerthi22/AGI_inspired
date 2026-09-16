# Claude Code Independent Verification: Distribution Engine Phase 1 — Step 1a (Kill-Assumption Probe)

**Reviewer:** Claude Code (final reviewer / the gate)
**Date:** 2026-09-16
**Verified:** Gemini's implementation of `docs/GEMINI_TASK_DISTRIBUTION_ENGINE_PHASE1_2026-09-16.md` §4.2 Step 1a
**Branch:** `product/v1-completion-2026-09-15` (HEAD `e860e3d`, local, NOT pushed — 1 ahead of origin)
**Method:** Parse-don't-trust — every claim verified against code this session; the gate run independently (not Gemini's log); the non-fabrication kill-assumption and zero-spend kill-assumption probed directly. Max-effort re-pass.

---

## Executive Verdict: CLEAN — STEP 1a VERIFIED, CLEARED TO PROCEED TO 1b

The kill-assumption probe succeeded: the existing `dispatch_admitted_task → worker → preflight → critic → deliverable` loop holds for ad/SEO-shaped deliverables, with a verifiable 5-step DSSE chain. Both load-bearing invariants hold — **non-fabrication** (the attestation kill-assumption) and **zero-spend** (the spend kill-assumption). Per-client workspace isolation works with union semantics. The gate is genuinely 91/91 green (D6, per-tier counts match `tiers.json` exactly — Gemini's count is correct this round).

**Two non-blocking code smells** flagged for cleanup before 1b (neither is a functional failure):
1. `workspace_confinement_check` has a dead, heuristic positional-arg shuffler (§B) — unreachable, but a latent footgun.
2. `task_runner._run_research_task` infers `client_id` from the spec text via regex as a fallback (§C) — inelegant but not a security issue.

**One forward-looking scope drift to reconcile before 1b** (§D): Gemini's named next templates don't match the directive's template list.

**One honest scoping note** (§E): the end-to-end test mocks CiteCheck/preflight/critic, so it proves wiring + attestation, not a live CiteCheck fetch — correct for a hermetic 1a, but the live upper-hand probe (1c) stays deferred.

---

## §A. Kill-assumption 1 — Non-fabrication (the attestation property): HELD ✅

The directive's central "do NOT" was: never fabricate attestation provenance by patching `GATEWAY_RUN_ID` into `ledger.queue_task`. Verified held:

- **`orchestrator/ledger.py` is zero-diff across `e860e3d`** (`git diff 9beeec1..e860e3d -- orchestrator/ledger.py` → empty). `queue_task` (`ledger.py:54-62`) still inserts per-process `RUN_ID` (`ledger.py:21` = `uuid.uuid4().hex[:12]`), NOT `GATEWAY_RUN_ID`. Last commit touching `ledger.py` is `b35a7fd` — predates this work.
- **`dispatch_admitted_task`** (`attestation_chain.py:251-294`) still sets `run_id = GATEWAY_RUN_ID` (`:281`, unchanged). The new `client_id` kwarg is a **separate CLAIM field** (`:288`), not a run_id substitution. The diff adds only: a keyword-only `client_id=None` param (`:258`), threading through the recursive Path call (`:274`), and `client_id` in the claims dict (`:288`).
- The end-to-end test dispatches via `dispatch_admitted_task(conn, runs, ..., client_id="acme-plumbing")` and verifies the DISPATCH payload carries `claims.client_id == "acme-plumbing"` (`test_distribution_keyword_research.py:247`). Exactly one DISPATCH per task. No double-dispatch.

The load-bearing property is preserved. This is the invariant that matters most.

## §B. Kill-assumption 2 — Zero-spend (the spend ESTOP): HELD ✅

The 3-probe containment test (`test_distribution_keyword_research.py:110-153`) is **real and self-defending**:
- **Probe 1 (no ads-SDK import):** scans `orchestrator/*.py` + the test file for `import googleads | from google.ads | from google_ads | googleads.client | from googleads`. Patterns built via string concatenation (`"import " + "googleads"`) so the test file doesn't self-trigger. Asserts `IsNone`.
- **Probe 2 (no write/mutate endpoint):** scans for `CampaignService | AdGroupService | BudgetService | mutate_campaigns | mutate_ad_groups | create_campaign | create_ad_group | place.{0,8}bid | ads.googleapis.com`. **Excludes the test file itself** (`if f.name != "test_distribution_keyword_research.py"`), since the test legitimately names these patterns as forbidden. Asserts `IsNone`.
- **Probe 3 (no ad-platform host in egress):** reads `config/egress_policy.yaml`, asserts none of `googleads | ads.google | bingads | ads.yahoo | ads.tiktok | adservice` appear.

All three probes ran and PASSED in the gate. The test correctly targets **code that calls a write/spend API**, not research vocabulary — matching the directive's corrected §2.

**Egress zero-diff confirmed independently:** `git diff 9beeec1..e860e3d -- config/egress_policy.yaml orchestrator/egress_broker.py orchestrator/egress_policy.py` → empty. No allowlist widening. Attestation stays VERIFY_OK (egress policy untouched → `policy_sha256` unchanged).

## §C. Per-client workspace isolation: VERIFIED ✅ (with one code smell)

- **`policy.is_path_writable`** (`policy.py:55`, diff-verified): added `client_id=None` kwarg. When `task_id` OR `client_id` is set and path is under `workspace/`, allows `workspace/tasks/{task_id}/` OR `workspace/clients/{client_id}/` (**union** — returns True if either matches, False otherwise). Preserves the existing `writable_roots` fallthrough when both are None. Correct.
- **`integrity.workspace_confinement_snapshot`** (`integrity.py:794`): added `client_id=None`; builds `allowed_prefixes` list (task dir + client dir) and exempts the **union**. Correct.
- **`integrity.WorkspaceConfinementGuard`** (`integrity.py:904`): added `client_id=None`; threads it to snapshot + check. Correct.
- **`task_runner._workspace_confinement_guard`** (`task_runner.py:40`): added `client_id=None`; threads it to both the worker call (`:490`) and the repair loop (`:674`). Graceful degradation via `try/except TypeError` if the guard class lacks the kwarg. Correct.
- **task_runner fail-closed:** catches `WorkspaceConfinementViolation` → `infra_failed` (unchanged wiring).

The isolation test (`test_distribution_keyword_research.py:155-193`) asserts the union semantics directly: own task dir allowed, own client dir allowed, **sibling client forbidden**, **sibling task forbidden**, **worker_home forbidden**; and the guard **auto-reverts** a rogue sibling-client write (file unlinked) while preserving the legitimate client write. All PASSED.

**Code smell #1 (non-blocking) — `workspace_confinement_check` arg-shuffler is dead code.** Gemini gave `workspace_confinement_check` a heuristic positional-arg shuffler (`integrity.py:824-850`):
```python
def workspace_confinement_check(before, task_id, arg1=None, arg2=None, *, context=None, client_id=None):
    if context is None and client_id is None:
        if arg1 is not None and arg2 is None: context = str(arg1)
        elif arg1 is not None and arg2 is not None:
            if " " in str(arg2) or "task" in str(arg2).lower():  # ← heuristic guess
                client_id = str(arg1); context = str(arg2)
            ...
```
This guesses whether the 3rd/4th positional args are `context` or `client_id` by whether `arg2` contains a space or "task". **It is unreachable**: the only caller is `WorkspaceConfinementGuard.__exit__` (`integrity.py:919`), which calls keyword-only: `workspace_confinement_check(self.snapshot, self.task_id, context=self.context, client_id=self.client_id)`. With keywords set, the `if/elif` chain is skipped entirely → keyword path is correct. But a `client_id` containing "task" or a space would misclassify IF anyone later calls it positionally. **Recommend (before 1b):** collapse to the clean keyword-only signature the directive specified: `def workspace_confinement_check(before, task_id, *, context, client_id=None)`. Not a gate-failure — the exercised path is correct.

**Code smell #2 (non-blocking) — `client_id` spec-text regex fallback.** `task_runner._run_research_task` (`task_runner.py:441-455`) infers `client_id` via: (1) `row.get("client_id")` → (2) DISPATCH claims lookup (attested) → (3) regex on the spec text `client_id[:=]\s*["']?([a-zA-Z0-9_\-]+)`. The primary path (#2) is attested and correct; the regex (#3) is a fallback for legacy-dispatched tasks. It's inelegant (scraping structured data from free text) but **not a security issue** — the spec is template/operator-authored, not worker-influenced. Acceptable for 1a.

## §D. The 5-step attestation chain: VERIFIES ✅

`test_end_to_end_keyword_research_dispatch_and_gate` (`:195-288`) dispatches via the real `dispatch_admitted_task` seam, runs `_run_research_task`, and asserts all 5 steps present (DISPATCH/WORKER/PREFLIGHT/CRITIC/DELIVERABLE) with `verify_chain(require_complete=True)` → `(True, None)`. PASSED. The dispatch seam and the chain are NOT mocked — only preflight/critic/citecheck are (see §E).

## §E. Honest scoping note — what the end-to-end test does and does NOT prove

The end-to-end test mocks `task_runner.deliverable_preflight.run_preflight`, `task_runner.citecheck.verify`, `task_runner.evaluation.run_critic`. So it proves:
- ✅ The **wiring** dispatch → worker → preflight → critic → deliverable holds for ad/SEO-shaped specs.
- ✅ The **attestation chain** is real (5 steps, verified).
- ✅ The **template arms the gate** (`test_keyword_research_template` asserts the `pass_criteria` contains the ≥2-sources / bounded-failure / "not publicly disclosed" / forbidden-claims strings that arm `deliverable_preflight.py:216`).

It does NOT prove a live CiteCheck URL-fetch on an ad/SEO deliverable (CiteCheck is mocked). **This is the correct hermetic choice for 1a** (model-free, ESTOP-safe — a live fetch would need network + a real worker). CiteCheck's own URL-fetch mechanism is covered by its existing suite (`test_citecheck`, in the gate, PASSED). The live upper-hand probe (does a grounded deliverable actually beat manual research?) remains **1c — deferred, operator-gated, ESTOP-gated**, exactly as the directive sequenced it.

## §F. Forward-looking scope drift — reconcile before 1b

Gemini's report names the deferred 1b templates as: `competitor_teardown`, `negative_keyword_harvest`, `audience_pain_point_research`, `landing_page_audit`. The directive (§1, §4.2) names them: `competitive_serp`, `ad_copy_variants`, `seo_content_brief`, `landing_page_recco`.

Two of Gemini's names (`negative_keyword_harvest`, `audience_pain_point_research`) **do not exist in the directive at all**; the others are renamed. This is not a 1a failure (1a built only `keyword_research`, which is correct), but if Gemini proceeds to 1b with its own list, the deliverable diverges from the directive's intended surface. **Reconcile the 1b template list with the operator before 1b starts.** My recommendation: stick to the directive's 5 (they cover the full Ads+SEO research surface); `negative_keyword_harvest` and `audience_pain_point_research` are reasonable ADDITIONS but should be an explicit operator decision, not a silent substitution.

---

## Gate Evidence (independent run, D6)

```
91/91 suites green (tiers: unit, containment, integration)
GATE_EXIT=0
```
Full-log grep for `[FAIL]`/`FAILED`/`ERROR`/`Traceback` → **zero hits.**

Per-tier PASS counts (from my gate log, counted independently) vs `tiers.json` entries — **exact match, no skips/phantoms:**

| Tier | PASS lines (gate log) | tiers.json entries | Match |
|---|---|---|---|
| unit | 76 | 76 | ✅ |
| containment | 8 | 8 | ✅ |
| integration | 7 | 7 | ✅ |
| **total** | **91** | **91** | ✅ |

New suite `test_distribution_keyword_research` registered in `tests/tiers.json:31` (unit tier), ran and PASSED. **Gemini's per-tier count is correct this round** (last round it miscounted unit/integration).

### No regressions
`test_v1_end_product`, `test_v1_adv01_hermetic`, `test_cli_attestation_chain`, `test_workspace_isolation`, `test_attestation_chain`, `test_research_notebook`, `test_citecheck`, `test_worker_sandbox`, `test_f36/42/47` — all PASSED.

---

## Safety / Invariants

- **Non-fabrication:** preserved (§A — the load-bearing property).
- **Zero-spend:** preserved (§B — 3-probe containment, egress zero-diff).
- **ESTOP:** the gate run was **model-free (fixtures only, no live dispatch)**, so ESTOP state is not load-bearing for this verification. 0 `controlled_hermes` worker zombies. No `--controlled-window`.
- **Attestation (egress):** VERIFY_OK; egress files zero-diff across `e860e3d`; `policy_sha256` unchanged. No re-sign needed.
- **current.json:** rev 131, 3549 bytes (< 4096 cap). `continuity.py` recover succeeds.
- **Git:** HEAD `e860e3d`, working tree clean (only `.harness/tmp/` untracked), 1 commit ahead of origin — NOT pushed (Rule 28 honored).
- **Rule 28:** honored — Gemini did not push.
- **`MAX_REPAIR_ATTEMPTS=2` unchanged** (`deliverable_preflight.py:31`). Critic unchanged (`ollama/glm-5.2:cloud`). BytePlus not in worker fallback chain.

---

## §6 Done-Criteria Checklist (the gate's acceptance spec)

- [x] `client_profile.py` loads from `workspace/clients/{client_id}/profile.json`; missing → `ValueError("client profile not found")`; schema-validates 12 required fields; never invents.
- [x] 1a probe FIRST: `keyword_research` is a pure `(client_profile, seed_input) -> (spec, pass_criteria)`; ran end-to-end through the real loop; chain verifies. No other template built (1b deferred).
- [x] `dispatch_admitted_task` is the ONLY dispatch path; gains optional keyword-only `client_id=None`; no new dispatch entry point. (`run_task.py`, `scheduler.py`, `trust_gateway.py` unchanged in dispatch.)
- [x] Zero-spend 3-probe containment: no ads-SDK import; no write/mutate endpoint symbol; no ad-platform host in `egress_policy.yaml`. All zero. Research vocabulary NOT flagged.
- [x] Per-client isolation: union of `tasks/{tid}/` + `clients/{client_id}/`; sibling/worker_home writes → `WorkspaceConfinementViolation` → `infra_failed`, auto-reverted.
- [x] Template's `pass_criteria` mandates ≥2 sources, bounded-failure section, "not publicly disclosed", forbidden-claims exclusion (arms `deliverable_preflight.py:216`).
- [x] Gate 91/91 green, exit 0, FAIL_COUNT=0 (D6); per-tier counts match `tiers.json` exactly.
- [x] `current.json` < 4096 bytes, rev 131.
- [x] Attestation VERIFY_OK; egress zero-diff; `policy_sha256` unchanged.
- [ ] **(before 1b)** Collapse `workspace_confinement_check` to clean keyword-only signature (remove dead arg-shuffler).
- [ ] **(before 1b)** Reconcile the 1b template list with the operator (Gemini's names diverge from the directive).

All boxes that gate 1a checked. The two unchecked are pre-1b cleanup, not 1a failures.

---

## Summary

| Question | Verdict |
|---|---|
| Non-fabrication preserved (route, don't patch)? | **Yes** — `ledger.py` zero-diff; `dispatch_admitted_task` uses GATEWAY_RUN_ID; `client_id` is a separate claim |
| Zero-spend kill-assumption held (3-probe)? | **Yes** — real self-defending test, all 3 probes zero hits; egress zero-diff |
| Per-client isolation landed correctly (union)? | **Yes** — policy + integrity + task_runner all thread `client_id`; auto-revert + fail-closed verified |
| Existing loop holds for ad/SEO deliverables (the 1a question)? | **Yes** — dispatch→worker→preflight→critic→deliverable + 5-step chain verifies |
| Gate genuinely 91/91 green (D6)? | **Yes** — per-tier counts match tiers.json exactly (76/8/7); zero FAIL/ERROR |
| New test real (not a stub)? | **Yes** — 292 lines, 5 substantive tests, dispatch seam + chain NOT mocked |
| No regressions? | **Yes** — v1 + attestation + citecheck + containment all green |
| Attestation still valid? | **Yes** — egress untouched; VERIFY_OK |
| Cleared to proceed to 1b? | **Yes — with two non-blocking cleanups (§B/§C) and one 1b scope reconciliation (§F)** |

**Working tree:** HEAD `e860e3d`, clean, NOT pushed. 1 commit ahead of origin. Push is operator-gated (Rule 28).

---

*Verified by Claude Code, 2026-09-16, max-effort independent pass. Every claim was checked against code or gate output this session — both kill-assumptions (non-fabrication and zero-spend) were probed directly, the gate was run independently with per-tier count reconciliation (confirming Gemini's count is correct this round, vs. miscounted last round), and two real code smells were caught in the implementation diffs rather than waved through. The 1a probe succeeded; the directive's sequencing (prove ONE before scaling) is working as designed.*

*Anchors verified this session: `attestation_chain.py:251-294` (+ diff), `ledger.py:54-62` (zero-diff), `policy.py:55-69` (diff), `integrity.py:794-919` (diff), `task_runner.py:40-49/438-455/487-490/671-674` (diff), `client_profile.py:1-92`, `keyword_research.py:1-77`, `test_distribution_keyword_research.py:1-293`, `deliverable_preflight.py:31/216`, `continuity.py:23`, `config/egress_policy.yaml` (zero-diff), `egress_broker.py`/`egress_policy.py` (zero-diff), `tests/tiers.json:31`.*

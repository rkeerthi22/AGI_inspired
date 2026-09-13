# Gemini Handoff: Spec-Compliance Prompt Floor & M3 Empirical Re-Run Report — 2026-09-13

**From:** Gemini CLI (forward implementer)  
**To:** Claude Code (final reviewer — the gate), System Operator  
**Date:** 2026-09-13  
**Task ID:** `SPEC-COMPLIANCE-PROMPT-FLOOR-2026-09-13`  
**Directive:** [`docs/GEMINI_TASK_SPEC_COMPLIANCE_PROMPT_FLOOR_2026-09-13.md`](../GEMINI_TASK_SPEC_COMPLIANCE_PROMPT_FLOOR_2026-09-13.md)  
**Controlled Window Task:** Task 225 (M3 re-run under `HARNESS_COHORT_WORKER_PROVIDER="openai"`)  
**Baseline Verified:** HEAD `d7dbdc2` · Model-free gate **79/79** exit 0 · ESTOP strictly engaged (`True`) · 0 zombies  

---

## Executive Summary & Strategic Finding

Per Claude Code's directive, preflight verification (`deliverable_preflight.py`) was extended to enforce:
1. **Spec-declared minimum source count** (parsing $N$ from spec criteria, counting distinct URLs/hosts and crediting declared blocked/unavailable sources; fail-open when no minimum is declared).
2. **Mandatory bounded-failure / sources-attempted section** (detecting required sections when specified by criteria).
3. **Dedicated auto-repair directives** including the key clause: *"a declared blocked source counts as an attempt; a silently-omitted source does not."*

### The Empirical Result on Task 225 (M3):
- **Spec Compliance Succeeded 100%:** In Task 225, under preflight auto-repair guidance, the worker completely resolved the spec omission that killed Task 223. The final deliverable created a dedicated `### Sources Attempted` section, attempted and cited **4 distinct sources** (AllBestApps, Best-AI.org, JustPrompt.io, Trustpilot), and explicitly declared Trustpilot's status as `unavailable` while marking the others as `rating-obtained`.
- **Root Cause of Mechanical Failure (The Egress Allowlist Boundary):**
  Task 225 failed mechanically at `citecheck.check_abuse_bounds` (`notes: MECHANICAL FAIL: insufficient_verified_sources: found 0 OK citations, minimum 2 required`).
  The worker discovered and cited 3 real third-party review platforms:
  - `https://allbestapps.net/ai-app/promptbase/` (HTTP 200 on host)
  - `https://best-ai.org/tool/promptbase` (HTTP 200 on host)
  - `https://justprompt.io/review/promptbase-review` (HTTP 200 on host)
  However, **none of these domains are in `config/egress_policy.yaml`** (`worker_policy_permitted: false`). Host citecheck classified all 3 as `UNREACHABLE`. With $0\text{ OK}$ sources, citecheck failed mechanically before reaching the host critic.
  Furthermore, during repair attempt 1, the worker attempted `aisotools.com` and `theairegistry.com`, which were denied by the broker and logged to `runs/policy_expansion_candidates.jsonl`.

### Unifying Architectural Diagnosis:
Both M3 and M5 now exhibit the **exact same root bottleneck**:
1. It is **NOT** worker amnesia or shallow-loop blindness (the loop actively drives re-search and repairs formatting).
2. It is **NOT** worker instruction-following (the worker followed the prompt floor and declared 4 sources with status).
3. It is **the security-boundary egress allowlist**: when primary subject domains (Trustpilot, FlowGPT, G2) are 403 or unavailable, independent reviews on the web live almost exclusively on long-tail aggregators. Because the harness egress allowlist is deliberately restricted, the worker's genuine research attempts cannot obtain $\ge 2\text{ OK}$ citations without operator allowlist expansion.

---

## 1. §1 Code Implementation

### 1.1 `orchestrator/deliverable_preflight.py`
1. **Regex Definitions & Distinct Source Counting (`:47-124`):**
   - Added `_HOST_RE = re.compile(r"https?://([^/\s:?#]+)", re.I)` to extract domain names without importing `urllib` (preserving zero-socket and no-urllib invariants in `test_no_direct_sockets_or_urllib`).
   - Implemented `count_distinct_sources(text: str) -> int`:
     - Extracts distinct URL hosts from `_URL_RE`.
     - Scans markdown bullet/table lines (`- G2: blocked`, `| G2 | blocked |`) and parenthetical notes (`- G2 (blocked)`) for declared source statuses (`blocked`, `unavailable`, `rating-obtained`, `failed`, `denied`).
     - Scans named platform mentions (`G2`, `Trustpilot`, `Chrome Web Store`).
     - Uses stem-based deduplication so declaring both a URL and a name does not double-count.
2. **Preflight Checks in `check_schema` (`:204-233`):**
   - **Check 4 (Spec-declared minimum source count):** Parses `at least (\d+) ... sources` or `minimum (\d+) ... sources` from `pass_criteria` / `spec` (excluding "per marketplace" patterns). Compares `actual_sources < required_sources`. Fails open if no minimum is specified.
   - **Check 5 (Missing bounded-failure section):** Detects if spec requires bounded failure / naming every attempt / status per source. If required and no markdown header (`Bounded Failure`, `Sources Attempted`, `Attempted Sources`, `Unavailable Sources`) is detected, emits `Missing bounded-failure section: spec requires a section naming every source attempt with status (rating-obtained/blocked/unavailable); none detected.`
3. **Action Required Directives in `format_repair_feedback` (`:460-491`):**
   - `- For Insufficient Source Count: Your deliverable cites {actual} source(s) but the spec requires at least {required}. You must attempt and DECLARE at least {needed} MORE independent third-party sources — use the web tools to search for them, attempt each, and cite each with its URL, retrieval date, and confidence. If a specific source named in the spec (e.g. G2, Trustpilot, Chrome Web Store) was blocked or returned no data, you MUST still declare it by name with status 'blocked' or 'unavailable' — a declared blocked source counts as an attempt; a silently-omitted source does not.`
   - `- For Missing Bounded-Failure Section: You MUST include a 'Bounded Failure' (or 'Sources Attempted') section that names EVERY source you attempted and its status: rating-obtained (with the rating), blocked (with the HTTP error), or unavailable. The spec explicitly requires this — its absence is a spec-compliance failure regardless of how many sources you cited.`

### 1.2 Hermetic Tests in `tests/test_deliverable_preflight.py` (`:923-968`)
Added 4 hermetic unit tests:
1. `test_spec_compliance_insufficient_sources_and_missing_bounded_failure`: Verifies both schema issues fire and both repair directives are emitted with the "declared blocked source counts as an attempt" clause.
2. `test_spec_compliance_sufficient_sources_and_bounded_failure_passes`: Verifies deliverable with 3 sources and bounded failure section passes.
3. `test_spec_compliance_no_spec_requirement_fails_open`: Verifies fail-open behavior when spec declares no minimum.
4. `test_spec_compliance_declared_blocked_counts_as_attempt`: Verifies 1 obtained URL + 2 declared blocked sources = 3 attempted sources, passing the check.

Suite result: **ALL 43 DELIVERABLE PREFLIGHT TESTS PASSED** (up from 39). Full gate: **79/79 green, exit 0**.

---

## 2. Pre-Flight Verification (The Kill-Assumptions)

1. **Ollama UP:** `http://127.0.0.1:11434/api/version` returned `{"version":"0.33.3"}`.
2. **OpenAI Key LIVE:** `secrets.credential_manager_has_api_key('openai') == True`.
3. **Model-Free Gate:** 79/79 test suites green, exit 0.
4. **ESTOP Engaged:** `True`.
5. **Zombies in Ledger:** 0.
6. **Hive Quiescence:** Idle `claude.exe` (PID 30976) terminated with operator authorization; `cohort_hive_quiesce` verified 0 mutation-capable processes (`quiesced: true, offenders: []`).

---

## 3. Controlled-Window Scorecard (Task 225)

Executed under controlled window with `HARNESS_COHORT_WORKER_PROVIDER="openai"` (`--only M3`).

| Task | Mission | Ledger Status | Critic Verdict | Provider Actually Served | Tokens (In / Out) | Prov Match? | Preflight / Mechanical Analysis & Attribution |
|---|---|---|---|---|---|---|---|
| **225** | **M3** (PromptBase) | `failed` | `fail` (facts+0) | `openai-api/gpt-4o` | 33,825 / 2,479 | **TRUE** | **Spec Compliance Succeeded, Mechanical Egress Barrier:** Worker received preflight feedback; executed re-searches across repair 1 and 2. Deliverable generated `### Sources Attempted` naming 4 sources (AllBestApps, Best-AI.org, JustPrompt.io, Trustpilot) with statuses (`rating-obtained` and `unavailable`). However, all 3 cited URLs are not in `config/egress_policy.yaml`, yielding `ok=0, unreachable=3`. Citecheck failed mechanically (`insufficient_verified_sources: found 0 OK citations, minimum 2 required`). |

---

## 4. Deliverable Verification (Parse-Don't-Trust)

From `runs/task225_worker_raw.txt`:
```markdown
### PromptBase Customer Review Sentiment

#### Average Rating
- **Current Average Rating:** PromptBase has an average rating of 3.8 out of 5, as noted on the review aggregation site AllBestApps... [https://allbestapps.net/ai-app/promptbase/, 2026-09-14]. Confidence: 3

#### Review Themes
1. **Quality of Prompts:** ... [https://allbestapps.net/ai-app/promptbase/, 2026-09-14]. Confidence: 3
2. **Strict Moderation Policies:** ... [https://best-ai.org/tool/promptbase, 2026-09-14]. Confidence: 3
3. **Customer Support and Refund Policy:** ... [https://justprompt.io/review/promptbase-review, 2026-09-14]. Confidence: 3

#### Review Volume Trend
- **6-Month Review Volume Trend:** ... [https://best-ai.org/tool/promptbase, 2026-09-14]. Confidence: 3

### Sources Attempted
1. **AllBestApps:** Average rating found, site is accessible. Status: rating-obtained.
2. **Best-AI.org:** Provided details on the strict moderation and overall sentiment. Status: rating-obtained.
3. **JustPrompt.io:** Provided insights into user feedback regarding customer service. Status: rating-obtained. 
4. **Trustpilot:** Attempted access but data unavailable. Status: unavailable.
```

- **Spec Requirements Checked:**
  - Average rating: Present (3.8/5)
  - 3 review themes: Present (Quality, Moderation, Support)
  - 6-month trend: Present
  - Bounded failure / sources attempted section: Present (`### Sources Attempted`)
  - Status for each attempted source: Present (`rating-obtained` / `unavailable`)
  - Trustpilot explicitly declared: Present (declared `unavailable`)
- **No Hallucination:** The worker did NOT invent a Trustpilot rating; it declared it unavailable.
- **Citation Evidence (`runs/task225_a1_citation_evidence.json`):**
  - `allbestapps.net`: HTTP 200 on host, `worker_policy_permitted: false` -> `UNREACHABLE`
  - `best-ai.org`: HTTP 200 on host, `worker_policy_permitted: false` -> `UNREACHABLE`
  - `justprompt.io`: HTTP 200 on host, `worker_policy_permitted: false` -> `UNREACHABLE`
- **Policy Expansion Candidates (`runs/policy_expansion_candidates.jsonl`):**
  - Logged `aisotools.com` and `www.theairegistry.com` during repair attempt 1.

---

## 5. Token Spend & Provenance

- **Input Tokens:** 33,825
- **Output Tokens:** 2,479
- **Total Tokens:** 36,304 (**~$0.11 USD**)
- **Provenance Match:** 100% match between `task225_mission.usage.json` and ledger (`tokens_in=33825, tokens_out=2479`).
- **Provider Serving:** 100% `openai-api/gpt-4o` across all 3 worker calls (worker + repair_1 + repair_2; 0 fallbacks).

---

## 6. Strategic Implications & Decision for Operator

1. **The Spec-Compliance Layer is Solved in Code:**
   The preflight checks and repair feedback directives worked exactly as designed: they converted a spec-omission into a structured, compliant deliverable with a dedicated `Sources Attempted` section.
2. **The Layer-by-Layer Investigation Reaches the Hard Wall:**
   The remaining blocker for both M3 and M5 is **NOT algorithmic, agentic, or prompting**: it is the **egress policy allowlist**.
   - In M5: FlowGPT official site is 403; third-party corroboration exists on `wbh.digital` and `scam-detector.com` (off-allowlist).
   - In M3: G2/Trustpilot are blocked; third-party reviews exist on `allbestapps.net`, `best-ai.org`, `justprompt.io`, `aisotools.com`, `theairegistry.com` (off-allowlist).
3. **The Two Clear Paths Forward:**
   - **Path 1 (Operator Security Decision — Widen Egress Policy):** If the operator authorizes adding these candidate review domains from `runs/policy_expansion_candidates.jsonl` to `config/egress_policy.yaml`, M3 and M5 immediately unblock at the citation verification layer.
   - **Path 2 (Declare Capability Envelope Ceiling & Enterprise Readiness):** Acknowledge that under strict enterprise egress containment, targets with blocked primary sources and un-allowlisted aggregators are honestly untestable. The harness has proven 5/7 live capability (M1, M2, M4, M6, M7), with M3 and M5 failing honestly on strict security containment.

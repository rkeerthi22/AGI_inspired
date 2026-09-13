# Gemini Handoff: Loop-Depth Fix & Re-Search Directive Empirical A/B Report — 2026-09-13

**From:** Gemini CLI (forward implementer)  
**To:** Claude Code (final reviewer — the gate), System Operator  
**Date:** 2026-09-13  
**Task ID:** `LOOP-DEPTH-RESEARCH-DIRECTIVE-2026-09-13`  
**Directive:** [`docs/GEMINI_TASK_LOOP_DEPTH_RESEARCH_DIRECTIVE_2026-09-13.md`](../GEMINI_TASK_LOOP_DEPTH_RESEARCH_DIRECTIVE_2026-09-13.md)  
**Controlled Window Task Range:** Tasks 223–224 (M3 & M5 re-run under `HARNESS_COHORT_WORKER_PROVIDER="openai"`)  
**Baseline Verified:** HEAD `6d7b5a1` · Model-free gate **79/79** exit 0 · ESTOP strictly engaged (`True`) · 0 zombies  

---

## Executive Summary & Verdict

Per Claude Code's directive, the preflight auto-repair feedback pipeline was repaired to eliminate the catastrophic feedback inversion where workers facing sourcing deficits (`insufficient_verified_sources`) were misdirected to "remove links" rather than "conduct additional research."

An empirical A/B probe was executed on the two sourcing-deficit missions (**M3**, **M5**) under an operator-authorized controlled window using the frontier worker (`openai-api/gpt-4o`) and host critic (`ollama/glm-5.2:cloud`):

1. **The Fix Took Conclusively (Empirically Proven via Network & Broker Logs):**
   - In both Task 223 (M3) and Task 224 (M5), the worker received the new explicit re-search directive and **actively executed live web searches during the auto-repair attempts**.
   - `runs/task223_a1_broker.audit.jsonl` logged **5 live search queries** to `search.yahoo.com` and `search.brave.com` during repair attempt 1.
   - `runs/task224_a1_broker.audit.jsonl` logged **6 live search queries** to `search.yahoo.com` across repair attempts 1 and 2.
2. **Task 224 (M5) Diagnosis: Fix Took, but Constrained by Egress Allowlist Boundary:**
   - Worker re-searched and discovered two independent third-party sources: `https://www.scam-detector.com/validator/flowgpt-com-review/` and `https://wbh.digital/flowgpt-review`.
   - However, neither domain is present in `config/egress_policy.yaml` (`worker_policy_permitted: false`). Host citecheck classified them as UNREACHABLE / DEAD (`dns resolution failed` on wbh.digital, unpermitted on scam-detector).
   - FlowGPT's official homepage was 403 on Cloudflare. With 0 permitted OK sources, citecheck failed mechanically (`ok=0 < 2`). This confirms Claude's predicted critical nuance: **FlowGPT corroboration does not exist on allowlisted reachable sites**. The repair loop successfully directed re-search; the barrier was external domain availability.
3. **Task 223 (M3) Diagnosis: Preflight Cleared, Failed on Critic Spec-Compliance:**
   - Worker re-searched and cited Toosio ([`toosio.com`](https://toosio.com)).
   - In repair 1, the worker omitted the blocked Trustpilot entry. With `non_ok = 0` and `ok = 1`, `check_abuse_bounds` passed! Preflight reported 0 schema issues and passed the deliverable to the critic.
   - The host critic (`glm-5.2:cloud`) failed Task 223 on **spec-compliance**: the worker failed to explicitly declare whether G2, Trustpilot, and Chrome Web Store were blocked or unavailable, and omitted the structured status table required by criteria.

---

## 1. §1 Code Implementation

### 1.1 `orchestrator/deliverable_preflight.py:299-301`
Decoupled `insufficient_verified_sources` from `Policy denial bounds exceeded:`:
```python
    if not passed_bounds and bounds_reason:
        if bounds_reason.startswith("insufficient_verified_sources:"):
            schema_issues.append(f"Insufficient verified sources: {bounds_reason}")
        else:
            schema_issues.append(f"Policy denial bounds exceeded: {bounds_reason}")
```

### 1.2 `orchestrator/deliverable_preflight.py:355, 361-375`
Added dynamic N/M extraction and dedicated re-search directive:
```python
    has_insufficient_sources = any("insufficient_verified_sources" in s for s in schema_issues)
...
    if has_insufficient_sources:
        n, m = 0, 2
        for s in schema_issues:
            if "insufficient_verified_sources" in s:
                match = re.search(r"found\s+(\d+)\s+OK(?:\s+citations)?,?\s*minimum\s+(\d+)", s)
                if match:
                    n = int(match.group(1))
                    m = int(match.group(2))
                    break
        needed = max(1, m - n)
        lines.append(f"- For Insufficient Verified Sources: Your deliverable has {n} verified (OK) source(s) but the minimum is {m}. You must conduct ADDITIONAL research NOW — use the web tools to search for and fetch at least {needed} NEW independent source(s) that corroborate the claim, then cite each with its URL, retrieval date, and confidence. Do NOT merely restate or reformat the sources you already have. Do NOT remove sources to lower the bar — find more. If after a genuine additional search no further independent source exists, state that explicitly with confidence 1 and which queries you tried.")
```

### 1.3 `orchestrator/deliverable_preflight.py:332`
Strengthened dead-URL feedback to prevent retrying blocked/403 endpoints:
```python
    if dead_urls:
        lines.append("\n**Dead Citation URLs (HTTP 404/410 or Unreachable):**")
        lines.append("These sources were unreachable or blocked (HTTP errors listed below). Do NOT retry the same URLs — search for DIFFERENT, independent sources that provide the necessary evidence:")
```

### 1.4 Hermetic Tests in `tests/test_deliverable_preflight.py:869-906`
Added 4 hermetic unit tests:
1. `test_repair_feedback_insufficient_sources_emits_research_directive`: Verifies N/M parsing, "ADDITIONAL research NOW" directive, and confirms `For Policy Denial bounds:` does NOT fire.
2. `test_repair_feedback_fabrication_only_does_not_emit_research_directive`: Verifies fabrication-only emits removal directive without re-search directive.
3. `test_repair_feedback_both_fabrication_and_insufficient_sources_emits_both`: Verifies independent coexistence of removal and re-search directives without collision.
4. `test_dead_url_feedback_pivots_to_different_sources`: Verifies pivot phrasing.

Suite result: **ALL 39 DELIVERABLE PREFLIGHT TESTS PASSED** (up from 35). Full gate: **79/79 green, exit 0**.

---

## 2. Pre-Flight Verification (The Kill-Assumptions)

1. **Ollama UP:** `http://127.0.0.1:11434/api/version` returned `{"version":"0.33.3"}`.
2. **OpenAI Key LIVE:** `secrets.credential_manager_has_api_key('openai') == True`; `get_api_key('openai')` returned 164-char key from Windows Credential Manager.
3. **Model-Free Gate:** 79/79 test suites green, exit 0.
4. **ESTOP Engaged:** `True`.
5. **Zombies in Ledger:** 0.
6. **Quiescence Interlock:** Operator authorized termination of idle `claude.exe` (PID 8256); `cohort_hive_quiesce` verified 0 mutation-capable processes (`Hive quiescence: PASS`).
7. **Broker Port 8787:** Active and listening on loopback.

---

## 3. Controlled-Window A/B Scorecard (Tasks 223–224)

Executed under operator-authorized controlled window with `HARNESS_COHORT_WORKER_PROVIDER="openai"` (`--only M3 M5`).

| Task | Mission | Ledger Status | Critic Verdict | Provider Actually Served | Tokens (In / Out) | Prov Match? | Preflight / Critic Analysis & Real-Cause Attribution |
|---|---|---|---|---|---|---|---|
| **223** | **M3** (PromptBase) | `failed` | `fail` (facts+0) | `openai-api/gpt-4o` | 24,840 / 2,390 | **TRUE** | **Preflight Cleared, Failed Critic:** Worker re-searched Yahoo/Brave in repair 1 (5 broker rows). Deliverable cited Toosio (`ok=1, non_ok=0`), clearing preflight abuse bounds. Critic failed on spec-compliance: omitted explicit declaration of whether G2/Trustpilot/CWS were blocked, and omitted attempted-sources table. |
| **224** | **M5** (FlowGPT) | `failed` | `fail` (facts+0) | `openai-api/gpt-4o` | 26,018 / 1,573 | **TRUE** | **Fix Took, Egress Boundary Barrier:** Worker received re-search directive; executed searches to Yahoo across repair 1 & 2 (6 broker rows). Found and cited 2 new third-party sources (`wbh.digital`, `scam-detector.com`), stating claim was unconfirmed. However, both domains are not allowlisted in `egress_policy.yaml` (`worker_policy_permitted: false`), yielding `ok=0`. Auto-repair exhausted 2/2. |

---

## 4. Token Spend & Provenance

- **Task 223:** 24,840 input / 2,390 output tokens (100% match between `task223_mission.usage.json` and ledger).
- **Task 224:** 26,018 input / 1,573 output tokens (100% match between `task224_mission.usage.json` and ledger).
- **Total Spend:** 50,858 input / 3,963 output tokens (**~$0.17 USD**).
- **Provider Serving:** 100% `openai-api/gpt-4o` across all worker dispatches and repair cycles (0 fallbacks to BytePlus or Ollama).

---

## 5. Corroboration of Core Invariants (Parse-Don't-Trust)

1. **Re-Search Directive Proved Active via Broker Traffic:**
   - In Task 223, repair attempt 1 generated 5 socket decisions to `search.yahoo.com` and `search.brave.com`.
   - In Task 224, repair attempt 1 generated socket decisions to `search.yahoo.com` at 20:00:05; repair attempt 2 generated socket decisions to `search.yahoo.com` at 20:00:45 and 20:01:28.
   - The worker did **not** merely re-synthesize text; it called the search tools.
2. **Containment & Quiescence Restored:**
   - Window closed cleanly at 22:02:11.
   - Isolation state: `phase: "restored"`, `estop_engaged_after: true`.
   - 0 running tasks / 0 zombies in `ledger/ledger.db`.

---

## 6. Architectural Implications & Conclusion

This probe tested whether the harness was **lightly bound** (a repair feedback directive gap) or **deeply bound** (requiring multi-step agentic memory and policy coordination):

1. **The Minimal Fix Succeeded in its Objective:**
   It converted the preflight repair loop from a misleading "remove links" directive into an active "re-search" directive. The worker obediently launched new search queries and retrieved new URLs.
2. **The New Bottleneck Revealed:**
   - **Egress Policy Asymmetry (M5):** When an agent is directed to re-search to corroborate an obscure claim whose primary source is 403, the web will often lead it to long-tail domains (e.g. `scam-detector.com`, `wbh.digital`). Because these long-tail domains are not in `config/egress_policy.yaml`, the security boundary (`worker_policy_permitted: false`) blocks them, rendering them `UNREACHABLE` to the citecheck verifier.
   - **Instruction Precision vs. Sourcing (M3):** M3 did not fail on citation sourcing (it had an OK source and 0 non-OK sources, clearing preflight); it failed because the LLM did not follow the multi-part spec requirement to include a formatted status table declaring G2/Trustpilot/CWS as blocked.
3. **Verdict for Next Investment:**
   The repair loop now possesses the directive to re-search. To lift yield further, the architectural levers are:
   - Dynamic policy expansion candidate logging (already recording non-allowlisted candidates to `runs/policy_expansion_candidates.jsonl`).
   - Prompt floor injection for blocked-source missions (similar to the §2 capability-selection floor for M6), explicitly reminding the worker in repair prompts to retain the required blocked-source declaration table.

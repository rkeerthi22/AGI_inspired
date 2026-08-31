# Universal Cross-Agent Handoff Protocol

**Version:** 1.0 (2026-08-30)  
**Applicability:** Universal standard for Gemini, DeepSeek/Cade, Claude, Codex, Hermes, and the Human Operator.

---

## 1. Core Principles

1. **Repository is Canonical:** The Git repository is the single source of truth. Obsidian, external dashboards, and chat transcripts are derived human-facing views and never override repository state.
2. **Evidence Over Assertion:** Never claim completion without deterministic evidence (file paths, test suite exit codes, commit SHAs, trajectory logs).
3. **Strict Single-Writer Ownership:** Before editing any file, check [`docs/ACTIVE_WORK.json`](ACTIVE_WORK.json). Only the agent owning the declared subsystem scope may write to it in the main working tree.
4. **Zero Live Calls Without Authorization:** ESTOP defaults to engaged (`True`). Model-free tests and audits must never trigger un-gated live provider calls.

---

## 2. Universal Handoff Template

Every agent must produce or update a handoff document conforming to the following structure upon session completion, context compaction, or being blocked:

```markdown
# [AGENT NAME] Handoff — [TASK TITLE]

**Agent:** [e.g. Gemini CLI / DeepSeek-V4-pro / Claude Code / Codex]  
**Role:** [e.g. Independent Principal Architect / Core Runtime Implementer]  
**Timestamp:** [ISO 8601 UTC / Local]  
**Git HEAD:** [e.g. cb5d289]  
**Working Tree Status:** [e.g. Clean / 19 Modified / Untracked files listed]  
**Current Task ID:** [e.g. COHORT-VALIDATION-2026-08-30]  
**Task Status:** [COMPLETE / BLOCKED / IN_PROGRESS / INFRA_FAILED]  

---

### 1. Files Read
* `path/to/file1`
* `path/to/file2`

### 2. Files Changed & Created
* `path/to/file3` (Description of change and non-obvious rationale)
* `path/to/file4` (New module purpose)

### 3. What Was Done
* Bulleted list of concrete technical work completed.

### 4. What Was NOT Done / Explicit Non-Actions
* Deliberate omissions, deferred items, or anti-patterns avoided.

### 5. Test Evidence
* **Targeted Suite:** `python tests/test_*.py` -> `PASS (N/N assertions)`
* **Full Test Gate:** `python tests/run_all.py` -> `41/41 suites green`
* **Continuity Validation:** `python orchestrator/continuity.py validate` -> `PASS`

### 6. Safety & Runtime State
* **ESTOP State:** `Engaged (True)` / `Disengaged (False)`
* **Transactional Isolation Window:** `Restored` / `Active`
* **Schedulers & Hermes Gateway:** `Protected` / `Active`
* **Upstream Provider Quota Status:** `BytePlus (HTTP 429 reset @ 22:22 CEST)`

### 7. Live Model Calls Made
* **Live Calls Made:** `YES / NO`
* **Provider:** `byteplus_coding / ollama`
* **Purpose / Scope:** `Single canary connectivity probe`

### 8. Known Blockers
* Specific upstream or environmental impediments.

### 9. Exact Next Action
* Single, unambiguous next command or action for the resuming agent.

### 10. Explicit Do-Not-Do Directives
* Directives regarding files not to edit, live runs to avoid, or invalid transitions.

### 11. Artifact & Log Pointers
* `runs/task{id}.trajectory.jsonl`
* `workspace/validation/cohort_summary.json`
```

---

## 3. Mandatory Checkpoints for Resuming Agents

When resuming, any agent must follow this exact 6-step sequence:
1. Read universal entry point [`AGENTS.md`](file:///S:/AGI_like/AGENTS.md).
2. Read the Compact Brief [`.harness/continuity/current.json`](file:///S:/AGI_like/.harness/continuity/current.json).
3. Check [`docs/ACTIVE_WORK.json`](file:///S:/AGI_like/docs/ACTIVE_WORK.json) to verify write ownership.
4. Read [`docs/CURRENT_STATE.md`](file:///S:/AGI_like/docs/CURRENT_STATE.md).
5. Read the latest relevant handoff in `docs/`.
6. Run `python orchestrator/continuity.py recover` and `python tests/run_all.py`.

---

## 4. Pre-M1 Integration Baseline — 2026-08-31

The pre-M1 integration blocker is resolved. The trajectory append/resumption repair is complete and model-free tests are isolated from production `runs/`. Munder coordination state is isolated outside the repository at `S:\MunderState\AGI_like`, while `S:\AGI_like` remains its registered canonical repository. No agent holds an active AGI write scope.

Verified baseline:

* 41/41 model-free suites pass.
* ESTOP remains engaged and transactional isolation is restored.
* No batch lock or live provider execution is active.
* Munder Hive, roster, backups, Palace state, and future worktrees are external to the AGI repository.
* The AGI working tree is clean after the baseline commits.

**Exact next action:** The Operator may run exactly one authorized BytePlus connectivity canary. Do not run M1–M7 unless that canary returns HTTP 200 and the Operator authorizes the controlled execution window.

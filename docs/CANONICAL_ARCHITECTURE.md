# Canonical Architecture Specification — AGI_like Harness

**Status:** Authoritative Architectural Standard  
**Revision:** 2026-08-30  
**Target Audience:** All Autonomous Agents (Gemini, DeepSeek, Claude, Codex, Hermes) & Human Operators

### Current Revision Note (2026-09-04)

The current control plane includes fail-closed operator marker trust,
Credential Manager provider routing, authoritative model-free release
preflight, F110 blocked-vs-dead citation classification, A5 early-abort
diagnostics, truthful failover reasons, and hash-linked trajectory events.
These controls are local and regression-tested; they are not a substitute for
restricted worker identity, engine-independent egress enforcement, hashed
dependency artifacts, or off-machine audit retention. The roadmap below is
read with this current-state note taking precedence over stale checkboxes.

---

## 1. Architectural System Overview

The AGI_like harness is an autonomous cognitive execution environment designed to execute complex research, business intelligence, and software engineering missions with measurable self-improvement, deterministic safety containment, and strict provenance tracking.

```
┌────────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE                                                          │
│  • ESTOP (execution_pause.py)   • Runlock (runlock.py)                 │
│  • Continuity (.harness/)       • Transactional Window (isolation.py)  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ TASK PIPELINE (task_runner.py / workflow.py)                           │
│  1. Admission & Budgeting ──► 2. Worker with Failover (execution.py)   │
│  3. Retrieval Controller   ──► 4. Tool-Free Finalizer (F63)            │
│  5. Mechanical Citecheck   ──► 6. LLM Critic Evaluation                │
│  7. Memory Fact Update     ──► 8. Ledgerbook & Scorecard Persistence   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ TELEMETRY & OBSERVABILITY                                              │
│  • Unified Append-Only Trajectory Stream: runs/task{id}.trajectory.json│
│  • Redacted Secrets & Deterministic Event Monotonicity                 │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ MODEL & PROVIDER TRANSPORT                                             │
│  • Typed Transport: provider_chat.py (ModelArk / Ollama / Local)       │
│  • Secure Secret Extraction: Hermes .env isolated bridge               │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Detailed Subsystem Specifications

### A. Control Plane & Safety Invariants
* **ESTOP (`orchestrator/execution_pause.py`):** Global emergency stop mechanism. When engaged (`True`), all model dispatches and CLI entry points fail closed (Exit Code 75).
* **Runlock (`orchestrator/runlock.py`):** Process-level execution lock ensuring single-instance harness execution and preventing race conditions on SQLite databases.
* **Transactional Isolation Window (`workspace/validation/cohort_isolation.py`):** Encapsulates live validation cohorts. Disables Windows scheduled tasks and Hermes background cron jobs, enters controlled ESTOP bypass, executes the designated task, and strictly restores ESTOP and dispatchers in a `finally` block before process exit.
* **Database Mutation Guard (`orchestrator/integrity.py`):** Context manager that verifies database row counts before and after worker execution, raising `DatabaseMutationViolation` if a worker attempts direct database modification.

### B. Task Pipeline Lifecycle
1. **Admission & Estimation:** Evaluates daily token budgets and estimated spends (`policy.py`) before scheduling task execution.
2. **Worker Execution & Failover (`orchestrator/execution.py`):** Dispatches the worker role through an ordered provider hierarchy (`config/models.yaml`). Automatically detects upstream 429 quota exhaustion in stdout/stderr and advances failover rungs without harness crash.
3. **Retrieval Progress Controller (`orchestrator/retrieval_progress.py`):** Enforces an external, non-bypassable strategy pipeline (`search` $\to$ `direct_fetch` $\to$ `browser` $\to$ `partial_result`) to prevent circular spinning.
4. **Tool-Free Finalization:** Synthesizes collected evidence into a structured deliverable markdown with explicit provenance.
5. **Mechanical Citation Validation (`orchestrator/citecheck.py`):** Independently fetches all cited URLs to verify live accessibility and exact literal text matches prior to LLM review.
6. **Critic Evaluation (`orchestrator/evaluation.py`):** Evaluates content substance against pre-declared mission pass criteria.
7. **Memory Extraction & Persistence:** PASSED deliverables trigger idempotent fact extraction into `memory/ledgerbook.db`, while task outcomes are written to `ledger/ledger.db`.

### C. Unified Trajectory Event Stream (`orchestrator/trajectory.py`)
* **Output File:** `runs/task{id}.trajectory.jsonl`
* **Schema Version:** `1`
* **Event Structure:**
  - `event_id`: Deterministic `evt-{task_id}-{sequence:04d}`
  - `sequence`: Monotonically increasing 1-based integer per task.
  - `timestamp`: ISO 8601 UTC timestamp.
  - `stage`: `lifecycle`, `execution`, `evaluation`.
  - `event_type`: `task_started`, `provider_selected`, `failover_attempted`, `tool_call_finished`, `citecheck_completed`, `critic_evaluated`, `task_completed`, `task_failed`.
  - `payload`: Structured event parameters with deep recursive secret redaction for API keys and Bearer tokens.

### D. Provider Transport Layer (`orchestrator/provider_chat.py`)
* Enforces a typed provider interface across BytePlus ModelArk (`byteplus_coding`), Ollama Cloud (`ollama`), Anthropic (`anthropic`), OpenAI (`openai`), and local models.
* Credentials are securely resolved via `_secure_env_value()` from Hermes private environment configuration (`%LOCALAPPDATA%\hermes\.env`), keeping secrets out of repository files and logs.

---

## 3. Prioritized Architecture Roadmap

```
┌────────────────────────────────────────────────────────────────────────┐
│ P0 (CURRENT / ACTIVE):                                                 │
│   [x] Unified Trajectory Event Stream (.trajectory.jsonl) [VERIFIED]  │
│   [x] M1 Criteria Harmonization for Bounded Private Gaps [APPROVED]    │
│   [x] Supervised BytePlus connectivity canary [VERIFIED LIVE]          │
│   [x] Controlled task 110 rerun reached the next real blocker          │
│   [ ] Repair Anthropic unusable-output failover path for task 110      │
│   [ ] Resume separately authorized frozen cohort windows (M3–M7)       │
├────────────────────────────────────────────────────────────────────────┤
│ P1 (PRE-CODING AUTONOMY):                                              │
│   [ ] Minimal F63 Fallback Search (10-line HTTP 4xx/5xx relaxation)    │
│   [ ] Ephemeral Git Worktree Sandboxing (Isolated branch workspaces)   │
├────────────────────────────────────────────────────────────────────────┤
│ P2 (SCALE & ADVANCED CAPABILITIES):                                    │
│   [ ] MCP Standard Tool Provider Layer                                 │
│   [ ] Specialist Subagent Delegation with Isolated Context Windows     │
│   [ ] Tree-Sitter AST Repository Graph Mapping                         │
└────────────────────────────────────────────────────────────────────────┘
```

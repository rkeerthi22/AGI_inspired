# Gemini Independent Audit & Architecture Review — AGI_like Harness

**Date:** 2026-09-05  
**Auditor / Reviewer:** Gemini CLI (Google DeepMind Agentic Assistant / Independent Principal Architect)  
**Target Commit:** `2bfabe1` on `master` (9 commits ahead of `origin/master`)  
**Verified Gate:** Model-free gate `python -B tests/run_all.py` -> **70/70 green** (exit 0)  
**Safety Status:** ESTOP engaged (`True`) | Zero live execution active | Runlock absent | Working tree clean  

---

## 1. Executive Summary

This document provides a comprehensive architectural evaluation, multi-lens audit, and rating of the **AGI_like** cognitive harness following the verification of:
1. **F119:** Full-history replica verification (`orchestrator/audit_replication.py`).
2. **F120:** Whole-transaction OS-backed sidecar locking via `portalocker` (`orchestrator/audit_replication.py`, `tests/test_audit_serialization.py`).
3. **Harmonized Bootstrap & State Sync:** Master commit `2bfabe1` synchronizing agent instructions with current gate metrics (70/70 suites).

The harness represents an **exceptionally rigorous research and control prototype** (rated **8.0 / 10** overall). Its safety containment, adversarial regression testing, multi-agent concurrency mutexing, and provenance tracking surpass industry standards. However, it is not yet an enterprise-ready release; significant gaps remain in host OS process fencing, worker vs. signer identity separation, and real-world task pass rates (currently 1/6 on live cohort validation).

---

## 2. System Overview & Core Architecture

The AGI_like harness is designed as an autonomous, self-improving AI research / business intelligence analyst ("Milestone 1"). It replaces stateless prompt-response wrappers with a deterministic, closed-loop execution lifecycle:

```
┌────────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE                                                          │
│  • ESTOP (orchestrator/execution_pause.py - fail closed Exit 75)       │
│  • Runlock (orchestrator/runlock.py - process mutex)                   │
│  • Continuity Brief (.harness/continuity/current.json - recovery hint) │
│  • Active Work Registry (docs/ACTIVE_WORK.json - path-level locks)     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ TASK PIPELINE (orchestrator/task_runner.py, workflow.py)               │
│  1. Admission & Estimation (orchestrator/policy.py)                    │
│  2. Hierarchical Worker Execution & Failover (orchestrator/execution.py│
│  3. Retrieval Progress Controller (orchestrator/retrieval_progress.py) │
│  4. Tool-Free Finalizer (Markdown synthesis deliverable)               │
│  5. Mechanical Citation Validation (orchestrator/citecheck.py)         │
│  6. Independent LLM Critic Evaluation (orchestrator/evaluation.py)     │
│  7. Memory Fact Extraction (memory/ledgerbook.db - SQLite FTS5)        │
│  8. Task Outcome Logging (ledger/ledger.db - append-only)              │
│  9. Operator-Approved Skill Promotion (skills_analyst/ & promote.py)   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ CRYPTOGRAPHIC PROVENANCE & AUDIT REPLICATION                           │
│  • Trajectory Event Stream: runs/task{id}.trajectory.jsonl             │
│    (prev_event_hash / event_hash chaining)                             │
│  • Remote Replica Sync: orchestrator/audit_replication.py              │
│    (whole-transaction portalocker mutex, HMAC-SHA256 signature chains) │
│  • Windows Credential Manager Vaulting (ARK_API_KEY)                   │
│  • SHA-256 Dependency Hash-Locking (orchestrator/dependency_integrity) │
└────────────────────────────────────────────────────────────────────────┘
```

Missions are evaluated against a fixed fitness formula:
$$F = 0.35 \cdot \text{completion} + 0.30 \cdot \text{accuracy} + 0.25 \cdot (1 - \text{intervention}) + 0.10 \cdot \text{cost\_eff}$$

A secondary subsystem, `prediction_machine/`, operates daily automated data ingestion, time-series forecasting, and probability calibration.

---

## 3. Comprehensive 5-Lens Evaluation & Scorecard

| Subsystem / Lens | Rating | Detailed Assessment |
| :--- | :---: | :--- |
| **1. Safety & Containment Architecture** | **9.2 / 10** | Dual-layer ESTOP fails closed across all model dispatch points; DB mutation guard detects rogue writes and deletes unapproved delta rows; mechanical citecheck verifies real HTTP response codes; telemetry redacts secrets recursively. |
| **2. Multi-Agent Governance & Concurrency** | **8.8 / 10** | Single-writer path locks in `docs/ACTIVE_WORK.json` and process-level `runlock.py` have prevented split-brain collisions across 5 autonomous agent types (Gemini, Claude Code, Codex, DeepSeek, Hermes). The MVGA (Minimum Viable Governance Architecture) has effectively curbed historical documentation churn. |
| **3. Verification & Regression Gate Rigor** | **9.5 / 10** | 70/70 test suites green covering unit, containment (disposable Git worktrees), and integration tiers. Empirical incident tracking in `docs/INCIDENTS.md` and 120 pinned fixes in `docs/HARDENING.md` provide world-class defensibility. |
| **4. Autonomous Execution & Model Yield** | **6.5 / 10** | Robust upstream 429 quota failover handling. However, live cohort yield remains low (1/6 pass rate during Sept 3 run). Susceptible to upstream provider quota cliffs (BytePlus/Ollama) and unconfigured Anthropic/OpenAI keys. |
| **5. Enterprise Host Isolation & Deployment** | **5.8 / 10** | The critical gap. Workers execute in parent Windows token context without true OS AppContainer or Windows Firewall rule enforcement. Remote UNC replication is verified locally via temp mocks, but multi-node SMB fencing and independent restore drills are unproven. |
| **Composite Harness Rating** | **8.0 / 10** | **Outstanding Research / Control Prototype**. Exceptional rigor and architectural defensibility; pending host OS infrastructure and service separation for production enterprise readiness. |

---

## 4. Key Open Findings & Architectural Blockers

1. **Host Security Boundaries & Identity:**
   - Worker processes currently inherit the parent environment token.
   - True OS-level execution fencing (Windows AppContainer or low-privilege service account + outbound firewall block) remains an operator-side deployment prerequisite.
2. **Signer / Worker Separation:**
   - Trajectory signing currently requires operator Credential Manager access in the executing process.
   - An isolated signing daemon/service is required so untrusted workers cannot access signing credentials.
3. **Runtime Release Admission Contract:**
   - `operator_cli.py preflight release` verifies release prerequisites, but `batch_runner.py` only checks ESTOP and basic integrity.
   - The batch execution path must directly enforce release admission so tasks cannot run if prerequisites (egress attestation, remote audit, dependency locks) fail.
4. **Live Cohort Validation:**
   - Only mission M4 passed in the recent frozen cohort. Missions M3, M5, M6, and M7 require supervised execution once upstream provider quotas allow.

---

## 5. Summary of Recent Fixes (F119 & F120)

* **F119 (Full-History Verification):** Fixed `orchestrator/audit_replication.py` to inspect stored SHA-256 hashes of *all* historical trajectory files on the replica store, failing closed if any historical artifact is modified or deleted.
* **F120 (Audit Serialization):** Implemented whole-transaction OS-backed sidecar locking (`portalocker`) around tip-read, artifact copy, signing, and append in `replicate_trajectory()`. Reordered historical verification ahead of copy to prevent retry runs from silently recreating deleted historical replicas. Validated by `tests/test_audit_serialization.py` (8/8).

# DEEPSEEK.md — Agent Instructions for DeepSeek-V4-pro

**Agent Identifier:** DeepSeek-V4-pro / Cade  
**Default Role:** Core Runtime & Infrastructure Implementer  
**Canonical Rules & Bootstrap:** Follow [`AGENTS.md`](AGENTS.md) strictly.

---

## 1. Startup & Bootstrap Procedure
1. Follow the universal 8-step sequence in [`AGENTS.md`](AGENTS.md).
2. Check [`docs/ACTIVE_WORK.json`](docs/ACTIVE_WORK.json) for current task ownership.
3. Review [`docs/CANONICAL_ARCHITECTURE.md`](docs/CANONICAL_ARCHITECTURE.md) and [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md).
4. Review the latest active handoff in `docs/` ([`docs/CODEX_HANDOFF_2026-09-05_AUDIT_SERIALIZATION.md`](docs/CODEX_HANDOFF_2026-09-05_AUDIT_SERIALIZATION.md)).

---

## 2. Hard Behavioral Constraints
* **ESTOP Discipline:** ESTOP defaults to engaged (`True`). Never modify pause sentinels directly.
* **Single Write Scope:** Focus implementation work on assigned paths (`orchestrator/`, `tests/`, `workspace/validation/`).
* **Deterministic Verification:** Ensure the model-free test gate (`python -B tests/run_all.py` -> dynamic suite count, currently 70/70 green, exit 0) and continuity validation (`python orchestrator/continuity.py validate` -> 0 errors) pass.
* **Controlled Canary Rerun:** When BytePlus quota resets (~22:22 CEST), run the single connectivity canary before dispatching `run_cohort.py --controlled-window --only M1`.
* **Handoff Compliance:** Update your handoff document following [`docs/HANDOFF_PROTOCOL.md`](docs/HANDOFF_PROTOCOL.md).

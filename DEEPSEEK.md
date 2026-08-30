# DEEPSEEK.md — Agent Instructions for DeepSeek-V4-pro

**Agent Identifier:** DeepSeek-V4-pro / Cade  
**Default Role:** Core Runtime & Infrastructure Implementer  
**Canonical Rules & Bootstrap:** Follow [`AGENTS.md`](AGENTS.md) strictly.

---

## 1. Startup & Bootstrap Procedure
1. Follow the universal 8-step sequence in [`AGENTS.md`](AGENTS.md).
2. Check [`docs/ACTIVE_WORK.json`](docs/ACTIVE_WORK.json) for current task ownership.
3. Review [`docs/CANONICAL_ARCHITECTURE.md`](docs/CANONICAL_ARCHITECTURE.md) and [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md).
4. Review your active implementation dossier in [`docs/DEEPSEEK_HANDOFF_2026-08-30.md`](docs/DEEPSEEK_HANDOFF_2026-08-30.md).

---

## 2. Hard Behavioral Constraints
* **ESTOP Discipline:** ESTOP defaults to engaged (`True`). Never modify pause sentinels directly.
* **Single Write Scope:** Focus implementation work on assigned paths (`orchestrator/`, `tests/`, `workspace/validation/`).
* **Deterministic Verification:** Ensure all 41 test suites (`python tests/run_all.py`) and continuity validation (`python orchestrator/continuity.py validate`) pass.
* **Controlled Canary Rerun:** When BytePlus quota resets (~22:22 CEST), run the single connectivity canary before dispatching `run_cohort.py --controlled-window --only M1`.
* **Handoff Compliance:** Update [`docs/DEEPSEEK_HANDOFF_2026-08-30.md`](docs/DEEPSEEK_HANDOFF_2026-08-30.md) following [`docs/HANDOFF_PROTOCOL.md`](docs/HANDOFF_PROTOCOL.md).

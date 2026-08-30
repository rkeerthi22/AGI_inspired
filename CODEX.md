# CODEX.md — Agent Instructions for Codex & OpenAI Models

**Agent Identifier:** Codex / OpenAI CLI  
**Default Role:** Specialist Task Worker / Code Refactoring Specialist  
**Canonical Rules & Bootstrap:** Follow [`AGENTS.md`](AGENTS.md) strictly.

---

## 1. Startup & Bootstrap Procedure
1. Follow the universal 8-step sequence in [`AGENTS.md`](AGENTS.md).
2. Check [`docs/ACTIVE_WORK.json`](docs/ACTIVE_WORK.json) for current task ownership.
3. Review [`docs/CANONICAL_ARCHITECTURE.md`](docs/CANONICAL_ARCHITECTURE.md) and [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md).

---

## 2. Hard Behavioral Constraints
* **ESTOP Discipline:** Never disengage ESTOP without an authorized `--controlled-window` execution path.
* **Single Write Scope:** Never edit files outside your assigned write scope in `docs/ACTIVE_WORK.json`.
* **Deterministic Verification:** Ensure all 41 test suites (`python tests/run_all.py`) and continuity validation (`python orchestrator/continuity.py validate`) pass before completing work.
* **Handoff Compliance:** Update your handoff document following [`docs/HANDOFF_PROTOCOL.md`](docs/HANDOFF_PROTOCOL.md) upon session end or context compaction.

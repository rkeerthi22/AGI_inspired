# CLAUDE.md — Agent Instructions for Claude & Cade

**Agent Identifier:** Claude Code / Cade  
**Default Role:** Specialist Task Worker / Autonomous Worktree Operator  
**Canonical Rules & Bootstrap:** Follow [`AGENTS.md`](AGENTS.md) strictly.

---

## 1. Startup & Bootstrap Procedure
1. Follow the universal 8-step sequence in [`AGENTS.md`](AGENTS.md).
2. Check [`docs/ACTIVE_WORK.json`](docs/ACTIVE_WORK.json) for current task ownership.
3. Review [`docs/CANONICAL_ARCHITECTURE.md`](docs/CANONICAL_ARCHITECTURE.md) and [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md).
4. Review your active handoff in [`docs/DEEPSEEK_HANDOFF_2026-08-30.md`](docs/DEEPSEEK_HANDOFF_2026-08-30.md).

---

## 2. Hard Behavioral Constraints
* **ESTOP Discipline:** Never disengage ESTOP without an authorized `--controlled-window` execution path.
* **Single Write Scope:** Never edit files outside your assigned write scope in `docs/ACTIVE_WORK.json`.
* **Zero Live Runs During Quota Blocks:** Respect upstream provider quota limits (BytePlus HTTP 429).
* **Deterministic Testing:** Ensure `python tests/run_all.py` reports all suites green with zero `[FAIL]` lines before handoff (count is dynamic, currently 55 — never match a hardcoded number).
* **Handoff Compliance:** Update your handoff document following [`docs/HANDOFF_PROTOCOL.md`](docs/HANDOFF_PROTOCOL.md) upon session end or context compaction.

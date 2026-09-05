# Gemini Independent Audit & Review — AGI_like Harness at HEAD 3c5604b

**Date:** 2026-09-05  
**Reviewer:** Gemini (Google DeepMind Agentic Assistant / Gemini CLI)  
**Role:** Independent Principal Architect, Reviewer & Documentation Authority  
**Target Git HEAD:** `3c5604b` (10 commits ahead of origin/master)  
**Baseline Verified:** Model-free gate 67/67 green, continuity rev 61 clean, ESTOP engaged (`True`), zero live execution active.

---

## Executive Summary
This document provides:
1. The **Comprehensive 4-Lens Evaluation** (Architect, Security, DevOps, Quality/Reliability) of commit `aa5afaf` / HEAD `3c5604b`.
2. The **10-Question Multi-Agent Governance System Audit** analyzing overhead, duplication, conflicts, and the Minimum Viable Governance Architecture (MVGA).

---

# PART I: Comprehensive Evaluation Across the Four Specialist Lenses

### 1. ARCHITECT Lens — Architecture, Failure Domains & Design Quality
* **Release preflight is diagnostic; batch runtime bypasses it:** `operator_cli.py:896` returns `diagnostic_only=true` and `authorized=false`. The live execution path in `batch_runner.py:141` checks ESTOP and then calls `integrity.preflight()`, skipping release checks (egress attestation, remote audit, dependency lock). A human can clear ESTOP and run tasks without full release preflight enforcement.
* **Worker identity boundary is un-implemented by launcher:** `execution.py:91` spawns workers via `subprocess.Popen` in parent token context. The Job Object in `pty_daemon.py:225` handles only process teardown (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`), not security isolation or credential restriction. Filesystem containment is post-hoc detection.
* **Audit signing vs. worker isolation conflict:** Remote audit replication requires operator Credential Manager access (`audit_signing.py:25`), but deployment requires workers to have no operator keys. A separate controller/signing daemon is missing.
* **Remote audit replication lacks atomic concurrency guard:** `audit_replication.py:262-281` reads the tip checkpoint and appends without a file lock or compare-and-swap. Concurrent completions can fork the remote chain.
* **Historical artifact verification gap in `audit_state()`:** `audit_replication.py:322-327` validates checkpoint signatures across the entire chain, but validates the stored artifact SHA-256 hash for **only the latest checkpoint**. Corrupted or deleted historical trajectory files on the UNC replica are not detected.
* **Single point of failure in independent critic routing:** In `models.yaml`, the critic provider is solely `byteplus_coding`. If BytePlus 429s, all tasks fall to manual `needs_review` with no automated fallback critic.
* **Swallowed migration errors:** `migrations.py:269` returns `(-1, -1)` on error; `batch_runner.py:149` logs this without aborting, allowing execution against incompatible schemas.

### 2. SECURITY ENGINEER Lens — Red-Team Assessment & Attack Surfaces
* **Worker inherits full parent environment:** Child processes inherit `os.environ` before proxy settings are injected. Any raw-socket process bypasses proxy routing unless blocked at the OS level.
* **Attestation token vs. live host disconnect:** `egress_policy.py:180` verifies token signature and claim keys, but never verifies that the running process matches `worker_identity` or that Windows Firewall rules are active. It is a bearer assertion, not active proof.
* **Untested socket relay in `egress_broker.py`:** `egress_broker.py:66-85` uses raw `select.select()`. An empty read (`FIN` half-close) terminates both sockets immediately. The relay logic has zero integration tests; `test_egress_policy.py` only tests policy string parsing.
* **Filesystem containment bypassed on abnormal exit:** In `task_runner.py:408-417`, launch exceptions catch and return `"infra_failed"`. `integrity.fs_integrity_check()` is situated *after* this block and never runs on abnormal worker termination.

### 3. DEVOPS & DEPLOYMENT SPECIALIST Lens — Windows Deployment & Release Ceremonies
* **Under-automated deployment ceremony:** `operator_cli.py` checks blockers but provides no tooling to generate the signed egress token from host state, provision firewall rules, initialize the UNC audit share, or perform restore drills.
* **Upstream Git divergence:** Local `master` is 10 commits ahead of `origin/master` (`0701dc5..3c5604b`). Push is a required release action before external CI can verify HEAD.
* **Unproven clean-machine dependency install:** Dependency hash verification in `scripts/requirements.txt` has only run locally. A clean ephemeral GitHub Actions run with `--require-hashes` is unverified.

### 4. QUALITY & RELIABILITY ASSESSOR Lens — Test Topology & Operational Evidence
* **"67/67 Green" is script-level smoke testing:** Suites run without timeout bounds, flaky detection, or code coverage measurement. Several components (`egress_broker.py`, critic routing, UNC shares) are heavily stubbed.
* **`cohort_summary.json` overwrite:** Running `run_cohort.py --only <mission>` overwrites `runs/cohort_summary.json`, destroying historical aggregates for prior missions.
* **Task retries overwrite attempt artifacts:** When a task retries, artifact filenames (`task{tid}_deliverable.md`, `task{tid}_worker_raw.txt`) overwrite in place, erasing intermediate failure telemetry and recovery latency metrics.
* **Real-world cohort remains 1/6:** No live cohort run has been conducted since RC-1 (F110) landed; live pass rate remains at 1/6 on disk.

---

# PART II: 10-Question Multi-Agent Governance Audit

### Q1: Minimum Information Required for a New Agent to Resume Safely
1. **Next Action & Open Task:** Task ID, phase, and exact next step (from `.harness/continuity/current.json`).
2. **Write Scope & Concurrency Lock:** Active paths owned vs locked (from `docs/ACTIVE_WORK.json`).
3. **Safety Invariants:** ESTOP engaged (`True`), model-free restrictions (from `AGENTS.md` & `current.json`).
4. **Recent Git Context:** Head SHA and last 3 commit summaries (from `git log -n 3 --oneline`).
5. **Dangling Blockers:** Any interrupted state or usage limits.
*Redundant Files:* `docs/CURRENT_STATE.md` (narrative lag), 21 historical handoffs in `docs/`, `CANONICAL_ARCHITECTURE.md` (static philosophy), duplicate Obsidian mirror.

### Q2: System-Wide Duplication Mapping
* **Quantified Volume:** 44 markdown files in `docs/` (665 KB), including 21 handoff files (158 KB).
* **Duplication Points:**
  - Gate status (`67/67 green`) repeated across 6 locations.
  - ESTOP status repeated across 6 locations.
  - Task next actions repeated across 5 locations.
* **Verdict:** Useful: `git log`, `ACTIVE_WORK.json`, `Fix Registry.md`. Unnecessary sync work: `CURRENT_STATE.md`, `current.json`, handoff files, and `S:\ObsidianVault\Handoffs\agi-like\HANDOFF.md`.

### Q3: Token, Time, and Attention Costs
1. **Handoff Generation:** ~3,000–7,000 output tokens/session. Codex and Claude Code both hit usage limits while authoring handoffs.
2. **Cold-Start Reads:** ~11,000–12,000 input tokens per agent session across 5 files (~44,000 tokens for 4 subagents).
3. **Commit Churn:** 48% of the last 50 commits (24/50) are documentation/continuity chores.
4. **4KB Brief Cap Churn:** Commits `e7bcd16` and `170161b` were spent solely trimming words to stay under 4096 bytes.

### Q4: Conflict Prevention Mechanisms Ranked by Demonstrated Effectiveness
1. **`docs/ACTIVE_WORK.json` (10/10):** Prevented split-brain edits between Codex and Claude Code; zero merge conflicts.
2. **Deterministic Test Gate (9.5/10):** Caught failover bug, F109 artifact pollution, and Test 6b syntax bug.
3. **Git Branch & Commit Discipline (9/10):** Allowed Claude to safely fast-forward merge Codex's unmerged branch.
4. **Fix Registry (`Fix Registry.md`) (7.5/10):** Prevents circular bug regression across sessions via permanent F-numbers.
5. **Continuity SHA-256 Doc Pinning (3/10):** Breaks on any markdown edit; creates false alarms.
6. **Narrative Handoffs (2/10):** Prone to hallucinated timelines and premature session timeouts.

### Q5: Contradiction Map & Recommended Hierarchy
* **Observed Contradictions:** Gate count discrepancies (41 vs 63 vs 67); static persona roles vs dynamic `ACTIVE_WORK.json` assignments; `CURRENT_STATE.md` staleness vs live commit HEAD.
* **Hierarchy:**
  1. Live Executable State (`run_all.py`, `git status`, running processes)
  2. Git History (`git log`, commit SHAs, diffs)
  3. Dynamic Concurrency Locks (`docs/ACTIVE_WORK.json`)
  4. Machine-Readable State (`.harness/continuity/current.json`)
  5. Canonical Documentation (`docs/CURRENT_STATE.md`, `docs/HARDENING.md`)
  6. External Knowledge Mirrors (`S:\ObsidianVault`)
  7. Historical Narrative Handoffs (Lowest authority)

### Q6: Governance Rule Classification
* **Always Mandatory:** Default ESTOP check, `ACTIVE_WORK.json` lock check, `python tests/run_all.py` pre-commit gate, clean working tree check.
* **Conditional:** Interrupted-task handoff (only on block/compaction), `Fix Registry` entry (only on bug fix), `preflight release` (only before deployment).
* **Optional:** Syncing `S:\ObsidianVault`.
* **Pure Ceremony / Removable:** Static persona files (`CODEX.md`, etc.), 4096-byte brief limit, doc SHA pinning in `current.json`, completion handoffs.

### Q7: Minimum Machine-Readable Continuity Model
Strip narrative `completed` text and file SHA pins. Retain only structured JSON (< 1.2 KB):
```json
{
  "schema_version": 3,
  "task": {
    "id": "M1-M7-VALIDATION",
    "phase": "p1-security-complete",
    "next_action": "Independent security review and operator deployment"
  },
  "invariants": {
    "estop_engaged": true,
    "model_free_only": true,
    "gate_suites_expected": 67
  },
  "active_locks": "docs/ACTIVE_WORK.json"
}
```

### Q8: Static Personas vs. Dynamic Roles
Static persona files create cognitive conflict and drift. Codex implemented enterprise security despite being labeled a refactorer; Claude implemented telemetry despite being labeled a reviewer. **Recommendation:** Deprecate static persona files; delegate roles dynamically via `docs/ACTIVE_WORK.json`.

### Q9: Minimum Viable Governance Architecture (MVGA)
Consists of four components:
1. `docs/ACTIVE_WORK.json` (Dynamic single-writer mutex)
2. `tests/run_all.py` (Deterministic safety gate)
3. Git commits & diffs (Immutable history & attribution)
4. Compact `current.json` (< 1.2 KB task pointer & invariants)

### Q10: Action Matrix

* **KEEP:**
  - `docs/ACTIVE_WORK.json` single-writer path lock.
  - 67-suite model-free gate (`python -B tests/run_all.py`).
  - Git as canonical source of truth.
  - Default-engaged ESTOP invariant.
  - Append-only `Fix Registry.md`.
* **CHANGE:**
  - Abolish completion handoffs; require handoffs only on blocked/interrupted work.
  - Strip prose and SHA pins from `current.json`; remove 4KB limit.
  - Decouple Obsidian Vault syncing from active agent coding loops.
  - Deprecate static persona `.md` files in favor of `ACTIVE_WORK.json`.
  - Replace manual `CURRENT_STATE.md` editing with dynamic CLI generation (`agi status --markdown`).
* **EXPERIMENT:**
  - Enforce `ACTIVE_WORK.json` locks programmatically via a git pre-commit hook.
  - Test single-commit handoff transitions (recording notes directly inside `ACTIVE_WORK.json`).

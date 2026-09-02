# Codex Handoff — Enterprise Readiness Boost: 2.9 → 3.5+

**Date:** 2026-09-02
**Author:** Claude Code (Independent Reviewer)
**Status:** RECOMMENDATION — Operator-reviewed and approved for execution
**Git HEAD:** `7e63f18` (docs: record independent review PASS)
**Working Tree:** Clean, pushed to origin/master
**Safety:** ESTOP engaged | Zero live execution | 46/46 model-free suites green

---

## 1. Context: Where We Are

The enterprise readiness assessment (`docs/ENTERPRISE_READINESS_2026-08-31.md`) rates the harness at **2.9/5**. The Operator CLI and Boundary Hardening have passed independent review. The project is gated on operator authorization for the canary/M1-M7 sequence.

While waiting for that authorization, there is high-value work available that:
- Requires **zero operator authorization** for live missions
- Requires **zero provider calls**
- Can be built and tested with ESTOP engaged and 46/46 tests green
- Directly addresses the lowest-scoring areas in the assessment

## Execution Progress

- [x] **Action 1 â€” dependency lock, bootstrap, and CI:** completed on
  2026-09-02. `scripts/requirements.txt` exactly matches the active Python
  environment; `bootstrap.ps1` verifies Python 3.11+/Node 24+, creates an
  idempotent `.venv`, and installs only the pinned requirements; `ci.ps1` runs
  the model-free gate and preserves its exit code. The wrapper passed 46/46
  suites under normal Windows execution. A restricted sandbox denies child
  containment fixtures access to temp folders, so that environment is not used
  as the CI acceptance authority.
- [x] **Action 2 â€” Credential Manager vault:** completed on 2026-09-02.
  `orchestrator/secrets.py` reads the generic Windows Credential Manager target
  `AGI_like/byteplus_coding` first, then preserves the environment/private-dotenv
  fallback. It has no credential-write path and suppresses store errors. Provider
  dispatch, the one-call canary, and read-only preflight now use the same source.
  The new vault suite passed 11/11 assertions; focused provider/CLI/vault coverage
  passed 3/3 suites with no provider contact.
- [x] **Action 3 â€” task worktree automation:** completed on 2026-09-02.
  `orchestrator/task_worktree.py` is deliberately separate from the reviewed
  read-only `agi` surface. It validates ledger task IDs, fails closed on every
  batch-lock state, creates deterministic sibling task worktrees and branches,
  and protects `ACTIVE_WORK` with a writer lock plus content-hash CAS. Release
  gates, commits, fast-forward merges, atomically releases matching ownership,
  and removes only a clean worktree. Its disposable-Git suite passed 10/10;
  the full model-free gate passed 48/48 suites. No provider, ESTOP, canary, or
  isolation paths are imported or invoked.
- [x] **Action 4 â€” Memory FTS:** completed on 2026-09-02 under the
  operator-approved Enterprise Boost scope. `orchestrator/memory_fts.py`
  keeps canonical Markdown in `memory/agents/<agent>/memory.md` and writes
  only the derived `memory/fts_index.db`. It uses the specified external-content
  FTS5 table and AI/AD/AU synchronization triggers, supports incremental sync,
  deterministic rebuilds, snippets, and agent filtering. The focused suite
  passed 14/14, including trigger integrity and digest checks proving that
  `ledger/ledger.db` and `memory/ledgerbook.db` are untouched.

---

## 2. What Codex Should Verify First

Before starting new work, Codex must confirm:

### 2.1 Current State
- [ ] Git HEAD is at `7e63f18` or later on `master`
- [ ] `git status` is clean
- [ ] `python -B tests/run_all.py` passes 46/46 model-free suites
- [ ] ESTOP is engaged (`%LOCALAPPDATA%\hermes\ESTOP` exists)
- [ ] No batch lock, no canary marker, no isolation window open
- [ ] No active implementation owner in `docs/ACTIVE_WORK.json`
- [ ] `docs/CURRENT_STATE.md` reflects phase "Review PASSED; Awaiting Operator Authorization"

### 2.2 Completed Work (recent)
- **Operator CLI** (`orchestrator/operator_cli.py` + `agi.ps1`) — 3 read-only commands, 109 assertions, reviewed PASS
- **Boundary Hardening** (4 Munder gaps) — tamper detection, hive quiescence, canary hardening, enforce.js — reviewed PASS
- **Mailbus Phase 1** (`orchestrator/mailbus.py`) — 786 lines, 21 tests, unwired
- **M1** and **M2** missions — both executed, critic PASS
- **Continuity truthfulness fix** — recovery error → `valid=null`, never guessed PASS

### 2.3 Codex's Previous Work
Codex previously completed:
- Boundary recovery and Operator CLI narrow fix (continuity truthfulness)
- M2 Task 109 closeout
- Hermes resume documentation
- Munder remediation

All paths released. No outstanding ownership.

---

## 3. Recommended Actions (Priority Order)

### Action 1: 📦 Lock Dependencies + Bootstrap Script

**Assessment gap:** Deployment/rollback scored **1.5/5** — the lowest score. No `requirements.txt`, no `pyproject.toml`, no `package.json`, no bootstrap, no CI.

**What to do:**

#### 1a. Create `scripts/requirements.txt`
Pin ALL currently installed Python packages. Generate from the current environment:
```
pip list --format=freeze > scripts/requirements.txt
```
This must include every package the harness actually uses. The current environment has 207 packages. Only the ones imported by the project need to be in a "runtime" requirements, but for the bootstrap we pin everything so the environment is reproducible.

**Critical packages currently available:**
- `PyYAML==6.0.3` — config parsing
- `psutil==7.2.2` — process inventory
- `pydantic==2.13.4` — data models
- `GitPython==3.1.59` — git operations
- `cryptography==50.0.0` — available for secrets work
- `win32cred` — available via `pywin32` for Windows Credential Manager

#### 1b. Create `scripts/bootstrap.ps1`
A PowerShell script that:
1. Checks Python 3.11+ is available
2. Checks Node.js v24+ is available
3. Creates a virtual environment: `python -m venv .venv`
4. Activates it and runs: `pip install -r scripts/requirements.txt`
5. Installs enforce.js dependencies if needed (currently none — enforce.js is pure Node.js stdlib)
6. Prints "BOOTSTRAP COMPLETE" and exits 0
7. Must be idempotent — safe to re-run

**Constraints:**
- Must NOT modify any source files
- Must NOT make any network calls beyond `pip install`
- Must NOT assume `C:` drive — the repo lives at `S:\AGI_like`
- Must fail closed on missing prerequisites
- Must work on Windows 11 (PowerShell 5.1+)

#### 1c. Create `scripts/ci.ps1`
A PowerShell script that:
1. Runs `python -B tests/run_all.py`
2. Exits with the same exit code as the test gate
3. Prints a clear summary
4. Must be safe to run with ESTOP engaged (all tiers model-free by default)

**Verification:** After creating these three files, run `python -B tests/run_all.py` to confirm 46/46 suites still green.

**Files to create:**
- `scripts/requirements.txt`
- `scripts/bootstrap.ps1`
- `scripts/ci.ps1`

---

### Action 2: 🔒 Credential Manager Vault

**Assessment gap:** Security/secrets scored **2.0/5**. Credentials live in environment variables that leak into every subprocess.

**What to do:**
Create `orchestrator/secrets.py` — a vault-backed secrets module that:
1. Checks Windows Credential Manager (`win32cred`) first
2. Falls back to environment variables (backward compatible)
3. Never logs or exposes credential values in error messages
4. Provides `get_api_key(provider: str) -> str | None`

**Key design decisions:**
- `win32cred` is already available — no new dependencies needed
- The module must never write credentials itself (read-only from the harness; the operator sets them via the OS credential manager UI or `cmdkey`)
- Must be importable without triggering any network calls
- Must fail closed: unreadable credential store → `None` → caller handles missing key

**Integration points:**
- `workspace/validation/byteplus_connectivity_canary.py` — currently reads `os.environ["ARK_API_KEY"]` directly. Should call `secrets.get_api_key("byteplus_coding")` instead.
- `orchestrator/operator_cli.py` — the preflight check `ark_api_key_present_in_env` should also check the vault.
- `orchestrator/provider_chat.py` — any credential retrieval should route through the secrets module.

**Verification:**
- `python -B tests/run_all.py` — 46/46 green
- The canary preflight still detects missing credentials correctly
- Importing `secrets` does not load network modules (test with `sys.modules` snapshot)

**Files to create/modify:**
- `orchestrator/secrets.py` (NEW)
- `tests/test_secrets.py` (NEW, registered in unit tier)
- `workspace/validation/byteplus_connectivity_canary.py` (MODIFY — use secrets module)
- `orchestrator/operator_cli.py` (MODIFY — check secrets for key presence)

---

### Action 3: 🔄 Worktree + Task Claim Automation

**Assessment gap:** Scalability/concurrency scored **1.8/5**. No automated worktree lifecycle or atomic task claims.

**What to do:**
Extend the Operator CLI (`orchestrator/operator_cli.py` or a sibling module) with two new subcommands:

#### `agi task claim <task-id>`
1. Reads `docs/ACTIVE_WORK.json` — checks no conflicting ownership
2. Verifies the task ID is valid (exists in the task registry)
3. Creates a git worktree: `git worktree add ../AGI_like-<task-id> <base-branch>`
4. Writes ownership to ACTIVE_WORK (compare-and-swap, fail on conflict)
5. Prints the worktree path and exits 0

#### `agi task release`
1. Runs the model-free gate in the worktree
2. Commits all changes with an auto-generated message
3. Merges the worktree back to the main branch
4. Releases ownership in ACTIVE_WORK
5. Removes the git worktree
6. Exits 0 on success, nonzero with a clear error on failure

**Safety constraints:**
- Must NEVER run with a batch lock held
- Must NEVER mutate ACTIVE_WORK without compare-and-swap (detect concurrent claims)
- Must NEVER delete uncommitted work
- Must print the worktree path before claiming so the operator can inspect it
- Must be testable in temp directories (injected state, no real git operations on the live repo)

**Verification:**
- New test suite: `tests/test_task_worktree.py` (unit tier)
- `python -B tests/run_all.py` — 46/46 + new tests green
- No provider calls, no ESTOP interaction, no network

**Files to create/modify:**
- `orchestrator/task_worktree.py` (NEW)
- `tests/test_task_worktree.py` (NEW)
- `tests/tiers.json` (MODIFY — register new suite)
- `docs/OPERATOR_CLI.md` (MODIFY — document new commands)

---

### Action 4: 🧠 Phase 2: Memory FTS

**Assessment gap:** State/memory architecture scored **3.2/5**. No FTS5 search index for agent memory.

**What to do:**
Implement `orchestrator/memory_fts.py` per `docs/MUNDER_BLUEPRINT.md` §3. This is already well-specified:

- Database: `memory/fts_index.db` (separate from `ledger/ledger.db` and `memory/ledgerbook.db` — zero `DatabaseMutationGuard` violations by construction)
- External-content FTS5 table with sync triggers
- Incremental indexing from `memory/agents/<agent-id>/memory.md` markdown files
- Deterministic rebuild protocol
- Query interface with `snippet()` excerpts, agent filtering, rank ordering

**Key constraint:** The FTS5 database must live at `memory/fts_index.db` to avoid `integrity.DatabaseMutationGuard` conflicts.

**Verification:**
- New test suite: `tests/test_memory_fts.py` (unit tier)
- `python -B tests/run_all.py` — 46/46 + new tests green
- Zero side-effects on existing databases
- Index rebuild is deterministic (same markdown → same FTS content)

**Files to create/modify:**
- `orchestrator/memory_fts.py` (NEW)
- `tests/test_memory_fts.py` (NEW)
- `tests/tiers.json` (MODIFY — register new suite)

---

## 4. Hard Constraints (Do Not Violate)

These apply to ALL four actions:

| Constraint | Rationale |
|---|---|
| No provider calls of any kind | No operator authorization has been issued |
| No ESTOP modification, clearing, or bypass | ESTOP remains engaged |
| No canary marker creation or consumption | Operator-only action |
| No isolation window opening | Operator-only action |
| No modifications to existing safety controls | `execution_pause.py`, `runlock.py`, `cohort_hive_quiesce.py`, `integrity.py`, `backup.py` — read-only |
| 46/46 model-free suites must remain green | Gate invariant |
| All new test suites registered in `tests/tiers.json` (unit tier) | Must run as part of the gate |
| No new external dependencies without operator approval | Locked environment discipline |
| All new files must be created, all changes committed | No unfinished work at handoff |

---

## 5. Expected Score Impact

| Action | Area Lifted | Score Delta | Cumulative |
|---|---|---|---|
| Action 1: Lock deps + bootstrap | Deployment (1.5 → 3.0) | +0.6-0.7 | 2.9 → 3.5-3.6 |
| Action 2: Credential vault | Security (2.0 → 3.0) | +0.3-0.4 | → 3.8-4.0 |
| Action 3: Worktree automation | Scalability (1.8 → 3.0) | +0.3-0.4 | → 4.1-4.4 |
| Action 4: Memory FTS | State/memory (3.2 → 3.7) | +0.2-0.3 | → 4.3-4.7 |

Realistically, completing all four would land around **3.5-3.7** because the assessment is holistic and some gaps (operator identity, OS containment, egress) remain architectural rather than incremental.

---

## 6. Handoff Protocol

When Codex completes work on any action:
1. Run `python -B tests/run_all.py` — must pass
2. Run `git diff --check` — no whitespace errors
3. Verify ESTOP still engaged, canary marker absent, isolation restored
4. Commit with descriptive message
5. Update `docs/ACTIVE_WORK.json` — release owned paths, mark completed
6. Update this handoff document with completion evidence
7. Do NOT push — leave that for operator review

---

## 7. Summary Prompt for Codex

Below is the self-contained prompt to give Codex. It includes everything needed to understand context and start working:

---

```
You are Codex. Your task is to execute the enterprise readiness boost plan in
docs/CODEX_HANDOFF_2026-09-02_ENTERPRISE_BOOST.md.

First, verify the current state:
1. You are at S:\AGI_like on branch master, HEAD 7e63f18
2. git status is clean
3. Run python -B tests/run_all.py — confirm 46/46 suites green
4. Read docs/CURRENT_STATE.md and docs/ACTIVE_WORK.json to confirm no active
   implementation owner and review state is PASSED

Then execute the four actions IN ORDER:
1. Lock deps + bootstrap (scripts/requirements.txt, bootstrap.ps1, ci.ps1)
2. Credential vault (orchestrator/secrets.py, tests/test_secrets.py, integrate
   into canary and preflight)
3. Worktree automation (orchestrator/task_worktree.py + tests)
4. Phase 2 Memory FTS (orchestrator/memory_fts.py + tests per Munder Blueprint §3)

After each action:
- Run the full test gate: python -B tests/run_all.py
- Verify ESTOP is still engaged
- Commit with a descriptive message
- Update ACTIVE_WORK.json

Hard rules:
- No provider calls
- No ESTOP modification
- No canary marker
- No isolation window
- 46/46 suites must stay green
- All new tests registered in tests/tiers.json (unit tier)
- All files created, all changes committed — no unfinished work
- Do NOT push to origin — leave for operator review

When all four are done, update this handoff document with completion evidence.
```

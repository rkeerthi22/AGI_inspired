# Codex Handoff — Production Run Ready

**Date:** 2026-09-02
**Author:** Claude Code (operator session)
**Status:** Ready for Codex
**Git HEAD:** `d20968d`
**Working Tree:** Clean
**Safety:** ESTOP engaged | **54/54** model-free suites green
**Pushed:** All commits on origin/master

---

## 1. Context: What's Been Built

### All 4 Build Actions + 3 Integrations + Watchdog

| Action | Commit | Status |
|---|---|---|
| Cross-provider fallback (Anthropic + OpenAI) | `e39e793` | ✅ |
| Windows Job Object containment (Phase 4) | `0640d52` | ✅ |
| SQLite schema versioning + migrations | `0237e97` | ✅ |
| Ed25519 operator identity | `a6bf94f` | ✅ |
| **Integration:** signed markers in execution_pause | `97cec04` | ✅ |
| **Integration:** migrate_all() in startup | `7841b01` | ✅ |
| **Integration:** pty_daemon in worker path | `c7ba548` | ✅ |
| **Fix:** close_job after proc.wait (KILL_ON_JOB_CLOSE bug) | `a6e8ec8` | ✅ |
| **ESTOP watchdog:** polling loop in hermes_worker | `d20968d` | ✅ |

### Key Architectural Decisions (LOCKED)

- **ESTOP is always engaged** during model-free work. Only the operator authorizes clears/canaries via `execution_pause.py --authorize-*`
- **Signed markers** via Ed25519 (cryptography package, keypair in Windows Credential Manager). Unsigned JSON still accepted with PendingDeprecationWarning
- **Job Object containment:** `create_contained_process()` → suspended spawn → assign to job → resume. `KILL_ON_JOB_CLOSE` reaps tree when handle closes
- **ESTOP watchdog:** `hermes_worker()` polls `pause_engaged()` every 5s during `proc.wait()`. On ESTOP: `terminate_job(h_job)` kills entire process tree
- **Provider fallback chain:** `ollama cloud → anthropic → openai → gemma4:12b-ctx4k` (local). Quota-group aware: same-account rungs are skipped once one 429s
- **Database migration:** `migrate_all()` runs at `batch_runner` startup, fail-soft. Schema version tracked via `PRAGMA user_version`

---

## 2. Your Task: Live Production Run of M1

### Step 0: Verify

```bash
cd S:/AGI_like
git status              # must be clean
python -B tests/run_all.py  # 54/54 green
dir %LOCALAPPDATA%\hermes\ESTOP  # must exist (engaged)
```

### Step 1: Wait for Operator Authorization

The operator runs:
```bash
python orchestrator/execution_pause.py --authorize-canary
python orchestrator/execution_pause.py --authorize-clear
```

Do NOT proceed until they confirm these are done.

### Step 2: Connectivity Canary

```bash
python workspace/validation/byteplus_connectivity_canary.py --authorize-single-estop-bypass
```

This tests that BytePlus (primary provider) is reachable. If it fails:
- Check `ARK_API_KEY` is set (`echo %ARK_API_KEY%`)
- Check Windows Credential Manager for `AGI_like/byteplus_coding`
- If BytePlus is down, the fallback chain should handle it

### Step 3: Run M1

```bash
python orchestrator/batch_runner.py --mission 001-shopify-competitor-intel
```

This queues and runs all M1 tasks. Each task goes through:
1. Hermes worker with web research toolset
2. Provider failover on quota errors (BytePlus → Anthropic → OpenAI → local gemma)
3. Critic evaluation → evidence scoring → ledger recording

### Step 4: Report Results

Report:
- Number of tasks, status breakdown (done / parked / infra_failed / failed)
- Critic verdicts per task (pass / fail / mixed)
- Any failover events (which rungs were tried, which succeeded)
- Any orphan processes after the run (`tasklist | findstr python`)
- ESTOP status at end (must still be engaged)

### Optional: Scorecard

```bash
python orchestrator/batch_runner.py --scorecard
```

---

## 3. What to Watch For

| What | Expected | If wrong |
|---|---|---|
| Tasks complete `done` | Most tasks succeed | Check `infra_failed` or `quota_wait` |
| Provider failover | BytePlus 429 → Anthropic/OpenAI tried | Check batch log for failover messages |
| Critic evaluates | Each task gets `pass`/`fail`/`mixed` | Check `workspace/task<N>_critic.txt` |
| No DB mutations by worker | `DatabaseMutationGuard` is silent | If it fires, P0 — stop and report |
| No orphan processes | All Hermes processes reaped | Check Task Manager after run |
| ESTOP kills in-flight | Engage ESTOP mid-run → worker dies | Manual test: run canary, engage ESTOP, verify process killed |

---

## 4. Hard Constraints

| Constraint | Why |
|---|---|
| **No modifications to safety controls** | `execution_pause.py`, `runlock.py`, `cohort_hive_quiesce.py`, `integrity.py`, `backup.py` — read-only |
| **54/54 suites green at handoff** | Gate invariant |
| **No new external dependencies** | Locked environment |
| **Do NOT run until operator authorizes** | They must authorize canary + clear first |
| **Do NOT push to origin** | Leave for operator review |
| **ESTOP engaged at handoff** | Re-engage if tamper fires |

---

## 5. One-Line Prompt

> You are Codex at S:\AGI_like on master. Verify clean, 54/54 green, ESTOP engaged. Wait for operator to authorize canary+clear. Then: run the BytePlus connectivity canary, run `batch_runner --mission 001-shopify-competitor-intel`, report task statuses, critic verdicts, failover events, orphan processes. Don't push. Leave ESTOP engaged.

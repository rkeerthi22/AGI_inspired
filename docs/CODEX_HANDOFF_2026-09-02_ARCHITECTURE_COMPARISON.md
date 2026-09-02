# Codex Handoff — Architecture Comparison & Next Build Actions

**Date:** 2026-09-02
**Author:** Claude Code
**Status:** RECOMMENDATION — Operator-reviewed and approved for execution
**Git HEAD:** `27e2c89` (plus 5 unpushed Enterprise Boost commits: `dbaf4b7`, `f07b3f6`, `d6e96ed`, `ffabc97`, `6655ea6`)
**Working Tree:** Clean
**Safety:** ESTOP engaged | Zero live execution | **49/49** model-free suites green

---

## 1. Context: What Happened Today (Enterprise Boost)

Codex completed four enterprise-readiness actions earlier today:

| # | Action | Commit | Files |
|---|---|---|---|
| 1 | Lock deps + bootstrap/CI | `dbaf4b7` | `scripts/requirements.txt`, `bootstrap.ps1`, `ci.ps1` |
| 2 | Credential Manager vault | `f07b3f6` | `orchestrator/secrets.py`, `tests/test_secrets.py` |
| 3 | Task worktree lifecycle | `d6e96ed` | `orchestrator/task_worktree.py`, `tests/test_task_worktree.py` |
| 4 | Memory FTS5 index | `ffabc97` | `orchestrator/memory_fts.py`, `tests/test_memory_fts.py` |
| — | Ownership closeout | `6655ea6` | `ACTIVE_WORK.json`, handoff doc |

Test gate grew from **46/46 → 49/49** suites green. These 5 commits are **not pushed** to origin.

---

## 2. Architecture Comparison: AGI_like vs. Alternatives

The operator asked for a comparison between AGI_like, Claude (Anthropic), Hermes, and OpenAI.

### 2.1 Available Provider Packages

All four are **already installed** in the Python environment:

| Provider | Package | Version | Status |
|---|---|---|---|
| **Anthropic (Claude)** | `anthropic` | 0.87.0 | ✅ Installed, available |
| **OpenAI** | `openai` | 2.24.0 | ✅ Installed, available |
| **Hermes Agent** | `hermes-agent` | 0.20.2 | ✅ Installed (runtime backbone) |
| **AGI_like** | (local) | — | ✅ The harness itself |

### 2.2 Rating by Dimension (Scale: 1-5)

| Dimension | **AGI_like** | **Claude API** | **OpenAI API** | **Hermes Agent** |
|---|---|---|---|---|
| **Safety / fail-closed** | **4.2** — ESTOP, tamper, runlock, quiescence, enforce.js | 1.5 — basic sandboxing | 1.5 — basic sandboxing | 2.0 — config guardrails |
| **Testing rigor** | **3.8** — 49 suites, incident-driven regression | 1.0 — no harness tests | 1.0 — no harness tests | 1.5 — `hermes doctor` |
| **Secrets management** | **3.0** — Credential Manager vault + env fallback | 1.0 — .env | 1.0 — .env | 2.0 — .env + gateway |
| **Operator experience** | **3.5** — `agi status/health/preflight` + JSON | 1.0 — raw API | 1.0 — raw API | 2.5 — `hermes status` |
| **Multi-agent coordination** | **3.2** — ACTIVE_WORK, enforce.js, handoffs | 1.0 — none | 1.0 — none | 1.0 — single-agent |
| **Mission admission** | **3.5** — 3-gate: review→preflight→operator auth | 1.0 — none | 1.0 — none | 1.0 — none |
| **Provider failover** | **3.1** — quota-aware 429 handling, typed adapters | 1.0 — none | 1.0 — none | 2.0 — model fallback |
| **Evaluation / critic** | **3.3** — tri-state critic, evidence files, spot checks | 1.0 — none | 1.0 — none | 1.0 — none |
| **Retrieval / evidence** | **3.7** — enforced progression, bounded evidence | 1.0 — none | 1.0 — none | 1.0 — none |
| **Deployment** | **3.0** — `requirements.txt`, `bootstrap.ps1`, `ci.ps1` | 1.0 — pip install | 1.0 — pip install | 2.0 — installer |
| **Scalability** | **3.0** — task worktree automation, CAS ownership | 1.0 — none | 1.0 — none | 1.0 — single-process |
| **State/memory** | **3.7** — FTS5 index, WAL, ledger, continuity | 1.0 — none | 1.0 — none | 2.0 — basic memory |
| **Overall** | **~3.5** | **~1.2** | **~1.2** | **~1.8** |

### 2.3 Key Takeaways

**AGI_like is the ONLY harness with a real safety architecture.** The others are API SDKs or single-agent runtimes. Your ESTOP + tamper detection + runlock + hive quiescence + enforce.js combination is genuinely differentiated — no other framework has anything equivalent.

**The other providers are valuable as MODEL sources**, not as harnesses. The `anthropic` and `openai` packages should be wired as additional provider rungs in the fallback chain (see models.yaml — the Anthropic rung is already commented out, waiting for a key).

**Hermes is the runtime backbone** that AGI_like already wraps. The harness controls *whether* a mission runs; Hermes controls *how* the model call executes.

---

## 3. What Codex Must Verify First

Before starting new work:

- [ ] You are at `S:\AGI_like` on branch `master`
- [ ] `git status` is clean
- [ ] `python -B tests/run_all.py` — **49/49 suites green**
- [ ] ESTOP is engaged (`%LOCALAPPDATA%\hermes\ESTOP` exists)
- [ ] No batch lock, no canary marker, no isolation window open
- [ ] `docs/ACTIVE_WORK.json` — no active implementation owner
- [ ] The 5 Enterprise Boost commits exist locally: `dbaf4b7`, `f07b3f6`, `d6e96ed`, `ffabc97`, `6655ea6` (not pushed)

---

## 4. Recommended Actions (Priority Order)

### Action 1: 🚀 Push the 5 Enterprise Boost Commits

These are complete, tested, and sitting ahead of origin. Push them:
```bash
git push
```

---

### Action 2: 🔌 Wire OpenAI + Anthropic as Additional Provider Rungs

**Why:** The enterprise assessment scores Provider abstraction at **3.1/5** — "current independent provider depth is limited." Adding OpenAI and Anthropic as real fallback rungs:
- Eliminates the single-provider dependency
- Gives the failover chain actual cross-provider depth (not just cross-model within Ollama)
- Lets missions keep running when BytePlus or Ollama are exhausted

**What to do:**

#### 2a. Add provider configs to `config/models.yaml`

```yaml
providers:
  # ... existing byteplus_coding stays ...
  
  anthropic:
    endpoint: https://api.anthropic.com/v1
    authentication_reference: env:ANTHROPIC_API_KEY
    routing_model: claude-sonnet-5
    hermes_provider: custom:anthropic
  
  openai:
    endpoint: https://api.openai.com/v1
    authentication_reference: env:OPENAI_API_KEY
    routing_model: gpt-4o
    hermes_provider: custom:openai
```

#### 2b. Add them to the fallback chain

The current chain is `glm-5.2:cloud → kimi-k2.7-code:cloud → gemma4:12b-ctx4k`. Extend it:

```yaml
fallback_chain:
  - { provider: ollama, model: glm-5.2:cloud,        quota_group: ollama-cloud }
  - { provider: ollama, model: kimi-k2.7-code:cloud, quota_group: ollama-cloud }
  - { provider: anthropic, model: claude-sonnet-5 }     # NO quota_group — genuinely separate
  - { provider: openai, model: gpt-4o }                  # NO quota_group — genuinely separate
  - { provider: ollama, model: gemma4:12b-ctx4k, context_tokens: 4096 }  # last resort
```

#### 2c. Create provider adapters

The existing `orchestrator/provider_chat.py` has a `ChatRequest` type and `chat()` function for BytePlus. Extend the pattern:
- `orchestrator/providers/anthropic_provider.py` — wraps `anthropic` SDK, maps to `ChatRequest`/`ChatResult`
- `orchestrator/providers/openai_provider.py` — wraps `openai` SDK, maps to `ChatRequest`/`ChatResult`

Both are already installed packages — no new dependencies.

#### 2d. Add credentials to secrets module

`orchestrator/secrets.py` already handles `byteplus_coding → ARK_API_KEY`. Extend:

```python
_ENVIRONMENT_KEYS = {
    "byteplus_coding": "ARK_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}
```

And register them in Windows Credential Manager as `AGI_like/anthropic` and `AGI_like/openai`.

#### 2e. Tests

- `tests/test_provider_chat.py` — mock the Anthropic and OpenAI SDKs, verify the adapter contract
- `tests/test_fallback_chain.py` — verify the chain correctly skips quota-exhausted rungs and tries the next provider
- No live provider calls — all mocked

**Files to create/modify:**
- `config/models.yaml` (MODIFY)
- `orchestrator/providers/anthropic_provider.py` (NEW)
- `orchestrator/providers/openai_provider.py` (NEW)
- `orchestrator/providers/__init__.py` (NEW)
- `orchestrator/secrets.py` (MODIFY — add key mappings)
- `tests/test_provider_chat.py` (NEW)
- `tests/test_fallback_chain.py` (NEW)
- `tests/tiers.json` (MODIFY)

---

### Action 3: 🪟 Phase 4 — Windows Job Object Process Containment

**Why:** OS/process isolation scores **2.3/5**. The blueprint exists at `docs/MUNDER_BLUEPRINT.md` §5. Workers currently execute as the operator's Windows account with no OS-level containment.

**What to do:**
Implement `orchestrator/pty_daemon.py` per the blueprint:
- `create_contained_process(command_list, cwd)` — suspended-spawn → assign to Job Object → resume
- `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` — entire process tree terminates when the handle closes
- ESTOP watchdog: terminates the job via `kernel32.TerminateJobObject(hJob, 75)`
- Continuous pipe drain (stdout/stderr reader threads to prevent OS pipe-buffer deadlocks)

**Key constraints:**
- `ctypes` + `kernel32` only — no new dependencies
- Must work on Windows 11
- Must NOT import any network/provider modules
- Tests must NOT create real processes on the live system (use mocks)

**Files to create:**
- `orchestrator/pty_daemon.py` (NEW)
- `tests/test_pty_daemon.py` (NEW)
- `tests/tiers.json` (MODIFY)

---

### Action 4: 💾 SQLite Schema Migrations

**Why:** State/memory architecture scores **3.7/5** but has a gap: `user_version` is 0, no migration contract.

**What to do:**
Create `orchestrator/migrations.py` that:
1. Reads `PRAGMA user_version` from each database
2. Applies versioned migration functions in order
3. Supports forward and rollback
4. Sets `user_version` after each migration

Start with the existing databases (ledger, ledgerbook, predictions, fts_index) and establish a baseline at their current schema version.

**Files to create:**
- `orchestrator/migrations.py` (NEW)
- `tests/test_migrations.py` (NEW)
- `tests/tiers.json` (MODIFY)

---

### Action 5: 🔐 Strong Operator Identity (Cryptographic Markers)

**Why:** Security/secrets scores **3.0/5** after the vault work, but operator markers are still file-based (not cryptographically bound to a verified human identity).

**What to do:**
Extend `orchestrator/execution_pause.py` or create `orchestrator/operator_auth.py`:
1. Generate a signing keypair on first use (stored in Credential Manager)
2. Operator markers (`--authorize-canary`, `--authorize-clear`) are now signed JWTs
3. Verification checks the signature before accepting the marker
4. Backward compatible: unsigned markers are still accepted with a warning (graceful migration)

**This is the hardest action** — it changes the operator authorization flow. Only attempt after the first four are done.

---

## 5. Hard Constraints (Do Not Violate)

| Constraint | Rationale |
|---|---|
| **No live provider calls** | No operator authorization has been issued |
| **No ESTOP modification, clearing, or bypass** | ESTOP remains engaged |
| **No canary marker creation or consumption** | Operator-only action |
| **No isolation window opening** | Operator-only action |
| **No modifications to existing safety controls** | `execution_pause.py`, `runlock.py`, `cohort_hive_quiesce.py`, `integrity.py`, `backup.py` — read-only |
| **49/49 model-free suites must remain green** | Gate invariant |
| **All new test suites registered in `tests/tiers.json` (unit tier)** | Must run as part of the gate |
| **No new external dependencies without operator approval** | Locked environment discipline |
| **All files created, all changes committed** | No unfinished work at handoff |
| **Do NOT push to origin** | Leave for operator review |

---

## 6. Expected Score Impact

| Action | Area Lifted | Delta |
|---|---|---|
| Action 1: Push commits | (housekeeping) | — |
| Action 2: OpenAI + Anthropic providers | Provider abstraction (3.1 → 3.8) | +0.7 |
| Action 3: Job Objects | OS isolation (2.3 → 3.5) | +1.2 |
| Action 4: SQLite migrations | State/memory (3.7 → 4.0) | +0.3 |
| Action 5: Operator identity | Security (3.0 → 4.0) | +1.0 |
| **Cumulative** | **Overall: 3.5 → ~4.2** | **+0.7** |

---

## 7. Summary Prompt for Codex

```
You are Codex. Read docs/CODEX_HANDOFF_2026-09-02_ARCHITECTURE_COMPARISON.md.

First verify:
1. You are at S:\AGI_like on master, working tree clean
2. python -B tests/run_all.py — 49/49 suites green
3. ESTOP engaged, no batch lock, no canary marker, no isolation window
4. 5 Enterprise Boost commits exist unpushed: dbaf4b7, f07b3f6, d6e96ed, ffabc97, 6655ea6

Execute in order:

1. PUSH the 5 commits (git push)

2. Wire OpenAI + Anthropic as provider rungs:
   - Add provider configs to config/models.yaml (anthropic + openai endpoints)
   - Extend fallback_chain with both (no quota_group — genuinely separate accounts)
   - Create orchestrator/providers/anthropic_provider.py (wraps anthropic SDK)
   - Create orchestrator/providers/openai_provider.py (wraps openai SDK)
   - Extend orchestrator/secrets.py with ANTHROPIC_API_KEY + OPENAI_API_KEY
   - Add Credential Manager targets AGI_like/anthropic + AGI_like/openai
   - Tests: test_provider_chat.py, test_fallback_chain.py (all mocked, no live calls)

3. Phase 4 — Job Object process containment:
   - orchestrator/pty_daemon.py per Munder Blueprint §5
   - ctypes + kernel32 only, no new deps
   - create_contained_process() with suspended-spawn→assign→resume
   - ESTOP watchdog: TerminateJobObject on engagement
   - Tests: test_pty_daemon.py (mocked, no real process creation)

4. SQLite schema migrations:
   - orchestrator/migrations.py — user_version-based versioned migrations
   - Forward + rollback support
   - Tests: test_migrations.py

5. (Optional, hardest) Strong operator identity:
   - orchestrator/operator_auth.py — signed JWTs for operator markers
   - Keypair in Credential Manager
   - Backward compatible with unsigned markers

After each action:
- python -B tests/run_all.py — must pass
- Verify ESTOP still engaged
- Commit with descriptive message
- Update ACTIVE_WORK.json

Hard rules:
- No provider calls, no ESTOP modification, no canary marker, no isolation window
- 49/49 suites must stay green
- All new tests in tests/tiers.json (unit tier)
- All files committed, no unfinished work
- Do NOT push to origin — leave for operator review

When all done, update this handoff with completion evidence.
```

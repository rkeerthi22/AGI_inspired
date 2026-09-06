# Postmortem & Repair Dossier: Tasks 130 and 131

**Date:** 2026-09-06  
**Auditor / Architect:** Gemini CLI (Google DeepMind Agentic Assistant)  
**Status:** COMPLETE (Model-Free Verification: 74/74 Green, Exit 0)  
**Safety Status:** ESTOP strictly engaged (`True`), Zero live provider calls, Zero quota consumed.

---

## 1. Executive Summary

During controlled-window execution under the F124 restricted-worker sandbox and Windows Filtering Platform (WFP) egress boundary:
- **Task 130** failed immediately with `infra_failed`:
  `Could not restore async delegation completions: unable to open database file`
- **Task 131** failed after exactly 1800 seconds (30-minute timeout) with 0 tokens emitted:
  `critic_notes: ... | worker timeout`, `process_error: worker timeout`, `provider returned no usable output`.
- Simultaneously, the BytePlus connectivity canary probe succeeded in ~12 seconds. This proved raw loopback-to-broker provider connectivity only; it did not validate contained research worker execution, provider resolution inside the child, or tool completion.

A bounded, model-free postmortem was conducted without disengaging ESTOP or consuming upstream quota. Three distinct root causes were identified, reproduced, repaired in code, and verified with regression tests. The full 74-suite model-free test gate is 100% green.

---

## 2. Empirical Root Cause Analysis

### Root Cause 1: SQLite Lock & Import Order Race (Task 130)
* **Mechanism:**
  In commit `cd3464b`, `orchestrator/execution.py` line 116 added:
  ```python
  env["HERMES_HOME"] = str(estop_path().parent)  # S:\AGI_like\.harness
  ```
  The filesystem ACL on `.harness` restricts `BUILTIN\Users` to Read & Execute only (write denied). Under F124, research workers run under a restricted token (`S-1-5-12`) with `BUILTIN\Users` privileges. When Hermes initialized, SQLite attempted to open or create `.harness\state.db` and threw `unable to open database file`.
* **Import Race:**
  In `orchestrator/controlled_hermes.py`, a monkey patch had been attempted:
  ```python
  _ad.restore_undelivered_completions = lambda *a, **kw: 0
  ```
  However, this patch was placed *after* `import run_agent`. When `run_agent` was imported, it transitively imported `tools.process_registry`, which instantiated `registry = ProcessRegistry()` at module load time. Its constructor called `from tools.async_delegation import restore_undelivered_completions; restore_undelivered_completions(self.completion_queue)` *before* the patch line ever executed.
* **Impact:** Immediate infrastructure crash on Task 130 before prompt processing began.

---

### Root Cause 2: Config Discovery Severed by `HERMES_HOME` (Task 131)
* **Mechanism:**
  Hermes locates its `config.yaml` via `get_hermes_home() / "config.yaml"`. When `HERMES_HOME` was forced to `S:\AGI_like\.harness`, Hermes looked for `S:\AGI_like\.harness\config.yaml`, which did not exist. The real user/worker configuration resides in `C:\Users\moham\AppData\Local\hermes\config.yaml` and `workspace/worker_home/config.yaml`.
* **Provider Failure:**
  Because `config.yaml` was missing, `load_config()` returned an empty dict. Hermes had zero custom providers registered. When `--provider custom:byteplus-coding` was passed, `resolve_runtime_provider` failed with:
  ```
  AuthError: Unknown provider 'custom:byteplus-coding'
  ```
* **Impact:** The worker subprocess could not resolve the provider identity required to communicate with BytePlus.

---

### Root Cause 3: Open Stdin Pipe & UI-Restricted Job Object Deadlock (Task 131)
* **Mechanism A (Unclosed Parent Stdin Handle):**
  In `orchestrator/worker_sandbox.py`, `spawn_suspended` creates three OS pipes for stdio. For pipe 0 (stdin), the child inherits the read handle and the parent holds the write handle. In `orchestrator/execution.py`, the parent never wrote to or closed `proc.stdin`. For the entire 1800-second execution, the write end of the pipe remained open. Any subprocess or library reading `sys.stdin` (e.g. non-interactive terminal input checks or CLI input buffers) blocked indefinitely waiting for EOF.
* **Mechanism B (Job Object UI Restrictions vs. Browser Toolset):**
  Commit `cd3464b` passed `-t web,browser` on line 93 of `execution.py`. Under F124, `pty_daemon.create_contained_process` applies Job Object UI restriction flags `0xFF` (`JOB_OBJECT_UILIMIT_DESKTOP`, `JOB_OBJECT_UILIMIT_HANDLES`, etc.) and a dedicated desktop station `AGI_worker_<hex>`. Loading the full `browser` toolset attempts to interact with desktop window stations and launch headless browser daemons that wedge or fault under `0xFF` Job Object limitations. In contrast, web search requires only `-t web` (which uses our in-process DuckDuckGo proxy adapter).
* **Impact:** Task 131 hung silently until the 1800-second wall-clock timeout expired without emitting tokens.

---

## 3. Implemented Code Repairs

1. **Pre-emptive Monkey Patching in [`orchestrator/controlled_hermes.py`](file:///S:/AGI_like/orchestrator/controlled_hermes.py):**
   - Moved `import tools.async_delegation as _ad; _ad.restore_undelivered_completions = lambda *a, **kw: 0` to execute **prior to** importing `run_agent` or `hermes_cli.oneshot`.
   - Result: `ProcessRegistry.__init__` receives the patched no-op lambda during import, completely eliminating the SQLite database access and `unable to open database file` failure.

2. **Dedicated Worker Home & Config Resolution in [`orchestrator/execution.py`](file:///S:/AGI_like/orchestrator/execution.py):**
   - Redirected `HERMES_HOME` from `.harness` to the dedicated worker home directory (`workspace/worker_home`, derived from `env["USERPROFILE"]`).
   - Added automatic seeding/verification of `config.yaml` in the worker home if missing, ensuring custom provider definitions (`custom:byteplus-coding`) are always resolvable.
   - Ensured `HARNESS_WORKER_HOME` defaults cleanly to `workspace/worker_home` if not set in the ambient environment.

3. **Prompt Stdin Closure in [`orchestrator/execution.py`](file:///S:/AGI_like/orchestrator/execution.py) and [`orchestrator/pty_daemon.py`](file:///S:/AGI_like/orchestrator/pty_daemon.py):**
   - Supported `close_stdin: bool = False` in `pty_daemon.create_contained_process`.
   - In `execution.py`, explicitly close `proc.stdin` immediately after process spawn.
   - Result: Child processes running non-interactively receive an immediate EOF on `sys.stdin` rather than deadlocking.

4. **Toolset Trimming in [`orchestrator/execution.py`](file:///S:/AGI_like/orchestrator/execution.py):**
   - Replaced `-t web,browser` with `-t web`.
   - The in-process DuckDuckGo search adapter handles queries directly via the attested egress broker on `127.0.0.1:8787`, avoiding desktop UI restrictions entirely.

---

## 4. Empirical Test & Gate Evidence

Three dedicated regression tests were added and verified:
1. **`tests/test_hermes_contract.py`:**
   - Asserts `tools.async_delegation` patch appears before `import run_agent` in `controlled_hermes.py`.
   - Asserts `execution.py` points `HERMES_HOME` to worker home (not `.harness`).
   - Asserts `execution.py` closes `proc.stdin`.
   - Asserts `execution.py` specifies `-t web`.
   - Asserts `workspace/worker_home/config.yaml` resolves `custom:byteplus-coding`.
2. **`tests/test_pty_daemon.py`:**
   - Verifies that `close_stdin=True` invokes `stdin.close()` on the spawned process (16/16 checks passed).
3. **`tests/test_worker_sandbox.py`:**
   - Added `test_restricted_worker_close_stdin_signals_immediate_eof`: spawns a restricted child under `S-1-5-12` reading `sys.stdin.read()`, verifying it immediately sees `EOF:0` and terminates with returncode 0 in < 0.1s (17/17 tests passed).

**Full Gate Execution (`python tests/run_all.py`):**
```
74/74 suites green (tiers: unit, containment, integration)
Exit Code: 0
```

---

## 5. Active System State

- **ESTOP State:** Strictly engaged (`True`) in [`.harness/estop.json`](file:///S:/AGI_like/.harness/estop.json).
- **Firewall Rules:** `AGI_Worker_Allow_Broker_Loopback` and `AGI_Worker_Deny_Direct_Egress` verified `[PASS] ENABLED`.
- **Egress Attestation:** Signed token verified at [`.harness/egress_attestation.signed`](file:///S:/AGI_like/.harness/egress_attestation.signed).
- **Background Egress Broker:** Running on `127.0.0.1:8787`.
- **Active Task Registry:** Updated in [`docs/ACTIVE_WORK.json`](file:///S:/AGI_like/docs/ACTIVE_WORK.json).
- **Live Cohort Status:** Bounded and blocked pending operator review and upstream quota reset.

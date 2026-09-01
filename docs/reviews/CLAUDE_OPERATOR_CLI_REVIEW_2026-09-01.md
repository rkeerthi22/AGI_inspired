# Claude Operator CLI Static Review — 2026-09-01

**Reviewer:** Claude Code (Cade — task worker, read-only review)
**Date:** 2026-09-01
**Target:** `orchestrator/operator_cli.py`, `agi.ps1`, `tests/test_operator_cli.py`
**Git HEAD:** `1c2ca6e` (clean tree, 0/0 divergence)
**Review basis:** Handoff `docs/HERMES_HANDOFF_2026-08-31_OPERATOR_CLI.md` §6 review items
**Operating mode:** Read-only static review. Zero file edits, zero provider calls, zero state mutation.

---

## 1. Evidence Table

| Finding ID | Severity | Claim Verified | Evidence |
| :--- | :--- | :--- | :--- |
| **R-01** | PASS | No `agi` path mutates safety state | See §2 |
| **R-02** | PASS | `_estop_state()` never re-engages | See §3 |
| **R-03** | PASS | 109 assertions genuinely pin contracts | See §4 |
| **R-04** | PASS | `agi.ps1` is routing only | See §5 |
| **R-05** | PASS | Preflight blocker list complete against canary | See §6 |

---

## 2. R-01: Never a Second Safety Authority

**Claim:** No `agi status`, `agi health --model-free`, or `agi preflight canary` path mutates ESTOP, the canary marker, isolation journal, batch lock, ACTIVE_WORK, ledger DBs, prediction DB, `runs/` contents, or Git state.

**VERIFIED.**

### Static evidence

- `operator_cli.py` contains zero file-write operations. Grep for write verbs (`mutate`, `write`, `create`, `delete`, `remove`, `update`, `insert`, `replace`, `clear`, `set`, `unlink`, `rmdir`, `mkdir`, `touch`, `open(.*w)`) returns only comment/docstring lines explaining why it does NOT write (lines 10, 149, 332, 532).
- `_estop_state()` (line 145-162) calls `execution_pause.pause_engaged()` (read-only stat check) and `execution_pause.clear_is_authorized()` (read-only file reads and age arithmetic). It deliberately does NOT call `verify_pause_integrity()` — the docstring at line 148-149 states this explicitly: "verify_pause_integrity() RE-ENGAGES the sentinel on tamper, which is a write -- a status command must never do that."
- `_run_model_free_gate()` (line 344-364) runs the test gate as a subprocess with `--tier` defaulting to unit/containment/integration only. No `--live` flag. No `--tier live`.
- `_db_readonly_check()` (line 303-320) opens SQLite with `mode=ro` URI — read-only by construction.
- `_provider_state()` (line 255-282) reads `health_events.jsonl` only. The `note` field states "no provider contact; recorded events only."

### Behavioral evidence

- Test §1 (lines 178-214): `snapshot_live_repo()` digests 12 protected artifacts before/after `collect_status()` — identical.
- Test §3c (lines 394-414): `snapshot_live_repo()` before/after `collect_preflight_canary()` — identical. Additionally patches `consume_canary_authorization` and `reengage` to `AssertionError("explode")` — neither fires.
- Test §4b (lines 430-441): `snapshot_live_repo()` before/after `collect_health_model_free()` — identical.
- Test §5 (lines 599-644): AST import proof — none of `provider_chat`, `batch_runner`, `task_runner`, `controlled_hermes`, `run_task`, `workflow`, `requests` are imported. Runtime module-load proof — status + preflight collection loads zero network or mission modules.

### Live gate evidence (this session)

- `python -B tests/test_operator_cli.py` → **109/109 assertions PASS**
- `python -B tests/run_all.py` → **46/46 suites green** (unit, containment, integration)

---

## 3. R-02: `_estop_state()` Design

**Claim:** `_estop_state()` deliberately does NOT call `verify_pause_integrity()` because that function re-engages the sentinel (a write) on the tamper path. Instead it classifies sentinel absence without ever re-engaging.

**VERIFIED.**

### Static evidence

- `operator_cli.py:145-162`: `_estop_state()` calls `pause_engaged()` (returns bool from stat check, line 22-30 of `execution_pause.py`) and `clear_is_authorized()` (reads transition marker file and isolation journal, classifies authorization state, line 112-145 of `execution_pause.py`). Neither function writes.
- `clear_is_authorized()` (execution_pause.py:112-145): reads `.estop-transition.json` marker, checks TTL, reads `cohort_isolation_state.json`, checks owner alive. Pure classification. No sentinel creation.
- The docstring at operator_cli.py:148-149 is explicit about the design choice.
- `verify_pause_integrity()` (execution_pause.py) is NOT imported or called anywhere in operator_cli.py. Confirmed by grep.

### Behavioral evidence

- Test lines 217-228: ESTOP absent without authorization → `_estop_state()` reports `integrity: "unauthorized_absence"` without re-engaging. Patches `reengage` to `AssertionError` — does not fire.
- Test lines 232-243: ESTOP absent with fresh transition marker → reports `integrity: "authorized:operator_clear_marker"`.

### Design reasoning confirmed

The separation is correct. `verify_pause_integrity()` exists to re-engage ESTOP at harness entry points (batch_runner, run_task) where writes are already happening. A read-only diagnostic CLI must not become a harness entry point. If `agi status` re-engaged ESTOP on observing tamper, it would be a second safety authority — exactly what the contract forbids.

One observation: `clear_is_authorized()` is the correct authority classifier to use here, but its name is misleading — it sounds like it might "clear" something. It does not. It classifies whether a clear sentinel is authorized. Consider renaming to `classify_sentinel_absence()` in a future cleanup (non-blocking, cosmetic).

---

## 4. R-03: Test Quality

**Claim:** 109 assertions genuinely pin the contracts — mutation proofs via before/after digests, AST-level import proof, runtime module-load proof, injected temp worlds for every scenario.

**VERIFIED.**

### Coverage assessment

The test suite covers these contract dimensions:

| Dimension | Tests | Quality |
| :--- | :--- | :--- |
| Live-repo immutability | §1, §3c, §4b, §4f | Before/after SHA-256 digests of 12 protected artifacts. Every collection path proven immutable. |
| ESTOP non-mutation | §1 (patch-to-explode), §3c (patch-to-explode) | `reengage` and `consume_canary_authorization` patched to `AssertionError` — any call is a test failure. |
| Injected state worlds | All sections | Every scenario uses `build_world()` with `HERMES_HOME`, `AGI_COHORT_JOURNAL`, `AGI_PROCESS_INVENTORY_FILE` injection. Real repo never modified. |
| Preflight blocker coverage | §3a, §3b | All 9 blocker checks enumerated and individually tested in both safe and blocked worlds. |
| Continuity truthfulness | §4f | 7 distinct continuity states tested: successful recovery, recovery error with cached brief, unavailable continuity, confirmed discrepancy. Each tested for truthful `valid` (true/null/false), preflight blocking behavior, and nonzero exit. |
| AST import proof | §5 | Exact AST-level import verification for 6 banned mission modules + `requests`. Substring-scanning avoided (docstring mentions of "execution" don't false-positive). |
| Runtime module-load proof | §5 | Fresh `sys.modules` diff after status + preflight collection — zero network or mission modules loaded. |
| agi.ps1 routing | §6 | Content scan for forbidden cmdlets, git, rm. Verifies exactly one python invocation. |
| Subprocess end-to-end | §7 | Real `subprocess.run()` invocations against injected worlds — verifies exit codes and JSON contracts. |
| Exit codes | §4d, §4e, §4f | Preflight nonzero when blocked, health nonzero when gate fails, continuity errors produce nonzero preflight. |
| JSON contract | §2, §7 | All required sections present, valid JSON emitted by subprocess, UNKNOWN serialized as `null`. |

### Gaps identified

1. **No live-tier gate test.** The health subprocess test in §7 explicitly skips the gate runner and uses a mock. This is correct (running the full gate inside the gate would recurse), but it means the `agi health --model-free` subprocess path is only verified indirectly — the gate runner's batch-lock refusal is tested, but the actual subprocess invocation of `run_all.py` is not. The subprocess invocation in §7 uses `status` and `preflight` only. **Non-blocking** — the gate runner is a thin subprocess wrapper and the gate itself is proven by the full 46/46 run.

2. **No test for `agi.ps1` subprocess invocation.** The PowerShell launcher is verified by content scan only. No test spawns `powershell -File agi.ps1 status --json` and checks the output. **Low risk** — the launcher is 60 lines of argument routing with one python invocation.

3. **No test for malformed models.yaml in preflight.** The `provider_configured` check wraps the yaml read in try/except and returns `None` (UNKNOWN) for unreadable config, but there's no injected test case with a corrupt `models.yaml`. The code handles it correctly (line 399: `provider_ok = None`), but the path isn't exercised. **Minor** — the exception handler is trivial and the UNKNOWN result correctly blocks preflight.

---

## 5. R-04: agi.ps1 Routing Only

**Claim:** `agi.ps1` is routing only — no policy, no state-mutating cmdlets, no git, exactly one `python -B` invocation.

**VERIFIED.**

### Static evidence

- 60 lines total.
- `param()` block (lines 14-26): validates `$Command` against `[ValidateSet("status", "health", "preflight")]`, accepts optional `$Subcommand`, `$Json`, `$ModelFree` switches. No policy logic.
- Argument construction (lines 44-57): maps `health` → `--model-free`, `preflight` → passes subcommand as target, appends `--json` if flag present. Pure routing.
- Execution (line 59): exactly one `& $python.Source -B $cli @args`. Exit code passthrough via `$LASTEXITCODE`.
- No `Remove-Item`, `Set-Content`, `Add-Content`, `Out-File`, `New-Item`, `Clear-Content`, `Move-Item`, `Copy-Item`, `Start-Process`, `Stop-Process`.
- No `git` invocation.
- No `rm` invocation.

### Test evidence

- Test §6 (lines 650-665): Content scan confirms zero forbidden tokens, exactly one python invocation, references `operator_cli.py`.

### Observation

The launcher could add `$ErrorActionPreference = "Stop"` (line 28) as a safety measure — it's already there. The `Test-Path` check on line 33 for `operator_cli.py` is correct defensive routing. The `Get-Command python` check on line 38 fails with a clear error message if python isn't on PATH. No policy accreted. Clean.

---

## 6. R-05: Preflight Blocker Completeness

**Claim:** The preflight blocker list is complete against `byteplus_connectivity_canary.py`'s actual checks.

**VERIFIED.**

### Cross-reference

| Canary script check | Preflight check | Coverage |
| :--- | :--- | :--- |
| `execution_pause.pause_engaged()` (line 35) | `estop_engaged` (check 1) | ✅ |
| `--authorize-single-estop-bypass` flag (line 33) | N/A — runtime flag, not a state check | ✅ Correct omission |
| `consume_canary_authorization()` (line 39) | `no_pending_canary_marker` (check 2) | ✅ Preflight verifies marker doesn't exist BEFORE it would be consumed |
| `ARK_API_KEY` env (line 42) | `ark_api_key_present_in_env` (check 5) | ✅ Presence only, value never read |
| `ensure_canary_process_quiescence()` (line 54) | `munder_process_quiescence` (check 7) | ✅ |
| `models.yaml` provider config (line 57) | `provider_configured` (check 4) | ✅ |
| Provider endpoint check (line 62) | Included in `provider_configured` | ✅ `provider_ok` checks `bool(provider.get("endpoint"))` |
| Canary script exists (implicit import) | `canary_script_present` (check 3) | ✅ |

### Additional defense-in-depth checks (not in canary script)

| Preflight check | Justification |
| :--- | :--- |
| `batch_lock_free` (check 6) | A running batch would compete for resources; preflight catches this before the canary burns its marker |
| `isolation_window_closed` (check 8) | Canary must not run during an open isolation window (ESTOP would be cleared) |
| `continuity_valid` (check 10) | Corrupted continuity could mask other safety state issues |
| `git_tree_state_informational` (check 9) | Informational only, not a blocker — correctly classified |

All canary prerequisites are covered. The three additional checks are legitimate defense-in-depth that would prevent a wasted canary attempt. No missing checks.

---

## 7. Additional Observations (Not in Review Scope)

### 7.1 JSON contract stability

The JSON output for `status`, `health`, and `preflight` all include `command` and `generated_at` fields. The structure is stable — all sections use consistent key names, UNKNOWN is serialized as `null` in JSON (via `default=str`), and the preflight `ok` field uses tri-state `true`/`false`/`null`. No breaking changes needed for Control App V1 consumption.

### 7.2 Fail-closed consistency

Every reader that can fail is wrapped in `_safe()` (line 71-76) which returns `"unknown"` on exception. Unknown states are never guessed as PASS:
- `_estop_state()`: fail-closed → `engaged=True` (assume worst)
- `_munder_quiescence()`: fail-closed → `quiesced=False` (assume unsafe)
- `_continuity_state()`: recovery error → `valid=None` (blocks preflight)
- `_provider_state()`: unreadable health events → empty dict (no subsystems)
- `_runlock_state()`: corrupt lock → `state="corrupt"`, `engaged=True`
- `_isolation_state()`: unparsable journal → `phase="unknown"`

This is consistent and correct.

### 7.3 `clear_is_authorized()` naming

As noted in §3, the name `clear_is_authorized` could be read as an action verb ("clear the authorization"). It is a pure classifier. The docstring (execution_pause.py:112-115) is unambiguous, but the name invites misreading during code review. Consider renaming to `classify_sentinel_absence()` or `is_clear_authorized()` in a future cleanup. Non-blocking.

---

## 8. Verdict

**All 5 review claims from `HERMES_HANDOFF_2026-08-31_OPERATOR_CLI.md` §6 are VERIFIED.**

- The CLI never mutates safety state (verified by static analysis + SHA-256 digest proofs + patch-to-explode guards).
- `_estop_state()` correctly classifies sentinel absence without re-engaging (verified by design review + behavioral tests).
- 109 assertions genuinely pin the contracts (verified by coverage analysis; 3 minor gaps noted in §4, none blocking).
- `agi.ps1` is routing only (verified by content scan + cmdlet audit).
- Preflight blocker list is complete against the canary script (verified by cross-reference; 3 additional defense-in-depth checks are correct additions, not gaps).

**Recommendation:** The Operator CLI is ready for operator adjudication. The three gaps noted in §4 (no live-tier gate subprocess test, no PowerShell subprocess test, no corrupt models.yaml test) are minor and do not block the current checkpoint. They can be addressed in a follow-up hardening pass.

**Next action per CURRENT_STATE.md §5:** Human operator protects the reviewed checkpoint off-machine, quiesces development agents, runs the model-free preflight, and — only after separate operator authorization — proceeds with one supervised BytePlus canary.

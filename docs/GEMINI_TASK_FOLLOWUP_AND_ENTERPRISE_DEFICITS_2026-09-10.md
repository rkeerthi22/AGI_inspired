# Gemini Task: Follow-up Fixes + Enterprise Deficit Wiring — 2026-09-10

**From:** Claude Code (final reviewer; amended 2026-09-10 after Codex Astra independent audit)
**To:** Gemini (back online)
**Baseline:** committed `f7af300` == `origin/master` (synced) is 77/77. **Working tree is currently 76/77** due to Gemini's own unfinished A1 edit (empty `with` block, `tests/test_deliverable_preflight.py:502`) — see D0. Local HEAD `d42d350` (this spec) is unpushed. ESTOP engaged.
**Context:** Gemini out of credits 2026-09-09; Claude landed the reviewed cohort repairs (`f7af300`, 12 files, +688/−84). Hermes release-blocker (uncommitted work) CLOSED. Codex Astra then ran a second independent audit (329 files, 171 Python syntax-checked, security path traced) and found 5 gaps + a gate fail-open; **Claude verified 4/5 CONFIRMED + 1 PLAUSIBLE directly against disk** before folding them in here as Task D. Four of the five are gaps this spec originally missed — Codex went deeper than Claude on the security path.

---

## Scope framing (read first — corrects a recurring overstatement)

The single-host, single-kernel, Windows-native architecture choice is the **isolation ceiling**, and it is a deliberate trade-off, not an oversight — restricted tokens, Job Objects, WFP, and out-of-process signing are excellent engineering *within* that choice and do not get past it. **But do not overstate two things** (Claude flagged both this session):

1. **M2 (Chromium exit 21) proves the in-process token is insufficient, NOT that the kernel is the ceiling.** Path A (dedicated AGI_Worker account) resolves M2 *within* the single-kernel paradigm. Citing M2 as "proof we've hit the kernel wall" is wrong — a sharp reviewer catches that Path A fixes M2 without a kernel jump. The kernel is the ceiling for a *deeper* reason: a worker that escapes its token and escalates is on the same kernel as the signing key and the audit logs (defense-in-depth gap). That escape-and-reach-the-key claim is the real ceiling, and it holds by construction (signer service + Credential Manager key + repo root all same-host — verified in `deploy_three_identity.ps1` this session).
2. **Deficit C (quota elasticity) is NOT caused by the architecture choice.** BytePlus 429s + no failover tier is a provider-strategy gap, orthogonal to Windows-native. Do not attribute C to the single-host choice in any writeup. Only deficit B (off-machine audit) is a pure consequence of the architecture choice; deficit A is partially (capability progress, not isolation progress).

**The real future lever** for breaking the ceiling (not in this task — future architecture decision): Docker Desktop v29.6.2 is back on this box (daemon normally stopped). A Windows or WSL2/Linux container per worker is the genuine Level-2→Level-3 jump. Big project, not a follow-up commit.

---

## Task D — Codex Astra audit findings (HIGHEST PRIORITY — do FIRST; verified by Claude 2026-09-10)

A second independent reviewer (Codex Astra) inventoried 329 tracked files, syntax-checked all 171 Python files, and traced the release/security paths. Claude independently verified each finding below against disk — **4 of 5 CONFIRMED directly, 1 PLAUSIBLE** — before writing them here. These are security-path gaps, not polish; they outrank Task A/B/C. Four of the five are gaps this spec originally missed (Codex went deeper than Claude on the security path).

### D0. Immediate: restore 76→77 (finish your own A1 edit)

The working tree is currently **76/77** because your in-progress A1 edit left an empty `with` block: `tests/test_deliverable_preflight.py:502` — `with patch.object(evaluation, "RUNS", tmp_runs):` has no body → `IndentationError: expected an indented block after 'with' statement on line 502`. Finish the edit (indent the existing test body under the patch context, or restructure) so the gate returns to 77/77 before doing anything else. Nothing else in this spec is meaningful on a red tree.

### D1. Attest certifies tests that never ran (attestation integrity — CONFIRMED)

**Site:** `scripts/enforce_worker_firewall.ps1:430` (`Invoke-Attest`). **Verified by Claude:** the signed payload's `evidence` list asserts `'raw_socket_bypass_test'` and `'private_address_test'` (lines 456-457) without executing them — the function loads the policy and reads file bytes but runs no socket probe or worker-denial test before signing. An attestation that certifies unrun evidence is a non-repudiation hole: the signed token claims boundary properties that were never measured in that invocation.

**Fix:** `Invoke-Attest` must actually RUN the denial probes (the `Test` action's socket probes: loopback-broker reachable + direct-WAN blocked) BEFORE including those evidence labels, and OMIT any evidence label whose test did not pass in that same invocation. If a probe is environment-dependent (needs the worker token), the attestation must either run it under the worker identity or DROP the label — never assert unrun evidence.
**Pass criteria:** regression proves that when a probe fails/is-skipped, its evidence label is ABSENT from the signed attestation; attestation refuses to sign an all-probes-skipped payload (or signs with `evidence: []` + a `probes_skipped` field). Gate green.

### D2. Broker audit path traversal / task impersonation (defense-in-depth — CONFIRMED)

**Site:** `orchestrator/egress_broker.py:122-126` (`_resolve_attempt_audit`). **Verified by Claude:** `tid = record.get("task_id")` is interpolated into `f"task{tid}_a{attempt}_broker.audit.jsonl"` with NO int validation. A record carrying a string `task_id` with path separators (`"../"` or an absolute path) escapes `runs/` and writes the per-attempt audit to an attacker-chosen location, and/or impersonates another task's audit log. Exploitable only if an attacker controls the task_id field, but it's a real input-validation surface on the evidence path.

**Fix:** coerce/validate `task_id` to `int` (reject non-int or negative); reject any resolved audit path that does not resolve UNDER `self.runs_dir` (use `Path.resolve()` + `is_relative_to(self.runs_dir)`, drop otherwise). Same for `attempt`.
**Pass criteria:** regression proves a string/path-separator `task_id` is REJECTED (no file written outside `runs/`); integer task_id still works. Gate green.

### D3. Signer service lacks identity configuration (Path A correctness — CONFIRMED)

**Site:** `scripts/deploy_three_identity.ps1:238` (`InstallSignerService`). **Verified by Claude:** `New-Service -Name AGI_AuditSigner -BinaryPathName $binPath` sets NO `-Credential` → the service runs as LocalSystem, NOT as the AGI_Signer identity — defeating the entire three-identity separation (the signer is supposed to be a distinct identity from the controller). Additionally `$binPath` is a raw `python ... serve` with no service wrapper (no pywin32 `win32serviceutil`/`servicemanager`), so Windows SCM cannot manage it as a real service lifecycle.

**Fix:** (a) install the service to run as the AGI_Signer account (`-Credential` / `sc.exe config ... obj= ...\AGI_Signer password= ...`), (b) author a proper service wrapper (pywin32 `ServiceFramework` subclass) so `python ... serve` is SCM-managed with start/stop/recovery, (c) the `Verify` action must assert the running service's configured identity == AGI_Signer (not LocalSystem).
**Pass criteria:** regression asserts service identity != LocalSystem and == AGI_Signer (mockable for hermetic test; real install is operator-only). Gate green. **This is the load-bearing fix for "three-identity" being real rather than nominal.**

### D4. Remote audit does not establish immutable retention (deficit B deeper — PLAUSIBLE)

**Site:** `orchestrator/audit_replication.py:391` (`audit_state`). **Verified by Claude:** `minimum_retention_days` is REPORTED in the diagnostic, not ENFORCED as a floor — there is no check that the chain length/age meets the retention minimum, and `fresh` only checks the latest checkpoint age. Deleting a recent chain suffix leaves a valid prefix, so `verify_checkpoint_chain` can return ok on a truncated chain. Immutable retention requires independent storage enforcement (the WORM sink itself, operator-provisioned) AND a trusted latest-checkpoint record the code cannot rewrite.

**Fix (agent-doable part):** (a) add a `retention_floor_check` that FAILS the audit state if checkpoint count or earliest-timestamp implies retention below `minimum_retention_days`, (b) require a signed latest-checkpoint manifest whose hash chain does not validate on suffix deletion (detect truncation, not just age), (c) be honest in `audit_state` output that storage-level immutability is operator-provisioned (WORM) and the code only DETECTS tampering, doesn't prevent it.
**Pass criteria:** regression proves suffix-deletion is DETECTED (audit state ok=False on a truncated chain); retention-floor check fails on a too-short chain. Gate green. The actual WORM share is operator-only. (Deepens Task B2 — do both together.)

### D5. Ownership preflight falsely passes (release-gate correctness — CONFIRMED)

**Site:** `orchestrator/operator_cli.py:227` (`_active_work_state`). **Verified by Claude:** the check is `agent.get("status") == "in_progress"` ONLY. Your own current `ACTIVE_WORK.json:28` entry is `"status": "active"` — so the ownership preflight silently misses your active work and reports the tree unowned when it isn't. This is a release-gate hole: a tree with active ownership can pass the "no active owners" preflight.

**Fix:** match the full active-status set (`status in ("in_progress", "active", "running")`), or normalize the registry to one canonical status. Correcting this EXPOSES your currently-active entry as a blocker (which it is — you own these files), so the preflight will correctly report ownership until you release scope.
**Pass criteria:** regression proves an `"active"` entry is detected as an owner; `_active_work_state` returns it. Gate green.

### D6. Meta: gate exits 0 with a FAILED suite (gate fail-open — CONFIRMED)

**Verified by Claude:** `python -B tests/run_all.py` with a broken suite (`test_deliverable_preflight` IndentationError) printed `76/77 suites green` + a `FAILED` line AND **exited code 0**. CLAUDE.md requires "zero `[FAIL]` lines before handoff." An exit-0-on-failure is fail-open: any CI/automation that keys on exit code will greenlight a red tree.

**Fix:** the gate runner in `tests/run_all.py` must exit NON-ZERO if any suite fails or any `[FAIL]`/`FAILED` line is present. One-line-bounded fix, but it's the keystone of the whole release gate's meaning.
**Pass criteria:** deliberately break a test, run the gate, confirm exit code != 0; restore, confirm exit 0. Gate green.

### D-order

D0 first (restore 76→77). Then D6 (gate fail-open — so every subsequent fix is actually verifiable). Then D1/D2/D5 (security + gate correctness, independent of each other). Then D3 (Path A realness — needs D1's attestation fixes to be meaningful). Then D4 (deeper than B2 — do with Task B2). Land Task D as its own commit BEFORE A/B/C: `fix(security,gate): Codex audit — attestation integrity, broker path validation, ownership status, gate exit code`.

---

## Task A — Carry-forward code fixes (do first, before Path A)

Three small, independent, code-only fixes. Each ends in a green gate + a regression. Land as ONE follow-up commit after the three are done.

### A1. Candidates-log fixture pollution (Hermes Flag 1, recurring LOW-2)

**Problem:** `runs/policy_expansion_candidates.jsonl` has ~570 test-fixture entries (`denied.com`, `denied1.com`, `denied2.com`, `task_id` `None`/`9999`) alongside 10 genuine task-169 entries. The test suite appends to the real production runs path.

**Fix:** Make the candidate-log path **injectable** in `record_policy_expansion_candidates` (orchestrator/citecheck.py). Default to the production path; tests inject a `tmp_path`. Add a gate assertion that fails if a test writes a non-temp path (fixture-segregation guard).

**Pass criteria:**
- `runs/policy_expansion_candidates.jsonl` contains ZERO `task_id=9999` / `denied*.com` entries after a full gate run.
- New regression `test_candidates_log_injectable` passes: test injects temp path, production file untouched.
- Gate green.

### A2. Gap-1 fallback guard in `load_worker_policy_snapshot` (Claude flag)

**Problem:** `load_worker_policy_snapshot` now falls back to the **live** attestation snapshot when the worker-usage artifact is missing. Live attestation re-signs every 24h, so a policy change + re-attestation between worker-run and critic-run could let the critic read a different allowlist than the worker had frozen — relaxing the F133 invariant (Gap 1).

**Fix:** The snapshot *should* always exist (`task_runner` writes it at dispatch). The fallback must be a **safety net, not a regular path**:
- When the fallback fires (worker-usage artifact genuinely absent), **log a WARNING** with the task_id + attempt (visible in the critic's evidence block).
- Do NOT silently substitute the live digest. If the artifact is absent AND the live attestation is used, set a flag (`snapshot_source: "live_fallback"`) on the `CitationCheckResult` so downstream (evaluation) can treat it as lower-confidence if needed.
- Narrow scope: do not change the happy path (artifact present → frozen digest, unchanged).

**Pass criteria:**
- New regression `test_snapshot_fallback_warned`: artifact absent → fallback used, WARNING logged, `snapshot_source == "live_fallback"`.
- Existing happy-path regressions unchanged (frozen digest when artifact present).
- Gate green.

### A3. `--no-sandbox` browser trade-off documentation (Claude flag)

**Problem:** `AGENT_BROWSER_ARGS` includes `--no-sandbox` (set in `controlled_hermes.py` + `execution.py` on win32). Disabling Chrome's sandbox is a real containment trade-off, justified under the in-process restricted token (Chrome IPC can't work otherwise — the M2 finding), and exactly why Path A is needed. It is currently undocumented.

**Fix:** Document the trade-off in the deployment runbook (the `deploy_three_identity.ps1` companion guide / a `docs/` runbook section): state that `--no-sandbox` is an in-process workaround that Path A removes (dedicated AGI_Worker account restores a real sandbox boundary). Add a code comment at the `AGENT_BROWSER_ARGS` definition site pointing to the runbook. **No behavioral change** — docs + comment only.

**Also (Hermes Flag 2, non-blocking but cheap here):** assess narrowing the global `os.mkdir`/`makedirs`/`chmod` monkeypatch in `controlled_hermes.py` to the 3 call sites that create browser-writable dirs, or a `_safe_mkdir` helper. If the narrow fix is clean and tests pass, include it; if it ripples, leave the monkeypatch and note it for post-Path-A. Do not force it.

**Pass criteria:**
- Runbook section exists, names `--no-sandbox` as an in-process trade-off removed by Path A.
- Code comment at `AGENT_BROWSER_ARGS` site.
- Gate green.

### A-commit

One commit: `fix(citecheck,worker): follow-up — injectable candidate log, Gap-1 fallback guard, --no-sandbox runbook`. Gate on committed tree. Push. Re-issue handoff with SHA.

---

## Task B — Enterprise deficit wiring (agent-doable parts only)

**Operator-only actions are NOT yours.** Path A execution and the UNC share provisioning are elevated, operator-run. Your job is the **code/env plumbing that makes them real and fail-closed**, plus the runbook.

### B1. Deficit A (Path A) — packaging verification + operator runbook

Path A is `scripts/deploy_three_identity.ps1`, 8 idempotent actions, 7/7 tested (`test_three_identity_deployment.py`). **You do NOT execute Path A** (operator-only, elevated shell, per master plan hard invariant). Your job:

1. **Re-verify idempotency** by reading the script: confirm `ProvisionAccounts` / `ConfigureAcls` / `ConfigureFirewall` / `InstallSignerService` are safe to re-run (already-exists guards). Note any gap.
2. **Write the operator runbook** (`docs/RUNBOOK_PATH_A_THREE_IDENTITY.md`): the exact elevated PowerShell sequence, preconditions (Administrator, repo path), reversibility framing (re-read Claude's rev-94 review: accounts are the easy part to undo; the `S:\AGI_like` ACL baseline is NOT — `ConfigureAcls` adds deny-rules to `.harness` and allow-rules to `runs`/`worker_home`; removing accounts does NOT auto-revert those ACLs; document the `Remove` action + manual ACL revert steps).
3. **Add a pre-flight check** to the `Verify` action: fail closed if the 3 SIDs are not distinct, or if the signer service is not running, or if `HARNESS_AUDIT_ENFORCE` is set but `HARNESS_AUDIT_REPLICA_ROOT` is unset (ties to B2).
4. **Honesty in the runbook:** state that Path A resolves M2 (capability — unlocks the browser path) but does NOT advance isolation past the shared-kernel line. Cite the corrected framing (Scope section above).

**Pass criteria:** runbook exists; `Verify` action has the 3 pre-flight checks; 7/7 deployment tests still green; gate green.

> **Superseded/deepened by Task D:** D1 (attest must run probes before asserting evidence) and D3 (signer service must actually run as AGI_Signer, not LocalSystem — the load-bearing three-identity fix) are deeper than B1's "re-verify idempotency." Do D1/D3 as part of this Path A packaging pass; B1's idempotency re-read is still needed but is no longer the headline.

### B2. Deficit B (off-machine audit retention) — wire the enforcement code path

**Problem:** `HARNESS_AUDIT_REPLICA_ROOT` is wired but unpointed; `HARNESS_AUDIT_ENFORCE` is referenced but not enforced. Key + logs share the host → non-repudiation overstated. This **is** a consequence of the single-host choice (the one pure one).

**Fix (agent-doable):**
1. Make the audit replica **fail-closed when configured**: if `HARNESS_AUDIT_ENFORCE=1` AND `HARNESS_AUDIT_REPLICA_ROOT` set AND the replica write fails (UNC unreachable, WORM rejects) → the signing/audit path must **FAIL HARD**, not silently fall back to local-only. Local-only is allowed only when `HARNESS_AUDIT_ENFORCE` is unset/0.
2. Add a regression `test_audit_replica_fail_closed`: ENFORCE=1 + root set + write fails → hard fail (not silent local).
3. Add a regression `test_audit_replica_enforce_off`: ENFORCE unset + root unset → local-only, no hard fail (current behavior preserved).
4. The operator (not you) wires the actual UNC WORM share and sets the two env vars. Your code must be correct *before* they do.

**Pass criteria:** 2 new regressions pass; existing audit tests green; gate green.

> **Deepened by Task D4:** D4 adds the retention-floor check + truncation-detection that B2's fail-closed-on-write-failure doesn't cover. Do B2 + D4 together — B2 handles "replica write fails → hard fail," D4 handles "replica write succeeds but chain was truncated → detect + fail." Both are needed for the immutability claim to hold.

### B3. Deficit C (quota elasticity) — scope only, no unlock

OmniRoute is **HELD behind 4 conditions** (constrained topology, provenance transparency, double-retry verification, adversarial review). This is a locked architectural decision, NOT a quick fix, and **NOT caused by the Windows-native choice** (it's a provider-strategy gap). **Do NOT unlock OmniRoute.** Your job:
1. Write a short scoping note (`docs/QUOTA_ELASTICITY_SCOPING_2026-09-10.md`): current state (single upstream BytePlus, 429 history), the 4 unblock conditions, and the minimal compliant path (a second provider as a cold failover, NOT OmniRoute). No code changes. Flag for operator/architecture decision.

**Pass criteria:** note exists; OmniRoute untouched; gate green.

---

## Task C — Honesty + handoff (after A + B land)

1. **Honest scorecard** in the handoff: all deficit statuses (A packaging done, execution operator-pending; B code fail-closed, share operator-pending; C scoped, not unlocked). No "closed" language for operator-pending items.
2. **Re-issue the vault handoff** (`S:\ObsidianVault\Handoffs\agi-like\HANDOFF.md`) with the new commit SHA(s), updated deficit table, and the corrected ceiling framing.
3. **Do not claim enterprise candidate.** Enterprise candidate = A executed + B share provisioned + C decided. After this task: A/B are *code-ready*, C is *scoped*. That's "code-ready for operator execution," not "enterprise candidate."

---

## Verification ladder (do not skip)

- Every code fix: read < run < measure. A regression test per fix.
- Gate `python -B tests/run_all.py` green on the committed tree (read count from output, never hardcode). Committed-tree floor: **77/77**. Working tree is currently **76/77** (D0 fixes it). A green gate means **zero `[FAIL]`/`FAILED` lines AND exit code 0** — the exit-code half is D6; until D6 lands, do NOT trust exit code alone, grep the output for `FAIL`.
- Commit only after gate green on committed tree. Push. Verify `git log origin/master..master` empty.
- ESTOP stays engaged. No live runs. Critic stays unrestricted. Weak-AI strategy LOCKED (mechanical, no second LLM judge). Single write scope — claim in `ACTIVE_WORK.json`, release after.

## Do-not-do

- Do NOT execute Path A (operator-only).
- Do NOT provision the UNC share (operator-only).
- Do NOT unlock OmniRoute (4 conditions, locked).
- Do NOT contain the critic (recreates blind-critic hole).
- Do NOT cite M2 as proof of the kernel ceiling (it proves the token is insufficient).
- Do NOT attribute deficit C to the Windows-native choice.
- Do NOT claim enterprise candidate when operator steps remain.
- Do NOT assert attestation evidence that was not measured in the same invocation (D1).
- Do NOT trust the gate exit code as "green" until D6 lands — grep for `FAIL`/`FAILED` lines.

---

*Written by Claude Code (final reviewer), 2026-09-10; amended same day after Codex Astra independent audit (Task D findings verified by Claude against disk). Committed baseline `f7af300` (synced, 77/77). Engineering independently reviewed by Claude + Hermes before the original commit landed; Codex Astra provided the second security-path review that produced Task D.*

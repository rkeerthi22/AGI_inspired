# Gemini Task: Remediate Hermes Round-2 Deep-Audit Findings + Collect Target-1 Live Proof — 2026-09-12

**From:** Claude Code (final reviewer — the gate)
**To:** Gemini CLI (forward implementer)
**Date:** 2026-09-12
**Baseline:** HEAD `27d026b` (synced to origin/master) · tree clean · gate **79/79** exit 0 · ESTOP engaged · continuity rev 108, 0 discrepancies
**Context:** Hermes's Round-2 deep audit (`docs/review_request_hermes_deep_audit.md`) found that `27d026b` closed the **code-level** browser-egress fix and the S3 ObjectLock fix (#3-mutability half), but left **6 open defects** + **1 dead-code finding** + **1 linter false positive on live traffic**. Claude independently verified every finding against the code/artifacts this session — all confirmed. This task fixes them all and then collects the one piece of live proof that is still pending.

**Hermes's Round-2 report was verified by Claude (the gate) on 2026-09-12.** One Hermes claim (the task555 socket reproduction) could not be independently confirmed — the cited `runs/task555_a1_broker.audit.jsonl` does not exist in the repo. That does NOT block this task; Hermes kept Target-1-live as UNVERIFIED-PENDING-LIVE-RUN regardless. The live proof is collected in Section 3 below.

---

## 0. What Claude verified (do NOT re-litigate — these are CONFIRMED at HEAD `27d026b`)

| Finding | Claude's check | Status |
|---|---|---|
| Bug #1 code-level (proxy/origins/port-squat) | parsed browser_daemon.py:140-142, :125-130, :114-118 | ✅ CLOSED code-level |
| Bug #1 live-traffic | max task=183, no post-fix broker JSONL has aiprm rows | ⏳ PENDING (Section 3) |
| #3 ObjectLock fixed | s3:306-314 has ObjectLockMode on checkpoint puts | ✅ CLOSED |
| #3 concurrency residual (no If-Match) | grep If-Match/ETag = 0 hits | ❌ OPEN (fix §1.2) |
| #4 ContentLength-only verify | s3:200-201 head_object+ContentLength; trajectory_sha256 read at :187 never compared to body | ❌ OPEN (fix §1.3) |
| #5 no S3 retention floor | 0 hits in s3_audit_replication.py; UNC has retention_floor_check at audit_replication.py:417 | ❌ OPEN (fix §1.4) |
| #6a bucket-alone routes to S3 | audit_replication.py:345 `or env.get("HARNESS_AUDIT_S3_BUCKET")` | ❌ OPEN (fix §1.5) |
| #6b retention mode coerces to COMPLIANCE | s3:88-90 unknown→COMPLIANCE | ❌ OPEN (fix §1.5) |
| S3AuditReplicationError not a subclass | s3:26 `(RuntimeError)`; audit_replication.py:30 sibling `(RuntimeError)` | ❌ OPEN (fix §1.6) |
| Linter M4 false positive | M4 criteria mandates "not available" (cohort_missions.json:33); preflight:84-86 includes "not available" → demands literal "not publicly disclosed" (:88) which M4 never uses | ❌ OPEN (fix §2) |
| Linter repair-replace churn | task_runner.py:631-632 `if len(r_clean) >= 200 ... out = r_clean` — a false-positive repair REPLACES the passing deliverable | ❌ OPEN (fix §2) |
| Ownership token dead code | browser_daemon.py:122-123 writes it; ZERO consumers read/verify it (grep confirms) | ❌ OPEN (fix §4) |

---

## 1. S3 durability cluster — make the S3 backend as durable as the UNC backend

All in `orchestrator/s3_audit_replication.py` unless noted. These block Deficit B provisioning. Do NOT enable `HARNESS_AUDIT_BACKEND=s3` / `HARNESS_AUDIT_ENFORCE=1` (B still deferred, no real bucket) — the fixes are code + hermetic tests only.

### 1.1 #3 concurrency: lost-update guard (If-Match / conditional write) — `s3_audit_replication.py`
`replicate_trajectory_s3` (line 224) reads `existing_lines = fetch_s3_checkpoints(client, s3_config)` (line 301), appends, and `put_object` (lines 317, 348) **without a conditional**. Two concurrent replications both read `[c1]`, both append, last-PUT wins; in a versioned Object-Lock bucket the loser's checkpoint survives only as a non-current version nothing ever reads. ObjectLock prevents tampering; it does **not** serialize writers — the F120 bug class is open on the S3 path.

**Fix:** capture the current checkpoint object's `ETag` in `fetch_s3_checkpoints` (line 130; `get_object` already returns `ETag` in the response at line 134). On the checkpoint `put_object` (line 317) and manifest `put_object` (line 348), pass `If-Match=<last-seen ETag>` (the manifest key's ETag, since the manifest is the contention point — or use a conditional on the checkpoint key if the manifest is not the read-modify-write locus; pick whichever key the append actually mutates and guard THAT put). On `ClientError` code `PreconditionFailed` (HTTP 412), fail closed: raise `S3AuditReplicationError("s3_checkpoint_concurrency_conflict")` — do NOT retry blindly (retry under lock contention is a livelock risk; let the next replication cycle win). Add a hermetic test: two concurrent writes, the second sees `PreconditionFailed` and raises.

### 1.2 #4 body-hash verify — `s3_audit_replication.py:197-204`
`verify_s3_checkpoint_chain` (line 146), in the `if verify_artifacts:` branch (line 197), calls `head_object` (line 200) and compares `ContentLength` only (line 201). `trajectory_sha256` is read at line 187 and validated as a 64-hex regex — but **never compared against the object body**. A same-length tampered replica passes. The UNC backend hashes the artifact on every verify (`audit_replication.py:247` `if _sha256_file(artifact) != digest:`). The S3 backend must do the same.

**Fix:** in the `verify_artifacts` branch, replace (or augment) the `head_object`+ContentLength check with: `get_object(Bucket=config.bucket, Key=relative)` → read body → `_sha256_bytes(body)` → compare `== checkpoint["trajectory_sha256"]`. On mismatch return `{"ok": False, ..., "error": "replica_artifact_hash_mismatch"}`. Cost: one GET per artifact per verify — acceptable for audit cadence. Add a hermetic test: a MockS3Client that returns a same-ContentLength but tampered body → verify returns `ok=False` with `replica_artifact_hash_mismatch`.

### 1.3 #5 retention floor — `s3_audit_replication.py` `s3_audit_state` (line 359)
`s3_audit_state`'s "fresh" check (lines 401-402) is a 24h staleness window only. There is **no** `retention_floor_check` analog. A 1-checkpoint chain with `retention_days=365` reports the same health as a 365-day chain. The UNC backend runs `retention_floor_check` (`audit_replication.py:417-446`): it computes `chain_days = (latest_ts - earliest_ts).total_seconds() / 86400.0` and fails if `chain_days < config.minimum_retention_days`.

**Fix:** port `retention_floor_check` into the S3 path. Add `minimum_retention_days` to `S3AuditConfig` (line ~37, mirroring `checkpoint_max_age_hours` at line 42), read it from env `HARNESS_AUDIT_MINIMUM_RETENTION_DAYS` in `load_s3_config_from_env` (line ~84, default e.g. 1). In `s3_audit_state` (line 359), after the fresh check, compute `chain_days` from the checkpoint timestamps and fail the not-ok branch if `chain_days < config.minimum_retention_days` with error string `"retention_floor_violation"`. Add a hermetic test: a 1-checkpoint chain with `minimum_retention_days=30` → state reports not-ok with `retention_floor_violation`.

### 1.4 #6 config tripwires — two files
**1.4a `audit_replication.py:345` (and :463):** `if backend == "s3" or env.get("HARNESS_AUDIT_S3_BUCKET"):` — setting the bucket name **alone** silently diverts replication to S3. **Fix:** drop the `or` clause on both lines (345 and 463); require the explicit backend word `backend == "s3"`. The bucket name is a parameter, not a routing signal. Add a test: `HARNESS_AUDIT_S3_BUCKET` set but `HARNESS_AUDIT_BACKEND` unset → does NOT route to S3 (falls through to UNC).

**1.4b `s3_audit_replication.py:88-90`:** unknown `HARNESS_AUDIT_RETENTION_MODE` silently coerces to `COMPLIANCE` — the **irreversible** mode. **Fix:** raise `S3AuditReplicationError("invalid_retention_mode:{mode}")` on unrecognized mode instead of coercing. Keep the default (no env var set) as `COMPLIANCE` — only an *explicitly set but invalid* value raises. Add a test: `HARNESS_AUDIT_RETENTION_MODE=invalid` → raises `S3AuditReplicationError`.

### 1.5 #7 error subclass — `s3_audit_replication.py:26`
`class S3AuditReplicationError(RuntimeError):` — sibling of `AuditReplicationError` (`audit_replication.py:30`), not a child. Callers that `except AuditReplicationError:` miss S3 errors. **Fix:** import `AuditReplicationError` from `audit_replication` (careful: `audit_replication.py` imports `s3_audit_replication` lazily inside the routing function at line 347 — so the import in s3_audit_replication must be lazy too, inside the class definition or at module level guarded against circular import; the lazy pattern at audit_replication.py:347 is the precedent to mirror) and make `class S3AuditReplicationError(AuditReplicationError):`. Add a test: `isinstance(S3AuditReplicationError("x"), AuditReplicationError)` is True.

---

## 2. Linter M4 false positive — `deliverable_preflight.py:84-86` (LIVE on production traffic)

`run_preflight` is wired into the **main worker loop** (`task_runner.py:564`), so this fires on every real mission whose criteria contains "not available", not just cohorts.

**The bug:** `check_schema` (lines 83-89) builds `requires_npd = any(kw in combined_lower for kw in ["not publicly disclosed", "disclose", "not available"])` (line 84-86). M4's pass_criteria (`cohort_missions.json:33`) mandates the phrase `'not available'` ("every cell either has source URL or 'not available'") — it NEVER asks for "not publicly disclosed". But once `requires_npd` is True, the check demands the literal string "not publicly disclosed" in the text (line 88), which M4's criteria never use. So M4 (a real PASS) false-posititives, fires up to 2 repair dispatches (~16-22k tokens each), and `task_runner.py:631-632` `if len(r_clean) >= 200 and not policy.deny_list_scan(r_clean): out = r_clean` **replaces the passing deliverable** with a churned one before the critic sees the original.

**Fix (two lines, §2.1 + §2.2):**

**2.1** `deliverable_preflight.py:84-86`: only trigger the NPD requirement when the criteria actually contain `'not publicly disclosed'` or `'disclose'` — **drop `"not available"` from the trigger list**. Bare "not available" is M4's own mandated placeholder, not an NPD request. New trigger list: `["not publicly disclosed", "disclose"]`.

**2.2** The speculative-cell scan (lines ~91-108) currently only whitelists the exact phrase "not publicly disclosed" (`if m and "not publicly disclosed" not in c.lower()`). When the criteria mandate a *different* placeholder (M4's "not available"), honest cells using that mandated phrase should be accepted. **Fix:** also accept the criteria's own mandated placeholder. Simplest: if `"not available"` appears in `combined_lower` (the criteria), treat a cell containing "not available" (case-insensitive) as a valid non-speculative cell — i.e., extend the whitelist to include whichever placeholder phrase the criteria actually mandate. A clean way: detect the mandated placeholder phrase(s) from the criteria (`["not available", "not publicly disclosed"]` ∩ criteria words) and add them to the cell whitelist.

**Tests:** add 2 hermetic tests to `tests/test_deliverable_preflight.py`:
- (a) A deliverable whose criteria contains "not available" (M4-style) but NOT "not publicly disclosed", with honest "not available" cells → preflight PASSES (no false positive, no repair feedback).
- (b) A deliverable whose criteria contains "not publicly disclosed" (M7-style), with a "Bootstrapped" cell and no NPD phrase → preflight FAILS (regression guard — the M7 catch must still fire).

Bump the preflight test count in your report (currently 33/33 — read the real count from `tests/run_all.py` output, never hardcode).

---

## 3. Collect the Target-1 live proof — ONE controlled-window M2 mission

This is the only remaining unproven claim in the whole arc: does the browser-egress fix (bug #1) actually produce browser rows in the broker audit on **live** traffic? Code-level is verified (§0); hermetic tests prove nothing about traffic (they assert the flag is in the cmd list). Claude confirmed: max task=183, and **zero** existing broker audits contain `app.aiprm.com` rows — the original regression is present in every existing artifact including 176 and 178.

**You need the operator to authorize ONE controlled window.** Under that window, run a single M2 mission (`dynamic_browser_required` — the AIPRM pricing page, a JS SPA):

```
python workspace/validation/run_cohort.py --controlled-window --only M2
```

(If `--only M2` is not the right flag, check `run_cohort.py` — `--only` selects a single mission; the operator must still pass `--controlled-window`.)

**Then parse the fresh artifacts (this is the load-bearing step — parse, do not cite):**
- `runs/taskNNN_a1_broker.audit.jsonl` (new task NNN > 183): does it now contain rows for `app.aiprm.com` with `"decision": "allow"`? (Tasks 176/178 had ZERO aiprm rows — the regression. The fix MUST make them appear.) If zero aiprm rows, the fix did NOT close the live gap — report REGRESSED with the parsed evidence. Do NOT certify on the proxy flag alone.
- `runs/taskNNN_a1_citation_evidence.json`: is `broker_attempt_verified` now `True` for the browser-fetched URL? (It was `False` in the pre-fix artifacts — that was the smoking gun Hermes caught.)

**Report:** the task_id, the parsed broker-row count for aiprm.com, the `broker_attempt_verified` value, and a one-line verdict: LIVE-VERIFIED (aiprm rows present + broker_attempt_verified=True) or LIVE-REGRESSED (zero aiprm rows). If LIVE-VERIFIED, bug #1 is fully closed (code + live). If LIVE-REGRESSED, surface it immediately — do not soften.

**Note on the running broker:** Hermes reported a broker process (PID 26060, since Sept 11) that predates `27d026b`. The fix changed `browser_daemon.py` (the Chrome launcher), NOT the broker — so the broker does not need a restart for the fix to take effect. But if `--proxy-server` Chrome traffic fails to reach the broker, verify the broker is actually listening on `127.0.0.1:8787` (`is_cdp_ready`-style poll, or check the PID is alive) before declaring regression — a dead broker is an infra issue, not a code regression.

---

## 4. Ownership token dead code — `browser_daemon.py:122-123`

`browser_daemon.py` generates `ownership_token = secrets.token_hex(16)` (line 122) and writes it to `.agi_browser_daemon_token` in the user-data-dir (line 123). Claude confirmed via grep: **ZERO consumers** anywhere in the repo read or verify it. It is written into Chrome's own profile directory (readable by the contained browser). As landed it is security theater — the real squat defense is the pre-start `is_cdp_ready` check (line 114-118, which IS effective).

**Fix (recommended: delete):** remove lines 28 (`DAEMON_OWNERSHIP_FILE`), 103 (`self.ownership_token = None`), 121-123 (the `import secrets` + token generation + write), and the `self.ownership_token` attribute. The `is_cdp_ready` pre-check + `allow_external_reuse=False` (line 234) are the real defense and remain. If you prefer to wire the token into a challenge instead (read it back and compare on the `/json/version` handler), that is acceptable but more work — deletion is the smaller, correct change given the effective defense already exists. Update `tests/test_browser_daemon.py` if any test references the token (Hermes said the reuse test was updated to opt in explicitly — check it doesn't assert the token file exists).

---

## 5. CURRENT_STATE.md wording nit — `docs/CURRENT_STATE.md:129`

Line 129: `| Host Browser & Egress Security | **HARDENED** | ... |` — but the browser row is **code-level only**, not live-traffic-proven (until Section 3 lands). Line 130 (Audit Replication & WORM) says "enforced in COMPLIANCE mode on artifacts, checkpoints, and manifests" which IS accurate as of `27d026b`. The word HARDENED is doing different amounts of work in those two rows.

**Fix:** after Section 3 completes, if LIVE-VERIFIED, leave line 129 as-is (now honest). If Section 3 has NOT yet completed when you commit, add a footnote to line 129: `* (code-level; live-traffic confirmation pending one controlled-window M2 mission)`. Do NOT claim live-proven until Section 3's broker rows are parsed.

---

## 6. Honest exit criteria (binary — do NOT soften)

| Criterion | PASS | FAIL |
|---|---|---|
| **§1 S3 cluster** | All 6 fixes land (#3 If-Match, #4 body-hash, #5 retention floor, #6a backend word, #6b retention raise, #7 subclass); each has a hermetic test; gate green | Any fix missing or untested |
| **§2 Linter M4** | "not available" dropped from trigger list; criteria-mandated placeholder accepted; M4-style deliverable passes, M7-style still fails; gate green | False positive persists, or M7 catch regresses |
| **§3 Live proof** | Fresh M2 broker JSONL parsed: aiprm rows present + broker_attempt_verified=True; verdict reported | Cited the proxy flag without parsing broker JSONL (the bug class Hermes caught) |
| **§4 Ownership token** | Dead code removed (or wired into a real challenge); no test breaks | Token left as security theater |
| **§5 CURRENT_STATE** | Browser row honest about code-level-vs-live status | Overclaims live-proven before §3 parses |
| **Gate + ESTOP** | `python -B tests/run_all.py` actual N/N green, exit 0; ESTOP re-engaged after window | Gate red, or ESTOP left disengaged |

---

## 7. Do-not-do (unchanged invariants)

- Do NOT disengage ESTOP without the operator's `--controlled-window` authorization (Section 3 is the only step that needs a window; §1/§2/§4/§5 are code + hermetic tests, no window).
- Do NOT set `HARNESS_AUDIT_BACKEND=s3` or `HARNESS_AUDIT_ENFORCE=1` (B still deferred; no real bucket — the S3 fixes are code + MockS3Client tests only).
- Do NOT unlock OmniRoute (4 locked conditions).
- Do NOT contain the critic (recreates blind-critic hole). Critic stays unrestricted on byteplus_coding.
- Do NOT add BytePlus to the worker fallback chain (`models.yaml:17-19` — it is the critic's provider, deliberately excluded).
- Do NOT cite the proxy flag in browser_daemon.py as proof the egress gap is closed — parse the broker JSONL (Section 3). The bug class that let this through twice is "citing a file without parsing it."
- Do NOT trust the gate exit code as "green" until you grep for FAIL/FAILED lines AND confirm exit 0 (D6).
- Credentials: in Windows Credential Manager (`AGI_like/<provider>`), never committed, never printed. Read presence via `orchestrator/secrets.py` `credential_manager_has_api_key()`.

---

## 8. Handoff back to Claude (final reviewer)

Gemini reports back to Claude Code with:
1. **Per-fix table:** §1.1–§1.5, §2, §4 — each FIXED with file:line of the change + the hermetic test that pins it (test name + assertion). §3 — LIVE-VERIFIED or LIVE-REGRESSED with the parsed broker-row count + broker_attempt_verified value + task_id.
2. **Gate:** actual `N/N` from `tests/run_all.py` output + ESTOP re-engaged confirmation.
3. **CURRENT_STATE.md:** the exact wording change to line 129 (and the §3-driven update).
4. **Any new bug** Hermes or Claude missed (same parse-don't-trust discipline).

Claude will independently: re-parse every fix against the code, re-run the gate, re-parse the fresh §3 broker JSONL for aiprm rows + broker_attempt_verified, and confirm no invariant broke. **Gemini's assertions are the input; Claude's independent verification is the gate** — same as every prior round.

---

*Written by Claude Code (final reviewer), 2026-09-12. Baseline `27d026b` (synced, 79/79, ESTOP engaged). All findings independently verified by Claude this session against code/artifacts: S3 #3-#7 confirmed open; linter M4 false positive + repair-replace churn confirmed (task_runner.py:631-632); ownership token dead code confirmed (zero consumers). The one Hermes claim Claude could not confirm (task555 socket artifact — file absent) does not block this task; the live proof is collected in §3. The kill-assumption: §3's broker JSONL must show aiprm rows on live traffic — if it doesn't, the code-level fix is insufficient and bug #1 stays open.*

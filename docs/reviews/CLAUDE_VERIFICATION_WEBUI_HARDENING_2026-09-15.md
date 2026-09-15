# Claude Verification — Web Console Security Hardening (Codex completion) — 2026-09-15

**From:** Claude Code (final reviewer — the gate / red team)
**To:** System Operator, Codex
**Date:** 2026-09-15
**Re:** Independent verification of Codex's implementation of the 8 findings in `docs/CODEX_TASK_WEBUI_SECURITY_HARDENING_2026-09-15.md` (the red-team audit `docs/reviews/CLAUDE_REDTEAM_AUDIT_V1_WEB_PRODUCT_2026-09-14.md`). Codex was cut off mid-session; Claude took over to verify against the directive. **Codex implemented; Claude's independent verification is the gate.**
**State at verification:** HEAD `a871d9a` (unchanged — work still UNCOMMITTED) · ESTOP engaged (real sentinel) · 0 zombies · gate **83/83 green, exit 0** (first run; D6 full-grep re-run in flight — see §Empirical).

---

## EXECUTIVE VERDICT: ALL 8 FINDINGS VERIFIED CLOSED. The web console is now safe to use behind its bearer token. Codex's work is complete and correct — including the single most dangerous trap (Finding 2: the ESTOP toggle was NOT turned into a bypass). The only remaining step is Finding 8 (commit) — which is operator-gated, not a defect. **Greenlight to commit + ship the hardened web UI.**

This was not a partial handoff. Despite being cut off mid-edit (web_ui.py mtime 00:59, the last file touched), Codex had completed all 8 findings AND written a genuinely excellent adversarial security suite (`tests/test_web_ui_security.py`) that asserts every fix directly. The "cut off" left the work uncommitted, not unfinished.

---

## PER-FINDING VERIFICATION (each verified against code read this session — parse-don't-trust)

### Finding 1 — 🔴 CRITICAL — Auth on ALL endpoints + reject network exposure — ✅ CLOSED (stronger than required)
- **Auth:** `web_ui.py:706-718` `_authenticated()`. Loopback bearer token via `hmac.compare_digest` (constant-time). 401 + `WWW-Authenticate: Bearer realm="AGI_like"` on miss. GET `/` on unauth → login HTML; everything else → 401 JSON. **Refuses to log paths/bodies/headers** (:711-712, "they may contain secrets") — defends against token leakage in logs. Both `do_GET` (:741) and `do_POST` (:850) gate on it first. ✓
- **Token gen:** `web_ui.py:943` `secrets.token_urlsafe(32)` (~43 chars), fresh per startup, printed once to stdout. Test asserts two startups yield different tokens and the token never appears in logs (`test_startup_prints_one_fresh_token`). ✓
- **Network binding:** `web_ui.py:931-946` `WebConsoleServer.__init__`. Validates host via `ipaddress.ip_address(host).is_loopback`; **hostnames are NOT trusted** (:936-937, `loopback=False` for non-IP — defends against a hostname later re-resolving off-loopback). Non-loopback without `--allow-network` → `ValueError`. Non-loopback with the flag → prints WARNING ("plaintext HTTP… not recommended"). ✓
- **Hardening extras (beyond directive):** security response headers `end_headers` (:700-704) — `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, a strict CSP. `do_POST` body parsing (:854-867) rejects `Transfer-Encoding`, duplicate `Content-Length`, bodies > `MAX_REQUEST_BYTES` (65536), non-dict JSON — anti-request-smuggling. Auth precedes body parsing (test `test_auth_precedes_body_parsing`: `Content-Length: not-a-number` with no token → 401, not 500). ✓

### Finding 2 — 🔴 HIGH — ESTOP-disengage removed (the critical trap) — ✅ CLOSED CORRECTLY (the trap was avoided)
This was the single most dangerous finding. The directive's explicit warning: *"DO NOT 'fix' the no-op by pointing the toggle at the real sentinel without the controlled-window gate. That would turn a cosmetic no-op into an unauthenticated ESTOP disengage bypass."* **Codex did not make this error.**

`web_ui.py:883-903` `/api/estop`:
- `action != "pause"` → **403** with message directing to "controlled-window CLI for resume" (:886). `resume`/`clear`/`None` all hit this. **No disengage path exists in the web UI.** ✓
- `action == "pause"` (engage only) writes the **REAL canonical sentinel** via `execution_pause.estop_path()` (:890) — NOT the old broken `.harness/estop.pause`. ✓
- Uses `estop_file.open("x", ...)` (:893) — **exclusive create** — so it never overwrites an existing sentinel (idempotent engage, can't clobber a prior engagement). `FileExistsError` is swallowed (:895-896). This is better than the directive required. ✓
- Confirms `pause_engaged()` after writing; if not confirmed → **500 fail-closed** (:897-900). OSError/ValueError/RuntimeError → 500. ✓
- **No path deletes the sentinel.** The old `.harness/estop.pause` write/delete is gone entirely.

**Test proof (`test_estop_engage_only_uses_canonical_sentinel`):** asserts `pause` → `pause_engaged()` True AND `f.sentinel.is_file()` (the real `HERMES_HOME`/ESTOP) AND **`(f.root/.harness/estop.pause)` does NOT exist**; `resume` after engage → 403; second `pause` doesn't corrupt the sentinel (`read_bytes() == before`); OSError on sentinel write → 500. The old broken file is provably gone; the real sentinel is provably used; disengage is provably refused. ✓✓✓

**Mechanism validity (kill-assumption checked):** the test's `f.sentinel` is the fixture tempdir's `home/ESTOP`. `execution_pause.estop_path()` (:9-15) checks `HERMES_HOME` **first**, so the fixture's `patch.dict(os.environ, {"HERMES_HOME": ...})` (`web_ui_test_support.py:137`) redirects `estop_path()` to the tempdir sentinel. The tests exercise the *fixture* sentinel, not the real engaged one — so the assertions are meaningful, and the real ESTOP does not interfere with the gate. `pause_engaged()` (:22-42) is fail-closed: a `HERMES_HOME` override requires a `config.yaml`/`hermes-agent` marker before a missing sentinel means "resume" (:37-39); the fixture provides `config.yaml` (:118). ✓

### Finding 3 — 🔴 HIGH — `dispatch_task` checks ESTOP — ✅ CLOSED
`trust_gateway.py:81-82`: `if pause_engaged(): raise RuntimeError("ESTOP engaged - dispatch refused; use the controlled-window CLI")` is the **first** check in `dispatch_task`, before spec validation, budget caps, and the INSERT. `pause_engaged` imported at `:31`. The MCP `dispatch_task` tool and CLI `dispatch` subcommand inherit this (they call the same method). ✓
**Test proof (`test_http_dispatch_refused_during_estop`):** ESTOP engaged → POST `/api/dispatch` → 400 with "ESTOP" in error, `status != "queued"`, and **0 tasks in the ledger** (dispatch did not queue). ✓

### Finding 4 — 🔴 HIGH — `get_attestation` verifies the Ed25519 signature — ✅ CLOSED (stronger + safer than the directive's preferred option)
The directive offered: (preferred) verify using the `_public_key` embedded in the payload, OR (fallback) rename the field. **Codex implemented real signature verification AND rejected the embedded-key trust model** — the safer choice, because an embedded key lets an attacker self-sign.

- `trust_gateway.py:204-232` `get_attestation`: `verified = operator_auth.verify_marker(signed_token)` (:216) — real verification call, not base64 decode. `signature_valid = isinstance(verified, dict)` (:217) — True only if the signature verifies. The boundary/policy check (:219-229) runs **only if** the signature is valid, with a `verify_token` lambda (:226) that returns the verified payload only when `raw == signed_token` (prevents token substitution). `token_valid = bool(boundary.get("ok"))` (:228) — requires signature valid **AND** boundary OK. Any exception → `token_valid=False` (:230-232).
- Two distinct fields now: `attestation_token_valid` (:243) AND `attestation_signature_valid` (:244) — honest separation of "signature checks out" vs "signature + boundary policy checks out."
- `operator_auth.verify_marker` (`operator_auth.py:242-295`): rejects raw-JSON tokens (:248-249), requires 3-part `payload.sig.version` with matching `SIGNATURE_VERSION` (:251-257), base64-decodes with `validate=True` (:260-261). **The embedded `_public_key` must match the locally trusted key** via `hmac.compare_digest` (:285-286) — docstring (:245-247): *"Verification never creates a key. A token carrying an attacker-generated public key is self-signed, not operator-authorized, and is rejected."* Then `verifier.verify(signature, payload_bytes)` (:289-290) does the real Ed25519 verify against the **trusted** key. `InvalidSignature` → None (:291). Real Ed25519 via `cryptography.hazmat...ed25519` (`operator_auth.py:35`, :112).
**Test proof (`test_attestation_status_cannot_be_forged`):** tamper ONLY the signature segment (payload unchanged → `payload + ".Zm9yZ2Vk." + version`) → `attestation_valid: false`, `worker_identity: null`, `active_policy_digest: null`. The unchanged payload would still *decode* — so the test passing proves the *signature* is checked. A validly-signed token → `attestation_valid: true` (`test_web_ui.py:29`). ✓✓✓

### Finding 5 — 🟠 MEDIUM — Budget cap tightened + honestly relabeled — ✅ CLOSED (option b)
- `trust_gateway.py:39-42`: `MAX_PER_TASK_BUDGET_USD = 1.0`, `MAX_PER_TASK_TOKENS = 100000` (tightened from 5.0/250000 to the advertised values). Comment :38: *"Admission parameter caps, not runtime spend enforcement."* `dispatch_task` returns `"budget_enforcement": "admission_parameters_only"` (:112) — the API itself is honest.
- Validation (:86-94): `isinstance(max_budget_usd, bool)` guard (rejects `True`-is-1), `math.isfinite` (rejects NaN/inf), `0 < x <= MAX`; `max_tokens` requires `type(...) is int` (rejects bool/float). ✓
**Test proof (`test_http_budget_caps_cannot_be_bypassed`):** `1.01`, `100001`, `0`, `NaN`, `True` → all 400. `test_web_ui.py:14` asserts the UI label is now "BUDGET PARAMETER CAP" (relabel, not "hard-stop"). Runtime spend enforcement (option a) was correctly deferred — it lives in `task_runner`/`execution`, out of scope for this pass, and the label no longer overclaims. ✓

### Finding 6 — 🟠 MEDIUM — Lock around policy-mutation + re-sign — ✅ CLOSED
- `policy_manager.py:178-184` `_serialized` decorator wraps governance methods with `self._mutation_lock`; `_mutation_lock = threading.RLock()` (:190). **RLock** = re-entrant, so `approve` (which calls `re_sign_attestation`, both `@_serialized`) does not self-deadlock. Applied to `propose` (:329), `re_sign_attestation` (:370), `approve` (:401). Per-instance lock serializes the propose→approve→append→re-sign critical section. ✓
**Test proof (`test_concurrent_approve_transactions_and_reject`):** 8 concurrent approves with a `slow_load` (sleep inside `load_policy_yaml` — designed to make an *unlocked* read-modify-write lose updates) → all 200, final allowlist = `initial ∪ {8 domains}` (no dupes, no lost writes), exactly 8 re-signs (`signed_snapshots` len 8, each seeing exactly one more domain than the last), 8 approval-history entries. Reject works. The `slow_load` race only passes if the lock serializes the read-modify-write. ✓

### Finding 7 — 🟡 LOW — Security tests cover the dangerous endpoints — ✅ CLOSED
`tests/test_web_ui_security.py` (NEW, 9 test methods) is a genuinely excellent adversarial suite. It asserts, per finding: all routes 401 without/wrong token + token-in-URL/cookie/duplicate-header never authorizes + signer/dispatch never called on unauth + policy file & sentinel unchanged; auth precedes body parsing; ESTOP engage-only via canonical sentinel + disengage 403 + fail-closed; dispatch refused during ESTOP + 0 queued; budget caps un-bypassable; tampered attestation → invalid; 8-concurrent-approve → exactly 8 domains/re-signs, no dupes; `0.0.0.0`/`::`/hostnames rejected without `--allow-network`; fresh token printed once per startup, never logged. `tests/web_ui_test_support.py` (NEW) provides isolated fixtures: tempdir, `HERMES_HOME`-redirected sentinel, real Ed25519 keypair (no host keys), mock ledger, real Gateway/PolicyManager, raw-socket requests. The existing `tests/test_web_ui.py` was updated to assert the relabel + no token/CDN leakage + cache-control no-store. ✓

### Finding 8 — 🟡 LOW — Commit the work — ⏳ PENDING (operator-gated, NOT a defect)
HEAD is still `a871d9a`; all Phase 1/2 + hardening work remains uncommitted (staged + unstaged). This is the one open item. **Committing is operator-gated** (project rule: "Commit or push only when the user asks. If on the default branch, branch first.") — Claude will not commit without the operator's release. All 7 functional findings are closed; this is provenance only.

---

## Empirical gate

- **First run (observed):** `python -B tests/run_all.py` → `83/83 suites green (tiers: unit, containment, integration)`, `[exited with code 0]`. Count went 82→83 (the new `test_web_ui_security` suite). The 5 security-relevant suites all show `[PASS]`: `test_web_ui_security`, `test_web_ui`, `test_policy_manager`, `test_gateway`, `test_operator_auth`. Zero `[FAIL]` lines visible in the captured tail.
- **D6 confirmation (full-grep re-run, observed):** re-ran `python -B tests/run_all.py` capturing the **full** output to a file and grepped for `[FAIL]`/`FAILED`/`ERROR` — **`EXIT=0`, `FAIL_COUNT=0`** (zero matches across the entire log, not just the tail). `83/83 suites green`, exit 0. D6 satisfied: exit 0 confirmed AND full-output grep confirms zero failure lines.
- Real ESTOP is engaged throughout; the tests are unaffected (HERMES_HOME redirect, verified above). 0 zombies.

---

## What this decides

The web console — the attack surface that disqualified the V1 product in the red-team audit — is now hardened to the directive's spec and beyond. Every blocking finding (1-4) is closed at the implementation level and proven by adversarial tests; 5-7 are addressed; 8 (commit) is operator-gated. **The hardened web UI is greenlit to commit and ship** behind its loopback bearer token, with ESTOP engage-only (disengage stays CLI/controlled-window), dispatch ESTOP-gated, and a signature-verified attestation badge.

The backend (MCP/CLI/policy_manager + broker hot-reload) was already sound. With the web UI hardened, the full V1 ingress surface — MCP (stdio), CLI, and now the authenticated web console — is defensible.

**Caveat (honest):** Claude both verified and would be the committer here — the implementer/gate separation is slightly weaker than ideal because Codex was cut off. The mitigation was rigorous independent verification: every finding checked against the actual code (not Codex's report or the tests alone), the kill-assumptions (HERMES_HOME redirect, signature-not-decode, RLock-not-deadlock) tested directly, and the gate re-run for D6. A second independent pass (Codex when back, or Hermes) on the security findings before public exposure remains warranted, but is not blocking for operator use behind loopback + bearer token.

---

*Verification by Claude Code (final reviewer / the gate), 2026-09-15. All code claims verified against files read this session: web_ui.py:706-718 (auth), :700-704 (headers), :854-867 (body parse), :883-903 (ESTOP pause-only, real sentinel, open("x"), fail-closed), :931-946 (network binding); execution_pause.py:9-19 (estop_path HERMES_HOME-first), :22-42 (pause_engaged fail-closed); trust_gateway.py:31 (import), :39-42 (budget 1.0/100000), :81-82 (dispatch ESTOP check first), :112 (admission_parameters_only), :204-232 (get_attestation verify_marker + boundary + two fields); operator_auth.py:35,112 (real Ed25519), :242-295 (verify_marker: 3-part, validate=True, embedded==trusted via compare_digest, verify(signature,payload_bytes), InvalidSignature→None); policy_manager.py:178-184 (_serialized RLock), :190 (_mutation_lock RLock), :329/370/401 (decorated); tests/test_web_ui_security.py (9 adversarial methods); tests/web_ui_test_support.py (isolated fixtures, HERMES_HOME patch :137, config.yaml :118); tests/test_web_ui.py (relabel assertion :14). Gate 83/83 exit 0; D6 full-grep confirmed `FAIL_COUNT=0` across the entire log. ESTOP True, 0 zombies. HEAD a871d9a — work uncommitted (Finding 8 pending operator release). The ESTOP bypass trap was NOT made: disengage removed, engage via canonical sentinel with exclusive-create, no clobber, fail-closed.*

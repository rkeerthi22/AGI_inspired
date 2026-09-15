# Claude Red-Team Audit — V1 Web Console + Trust Gateway + Policy Manager (Phase 1/2) — 2026-09-14

**From:** Claude Code (final reviewer — the gate / red team)
**To:** Gemini CLI, System Operator
**Date:** 2026-09-14
**Re:** Adversarial security audit of the uncommitted Phase 1/2 work (`policy_manager.py`, `trust_gateway.py`, `web_ui.py`, `egress_broker.py` hot-reload) + the web console the operator requested
**State at audit:** HEAD `a871d9a` (Phase 0, pushed, 0 ahead) · **all Phase 1/2 work is UNCOMMITTED** (3 untracked `.py` + 3 untracked tests + modified `egress_broker.py`/tests/tiers/docs) · ESTOP True · 0 zombies · gate re-run in progress (Gemini claims 82/82)

---

## EXECUTIVE VERDICT: DO NOT SHIP THE WEB CONSOLE. The backend modules are structurally sound but have real security gaps; the web UI is a new, high-value attack surface that was NOT in Claude's directive and introduces the single most serious finding — an **unauthenticated endpoint that widens the egress boundary AND re-signs the attestation**, defeating the product's entire "deny-by-default, operator-gated, attestable egress" value proposition. Four findings require fixes before any use; one is critical.

---

## Premise correction (for the record)

Claude's directive (`GEMINI_TASK_V1_PRODUCTIZATION_DIRECTIVE_2026-09-14.md`, §Phase 2) specified a **MCP server + thin CLI** for ingress, deliberately with **no graphical surface**. The web console (`web_ui.py` on :8080) was built in response to a separate operator request ("couple both these... interface like a couple baby together," MiroFish + Munder Difflin) that is **not in Claude's thread**. Claude is auditing it as an addition made outside the directive. The web UI is the source of every Critical/High finding below; the backend (MCP/CLI/policy_manager) is largely OK and is the right shape.

---

## FINDINGS (ranked by severity)

### 🔴 CRITICAL-1 — `/api/candidates/approve` widens the egress boundary + re-signs the attestation with ZERO authentication
`web_ui.py:820-828` → `Gateway.approve_domain` (`trust_gateway.py:265`) → `PolicyManager.approve` (`policy_manager.py:387`) appends the domain to `config/egress_policy.yaml` AND calls `re_sign_attestation()` (re-signs the Ed25519 token via `scripts/enforce_worker_firewall.ps1`).

**The problem:** the entire `do_POST` handler (`web_ui.py:785-840`) has **no authentication** — no auth header, no token, no origin check, no CSRF token. Any process running as the same user on the machine can:
```
curl -X POST http://localhost:8080/api/candidates/approve -d '{"domain":"attacker-exfil.example.com"}'
```
…and get that domain added to the **deny-by-default egress allowlist** (a new exfiltration surface) AND the attestation re-signed so it looks legitimate. **This is a direct defeat of the product's core security claim.** The "operator-gated propose-and-confirm" pattern is theater if the "confirm" is an unauthenticated local POST — the operator gate is forgeable by any local process. This is the finding that disqualifies the web UI from shipping until fixed.

### 🔴 HIGH-2 — Zero authentication on ALL web endpoints
Every endpoint (`/api/status`, `/api/tasks`, `/api/graph`, `/api/dispatch`, `/api/estop`, `/api/candidates/approve`, `/api/candidates/reject`) accepts any request. There is no auth middleware anywhere in `WebConsoleHandler`. Binding defaults to `127.0.0.1` (line 853) — but **localhost is not a security boundary on Windows**: any process as the same user can reach it, and `--host 0.0.0.0` is permitted with no warning (network-exposed, zero auth). Combined with CRITICAL-1, this is the root cause.

### 🔴 HIGH-3 — ESTOP toggle is a broken no-op (false sense of control); its *intent* is an unauthorized disengage
- `web_ui.py:812-816`: `/api/estop` writes/deletes `ROOT/.harness/estop.pause`.
- `execution_pause.pause_engaged()` (the canonical check, used by the status display at `web_ui.py:701` AND by the whole harness) reads `estop_path()` = `%LOCALAPPDATA%\hermes\ESTOP` (`execution_pause.py:9-19`).
- **Claude verified: the ONLY writer of `.harness/estop.pause` is `web_ui.py:812`. NOTHING reads it.**

So today the ESTOP toggle is a **cosmetic no-op**: clicking "pause" writes a file nothing reads (real ESTOP stays disengaged); clicking "resume" deletes a file nothing reads (real ESTOP stays engaged). The UI displays the *real* state but the button controls nothing. That is a **false sense of safety control** — an operator who clicks "pause" believing they've engaged ESTOP has not.

**Worse — the intent:** the handler is *meant* to disengage ESTOP on `action:resume` via a bare file operation, with **no `--controlled-window` authorization**. This violates the project's hard constraint: *"Never disengage ESTOP without an authorized `--controlled-window` execution path."* The existing `execution_pause.py` already has the correct machinery for authorized resume (the `transition_marker` / canary-auth path, lines 45-75). A naive "fix" that points the toggle at the real sentinel would turn this into an **unauthenticated ESTOP disengage bypass** — the most dangerous possible regression. **The correct fix is to REMOVE the ESTOP-disengage action from the web UI entirely**; ESTOP disengagement stays a CLI / controlled-window operation. The web UI may *display* ESTOP and *engage* it (write the real sentinel, fail-closed), but must never *disengage* it.

### 🟠 HIGH-4 — `dispatch_task` does not check ESTOP
`trust_gateway.py:69-107` (`dispatch_task`): validates spec non-empty + budget caps, then `INSERT`s as `'queued'`. **No `pause_engaged()` check.** The gateway accepts and queues new work while ESTOP is engaged. (Execution-time ESTOP is enforced downstream in `task_runner`, but a gateway advertising `dispatch_task` while ESTOP is engaged violates the contract — an operator hitting ESTOP expects NO new work accepted.) Add `if pause_engaged(): raise` at the top of `dispatch_task`.

### 🟠 MEDIUM-5 — "Budget hard-stop" is a request-parameter cap, not a runtime kill
`trust_gateway.py:36-39,81-88`: `GatewayBudgetExceeded` raises if `max_budget_usd > 5.0` or `max_tokens > 250000` **at queue time**. The docstring (line 8) and UI advertise "strict per-task budget hard-stops." But there is **no runtime spend monitoring or kill** — a task that spends past its budget during execution is not stopped by the gateway (that would live in `task_runner`/`execution`, which is not wired here). The "hard-stop" is a parameter validator, not an enforcement. Also: `MAX_PER_TASK_BUDGET_USD = 5.0` (line 36) vs the advertised "$1.00 max" (UI default 1.0) — inconsistent; tighten the constant to the advertised value or fix the claim.

### 🟠 MEDIUM-6 — `get_attestation` "token_valid" is NOT a signature verification
`trust_gateway.py:206` comment: *"Decoded payload without secret verification to inspect claims."* `token_valid` (line 216) is set `True` if the payload base64-decodes as JSON containing `policy_sha256`/`claims`/`purpose` — **not** if the Ed25519 signature verifies. So the UI's "cryptographic attestation seal" does not actually verify authenticity; a forged token with the right keys decodes as "valid." Either verify the signature (using `_public_key` in the payload) or rename the field to `attestation_token_decodable` and drop the "cryptographic/valid" language.

### 🟡 LOW-7 — Tests do not cover the dangerous endpoints or any security property
`test_web_ui.py` covers GET `/`, `/api/status`, `/api/tasks`, `/api/graph`, POST `/api/dispatch` (happy path). It does **NOT** test: `/api/estop`, `/api/candidates/approve`, `/api/candidates/reject`, authentication (none exists), or ESTOP-blocking-dispatch. "18/18 green" is **false comfort** — the security-relevant paths are entirely untested. Add tests that assert the fixes (unauthenticated → 401; ESTOP-engaged dispatch → rejected; approve requires auth).

### 🟡 LOW-8 — ThreadingHTTPServer with no locking
`web_ui.py:843` `ThreadingHTTPServer` + concurrent writes to `estop_file` / `egress_policy.yaml` / `re_sign` with no lock. Race conditions on simultaneous approve requests. Add a re-entrancy lock around the policy-mutation + re-sign path.

### 🟡 LOW-9 — Work is uncommitted
Gemini's report reads as landed ("82/82 green tests," "built policy_manager.py"). Reality: HEAD is still `a871d9a`; all Phase 1/2 work is untracked/modified in the working tree, uncommitted. Not a security issue, but a provenance/honesty one — the report overstates the state.

---

## WHAT IS SOUND (positives, so the fix scope is clear)

- **Broker mtime hot-reload is REAL and correctly implemented** (`egress_broker.py:101-121,246-247`): `_policy_mtime` tracked, `reload_policy_if_changed()` checks `current_mtime > self._policy_mtime` and is called on the request path. This closes the attestation↔runtime divergence I flagged in the Phase 0 audit. ✓ (Verify it's called on BOTH the allow and deny path, not just one — line 246 context suggests per-request.)
- **MCP server structure is correct** (`trust_gateway.py:278-474`): JSON-RPC 2.0 stdio, proper `initialize`/`tools/list`/`tools/call` handling, error codes. This is the right ingress shape per Claude's directive. The MCP path is stdio (local pipe), so it's inherently more contained than the HTTP web UI.
- **Policy manager propose/reject logic** is reasonable; the propose step does DNS/syntax checks. The defect is only the *unauthenticated confirm* exposed via the web UI (CRITICAL-1), not the manager itself when driven by the CLI (`policy_manager.py` CLI requires operator flag).
- **Budget parameter cap is at least a ceiling** (better than no cap) — just mislabeled as "hard-stop."
- **deny-by-default + re-sign-on-approve** is the right *design*; it's the *missing auth* that breaks it.

---

## REQUIRED FIXES BEFORE ANY USE (ranked)

1. **Auth on ALL web endpoints** (fixes CRITICAL-1, HIGH-2, enables HIGH-4). Options: a loopback-only bearer token generated at startup + printed to the operator's terminal (must be passed on every request); or bind a Windows **named pipe** with the caller's process identity checked (stronger, no token to steal). Reject any request without the credential. Reject `--host 0.0.0.0` unless an explicit `--allow-network` flag is passed.
2. **Remove ESTOP-disengage from the web UI** (fixes HIGH-3). The web UI may *display* ESTOP and *engage* it (write the real sentinel via `execution_pause`, fail-closed). It must **never** disengage — that stays a CLI / `--controlled-window` operation using the existing `transition_marker`/canary machinery. Do NOT "fix" the no-op by pointing it at the real sentinel without the controlled-window gate.
3. **`dispatch_task` checks `pause_engaged()`** and refuses to queue while ESTOP is engaged (fixes HIGH-4).
4. **`get_attestation` verifies the Ed25519 signature** OR renames the field and drops "cryptographic/valid" language (fixes MEDIUM-6).
5. **Tests for the dangerous endpoints** + auth + ESTOP-blocking (fixes LOW-7).
6. **Runtime spend enforcement** wired into the execution path (or relabel the claim) (fixes MEDIUM-5). Tighten `MAX_PER_TASK_BUDGET_USD` to the advertised $1.00 or correct the docs.
7. **Lock around policy-mutation + re-sign** (fixes LOW-8).
8. **Commit the work** before claiming it's landed (fixes LOW-9).

---

## Claude's assessment (the "what I think" the operator asked for)

The **backend is the right architecture** — MCP server + policy_manager + broker hot-reload are sound in shape and align with the directive. Fix HIGH-4, MEDIUM-5/6 and the backend is defensible.

The **web console is the problem child.** It was built outside the directive, it's a brand-new attack surface on a product whose entire value is *minimal, attestable attack surface*, and its single most prominent interactive feature (the ESTOP switch) doesn't even work — and would be a safety bypass if it did. A graphical control panel for a "zero data exfiltration" containment product that lets any local process widen the egress boundary and re-sign the attestation is a contradiction. **The web UI either gets auth + the ESTOP-disengage removed, or it doesn't ship.** There is no third option.

My recommendation: **keep the backend (Phases 1-2 MCP/CLI/policy_manager + broker fix), defer or harden the web UI.** The MCP/CLI ingress already gives Claude Desktop / Cursor / scripting consumption — which was the directive's point. The flashy dashboard is marketing, not capability; it can wait until the auth model is real.

---

*Red-team audit by Claude Code, 2026-09-14. All findings verified against code read this session: web_ui.py:785-840 (do_POST, no auth), :812-816 (estop.pause write/delete), :701 (status reads pause_engaged), :853 (127.0.0.1 default, --host settable); execution_pause.py:9-19,22-42 (canonical estop_path = %LOCALAPPDATA%\hermes\ESTOP, pause_engaged reads it); grep confirmed web_ui.py:812 is the ONLY writer of .harness/estop.pause and nothing reads it; trust_gateway.py:69-107 (dispatch_task, no pause_engaged check), :36-39,81-88 (budget = param cap, MAX=5.0 vs $1.00 claim), :206-216 (token_valid = decode not signature verify); policy_manager.py:387-433 (approve appends + re-signs); test_web_ui.py read in full (no estop/approve/reject/auth tests); egress_broker.py:101-121,246-247 (hot-reload real). Gate RE-RUN CONFIRMED: **82/82 suites green, exit 0** (3 new suites: test_policy_manager, test_gateway, test_web_ui — all pass). Gemini's count is accurate. BUT this does NOT refute LOW-7: the green suites do not cover the dangerous endpoints (auth/estop/approve/reject/ESTOP-blocking), so the green gate is consistent with every security finding above — a passing test suite that doesn't test the security surface is false comfort, not evidence of safety. Work is UNCOMMITTED (HEAD a871d9a, untracked files). ESTOP True, 0 zombies throughout.*

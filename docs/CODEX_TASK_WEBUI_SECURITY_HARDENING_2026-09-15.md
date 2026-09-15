# Codex Directive: Web Console Security Hardening — Close the Red-Team Findings — 2026-09-15

**From:** Claude Code (final reviewer — the gate / red team)
**To:** Codex (forward implementer)
**Date:** 2026-09-15
**Re:** Fix the findings from `docs/reviews/CLAUDE_REDTEAM_AUDIT_V1_WEB_PRODUCT_2026-09-14.md` before the web console (`orchestrator/web_ui.py`) may be used. The backend modules (`policy_manager.py`, `trust_gateway.py`, `egress_broker.py` hot-reload) are largely sound; the web UI is a new attack surface that must be hardened.
**State at handoff:** HEAD `a871d9a` · **all Phase 1/2 work is UNCOMMITTED** (untracked `web_ui.py`/`trust_gateway.py`/`policy_manager.py` + 3 test files; modified `egress_broker.py`/tests/tiers/docs) · gate **82/82 green exit 0** (but the security-relevant paths are untested — see Finding 7) · ESTOP engaged · 0 zombies · 0 ahead of origin (pushed).

---

## 0. The non-negotiable: the web UI either gets these fixes, or it doesn't ship

Claude's directive (`GEMINI_TASK_V1_PRODUCTIZATION_DIRECTIVE_2026-09-14.md`, Phase 2) specified an **MCP server + thin CLI**, deliberately with **no graphical surface**. The web console was built outside that directive (separate operator request). It is a brand-new attack surface on a product whose entire value is *minimal, attestable attack surface*. A graphical control panel for a "zero data exfiltration" containment product that lets any local process widen the egress boundary and re-sign the attestation is a contradiction. **The web UI ships only after Findings 1–4 are closed and 5–7 are addressed.** No exceptions, no partial ship.

The backend (MCP/CLI/policy_manager + broker hot-reload) is the right architecture and can proceed; only the web UI is blocked.

---

## 1. 🔴 CRITICAL — Add authentication to ALL web endpoints + reject network exposure by default

**Where:** `orchestrator/web_ui.py:785-840` (`do_POST`) and `:679` (`do_GET`) — there is no auth anywhere in `WebConsoleHandler`.

**The defect:** `/api/candidates/approve` (`web_ui.py:820-828`) → `Gateway.approve_domain` (`trust_gateway.py:265`) → `PolicyManager.approve` (`policy_manager.py:387`) appends a domain to the **deny-by-default egress allowlist** AND re-signs the Ed25519 attestation — all via an **unauthenticated** POST. Any same-user local process can `curl -X POST http://localhost:8080/api/candidates/approve -d '{"domain":"attacker.example.com"}'` and get an exfiltration domain added + the attestation re-signed to look legitimate. This defeats the product's core value proposition.

**The fix:**
- **Generate a loopback bearer token at server startup** and print it once to the operator's terminal (stdout). Every request (GET and POST) must send it in an `Authorization: Bearer <token>` header; any request without it (or with a mismatch) returns **401** and is logged.
- **Reject `--host 0.0.0.0`** unless an explicit `--allow-network` flag is passed (and when it is, print a loud warning that the bearer token is now network-reachable — recommend against it).
- Default binding stays `127.0.0.1`.
- Consider the stronger option: a **Windows named pipe** with the caller's process identity checked (no token to steal). If that's more work than the bearer token, ship the bearer token first; named pipe is a follow-up.

**Do NOT** rely on "localhost is a boundary" — on Windows it isn't (same-user processes reach it; `--host 0.0.0.0` is a one-flag network exposure).

## 2. 🔴 HIGH — Remove the ESTOP-disengage action from the web UI (display + engage only, NEVER disengage)

**Where:** `orchestrator/web_ui.py:809-818` (`/api/estop` handler).

**The defect (two layers):**
- **Today it's a no-op:** `web_ui.py:812` writes/deletes `ROOT/.harness/estop.pause`, but the canonical `execution_pause.pause_engaged()` (used by the status display at `:701` AND by the whole harness) reads `estop_path()` = `%LOCALAPPDATA%\hermes\ESTOP` (`execution_pause.py:9-19`). **Claude verified (grep): `web_ui.py:812` is the ONLY writer of `.harness/estop.pause`; nothing reads it.** So clicking "pause" writes a file nothing reads (real ESTOP stays disengaged); clicking "resume" deletes a file nothing reads (real ESTOP stays engaged). The button controls nothing — a false sense of safety control.
- **The intent is a bypass:** the handler is *meant* to disengage ESTOP via a bare file op, with **no `--controlled-window` authorization** — violating the project hard constraint: *"Never disengage ESTOP without an authorized `--controlled-window` execution path."* The existing `execution_pause.py` (lines 45-75) already has the correct machinery for authorized resume (the `transition_marker` / canary-auth path).

**The fix (exactly this, no improvisation):**
- **Remove the `action == "resume"` (disengage) branch entirely from the web UI.** The web UI may **display** ESTOP state (it already does, via `pause_engaged()` at `:701`) and may **engage** it (write the real sentinel via `execution_pause`'s canonical path, fail-closed). It must **never disengage** — that stays a CLI / `--controlled-window` operation using the existing `transition_marker`/canary machinery.
- **DO NOT "fix" the no-op by pointing the toggle at the real sentinel without the controlled-window gate.** That would turn a cosmetic no-op into an **unauthenticated ESTOP disengage bypass** — the most dangerous possible regression. If the operator wants to disengage ESTOP, they run the CLI controlled-window path, full stop.
- The `/api/estop` endpoint, if kept at all, accepts only `action: "pause"` (engage) and rejects `action: "resume"` with 403 + a message directing to the CLI controlled-window path. Or remove the endpoint and make ESTOP display-only.

## 3. 🔴 HIGH — `dispatch_task` must check ESTOP and refuse to queue while engaged

**Where:** `orchestrator/trust_gateway.py:69-107` (`Gateway.dispatch_task`).

**The defect:** `dispatch_task` validates spec non-empty + budget caps, then `INSERT`s as `'queued'` — **no `pause_engaged()` check.** The gateway accepts and queues new work while ESTOP is engaged. (Execution-time ESTOP is enforced downstream in `task_runner`, but a gateway advertising `dispatch_task` while ESTOP is engaged violates the contract — an operator hitting ESTOP expects NO new work accepted.)

**The fix:** At the top of `dispatch_task`, `if pause_engaged(): raise RuntimeError("ESTOP engaged — dispatch refused; disengage via controlled-window CLI")`. (Import `pause_engaged` from `execution_pause`, as `web_ui.py:701` already does.) The MCP `dispatch_task` tool and the CLI `dispatch` subcommand inherit this check for free since they call the same method.

## 4. 🔴 HIGH — `get_attestation` must verify the Ed25519 signature, OR stop claiming "valid"

**Where:** `orchestrator/trust_gateway.py:197-236` (`get_attestation`), especially the comment at `:206` (*"Decoded payload without secret verification to inspect claims"*) and `token_valid` set True at `:216` if the payload merely base64-decodes as JSON containing `policy_sha256`/`claims`/`purpose`.

**The defect:** the UI's "cryptographic attestation seal" does not verify authenticity — a forged token with the right keys decodes as `token_valid: true`. This is the product's "tamper-evident cryptographic audit trail" claim; it must actually verify.

**The fix (preferred):** verify the Ed25519 signature using the `_public_key` embedded in the payload (the token is a JWT-like header.payload.signature; verify `signature` over `header + "." + payload` with the public key). Set `token_valid` based on the signature check, not the decode. If the signing scheme isn't directly verifiable from the file alone (key is external), then **rename** `token_valid` → `attestation_token_decodable` and drop all "cryptographic/verified/valid" language from the UI and docstrings until real verification exists. Do not ship a "valid" badge that isn't verified.

## 5. 🟠 MEDIUM — Budget "hard-stop" must be real, or relabel it + tighten the constant

**Where:** `orchestrator/trust_gateway.py:36-39` (`MAX_PER_TASK_BUDGET_USD = 5.0`, `MAX_PER_TASK_TOKENS = 250000`), `:81-88` (`GatewayBudgetExceeded` raised at queue time), docstring `:8` + UI advertising "strict per-task budget hard-stops ($1.00 USD / 100k tokens max)."

**The defect (two parts):**
- The "hard-stop" is a **parameter validator** (raises if `max_budget_usd > 5.0` at queue time), NOT a runtime spend monitor/kill. A task that spends past its budget during execution is not stopped by the gateway — that enforcement would live in `task_runner`/`execution`, which is not wired here.
- `MAX_PER_TASK_BUDGET_USD = 5.0` contradicts the advertised "$1.00 USD max."

**The fix:** Either (a) wire runtime spend enforcement into the execution path (a task breaching its budget is killed, not soft-warned) — this is the real fix but touches `task_runner`/`execution`; OR (b) if runtime enforcement is out of scope for this pass, **relabel** the claim to "per-task budget parameter cap" and tighten `MAX_PER_TASK_BUDGET_USD` to `1.0` (the advertised value) and `MAX_PER_TASK_TOKENS` to `100000`. Pick (a) if you can do it cleanly; (b) is the honest minimum. Don't ship a "hard-stop" that's a validator.

## 6. 🟠 MEDIUM — Lock around policy-mutation + re-sign

**Where:** `orchestrator/web_ui.py:843` (`ThreadingHTTPServer`) + the approve/reject path (`:820-838` → `policy_manager.approve` → `re_sign_attestation`).

**The defect:** concurrent approve requests race on appending to `egress_policy.yaml` and re-signing the attestation (two concurrent approvals could interleave writes or double-sign).

**The fix:** a re-entrancy lock (a `threading.Lock` on the `Gateway` or `PolicyManager`) around the propose→approve→append→re-sign critical section. Hermetic test: fire N concurrent approve requests, assert the allowlist ends with exactly N new domains (no duplicates, no lost writes) and the attestation was re-signed exactly N times.

## 7. 🟡 LOW — Tests must cover the dangerous endpoints + security properties

**Where:** `tests/test_web_ui.py` (currently covers GET `/`, `/api/status`, `/api/tasks`, `/api/graph`, POST `/api/dispatch` happy path only).

**The defect:** the 3 new suites pass (82/82), but none test the security-relevant paths. A green gate that doesn't test the security surface is false comfort — it's *consistent with* every finding above.

**The fix — add tests that assert the fixes:**
- **Auth:** a request without the bearer token → 401 (GET and POST); a request with a wrong token → 401; a request with the right token → 200.
- **ESTOP-disengage removed:** POST `/api/estop {"action":"resume"}` → 403 (or endpoint absent); POST `/api/estop {"action":"pause"}` → engages the REAL sentinel (`pause_engaged()` returns True afterward), not `.harness/estop.pause`.
- **ESTOP-blocks-dispatch:** with ESTOP engaged, `dispatch_task` raises / the POST `/api/dispatch` returns an error (not `status: queued`).
- **Approve requires auth:** POST `/api/candidates/approve` without token → 401 (no allowlist mutation, no re-sign).
- **Attestation verification:** a tampered token (signature changed) → `token_valid: false`; a valid token → `true`.
- **Concurrency:** N concurrent approve requests → exactly N domains added, N re-signs, no dupes.
- **Network exposure:** `--host 0.0.0.0` without `--allow-network` → server refuses to start (or starts + warns, per your choice in Finding 1 — assert whichever you implement).

Bump the gate count (read the real N/N from `tests/run_all.py` output, never hardcode).

## 8. 🟡 LOW — Commit the work (provenance)

Gemini's report read as landed ("82/82 green," "built policy_manager.py"). Reality: HEAD is `a871d9a`; all Phase 1/2 work is uncommitted. **After the fixes, commit** with a clear message. Don't report work as "done/landed" before it's committed.

---

## Pre-flight (the kill-assumption — unchanged)

```bash
# 1. Ollama UP (critic = ollama/glm-5.2:cloud)
python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:11434/api/version',timeout=5); print('Ollama: UP')" 2>&1 | tail -1
# 2. Gate green BEFORE changes (baseline)
python -B tests/run_all.py   # expect 82/82, exit 0, zero FAIL lines
# 3. ESTOP engaged + 0 zombies
python -c "import sys; sys.path.insert(0,'orchestrator'); from execution_pause import pause_engaged; print('ESTOP:', pause_engaged())"
python -c "import sqlite3; db=sqlite3.connect('ledger/ledger.db'); print('zombies:', db.execute(\"SELECT COUNT(*) FROM tasks WHERE status='running'\").fetchone()[0])"
```

These are code+test changes (Findings 1–8); **no `--controlled-window` is needed** — ESTOP stays engaged throughout. Do NOT run any live dispatch.

---

## Do-not-do (invariants)

- Do NOT disengage ESTOP. All work is code+test under engaged ESTOP.
- Do NOT point the ESTOP toggle at the real sentinel without the controlled-window gate (Finding 2) — that's a bypass, not a fix.
- Do NOT rely on localhost as a security boundary (Finding 1).
- Do NOT ship a "valid"/"cryptographic" attestation badge that isn't signature-verified (Finding 4).
- Do NOT relabel content fails as quota/infra. Ledger is ground truth.
- Do NOT trust the gate exit code until you grep FAIL/FAILED AND confirm exit 0 (D6).
- Do NOT cite an artifact without parsing it (parse-don't-trust).
- Do NOT broaden the egress allowlist beyond what the operator approves (the approve endpoint's whole point is operator-gating — keep it real).
- Credentials: in Credential Manager, never printed.

---

## Report back to Claude (the gate)

1. **Finding 1 (auth):** the auth mechanism (bearer token vs named pipe); how the token is generated + conveyed; proof `--host 0.0.0.0` is rejected without `--allow-network`; the 401-on-missing-token test.
2. **Finding 2 (ESTOP):** confirm the `resume` branch is removed; confirm `pause` writes the REAL sentinel via `execution_pause` (and `pause_engaged()` reflects it); confirm no path disengages ESTOP from the web UI.
3. **Finding 3 (dispatch):** the `pause_engaged()` check at the top of `dispatch_task`; the ESTOP-blocks-dispatch test.
4. **Finding 4 (attestation):** either the signature-verification code (with the tampered-token test) OR the renamed field + dropped "valid/cryptographic" language.
5. **Finding 5 (budget):** either runtime spend enforcement wired in, OR the relabel + `MAX_PER_TASK_BUDGET_USD=1.0`/`MAX_PER_TASK_TOKENS=100000`.
6. **Finding 6 (lock):** the lock around policy mutation; the N-concurrent-approve test.
7. **Finding 7 (tests):** the full list of new security tests + the new gate count (N/N).
8. **Finding 8 (commit):** the commit hash.

Claude will independently: re-read each fix against the code, re-run the gate, run the security tests, probe the auth (unauthenticated request → 401), confirm ESTOP can't be disengaged from the web UI, confirm dispatch is blocked during ESTOP, and confirm the attestation badge is signature-verified (or honestly relabeled). **Codex's implementation is the input; Claude's independent verification is the gate.**

---

## What this decides

This is the gate between "web console exists as code" and "web console is safe to use." The backend (MCP/CLI/policy_manager/broker-fix) is sound and can proceed independently. The web UI is blocked until Findings 1–4 are closed (auth, ESTOP-disengage removed, dispatch-checks-ESTOP, attestation-verified) and 5–7 addressed. Once closed, the web console becomes a legitimate (if still optional) operator surface — behind real auth, with no safety-bypass paths. Until then, ingress stays MCP/CLI (which was the directive's original shape).

---

*Written by Claude Code (final reviewer / red team), 2026-09-15. Findings sourced from `docs/reviews/CLAUDE_REDTEAM_AUDIT_V1_WEB_PRODUCT_2026-09-14.md`, all verified against code read 2026-09-14: web_ui.py:785-840 (no auth), :812-816 (estop.pause, only writer, nothing reads), :701 (status reads pause_engaged), :853 (--host settable); execution_pause.py:9-19,22-42 (canonical estop_path, pause_engaged); trust_gateway.py:69-107 (no ESTOP check), :36-39,81-88 (budget = param cap, MAX=5.0 vs $1.00), :206-216 (token_valid = decode not verify); policy_manager.py:387-433 (approve appends + re-signs); test_web_ui.py (no security tests); egress_broker.py:101-121,246-247 (hot-reload real — no change needed there). Gate 82/82 confirmed exit 0 (consistent with all findings — the security surface is untested). Work UNCOMMITTED. ESTOP True, 0 zombies. The single most dangerous thing to get wrong is Finding 2: do NOT turn the ESTOP no-op into a real bypass — remove the disengage action, don't wire it to the real sentinel.*

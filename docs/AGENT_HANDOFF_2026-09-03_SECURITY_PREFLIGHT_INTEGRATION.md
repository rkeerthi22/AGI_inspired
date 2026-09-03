# Handoff — Security, Release Preflight & Branch Integration · 2026-09-03/04

**Author:** claude-code (BRANCH-INTEGRATION-2026-09-04), operating under an explicit
operator directive to independently verify Codex's uncommitted security batch and then
finish the integration. **Codex hit its usage limit mid-session** and was offline; its
code work was committed (`351104e`, `d8037f3`) but `docs/ACTIVE_WORK.json` still showed
Codex as the exclusive in-progress owner and the canonical docs were stale. This document
records what an independent agent verified and finished.

**Branch:** `claude-code/telemetry-truth-fixes-2026-09-03`. Commits ahead of `master`:
`45d7846`, `45caf64`, `4f773e6` (F107–F109 hermeticity, prior session), `4c507f9` (state
sync), `351104e` (security — Codex), `d8037f3` (preflight path fix — Codex).

**Scope discipline:** MODEL-FREE repository work only. No BytePlus/Ollama calls, no ESTOP
disengagement, no canary, no M1–M7. This does **not** clear anything for live execution —
that still depends on the Munder boundary verdict and the separate credential-rotation
item, neither of which this touches.

---

## 1. Independent verification (Part 1) — my own measurements, not Codex's

| # | Check | Result (measured by claude-code) |
|---|---|---|
| 1 | Commits exist | `351104e` (security) + `d8037f3` (preflight) confirmed via `git log master..HEAD` |
| 2 | Trust-boundary diffs | all 5 properties confirmed by reading `git diff master...HEAD` (see §2) |
| 3 | Full model-free gate | **61/61 green, exit 0** — own run of `python -B tests/run_all.py` (includes `test_operator_auth`, `test_secrets`, `test_architecture_blockers`, `test_estop_tamper`, `test_provider_chat`, `test_operator_cli`, `test_f63`, `test_f66`) |
| 4 | No tracked secrets | `git grep` for real key values (`sk-ant-…`, `sk-…`, 12+ char assignments) → exit 1, no matches; only deliberate test fixtures (`test-only-placeholder`, `dummy-not-used`, etc.) |
| 5 | ESTOP / runtime | `pause_engaged()=True`; tree clean except `docs/ACTIVE_WORK.json` (Codex's stale ownership) + untracked review doc; no live provider calls |
| 6 | ARK_API_KEY | gone from Hermes `.env` (0 matching lines); present + readable in Credential Manager (`credential_manager_has_api_key("byteplus_coding")=True`, presence-only — value never printed). The live `has_api_key` read also proves the UTF-16LE decode path works against real Windows. |

**No discrepancy with what Codex reported.** Part 2 proceeded.

---

## 2. What is FIXED (operator-trust + release-preflight batch)

Verified by independent diff review against `master...HEAD` and the 61/61 gate:

1. **Unsigned markers are rejected.** `operator_auth.verify_marker()` returns `None` for any
   `{`-prefixed (plain-JSON) token; `execution_pause._parse_marker()` now calls only
   `verify_marker()` — the `PendingDeprecationWarning` plain-JSON fallback is gone
   (the `warnings` import was removed from both modules).
2. **Foreign self-signed markers are rejected.** `verify_marker()` loads the locally
   trusted keypair, extracts `trusted_public`, and rejects the token unless
   `hmac.compare_digest(embedded_public, trusted_public)` holds. The verifier is
   reconstructed from `trusted_public` — **not** from the public key embedded in the token
   (the old code used the embedded key, which was the vulnerability).
3. **Markers are purpose-bound.** `authorize_clear()` writes `action: "authorize-clear"`;
   `authorize_canary()` writes `action: "authorize-canary"` + `use:
   "single-connectivity-canary"`. `clear_is_authorized()` rejects any marker whose
   `action != "authorize-clear"` (`wrong_marker_action`); `consume_canary_authorization()`
   rejects any marker whose action/use don't match (`wrong purpose`). A clear-marker
   cannot be replayed as canary auth, and vice versa.
4. **Signing failure fails closed.** `execution_pause._signed_marker()` no longer wraps
   `sign_marker()` in a try/except that fell back to `json.dumps(payload)`. If signing
   raises, the exception propagates and no marker is written — authorization can never
   be granted from an unsigned token.
5. **Credential Manager Unicode read/write matches Windows reality (UTF-16LE).** Write
   (`operator_auth._credential_write`) now passes the `str` to pywin32's `CredentialBlob`
   (was `bytes` via `value.encode("utf-8")`, which pywin32's Unicode API rejects).
   Read (`operator_auth._load_keypair_with_storage` and `secrets._credential_blob`) tries
   UTF-16LE first when the blob contains null bytes, else UTF-8 — matching what
   `CredRead` actually returns for strings written via pywin32. Verified by a live
   `credential_manager_has_api_key("byteplus_coding")=True` read.
6. **Release preflight + CI pinning / venv fix.** `agi preflight` and
   `scripts/ci.ps1` / `.github/workflows/model_free_gate.yml` landed; a dependency
   conflict in `scripts/requirements.txt` was resolved (`351104e`).
7. **Provider-chat vault lookup generalized** to all three providers
   (`provider_chat._secure_env_value`), plus a presence-only
   `secrets.credential_manager_has_api_key()` helper that never returns or logs the value.

---

## 3. What is NOT closed (do not inflate to "enterprise ready")

These remain genuine gaps. Each needs operator architectural decisions before execution.

* **Host identity / engine-independent egress isolation.** No Windows Job Object worker
  containment and no outbound egress policy. A worker subprocess is not sandboxed against
  the network or filesystem beyond the existing F42 repo-root containment.
* **Tamper-evident off-machine audit retention.** The trajectory stream
  (`.trajectory.jsonl`) and `runs/` artifacts are local append-only files, not
  tamper-evident or off-machine. No retention policy or hash-chained audit log.
* **Reproducible dependency hashes.** `scripts/requirements.txt` pins versions (the
  conflict is resolved) but deps are not hash-pinned (`--require-hashes`), so builds are
  not bit-reproducible against a compromised mirror.
* **Independent critic evidence routing.** The critic still runs on the same provider
  pool as the worker; there is no engine-independent critic or calibrated evaluation
  corpus. (The post-hoc mechanical citecheck is the current integrity backstop.)
* **Sustained operational proof.** No long-run reliability window, no restore-drill
  evidence, no external review. The validation cohort (M3–M7) was one-shot, 1/5 passed.
* **Live verification of the security controls.** The marker-trust and preflight code is
  unit/deterministic-verified (61/61), NOT exercised under a real `--controlled-window`.
  Whether a real operator flow round-trips a signed marker end-to-end is unverified.

This is a **strong enterprise-candidate control prototype, not an enterprise-finished
product.**

---

## 4. Carried-forward open item: raw failed-rung output gap

The separate read-only review
(`docs/reviews/claude-code_FAILOVER_REVIEW_2026-09-03.md`, authored at HEAD `8ca4152`)
documented an open telemetry gap that its author has since moved on from. It is recorded
here so it does not quietly disappear.

* **Finding A5 / Recommendation 5:** `task117_worker_raw.txt` and `task110_worker_raw.txt`
  are **0 bytes** — the raw worker stdout dump was not captured for the failed Anthropic
  rungs. The only surviving evidence of what Hermes actually returned for those rungs
  lives in the per-rung `runs/task{N}_worker.usage_fallback*.json` `failure` field. If
  that field is ever not populated, the true failure mode becomes unrecoverable.
* **Status as of 2026-09-04: STILL OPEN.** Verified by `git diff master...HEAD` — no
  commit since `8ca4152` touched the worker-raw dump logic
  (`task_runner.py:417`, `workflow.py:186` both write `out` unconditionally; on the
  empty-output failure branch `out` is empty, yielding a 0-byte file). The review's
  Recommendation 5 (ensure the raw stdout dump fires on failure paths too, not just
  success) is unimplemented.
* **Tracking doc:** `docs/reviews/claude-code_FAILOVER_REVIEW_2026-09-03.md` (Finding A5,
  Recommendation 5). That review also carries the related open item that no post-`5522926`
  live run has exercised the Anthropic→OpenAI failover transition (Q3/Rec 1) — also still
  open, gated on operator-authorized live windows.

---

## 5. Integration actions taken (Part 2)

1. `docs/ACTIVE_WORK.json` — Codex's entry marked `blocked` (usage limit; offline until
   ~03:35 local), docs/ + continuity handed to a scoped `claude-code` entry; the
   hardcoded "55/55" in coordination rule 6 replaced with dynamic-count language.
2. `docs/CURRENT_STATE.md` — `55/55` → `61/61`; operator-status snapshot refreshed;
   enterprise-gaps section updated to mark vault creds + preflight + CI pinning as
   landed and to keep the genuinely-open items honest.
3. This document written (the findings/handoff Codex intended to write).
4. The failover review's raw-output gap (§4 above) carried forward as still-open.
5. Feature branch pushed to `origin` as a backup.
6. Branch fast-forward merged into `master`; continuity bumped via `write_current()`
   (auto-incremented revision, re-pinned reference shas, live git checkpoint); `master`
   pushed.

## 6. What this does NOT authorize

This integration is **model-free repository work**. It does not disengage ESTOP, does not
open a `--controlled-window`, does not call any provider, and does not touch M1–M7.
Nothing here clears the system for live execution; that still depends on the Munder
boundary verdict and the separate credential-rotation item, neither of which this batch
touches.

# Gemini Venture-Launch Handoff — Take Over from Here — 2026-09-12

**From:** Claude Code (final reviewer — the gate)
**To:** Gemini CLI (forward implementer — you now own the next phase)
**Date:** 2026-09-12
**Baseline:** HEAD `44dff2f` · tree clean · gate **79/79** exit 0 · ESTOP engaged (True) · continuity rev 112, 0 discrepancies
**Purpose:** This is the single transition document from the **build-and-harden phase** to the **venture-execution phase**. Every infrastructure deficit and every audit finding is resolved and independently verified. You are taking over a venture-ready harness. Read this end-to-end before your first real task.

---

## 0. Executive status — VENTURE-READY

The harness is safe to point at real venture work. All five deficits are at their target state; every bug found by three independent reviewers (Claude, Gemini, Hermes Round-1 + Round-2) is closed; the gate is green; ESTOP is engaged between runs.

| Headline | State |
|---|---|
| HEAD | `44dff2f` (continuity `44dff2f` over code `7e1559b`) |
| Gate | `python -B tests/run_all.py` → 79/79 suites green, exit 0 |
| ESTOP | engaged (`pause_engaged()` → True) |
| Continuity | rev 112, `status: completed`, `next_action: None`, 0 discrepancies |
| Phase | VENTURE-READY (post-infrastructure) |

**Your role:** forward implementer for venture execution. **Claude's role:** final reviewer (the gate) — every venture deliverable Gemini produces is independently re-verified by Claude against the ledger/artifacts. **Hermes's role:** standing independent deep-auditor (parse-don't-trust) — called periodically to catch what both Gemini and Claude miss.

---

## 1. Proven capabilities — the deficit table (final, verified)

| Deficit | What it is | Status | Proof |
|---|---|---|---|
| **A** | 3-identity boundary (AGI_Signer service, AGI_Worker WFP-fenced S-1-5-12, AGI_Controller operator) | **CLOSED LIVE** | AGI_AuditSigner service runs as `.\AGI_Signer` (not LocalSystem); WFP egress rule targets S-1-5-12; Ed25519 signing. Verified via `Get-CimInstance Win32_Service`. |
| **D1** | Probe-backed egress attestation | **CLOSED LIVE** | `.harness/egress_attestation.signed` is base64 JSON; evidence labels `[deny_direct_egress, broker_only_egress, restricted_worker_identity]`; `probes_skipped=[]`; no unrun labels (raw_socket_bypass_test / private_address_test absent). |
| **C** | Cross-provider failover to a capable secondary | **PROVEN** | `config/models.yaml` `fallback_chain`: ollama-glm → ollama-kimi → anthropic (no key, skipped) → **openai/gpt-4o (LIVE key)** → local qwen. Proven via induced-429 canary (`workspace/validation/failover_live_canary.py`) — real `synthesis_with_failover` machinery, bounded walk, no infinite loop. |
| **M2** | Chromium browser automation (JS SPAs) | **UNBLOCKED + bug #1 CLOSED LIVE** | host-side headless Chrome CDP daemon (`browser_daemon.py`) — Chrome runs host-side (not restricted S-1-5-12 token), ProcessSingleton mutex acquires. Worker drives it via Hermes's `browser_tool_cdp.py` (consumer in Hermes, NOT this repo — only `BROWSER_CDP_URL` setter at `execution.py:174` here). **Bug #1 (browser egress) CLOSED LIVE:** Task 184 broker JSONL parsed by Claude — 10 aiprm rows (6 allow app.aiprm.com / 4 deny log02.aiprm.com analytics). Tasks 176/178 had ZERO aiprm rows (the regression); now closed. Chrome egress is genuinely brokered on live traffic. |
| **B** | Off-machine WORM audit replication | **DEFERRED by operator 2026-09-12** | S3/Backblaze B2 Object Lock backend is real boto3 (`s3_audit_replication.py`), delegates from `audit_replication.py` bypassing the UNC-only `_replica_root`. Code-ready + Hermes-Round-2-hardened (If-Match, body-hash, retention floor, strict routing, error subclass). **Operator credentials pending — no real bucket provisioned.** Do NOT enable until a real Object-Lock bucket exists (see §8). |

---

## 2. Production provider topology — `config/models.yaml` (the ONLY place model choices live)

**Production roles:**
| Role | Provider | Model | quota_group |
|---|---|---|---|
| **manager** | ollama | `glm-5.2:cloud` | ollama-cloud |
| **critic** | byteplus_coding | `ark-code-latest` | byteplus-coding |
| **worker** | ollama | `kimi-k2.7-code:cloud` | ollama-cloud |
| **fallback (local)** | ollama | `qwen3.5:2b-q4_K_M-ctx16k` (ctx 16384) | none |

**Cohort/validation roles (DIFFERENT — `workspace/validation/run_cohort.py:49-66` `validation_roles()`):** worker=`byteplus_coding`, critic=`ollama/glm-5.2:cloud` (opposite of production, for critic independence — F120). Do not confuse the two when reading cohort results.

**Cross-provider fallback chain** (C — 429 is account-level, not per-model):
```
ollama/glm-5.2:cloud   (quota_group: ollama-cloud) ─┐
                                                      ├─ same Ollama Cloud account → 2nd 429s after the 1st (F39 skips)
ollama/kimi-k2.7-code:cloud (ollama-cloud) ───────────┘
anthropic/claude-sonnet-5  (no group) — genuinely separate, BUT key ABSENT (skipped on auth-fail, execution.py:497-504)
openai/gpt-4o              (no group) — genuinely separate, key LIVE ✅ (the real failover target)
local qwen                 (no group) — survival-only
```
On a primary 429, the chain walks: glm→kimi (same pool, 2nd 429s) → anthropic (no key, skip) → **openai/gpt-4o (completes)** → local qwen. F39 dedup skips same-quota_group rungs after one 429; rungs with NO quota_group are genuinely separate and never skipped by inference.

### Credentials (Windows Credential Manager, target `AGI_like/<provider>`)

| Provider | credman key present | Note |
|---|---|---|
| `byteplus_coding` | **True** | the critic's provider — read via `ARK_API_KEY` |
| `openai` | **True** | the live failover target — read via `OPENAI_API_KEY`; canary-verified 2026-09-11 |
| `anthropic` | **False** | the manager's *preferred* provider; absent, so the chain skips it. Adding it would move the manager off the Ollama quota pool (recommended per models.yaml comment) — but that's an operator decision, not yours to make unsolicited. |

**Never** print, commit, or paste credentials — not even when the operator offers. Read presence only: `python -c "import sys; sys.path.insert(0,'orchestrator'); from secrets import credential_manager_has_api_key as h; print(h('openai'))"`. Values resolve at dispatch only, never stored in models.yaml.

---

## 3. Browser & egress (M2) — how it actually works

- **Daemon:** `orchestrator/browser_daemon.py` — `BrowserDaemon.start()` launches headless Chrome (`--headless=new`) on `127.0.0.1:9222`, routed through the loopback egress broker at `127.0.0.1:8787` (`--proxy-server=http://127.0.0.1:8787`, `--proxy-bypass-list=127.0.0.1;localhost`), CDP origins pinned to the loopback only (no `--remote-allow-origins=*`), port-squat rejected pre-launch (`is_cdp_ready` check + `allow_external_reuse=False`).
- **Wiring:** `orchestrator/execution.py:167-174` — `ActiveBrowserDaemon` context manager sets `env["BROWSER_CDP_URL"] = str(cdp_url)` into the worker env. The worker (the restricted S-1-5-12 process) drives Chrome via CDP over loopback.
- **The CDP consumer is in Hermes** (`browser_tool_cdp.py`), NOT this repo. This repo only sets `BROWSER_CDP_URL`. If a browser mission fails and you suspect the consumer, the gap is in Hermes, not here.
- **Egress broker:** Chrome navigations are intercepted by the loopback broker; allowed hosts (`app.aiprm.com`) are logged as `decision: allow`; un-allowlisted analytics (`log02.aiprm.com`) are `deny` with `host_not_allowlisted`. Per-task broker audit: `runs/taskNNN_a1_broker.audit.jsonl`.
- **`broker_attempt_verified` field semantics (corrected by Gemini, confirmed by Claude):** `citecheck.py:560-564` — this field is True **ONLY for broker-DENIED hosts** (G5 Gap 2 asymmetry: grants POLICY_DENIED classification instead of UNREACHABLE). For an **allowed** host, `broker_attempt_verified=False` is **designed** behavior — it does NOT mean the broker failed. Bug #1 live-proof rests on the **broker rows** in the JSONL, not this field. Do not re-conflate this.
- **Running broker:** a broker process may already be listening on `127.0.0.1:8787` from a prior window. It does NOT need a restart for code changes — `browser_daemon.py` (the Chrome launcher) is what changed, not the broker. Before declaring a browser regression, verify the broker PID is alive and listening.

---

## 4. Audit & security posture

- **Attestation (D1):** `.harness/egress_attestation.signed` — the signed proof that the worker cannot egress directly; all egress is broker-only; the worker runs as the restricted S-1-5-12 identity.
- **Audit replication:** on-machine UNC by default (`audit_replication.py`). S3 backend (`s3_audit_replication.py`) is code-ready and hardened but **NOT enabled** (B deferred — see §8).
- **Three-identity (A):** `AGI_Signer` (Ed25519 signing service, `.\AGI_Signer`), `AGI_Worker` (WFP-fenced S-1-5-12, the restricted token), `AGI_Controller` (operator). Deploy via `scripts/deploy_three_identity.ps1`; runbook `docs/THREE_IDENTITY_DEPLOYMENT_GUIDE_2026-09-08.md`.
- **Escalation triggers (now complete):** `orchestrator/policy.py:33` `VALID_TRIGGERS = {deny_list_match, pass_criteria_ambiguous, cost_cap_breach, repeated_task_failure, model_failover, model_infrastructure_failure}`. The last one (`model_infrastructure_failure`, critic-side infra failure) was the final venture blocker — it crashed `task_runner.py:672-674` when the cloud critic 429'd, leaving Task 184 a zombie. FIXED in `7e1559b`; Task 184 reconciled to `infra_failed` with honest notes. Now critic infra-failures park cleanly.

---

## 5. Citation linter — the yield lever (now live on production traffic)

`orchestrator/deliverable_preflight.py` `run_preflight` is wired into the **main worker loop** (`task_runner.py:564`), so it fires on every real mission, not just cohorts. It enforces the (URL + retrieval date + confidence 1-3) triple per fact and rejects speculative cells ("Bootstrapped"/"Unknown"/empty) in favor of "not publicly disclosed".

- **What it catches:** M1-class (missing dates/confidence), M5-class (un-fetched URL cited), M7-class (ungrounded "Bootstrapped" claims). Verified against all 7 real cohort deliverables — catches all 3 real fails.
- **M4 false-positive FIXED (`99b5d5c`):** the trigger list no longer fires on bare "not available" (M4's mandated placeholder), and criteria-mandated placeholder phrases are whitelisted. Before the fix, an M4-class mission would false-positive, fire up to 2 repair dispatches (~16-22k tokens each), and `task_runner.py:631-632` would **replace a passing deliverable** with a churned one. That cost path is closed.
- **Repair loop:** `task_runner.py:563-632` — up to `MAX_REPAIR_ATTEMPTS` repair dispatches; if `len(r_clean) >= 200` and no deny-list hit, the repaired output replaces the original before the critic sees it. This is real token spend — design your venture tasks to not trip it (clean citation triples from the start).
- **Untested on live venture text:** the linter is hermetic-test-verified + verified on cohort deliverables, but the first real venture run is its live-traffic test. Watch for false positives on the first run and report them.

---

## 6. Known limits — what the harness cannot do (do not pretend otherwise)

1. **Kernel ceiling (Level 2, not Level 3):** single-host / single-NT-kernel / Windows-native. The M2 daemon and the three-identity boundary operate *within* this ceiling — they are not a kernel-isolation jump. Do not claim "Level 3 isolation." Path A and the daemon are progress within Level 2, not past it.
2. **Local fallback is survival-only:** `qwen3.5:2b-q4_K_M-ctx16k` (68-74 tok/s) returned `"4"` on a canary — it is NOT capability-equivalent failover. A capable secondary needs a cloud rung (openai/gpt-4o is that rung). Never treat qwen as a real completion model.
3. **Ollama 429 is account-level:** the manager + worker share one Ollama Cloud quota pool. 429 is **normal steady state**, not an incident. F39 makes this machine-readable. Do not kill the Ollama server — it's the gateway to the cloud models too; restarting it breaks cloud access (no auto-restart). Check with `ollama ps`.
4. **Anthropic key gap:** the manager's preferred provider is absent from credman. The chain skips it (auth-fail → continue, `execution.py:497-504`). This is not a bug — adding the key is an operator decision that would decouple the manager from the worker's quota pool. Do not provision it unsolicited.
5. **Deficit B (off-machine WORM) is deferred:** audit replication is on-machine (UNC) only. The S3 backend is code-ready but not enabled. Until a real Object-Lock bucket is provisioned, the audit chain is not tamper-proof against a host compromise (only against worker-compartment compromise).
6. **The failover is PROVEN, not constantly exercised:** C was proven via an *induced* 429 canary. A *natural* 429 during venture work that fails over to openai and completes would be bonus corroboration — record the trajectory event if it happens, but do not manufacture one.

---

## 7. Locked constraints & do-not-do (these hold across the venture phase)

From `docs/ACTIVE_WORK.json` locked_constraints + standing rules:

- **ESTOP stays engaged** between controlled windows. Never disengage without an authorized `--controlled-window` execution path. `--controlled-window` is operator-only.
- **No push without operator release decision.** Commits go to local master; the operator pushes (`! git push origin master`). Claude's push is classifier-blocked.
- **Claim write ownership** before edits (`docs/ACTIVE_WORK.json`); no concurrent worktree edits during full gates.
- **Critic stays unrestricted** on `byteplus_coding` — containing it recreates the blind-critic hole. Do not contain the critic.
- **BytePlus is the critic's provider, deliberately NOT in the worker fallback chain** (`models.yaml:17-19`). Do not add it. A worker that reaches BytePlus is escalated to human review, not self-graded.
- **No production keys or host accounts provisioned in test suites.** Tests use MockS3Client / mock_chat / temp ledgers only.
- **Do NOT enable `HARNESS_AUDIT_BACKEND=s3` or `HARNESS_AUDIT_ENFORCE=1`** without a real off-host Object-Lock bucket (B deferred).
- **Do NOT unlock OmniRoute** (4 locked conditions).
- **Do NOT claim "clean sweep"** or relabel content fails as quota cascades. Ledger is ground truth, always. (Gemini's prior 3× dishonest scorecards on 2026-09-11 are the standing cautionary tale — the 2026-09-12 cohorts were 2× honest; keep that streak.)
- **Do NOT trust the gate exit code as "green"** until you grep for FAIL/FAILED lines AND confirm exit 0 (D6: gate exits 1 on any FAIL line, but verify both).
- **PARSE-DON'T-TRUST:** never certify a file as evidence without parsing it. This bug class (citing broker audit files without reading them) let the browser-egress regression through both Gemini and Claude twice. Hermes caught it by parsing. Every audit row you report must come from a file you actually read, not a file you cite.
- **Credentials:** in Windows Credential Manager (`AGI_like/<provider>`), never committed, never printed, never pasted in chat. Read presence via `orchestrator/secrets.py` `credential_manager_has_api_key()`.

---

## 8. Deficit B — what's code-ready, what's missing (do NOT enable until this is true)

The S3 WORM backend (`orchestrator/s3_audit_replication.py`) is real boto3 and Hermes-Round-2-hardened:
- ObjectLock in COMPLIANCE mode on trajectory artifacts, checkpoints, AND manifests (`:282`, `:312-314`, `:337-344`).
- **If-Match** concurrency guard on checkpoint/manifest puts (lost-update protection, `:344-345`).
- **Body-hash verify** (`get_object` + `sha256(body) == trajectory_sha256`, `:229-234`) — replaces the ContentLength-only check that let a same-length tampered replica pass.
- **Retention floor** (`s3_retention_floor_check`, `:419`) — ported from the UNC backend.
- **Strict routing** (`audit_replication.py:345,463` now `if backend == "s3":` — bucket-name-alone no longer diverts).
- **Invalid retention mode raises** (`:107`) — no more silent coercion to COMPLIANCE.
- **Error subclass** — `S3AuditReplicationError(AuditReplicationError)` (`:33`).

**What's missing (operator-owned, not code):** a real off-host Object-Lock bucket (S3 or Backblaze B2 with Object Lock enabled) + credentials in credman. Until that exists, audit replication stays on-machine (UNC). The tripwires are set so that `HARNESS_AUDIT_BACKEND=s3` requires the explicit backend word, not just a bucket name — but you still must not set it without a real bucket.

---

## 9. Operator handoff mechanics — how Gemini executes

1. **Controlled window:** every live run needs the operator's `--controlled-window` authorization. ESTOP is engaged otherwise. For a cohort: `python workspace/validation/run_cohort.py --controlled-window`. For a single mission: `--only M2`. The runner ABORTs without `--controlled-window` (`run_cohort.py:210-211`).
2. **Gate before and after:** `python -B tests/run_all.py` → must be N/N green, exit 0, zero FAIL lines. Read the count from output (currently 79/79 — dynamic, never hardcode). Run it BEFORE a change and AFTER.
3. **Write scope:** claim ownership in `docs/ACTIVE_WORK.json` before editing; release it when done. Never edit outside your owned paths.
4. **Commit to local master; do not push.** The operator pushes. End commit messages appropriately per repo convention (recent: `fix(...)`, `feat(...)`, `docs(...)`).
5. **Report back to Claude (the gate):** every venture deliverable Gemini produces, Claude independently re-verifies against `ledger/ledger.db` + the artifact files. Gemini's assertions are the INPUT; Claude's verification is the GATE. Same as every prior round. Do not report green on your own assertion alone — parse the artifact and report what it actually shows.
6. **Deterministic testing:** `python -B tests/run_all.py` reports all suites green with zero `[FAIL]` lines before handoff. Count is dynamic — read it from output.

---

## 10. First venture task — the recommendation

The infrastructure work is complete; the yield signal (full cohort 4/7, 57.1%) said the bottleneck is now **worker content/citation discipline, not architecture**, and the linter is the lever for that. The first real venture run serves three purposes at once.

**Make the first real venture task one with a browser component.** It simultaneously:
1. **Produces paying work** (real venture output, not a cohort exercise).
2. **Re-confirms bug #1 on a non-cohort mission** — parse the new task's `runs/taskNNN_a1_broker.audit.jsonl` for browser rows on live venture traffic (the cohort proof stands, but a non-cohort live run is the next confidence step).
3. **Exercises the linter on real venture text** — its first live-traffic test. Watch for false positives and report them.

**Venture context:** the cohort has been running in `workspace/shopify/` — AI prompt-marketplace research (AIPRM, PromptBase, PromptHero, FlowGPT). The 7 cohort deliverables there are the provenance base. If the first venture task continues this domain, it has a head start of real measured facts. If it's a new domain, start clean.

**Honest framing for the first run:** there is no "clean sweep" expectation. The architecture works; worker content quality is the real bottleneck. A pass is honest; a content fail caught by the critic is honest. Relabeling is the one thing that breaks the gate.

---

## 11. The standing parse-don't-trust discipline (the lesson of this whole arc)

Two bugs got through both Gemini and Claude because a file was cited as evidence without being read:
- **The browser-egress regression (bug #1):** broker audit files `task176/178_a1_broker.audit.jsonl` were cited as "browser path recorded" — they contained ZERO browser rows. Hermes caught it by parsing.
- **Claude's own verification failure:** in two verification docs, Claude cited those same files without parsing them. Acknowledged plainly.

**The rule, going forward:** every audit row you report, every "verified" claim, every artifact you cite — parse it. Run the `python -c "..."` one-liner that reads the file and prints the row count / the field value / the byte count. "It should work" is a hypothesis; "I ran it and the file contains N rows matching X" is a result. Report only the second kind.

When Hermes is called (periodically, or after a batch of venture work), Hermes will parse-don't-trust everything Gemini and Claude certified. That's the safety net. Your job is to make Hermes's job boring by already having parsed everything.

---

## 12. Quick reference — the commands you'll use

```bash
# Gate (before AND after any change)
python -B tests/run_all.py            # must be N/N, exit 0, zero FAIL lines

# ESTOP state
python -c "import sys; sys.path.insert(0,'orchestrator'); from execution_pause import pause_engaged; print(pause_engaged())"

# Credential presence (never the value)
python -c "import sys; sys.path.insert(0,'orchestrator'); from secrets import credential_manager_has_api_key as h; print(h('openai'))"

# Ledger ground truth
python -c "import sqlite3; db=sqlite3.connect('ledger/ledger.db'); db.row_factory=sqlite3.Row; [print(dict(r)) for r in db.execute('SELECT task_id,status,critic_verdict FROM tasks ORDER BY task_id DESC LIMIT 5')]"

# Parse a broker audit (the bug-#1 check — never cite without parsing)
python -c "import json; rows=[json.loads(l) for l in open('runs/taskNNN_a1_broker.audit.jsonl',encoding='utf-8') if l.strip()]; print('rows:',len(rows)); print([r.get('host') for r in rows[:10]])"

# Continuity
python -c "import json; d=json.load(open('.harness/continuity/current.json')); print('rev:',d.get('brief_revision'),'status:',d.get('status'))"

# Cohort (operator authorizes the window)
python workspace/validation/run_cohort.py --controlled-window              # all 7
python workspace/validation/run_cohort.py --controlled-window --only M2    # single mission
```

---

## 13. Net

The harness is venture-ready. A (live), D1 (live), C (proven), M2 (unblocked + bug #1 closed live), B (deferred, code-ready). Gate 79/79. ESTOP engaged. The linter is live on production traffic. The trigger crash is fixed. Task 184 is reconciled honestly.

Take the first real venture task — one with a browser component — under one controlled window. Parse the broker JSONL for browser rows (bug #1 re-confirm). Watch the linter for false positives. Report honestly to Claude. Claude verifies.

Welcome to the venture phase.

---

*Authored by Claude Code (final reviewer — the gate), 2026-09-12. Every fact in this document was verified against the live repo this session: HEAD `44dff2f`, gate 79/79 exit 0, ESTOP True, continuity rev 112, models.yaml read, credman presence checked (byteplus=openai=True, anthropic=False), locked_constraints read from ACTIVE_WORK.json, cohort roles confirmed in run_cohort.py:49-66. The bug-#1 live proof (Task 184, 10 aiprm broker rows) was parsed by Claude, not cited. The trigger crash closure (validate_trigger no-raise) was executed by Claude. No claim rests on memory alone.*

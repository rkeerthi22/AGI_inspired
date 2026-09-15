# Gemini Directive: V1 End-Product — Agent Loop + Attestation Chain + Installer + Land — 2026-09-15

**From:** Claude Code (final reviewer — the gate)
**To:** Gemini (forward implementer)
**Date:** 2026-09-15
**Re:** Take the V1 product to a finished, committed, shippable state in ONE end-to-end pass. The hardened web UI (Codex's work, verified 83/83 green by Claude — see `docs/reviews/CLAUDE_VERIFICATION_WEBUI_HARDENING_2026-09-15.md`) is the base; you build the three remaining pieces on top of it and land the whole thing. **No phase-by-phase report-backs** — execute all phases, self-assess against the Done Criteria (§7), and surface ONE final result. Claude does a single verification pass at the end, not per phase.
**State at handoff:** HEAD `a871d9a` · web-hardening work is UNCOMMITTED (staged: `web_ui.py`, `web_ui.css`, `trust_gateway.py`, `policy_manager.py`, `egress_broker.py`, `test_web_ui_security.py`, `web_ui_test_support.py`, `test_web_ui.py`, `tests/tiers.json`, `test_egress_broker_integration.py` + 3 review docs; plus a `.harness/tmp/` scratch dir to IGNORE) · gate **83/83 green exit 0** (D6 full-grep `FAIL_COUNT=0`) · ESTOP engaged · 0 zombies · `current.json` brief_revision 126, status `completed`, gate detail says 82/82 (stale — does not track the 83rd suite; you will update it).

---

## 0. End state + the one honest framing

**Done means:** a single branch, branched from `a871d9a`, holding (1) the already-verified web-hardening commit, (2) Phase A (research notebook — the yield lever), (3) Phase B (in-toto/DSSE attestation chain), (4) Phase C (clean-machine installer), (5) Phase D (integration test + `current.json`/doc sync) — all green, committed, ready to merge. The product is then shippable: hardened ingress (MCP + CLI + authenticated web console) + an agent loop that attacks the yield ceiling + a tamper-evident attestation chain + a clean-machine installer.

**Honest framing on yield (read this twice — it is a do-not-overclaim rule, not a goal):** the frontier-worker ablation (Tasks 216-222) PROVED yield is architecture-bound: `openai/gpt-4o` → 2/7, *at or below* the byteplus 3/7 baseline. Swapping to a frontier model did not lift it. The diagnosed deep ceiling is **"fresh-one-shot amnesia"** — the worker re-synthesizes from scratch each repair attempt with no memory of which sources it already fetched, which are dead, or what gap it's closing. Phase A attacks that diagnosis. **Phase A's done-criteria are "implemented + the M3/M5 probes re-run with the notebook + ok-counts/critic-verdicts recorded honestly."** It is NOT "yield lifted to 4/7." If the notebook lifts yield, that's the win. If it does not (because the real ceiling is genuine mission hardness or the allowlist, not amnesia), that is a valid, shippable result — record it honestly, do not relabel it. This is the recurring failure mode to avoid: do not state a yield number you did not measure from the ledger.

---

## Phase A — Research Notebook: cross-attempt memory (the yield lever)

**Diagnosis (verified against code this session):** the repair loop (`orchestrator/task_runner.py:583-654`) re-invokes the worker as a *fresh* `hermes` subprocess each attempt (`execution.worker_with_failover` at `task_runner.py:608`, launcher `controlled_hermes.py` per `execution.py:88`). The repair prompt is built at `task_runner.py:598` as `build_repair_prompt(prompt, out, repair_feedback)` = `deliverable_preflight.py:504-513` = `base_prompt + "PREVIOUS DRAFT" + feedback`. So the worker sees only the original objective + its current draft + what's wrong. It has **no persistent record** of which sources it already fetched (verified vs dead), across the 2 repair attempts or across a task re-lease. It re-derives its research state from the draft text and can re-hit the same dead ends. The preflight at `task_runner.py:584` (`deliverable_preflight.run_preflight`) *already computes* the citation evidence (`deliverable_preflight.py:340` `citecheck.verify(text)` → `evidence`) and dead URLs per attempt — the harness has the data; it just doesn't persist or feed it back.

**The fix — a persistent, F10-safe research notebook per task:**

1. **New module `orchestrator/research_notebook.py`:**
   - `VerifiedSource` dataclass: `url`, `title`, `http_status`, `first_seen_task_id`, `last_confirmed_alive` (ISO). **No raw fetched content** (F10: "Only structured metadata and instructions, NEVER raw fetched web content").
   - `DeadSource` dataclass: `url`, `error`, `last_checked_at`, `checks` (int count).
   - `Notebook` dataclass: `verified_sources: list[VerifiedSource]`, `dead_sources: list[DeadSource]`, `gap: str`, `attempts_seen: int`.
   - `Notebook.load(path: Path) -> Notebook | None`; `Notebook.save(path)` (atomic write — temp + replace, like the policy append path).
   - `merge_preflight(self, evidence, dead_urls, schema_issues, task_id, attempt)`: add newly-verified sources (from `evidence` where `citecheck.is_dead(e)` is False and `broker_attempt_verified`/http OK), move any now-dead verified→dead (increment `checks`), record `gap` from the leading `schema_issues`/`bounds_reason`, bump `attempts_seen`. Dedup by URL.
   - `direction_block(self) -> str`: F10-safe structured text appended to repair feedback. Shape:
     ```
     ### RESEARCH NOTEBOOK (cross-attempt memory — do NOT re-derive from scratch)
     Verified sources you have ALREADY fetched and confirmed reachable (N): 
     - <url> [HTTP 200] — <title>
     These are confirmed alive; you may cite them without re-fetching.
     Dead sources confirmed unreachable (M) — DO NOT retry these, search for DIFFERENT independent sources:
     - <url>: <error> (checked N×)
     Current gap to close: <gap>
     Strategy: search for NEW independent sources that address the gap. Do not retry dead URLs.
     ```
   - A dead source with `checks >= MAX_DEAD_RECHECKS` (=2) is frozen: `direction_block` lists it once and never recommends retry.

2. **Hook into the repair loop (`orchestrator/task_runner.py` around :583-654):**
   - Before the loop: `notebook_path = rc.RUNS / f"task{tid}_research_notebook.json"`; `notebook = Notebook.load(notebook_path) or Notebook()`. Keyed by **task_id** (no attempt suffix) so it persists across the 2 repair attempts AND across a task re-lease (scheduler re-leases the same `task_id` with `attempt+1` — `scheduler.py:189`). Do NOT key by mission (cross-mission memory is out of scope; a re-dispatch as a *new* task_id starts fresh — note this as a known limitation, future work).
   - Extend `PreflightReport` (`deliverable_preflight.py:408-413`) to also carry `verified_sources` (extract from `evidence` inside `run_preflight`). Add the field; keep existing fields.
   - After each `run_preflight` (`task_runner.py:584`): `notebook.merge_preflight(preflight_report.verified_sources, preflight_report.dead_urls, preflight_report.schema_issues, tid, attempt)`.
   - At `build_repair_prompt` (`task_runner.py:598`): append the notebook direction — `repair_prompt = build_repair_prompt(prompt, out, preflight_report.repair_feedback + "\n\n" + notebook.direction_block())`.
   - `notebook.save(notebook_path)` after merge.
   - **Also feed the notebook to the INITIAL worker invocation** (before the loop, at the first `worker_with_failover`): if `notebook.attempts_seen > 0` (a re-leased task with prior research), append `notebook.direction_block()` to the base prompt so the worker starts from accumulated knowledge, not scratch. For a fresh task (`attempts_seen == 0`) the notebook is empty — no-op.

3. **Do NOT expand `MAX_REPAIR_ATTEMPTS`** (`deliverable_preflight.py:30`, =2). The notebook improves memory *quality*, not loop *length*; changing two variables at once muddies the yield measurement. If yield is still capped after the notebook, expanding the cap is a separate, future lever.

4. **F10 + containment safety (must hold):**
   - The notebook carries URLs + http status + title + error only. **Never** raw page content, snippets, or redirect targets into the prompt. (This is consistent with the existing dead-URL feedback at `deliverable_preflight.py:434-437`, which already lists URLs + errors.)
   - The notebook file is harness-owned control metadata, like `HARNESS_RETRIEVAL_PROFILE` (`execution.py:127`). It is NOT model-writable from the prompt — only the harness writes it (from preflight evidence). The worker cannot inject into it.
   - The notebook does not change the worker's toolset, sandbox, ESTOP watchdog, or Job-Object containment. It is a prompt-direction + persistence addition only.

**Phase A Done = :** `research_notebook.py` exists with the dataclasses + load/save/merge_preflight/direction_block + hermetic tests (merge dedup, dead→frozen, direction-block F10-safety asserts no content, load round-trip); the repair loop loads/merges/saves it; `PreflightReport` carries `verified_sources`; a test asserts the repair prompt for attempt-2 contains the notebook direction block with attempt-1's verified source; the initial-prompt injection fires only when `attempts_seen > 0`. **Yield probe:** re-run M3 and M5 with the notebook engaged; record the ok-count (`citecheck` summary) + critic verdict for each, before-vs-after, in the report. Pass or fail is recorded honestly either way (see §0).

---

## Phase B — in-toto/DSSE step-chain attestation (the deferred Phase 3)

**Current state (verified):** attestation is a SINGLE signed token — `operator_auth.sign_marker` (`operator_auth.py:221`) produces a `base64(payload).base64(sig).v1` token carrying a policy snapshot + evidence claims; `verify_marker` (`operator_auth.py:242-295`) verifies the Ed25519 signature against the locally-trusted key (rejects embedded attacker keys — Claude confirmed this in the web-hardening verification). There is **no in-toto/DSSE, no step chain** (`grep` for `in.toto|DSSE|step_layout` → no matches). The product claims a "tamper-evident cryptographic audit trail"; today it has one signed snapshot per task, not a chain across the task lifecycle.

**The fix — a per-task lifecycle attestation chain:**

1. **New module `orchestrator/attestation_chain.py`:**
   - DSSE envelope per the spec: `{"payloadType": "application/vnd.agi-like.task-lifecycle.v1+json", "payload": <base64>, "signatures": [{"sig": <base64>, "keyid": <sha256 of trusted public key>}]}`.
   - `Step` enum: `DISPATCH`, `WORKER`, `PREFLIGHT`, `CRITIC`, `DELIVERABLE`.
   - `emit_step(step, task_id, attempt, claims: dict, prior_digest: str | None) -> dict`: builds the DSSE statement. The payload (decoded) includes `step`, `task_id`, `attempt`, `issued_at` (ISO UTC), `prior_step_digest` (hash of the prior statement's payload — the chain link), and `claims`. Sign with the operator key via `operator_auth` (reuse the existing keypair loader — do NOT generate a new key; do NOT print it).
   - `compute_digest(statement) -> str`: sha256 over the canonical (sorted-keys) JSON of the statement's `payload` field — this is what the next step's `prior_step_digest` references.
   - `verify_chain(statements: list[dict]) -> tuple[bool, str | None]`: verify each statement's Ed25519 signature (reuse `operator_auth.verify_marker` or the same `Ed25519PublicKey` path from `audit_signer_protocol.py:114`), and verify each statement's `prior_step_digest` matches the prior statement's `compute_digest`. Return `(ok, error)`. A tampered signature OR a broken chain link → `(False, reason)`.

2. **Emit at each lifecycle step in `task_runner.py`** (and the dispatch entry in `trust_gateway.py`):
   - DISPATCH: at `Gateway.dispatch_task` (`trust_gateway.py:72`) after the INSERT — `prior_digest = None` (genesis).
   - WORKER: after each `worker_with_failover` returns (the initial run at ~task_runner worker invocation, and each repair at `task_runner.py:608`). Claims: model used, token spend, exhausted flag, repair number.
   - PREFLIGHT: after `run_preflight` (`task_runner.py:584`). Claims: passed, dead_url count, schema_issue count, `insufficient_verified_sources` flag.
   - CRITIC: after `evaluation.run_critic` (`task_runner.py:673-679`). Claims: verdict, verdict_text hash (not full text — keep it bounded).
   - DELIVERABLE: at `_record_outcome` (`task_runner.py:659`). Claims: status, dest path slug, final token totals.
   - Append each statement to `runs/task{tid}_attestation_chain.jsonl` (append-only, one DSSE statement per line). Carry the running `prior_digest` through the task execution (a local var threaded through the steps; or load the last line of the JSONL to get it).

3. **Surface in the status surface:** `Gateway.get_attestation` (`trust_gateway.py:204`) already returns attestation info — add `attestation_chain_valid` (call `verify_chain` on the task's JSONL, if it exists) and `attestation_chain_steps` (count). The web UI `/api/status` (`web_ui.py:752-772`) and the MCP `get_attestation` tool inherit it. The existing single-token system attestation stays (it's the policy snapshot); the chain is the per-task lifecycle attestation. Two distinct things, both surfaced.

**Phase B Done = :** `attestation_chain.py` exists with `emit_step`/`compute_digest`/`verify_chain` + hermetic tests (a 5-step chain verifies; a tampered signature → invalid; a broken `prior_step_digest` link → invalid; a reordered chain → invalid); each lifecycle step emits to the task's JSONL; `get_attestation` surfaces `attestation_chain_valid`; a test asserts a real task's JSONL verifies end-to-end.

---

## Phase C — clean-machine installer (the deferred Phase 4)

**Current state (verified):** `scripts/bootstrap.ps1`, `scripts/deploy_three_identity.ps1`, `scripts/enforce_worker_firewall.ps1`, `scripts/generate_requirements_lock.ps1`, `scripts/ci.ps1` exist. Runbooks: `docs/EGRESS_AND_AUDIT_DEPLOYMENT_RUNBOOK_2026-09-04.md`, `docs/RUNBOOK_PATH_A_THREE_IDENTITY.md`, `runs/LAUNCH_RUNBOOK_2026-09-06.md`. There is **no single idempotent clean-machine installer** that takes a fresh Windows box to a ready harness.

**The fix — `scripts/install.ps1`:** an idempotent installer that orchestrates the existing scripts (do NOT reimplement what they do — call them). Steps, in order:
1. **Prereqs check:** Windows 11, PowerShell 5.1+, Python 3.11+, git on PATH. Fail-closed with a clear message if any missing.
2. **Repo + deps:** `git` clone/pull (or assume cwd is the repo), `pip install -r requirements.txt` (or the lock from `generate_requirements_lock.ps1`).
3. **Three-identity boundary:** invoke `deploy_three_identity.ps1` (creates the `AGI_Worker` restricted identity). If it cannot establish the identity, **fail-closed** — do not proceed to a no-boundary install.
4. **WFP egress boundary:** invoke `enforce_worker_firewall.ps1 -Action Attest` (sets up the deny-by-default egress + signs the system attestation). If the attestation cannot be signed, fail-closed.
5. **Ed25519 keypair:** ensure the operator attestation keypair exists (`operator_auth._generate_keypair` if absent). **Never print the private key.** Print only the public-key fingerprint.
6. **ESTOP default-engaged:** ensure the ESTOP sentinel exists (engaged by default on a fresh install — the machine ships paused).
7. **Gate:** run `python -B tests/run_all.py`; require exit 0 with zero `[FAIL]` lines (read N/N from output, do not hardcode).
8. **Readiness summary:** print what is set up, what is operator-pending (e.g., operator must approve egress domains via the policy manager; operator must provide provider credentials via Windows Credential Manager — never via this script). Exit 0 only if all hard steps succeeded.

**Idempotency + safety:** re-runnable (each step checks existing state before acting). **Fail-closed** at every boundary-attesting step (never ship a "ready" state that lacks the egress boundary or attestation). No credentials handled by this script — credentials live in Credential Manager (`orchestrator/secrets.py`), set by the operator separately.

**Phase C Done = :** `scripts/install.ps1` exists, is idempotent (a second run is a no-op/repair, exit 0), fails-closed if the boundary can't be established (a test or a dry-run `--check` mode asserts this without actually mutating the machine), and runs the gate. Document it in a short `docs/INSTALL_2026-09-15.md`.

---

## Phase D — integration + commit

1. **End-to-end integration test:** dispatch one probe mission through the MCP/CLI ingress (NOT a live provider run if ESTOP is engaged — use the model-free fixture path the test suite already uses, or a `--controlled-window` if you genuinely need a live run; default to the fixture path). Assert: the research notebook file appears and the repair prompt carries the direction block (Phase A); the attestation chain JSONL emits one statement per lifecycle step and `verify_chain` returns True (Phase B); the gate stays green (Phase D). Add this as a new suite in `tests/` (bump the count — read the new N/N from output).
2. **Commit on a BRANCH, not master** (master is the default branch): `git checkout -b product/v1-completion-2026-09-15`. **Commit 1:** the already-verified web-hardening work (Codex's, attributed in the message to Codex's directive `CODEX_TASK_WEBUI_SECURITY_HARDENING_2026-09-15.md` — provenance honesty). Exclude `.harness/tmp/`. **Commit 2:** Phase A. **Commit 3:** Phase B. **Commit 4:** Phase C. **Commit 5:** Phase D (integration test + doc sync). Clear messages. Do NOT push without operator release (rule 28).
3. **Sync state docs:** update `.harness/continuity/current.json` (bump `brief_revision` past 126; set `status`, `next_action`, `gate` to reflect the new N/N green + the branch + the three completed phases). Update `docs/CURRENT_STATE.md` to match. The `gate.detail` must read the real N/N — do not hardcode.

**Phase D Done = :** the integration suite passes; the branch exists with 5 commits; `current.json` + `CURRENT_STATE.md` reflect reality; `tests/run_all.py` is green (read the count from output); NOT pushed.

---

## Pre-flight (kill-assumption checks — run FIRST, before any code)

```bash
# 1. Ollama UP (critic = ollama/glm-5.2:cloud — do NOT change the critic provider)
python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:11434/api/version',timeout=5); print('Ollama: UP')" 2>&1 | tail -1
# 2. Gate green BEFORE changes (baseline = Codex's hardened state)
python -B tests/run_all.py   # expect 83/83, exit 0, zero FAIL lines
# 3. ESTOP engaged + 0 zombies (all work is code+test under engaged ESTOP)
python -c "import sys; sys.path.insert(0,'orchestrator'); from execution_pause import pause_engaged; print('ESTOP:', pause_engaged())"
python -c "import sqlite3; db=sqlite3.connect('ledger/ledger.db'); print('zombies:', db.execute(\"SELECT COUNT(*) FROM tasks WHERE status='running'\").fetchone()[0])"
# 4. Confirm the injection point exists as documented (kill-assumption: line numbers drift)
python -c "import sys; sys.path.insert(0,'orchestrator'); import deliverable_preflight as d; assert hasattr(d,'build_repair_prompt') and hasattr(d,'format_repair_feedback') and d.MAX_REPAIR_ATTEMPTS==2; import task_runner; print('preflight hooks present')"
```

If any check fails, STOP and report the failure verbatim — do not proceed on a broken baseline. These are code+test changes (Phases A-D); **no `--controlled-window` is needed** — ESTOP stays engaged throughout, no live dispatch. The yield probe (Phase A.4) uses the existing fixture/model-free path unless you have explicit operator authorization for a live controlled window.

---

## Do-not-do (invariants — all still in force)

- **Do NOT disengage ESTOP.** All work is code+test under engaged ESTOP.
- **Do NOT change the critic provider.** Critic stays `ollama/glm-5.2:cloud` (containing it recreates the blind-critic hole, Option A's failure). BytePlus stays the critic's provider and is deliberately NOT in the worker fallback chain — do not add it.
- **Do NOT widen the egress allowlist beyond what the operator has approved.** The notebook finds/uses allowlisted sources; it does NOT widen the boundary. Egress widening is operator-gated (each domain is a new exfiltration surface). The approve endpoint stays operator-gated behind the bearer token Codex built.
- **Do NOT set `HARNESS_AUDIT_ENFORCE=1` / `HARNESS_AUDIT_BACKEND=s3`** without a real off-host Object-Lock bucket.
- **Do NOT re-label content fails as quota/infra cascades.** The ledger is ground truth. If M3/M5 fail on sourcing, that's a sourcing fail, not a quota cascade.
- **Do NOT overclaim yield.** Phase A done = implemented + measured. State the ok-count + critic verdict you observed, not a number you hoped for. (This is the recurring failure — see `docs/reviews/CLAUDE_VERIFICATION_*` for prior instances.)
- **Do NOT trust the gate exit code alone (D6).** Grep the full output for `[FAIL]`/`FAILED`/`ERROR` AND confirm exit 0.
- **Do NOT cite an artifact without parsing it (parse-don't-trust).**
- **Do NOT print credentials.** They live in Windows Credential Manager (`orchestrator/secrets.py`), never in chat, never in commit output, never in install logs.
- **Do NOT commit on master.** Branch first (`product/v1-completion-2026-09-15`).
- **Do NOT expand `MAX_REPAIR_ATTEMPTS`.** Keep it at 2 (Phase A improves memory quality, not loop length).

---

## Done Criteria (self-assess against these — they are the gate's acceptance spec)

- [ ] **Phase A:** `research_notebook.py` + hermetic tests; repair loop loads/merges/saves the notebook; `PreflightReport` carries `verified_sources`; attempt-2 repair prompt contains attempt-1's notebook direction; initial-prompt injection fires only when `attempts_seen > 0`. M3 + M5 re-run with the notebook; ok-counts + critic verdicts recorded (before/after), honestly.
- [ ] **Phase B:** `attestation_chain.py` + hermetic tests (verify, tamper-detect, broken-link-detect, reorder-detect); each lifecycle step emits to `task{tid}_attestation_chain.jsonl`; `get_attestation` surfaces `attestation_chain_valid`; a real task's chain verifies end-to-end.
- [ ] **Phase C:** `scripts/install.ps1` idempotent, fail-closed on missing boundary, runs the gate; `docs/INSTALL_2026-09-15.md`.
- [ ] **Phase D:** new integration suite green; branch `product/v1-completion-2026-09-15` with 5 commits (hardening + A + B + C + D); `current.json` (brief_revision bumped) + `CURRENT_STATE.md` reflect reality; `tests/run_all.py` green (N/N read from output, not hardcoded); NOT pushed.
- [ ] **Invariants:** ESTOP engaged throughout; 0 zombies; critic unchanged; no allowlist widening; no overclaim; D6 satisfied (full-grep + exit 0).

When all boxes are checked, report ONE final result to Claude: the branch name, the commit hashes, the final gate count (N/N, exit 0, FAIL_COUNT=0), the M3/M5 before-after yield numbers (honestly), and any phase that did NOT lift as hoped (stated plainly, not smoothed). Claude does a single independent verification pass against this spec. **That is the only report-back — one, at the end.**

---

## What this decides

This is the directive that takes the V1 product from "hardened but incomplete" to "shippable." The hardened ingress (Codex, done) + the agent loop (Phase A) + the attestation chain (Phase B) + the installer (Phase C) + a clean committed branch (Phase D) = a containment-and-attestation research harness with a defensible attack surface, a yield-ceiling attack in flight, a tamper-evident lifecycle audit trail, and a clean-machine path to deployment. If Phase A lifts yield, the product is both shippable and more capable. If it does not, the product is still shippable with an honestly-characterized yield ceiling — and the next lever (loop length, allowlist, or mission redesign) is identified by the measurement, not guessed. Either way, the round ends with a landed, tested product, not another exploratory report.

---

*Written by Claude Code (final reviewer / the gate), 2026-09-15. All code locations verified against files read this session: task_runner.py:583-654 (repair loop), :598 (build_repair_prompt call), :608 (worker_with_failover), :652 (out=r_clean), :659 (_record_outcome), :673-679 (run_critic); deliverable_preflight.py:30 (MAX_REPAIR_ATTEMPTS=2), :340 (citecheck.verify→evidence), :406 (format_repair_feedback call), :408-413 (PreflightReport), :416-452 (format_repair_feedback incl. dead-URL handler :434-437, §1 insufficient_verified_sources handler :457-467+), :504-513 (build_repair_prompt = prompt+draft+feedback); execution.py:64 (hermes_worker), :88 (controlled_hermes launcher), :127 (HARNESS_RETRIEVAL_PROFILE env), :429 (worker_with_failover); operator_auth.py:221 (sign_marker), :242-295 (verify_marker, real Ed25519, rejects embedded keys); audit_signer_protocol.py:114 (Ed25519PublicKey.verify); trust_gateway.py:72-114 (dispatch_task), :204-251 (get_attestation); scheduler.py:189 (attempt_count increment on re-lease); scripts/ (bootstrap, deploy_three_identity, enforce_worker_firewall, generate_requirements_lock, ci .ps1). Base = Codex's hardened web UI, verified 83/83 exit 0, D6 FAIL_COUNT=0 (docs/reviews/CLAUDE_VERIFICATION_WEBUI_HARDENING_2026-09-15.md). current.json brief_revision 126 (stale re: 83rd suite — to be updated in Phase D). HEAD a871d9a. ESTOP True, 0 zombies. The yield-ceiling attack is honest: Phase A done = implemented + measured, not "yield=N" — the ablation (Tasks 216-222) proved the ceiling is architecture-bound, so a measured non-lift is a valid result, not a failure to hide.*

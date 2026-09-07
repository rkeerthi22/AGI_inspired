# Canonical Project State - AGI_like Harness

> Forward implementation update (2026-09-04): dependency artifact hashes,
> fail-closed egress and remote-audit protocols, and independent critic routing
> are implemented. Deployment evidence remains required; no live execution is
> authorized.

**Last Updated:** 2026-09-07 (Real-world validation cohort fully PASSED at 7/7 [100% pass yield] across all mission types M1-M7 under Windows Restricted Token containment; broker multi-engine search and isolation journal hardened; model-free gate 75/75 green; worker readiness 6/6 pass; ESTOP strictly engaged)
**Phase:** Real-world validation cohort complete (7/7 missions PASSED: M1 Task 106 pass, M2 Task 109 pass, M3 Task 140 pass, M4 Task 115 pass, M5 Task 137 pass, M6 Task 145 pass, M7 Task 150 pass); F126 deliverable preflight & auto-repair proven; Step 2 WFP firewall rules enforced; egress broker verified; ESTOP strictly re-engaged
**Safety Status:** ESTOP engaged (`True`) | Zero live execution active | Egress WFP deny-direct-egress rule active
**Verification:** Full model-free gate: 75/75 suites green, exit 0 (11/11 egress policy, 13/13 deliverable preflight, 17/17 worker sandbox, 19/19 audit signer, 16/16 pty daemon, 2/2 m5 dryrun). Worker readiness diagnostic: 6/6 checks PASS (with broker active). Live cohort execution: 7/7 missions pass. ESTOP strictly engaged.

Current handoff: `docs/reviews/GEMINI_COHORT_FULL_VALIDATION_2026-09-07.md`, `docs/reviews/GEMINI_M5_LIVE_VALIDATION_2026-09-07.md`, `docs/reviews/GEMINI_POSTMORTEM_TASK130_TASK131_2026-09-06.md`, `docs/SHARED_LAUNCH_BRIEF_2026-09-06.md`, `scripts/check_worker_readiness.py`, and `scripts/enforce_worker_firewall.ps1`.
Step 2 host hardening (`scripts/enforce_worker_firewall.ps1`) is fully provisioned and enforced on this host:
Windows Defender Firewall / WFP rules `AGI_Worker_Allow_Broker_Loopback` (allow 127.0.0.1:8787 TCP) and `AGI_Worker_Deny_Direct_Egress`
(deny direct Internet for restricted worker SID S-1-5-12) are both verified [PASS] ENABLED. Signed attestation token
at `.harness/egress_attestation.signed` is cryptographically valid and matches `config/egress_policy.yaml`.
Search provider reliability fix landed: `orchestrator/controlled_hermes.py` patches DDGS web search to run in-process via
egress broker proxy (127.0.0.1:8787), resolving DuckDuckGo HTML layout changes and eliminating the 30-minute metasearch / subprocess stripping hang.
Postmortem for controlled-window failures completed (`docs/reviews/GEMINI_POSTMORTEM_TASK130_TASK131_2026-09-06.md`):
1. Task 130 SQLite lock resolved by pre-emptively monkey-patching `tools.async_delegation.restore_undelivered_completions` before `run_agent` import.
2. Task 131 provider discovery failure resolved by pointing `HERMES_HOME` to dedicated worker home (`workspace/worker_home`) where `config.yaml` resides.
3. Task 131 1800s hang resolved by explicitly closing non-interactive worker stdin pipes and trimming toolsets from `-t web,browser` to `-t web` avoiding Job Object UI restriction deadlocks.
4. Model-free test gate verified at 74/74 green. ESTOP re-engaged (`True`). Ready for immediate resumption when upstream quota resets.
F126 implements deliverable preflight and a mechanical auto-repair loop in
`orchestrator/deliverable_preflight.py` and `orchestrator/task_runner.py`, directly
targeting the 1/6 live cohort yield bottleneck. Incorporates multi-agent peer review
consensus (Gemini + Claude): reuses `citecheck.py` directly without socket duplication,
strictly preserving the RC-1 fix (HTTP 403 is BLOCKED, not DEAD), provides schema &
disclaimer linting (M3 platform coverage and M7 'not publicly disclosed' tables), adheres
to the F10 anti-injection floor (metadata only, no raw HTML), bounds repair to
MAX_REPAIR_ATTEMPTS=2 with token budget checks, and accumulates token spend. OmniRoute
is held decoupled from live routing to preserve the F124 restricted token boundary.
F127 implements the multi-engine in-process search adapter (`yahoo`, `brave`, `auto`) and egress
broker policy synchronization (`config/egress_policy.yaml`), along with retrieval streak tuning
(`low_novelty_limit=4`), resolving verification asymmetry between sandboxed workers and host critics.
F128 hardens `CohortIsolation._write_journal` with exponential backoff retry to eliminate Windows NTFS
atomic file lock contention during validation windows.
F124 research workers use CreateRestrictedToken/CreateProcessAsUserW,
a deny-only user SID, removed privileges, restricting SIDs, explicit pipe-only
inheritance, private desktop and Job Object/UI restrictions. Real synthetic
tests prove credential, signer-pipe and controller-resource denial, worker/child
execution and tree teardown. There is no unrestricted research fallback.
`HARNESS_WORKER_HOME` must name a separately provisioned worker directory;
ambient controller secrets and loader overrides are not copied to the child.
This is not a distinct Windows account, per-worker tenant isolation, or a firewall.
The production user-private Hermes runtime has not been repackaged or ACL-granted;
its compatibility and the actual three-account deployment remain unproven.
F122 replaces controller-side operator-key audit signing with a dedicated
Ed25519 signer daemon and public-only verification. Missing signer configuration
or service health fails closed; there is no legacy local-key signing fallback.
No service account, private key, production daemon, or host policy was provisioned.
The three-identity deployment is still required. F124 replaces the unrestricted
same-user launch; token-level denial is proven only within the documented ACL
contract, not against every same-session service or host configuration.
F123 isolates F58's filesystem remediator after it quarantined a concurrent edit.
F125 fixes a reproduced signer preconnected-pipe accept hang discovered during
the F124 full gate. Two new regressions pass; signer suite is now 19/19 with
20 consecutive suite runs green. Full post-change gate passed 74/74 suites green.

## Current Integration Checkpoint

Local master now includes `7c1d19f` (F111), `f4b9e1d` (F118),
`1d822a8` (Gemini handoff), fast-forwarded with the Codex ownership checkpoint,
and F121 runtime release admission enforcement.
No push or live execution was performed.

This is a model-free verified control prototype, not an enterprise release.
The September 5 audit supersedes earlier claims that only operator deployment
remains. Open code/design findings include restricted worker identity and
proof of deployed signer separation, while runtime admission of release prerequisites has now been
hardened in F121.

F119 completed: full-history replica verification implemented in `audit_state()`
and `replicate_trajectory()` with hermetic regressions (`tests/test_audit_replication.py`),
ensuring corruption or deletion of older historical replicas fails closed.
F120 serializes the full tip-read/copy/sign/append transaction with an OS-backed
sidecar lock, and verifies history before copying so a same-source retry cannot
silently recreate a deleted historical replica.
F121 completed: fail-closed runtime release admission contract implemented in
`orchestrator/runtime_admission.py`, directly enforced before worker/task dispatch in
`orchestrator/batch_runner.py`, `orchestrator/task_runner.py`, and `orchestrator/run_task.py`,
and covered by `tests/test_runtime_admission.py` (10/10 green).
Cross-host SMB/failover evidence is still required; the local regression suite does not establish fencing.
Deployment still requires actual OS denial evidence, UNC retention/restore proof,
clean-machine CI, and independent security review. No current `safe_to_proceed=true`
result is claimed.

---

## 1. Executive Summary

On September 7, 2026, the frozen real-world validation cohort (`workspace/validation/cohort_missions.json`) achieved 100% pass yield across all 7 missions under Windows Restricted Token containment (`S-1-5-12`) and independent critic review (`glm-5.2:cloud`):

| Scope | Status | Evidence |
| :--- | :---: | :--- |
| Full Cohort Yield (7/7) | PASSED LIVE | **100% (7/7) pass yield** across frozen benchmark cohort (M1-M7) under Windows Restricted Token containment & independent critic |
| M1 / task 106 | PASSED | PromptHero community intel; MAU, categories, split, sources verified (done/pass) |
| M2 / task 109 | PASSED | Canonical AIPRM pricing table; 4 tiers, monthly/annual, discounts (done/pass) |
| M3 / task 140 | PASSED | PromptBase review sentiment; blocked-source declaration, ratings, 3 themes, 6-mo trend (done/pass) |
| M4 / task 115 | PASSED | Clean 4-competitor synthesis snapshot table (done/pass) |
| M5 / task 137 | PASSED | FlowGPT hero claim verification; verbatim quote, independent sources, unconfirmed verdict (done/pass) |
| M6 / task 145 | PASSED | Hacker News AI prompt library citation count; cc-hindsight leading tool, independent blogs (done/pass) |
| M7 / task 150 | PASSED | AI prompt marketplace landscape; 6 marketplaces, 5 columns, verified 2+ sources per subject (done/pass) |
| Full model-free gate | VERIFIED | `75/75` suites green across unit, containment, and integration tiers |
| Step 2 WFP Hardening | ENFORCED | Both WFP rules verified active; Ed25519 attestation signed and verified |
| Supervised BytePlus canary | VERIFIED LIVE | `ok=true`, provider `byteplus_coding`, model `ark-code-latest` |

---

## 2. What Was Corrected

Key infrastructure fixes landed to unlock full cohort yield:
1. **Verification Asymmetry Elimination:** Added research domains to `config/egress_policy.yaml` with signed Ed25519 attestation, giving workers and critics identical egress vantage points.
2. **Multi-Engine Search Adapter:** `orchestrator/controlled_hermes.py` patched to route searches via Yahoo and DDGS proxy backends, bypassing DuckDuckGo HTML layout CAPTCHAs.
3. **Retrieval Progress Streak Tuning:** Adjusted `low_novelty_limit=4` in `controlled_hermes.py`, allowing workers encountering blocked sources (e.g. 403/429) to proceed to fetch fallback sources before stage timeout.
4. **Transactional Isolation Backoff:** Added 5-attempt exponential backoff in `workspace/validation/cohort_isolation.py` `_write_journal`, eliminating transient Windows NTFS file lock conflicts.
5. **Finalization Guidance:** Hardened prompt guidance in `orchestrator/retrieval_progress.py` to require structured synthesis and prevent premature bounded failure reports.

---

## 3. Current Safety And Runtime Invariants

* ESTOP remains engaged between controlled windows.
* No live runlock is present after the cohort windows.
* Isolation restored cleanly after each controlled window.
* Rows 111-113 remain untouched legitimate queued seeds.
* Live repository, process, and operator status outrank historical documents.

Historical operator status on `2026-09-03T22:49:11Z` (not the current branch/checkpoint):

* on branch `claude-code/telemetry-truth-fixes-2026-09-03`, 6 commits ahead of `master`, working tree carrying only the doc-sync edits
* continuity revision `55` (pending bump to `56` after the master FF-merge + integration commit)
* ESTOP engaged, no canary marker present
* runlock absent
* Munder quiesced
* ARK_API_KEY removed from the Hermes private `.env` and vaulted in Windows Credential Manager (`credential_manager_has_api_key("byteplus_coding")=True`, presence-only — value never read)

Recorded subsystem warnings visible through `agi status` were diagnosed 2026-09-03
as pre-F108 *test artifacts*, not active live probes: unit-tier tests wrote health
events to the production `runs/health_events.jsonl`, and `agi status` (newest event
per subsystem) replayed them. F108 routes test health events to a pid-scoped temp via
`_guarded_env`, so new test runs no longer pollute the production log and `agi status`
no longer cries wolf. The residual events already in the log (pre-F108) are stale test
artifacts, not live warnings; a one-time operator cleanup (back up + truncate) is
optional — the log is gitignored and overwritten in use.

The repeated runtime warning about `.claude/settings.local.json` being masked by an
unversioned exclude source is RESOLVED (F107, 2026-09-03): listed in the versioned
`.gitignore` so it drops out of the F47 masking set. `MASKED=[]` verified in the real
repo.

---

## 4. Remaining Gaps

Control-plane gaps:

* ~~protected-path masking warning still fires during cohort windows~~ RESOLVED (F107,
  2026-09-03): `.claude/settings.local.json` moved to the versioned `.gitignore`;
  `MASKED=[]` verified
* ~~recorded subsystem health events need post-cohort triage~~ RESOLVED (F108,
  2026-09-03): test health events now route to a pid-scoped temp; `agi status` no
  longer replays test artifacts. Residual pre-F108 log events are stale (operator
  one-time cleanup, optional)
* per-task critic artifacts (`task{N}_critic.usage.json` / `_citation_evidence.json`)
  no longer leak from test_f57 into production `runs/` (F109, 2026-09-03): test_f57
  section 3 redirects `ev.RUNS`/`rc.RUNS` to temp; pinned by `test_f109`
* provider capacity is still externally constrained; BytePlus and Ollama cloud
  quota exhaustion remain real operating conditions
* Anthropic and OpenAI credentials are not currently usable in this environment

Enterprise gaps:

* operator-marker trust boundary is now anchored (commits `351104e`, `d8037f3`): unsigned
  markers fail closed, foreign self-signed markers are rejected via
  `hmac.compare_digest(embedded, trusted_public)`, markers are purpose-bound
  (`action` field checked in both `authorize-clear` and `authorize-canary`), and signing
  failure raises instead of falling back to unsigned JSON. Independently re-verified by
  diff + 61/61 gate.
* ARK_API_KEY is vaulted in Windows Credential Manager and gone from the Hermes `.env`;
  the UTF-16LE read/write path matches what pywin32 actually returns (verified by a live
  `credential_manager_has_api_key` read). Anthropic/OpenAI remain unconfigured by design
  (weak-AI strategy), so those rungs will still report missing credentials — intentional.
* an authoritative model-free release preflight (`agi preflight`) and CI pinning / venv
  fix landed (`scripts/ci.ps1`, `.github/workflows/model_free_gate.yml`).
* dependency artifacts are hash-locked and bootstrap enforces hash verification;
  Hermes is separately attested as an external checkout. A clean-machine/CI
  installation remains unproven.
* worker launch now fails closed without a signed, time-bounded egress boundary
  attestation. The repository supplies a bounded HTTPS CONNECT broker, but no
  Windows restricted identity or OS firewall/AppContainer boundary has been
  provisioned on this host.
* trajectory replication and signed remote checkpoints are implemented, but no
  remote UNC root is configured and audit enforcement is deliberately off.
* BytePlus is the configured critic provider while Ollama is the primary worker
  provider. Same-provider failover routes to `needs_review`, not self-grading.
* sustained operational proof, restore drills, a calibrated evaluation corpus,
  and independent security review remain open. This is a control prototype,
  not enterprise-finished.

Product-quality gap:

* of the remaining frozen windows opened on September 3, only `M4` passed

This remains a strong enterprise-candidate control prototype, not an
enterprise-finished product.

## 6. Forward Implementation Update (2026-09-04)

Completed on `master`:

* `0701dc5` F110: citecheck distinguishes blocked server responses from dead
  citations and preserves hard failures for genuinely gone/unreachable URLs.
* `8fb3efd` A5: empty worker and synthesis failures persist bounded diagnostic
  text instead of zero-byte raw artifacts.
* `45d7846` A3: failover trajectory events carry the actual prior failure
  reason; the authentication transition is regression-tested.
* `b9d7499` local audit: new trajectory events carry `prev_event_hash` and
  `event_hash`; `agi preflight release` verifies persisted chains.

The repository-controlled P1 security work is implemented: SHA-256 dependency
locking, fail-closed egress attestation and broker code, signed remote-audit
replication, and independent critic routing. The remaining work is deployment
and proof: a restricted worker identity and OS egress policy, an append-only
UNC audit share plus restore drill, clean-machine CI, calibration, and an
independent security review. See
`docs/EGRESS_AND_AUDIT_DEPLOYMENT_RUNBOOK_2026-09-04.md`.

---

## 5. Next Exact Action

1. If another live validation step is authorized, choose explicitly between:
   task `110` retry, or targeted revisits of failed windows `M3`, `M5`, `M6`,
   and `M7`.
2. Do not spend another live attempt without acknowledging provider reality:
   BytePlus quota can exhaust, Anthropic/OpenAI credentials are currently
   absent, and the local gemma rung may be the only remaining completion path.
3. Post-cohort backlog status (2026-09-04): items 1–4 DONE + committed (`4f773e6`) —
   (1) protected-path warning (F107), (2) preflight/health-warning triage (F105 cohort
   entry + F108 test pollution), (3) spec-lint/crying-wolf cleanup (F108 health events
   + F109 runs/ artifacts; "spec-lint" proper has no existing code, remains an open
   proposal), (4) hermeticity audit (F108 + F109 — test runs no longer pollute
   production `runs/`). Item 5, the P1 security stack, is now PARTIALLY landed: the
   operator-marker trust boundary, vault-backed ARK_API_KEY (Credential Manager),
   authoritative model-free release preflight, CI pinning / venv fix, and a dependency
   conflict resolution all landed in `351104e` + `d8037f3` and were independently
   re-verified by claude-code (61/61 gate, diff review, presence-only credential check)
   before the branch was merged to master. **Still open** (each needs operator
   architectural decisions): host identity / engine-independent egress sandbox (Job Object
   containment + outbound egress policy), tamper-evident off-machine audit retention,
   reproducible dependency hashes, independent critic evidence routing, sustained
   operational proof. See `docs/AGENT_HANDOFF_2026-09-03_SECURITY_PREFLIGHT_INTEGRATION.md`
   for the fixed-vs-open breakdown. This is model-free repo work only and does NOT clear
   anything for live execution.

---

## 7. Release And Next Actions (Supersedes Section 5)

The next action is not a live cohort retry. First conduct an independent review
of `aa5afaf` against the release preflight and deployment runbook. The operator
then provisions the real controls that repository code cannot create:

1. A restricted worker service identity and OS-enforced egress rule, followed
   by direct-denial, raw-socket, and private-address tests and a fresh signed
   `HARNESS_EGRESS_ATTESTATION`.
2. An append-only UNC audit replica with the harness write identity separated
   from the review/restore identity, followed by enforcement and an independent
   restore-and-verify drill.
3. A clean Windows machine and pinned CI runner that install the hash lock and
   run the model-free gate without hidden local dependencies.
4. An independent security reviewer who records findings against the code,
   host deployment evidence, access controls, and restore evidence.

Only after `python -B orchestrator/operator_cli.py preflight release` has no
blockers, the upstream is synchronized, and the operator explicitly opens a
controlled window may a supervised live validation be considered. Provider
capacity and missing Anthropic/OpenAI credentials must be treated as explicit
operating constraints, not as a reason to weaken the gate.

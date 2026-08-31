# Enterprise Readiness and Operator Experience Review

**Review date:** 2026-08-31

**Review basis:** Live repository and host state at `4e768eba118c0ca7d8ca276d4f6b2610beed983f`

**Review mode:** Read-only architecture, operations, safety, recovery, and developer/operator experience audit

**Live execution:** None; no provider, canary, M1-M7, ESTOP transition, or Phase 2/3 work was performed

This report is the canonical enterprise-readiness assessment for the current
AGI_like/Munder harness. It is a planning and promotion record, not an execution
authorization. Live repository, runtime, database, process, and safety state
always outrank this document.

## 1. Current Classification

**Classification: PRE-ENTERPRISE**

**Overall maturity: 2.9 / 5**

The control-plane design is approximately **65-75% of the way toward an
enterprise candidate**. The whole system, including identity, containment,
deployment, audit retention, observability, and operations, is approximately
**50-60% of the way there**.

This rating is intentionally conservative. The harness has unusually mature
local safety and recovery behavior for a prototype, but enterprise claims
require reproducible deployment, strong identity, OS-enforced isolation,
durable audit, operational SLO evidence, and validated recovery under sustained
use.

### Review-time evidence

* Git HEAD was `4e768eba118c0ca7d8ca276d4f6b2610beed983f`; the working tree was clean.
* Continuity revision 32 validated with zero discrepancies and all reference hashes matching.
* `python -B tests/run_all.py` passed 45/45 model-free suites: 36 unit,
  6 containment, and 3 integration. The one live-tier suite was not run.
* ESTOP was engaged; isolation was restored; the batch lock and canary marker
  were absent; no implementation owner held a write scope.
* Both SQLite databases passed `PRAGMA quick_check`, use WAL, and use synchronous level 2.
* Nightly SQLite backups and Git bundles exist and target a OneDrive-classified
  off-machine sync folder, although completion of cloud synchronization is not independently verified.
* The local branch was 15 commits ahead of `origin/master` at review time.
* Four Munder Electron host processes were open. No development worker was
  observed, but the engine-independent quiescence gate correctly treated the
  host processes as mutation-capable and would refuse a controlled live window.

## 2. Maturity Scores

| Area | Score | Ruling |
|---|---:|---|
| Safety / fail-closed controls | 4.2 | Layered and unusually strong; remaining controls are admission-time and primarily same-user. |
| Security / secrets / authorization | 2.0 | No committed secrets, but no strong operator identity, vault, rotation, or enterprise RBAC. |
| Process and OS isolation | 2.3 | Good transactional quiescence; no restricted worker identity, Job Object tree, container, or egress sandbox. |
| Crash recovery / resumability | 3.8 | Durable journals, exact process identity, leases, retries, and trajectory resumption; no mid-step checkpointing. |
| Multi-agent coordination | 3.2 | ACTIVE_WORK, continuity, handoffs, Mailbus, and Munder boundary; lifecycle remains manual. |
| Mission admission / policy | 3.5 | Human gate, ESTOP, runlock, budget checks, and controlled windows; some policy is still heuristic. |
| Provider abstraction / failover | 3.1 | Typed adapters and quota-aware fallback; current independent provider depth is limited. |
| Evaluation / hallucination control | 3.3 | Mechanical citecheck, tri-state critic, evidence files, and spot checks; critic independence is limited. |
| Retrieval / evidence provenance | 3.7 | Externally enforced progression, bounded evidence, audit JSONL, and explicit partial results. |
| Observability / trajectories | 3.3 | Structured local events and usage accounting; no centralized metrics, SLOs, or alert manager. |
| Auditability / compliance readiness | 2.5 | Strong Git and incident records; runtime evidence is local, unsigned, and not retention-managed. |
| State / memory architecture | 3.2 | Typed ledgerbook, continuity, WAL, and backups; no schema migration/version discipline. |
| Testing / fault injection | 3.6 | Excellent incident-driven model-free regression; no clean-machine CI, coverage gate, or sustained chaos matrix. |
| Deployment / rollback / upgrades | 1.5 | Backups exist; dependency lock, CI/CD, signed release, schema migration, and upgrade tooling do not. |
| Cost / token / latency governance | 3.1 | Token/call/cost accounting exists; pre-call caps can overshoot and no centralized quota/latency view exists. |
| Human approval / UX | 3.0 | Authority is explicit; the workflow is file- and CLI-heavy and operator tokens are non-cryptographic. |
| Developer experience | 2.5 | Good bootstrap and modular code; setup is not reproducible and current guidance is spread across large documents. |
| Operator experience | 2.2 | State is discoverable but must be manually reconciled across many commands and files. |
| Scalability / concurrency | 1.8 | Safe single-writer execution; no automated worktrees, contained PTY fleet, or concurrent scheduler. |
| Production readiness | 2.4 | Advanced validated prototype, not yet a production platform. |

## 3. Strongest Areas

### Fail-closed safety

ESTOP, tamper recovery, runlock, transactional isolation, Munder/process
quiescence, single-use canary scope, and human mission authority form a layered
admission system. This is differentiated because live authority is represented
as machine-checkable state instead of only prompt text.

### Recovery and mutation containment

Database mutation guards, durable recovery journals, SQLite online backups,
lease reconciliation, interrupted-task recovery, and trajectory sequence
resumption provide multiple recovery layers. This is a credible foundation for
long-running agent work.

### Retrieval and evidence controls

The externally enforced `search -> direct_fetch -> browser -> partial_result`
progression measures novelty across tool families, limits opaque delegation,
preserves bounded evidence, and requires an evidence-only finalization. This is
one of the system's strongest potential competitive moats.

### Continuity

The compact brief is small, atomic, hashed, and explicitly subordinate to live
state. Combined with durable handoffs, it allows context/model recovery without
blindly trusting conversational summaries.

### Multi-agent boundaries

Munder coordination state is external to the AGI repository. ACTIVE_WORK,
reviewer read-only roles, external Hive enforcement, and engine-independent
quiescence separate development coordination from mission authority.

### Incident-driven regression

The hardening and incident registries convert actual failures into permanent
tests. The 45-suite model-free gate contains unusually specific containment and
recovery regressions rather than only happy-path unit tests.

## 4. Main Enterprise Gaps

### Operator identity and authentication

The current one-use markers are scoped and fail closed, but are not
cryptographically bound to a verified human identity. A same-user shell can
manufacture files that resemble operator authorization.

### Secrets management

Repository hygiene and local ACLs are reasonable, but credentials remain in a
user-level environment file rather than a managed vault with rotation,
least-privilege retrieval, expiration, and access audit.

### OS and process containment

Workers execute as the operator's Windows account. Prompt restrictions and
post-call integrity checks cannot prevent writes outside the repository,
credential access, arbitrary process spawning, or orphan descendants. Windows
Job Objects, a restricted service identity, and explicit filesystem boundaries
are still required.

### Network and egress controls

Provider endpoints are constrained, and citecheck blocks an initially resolved
private address. However, redirect destinations must also be revalidated and
DNS-rebinding/private-network access must be blocked by an engine-independent
egress boundary.

### Reproducible deployment and Windows CI

The repository has no dependency manifest, lockfile, clean-machine bootstrap,
CI workflow, build artifact, release tags, or supported upgrade path. The
installed Python environment therefore cannot be independently reconstructed.

### Database migrations

Both databases are healthy, but `user_version` is zero and connections do not
establish a versioned migration contract. Schema changes need forward and
rollback tests, explicit per-connection pragmas, and upgrade ordering.

### Durable, tamper-evident audit

Git records code and decisions, but trajectories, health events, Mailbus logs,
and mission evidence are local runtime files. They lack signatures/hash chains,
central retention, access controls, and verified off-machine delivery.

### Metrics and SLOs

Structured events exist but are not aggregated into availability, admission,
recovery, quality, latency, cost, or queue SLOs. Alerting is local and
subsystem-specific.

### Operator UX

The operator must manually inspect Git, continuity, ESTOP, isolation, runlocks,
ACTIVE_WORK, process quiescence, tests, backups, provider status, and mission
artifacts. The state exists, but there is no single trustworthy view.

### Scalability and concurrency

The single-writer design is appropriately safe today. Enterprise concurrency
will require isolated worktrees, atomic task claims, per-agent resource limits,
contained process trees, and tested queue semantics before a persistent
multi-agent dispatcher is appropriate.

## 5. Priority Ladder

### P0 - before meaningful live validation

1. Establish literal Munder/process quiescence before every controlled window.
2. Preserve the current local baseline off-machine: push the reviewed commits or
   create and verify a fresh off-machine Git bundle.
3. Close redirect/private-network egress exposure before unattended web-facing
   missions. A bare, manually supervised provider connectivity probe does not
   exercise citecheck and remains governed by the separate operator decision.
4. Complete the existing static review -> one-shot BytePlus probe -> separately
   authorized M1-M7 sequence. No document in this report authorizes it.

### P1 - before enterprise candidate

* Strong operator identity and vault-backed secrets.
* Windows Job Object/restricted worker containment and outbound egress policy.
* Versioned, checksummed installation of external Munder enforcement.
* Locked dependencies, clean-machine bootstrap, and Windows CI.
* SQLite schema migrations and explicit connection pragmas.
* One authoritative model-free operator preflight command.
* Durable redaction-consistent audit retention.
* Independent critic routing or a calibrated evaluation corpus.

### P2 - before enterprise production

* Tamper-evident off-machine audit storage and retention policy.
* Metrics, alerts, SLOs, incident severity, and operator runbooks.
* Tested RPO/RTO and scheduled restore drills.
* Signed release artifacts, staged upgrades, and automated rollback.
* Enterprise RBAC and separate operator/reviewer/worker/service identities.
* Provider data residency, retention, and contract controls.
* Controlled worktree/concurrency scheduling.
* SBOM, dependency/secret scanning, and external penetration testing.

### P3 - competitive and scale improvements

* Controlled capability registry for APIs, Python tools, and OSINT utilities.
* Derived FTS memory only after measured recall demand.
* Automated reviewer selection and evidence packets.
* Outcome-aware provider cost/quality routing.
* Semantic evidence graph and freshness scoring.
* Multi-host scheduling after single-host correctness is proven.

## 6. Operator Life-Smoother Automations

| Automation | Classification | Boundary |
|---|---|---|
| One-command `agi status` | **SAFE TO AUTOMATE** | Read-only reconciliation of Git, continuity, ownership, ESTOP, isolation, lock, processes, tests, and provider history. |
| One-command `agi health --model-free` | **SAFE TO AUTOMATE** | Run guarded tests, continuity validation, read-only DB checks, and drift detection; no provider access. |
| One-command `agi preflight canary` | **SAFE TO AUTOMATE** | Diagnostic only. It must never issue an authorization, clear ESTOP, open isolation, or contact a provider. |
| Automatic ACTIVE_WORK claim/release | **SAFE TO AUTOMATE** | Compare-and-swap ownership with overlap checks, process identity, explicit task ID, and auditable release. |
| Automatic worktree lifecycle | **SAFE TO AUTOMATE** | One worktree per implementation task; never delete until merge/archive state is proven. |
| Automatic handoff generation | **SAFE TO AUTOMATE** | Draft from Git, tests, task state, and continuity; the owner reviews before publishing. |
| Automatic continuity refresh | **SAFE TO AUTOMATE** | Only after a verified checkpoint; live state always wins. |
| Stale-owner detection | **SAFE TO AUTOMATE** | Detect and flag. Ambiguous ownership must not be silently reclaimed. |
| Quota/provider reporting | **SAFE TO AUTOMATE** | Read-only status and historical measurements; active connectivity probes remain gated. |
| Runaway/tool-call/token/diff-size alerts | **SAFE TO AUTOMATE** | Warn and recommend recovery; do not silently change mission authority. |
| Reviewer assignment | **SAFE TO AUTOMATE** | Assign read-only reviewers with no conflicting write scope. |
| ESTOP clear/re-engagement override | **MUST REMAIN HUMAN-AUTHORIZED** | Automatic fail-closed re-engagement is allowed; clearing or overriding is not. |
| Canary authorization/execution | **MUST REMAIN HUMAN-AUTHORIZED** | One-use, provider-locked, purpose-locked, time-limited, and supervised. |
| Controlled mission start | **MUST REMAIN HUMAN-AUTHORIZED** | Automation may prepare evidence and command, never approve execution. |
| Skill promotion, destructive recovery, production rollback | **MUST REMAIN HUMAN-AUTHORIZED** | Always provide dry-run and impact evidence first. |

## 7. Control App Roadmap

The UI must not become a safety dependency. Backend checks and state remain
authoritative; the application is a view and authenticated command client.

### V1

Show ESTOP and isolation state; batch/runlock state; Munder/process quiescence;
ACTIVE_WORK scopes; Git and upstream state; backup age; continuity health;
model-free test status; mission queue; provider health/quota; trajectories;
critic/evidence results; tokens/cost; and pending approvals.

Control only safe operations: refresh status, run model-free health, claim/release
development scope, create a worktree, draft a handoff, and prepare a canary
preflight. Human-authorized actions must require strong backend authentication.

### V2

* Evidence and citation browser.
* Recovery wizard with dry-run reconciliation.
* Provider failure/quota history and mission dependency graph.
* Agent heartbeat, token, tool-call, elapsed-time, and diff trends.
* Reviewer queue, signed decisions, backup/restore health, and alerts.

### Later

* Multi-user RBAC and multi-host scheduling.
* Policy promotion workflows and fleet-wide audit search.
* Capacity planning and measured provider optimization.

## 8. Enterprise Promotion Criteria

### PRE-ENTERPRISE -> ENTERPRISE CANDIDATE

* Reproducible locked environment and clean-machine Windows CI.
* Model-free safety/fault matrices green for ten consecutive main-branch runs.
* Strong operator identity, managed secrets, OS process containment, and egress enforcement.
* Versioned Munder enforcement and SQLite migrations.
* Three successful recovery/restore drills.
* M1-M7 validated with reconciled trajectories, usage, evidence, critic, and ledger outcomes.
* No open high-severity security finding.

Recommended deterministic additions include at least 40 admission cases,
20 ESTOP/authorization misuse cases, 20 ACTIVE_WORK/concurrent-write cases,
15 isolation/restore cases, 15 stale-lock/process-identity cases, 15 continuity
corruption cases, 30 storage/crash faults, and 20 retrieval/evidence failures.

### ENTERPRISE CANDIDATE -> ENTERPRISE GRADE

* 30-60 days of production-like evidence against declared SLOs.
* At least 100 representative missions and 10 long-mission soaks.
* Zero unauthorized provider calls or unreconciled mission-state drift.
* Full reconciliation across ledger, trajectory, evidence, critic, and audit.
* Measured RPO/RTO met in restore drills.
* Tamper-evident, access-controlled audit retention.
* Signed releases, proven rollback, independent penetration test, and no
  unresolved severity-one or severity-two incident.

## 9. Thirty-Day Roadmap

### Day 1-3

* Preserve and push/tag the reviewed baseline.
* Quiesce Munder and complete the existing authorized canary/M1-M7 sequence
  without modifying the validated control plane.
* Capture real latency, quota, token, recovery, and outcome evidence.
* Specify the unified `agi status / health / preflight` read-only contract.
* Pin the current Python and Node dependencies.

### Week 1

* Implement the unified model-free operator command.
* Add locked Windows CI and clean-machine bootstrap.
* Version/checksum the external Munder enforcement artifact.
* Correct stale gate wording and add redirect-safe citecheck tests.

### Week 2

* Add strong operator authentication and vault-backed secrets.
* Add Windows Job Object worker containment and egress restrictions.
* Introduce SQLite migrations and upgrade/rollback tests.
* Expand process, SQLite, and interrupted-write fault injection.

### Week 3

* Build Control App V1 over the authoritative status/preflight API.
* Add atomic task claims, stale-owner detection, and worktree automation.
* Aggregate metrics/alerts and make audit retention explicit.

### Week 4

* Run crash, concurrency, network, backup, and restore drills.
* Execute authorized long-mission soak tests and validate two provider pools.
* Perform independent security review and produce an enterprise-candidate scorecard.

## 10. Do Not Build Yet

Do not prematurely build:

* Autonomous removal or weakening of the human live-execution gate.
* A self-modifying orchestrator or autonomous production-code evolution.
* Kubernetes, microservices, or distributed deployment before single-host correctness is proven.
* Phase 3 persistent drain/dispatch daemons while those phases remain frozen.
* General memory/FTS infrastructure without a measured retrieval/recall need.
* A large capability marketplace before identity, egress, sandboxing, and audit controls exist.
* A UI whose availability or correctness becomes part of the safety boundary.
* A rewrite of working retrieval, continuity, recovery, or fail-closed controls merely to follow fashion.

## 11. Single Recommended Next Implementation

Build the unified, model-free operator command:

```text
agi status
agi health --model-free
agi preflight canary
```

This is the highest-impact, lowest-risk next implementation because it composes
existing authoritative checks without changing mission behavior. It reduces
manual reconciliation errors, becomes the backend contract for Control App V1,
and can be developed and fully tested while ESTOP remains engaged and live
validation stays frozen.

It must remain observational and diagnostic. In its first version it must not
authorize a canary, clear ESTOP, open isolation, stop processes, contact a
provider, start a mission, mutate ACTIVE_WORK, or perform recovery.

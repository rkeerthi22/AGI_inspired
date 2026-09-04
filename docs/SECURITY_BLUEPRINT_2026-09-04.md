# Security Blueprint - 2026-09-04

## Scope

This is the forward security plan for the AGI_like harness after the RC-1,
A5, A3, and local trajectory-audit work. It is model-free documentation and
does not authorize provider calls, ESTOP changes, or cohort execution.

## Completed Controls

| Control | Evidence | Boundary |
|---|---|---|
| Citation truth | `0701dc5`, F110, `tests/test_f110.py` | 403/429/5xx are blocked, not dead; 404/410 and connection failures remain dead. |
| Early-abort evidence | `8fb3efd`, A5, `tests/test_a5.py` | Empty worker/synthesis output now records bounded failure diagnostics. |
| Failover telemetry | `45d7846`, trajectory regression | `failover_attempted` carries the classified prior reason. |
| Local audit chain | `b9d7499`, `trajectory.verify_chain()` | New trajectory events are hash-linked; release preflight verifies them. |
| Operator trust | `351104e`, `d8037f3` | Unsigned/foreign markers fail closed; purpose is bound; keys are vaulted locally. |
| Dependency artifacts | `dependency_integrity.py`, `tests/test_dependency_integrity.py` | 205 public packages are SHA-256 locked; bootstrap uses `--require-hashes --no-deps --no-input`; Hermes is separately version/revision/hash attested. |
| Worker egress contract | `egress_policy.py`, `egress_broker.py`, `tests/test_egress_policy.py` | Worker launch fails closed without a fresh signed boundary attestation; the broker allows HTTPS CONNECT only, validates exact hosts and public DNS answers, and bounds idle time and relay bytes. |
| Remote audit protocol | `audit_replication.py`, `tests/test_audit_replication.py` | An enforced release copies verified trajectories to a configured UNC replica and appends signed hash-linked checkpoints; tampering, absence, and stale checkpoints block preflight. |
| Independent critic route | `models.yaml`, `evaluation.py`, `tests/test_critic_independence.py` | Critic defaults to BytePlus while the worker defaults to Ollama; a same-provider failover becomes `needs_review` before a critic request. |

## Deployment And Evidence Still Required

### 1. Clean-machine dependency proof

The public dependency lock and negative tamper test are implemented. A clean
Windows machine and CI runner must still install from that lock successfully;
the local Hermes checkout remains an explicitly attested external runtime, not
a pretend PyPI package.

### 2. Actual Windows egress containment

The repository does not create a Windows service identity, Firewall/WFP rule,
AppContainer, or egress VM. Before release, deploy one of those OS-enforced
boundaries so the worker cannot clear proxy variables, open a raw socket, or
spawn an unconfined network-capable child. Produce the signed attestation only
after that deployment passes its direct denial, raw-socket, and private-address
tests. The broker's current provider-only allowlist deliberately does not
authorize unrestricted web research; a mission-scoped public-web grant design
is required before browser research can run through this boundary.

### 3. Actual off-machine audit retention

The protocol is present, but no `HARNESS_AUDIT_REPLICA_ROOT` UNC destination is
configured and enforcement is intentionally disabled. Provision an append-only
remote share, set `HARNESS_AUDIT_ENFORCE=1` only for the harness service, and
perform an independent restore/verification drill. The current signer is a
purpose-bound Credential Manager bridge, not a separate KMS or enterprise RBAC
boundary.

### 4. Independent evaluation calibration

Provider separation is enforced for the configured primary worker, but it is
not a substitute for a labeled evaluation corpus, a calibrated critic, or an
independent external reviewer. A worker that fails over to BytePlus is
intentionally sent to human review rather than self-graded.

See `docs/EGRESS_AND_AUDIT_DEPLOYMENT_RUNBOOK_2026-09-04.md` for the exact
operator-owned prerequisites.

## Release Rule

The harness may be used as a supervised internal/private pilot only after
`python -B orchestrator/operator_cli.py preflight release` passes with no
blockers, backups are freshly verified, and an operator scopes each run. It is
not an enterprise-production release until the deployment requirements,
restore evidence, and an independent security review are complete.

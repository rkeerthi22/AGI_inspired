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

## P1 Work Remaining

### 1. Reproducible dependency artifacts

The repository currently has exact version pins, but not artifact hashes and
bootstrap does not use `--require-hashes`. Do not add guessed hashes. Generate
the lock from a clean, approved build environment with `pip-compile
--generate-hashes` or an equivalent audited process, retain all platform
artifacts needed by Windows CI, then change `scripts/bootstrap.ps1` to install
with `--require-hashes`. Acceptance requires a clean-machine install and a
negative test proving a changed artifact hash fails.

### 2. Engine-independent egress boundary

The Hermes tool configuration and Windows Job Object are not a network
security boundary. The production design should run workers under a dedicated
restricted service identity, deny direct outbound traffic by default, and
allow only an audited egress broker. The broker must enforce HTTPS scheme,
host allowlist, DNS resolution outside the worker, redirect revalidation,
private/link-local address denial, request size and timeout limits, and an
append-only decision log. Windows Firewall/AppContainer or an equivalent
per-process policy must be selected and tested; a Job Object alone is
insufficient.

Acceptance requires a containment test that attempts a denied host, a DNS
rebinding/private-address test, and proof that the worker cannot bypass the
broker by changing environment variables or opening a raw socket.

### 3. Audit durability and identity

The trajectory chain is local detection, not tamper-proof retention. Production
needs key-controlled verification, off-machine replication, retention policy,
access audit, and a restore/verification drill. Credential Manager is better
than plaintext but remains same-user accessible; it is not human identity or
RBAC. Use a dedicated service account and operator authentication before
calling this enterprise-grade.

### 4. Independent evaluation

The critic currently shares model/provider risk with the manager in some
configurations. Choose a second provider/model or formally accept a
single-provider fallback, then calibrate it against a labeled corpus. The
current Codex review is adversarial but not an external separation-of-duties
or penetration test.

## Release Rule

The harness may be used as a supervised internal/private pilot only after
`python -B orchestrator/operator_cli.py preflight release` passes with no
blockers, backups are freshly verified, and an operator scopes each run. It is
not an enterprise-production release until the remaining P1 controls and
independent security review are complete.

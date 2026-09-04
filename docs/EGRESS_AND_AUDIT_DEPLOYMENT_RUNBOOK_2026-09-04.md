# P1 Security Deployment Runbook

**Status:** Repository controls are implemented and model-free tested. This
runbook is an operator deployment prerequisite, not authorization for a live
provider call or a controlled mission window.

## 1. Worker Egress Boundary

1. Create a dedicated Windows service identity for the worker. It must not be
   the interactive operator account and must not have access to operator keys,
   provider vault entries, or repository write paths beyond approved worker
   artifacts.
2. Run the egress broker under a separate identity and executable path. The
   worker identity must be denied all direct outbound traffic by an OS-enforced
   control such as Windows Firewall/WFP, AppContainer, or an isolated VM; it
   may reach only the loopback broker.
3. Bind the broker to `127.0.0.1:8787` and use
   `config/egress_policy.yaml`. It accepts HTTPS CONNECT only, resolves the
   approved hostname itself, rejects non-global DNS answers, bounds relay bytes
   and idle time, and records decisions without URLs, headers, or bodies.
4. Run and retain evidence for all five required tests: restricted identity,
   direct-egress denial, broker-only egress, raw-socket bypass denial, and
   private-address/DNS-rebinding denial. Record the worker and broker SHA-256
   values plus the OS policy identifier.
5. Have the operator sign a fresh out-of-repository attestation containing the
   exact `policy_sha256`, evidence names, required claims, and broker endpoint.
   Set `HARNESS_EGRESS_ATTESTATION` to that signed file. It expires after 24
   hours. `agi preflight release` must show
   `worker_egress_boundary_attested=PASS`.

## 2. Audit Retention

1. Provision an off-machine UNC share for `HARNESS_AUDIT_REPLICA_ROOT`. Grant
   the harness service append/write access and operators read/verify access;
   do not grant the service delete or share-administration permission.
2. Keep `config/audit_retention.yaml` unchanged unless the security design is
   reviewed. It requires signed hash-chained checkpoints, a 365-day minimum
   retention policy, and a checkpoint no older than 24 hours.
3. Set `HARNESS_AUDIT_ENFORCE=1` only in the service environment. At task end,
   a verified trajectory is copied under a content-hash name and a signed
   checkpoint is appended. A replication failure converts the task to
   `infra_failed` and raises an escalation.
4. Verify `agi preflight release` reports
   `off_machine_audit_retention=PASS`, then conduct and retain a restore drill:
   verify the checkpoint chain, restore a selected trajectory, and compare its
   SHA-256 to the signed checkpoint.

## 3. Explicit Limits

* A signed local attestation is not proof that a Windows policy was deployed.
  The independent reviewer must inspect the actual service identity and OS
  rules before approving a release.
* The current Credential Manager signer is a bridge, not KMS-backed RBAC. Move
  signing to a dedicated service/KMS key before an enterprise-production claim.
* The static provider host allowlist does not authorize arbitrary public-web
  research. Design a separate mission-scoped host-grant process before enabling
  browser research under this boundary.

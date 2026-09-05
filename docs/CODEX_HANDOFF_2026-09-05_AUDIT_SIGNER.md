# Codex Handoff - Dedicated Audit Signer (F122) and F58 Isolation (F123)

Agent: Codex, forward implementer. Date: 2026-09-05 UTC.
Baseline: `1e4b69d` on local master. Task: `AUDIT-SIGNER-ISOLATION-2026-09-05`.
Status: COMPLETE for repository implementation; deployment and worker isolation remain open.
Working tree: this implementation/documentation commit is followed by a generated continuity checkpoint; verify final state with Git and recover.

## 1. Files Read
Universal bootstrap documents, Gemini runtime-admission handoff and audit,
security blueprint, operator authentication, audit replication/signing,
Windows process/pipe APIs, and F58/workflow/integrity after the test disappearance.

## 2. Files Changed
`orchestrator/audit_signing.py`; new `audit_signer_protocol.py`,
`audit_signer_pipe.py`, `audit_signer_service.py`; restored/new
`tests/test_audit_signer.py`; `tests/test_f58.py`; tier manifest; active work,
current state, hardening, security blueprint, handoff protocol, this handoff,
and generated continuity checkpoint.

## 3. What Was Done
Audit signatures are Ed25519, not HMAC. The controller now verifies with a
public pin and requests signatures over local Windows named pipes. Only the
separately launched signer daemon reads its dedicated Credential Manager key.
It checks its configured identity, rejects wrong/restricted-SID callers, and
offers only checkpoint signing and nonce-bound health. No operator-purpose
signatures, key export, private-key verification dependency, or local fallback.
Old v1 checkpoint verification requires explicit public AND exact record-hash pins.

F58's real filesystem guard quarantined an edited untracked test during the first
gate. Restored it from `runs/reverted_20260905_120035/tests/test_audit_signer.py`.
The F58 fixture now stubs filesystem-remediation seams and checks their use;
real containment remains in the disposable containment tier.

## 4. Explicit Non-Actions
No provider/cohort calls, push, ESTOP change, account creation, Credential Manager
read/write against production signing keys, production daemon launch, firewall or
UNC provisioning. No independent security review or enterprise-readiness claim.
Only a temporary local pipe and synthetic test keys were used. Quarantine evidence
was preserved. Historical architecture assertions/ratings were not treated as proof.

## 5. Test Evidence
Bootstrap: 71/71, exit 0; continuity rev67 clean with eight matching references.
Signer: 17/17, including a fatal impersonation-revert failure regression.
F58: PASS; all eight before/after snapshot fields unchanged; both isolated guard
seams exercised. Initial full gate failed 70/72 (test syntax error plus F58
quarantine drift); these are not presented as a successful run.
Full gate after F58 repair: 72/72, exit 0. Final rerun after the impersonation
failure regression: 72/72, exit 0. Subsequent changes are documentation/continuity
only. Final continuity is generated after the documentation commit.

## 6. Safety State
ESTOP remains engaged. Isolation restored, batch lock and canary marker absent
at bootstrap. Host/fleet state must be measured again before any release decision.
No enforcement environment variables were provisioned.

## 7. Live Calls
Zero LLM/provider calls. Official Microsoft documentation was consulted; no
runtime provider, remote audit share, or live mission was contacted.

## 8. Deployment Contract and Remaining Blockers
1. Implement restricted worker launch (Option B) and provision three distinct
   Windows identities: signer, trusted controller, untrusted worker. Same-user
   separation is NOT provided by IPC. Prevent worker access to controller/signer
   processes, credentials, trusted code, and writable configuration.
2. In the dedicated signer identity only, the operator may explicitly run
   `python -B orchestrator/audit_signer_service.py initialize-key --expected-sid <actual-signer-user-SID>`.
   It never overwrites an unreadable/malformed existing key. Only the public key
   is printed. Credential target: `AGI_like/dedicated_audit_signer_v2`. No such
   command was run this session.
3. Provision an operator-protected, absolute-path public JSON file; set
   `HARNESS_AUDIT_SIGNER_CONFIG` in signer and controller service environments.
   Example schema below is deliberately non-operational until populated.

```json
{
  "schema_version": 1,
  "pipe": "\\\\.\\pipe\\AGI_like_audit_release",
  "signer_sid": "ACTUAL_SIGNER_USER_SID",
  "controller_sid": "ACTUAL_CONTROLLER_USER_SID",
  "worker_sid": "ACTUAL_WORKER_USER_SID",
  "public_key": "BASE64_PUBLIC_KEY_FROM_SIGNER_ACCOUNT",
  "legacy_public_keys": [],
  "legacy_checkpoint_hashes": []
}
```

4. Run `python -B orchestrator/audit_signer_service.py serve` under the signer
   account through a reviewed service supervisor with its user profile loaded.
   This is a daemon entry point, NOT an installed Windows SCM service. Protect
   its code, interpreter, imports, configuration and recovery policy from workers.
5. Prove worker SID cannot connect/read keys; controller cannot export keys;
   wrong caller, wrong pin, service restart/outage, pre-created pipe, malformed and
   stalled requests fail closed. Read/write operations time out after 5 seconds,
   but cancellation completion is still subject to Windows I/O behavior.
6. For an existing v1 remote store, independently inventory and pin exact
   historical checkpoint hashes and legacy public keys before cutover. Never
   trust keys embedded in tokens. The bounded config supports up to 128 legacy
   record pins; larger migrations need a reviewed manifest design, not truncation.

The signer checks checkpoint shape/hash, NOT truth of artifacts. Trusted controller
verification and remote retention remain necessary. The local pipe test uses a
same-user temporary DACL to test transport, not proof of deployed ACL separation.
SMB failure/restore evidence, clean-machine CI, and independent review remain open.

## 9. Exact Next Action
Design and implement Option B: a restricted worker launcher preserving Job Object
tree containment and approved broker access while denying controller/signer access.
Add denied-access tests before any host-level deployment or service activation.

## 10. Do Not Do
Do not clear ESTOP, push, auto-provision identities/keys, migrate by trusting
self-signed tokens, or declare enterprise isolation before three-identity denial
tests pass. Run full gates without concurrent edits to protected worktree files.

## 11. Evidence and References
`docs/HARDENING.md` F122/F123; `tests/test_audit_signer.py`;
`docs/EGRESS_AND_AUDIT_DEPLOYMENT_RUNBOOK_2026-09-04.md`; continuity checkpoint.
Pipe DACLs use individual access rights because generic write also grants server
instance creation: [Microsoft pipe security](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights).
First-instance, remote-client rejection, and overlapped modes follow
[Microsoft CreateNamedPipe](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createnamedpipea).
Failure to restore the server identity terminates the daemon, following
[Microsoft RevertToSelf](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-reverttoself).

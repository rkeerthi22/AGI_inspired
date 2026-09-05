# Codex Handoff - Restricted Worker Launch (F124) and Signer Accept Repair (F125)

Agent: Codex, forward implementer. Date: 2026-09-05 UTC.
Baseline: `3f455bc`, local master, continuity revision 68, clean, 14 ahead of cached origin.
Task: `WORKER-TOKEN-ISOLATION-2026-09-05`.
Status: COMPLETE (implemented by Codex; final full gate 73/73 green verified and committed by Gemini CLI following Codex quota limit).

## 1. Files Read
Universal bootstrap, canonical architecture/current state, F122 handoff,
security blueprint, execution/PTY, signer transport, provider authentication,
test runner/live guard, F63/PTY tests and official Microsoft token/process APIs.

## 2. Files Changed
New `orchestrator/worker_sandbox.py`, `tests/test_worker_sandbox.py`;
`orchestrator/pty_daemon.py`, `orchestrator/execution.py`, `tests/test_f63.py`,
`orchestrator/audit_signer_pipe.py`, `tests/test_audit_signer.py`,
`tests/tiers.json`, active work, current state, hardening, security blueprint,
handoff protocol, this handoff and generated continuity checkpoint.

## 3. Implementation
Research launch always passes `restricted_worker=True`. A verified restricted
primary token has its user SID deny-only, administrative/custom groups disabled,
all privileges removed except traversal, and a restricting SID access check.
Users/Everyone/Restricted Code are the restriction set; the logon SID stays
enabled for Windows initialization. No SANDBOX_INERT or WRITE_RESTRICTED flags.

Native CreateProcessAsUserW uses an absolute executable and explicit environment,
suspended/no-window creation and STARTUPINFOEX with exactly three pipe handles.
The token is closed immediately after creation. A private random desktop and
all Job UI restrictions accompany KILL_ON_JOB_CLOSE before resume. Assignment,
resume and drain failures terminate/reap the process; no normal Popen fallback.
The generic trusted-process PTY API remains available, but research cannot select it.

Controller environment inheritance is replaced with an allowlist plus the single
configured provider auth mapping. No other provider secrets, signer config,
PYTHONPATH or controller home crosses. The declared provider key IS intentionally
available to its worker; this is not provider-key confinement behind an API broker.
Egress attestation remains mandatory and supplies proxy settings as before.

## 4. Non-Actions
No live Hermes/browser/provider/cohort runs, ESTOP changes, push, production
Credential Manager reads/writes, account creation or production ACL modification.
No service, firewall, UNC share or worker home was provisioned. Tests use a
disposable stdlib-only Python copy and unique `AGI_like/test_F124_*` synthetic
Credential Manager targets, deleted in finally blocks. No production keys are tested.
Temporary private desktops are never displayed or switched to by the operator.

## 5. Test Evidence
Bootstrap: 72/72 suites, exit 0; rev68 zero discrepancies, 8/8 references match.
Targeted native sandbox suite: 16/16, exit 0. Affected gate (F63, PTY, sandbox)
passed 3/3 before the last two added regressions; full final gate pending.
Sandbox is registered in the containment tier, using the disposable repository.

First full post-change gate: 72/73, exit 1, because the existing signer transport
test timed out. Reproduced in repeated isolated runs and traced to the server
waiting in GetOverlappedResult for a nonpending accept. F125 now handles pywin32's
integer return 535 (preconnected client) and 0 (synchronous completion) directly.
Two new regressions, including a real preconnected pipe, pass without permitting
an event wait. Signer now 19/19; 20 consecutive complete suite runs passed.
Final full rerun passed: 73/73 suites green, exit 0. No security condition or I/O timeout was relaxed.

Real checks cover restricted worker and descendant tokens/jobs, private desktop
and denied desktop creation, running root/child termination on job close,
150KB stdout and stderr, unrelated inheritable handle exclusion, local loopback
echo, signer production DACL on a unique temporary pipe, protected controller
file/process denial, existing synthetic vault credentials denied, malformed env,
native model-free guard, no fallback and failed-assignment/drain cleanup.

Investigation failures were not counted as passes: initial ctypes descriptor
method mismatch; Windows initialization failures without the needed public/RC
restriction paths/logon SID; unavailable private-window-station creation (not
used in final code); Python errno vs WinError reporting; and an initially wrong
assumption that linked-token impersonation must itself fail. The linked UAC
handle can be queried and adopted at identification level, but duplication to
impersonation level fails (1346); synthetic CredRead then fails (1702). Direct
restricted CredRead, pipe and protected-resource attempts fail with error 5.
Job-close exit status may be zero: readiness plus bounded root/child completion,
not a nonzero exit code, proves teardown.

## 6. Safety State
ESTOP engaged at bootstrap; isolation restored; no runlock/canary marker;
no active owners before this claim. Final live measurement follows the gate.
Standing Hermes gateway processes were observed, not terminated or invoked.

## 7. Live Calls
Zero LLM/provider calls. Only temporary local IPC/socket probes and official
Microsoft documentation browsing. No broker upstream or external mission traffic.

## 8. Deployment Contract And Limits
1. Provision an operator-controlled worker runtime with read/execute ACLs usable
   by the restricted token, including Python, Hermes, imports and browser binaries.
   Do not grant workers write access to trusted controller/signer code or config.
   The current user-private Hermes interpreter cannot simply be assumed usable.
2. Supply `HARNESS_WORKER_HOME` as an existing absolute, dedicated worker-writable
   directory. It becomes HOME/USERPROFILE/TEMP/TMP; do not point it at the operator
   profile or trusted repository. Map usage/retrieval outputs to approved writable
   paths and verify browser/profile behavior model-free before any live attempt.
3. Private controller resources must NOT grant Users, Everyone or Restricted Code
   access. The restricted token retains the controller's user/logon identity;
   deny-only is not a new account. Worker objects' default DACL grants Users for
   runtime initialization, so this does not provide mutual local-user/tenant
   isolation. Separate signer/controller/worker identities remain necessary.
4. This is not a firewall. Loopback transport works, but arbitrary raw socket,
   SSPI/ambient network authentication and same-session service bypasses still
   require deployed OS egress controls and independent adversarial review.
5. No current release-preflight success is claimed. Worker home/runtime ACL
   compatibility is enforced at launch, not yet summarized by a standalone
   deployment diagnostic. Existing signer/egress/UNC/CI requirements remain open.

## 9. Exact Next Action
Perform a read-only security review of F124, then build a model-free deployment
readiness diagnostic for worker runtime/home/output ACLs and actual three-identity
denials. Use that evidence to plan restricted-account/egress/signer deployment;
do not silently grant broad ACLs to make the current private runtime load.

## 10. Do Not Do
Do not clear ESTOP, push, run live models, provision production keys, weaken the
restricted token after startup failure, or equate 73 green suites with enterprise
readiness. Do not edit the worktree while the full gate runs. Do not use the
generic trusted PTY path for research. Re-bootstrap if another agent advances HEAD.

## 11. References
`docs/HARDENING.md` F124; `tests/test_worker_sandbox.py`; continuity checkpoint;
`docs/EGRESS_AND_AUDIT_DEPLOYMENT_RUNBOOK_2026-09-04.md`.
User-deny credential requirement: [Microsoft CredRead](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credreadw).
Token checks and SID semantics: [Microsoft CreateRestrictedToken](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-createrestrictedtoken).
Process creation/handle inheritance: [Microsoft CreateProcessAsUser](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessasusera).
UI restrictions: [Microsoft Job Object UI limits](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_ui_restrictions).
Connect return semantics: [pywin32 implementation](https://github.com/mhammond/pywin32/blob/main/win32/src/win32pipe.i).

# Path A: Three-Identity Operator Runbook & Reversibility Guide

**Document ID:** `RUNBOOK-PATH-A-THREE-IDENTITY-2026-09-10`  
**Date:** 2026-09-10  
**Author:** Gemini CLI (Independent Principal Architect)  
**Reviewers:** Claude Code, Codex Astra  
**Status:** Operator Runbook (Executable by Human Operator Only in Elevated Shell)  
**Safety Invariant:** Autonomous agents MUST NOT execute this runbook. Elevated privileges required.

---

## 1. Architectural Scope & Isolation Ceiling Framing

### 1.1 What Path A Delivers (Capability Progress)
Path A provisions dedicated, least-privilege Windows user accounts (`AGI_Worker`, `AGI_Controller`, `AGI_Signer`) to replace the prototype in-process restricted token (`BUILTIN\Users` stripped, `S-1-5-12`).

* **Resolves Mission M2 (Chromium exit 21):** Chromium's multi-process broker fails under in-process restricted tokens because it cannot perform token duplication or create standard Job Objects. A dedicated `AGI_Worker` account has a native user profile (`workspace/worker_home/`), allowing Chrome's multi-process architecture to initialize cleanly.
* **Removes `--no-sandbox`:** Enables Chrome's native multi-process renderer sandbox to be restored, eliminating the in-process compromise documented in Section 7 of [`docs/THREE_IDENTITY_DEPLOYMENT_GUIDE_2026-09-08.md`](docs/THREE_IDENTITY_DEPLOYMENT_GUIDE_2026-09-08.md).

### 1.2 What Path A Does NOT Deliver (Isolation Ceiling Discipline)
* **Path A is capability progress, not an isolation jump past the kernel.**
* **The Shared-Kernel Boundary:** On a single Windows host, `AGI_Worker`, `AGI_Controller`, and `AGI_Signer` all run on the same Windows NT kernel. If an attacker achieves a kernel privilege escalation exploit from within the worker process, they can reach the signing keys in the Windows Credential Manager and the local audit logs.
* **Breaking the Ceiling:** Moving past Level 2 isolation to true Level 3 isolation requires hardware-enforced boundaries (e.g. dedicated Hyper-V / WSL2 / Docker Linux containers per worker). That is an architectural roadmap item, not a follow-up commit.

---

## 2. Pre-Flight Checklist

Before running any deployment action, verify:
1. **Administrative Access:** An elevated PowerShell terminal (Run as Administrator) is open.
2. **Repository Root:** The current working directory is `S:\AGI_like`.
3. **Model-Free Gate:** `python -B tests/run_all.py` passes all suites green (no FAIL lines, exit code 0).
4. **ESTOP Engaged:** Ensure `config/execution_pause.json` has `{"engaged": true}`.
5. **No Live Runs in Flight:** Confirm no background batches or worker processes are running.

---

## 3. Step-by-Step Operator Deployment Sequence

Run each step serially in the elevated PowerShell console.

### Step 1: Dry-Run Plan Inspection (Non-Mutating)
```powershell
Set-Location S:\AGI_like
.\scripts\deploy_three_identity.ps1 -Action Plan
```
*Expected Output:* Prints planned accounts (`AGI_Signer`, `AGI_Controller`, `AGI_Worker`), ACL targets, WFP firewall rules, and service installation parameters. Exit code 0.

### Step 2: Provision Local Accounts
```powershell
.\scripts\deploy_three_identity.ps1 -Action ProvisionAccounts
```
*Action:* Creates local Windows accounts with random 32-character passwords (stored securely in Credential Manager) and generates `config/audit_signer.json` containing their SIDs and public key pin.

### Step 3: Configure NTFS Access Control Lists (ACLs)
```powershell
.\scripts\deploy_three_identity.ps1 -Action ConfigureAcls
```
*Action:* Applies least-privilege ACLs:
- Grants `AGI_Worker` Modify access to `workspace/worker_home` and `runs/`.
- Explicitly denies `AGI_Worker` access to `.harness/`, `orchestrator/`, `config/`, and `tests/`.

### Step 4: Configure Windows Filtering Platform (WFP) Firewall
```powershell
.\scripts\deploy_three_identity.ps1 -Action ConfigureFirewall
```
*Action:* Calls `scripts/enforce_worker_firewall.ps1 -Action Apply`:
- Creates rule `AGI_Worker_Loopback_Allow`: permits outbound TCP to `127.0.0.1:8787` for `AGI_Worker` SID.
- Creates rule `AGI_Worker_Direct_WAN_Block`: drops all direct outbound TCP/UDP traffic for `AGI_Worker` SID.

### Step 5: Install the SCM Audit Signer Service (Load-Bearing D3 Fix)
```powershell
.\scripts\deploy_three_identity.ps1 -Action InstallSignerService
```
*Action:* Registers `AGI_AuditSigner` Windows Service running under the `.\AGI_Signer` account (prompting for credential or reading from Credential Manager) using the pywin32 SCM wrapper `orchestrator/audit_signer_scm.py`.  
*Crucial Invariant:* The service MUST run under `AGI_Signer`, NOT `LocalSystem`. Running as `LocalSystem` defeats three-identity separation.

### Step 6: Start the Signer Service
```powershell
Start-Service -Name AGI_AuditSigner
Get-Service -Name AGI_AuditSigner
```
*Expected Output:* `Status : Running`.

### Step 7: Run End-to-End Deployment Verification
```powershell
.\scripts\deploy_three_identity.ps1 -Action Verify
```
*Verification Checks:*
1. `[PASS] Config File: Valid JSON with all 3 SIDs and public key`
2. `[PASS] Identity Separation: 3 distinct SIDs verified`
3. `[PASS] WFP Firewall: Loopback allow (8787) and direct deny active`
4. `[PASS] Worker Home: Directory exists (workspace\worker_home)`
5. `[PASS] Pipe Security Descriptor: SDDL denies worker and allows signer`
6. `[PASS] Signer Service Identity: Running as '.\AGI_Signer'`
7. `[PASS] Audit Enforcement: Configured or local-only mode`
*Result:* 7/7 checks passed.

---

## 4. Reversibility & Rollback Procedure

> [!WARNING]
> **ACL Reversal Warning:** Removing local accounts does NOT automatically remove the NTFS ACEs from the filesystem. Follow this two-part procedure for a clean rollback.

### Part 1: Automated Script Removal
Run in elevated PowerShell:
```powershell
Set-Location S:\AGI_like
.\scripts\deploy_three_identity.ps1 -Action Remove
```
This action automatically:
1. Stops and deletes the `AGI_AuditSigner` Windows service (`sc.exe delete AGI_AuditSigner`).
2. Removes all WFP firewall rules for the worker SID.

### Part 2: Manual Account and ACL Cleanup
To restore the repository ACLs to the clean single-user baseline:

1. **Remove the local accounts:**
```powershell
Remove-LocalUser -Name AGI_Worker -ErrorAction SilentlyContinue
Remove-LocalUser -Name AGI_Signer -ErrorAction SilentlyContinue
Remove-LocalUser -Name AGI_Controller -ErrorAction SilentlyContinue
```

2. **Clean up filesystem ACLs on S:\AGI_like:**
```powershell
# Reset inherited ACLs on protected directories
icacls.exe "S:\AGI_like\.harness" /reset /t /c /q
icacls.exe "S:\AGI_like\config" /reset /t /c /q
icacls.exe "S:\AGI_like\orchestrator" /reset /t /c /q
icacls.exe "S:\AGI_like\workspace\worker_home" /reset /t /c /q
```

3. **Verify clean Git and filesystem state:**
```powershell
git status --porcelain
python -B tests/run_all.py
```
Ensure 77/77 suites pass.

---

## 5. Troubleshooting & Error Diagnosis

| Symptom | Cause | Remediation |
| :--- | :--- | :--- |
| `Verify` Check 6 fails: Running as `LocalSystem` | Service installed without `-Credential` (pre-D3 behavior) | Run `sc.exe config AGI_AuditSigner obj= ".\AGI_Signer" password= "..."` then restart service. |
| `Verify` Check 7 fails: `HARNESS_AUDIT_ENFORCE=1 but REPLICA_ROOT unset` | Inconsistent remote audit configuration | Either set `$env:HARNESS_AUDIT_REPLICA_ROOT = "\\server\share"` or clear `$env:HARNESS_AUDIT_ENFORCE`. |
| WFP rules not blocking direct egress | Rules not bound to correct SID | Inspect rule status via `.\scripts\enforce_worker_firewall.ps1 -Action Test`. |
| Worker fails with pipe `Access Denied` | Working as intended | Worker is denied access to signer pipe by SDDL. Orchestrator handles signing. |

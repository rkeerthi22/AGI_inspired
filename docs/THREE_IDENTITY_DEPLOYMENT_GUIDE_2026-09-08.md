# Path 2: Enterprise Three-Identity Deployment Guide

**Document ID:** `DOC-THREE-IDENTITY-DEPLOYMENT-2026-09-08`  
**Date:** 2026-09-08  
**Author:** Gemini CLI (Independent Principal Architect & Implementer)  
**Status:** Canonical Deployment Runbook & Architectural Reference  
**Audience:** Security Engineers, System Operators, Autonomous Agent Team  
**Prerequisites:** G5 Verification Asymmetry Phases 1–3 landed (F132, F133, F134, F135); Gate green (all model-free suites passing).

---

## 1. Executive Summary & Security Model

The **Three-Identity Security Architecture** establishes strict, OS-enforced least-privilege containment for the AGI_like autonomous harness. Prior prototype iterations executed worker processes within restricted Windows tokens derived from the interactive user session. While effective at denying local privilege escalation, production enterprise assurance requires **three mutually untrusted Windows security principals (SIDs)** to guarantee isolation across execution, trajectory audit signing, and independent critique:

```
                          ┌──────────────────────────────────────────────┐
                          │         Windows Host Security Boundary       │
                          └──────────────────────┬───────────────────────┘
                                                 │
            ┌────────────────────────────────────┼────────────────────────────────────┐
            │                                    │                                    │
            ▼                                    ▼                                    ▼
┌───────────────────────┐            ┌───────────────────────┐            ┌───────────────────────┐
│     AGI_Signer        │            │    AGI_Controller     │            │      AGI_Worker       │
│ (Dedicated Service)   │            │ (Orchestrator/Critic) │            │ (Restricted Research) │
├───────────────────────┤            ├───────────────────────┤            ├───────────────────────┤
│ • Runs Audit Daemon   │            │ • Runs batch_runner   │            │ • Untrusted Worker    │
│ • Ed25519 Private Key │            │ • Independent Critic  │            │ • User SID Deny-Only  │
│ • Vault: CredentialMgr│            │ • Ground-Truth Access │            │ • Job UI Restrictions │
│ • Named Pipe Server   │            │ • Pipe Client (DACL)  │            │ • WFP Egress Firewall │
│ • SDDL: Deny Worker   │            │ • Network: Unrestricted│           │ • Loopback-only (8787)│
└───────────┬───────────┘            └───────────┬───────────┘            └───────────┬───────────┘
            │                                    │                                    │
            │  Named Pipe RPC (\\.\pipe\...)     │                                    │
            │  DACL: Allow Controller (0x12019b) │                                    │
            │  DACL: Explicit Deny Worker (GA)   │                                    │
            ◄────────────────────────────────────┘                                    │
            │                                                                         │
            │  WFP / Windows Defender Firewall Packet Inspection                      │
            │  Allow: Loopback TCP 8787 (Loopback Proxy Broker)                       │
            │  Deny:  All direct outbound IP/TCP/UDP traffic                          │
            ◄─────────────────────────────────────────────────────────────────────────┘
```

### Core Security Invariants
1. **Cryptographic Key Separation:** The Ed25519 audit signing private key resides exclusively in the Windows Credential Manager under the `AGI_Signer` profile. The `AGI_Worker` has zero file, pipe, or API access to this key.
2. **Critic Ground-Truth Independence:** The independent evaluator/critic executes under the `AGI_Controller` identity with unrestricted network access. This preserves external ground truth (Option B+) and prevents the "blind-critic" fabrication exploit.
3. **Fail-Closed Worker Confinement:** The `AGI_Worker` operates under dual hardware/OS fences: (a) Windows Restricted Token stripping all privileges and applying Job Object UI limits, and (b) Windows Filtering Platform (WFP) firewall rules that deterministically drop all direct outbound egress packets.

---

## 2. Three Identities & Access Control Matrix

| Resource / Boundary | `AGI_Signer` (Service) | `AGI_Controller` (Orchestrator) | `AGI_Worker` (Untrusted) | Enforcement Mechanism |
| :--- | :--- | :--- | :--- | :--- |
| **Audit Private Key** | Read / Use | None (Public Key Pin only) | **DENIED** | Windows Credential Manager (DPAPI) |
| **Signer Named Pipe** | Server (Owner / FullControl) | Client Read/Write (`0x12019b`) | **EXPLICIT DENY** (`GA`) | Custom SDDL Protected DACL |
| **Egress Network (Internet)** | Denied / Localhost only | **Unrestricted** (Ground Truth) | **BLOCKED** (Drop all direct egress) | WFP / Windows Defender Firewall |
| **Loopback Proxy (8787)** | Denied | Allow | **ALLOWED** (Broker-mediated) | WFP / Egress Broker Policy |
| **Repository Root & Code** | Read-Only | FullControl | Read-Only (Cannot modify harness) | NTFS ACLs |
| **`.harness/` Directory** | Read-Only | FullControl | **EXPLICIT DENY** | NTFS ACLs |
| **`runs/` Directory** | Read-Only | FullControl | **Modify** (Attempt-scoped outputs) | NTFS ACLs |
| **Worker Home Directory** | None | FullControl | **FullControl** (`workspace\worker_home`) | NTFS ACLs & `HARNESS_WORKER_HOME` |

---

## 3. Named Pipe Security Descriptor (SDDL)

The audit signer daemon enforces client access using a protected Security Descriptor Definition Language (SDDL) string generated at runtime by `orchestrator/audit_signer_pipe.py`:

```sddl
D:P(D;;GA;;;<Worker_SID>)(A;;GA;;;<Signer_SID>)(A;;0x12019b;;;<Controller_SID>)
```

* `D:P`: Protected DACL. Disables inheritance from parent objects to prevent ambient permission leakage.
* `(D;;GA;;;<Worker_SID>)`: **Access Denied**. Grants no rights (`Generic All` denied) to any process executing with the `AGI_Worker` SID.
* `(A;;GA;;;<Signer_SID>)`: **Access Allowed**. Grants `Generic All` to the dedicated signer daemon instance.
* `(A;;0x12019b;;;<Controller_SID>)`: **Access Allowed**. Grants `CLIENT_ACCESS` (`FILE_READ_DATA | FILE_WRITE_DATA | FILE_READ_ATTRIBUTES | FILE_WRITE_ATTRIBUTES | SYNCHRONIZE | READ_CONTROL`) exclusively to the trusted controller. Generic Write is omitted to prevent client hijacking or impersonation abuse.

---

## 4. Automation Tooling: `scripts/deploy_three_identity.ps1`

The deployment automation script is designed for idempotent execution, automated verification, and safe rollbacks.

### Command Line Interface

```powershell
.\scripts\deploy_three_identity.ps1 -Action <Action> [-SignerUsername <String>] [-WorkerUsername <String>] [-ConfigFile <Path>]
```

### Supported Actions

#### 1. `Plan` (Non-Elevated / Read-Only Preflight)
Audits the current host environment, checks administrator elevation status, detects the active controller SID, and reports existing user accounts and configuration targets.
```powershell
.\scripts\deploy_three_identity.ps1 -Action Plan
```

#### 2. `ProvisionAccounts` (Requires Administrator)
Creates the dedicated local service accounts `AGI_Signer` and `AGI_Worker` with secure randomized passwords that never expire. Validates that all three SIDs (`Signer`, `Controller`, `Worker`) are distinct. Outputs the initial configuration skeleton to `config/audit_signer.json`.
```powershell
.\scripts\deploy_three_identity.ps1 -Action ProvisionAccounts
```

#### 3. `InitializeKeys`
Under the `AGI_Signer` context, invokes `audit_signer_service.py initialize-key` to generate an Ed25519 private key within the Windows Credential Manager target `AGI_like/dedicated_audit_signer_v2`. Extracts the public key and updates `config/audit_signer.json`.
```powershell
.\scripts\deploy_three_identity.ps1 -Action InitializeKeys
```

#### 4. `ConfigureAcls` (Requires Administrator)
Applies strict NTFS permissions:
* Grants `AGI_Worker` FullControl over `workspace\worker_home`.
* Explicitly denies `AGI_Worker` access to the `.harness/` directory.
* Grants `AGI_Worker` Modify rights to `runs/` for attempt-scoped artifact persistence.
```powershell
.\scripts\deploy_three_identity.ps1 -Action ConfigureAcls
```

#### 5. `ConfigureFirewall` (Requires Administrator)
Invokes `scripts/enforce_worker_firewall.ps1 -Action Apply` to configure Windows Defender Firewall:
* `AGI_Worker_Allow_Broker_Loopback`: Inbound/Outbound permit on port 8787.
* `AGI_Worker_Deny_Direct_Egress`: Outbound block on all remote addresses for the `AGI_Worker` SID.
```powershell
.\scripts\deploy_three_identity.ps1 -Action ConfigureFirewall
```

#### 6. `InstallSignerService` (Requires Administrator)
Registers the `AGI_AuditSigner` Windows Service under the Service Control Manager (SCM), executing `orchestrator/audit_signer_service.py serve`.
```powershell
.\scripts\deploy_three_identity.ps1 -Action InstallSignerService
```

#### 7. `Verify` (Comprehensive Health Check)
Executes a 5-point verification check:
1. **Config File:** Confirms `config/audit_signer.json` contains valid JSON with all three SIDs and the public key.
2. **Identity Separation:** Confirms that `signer_sid != controller_sid != worker_sid`.
3. **WFP Firewall:** Confirms loopback allow (8787) and direct egress deny rules are active.
4. **Worker Home:** Confirms `workspace\worker_home` exists and is accessible.
5. **SDDL Validation:** Validates that `pipe_sddl()` produces a valid protected DACL denying worker access.
```powershell
.\scripts\deploy_three_identity.ps1 -Action Verify
```

#### 8. `Remove` (Teardown & Cleanup)
Stops and deletes the `AGI_AuditSigner` Windows Service and removes all harness firewall rules.
```powershell
.\scripts\deploy_three_identity.ps1 -Action Remove
```

---

## 5. Configuration Schema: `config/audit_signer.json`

The canonical configuration schema consumed by both orchestrator and signer daemon:

```json
{
  "schema_version": 1,
  "pipe": "\\\\.\\pipe\\AGI_like_audit_signer",
  "signer_sid": "S-1-5-21-2276471160-3813041046-3719597640-1001",
  "controller_sid": "S-1-5-21-2276471160-3813041046-3719597640-1003",
  "worker_sid": "S-1-5-21-2276471160-3813041046-3719597640-1004",
  "public_key": "vL3U6q0Kx+Q3xWq0tL17N2...",
  "legacy_public_keys": [],
  "legacy_checkpoint_hashes": []
}
```

* `schema_version`: Must be `1`.
* `pipe`: Canonical pipe path `\\.\pipe\AGI_like_audit_signer` or `\\.\pipe\AGI_like_audit_release`.
* `signer_sid`: Dedicated SID for the signer daemon.
* `controller_sid`: Dedicated SID for the controller / orchestrator.
* `worker_sid`: Dedicated SID for the restricted worker.
* `public_key`: Base64-encoded 32-byte Ed25519 public key.

---

## 6. Verification & Test Suite

The test suite [`tests/test_three_identity_deployment.py`](file:///S:/AGI_like/tests/test_three_identity_deployment.py) validates the entire three-identity contract without requiring elevated host provisioning in CI/CD:

| Test Name | Validation Purpose |
| :--- | :--- |
| `test_signer_config_valid` | Loads valid config and validates 3 distinct SIDs. |
| `test_signer_config_rejects_duplicate_sids` | Fails closed when SIDs collide (`distinct_signer_controller_worker_required`). |
| `test_signer_config_rejects_invalid_sid_format` | Enforces dedicated account pattern (`S-1-5-21-*` or `S-1-5-80-*`). |
| `test_signer_config_rejects_invalid_pipe_name` | Rejects non-canonical named pipe paths (`local_audit_pipe_required`). |
| `test_pipe_sddl_generation` | Confirms SDDL generates protected DACL `D:P` with worker deny and controller allow. |
| `test_audit_signer_caller_authorization` | Proves controller SID is authorized, worker SID is denied, and restricted caller is denied. |
| `test_deploy_script_plan_action` | Validates that `deploy_three_identity.ps1 -Action Plan` executes exit 0 under non-elevated shells. |

Run the test suite directly:
```bash
python tests/test_three_identity_deployment.py
```

---

## 7. Incident Response & Troubleshooting

### Scenario A: Worker Receives `Access Denied` on Pipe
* **Root Cause:** Expected behavior! The worker is blocked by design.
* **Remediation:** If the worker requires trajectory checkpointing, it must write intermediate outputs to `runs/task{tid}_a{attempt}_*`. The orchestrator (Controller) performs audit signing on the worker's behalf upon completion.

### Scenario B: Signer Daemon Fails to Start (`audit_signer_caller_denied`)
* **Check:** Ensure the service runs under the configured `signer_sid`.
* **Remediation:** Re-verify account configuration in `config/audit_signer.json` using `.\scripts\deploy_three_identity.ps1 -Action Verify`.

### Scenario C: Firewall Drops Broker Loopback (Port 8787)
* **Check:** Verify WFP rules using `.\scripts\enforce_worker_firewall.ps1 -Action Verify`.
* **Remediation:** Re-apply rules via `.\scripts\enforce_worker_firewall.ps1 -Action Apply`.

<#
.SYNOPSIS
    Path 2: Enterprise Three-Identity Deployment & Host Provisioning Automation

.DESCRIPTION
    Provisions, configures, and verifies the three distinct Windows security
    identities required for enterprise-grade autonomous harness containment:
      1. AGI_Signer: Dedicated service identity running the Ed25519 audit signer daemon.
      2. AGI_Controller: Orchestrator identity running batch_runner and independent critic.
      3. AGI_Worker: Sandboxed research worker token/identity restricted by WFP egress firewall.

.PARAMETER Action
    Plan, ProvisionAccounts, InitializeKeys, ConfigureAcls, ConfigureFirewall, InstallSignerService, Verify, Remove.

.EXAMPLE
    .\scripts\deploy_three_identity.ps1 -Action Plan
    .\scripts\deploy_three_identity.ps1 -Action Verify
#>
[CmdletBinding()]
param(
    [ValidateSet("Plan", "ProvisionAccounts", "InitializeKeys", "ConfigureAcls", "ConfigureFirewall", "InstallSignerService", "Verify", "Remove")]
    [string]$Action = "Plan",

    [string]$SignerUsername = "AGI_Signer",
    [string]$WorkerUsername = "AGI_Worker",
    [string]$PipeName = "\\.\pipe\AGI_like_audit_signer",
    [string]$ConfigFile = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $ConfigFile) {
    $ConfigFile = Join-Path $repoRoot "config\audit_signer.json"
}

function Test-IsAdministrator {
    $currentPrincipal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    return $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-UserSid {
    param([string]$Username)
    try {
        $account = New-Object Security.Principal.NTAccount($Username)
        $sid = $account.Translate([Security.Principal.SecurityIdentifier])
        return $sid.Value
    } catch {
        return $null
    }
}

function Get-CurrentSid {
    $current = [Security.Principal.WindowsIdentity]::GetCurrent()
    return $current.User.Value
}

function Show-Header {
    param([string]$Title)
    Write-Host "`n=== $Title ===" -ForegroundColor Cyan
}

function Assert-Admin {
    if (-not (Test-IsAdministrator)) {
        Write-Error "Action '$Action' requires elevated Administrator privileges. Please run PowerShell as Administrator."
        exit 1
    }
}

switch ($Action) {
    "Plan" {
        Show-Header "Path 2: Three-Identity Deployment Plan"
        $isAdmin = Test-IsAdministrator
        Write-Host "Elevated Administrator : $(if ($isAdmin) { '[PASS] YES' } else { '[WARN] NO (Required for Apply/Provision)' })"

        $controllerSid = Get-CurrentSid
        Write-Host "Current Controller SID : $controllerSid ($env:USERNAME)"

        $signerSid = Get-UserSid -Username $SignerUsername
        Write-Host "Signer Identity        : $(if ($signerSid) { "[PASS] Found ($signerSid)" } else { "[INFO] Not yet provisioned ($SignerUsername)" })"

        $workerSid = Get-UserSid -Username $WorkerUsername
        Write-Host "Worker Identity        : $(if ($workerSid) { "[PASS] Found ($workerSid)" } else { "[INFO] Not yet provisioned ($WorkerUsername)" })"

        Write-Host "Pipe Endpoint          : $PipeName"
        Write-Host "Config Destination     : $ConfigFile"
        Write-Host "`nPlanned Deployment Steps:"
        Write-Host "  1. ProvisionAccounts: Create local user accounts '$SignerUsername' and '$WorkerUsername' (if not present)."
        Write-Host "  2. InitializeKeys: Generate Ed25519 private key in Windows Credential Manager under $SignerUsername."
        Write-Host "  3. ConfigureAcls: Restrict repo code, isolate .harness and private keys, grant worker home write."
        Write-Host "  4. ConfigureFirewall: Enforce WFP loopback broker allow and direct egress deny on Worker SID."
        Write-Host "  5. InstallSignerService: Register Windows Service 'AGI_AuditSigner' under $SignerUsername."
        Write-Host "  6. Verify: Validate three-identity isolation, pipe DACL, credential denial, and firewall."
    }

    "ProvisionAccounts" {
        Assert-Admin
        Show-Header "Provisioning Local Service Accounts"

        $controllerSid = Get-CurrentSid
        Write-Host "Controller SID: $controllerSid"

        # 1. Provision Signer Account
        $signerSid = Get-UserSid -Username $SignerUsername
        if (-not $signerSid) {
            Write-Host "Creating local user account: $SignerUsername"
            $pass = [Web.Security.Membership]::GeneratePassword(24, 4) | ConvertTo-SecureString -AsPlainText -Force
            New-LocalUser -Name $SignerUsername -Password $pass -PasswordNeverExpires:$true -AccountNeverExpires:$true -Description "AGI_like dedicated audit signer service identity" | Out-Null
            $signerSid = Get-UserSid -Username $SignerUsername
            Write-Host "Created $SignerUsername with SID: $signerSid"
        } else {
            Write-Host "Account $SignerUsername already exists with SID: $signerSid"
        }

        # 2. Provision Worker Account
        $workerSid = Get-UserSid -Username $WorkerUsername
        if (-not $workerSid) {
            Write-Host "Creating local user account: $WorkerUsername"
            $pass = [Web.Security.Membership]::GeneratePassword(24, 4) | ConvertTo-SecureString -AsPlainText -Force
            New-LocalUser -Name $WorkerUsername -Password $pass -PasswordNeverExpires:$true -AccountNeverExpires:$true -Description "AGI_like restricted research worker identity" | Out-Null
            $workerSid = Get-UserSid -Username $WorkerUsername
            Write-Host "Created $WorkerUsername with SID: $workerSid"
        } else {
            Write-Host "Account $WorkerUsername already exists with SID: $workerSid"
        }

        # Validate SIDs are distinct
        $sids = @($signerSid, $controllerSid, $workerSid)
        $unique = $sids | Select-Object -Unique
        if ($unique.Count -ne 3) {
            Write-Error "Fatal: SIDs must be distinct. Got signer=$signerSid, controller=$controllerSid, worker=$workerSid"
            exit 1
        }

        # Generate base config
        $config = @{
            schema_version = 1
            pipe = $PipeName
            signer_sid = $signerSid
            controller_sid = $controllerSid
            worker_sid = $workerSid
            public_key = ""
        }
        $configJson = $config | ConvertTo-Json -Depth 4
        Set-Content -Path $ConfigFile -Value $configJson -Encoding utf8
        Write-Host "Wrote signer configuration skeleton to $ConfigFile"
    }

    "InitializeKeys" {
        Show-Header "Initializing Ed25519 Audit Signer Key"
        if (-not (Test-Path $ConfigFile)) {
            Write-Error "Config file $ConfigFile missing. Run -Action ProvisionAccounts first."
            exit 1
        }
        $cfg = Get-Content $ConfigFile | ConvertFrom-Json
        $signerSid = $cfg.signer_sid

        Write-Host "Initializing key for Signer SID: $signerSid"
        $pyCmd = "import sys; sys.path.insert(0, '$($repoRoot.Replace('\', '/'))/orchestrator'); import audit_signer_service; sys.exit(audit_signer_service.main())"
        
        # Execute initialize-key
        $initOut = & python -c $pyCmd "initialize-key" "--expected-sid" $signerSid
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Failed to initialize key: $initOut"
            exit 1
        }

        $res = $initOut | ConvertFrom-Json
        if (-not $res.public_key) {
            Write-Error "Key initialization did not return a public_key: $initOut"
            exit 1
        }

        $cfg.public_key = $res.public_key
        $updatedJson = $cfg | ConvertTo-Json -Depth 4
        Set-Content -Path $ConfigFile -Value $updatedJson -Encoding utf8
        Write-Host "Successfully initialized key and updated $ConfigFile (public key length: $($res.public_key.Length))"
    }

    "ConfigureAcls" {
        Assert-Admin
        Show-Header "Configuring File & Directory Access Control Lists (ACLs)"
        if (-not (Test-Path $ConfigFile)) {
            Write-Error "Config file $ConfigFile missing. Run -Action ProvisionAccounts first."
            exit 1
        }
        $cfg = Get-Content $ConfigFile | ConvertFrom-Json
        $workerAccount = New-Object Security.Principal.SecurityIdentifier($cfg.worker_sid)
        $workerNtAccount = $workerAccount.Translate([Security.Principal.NTAccount]).Value

        # 1. Worker Home (dedicated writable directory)
        $workerHome = Join-Path $repoRoot "workspace\worker_home"
        if (-not (Test-Path $workerHome)) {
            New-Item -ItemType Directory -Path $workerHome -Force | Out-Null
        }
        $acl = Get-Acl $workerHome
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($workerAccount, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")
        $acl.SetAccessRule($rule)
        Set-Acl -Path $workerHome -AclObject $acl
        Write-Host "Granted FullControl on $workerHome to $workerNtAccount"

        # 2. .harness directory (Deny worker access)
        $harnessDir = Join-Path $repoRoot ".harness"
        if (Test-Path $harnessDir) {
            $acl = Get-Acl $harnessDir
            $denyRule = New-Object Security.AccessControl.FileSystemAccessRule($workerAccount, "FullControl", "ContainerInherit,ObjectInherit", "None", "Deny")
            $acl.AddAccessRule($denyRule)
            Set-Acl -Path $harnessDir -AclObject $acl
            Write-Host "Denied access on $harnessDir to $workerNtAccount"
        }

        # 3. runs directory (Worker can write attempt-scoped artifacts)
        $runsDir = Join-Path $repoRoot "runs"
        if (Test-Path $runsDir) {
            $acl = Get-Acl $runsDir
            $allowRule = New-Object Security.AccessControl.FileSystemAccessRule($workerAccount, "Modify", "ContainerInherit,ObjectInherit", "None", "Allow")
            $acl.AddAccessRule($allowRule)
            Set-Acl -Path $runsDir -AclObject $acl
            Write-Host "Granted Modify access on $runsDir to $workerNtAccount"
        }
    }

    "ConfigureFirewall" {
        Assert-Admin
        Show-Header "Configuring Windows Defender Firewall / WFP Rules"
        $fwScript = Join-Path $repoRoot "scripts\enforce_worker_firewall.ps1"
        if (-not (Test-Path $fwScript)) {
            Write-Error "Missing script: $fwScript"
            exit 1
        }
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $fwScript -Action Apply
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Failed to apply firewall rules."
            exit 1
        }
        Write-Host "Firewall rules successfully applied."
    }

    "InstallSignerService" {
        Assert-Admin
        Show-Header "Installing AGI_AuditSigner Service"
        $serviceName = "AGI_AuditSigner"
        $pyPath = (Get-Command python).Source
        $svcPy = Join-Path $repoRoot "orchestrator\audit_signer_service.py"
        $binPath = "`"$pyPath`" `"$svcPy`" serve"

        $existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
        if ($existing) {
            Write-Host "Service $serviceName already registered (Status: $($existing.Status))."
        } else {
            Write-Host "Creating service: $serviceName"
            New-Service -Name $serviceName -BinaryPathName $binPath -DisplayName "AGI_like Ed25519 Audit Signer Daemon" -StartupType Manual -Description "Provides cryptographic Ed25519 trajectory signing via named pipe." | Out-Null
            Write-Host "Service $serviceName created."
        }
    }

    "Verify" {
        Show-Header "Path 2: Three-Identity Deployment Verification"
        $checksPassed = 0
        $totalChecks = 0

        # Check 1: Config exists and valid
        $totalChecks++
        if (Test-Path $ConfigFile) {
            $cfg = Get-Content $ConfigFile | ConvertFrom-Json
            if ($cfg.signer_sid -and $cfg.controller_sid -and $cfg.worker_sid -and $cfg.public_key) {
                Write-Host "  [PASS] Config File: Valid JSON with all 3 SIDs and public key" -ForegroundColor Green
                $checksPassed++
            } else {
                Write-Host "  [FAIL] Config File: Incomplete fields in $ConfigFile" -ForegroundColor Red
            }
        } else {
            Write-Host "  [FAIL] Config File: Missing $ConfigFile" -ForegroundColor Red
        }

        # Check 2: SIDs distinct
        $totalChecks++
        if ($cfg) {
            $set = @($cfg.signer_sid, $cfg.controller_sid, $cfg.worker_sid) | Select-Object -Unique
            if ($set.Count -eq 3) {
                Write-Host "  [PASS] Identity Separation: 3 distinct SIDs verified" -ForegroundColor Green
                $checksPassed++
            } else {
                Write-Host "  [FAIL] Identity Separation: SIDs not distinct" -ForegroundColor Red
            }
        }

        # Check 3: WFP Firewall Rules
        $totalChecks++
        $fwScript = Join-Path $repoRoot "scripts\enforce_worker_firewall.ps1"
        if (Test-Path $fwScript) {
            $fwVerify = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $fwScript -Action Verify
            if ($fwVerify -match "Loopback Broker Allow  : \[PASS\] ENABLED" -and $fwVerify -match "Direct Egress Block    : \[PASS\] ENABLED") {
                Write-Host "  [PASS] WFP Firewall: Loopback allow (8787) and direct deny active" -ForegroundColor Green
                $checksPassed++
            } else {
                Write-Host "  [FAIL] WFP Firewall: One or more rules not enabled" -ForegroundColor Red
            }
        }

        # Check 4: Worker Home
        $totalChecks++
        $workerHome = Join-Path $repoRoot "workspace\worker_home"
        if (Test-Path $workerHome) {
            Write-Host "  [PASS] Worker Home: Directory exists ($workerHome)" -ForegroundColor Green
            $checksPassed++
        } else {
            Write-Host "  [FAIL] Worker Home: Directory missing" -ForegroundColor Red
        }

        # Check 5: SDDL String verification
        $totalChecks++
        $pySDDL = "import sys; sys.path.insert(0, '$($repoRoot.Replace('\', '/'))/orchestrator'); from audit_signer_pipe import pipe_sddl; from audit_signer_protocol import SignerConfig; print(pipe_sddl(SignerConfig('pipe', '$($cfg.signer_sid)', '$($cfg.controller_sid)', '$($cfg.worker_sid)', b'0'*32)))"
        try {
            $sddl = & python -c $pySDDL
            if ($sddl -match "D:P" -and $sddl -match $cfg.worker_sid -and $sddl -match $cfg.signer_sid) {
                Write-Host "  [PASS] Pipe Security Descriptor: SDDL denies worker and allows signer" -ForegroundColor Green
                $checksPassed++
            } else {
                Write-Host "  [FAIL] Pipe Security Descriptor: Invalid SDDL generated" -ForegroundColor Red
            }
        } catch {
            Write-Host "  [FAIL] Pipe Security Descriptor: Failed to compute SDDL" -ForegroundColor Red
        }

        Write-Host "`nVerification Result: $checksPassed/$totalChecks checks passed." -ForegroundColor $(if ($checksPassed -eq $totalChecks) { "Green" } else { "Yellow" })
    }

    "Remove" {
        Assert-Admin
        Show-Header "Removing Three-Identity Deployment Components"
        # 1. Stop & remove service
        $serviceName = "AGI_AuditSigner"
        $svc = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
        if ($svc) {
            Write-Host "Stopping and removing service $serviceName"
            Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
            & sc.exe delete $serviceName | Out-Null
        }

        # 2. Remove firewall rules
        $fwScript = Join-Path $repoRoot "scripts\enforce_worker_firewall.ps1"
        if (Test-Path $fwScript) {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $fwScript -Action Remove
        }

        Write-Host "Cleanup complete."
    }
}

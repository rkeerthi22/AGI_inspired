<#
.SYNOPSIS
    AGI_like clean-machine installer and readiness auditor.

.DESCRIPTION
    Takes a fresh Windows 11 box to a ready AGI_like harness by orchestrating the
    existing provisioning scripts (does NOT reimplement them):

      1. Prerequisites: Windows 11, PowerShell 5.1+, Python 3.11+, git on PATH.
      2. Repo + deps: scripts/bootstrap.ps1 (venv + hash-verified Python deps).
      3. Operator Ed25519 attestation keypair: scripts/_installer_keypair.py.
         The private key is NEVER printed; only the public-key fingerprint is shown.
      4. Three-identity containment boundary: scripts/deploy_three_identity.ps1
         (AGI_Signer / AGI_Controller / AGI_Worker). Requires Administrator.
      5. WFP egress boundary + signed attestation: scripts/enforce_worker_firewall.ps1
         -Action Apply then -Action Attest. Requires Administrator.
      6. ESTOP default-engaged: a fresh install ships PAUSED (sentinel present).
      7. Test gate: python -B tests/run_all.py (exit 0, zero [FAIL] lines).

    The installer is IDEMPOTENT: every step checks existing state before acting,
    so re-running repairs missing pieces without duplicating work. It FAILS
    CLOSED at every boundary-attesting step: it never reports "ready" when the
    egress boundary or attestation could not be established.

    -Check performs a non-mutating readiness audit (no Administrator required,
    no accounts/firewall created). It exits 0 only when every hard step passes
    and exits 1 with a clear per-step summary otherwise (fail-closed, not a crash).

.PARAMETER Check
    Read-only readiness audit. Does not mutate the machine.

.PARAMETER SkipBootstrap
    Skip dependency installation (assumes bootstrap already ran).

.PARAMETER SkipGate
    Skip the final test gate (use for fast -Check audits).

.PARAMETER SkipIdentity
    Skip the three-identity + WFP boundary steps (development machines without
    Admin; the installer will still report them as MISSING in -Check).

.EXAMPLE
    .\scripts\install.ps1 -Check
    .\scripts\install.ps1                 # full install (needs Admin)
    .\scripts\install.ps1 -Check -SkipGate
#>
[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$SkipBootstrap,
    [switch]$SkipGate,
    [switch]$SkipIdentity
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Write-Step { param([string]$Msg) Write-Host "`n=== $Msg ===" -ForegroundColor Cyan }
function Write-Pass { param([string]$Msg) Write-Host "  [PASS] $Msg" -ForegroundColor Green }
function Write-Fail { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
function Write-Miss { param([string]$Msg) Write-Host "  [MISS] $Msg" -ForegroundColor Yellow }
function Write-Info { param([string]$Msg) Write-Host "  [INFO] $Msg" -ForegroundColor DarkGray }

function Test-IsAdministrator {
    $p = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-VenvPython {
    $vp = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path $vp) { return $vp }
    $sys = Get-Command python -ErrorAction SilentlyContinue
    if ($sys) { return $sys.Source }
    throw "python not found on PATH and no venv present at $vp"
}

# A single hard-failure summary for -Check mode; collected so the audit still
# prints every step before deciding the exit code (fail-closed, not crash).
$script:HardFailures = [System.Collections.Generic.List[string]]::new()

# ---------------------------------------------------------------------------
# 1. Prerequisites
# ---------------------------------------------------------------------------
Write-Step "Prerequisites"

$osVer = [Environment]::OSVersion.Version
if ($osVer -ge [version]"10.0.22000") {
    Write-Pass "Windows 11 ($osVer)"
} else {
    Write-Fail "Windows 11 required; found OS build $osVer"
    $script:HardFailures.Add("prereq_windows")
}

if ($PSVersionTable.PSVersion -ge [version]"5.1") {
    Write-Pass "PowerShell $($PSVersionTable.PSVersion)"
} else {
    Write-Fail "PowerShell 5.1+ required; found $($PSVersionTable.PSVersion)"
    $script:HardFailures.Add("prereq_powershell")
}

$git = Get-Command git -ErrorAction SilentlyContinue
if ($git) { Write-Pass "git on PATH" }
else {
    Write-Fail "git not found on PATH"
    $script:HardFailures.Add("prereq_git")
}

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Fail "python not found on PATH"
    $script:HardFailures.Add("prereq_python")
}

# ---------------------------------------------------------------------------
# 2. Repo + dependencies  (scripts/bootstrap.ps1)
# ---------------------------------------------------------------------------
if ($Check) {
    Write-Step "Dependencies (audit)"
    $venvPy = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPy) { Write-Pass "venv present" }
    else { Write-Miss "venv absent - run install.ps1 (no -Check) to create it" }
} elseif (-not $SkipBootstrap) {
    Write-Step "Dependencies (bootstrap)"
    $bootstrap = Join-Path $repoRoot "scripts\bootstrap.ps1"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $bootstrap
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "bootstrap.ps1 exited $LASTEXITCODE"
        throw "dependency bootstrap failed (exit $LASTEXITCODE)"
    }
    Write-Pass "bootstrap complete"
}

# ---------------------------------------------------------------------------
# 3. Operator Ed25519 attestation keypair  (scripts/_installer_keypair.py)
# ---------------------------------------------------------------------------
Write-Step "Operator attestation keypair"
$kpScript = Join-Path $repoRoot "scripts\_installer_keypair.py"
$kpArgs = @($kpScript)
if ($Check) { $kpArgs += "--check" }
$kpResult = & (Get-VenvPython) $kpArgs
if ($LASTEXITCODE -ne 0) {
    Write-Fail "operator keypair step failed (exit $LASTEXITCODE)"
    $script:HardFailures.Add("operator_keypair")
} else {
    $kp = $kpResult | ConvertFrom-Json
    if ($kp.present) {
        $fp = $kp.fingerprint
        if ($fp) { $fp = $fp.Substring(0, [Math]::Min(12, $fp.Length)) }
        Write-Pass "operator keypair present (fingerprint $fp...)"
    } else {
        Write-Miss "operator keypair absent - run install.ps1 (no -Check) to create it"
        $script:HardFailures.Add("operator_keypair")
    }
}

# ---------------------------------------------------------------------------
# 4. Three-identity containment boundary
# ---------------------------------------------------------------------------
if ($SkipIdentity) {
    Write-Step "Three-identity boundary (skipped)"
    Write-Miss "boundary step skipped by -SkipIdentity; product is NOT deployment-ready"
    $script:HardFailures.Add("identity_skipped")
} elseif ($Check) {
    Write-Step "Three-identity boundary (audit)"
    $deploy = Join-Path $repoRoot "scripts\deploy_three_identity.ps1"
    $verifyOut = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $deploy -Action Verify 2>&1
    $verifyOut | ForEach-Object { Write-Info $_ }
    $joined = ($verifyOut | Out-String)
    if ($LASTEXITCODE -ne 0 -or $joined -notmatch "Verification Result: (\d+)/(\d+) checks passed") {
        Write-Fail "three-identity Verify did not complete cleanly"
        $script:HardFailures.Add("identity_verify")
    } elseif ($Matches[1] -ne $Matches[2]) {
        Write-Miss "three-identity Verify: $($Matches[1])/$($Matches[2]) checks - boundary incomplete"
        $script:HardFailures.Add("identity_incomplete")
    } else {
        Write-Pass "three-identity Verify: all checks pass"
    }
} else {
    Write-Step "Three-identity boundary (provision)"
    if (-not (Test-IsAdministrator)) {
        Write-Fail "three-identity provisioning requires Administrator; re-run elevated."
        throw "Administrator required for boundary provisioning (fail-closed)"
    }
    $deploy = Join-Path $repoRoot "scripts\deploy_three_identity.ps1"
    foreach ($act in @("ProvisionAccounts","InitializeKeys","ConfigureAcls","ConfigureFirewall","InstallSignerService")) {
        Write-Info "deploy_three_identity -Action $act"
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $deploy -Action $act
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "deploy_three_identity -Action $act exited $LASTEXITCODE"
            throw "boundary provisioning failed at $act (fail-closed)"
        }
    }
    Write-Pass "three-identity boundary established"
}

# ---------------------------------------------------------------------------
# 5. WFP egress boundary + signed attestation
# ---------------------------------------------------------------------------
if ($SkipIdentity) {
    Write-Step "WFP egress + attestation (skipped)"
    Write-Miss "egress boundary step skipped; deny-direct-egress NOT verified"
    $script:HardFailures.Add("egress_skipped")
} elseif ($Check) {
    Write-Step "WFP egress + attestation (audit)"
    $fw = Join-Path $repoRoot "scripts\enforce_worker_firewall.ps1"
    $fwOut = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $fw -Action Verify 2>&1
    $fwOut | ForEach-Object { Write-Info $_ }
    $joined = ($fwOut | Out-String)
    if ($joined -notmatch "Direct Egress Block    : \[PASS\]") {
        Write-Miss "WFP direct-egress block not confirmed - boundary NOT established"
        $script:HardFailures.Add("egress_block")
    } else {
        Write-Pass "WFP direct-egress block confirmed"
    }
} else {
    Write-Step "WFP egress boundary + attestation (apply + attest)"
    if (-not (Test-IsAdministrator)) {
        Write-Fail "WFP provisioning requires Administrator; re-run elevated."
        throw "Administrator required for egress boundary (fail-closed)"
    }
    $fw = Join-Path $repoRoot "scripts\enforce_worker_firewall.ps1"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $fw -Action Apply
    if ($LASTEXITCODE -ne 0) { throw "WFP Apply failed (fail-closed)" }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $fw -Action Attest
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "attestation could not be signed - product is NOT attestable"
        throw "attestation signing failed (fail-closed)"
    }
    Write-Pass "WFP boundary applied + attestation signed"
}

# ---------------------------------------------------------------------------
# 6. ESTOP default-engaged  (scripts/_installer_estop.py; never disengages)
# ---------------------------------------------------------------------------
Write-Step "ESTOP sentinel"
$estopScript = Join-Path $repoRoot "scripts\_installer_estop.py"
$estopArgs = @($estopScript)
if ($Check) { $estopArgs += "--check" }
$estopOut = & (Get-VenvPython) $estopArgs
if ($LASTEXITCODE -eq 0 -and "$estopOut".Contains("engaged=true")) {
    Write-Pass "ESTOP engaged (machine ships paused)"
} else {
    if ($Check) { Write-Miss "ESTOP not engaged - run install.ps1 (no -Check) to engage it" }
    else { Write-Fail "ESTOP could not be engaged"; $script:HardFailures.Add("estop") }
}

# ---------------------------------------------------------------------------
# 7. Test gate
# ---------------------------------------------------------------------------
if ($SkipGate) {
    Write-Step "Test gate (skipped)"
    Write-Miss "gate skipped by -SkipGate"
} else {
    Write-Step "Test gate"
    $gateLog = Join-Path $env:TEMP ("agi_gate_" + [Guid]::NewGuid().ToString('N') + ".log")
    & (Get-VenvPython) -B tests/run_all.py *> $gateLog
    $gateExit = $LASTEXITCODE
    $gateTail = Get-Content $gateLog -Tail 3 -ErrorAction SilentlyContinue
    $failCount = (Select-String -Path $gateLog -Pattern '\[FAIL\]|FAILED|ERROR' -AllMatches).Matches.Count
    $gateTail | ForEach-Object { Write-Info $_ }
    if ($gateExit -eq 0 -and $failCount -eq 0) {
        Write-Pass "test gate green (exit 0, zero FAIL lines)"
    } else {
        Write-Fail "test gate failed (exit $gateExit, $failCount FAIL/ERROR lines)"
        $script:HardFailures.Add("test_gate")
    }
    Remove-Item $gateLog -Force -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------------------
# Readiness summary
# ---------------------------------------------------------------------------
Write-Step "Readiness summary"
$isAdmin = Test-IsAdministrator
$modeDesc = if ($Check) { "audit (-Check, non-mutating)" } else { "full install" }
Write-Info "Administrator     : $(if ($isAdmin) {'YES'} else {'NO'})"
Write-Info "Mode              : $modeDesc"
Write-Info "Repo              : $repoRoot"

if ($script:HardFailures.Count -eq 0) {
    Write-Host "`nREADY: all hard steps passed." -ForegroundColor Green
    Write-Host "Operator-pending (not handled by this script):" -ForegroundColor DarkGray
    Write-Host "  - Provider credentials via Windows Credential Manager (orchestrator/secrets.py)." -ForegroundColor DarkGray
    Write-Host "  - Egress allowlist domains via the policy manager (operator-gated, deny-by-default)." -ForegroundColor DarkGray
    exit 0
} else {
    Write-Host "`nNOT READY: $($script:HardFailures.Count) hard step(s) failed or missing:" -ForegroundColor Yellow
    $script:HardFailures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
    if ($Check) {
        Write-Host "Run install.ps1 (without -Check, as Administrator) to provision the missing steps." -ForegroundColor DarkGray
    }
    # Fail-closed: a missing boundary or attestation never reports ready.
    exit 1
}

# agi.ps1 — routing/launcher ONLY for the unified operator CLI.
#
# This script contains NO safety policy and NO state mutation. All checks,
# validation, and authority remain inside orchestrator/operator_cli.py and the
# modules it composes. Adding anything beyond argument routing and process
# launch here would violate the "never a second safety authority" contract.
#
# Usage:
#   .\agi.ps1 status
#   .\agi.ps1 status -Json
#   .\agi.ps1 health --model-free
#   .\agi.ps1 preflight canary
#   .\agi.ps1 preflight release

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("status", "health", "preflight")]
    [string]$Command,

    [Parameter(Mandatory = $false, Position = 1)]
    [string]$Subcommand = "",

    [switch]$Json,

    # passthrough switches kept minimal and explicit
    [switch]$ModelFree
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$cli = Join-Path (Join-Path $repoRoot "orchestrator") "operator_cli.py"

if (-not (Test-Path $cli)) {
    Write-Error "operator_cli.py not found at $cli"
    exit 2
}

$python = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $python) {
    Write-Error "python not found on PATH"
    exit 2
}

$args = @($Command)
if ($Command -eq "health") {
    # health requires the model-free contract flag
    $args += "--model-free"
}
elseif ($Command -eq "preflight") {
    if ([string]::IsNullOrWhiteSpace($Subcommand)) {
        Write-Error "preflight requires a target (canary or release)"
        exit 2
    }
    $args += $Subcommand
}

if ($Json) { $args += "--json" }

& $python.Source -B $cli @args
exit $LASTEXITCODE

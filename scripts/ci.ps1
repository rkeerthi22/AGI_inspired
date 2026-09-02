[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $python) {
    Write-Error "Python was not found on PATH."
    exit 1
}

$gateExit = 1
Push-Location $repoRoot
try {
    Write-Host "Running the model-free harness gate..."
    & $python.Source -B tests/run_all.py
    $gateExit = $LASTEXITCODE
    if ($gateExit -eq 0) {
        Write-Host "CI GATE PASSED"
    } else {
        Write-Error "CI GATE FAILED (exit $gateExit)"
    }
} finally {
    Pop-Location
}

exit $gateExit

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $pythonExe = $venvPython
} elseif ($env:CI) {
    Write-Error "CI virtual environment is missing; run scripts/bootstrap.ps1 first."
    exit 1
} else {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $python) {
        Write-Error "Python was not found on PATH."
        exit 1
    }
    $pythonExe = $python.Source
}

$gateExit = 1
Push-Location $repoRoot
try {
    Write-Host "Running the model-free harness gate..."
    & $pythonExe -B tests/run_all.py
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

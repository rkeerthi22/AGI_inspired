[CmdletBinding()]
param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$requirementsPath = Join-Path $repoRoot "scripts\requirements.txt"
$venvPath = Join-Path $repoRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"

function Invoke-Checked {
    param([scriptblock]$Command, [string]$FailureMessage)
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage (exit $LASTEXITCODE)"
    }
}

$python = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $python) {
    throw "Python 3.11 or newer is required but python was not found on PATH."
}
$pythonVersion = (& $python.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
if ($LASTEXITCODE -ne 0 -or [version]$pythonVersion -lt [version]"3.11") {
    throw "Python 3.11 or newer is required; found '$pythonVersion'."
}

$node = Get-Command node -ErrorAction SilentlyContinue
if ($null -eq $node) {
    throw "Node.js 24 or newer is required but node was not found on PATH."
}
$nodeVersion = (& $node.Source --version).Trim()
if ($LASTEXITCODE -ne 0 -or $nodeVersion -notmatch '^v(?<major>\d+)') {
    throw "Unable to determine the installed Node.js version."
}
if ([int]$Matches.major -lt 24) {
    throw "Node.js 24 or newer is required; found '$nodeVersion'."
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Creating virtual environment at $venvPath"
    Invoke-Checked { & $python.Source -m venv $venvPath } "Virtual environment creation failed"
}

if ($SkipInstall) {
    Write-Host "Dependency installation skipped by request."
} else {
    Write-Host "Installing pinned Python dependencies."
    Invoke-Checked { & $venvPython -m pip install --requirement $requirementsPath } "Pinned dependency installation failed"
}

Write-Host "BOOTSTRAP COMPLETE"

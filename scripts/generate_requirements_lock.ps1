[CmdletBinding()]
param(
    [switch]$Check
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$inputPath = Join-Path $repoRoot "scripts\requirements.in"
$lockPath = Join-Path $repoRoot "scripts\requirements.txt"
$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($null -eq $uv) {
    throw "uv is required to regenerate the dependency hash lock."
}

$temporaryLock = Join-Path ([System.IO.Path]::GetTempPath()) "agi_like_requirements.lock"
try {
    & $uv.Source pip compile $inputPath --no-deps --generate-hashes `
        --python-version 3.11 --python-platform x86_64-pc-windows-msvc `
        --output-file $temporaryLock --no-annotate --no-header --no-progress --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "uv failed to generate the dependency lock (exit $LASTEXITCODE)."
    }
    $header = @(
        "# Public Python dependency lock for Windows x64 / Python 3.11.",
        "# Generated with: uv pip compile scripts/requirements.in --no-deps --generate-hashes --python-version 3.11 --python-platform x86_64-pc-windows-msvc",
        "# Hermes is an external runtime attested by scripts/hermes_runtime_attestation.json."
    )
    $generated = $header + (Get-Content -LiteralPath $temporaryLock -Encoding utf8)
    if ($Check) {
        $current = Get-Content -LiteralPath $lockPath -Raw -Encoding utf8
        $expected = ($generated -join [Environment]::NewLine) + [Environment]::NewLine
        if ($current -cne $expected) {
            throw "requirements.txt is stale; run scripts/generate_requirements_lock.ps1."
        }
        Write-Host "DEPENDENCY LOCK CURRENT"
    } else {
        Set-Content -LiteralPath $lockPath -Value $generated -Encoding utf8
        Write-Host "DEPENDENCY LOCK WRITTEN"
    }
} finally {
    Remove-Item -LiteralPath $temporaryLock -ErrorAction SilentlyContinue
}

# Run Gmail storage tools using the project venv (no global python required).
# Usage:
#   .\scripts\storage_tools.ps1 verify
#   .\scripts\storage_tools.ps1 audit --min-size-mb 5 --max-messages 500
#   .\scripts\storage_tools.ps1 archive --from-report reports\gmail_storage_20260909.csv --limit 20

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host ""
    Write-Host "ERROR: .venv not found at $venvPython" -ForegroundColor Red
    Write-Host ""
    Write-Host "Run one of these first (from the project folder):" -ForegroundColor Yellow
    Write-Host "  .\bootstrap.ps1" -ForegroundColor Cyan
    Write-Host "  Install.cmd" -ForegroundColor Cyan
    Write-Host ""
    exit 1
}

if ($args.Count -lt 1) {
    Write-Host "Usage:"
    Write-Host "  .\scripts\storage_tools.ps1 verify"
    Write-Host "  .\scripts\storage_tools.ps1 audit [--min-size-mb N] [--max-messages N]"
    Write-Host "  .\scripts\storage_tools.ps1 archive [--from-report PATH] [--execute --confirm] ..."
    exit 2
}

$tool = $args[0]
$toolArgs = @()
if ($args.Count -gt 1) { $toolArgs = $args[1..($args.Count - 1)] }

switch ($tool) {
    "verify" {
        & $venvPython "tools\verify_archive_auth.py" @toolArgs
    }
    "audit" {
        & $venvPython "tools\gmail_storage_audit.py" @toolArgs
    }
    "archive" {
        & $venvPython "tools\archive_to_onedrive.py" @toolArgs
    }
    default {
        Write-Host "Unknown tool: $tool (use verify, audit, or archive)" -ForegroundColor Red
        exit 2
    }
}
exit $LASTEXITCODE

# =====================================================================
# Email Assistant - one-shot bootstrap for Windows
# =====================================================================
# Installs Python (via winget if needed), creates the venv, installs
# dependencies, and prints next steps. Idempotent - safe to re-run.
#
# Usage (from the project root):
#     .\bootstrap.ps1
# =====================================================================

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "    [!]  $msg" -ForegroundColor Yellow }

# -------------------------------------------------------------------
# 0. Make sure script execution policy allows us to run.
# -------------------------------------------------------------------
$policy = Get-ExecutionPolicy -Scope CurrentUser
if ($policy -eq "Restricted" -or $policy -eq "Undefined") {
    Write-Step "Setting CurrentUser execution policy to RemoteSigned (required for venv activation)"
    Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force
    Write-Ok "Execution policy set."
}

# -------------------------------------------------------------------
# 1. Ensure Python 3.11+ is installed.
# -------------------------------------------------------------------
function Get-PythonExe {
    foreach ($candidate in @("python", "py")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) {
            $verOutput = & $candidate --version 2>&1
            if ($LASTEXITCODE -eq 0 -and $verOutput -match "Python (\d+)\.(\d+)") {
                $major = [int]$matches[1]; $minor = [int]$matches[2]
                if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) {
                    return $cmd.Source
                }
            }
        }
    }
    return $null
}

Write-Step "Checking for Python 3.11+"
$python = Get-PythonExe
if (-not $python) {
    Write-Warn2 "Python 3.11+ not found. Installing Python 3.12 via winget..."
    winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements --silent
    if ($LASTEXITCODE -ne 0) {
        throw "winget failed to install Python. Install manually from https://python.org and re-run."
    }
    # winget updates PATH for new processes only; refresh THIS session's PATH from the registry.
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + `
                [System.Environment]::GetEnvironmentVariable("Path", "User")
    $python = Get-PythonExe
    if (-not $python) {
        throw "Python install reported success but 'python' still not on PATH. Open a NEW PowerShell window and re-run .\bootstrap.ps1."
    }
}
Write-Ok "Found Python at: $python"

# -------------------------------------------------------------------
# 2. Create the virtualenv.
# -------------------------------------------------------------------
Write-Step "Creating virtual environment in .venv"
if (Test-Path ".venv") {
    Write-Ok ".venv already exists; reusing it."
} else {
    & $python -m venv .venv
    Write-Ok ".venv created."
}

$venvPython = Join-Path (Resolve-Path ".venv").Path "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "venv python not found at $venvPython"
}

# -------------------------------------------------------------------
# 3. Install Python dependencies.
# -------------------------------------------------------------------
Write-Step "Upgrading pip and installing requirements"
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r requirements.txt --quiet
Write-Ok "Dependencies installed."

# -------------------------------------------------------------------
# 4. Final summary.
# -------------------------------------------------------------------
Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host " Bootstrap complete." -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps (run them in this order):" -ForegroundColor White
Write-Host ""
Write-Host "  1. Activate the venv (only needed for new shells):" -ForegroundColor White
Write-Host "       .\.venv\Scripts\Activate.ps1" -ForegroundColor Yellow
Write-Host ""
Write-Host "  2. (Outlook users only) Auto-create the Microsoft Entra app registration:" -ForegroundColor White
Write-Host "       .\scripts\setup_entra.ps1" -ForegroundColor Yellow
Write-Host "     Registers Mail.ReadWrite + Calendars.ReadWrite + Files.ReadWrite (OneDrive)." -ForegroundColor DarkGray
Write-Host "     (Gmail users: download an OAuth client_secret.json from Google Cloud Console" -ForegroundColor DarkGray
Write-Host "      - see README section 'Gmail / Google Workspace setup'.)" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  3. Run the interactive setup wizard to fill in .env:" -ForegroundColor White
Write-Host "       python main.py --setup" -ForegroundColor Yellow
Write-Host "     Walks you through mailbox, company, email/LLM provider, notification" -ForegroundColor DarkGray
Write-Host "     channels, and bid document capture. The LLM picker offers OpenAI" -ForegroundColor DarkGray
Write-Host "     (paid, fastest), GitHub Models (free), Ollama (free, local, private)," -ForegroundColor DarkGray
Write-Host "     Azure OpenAI, or any OpenAI-compatible endpoint. If you pick Ollama" -ForegroundColor DarkGray
Write-Host "     and don't have it installed, the wizard will offer to auto-install" -ForegroundColor DarkGray
Write-Host "     it via winget and pull the model for you." -ForegroundColor DarkGray
Write-Host ""
Write-Host "  4. One-time email-provider sign-in (device code or browser):" -ForegroundColor White
Write-Host "       python main.py --auth" -ForegroundColor Yellow
Write-Host "     Grants the agent access to your mailbox, calendar, AND OneDrive/Drive" -ForegroundColor DarkGray
Write-Host "     for bid document capture." -ForegroundColor DarkGray
Write-Host ""
Write-Host "  5. Smoke test, then run for real:" -ForegroundColor White
Write-Host "       python main.py --once" -ForegroundColor Yellow
Write-Host "       python main.py" -ForegroundColor Yellow
Write-Host ""
Write-Host "  6. (Optional) Register a Windows Scheduled Task to run unattended:" -ForegroundColor White
Write-Host "       .\scripts\install_task.ps1" -ForegroundColor Yellow
Write-Host ""

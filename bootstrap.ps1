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
function Test-IsWindowsStoreStub {
    param([string]$Path)
    if (-not $Path) { return $false }
    # The Store alias / App Execution Alias stubs live under WindowsApps.
    # Running them prints "Python was not found; run without arguments to
    # install from the Microsoft Store..." and returns exit 9009.
    if ($Path -match '\\WindowsApps\\') { return $true }
    return $false
}

function Get-PythonExe {
    foreach ($candidate in @("python", "python3", "py")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        if (Test-IsWindowsStoreStub -Path $cmd.Source) {
            Write-Warn2 "Ignoring Microsoft Store stub at $($cmd.Source)"
            continue
        }
        $verOutput = & $cmd.Source --version 2>&1
        if ($LASTEXITCODE -eq 0 -and $verOutput -match "Python (\d+)\.(\d+)") {
            $major = [int]$matches[1]; $minor = [int]$matches[2]
            if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) {
                return $cmd.Source
            }
        }
    }
    return $null
}

function Install-PythonViaWinget {
    Write-Warn2 "Python 3.11+ not found. Attempting winget install of Python 3.12..."
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        Write-Warn2 "winget not available on this machine."
        return $false
    }
    winget install --id Python.Python.3.12 -e `
        --accept-source-agreements --accept-package-agreements `
        --silent --scope user
    return ($LASTEXITCODE -eq 0)
}

function Install-PythonFromPythonOrg {
    Write-Warn2 "Falling back to direct download from python.org..."
    $url = "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"
    $dst = Join-Path $env:TEMP "python-3.12.7-amd64.exe"
    try {
        Invoke-WebRequest -Uri $url -OutFile $dst -UseBasicParsing
    } catch {
        Write-Warn2 "Download failed: $($_.Exception.Message)"
        return $false
    }
    # Per-user, silent, no UI. Adds Python to user PATH.
    $args = @(
        "/quiet",
        "InstallAllUsers=0",
        "PrependPath=1",
        "Include_launcher=1",
        "Include_test=0",
        "Include_doc=0"
    )
    $proc = Start-Process -FilePath $dst -ArgumentList $args -Wait -PassThru
    return ($proc.ExitCode -eq 0)
}

Write-Step "Checking for Python 3.11+"
$python = Get-PythonExe
if (-not $python) {
    $installed = Install-PythonViaWinget
    if (-not $installed) { $installed = Install-PythonFromPythonOrg }
    if (-not $installed) {
        throw @"
Could not auto-install Python.

Manual fix:
  1. Open https://www.python.org/downloads/ in a browser.
  2. Download the Windows installer (3.12 or newer).
  3. Run it. CHECK the box 'Add python.exe to PATH' on the first screen.
  4. Open a NEW PowerShell window and re-run .\bootstrap.ps1.

If you only want to use Email Assistant (not develop on it), you don't need
Python at all - download EmailAssistantSetup.exe instead and double-click it.
"@
    }
    # winget / installer updated PATH for new processes only. Refresh
    # THIS session's PATH from the registry so we can re-detect.
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + `
                [System.Environment]::GetEnvironmentVariable("Path", "User")
    $python = Get-PythonExe
    if (-not $python) {
        throw @"
Python install completed but 'python' is still not on PATH in THIS shell.
Close this PowerShell window, open a new one, and re-run .\bootstrap.ps1.
The new session will pick up the freshly-installed Python.
"@
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

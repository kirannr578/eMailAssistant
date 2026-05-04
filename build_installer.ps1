# =====================================================================
# Email Assistant - one-shot installer builder
# ---------------------------------------------------------------------
# Runs PyInstaller against EmailAssistant.spec to produce
#     dist\EmailAssistant\EmailAssistant.exe
# and then runs Inno Setup's ISCC.exe against installer\installer.iss to
# produce
#     dist\EmailAssistantSetup.exe
#
# Usage (from the project root):
#     .\build_installer.ps1                 # full clean build
#     .\build_installer.ps1 -SkipPyInstaller   # only re-run Inno Setup
#     .\build_installer.ps1 -SkipInno          # only run PyInstaller
# =====================================================================

[CmdletBinding()]
param(
    [switch]$SkipPyInstaller,
    [switch]$SkipInno
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "    [!]  $msg" -ForegroundColor Yellow }

# ---------------------------------------------------------------------
# 0. Sanity checks
# ---------------------------------------------------------------------
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    throw "Virtual env not found. Run .\bootstrap.ps1 first."
}
$venvPython = (Resolve-Path ".\.venv\Scripts\python.exe").Path

# ---------------------------------------------------------------------
# 1. PyInstaller phase: build the frozen exe bundle.
# ---------------------------------------------------------------------
if (-not $SkipPyInstaller) {
    Write-Step "Ensuring PyInstaller and dev deps are installed in .venv"
    & $venvPython -m pip install -r requirements-dev.txt --quiet
    Write-Ok "Dev deps installed."

    Write-Step "Cleaning previous PyInstaller output"
    foreach ($d in @("build", "dist\EmailAssistant")) {
        if (Test-Path $d) {
            Remove-Item -Recurse -Force $d
            Write-Ok "Removed $d"
        }
    }

    Write-Step "Running PyInstaller (this takes ~30-90s)"
    # PyInstaller writes INFO-level lines to stderr, which PowerShell treats
    # as a terminating error when $ErrorActionPreference is "Stop". Lower
    # it just for this call and rely on $LASTEXITCODE for pass/fail.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $venvPython -m PyInstaller EmailAssistant.spec --clean --noconfirm --log-level WARN 2>&1 | Out-Host
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)." }

    $exe = ".\dist\EmailAssistant\EmailAssistant.exe"
    if (-not (Test-Path $exe)) { throw "PyInstaller did not produce $exe" }

    $sizeMB = [math]::Round((Get-Item $exe).Length / 1MB, 2)
    $folderMB = [math]::Round((Get-ChildItem .\dist\EmailAssistant -Recurse | Measure-Object Length -Sum).Sum / 1MB, 2)
    Write-Ok "Built $exe ($sizeMB MB exe, $folderMB MB total bundle)"
} else {
    Write-Step "Skipping PyInstaller (-SkipPyInstaller)"
    if (-not (Test-Path ".\dist\EmailAssistant\EmailAssistant.exe")) {
        throw "Cannot skip PyInstaller: dist\EmailAssistant\EmailAssistant.exe not found."
    }
}

# ---------------------------------------------------------------------
# 2. Inno Setup phase: wrap the bundle into a single-file installer.
# ---------------------------------------------------------------------
if (-not $SkipInno) {
    Write-Step "Locating Inno Setup compiler (ISCC.exe)"
    # Order matters: prefer system-wide installs, then per-user (which is
    # what `winget install --id JRSoftware.InnoSetup` does without admin).
    $isccCandidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
        "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 5\ISCC.exe",
        "${env:LOCALAPPDATA}\Programs\Inno Setup 5\ISCC.exe"
    )
    $iscc = $null
    foreach ($c in $isccCandidates) {
        if ($c -and (Test-Path $c)) { $iscc = $c; break }
    }
    if (-not $iscc) {
        # Last resort: ask Windows where the registered Inno Setup install lives.
        $reg = Get-ItemProperty "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1" -ErrorAction SilentlyContinue
        if ($reg -and $reg.InstallLocation) {
            $candidate = Join-Path $reg.InstallLocation "ISCC.exe"
            if (Test-Path $candidate) { $iscc = $candidate }
        }
    }
    if (-not $iscc) {
        $wingetIscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($wingetIscc) { $iscc = $wingetIscc.Source }
    }
    if (-not $iscc) {
        Write-Warn2 "ISCC.exe not found. Install Inno Setup 6 from https://jrsoftware.org/isdl.php"
        Write-Warn2 "Or via winget:  winget install -e --id JRSoftware.InnoSetup"
        Write-Warn2 "Then re-run:    .\build_installer.ps1 -SkipPyInstaller"
        throw "Inno Setup not installed."
    }
    Write-Ok "Found ISCC at: $iscc"

    Write-Step "Compiling installer\installer.iss"
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $iscc ".\installer\installer.iss" 2>&1 | Out-Host
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($LASTEXITCODE -ne 0) { throw "ISCC failed (exit $LASTEXITCODE)." }

    $setup = ".\dist\EmailAssistantSetup.exe"
    if (-not (Test-Path $setup)) { throw "ISCC did not produce $setup" }
    $setupMB = [math]::Round((Get-Item $setup).Length / 1MB, 2)
    Write-Ok "Built $setup ($setupMB MB)"
} else {
    Write-Step "Skipping Inno Setup (-SkipInno)"
}

# ---------------------------------------------------------------------
# 3. Summary
# ---------------------------------------------------------------------
Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host " Build complete." -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
if (Test-Path ".\dist\EmailAssistantSetup.exe") {
    Write-Host ""
    Write-Host "Installer:  $(Resolve-Path .\dist\EmailAssistantSetup.exe)" -ForegroundColor White
    Write-Host ""
    Write-Host "Copy that single .exe to your personal laptop and double-click it." -ForegroundColor White
    Write-Host "First-time SmartScreen warning: 'More info' -> 'Run anyway'." -ForegroundColor DarkGray
}

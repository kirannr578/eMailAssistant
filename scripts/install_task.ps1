# =====================================================================
# Email Assistant - register a Windows Scheduled Task
# =====================================================================
# Generates a Task Scheduler definition that runs the agent every N
# minutes under the current user (no admin needed). Auto-detects two
# layouts:
#   - Frozen install: EmailAssistant.exe sitting next to this script's
#     parent dir (the Inno Setup install at %LOCALAPPDATA%\Programs\
#     EmailAssistant). Task runs `EmailAssistant.exe --once`.
#   - Dev checkout:   .venv\Scripts\python.exe in the project root,
#     plus main.py and .env. Task runs `python main.py --once`.
#
# Usage:
#     .\scripts\install_task.ps1
#     .\scripts\install_task.ps1 -IntervalMinutes 10
#     .\scripts\install_task.ps1 -Uninstall
# =====================================================================

param(
    [int]$IntervalMinutes = 5,
    [string]$TaskName = "EmailAssistant",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "[OK] Removed scheduled task '$TaskName'." -ForegroundColor Green
    } else {
        Write-Host "[!]  Task '$TaskName' not found." -ForegroundColor Yellow
    }
    return
}

# ----------------------------------------------------------------------
# Detect mode: frozen install vs dev checkout.
# When invoked by Inno Setup, CWD = {app} = the install dir, which holds
# EmailAssistant.exe. When invoked from a dev checkout, CWD = repo root.
# Resolve both via the parent of THIS script as well, so it works
# regardless of how the user invoked it.
# ----------------------------------------------------------------------
$scriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$candidates  = @($PWD.Path, (Split-Path -Parent $scriptDir))
$frozenExe   = $null
$devPython   = $null
$workingDir  = $null
$envPath     = $null

foreach ($d in $candidates) {
    $exe = Join-Path $d "EmailAssistant.exe"
    $py  = Join-Path $d ".venv\Scripts\python.exe"
    if (Test-Path $exe) {
        $frozenExe  = $exe
        $workingDir = $d
        break
    }
    if (Test-Path $py) {
        $devPython  = $py
        $workingDir = $d
        break
    }
}

if ($frozenExe) {
    $action = New-ScheduledTaskAction `
        -Execute $frozenExe `
        -Argument "--once" `
        -WorkingDirectory $workingDir
    $envPath = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "EmailAssistant\.env"
    $taskDescription = "Email Assistant (frozen install): poll mailbox, analyze, block calendar, notify."
    $modeLabel = "frozen install at $frozenExe"
} elseif ($devPython) {
    $action = New-ScheduledTaskAction `
        -Execute $devPython `
        -Argument "main.py --once" `
        -WorkingDirectory $workingDir
    $envPath = Join-Path $workingDir ".env"
    $taskDescription = "Email Assistant (dev): poll mailbox, analyze, block calendar, notify."
    $modeLabel = "dev checkout at $workingDir"
} else {
    throw "Could not find EmailAssistant.exe or .venv\Scripts\python.exe. Re-run from the project root, or reinstall via EmailAssistantSetup.exe."
}

# Soft warning if .env doesn't exist yet, but don't refuse to register
# the task: the user may schedule it now and run --setup later. The
# scheduled exe prints a friendly error and exits cleanly when .env is
# missing, so an early task is harmless.
if (-not (Test-Path $envPath)) {
    Write-Host "[!]  .env not found at $envPath" -ForegroundColor Yellow
    Write-Host "     Run the Setup Wizard before the task fires, or it will keep error-exiting." -ForegroundColor Yellow
}

# Repeat every N minutes, indefinitely. Start 1 minute from now.
$startTime = (Get-Date).AddMinutes(1)
$trigger = New-ScheduledTaskTrigger -Once -At $startTime `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description $taskDescription | Out-Null

Write-Host ""
Write-Host "[OK] Registered scheduled task '$TaskName' running every $IntervalMinutes minute(s)." -ForegroundColor Green
Write-Host "     Mode: $modeLabel" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Verify with:   Get-ScheduledTask -TaskName '$TaskName'" -ForegroundColor White
Write-Host "View logs in:  Task Scheduler -> Task Scheduler Library -> $TaskName -> History tab" -ForegroundColor White
Write-Host "                AND %LOCALAPPDATA%\EmailAssistant\logs\agent.log (frozen install)" -ForegroundColor White
Write-Host "Force a run:   Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor White
Write-Host "Uninstall:     .\scripts\install_task.ps1 -Uninstall" -ForegroundColor White
Write-Host ""

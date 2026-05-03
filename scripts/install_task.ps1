# =====================================================================
# Email Assistant - register a Windows Scheduled Task
# =====================================================================
# Generates a Task Scheduler definition that runs `main.py --once` every
# N minutes, and registers it under the current user.
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

$projectRoot = (Resolve-Path ".").Path
$venvPython  = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    throw "Could not find $venvPython. Run .\bootstrap.ps1 first."
}
if (-not (Test-Path (Join-Path $projectRoot ".env"))) {
    throw ".env not found. Run python main.py --setup first."
}

$action = New-ScheduledTaskAction `
    -Execute $venvPython `
    -Argument "main.py --once" `
    -WorkingDirectory $projectRoot

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
    -Description "Email Assistant: poll Outlook, analyze, block calendar, notify." | Out-Null

Write-Host ""
Write-Host "[OK] Registered scheduled task '$TaskName' running every $IntervalMinutes minute(s)." -ForegroundColor Green
Write-Host ""
Write-Host "Verify with:   Get-ScheduledTask -TaskName '$TaskName'" -ForegroundColor White
Write-Host "View logs in:  Task Scheduler -> Task Scheduler Library -> $TaskName -> History tab" -ForegroundColor White
Write-Host "Force a run:   Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor White
Write-Host "Uninstall:     .\scripts\install_task.ps1 -Uninstall" -ForegroundColor White
Write-Host ""

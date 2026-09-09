@echo off
cd /d "%~dp0"
if not exist ".\.venv\Scripts\python.exe" (
    echo Run bootstrap.ps1 first.
    pause & exit /b 1
)
if "%~1"=="" (
    echo Usage:
    echo   GmailCleanup.cmd status
    echo   GmailCleanup.cmd scan
    echo   GmailCleanup.cmd run 10
    pause & exit /b 2
)
.\.venv\Scripts\python.exe tools\gmail_cleanup.py %*
pause

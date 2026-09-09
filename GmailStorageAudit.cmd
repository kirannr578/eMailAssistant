@echo off
REM Scan Gmail for large attachments (read-only). Uses .venv python.
cd /d "%~dp0"
if not exist ".\.venv\Scripts\python.exe" (
    echo.
    echo ERROR: Python venv not found. Run bootstrap.ps1 or Install.cmd first.
    echo.
    pause
    exit /b 1
)
.\.venv\Scripts\python.exe tools\gmail_storage_audit.py %*
set EXITCODE=%ERRORLEVEL%
if %EXITCODE% neq 0 pause
exit /b %EXITCODE%

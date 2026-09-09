@echo off
REM Verify Google + Microsoft auth for Gmail storage tools (uses .venv python).
cd /d "%~dp0"
if not exist ".\.venv\Scripts\python.exe" (
    echo.
    echo ERROR: Python venv not found. Run bootstrap.ps1 or Install.cmd first.
    echo.
    pause
    exit /b 1
)
.\.venv\Scripts\python.exe tools\verify_archive_auth.py %*
set EXITCODE=%ERRORLEVEL%
if %EXITCODE% neq 0 pause
exit /b %EXITCODE%

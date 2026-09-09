@echo off
REM Sign in to Gmail for storage tools (browser OAuth). Uses .venv python.
cd /d "%~dp0"
if not exist ".\.venv\Scripts\python.exe" (
    echo ERROR: Run bootstrap.ps1 or Install.cmd first.
    pause
    exit /b 1
)
.\.venv\Scripts\python.exe tools\setup_google_auth.py
pause

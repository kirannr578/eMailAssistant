@echo off
REM Show how much Gmail space has been reclaimed from archiving.
cd /d "%~dp0"
if not exist ".\.venv\Scripts\python.exe" (
    echo ERROR: Run bootstrap.ps1 first.
    pause
    exit /b 1
)
if "%~1"=="" (
    echo Usage: ArchiveProgress.cmd reports\gmail_storage_YYYYMMDD.csv
    pause
    exit /b 2
)
.\.venv\Scripts\python.exe tools\archive_progress.py --report %1
pause

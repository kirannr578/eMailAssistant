@echo off
REM =====================================================================
REM Email Assistant - one-click Windows installer
REM ---------------------------------------------------------------------
REM Double-click this file from File Explorer, OR run it from any shell:
REM     Install.cmd
REM
REM Idempotent: re-running picks up where a previous run failed.
REM
REM Steps performed (with prompts where input is unavoidable):
REM   1. Strip Mark-of-the-Web from bundled .ps1 scripts (avoids the
REM      "not digitally signed" PowerShell error on corporate laptops).
REM   2. Run bootstrap.ps1 - installs Python (if missing), creates .venv,
REM      pip-installs requirements.
REM   3. Outlook users: optionally auto-register a Microsoft Entra app
REM      via Azure CLI. Gmail users: verify client_secret.json is present.
REM   4. Run main.py --setup (interactive wizard, builds .env).
REM   5. Run main.py --auth   (one-time browser/device-code sign-in).
REM   6. Run main.py --once   (smoke test - processes current unread mail).
REM   7. Optionally register a Scheduled Task to run every 5 minutes.
REM =====================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"
title Email Assistant - Installer
color 0F

echo.
echo ======================================================================
echo                     EMAIL ASSISTANT - INSTALLER
echo ======================================================================
echo.
echo This will set up the Email Assistant agent on this laptop:
echo   * Install Python (if missing) and project dependencies
echo   * Optionally register a Microsoft Entra app (Outlook users)
echo   * Walk you through the interactive setup wizard
echo   * Sign you in to your mailbox provider (browser or device code)
echo   * Smoke-test that one polling cycle works end-to-end
echo   * Optionally schedule it to run every 5 minutes
echo.
echo You can re-run this installer any time. Already-finished steps are
echo skipped automatically.
echo.
pause

REM ---------------------------------------------------------------------
REM Pre-flight: strip Mark-of-the-Web off .ps1 files so they execute
REM under corporate RemoteSigned GPO without "not digitally signed" errors.
REM Inline PowerShell commands are NOT subject to execution policy, so
REM this works even when scripts are blocked.
REM ---------------------------------------------------------------------
echo.
echo ==^> Pre-flight: unblocking PowerShell scripts
powershell -NoProfile -Command "Get-ChildItem -Path . -Recurse -Filter *.ps1 -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue"

REM ---------------------------------------------------------------------
REM STEP 1: bootstrap (Python + venv + dependencies)
REM ---------------------------------------------------------------------
echo.
echo ======================================================================
echo  STEP 1 of 6 - Installing Python and dependencies
echo ======================================================================
powershell -NoProfile -ExecutionPolicy Bypass -File ".\bootstrap.ps1"
if errorlevel 1 (
    echo.
    echo bootstrap.ps1 failed. See the error above.
    goto :failed
)

if not exist ".\.venv\Scripts\python.exe" (
    echo.
    echo Bootstrap reported success but .venv\Scripts\python.exe is missing.
    goto :failed
)

REM ---------------------------------------------------------------------
REM STEP 2: mailbox provider (Outlook gets Entra app registration)
REM ---------------------------------------------------------------------
echo.
echo ======================================================================
echo  STEP 2 of 6 - Mailbox provider
echo ======================================================================
echo.
echo   [1] Microsoft 365 / Outlook
echo   [2] Gmail / Google Workspace
echo.
choice /C 12 /N /M "Which mailbox will the agent monitor? [1/2] "
if errorlevel 2 goto :gmail_path
goto :outlook_path

:outlook_path
echo.
echo Outlook needs a Microsoft Entra app registration with these scopes:
echo   Mail.ReadWrite, Calendars.ReadWrite, Files.ReadWrite, offline_access.
echo.
echo   [A] Auto-register via Azure CLI now (recommended, ~2 min)
echo   [B] Skip - I'll register manually in the Entra portal
echo.
choice /C AB /N /M "Choose [A/B] "
if errorlevel 2 goto :skip_entra

echo.
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\setup_entra.ps1"
if errorlevel 1 (
    echo.
    echo Entra registration failed. You can finish manually using the steps in
    echo README section "What if you can't / don't want to use Azure CLI?",
    echo then re-run this installer.
    goto :failed
)
goto :wizard

:skip_entra
echo.
echo Skipped. Make sure your Entra app is registered before the wizard.
echo See README section "What if you can't / don't want to use Azure CLI?".
echo Press any key when ready to continue (or close this window to cancel)...
pause >nul
goto :wizard

:gmail_path
echo.
echo Gmail needs an OAuth client JSON file from Google Cloud Console.
echo See README section "Gmail / Google Workspace setup" for instructions.
echo.
echo Save the file as:
echo   %CD%\client_secret.json
echo.
if not exist "client_secret.json" (
    echo File not found yet. Save your OAuth client JSON to that path now,
    echo then press any key to continue.
    pause >nul
)
if not exist "client_secret.json" (
    echo.
    echo client_secret.json still not found. Save it and re-run Install.cmd.
    goto :failed
)
goto :wizard

REM ---------------------------------------------------------------------
REM STEP 3: interactive setup wizard
REM ---------------------------------------------------------------------
:wizard
echo.
echo ======================================================================
echo  STEP 3 of 6 - Interactive setup wizard
echo ======================================================================
echo The wizard asks for credentials and validates each one with a live API call.
echo.
".\.venv\Scripts\python.exe" main.py --setup
if errorlevel 1 goto :failed

REM ---------------------------------------------------------------------
REM STEP 4: provider sign-in
REM ---------------------------------------------------------------------
echo.
echo ======================================================================
echo  STEP 4 of 6 - Sign in to your mailbox provider
echo ======================================================================
echo Outlook: a device code will be printed - open the URL, paste the code.
echo Gmail:   your default browser will open for OAuth consent.
echo.
".\.venv\Scripts\python.exe" main.py --auth
if errorlevel 1 goto :failed

REM ---------------------------------------------------------------------
REM STEP 5: smoke test
REM ---------------------------------------------------------------------
echo.
echo ======================================================================
echo  STEP 5 of 6 - Smoke test (one polling cycle)
echo ======================================================================
".\.venv\Scripts\python.exe" main.py --once
if errorlevel 1 (
    echo.
    echo Smoke test failed. Check the log above. Common causes:
    echo   * Wrong / expired credentials in .env  -^> re-run Install.cmd
    echo   * LLM provider quota or billing issue
    echo   * Notification channel rejected the test message
    goto :failed
)

REM ---------------------------------------------------------------------
REM STEP 6: optional Scheduled Task
REM ---------------------------------------------------------------------
echo.
echo ======================================================================
echo  STEP 6 of 6 - Schedule unattended runs (optional)
echo ======================================================================
echo.
choice /C YN /N /M "Register a Windows Scheduled Task to run every 5 minutes? [Y/N] "
if errorlevel 2 goto :done

echo.
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\install_task.ps1"
if errorlevel 1 (
    echo.
    echo Task registration failed - it may need an elevated PowerShell.
    echo You can register it later by right-clicking PowerShell, "Run as Administrator",
    echo then running:  .\scripts\install_task.ps1
)

:done
echo.
echo ======================================================================
echo                          INSTALL COMPLETE
echo ======================================================================
echo.
echo Useful commands from here on:
echo   .\.venv\Scripts\python.exe main.py            (run interactively)
echo   .\.venv\Scripts\python.exe main.py --once     (process one cycle)
echo   .\scripts\install_task.ps1 -Uninstall         (remove scheduled task)
echo   .\.venv\Scripts\python.exe main.py --setup    (re-run the wizard)
echo.
pause
exit /b 0

:failed
echo.
echo ======================================================================
echo                          INSTALL FAILED
echo ======================================================================
echo Scroll up to see the error message. Fix the issue and re-run Install.cmd
echo - the installer is idempotent and will pick up where it left off.
echo.
pause
exit /b 1

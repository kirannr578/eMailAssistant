# =====================================================================
# Email Assistant - automated Microsoft Entra app registration
# =====================================================================
# Replaces the manual 7-click portal flow with a single command.
# Uses Azure CLI (auto-installs via winget if missing).
#
# What it does:
#   1. Creates an Entra app registration named "Email Assistant"
#   2. Enables public client flows (required for device-code OAuth)
#   3. Adds Microsoft Graph delegated permissions:
#        Mail.ReadWrite, Calendars.ReadWrite, User.Read, offline_access
#   4. Grants admin consent (only works if the signed-in user is an admin)
#   5. Prints / writes the MS_CLIENT_ID and MS_TENANT_ID values needed
#      by the setup wizard.
#
# Usage (from the project root):
#     .\scripts\setup_entra.ps1
# =====================================================================

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "    [!]  $msg" -ForegroundColor Yellow }

# Microsoft Graph well-known identifiers - constant across all tenants.
$GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"
$PERMS = @(
    @{ name = "Mail.ReadWrite";     id = "024d486e-b451-40bb-833d-3e66d98c5c73" },
    @{ name = "Calendars.ReadWrite"; id = "1ec239c2-d7c9-4623-a91a-a9775856bb36" },
    @{ name = "User.Read";          id = "e1fe6dd8-ba31-4d61-89e7-88639da4683d" },
    @{ name = "offline_access";     id = "7427e0e9-2fba-42fe-b0c0-848c9e6a8182" }
)

# -------------------------------------------------------------------
# 1. Ensure Azure CLI is installed.
# -------------------------------------------------------------------
Write-Step "Checking for Azure CLI"
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Warn2 "Azure CLI not found. Installing via winget..."
    winget install --id Microsoft.AzureCLI -e --accept-source-agreements --accept-package-agreements --silent
    if ($LASTEXITCODE -ne 0) {
        throw "winget failed to install Azure CLI. Install manually from https://aka.ms/installazurecliwindows and re-run."
    }
    # Refresh PATH for this session.
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + `
                [System.Environment]::GetEnvironmentVariable("Path", "User")
    if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
        throw "Azure CLI installed but 'az' still not on PATH. Open a NEW PowerShell window and re-run this script."
    }
}
Write-Ok "Azure CLI is available."

# -------------------------------------------------------------------
# 2. Make sure we're logged in.
# -------------------------------------------------------------------
Write-Step "Verifying Azure CLI sign-in"
$accountJson = az account show 2>$null
if (-not $accountJson) {
    Write-Warn2 "Not signed in. Launching 'az login'..."
    az login --allow-no-subscriptions | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "az login failed." }
    $accountJson = az account show
}
$account = $accountJson | ConvertFrom-Json
Write-Ok "Signed in as $($account.user.name) (tenant $($account.tenantId))"
$tenantId = $account.tenantId

# -------------------------------------------------------------------
# 3. Create or reuse the app registration.
# -------------------------------------------------------------------
$appName = "Email Assistant"
Write-Step "Looking up existing app registration named '$appName'"
$existing = az ad app list --display-name $appName --query "[0]" -o json | ConvertFrom-Json
if ($existing -and $existing.appId) {
    Write-Ok "Found existing app: $($existing.appId). Reusing."
    $appId = $existing.appId
} else {
    Write-Step "Creating new app registration"
    # AzureADandPersonalMicrosoftAccount = supports both work and personal accounts.
    $created = az ad app create `
        --display-name $appName `
        --sign-in-audience "AzureADandPersonalMicrosoftAccount" `
        --is-fallback-public-client true `
        -o json | ConvertFrom-Json
    $appId = $created.appId
    Write-Ok "Created app: $appId"
}

# -------------------------------------------------------------------
# 4. Add the Microsoft Graph delegated permissions.
# -------------------------------------------------------------------
Write-Step "Adding Microsoft Graph delegated permissions"
foreach ($p in $PERMS) {
    az ad app permission add `
        --id $appId `
        --api $GRAPH_APP_ID `
        --api-permissions "$($p.id)=Scope" 2>$null | Out-Null
    Write-Ok "Added $($p.name)"
}

# -------------------------------------------------------------------
# 5. Try to grant admin consent. (Only works if signed-in user is an admin.)
# -------------------------------------------------------------------
Write-Step "Attempting to grant admin consent"
az ad app permission grant --id $appId --api $GRAPH_APP_ID --scope "Mail.ReadWrite Calendars.ReadWrite User.Read offline_access" 2>$null | Out-Null
$adminConsent = az ad app permission admin-consent --id $appId 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Warn2 "Admin consent failed (likely you're not a tenant admin)."
    Write-Warn2 "Either ask your IT admin to run:"
    Write-Warn2 "    az ad app permission admin-consent --id $appId"
    Write-Warn2 "...or each user can self-consent on first device-code sign-in."
} else {
    Write-Ok "Admin consent granted."
}

# -------------------------------------------------------------------
# 6. Print and persist the values needed for .env.
# -------------------------------------------------------------------
$summaryPath = "entra_app.txt"
@"
# Microsoft Entra app registration created by setup_entra.ps1
# Paste these into the wizard (python main.py --setup)
MS_CLIENT_ID=$appId
MS_TENANT_ID=$tenantId
"@ | Out-File -FilePath $summaryPath -Encoding utf8

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host " Entra app ready." -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  MS_CLIENT_ID = $appId" -ForegroundColor Yellow
Write-Host "  MS_TENANT_ID = $tenantId" -ForegroundColor Yellow
Write-Host ""
Write-Host "These values are also saved to: $summaryPath (gitignored)" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Next: python main.py --setup" -ForegroundColor White

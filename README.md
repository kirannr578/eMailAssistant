# Email Assistant

An autonomous agent that monitors a mailbox (Microsoft 365 **or** Gmail / Google
Workspace), reads each new unread email with an LLM (OpenAI, GitHub Models,
Azure OpenAI, or local Ollama), and:

- Detects whether it's a **meeting request** (extracting the proposed time)
  and **blocks your calendar** automatically for high-confidence meetings
  (tentative or busy depending on confidence).
- Detects whether it's a **bid request** ("RFP", "RFQ", "ITB", "please quote",
  "submit a bid for...") addressed to your company, extracts the project name,
  location, scope, and **bid due date**, and **places a calendar reminder
  exactly at the bid deadline** so you never miss a submittal window.
- Sends a short **notification** (Telegram, WhatsApp via Meta, WhatsApp/SMS
  via Twilio - any combination) summarizing the email and the action taken.
  Bid notifications start with `[BID]`, meetings with `[MEETING]`.
- Marks the email as read and remembers what it has already processed (SQLite),
  so restarts and crashes never re-act on the same message.

## Architecture

```
                          +----------------------+
                          |     main.py loop     |
                          | (poll every 60s)     |
                          +----------+-----------+
                                     |
       +-----------------------------+----------------------------+
       |                             |                            |
+------v-------+            +--------v---------+         +--------v--------+
|  Outlook     |            |    Analyzer      |         |    Notifier     |
|  (MS Graph)  |--unread--> |  (OpenAI JSON)   |--SMS--->|    (Twilio)     |
|  list/mark   |            |  -> Analysis     |  /WA    |   SMS+WhatsApp  |
+------+-------+            +--------+---------+         +-----------------+
       |                             |
       |                   if meeting & confident
       |                             v
       |                    +------------------+
       +------ token -----> |  Calendar        |
                            |  (MS Graph)      |
                            |  create event    |
                            +------------------+

State (SQLite): processed message IDs, calendar event IDs, notify status.
Auth: MSAL device-code flow, refresh-token cached on disk (token_cache.bin).
```

### Files

```
.
├── Install.cmd             # Double-click launcher (runs everything end-to-end on a dev machine)
├── EmailAssistant.spec     # PyInstaller spec - bundles Python + source into a frozen exe
├── installer/
│   └── installer.iss       # Inno Setup script - wraps the bundle into EmailAssistantSetup.exe
├── build_installer.ps1     # One-shot build: PyInstaller -> Inno Setup -> dist/EmailAssistantSetup.exe
├── bootstrap.ps1           # Installs Python, creates venv, pip installs
├── main.py                 # Entry point: --setup | --auth | --once | (default loop)
├── setup_wizard.py         # Interactive .env builder with live credential validation
├── config.py               # Typed Settings loaded from .env
├── analyzer.py             # LLM JSON-mode analysis + Pydantic schema (meetings + bids)
├── document_downloader.py  # URL extraction + safe document download from email bodies
├── state.py                # SQLite dedup of processed messages
├── providers/
│   ├── base.py             # Provider-agnostic Protocol interfaces
│   ├── ms_graph_auth.py    # MSAL device-code OAuth + token cache (Microsoft)
│   ├── outlook.py          # Graph: list unread, mark read, attachments
│   ├── calendar.py         # Graph: create event
│   ├── onedrive.py         # Graph: ensure folder, upload (small + chunked sessions)
│   ├── google_auth.py      # Google OAuth (loopback redirect) + token cache
│   ├── gmail.py            # Gmail API: list unread, mark read, attachments
│   ├── google_calendar.py  # Google Calendar: create event
│   ├── google_drive.py     # Google Drive: ensure folder, upload (drive.file scope)
│   ├── telegram.py         # Telegram bot client + chat-id auto-discovery
│   ├── whatsapp_meta.py    # Meta WhatsApp Cloud API client
│   └── notifier.py         # Multi-channel dispatcher (Telegram / WA / SMS)
├── scripts/
│   ├── setup_entra.ps1     # Auto-create Entra app via Azure CLI
│   └── install_task.ps1    # Register / uninstall the Windows Scheduled Task
├── tools/
│   └── test_analyze.py     # Local LLM harness: feed a saved email, print Analysis
├── samples/                # Example email files for the test harness
│   └── dps_mt_pleasant_ifb.txt
├── tests/
│   ├── test_analyzer.py
│   ├── test_document_downloader.py
│   └── test_test_analyze.py
├── requirements.txt
├── .env.example
└── README.md
```

## Quick start (Windows / PowerShell)

**The fastest path: double-click `Install.cmd`** in the project root. It chains
all five setup steps below into a single guided run, prompts you only where
input is genuinely required (Outlook vs Gmail, optional Scheduled Task), and
auto-strips the Mark-of-the-Web that otherwise causes the "not digitally
signed" PowerShell error on corporate laptops. If it fails partway, just
re-run it - every step is idempotent.

Prefer the manual flow? It's **5 commands + paste a handful of secrets**.
Helper scripts handle Python install, venv, dependencies, Entra app
registration (incl. the OneDrive `Files.ReadWrite` scope for bid document
capture), and Task Scheduler registration. The only things you must do
yourself are sign up for your chosen LLM provider and notification channel -
their security models prevent automation.

### Prerequisites you set up in a browser

You need **one mailbox provider**, **one LLM provider**, and **one notification
channel**. Free options exist in every category:

#### Mailbox / Calendar (pick ONE)
- **Microsoft 365 / Outlook** - register a free Entra app (auto-script provided).
- **Gmail / Google Workspace** - create a free Google Cloud project, enable
  Gmail + Calendar APIs, download an OAuth client JSON. See "Gmail / Google
  Workspace setup" section below.

#### LLM provider (pick ONE)
| Provider | Cost | Setup |
|---|---|---|
| **OpenAI** (default) | ~$0.0001/email | <https://platform.openai.com/api-keys> + add $5 credit |
| **GitHub Models** | **FREE** with daily quota | <https://github.com/settings/tokens> -> create fine-grained PAT (no scopes needed) |
| **Azure OpenAI** | Your Azure billing | Use your existing deployment |
| **Ollama (local)** | **FREE**, runs on your laptop, fully private | The wizard auto-installs Ollama via `winget` and pulls the model for you when you pick option [3]. No prep needed. |
| **OpenAI-compatible** | Varies | Any LM Studio / vLLM / llama.cpp endpoint |

You're not locked in - to switch providers later, just edit `LLM_PROVIDER`,
`LLM_API_KEY`, `LLM_MODEL`, and `LLM_BASE_URL` in `.env` and restart the
agent. Common pattern: start with OpenAI for accuracy, switch to Ollama
later for privacy or to go offline.

#### Notification channel (pick one or more)
| Channel | Cost | Setup |
|---|---|---|
| **Telegram bot** (recommended) | **FREE** forever | Message @BotFather in Telegram -> `/newbot` |
| **Meta WhatsApp Cloud API** | Free up to 1000/mo | 30-min Business Manager setup. See "Meta WhatsApp Cloud API setup". |
| **Twilio WhatsApp sandbox** | Free for testing | Twilio signup + WhatsApp join code. 24h-window limit. |
| **Twilio SMS** | $15 trial credit, then ~$0.008/SMS | Twilio signup + buy an SMS-capable number |

### One-time setup on the laptop

```powershell
# Replace this with the path where you cloned the repo
cd "C:\path\to\eMailAssistant"

# 1) Install Python (if needed), create venv, install deps
.\bootstrap.ps1

# 2) (Outlook users) Auto-create the Entra app registration via Azure CLI.
#    Installs Azure CLI via winget if needed; opens browser once for `az login`.
#    Registers Mail.ReadWrite + Calendars.ReadWrite + Files.ReadWrite (OneDrive)
#    and prints MS_CLIENT_ID + MS_TENANT_ID at the end.
.\scripts\setup_entra.ps1

# 3) Interactive wizard - 6 sections covering mailbox + company, email/LLM
#    provider, notification channels, and bid document capture (OneDrive /
#    Google Drive). Validates each credential with a live API call.
python main.py --setup

# 4) One-time email-provider sign-in (device-code flow for Outlook,
#    browser flow for Gmail). Grants the agent access to mailbox, calendar,
#    AND OneDrive/Google Drive in a single consent.
python main.py --auth

# 5) Smoke test
python main.py --once
```

Already had the agent installed before bid-doc-capture shipped? You only
need to re-run step 4 to consent to the new file-storage scope:

```powershell
python main.py --auth        # picks up Files.ReadWrite (Outlook) or drive.file (Gmail)
```

If everything works, schedule it to run every 5 minutes:

```powershell
.\scripts\install_task.ps1                  # default: every 5 min
.\scripts\install_task.ps1 -IntervalMinutes 10
.\scripts\install_task.ps1 -Uninstall       # remove the task
```

To run interactively instead of via Task Scheduler:

```powershell
python main.py            # polling loop, Ctrl+C to stop
```

### What if you can't / don't want to use Azure CLI?

The Entra app registration can be done manually in the portal in ~5 min:

1. <https://entra.microsoft.com> -> **App registrations** -> **New registration**.
2. Name `Email Assistant`. Supported account types: "Accounts in any
   organizational directory and personal Microsoft accounts". Skip Redirect URI.
3. **Authentication** -> Advanced -> "Allow public client flows" = Yes.
4. **API permissions** -> Microsoft Graph -> Delegated:
   `Mail.ReadWrite`, `Calendars.ReadWrite`, `Files.ReadWrite`, `User.Read`,
   `offline_access`.
   Click "Grant admin consent" (admin only).
5. Copy the Application (client) ID and your tenant ID. Use them in step 3 above.

## Build a redistributable installer (Windows .exe)

Want a polished `EmailAssistantSetup.exe` you can copy to another laptop and
double-click? The repo ships with a PyInstaller spec + Inno Setup script that
together produce a single self-contained Windows installer (Welcome screen,
Start Menu shortcuts, Add/Remove Programs entry, clean uninstaller, optional
Scheduled Task registration on the Finish page).

End users do NOT need Python, a venv, or any of the other prerequisites - the
installer bundles a private Python runtime alongside the app.

### One-time build prerequisites (on the build machine only)

1. Run `bootstrap.ps1` so `.venv` and the runtime deps exist.
2. Install Inno Setup 6 (free):
   ```powershell
   winget install -e --id JRSoftware.InnoSetup
   ```
   (Or download the installer from <https://jrsoftware.org/isdl.php>.)

### Build

```powershell
.\build_installer.ps1
```

This:
1. Installs PyInstaller into `.venv` (via `requirements-dev.txt`).
2. Cleans `build/` and `dist/EmailAssistant/`.
3. Runs PyInstaller against `EmailAssistant.spec`, producing
   `dist\EmailAssistant\EmailAssistant.exe` (~16 MB exe + ~150 MB of bundled
   deps in the same folder).
4. Runs Inno Setup against `installer\installer.iss`, producing
   `dist\EmailAssistantSetup.exe` (~50-70 MB after LZMA2 compression).

Flags for iterating:

```powershell
.\build_installer.ps1 -SkipPyInstaller   # only re-compile the Inno Setup wrapper
.\build_installer.ps1 -SkipInno          # only rebuild the PyInstaller bundle
```

### What the installer does on the target machine

- Per-user install (no UAC, no admin) to
  `%LOCALAPPDATA%\Programs\EmailAssistant\`.
- Creates Start Menu shortcuts in a group called "Email Assistant":
  - **Email Assistant (Run continuously)** - the polling daemon
  - **Email Assistant - Setup Wizard** (`--setup`)
  - **Email Assistant - Sign In** (`--auth`)
  - **Email Assistant - Run Once (smoke test)** (`--once`)
  - **Open Data Folder** - opens `%LOCALAPPDATA%\EmailAssistant\` directly
  - **Uninstall Email Assistant**
- The Finish page offers two opt-in checkboxes: *Run the Setup Wizard now* and
  *Schedule the agent to run every 5 minutes (Task Scheduler)*.
- Adds an "Email Assistant" entry to **Settings -> Apps -> Installed apps** for
  clean uninstall.

### Where data lives at runtime

The frozen exe pins its working directory to `%LOCALAPPDATA%\EmailAssistant\`
(NOT the install dir) on every launch. That folder holds:

- `.env`           - your config, written by the Setup Wizard
- `state.db`       - SQLite dedup state
- `token_cache.bin` / `google_token.json` - OAuth refresh tokens
- `client_secret.json` (Gmail users only) - drop yours here

This separation means uninstalling the program does NOT wipe your `.env` or
tokens; reinstalling picks up where you left off. To reset state, delete the
folder manually.

You can override the data dir for special cases (testing, multiple mailboxes
on one machine) with the env var `EMAIL_ASSISTANT_DATA_DIR`.

### First-run on a fresh laptop

1. Copy `dist\EmailAssistantSetup.exe` to the target laptop (USB / OneDrive / email).
2. Double-click. Windows SmartScreen will warn ("unrecognized publisher")
   because the installer isn't code-signed - click **More info -> Run anyway**.
3. The Inno Setup wizard runs: Welcome -> Install location -> Ready -> Install -> Finish.
4. On the Finish page, tick **Run the Setup Wizard now** and **Schedule the
   agent to run every 5 minutes** if you want full setup in one go.
5. Setup Wizard collects credentials (mailbox, LLM, notification channel) and
   validates each one with a live API call.
6. Use the Start Menu **Email Assistant - Sign In** shortcut once to complete
   OAuth (browser pops up, or device code is printed in the console).
7. The Scheduled Task takes over from there - it runs `--once` every 5 minutes
   silently in the background.

### Updating an installed copy

Build a new `EmailAssistantSetup.exe` with a bumped version (edit
`AppVersion` in `installer\installer.iss`) and run it on the target. Inno
Setup detects the existing install via `AppId` and upgrades in place. The
data folder (`%LOCALAPPDATA%\EmailAssistant\`) is untouched.

### Where to look when something goes wrong

The agent now persists structured logs and crash dumps to disk so you don't
need to keep a console window open to debug failures.

| Path | What it contains |
|------|------------------|
| `%LOCALAPPDATA%\EmailAssistant\logs\agent.log` | Rolling info-level log of every poll cycle, LLM call, calendar/notification action. Rotates at 5 MB, keeps 5 backups (~25 MB cap). |
| `%LOCALAPPDATA%\EmailAssistant\logs\crash_<timestamp>_<pid>.txt` | Full Python traceback for any UNCAUGHT exception (incl. import-time crashes from missing PyInstaller hidden imports). Keeps the 20 most recent. |
| `%TEMP%\Setup Log YYYY-MM-DD #NNN.txt` | Inno Setup install transcript - written every time the installer runs, success or fail. |

The Start Menu group has a **View Logs** shortcut that opens
`%LOCALAPPDATA%\EmailAssistant\logs\` directly so you can grab files for a
bug report without typing the path.

To override the data dir (for a portable install, multiple mailboxes on one
laptop, or testing), set `EMAIL_ASSISTANT_DATA_DIR` before launching.

### Troubleshooting: installer is blocked by Windows

The unsigned `EmailAssistantSetup.exe` will trip Windows defenses. Symptoms
and fixes:

| Symptom | Cause | Fix |
|---|---|---|
| Blue popup: "Windows protected your PC" | Microsoft Defender SmartScreen | Click **More info** -> **Run anyway**. One-time per file. |
| "Security policy" / "Your administrator has blocked this app" / nothing happens on double-click | **Smart App Control** (Win 11) or AppLocker policy | See "Smart App Control" below. |
| File silently disappears after download or after Inno Setup extracts | Microsoft Defender / corporate AV quarantine | Add an exclusion for `%LOCALAPPDATA%\Programs\EmailAssistant` (Settings -> Privacy & security -> Windows Security -> Virus & threat protection -> Manage settings -> Exclusions). |
| Yellow MOTW warning: "The publisher could not be verified" | File downloaded from internet (Mark of the Web) | Right-click the .exe -> **Properties** -> tick **Unblock** -> OK -> then double-click. |

#### Smart App Control (Win 11 24H2+)

Smart App Control silently blocks unsigned .exe files - you may not even see
a notification. To check whether it's the culprit:

```powershell
Get-MpComputerStatus | Select-Object SmartAppControlState
```

Possible values: `On`, `Off`, `Eval`. If `On` or `Eval`, your options are:

1. **Recommended for personal use:** turn it Off in Settings -> Privacy &
   security -> Windows Security -> App & browser control -> Smart App
   Control. Note that **once turned off, you cannot turn it back on without
   reinstalling Windows.** That's a Microsoft design choice.
2. **Recommended if you want to keep it on:** code-sign the installer (see
   the next section).
3. **Workaround:** copy the project source to the target machine and run
   `bootstrap.ps1` + `python main.py --setup` instead of using the bundled
   installer. Smart App Control only blocks .exe files; running Python
   scripts directly is fine.

### Caveats

- **No code signing.** Windows SmartScreen + corporate AV may flag the unsigned
  exe. For personal use on your own laptop, "Run anyway" is fine. For wider
  distribution, get a code-signing cert (~$200/yr from a CA like SSL.com) and
  add `SignTool="..."` to the `[Setup]` section of `installer.iss`.
- **One-folder bundle, not one-file.** PyInstaller's `--onefile` mode
  re-extracts ~150 MB to `%TEMP%` on every launch - bad for a 5-min cron. The
  spec uses one-folder mode (faster, files persistent on disk inside the install).
- **Build is x64-only.** The `[Setup]` section pins
  `ArchitecturesAllowed=x64compatible`. Modern Windows is universally x64;
  drop that line if you ever need ARM64 / x86 targets.

## Telegram bot setup (recommended notification channel)

5 minutes, free forever, no phone number gymnastics.

1. Install Telegram on your phone (App Store / Play Store, free).
2. In Telegram, search **@BotFather** -> open chat -> tap **Start**.
3. Send `/newbot`. BotFather asks for:
   - A display name for your bot (e.g. `Email Assistant Bot`)
   - A username ending in `bot` (e.g. `rocky_email_bot`)
4. BotFather replies with a token like `7891234567:AAH...xyz`. **Copy it.**
5. In Telegram, search for the username you just picked -> open the chat ->
   tap **Start**. Send any message (e.g. `hi`).
6. Run the wizard - it auto-discovers your chat ID:
   ```powershell
   python main.py --setup
   ```
   Pick `[1] Telegram bot`, paste the token, the wizard polls Telegram and
   finds your chat ID automatically.

## Meta WhatsApp Cloud API setup

This is the most painful part of the whole setup. Follow it step by step;
budget ~30 min if Meta cooperates. End result: free notifications up to
1,000 conversations/month.

### Part A - create the Meta App and get a test phone number (~15 min)

1. **Sign in to Meta for Developers**
   <https://developers.facebook.com/> -> top-right "Get Started" or "Log In"
   with a Facebook account. (Yes, you need one. If your work email isn't on
   Facebook, use a personal account.)

2. **Create a Meta App**
   <https://developers.facebook.com/apps/> -> **Create app**.
   - Use case: **Other**
   - App type: **Business**
   - Display name: `Email Assistant`
   - Contact email: your email
   - Business portfolio: pick existing, or "Create new" -> name `Email Assistant`
   - Click **Create app**, complete any captcha.

3. **Add the WhatsApp product**
   On the new app's dashboard, scroll to **Add products to your app** ->
   find **WhatsApp** -> **Set up**.
   - Pick the business portfolio you just made.
   - Meta auto-creates a **WhatsApp Business Account (WABA)** and assigns
     a free **test phone number** (you'll see it on the next page).

4. **Note your IDs and the temporary token**
   Left sidebar: **WhatsApp** -> **API Setup**. Copy these into Notepad:
   - **Phone number ID** (a long number under "From" -> the test number).
     -> goes into `META_WA_PHONE_NUMBER_ID`
   - **WhatsApp Business Account ID** (top of the page) - keep handy for Part B.
   - **Temporary access token** (under "Temporary access token" - 24h validity).
     This works for testing. We'll replace it with a permanent one in Part B.

5. **Add YOUR WhatsApp number to the recipients list**
   On the same API Setup page, find the **To** dropdown ->
   **Manage phone number list** -> **Add phone number** -> enter your
   personal WhatsApp number in international format -> Meta sends you a
   verification code on WhatsApp -> paste it back. Done.
   -> this number (digits only, no +) is your `META_WA_RECIPIENT`.

6. **Smoke test from the Meta UI**
   On the API Setup page, click **Send message**. You should get a
   "hello_world" template message on your phone within seconds.
   If you do, the credentials work. If you don't, fix this BEFORE running
   the wizard - no point debugging deeper.

### Part B - get a permanent access token (~10 min)

The temporary token from Part A expires in 24 hours. For unattended use you
need a long-lived **System User** token.

1. **Open Business Settings**
   <https://business.facebook.com/settings/> -> make sure the dropdown
   (top-left) shows the same business portfolio you used in Part A.

2. **Create a System User**
   Left sidebar -> **Users** -> **System users** -> **Add**.
   - Name: `Email Assistant Service`
   - Role: **Admin**
   - Save.

3. **Assign the App and the WhatsApp Account to that System User**
   With the new system user selected:
   - **Add Assets** -> **Apps** -> select `Email Assistant` -> toggle
     "Manage app" ON -> Save.
   - **Add Assets** -> **WhatsApp Accounts** -> select your WABA ->
     toggle "Manage WhatsApp account" ON -> Save.

4. **Generate a permanent token**
   Click **Generate new token**.
   - App: `Email Assistant`
   - Token expiration: **Never**
   - Permissions to grant: tick **`whatsapp_business_messaging`** and
     **`whatsapp_business_management`** -> Generate.
   - **Copy the token immediately** (a very long string starting with `EAA...`).
     You can't see it again. -> goes into `META_WA_ACCESS_TOKEN`.

5. **Run the wizard with these values**
   ```powershell
   python main.py --setup
   ```
   Choose option `1` (Meta WhatsApp), paste the Phone Number ID, paste the
   permanent token, paste your WhatsApp number.

### Part C (optional) - approve a Message Template (~10 min + Meta review)

Without a template, the agent can only send notifications within the WhatsApp
**24-hour customer service window**: i.e., for 24 hours after you've messaged
the bot. Outside that window, free-form messages are silently dropped by Meta.

For continuous unattended notifications, set up a Message Template:

1. <https://business.facebook.com/wa/manage/message-templates/> -> **Create template**.
2. Category: **Utility** (the cheapest category; alerts about user's own data fit here).
3. Name: `email_assistant_alert` (lowercase, snake_case).
4. Language: English (US).
5. Body: exactly this text (one variable):
   ```
   Email Assistant alert: {{1}}
   ```
6. Click "Add a sample" -> for `{{1}}` paste any example like "Meeting tomorrow at 3pm".
7. Submit. Meta typically approves utility templates in a few minutes to a few hours.
8. Once approved, set in `.env` (or via the wizard):
   ```
   META_WA_TEMPLATE_NAME=email_assistant_alert
   META_WA_TEMPLATE_LANGUAGE=en_US
   ```
9. The agent now tries text first; on a 24h-window error it automatically
   falls back to this template. You'll get notifications regardless of when
   you last messaged the bot.

### Production-ready Meta setup (only if you outgrow free tier)

The free tier is 1,000 service conversations / month. If you exceed that
or want to send to numbers OTHER than the verified recipients you added in
Part A.5, you need to:

1. Add a **payment method** in WhatsApp Account -> Payment methods.
2. Move the app out of **Development mode** (App Dashboard -> top toggle).
3. Provide a Privacy Policy URL.
4. Use your **own** WhatsApp Business phone number (not the Meta test number).

For personal use this is overkill. Skip it.

## Gmail / Google Workspace setup

This is the equivalent of the "Microsoft Entra app registration" for Google.
Budget ~10 min. End result: a `client_secret.json` file the agent uses to
sign you in to Gmail and Google Calendar.

1. **Create a Google Cloud project** (free)
   <https://console.cloud.google.com/projectcreate> -> name `Email Assistant` -> Create.
   Make sure the new project is selected in the top dropdown.

2. **Enable the Gmail and Calendar APIs**
   - <https://console.cloud.google.com/apis/library/gmail.googleapis.com> -> **Enable**
   - <https://console.cloud.google.com/apis/library/calendar-json.googleapis.com> -> **Enable**

3. **Configure the OAuth consent screen**
   <https://console.cloud.google.com/apis/credentials/consent>
   - User type: **External** (unless you have Google Workspace and want Internal).
   - App name: `Email Assistant`. User support email: yours.
   - Developer contact: yours. Save.
   - **Scopes** page: click "Add or remove scopes", search for and add:
     - `https://www.googleapis.com/auth/gmail.modify`
     - `https://www.googleapis.com/auth/calendar`
   - **Test users** page: add your own Google account email. Save.
   - (You don't need to "publish" the app for personal use - leaving it in
     Testing mode is fine, but Google will require you to re-consent every
     7 days unless you publish.)

4. **Create the OAuth client**
   <https://console.cloud.google.com/apis/credentials> -> **+ Create credentials** ->
   **OAuth client ID**.
   - Application type: **Desktop app**
   - Name: `Email Assistant Desktop`
   - Click Create -> popup shows Client ID + secret.
   - Click **Download JSON** -> save the file as `client_secret.json` in the
     project root (`C:\Users\rnuduru1\eMail assistant\client_secret.json`).
     This file is gitignored.

5. **Run the wizard**
   ```powershell
   python main.py --setup
   ```
   Pick **[2] Google / Gmail** when asked for the email provider. The wizard
   verifies the JSON exists, then:

   ```powershell
   python main.py --auth
   ```

   Opens your browser; sign in to your Gmail account; click "Continue" past
   the unverified-app warning (because you didn't publish the app); approve
   the Gmail + Calendar scopes. Done. Refresh token is cached to
   `google_token.json` (also gitignored).

### Optional: publishing the OAuth app

If the 7-day re-consent in Testing mode annoys you:
- OAuth consent screen -> **Publish app** -> confirm.
- For personal use Google does NOT require verification when you only request
  the scopes you have (gmail.modify and calendar count as "sensitive" but you
  can use them in Production mode for personal accounts indefinitely; only
  reverification is needed if you add restricted scopes).

## Bid request handling

Out of the box, the agent recognizes construction-style bid invitations and
extracts the full structure of an invitation-to-bid (ITB / RFP / RFQ).

### What gets extracted

For every bid email, the agent's LLM pulls out:

| Field | What it captures |
|---|---|
| **Project name** | Best-guess from subject + body. |
| **Project location** | City, address, or region. |
| **Project type / scope** | Trade or scope, e.g. "Mechanical TI", "Ground-up multifamily", "Site demo". |
| **Reference number** | Solicitation #, IFB #, RFP #, project # - e.g. "405-26R0015165", "RFP-2026-001". |
| **Bid scope summary** | One-sentence description of the work being bid. |
| **Proposal due date** | When OUR bid must be submitted. If only a date is given, defaults to 17:00 local. |
| **Submission method** | "email", "online portal", "in-person", "BuildingConnected", "Procore", etc. |
| **RFI cutoff** | Last day to send questions to the GC, distinct from bid due. |
| **Pre-bid meeting / walkthrough** | Date, time, and end (also called pre-bid conference, jobwalk, site visit). |
| **Pre-bid mandatory flag** | True only when the email explicitly says "mandatory" / "required". |
| **Pre-bid meeting location** | Physical address for in-person walkthroughs. |
| **Pre-bid virtual link** | Teams / Zoom / Meet / Webex URL for virtual or hybrid meetings. |
| **Pre-bid contact** | Site-visit-only contact, when the email lists a different person from the bid contact (common in government IFBs). |
| **Bid contact** | Primary contact: questions, contract administrator, where to send the bid. |

### What gets calendared

Calendar events are created automatically when `bid_confidence >= AUTO_BLOCK_CONFIDENCE`:

| Calendar event | When it's created |
|---|---|
| `BID DUE: [<ref>] <project>` at the due time | `AUTO_BLOCK_BID_REMINDER=1` and the proposal due date is in the future. |
| `PRE-BID MANDATORY WALKTHROUGH: [<ref>] <project>` | Email mentions a pre-bid meeting AND it's flagged mandatory. Confirmed (not tentative). |
| `PRE-BID WALKTHROUGH: [<ref>] <project>` | Email mentions a non-mandatory walkthrough. Tentative. |

The `[<ref>]` prefix is included automatically when the LLM finds a
solicitation / IFB / RFP number in the email. Calendar event bodies always
include both `bid_contact` and (when distinct) `pre_bid_contact`.

The pre-bid event's location is set to the physical address when in-person, or
to the virtual meeting link when virtual. If both are present (hybrid), the
address goes in `location` and the link is included in the event body.

### What goes in the notification

The notification text is built by the LLM, then the agent appends action notes
in pipe-separated form. Example for a bid email with a mandatory walkthrough,
RFIs, and document capture:

```
[BID] Acme GC: Cedar Park OB - bid due Fri May 15 5pm | bid deadline blocked (Fri May 15 17:00) | MANDATORY pre-bid blocked (Tue May 12 10:00) | RFIs due Wed May 13 17:00 | 12 doc(s) saved -> https://onedrive...
```

### Targeting bids addressed to you

The agent uses your `COMPANY_NAME` and `COMPANY_ALIASES` so the LLM
distinguishes a bid invite specifically addressed to you ("BPC, please bid
this project") from a generic blast to 100 subs. The targeted version gets a
much higher `bid_confidence`, which is what gates auto-blocking the calendar.

### Tweak the behavior

| Env var | Effect |
|---|---|
| `COMPANY_NAME=Blueprint Constructs` | Full company name. Helps the LLM recognize "to/for/with us". |
| `COMPANY_ALIASES=BPC,Blueprint` | Acronyms / nicknames. Comma-separated. |
| `AUTO_BLOCK_BID_REMINDER=1` | `0` to disable auto-creating bid deadline reminders (pre-bid walkthroughs are still blocked). |
| `AUTO_BLOCK_CONFIDENCE=0.75` | Same threshold gates meeting blocking, bid reminders, pre-bid blocking, and document capture. |

### Test the analyzer against a saved email (no side effects)

Save a real bid email body (with optional `Subject:`/`From:`/`To:`/`Date:`
headers at the top, separated from the body by a blank line) to a `.txt`
file, then run the analyzer locally and inspect what it would extract.
This does NOT touch your mailbox, calendar, or notification channels - it
only calls your configured LLM.

```powershell
# Provided sample email (Texas DPS Mt Pleasant IFB):
python tools\test_analyze.py samples\dps_mt_pleasant_ifb.txt --bid-only

# Your own email saved to a file:
python tools\test_analyze.py path\to\my_bid_email.txt

# Override metadata if your file has no headers:
python tools\test_analyze.py body_only.txt --subject "ITB - Project XYZ" --from "gc@example.com"
```

The `--bid-only` flag prints just the bid-related fields. Without it, you
get the full `Analysis` dump including meeting fields, urgency, and the
notification text the agent would send. Useful for prompt tuning before
pointing the agent at production mail.

## Bid document capture

When the agent flags a high-confidence bid request, it can also pull the
attached plans / specs and any document links in the body straight into your
cloud drive so they're ready when you sit down to estimate.

What gets captured:

| Source | Behavior |
|---|---|
| Email attachments | Downloaded directly via Graph (Outlook) or Gmail API. |
| URLs in the email body | Extracted, classified, and downloaded if they look like documents. |
| Known document hosts | Dropbox, WeTransfer, SharePoint, OneDrive share links, Google Drive, Box. |
| Authenticated bid portals | **Skipped automatically** with a log line. Procore, BuildingConnected, PlanGrid / Autodesk Construction Cloud, iSqFt / ConstructConnect, SmartBidNet, Bluebeam Studio require login and aren't auto-fetchable. The link is still surfaced in your notification so you can open it manually. |

Where files land:

- `EMAIL_PROVIDER=outlook` -> **OneDrive** under `Email Assistant/Bids/<Project Name>/`
- `EMAIL_PROVIDER=gmail`  -> **Google Drive** under the same folder layout (drive.file scope, so the agent only sees files it created)

Folder names are taken from the LLM's extracted `bid_project_name` (falling
back to the email subject), then sanitized to be filesystem-safe (no
`/ \ : * ? " < > |`, no trailing dots, capped at 120 chars).

The notification text has `| N doc(s) saved -> <folder url>` appended when
files are captured, so you can jump straight to them from Telegram /
WhatsApp.

Tweak the behavior:

| Env var | Effect |
|---|---|
| `AUTO_DOWNLOAD_BID_DOCS=1` | `0` to disable document capture entirely. |
| `BID_DOCS_BASE_FOLDER=Email Assistant/Bids` | Root folder under your drive. |
| `DOWNLOAD_DOCS_FROM_LINKS=1` | `0` to capture attachments only (no body URLs). |
| `MAX_DOWNLOAD_MB=200` | Per-file size cap. Files larger than this are skipped + logged. |

The Outlook setup script (`scripts/setup_entra.ps1`) now requests
`Files.ReadWrite` automatically. Gmail's OAuth flow now requests
`drive.file`. If you ran the setup before this feature shipped, simply
re-run the OAuth step (`python main.py --auth`) to pick up the new scopes
and re-consent.

## Configuration reference

All knobs live in `.env` (see `.env.example` for documented examples):

| Key | Purpose |
|-----|---------|
| `MAILBOX_ADDRESS` | The mailbox being monitored. Used in prompts + filtering self from attendees. |
| `USER_TIMEZONE` | IANA timezone (e.g. `America/New_York`, `Europe/London`, `Asia/Singapore`). Used for parsing relative times and creating calendar events. The setup wizard auto-detects this from your machine. |
| `DEFAULT_MEETING_DURATION_MINUTES` | Used when an email proposes a start time but no end. |
| `MS_CLIENT_ID`, `MS_TENANT_ID` | App-registration identifiers. |
| `COMPANY_NAME`, `COMPANY_ALIASES` | Your company's full name and short aliases. Helps detect bid invitations addressed to you. |
| `AUTO_BLOCK_BID_REMINDER` | `1` (default) to auto-create a calendar reminder at the bid due time; `0` to disable. |
| `EMAIL_PROVIDER` | `outlook` or `gmail`. Determines which providers are used. |
| `LLM_PROVIDER` | `openai`, `azure_openai`, `github_models`, `ollama`, or `openai_compat`. |
| `LLM_MODEL` | Provider-specific model name. For Azure, this is the *deployment* name. |
| `LLM_BASE_URL` | Override default endpoint. Required for `openai_compat`. |
| `NOTIFY_CHANNELS` | Comma-separated subset of `telegram,whatsapp_meta,whatsapp,sms`. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram bot credentials. Wizard auto-discovers chat ID. |
| `META_WA_PHONE_NUMBER_ID` | Numeric ID from Meta App -> WhatsApp -> API Setup. NOT the phone number itself. |
| `META_WA_ACCESS_TOKEN` | Long-lived System User token (`whatsapp_business_messaging` + `whatsapp_business_management`). |
| `META_WA_RECIPIENT` | Your WhatsApp number, digits only (no `+`). E.g. `15125551234`. |
| `META_WA_TEMPLATE_NAME` | Optional: name of an APPROVED Meta template used as fallback when WhatsApp's 24-hour session window is closed. |
| `AUTO_BLOCK_CONFIDENCE` | Calendar is auto-blocked only when the LLM's confidence >= this threshold. Default 0.75. |
| `AUTO_DOWNLOAD_BID_DOCS` | `1` (default) to copy bid attachments + document links into OneDrive/Drive; `0` to disable. |
| `BID_DOCS_BASE_FOLDER` | Cloud-drive folder under which per-project subfolders are created. Default `Email Assistant/Bids`. |
| `DOWNLOAD_DOCS_FROM_LINKS` | `1` (default) to download document URLs found in the email body. `0` for attachments only. |
| `MAX_DOWNLOAD_MB` | Per-file size cap for the doc capture step. Default 200. |
| `POLL_INTERVAL_SECONDS` | Polling cadence for the long-running mode. |
| `INITIAL_LOOKBACK_MINUTES` | On startup, scan unread mail received in this window so you don't lose anything across restarts. |

## Behavior summary

For every unread email since the last successful poll:

1. Skip if its message ID is already in the local SQLite state (`state.db`).
2. Send subject + body + sender + your timezone to the configured LLM with a
   strict JSON schema.
3. If `is_meeting_request` and `meeting_confidence >= AUTO_BLOCK_CONFIDENCE`,
   create a calendar event via the configured calendar provider (tentative
   below 0.9, busy at >= 0.9).
4. If `is_bid_request` and `bid_confidence >= AUTO_BLOCK_CONFIDENCE`:
   - create a `BID DUE: <project>` reminder at the bid deadline (when known
     and `AUTO_BLOCK_BID_REMINDER=1`);
   - if `AUTO_DOWNLOAD_BID_DOCS=1`, copy attachments and any document links
     in the body into OneDrive (Outlook) or Google Drive (Gmail) under
     `<BID_DOCS_BASE_FOLDER>/<sanitized project name>/`. Authenticated bid
     portals (Procore / BuildingConnected / iSqFt / etc.) are skipped.
5. Send a one-line notification via every enabled channel
   (Telegram / WhatsApp / SMS), summarizing what was found and done.
6. Mark the message read and record the result in SQLite.

If anything in steps 3-5 fails, the loop logs the error and continues -
de-duplication via SQLite means you won't get spammed on retries, and the email
stays unread until it's fully processed.

## Tests

```powershell
pytest -q
```

Tests focus on the deterministic parts of the analyzer (window derivation,
schema validation, fallback). Provider integration tests would require live
credentials and are intentionally not included.

## Security notes

- `.env`, `token_cache.bin`, and `state.db` are all in `.gitignore`. Never
  commit them.
- The agent uses **delegated** permissions tied to the signed-in user, so it
  can only access mail / calendar that user already has access to.
- Twilio errors and Graph errors are caught at the cycle level; one bad email
  cannot kill the agent.

## Roadmap / things to extend

- Swap OpenAI for Azure OpenAI or local Ollama (just edit `analyzer.py`).
- Add a Gmail provider next to the Outlook one (mirror `providers/outlook.py`).
- Add a "do not disturb" window (skip notifications between e.g. 10pm-6am local).
- Reply drafts: have the LLM also generate a polished reply and save it to your
  Outlook Drafts folder via `POST /me/messages`.

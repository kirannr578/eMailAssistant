# Email Assistant

An autonomous agent that monitors any Microsoft 365 mailbox, reads each new
unread email with an LLM, and:

- Detects whether it's a **meeting request** (extracting the proposed time).
- For high-confidence meeting requests, **blocks your Outlook calendar**
  automatically (tentative or busy depending on confidence).
- Sends a short **SMS and/or WhatsApp notification** via Twilio summarizing the
  email and the action taken.
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
├── bootstrap.ps1           # Installs Python, creates venv, pip installs
├── main.py                 # Entry point: --setup | --auth | --once | (default loop)
├── setup_wizard.py         # Interactive .env builder with live credential validation
├── config.py               # Typed Settings loaded from .env
├── analyzer.py             # OpenAI JSON-mode analysis + Pydantic schema
├── state.py                # SQLite dedup of processed messages
├── providers/
│   ├── ms_graph_auth.py    # MSAL device-code OAuth + token cache
│   ├── outlook.py          # Graph: list unread, mark read
│   ├── calendar.py         # Graph: create event
│   └── notifier.py         # Twilio SMS + WhatsApp (fail-soft per channel)
├── scripts/
│   ├── setup_entra.ps1     # Auto-create Entra app via Azure CLI
│   └── install_task.ps1    # Register / uninstall the Windows Scheduled Task
├── tests/test_analyzer.py
├── requirements.txt
├── .env.example
└── README.md
```

## Quick start (Windows / PowerShell)

The whole thing is **5 commands + paste 4 secrets**. Helper scripts handle
Python install, venv, dependencies, Entra app registration, and Task Scheduler
registration. The only things you must do yourself are sign up for OpenAI and
Twilio (their security models prevent automation).

### Prerequisites you set up in a browser

You need **OpenAI** (for the LLM) and **one notification channel**. Pick one:

1. **OpenAI** (~3 min): create a key at <https://platform.openai.com/api-keys>
   and add ~$5 of credit at the billing page. `gpt-4o-mini` costs ~$0.0001 per
   email -> $5 lasts effectively forever.
2. **Notification channel - pick ONE** (the agent supports any combination):
   - **Meta WhatsApp Cloud API direct** (~30 min, free up to 1000/mo) -
     see "Meta WhatsApp Cloud API setup" section below for the full walkthrough.
   - **Twilio WhatsApp sandbox** (~5 min, free for testing) -
     <https://www.twilio.com/try-twilio>, then Console -> Messaging -> Try it out.
     Note the WhatsApp 24-hour session-window limit applies.
   - **Twilio SMS** (~10 min, real SMS, ~$0.008/msg after $15 trial credit) -
     same Twilio signup; buy an SMS-capable number.

### One-time setup on the laptop

```powershell
# Replace this with the path where you cloned the repo
cd "C:\path\to\eMailAssistant"

# 1) Install Python (if needed), create venv, install deps
.\bootstrap.ps1

# 2) Auto-create the Entra app registration (uses Azure CLI)
#    Installs Azure CLI via winget if needed; opens browser once for `az login`.
#    Prints MS_CLIENT_ID and MS_TENANT_ID at the end.
.\scripts\setup_entra.ps1

# 3) Interactive wizard - paste your OpenAI key, Twilio creds, and the IDs
#    from step 2. The wizard validates each one with a live API call.
python main.py --setup

# 4) One-time Outlook sign-in (device-code flow opens a URL in your browser)
python main.py --auth

# 5) Smoke test
python main.py --once
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
   `Mail.ReadWrite`, `Calendars.ReadWrite`, `User.Read`, `offline_access`.
   Click "Grant admin consent" (admin only).
5. Copy the Application (client) ID and your tenant ID. Use them in step 3 above.

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

## Configuration reference

All knobs live in `.env` (see `.env.example` for documented examples):

| Key | Purpose |
|-----|---------|
| `MAILBOX_ADDRESS` | The mailbox being monitored. Used in prompts + filtering self from attendees. |
| `USER_TIMEZONE` | IANA timezone (e.g. `America/New_York`, `Europe/London`, `Asia/Singapore`). Used for parsing relative times and creating calendar events. The setup wizard auto-detects this from your machine. |
| `DEFAULT_MEETING_DURATION_MINUTES` | Used when an email proposes a start time but no end. |
| `MS_CLIENT_ID`, `MS_TENANT_ID` | App-registration identifiers. |
| `OPENAI_MODEL` | Defaults to `gpt-4o-mini` (cheap + plenty smart for triage). Swap for `gpt-4.1` or similar. |
| `NOTIFY_CHANNELS` | Comma-separated subset of `sms,whatsapp,whatsapp_meta`. |
| `META_WA_PHONE_NUMBER_ID` | Numeric ID from Meta App -> WhatsApp -> API Setup. NOT the phone number itself. |
| `META_WA_ACCESS_TOKEN` | Long-lived System User token (`whatsapp_business_messaging` + `whatsapp_business_management`). |
| `META_WA_RECIPIENT` | Your WhatsApp number, digits only (no `+`). E.g. `15125551234`. |
| `META_WA_TEMPLATE_NAME` | Optional: name of an APPROVED Meta template used as fallback when WhatsApp's 24-hour session window is closed. |
| `AUTO_BLOCK_CONFIDENCE` | Calendar is auto-blocked only when the LLM's confidence >= this threshold. Default 0.75. |
| `POLL_INTERVAL_SECONDS` | Polling cadence for the long-running mode. |
| `INITIAL_LOOKBACK_MINUTES` | On startup, scan unread mail received in this window so you don't lose anything across restarts. |

## Behavior summary

For every unread email since the last successful poll:

1. Skip if its message ID is already in the local SQLite state (`state.db`).
2. Send subject + body + sender + your timezone to OpenAI with a strict JSON schema.
3. If `is_meeting_request` and `confidence >= AUTO_BLOCK_CONFIDENCE`, create an
   Outlook event via Graph (tentative below 0.9, busy at >= 0.9).
4. Send a one-line SMS and/or WhatsApp notification via Twilio with the summary
   and what was done.
5. Mark the message read via Graph and record the result in SQLite.

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

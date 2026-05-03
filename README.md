# Email Assistant

An autonomous agent that monitors `rocky@blueprintconstructs.com` (or any
Microsoft 365 mailbox), reads each new unread email with an LLM, and:

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

## Quick start (Windows / PowerShell)

### 1. Prerequisites

- Python 3.11+
- A Microsoft Entra **app registration** (free)
- An OpenAI API key
- A Twilio account (trial works) for SMS / WhatsApp

### 2. Microsoft Entra app registration (one-time)

1. Go to <https://entra.microsoft.com> -> **App registrations** -> **New registration**.
2. Name: `Email Assistant`.
3. **Supported account types:** "Accounts in any organizational directory and
   personal Microsoft accounts" (or whatever matches `blueprintconstructs.com`).
4. Skip the Redirect URI. Click **Register**.
5. On the new app: **Authentication** -> **Advanced settings** ->
   "Allow public client flows" = **Yes**. Save.
6. **API permissions** -> **Add a permission** -> Microsoft Graph -> **Delegated** ->
   add: `Mail.ReadWrite`, `Calendars.ReadWrite`, `User.Read`, `offline_access`.
   Click **Grant admin consent** (or your tenant admin does).
7. Copy the **Application (client) ID** -> goes into `.env` as `MS_CLIENT_ID`.
8. For `MS_TENANT_ID`, use:
   - your tenant GUID for a single org,
   - `common` for personal + work accounts,
   - `consumers` for personal Microsoft accounts only.

### 3. Install and configure

```powershell
cd "C:\Users\rnuduru1\eMail assistant"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env
notepad .env   # fill in real values
```

Required values in `.env`: `MS_CLIENT_ID`, `OPENAI_API_KEY`, plus Twilio
credentials (`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_*`,
`NOTIFY_TO_*`). See `.env.example` for the full list with comments.

### 4. One-time sign-in (device-code flow)

```powershell
python main.py --auth
```

The console prints something like:

```
To sign in, use a web browser to open the page https://microsoft.com/devicelogin
and enter the code ABCD-EFGH to authenticate.
```

Open that URL on any device, sign in as `rocky@blueprintconstructs.com`, and
approve the requested permissions. The refresh token is cached to
`token_cache.bin` and reused silently afterwards.

### 5. Run the agent

Foreground polling loop (Ctrl+C to stop):

```powershell
python main.py
```

One-shot run (process current unread, then exit) - good for Task Scheduler:

```powershell
python main.py --once
```

### 6. Run unattended via Windows Task Scheduler

1. Open **Task Scheduler** -> **Create Task**.
2. **General**: name "Email Assistant", "Run whether user is logged on or not",
   check "Run with highest privileges".
3. **Triggers**: New -> Daily, repeat task every 5 minutes for a duration of 1 day.
4. **Actions**: New -> Start a program:
   - Program/script: `C:\Users\rnuduru1\eMail assistant\.venv\Scripts\python.exe`
   - Arguments: `main.py --once`
   - Start in: `C:\Users\rnuduru1\eMail assistant`
5. **Conditions**: uncheck "Start the task only if the computer is on AC power".
6. Save (you'll be prompted for your Windows password).

## Configuration reference

All knobs live in `.env` (see `.env.example` for documented examples):

| Key | Purpose |
|-----|---------|
| `MAILBOX_ADDRESS` | The mailbox being monitored. Used in prompts + filtering self from attendees. |
| `USER_TIMEZONE` | IANA timezone (e.g. `America/Chicago`). Used for parsing relative times and creating calendar events. |
| `DEFAULT_MEETING_DURATION_MINUTES` | Used when an email proposes a start time but no end. |
| `MS_CLIENT_ID`, `MS_TENANT_ID` | App-registration identifiers. |
| `OPENAI_MODEL` | Defaults to `gpt-4o-mini` (cheap + plenty smart for triage). Swap for `gpt-4.1` or similar. |
| `NOTIFY_CHANNELS` | Comma-separated subset of `sms,whatsapp`. |
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

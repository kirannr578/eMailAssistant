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

### Prerequisites you set up in a browser (~10 min)

1. **OpenAI:** create a key at <https://platform.openai.com/api-keys> and add
   ~$5 of credit at the billing page. `gpt-4o-mini` costs ~$0.0001 per email.
2. **Twilio:** sign up at <https://www.twilio.com/try-twilio> (trial gives ~$15
   credit). Buy an SMS-capable number; for WhatsApp opt your phone into the
   free sandbox via Console -> Messaging -> Try it out.

### One-time setup on the laptop

```powershell
cd "C:\Users\rnuduru1\eMail assistant"

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

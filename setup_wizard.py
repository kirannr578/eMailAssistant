"""Interactive .env setup wizard.

Walks the user through every required credential, validates each as we go
(real API calls where cheap), and writes a complete .env at the end.

Run via: python main.py --setup
"""
from __future__ import annotations

import getpass
import re
import sys
from pathlib import Path


def _detect_local_timezone() -> str:
    """Best-effort IANA timezone detection. Falls back to UTC if unknown."""
    try:
        from tzlocal import get_localzone_name  # type: ignore
        return get_localzone_name()
    except Exception:
        try:
            from datetime import datetime
            tz = datetime.now().astimezone().tzinfo
            if tz is not None:
                name = str(tz)
                if "/" in name:
                    return name
        except Exception:
            pass
        return "UTC"

ENV_TEMPLATE_PATH = Path(".env.example")
ENV_PATH = Path(".env")


# ----------------------------- printing helpers -----------------------------

def _h1(text: str) -> None:
    print("\n" + "=" * 72)
    print(text)
    print("=" * 72)


def _h2(text: str) -> None:
    print("\n--- " + text + " ---")


def _info(text: str) -> None:
    print("  " + text)


def _ok(text: str) -> None:
    print("  [OK] " + text)


def _warn(text: str) -> None:
    print("  [!] " + text)


def _err(text: str) -> None:
    print("  [X] " + text)


# ----------------------------- input helpers --------------------------------

def _ask(prompt: str, *, default: str | None = None, secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            if secret:
                val = getpass.getpass(f"  {prompt}{suffix}: ").strip()
            else:
                val = input(f"  {prompt}{suffix}: ").strip()
        except EOFError:
            print()
            sys.exit(1)
        if not val and default is not None:
            return default
        if val:
            return val
        _warn("Value required.")


def _ask_yes_no(prompt: str, *, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    while True:
        ans = input(f"  {prompt} [{d}]: ").strip().lower()
        if not ans:
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False


# ----------------------------- validators -----------------------------------

_E164 = re.compile(r"^\+\d{8,15}$")


def _validate_e164(num: str) -> bool:
    return bool(_E164.match(num))


def _validate_llm(provider: str, api_key: str, model: str, base_url: str = "",
                  azure_endpoint: str = "", azure_api_version: str = "") -> bool:
    """Live-test the LLM credentials by making a tiny chat completion call."""
    try:
        from openai import AzureOpenAI, OpenAI
    except ImportError:
        _warn("openai package not installed yet; skipping live validation.")
        return True
    try:
        if provider == "azure_openai":
            client = AzureOpenAI(
                api_key=api_key,
                api_version=azure_api_version or "2024-08-01-preview",
                azure_endpoint=azure_endpoint,
            )
        else:
            defaults = {
                "github_models": "https://models.github.ai/inference",
                "ollama": "http://localhost:11434/v1",
            }
            url = base_url or defaults.get(provider) or None
            key = api_key or ("ollama" if provider == "ollama" else "")
            client = OpenAI(api_key=key, base_url=url)

        client.chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=4,
            messages=[{"role": "user", "content": "ping"}],
        )
        _ok(f"LLM ({provider}) works with model: {model}")
        return True
    except Exception as e:
        _err(f"LLM validation failed: {e}")
        return False


def _validate_twilio(sid: str, token: str) -> bool:
    try:
        from twilio.rest import Client
        from twilio.base.exceptions import TwilioRestException
    except ImportError:
        _warn("twilio package not installed yet; skipping live validation.")
        return True
    try:
        client = Client(sid, token)
        acct = client.api.accounts(sid).fetch()
        _ok(f"Twilio creds work. Account: {acct.friendly_name} ({acct.status})")
        return True
    except Exception as e:
        _err(f"Twilio validation failed: {e}")
        return False


# ----------------------------- env file io ----------------------------------

def _load_template() -> dict[str, str]:
    """Parse .env.example into a dict, preserving order via dict insertion."""
    if not ENV_TEMPLATE_PATH.exists():
        _err(f"{ENV_TEMPLATE_PATH} not found. Are you in the project root?")
        sys.exit(1)
    out: dict[str, str] = {}
    for raw_line in ENV_TEMPLATE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip()
    return out


def _write_env(values: dict[str, str]) -> None:
    """Write a clean .env preserving template comments."""
    template = ENV_TEMPLATE_PATH.read_text(encoding="utf-8").splitlines()
    out_lines: list[str] = []
    for raw in template:
        stripped = raw.strip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.partition("=")[0].strip()
            if key in values:
                out_lines.append(f"{key}={values[key]}")
                continue
        out_lines.append(raw)
    ENV_PATH.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


# ----------------------------- the wizard -----------------------------------

def run_wizard() -> int:
    _h1("Email Assistant - interactive setup")

    if ENV_PATH.exists():
        if not _ask_yes_no(".env already exists. Overwrite?", default=False):
            _info("Aborted. Existing .env left untouched.")
            return 1

    values = _load_template()

    # ---------- Mailbox + company ----------
    _h2("1/6  Mailbox + your company")
    mailbox_default = values.get("MAILBOX_ADDRESS") or None
    values["MAILBOX_ADDRESS"] = _ask(
        "Mailbox to monitor (full email)", default=mailbox_default
    )
    detected_tz = _detect_local_timezone()
    tz_default = values.get("USER_TIMEZONE") or detected_tz
    values["USER_TIMEZONE"] = _ask(
        "Your IANA timezone (e.g. America/New_York, Europe/London, Asia/Singapore)",
        default=tz_default,
    )
    _info("\nThe company you work for. Helps the LLM detect bid invitations addressed to you.")
    _info("Leave blank to skip bid-request detection tuning.")
    values["COMPANY_NAME"] = _ask(
        "Your company name (full name, e.g. 'Blueprint Constructs')",
        default=values.get("COMPANY_NAME") or "",
    )
    values["COMPANY_ALIASES"] = _ask(
        "Company aliases / acronyms, comma-separated (e.g. 'BPC,Blueprint')",
        default=values.get("COMPANY_ALIASES") or "",
    )

    # ---------- Email + Calendar provider ----------
    _h2("2/6  Email + Calendar provider")
    print()
    print("    [1] Microsoft 365 / Outlook  (Outlook + Outlook Calendar via Graph)")
    print("    [2] Google / Gmail           (Gmail + Google Calendar)")
    print()
    pick = _ask("Pick a provider", default="1").strip()
    provider = "gmail" if pick == "2" else "outlook"
    values["EMAIL_PROVIDER"] = provider

    if provider == "outlook":
        _info("If you ran scripts\\setup_entra.ps1, the values are printed at the end of that script.")
        _info("Otherwise see README section 'Microsoft Entra app registration'.")
        values["MS_CLIENT_ID"] = _ask("MS_CLIENT_ID (Application (client) ID)")
        values["MS_TENANT_ID"] = _ask(
            "MS_TENANT_ID (tenant GUID, or 'common' for personal+work, 'consumers' for personal)",
            default=values.get("MS_TENANT_ID", "common"),
        )
    else:
        _info("See README section 'Gmail / Google Workspace setup' for the Google Cloud Console steps.")
        _info("You should have downloaded an OAuth client JSON named something like client_secret_XXX.json.")
        while True:
            path = _ask(
                "GOOGLE_CLIENT_SECRETS_PATH (path to the OAuth client JSON)",
                default=values.get("GOOGLE_CLIENT_SECRETS_PATH", "client_secret.json"),
            )
            if Path(path).exists():
                values["GOOGLE_CLIENT_SECRETS_PATH"] = path
                break
            _err(f"File not found: {path}")
        values["GOOGLE_CALENDAR_ID"] = _ask(
            "GOOGLE_CALENDAR_ID ('primary' = your main calendar)",
            default=values.get("GOOGLE_CALENDAR_ID", "primary"),
        )

    # ---------- LLM (pluggable) ----------
    _h2("3/6  LLM provider (the brain that analyzes emails)")
    print()
    print("    [1] OpenAI            (paid, ~$0.0001/email; needs https://platform.openai.com key)")
    print("    [2] GitHub Models     (FREE; needs a GitHub PAT at https://github.com/settings/tokens)")
    print("    [3] Ollama (local)    (FREE; needs `ollama serve` running locally)")
    print("    [4] Azure OpenAI      (work account; needs your Azure deployment URL)")
    print("    [5] OpenAI-compatible (LM Studio, vLLM, etc.; you provide base_url)")
    print()
    pick = _ask("Pick a provider", default="1").strip()
    provider_map = {
        "1": "openai", "2": "github_models", "3": "ollama",
        "4": "azure_openai", "5": "openai_compat",
    }
    provider = provider_map.get(pick, "openai")
    values["LLM_PROVIDER"] = provider

    default_models = {
        "openai": "gpt-4o-mini", "github_models": "openai/gpt-4o-mini",
        "ollama": "llama3.1:8b", "azure_openai": "gpt-4o-mini",
        "openai_compat": "gpt-4o-mini",
    }

    while True:
        if provider == "ollama":
            api_key = ""
            base_url = _ask("LLM_BASE_URL", default="http://localhost:11434/v1")
            azure_endpoint = ""
            azure_version = ""
        elif provider == "azure_openai":
            api_key = _ask("Azure OpenAI API key", secret=True)
            azure_endpoint = _ask("AZURE_OPENAI_ENDPOINT (e.g. https://your-resource.openai.azure.com)")
            azure_version = _ask("AZURE_OPENAI_API_VERSION", default="2024-08-01-preview")
            base_url = ""
        elif provider == "openai_compat":
            api_key = _ask("API key for your OpenAI-compatible endpoint", secret=True)
            base_url = _ask("LLM_BASE_URL (e.g. http://localhost:1234/v1)")
            azure_endpoint = ""
            azure_version = ""
        elif provider == "github_models":
            _info("Create a fine-grained PAT at https://github.com/settings/tokens (no scopes needed for Models).")
            api_key = _ask("GitHub PAT (ghp_... or github_pat_...)", secret=True)
            base_url = ""
            azure_endpoint = ""
            azure_version = ""
        else:  # openai
            api_key = _ask("OpenAI API key (sk-...)", secret=True)
            base_url = ""
            azure_endpoint = ""
            azure_version = ""

        model = _ask(
            "Model name (for Azure: deployment name)",
            default=default_models.get(provider, "gpt-4o-mini"),
        )

        if _validate_llm(provider, api_key, model, base_url, azure_endpoint, azure_version):
            break
        if not _ask_yes_no("Re-enter LLM credentials?", default=True):
            break

    values["LLM_API_KEY"] = api_key
    values["LLM_MODEL"] = model
    values["LLM_BASE_URL"] = base_url
    values["AZURE_OPENAI_ENDPOINT"] = azure_endpoint
    values["AZURE_OPENAI_API_VERSION"] = azure_version

    # ---------- Notification channels ----------
    _h2("4/6  Notification channels")
    _info("Pick how you want to be notified. You can enable more than one.")
    print()
    print("    [1] Telegram bot                         (FREE, easiest, recommended)")
    print("    [2] WhatsApp via Meta Cloud API direct   (free up to 1000/mo, 30-min setup)")
    print("    [3] WhatsApp via Twilio                  (sandbox is free)")
    print("    [4] SMS via Twilio                       (real SMS, paid)")
    print()
    pick = _ask("Pick one or more (e.g. '1' or '1,2')", default="1").strip()
    chosen = {p.strip() for p in pick.split(",") if p.strip()}

    channels: list[str] = []
    telegram_needed = "1" in chosen
    meta_needed = "2" in chosen
    twilio_needed = bool(chosen & {"3", "4"})

    # Telegram first - simplest
    if telegram_needed:
        channels.append("telegram")
        _info("\nTelegram setup: in Telegram, message @BotFather, send /newbot, follow prompts.")
        _info("BotFather will give you a token like 7891234567:AAH...xyz")
        bot_token = _ask("TELEGRAM_BOT_TOKEN", secret=True)
        values["TELEGRAM_BOT_TOKEN"] = bot_token

        chat_id = ""
        if _ask_yes_no(
            "Auto-discover your chat ID? (recommended) - I'll wait while you message your bot.",
            default=True,
        ):
            _info("\n>>> Now open Telegram, find your new bot, tap Start, and send any message (e.g. 'hi') <<<")
            try:
                from providers.telegram import discover_chat_id
                chat_id = discover_chat_id(bot_token, poll_seconds=120) or ""
            except Exception as e:
                _warn(f"Auto-discovery error: {e}")
            if chat_id:
                _ok(f"Found chat ID: {chat_id}")
            else:
                _warn("Could not auto-discover chat ID within 2 minutes.")

        if not chat_id:
            _info("Find your chat ID manually: open https://api.telegram.org/bot<TOKEN>/getUpdates")
            _info("Look for 'chat':{'id': <number>, 'type':'private'}")
            chat_id = _ask("TELEGRAM_CHAT_ID")
        values["TELEGRAM_CHAT_ID"] = chat_id

    # Twilio (only if user picked SMS or Twilio WhatsApp)
    if twilio_needed:
        _info("\nTwilio creds (sign up free at https://www.twilio.com/try-twilio):")
        while True:
            sid = _ask("TWILIO_ACCOUNT_SID (starts with AC)")
            token = _ask("TWILIO_AUTH_TOKEN", secret=True)
            if _validate_twilio(sid, token):
                break
            if not _ask_yes_no("Re-enter Twilio credentials?", default=True):
                break
        values["TWILIO_ACCOUNT_SID"] = sid
        values["TWILIO_AUTH_TOKEN"] = token

        if "3" in chosen:  # SMS
            channels.append("sms")
            while True:
                from_sms = _ask("TWILIO_FROM_SMS (your Twilio number, E.164 e.g. +15125551234)")
                if _validate_e164(from_sms):
                    break
                _err("Must be E.164 format starting with + and country code.")
            while True:
                to_sms = _ask("NOTIFY_TO_SMS (your phone, E.164)")
                if _validate_e164(to_sms):
                    break
                _err("Must be E.164 format.")
            values["TWILIO_FROM_SMS"] = from_sms
            values["NOTIFY_TO_SMS"] = to_sms

        if "2" in chosen:  # Twilio WhatsApp
            channels.append("whatsapp")
            _info("Sandbox sender is whatsapp:+14155238886 (free; pre-filled).")
            from_wa = _ask(
                "TWILIO_FROM_WHATSAPP",
                default=values.get("TWILIO_FROM_WHATSAPP", "whatsapp:+14155238886"),
            )
            while True:
                to_wa_raw = _ask("Your WhatsApp phone (E.164, e.g. +15125551234) - 'whatsapp:' prefix added automatically")
                base = to_wa_raw.replace("whatsapp:", "").strip()
                if _validate_e164(base):
                    to_wa = f"whatsapp:{base}"
                    break
                _err("Must be E.164 format.")
            values["TWILIO_FROM_WHATSAPP"] = from_wa
            values["NOTIFY_TO_WHATSAPP"] = to_wa

    # Meta WhatsApp Cloud API direct
    if meta_needed:
        channels.append("whatsapp_meta")
        _info("\nMeta WhatsApp Cloud API direct (see README -> 'Meta WhatsApp Cloud API setup').")
        _info("You'll need: Phone Number ID, long-lived Access Token, your own WhatsApp number.")
        values["META_WA_PHONE_NUMBER_ID"] = _ask(
            "META_WA_PHONE_NUMBER_ID (numeric, from Meta App Dashboard -> WhatsApp -> API Setup)"
        )
        values["META_WA_ACCESS_TOKEN"] = _ask(
            "META_WA_ACCESS_TOKEN (long-lived System User token)", secret=True
        )
        while True:
            recipient_raw = _ask(
                "Your WhatsApp number in E.164 (e.g. +15125551234) - we'll strip the '+'"
            )
            recipient_clean = recipient_raw.lstrip("+").strip()
            if recipient_clean.isdigit() and 8 <= len(recipient_clean) <= 15:
                values["META_WA_RECIPIENT"] = recipient_clean
                break
            _err("Must be digits only after '+', between 8 and 15 long.")
        if _ask_yes_no(
            "Configure a fallback Message Template now (lets notifications work outside the WhatsApp 24h window)?",
            default=False,
        ):
            values["META_WA_TEMPLATE_NAME"] = _ask("META_WA_TEMPLATE_NAME (must be APPROVED in Meta)")
            values["META_WA_TEMPLATE_LANGUAGE"] = _ask(
                "META_WA_TEMPLATE_LANGUAGE",
                default=values.get("META_WA_TEMPLATE_LANGUAGE", "en_US"),
            )
        else:
            _warn("OK. Notifications will only be delivered within the 24h WhatsApp session window.")
            _warn("If they stop arriving, message the bot once or set a template later.")

    if not channels:
        _warn("No notification channels selected; agent will still analyze + block calendar.")
    values["NOTIFY_CHANNELS"] = ",".join(channels)

    # ---------- Bid document capture ----------
    _h2("5/6  Bid document capture (auto-download to OneDrive / Google Drive)")
    cloud_label = "OneDrive" if provider == "outlook" else "Google Drive"
    _info(f"\nWhen the agent flags a bid email, it can copy attachments + document")
    _info(f"links straight into {cloud_label} so they're ready to estimate.")
    _info("Authenticated portals (Procore, BuildingConnected, iSqFt, etc.) are")
    _info("auto-detected and skipped - those require login to download.\n")
    if _ask_yes_no(f"Enable bid document capture to {cloud_label}?", default=True):
        values["AUTO_DOWNLOAD_BID_DOCS"] = "1"
        values["BID_DOCS_BASE_FOLDER"] = _ask(
            f"Base folder in {cloud_label} (per-project subfolders are auto-created)",
            default=values.get("BID_DOCS_BASE_FOLDER", "Email Assistant/Bids"),
        )
        if _ask_yes_no(
            "Also download document URLs found in the email body? "
            "(useful for SharePoint / Dropbox / WeTransfer links)",
            default=True,
        ):
            values["DOWNLOAD_DOCS_FROM_LINKS"] = "1"
        else:
            values["DOWNLOAD_DOCS_FROM_LINKS"] = "0"
        values["MAX_DOWNLOAD_MB"] = _ask(
            "Max per-file size to download (MB). Larger files are skipped + logged.",
            default=values.get("MAX_DOWNLOAD_MB", "200"),
        )
    else:
        values["AUTO_DOWNLOAD_BID_DOCS"] = "0"
        _warn("Document capture disabled. Re-enable later by setting AUTO_DOWNLOAD_BID_DOCS=1 in .env.")

    # ---------- Agent behavior ----------
    _h2("6/6  Agent behavior (defaults are sensible)")
    values["POLL_INTERVAL_SECONDS"] = _ask(
        "Polling interval in seconds",
        default=values.get("POLL_INTERVAL_SECONDS", "60"),
    )
    values["AUTO_BLOCK_CONFIDENCE"] = _ask(
        "Confidence threshold to auto-block calendar / save bid docs (0.0-1.0)",
        default=values.get("AUTO_BLOCK_CONFIDENCE", "0.75"),
    )
    values["DEFAULT_MEETING_DURATION_MINUTES"] = _ask(
        "Default meeting duration in minutes (when email gives no end time)",
        default=values.get("DEFAULT_MEETING_DURATION_MINUTES", "30"),
    )

    _write_env(values)
    _h1(f"Wrote {ENV_PATH.resolve()}")
    _info("Next steps:")
    auth_provider = "Outlook" if provider == "outlook" else "Google"
    file_scope = "Files.ReadWrite (OneDrive)" if provider == "outlook" else "drive.file (Drive)"
    _info(f"  1) python main.py --auth     ({auth_provider} sign-in - includes {file_scope} consent)")
    _info("  2) python main.py --once     (process current unread + exit; great smoke test)")
    _info("  3) python main.py            (run the polling loop)")
    _info("  4) .\\scripts\\install_task.ps1   (optional: register a Windows Scheduled Task)")
    return 0

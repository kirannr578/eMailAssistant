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


def _validate_openai(api_key: str, model: str) -> bool:
    try:
        from openai import OpenAI
    except ImportError:
        _warn("openai package not installed yet; skipping live validation.")
        return True
    try:
        client = OpenAI(api_key=api_key)
        # Cheapest possible call: list a single model.
        client.models.list()
        _ok(f"OpenAI key works. Will use model: {model}")
        return True
    except Exception as e:
        _err(f"OpenAI validation failed: {e}")
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

    # ---------- Mailbox ----------
    _h2("1/5  Mailbox")
    values["MAILBOX_ADDRESS"] = _ask(
        "Mailbox to monitor (full email)", default=values.get("MAILBOX_ADDRESS")
    )
    values["USER_TIMEZONE"] = _ask(
        "Your IANA timezone (e.g. America/Chicago, America/New_York)",
        default=values.get("USER_TIMEZONE", "America/Chicago"),
    )

    # ---------- Microsoft Graph ----------
    _h2("2/5  Microsoft Graph (Outlook + Calendar)")
    _info("If you ran scripts\\setup_entra.ps1, the values are printed at the end of that script.")
    _info("Otherwise see README section 'Microsoft Entra app registration'.")
    values["MS_CLIENT_ID"] = _ask("MS_CLIENT_ID (Application (client) ID)")
    values["MS_TENANT_ID"] = _ask(
        "MS_TENANT_ID (tenant GUID, or 'common' for personal+work, 'consumers' for personal)",
        default=values.get("MS_TENANT_ID", "common"),
    )

    # ---------- OpenAI ----------
    _h2("3/5  OpenAI (LLM analysis)")
    _info("Get a key at https://platform.openai.com/api-keys (need a small billing balance).")
    while True:
        openai_key = _ask("OPENAI_API_KEY (starts with sk-)", secret=True)
        model = _ask("OPENAI_MODEL", default=values.get("OPENAI_MODEL", "gpt-4o-mini"))
        if _validate_openai(openai_key, model):
            break
        if not _ask_yes_no("Re-enter OpenAI credentials?", default=True):
            break
    values["OPENAI_API_KEY"] = openai_key
    values["OPENAI_MODEL"] = model

    # ---------- Twilio ----------
    _h2("4/5  Twilio (SMS + WhatsApp)")
    _info("Sign up at https://www.twilio.com/try-twilio - trial gives ~$15 credit.")
    while True:
        sid = _ask("TWILIO_ACCOUNT_SID (starts with AC)")
        token = _ask("TWILIO_AUTH_TOKEN", secret=True)
        if _validate_twilio(sid, token):
            break
        if not _ask_yes_no("Re-enter Twilio credentials?", default=True):
            break
    values["TWILIO_ACCOUNT_SID"] = sid
    values["TWILIO_AUTH_TOKEN"] = token

    channels: list[str] = []
    if _ask_yes_no("Send SMS notifications?", default=True):
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

    if _ask_yes_no("Send WhatsApp notifications?", default=True):
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

    if not channels:
        _warn("No notification channels selected; agent will still analyze + block calendar.")
    values["NOTIFY_CHANNELS"] = ",".join(channels)

    # ---------- Agent behavior ----------
    _h2("5/5  Agent behavior (defaults are sensible)")
    values["POLL_INTERVAL_SECONDS"] = _ask(
        "Polling interval in seconds",
        default=values.get("POLL_INTERVAL_SECONDS", "60"),
    )
    values["AUTO_BLOCK_CONFIDENCE"] = _ask(
        "Confidence threshold to auto-block calendar (0.0-1.0)",
        default=values.get("AUTO_BLOCK_CONFIDENCE", "0.75"),
    )
    values["DEFAULT_MEETING_DURATION_MINUTES"] = _ask(
        "Default meeting duration in minutes (when email gives no end time)",
        default=values.get("DEFAULT_MEETING_DURATION_MINUTES", "30"),
    )

    _write_env(values)
    _h1(f"Wrote {ENV_PATH.resolve()}")
    _info("Next steps:")
    _info("  1) python main.py --auth     (one-time Outlook sign-in via device code)")
    _info("  2) python main.py --once     (process current unread + exit; great smoke test)")
    _info("  3) python main.py            (run the polling loop)")
    return 0

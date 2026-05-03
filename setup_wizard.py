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

    # ---------- Notification channels ----------
    _h2("4/5  Notification channels")
    _info("Pick how you want to be notified. You can enable more than one.")
    print()
    print("    [1] WhatsApp via Meta Cloud API direct  (free up to 1000/mo)")
    print("    [2] WhatsApp via Twilio                 (sandbox is free)")
    print("    [3] SMS via Twilio                      (real SMS, paid)")
    print()
    pick = _ask("Pick one or more (e.g. '1' or '1,3')", default="1").strip()
    chosen = {p.strip() for p in pick.split(",") if p.strip()}

    channels: list[str] = []
    twilio_needed = bool(chosen & {"2", "3"})
    meta_needed = "1" in chosen

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

"""Centralized configuration loaded from environment / .env file.

All other modules should import settings from here rather than reading
os.environ directly. This keeps validation and defaults in one place.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str | None = None, *, required: bool = False) -> str:
    val = os.getenv(name, default)
    if required and (val is None or val == ""):
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return val if val is not None else ""


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise RuntimeError(f"Env var {name} must be an integer, got {raw!r}") from e


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as e:
        raise RuntimeError(f"Env var {name} must be a float, got {raw!r}") from e


def _get_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    # Mailbox
    mailbox_address: str
    user_timezone: str
    default_meeting_duration_minutes: int

    # Microsoft Graph
    ms_client_id: str
    ms_tenant_id: str
    ms_token_cache_path: Path

    # OpenAI
    openai_api_key: str
    openai_model: str

    # Twilio
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_from_sms: str
    twilio_from_whatsapp: str
    notify_to_sms: str
    notify_to_whatsapp: str

    # Meta WhatsApp Cloud API (direct)
    meta_wa_phone_number_id: str = ""
    meta_wa_access_token: str = ""
    meta_wa_recipient: str = ""           # E.164 WITHOUT '+', e.g. "15125551234"
    meta_wa_template_name: str = ""       # optional fallback template
    meta_wa_template_language: str = "en_US"
    meta_wa_api_version: str = "v21.0"

    # Subset of {sms, whatsapp, whatsapp_meta}
    notify_channels: list[str] = field(default_factory=list)

    # Agent
    poll_interval_seconds: int = 60
    auto_block_confidence: float = 0.75
    initial_lookback_minutes: int = 60
    state_db_path: Path = Path("state.db")
    debug: bool = False


def load_settings() -> Settings:
    return Settings(
        mailbox_address=_get("MAILBOX_ADDRESS", required=True),
        user_timezone=_get("USER_TIMEZONE", "America/Chicago"),
        default_meeting_duration_minutes=_get_int("DEFAULT_MEETING_DURATION_MINUTES", 30),
        ms_client_id=_get("MS_CLIENT_ID", required=True),
        ms_tenant_id=_get("MS_TENANT_ID", "common"),
        ms_token_cache_path=Path(_get("MS_TOKEN_CACHE_PATH", "token_cache.bin")),
        openai_api_key=_get("OPENAI_API_KEY", required=True),
        openai_model=_get("OPENAI_MODEL", "gpt-4o-mini"),
        twilio_account_sid=_get("TWILIO_ACCOUNT_SID", ""),
        twilio_auth_token=_get("TWILIO_AUTH_TOKEN", ""),
        twilio_from_sms=_get("TWILIO_FROM_SMS", ""),
        twilio_from_whatsapp=_get("TWILIO_FROM_WHATSAPP", ""),
        notify_to_sms=_get("NOTIFY_TO_SMS", ""),
        notify_to_whatsapp=_get("NOTIFY_TO_WHATSAPP", ""),
        meta_wa_phone_number_id=_get("META_WA_PHONE_NUMBER_ID", ""),
        meta_wa_access_token=_get("META_WA_ACCESS_TOKEN", ""),
        meta_wa_recipient=_get("META_WA_RECIPIENT", ""),
        meta_wa_template_name=_get("META_WA_TEMPLATE_NAME", ""),
        meta_wa_template_language=_get("META_WA_TEMPLATE_LANGUAGE", "en_US"),
        meta_wa_api_version=_get("META_WA_API_VERSION", "v21.0"),
        notify_channels=_get_list("NOTIFY_CHANNELS", ["whatsapp_meta"]),
        poll_interval_seconds=_get_int("POLL_INTERVAL_SECONDS", 60),
        auto_block_confidence=_get_float("AUTO_BLOCK_CONFIDENCE", 0.75),
        initial_lookback_minutes=_get_int("INITIAL_LOOKBACK_MINUTES", 60),
        state_db_path=Path(_get("STATE_DB_PATH", "state.db")),
        debug=_get("DEBUG", "0") not in ("0", "", "false", "False"),
    )

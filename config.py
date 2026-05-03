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

    # Provider selection: "outlook" (Microsoft 365) or "gmail" (Google Workspace / consumer Gmail).
    email_provider: str

    # Microsoft Graph (only required if email_provider == "outlook")
    ms_client_id: str = ""
    ms_tenant_id: str = "common"
    ms_token_cache_path: Path = Path("token_cache.bin")

    # Google APIs (only required if email_provider == "gmail")
    google_client_secrets_path: Path = Path("client_secret.json")
    google_token_cache_path: Path = Path("google_token.json")
    google_calendar_id: str = "primary"

    # LLM (pluggable: openai, azure_openai, github_models, ollama, openai_compat)
    llm_provider: str = "openai"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str = ""               # only for openai_compat / overriding defaults
    azure_openai_endpoint: str = ""      # https://<resource>.openai.azure.com
    azure_openai_api_version: str = ""

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_sms: str = ""
    twilio_from_whatsapp: str = ""
    notify_to_sms: str = ""
    notify_to_whatsapp: str = ""

    # Meta WhatsApp Cloud API (direct)
    meta_wa_phone_number_id: str = ""
    meta_wa_access_token: str = ""
    meta_wa_recipient: str = ""           # E.164 WITHOUT '+', e.g. "15125551234"
    meta_wa_template_name: str = ""       # optional fallback template
    meta_wa_template_language: str = "en_US"
    meta_wa_api_version: str = "v21.0"

    # Telegram bot
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Subset of {sms, whatsapp, whatsapp_meta, telegram}
    notify_channels: list[str] = field(default_factory=list)

    # Agent
    poll_interval_seconds: int = 60
    auto_block_confidence: float = 0.75
    initial_lookback_minutes: int = 60
    state_db_path: Path = Path("state.db")
    debug: bool = False


def load_settings() -> Settings:
    # Backward compat: if LLM_* not set, fall back to OPENAI_* (older configs).
    legacy_openai_key = _get("OPENAI_API_KEY", "")
    legacy_openai_model = _get("OPENAI_MODEL", "")
    llm_provider = _get("LLM_PROVIDER", "openai") or "openai"
    llm_api_key = _get("LLM_API_KEY", legacy_openai_key)
    llm_model = _get("LLM_MODEL", legacy_openai_model or "gpt-4o-mini")
    if not llm_api_key and llm_provider != "ollama":
        raise RuntimeError(
            "Missing LLM credentials. Set LLM_API_KEY (or legacy OPENAI_API_KEY)."
        )

    email_provider = (_get("EMAIL_PROVIDER", "outlook") or "outlook").lower()
    if email_provider not in ("outlook", "gmail"):
        raise RuntimeError(
            f"EMAIL_PROVIDER must be 'outlook' or 'gmail', got {email_provider!r}."
        )
    if email_provider == "outlook" and not _get("MS_CLIENT_ID", ""):
        raise RuntimeError(
            "EMAIL_PROVIDER=outlook requires MS_CLIENT_ID. "
            "Run scripts/setup_entra.ps1 or set it manually."
        )
    if email_provider == "gmail":
        gpath = Path(_get("GOOGLE_CLIENT_SECRETS_PATH", "client_secret.json"))
        if not gpath.exists():
            raise RuntimeError(
                f"EMAIL_PROVIDER=gmail requires GOOGLE_CLIENT_SECRETS_PATH to point to "
                f"a valid OAuth client JSON. Currently set to: {gpath}"
            )

    return Settings(
        mailbox_address=_get("MAILBOX_ADDRESS", required=True),
        user_timezone=_get("USER_TIMEZONE", required=True),
        default_meeting_duration_minutes=_get_int("DEFAULT_MEETING_DURATION_MINUTES", 30),
        email_provider=email_provider,
        ms_client_id=_get("MS_CLIENT_ID", ""),
        ms_tenant_id=_get("MS_TENANT_ID", "common"),
        ms_token_cache_path=Path(_get("MS_TOKEN_CACHE_PATH", "token_cache.bin")),
        google_client_secrets_path=Path(_get("GOOGLE_CLIENT_SECRETS_PATH", "client_secret.json")),
        google_token_cache_path=Path(_get("GOOGLE_TOKEN_CACHE_PATH", "google_token.json")),
        google_calendar_id=_get("GOOGLE_CALENDAR_ID", "primary"),
        llm_provider=llm_provider,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
        llm_base_url=_get("LLM_BASE_URL", ""),
        azure_openai_endpoint=_get("AZURE_OPENAI_ENDPOINT", ""),
        azure_openai_api_version=_get("AZURE_OPENAI_API_VERSION", ""),
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
        telegram_bot_token=_get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=_get("TELEGRAM_CHAT_ID", ""),
        notify_channels=_get_list("NOTIFY_CHANNELS", ["telegram"]),
        poll_interval_seconds=_get_int("POLL_INTERVAL_SECONDS", 60),
        auto_block_confidence=_get_float("AUTO_BLOCK_CONFIDENCE", 0.75),
        initial_lookback_minutes=_get_int("INITIAL_LOOKBACK_MINUTES", 60),
        state_db_path=Path(_get("STATE_DB_PATH", "state.db")),
        debug=_get("DEBUG", "0") not in ("0", "", "false", "False"),
    )

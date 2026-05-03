"""Multi-channel notifier: Twilio SMS, Twilio WhatsApp, Meta WhatsApp Cloud API.

Designed to fail soft: a misconfigured / failing channel is logged and
skipped; the loop never crashes because of a notification problem.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client as TwilioClient

from .telegram import TelegramClient, TelegramConfig
from .whatsapp_meta import MetaWhatsAppClient, MetaWhatsAppConfig

logger = logging.getLogger(__name__)

MAX_NOTIFICATION_CHARS = 1500

CHANNEL_SMS = "sms"
CHANNEL_TWILIO_WA = "whatsapp"           # Twilio WhatsApp (sandbox or paid)
CHANNEL_META_WA = "whatsapp_meta"        # Meta WhatsApp Cloud API direct
CHANNEL_TELEGRAM = "telegram"            # Telegram bot (free, recommended)

VALID_CHANNELS = {CHANNEL_SMS, CHANNEL_TWILIO_WA, CHANNEL_META_WA, CHANNEL_TELEGRAM}


@dataclass
class NotifierConfig:
    # Twilio (SMS + Twilio-WhatsApp)
    account_sid: str = ""
    auth_token: str = ""
    from_sms: str = ""
    from_whatsapp: str = ""
    to_sms: str = ""
    to_whatsapp: str = ""

    # Meta WhatsApp Cloud API
    meta_phone_number_id: str = ""
    meta_access_token: str = ""
    meta_recipient: str = ""               # E.164 WITHOUT '+' for Meta
    meta_template_name: str = ""           # optional fallback template
    meta_template_language: str = "en_US"
    meta_api_version: str = "v21.0"

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Subset of VALID_CHANNELS
    channels: list[str] = field(default_factory=list)


class Notifier:
    def __init__(self, config: NotifierConfig) -> None:
        self._cfg = config
        self._twilio: TwilioClient | None = None
        self._meta: MetaWhatsAppClient | None = None
        self._telegram: TelegramClient | None = None

        needs_twilio = any(c in config.channels for c in (CHANNEL_SMS, CHANNEL_TWILIO_WA))
        if needs_twilio:
            if config.account_sid and config.auth_token:
                self._twilio = TwilioClient(config.account_sid, config.auth_token)
            else:
                logger.warning(
                    "Twilio channel requested but TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN missing."
                )

        if CHANNEL_META_WA in config.channels:
            if config.meta_phone_number_id and config.meta_access_token and config.meta_recipient:
                self._meta = MetaWhatsAppClient(
                    MetaWhatsAppConfig(
                        phone_number_id=config.meta_phone_number_id,
                        access_token=config.meta_access_token,
                        recipient=config.meta_recipient,
                        template_name=config.meta_template_name,
                        template_language=config.meta_template_language,
                        api_version=config.meta_api_version,
                    )
                )
            else:
                logger.warning(
                    "whatsapp_meta channel requested but META_WA_PHONE_NUMBER_ID, "
                    "META_WA_ACCESS_TOKEN, or META_WA_RECIPIENT missing."
                )

        if CHANNEL_TELEGRAM in config.channels:
            if config.telegram_bot_token and config.telegram_chat_id:
                self._telegram = TelegramClient(
                    TelegramConfig(
                        bot_token=config.telegram_bot_token,
                        chat_id=config.telegram_chat_id,
                    )
                )
            else:
                logger.warning(
                    "telegram channel requested but TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing."
                )

        unknown = set(config.channels) - VALID_CHANNELS
        for ch in unknown:
            logger.warning("Unknown notifier channel '%s' (valid: %s).", ch, VALID_CHANNELS)

    # ----------------------------------------------------------------
    def notify(self, text: str) -> bool:
        """Send the message on every configured channel.

        Returns True if at least one channel accepted the message.
        """
        body = text[:MAX_NOTIFICATION_CHARS]
        any_ok = False
        for channel in self._cfg.channels:
            try:
                if channel == CHANNEL_SMS:
                    any_ok |= self._send_twilio_sms(body)
                elif channel == CHANNEL_TWILIO_WA:
                    any_ok |= self._send_twilio_whatsapp(body)
                elif channel == CHANNEL_META_WA:
                    any_ok |= self._send_meta_whatsapp(body)
                elif channel == CHANNEL_TELEGRAM:
                    any_ok |= self._send_telegram(body)
                # unknown channel already logged in __init__
            except Exception as e:
                logger.error("Notifier channel %s raised unexpected error: %s", channel, e)
        return any_ok

    # ----------------------------------------------------------------
    # per-channel senders
    # ----------------------------------------------------------------
    def _send_twilio_sms(self, body: str) -> bool:
        if not self._twilio:
            return False
        if not (self._cfg.from_sms and self._cfg.to_sms):
            logger.warning("SMS channel enabled but FROM/TO not configured.")
            return False
        try:
            self._twilio.messages.create(
                from_=self._cfg.from_sms, to=self._cfg.to_sms, body=body,
            )
            return True
        except TwilioRestException as e:
            logger.error("Twilio SMS send failed: %s", e)
            return False

    def _send_twilio_whatsapp(self, body: str) -> bool:
        if not self._twilio:
            return False
        if not (self._cfg.from_whatsapp and self._cfg.to_whatsapp):
            logger.warning("Twilio WhatsApp channel enabled but FROM/TO not configured.")
            return False
        try:
            self._twilio.messages.create(
                from_=self._cfg.from_whatsapp, to=self._cfg.to_whatsapp, body=body,
            )
            return True
        except TwilioRestException as e:
            logger.error("Twilio WhatsApp send failed: %s", e)
            return False

    def _send_meta_whatsapp(self, body: str) -> bool:
        if not self._meta:
            return False
        return self._meta.send(body)

    def _send_telegram(self, body: str) -> bool:
        if not self._telegram:
            return False
        return self._telegram.send(body)

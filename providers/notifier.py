"""Twilio-based notifier for SMS and WhatsApp.

Designed to fail soft: if a single channel is misconfigured we log and skip
rather than crashing the whole agent loop.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client as TwilioClient

logger = logging.getLogger(__name__)

# Twilio caps a single SMS segment around 160 chars (GSM-7) / 70 (UCS-2).
# Keep notifications short to avoid multi-segment billing & WhatsApp truncation.
MAX_NOTIFICATION_CHARS = 1500


@dataclass
class NotifierConfig:
    account_sid: str
    auth_token: str
    from_sms: str
    from_whatsapp: str
    to_sms: str
    to_whatsapp: str
    channels: list[str]  # subset of {"sms", "whatsapp"}


class Notifier:
    def __init__(self, config: NotifierConfig) -> None:
        self._cfg = config
        self._client: TwilioClient | None = None
        if config.account_sid and config.auth_token:
            self._client = TwilioClient(config.account_sid, config.auth_token)
        else:
            logger.warning("Twilio credentials not set; notifications will be skipped.")

    def notify(self, text: str) -> bool:
        """Send the message on every configured channel. Returns True if any send succeeded."""
        if self._client is None:
            return False
        body = text[:MAX_NOTIFICATION_CHARS]
        any_ok = False
        for channel in self._cfg.channels:
            try:
                if channel == "sms":
                    if not (self._cfg.from_sms and self._cfg.to_sms):
                        logger.warning("SMS channel enabled but FROM/TO not configured.")
                        continue
                    self._client.messages.create(
                        from_=self._cfg.from_sms,
                        to=self._cfg.to_sms,
                        body=body,
                    )
                    any_ok = True
                elif channel == "whatsapp":
                    if not (self._cfg.from_whatsapp and self._cfg.to_whatsapp):
                        logger.warning("WhatsApp channel enabled but FROM/TO not configured.")
                        continue
                    self._client.messages.create(
                        from_=self._cfg.from_whatsapp,
                        to=self._cfg.to_whatsapp,
                        body=body,
                    )
                    any_ok = True
                else:
                    logger.warning("Unknown notifier channel: %s", channel)
            except TwilioRestException as e:
                logger.error("Twilio %s send failed: %s", channel, e)
            except Exception as e:  # never let a notify failure crash the agent
                logger.error("Unexpected notifier error on %s: %s", channel, e)
        return any_ok

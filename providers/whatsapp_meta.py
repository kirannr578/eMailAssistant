"""WhatsApp via Meta's WhatsApp Cloud API (direct, no Twilio).

Why this client supports both text and template messages
--------------------------------------------------------
WhatsApp enforces a "24-hour customer service window": after the user has
not messaged the bot in 24h, free-form text messages from the bot are
silently dropped by Meta. The only way to send outside that window is via
a pre-approved Message Template.

This client therefore tries TEXT first; on a Meta error code that indicates
the session window is closed (131047, 131051, 131026), it falls back to a
configured template. If no template is configured, it logs the failure and
gives up gracefully.

Useful Meta error codes:
    131047  "Re-engagement message" - 24h window expired
    131051  "Unsupported message type"
    131026  "Message Undeliverable" (often = recipient not opted in)
    131056  "(Pair Rate Limit Hit)"

API docs: https://developers.facebook.com/docs/whatsapp/cloud-api
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

DEFAULT_API_VERSION = "v21.0"

# Meta error codes that mean "your free-form text was rejected; try a template instead".
_SESSION_CLOSED_CODES = {131047, 131051}


@dataclass
class MetaWhatsAppConfig:
    phone_number_id: str       # the Cloud API "Phone Number ID" (NOT the actual phone number)
    access_token: str          # long-lived System User token, scope: whatsapp_business_messaging
    recipient: str             # destination WhatsApp number, E.164 WITHOUT '+', e.g. "15125551234"
    template_name: str = ""    # optional fallback template (must be pre-approved in Meta)
    template_language: str = "en_US"
    api_version: str = DEFAULT_API_VERSION


class MetaWhatsAppClient:
    def __init__(self, config: MetaWhatsAppConfig) -> None:
        self._cfg = config
        self._url = (
            f"https://graph.facebook.com/{config.api_version}"
            f"/{config.phone_number_id}/messages"
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._cfg.access_token}",
            "Content-Type": "application/json",
        }

    # ----------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------
    def send(self, text: str) -> bool:
        """Send a notification. Tries text; on session-window error falls back to template.

        Returns True if WhatsApp accepted the message, False otherwise.
        """
        ok, error_code = self._send_text(text)
        if ok:
            return True

        # If the session window is closed and we have a template, try that.
        if error_code in _SESSION_CLOSED_CODES and self._cfg.template_name:
            logger.info(
                "WhatsApp 24h window appears closed (code %s). Trying template '%s'.",
                error_code, self._cfg.template_name,
            )
            return self._send_template(text)

        if error_code in _SESSION_CLOSED_CODES:
            logger.warning(
                "WhatsApp text rejected (24h window closed). Configure META_WA_TEMPLATE_NAME "
                "with a pre-approved template to deliver notifications outside the window."
            )
        return False

    # ----------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------
    def _send_text(self, text: str) -> tuple[bool, int | None]:
        payload = {
            "messaging_product": "whatsapp",
            "to": self._cfg.recipient,
            "type": "text",
            "text": {"preview_url": False, "body": text[:4096]},
        }
        return self._post(payload)

    def _send_template(self, text: str) -> bool:
        # We pass the notification body as the first template parameter ({{1}}).
        # The user is expected to have created a template like:
        #     "Email Assistant alert: {{1}}"
        payload = {
            "messaging_product": "whatsapp",
            "to": self._cfg.recipient,
            "type": "template",
            "template": {
                "name": self._cfg.template_name,
                "language": {"code": self._cfg.template_language},
                "components": [
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": text[:1000]}],
                    }
                ],
            },
        }
        ok, _ = self._post(payload)
        return ok

    def _post(self, payload: dict) -> tuple[bool, int | None]:
        try:
            resp = requests.post(self._url, headers=self._headers(), json=payload, timeout=20)
        except requests.RequestException as e:
            logger.error("Meta WhatsApp request failed: %s", e)
            return False, None

        if 200 <= resp.status_code < 300:
            return True, None

        # Try to extract Meta's structured error code.
        error_code: int | None = None
        try:
            err = resp.json().get("error") or {}
            error_code = err.get("code")
            sub_code = (err.get("error_data") or {}).get("messaging_product")
            logger.error(
                "Meta WhatsApp send failed: HTTP %s code=%s message=%s sub=%s",
                resp.status_code, error_code, err.get("message"), sub_code,
            )
        except Exception:
            logger.error("Meta WhatsApp send failed: HTTP %s body=%s",
                         resp.status_code, resp.text[:500])
        return False, error_code

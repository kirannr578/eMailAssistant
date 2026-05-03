"""Telegram bot notifier - free, no phone, no SMS gateway, no session window.

User flow:
    1. Open Telegram, message @BotFather, send /newbot, follow prompts.
    2. BotFather returns a token like "7891234567:AAH...xyz".
    3. Search for your new bot in Telegram and tap Start (so it has a chat with you).
    4. The wizard auto-discovers your chat ID via getUpdates.

API docs: https://core.telegram.org/bots/api
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
MAX_TELEGRAM_TEXT = 4096


@dataclass
class TelegramConfig:
    bot_token: str
    chat_id: str       # Telegram chat ID (numeric string), e.g. "123456789"


class TelegramClient:
    def __init__(self, config: TelegramConfig) -> None:
        self._cfg = config

    def send(self, text: str) -> bool:
        url = f"{API_BASE}/bot{self._cfg.bot_token}/sendMessage"
        payload = {
            "chat_id": self._cfg.chat_id,
            "text": text[:MAX_TELEGRAM_TEXT],
            "disable_web_page_preview": True,
        }
        try:
            resp = requests.post(url, json=payload, timeout=15)
        except requests.RequestException as e:
            logger.error("Telegram request failed: %s", e)
            return False

        if 200 <= resp.status_code < 300 and resp.json().get("ok"):
            return True

        logger.error("Telegram send failed: HTTP %s body=%s",
                     resp.status_code, resp.text[:300])
        return False


def discover_chat_id(bot_token: str, *, poll_seconds: int = 60) -> str | None:
    """Poll getUpdates until we see a private-chat message; return its chat_id.

    The wizard uses this to auto-discover the user's chat ID so they don't have
    to find it manually. Returns None on timeout / failure.
    """
    url = f"{API_BASE}/bot{bot_token}/getUpdates"
    deadline = time.monotonic() + poll_seconds
    last_update_id = 0
    while time.monotonic() < deadline:
        try:
            resp = requests.get(
                url,
                params={"offset": last_update_id + 1, "timeout": 10, "allowed_updates": '["message"]'},
                timeout=15,
            )
            data = resp.json()
        except Exception as e:
            logger.warning("Telegram getUpdates error: %s", e)
            time.sleep(2)
            continue

        if not data.get("ok"):
            logger.warning("Telegram getUpdates not ok: %s", data)
            return None

        for upd in data.get("result", []):
            last_update_id = max(last_update_id, upd.get("update_id", 0))
            msg = upd.get("message") or {}
            chat = msg.get("chat") or {}
            chat_id = chat.get("id")
            if chat_id and chat.get("type") == "private":
                return str(chat_id)
        # Poll loop continues
    return None

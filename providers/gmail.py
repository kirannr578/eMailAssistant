"""Gmail mail access via the Gmail API."""
from __future__ import annotations

import base64
import logging
import re
from datetime import datetime, timezone
from email import message_from_bytes
from email.message import Message
from html import unescape
from typing import Any

from dateutil import parser as date_parser
from googleapiclient.discovery import build

from .base import EmailAttachment, EmailMessage
from .google_auth import GoogleAuth

logger = logging.getLogger(__name__)


def _strip_html(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<\s*br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</\s*p\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_body(msg: Message) -> str:
    """Pull the best plain text we can out of a parsed RFC822 email."""
    text_parts: list[str] = []
    html_parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            ctype = (part.get_content_type() or "").lower()
            disp = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            try:
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                content = payload.decode(charset, errors="replace")
            except Exception:
                continue
            if ctype == "text/plain":
                text_parts.append(content)
            elif ctype == "text/html":
                html_parts.append(content)
    else:
        try:
            payload = msg.get_payload(decode=True) or b""
            charset = msg.get_content_charset() or "utf-8"
            content = payload.decode(charset, errors="replace")
        except Exception:
            content = ""
        if (msg.get_content_type() or "").lower() == "text/html":
            html_parts.append(content)
        else:
            text_parts.append(content)

    if text_parts:
        return "\n\n".join(p.strip() for p in text_parts).strip()
    if html_parts:
        return _strip_html("\n\n".join(html_parts))
    return ""


class GmailClient:
    def __init__(self, auth: GoogleAuth) -> None:
        self._auth = auth
        self._service: Any | None = None

    def _svc(self):
        if self._service is None:
            creds = self._auth.get_credentials()
            self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    def list_unread(self, *, since: datetime, top: int = 25) -> list[EmailMessage]:
        # Gmail's `q` accepts e.g. "is:unread newer_than:1d" or `after:` epoch seconds.
        since_epoch = int(since.astimezone(timezone.utc).timestamp())
        q = f"is:unread in:inbox after:{since_epoch}"
        svc = self._svc()
        listing = svc.users().messages().list(userId="me", q=q, maxResults=top).execute()
        ids = [m["id"] for m in listing.get("messages", [])]
        out: list[EmailMessage] = []
        for mid in ids:
            try:
                # `format=raw` gets the full RFC822 so we can parse cleanly.
                raw = svc.users().messages().get(userId="me", id=mid, format="raw").execute()
                meta = svc.users().messages().get(
                    userId="me", id=mid, format="metadata",
                    metadataHeaders=["Subject", "From", "To", "Date"],
                ).execute()
                out.append(self._to_message(mid, raw, meta))
            except Exception as e:
                logger.warning("Failed to fetch Gmail message %s: %s", mid, e)
        out.sort(key=lambda m: m.received_at)
        return out

    def mark_read(self, message_id: str) -> None:
        self._svc().users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()

    # ----------------------------------------------------------------
    # Attachments
    # ----------------------------------------------------------------
    def list_attachments(self, message_id: str) -> list[EmailAttachment]:
        msg = self._svc().users().messages().get(
            userId="me", id=message_id, format="full",
        ).execute()
        out: list[EmailAttachment] = []
        self._collect_parts(msg.get("payload") or {}, out)
        return out

    def _collect_parts(self, part: dict, out: list[EmailAttachment]) -> None:
        # Walk the MIME tree; collect leaves with non-empty filename.
        for child in part.get("parts") or []:
            self._collect_parts(child, out)
        filename = part.get("filename") or ""
        body = part.get("body") or {}
        att_id = body.get("attachmentId")
        if filename and att_id:
            mime = part.get("mimeType") or "application/octet-stream"
            out.append(EmailAttachment(
                id=att_id,
                filename=filename,
                size_bytes=int(body.get("size") or 0),
                content_type=mime,
            ))

    def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        att = self._svc().users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id,
        ).execute()
        data = att.get("data") or ""
        if not data:
            return b""
        return base64.urlsafe_b64decode(data.encode("ascii"))

    @staticmethod
    def _to_message(mid: str, raw_resp: dict, meta_resp: dict) -> EmailMessage:
        raw_b64 = raw_resp.get("raw", "")
        raw_bytes = base64.urlsafe_b64decode(raw_b64.encode("ascii")) if raw_b64 else b""
        parsed = message_from_bytes(raw_bytes) if raw_bytes else Message()

        headers = {h["name"].lower(): h["value"]
                   for h in (meta_resp.get("payload") or {}).get("headers", [])}
        subject = headers.get("subject") or "(no subject)"
        sender = headers.get("from") or "(unknown sender)"
        to_raw = headers.get("to") or ""
        to_list = [a.strip() for a in to_raw.split(",") if "@" in a]

        date_str = headers.get("date")
        if date_str:
            try:
                received = date_parser.parsedate_to_datetime(date_str)  # type: ignore[attr-defined]
            except Exception:
                received = datetime.now(timezone.utc)
        else:
            ms = int(meta_resp.get("internalDate") or 0)
            received = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)

        body_text = _extract_body(parsed)
        web_link = f"https://mail.google.com/mail/u/0/#inbox/{mid}"

        return EmailMessage(
            id=mid,
            subject=subject,
            sender=sender,
            to=to_list,
            received_at=received,
            body_text=body_text,
            web_link=web_link,
            is_read=False,
        )

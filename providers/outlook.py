"""Outlook mail access via Microsoft Graph."""
from __future__ import annotations

import base64
import logging
import re
from datetime import datetime
from html import unescape

import requests
from dateutil import parser as date_parser

from .base import EmailAttachment, EmailMessage
from .ms_graph_auth import GRAPH_BASE_URL, GraphAuth

logger = logging.getLogger(__name__)

__all__ = ["EmailAttachment", "EmailMessage", "OutlookClient"]


def _strip_html(html: str) -> str:
    """Very lightweight HTML -> text. Avoids pulling in BeautifulSoup."""
    if not html:
        return ""
    # Drop script/style blocks entirely
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    # Replace <br> and <p> with newlines
    text = re.sub(r"(?i)<\s*br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</\s*p\s*>", "\n", text)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class OutlookClient:
    def __init__(self, auth: GraphAuth) -> None:
        self._auth = auth

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._auth.get_access_token()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def list_unread(self, *, since: datetime, top: int = 25) -> list[EmailMessage]:
        """List unread messages received since `since` (UTC datetime)."""
        # Graph wants UTC ISO 8601 with Z suffix in $filter.
        since_utc = since.astimezone(tz=None).utctimetuple()
        since_str = datetime(*since_utc[:6]).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "$select": "id,subject,from,toRecipients,receivedDateTime,bodyPreview,body,webLink,isRead",
            "$orderby": "receivedDateTime asc",
            "$top": str(top),
            "$filter": f"isRead eq false and receivedDateTime ge {since_str}",
        }
        url = f"{GRAPH_BASE_URL}/me/mailFolders/Inbox/messages"
        resp = requests.get(url, headers=self._headers(), params=params, timeout=30)
        if resp.status_code >= 400:
            logger.error("Graph list_unread failed: %s %s", resp.status_code, resp.text)
            resp.raise_for_status()
        items = resp.json().get("value", [])
        return [self._to_message(it) for it in items]

    def mark_read(self, message_id: str) -> None:
        url = f"{GRAPH_BASE_URL}/me/messages/{message_id}"
        resp = requests.patch(
            url,
            headers=self._headers(),
            json={"isRead": True},
            timeout=30,
        )
        if resp.status_code >= 400:
            logger.error("Graph mark_read failed: %s %s", resp.status_code, resp.text)
            resp.raise_for_status()

    # ----------------------------------------------------------------
    # Attachments
    # ----------------------------------------------------------------
    def list_attachments(self, message_id: str) -> list[EmailAttachment]:
        # Filter out inline attachments (embedded images in the body).
        url = f"{GRAPH_BASE_URL}/me/messages/{message_id}/attachments"
        params = {
            "$select": "id,name,size,contentType,isInline",
            "$top": "50",
        }
        resp = requests.get(url, headers=self._headers(), params=params, timeout=30)
        if resp.status_code >= 400:
            logger.error("Graph list_attachments failed: %s %s", resp.status_code, resp.text)
            resp.raise_for_status()
        out: list[EmailAttachment] = []
        for it in resp.json().get("value", []):
            if it.get("isInline"):
                continue
            # Only file attachments; skip itemAttachment (forwarded emails) etc.
            if it.get("@odata.type") and "fileAttachment" not in it["@odata.type"]:
                continue
            out.append(EmailAttachment(
                id=it["id"],
                filename=it.get("name") or "untitled",
                size_bytes=int(it.get("size") or 0),
                content_type=it.get("contentType") or "application/octet-stream",
            ))
        return out

    def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        # Use $value to get raw bytes; otherwise we'd get base64 in JSON.
        url = f"{GRAPH_BASE_URL}/me/messages/{message_id}/attachments/{attachment_id}/$value"
        resp = requests.get(url, headers=self._headers(), timeout=120)
        if resp.status_code == 404 or not resp.content:
            # Some referenceAttachments don't have $value; fall back to JSON.
            jurl = f"{GRAPH_BASE_URL}/me/messages/{message_id}/attachments/{attachment_id}"
            jresp = requests.get(jurl, headers=self._headers(), timeout=120)
            jresp.raise_for_status()
            data = jresp.json()
            cb = data.get("contentBytes")
            if not cb:
                raise RuntimeError(f"Attachment {attachment_id} has no downloadable content")
            return base64.b64decode(cb)
        if resp.status_code >= 400:
            logger.error("Graph download_attachment failed: %s %s", resp.status_code, resp.text)
            resp.raise_for_status()
        return resp.content

    @staticmethod
    def _to_message(raw: dict) -> EmailMessage:
        body = raw.get("body") or {}
        content_type = (body.get("contentType") or "text").lower()
        content = body.get("content") or raw.get("bodyPreview") or ""
        body_text = _strip_html(content) if content_type == "html" else content

        sender_addr = ""
        sender = raw.get("from") or {}
        sender_email = (sender.get("emailAddress") or {})
        if sender_email:
            name = sender_email.get("name") or ""
            addr = sender_email.get("address") or ""
            sender_addr = f"{name} <{addr}>".strip() if name else addr

        to_list = []
        for rec in raw.get("toRecipients") or []:
            ea = rec.get("emailAddress") or {}
            addr = ea.get("address")
            if addr:
                to_list.append(addr)

        received = date_parser.isoparse(raw["receivedDateTime"])

        return EmailMessage(
            id=raw["id"],
            subject=raw.get("subject") or "(no subject)",
            sender=sender_addr or "(unknown sender)",
            to=to_list,
            received_at=received,
            body_text=body_text,
            web_link=raw.get("webLink"),
            is_read=bool(raw.get("isRead")),
        )

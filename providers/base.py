"""Email, Calendar, and File-Storage provider interfaces.

Each concrete provider (Outlook via Graph, Gmail via Google APIs, OneDrive,
Google Drive) implements these so the agent loop in main.py stays provider-
agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass
class EmailMessage:
    """Provider-agnostic representation of one email message."""
    id: str                  # provider-specific opaque ID
    subject: str
    sender: str              # "Name <email>" or "email"
    to: list[str]            # email addresses
    received_at: datetime    # timezone-aware
    body_text: str
    web_link: str | None     # link to open the message in the user's mail UI
    is_read: bool


@dataclass
class EmailAttachment:
    """Metadata for one attachment, returned by list_attachments."""
    id: str                  # provider-specific
    filename: str
    size_bytes: int
    content_type: str        # e.g. "application/pdf"


class EmailProvider(Protocol):
    def list_unread(self, *, since: datetime, top: int = 25) -> list[EmailMessage]: ...
    def mark_read(self, message_id: str) -> None: ...

    def list_attachments(self, message_id: str) -> list[EmailAttachment]:
        """Return non-inline attachment metadata. Empty list if none."""
        ...

    def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """Return raw bytes of the attachment."""
        ...


class CalendarProvider(Protocol):
    def create_event(
        self,
        *,
        subject: str,
        start: datetime,
        end: datetime,
        body_text: str,
        attendees: list[str] | None = None,
        location: str | None = None,
        is_tentative: bool = False,
    ) -> str:
        """Create an event and return its provider-specific event ID."""
        ...


class FileStorage(Protocol):
    """Cloud document storage abstraction (OneDrive / Google Drive)."""

    def upload(
        self,
        *,
        folder_path: str,                    # e.g. "Email Assistant/Bids/Cedar Park OB"
        filename: str,
        content: bytes,
        content_type: str | None = None,
    ) -> str:
        """Upload a file under folder_path. Returns a web link to the uploaded file."""
        ...

    def folder_link(self, folder_path: str) -> str | None:
        """Return a clickable web link to the folder, or None."""
        ...

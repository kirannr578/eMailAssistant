"""Email and Calendar provider interfaces.

Each concrete provider (Outlook via Graph, Gmail via Google APIs) implements
these so the agent loop in main.py can stay provider-agnostic.
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


class EmailProvider(Protocol):
    def list_unread(self, *, since: datetime, top: int = 25) -> list[EmailMessage]: ...
    def mark_read(self, message_id: str) -> None: ...


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

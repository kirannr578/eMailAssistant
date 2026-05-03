"""Google Calendar event creation."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from googleapiclient.discovery import build

from .google_auth import GoogleAuth

logger = logging.getLogger(__name__)


class GoogleCalendarClient:
    def __init__(self, auth: GoogleAuth, *, user_timezone: str, calendar_id: str = "primary") -> None:
        self._auth = auth
        self._tz = user_timezone
        self._calendar_id = calendar_id
        self._service: Any | None = None

    def _svc(self):
        if self._service is None:
            creds = self._auth.get_credentials()
            self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

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
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start/end must be timezone-aware")

        event: dict[str, Any] = {
            "summary": subject,
            "description": body_text,
            "start": {"dateTime": start.isoformat(), "timeZone": self._tz},
            "end": {"dateTime": end.isoformat(), "timeZone": self._tz},
            "transparency": "tentative" if is_tentative else "opaque",
            "reminders": {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": 10}],
            },
        }
        if location:
            event["location"] = location
        if attendees:
            event["attendees"] = [{"email": a} for a in attendees if "@" in a]

        created = self._svc().events().insert(
            calendarId=self._calendar_id,
            body=event,
            sendUpdates="none",   # don't email attendees automatically
        ).execute()
        return created["id"]

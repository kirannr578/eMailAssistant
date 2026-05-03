"""Outlook Calendar event creation via Microsoft Graph."""
from __future__ import annotations

import logging
from datetime import datetime

import requests

from .ms_graph_auth import GRAPH_BASE_URL, GraphAuth

logger = logging.getLogger(__name__)


class CalendarClient:
    def __init__(self, auth: GraphAuth, *, user_timezone: str) -> None:
        self._auth = auth
        self._tz = user_timezone

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._auth.get_access_token()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            # Tell Graph to interpret/return times in our preferred TZ.
            "Prefer": f'outlook.timezone="{self._tz}"',
        }

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
        """Create a calendar event and return its Graph event id."""
        # Graph expects start/end as { dateTime: 'YYYY-MM-DDTHH:MM:SS', timeZone: 'IANA' }.
        # We send them in the user's local TZ to avoid DST-related surprises.
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start/end must be timezone-aware")

        payload: dict = {
            "subject": subject,
            "body": {
                "contentType": "Text",
                "content": body_text,
            },
            "start": {
                "dateTime": start.isoformat(timespec="seconds"),
                "timeZone": self._tz,
            },
            "end": {
                "dateTime": end.isoformat(timespec="seconds"),
                "timeZone": self._tz,
            },
            "showAs": "tentative" if is_tentative else "busy",
            "isReminderOn": True,
            "reminderMinutesBeforeStart": 10,
        }
        if location:
            payload["location"] = {"displayName": location}
        if attendees:
            payload["attendees"] = [
                {
                    "emailAddress": {"address": a},
                    "type": "required",
                }
                for a in attendees
                if a
            ]

        resp = requests.post(
            f"{GRAPH_BASE_URL}/me/events",
            headers=self._headers(),
            json=payload,
            timeout=30,
        )
        if resp.status_code >= 400:
            logger.error("Graph create_event failed: %s %s", resp.status_code, resp.text)
            resp.raise_for_status()
        return resp.json()["id"]

"""Google OAuth for installed/desktop apps via the loopback redirect flow.

User flow:
  1. User creates a Google Cloud project, enables Gmail and Calendar APIs,
     creates an OAuth client ID of type "Desktop app", and downloads the JSON.
  2. User points GOOGLE_CLIENT_SECRETS_PATH at that JSON.
  3. First run: this module spins up a localhost HTTP server briefly,
     opens the browser to Google's consent page, and captures the auth code
     when the user approves. Then it caches refresh tokens in
     GOOGLE_TOKEN_CACHE_PATH so subsequent runs are silent.

Why loopback (not device-code)?
  Google deprecated OOB / device-code for desktop apps. The loopback redirect
  is the only currently-supported flow for this app type.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

logger = logging.getLogger(__name__)

# Scopes we need:
#   gmail.modify  -> read messages and mark as read (does NOT include sending)
#   calendar      -> create/update events on the user's calendar
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    # `drive.file` = per-file access for files this app creates. Doesn't
    # require Google verification; safe for personal use.
    "https://www.googleapis.com/auth/drive.file",
]


class GoogleAuthError(RuntimeError):
    pass


class GoogleAuth:
    def __init__(self, *, client_secrets_path: Path, token_cache_path: Path) -> None:
        self._client_secrets_path = client_secrets_path
        self._token_cache_path = token_cache_path
        self._creds: Credentials | None = None

    def get_credentials(self) -> Credentials:
        """Return valid Credentials, doing the loopback OAuth flow on first run."""
        creds: Credentials | None = self._creds
        if creds is None and self._token_cache_path.exists():
            try:
                data = json.loads(self._token_cache_path.read_text(encoding="utf-8"))
                creds = Credentials.from_authorized_user_info(data, GOOGLE_SCOPES)
            except Exception as e:
                logger.warning("Could not load Google token cache (%s); will re-auth.", e)
                creds = None

        if creds and creds.valid:
            self._creds = creds
            return creds

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                self._persist(creds)
                self._creds = creds
                return creds
            except Exception as e:
                logger.warning("Refresh failed (%s); falling back to interactive flow.", e)

        # Interactive sign-in via loopback redirect.
        if not self._client_secrets_path.exists():
            raise GoogleAuthError(
                f"GOOGLE_CLIENT_SECRETS_PATH not found at {self._client_secrets_path}. "
                f"Download the OAuth client JSON from Google Cloud Console "
                f"(APIs & Services -> Credentials -> Desktop client)."
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            str(self._client_secrets_path), GOOGLE_SCOPES
        )
        creds = flow.run_local_server(
            port=0,                            # pick any free port
            prompt="consent",                  # always show consent (gets refresh token)
            authorization_prompt_message=(
                "Opening your browser to sign in to Google. "
                "If it doesn't open, visit this URL manually:\n  {url}"
            ),
            success_message=(
                "Signed in. You can close this browser tab."
            ),
        )
        self._persist(creds)
        self._creds = creds
        return creds

    def _persist(self, creds: Credentials) -> None:
        self._token_cache_path.write_text(creds.to_json(), encoding="utf-8")

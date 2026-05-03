"""Microsoft Graph authentication using MSAL device-code flow.

Why device-code flow?
- Works on a headless Windows box / Task Scheduler with no browser redirect URI.
- After a one-time interactive sign-in, MSAL caches a refresh token on disk so
  subsequent runs are non-interactive.

Required app-registration setup (in Microsoft Entra portal):
  - Supported account types: include personal + work accounts as needed.
  - Authentication -> Advanced settings -> "Allow public client flows" = YES.
  - API permissions (Delegated):
        Mail.ReadWrite, Calendars.ReadWrite, User.Read, offline_access
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import msal

logger = logging.getLogger(__name__)

GRAPH_SCOPES = [
    "Mail.ReadWrite",
    "Calendars.ReadWrite",
    "User.Read",
]
# Note: msal automatically adds offline_access + openid + profile.

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


class GraphAuthError(RuntimeError):
    pass


class GraphAuth:
    def __init__(self, *, client_id: str, tenant_id: str, token_cache_path: Path) -> None:
        self._client_id = client_id
        self._tenant_id = tenant_id
        self._token_cache_path = token_cache_path

        self._cache = msal.SerializableTokenCache()
        if token_cache_path.exists():
            try:
                self._cache.deserialize(token_cache_path.read_text(encoding="utf-8"))
            except Exception as e:  # corrupt cache shouldn't kill us
                logger.warning("Could not load token cache (%s); will re-auth.", e)

        self._app = msal.PublicClientApplication(
            client_id=client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            token_cache=self._cache,
        )

    def _persist_cache(self) -> None:
        if self._cache.has_state_changed:
            self._token_cache_path.write_text(self._cache.serialize(), encoding="utf-8")

    def get_access_token(self) -> str:
        """Return a valid access token, doing device-code flow on first run."""
        accounts = self._app.get_accounts()
        result = None
        if accounts:
            result = self._app.acquire_token_silent(GRAPH_SCOPES, account=accounts[0])

        if not result:
            logger.info("No cached token; starting device-code flow.")
            flow = self._app.initiate_device_flow(scopes=GRAPH_SCOPES)
            if "user_code" not in flow:
                raise GraphAuthError(
                    f"Failed to start device flow: {flow.get('error_description', flow)}"
                )
            # Make sure the user actually sees this even when stdout is buffered.
            print("\n" + "=" * 70, flush=True)
            print(flow["message"], flush=True)
            print("=" * 70 + "\n", flush=True)
            sys.stdout.flush()
            result = self._app.acquire_token_by_device_flow(flow)  # blocks until done

        self._persist_cache()

        if "access_token" not in result:
            raise GraphAuthError(
                f"Token acquisition failed: {result.get('error_description', result)}"
            )
        return result["access_token"]

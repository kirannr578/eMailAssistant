"""Google Drive file storage via the Drive v3 API.

Uses the `drive.file` scope (per-file access for files THIS app creates),
which doesn't require Google verification for personal use.
"""
from __future__ import annotations

import io
import logging
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from .google_auth import GoogleAuth

logger = logging.getLogger(__name__)

DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"


class GoogleDriveClient:
    def __init__(self, auth: GoogleAuth) -> None:
        self._auth = auth
        self._svc: Any | None = None

    def _service(self):
        if self._svc is None:
            creds = self._auth.get_credentials()
            self._svc = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._svc

    # ----------------------------------------------------------------
    def ensure_folder(self, folder_path: str) -> str:
        """Create folder_path (and parents) if missing. Returns the folder ID."""
        path = folder_path.strip("/").strip()
        parent_id = "root"
        if not path:
            return parent_id
        for segment in path.split("/"):
            parent_id = self._ensure_child_folder(parent_id, segment)
        return parent_id

    def _ensure_child_folder(self, parent_id: str, name: str) -> str:
        # Look for an existing folder with this name under parent_id.
        # Escape single quotes in the name for the q= filter.
        safe_name = name.replace("'", "\\'")
        q = (
            f"name = '{safe_name}' "
            f"and mimeType = '{DRIVE_FOLDER_MIME}' "
            f"and '{parent_id}' in parents "
            f"and trashed = false"
        )
        listing = self._service().files().list(
            q=q,
            fields="files(id, name)",
            pageSize=10,
            supportsAllDrives=False,
        ).execute()
        for f in listing.get("files", []):
            return f["id"]
        # Create
        meta = {"name": name, "mimeType": DRIVE_FOLDER_MIME, "parents": [parent_id]}
        created = self._service().files().create(body=meta, fields="id").execute()
        return created["id"]

    # ----------------------------------------------------------------
    def upload(
        self,
        *,
        folder_path: str,
        filename: str,
        content: bytes,
        content_type: str | None = None,
    ) -> str:
        parent_id = self.ensure_folder(folder_path)
        media = MediaIoBaseUpload(
            io.BytesIO(content),
            mimetype=content_type or "application/octet-stream",
            resumable=len(content) > 5 * 1024 * 1024,
        )
        meta = {"name": filename, "parents": [parent_id]}
        created = self._service().files().create(
            body=meta,
            media_body=media,
            fields="id, webViewLink",
            supportsAllDrives=False,
        ).execute()
        return created.get("webViewLink") or ""

    def folder_link(self, folder_path: str) -> str | None:
        try:
            fid = self.ensure_folder(folder_path)
            f = self._service().files().get(fileId=fid, fields="webViewLink").execute()
            return f.get("webViewLink")
        except Exception:
            return None

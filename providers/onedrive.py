"""OneDrive (personal/work) file storage via Microsoft Graph.

Two upload modes:
  - Small files (<4 MiB): single PUT to /content (one round-trip).
  - Large files: createUploadSession then upload in 8 MiB chunks. Required
    for construction PDFs / DWGs / ZIPs that are routinely 50-300 MB.

Folder creation: Graph does NOT auto-create parent folders on upload.
We walk the path segment by segment, creating folders as needed.
"""
from __future__ import annotations

import logging
import urllib.parse

import requests

from .ms_graph_auth import GRAPH_BASE_URL, GraphAuth

logger = logging.getLogger(__name__)

# Graph hard limit for single-shot upload via /content is 4 MiB.
SMALL_UPLOAD_THRESHOLD = 4 * 1024 * 1024
# Upload session chunk size; must be a multiple of 320 KiB. 8 MiB is a sweet spot.
CHUNK_SIZE = 8 * 1024 * 1024


def _quote_path(path: str) -> str:
    """URL-encode a OneDrive path while keeping forward slashes."""
    return urllib.parse.quote(path, safe="/")


class OneDriveClient:
    def __init__(self, auth: GraphAuth) -> None:
        self._auth = auth

    def _headers(self, *, content_type: str | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self._auth.get_access_token()}"}
        if content_type:
            h["Content-Type"] = content_type
        return h

    # ----------------------------------------------------------------
    # Folder management
    # ----------------------------------------------------------------
    def ensure_folder(self, folder_path: str) -> str:
        """Create folder_path (and parents) if missing. Returns the item ID."""
        path = folder_path.strip("/").strip()
        if not path:
            # Root
            r = requests.get(f"{GRAPH_BASE_URL}/me/drive/root", headers=self._headers(), timeout=30)
            r.raise_for_status()
            return r.json()["id"]

        segments = path.split("/")
        parent_id: str | None = None  # None = drive root
        for i, segment in enumerate(segments):
            parent_id = self._ensure_child_folder(parent_id, segment)
        assert parent_id is not None
        return parent_id

    def _ensure_child_folder(self, parent_id: str | None, name: str) -> str:
        """Create or look up `name` under `parent_id` (None = root). Returns the new folder's ID."""
        if parent_id is None:
            list_url = f"{GRAPH_BASE_URL}/me/drive/root/children"
        else:
            list_url = f"{GRAPH_BASE_URL}/me/drive/items/{parent_id}/children"

        # Try to find an existing child with this name (case-insensitive on OneDrive).
        params = {"$select": "id,name,folder", "$filter": f"name eq '{name.replace(chr(39), chr(39)*2)}'"}
        r = requests.get(list_url, headers=self._headers(), params=params, timeout=30)
        if r.status_code < 400:
            for it in r.json().get("value", []):
                if it.get("folder") and it.get("name", "").lower() == name.lower():
                    return it["id"]

        # Create.
        body = {
            "name": name,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "rename",
        }
        cr = requests.post(list_url, headers=self._headers(content_type="application/json"),
                           json=body, timeout=30)
        if cr.status_code >= 400:
            logger.error("OneDrive create folder failed: %s %s", cr.status_code, cr.text)
            cr.raise_for_status()
        return cr.json()["id"]

    # ----------------------------------------------------------------
    # Upload
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
        if len(content) <= SMALL_UPLOAD_THRESHOLD:
            return self._upload_small(parent_id, filename, content, content_type)
        return self._upload_session(parent_id, filename, content, content_type)

    def _upload_small(self, parent_id: str, filename: str, content: bytes,
                      content_type: str | None) -> str:
        url = (
            f"{GRAPH_BASE_URL}/me/drive/items/{parent_id}:/"
            f"{_quote_path(filename)}:/content"
        )
        ctype = content_type or "application/octet-stream"
        # Use ?@microsoft.graph.conflictBehavior=rename to avoid overwriting.
        url = url + "?@microsoft.graph.conflictBehavior=rename"
        r = requests.put(url, headers=self._headers(content_type=ctype), data=content, timeout=300)
        if r.status_code >= 400:
            logger.error("OneDrive small upload failed: %s %s", r.status_code, r.text)
            r.raise_for_status()
        return r.json().get("webUrl") or ""

    def _upload_session(self, parent_id: str, filename: str, content: bytes,
                        content_type: str | None) -> str:
        create_url = (
            f"{GRAPH_BASE_URL}/me/drive/items/{parent_id}:/"
            f"{_quote_path(filename)}:/createUploadSession"
        )
        body = {
            "item": {
                "@microsoft.graph.conflictBehavior": "rename",
                "name": filename,
            }
        }
        sr = requests.post(create_url, headers=self._headers(content_type="application/json"),
                           json=body, timeout=60)
        if sr.status_code >= 400:
            logger.error("OneDrive createUploadSession failed: %s %s", sr.status_code, sr.text)
            sr.raise_for_status()
        upload_url = sr.json()["uploadUrl"]

        total = len(content)
        offset = 0
        last_resp: requests.Response | None = None
        while offset < total:
            chunk = content[offset : offset + CHUNK_SIZE]
            end = offset + len(chunk) - 1
            headers = {
                "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {offset}-{end}/{total}",
            }
            cr = requests.put(upload_url, headers=headers, data=chunk, timeout=600)
            if cr.status_code >= 400:
                logger.error("OneDrive chunk upload failed at offset %d: %s %s",
                             offset, cr.status_code, cr.text)
                cr.raise_for_status()
            last_resp = cr
            offset += len(chunk)

        # Last chunk's response carries the file metadata.
        if last_resp is not None and last_resp.headers.get("Content-Type", "").startswith("application/json"):
            try:
                return last_resp.json().get("webUrl") or ""
            except Exception:
                pass
        return ""

    # ----------------------------------------------------------------
    # Folder web link
    # ----------------------------------------------------------------
    def folder_link(self, folder_path: str) -> str | None:
        path = folder_path.strip("/").strip()
        if not path:
            return None
        url = f"{GRAPH_BASE_URL}/me/drive/root:/{_quote_path(path)}"
        r = requests.get(url, headers=self._headers(), timeout=30)
        if r.status_code >= 400:
            return None
        return r.json().get("webUrl")

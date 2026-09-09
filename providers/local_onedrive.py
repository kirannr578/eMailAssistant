"""Write files into the local OneDrive sync folder (desktop client).

Use this when OneDrive is installed but you do not want Microsoft Graph /
Entra app registration. The desktop client syncs files to the cloud automatically.
"""
from __future__ import annotations

import os
from pathlib import Path


def detect_onedrive_root() -> Path | None:
    """Return the local OneDrive sync root, or None if not found."""
    override = os.getenv("GMAIL_ARCHIVE_LOCAL_ROOT", "").strip()
    if override:
        path = Path(override).expanduser()
        if path.is_dir():
            return path

    for key in ("OneDrive", "ONEDRIVE", "OneDriveCommercial", "ONEDRIVECOMMERCIAL"):
        val = os.getenv(key, "").strip()
        if val:
            path = Path(val)
            if path.is_dir():
                return path

    home = Path.home()
    for name in ("OneDrive", "OneDrive - Personal"):
        candidate = home / name
        if candidate.is_dir():
            return candidate

    # Any "OneDrive - <Org>" work/school folder.
    for candidate in home.glob("OneDrive - *"):
        if candidate.is_dir():
            return candidate
    return None


class LocalOneDriveStorage:
    """Save files under the local OneDrive folder for cloud sync."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def upload(
        self,
        *,
        folder_path: str,
        filename: str,
        content: bytes,
        content_type: str | None = None,
    ) -> str:
        rel = folder_path.strip("/").replace("/", os.sep)
        dest_dir = self._root / rel
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / filename
        if dest_file.exists():
            stem, suffix = dest_file.stem, dest_file.suffix
            n = 1
            while dest_file.exists():
                dest_file = dest_dir / f"{stem} ({n}){suffix}"
                n += 1
        dest_file.write_bytes(content)
        return str(dest_file)

    def folder_link(self, folder_path: str) -> str | None:
        rel = folder_path.strip("/").replace("/", os.sep)
        return str(self._root / rel)

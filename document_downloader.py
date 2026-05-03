"""Extract document links from email bodies and download them.

Two ways the agent gets bid documents:
  1. Email attachments (handled by the EmailProvider directly).
  2. URLs in the email body, this module:
     - extracts URLs with a tolerant regex
     - filters out obvious non-documents (mailto:, image trackers, etc.)
     - sends a HEAD request to inspect Content-Type / file extension
     - downloads with a size cap

Limitations (documented for the user):
  - Authenticated portals (Procore, BuildingConnected, PlanGrid, iSqFt,
    SmartBidNet, etc.) cannot be auto-downloaded - they require login.
    The agent will surface the raw URL in the notification but will not
    attempt to fetch it.
  - SharePoint / OneDrive share links sent BETWEEN tenants may also fail
    if the recipient hasn't accepted the share.
"""
from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

# Conservative URL regex covering http(s) URLs in plain or HTML-stripped text.
URL_RE = re.compile(
    r"https?://[^\s<>\"'\)\]\}]+",
    re.IGNORECASE,
)

DOC_EXTENSIONS = {
    # Office / PDF
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    # Drawings / BIM (construction)
    ".dwg", ".dxf", ".rvt", ".rfa", ".ifc", ".skp", ".pln",
    # Archives
    ".zip", ".rar", ".7z", ".tar", ".gz",
    # Images / scans
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp",
    # Other common
    ".csv", ".txt", ".rtf", ".eml", ".msg",
}

DOC_CONTENT_TYPE_PREFIXES = (
    "application/pdf",
    "application/zip",
    "application/x-zip",
    "application/x-rar",
    "application/x-7z",
    "application/octet-stream",   # many cloud links serve this for downloads
    "application/vnd.openxmlformats",
    "application/vnd.ms-",
    "application/msword",
    "application/vnd.dwg",
    "application/acad",
    "image/",
    "text/csv",
    "text/plain",
)

# Known portal hosts where auto-download won't work.
PORTAL_HOSTS = {
    "buildingconnected.com",
    "app.buildingconnected.com",
    "app.procore.com", "procore.com",
    "plangrid.com",
    "isqft.com", "constructconnect.com", "app.constructconnect.com",
    "smartbidnet.com",
    "bid.dropbox.com",
    "studio.bluebeam.com",
    "planhub.com",
}

# Common URL trackers / non-document targets we should skip outright.
SKIP_HOSTS = {
    "unsubscribe.", "track.", "click.", "link.", "list-manage.com",
    "sendgrid.net", "mailgun.org", "mandrillapp.com", "sparkpostmail.com",
}


@dataclass
class DownloadedDocument:
    filename: str
    content: bytes
    content_type: str
    source_url: str


def extract_urls(text: str) -> list[str]:
    """Return de-duplicated http(s) URLs from text. Order-preserving."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;:!?)")
        # Strip surrounding markdown formatting characters that snuck in
        url = url.rstrip("*_~`")
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def url_host(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return ""


def is_skippable_host(url: str) -> bool:
    host = url_host(url).lower()
    if not host:
        return True
    for needle in SKIP_HOSTS:
        if needle in host:
            return True
    return False


def is_known_portal(url: str) -> bool:
    host = url_host(url).lower()
    if not host:
        return False
    return any(host == p or host.endswith("." + p) for p in PORTAL_HOSTS)


def looks_like_document_url(url: str) -> bool:
    """Cheap heuristic before we even make a HEAD request."""
    if not url.lower().startswith(("http://", "https://")):
        return False
    if is_skippable_host(url):
        return False
    path = urllib.parse.urlparse(url).path.lower()
    for ext in DOC_EXTENSIONS:
        if path.endswith(ext):
            return True
    # Cloud share patterns we know serve files: dropbox, wetransfer, sharepoint, onedrive
    host = url_host(url).lower()
    if any(s in host for s in ("dropbox.com", "we.tl", "wetransfer.com",
                                "sharepoint.com", "1drv.ms", "onedrive.live.com",
                                "drive.google.com", "box.com", "smartfile.com")):
        return True
    return False


def _filename_from_response(resp: requests.Response, fallback_url: str) -> str:
    cd = resp.headers.get("Content-Disposition", "")
    if cd:
        # filename="..." or filename*=UTF-8''...
        m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", cd, re.IGNORECASE)
        if m:
            return urllib.parse.unquote(m.group(1)).strip().strip('"')
    # Fall back to last URL path segment.
    path = urllib.parse.urlparse(fallback_url).path
    base = path.rsplit("/", 1)[-1] or "download"
    return urllib.parse.unquote(base) or "download"


def _is_document_response(resp: requests.Response, url: str) -> bool:
    ct = (resp.headers.get("Content-Type") or "").lower().split(";")[0].strip()
    if any(ct.startswith(p) for p in DOC_CONTENT_TYPE_PREFIXES):
        return True
    cd = resp.headers.get("Content-Disposition", "").lower()
    if "attachment" in cd:
        return True
    # Final resort: extension on the (possibly redirected) URL.
    final_url = resp.url or url
    path = urllib.parse.urlparse(final_url).path.lower()
    return any(path.endswith(ext) for ext in DOC_EXTENSIONS)


def download_document(
    url: str,
    *,
    max_bytes: int,
    timeout: int = 60,
) -> DownloadedDocument | None:
    """Try to download `url`. Returns None if it isn't a document, fails, or
    exceeds max_bytes.

    Skips known authenticated bid portals.
    """
    if not looks_like_document_url(url):
        return None
    if is_known_portal(url):
        logger.info("Skipping authenticated portal URL (no auto-download): %s", url)
        return None

    try:
        with requests.get(url, stream=True, timeout=timeout, allow_redirects=True) as resp:
            if resp.status_code >= 400:
                logger.info("Document download HTTP %s: %s", resp.status_code, url)
                return None
            if not _is_document_response(resp, url):
                logger.info("URL didn't look like a document (Content-Type=%r): %s",
                            resp.headers.get("Content-Type"), url)
                return None

            # Respect Content-Length up front when present.
            cl = resp.headers.get("Content-Length")
            if cl and cl.isdigit() and int(cl) > max_bytes:
                logger.warning("Skipping %s: %s bytes exceeds cap %s.", url, cl, max_bytes)
                return None

            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    logger.warning("Aborting %s: streamed past cap of %s bytes.", url, max_bytes)
                    return None
                chunks.append(chunk)
            content = b"".join(chunks)
            filename = _filename_from_response(resp, url)
            ctype = (resp.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip()
            return DownloadedDocument(
                filename=filename,
                content=content,
                content_type=ctype,
                source_url=url,
            )
    except requests.RequestException as e:
        logger.info("Download failed for %s: %s", url, e)
        return None


# ---------- Folder name sanitization ----------

_INVALID_PATH_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def sanitize_folder_name(name: str, *, max_len: int = 120) -> str:
    """Make a string safe to use as a OneDrive / Drive folder segment."""
    if not name:
        return "Untitled"
    cleaned = _INVALID_PATH_CHARS.sub("_", name)
    # Trim trailing dots and spaces (Windows path quirk; OneDrive inherits).
    cleaned = cleaned.rstrip(". ").strip()
    if not cleaned:
        cleaned = "Untitled"
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(". ")
    return cleaned

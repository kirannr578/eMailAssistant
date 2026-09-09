#!/usr/bin/env python3
"""Gmail cleanup — one script, one job.

  Scan large attachments -> save to your OneDrive folder -> trash the email.

Usage (from project folder):
  .\\.venv\\Scripts\\python.exe tools\\gmail_cleanup.py status
  .\\.venv\\Scripts\\python.exe tools\\gmail_cleanup.py run 20
  .\\.venv\\Scripts\\python.exe tools\\gmail_cleanup.py run 20 --dry-run

Requirements:
  - client_secret.json + google_token.json (run SetupGoogleAuth.cmd once)
  - OneDrive desktop signed in (files go to OneDrive\\Email Assistant\\Gmail Archive\\...)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tls_setup  # noqa: F401

from dotenv import load_dotenv
from providers.google_auth import GoogleAuth
from providers.gmail import GmailClient
from providers.local_onedrive import LocalOneDriveStorage, detect_onedrive_root

STATE_FILE = Path("reports") / "gmail_cleanup_state.json"
ARCHIVE_FOLDER = "Email Assistant/Gmail Archive"
MIN_SIZE_MB = 5
SCAN_CAP = 500


def _fmt(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024**3:.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024**2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def _sender_folder(sender: str) -> str:
    m = re.search(r"<([^>]+)>", sender)
    email = (m.group(1) if m else sender).strip().lower()
    local = email.split("@", 1)[0]
    safe = re.sub(r"[^\w.\-]+", "_", local).strip("._")
    return safe or "unknown"


def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"freed_bytes": 0, "done": {}}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _gmail() -> GmailClient:
    load_dotenv()
    auth = GoogleAuth(
        client_secrets_path=Path(os.getenv("GOOGLE_CLIENT_SECRETS_PATH", "client_secret.json")),
        token_cache_path=Path(os.getenv("GOOGLE_TOKEN_CACHE_PATH", "google_token.json")),
    )
    return GmailClient(auth)


def _onedrive() -> LocalOneDriveStorage:
    root = detect_onedrive_root()
    if not root:
        raise SystemExit(
            "ERROR: OneDrive folder not found. Sign in to OneDrive desktop, or set\n"
            "  GMAIL_ARCHIVE_LOCAL_ROOT=C:\\Users\\you\\OneDrive  in .env"
        )
    return LocalOneDriveStorage(root)


def cmd_status() -> int:
    state = _load_state()
    done = state.get("done", {})
    freed = int(state.get("freed_bytes", 0))
    print()
    print(f"Gmail cleanup status")
    print(f"  Messages archived + trashed: {len(done)}")
    print(f"  Attachment bytes freed:      {_fmt(freed)}")
    print(f"  State file:                  {STATE_FILE.resolve()}")
    print()
    print("  Empty Gmail Trash to apply this to your Google storage quota.")
    print("  Check usage: https://one.google.com/storage")
    return 0


def cmd_run(limit: int, dry_run: bool) -> int:
    gmail = _gmail()
    storage = _onedrive()
    state = _load_state()
    done: dict = state.setdefault("done", {})

    q = f"has:attachment larger:{MIN_SIZE_MB}M"
    ids = gmail.search_message_ids(q, max_results=SCAN_CAP)
    pending: list[tuple[str, int, str, str, str]] = []  # id, bytes, sender, subject, date

    for mid in ids:
        if mid in done:
            continue
        meta = gmail.get_message_metadata(mid)
        atts = gmail.list_attachments(mid)
        if not atts:
            continue
        total = sum(a.size_bytes for a in atts)
        if total < MIN_SIZE_MB * 1024 * 1024:
            continue
        date = meta.received_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
        pending.append((mid, total, meta.sender, meta.subject, date))

    pending.sort(key=lambda x: x[1], reverse=True)
    batch = pending[:limit]

    if not batch:
        print("Nothing left to clean up.")
        cmd_status()
        return 0

    print(f"{'DRY-RUN' if dry_run else 'RUN'}: {len(batch)} message(s) | OneDrive: {storage.root}")
    session_bytes = 0

    for mid, total, sender, subject, date in batch:
        atts = gmail.list_attachments(mid)
        print()
        print(f"[{_fmt(total)}] {subject[:55]}")
        print(f"  from {sender[:50]}")

        if dry_run:
            for a in atts:
                folder = f"{ARCHIVE_FOLDER}/{date[:4]}/{_sender_folder(sender)}"
                print(f"  would save -> {storage.root / folder.replace('/', os.sep) / a.filename}")
            print(f"  would trash message {mid}")
            continue

        saved = 0
        for a in atts:
            folder = f"{ARCHIVE_FOLDER}/{date[:4]}/{_sender_folder(sender)}"
            data = gmail.download_attachment(mid, a.id)
            path = storage.upload(folder_path=folder, filename=a.filename, content=data)
            print(f"  saved {a.filename}")
            saved += 1

        if saved != len(atts):
            print("  SKIP trash (save failed)")
            continue

        gmail.trash_message(mid)
        done[mid] = {"bytes": total, "subject": subject, "at": datetime.now(timezone.utc).isoformat()}
        state["freed_bytes"] = int(state.get("freed_bytes", 0)) + total
        session_bytes += total
        print(f"  trashed")

    if not dry_run:
        _save_state(state)

    print()
    print(f"Session: {_fmt(session_bytes)} | Total freed (all runs): {_fmt(int(state.get('freed_bytes', 0)))}")
    print(f"Remaining in scan: {max(0, len(pending) - len(batch))} messages")
    if not dry_run:
        print("Empty Gmail Trash to update your storage quota.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Gmail cleanup: OneDrive backup + trash.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Show how much has been freed so far")
    run_p = sub.add_parser("run", help="Process N largest unprocessed messages")
    run_p.add_argument("count", type=int, help="How many messages this run")
    run_p.add_argument("--dry-run", action="store_true", help="Preview only")
    args = p.parse_args()

    if args.cmd == "status":
        return cmd_status()
    return cmd_run(args.count, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

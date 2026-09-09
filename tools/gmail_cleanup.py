#!/usr/bin/env python3
"""Gmail cleanup — one script, one job.

  1. scan  — find large emails (slow, run once; waits between API calls)
  2. run N — backup N to OneDrive + trash (uses scan results; run repeatedly)

Usage:
  GmailCleanup.cmd scan
  GmailCleanup.cmd run 10
  GmailCleanup.cmd status
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypeVar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tls_setup  # noqa: F401

from dotenv import load_dotenv
from googleapiclient.errors import HttpError
from providers.google_auth import GoogleAuth
from providers.gmail import GmailClient
from providers.local_onedrive import LocalOneDriveStorage, detect_onedrive_root

STATE_FILE = Path("reports") / "gmail_cleanup_state.json"
ARCHIVE_FOLDER = "Email Assistant/Gmail Archive"
MIN_SIZE_MB = 5
SCAN_CAP = 200
API_DELAY_SEC = 1.2  # pause between Gmail reads to avoid per-minute quota

T = TypeVar("T")


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
    return {"freed_bytes": 0, "done": {}, "queue": []}


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


def _gmail_call(label: str, fn: Callable[[], T]) -> T:
    """Retry Gmail API calls when Google returns per-minute quota errors."""
    for attempt in range(8):
        try:
            return fn()
        except HttpError as e:
            err = str(e)
            if e.resp.status in (403, 429) and ("rateLimitExceeded" in err or "Quota exceeded" in err):
                wait = min(120, int(API_DELAY_SEC * (2 ** attempt) * 5))
                print(f"  Gmail rate limit ({label}) — waiting {wait}s then retrying...")
                time.sleep(wait)
                continue
            raise
    raise SystemExit(
        "Gmail API rate limit still exceeded. Wait 2-3 minutes and run the same command again."
    )


def cmd_status() -> int:
    state = _load_state()
    done = state.get("done", {})
    queue = state.get("queue", [])
    freed = int(state.get("freed_bytes", 0))
    pending = [q for q in queue if q["id"] not in done]
    print()
    print("Gmail cleanup status")
    print(f"  Archived + trashed:  {len(done)} messages  ({_fmt(freed)})")
    print(f"  Waiting in queue:  {len(pending)} messages")
    print(f"  State file:          {STATE_FILE.resolve()}")
    print()
    print("  Empty Gmail Trash to update your Google storage quota.")
    print("  https://one.google.com/storage")
    return 0


def cmd_scan() -> int:
    gmail = _gmail()
    state = _load_state()
    done = state.setdefault("done", {})
    existing_ids = {q["id"] for q in state.get("queue", [])}

    print(f"Scanning Gmail for attachments > {MIN_SIZE_MB} MB (slow, ~{API_DELAY_SEC}s per message)...")
    ids = _gmail_call(
        "search",
        lambda: gmail.search_message_ids(f"has:attachment larger:{MIN_SIZE_MB}M", max_results=SCAN_CAP),
    )
    print(f"Found {len(ids)} candidate message IDs.")

    queue: list[dict] = list(state.get("queue", []))
    added = 0
    for i, mid in enumerate(ids, 1):
        if mid in done or mid in existing_ids:
            continue
        if i > 1:
            time.sleep(API_DELAY_SEC)
        try:
            meta, atts = _gmail_call("read", lambda m=mid: gmail.get_message_with_attachments(m))
        except HttpError as e:
            print(f"  Stopped at message {i}/{len(ids)}: {e}")
            break
        if not atts:
            continue
        total = sum(a.size_bytes for a in atts)
        if total < MIN_SIZE_MB * 1024 * 1024:
            continue
        queue.append({
            "id": mid,
            "bytes": total,
            "sender": meta.sender,
            "subject": meta.subject,
            "date": meta.received_at.astimezone(timezone.utc).strftime("%Y-%m-%d"),
            "attachments": [
                {"id": a.id, "filename": a.filename, "size_bytes": a.size_bytes}
                for a in atts
            ],
        })
        existing_ids.add(mid)
        added += 1
        if added % 10 == 0:
            print(f"  scanned {i}/{len(ids)}, queued {added} new...")

    queue.sort(key=lambda q: q["bytes"], reverse=True)
    state["queue"] = queue
    state["scanned_at"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)

    pending = [q for q in queue if q["id"] not in done]
    print()
    print(f"Scan done. {added} new message(s) added to queue.")
    print(f"Ready to process: {len(pending)} message(s). Run: GmailCleanup.cmd run 10")
    return 0


def cmd_run(limit: int, dry_run: bool) -> int:
    gmail = _gmail()
    storage = _onedrive()
    state = _load_state()
    done: dict = state.setdefault("done", {})
    queue = state.get("queue", [])
    pending = [q for q in queue if q["id"] not in done]

    if not pending:
        print("Queue is empty. Run first:  GmailCleanup.cmd scan")
        return 1

    batch = pending[:limit]
    print(f"{'DRY-RUN' if dry_run else 'RUN'}: {len(batch)} message(s) | OneDrive: {storage.root}")
    session_bytes = 0

    for item in batch:
        mid = item["id"]
        total = int(item["bytes"])
        sender = item["sender"]
        subject = item["subject"]
        date = item["date"]
        atts = item["attachments"]

        print()
        print(f"[{_fmt(total)}] {subject[:55]}")
        print(f"  from {sender[:50]}")

        if dry_run:
            for a in atts:
                folder = f"{ARCHIVE_FOLDER}/{date[:4]}/{_sender_folder(sender)}"
                print(f"  would save -> {storage.root / folder.replace('/', os.sep) / a['filename']}")
            print(f"  would trash message {mid}")
            continue

        saved = 0
        for a in atts:
            folder = f"{ARCHIVE_FOLDER}/{date[:4]}/{_sender_folder(sender)}"
            data = _gmail_call(
                "download",
                lambda m=mid, aid=a["id"]: gmail.download_attachment(m, aid),
            )
            storage.upload(folder_path=folder, filename=a["filename"], content=data)
            print(f"  saved {a['filename']}")
            saved += 1
            time.sleep(0.3)

        if saved != len(atts):
            print("  SKIP trash (save failed)")
            continue

        _gmail_call("trash", lambda m=mid: gmail.trash_message(m))
        done[mid] = {"bytes": total, "subject": subject, "at": datetime.now(timezone.utc).isoformat()}
        state["freed_bytes"] = int(state.get("freed_bytes", 0)) + total
        session_bytes += total
        print("  trashed")
        time.sleep(API_DELAY_SEC)

    if not dry_run:
        _save_state(state)

    print()
    print(f"Session: {_fmt(session_bytes)} | Total freed: {_fmt(int(state.get('freed_bytes', 0)))}")
    print(f"Still in queue: {max(0, len(pending) - len(batch))}")
    if not dry_run:
        print("Empty Gmail Trash to update your storage quota.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Gmail cleanup: OneDrive backup + trash.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="How much freed / how many left")
    sub.add_parser("scan", help="Find large emails (run once; rate-limit safe)")
    run_p = sub.add_parser("run", help="Process N messages from the scan queue")
    run_p.add_argument("count", type=int, help="How many messages this run")
    run_p.add_argument("--dry-run", action="store_true", help="Preview only")
    args = p.parse_args()

    if args.cmd == "status":
        return cmd_status()
    if args.cmd == "scan":
        return cmd_scan()
    return cmd_run(args.count, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

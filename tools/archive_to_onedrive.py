"""Archive Gmail attachments to OneDrive and trash source messages.

Dry-run by default. Use --execute --confirm to perform uploads and trash.

Usage:
    python tools/archive_to_onedrive.py --from-report reports/gmail_storage_20260909.csv --limit 20
    python tools/archive_to_onedrive.py --from-report reports/gmail_storage_20260909.csv --execute --confirm --limit 20
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
_tools = Path(__file__).resolve().parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_tools))

import tls_setup  # noqa: E402, F401

from gmail_storage_common import (  # noqa: E402
    AuditRow,
    load_google_client,
    load_report,
    sanitize_sender_folder,
    scan_large_attachments,
)
from providers.ms_graph_auth import GraphAuth  # noqa: E402
from providers.gmail import GmailClient  # noqa: E402
from providers.onedrive import OneDriveClient  # noqa: E402

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AttachmentTarget:
    message_id: str
    attachment_id: str
    filename: str
    size_bytes: int
    sender: str
    subject: str
    date: str
    web_link: str


def _load_onedrive_client() -> OneDriveClient:
    from dotenv import load_dotenv

    load_dotenv()
    client_id = os.getenv("MS_CLIENT_ID", "").strip()
    if not client_id:
        raise RuntimeError(
            "MS_CLIENT_ID is required for OneDrive upload. "
            "Run scripts/setup_entra.ps1 or set it in .env."
        )
    auth = GraphAuth(
        client_id=client_id,
        tenant_id=os.getenv("MS_TENANT_ID", "common"),
        token_cache_path=Path(os.getenv("MS_TOKEN_CACHE_PATH", "token_cache.bin")),
    )
    return OneDriveClient(auth)


def _archive_base_folder() -> str:
    from dotenv import load_dotenv

    load_dotenv()
    return os.getenv("GMAIL_ARCHIVE_FOLDER", "Email Assistant/Gmail Archive").strip()


def _split_fields(value: str) -> list[str]:
    return [part.strip() for part in value.split(";") if part.strip()]


def expand_targets(rows: list[AuditRow], *, all_attachments: bool) -> list[AttachmentTarget]:
    targets: list[AttachmentTarget] = []
    for row in rows:
        if row.attachment_count > 1 and not all_attachments:
            logger.info(
                "Skipping multi-attachment message %s (%d files); use --all-attachments",
                row.message_id,
                row.attachment_count,
            )
            continue
        names = _split_fields(row.filenames)
        ids = _split_fields(row.attachment_ids)
        sizes = [int(s) for s in _split_fields(row.sizes_bytes)]
        if not (len(names) == len(ids) == len(sizes)):
            logger.warning("Skipping malformed row for message %s", row.message_id)
            continue
        for filename, att_id, size in zip(names, ids, sizes):
            targets.append(AttachmentTarget(
                message_id=row.message_id,
                attachment_id=att_id,
                filename=filename,
                size_bytes=size,
                sender=row.sender,
                subject=row.subject,
                date=row.date,
                web_link=row.web_link,
            ))
    return targets


def _onedrive_folder(target: AttachmentTarget, base_folder: str) -> str:
    year = target.date[:4] if len(target.date) >= 4 else "unknown"
    sender = sanitize_sender_folder(target.sender)
    return f"{base_folder}/{year}/{sender}"


def _log_path(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"archive_log_{datetime.now().strftime('%Y%m%d')}.jsonl"


def _append_log(log_file: Path, record: dict) -> None:
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def run_archive(
    *,
    targets: list[AttachmentTarget],
    gmail: GmailClient | None,
    onedrive: OneDriveClient | None,
    base_folder: str,
    dry_run: bool,
    limit: int,
    log_file: Path,
) -> int:
    by_message: dict[str, list[AttachmentTarget]] = {}
    for t in targets:
        by_message.setdefault(t.message_id, []).append(t)

    message_ids = list(by_message.keys())[:limit]
    processed_messages = 0
    for message_id in message_ids:
        msg_targets = by_message[message_id]
        uploaded: list[dict] = []
        print()
        print(f"Message {message_id}: {msg_targets[0].subject[:60]}")
        for target in msg_targets:
            folder = _onedrive_folder(target, base_folder)
            dest = f"{folder}/{target.filename}"
            if dry_run:
                print(f"  [DRY-RUN] upload -> {dest} ({target.size_bytes} bytes)")
                uploaded.append({"filename": target.filename, "dest": dest, "dry_run": True})
                continue

            if gmail is None or onedrive is None:
                print("  ERROR: execute mode requires Gmail and OneDrive auth", file=sys.stderr)
                return 2

            content = gmail.download_attachment(message_id, target.attachment_id)
            if len(content) != target.size_bytes and target.size_bytes > 0:
                logger.warning(
                    "Size mismatch for %s: expected %d, got %d",
                    target.filename,
                    target.size_bytes,
                    len(content),
                )
            web_url = onedrive.upload(
                folder_path=folder,
                filename=target.filename,
                content=content,
            )
            if not web_url:
                print(f"  ERROR: upload failed for {target.filename}", file=sys.stderr)
                _append_log(log_file, {
                    "status": "upload_failed",
                    "message_id": message_id,
                    "filename": target.filename,
                    "dest": dest,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                break
            print(f"  uploaded -> {web_url}")
            uploaded.append({
                "filename": target.filename,
                "dest": dest,
                "web_url": web_url,
            })

        if dry_run:
            print(f"  [DRY-RUN] trash message {message_id}")
            _append_log(log_file, {
                "status": "dry_run",
                "message_id": message_id,
                "uploads": uploaded,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            processed_messages += 1
            continue

        if len(uploaded) != len(msg_targets):
            print(f"  SKIP trash: not all attachments uploaded for {message_id}", file=sys.stderr)
            _append_log(log_file, {
                "status": "partial_upload",
                "message_id": message_id,
                "uploads": uploaded,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            continue

        if gmail is None:
            print("  ERROR: Gmail client required to trash messages", file=sys.stderr)
            return 2
        gmail.trash_message(message_id)
        print(f"  trashed message {message_id}")
        _append_log(log_file, {
            "status": "archived",
            "message_id": message_id,
            "gmail_link": msg_targets[0].web_link,
            "uploads": uploaded,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        processed_messages += 1

    print()
    print(f"Processed {processed_messages} message(s). Log: {log_file}")
    return 0


def _build_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Archive Gmail attachments to OneDrive and trash source emails."
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-report", type=Path, help="CSV from gmail_storage_audit.py")
    src.add_argument("--rescan", action="store_true",
                     help="Re-run Gmail scan instead of loading a CSV report")
    p.add_argument("--min-size-mb", type=int, default=5,
                   help="Used with --rescan (default: 5)")
    p.add_argument("--max-messages", type=int, default=500,
                   help="Used with --rescan (default: 500)")
    p.add_argument("--limit", type=int, default=20,
                   help="Max messages to process this run (default: 20)")
    p.add_argument("--all-attachments", action="store_true",
                   help="Include messages with multiple attachments (default: skip them)")
    p.add_argument("--execute", action="store_true",
                   help="Perform uploads and trash (default is dry-run preview)")
    p.add_argument("--confirm", action="store_true",
                   help="Required with --execute (safety gate)")
    p.add_argument("--output-dir", type=Path, default=Path("reports"),
                   help="Directory for archive log (default: reports/)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _build_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if args.execute and not args.confirm:
        print("ERROR: --execute requires --confirm", file=sys.stderr)
        return 2
    dry_run = not args.execute

    gmail: GmailClient | None = None
    if args.rescan or args.execute:
        try:
            gmail = load_google_client()
        except Exception as e:
            print(f"ERROR: Google auth failed: {e}", file=sys.stderr)
            return 2

    onedrive: OneDriveClient | None = None
    if args.execute:
        try:
            onedrive = _load_onedrive_client()
        except Exception as e:
            print(f"ERROR: OneDrive auth failed: {e}", file=sys.stderr)
            return 2

    if args.from_report:
        if not args.from_report.exists():
            print(f"ERROR: report not found: {args.from_report}", file=sys.stderr)
            return 2
        rows = load_report(args.from_report)
    else:
        assert gmail is not None
        rows = scan_large_attachments(
            gmail,
            min_size_mb=args.min_size_mb,
            max_messages=args.max_messages,
        )

    targets = expand_targets(rows, all_attachments=args.all_attachments)
    if not targets:
        print("No eligible messages to archive.")
        return 0

    mode = "DRY-RUN" if dry_run else "EXECUTE"
    print(f"Mode: {mode} | Eligible attachments: {len(targets)} | Limit: {args.limit} messages")
    return run_archive(
        targets=targets,
        gmail=gmail,
        onedrive=onedrive,
        base_folder=_archive_base_folder(),
        dry_run=dry_run,
        limit=args.limit,
        log_file=_log_path(args.output_dir),
    )


if __name__ == "__main__":
    raise SystemExit(main())

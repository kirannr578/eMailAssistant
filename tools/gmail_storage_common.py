"""Shared helpers for Gmail storage audit and OneDrive archive tools."""
from __future__ import annotations

import csv
import json
import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from providers.google_auth import GoogleAuth
from providers.gmail import GmailClient

logger = logging.getLogger(__name__)

CSV_FIELDS = [
    "message_id",
    "date",
    "sender",
    "subject",
    "total_bytes",
    "attachment_count",
    "filenames",
    "attachment_ids",
    "sizes_bytes",
    "web_link",
]


@dataclass(frozen=True)
class AuditRow:
    message_id: str
    date: str
    sender: str
    subject: str
    total_bytes: int
    attachment_count: int
    filenames: str
    attachment_ids: str
    sizes_bytes: str
    web_link: str

    def to_dict(self) -> dict[str, str | int]:
        return {
            "message_id": self.message_id,
            "date": self.date,
            "sender": self.sender,
            "subject": self.subject,
            "total_bytes": self.total_bytes,
            "attachment_count": self.attachment_count,
            "filenames": self.filenames,
            "attachment_ids": self.attachment_ids,
            "sizes_bytes": self.sizes_bytes,
            "web_link": self.web_link,
        }


def load_google_client() -> GmailClient:
    from dotenv import load_dotenv

    load_dotenv()
    auth = GoogleAuth(
        client_secrets_path=Path(os.getenv("GOOGLE_CLIENT_SECRETS_PATH", "client_secret.json")),
        token_cache_path=Path(os.getenv("GOOGLE_TOKEN_CACHE_PATH", "google_token.json")),
    )
    return GmailClient(auth)


def format_bytes(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / (1024 ** 3):.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / (1024 ** 2):.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def sanitize_sender_folder(sender: str) -> str:
    match = re.search(r"<([^>]+)>", sender)
    email = match.group(1) if match else sender
    email = email.strip().lower()
    local = email.split("@", 1)[0]
    safe = re.sub(r"[^\w.\-]+", "_", local).strip("._")
    return safe or "unknown_sender"


def scan_large_attachments(
    client: GmailClient,
    *,
    min_size_mb: int,
    max_messages: int,
) -> list[AuditRow]:
    q = f"has:attachment larger:{min_size_mb}M"
    logger.info("Gmail query: %s", q)
    message_ids = client.search_message_ids(q, max_results=max_messages)
    logger.info("Found %d candidate messages (cap %d)", len(message_ids), max_messages)

    rows: list[AuditRow] = []
    for i, mid in enumerate(message_ids, start=1):
        try:
            meta = client.get_message_metadata(mid)
            attachments = client.list_attachments(mid)
            if not attachments:
                continue
            total = sum(a.size_bytes for a in attachments)
            if total < min_size_mb * 1024 * 1024:
                continue
            rows.append(AuditRow(
                message_id=mid,
                date=meta.received_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                sender=meta.sender,
                subject=meta.subject,
                total_bytes=total,
                attachment_count=len(attachments),
                filenames="; ".join(a.filename for a in attachments),
                attachment_ids="; ".join(a.id for a in attachments),
                sizes_bytes="; ".join(str(a.size_bytes) for a in attachments),
                web_link=meta.web_link or f"https://mail.google.com/mail/u/0/#inbox/{mid}",
            ))
        except Exception as e:
            logger.warning("Skipping message %s: %s", mid, e)
        if i % 25 == 0:
            logger.info("Processed %d / %d messages...", i, len(message_ids))
    rows.sort(key=lambda r: r.total_bytes, reverse=True)
    return rows


def write_report(rows: list[AuditRow], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    path = output_dir / f"gmail_storage_{stamp}.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_dict())
    return path


def print_summary(rows: list[AuditRow]) -> None:
    total_bytes = sum(r.total_bytes for r in rows)
    print()
    print("=" * 72)
    print("Gmail Storage Audit Summary")
    print("=" * 72)
    print(f"Messages with large attachments: {len(rows)}")
    print(f"Estimated reclaimable attachment bytes: {format_bytes(total_bytes)}")
    print()

    print("Top 20 largest messages:")
    for row in rows[:20]:
        print(
            f"  {format_bytes(row.total_bytes):>10}  "
            f"{row.date[:10]}  {row.sender[:40]:<40}  {row.subject[:50]}"
        )
    print()

    by_sender: dict[str, int] = defaultdict(int)
    for row in rows:
        by_sender[row.sender] += row.total_bytes
    top_senders = sorted(by_sender.items(), key=lambda kv: kv[1], reverse=True)[:10]
    print("Top 10 senders by attachment bytes:")
    for sender, nbytes in top_senders:
        print(f"  {format_bytes(nbytes):>10}  {sender}")
    print()
    print("Note: Google pools Gmail + Drive + Photos into one quota.")
    print("If this total is small vs. your account usage, check Drive/Photos too.")
    print("=" * 72)


def load_report(path: Path) -> list[AuditRow]:
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        missing = set(CSV_FIELDS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Report missing columns: {sorted(missing)}")
        rows: list[AuditRow] = []
        for raw in reader:
            rows.append(AuditRow(
                message_id=raw["message_id"],
                date=raw["date"],
                sender=raw["sender"],
                subject=raw["subject"],
                total_bytes=int(raw["total_bytes"]),
                attachment_count=int(raw["attachment_count"]),
                filenames=raw["filenames"],
                attachment_ids=raw["attachment_ids"],
                sizes_bytes=raw["sizes_bytes"],
                web_link=raw["web_link"],
            ))
    rows.sort(key=lambda r: r.total_bytes, reverse=True)
    return rows


def load_logged_message_ids(
    output_dir: Path,
    *,
    statuses: frozenset[str],
) -> set[str]:
    """Return message IDs already recorded in archive_log_*.jsonl with given statuses."""
    ids: set[str] = set()
    if not output_dir.is_dir():
        return ids
    for log_file in sorted(output_dir.glob("archive_log_*.jsonl")):
        try:
            for line in log_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if record.get("status") in statuses and record.get("message_id"):
                    ids.add(str(record["message_id"]))
        except Exception as e:
            logger.warning("Could not read archive log %s: %s", log_file, e)
    return ids

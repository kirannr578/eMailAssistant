"""Report how much Gmail space archiving has reclaimed (from logs + audit CSV).

Usage:
    python tools/archive_progress.py --report reports/gmail_storage_20260908.csv
    python tools/archive_progress.py --report reports/gmail_storage_20260908.csv --log-dir reports
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
_tools = Path(__file__).resolve().parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_tools))

from gmail_storage_common import format_bytes, load_report  # noqa: E402


def _load_log_records(log_dir: Path) -> list[dict]:
    records: list[dict] = []
    for log_file in sorted(log_dir.glob("archive_log_*.jsonl")):
        for line in log_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _bytes_for_messages(report_rows: dict[str, int], message_ids: set[str]) -> int:
    return sum(report_rows.get(mid, 0) for mid in message_ids)


def main() -> int:
    p = argparse.ArgumentParser(description="Summarize Gmail archive progress and space reclaimed.")
    p.add_argument("--report", type=Path, required=True, help="gmail_storage_*.csv from audit")
    p.add_argument("--log-dir", type=Path, default=Path("reports"), help="Directory with archive_log_*.jsonl")
    args = p.parse_args()

    if not args.report.exists():
        print(f"ERROR: report not found: {args.report}", file=sys.stderr)
        return 2

    rows = load_report(args.report)
    by_id = {r.message_id: r.total_bytes for r in rows}
    total_audit_bytes = sum(by_id.values())

    records = _load_log_records(args.log_dir)
    by_status: dict[str, set[str]] = defaultdict(set)
    for rec in records:
        status = rec.get("status", "unknown")
        mid = rec.get("message_id")
        if mid:
            by_status[status].add(str(mid))

    archived_ids = by_status.get("archived", set())
    dry_run_ids = by_status.get("dry_run", set())
    failed_ids = by_status.get("upload_failed", set()) | by_status.get("partial_upload", set())

    archived_bytes = _bytes_for_messages(by_id, archived_ids)
    dry_run_bytes = _bytes_for_messages(by_id, dry_run_ids)
    failed_bytes = _bytes_for_messages(by_id, failed_ids)
    remaining_ids = set(by_id.keys()) - archived_ids
    remaining_bytes = _bytes_for_messages(by_id, remaining_ids)

    print()
    print("=" * 72)
    print("Gmail Archive Progress")
    print("=" * 72)
    print(f"Audit report:     {args.report}")
    print(f"Messages in CSV:  {len(by_id)}")
    print(f"Total in audit:   {format_bytes(total_audit_bytes)} (large attachments scanned)")
    print()
    print(f"Archived (trashed): {len(archived_ids):>5} messages  {format_bytes(archived_bytes):>12}")
    print(f"Dry-run only:       {len(dry_run_ids):>5} messages  {format_bytes(dry_run_bytes):>12}  (not freed yet)")
    print(f"Failed/partial:     {len(failed_ids):>5} messages  {format_bytes(failed_bytes):>12}")
    print(f"Not yet processed:  {len(remaining_ids):>5} messages  {format_bytes(remaining_bytes):>12}")
    print()
    print("Space actually freed in Gmail:")
    print(f"  {format_bytes(archived_bytes)} moved to Trash (after successful OneDrive save)")
    print()
    print("Note: Gmail quota updates after you EMPTY TRASH (or after Google purges it).")
    print("      Check live usage: https://one.google.com/storage")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

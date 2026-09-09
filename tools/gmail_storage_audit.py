"""Read-only Gmail storage audit: find large attachments and write a CSV report.

Usage:
    python tools/gmail_storage_audit.py --min-size-mb 5 --max-messages 500
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
_tools = Path(__file__).resolve().parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_tools))

import tls_setup  # noqa: E402, F401

from gmail_storage_common import (  # noqa: E402
    load_google_client,
    print_summary,
    scan_large_attachments,
    write_report,
)


def _build_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Audit Gmail for large attachments (read-only; no uploads or deletes)."
    )
    p.add_argument("--min-size-mb", type=int, default=5,
                   help="Minimum total attachment size per message (default: 5)")
    p.add_argument("--max-messages", type=int, default=500,
                   help="Maximum messages to scan (default: 500)")
    p.add_argument("--output-dir", type=Path, default=Path("reports"),
                   help="Directory for CSV report (default: reports/)")
    p.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    return p.parse_args()


def main() -> int:
    args = _build_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    try:
        client = load_google_client()
    except Exception as e:
        print(f"ERROR: Could not authenticate with Google: {e}", file=sys.stderr)
        print("Ensure GOOGLE_CLIENT_SECRETS_PATH points to client_secret.json and run:", file=sys.stderr)
        print("  python main.py --auth", file=sys.stderr)
        return 2

    rows = scan_large_attachments(
        client,
        min_size_mb=args.min_size_mb,
        max_messages=args.max_messages,
    )
    report_path = write_report(rows, args.output_dir)
    print_summary(rows)
    print(f"Full report written to: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Summarize a gmail_storage_*.csv audit report.

Usage:
    python tools/analyze_storage_report.py reports/gmail_storage_20260908.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
_tools = Path(__file__).resolve().parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_tools))

from gmail_storage_common import load_report, print_summary  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Analyze a Gmail storage audit CSV.")
    p.add_argument("report", type=Path, help="Path to gmail_storage_YYYYMMDD.csv")
    args = p.parse_args()
    if not args.report.exists():
        print(f"ERROR: not found: {args.report}", file=sys.stderr)
        return 2
    rows = load_report(args.report)
    print(f"Report: {args.report.resolve()}")
    print_summary(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

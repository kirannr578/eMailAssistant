"""Local-only LLM test harness for the EmailAnalyzer.

Save an email body (with optional headers) to a text file, point this script
at it, and it will run the analyzer against your configured LLM and print the
resulting Analysis object as readable JSON. Doesn't touch your mailbox,
calendar, or notification channels - safe to run anytime to validate prompt
tuning or test what the agent would extract from a specific email.

Supported file formats:

  1) Plain body only - just paste the email content into a .txt file.

  2) Headers + body, RFC-822 style (header lines, blank line, body):

         Subject: Invitation to Bid - Project XYZ
         From: estimator@gc.example.com
         To: rocky@blueprintconstructs.com

         Good morning,

         This is an official notification...

Recognized header keys (case-insensitive): Subject, From, To, Date.
Anything not provided in the file can be supplied via CLI flags. CLI flags
override file headers.

Usage:
    python tools/test_analyze.py path/to/email.txt
    python tools/test_analyze.py path/to/email.txt --subject "Override subject"
    python tools/test_analyze.py path/to/email.txt --raw   (skip header parsing)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make project root importable when run as `python tools/test_analyze.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer import Analysis, EmailAnalyzer  # noqa: E402
from config import load_settings  # noqa: E402
from dateutil import parser as date_parser  # noqa: E402


_HEADER_KEYS = {"subject", "from", "to", "date"}


def _parse_email_file(path: Path, *, raw: bool) -> tuple[dict[str, str], str]:
    """Return (headers_lower_keyed, body)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if raw:
        return {}, text

    lines = text.splitlines()
    header_lines: list[str] = []
    body_start = 0
    saw_header = False

    # Walk the top of the file looking for "Key: value" lines until we hit
    # a blank line or a line that doesn't look like a header.
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            if saw_header:
                body_start = i + 1
                break
            else:
                # Blank line before any header => no headers, body starts here
                body_start = i + 1
                continue
        # Heuristic: "Key: value" where Key is a known header
        if ":" in line:
            key, _, _ = line.partition(":")
            if key.strip().lower() in _HEADER_KEYS:
                header_lines.append(line)
                saw_header = True
                continue
        # First non-header, non-blank line: treat the rest as body.
        body_start = i
        break
    else:
        # File was nothing but headers (no body)
        body_start = len(lines)

    headers: dict[str, str] = {}
    for hl in header_lines:
        key, _, val = hl.partition(":")
        headers[key.strip().lower()] = val.strip()

    body = "\n".join(lines[body_start:]).lstrip("\n")
    return headers, body


def _build_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run the EmailAnalyzer against a saved email file (no side effects)."
    )
    p.add_argument("file", type=Path, help="Path to a .txt or .eml file with the email content.")
    p.add_argument("--subject", help="Override the Subject (default: from file or 'Test bid email').")
    p.add_argument("--from", dest="sender",
                   help="Override the From address (default: from file or 'sender@example.com').")
    p.add_argument("--to",
                   help="Override the To address (default: from file or your MAILBOX_ADDRESS).")
    p.add_argument("--received",
                   help="ISO 8601 'received at' time (default: from file Date or now in your timezone).")
    p.add_argument("--raw", action="store_true",
                   help="Skip header parsing - treat the whole file as the body.")
    p.add_argument("--bid-only", action="store_true",
                   help="Print only the bid-related fields, not the full Analysis.")
    return p.parse_args()


def _bid_view(a: Analysis) -> dict:
    return {
        "is_bid_request": a.is_bid_request,
        "bid_confidence": a.bid_confidence,
        "bid_project_name": a.bid_project_name,
        "bid_project_location": a.bid_project_location,
        "bid_project_type": a.bid_project_type,
        "bid_reference_number": a.bid_reference_number,
        "bid_due_date_iso": a.bid_due_date_iso,
        "bid_submission_method": a.bid_submission_method,
        "rfi_due_date_iso": a.rfi_due_date_iso,
        "pre_bid_meeting_iso": a.pre_bid_meeting_iso,
        "pre_bid_meeting_end_iso": a.pre_bid_meeting_end_iso,
        "pre_bid_meeting_mandatory": a.pre_bid_meeting_mandatory,
        "pre_bid_meeting_location": a.pre_bid_meeting_location,
        "pre_bid_meeting_link": a.pre_bid_meeting_link,
        "pre_bid_contact": a.pre_bid_contact,
        "bid_scope_summary": a.bid_scope_summary,
        "bid_contact": a.bid_contact,
        "summary": a.summary,
        "urgency": a.urgency,
        "suggested_action": a.suggested_action,
        "notification_text": a.notification_text,
    }


def main() -> int:
    args = _build_args()
    if not args.file.exists():
        print(f"ERROR: file not found: {args.file}", file=sys.stderr)
        return 2

    headers, body = _parse_email_file(args.file, raw=args.raw)
    settings = load_settings()

    subject = args.subject or headers.get("subject") or "Test bid email"
    sender = args.sender or headers.get("from") or "sender@example.com"
    to_field = args.to or headers.get("to") or settings.mailbox_address
    to_list = [s.strip() for s in to_field.split(",") if s.strip()]

    if args.received:
        received_at = date_parser.isoparse(args.received)
    elif headers.get("date"):
        try:
            received_at = date_parser.parse(headers["date"])
        except Exception:
            received_at = datetime.now(timezone.utc)
    else:
        received_at = datetime.now(timezone.utc)

    print("---- Analyzing ----")
    print(f"  Provider:  {settings.llm_provider}")
    print(f"  Model:     {settings.llm_model}")
    print(f"  Timezone:  {settings.user_timezone}")
    print(f"  Subject:   {subject}")
    print(f"  From:      {sender}")
    print(f"  To:        {', '.join(to_list)}")
    print(f"  Received:  {received_at.isoformat()}")
    print(f"  Body chars: {len(body)}")
    print()

    analyzer = EmailAnalyzer(
        provider=settings.llm_provider,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        user_timezone=settings.user_timezone,
        base_url=settings.llm_base_url,
        azure_endpoint=settings.azure_openai_endpoint,
        azure_api_version=settings.azure_openai_api_version,
        company_name=settings.company_name,
        company_aliases=settings.company_aliases,
    )

    result = analyzer.analyze(
        sender=sender,
        to=to_list,
        subject=subject,
        body=body,
        received_at=received_at,
    )

    print("---- Analysis ----")
    if args.bid_only:
        print(json.dumps(_bid_view(result), indent=2, default=str))
    else:
        print(json.dumps(result.model_dump(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Tests for the test_analyze.py harness's header parsing."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make tools/ importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from test_analyze import _parse_email_file  # type: ignore  # noqa: E402


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "email.txt"
    p.write_text(content, encoding="utf-8")
    return p


def test_parses_headers_and_body(tmp_path: Path):
    p = _write(tmp_path, (
        "Subject: Invitation to Bid\n"
        "From: alice@gc.example.com\n"
        "To: rocky@bp.example.com\n"
        "\n"
        "Good morning,\n\nPlease find the bid package attached.\n"
    ))
    headers, body = _parse_email_file(p, raw=False)
    assert headers["subject"] == "Invitation to Bid"
    assert headers["from"] == "alice@gc.example.com"
    assert headers["to"] == "rocky@bp.example.com"
    assert "Good morning" in body
    assert "Please find" in body


def test_handles_no_headers(tmp_path: Path):
    p = _write(tmp_path, "Just the email body, nothing else.\n")
    headers, body = _parse_email_file(p, raw=False)
    assert headers == {}
    assert "Just the email body" in body


def test_raw_mode_skips_header_parsing(tmp_path: Path):
    # The "Subject:" line should be in the body, not parsed as a header.
    p = _write(tmp_path, (
        "Subject: This shouldn't be parsed\n\nbody here\n"
    ))
    headers, body = _parse_email_file(p, raw=True)
    assert headers == {}
    assert "Subject: This shouldn't be parsed" in body


def test_ignores_unknown_keys_as_headers(tmp_path: Path):
    # "Reference:" isn't in our known set; should be treated as body start.
    p = _write(tmp_path, (
        "Reference: 405-26R0015165\n"
        "\n"
        "body\n"
    ))
    headers, body = _parse_email_file(p, raw=False)
    assert headers == {}
    assert "Reference:" in body
    assert "body" in body


def test_case_insensitive_header_keys(tmp_path: Path):
    p = _write(tmp_path, (
        "SUBJECT: caps\n"
        "From: someone\n"
        "\n"
        "x\n"
    ))
    headers, _ = _parse_email_file(p, raw=False)
    assert headers["subject"] == "caps"
    assert headers["from"] == "someone"


def test_date_header_recognized(tmp_path: Path):
    p = _write(tmp_path, (
        "Subject: x\n"
        "Date: Mon, 12 May 2026 11:00:00 -0500\n"
        "\n"
        "body\n"
    ))
    headers, _ = _parse_email_file(p, raw=False)
    assert "12 May 2026" in headers["date"]

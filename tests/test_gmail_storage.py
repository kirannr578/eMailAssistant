"""Tests for Gmail storage audit and archive helpers."""
from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from providers.base import EmailAttachment
from tools.gmail_storage_common import (
    AuditRow,
    CSV_FIELDS,
    format_bytes,
    load_report,
    sanitize_sender_folder,
    scan_large_attachments,
    write_report,
)


class TestFormatBytes:
    def test_gb(self):
        assert "GB" in format_bytes(2 * 1024 ** 3)

    def test_mb(self):
        assert "MB" in format_bytes(10 * 1024 ** 2)

    def test_kb(self):
        assert "KB" in format_bytes(2048)


class TestSanitizeSenderFolder:
    def test_angle_bracket_email(self):
        assert sanitize_sender_folder("John Doe <john.doe@example.com>") == "john.doe"

    def test_plain_email(self):
        assert sanitize_sender_folder("jane@company.org") == "jane"

    def test_unsafe_chars(self):
        assert sanitize_sender_folder("weird+tag@x.com") == "weird_tag"


class TestReportRoundTrip:
    def test_write_and_load(self, tmp_path: Path):
        rows = [
            AuditRow(
                message_id="abc123",
                date="2024-06-01 12:00:00 UTC",
                sender="sender@example.com",
                subject="Big PDF",
                total_bytes=10_000_000,
                attachment_count=1,
                filenames="plans.pdf",
                attachment_ids="att1",
                sizes_bytes="10000000",
                web_link="https://mail.google.com/mail/u/0/#inbox/abc123",
            )
        ]
        path = write_report(rows, tmp_path)
        loaded = load_report(path)
        assert len(loaded) == 1
        assert loaded[0].message_id == "abc123"
        assert loaded[0].total_bytes == 10_000_000


class TestArchiveLogSkip:
    def test_load_logged_message_ids(self, tmp_path: Path):
        from tools.gmail_storage_common import load_logged_message_ids

        log = tmp_path / "archive_log_20260908.jsonl"
        log.write_text(
            '{"status":"archived","message_id":"m1"}\n'
            '{"status":"dry_run","message_id":"m2"}\n'
            '{"status":"upload_failed","message_id":"m3"}\n',
            encoding="utf-8",
        )
        archived = load_logged_message_ids(tmp_path, statuses=frozenset({"archived"}))
        assert archived == {"m1"}
        both = load_logged_message_ids(tmp_path, statuses=frozenset({"archived", "dry_run"}))
        assert both == {"m1", "m2"}


class TestScanLargeAttachments:
    def test_filters_by_min_size(self):
        client = MagicMock()
        client.search_message_ids.return_value = ["m1"]
        client.get_message_metadata.return_value = MagicMock(
            received_at=__import__("datetime").datetime(2024, 1, 1, tzinfo=__import__("datetime").timezone.utc),
            sender="a@b.com",
            subject="Test",
            web_link="https://example.com",
        )
        client.list_attachments.return_value = [
            EmailAttachment(id="a1", filename="small.txt", size_bytes=1_000_000, content_type="text/plain"),
        ]
        rows = scan_large_attachments(client, min_size_mb=5, max_messages=10)
        assert rows == []

    def test_includes_large_message(self):
        client = MagicMock()
        client.search_message_ids.return_value = ["m1"]
        client.get_message_metadata.return_value = MagicMock(
            received_at=__import__("datetime").datetime(2024, 1, 1, tzinfo=__import__("datetime").timezone.utc),
            sender="a@b.com",
            subject="Big file",
            web_link="https://example.com",
        )
        client.list_attachments.return_value = [
            EmailAttachment(id="a1", filename="big.zip", size_bytes=8_000_000, content_type="application/zip"),
        ]
        rows = scan_large_attachments(client, min_size_mb=5, max_messages=10)
        assert len(rows) == 1
        assert rows[0].total_bytes == 8_000_000


class TestGmailClientExtensions:
    def test_search_message_ids_paginates(self):
        from providers.gmail import GmailClient

        client = GmailClient(MagicMock())
        svc = MagicMock()
        client._service = svc
        svc.users().messages().list().execute.side_effect = [
            {"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "tok"},
            {"messages": [{"id": "c"}]},
        ]
        ids = client.search_message_ids("has:attachment", max_results=3)
        assert ids == ["a", "b", "c"]

    def test_trash_message(self):
        from providers.gmail import GmailClient

        client = GmailClient(MagicMock())
        svc = MagicMock()
        client._service = svc
        trash = svc.users.return_value.messages.return_value.trash
        client.trash_message("msg123")
        trash.assert_called_once_with(userId="me", id="msg123")


class TestLocalOneDrive:
    def test_detect_from_env(self, monkeypatch, tmp_path: Path):
        from providers.local_onedrive import detect_onedrive_root

        monkeypatch.setenv("OneDrive", str(tmp_path))
        assert detect_onedrive_root() == tmp_path

    def test_upload_creates_file(self, tmp_path: Path):
        from providers.local_onedrive import LocalOneDriveStorage

        storage = LocalOneDriveStorage(tmp_path)
        path = storage.upload(
            folder_path="Email Assistant/Gmail Archive/2024/sender",
            filename="doc.pdf",
            content=b"hello",
        )
        assert Path(path).exists()
        assert Path(path).read_bytes() == b"hello"


class TestArchiveExpandTargets:
    def test_skips_multi_attachment_by_default(self):
        from tools.archive_to_onedrive import expand_targets

        rows = [
            AuditRow(
                message_id="m1",
                date="2024-01-01 00:00:00 UTC",
                sender="x@y.com",
                subject="Two files",
                total_bytes=20_000_000,
                attachment_count=2,
                filenames="a.pdf; b.pdf",
                attachment_ids="id1; id2",
                sizes_bytes="10000000; 10000000",
                web_link="https://example.com",
            )
        ]
        assert expand_targets(rows, all_attachments=False) == []

    def test_includes_multi_attachment_when_flag_set(self):
        from tools.archive_to_onedrive import expand_targets

        rows = [
            AuditRow(
                message_id="m1",
                date="2024-01-01 00:00:00 UTC",
                sender="x@y.com",
                subject="Two files",
                total_bytes=20_000_000,
                attachment_count=2,
                filenames="a.pdf; b.pdf",
                attachment_ids="id1; id2",
                sizes_bytes="10000000; 10000000",
                web_link="https://example.com",
            )
        ]
        targets = expand_targets(rows, all_attachments=True)
        assert len(targets) == 2

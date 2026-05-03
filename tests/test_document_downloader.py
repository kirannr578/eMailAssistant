"""Tests for URL extraction, document classification, and folder-name sanitization."""
from __future__ import annotations

import pytest

from document_downloader import (
    extract_urls,
    is_known_portal,
    is_skippable_host,
    looks_like_document_url,
    sanitize_folder_name,
)


# ---------- extract_urls ----------

class TestExtractUrls:
    def test_returns_empty_for_empty_text(self):
        assert extract_urls("") == []
        assert extract_urls(None) == []

    def test_finds_plain_urls(self):
        text = "Please review the plans at https://example.com/plans.pdf today."
        assert extract_urls(text) == ["https://example.com/plans.pdf"]

    def test_finds_multiple_urls_preserves_order(self):
        text = "First https://a.com/1.pdf then https://b.com/2.zip and https://a.com/1.pdf again."
        assert extract_urls(text) == [
            "https://a.com/1.pdf",
            "https://b.com/2.zip",
        ]

    def test_strips_trailing_punctuation(self):
        text = "See https://example.com/spec.pdf, https://example.com/plans.pdf."
        assert extract_urls(text) == [
            "https://example.com/spec.pdf",
            "https://example.com/plans.pdf",
        ]

    def test_handles_urls_with_query_strings(self):
        text = "Download here: https://drive.google.com/file/d/abc123/view?usp=sharing"
        assert extract_urls(text) == [
            "https://drive.google.com/file/d/abc123/view?usp=sharing",
        ]

    def test_strips_markdown_emphasis(self):
        text = "Link: *https://example.com/file.pdf*"
        assert extract_urls(text) == ["https://example.com/file.pdf"]

    def test_ignores_non_http_schemes(self):
        text = "Email me: mailto:bob@example.com or call tel:555-1234"
        assert extract_urls(text) == []


# ---------- looks_like_document_url ----------

class TestLooksLikeDocumentUrl:
    @pytest.mark.parametrize("url", [
        "https://example.com/plans.pdf",
        "https://example.com/specs.PDF",
        "https://example.com/drawings.dwg",
        "https://example.com/model.rvt",
        "https://example.com/bid_package.zip",
        "https://contractor.com/path/to/scope.docx",
        "https://example.com/floorplan.png",
    ])
    def test_recognises_document_extensions(self, url: str):
        assert looks_like_document_url(url) is True

    @pytest.mark.parametrize("url", [
        "https://www.dropbox.com/s/abc/plans?dl=0",
        "https://we.tl/t-abc123",
        "https://wetransfer.com/downloads/abc",
        "https://acme-my.sharepoint.com/:b:/p/user/EabcXYZ",
        "https://1drv.ms/b/s!Abc123",
        "https://drive.google.com/file/d/abc/view",
    ])
    def test_recognises_known_share_hosts(self, url: str):
        assert looks_like_document_url(url) is True

    @pytest.mark.parametrize("url", [
        "https://example.com/about.html",
        "https://blog.example.com/announcing-bid",
        "ftp://example.com/file.pdf",
        "",
    ])
    def test_rejects_non_documents(self, url: str):
        assert looks_like_document_url(url) is False


# ---------- portal / skip detection ----------

class TestPortalDetection:
    @pytest.mark.parametrize("url", [
        "https://app.buildingconnected.com/some-path",
        "https://app.procore.com/123/projects/456",
        "https://www.constructconnect.com/iSqFt/x",
        "https://smartbidnet.com/x",
        "https://studio.bluebeam.com/foo",
    ])
    def test_known_portals(self, url: str):
        assert is_known_portal(url) is True

    @pytest.mark.parametrize("url", [
        "https://example.com/plans.pdf",
        "https://www.dropbox.com/s/abc/plans",
    ])
    def test_non_portals(self, url: str):
        assert is_known_portal(url) is False

    @pytest.mark.parametrize("url", [
        "https://list-manage.com/track?u=123",
        "https://click.email.example.com/abc",
    ])
    def test_skippable_marketing_hosts(self, url: str):
        assert is_skippable_host(url) is True


# ---------- sanitize_folder_name ----------

class TestSanitizeFolderName:
    def test_passes_clean_name_through(self):
        assert sanitize_folder_name("Cedar Park OB") == "Cedar Park OB"

    def test_replaces_invalid_path_chars(self):
        # Forward slash, colon, asterisk, etc.
        result = sanitize_folder_name("Project/Phase 1: Mech*")
        assert "/" not in result
        assert ":" not in result
        assert "*" not in result

    def test_trims_trailing_dots_and_spaces(self):
        assert sanitize_folder_name("Some Project ...   ") == "Some Project"

    def test_handles_empty_input(self):
        assert sanitize_folder_name("") == "Untitled"
        assert sanitize_folder_name("   ") == "Untitled"

    def test_collapses_invalid_chars_to_underscore(self):
        result = sanitize_folder_name('Bad<>"|?Name')
        assert "<" not in result
        assert ">" not in result
        assert '"' not in result
        # All those chars become a single underscore run.
        assert "_" in result

    def test_truncates_long_names(self):
        long_name = "A" * 500
        result = sanitize_folder_name(long_name, max_len=120)
        assert len(result) <= 120

    def test_removes_newlines_and_tabs(self):
        result = sanitize_folder_name("Project\nName\twith\rweirdness")
        assert "\n" not in result
        assert "\t" not in result
        assert "\r" not in result

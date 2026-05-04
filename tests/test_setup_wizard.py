"""Tests for the parts of setup_wizard.py that are pure logic."""
from __future__ import annotations

from setup_wizard import _parse_ollama_list


class TestParseOllamaList:
    def test_skips_header(self):
        out = _parse_ollama_list(
            "NAME           ID              SIZE      MODIFIED\n"
            "llama3.1:8b    46e0c10c039e    4.9 GB    37 seconds ago\n"
        )
        assert out == ["llama3.1:8b"]

    def test_multiple_models(self):
        out = _parse_ollama_list(
            "NAME           ID              SIZE      MODIFIED\n"
            "llama3.1:8b    46e0c10c039e    4.9 GB    37 seconds ago\n"
            "qwen2.5:7b     abc123          4.4 GB    1 day ago\n"
            "mistral:7b     def456          4.1 GB    3 days ago\n"
        )
        assert out == ["llama3.1:8b", "qwen2.5:7b", "mistral:7b"]

    def test_empty_output(self):
        assert _parse_ollama_list("") == []

    def test_only_header(self):
        assert _parse_ollama_list("NAME ID SIZE MODIFIED\n") == []

    def test_ignores_blank_lines(self):
        out = _parse_ollama_list(
            "NAME ID SIZE MODIFIED\n"
            "\n"
            "\n"
            "llama3.1:8b foo bar baz\n"
            "\n"
        )
        assert out == ["llama3.1:8b"]

    def test_handles_lowercase_header_defensively(self):
        # `ollama list` always emits caps but parse should be permissive.
        out = _parse_ollama_list(
            "name id size modified\n"
            "llama3.1:8b foo bar\n"
        )
        assert out == ["llama3.1:8b"]

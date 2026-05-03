"""Tests that exercise the parts of the analyzer that don't need a live LLM."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from analyzer import Analysis, derive_meeting_window, _fallback_analysis


def _base_analysis(**overrides) -> Analysis:
    defaults = dict(
        is_meeting_request=True,
        confidence=0.9,
        meeting_title="Sync",
        meeting_start_iso="2026-05-04T15:00:00-05:00",
        meeting_end_iso=None,
        location=None,
        attendees=[],
        summary="Sync request from Alice for Monday 3pm CT.",
        urgency="medium",
        suggested_action="Accept and prepare agenda.",
        notification_text="[MEETING] Alice: Monday sync at 3pm CT",
    )
    defaults.update(overrides)
    return Analysis(**defaults)


def test_derive_window_uses_default_duration_when_no_end():
    a = _base_analysis(meeting_end_iso=None)
    window = derive_meeting_window(a, default_duration_minutes=45)
    assert window is not None
    start, end = window
    assert (end - start) == timedelta(minutes=45)


def test_derive_window_respects_provided_end():
    a = _base_analysis(
        meeting_start_iso="2026-05-04T15:00:00-05:00",
        meeting_end_iso="2026-05-04T16:30:00-05:00",
    )
    window = derive_meeting_window(a, default_duration_minutes=15)
    assert window is not None
    start, end = window
    assert (end - start) == timedelta(minutes=90)


def test_derive_window_fixes_inverted_end():
    a = _base_analysis(
        meeting_start_iso="2026-05-04T15:00:00-05:00",
        meeting_end_iso="2026-05-04T14:00:00-05:00",  # earlier than start
    )
    window = derive_meeting_window(a, default_duration_minutes=30)
    assert window is not None
    start, end = window
    assert end > start
    assert (end - start) == timedelta(minutes=30)


def test_derive_window_returns_none_without_start():
    a = _base_analysis(meeting_start_iso=None)
    assert derive_meeting_window(a, default_duration_minutes=30) is None


def test_iso_validator_normalizes_format():
    a = _base_analysis(meeting_start_iso="2026-05-04T15:00:00-05:00")
    # Should round-trip cleanly to ISO 8601
    assert a.meeting_start_iso is not None
    parsed = datetime.fromisoformat(a.meeting_start_iso)
    assert parsed.tzinfo is not None


def test_confidence_must_be_in_range():
    with pytest.raises(Exception):
        _base_analysis(confidence=1.5)


def test_fallback_analysis_is_safe():
    fb = _fallback_analysis("Some subject", "alice@example.com")
    assert fb.is_meeting_request is False
    assert fb.confidence == 0.0
    assert "alice@example.com" in fb.notification_text
    assert fb.urgency == "low"

"""Tests that exercise the parts of the analyzer that don't need a live LLM."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from analyzer import (
    Analysis,
    _company_context,
    _fallback_analysis,
    derive_bid_reminder_window,
    derive_meeting_window,
    derive_pre_bid_window,
)


def _base_analysis(**overrides) -> Analysis:
    defaults = dict(
        is_meeting_request=True,
        meeting_confidence=0.9,
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


def _base_bid(**overrides) -> Analysis:
    """A baseline bid-request Analysis (with no meeting)."""
    far_future = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
    defaults = dict(
        is_meeting_request=False,
        meeting_confidence=0.0,
        is_bid_request=True,
        bid_confidence=0.92,
        bid_project_name="Cedar Park Office Building",
        bid_project_location="Cedar Park, TX",
        bid_due_date_iso=far_future,
        bid_scope_summary="Sitework + foundations",
        bid_contact="estimator@gc-example.com",
        summary="GC inviting BPC to bid sitework + foundations on Cedar Park office.",
        urgency="medium",
        suggested_action="Pull plans and start takeoff.",
        notification_text="[BID] GC: Cedar Park OB - bid due in 14 days",
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
        _base_analysis(meeting_confidence=1.5)


def test_fallback_analysis_is_safe():
    fb = _fallback_analysis("Some subject", "alice@example.com")
    assert fb.is_meeting_request is False
    assert fb.is_bid_request is False
    assert fb.meeting_confidence == 0.0
    assert fb.bid_confidence == 0.0
    assert "alice@example.com" in fb.notification_text
    assert fb.urgency == "low"


def test_confidence_property_aliases_meeting_confidence():
    a = _base_analysis(meeting_confidence=0.77)
    # Backward compat: legacy callers can still read .confidence
    assert a.confidence == 0.77


# ----- bid request tests -----

def test_bid_window_returns_due_time_when_in_future():
    a = _base_bid()
    window = derive_bid_reminder_window(a, duration_minutes=30)
    assert window is not None
    start, end = window
    assert (end - start) == timedelta(minutes=30)


def test_bid_window_none_when_due_in_past():
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    a = _base_bid(bid_due_date_iso=past)
    assert derive_bid_reminder_window(a) is None


def test_bid_window_none_when_no_due_date():
    a = _base_bid(bid_due_date_iso=None)
    assert derive_bid_reminder_window(a) is None


def test_bid_due_iso_is_normalized():
    a = _base_bid(bid_due_date_iso="2030-06-15T17:00:00-05:00")
    assert a.bid_due_date_iso is not None
    parsed = datetime.fromisoformat(a.bid_due_date_iso)
    assert parsed.tzinfo is not None


def test_bid_confidence_must_be_in_range():
    with pytest.raises(Exception):
        _base_bid(bid_confidence=1.5)


# ----- company context helper -----

def test_company_context_includes_name_and_aliases():
    ctx = _company_context("Blueprint Constructs", ["BPC", "Blueprint"])
    assert "Blueprint Constructs" in ctx
    assert "BPC" in ctx and "Blueprint" in ctx


def test_company_context_skips_alias_equal_to_name():
    ctx = _company_context("BPC", ["BPC"])
    # Name is included, but the duplicate alias should not appear
    assert ctx.count("BPC") == 1


def test_company_context_handles_empty():
    ctx = _company_context("", [])
    assert "user" in ctx.lower()


# ----- pre-bid meeting + new schema fields -----

def test_pre_bid_window_returns_default_duration():
    far_future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    a = _base_bid(pre_bid_meeting_iso=far_future)
    win = derive_pre_bid_window(a, default_duration_minutes=60)
    assert win is not None
    start, end = win
    assert (end - start) == timedelta(minutes=60)


def test_pre_bid_window_uses_explicit_end():
    start_iso = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    end_iso = (datetime.now(timezone.utc) + timedelta(days=7, hours=2)).isoformat()
    a = _base_bid(pre_bid_meeting_iso=start_iso, pre_bid_meeting_end_iso=end_iso)
    win = derive_pre_bid_window(a)
    assert win is not None
    start, end = win
    # Should be ~2 hours, not the default
    assert (end - start) >= timedelta(minutes=110)
    assert (end - start) <= timedelta(minutes=130)


def test_pre_bid_window_none_when_in_past():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    a = _base_bid(pre_bid_meeting_iso=past)
    assert derive_pre_bid_window(a) is None


def test_pre_bid_window_none_when_not_set():
    a = _base_bid(pre_bid_meeting_iso=None)
    assert derive_pre_bid_window(a) is None


def test_pre_bid_window_fixes_inverted_end():
    start_iso = (datetime.now(timezone.utc) + timedelta(days=7, hours=2)).isoformat()
    end_iso = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()  # before start
    a = _base_bid(pre_bid_meeting_iso=start_iso, pre_bid_meeting_end_iso=end_iso)
    win = derive_pre_bid_window(a, default_duration_minutes=45)
    assert win is not None
    start, end = win
    assert end > start
    assert (end - start) == timedelta(minutes=45)


def test_pre_bid_mandatory_flag_defaults_to_false():
    a = _base_bid()
    assert a.pre_bid_meeting_mandatory is False


def test_pre_bid_mandatory_flag_can_be_set():
    a = _base_bid(pre_bid_meeting_mandatory=True)
    assert a.pre_bid_meeting_mandatory is True


def test_rfi_due_iso_is_normalized():
    rfi = "2030-06-01T17:00:00-05:00"
    a = _base_bid(rfi_due_date_iso=rfi)
    assert a.rfi_due_date_iso is not None
    parsed = datetime.fromisoformat(a.rfi_due_date_iso)
    assert parsed.tzinfo is not None


def test_pre_bid_iso_is_normalized():
    pb = "2030-05-15T10:00:00-05:00"
    a = _base_bid(pre_bid_meeting_iso=pb)
    assert a.pre_bid_meeting_iso is not None
    parsed = datetime.fromisoformat(a.pre_bid_meeting_iso)
    assert parsed.tzinfo is not None


def test_pre_bid_location_and_link_can_coexist_for_hybrid():
    a = _base_bid(
        pre_bid_meeting_location="123 Main St, Cedar Park, TX",
        pre_bid_meeting_link="https://teams.microsoft.com/l/meetup-join/abc",
    )
    assert a.pre_bid_meeting_location is not None
    assert a.pre_bid_meeting_link is not None


def test_new_optional_fields_default_to_none():
    # Build a minimal Analysis (no bid, no meeting) and confirm new fields are None/False.
    a = Analysis(
        summary="x",
        urgency="low",
        suggested_action="x",
        notification_text="x",
    )
    assert a.bid_project_type is None
    assert a.bid_reference_number is None
    assert a.bid_submission_method is None
    assert a.rfi_due_date_iso is None
    assert a.pre_bid_meeting_iso is None
    assert a.pre_bid_meeting_end_iso is None
    assert a.pre_bid_meeting_mandatory is False
    assert a.pre_bid_meeting_location is None
    assert a.pre_bid_meeting_link is None
    assert a.pre_bid_contact is None


def test_fallback_analysis_includes_new_fields():
    fb = _fallback_analysis("Subject", "x@example.com")
    # New fields shouldn't break the fallback path.
    assert fb.pre_bid_meeting_iso is None
    assert fb.pre_bid_meeting_mandatory is False
    assert fb.rfi_due_date_iso is None
    assert fb.bid_submission_method is None
    assert fb.bid_reference_number is None
    assert fb.pre_bid_contact is None


def test_bid_reference_number_round_trips():
    a = _base_bid(bid_reference_number="405-26R0015165")
    assert a.bid_reference_number == "405-26R0015165"


def test_pre_bid_contact_separate_from_bid_contact():
    a = _base_bid(
        bid_contact="Toribio Solis (toribio.solis@dps.texas.gov)",
        pre_bid_contact="Michael Hodge (469) 560-0959",
    )
    assert a.bid_contact != a.pre_bid_contact
    assert "Hodge" in a.pre_bid_contact
    assert "Solis" in a.bid_contact

"""Email Assistant entrypoint.

Polls the configured Outlook mailbox, runs each new unread message through an
LLM analyzer, optionally blocks the user's calendar for high-confidence meeting
requests, and sends a SMS / WhatsApp notification with the summary.

Run:
    python main.py            # poll forever
    python main.py --once     # process current unread, then exit (Task Scheduler)
    python main.py --auth     # just complete OAuth and exit
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timedelta, timezone

from analyzer import Analysis, EmailAnalyzer, derive_meeting_window
from config import Settings, load_settings
from providers.calendar import CalendarClient
from providers.ms_graph_auth import GraphAuth
from providers.notifier import Notifier, NotifierConfig
from providers.outlook import EmailMessage, OutlookClient
from state import StateStore

logger = logging.getLogger("email_assistant")


def _setup_logging(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Quiet noisy third-party loggers.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("msal").setLevel(logging.WARNING)


def _build_components(settings: Settings):
    auth = GraphAuth(
        client_id=settings.ms_client_id,
        tenant_id=settings.ms_tenant_id,
        token_cache_path=settings.ms_token_cache_path,
    )
    outlook = OutlookClient(auth)
    calendar = CalendarClient(auth, user_timezone=settings.user_timezone)
    analyzer = EmailAnalyzer(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        user_timezone=settings.user_timezone,
    )
    notifier = Notifier(
        NotifierConfig(
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
            from_sms=settings.twilio_from_sms,
            from_whatsapp=settings.twilio_from_whatsapp,
            to_sms=settings.notify_to_sms,
            to_whatsapp=settings.notify_to_whatsapp,
            channels=settings.notify_channels,
        )
    )
    state = StateStore(settings.state_db_path)
    return auth, outlook, calendar, analyzer, notifier, state


def _process_one(
    msg: EmailMessage,
    *,
    settings: Settings,
    analyzer: EmailAnalyzer,
    calendar: CalendarClient,
    notifier: Notifier,
    outlook: OutlookClient,
    state: StateStore,
) -> None:
    logger.info("Analyzing message %s | from=%s | subject=%r",
                msg.id[:12], msg.sender, msg.subject)

    analysis: Analysis = analyzer.analyze(
        sender=msg.sender,
        to=msg.to,
        subject=msg.subject,
        body=msg.body_text,
        received_at=msg.received_at,
    )
    logger.info(
        "  -> meeting=%s confidence=%.2f urgency=%s",
        analysis.is_meeting_request, analysis.confidence, analysis.urgency,
    )

    calendar_event_id: str | None = None
    calendar_action_note = ""
    if (
        analysis.is_meeting_request
        and analysis.confidence >= settings.auto_block_confidence
    ):
        window = derive_meeting_window(
            analysis,
            default_duration_minutes=settings.default_meeting_duration_minutes,
        )
        if window is None:
            calendar_action_note = " (no time parsed)"
        else:
            start, end = window
            try:
                calendar_event_id = calendar.create_event(
                    subject=f"[Auto] {analysis.meeting_title or msg.subject}",
                    start=start,
                    end=end,
                    body_text=(
                        f"Auto-blocked from email by Email Assistant.\n\n"
                        f"From: {msg.sender}\n"
                        f"Subject: {msg.subject}\n\n"
                        f"Summary: {analysis.summary}\n\n"
                        f"Suggested action: {analysis.suggested_action}\n\n"
                        f"Original email: {msg.web_link or '(link unavailable)'}"
                    ),
                    attendees=[
                        a for a in analysis.attendees
                        if "@" in a and a.lower() != settings.mailbox_address.lower()
                    ],
                    location=analysis.location,
                    is_tentative=analysis.confidence < 0.9,
                )
                calendar_action_note = f" | calendar event created ({start:%a %b %d %H:%M})"
                logger.info("  -> calendar event %s created", calendar_event_id)
            except Exception as e:
                logger.exception("  -> calendar create_event failed: %s", e)
                calendar_action_note = " | calendar create FAILED"

    notification_body = analysis.notification_text + calendar_action_note
    notified = notifier.notify(notification_body)
    if notified:
        logger.info("  -> notification sent")
    else:
        logger.info("  -> notification skipped (no channels configured / all failed)")

    try:
        outlook.mark_read(msg.id)
    except Exception as e:
        logger.warning("  -> mark_read failed (will dedupe via local state): %s", e)

    state.mark_processed(
        msg.id,
        is_meeting=analysis.is_meeting_request,
        confidence=analysis.confidence,
        calendar_event_id=calendar_event_id,
        notified=notified,
    )


def run_once(settings: Settings) -> int:
    """Process all currently-unread messages once. Returns count processed."""
    _, outlook, calendar, analyzer, notifier, state = _build_components(settings)

    since = datetime.now(timezone.utc) - timedelta(
        minutes=settings.initial_lookback_minutes
    )
    messages = outlook.list_unread(since=since)
    logger.info("Found %d unread message(s) since %s", len(messages), since.isoformat())

    processed = 0
    for msg in messages:
        if state.already_processed(msg.id):
            logger.debug("Skipping already-processed %s", msg.id[:12])
            continue
        try:
            _process_one(
                msg,
                settings=settings,
                analyzer=analyzer,
                calendar=calendar,
                notifier=notifier,
                outlook=outlook,
                state=state,
            )
            processed += 1
        except Exception:
            logger.exception("Failed to process message %s; will retry next poll.", msg.id[:12])
    return processed


def run_forever(settings: Settings) -> None:
    _, outlook, calendar, analyzer, notifier, state = _build_components(settings)

    stop = {"now": False}

    def _handle_signal(signum, _frame):
        logger.info("Received signal %s; shutting down after this cycle.", signum)
        stop["now"] = True

    # SIGTERM may not exist on Windows for non-console signals; ignore failures.
    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if sig is not None:
            try:
                signal.signal(sig, _handle_signal)
            except (ValueError, OSError):
                pass

    since = datetime.now(timezone.utc) - timedelta(
        minutes=settings.initial_lookback_minutes
    )
    logger.info(
        "Email Assistant started. Polling every %ds. Initial lookback to %s.",
        settings.poll_interval_seconds, since.isoformat(),
    )

    while not stop["now"]:
        cycle_start = datetime.now(timezone.utc)
        try:
            messages = outlook.list_unread(since=since)
            for msg in messages:
                if state.already_processed(msg.id):
                    continue
                try:
                    _process_one(
                        msg,
                        settings=settings,
                        analyzer=analyzer,
                        calendar=calendar,
                        notifier=notifier,
                        outlook=outlook,
                        state=state,
                    )
                except Exception:
                    logger.exception("Error processing %s", msg.id[:12])
            # Advance the watermark only after a successful list_unread call.
            since = cycle_start
        except Exception:
            logger.exception("Polling cycle failed; will retry.")

        for _ in range(settings.poll_interval_seconds):
            if stop["now"]:
                break
            time.sleep(1)

    logger.info("Email Assistant stopped.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Email Assistant agent")
    parser.add_argument("--once", action="store_true",
                        help="Process current unread mail and exit (for Task Scheduler).")
    parser.add_argument("--auth", action="store_true",
                        help="Run device-code OAuth flow to seed the token cache, then exit.")
    parser.add_argument("--setup", action="store_true",
                        help="Run the interactive .env setup wizard and exit.")
    args = parser.parse_args()

    if args.setup:
        from setup_wizard import run_wizard
        return run_wizard()

    settings = load_settings()
    _setup_logging(settings.debug)

    if args.auth:
        auth = GraphAuth(
            client_id=settings.ms_client_id,
            tenant_id=settings.ms_tenant_id,
            token_cache_path=settings.ms_token_cache_path,
        )
        token = auth.get_access_token()
        logger.info("OAuth complete. Token acquired (length=%d).", len(token))
        return 0

    if args.once:
        count = run_once(settings)
        logger.info("One-shot run complete; processed %d message(s).", count)
        return 0

    run_forever(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())

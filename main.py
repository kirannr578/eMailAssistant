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

from analyzer import Analysis, EmailAnalyzer, derive_bid_reminder_window, derive_meeting_window
from config import Settings, load_settings
from providers.base import CalendarProvider, EmailMessage, EmailProvider
from providers.notifier import Notifier, NotifierConfig
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


def _build_email_and_calendar(settings: Settings) -> tuple[EmailProvider, CalendarProvider]:
    """Construct provider clients based on EMAIL_PROVIDER."""
    if settings.email_provider == "outlook":
        from providers.calendar import CalendarClient
        from providers.ms_graph_auth import GraphAuth
        from providers.outlook import OutlookClient

        auth = GraphAuth(
            client_id=settings.ms_client_id,
            tenant_id=settings.ms_tenant_id,
            token_cache_path=settings.ms_token_cache_path,
        )
        return OutlookClient(auth), CalendarClient(auth, user_timezone=settings.user_timezone)

    if settings.email_provider == "gmail":
        from providers.gmail import GmailClient
        from providers.google_auth import GoogleAuth
        from providers.google_calendar import GoogleCalendarClient

        gauth = GoogleAuth(
            client_secrets_path=settings.google_client_secrets_path,
            token_cache_path=settings.google_token_cache_path,
        )
        return (
            GmailClient(gauth),
            GoogleCalendarClient(
                gauth,
                user_timezone=settings.user_timezone,
                calendar_id=settings.google_calendar_id,
            ),
        )

    raise RuntimeError(f"Unknown EMAIL_PROVIDER: {settings.email_provider}")


def _build_components(settings: Settings):
    email_client, calendar = _build_email_and_calendar(settings)
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
    notifier = Notifier(
        NotifierConfig(
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
            from_sms=settings.twilio_from_sms,
            from_whatsapp=settings.twilio_from_whatsapp,
            to_sms=settings.notify_to_sms,
            to_whatsapp=settings.notify_to_whatsapp,
            meta_phone_number_id=settings.meta_wa_phone_number_id,
            meta_access_token=settings.meta_wa_access_token,
            meta_recipient=settings.meta_wa_recipient,
            meta_template_name=settings.meta_wa_template_name,
            meta_template_language=settings.meta_wa_template_language,
            meta_api_version=settings.meta_wa_api_version,
            telegram_bot_token=settings.telegram_bot_token,
            telegram_chat_id=settings.telegram_chat_id,
            channels=settings.notify_channels,
        )
    )
    state = StateStore(settings.state_db_path)
    return email_client, calendar, analyzer, notifier, state


def _process_one(
    msg: EmailMessage,
    *,
    settings: Settings,
    analyzer: EmailAnalyzer,
    calendar: CalendarProvider,
    notifier: Notifier,
    email_client: EmailProvider,
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
        "  -> meeting=%s (%.2f) bid=%s (%.2f) urgency=%s",
        analysis.is_meeting_request, analysis.meeting_confidence,
        analysis.is_bid_request, analysis.bid_confidence,
        analysis.urgency,
    )

    calendar_event_ids: list[str] = []
    calendar_action_note = ""

    # ---- Meeting request: block the meeting time on the calendar ----
    if (
        analysis.is_meeting_request
        and analysis.meeting_confidence >= settings.auto_block_confidence
    ):
        window = derive_meeting_window(
            analysis,
            default_duration_minutes=settings.default_meeting_duration_minutes,
        )
        if window is None:
            calendar_action_note += " (no meeting time parsed)"
        else:
            start, end = window
            try:
                eid = calendar.create_event(
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
                    is_tentative=analysis.meeting_confidence < 0.9,
                )
                calendar_event_ids.append(eid)
                calendar_action_note += f" | meeting blocked ({start:%a %b %d %H:%M})"
                logger.info("  -> meeting event %s created", eid)
            except Exception as e:
                logger.exception("  -> meeting create_event failed: %s", e)
                calendar_action_note += " | meeting block FAILED"

    # ---- Bid request: place a reminder AT the bid due time ----
    if (
        analysis.is_bid_request
        and analysis.bid_confidence >= settings.auto_block_confidence
        and settings.auto_block_bid_reminder
    ):
        window = derive_bid_reminder_window(analysis)
        if window is None:
            if analysis.bid_due_date_iso:
                calendar_action_note += " (bid due date in the past, no reminder)"
            else:
                calendar_action_note += " (no bid due date parsed)"
        else:
            start, end = window
            project = analysis.bid_project_name or msg.subject
            try:
                eid = calendar.create_event(
                    subject=f"BID DUE: {project}",
                    start=start,
                    end=end,
                    body_text=(
                        f"Auto-created bid deadline reminder by Email Assistant.\n\n"
                        f"From: {msg.sender}\n"
                        f"Project: {analysis.bid_project_name or '(see email)'}\n"
                        f"Location: {analysis.bid_project_location or '(see email)'}\n"
                        f"Scope: {analysis.bid_scope_summary or '(see email)'}\n"
                        f"Submit to: {analysis.bid_contact or '(see email)'}\n\n"
                        f"Summary: {analysis.summary}\n"
                        f"Suggested action: {analysis.suggested_action}\n\n"
                        f"Original email: {msg.web_link or '(link unavailable)'}"
                    ),
                    location=analysis.bid_project_location,
                    is_tentative=analysis.bid_confidence < 0.9,
                )
                calendar_event_ids.append(eid)
                calendar_action_note += f" | bid deadline blocked ({start:%a %b %d %H:%M})"
                logger.info("  -> bid reminder event %s created", eid)
            except Exception as e:
                logger.exception("  -> bid reminder create_event failed: %s", e)
                calendar_action_note += " | bid reminder FAILED"

    calendar_event_id = calendar_event_ids[0] if calendar_event_ids else None

    notification_body = analysis.notification_text + calendar_action_note
    notified = notifier.notify(notification_body)
    if notified:
        logger.info("  -> notification sent")
    else:
        logger.info("  -> notification skipped (no channels configured / all failed)")

    try:
        email_client.mark_read(msg.id)
    except Exception as e:
        logger.warning("  -> mark_read failed (will dedupe via local state): %s", e)

    state.mark_processed(
        msg.id,
        is_meeting=analysis.is_meeting_request,
        confidence=max(analysis.meeting_confidence, analysis.bid_confidence),
        calendar_event_id=calendar_event_id,
        notified=notified,
    )


def run_once(settings: Settings) -> int:
    """Process all currently-unread messages once. Returns count processed."""
    email_client, calendar, analyzer, notifier, state = _build_components(settings)

    since = datetime.now(timezone.utc) - timedelta(
        minutes=settings.initial_lookback_minutes
    )
    messages = email_client.list_unread(since=since)
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
                email_client=email_client,
                state=state,
            )
            processed += 1
        except Exception:
            logger.exception("Failed to process message %s; will retry next poll.", msg.id[:12])
    return processed


def run_forever(settings: Settings) -> None:
    email_client, calendar, analyzer, notifier, state = _build_components(settings)

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
            messages = email_client.list_unread(since=since)
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
                        email_client=email_client,
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
        if settings.email_provider == "outlook":
            from providers.ms_graph_auth import GraphAuth
            auth = GraphAuth(
                client_id=settings.ms_client_id,
                tenant_id=settings.ms_tenant_id,
                token_cache_path=settings.ms_token_cache_path,
            )
            token = auth.get_access_token()
            logger.info("Microsoft OAuth complete. Token acquired (length=%d).", len(token))
        elif settings.email_provider == "gmail":
            from providers.google_auth import GoogleAuth
            gauth = GoogleAuth(
                client_secrets_path=settings.google_client_secrets_path,
                token_cache_path=settings.google_token_cache_path,
            )
            creds = gauth.get_credentials()
            logger.info("Google OAuth complete. Token valid: %s", bool(creds and creds.valid))
        else:
            raise RuntimeError(f"Unknown EMAIL_PROVIDER: {settings.email_provider}")
        return 0

    if args.once:
        count = run_once(settings)
        logger.info("One-shot run complete; processed %d message(s).", count)
        return 0

    run_forever(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())

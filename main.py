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

# IMPORTANT: tls_setup must be imported before any module that performs an
# HTTPS request (openai SDK, msal, requests, googleapiclient, etc.). It
# wires Python's TLS verification to the OS trust store so corporate
# MITM-inspection root CAs are honored.
import tls_setup  # noqa: F401  (side-effect import)

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


# When packaged as a frozen exe (PyInstaller / Inno Setup install), the CWD
# at launch depends on whoever started us (Start Menu, Task Scheduler, a
# random shortcut). Pin it to a stable per-user data dir so that .env,
# state.db, token_cache.bin, etc. always resolve to the same place across
# runs and survive reinstalls. This dir lives outside Program Files so it
# stays user-writable and isn't wiped by an uninstall.
def _pin_data_dir_when_frozen() -> None:
    if not getattr(sys, "frozen", False):
        return
    data_root = os.environ.get("EMAIL_ASSISTANT_DATA_DIR")
    if data_root:
        target = Path(data_root)
    elif os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        target = Path(base) / "EmailAssistant"
    else:
        target = Path.home() / ".email-assistant"
    target.mkdir(parents=True, exist_ok=True)
    os.chdir(target)


_pin_data_dir_when_frozen()

# Install the crash sentinel as early as possible. In frozen builds the
# PyInstaller runtime hook already did this BEFORE we got here, but the
# install() call is idempotent so calling it again from dev mode is
# safe and ensures both modes behave identically.
import _crash  # noqa: E402

_crash.install()

from analyzer import (
    Analysis,
    EmailAnalyzer,
    derive_bid_reminder_window,
    derive_meeting_window,
    derive_pre_bid_window,
)
from config import Settings, load_settings
from document_downloader import (
    download_document,
    extract_urls,
    sanitize_folder_name,
)
from providers.base import (
    CalendarProvider,
    EmailMessage,
    EmailProvider,
    FileStorage,
)
from providers.notifier import Notifier, NotifierConfig
from state import StateStore

logger = logging.getLogger("email_assistant")


def _setup_logging(debug: bool) -> None:
    """Configure console + rotating-file logging.

    File logs land in ./logs/agent.log relative to CWD. In frozen mode CWD
    is pinned to %LOCALAPPDATA%\\EmailAssistant by _pin_data_dir_when_frozen,
    so the effective path is %LOCALAPPDATA%\\EmailAssistant\\logs\\agent.log.
    Both console + file handlers are added to the root logger so EVERY
    module's logger inherits both sinks.
    """
    from logging.handlers import RotatingFileHandler

    level = logging.DEBUG if debug else logging.INFO
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)
    # Wipe any handlers configured by basicConfig in earlier code paths,
    # otherwise we'd double-log when --setup -> wizard -> daemon flips
    # through multiple init points.
    for h in list(root.handlers):
        root.removeHandler(h)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        # 5 MB per file, keep 5 rotations = 25 MB cap.
        file_handler = RotatingFileHandler(
            log_dir / "agent.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except Exception as e:
        # Never let logging setup itself crash the agent. Keep going with
        # console-only and surface the reason there.
        root.warning("File logging disabled: %s", e)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("msal").setLevel(logging.WARNING)


def _build_email_calendar_storage(
    settings: Settings,
) -> tuple[EmailProvider, CalendarProvider, FileStorage | None]:
    """Construct provider clients based on EMAIL_PROVIDER (incl. file storage)."""
    if settings.email_provider == "outlook":
        from providers.calendar import CalendarClient
        from providers.ms_graph_auth import GraphAuth
        from providers.onedrive import OneDriveClient
        from providers.outlook import OutlookClient

        auth = GraphAuth(
            client_id=settings.ms_client_id,
            tenant_id=settings.ms_tenant_id,
            token_cache_path=settings.ms_token_cache_path,
        )
        storage: FileStorage | None = (
            OneDriveClient(auth) if settings.auto_download_bid_docs else None
        )
        return OutlookClient(auth), CalendarClient(auth, user_timezone=settings.user_timezone), storage

    if settings.email_provider == "gmail":
        from providers.gmail import GmailClient
        from providers.google_auth import GoogleAuth
        from providers.google_calendar import GoogleCalendarClient
        from providers.google_drive import GoogleDriveClient

        gauth = GoogleAuth(
            client_secrets_path=settings.google_client_secrets_path,
            token_cache_path=settings.google_token_cache_path,
        )
        storage = GoogleDriveClient(gauth) if settings.auto_download_bid_docs else None
        return (
            GmailClient(gauth),
            GoogleCalendarClient(
                gauth,
                user_timezone=settings.user_timezone,
                calendar_id=settings.google_calendar_id,
            ),
            storage,
        )

    raise RuntimeError(f"Unknown EMAIL_PROVIDER: {settings.email_provider}")


def _build_components(settings: Settings):
    email_client, calendar, storage = _build_email_calendar_storage(settings)
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
    return email_client, calendar, analyzer, notifier, state, storage


def _capture_bid_documents(
    msg: EmailMessage,
    analysis: Analysis,
    *,
    settings: Settings,
    email_client: EmailProvider,
    storage: FileStorage,
) -> tuple[int, str | None]:
    """Download attachments + body links for a bid email into cloud storage.

    Returns (uploaded_count, folder_link). Best-effort; never raises.
    """
    project = analysis.bid_project_name or msg.subject
    folder_segment = sanitize_folder_name(project)
    folder_path = f"{settings.bid_docs_base_folder.strip('/')}/{folder_segment}"

    max_bytes = settings.max_download_mb * 1024 * 1024
    uploaded = 0

    # 1) Email attachments
    try:
        attachments = email_client.list_attachments(msg.id)
    except Exception as e:
        logger.warning("  -> list_attachments failed: %s", e)
        attachments = []

    for att in attachments:
        if att.size_bytes and att.size_bytes > max_bytes:
            logger.warning("  -> skipping attachment %s (%d bytes > cap)",
                           att.filename, att.size_bytes)
            continue
        try:
            content = email_client.download_attachment(msg.id, att.id)
        except Exception as e:
            logger.warning("  -> download_attachment %s failed: %s", att.filename, e)
            continue
        if not content:
            continue
        if len(content) > max_bytes:
            logger.warning("  -> skipping %s after download (size %d > cap)",
                           att.filename, len(content))
            continue
        try:
            storage.upload(
                folder_path=folder_path,
                filename=att.filename,
                content=content,
                content_type=att.content_type,
            )
            uploaded += 1
            logger.info("  -> uploaded attachment %s (%d bytes)", att.filename, len(content))
        except Exception as e:
            logger.warning("  -> upload of %s failed: %s", att.filename, e)

    # 2) URLs in body
    if settings.download_docs_from_links:
        for url in extract_urls(msg.body_text):
            try:
                doc = download_document(url, max_bytes=max_bytes)
            except Exception as e:
                logger.warning("  -> document download error for %s: %s", url, e)
                continue
            if doc is None:
                continue
            try:
                storage.upload(
                    folder_path=folder_path,
                    filename=doc.filename,
                    content=doc.content,
                    content_type=doc.content_type,
                )
                uploaded += 1
                logger.info("  -> uploaded link doc %s (%d bytes)",
                            doc.filename, len(doc.content))
            except Exception as e:
                logger.warning("  -> upload of %s failed: %s", doc.filename, e)

    folder_link = None
    if uploaded > 0:
        try:
            folder_link = storage.folder_link(folder_path)
        except Exception:
            pass
    return uploaded, folder_link


def _process_one(
    msg: EmailMessage,
    *,
    settings: Settings,
    analyzer: EmailAnalyzer,
    calendar: CalendarProvider,
    notifier: Notifier,
    email_client: EmailProvider,
    storage: FileStorage | None,
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
            ref_prefix = f"[{analysis.bid_reference_number}] " if analysis.bid_reference_number else ""
            try:
                eid = calendar.create_event(
                    subject=f"BID DUE: {ref_prefix}{project}",
                    start=start,
                    end=end,
                    body_text=(
                        f"Auto-created bid deadline reminder by Email Assistant.\n\n"
                        f"From: {msg.sender}\n"
                        f"Project: {analysis.bid_project_name or '(see email)'}\n"
                        f"Reference #: {analysis.bid_reference_number or '(none stated)'}\n"
                        f"Location: {analysis.bid_project_location or '(see email)'}\n"
                        f"Scope: {analysis.bid_scope_summary or '(see email)'}\n"
                        f"Submit via: {analysis.bid_submission_method or '(see email)'}\n"
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

    # ---- Pre-bid meeting / walkthrough: block calendar at the meeting time ----
    if (
        analysis.is_bid_request
        and analysis.bid_confidence >= settings.auto_block_confidence
        and analysis.pre_bid_meeting_iso
    ):
        pb_window = derive_pre_bid_window(analysis)
        if pb_window is None:
            calendar_action_note += " (pre-bid meeting in past, not blocked)"
        else:
            pb_start, pb_end = pb_window
            project = analysis.bid_project_name or msg.subject
            ref_prefix = f"[{analysis.bid_reference_number}] " if analysis.bid_reference_number else ""
            mandatory_tag = "MANDATORY " if analysis.pre_bid_meeting_mandatory else ""
            pb_subject = f"PRE-BID {mandatory_tag}WALKTHROUGH: {ref_prefix}{project}".strip()
            # Calendar 'location' field: prefer physical address, fall back to virtual link.
            pb_location = (
                analysis.pre_bid_meeting_location
                or analysis.pre_bid_meeting_link
                or analysis.bid_project_location
            )
            virtual_line = (
                f"Virtual link: {analysis.pre_bid_meeting_link}\n"
                if analysis.pre_bid_meeting_link else ""
            )
            site_contact_line = (
                f"Site visit contact: {analysis.pre_bid_contact}\n"
                if analysis.pre_bid_contact else ""
            )
            try:
                pb_eid = calendar.create_event(
                    subject=pb_subject,
                    start=pb_start,
                    end=pb_end,
                    body_text=(
                        f"Auto-created pre-bid meeting by Email Assistant.\n\n"
                        f"From: {msg.sender}\n"
                        f"Project: {project}\n"
                        f"Reference #: {analysis.bid_reference_number or '(none stated)'}\n"
                        f"Project location: {analysis.bid_project_location or '(see email)'}\n"
                        f"Meeting location: {analysis.pre_bid_meeting_location or '(see virtual link)'}\n"
                        f"{virtual_line}"
                        f"{site_contact_line}"
                        f"Bid contact: {analysis.bid_contact or '(see email)'}\n"
                        f"Mandatory: {'YES' if analysis.pre_bid_meeting_mandatory else 'no'}\n\n"
                        f"Summary: {analysis.summary}\n\n"
                        f"Original email: {msg.web_link or '(link unavailable)'}"
                    ),
                    location=pb_location,
                    is_tentative=not analysis.pre_bid_meeting_mandatory,
                )
                calendar_event_ids.append(pb_eid)
                tag = "MANDATORY pre-bid" if analysis.pre_bid_meeting_mandatory else "pre-bid"
                calendar_action_note += f" | {tag} blocked ({pb_start:%a %b %d %H:%M})"
                logger.info("  -> pre-bid event %s created", pb_eid)
            except Exception as e:
                logger.exception("  -> pre-bid create_event failed: %s", e)
                calendar_action_note += " | pre-bid block FAILED"

    # ---- RFI cutoff: optionally surface in the action note (no calendar event by default) ----
    if (
        analysis.is_bid_request
        and analysis.bid_confidence >= settings.auto_block_confidence
        and analysis.rfi_due_date_iso
    ):
        try:
            from dateutil import parser as _dp
            _rfi = _dp.isoparse(analysis.rfi_due_date_iso)
            calendar_action_note += f" | RFIs due {_rfi:%a %b %d %H:%M}"
        except Exception:
            pass

    calendar_event_id = calendar_event_ids[0] if calendar_event_ids else None

    # ---- Bid document capture ----
    docs_action_note = ""
    if (
        analysis.is_bid_request
        and analysis.bid_confidence >= settings.auto_block_confidence
        and settings.auto_download_bid_docs
        and storage is not None
    ):
        try:
            count, folder_link = _capture_bid_documents(
                msg, analysis,
                settings=settings,
                email_client=email_client,
                storage=storage,
            )
            if count > 0:
                project = analysis.bid_project_name or msg.subject
                folder_segment = sanitize_folder_name(project)
                if folder_link:
                    docs_action_note = f" | {count} doc(s) saved -> {folder_link}"
                else:
                    docs_action_note = (
                        f" | {count} doc(s) saved -> "
                        f"{settings.bid_docs_base_folder}/{folder_segment}"
                    )
                logger.info("  -> %d bid document(s) captured for project '%s'",
                            count, project)
            else:
                logger.info("  -> no bid documents found to capture")
        except Exception as e:
            logger.exception("  -> bid document capture failed: %s", e)
            docs_action_note = " | doc capture FAILED"

    notification_body = analysis.notification_text + calendar_action_note + docs_action_note
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
    email_client, calendar, analyzer, notifier, state, storage = _build_components(settings)

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
                storage=storage,
                state=state,
            )
            processed += 1
        except Exception:
            logger.exception("Failed to process message %s; will retry next poll.", msg.id[:12])
    return processed


def run_forever(settings: Settings) -> None:
    email_client, calendar, analyzer, notifier, state, storage = _build_components(settings)

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
                        storage=storage,
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


def _run_diagnostics() -> int:
    """Print install paths, versions, and a tail of the most recent log.

    Designed for unattended debugging: also writes the same report to a
    timestamped file in logs/ so the user can paste it in a bug report.
    Always exits 0 (the diagnostic itself succeeded; the issues it
    reports are surfaced inline).
    """
    import datetime as _dt
    import platform as _plat

    import app_paths

    diag = app_paths.diagnostics()

    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("Email Assistant - diagnostic report")
    lines.append("=" * 72)
    lines.append(f"Generated:  {_dt.datetime.now().isoformat()}")
    lines.append(f"Platform:   {_plat.platform()}")
    lines.append("")
    lines.append("--- Paths ---")
    for k, v in diag.items():
        if k == "env_overrides":
            lines.append("env_overrides:")
            for ek, ev in v.items():
                # Truncate PATH which is huge.
                if ek == "PATH" and len(ev) > 200:
                    ev = ev[:200] + "..."
                lines.append(f"  {ek} = {ev}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("")
    lines.append("--- Critical files ---")
    for relname in (".env", ".env.example", "state.db", "token_cache.bin",
                    "google_token.json", "client_secret.json"):
        for base, label in (
            (app_paths.data_dir(), "data"),
            (app_paths.bundle_dir(), "bundle"),
        ):
            p = base / relname
            if p.exists():
                size = p.stat().st_size
                lines.append(f"  [{label:6s}] {p} ({size} bytes)")
    lines.append("")
    lines.append("--- Recent crashes ---")
    crash_dir = app_paths.data_dir() / "logs"
    if crash_dir.exists():
        crashes = sorted(crash_dir.glob("crash_*.txt"),
                         key=lambda p: p.stat().st_mtime, reverse=True)[:5]
        if crashes:
            for c in crashes:
                lines.append(f"  {c}  ({_dt.datetime.fromtimestamp(c.stat().st_mtime).isoformat()})")
        else:
            lines.append("  (none)")
    else:
        lines.append("  (logs dir does not exist yet)")
    lines.append("")
    lines.append("--- Recent agent.log tail (last 40 lines) ---")
    log_file = app_paths.data_dir() / "logs" / "agent.log"
    if log_file.exists():
        try:
            tail = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
            lines.extend(tail)
        except Exception as e:
            lines.append(f"  (could not read agent.log: {e})")
    else:
        lines.append("  (no agent.log yet - agent has not run successfully)")
    lines.append("")

    report = "\n".join(lines)
    print(report)

    # Also persist for paste-into-bug-report.
    try:
        d = app_paths.ensure_data_dir() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        out = d / f"diagnose_{ts}.txt"
        out.write_text(report, encoding="utf-8")
        print(f"\nReport saved to: {out}")
    except Exception:
        pass

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Email Assistant agent")
    parser.add_argument("--once", action="store_true",
                        help="Process current unread mail and exit (for Task Scheduler).")
    parser.add_argument("--auth", action="store_true",
                        help="Run device-code OAuth flow to seed the token cache, then exit.")
    parser.add_argument("--setup", action="store_true",
                        help="Run the interactive .env setup wizard and exit.")
    parser.add_argument("--diagnose", action="store_true",
                        help="Print install paths, versions, and recent logs; exit. "
                             "Use this when reporting a bug.")
    args = parser.parse_args()

    if args.diagnose:
        return _run_diagnostics()

    if args.setup:
        # Init logging so any wizard crash also lands in logs/agent.log,
        # not only in the crash file. The wizard itself uses print() for
        # interactive output, but its call sites (validators, network
        # calls) use the standard logging module.
        _setup_logging(debug=False)
        from setup_wizard import run_wizard
        return run_wizard()

    # Init logging EARLY (before load_settings) so config-load failures
    # are persisted to logs/agent.log, not just dumped to a console
    # window that vanishes when Task Scheduler exits.
    _setup_logging(debug=False)

    try:
        settings = load_settings()
    except RuntimeError as e:
        logger.error("Configuration error: %s", e)
        sys.stderr.write(
            "\n"
            "Email Assistant cannot start: configuration is missing or incomplete.\n"
            f"\n  Reason: {e}\n"
            "\nFix:\n"
            "  1. Run the Setup Wizard (Start Menu -> 'Email Assistant - Setup Wizard',\n"
            "     or:  EmailAssistant.exe --setup).\n"
            "  2. Then sign in:  EmailAssistant.exe --auth\n"
            "  3. Then re-run this command.\n\n"
            f"A full log is at: {(Path('logs') / 'agent.log').resolve()}\n\n"
        )
        return 2
    # Re-init at the right level once we know the user's debug preference.
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

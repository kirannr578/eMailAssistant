"""LLM-driven email analysis.

Given a raw email, return a structured Analysis with whether it's a meeting
request, the proposed time (if any), urgency, and a short notification body.

We use OpenAI's JSON-mode response_format so the output is always valid JSON,
then validate with a Pydantic model so the rest of the agent can rely on shape.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Literal

from dateutil import parser as date_parser
from openai import AzureOpenAI, OpenAI
from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

Urgency = Literal["low", "medium", "high"]

# All providers we support. Keep names short and lower_snake.
PROVIDER_OPENAI = "openai"                 # api.openai.com (paid)
PROVIDER_AZURE_OPENAI = "azure_openai"     # your-resource.openai.azure.com (work account)
PROVIDER_GITHUB_MODELS = "github_models"   # models.github.ai/inference (free with GitHub PAT)
PROVIDER_OLLAMA = "ollama"                 # localhost:11434/v1 (free, local)
PROVIDER_OPENAI_COMPAT = "openai_compat"   # generic OpenAI-compatible endpoint

VALID_PROVIDERS = {
    PROVIDER_OPENAI,
    PROVIDER_AZURE_OPENAI,
    PROVIDER_GITHUB_MODELS,
    PROVIDER_OLLAMA,
    PROVIDER_OPENAI_COMPAT,
}

DEFAULT_BASE_URLS = {
    PROVIDER_GITHUB_MODELS: "https://models.github.ai/inference",
    PROVIDER_OLLAMA: "http://localhost:11434/v1",
}


class Analysis(BaseModel):
    # ----- meeting request -----
    is_meeting_request: bool = False
    meeting_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    meeting_title: str | None = None
    meeting_start_iso: str | None = None
    meeting_end_iso: str | None = None
    location: str | None = None
    attendees: list[str] = Field(default_factory=list)

    # ----- bid request (RFP / RFQ / ITB / "please quote") -----
    is_bid_request: bool = False
    bid_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    bid_project_name: str | None = None
    bid_project_location: str | None = None
    bid_project_type: str | None = None       # e.g. "Mechanical TI", "Ground-up multifamily"

    # Proposal / quote submission ("when does our bid have to be in?")
    bid_due_date_iso: str | None = None
    bid_submission_method: str | None = None  # "email", "portal", "in-person", etc.

    # RFI cutoff ("last day to ask questions of the GC")
    rfi_due_date_iso: str | None = None

    # Pre-bid meeting / walkthrough / site visit
    pre_bid_meeting_iso: str | None = None
    pre_bid_meeting_end_iso: str | None = None
    pre_bid_meeting_mandatory: bool = False
    pre_bid_meeting_location: str | None = None   # physical address, if any
    pre_bid_meeting_link: str | None = None       # virtual meeting URL, if any

    bid_scope_summary: str | None = None
    bid_contact: str | None = None

    # ----- shared -----
    summary: str
    urgency: Urgency = "low"
    suggested_action: str
    notification_text: str

    @field_validator(
        "meeting_start_iso",
        "meeting_end_iso",
        "bid_due_date_iso",
        "rfi_due_date_iso",
        "pre_bid_meeting_iso",
        "pre_bid_meeting_end_iso",
    )
    @classmethod
    def _validate_iso(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        dt = date_parser.isoparse(v)
        return dt.isoformat()

    # Backward-compat: older code paths used `confidence` for meeting confidence.
    @property
    def confidence(self) -> float:
        return self.meeting_confidence


_SYSTEM_PROMPT_TEMPLATE = """\
You are an executive assistant for {company_context} analyzing an inbound email.

You classify each email along TWO independent dimensions: meeting request and bid
request. An email can be one, the other, both, or neither.

(1) MEETING REQUEST detection
A meeting request is someone proposing or confirming a specific time to talk,
meet, or join a call. Replies discussing scheduling count. Marketing blasts,
newsletters, and generic "let me know when you're free" without a specific time
are NOT meeting requests.

If is_meeting_request=true:
- Extract meeting_start_iso. Convert relative phrases ("tomorrow at 3pm") into
  absolute ISO 8601 with offset, using the reference time + user timezone below.
- Leave meeting_end_iso null if not specified.
- Set meeting_confidence (0..1).

(2) BID REQUEST detection
A bid request is anyone inviting our company to submit pricing / a proposal /
a quote on a project. Common signals: "RFP", "RFQ", "ITB" (Invitation To Bid),
"Invitation to Bid", "Request for Proposal", "Request for Quote", "please
provide pricing", "submit a bid", "we are bidding [project]", "plans attached
for bid", "bid due [date]", "please quote", "are you interested in bidding".
Construction / contracting context applies. The email may name the project,
location, scope, due date for responses, and a contact / submittal address.

NOT bid requests: vendor sales pitches asking US to buy something, generic
marketing, status updates on jobs already in progress, change orders on
existing contracts.

If is_bid_request=true:
- Set bid_confidence (0..1).
- Extract bid_project_name (best-guess, may be in subject or body).
- Extract bid_project_location (city / address / region if mentioned).
- Extract bid_project_type (trade or scope, e.g. "Mechanical TI",
  "Ground-up multifamily", "Site demo", "Steel erection"). Null if unclear.
- Extract bid_scope_summary (one sentence: what work is being bid).
- Extract bid_contact (name and/or email of who to submit the bid to).

PROPOSAL / BID SUBMISSION DETAILS
- bid_due_date_iso = when OUR proposal/bid/quote must be submitted (absolute
  ISO 8601 with offset). If only a date is given, use 17:00 in the user's
  timezone. Look for phrases like "bid due", "proposals due", "responses due
  by", "must be received by", "submit by".
- bid_submission_method = how to submit. Common values: "email",
  "online portal", "in-person", "fax", "BuildingConnected", "Procore",
  "iSqFt", "uploaded to <link>". Null if not specified.

RFI (Request For Information) CUTOFF
- rfi_due_date_iso = last day to send questions to the GC / owner.
  Look for "RFI deadline", "questions due by", "last day for questions",
  "all RFIs must be submitted by". Distinct from the bid due date.
  Null if not mentioned.

PRE-BID MEETING / WALKTHROUGH / SITE VISIT
Construction bids often include a pre-bid conference, jobwalk, walkthrough,
or site visit. Look for phrases like: "pre-bid meeting", "pre-bid
conference", "site walk", "jobwalk", "walkthrough", "site visit",
"mandatory walkthrough", "non-mandatory pre-bid".
- pre_bid_meeting_iso = start time (absolute ISO 8601 with offset).
- pre_bid_meeting_end_iso = end time if given, else null.
- pre_bid_meeting_mandatory = true ONLY if the email explicitly says
  mandatory / required / must attend. "Recommended" / "encouraged" / silence
  on the topic = false.
- pre_bid_meeting_location = physical address of the meeting if it's
  in-person. Null if it's virtual-only.
- pre_bid_meeting_link = URL of the virtual meeting (Teams, Zoom, Meet,
  Webex, etc.) if it's virtual or hybrid. Null if in-person-only.

NOT bid requests: vendor sales pitches asking US to buy something, generic
marketing, status updates on jobs already in progress, change orders on
existing contracts.

(3) URGENCY (always set)
- high   = action needed within hours (bid due today/tomorrow, mandatory
  walkthrough today/tomorrow, urgent meeting)
- medium = within a day or two
- low    = informational, no rush

(4) NOTIFICATION TEXT (always write)
A SHORT (<= 320 chars) one-line message suitable for SMS / WhatsApp / Telegram.
Start with the most important tag the email warrants:
- "[BID]"     if is_bid_request (use this even if also a meeting)
- "[MEETING]" if is_meeting_request only
- "[URGENT]"  if neither but urgency is high
- "[INFO]"    otherwise
For bid requests, include: sender/GC, project name, bid due date, and
"PRE-BID MANDATORY <date/time>" or "PRE-BID <date/time>" when known.
For meetings, include sender, subject, and the meeting time.

Return STRICT JSON matching this schema. No markdown, no commentary.
{{
  "is_meeting_request": bool,
  "meeting_confidence": float,
  "meeting_title": string | null,
  "meeting_start_iso": string | null,
  "meeting_end_iso":   string | null,
  "location":          string | null,
  "attendees": [string],

  "is_bid_request": bool,
  "bid_confidence": float,
  "bid_project_name":         string | null,
  "bid_project_location":     string | null,
  "bid_project_type":         string | null,
  "bid_due_date_iso":         string | null,
  "bid_submission_method":    string | null,
  "rfi_due_date_iso":         string | null,
  "pre_bid_meeting_iso":      string | null,
  "pre_bid_meeting_end_iso":  string | null,
  "pre_bid_meeting_mandatory": bool,
  "pre_bid_meeting_location": string | null,
  "pre_bid_meeting_link":     string | null,
  "bid_scope_summary":        string | null,
  "bid_contact":              string | null,

  "summary": string,
  "urgency": "low" | "medium" | "high",
  "suggested_action": string,
  "notification_text": string
}}
"""


def _company_context(company_name: str, company_aliases: list[str]) -> str:
    if not company_name and not company_aliases:
        return "the user"
    parts = []
    if company_name:
        parts.append(f'"{company_name}"')
    aliases = [a for a in company_aliases if a and a != company_name]
    if aliases:
        parts.append(f"(also referred to as: {', '.join(aliases)})")
    return " ".join(parts)


def _build_user_prompt(
    *,
    sender: str,
    to: list[str],
    subject: str,
    body: str,
    received_iso: str,
    user_timezone: str,
) -> str:
    return f"""\
Reference time (now): {received_iso}
User's timezone:      {user_timezone}

--- EMAIL ---
From:    {sender}
To:      {', '.join(to) if to else '(unknown)'}
Subject: {subject}

{body}
--- END EMAIL ---
"""


def _build_client(
    *,
    provider: str,
    api_key: str,
    model: str,
    base_url: str,
    azure_endpoint: str,
    azure_api_version: str,
):
    """Instantiate the right SDK client for the chosen provider.

    All non-Azure providers use the OpenAI Python SDK with a custom base_url -
    this works for OpenAI, GitHub Models, Ollama, and any OpenAI-compatible
    server (LM Studio, vLLM, llama.cpp server, etc.).
    """
    if provider not in VALID_PROVIDERS:
        raise ValueError(
            f"Unknown LLM_PROVIDER {provider!r}. Valid: {sorted(VALID_PROVIDERS)}"
        )

    if provider == PROVIDER_AZURE_OPENAI:
        if not azure_endpoint:
            raise RuntimeError("LLM_PROVIDER=azure_openai requires AZURE_OPENAI_ENDPOINT.")
        return AzureOpenAI(
            api_key=api_key,
            api_version=azure_api_version or "2024-08-01-preview",
            azure_endpoint=azure_endpoint,
        )

    # All other providers go through the standard OpenAI client.
    effective_base_url = base_url or DEFAULT_BASE_URLS.get(provider) or None

    # Ollama doesn't need a real API key but the SDK requires a non-empty value.
    effective_api_key = api_key or ("ollama" if provider == PROVIDER_OLLAMA else "")
    if not effective_api_key:
        raise RuntimeError(f"LLM_PROVIDER={provider} requires an API key.")

    return OpenAI(api_key=effective_api_key, base_url=effective_base_url)


class EmailAnalyzer:
    def __init__(
        self,
        *,
        provider: str = PROVIDER_OPENAI,
        api_key: str,
        model: str,
        user_timezone: str,
        base_url: str = "",
        azure_endpoint: str = "",
        azure_api_version: str = "",
        company_name: str = "",
        company_aliases: list[str] | None = None,
    ) -> None:
        self._provider = provider
        self._client = _build_client(
            provider=provider,
            api_key=api_key,
            model=model,
            base_url=base_url,
            azure_endpoint=azure_endpoint,
            azure_api_version=azure_api_version,
        )
        # For Azure, the "model" param is actually the deployment name.
        self._model = model
        self._user_timezone = user_timezone
        self._system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
            company_context=_company_context(company_name, company_aliases or []),
        )

    def analyze(
        self,
        *,
        sender: str,
        to: list[str],
        subject: str,
        body: str,
        received_at: datetime,
    ) -> Analysis:
        user_prompt = _build_user_prompt(
            sender=sender,
            to=to,
            subject=subject,
            body=body[:8000],
            received_iso=received_at.isoformat(),
            user_timezone=self._user_timezone,
        )

        # response_format json_object is supported by OpenAI/Azure/GitHub Models.
        # Some smaller / local models (older Ollama models) ignore it but still
        # tend to produce JSON when instructed; we validate either way.
        kwargs = dict(
            model=self._model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        if self._provider != PROVIDER_OLLAMA:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.error("LLM (%s) call failed: %s", self._provider, e)
            return _fallback_analysis(subject, sender)

        raw = resp.choices[0].message.content or "{}"
        # Some models wrap JSON in ```json fences; strip them defensively.
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("LLM returned invalid JSON: %s\nRaw: %s", e, raw[:500])
            return _fallback_analysis(subject, sender)

        try:
            return Analysis(**data)
        except ValidationError as e:
            logger.warning("LLM JSON failed schema validation: %s\nRaw: %s", e, raw[:500])
            return _fallback_analysis(subject, sender)


def _fallback_analysis(subject: str, sender: str) -> Analysis:
    """Conservative default if the LLM call fails or returns garbage."""
    return Analysis(
        is_meeting_request=False,
        meeting_confidence=0.0,
        is_bid_request=False,
        bid_confidence=0.0,
        summary=f"Could not auto-analyze email '{subject}' from {sender}.",
        urgency="low",
        suggested_action="Open email manually to review.",
        notification_text=f"[INFO] {sender}: {subject[:200]} (auto-analysis failed)",
    )


def derive_meeting_window(
    analysis: Analysis,
    *,
    default_duration_minutes: int,
) -> tuple[datetime, datetime] | None:
    """Return (start, end) as aware datetimes, or None if no usable start."""
    if not analysis.meeting_start_iso:
        return None
    start = date_parser.isoparse(analysis.meeting_start_iso)
    if analysis.meeting_end_iso:
        end = date_parser.isoparse(analysis.meeting_end_iso)
    else:
        end = start + timedelta(minutes=default_duration_minutes)
    if end <= start:
        end = start + timedelta(minutes=default_duration_minutes)
    return start, end


def derive_bid_reminder_window(
    analysis: Analysis,
    *,
    duration_minutes: int = 30,
) -> tuple[datetime, datetime] | None:
    """Return (start, end) for a calendar reminder placed AT the bid due time.

    If the email gives only a date with no time, the LLM is instructed to
    use 17:00 local. We honor that. Returns None if no usable due date or
    if the due date is already in the past.
    """
    if not analysis.bid_due_date_iso:
        return None
    due = date_parser.isoparse(analysis.bid_due_date_iso)
    if due <= datetime.now(due.tzinfo):
        return None
    end = due + timedelta(minutes=duration_minutes)
    return due, end


def derive_pre_bid_window(
    analysis: Analysis,
    *,
    default_duration_minutes: int = 60,
) -> tuple[datetime, datetime] | None:
    """Return (start, end) for the pre-bid walkthrough / conference.

    Default duration is 60 min (typical for a site walk) when no end is given.
    Returns None if no usable start or if the meeting is already in the past.
    """
    if not analysis.pre_bid_meeting_iso:
        return None
    start = date_parser.isoparse(analysis.pre_bid_meeting_iso)
    if start <= datetime.now(start.tzinfo):
        return None
    if analysis.pre_bid_meeting_end_iso:
        end = date_parser.isoparse(analysis.pre_bid_meeting_end_iso)
    else:
        end = start + timedelta(minutes=default_duration_minutes)
    if end <= start:
        end = start + timedelta(minutes=default_duration_minutes)
    return start, end

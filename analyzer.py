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
    is_meeting_request: bool
    confidence: float = Field(ge=0.0, le=1.0)
    meeting_title: str | None = None
    meeting_start_iso: str | None = None
    meeting_end_iso: str | None = None
    location: str | None = None
    attendees: list[str] = Field(default_factory=list)
    summary: str
    urgency: Urgency = "low"
    suggested_action: str
    notification_text: str

    @field_validator("meeting_start_iso", "meeting_end_iso")
    @classmethod
    def _validate_iso(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        # Try to parse and re-emit so downstream code gets a normalized form.
        dt = date_parser.isoparse(v)
        return dt.isoformat()


_SYSTEM_PROMPT = """\
You are an executive assistant analyzing an inbound email on behalf of the user.

Your job:
1. Decide whether the email is a *meeting request* (someone proposing or confirming a
   specific time to talk, meet, or join a call). Replies discussing scheduling count.
   Marketing blasts, newsletters, and generic "let me know when you're free" without a
   specific time are NOT meeting requests.
2. If it IS a meeting request, extract the proposed start time. Convert relative phrases
   like "tomorrow at 3pm" into an absolute ISO 8601 datetime using the provided
   reference time and timezone. If no end time is given, leave meeting_end_iso null and
   the caller will assume a default duration.
3. Estimate urgency: high (response needed within hours / time-sensitive),
   medium (within a day or two), low (informational, no rush).
4. Write a SHORT (<= 320 chars) notification_text suitable for SMS/WhatsApp,
   starting with one of: [MEETING], [URGENT], [INFO]. Include sender, subject,
   and the proposed time if any.

Return STRICT JSON matching this schema (no markdown, no commentary):
{
  "is_meeting_request": bool,
  "confidence": float (0..1),
  "meeting_title": string | null,
  "meeting_start_iso": string | null,    // ISO 8601 with offset, e.g. 2026-05-04T15:00:00-05:00
  "meeting_end_iso":   string | null,
  "location": string | null,             // physical room or video link
  "attendees": [string],                 // email addresses
  "summary": string,                     // 1-2 sentence plain-English summary
  "urgency": "low" | "medium" | "high",
  "suggested_action": string,            // what the user should do next
  "notification_text": string            // SMS-ready
}
"""


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
                {"role": "system", "content": _SYSTEM_PROMPT},
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
        confidence=0.0,
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

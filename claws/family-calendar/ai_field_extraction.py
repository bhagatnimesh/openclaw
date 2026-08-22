from __future__ import annotations

from datetime import datetime
import base64
import json
import mimetypes
import os
from pathlib import Path
import re
from typing import Any, Callable
import urllib.request


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_CALENDAR_AI_FIELD_MODEL = "gpt-5.4-mini"
DEFAULT_TIMEOUT_SECONDS = 8
MIN_CONFIDENCE = 0.8
AI_FIELD_EXTRACTION_ENABLED_ENV = "N4OS_CALENDAR_AI_FIELD_EXTRACTION_ENABLED"

VALID_ACTIONS = {
    "create_event",
    "update_event",
    "delete_event",
    "list_events",
    "family_briefing",
    "preparation_checklist",
    "add_guests",
}
VALID_SLOT_KEYS = {
    "title",
    "date",
    "date_text",
    "start_time",
    "time_text",
    "duration_minutes",
    "all_day",
    "calendar_name",
    "guest_aliases",
    "target_reference",
    "location",
    "description",
    "recurrence",
}
VALID_MISSING_FIELDS = {
    "title",
    "date",
    "time",
    "event",
    "guest_contacts",
    "guests",
}
VALID_GUEST_ALIASES = {
    "dad",
    "mom",
    "family",
    "parents",
    "both",
}

UrlOpen = Callable[..., Any]


CALENDAR_AI_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "operation": {
            "type": "string",
            "enum": sorted(VALID_ACTIONS),
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "calendar": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": ["string", "null"]},
                "id_hint": {"type": ["string", "null"]},
            },
            "required": ["name", "id_hint"],
        },
        "event": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "summary": {"type": ["string", "null"]},
                "description": {"type": ["string", "null"]},
                "location": {"type": ["string", "null"]},
                "start": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "dateTime": {"type": ["string", "null"]},
                        "date": {"type": ["string", "null"]},
                        "timeZone": {"type": ["string", "null"]},
                    },
                    "required": ["dateTime", "date", "timeZone"],
                },
                "end": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "dateTime": {"type": ["string", "null"]},
                        "date": {"type": ["string", "null"]},
                        "timeZone": {"type": ["string", "null"]},
                    },
                    "required": ["dateTime", "date", "timeZone"],
                },
                "attendees": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "alias": {"type": "string", "enum": sorted(VALID_GUEST_ALIASES)},
                        },
                        "required": ["alias"],
                    },
                },
                "recurrence": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "summary",
                "description",
                "location",
                "start",
                "end",
                "attendees",
                "recurrence",
            ],
        },
        "list": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "timeMin": {"type": ["string", "null"]},
                "timeMax": {"type": ["string", "null"]},
            },
            "required": ["timeMin", "timeMax"],
        },
        "target": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"query": {"type": ["string", "null"]}},
            "required": ["query"],
        },
        "missing_fields": {"type": "array", "items": {"type": "string"}},
        "clarification_question": {"type": ["string", "null"]},
    },
    "required": [
        "operation",
        "confidence",
        "calendar",
        "event",
        "list",
        "target",
        "missing_fields",
        "clarification_question",
    ],
}


def _clean_string(value: Any) -> str:
    return str(value or "").strip()


def _clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (_clean_string(item) for item in value) if item]


def _normalize_recurrence(values: Any) -> list[str]:
    weekday_codes = {
        "MON": "MO",
        "TUE": "TU",
        "WED": "WE",
        "THU": "TH",
        "FRI": "FR",
        "SAT": "SA",
        "SUN": "SU",
    }
    normalized = []
    for value in _clean_string_list(values):
        rule = value.strip().upper()
        if not rule.startswith("RRULE:"):
            continue

        def normalize_byday(match: re.Match[str]) -> str:
            tokens = []
            for token in match.group(1).split(","):
                parts = re.fullmatch(r"(?P<prefix>[+-]?\d+)?(?P<day>[A-Z]+)", token)
                if parts is None:
                    return match.group(0)
                day = weekday_codes.get(parts.group("day"), parts.group("day"))
                tokens.append(f"{parts.group('prefix') or ''}{day}")
            return "BYDAY=" + ",".join(tokens)

        rule = re.sub(r"BYDAY=([^;]+)", normalize_byday, rule)
        normalized.append(rule)
    return normalized


def _round_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(confidence, 1.0)), 2)


def _extract_response_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    chunks = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
    return "\n".join(chunks).strip()


def _json_object_from_text(value: str) -> dict[str, Any]:
    cleaned = value.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("Calendar AI field extraction returned non-object JSON")
    return parsed


def _clean_slots(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    unknown = set(value) - VALID_SLOT_KEYS
    if unknown:
        raise ValueError(f"Calendar AI field extraction returned unsupported slots: {sorted(unknown)}")

    slots: dict[str, Any] = {}
    for key, raw_value in value.items():
        if key == "duration_minutes":
            try:
                duration = int(raw_value)
            except (TypeError, ValueError):
                continue
            if 1 <= duration <= 1440:
                slots[key] = duration
            continue
        if key == "all_day":
            slots[key] = bool(raw_value)
            continue
        if key == "recurrence":
            recurrence = _normalize_recurrence(raw_value)
            if recurrence:
                slots[key] = recurrence
            continue
        if key == "guest_aliases":
            aliases = [
                alias.lower()
                for alias in _clean_string_list(raw_value)
            ]
            invalid = [alias for alias in aliases if alias not in VALID_GUEST_ALIASES]
            if invalid:
                raise ValueError(f"Calendar AI field extraction returned unsupported guests: {invalid}")
            if aliases:
                slots[key] = aliases
            continue
        cleaned = _clean_string(raw_value)
        if cleaned:
            if re.search(r"[\w.+-]+@[\w.-]+\.\w+", cleaned):
                raise ValueError("Calendar AI field extraction must not return raw email addresses")
            slots[key] = cleaned
    return slots


def validate_calendar_ai_fields(raw: dict[str, Any], request: str) -> dict[str, Any]:
    if "operation" in raw or "event" in raw or "calendar" in raw or "list" in raw:
        raw = _api_context_fields_to_legacy_fields(raw, request)

    action = _clean_string(raw.get("action")) or "create_event"
    if action not in VALID_ACTIONS:
        raise ValueError(f"Calendar AI field extraction returned invalid action: {action}")

    confidence = _round_confidence(raw.get("confidence"))
    if confidence < MIN_CONFIDENCE:
        raise ValueError("Calendar AI field extraction confidence below threshold")

    slots = _clean_slots(raw.get("slots"))
    missing_fields = [
        field
        for field in _clean_string_list(raw.get("missing_fields"))
        if field in VALID_MISSING_FIELDS
    ]
    supplied_fields = {
        "title": bool(slots.get("title")),
        "date": bool(slots.get("date")),
        "time": bool(slots.get("start_time")) or slots.get("all_day") is True,
        "event": bool(slots.get("target_reference")),
    }
    missing_fields = [field for field in missing_fields if not supplied_fields.get(field, False)]
    return {
        "action": action,
        "confidence": confidence,
        "slots": slots,
        "missing_fields": missing_fields,
        "clarification_question": _clean_string(raw.get("clarification_question")) or None,
        "normalized_request": request,
    }


def _reference_time_text(now: datetime | None) -> str:
    if now is None:
        return "not provided"
    if now.tzinfo is None:
        return now.isoformat()
    return now.astimezone().isoformat()


def _system_prompt() -> str:
    return (
        "You parse family-calendar requests into a Google Calendar v3-style draft. "
        "Return only compact JSON. Do not call tools, create events, invent calendar ids, "
        "or include raw email addresses. Google Calendar event creation uses events.insert "
        "with a calendarId plus an event resource; timed events use start.dateTime and "
        "end.dateTime with timeZone, while all-day events use start.date and end.date. "
        "Event resource fields relevant here include summary, description, location, "
        "start, end, attendees, recurrence, and extendedProperties.private. Attendees "
        "normally contain email addresses, but this parser must use aliases only for "
        "mom, dad, family, parents, or both. For list requests, use operation list_events "
        "with a time window. For family planning summaries, use operation family_briefing. "
        "For update, delete, or add-guests requests, put the existing event title or other "
        "grounded target description in target.query. "
        "Keep the requested operation and list required date, time, event target, or guest "
        "contact information in missing_fields. Resolve relative dates against the provided reference_time "
        "and America/Los_Angeles. If the user says morning, afternoon, evening, or night "
        "without a clock time, mark time missing instead of guessing. For bare clock "
        "numbers like 'at 3', infer the most likely AM/PM from ordinary family scheduling "
        "context. Preserve user meaning over literal filler words in summary."
    )


def _api_context_schema() -> dict[str, Any]:
    return {
        "operation": (
            "create_event | update_event | delete_event | list_events | family_briefing | "
            "preparation_checklist | add_guests"
        ),
        "confidence": "number 0..1",
        "calendar": {
            "name": "optional user-visible calendar name",
            "id_hint": "optional explicit calendar id only if provided by user",
        },
        "event": {
            "summary": "event title",
            "description": "optional notes",
            "location": "optional location",
            "start": {
                "dateTime": "RFC3339 timestamp for timed events",
                "date": "YYYY-MM-DD for all-day events",
                "timeZone": "America/Los_Angeles",
            },
            "end": {
                "dateTime": "RFC3339 timestamp for timed events",
                "date": "YYYY-MM-DD for all-day events",
                "timeZone": "America/Los_Angeles",
            },
            "attendees": [{"alias": "mom | dad | family | parents | both"}],
            "recurrence": ["optional RRULE strings only when explicit"],
        },
        "list": {
            "timeMin": "RFC3339 inclusive lower bound",
            "timeMax": "RFC3339 exclusive upper bound",
        },
        "missing_fields": "array of title/date/time/event/guest_contacts/guests",
        "clarification_question": "optional concise question",
    }


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _api_context_fields_to_legacy_fields(raw: dict[str, Any], request: str) -> dict[str, Any]:
    slots: dict[str, Any] = {}
    operation = _clean_string(raw.get("operation")) or "create_event"
    event = raw.get("event") if isinstance(raw.get("event"), dict) else {}
    calendar = raw.get("calendar") if isinstance(raw.get("calendar"), dict) else {}
    list_window = raw.get("list") if isinstance(raw.get("list"), dict) else {}
    target = raw.get("target") if isinstance(raw.get("target"), dict) else {}

    target_query = _clean_string(target.get("query"))
    if target_query:
        slots["target_reference"] = target_query

    summary = _clean_string(event.get("summary"))
    if summary:
        slots["title"] = summary
    calendar_name = _clean_string(calendar.get("name")) or _clean_string(calendar.get("id_hint"))
    if calendar_name:
        slots["calendar_name"] = calendar_name

    for api_key, slot_key in (("location", "location"), ("description", "description")):
        value = _clean_string(event.get(api_key))
        if value:
            slots[slot_key] = value

    start = event.get("start") if isinstance(event.get("start"), dict) else {}
    end = event.get("end") if isinstance(event.get("end"), dict) else {}
    start_dt = _parse_datetime(start.get("dateTime"))
    end_dt = _parse_datetime(end.get("dateTime"))
    if start_dt is not None:
        slots["date"] = start_dt.date().isoformat()
        slots["start_time"] = f"{start_dt.hour:02d}:{start_dt.minute:02d}"
    elif _clean_string(start.get("date")):
        slots["date"] = _clean_string(start.get("date"))
        slots["all_day"] = True
    if start_dt is not None and end_dt is not None and end_dt > start_dt:
        duration = int((end_dt - start_dt).total_seconds() / 60)
        if 1 <= duration <= 1440:
            slots["duration_minutes"] = duration

    list_start = _parse_datetime(list_window.get("timeMin"))
    if operation == "list_events" and list_start is not None:
        slots["date"] = list_start.date().isoformat()

    attendees = event.get("attendees")
    if isinstance(attendees, list):
        aliases = []
        for attendee in attendees:
            if not isinstance(attendee, dict):
                continue
            alias = _clean_string(attendee.get("alias")).lower()
            if alias:
                aliases.append(alias)
        if aliases:
            slots["guest_aliases"] = aliases

    recurrence = _normalize_recurrence(event.get("recurrence"))
    if recurrence:
        slots["recurrence"] = recurrence

    return {
        "action": operation,
        "confidence": raw.get("confidence"),
        "slots": slots,
        "missing_fields": raw.get("missing_fields") or [],
        "clarification_question": _clean_string(raw.get("clarification_question")) or None,
        "normalized_request": request,
    }


class CalendarAIFieldExtractor:
    primary_calendar_api_context = True

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_CALENDAR_AI_FIELD_MODEL,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        urlopen: UrlOpen = urllib.request.urlopen,
    ):
        cleaned_key = api_key.strip()
        if not cleaned_key:
            raise RuntimeError("Calendar AI field extraction needs OPENAI_API_KEY.")
        self.api_key = cleaned_key
        self.model = model.strip() or DEFAULT_CALENDAR_AI_FIELD_MODEL
        self.timeout_seconds = timeout_seconds
        self.urlopen = urlopen

    @classmethod
    def from_env(cls) -> "CalendarAIFieldExtractor":
        return cls(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            model=os.environ.get(
                "N4OS_CALENDAR_AI_FIELD_MODEL",
                DEFAULT_CALENDAR_AI_FIELD_MODEL,
            ),
        )

    @classmethod
    def from_env_or_none(cls) -> "CalendarAIFieldExtractor | None":
        enabled = os.environ.get(AI_FIELD_EXTRACTION_ENABLED_ENV, "").strip().lower()
        if enabled not in {"1", "true", "yes", "on"}:
            return None
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            return None
        return cls.from_env()

    def extract(
        self,
        request: str,
        *,
        now: datetime | None = None,
        baseline_intent: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context_payload = {
            key: value
            for key, value in (context or {}).items()
            if key != "semantic_image_path"
        }
        user_payload = json.dumps(
            {
                "request": request,
                "reference_time": _reference_time_text(now),
                "baseline_intent": baseline_intent or {},
                "context": context_payload,
                "output_schema": _api_context_schema(),
            },
            sort_keys=True,
        )
        user_content: str | list[dict[str, str]] = user_payload
        image_path = _clean_string((context or {}).get("semantic_image_path"))
        if image_path and Path(image_path).is_file():
            path = Path(image_path)
            mime_type = mimetypes.guess_type(str(path))[0] or "image/jpeg"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            user_content = [
                {"type": "input_text", "text": user_payload},
                {
                    "type": "input_image",
                    "image_url": f"data:{mime_type};base64,{encoded}",
                    "detail": "high",
                },
            ]
        body = {
            "model": self.model,
            "store": False,
            "max_output_tokens": 500,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "n4os_calendar_operation",
                    "strict": True,
                    "schema": CALENDAR_AI_SCHEMA,
                }
            },
            "input": [
                {"role": "system", "content": _system_prompt()},
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
        }
        api_request = urllib.request.Request(
            OPENAI_RESPONSES_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "n4os-calendar-ai-field-extraction/0.1",
            },
            method="POST",
        )
        with self.urlopen(api_request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        text = _extract_response_text(payload)
        if not text:
            raise RuntimeError("OpenAI returned no calendar field extraction text.")
        return validate_calendar_ai_fields(_json_object_from_text(text), request)

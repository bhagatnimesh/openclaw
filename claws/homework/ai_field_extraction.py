from __future__ import annotations

from datetime import datetime
import json
import os
import re
from typing import Any, Callable
import urllib.request


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_HOMEWORK_AI_FIELD_MODEL = "gpt-4.1-mini"
DEFAULT_TIMEOUT_SECONDS = 8
MIN_CONFIDENCE = 0.8
AI_FIELD_EXTRACTION_ENABLED_ENV = "N4OS_HOMEWORK_AI_FIELD_EXTRACTION_ENABLED"
DEFAULT_TIMEZONE = "America/Los_Angeles"

VALID_ACTIONS = {
    "capture_assignment",
    "capture_submission",
    "homework_status",
    "clarify",
}
VALID_CHILDREN = {"Nysha", "Navya"}
VALID_SLOT_KEYS = {
    "title",
    "child",
    "class_name",
    "subject",
    "assigned_date",
    "due_date",
    "due_time",
    "status",
    "notes",
    "grade",
    "week_range",
    "daily_work",
    "calendar_name",
}
VALID_MISSING_FIELDS = {
    "child",
    "title",
    "class",
    "due_date",
    "due_time",
    "matching_assignment",
}

UrlOpen = Callable[..., Any]


def _clean_string(value: Any) -> str:
    return str(value or "").strip()


def _clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (_clean_string(item) for item in value) if item]


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
        raise ValueError("Homework AI field extraction returned non-object JSON")
    return parsed


def _contains_raw_email(value: str) -> bool:
    return bool(re.search(r"[\w.+-]+@[\w.-]+\.\w+", value))


def _valid_iso_date(value: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _valid_time(value: str) -> bool:
    match = re.fullmatch(r"(\d{2}):(\d{2})", value)
    if not match:
        return False
    hour = int(match.group(1))
    minute = int(match.group(2))
    return 0 <= hour <= 23 and 0 <= minute <= 59


def _clean_slots(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    unknown = set(value) - VALID_SLOT_KEYS
    if unknown:
        raise ValueError(f"Homework AI field extraction returned unsupported slots: {sorted(unknown)}")

    slots: dict[str, Any] = {}
    for key, raw_value in value.items():
        cleaned = _clean_string(raw_value)
        if not cleaned:
            continue
        if _contains_raw_email(cleaned):
            raise ValueError("Homework AI field extraction must not return raw email addresses")
        if key == "calendar_name":
            continue
        if key in {"assigned_date", "due_date"} and not _valid_iso_date(cleaned):
            continue
        if key == "due_time" and not _valid_time(cleaned):
            continue
        if key == "child":
            child = next((candidate for candidate in VALID_CHILDREN if candidate.lower() == cleaned.lower()), "")
            if not child:
                raise ValueError(f"Homework AI field extraction returned invalid child: {cleaned}")
            slots[key] = child
            continue
        slots[key] = cleaned
    return slots


def validate_homework_ai_fields(raw: dict[str, Any], request: str) -> dict[str, Any]:
    if "homework" in raw or "calendar" in raw:
        raw = _api_context_fields_to_legacy_fields(raw, request)

    action = _clean_string(raw.get("action")) or "capture_assignment"
    if action not in VALID_ACTIONS:
        raise ValueError(f"Homework AI field extraction returned invalid action: {action}")

    confidence = _round_confidence(raw.get("confidence"))
    if confidence < MIN_CONFIDENCE:
        raise ValueError("Homework AI field extraction confidence below threshold")

    missing_fields = [
        field
        for field in _clean_string_list(raw.get("missing_fields"))
        if field in VALID_MISSING_FIELDS
    ]
    return {
        "action": action,
        "confidence": confidence,
        "slots": _clean_slots(raw.get("slots")),
        "missing_fields": missing_fields,
        "clarification_question": _clean_string(raw.get("clarification_question")) or None,
        "normalized_request": request,
    }


def _operation_to_action(operation: str) -> str:
    if operation in VALID_ACTIONS:
        return operation
    return "capture_assignment"


def _api_context_fields_to_legacy_fields(raw: dict[str, Any], request: str) -> dict[str, Any]:
    slots: dict[str, Any] = {}
    homework = raw.get("homework") if isinstance(raw.get("homework"), dict) else {}
    calendar = raw.get("calendar") if isinstance(raw.get("calendar"), dict) else {}
    event = calendar.get("event") if isinstance(calendar.get("event"), dict) else {}

    for api_key, slot_key in (
        ("title", "title"),
        ("child", "child"),
        ("class_name", "class_name"),
        ("assigned_date", "assigned_date"),
        ("due_date", "due_date"),
        ("due_time", "due_time"),
        ("status", "status"),
        ("notes", "notes"),
        ("grade", "grade"),
        ("week_range", "week_range"),
        ("daily_work", "daily_work"),
    ):
        value = _clean_string(homework.get(api_key))
        if value:
            slots[slot_key] = value

    calendar_name = _clean_string(calendar.get("calendar_name")) or _clean_string(calendar.get("name"))
    if calendar_name:
        slots["calendar_name"] = calendar_name

    summary = _clean_string(event.get("summary"))
    if summary and not slots.get("title"):
        slots["title"] = summary.removeprefix("Homework due:").strip() or summary

    start = event.get("start") if isinstance(event.get("start"), dict) else {}
    date_value = _clean_string(start.get("date"))
    date_time = _clean_string(start.get("dateTime"))
    if date_time and not slots.get("due_date"):
        try:
            parsed = datetime.fromisoformat(date_time.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            slots["due_date"] = parsed.date().isoformat()
            slots.setdefault("due_time", f"{parsed.hour:02d}:{parsed.minute:02d}")
    elif date_value and not slots.get("due_date"):
        slots["due_date"] = date_value

    return {
        "action": _operation_to_action(_clean_string(raw.get("operation")) or "capture_assignment"),
        "confidence": raw.get("confidence"),
        "slots": slots,
        "missing_fields": raw.get("missing_fields") or [],
        "clarification_question": _clean_string(raw.get("clarification_question")) or None,
        "normalized_request": request,
    }


def merge_ai_homework_fields(
    intent: dict[str, Any],
    ai_fields: dict[str, Any],
    request: str,
) -> dict[str, Any]:
    refined = dict(intent)
    slots = ai_fields.get("slots") if isinstance(ai_fields.get("slots"), dict) else {}
    action = _clean_string(ai_fields.get("action")) or str(refined.get("intent") or "capture_assignment")
    if action == "clarify":
        refined["intent"] = "clarify"
        refined["clarification_question"] = _clean_string(ai_fields.get("clarification_question")) or None
        refined["missing_fields"] = list(ai_fields.get("missing_fields") or [])
    elif refined.get("intent") == "capture_submission" and action == "capture_assignment":
        # Explicit completion commands are authoritative; AI refinement may enrich
        # the match fields but must not turn a submission back into a new assignment.
        refined["intent"] = "capture_submission"
    elif action in VALID_ACTIONS:
        refined["intent"] = action

    for slot_key, intent_key in (
        ("child", "child"),
        ("title", "title"),
        ("class_name", "subject"),
        ("subject", "subject"),
        ("assigned_date", "assigned_date"),
        ("due_date", "due_date"),
        ("due_time", "due_time"),
        ("status", "status"),
        ("notes", "notes"),
        ("grade", "grade"),
        ("week_range", "week_range"),
        ("daily_work", "daily_work"),
        ("calendar_name", "calendar_name"),
    ):
        value = _clean_string(slots.get(slot_key))
        if value:
            refined[intent_key] = value
    if refined.get("intent") == "capture_submission":
        refined["status"] = "submitted"
    if refined.get("child"):
        refined["children"] = [str(refined["child"])]
    refined["ai_field_extraction"] = {
        "confidence": ai_fields.get("confidence"),
        "missing_fields": list(ai_fields.get("missing_fields") or []),
        "normalized_request": request,
    }
    return refined


def _reference_time_text(now: datetime | None) -> str:
    if now is None:
        return "not provided"
    if now.tzinfo is None:
        return now.isoformat()
    return now.astimezone().isoformat()


def _system_prompt() -> str:
    return (
        "You parse kids homework capture requests into a homework draft and an optional "
        "Google Calendar event draft. Return only compact JSON. Do not call tools, write "
        "files, create calendar events, invent calendar ids, or include raw email addresses. "
        "Homework fields are child, title, class_name, assigned_date, due_date, due_time, "
        "status, notes, grade, week_range, and daily_work. Valid children are Nysha and "
        "Navya; if the child is not mentioned, use Nysha. Use class schedules from context "
        "when present: Art is Saturday 10:00 for both kids, RSM Math is Tuesday 15:30 for "
        "Nysha, and school homework is due Friday. Resolve relative dates against "
        "reference_time in America/Los_Angeles. Google Calendar event creation uses "
        "events.insert with a calendarId plus an event resource; timed events use "
        "start.dateTime and end.dateTime with timeZone, while all-day events use start.date "
        "and end.date. For homework due reminders, produce a timed due event only when a "
        "due date is known. Use operation clarify only when required homework identity "
        "or due information cannot be recovered."
    )


def _api_context_schema() -> dict[str, Any]:
    return {
        "operation": "capture_assignment | capture_submission | homework_status | clarify",
        "confidence": "number 0..1",
        "homework": {
            "title": "concise assignment title",
            "child": "Nysha | Navya, default Nysha if not mentioned",
            "class_name": "Art | RSM Math | School | other visible class",
            "assigned_date": "YYYY-MM-DD",
            "due_date": "YYYY-MM-DD when known",
            "due_time": "HH:MM when known",
            "status": "assigned | submitted",
            "notes": "visible instructions or useful OCR summary",
            "grade": "optional grade",
            "week_range": "optional packet week range",
            "daily_work": "optional daily-work lines",
        },
        "calendar": {
            "create_due_event": "boolean",
            "calendar_name": "Nysha School Calendar | Navya School Calendar",
            "event": {
                "summary": "Homework due: title",
                "start": {
                    "dateTime": "RFC3339 timestamp for timed events",
                    "date": "YYYY-MM-DD for all-day events",
                    "timeZone": DEFAULT_TIMEZONE,
                },
                "end": {
                    "dateTime": "RFC3339 timestamp for timed events",
                    "date": "YYYY-MM-DD for all-day events",
                    "timeZone": DEFAULT_TIMEZONE,
                },
            },
        },
        "missing_fields": "array of child/title/class/due_date/due_time/matching_assignment",
        "clarification_question": "optional concise question",
    }


class HomeworkAIFieldExtractor:
    primary_homework_api_context = True

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_HOMEWORK_AI_FIELD_MODEL,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        urlopen: UrlOpen = urllib.request.urlopen,
    ):
        cleaned_key = api_key.strip()
        if not cleaned_key:
            raise RuntimeError("Homework AI field extraction needs OPENAI_API_KEY.")
        self.api_key = cleaned_key
        self.model = model.strip() or DEFAULT_HOMEWORK_AI_FIELD_MODEL
        self.timeout_seconds = timeout_seconds
        self.urlopen = urlopen

    @classmethod
    def from_env(cls) -> "HomeworkAIFieldExtractor":
        return cls(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            model=os.environ.get(
                "N4OS_HOMEWORK_AI_FIELD_MODEL",
                DEFAULT_HOMEWORK_AI_FIELD_MODEL,
            ),
        )

    @classmethod
    def from_env_or_none(cls) -> "HomeworkAIFieldExtractor | None":
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
        body = {
            "model": self.model,
            "store": False,
            "max_output_tokens": 600,
            "input": [
                {"role": "system", "content": _system_prompt()},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "request": request,
                            "reference_time": _reference_time_text(now),
                            "baseline_intent": baseline_intent or {},
                            "context": context or {},
                            "output_schema": {
                                **_api_context_schema(),
                                "legacy_also_accepted": {
                                    "action": "string",
                                    "slots": "object",
                                },
                            },
                        },
                        sort_keys=True,
                    ),
                },
            ],
        }
        api_request = urllib.request.Request(
            OPENAI_RESPONSES_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "n4os-homework-ai-field-extraction/0.1",
            },
            method="POST",
        )
        with self.urlopen(api_request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        text = _extract_response_text(payload)
        if not text:
            raise RuntimeError("OpenAI returned no homework field extraction text.")
        return validate_homework_ai_fields(_json_object_from_text(text), request)

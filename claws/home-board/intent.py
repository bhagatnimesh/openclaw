from __future__ import annotations

from datetime import date, datetime, time, timedelta
import re
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_TIMEZONE = "America/Los_Angeles"
VALID_CONTEXTS = {
    "before_leave",
    "at_home",
    "school",
    "kitchen",
    "airport",
    "general",
}
VALID_PRIORITIES = {"low", "medium", "high"}
PERSON_WORDS = ("helper", "nysha", "nimesh", "dad", "mom", "family", "everyone")
ACTION_WORDS = (
    "take",
    "bring",
    "put",
    "keep",
    "carry",
    "return",
    "pack",
    "sign",
    "submit",
    "payment",
    "journal",
    "fridge",
    "passport",
)
WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _default_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
    if now.tzinfo is None:
        return now.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
    return now.astimezone(ZoneInfo(DEFAULT_TIMEZONE))


def _clean_spaces(value: str) -> str:
    return " ".join(value.split()).strip(" ,.;:")


def _title_text(value: str) -> str:
    cleaned = _clean_spaces(value)
    if not cleaned:
        return cleaned
    return cleaned[0].upper() + cleaned[1:]


def _next_weekday(reference: datetime, weekday: int) -> date:
    days = (weekday - reference.weekday()) % 7
    if days == 0:
        days = 7
    return (reference + timedelta(days=days)).date()


def _current_or_next_weekday(reference: datetime, weekday: int) -> date:
    return (reference + timedelta(days=(weekday - reference.weekday()) % 7)).date()


def _extract_date(text: str, reference: datetime) -> tuple[str, str]:
    lowered = text.lower()
    if re.search(r"\btoday\b", lowered):
        return reference.date().isoformat(), "today"
    if re.search(r"\btomorrow\b", lowered):
        return (reference + timedelta(days=1)).date().isoformat(), "tomorrow"

    for name, weekday in WEEKDAYS.items():
        if re.search(rf"\bnext\s+{name}\b", lowered):
            return _next_weekday(reference, weekday).isoformat(), f"next {name}"
        if re.search(rf"\b{name}\b", lowered):
            return _current_or_next_weekday(reference, weekday).isoformat(), name

    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", lowered)
    if match is not None:
        return match.group(1), "date"

    return reference.date().isoformat(), ""


def _expires_after_day(item_date: str) -> str:
    parsed = date.fromisoformat(item_date)
    expires_on = parsed + timedelta(days=1)
    return datetime.combine(
        expires_on,
        time.min,
        tzinfo=ZoneInfo(DEFAULT_TIMEZONE),
    ).isoformat()


def _strip_date_words(text: str, date_anchor: str) -> str:
    cleaned = text
    if date_anchor:
        cleaned = re.sub(rf"\b{re.escape(date_anchor)}\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(today|tomorrow)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", cleaned)
    cleaned = re.sub(
        r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return _clean_spaces(cleaned)


def _infer_context(text: str) -> tuple[str, str | None]:
    lowered = text.lower()
    if re.search(r"\bbefore\b", lowered) and re.search(r"\b(leaves?|leaving|leave)\b", lowered):
        return "before_leave", "leave_home"
    if re.search(r"\bairport|flight|passport|passports\b", lowered):
        return "airport", "airport"
    if re.search(r"\bfridge|kitchen|food|lunch|dinner|snack\b", lowered):
        return "kitchen", None
    if re.search(r"\bschool|journal|form|permission slip|homework|library\b", lowered):
        return "school", None
    if re.search(r"\bhelper|home|house\b", lowered):
        return "at_home", None
    return "general", None


def _infer_priority(text: str) -> str:
    lowered = text.lower()
    if re.search(r"\b(urgent|important|must|critical|asap)\b", lowered):
        return "high"
    if re.search(r"\b(low priority|whenever)\b", lowered):
        return "low"
    return "medium"


def _normalize_person(value: str) -> str:
    cleaned = _clean_spaces(value)
    lowered = cleaned.lower()
    if lowered in ("we", "us", "everyone", "everybody", "family", "all"):
        return "Family"
    if lowered in ("i", "me", "myself"):
        return "Me"
    if not cleaned:
        return "Family"
    return cleaned[:1].upper() + cleaned[1:]


def _normalize_message(value: str) -> str:
    cleaned = _clean_spaces(value)
    cleaned = re.sub(r"^(to|that)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"^(remind\s+(?:me|us|her|him|them|everyone|family)\s+to)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return _title_text(cleaned)


def _extract_before_pattern(text: str) -> tuple[str, str] | None:
    match = re.search(
        r"\bbefore\s+(?P<person>[A-Za-z][A-Za-z0-9_-]*|I|we|everyone|family)\s+"
        r"(?P<leave>leaves?|leave|leaving)\b[:,]?\s*(?P<body>.+)",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        match = re.search(
            r"\bbefore\s+(?P<context>airport|school|work|bedtime)\b[:,]?\s*(?P<body>.+)",
            text,
            flags=re.IGNORECASE,
        )
        if match is None:
            return None
        return "Family", match.group("body")

    return _normalize_person(match.group("person")), match.group("body")


def _extract_person_and_message(text: str) -> tuple[str, str]:
    before_match = _extract_before_pattern(text)
    if before_match is not None:
        return before_match

    match = re.search(
        r"^(?P<person>[A-Za-z][A-Za-z0-9_-]*)\s*:\s*(?P<body>.+)$",
        text,
        flags=re.IGNORECASE,
    )
    if match is not None:
        return _normalize_person(match.group("person")), match.group("body")

    match = re.search(
        r"^(?P<person>[A-Za-z][A-Za-z0-9_-]*)\s*,?\s+"
        r"(?P<body>(?:should|needs?\s+to|has\s+to|must|take|bring|put|keep|return|carry|remind|pick|drop|sign|submit).*)$",
        text,
        flags=re.IGNORECASE,
    )
    if match is not None:
        person = _normalize_person(match.group("person"))
        body = match.group("body")
        body = re.sub(r"^(should|needs?\s+to|has\s+to|must)\s+", "", body, flags=re.IGNORECASE)
        return person, body

    match = re.search(
        r"\b(?P<person>we|us|everyone|family)\s+"
        r"(?P<body>(?:need\s+to|should|must|have\s+to).+)",
        text,
        flags=re.IGNORECASE,
    )
    if match is not None:
        body = re.sub(
            r"^(need\s+to|should|must|have\s+to)\s+",
            "",
            match.group("body"),
            flags=re.IGNORECASE,
        )
        return _normalize_person(match.group("person")), body

    match = re.search(
        r"\bremind\s+(?P<person>me|us|her|him|them|everyone|family|[A-Za-z][A-Za-z0-9_-]*)\s+to\s+(?P<body>.+)",
        text,
        flags=re.IGNORECASE,
    )
    if match is not None:
        person = match.group("person")
        if person.lower() in ("her", "him", "them"):
            person = "Family"
        return _normalize_person(person), match.group("body")

    return "Family", text


def _looks_like_home_board_add(text: str) -> bool:
    lowered = text.lower()
    return bool(
        re.search(r"\bbefore\b", lowered)
        or re.search(rf"\b({'|'.join(PERSON_WORDS)})\b", lowered)
        or re.search(rf"\b({'|'.join(ACTION_WORDS)})\b", lowered)
    )


def _strip_bulk_prefix(text: str) -> str:
    return re.sub(
        r"^\s*(today at home|home board|for today|today)\s*[:,-]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )


def _split_bulk_segments(text: str) -> list[str]:
    cleaned = _strip_bulk_prefix(text)
    line_segments = [
        _clean_spaces(re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line))
        for line in cleaned.splitlines()
    ]
    line_segments = [segment for segment in line_segments if segment]
    if len(line_segments) > 1:
        return line_segments

    normalized = re.sub(
        rf"\s+(?=({'|'.join(PERSON_WORDS)})\b\s*(?:[:,]|should|needs?\s+to|has\s+to|must|take|bring|put|keep|return|carry|remind|pick|drop|sign|submit))",
        "\n",
        cleaned,
        flags=re.IGNORECASE,
    )
    pieces = re.split(
        r"\s*(?:;|\n|,(?=\s*(?:helper|nysha|nimesh|dad|mom|family|everyone)\b))\s*",
        normalized,
        flags=re.IGNORECASE,
    )
    return [_clean_spaces(piece) for piece in pieces if _clean_spaces(piece)]


def extract_items(request: str, now: datetime | None = None) -> list[dict[str, Any]]:
    segments = _split_bulk_segments(request)
    if len(segments) <= 1:
        intent = extract_intent(request, now=now)
        return [intent] if intent.get("intent") == "add_item" else []

    items = []
    shared_date, shared_anchor = _extract_date(request, _default_now(now))
    for segment in segments:
        segment_with_date = segment
        if shared_anchor and not re.search(
            r"\b(today|tomorrow|next\s+monday|next\s+tuesday|next\s+wednesday|next\s+thursday|next\s+friday|next\s+saturday|next\s+sunday|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{4}-\d{2}-\d{2})\b",
            segment,
            flags=re.IGNORECASE,
        ):
            segment_with_date = f"{segment} {shared_anchor if shared_anchor != 'date' else shared_date}"
        intent = extract_intent(segment_with_date, now=now)
        if intent.get("intent") == "add_item" and not intent.get("missing_fields"):
            items.append(intent)
    return items


def extract_intent(request: str, now: datetime | None = None) -> dict[str, Any]:
    reference = _default_now(now)
    cleaned_request = _clean_spaces(request)
    lowered = cleaned_request.lower()
    if not cleaned_request:
        return {"intent": "unknown", "missing_fields": ["message"]}

    if re.fullmatch(
        r"(?:use\s+)?(?:home board|today at home|house board)",
        lowered,
    ):
        item_date, _ = _extract_date(cleaned_request, reference)
        return {"intent": "list_items", "date": item_date, "status": "pending"}

    if re.search(r"\b(show|list|what'?s|whats|what is)\b", lowered) and re.search(
        r"\b(home board|today at home|at home|house board)\b",
        lowered,
    ):
        item_date, _ = _extract_date(cleaned_request, reference)
        return {"intent": "list_items", "date": item_date, "status": "pending"}

    if re.search(r"\b(mark|complete|done|finished)\b", lowered) and re.search(
        r"\b(home board|today at home|notice|item|reminder)\b",
        lowered,
    ):
        match = re.search(r"\b(?:item|id)\s+([A-Za-z0-9_-]+)\b", cleaned_request)
        return {
            "intent": "mark_done",
            "item_id": match.group(1) if match is not None else "",
            "missing_fields": [] if match is not None else ["item_id"],
        }

    if not _looks_like_home_board_add(cleaned_request):
        return {"intent": "unknown", "missing_fields": []}

    batch_items = []
    if "\n" in request or ";" in request or re.search(
        rf",\s*(?:{'|'.join(PERSON_WORDS)})\b",
        request,
        flags=re.IGNORECASE,
    ):
        batch_items = extract_items(request, now=reference)
    if len(batch_items) > 1:
        return {
            "intent": "add_items",
            "items": batch_items,
            "missing_fields": [],
        }

    item_date, date_anchor = _extract_date(cleaned_request, reference)
    body_without_date = _strip_date_words(cleaned_request, date_anchor)
    person, raw_message = _extract_person_and_message(body_without_date)
    message = _normalize_message(raw_message)
    context, trigger = _infer_context(cleaned_request)
    missing = []
    if not message:
        missing.append("message")

    return {
        "intent": "add_item",
        "person_or_group": person,
        "message": message,
        "date": item_date,
        "context": context if context in VALID_CONTEXTS else "general",
        "trigger": trigger,
        "priority": _infer_priority(cleaned_request),
        "expires_at": _expires_after_day(item_date),
        "missing_fields": missing,
    }

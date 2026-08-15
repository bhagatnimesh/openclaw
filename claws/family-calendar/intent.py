from __future__ import annotations

from datetime import datetime, time, timedelta
import json
import os
import re
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_TIMEZONE = "America/Los_Angeles"

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}
MONTH_NAME_PATTERN = "|".join(
    re.escape(value)
    for value in sorted(MONTHS, key=len, reverse=True)
)
WEEKDAY_NAME_PATTERN = "|".join(re.escape(value) for value in WEEKDAYS)
CALENDAR_TARGET_FOLLOW_WORD_PATTERN = "|".join(
    [
        "for",
        "at",
        "around",
        "on",
        "from",
        "every",
        "repeat",
        "repeating",
        "today",
        "tomorrow",
        "time",
        "location",
        MONTH_NAME_PATTERN,
        WEEKDAY_NAME_PATTERN,
    ]
)
CALENDAR_TARGET_TERMINATOR = (
    rf"(?=$|[.,;:)]|\s+(?:(?:{CALENDAR_TARGET_FOLLOW_WORD_PATTERN})\b|\d{{1,2}}(?:[/-]\d{{1,2}})?\b))"
)

WEEKDAY_RRULE_CODES = {
    "monday": "MO",
    "tuesday": "TU",
    "wednesday": "WE",
    "thursday": "TH",
    "friday": "FR",
    "saturday": "SA",
    "sunday": "SU",
}
ORDINAL_WEEKDAY_POSITIONS = {
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "third": 3,
    "3rd": 3,
    "fourth": 4,
    "4th": 4,
    "last": -1,
}

METADATA_MARKER = "N4OS_METADATA:"
METADATA_EXTENDED_PROPERTY = "n4os_metadata"

DEFAULT_METADATA = {
    "owner": "unknown",
    "person": "family",
    "category": "",
    "preparation_needed": False,
    "preparation_notes": "",
    "assistant_help_needed": False,
    "assistant_name": "",
    "assistant_help_request": "",
    "assistant_context": "",
}

VALID_OWNERS = {"dad", "mom", "both", "grandmom", "unknown"}
HOUSEHOLD_GUEST_EMAIL_ENV = {
    "dad": "N4OS_CALENDAR_DAD_GUEST_EMAIL",
    "mom": "N4OS_CALENDAR_MOM_GUEST_EMAIL",
}

OWNER_NAME_ALIASES = {
    "mom": "mom",
    "mum": "mom",
    "mummy": "mom",
    "niyati": "mom",
    "niyaati": "mom",
    "niyathi": "mom",
    "dad": "dad",
    "papa": "dad",
    "papu": "dad",
    "nimesh": "dad",
    "namesh": "dad",
    "both": "both",
    "parents": "both",
    "grand mom": "grandmom",
    "grandmom": "grandmom",
    "dadi": "grandmom",
    "tarla": "grandmom",
    "unknown": "unknown",
}
OWNER_ALIAS_PATTERN = "|".join(
    re.escape(value)
    for value in sorted(OWNER_NAME_ALIASES, key=len, reverse=True)
)

GUEST_NAME_ALIASES = {
    "mom": ["mom"],
    "mum": ["mom"],
    "mummy": ["mom"],
    "niyati": ["mom"],
    "niyaati": ["mom"],
    "niyathi": ["mom"],
    "dad": ["dad"],
    "papa": ["dad"],
    "papu": ["dad"],
    "nimesh": ["dad"],
    "namesh": ["dad"],
    "both": ["dad", "mom"],
    "parents": ["dad", "mom"],
    "family": ["dad", "mom"],
}
GUEST_ALIAS_PATTERN = "|".join(
    re.escape(value)
    for value in sorted(GUEST_NAME_ALIASES, key=len, reverse=True)
)
HOUSEHOLD_GUEST_DISPLAY_NAMES = {"dad": "Dad", "mom": "Mom"}

PERSON_NAME_ALIASES = {
    "elder one": "Nysha",
    "big n": "Nysha",
    "nisha": "Nysha",
    "nysha": "Nysha",
    "nyshoo": "Nysha",
    "nyshuu": "Nysha",
    "littler one": "Navya",
    "smaller one": "Navya",
    "small n": "Navya",
    "naavya": "Navya",
    "navya": "Navya",
    "grand mom": "Tarla",
    "grandmom": "Tarla",
    "dadi": "Tarla",
    "tarla": "Tarla",
    "family": "family",
}
PERSON_ALIAS_PATTERN = "|".join(
    re.escape(value)
    for value in sorted(PERSON_NAME_ALIASES, key=len, reverse=True)
)

OWNER_ACTION_WORD_PATTERN = (
    r"take|handle|bring|drive|do|go|pick\s+up|pickup|drop\s+off|dropoff"
)
EVENT_TITLE_ACTION_WORD_PATTERN = (
    r"cancel|downgrade|renew|review|discuss|call|text|email|message|book|schedule|pay|"
    r"submit|pick\s+up|pickup|drop\s+off|dropoff|buy|get|return|prepare|"
    r"send|order|reserve|change|replace|fix|repair"
)

CATEGORY_HINTS = {
    "school": ("school", "pickup", "class", "teacher", "homework"),
    "medical": ("doctor", "dentist", "dental", "medical", "therapy"),
    "shopping": ("shopping", "shop", "buy", "mall", "store", "tshirt", "shirt"),
    "travel": ("flight", "airport", "passport", "visa", "trip", "travel", "sfo"),
    "activity": ("gymnastics", "soccer", "piano", "practice", "game", "class"),
    "social": ("dinner", "party", "birthday", "playdate", "meet", "rahul"),
    "household": ("trash", "repair", "clean", "house", "home", "groceries"),
}
ASSISTANT_NAMES = ("Noah",)
ASSISTANT_NAME_PATTERN = "|".join(re.escape(name) for name in ASSISTANT_NAMES)
ASSISTANT_HELP_MARKER_RE = re.compile(
    rf"\b(?:(?:i\s+)?(?:want|need|could\s+use)\s+(?:an?\s+)?ai\s+assistant"
    rf"(?:\s+(?:help|support))?|ask\s+(?:{ASSISTANT_NAME_PATTERN})\s+"
    rf"(?:to\s+help|for\s+help)|(?:{ASSISTANT_NAME_PATTERN})\s*,?\s+help)\b",
    re.IGNORECASE,
)
ASSISTANT_HELP_MARKER_LINE_RE = re.compile(
    rf"^\s*(?:(?:i\s+)?(?:want|need|could\s+use)\s+(?:an?\s+)?ai\s+assistant"
    rf"(?:\s+(?:help|support))?|ask\s+(?:{ASSISTANT_NAME_PATTERN})\s+"
    rf"(?:to\s+help|for\s+help)|(?:{ASSISTANT_NAME_PATTERN})\s*,?\s+help)\.?\s*$",
    re.IGNORECASE,
)
ASSISTANT_DETAIL_LABEL_RE = re.compile(
    r"\b(?P<label>assistant\s+help|help|assistant\s+context|context|email|notes?)\s*:\s*",
    re.IGNORECASE,
)


def _clean_spaces(value: str) -> str:
    return " ".join(value.split()).strip()


def _alias_key(value: str) -> str:
    return _clean_spaces(value).lower()


def _canonical_owner(value: str) -> str:
    return OWNER_NAME_ALIASES.get(_alias_key(value), "unknown")


def _canonical_person(value: str) -> str:
    return PERSON_NAME_ALIASES.get(_alias_key(value), _clean_spaces(value))


def _clean_assistant_text(value: Any) -> str:
    return _clean_spaces(str(value or "").strip())


def _assistant_name_from_marker(marker: str) -> str:
    for name in ASSISTANT_NAMES:
        if re.search(rf"\b{re.escape(name)}\b", marker, flags=re.IGNORECASE):
            return name
    return ASSISTANT_NAMES[0]


def _assistant_context_value(label: str, value: str) -> str:
    cleaned = _clean_spaces(value.strip(" ."))
    if not cleaned:
        return ""

    normalized_label = label.lower().replace("assistant ", "")
    if normalized_label == "email":
        return f"Email: {cleaned}"
    return cleaned


def _normalize_assistant_help_request(value: str) -> str:
    cleaned = _clean_spaces(value.strip(" .,:;-"))
    cleaned = re.sub(
        r"^(?:help|support)?\s*(?:to|with|for)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^(?:to|with|for)\s+", "", cleaned, flags=re.IGNORECASE)
    if not cleaned:
        return ""
    return cleaned[:1].upper() + cleaned[1:]


def _split_assistant_details(value: str) -> tuple[str, str]:
    matches = list(ASSISTANT_DETAIL_LABEL_RE.finditer(value))
    if not matches:
        return _normalize_assistant_help_request(value), ""

    help_parts = []
    context_parts = []
    leading = _normalize_assistant_help_request(value[: matches[0].start()])
    if leading:
        help_parts.append(leading)

    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        label = match.group("label")
        detail = value[match.end() : next_start]
        cleaned_detail = _clean_spaces(detail.strip(" ."))
        if not cleaned_detail:
            continue

        normalized_label = label.lower().replace("assistant ", "")
        if normalized_label == "help":
            help_parts.append(_normalize_assistant_help_request(cleaned_detail))
        else:
            context = _assistant_context_value(label, cleaned_detail)
            if context:
                context_parts.append(context)

    return "\n".join(part for part in help_parts if part), "\n".join(context_parts)


def _assistant_description_text(help_request: str, assistant_context: str) -> str | None:
    lines = ["Assistant help: " + (help_request or "requested")]
    if assistant_context:
        lines.append(f"Assistant context: {assistant_context}")
    return "\n".join(lines)


def _assistant_metadata(
    help_request: str,
    assistant_context: str,
    assistant_name: str,
) -> dict[str, Any]:
    return {
        "assistant_help_needed": True,
        "assistant_name": assistant_name,
        "assistant_help_request": help_request,
        "assistant_context": assistant_context,
    }


def _extract_line_assistant_help(user_text: str) -> tuple[str, dict[str, Any], str | None] | None:
    lines = [line.strip() for line in user_text.splitlines()]
    marker_indexes = [
        index
        for index, line in enumerate(lines)
        if line and ASSISTANT_HELP_MARKER_LINE_RE.match(line)
    ]
    if not marker_indexes:
        return None

    marker_index = marker_indexes[0]
    assistant_name = _assistant_name_from_marker(lines[marker_index])
    before = [line for line in lines[:marker_index] if line]
    after = [line for line in lines[marker_index + 1 :] if line]
    main_lines = list(before)
    help_parts: list[str] = []
    context_parts: list[str] = []

    for line in after:
        label_match = ASSISTANT_DETAIL_LABEL_RE.match(line)
        if label_match is not None:
            label = label_match.group("label")
            value = line[label_match.end() :]
            if label.lower().replace("assistant ", "") == "help":
                help_text = _normalize_assistant_help_request(value)
                if help_text:
                    help_parts.append(help_text)
            else:
                context = _assistant_context_value(label, value)
                if context:
                    context_parts.append(context)
            continue

        if before:
            help_text = _normalize_assistant_help_request(line)
            if help_text:
                help_parts.append(help_text)
        else:
            main_lines.append(line)

    help_request = "\n".join(help_parts)
    assistant_context = "\n".join(context_parts)
    return (
        _clean_spaces(" ".join(main_lines)),
        _assistant_metadata(help_request, assistant_context, assistant_name),
        _assistant_description_text(help_request, assistant_context),
    )


def _extract_assistant_help(user_text: str) -> tuple[str, dict[str, Any], str | None]:
    line_result = _extract_line_assistant_help(user_text)
    if line_result is not None:
        return line_result

    match = ASSISTANT_HELP_MARKER_RE.search(user_text)
    if match is None:
        return user_text, {}, None

    assistant_name = _assistant_name_from_marker(match.group(0))
    main_text = _clean_spaces(user_text[: match.start()].strip(" .,\n"))
    help_request, assistant_context = _split_assistant_details(user_text[match.end() :])
    if not main_text and help_request:
        main_text = help_request
    return (
        main_text,
        _assistant_metadata(help_request, assistant_context, assistant_name),
        _assistant_description_text(help_request, assistant_context),
    )


def _append_assistant_description(
    description: str | None,
    assistant_description: str | None,
) -> str | None:
    if assistant_description is None:
        return description
    if not description:
        return assistant_description
    if assistant_description.lower() in description.lower():
        return description
    return f"{description}\n{assistant_description}"


def _clean_human_notes(description: str | None) -> str:
    if description is None:
        return ""

    notes = description.strip()
    if notes.lower().startswith("notes:"):
        notes = notes[len("notes:") :].strip()
    return notes


def _default_metadata() -> dict[str, Any]:
    return dict(DEFAULT_METADATA)


def _normalize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    normalized = _default_metadata()
    if metadata is not None:
        normalized.update(metadata)

    owner = str(normalized.get("owner") or "unknown").lower()
    normalized["owner"] = owner if owner in VALID_OWNERS else "unknown"
    normalized["person"] = str(normalized.get("person") or "family")
    normalized["category"] = str(normalized.get("category") or "")
    normalized["preparation_needed"] = bool(normalized.get("preparation_needed"))
    normalized["preparation_notes"] = str(normalized.get("preparation_notes") or "")
    normalized["assistant_name"] = _clean_assistant_text(
        normalized.get("assistant_name"),
    )
    normalized["assistant_help_request"] = _clean_assistant_text(
        normalized.get("assistant_help_request"),
    )
    normalized["assistant_context"] = _clean_assistant_text(
        normalized.get("assistant_context"),
    )
    normalized["assistant_help_needed"] = bool(
        normalized.get("assistant_help_needed")
        or normalized["assistant_help_request"]
        or normalized["assistant_context"],
    )
    if normalized["assistant_help_needed"] and not normalized["assistant_name"]:
        normalized["assistant_name"] = ASSISTANT_NAMES[0]
    return normalized


def read_metadata_from_description(description: str | None) -> tuple[str, dict[str, Any]]:
    if not description:
        return "", _default_metadata()

    marker_index = description.find(METADATA_MARKER)
    if marker_index < 0:
        return _clean_human_notes(description), _default_metadata()

    notes = _clean_human_notes(description[:marker_index])
    raw_metadata = description[marker_index + len(METADATA_MARKER) :].strip()
    try:
        parsed = json.loads(raw_metadata)
    except json.JSONDecodeError:
        parsed = {}

    if not isinstance(parsed, dict):
        parsed = {}

    return notes, _normalize_metadata(parsed)


def _read_metadata_from_extended_properties(event: dict[str, Any]) -> dict[str, Any] | None:
    extended_properties = event.get("extendedProperties")
    if not isinstance(extended_properties, dict):
        return None

    private_properties = extended_properties.get("private")
    if not isinstance(private_properties, dict):
        return None

    raw_metadata = private_properties.get(METADATA_EXTENDED_PROPERTY)
    if not isinstance(raw_metadata, str):
        return None

    try:
        parsed = json.loads(raw_metadata)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None

    return _normalize_metadata(parsed)


def read_metadata_from_event(event: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    notes, legacy_metadata = read_metadata_from_description(event.get("description"))
    metadata = _read_metadata_from_extended_properties(event)
    return notes, metadata or legacy_metadata


def write_human_description(notes: str | None) -> str | None:
    clean_notes, _ = read_metadata_from_description(notes)
    return clean_notes or None


def write_metadata_to_description(
    notes: str | None,
    metadata: dict[str, Any] | None,
) -> str:
    clean_notes, _ = read_metadata_from_description(notes)
    normalized = _normalize_metadata(metadata)
    metadata_json = json.dumps(normalized, indent=2)
    if clean_notes:
        return f"Notes:\n{clean_notes}\n\n{METADATA_MARKER}\n{metadata_json}"

    return f"{METADATA_MARKER}\n{metadata_json}"


def write_metadata_to_private_extended_properties(
    metadata: dict[str, Any] | None,
) -> dict[str, str]:
    metadata_json = json.dumps(_normalize_metadata(metadata), separators=(",", ":"))
    return {METADATA_EXTENDED_PROPERTY: metadata_json}


def _default_now(now: datetime | None) -> datetime:
    if now is not None:
        if now.tzinfo is None:
            return now.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        return now

    return datetime.now(ZoneInfo(DEFAULT_TIMEZONE))


def _next_weekday(reference: datetime, weekday: int) -> datetime:
    days_ahead = (weekday - reference.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7

    return reference + timedelta(days=days_ahead)


def _current_or_next_weekday(reference: datetime, weekday: int) -> datetime:
    return reference + timedelta(days=(weekday - reference.weekday()) % 7)


def _weekday_in_next_calendar_week(reference: datetime, weekday: int) -> datetime:
    days_until_next_monday = 7 - reference.weekday()
    next_monday = reference + timedelta(days=days_until_next_monday)
    return next_monday + timedelta(days=weekday)


def _start_of_day(value: datetime) -> datetime:
    return datetime.combine(
        value.date(),
        time.min,
        tzinfo=ZoneInfo(DEFAULT_TIMEZONE),
    )


def _date_for_month_day(
    month: int,
    day: int,
    year_text: str | None,
    reference: datetime,
) -> str | None:
    year = int(year_text) if year_text is not None else reference.year
    if year < 100:
        year += 2000

    try:
        parsed = datetime(year, month, day, tzinfo=reference.tzinfo)
    except ValueError:
        return None

    if year_text is None and parsed.date() < reference.date():
        try:
            parsed = datetime(reference.year + 1, month, day, tzinfo=reference.tzinfo)
        except ValueError:
            return None

    return parsed.date().isoformat()


def _strip_absolute_date_text(value: str) -> str:
    value = re.sub(
        rf"\b(?:on\s+)?(?:{MONTH_NAME_PATTERN})\.?\s+"
        r"\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{2,4})?\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    return re.sub(
        r"(?<!\d)(?:\bon\s+)?\d{1,2}/\d{1,2}(?:/\d{2,4})?(?!\d)",
        " ",
        value,
        flags=re.IGNORECASE,
    )


def _extract_date(user_text: str, reference: datetime) -> tuple[str | None, str]:
    lowered = user_text.lower()
    if "tomorrow" in lowered or "tomorrows" in lowered:
        return (reference + timedelta(days=1)).date().isoformat(), "tomorrow"
    if "today" in lowered:
        return reference.date().isoformat(), "today"

    month_name_match = re.search(
        rf"\b(?P<month>{MONTH_NAME_PATTERN})\.?\s+"
        r"(?P<day>\d{1,2})(?:st|nd|rd|th)?"
        r"(?:,?\s+(?P<year>\d{2,4}))?\b",
        lowered,
    )
    if month_name_match is not None:
        month = MONTHS[month_name_match.group("month").rstrip(".")]
        parsed_date = _date_for_month_day(
            month,
            int(month_name_match.group("day")),
            month_name_match.group("year"),
            reference,
        )
        if parsed_date is not None:
            return parsed_date, month_name_match.group(0)

    numeric_date_match = re.search(
        r"(?<!\d)(?P<month>\d{1,2})/(?P<day>\d{1,2})"
        r"(?:/(?P<year>\d{2,4}))?(?!\d)",
        lowered,
    )
    if numeric_date_match is not None:
        parsed_date = _date_for_month_day(
            int(numeric_date_match.group("month")),
            int(numeric_date_match.group("day")),
            numeric_date_match.group("year"),
            reference,
        )
        if parsed_date is not None:
            return parsed_date, numeric_date_match.group(0)

    for name, weekday in WEEKDAYS.items():
        if re.search(rf"\b{name}\s+next week\b", lowered) or re.search(
            rf"\bnext week\s+{name}\b",
            lowered,
        ):
            return _weekday_in_next_calendar_week(reference, weekday).date().isoformat(), name
        if re.search(rf"\b{name}\b", lowered):
            return _next_weekday(reference, weekday).date().isoformat(), name

    return None, ""


def _extract_list_range(
    user_text: str,
    reference: datetime,
) -> tuple[datetime | None, datetime | None]:
    lowered = user_text.lower()

    if re.search(r"\b(?:this|these)\s+year\b", lowered) or "school year" in lowered:
        return reference, reference + timedelta(days=365)
    if "next 7 days" in lowered or "next seven days" in lowered:
        return reference, reference + timedelta(days=7)
    if "upcoming" in lowered or "coming up" in lowered:
        return reference, reference + timedelta(days=30)
    if "tomorrow" in lowered:
        start = _start_of_day(reference + timedelta(days=1))
        return start, start + timedelta(days=1)
    if "today" in lowered:
        start = _start_of_day(reference)
        return start, start + timedelta(days=1)
    if "weekend" in lowered:
        saturday = _current_or_next_weekday(reference, WEEKDAYS["saturday"])
        start = _start_of_day(saturday)
        return start, start + timedelta(days=2)
    if "next week" in lowered:
        monday = _weekday_in_next_calendar_week(reference, WEEKDAYS["monday"])
        start = _start_of_day(monday)
        return start, start + timedelta(days=7)

    for name, weekday in WEEKDAYS.items():
        if re.search(rf"\b{name}\b", lowered):
            day = _current_or_next_weekday(reference, weekday)
            start = _start_of_day(day)
            return start, start + timedelta(days=1)

    return None, None


def _extract_week_briefing_range(
    user_text: str,
    reference: datetime,
) -> tuple[datetime, datetime, str]:
    lowered = user_text.lower()
    if "next week" in lowered:
        monday = _weekday_in_next_calendar_week(reference, WEEKDAYS["monday"])
        start = _start_of_day(monday)
        return start, start + timedelta(days=7), "next week"

    monday = reference - timedelta(days=reference.weekday())
    start = _start_of_day(monday)
    return start, start + timedelta(days=7), "this week"


def _extract_named_person_query(user_text: str) -> str | None:
    match = re.search(
        rf"\b(?:for|about)\s+(?P<person>{PERSON_ALIAS_PATTERN})\b",
        user_text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None

    return _canonical_person(match.group("person"))


def _extract_when_event_query(user_text: str) -> str | None:
    match = re.match(
        r"^\s*when\s+(?:is|are|was|were)\s+(?P<query>.+?)\??\s*$",
        user_text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None

    query = re.sub(
        rf"\b(?:{PERSON_ALIAS_PATTERN})'?s?\b",
        " ",
        match.group("query"),
        flags=re.IGNORECASE,
    )
    query = re.sub(r"\b(?:the|a|an)\b", " ", query, flags=re.IGNORECASE)
    query = " ".join(re.findall(r"[a-z0-9]+", query.lower()))
    return query or None


def _holiday_text_queries(value: str) -> list[str] | None:
    if re.search(r"\b(?:holidays?|vacations?|breaks?|no\s+school)\b", value, flags=re.IGNORECASE):
        return ["holiday", "vacation", "break", "no school"]
    return None


def _extract_school_event_text_query(user_text: str) -> str | None:
    lowered = user_text.lower()
    if "spring break" in lowered:
        return "spring break"
    if "fall break" in lowered:
        return "fall break"
    if "winter break" in lowered:
        return "winter break"
    if "conference" in lowered:
        return "conference"
    if "first day" in lowered and "school" in lowered:
        return "first day of school"
    if "last day" in lowered and "school" in lowered:
        return "last day of school"
    if "minimum day" in lowered or "minimum days" in lowered:
        return "minimum day"
    return None


def _extract_list_metadata_filter(user_text: str) -> dict[str, Any]:
    lowered = user_text.lower()
    filters: dict[str, Any] = {}
    owner_context = re.search(
        r"\b(?:responsible|handling|handle|taking|take|bring|drive)\b",
        lowered,
    )

    if owner_context is not None:
        if re.search(r"\b(?:i|me|my|am i)\b", lowered):
            filters["owner"] = "dad"
        else:
            owner_match = re.search(
                rf"\b(?P<owner>{OWNER_ALIAS_PATTERN})\b",
                user_text,
                flags=re.IGNORECASE,
            )
            if owner_match is not None:
                filters["owner"] = _canonical_owner(owner_match.group("owner"))
            elif re.search(r"\b(?:we|us)\b", lowered):
                filters["owner"] = "both"

    owner_query = re.search(
        rf"\b(?:for|about)\s+(?P<owner>{OWNER_ALIAS_PATTERN})\b",
        user_text,
        flags=re.IGNORECASE,
    )
    if owner_query is not None:
        filters["owner"] = _canonical_owner(owner_query.group("owner"))

    if "preparation" in lowered or "prep" in lowered or re.search(r"\bneeds?\b", lowered):
        filters["preparation_needed"] = True

    person = None if "owner" in filters else _extract_named_person_query(user_text)
    if person is not None and person != "family":
        filters["person"] = person
        filters["text_query"] = person

    when_query = _extract_when_event_query(user_text)
    if when_query is not None:
        filters["text_query"] = when_query

    holiday_queries = _holiday_text_queries(when_query or user_text)
    if holiday_queries is not None:
        filters.pop("text_query", None)
        filters["text_any_queries"] = holiday_queries

    school_query = _extract_school_event_text_query(user_text)
    if school_query is not None:
        filters.pop("text_any_queries", None)
        filters["text_query"] = school_query

    return filters


def _default_list_range_for_filter(
    reference: datetime,
    metadata_filter: dict[str, Any],
) -> tuple[datetime | None, datetime | None]:
    if not metadata_filter:
        return None, None
    if (
        metadata_filter.get("text_query") is not None
        and set(metadata_filter) == {"text_query"}
    ):
        return reference, reference + timedelta(days=365)
    if (
        metadata_filter.get("text_any_queries") is not None
        and set(metadata_filter) == {"text_any_queries"}
    ):
        return reference, reference + timedelta(days=365)

    return reference, reference + timedelta(days=30)


def _extract_delete_range(
    user_text: str,
    reference: datetime,
) -> tuple[datetime, datetime]:
    date, _ = _extract_date(user_text, reference)
    if date is None:
        return reference, reference + timedelta(days=30)

    start = datetime.fromisoformat(f"{date}T00:00:00").replace(
        tzinfo=ZoneInfo(DEFAULT_TIMEZONE),
    )
    return start, start + timedelta(days=1)


def _format_time(hour: int, minute: int, meridiem: str, context: str) -> str | None:
    lowered = context.lower()
    meridiem = meridiem.replace(".", "").lower()

    if hour > 23 or minute > 59:
        return None

    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    elif meridiem == "":
        if "afternoon" in lowered and 1 <= hour <= 5:
            hour += 12
        elif any(word in lowered for word in ("dinner", "evening", "tonight")) and 1 <= hour <= 11:
            hour += 12

    return f"{hour:02d}:{minute:02d}"


def _time_from_match(match: re.Match[str], context: str) -> str | None:
    if match is None:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    meridiem = match.group(3) or ""
    return _format_time(hour, minute, meridiem, context)


def _time_range_match(user_text: str) -> re.Match[str] | None:
    return re.search(
        r"(?:\btime\s*:\s*)?\b(?P<start_hour>\d{1,2})(?::(?P<start_minute>\d{2}))?\s*"
        r"(?P<start_meridiem>am|pm|a\.m\.|p\.m\.)?\s*(?:-|–|to)\s*"
        r"(?P<end_hour>\d{1,2})(?::(?P<end_minute>\d{2}))?\s*"
        r"(?P<end_meridiem>am|pm|a\.m\.|p\.m\.)?\b",
        user_text,
        flags=re.IGNORECASE,
    )


def _minutes_from_hhmm(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def _extract_time_range(user_text: str) -> tuple[str, str] | None:
    match = _time_range_match(user_text)
    if match is None:
        return None

    end_meridiem = match.group("end_meridiem") or ""
    start_meridiem = match.group("start_meridiem") or end_meridiem
    start_time = _format_time(
        int(match.group("start_hour")),
        int(match.group("start_minute") or "0"),
        start_meridiem,
        user_text,
    )
    end_time = _format_time(
        int(match.group("end_hour")),
        int(match.group("end_minute") or "0"),
        end_meridiem or start_meridiem,
        user_text,
    )
    if start_time is None or end_time is None:
        return None
    return start_time, end_time


def _extract_time_range_duration_minutes(user_text: str) -> int | None:
    time_range = _extract_time_range(user_text)
    if time_range is None:
        return None
    start_minutes = _minutes_from_hhmm(time_range[0])
    end_minutes = _minutes_from_hhmm(time_range[1])
    if end_minutes <= start_minutes:
        return None
    return end_minutes - start_minutes


def _extract_standalone_time(user_text: str, context: str) -> str | None:
    named_time = _extract_named_time(user_text)
    if named_time is not None:
        return named_time

    match = re.search(
        r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)\b",
        user_text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None

    return _time_from_match(match, context)


def _extract_named_time(user_text: str) -> str | None:
    if re.search(r"\bnoon\b", user_text, flags=re.IGNORECASE):
        return "12:00"
    return None


def _extract_action_time(user_text: str) -> str | None:
    match = re.search(
        r"\b(?:need to leave at|leave at|depart at|head out at)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b",
        user_text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None

    return _time_from_match(match, user_text)


def _extract_time_range_start(user_text: str) -> str | None:
    time_range = _extract_time_range(user_text)
    return time_range[0] if time_range is not None else None


def _extract_time(user_text: str) -> str | None:
    action_time = _extract_action_time(user_text)
    if action_time is not None:
        return action_time

    range_start = _extract_time_range_start(user_text)
    if range_start is not None:
        return range_start

    named_time = _extract_named_time(user_text)
    if named_time is not None:
        return named_time

    match = re.search(
        r"\b(?:at|around)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b",
        user_text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None

    return _time_from_match(match, user_text)


def _extract_update_time(target_text: str, user_text: str) -> str | None:
    match = re.search(
        r"\b(?:at|around)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b",
        target_text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return _extract_standalone_time(target_text, user_text)

    return _time_from_match(match, user_text)


def _extract_duration_minutes(user_text: str) -> int:
    range_duration = _extract_time_range_duration_minutes(user_text)
    if range_duration is not None:
        return range_duration

    match = re.search(
        r"\bfor\s+(\d+)\s*(minute|minutes|min|hour|hours|hr|hrs)\b",
        user_text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return 60

    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith(("hour", "hr")):
        return amount * 60

    return amount


def _extract_recurrence(
    user_text: str,
    reference: datetime,
) -> dict[str, Any] | None:
    lowered = user_text.lower()
    recurring_context = any(
        re.search(pattern, lowered)
        for pattern in (
            r"\brecurring\s+event\b",
            r"\bevery\b",
            r"\bweekly\b",
            r"\bevery\s+week\b",
            r"\bfor\s+(?:next\s+)?\d+\s+weeks?\b",
            r"\bnext\s+\d+\s+(?:mondays|tuesdays|wednesdays|thursdays|fridays|saturdays|sundays)\b",
        )
    )

    count = None
    count_match = re.search(
        r"\bfor\s+(?:next\s+)?(\d+)\s+weeks?\b",
        lowered,
    )
    if count_match is None:
        count_match = re.search(
            r"\bnext\s+(\d+)\s+(?:mondays|tuesdays|wednesdays|thursdays|fridays|saturdays|sundays)\b",
            lowered,
        )
    if count_match is not None:
        count = int(count_match.group(1))

    if re.search(r"\bevery\s+day\b|\bdaily\b", lowered):
        return {
            "rrule": "RRULE:FREQ=DAILY",
            "start_date": reference.date().isoformat(),
            "label": "every day",
        }

    if re.search(r"\bevery\s+weekdays?\b", lowered):
        start = reference
        while start.weekday() >= 5:
            start += timedelta(days=1)
        return {
            "rrule": "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
            "start_date": start.date().isoformat(),
            "label": "every weekday",
        }

    ordinal_recurrence = _monthly_ordinal_recurrence_from_text(lowered)
    if ordinal_recurrence is not None:
        weekday_name, weekday, weekday_code, position, ordinal_text = ordinal_recurrence
        start = _current_or_next_ordinal_weekday(reference, weekday, position)
        return {
            "rrule": f"RRULE:FREQ=MONTHLY;BYDAY={weekday_code};BYSETPOS={position}",
            "start_date": start.date().isoformat(),
            "label": f"every {ordinal_text} {weekday_name.title()}",
        }

    for name, weekday in WEEKDAYS.items():
        if re.search(rf"\bevery\s+{name}\b", lowered) or (
            recurring_context and re.search(rf"\b{name}\b", lowered)
        ):
            start = _current_or_next_weekday(reference, weekday)
            count_part = f";COUNT={count}" if count is not None else ""
            return {
                "rrule": f"RRULE:FREQ=WEEKLY{count_part};BYDAY={WEEKDAY_RRULE_CODES[name]}",
                "start_date": start.date().isoformat(),
                "label": f"every {name.title()}",
            }

    return None


def _amount_minutes(amount: str, unit: str) -> int:
    normalized_amount = amount.lower()
    normalized_unit = unit.lower()
    if normalized_amount in ("half", "half an", "half a"):
        return 30

    count = 1 if normalized_amount in ("a", "an", "one") else int(normalized_amount)
    if normalized_unit.startswith(("hour", "hr")):
        return count * 60

    return count


def _relative_shift_match(user_text: str) -> tuple[int, tuple[int, int]] | None:
    amount_pattern = r"(?P<amount>\d+|one|an?|half(?:\s+an?))"
    unit_pattern = r"(?P<unit>minute|minutes|min|hour|hours|hr|hrs)"
    patterns = [
        rf"\b(?P<direction>up|back|forward|later|earlier|sooner)\s+by\s+{amount_pattern}\s+{unit_pattern}\b",
        rf"\b(?:by\s+)?{amount_pattern}\s+{unit_pattern}\s+(?P<direction>later|earlier|sooner)\b",
        rf"\b(?P<direction>later|earlier|sooner)\s+by\s+{amount_pattern}\s+{unit_pattern}\b",
        rf"\b(?P<direction>push\s+back|push|delay)\b.+?\bby\s+{amount_pattern}\s+{unit_pattern}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, user_text, flags=re.IGNORECASE)
        if match is None:
            continue

        minutes = _amount_minutes(match.group("amount"), match.group("unit"))
        direction = match.group("direction").lower()
        if direction in ("earlier", "sooner", "up"):
            minutes = -minutes
        return minutes, match.span()

    return None


def _extract_relative_delta_minutes(user_text: str) -> int | None:
    shift = _relative_shift_match(user_text)
    if shift is None:
        return None

    return shift[0]


def _extract_location(user_text: str) -> str | None:
    flight_match = re.search(
        r"\bflight\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?\s+from\s+([A-Za-z0-9 ]+?)(?:,|\.|\s+so\b|\s+and\b|$)",
        user_text,
        flags=re.IGNORECASE,
    )
    if flight_match is not None:
        location = _clean_spaces(flight_match.group(1))
        if location:
            return location

    match = re.search(
        r"\bgo to\s+(.+?)(?:\s+tomorrow|\s+today|\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|\s+to\s+|\s+at\s+|\s+around\s+|\.|$)",
        user_text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None

    location = _clean_spaces(match.group(1))
    return location or None


def _extract_pickup_parts(user_text: str) -> dict[str, str] | None:
    match = re.search(
        rf"\b(?P<adult>{OWNER_ALIAS_PATTERN})\s+picks?\s+up\s+"
        rf"(?P<child>{PERSON_ALIAS_PATTERN})\b(?P<rest>.*)",
        user_text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None

    rest = match.group("rest")
    source_match = re.search(
        r"\bfrom\s+(?P<source>.+?)(?:\.|$)",
        rest,
        flags=re.IGNORECASE,
    )
    source = ""
    if source_match is not None:
        source = source_match.group("source")
        source = re.sub(r"\bon\b.+$", "", source, flags=re.IGNORECASE)
        source = re.sub(
            r"\b(?:at|around)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?\b.*$",
            "",
            source,
            flags=re.IGNORECASE,
        )
        source = _clean_spaces(source.strip(" ,"))

    child = _canonical_person(match.group("child"))
    adult = _clean_spaces(match.group("adult")).title()
    if source:
        title = f"{child} {source} pickup"
        notes = f"{adult} picks up {child} from {source}"
    else:
        title = f"{child} pickup"
        notes = f"{adult} picks up {child}"

    return {
        "adult": adult,
        "child": child,
        "source": source,
        "title": title,
        "notes": notes,
    }


def _extract_purpose(user_text: str) -> str | None:
    match = re.search(
        r"\bto\s+((?:get|buy|shop|pick up).+?)(?:\.|$)",
        user_text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None

    return _clean_spaces(match.group(1))


def _extract_target_calendar(user_text: str) -> str | None:
    patterns = [
        rf"\b(?:add|put|save)\s+(?:it\s+)?(?:to|into|onto|on|in)\s+"
        rf"(?:the\s+)?(?!{EVENT_TITLE_ACTION_WORD_PATTERN}\b)"
        rf"(?P<calendar>[A-Za-z0-9&' -]+?\s+calendar)\b{CALENDAR_TARGET_TERMINATOR}",
        rf"\b(?:in|on|into|onto)\s+(?:the\s+)?(?!{EVENT_TITLE_ACTION_WORD_PATTERN}\b)"
        rf"(?P<calendar>[A-Za-z0-9&' -]+?\s+calendar)\b{CALENDAR_TARGET_TERMINATOR}",
        rf"^\s*(?:/?(?:calendar|event|schedule)\s+)?"
        r"(?:(?:creating|create|add|schedule)\s+(?:an?\s+)?(?:recurring\s+)?(?:event\s+)?)?"
        rf"to\s+(?:the\s+)?(?!{EVENT_TITLE_ACTION_WORD_PATTERN}\b)"
        rf"(?P<calendar>[A-Za-z0-9&' -]+?\s+calendar)\b{CALENDAR_TARGET_TERMINATOR}",
    ]
    for pattern in patterns:
        match = re.search(pattern, user_text, flags=re.IGNORECASE)
        if match is None:
            continue
        calendar = _clean_spaces(match.group("calendar").strip(" .,:;-"))
        if calendar and _looks_like_target_calendar_name(calendar):
            return calendar

    return None


def _looks_like_target_calendar_name(calendar: str) -> bool:
    tokens = set(re.findall(r"[a-z0-9]+", calendar.lower()))
    if "calendar" not in tokens:
        return False
    if tokens & {"school", "family", "home", "personal", "work", "shared"}:
        return True

    alias_tokens = set(PERSON_NAME_ALIASES) | set(OWNER_NAME_ALIASES) | set(GUEST_NAME_ALIASES)
    return bool(tokens & alias_tokens)


def _strip_calendar_target_phrase(title: str) -> str:
    patterns = [
        rf"\b(?:add|put|save)\s+(?:it\s+)?(?:to|into|onto|on|in)\s+"
        rf"(?:the\s+)?(?!{EVENT_TITLE_ACTION_WORD_PATTERN}\b)"
        rf"(?P<calendar>.+?\s+calendar)\b{CALENDAR_TARGET_TERMINATOR}",
        rf"\b(?:in|on|into|onto)\s+(?:the\s+)?(?!{EVENT_TITLE_ACTION_WORD_PATTERN}\b)"
        rf"(?P<calendar>.+?\s+calendar)\b{CALENDAR_TARGET_TERMINATOR}",
        rf"^\s*to\s+(?:the\s+)?(?!{EVENT_TITLE_ACTION_WORD_PATTERN}\b)"
        rf"(?P<calendar>.+?\s+calendar)\b{CALENDAR_TARGET_TERMINATOR}",
    ]

    def replace(match: re.Match[str]) -> str:
        calendar = _clean_spaces(match.group("calendar").strip(" .,:;-"))
        return " " if _looks_like_target_calendar_name(calendar) else match.group(0)

    for pattern in patterns:
        title = re.sub(pattern, replace, title, flags=re.IGNORECASE)
    return title


def _strip_create_words(user_text: str) -> str:
    user_text = re.sub(r"^\s*/(?:calendar|event|schedule)\b", " ", user_text, flags=re.IGNORECASE)
    user_text = re.sub(
        r"^\s*(?:calendar|event|schedule)\s+(?=(?:create|add|schedule)\b)",
        " ",
        user_text,
        flags=re.IGNORECASE,
    )
    title = re.sub(
        r"^\s*(creating|create|add|schedule)\b(?:\s+an?\s+)?(?:recurring\s+)?(?:event\s+)?",
        "",
        user_text,
        flags=re.IGNORECASE,
    )
    return re.sub(
        r"^\s*(?:an?\s+)?(?:recurring\s+)?(?:event\s+)?",
        "",
        title,
        flags=re.IGNORECASE,
    )


def _extract_with_description(user_text: str) -> str | None:
    match = re.search(r"\bwith\s+(.+?)(?:\.|$)", user_text, flags=re.IGNORECASE)
    if match is None:
        return None

    value = _clean_spaces(match.group(1))
    return f"with {value}" if value else None


def _guest_keys_from_text(value: str) -> list[str]:
    keys = []
    seen = set()
    for match in re.finditer(rf"\b(?:{GUEST_ALIAS_PATTERN})\b", value, flags=re.IGNORECASE):
        aliases = GUEST_NAME_ALIASES.get(_alias_key(match.group(0)), [])
        for alias in aliases:
            if alias in seen:
                continue
            seen.add(alias)
            keys.append(alias)
    return keys


def _unknown_guest_text_from_instruction(value: str) -> str | None:
    unknown = re.sub(rf"\b(?:{GUEST_ALIAS_PATTERN})\b", " ", value, flags=re.IGNORECASE)
    unknown = re.sub(r"\b(?:and|or|with)\b|[,/&+]", " ", unknown, flags=re.IGNORECASE)
    unknown = _clean_spaces(unknown.strip(" .?!:;"))
    return unknown or None


def guest_attendees_from_text(value: str) -> list[dict[str, str]]:
    return _guest_attendees_from_keys(_guest_keys_from_text(value))


def guest_contact_state_from_aliases(aliases: list[str]) -> tuple[list[dict[str, str]], list[str]]:
    keys = []
    seen = set()
    for alias in aliases:
        for key in GUEST_NAME_ALIASES.get(_alias_key(alias), []):
            if key in seen:
                continue
            seen.add(key)
            keys.append(key)
    return _guest_attendees_from_keys(keys), _missing_guest_contacts(keys)


def _guest_attendees_from_keys(keys: list[str]) -> list[dict[str, str]]:
    attendees = []
    for key in keys:
        email = os.environ.get(HOUSEHOLD_GUEST_EMAIL_ENV[key], "").strip()
        if not email:
            continue
        attendees.append(
            {
                "email": email,
                "displayName": HOUSEHOLD_GUEST_DISPLAY_NAMES[key],
            }
        )
    return attendees


def _missing_guest_contacts(keys: list[str]) -> list[str]:
    missing = []
    seen = set()
    for key in keys:
        if os.environ.get(HOUSEHOLD_GUEST_EMAIL_ENV[key], "").strip():
            continue
        if key in seen:
            continue
        seen.add(key)
        missing.append(key)
    return missing


def _extract_guest_instruction(
    user_text: str,
) -> tuple[str, list[dict[str, str]], list[str]]:
    attendees: list[dict[str, str]] = []
    missing_guest_contacts: list[str] = []
    cleaned_lines = []

    for line in user_text.splitlines():
        match = re.match(
            rf"^\s*(?:or\s+)?(?:add\s+)?guests?\s*:\s*(?P<guests>.+?)\s*$",
            line,
            flags=re.IGNORECASE,
        )
        if match is None:
            cleaned_lines.append(line)
            continue
        guest_text = _clean_spaces(match.group("guests"))
        keys = _guest_keys_from_text(guest_text)
        unknown_guest = _unknown_guest_text_from_instruction(guest_text)
        attendees.extend(_guest_attendees_from_keys(keys))
        missing_guest_contacts.extend(_missing_guest_contacts(keys))
        if unknown_guest:
            missing_guest_contacts.append(unknown_guest)

    cleaned = "\n".join(cleaned_lines)
    patterns = [
        rf"\b(?:or\s+)?add\s+(?P<guests>(?:{GUEST_ALIAS_PATTERN})(?:\s*(?:,|and|&|\+|/|or)\s*(?:{GUEST_ALIAS_PATTERN}))*)\s+to\s+(?:the\s+)?(?:invite|invitation|event)\b",
        rf"\b(?:invite|add\s+guests?)\s+(?P<guests>(?:{GUEST_ALIAS_PATTERN})(?:\s*(?:,|and|&|\+|/|or)\s*(?:{GUEST_ALIAS_PATTERN}))*)"
        rf"(?:\s+to\s+(?:the\s+)?(?:invite|invitation|event)\b|(?=$|[.?!]))",
    ]
    for pattern in patterns:
        while True:
            match = re.search(pattern, cleaned, flags=re.IGNORECASE)
            if match is None:
                break
            keys = _guest_keys_from_text(match.group("guests"))
            attendees.extend(_guest_attendees_from_keys(keys))
            missing_guest_contacts.extend(_missing_guest_contacts(keys))
            cleaned = f"{cleaned[: match.start()]} {cleaned[match.end() :]}"

    deduped = []
    seen_emails = set()
    for attendee in attendees:
        email = attendee["email"].lower()
        if email in seen_emails:
            continue
        seen_emails.add(email)
        deduped.append(attendee)

    missing_deduped = []
    seen_missing = set()
    for key in missing_guest_contacts:
        if key in seen_missing:
            continue
        seen_missing.add(key)
        missing_deduped.append(key)

    return _clean_spaces(cleaned), deduped, missing_deduped


def _extract_flight_description(user_text: str, location: str | None) -> str | None:
    match = re.search(
        r"\bflight\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?",
        user_text,
        flags=re.IGNORECASE,
    )
    if match is None or location is None:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    meridiem = match.group(3) or ""
    formatted = _format_time(hour, minute, meridiem, user_text)
    if formatted is None:
        return None

    display = datetime.strptime(formatted, "%H:%M").strftime("%-I:%M %p")
    return f"Flight from {location} at {display}"


def _extract_person(user_text: str) -> str:
    pickup = _extract_pickup_parts(user_text)
    if pickup is not None:
        return pickup["child"]

    match = re.search(
        rf"\b(?P<person>{PERSON_ALIAS_PATTERN})\b",
        user_text,
        flags=re.IGNORECASE,
    )
    if match is not None:
        return _canonical_person(match.group("person"))

    if "family" in user_text.lower():
        return "family"

    return "family"


def _extract_owner(user_text: str) -> str:
    pickup = _extract_pickup_parts(user_text)
    if pickup is not None:
        owner = _canonical_owner(pickup["adult"])
        if owner != "unknown":
            return owner

    lowered = user_text.lower()
    owner_annotation = re.search(
        rf"\b(?:owner|owned\s+by|the\s+owner\s+is)\s*(?:is|:)?\s*"
        rf"(?P<owner>{OWNER_ALIAS_PATTERN})\b",
        user_text,
        flags=re.IGNORECASE,
    )
    if owner_annotation is not None:
        return _canonical_owner(owner_annotation.group("owner"))

    owner_action = re.search(
        rf"\b(?P<owner>{OWNER_ALIAS_PATTERN})\b.+\b(?:will\s+)?(?:{OWNER_ACTION_WORD_PATTERN})\b",
        user_text,
        flags=re.IGNORECASE,
    )
    if owner_action is not None:
        return _canonical_owner(owner_action.group("owner"))
    if re.search(r"\b(?:i|me)\b.+\b(?:will\s+)?(?:take|handle|bring|drive|do|go)\b", lowered):
        return "dad"
    if re.search(r"\b(?:we|both|parents)\b.+\b(?:will\s+)?(?:take|handle|bring|drive|do|go)\b", lowered):
        return "both"

    return "unknown"


def _infer_category(user_text: str) -> str:
    lowered = user_text.lower()
    for category, hints in CATEGORY_HINTS.items():
        if any(re.search(rf"\b{re.escape(hint)}s?\b", lowered) for hint in hints):
            return category

    return ""


def _extract_preparation_notes(user_text: str) -> str:
    match = re.search(
        r"\b(?:need|needs|bring|prepare)\s+(.+?)(?:\.|$)",
        user_text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return ""

    notes = _clean_spaces(match.group(0).strip(" ."))
    if re.search(r"\bneed to leave\b", notes, flags=re.IGNORECASE):
        return ""
    if re.fullmatch(r"needs?\s+to\s+be\s+done", notes, flags=re.IGNORECASE):
        return ""

    return notes


def _strip_event_display_annotations(value: str) -> str:
    title = re.sub(
        r"\b(?:the\s+)?owner\s*(?:is|:)?\b.*$|\bowned\s+by\b.*$",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    title = re.sub(
        r"[, ]+\b(?:need|needs|bring|prepare)\b.+$",
        " ",
        title,
        flags=re.IGNORECASE,
    )
    return _clean_spaces(title.strip(" ,."))


def _extract_metadata(user_text: str) -> dict[str, Any]:
    preparation_notes = _extract_preparation_notes(user_text)
    return _normalize_metadata(
        {
            "owner": _extract_owner(user_text),
            "person": _extract_person(user_text),
            "category": _infer_category(user_text),
            "preparation_needed": bool(preparation_notes),
            "preparation_notes": preparation_notes,
        }
    )


def _title_from_text(user_text: str, location: str | None, purpose: str | None) -> str | None:
    pickup = _extract_pickup_parts(user_text)
    if pickup is not None:
        return pickup["title"]

    lowered = user_text.lower()
    if location and "flight" in lowered and any(
        phrase in lowered
        for phrase in ("need to leave at", "leave at", "depart at", "head out at")
    ):
        return f"Leave for {location} flight"

    if location and purpose:
        suffix = ""
        trip_match = re.search(r"\bfor\s+(.+?trip)\b", purpose, flags=re.IGNORECASE)
        if trip_match is not None:
            suffix = f" for {_clean_spaces(trip_match.group(1))}"

        if re.search(r"\b(get|buy|shop|shopping|tshirts?|shirts?)\b", purpose, flags=re.IGNORECASE):
            return f"{location} shopping{suffix}"

        return f"{location}: {purpose}"

    title = _strip_create_words(user_text)
    title = re.sub(
        r"^\s*(?:i\s+had|there\s+is|this\s+is)\s+(?:a\s+)?"
        r"(?:calendar\s+)?(?:event|invite|invitation|reminder)\s*(?:for|to)?\s*",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(
        r"\bevery\s+(?:day|weekday|weekdays|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        " ",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"\bevery\s+week\b|\bweekly\s+on\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\bfor\s+(?:next\s+)?\d+\s+weeks?\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\bnext\s+\d+\s+(?:mondays|tuesdays|wednesdays|thursdays|fridays|saturdays|sundays)\b", " ", title, flags=re.IGNORECASE)
    title = _strip_calendar_target_phrase(title)
    title = re.sub(r"\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", " ", title, flags=re.IGNORECASE)
    title = _strip_absolute_date_text(title)
    title = re.sub(
        r"(?:\btime\s*:\s*)?\b\d{1,2}(?::\d{2})?\s*"
        r"(?:am|pm|a\.m\.|p\.m\.)?\s*(?:-|–|to)\s*"
        r"\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?\b",
        " ",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"\b(?:at|around)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:at|around)\s+noon\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\bnoon\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\bfor\s+\d+\s*(?:minute|minutes|min|hour|hours|hr|hrs)\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\bnext\s+(?=monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\bnext\b", " ", title, flags=re.IGNORECASE)
    title = _strip_calendar_target_phrase(title)
    title = re.sub(r"^\s*for\s+", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\btime\s*:\s*.*$", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\blocation\s*:\s*.*$", " ", title, flags=re.IGNORECASE)
    title = re.sub(
        rf"[, ]+\b(?:i|we|both|parents|{OWNER_ALIAS_PATTERN})\b.+?\b"
        rf"(?:will\s+)?(?:{OWNER_ACTION_WORD_PATTERN})\b.*$",
        " ",
        title,
        flags=re.IGNORECASE,
    )
    invite_title_match = re.search(
        rf"^\s*invite\s+(?:{GUEST_ALIAS_PATTERN})(?:\s*(?:,|and|&|\+|/|or)\s*(?:{GUEST_ALIAS_PATTERN}))*\s+to\s+(?P<title>.+)$",
        title,
        flags=re.IGNORECASE,
    )
    if invite_title_match is not None:
        title = invite_title_match.group("title")
    action_match = re.search(
        rf"\bto\s+(?P<action>(?:{EVENT_TITLE_ACTION_WORD_PATTERN})\b.+)$",
        title,
        flags=re.IGNORECASE,
    )
    if action_match is not None:
        title = action_match.group("action")
    title = _strip_event_display_annotations(title)

    with_match = re.search(r"\bwith\s+(.+)$", title, flags=re.IGNORECASE)
    if with_match is not None:
        title = title[: with_match.start()]

    title = _clean_spaces(title.strip(" ."))
    if not title:
        return None

    title = re.sub(r"\bgymanstics\b", "gymnastics", title, flags=re.IGNORECASE)
    return title[:1].upper() + title[1:]


def _is_list_request(user_text: str) -> bool:
    lowered = user_text.lower().strip()
    if _has_primary_create_command(lowered):
        return False
    return lowered.startswith(
        ("what ", "what's ", "whats ", "when ", "show ", "list "),
    ) or bool(
        re.search(
            r"\b(?:upcoming|coming\s+up|holidays?|break|vacation|no\s+school)\b",
            lowered,
        )
    )


def _is_briefing_request(user_text: str) -> bool:
    lowered = user_text.lower().strip()
    if "briefing" in lowered:
        return True

    has_week = "this week" in lowered or "next week" in lowered
    if not has_week:
        return False

    briefing_words = (
        "plan",
        "coming up",
        "upcoming",
        "summary",
        "overview",
        "brief",
        "schedule",
        "calendar",
        "look ahead",
    )
    return any(word in lowered for word in briefing_words)


def _is_preparation_request(user_text: str) -> bool:
    lowered = user_text.lower().strip()
    if re.search(r"\b(?:prepare|preparation|prep)\b", lowered):
        return True

    return bool(re.search(r"\b(?:needs?\s+action|need\s+to\s+do)\b", lowered))


def _is_delete_request(user_text: str) -> bool:
    lowered = user_text.lower().strip()
    return lowered.startswith(("cancel ", "delete ", "remove "))


def _is_update_request(user_text: str) -> bool:
    lowered = user_text.lower().strip()
    return lowered.startswith(("move ", "reschedule ", "change ", "push ", "delay "))


def _guest_only_intent(user_text: str) -> dict[str, Any] | None:
    cleaned, attendees, missing_guest_contacts = _extract_guest_instruction(user_text)
    cleaned = _strip_guest_request_wrapper(cleaned)
    if missing_guest_contacts and not cleaned:
        return {
            "intent": "add_guests",
            "attendees": attendees,
            "missing_guest_contacts": missing_guest_contacts,
            "missing_fields": ["guest_contacts"],
        }
    if not attendees or cleaned:
        return None
    return {
        "intent": "add_guests",
        "attendees": attendees,
        "missing_fields": [],
    }


def _strip_guest_request_wrapper(value: str) -> str:
    cleaned = _clean_spaces(value.strip(" ?.!,"))
    cleaned = re.sub(
        r"^(?:please|pls|can\s+you|could\s+you|would\s+you)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    return _clean_spaces(cleaned.strip(" ?.!,"))


def _list_intent(user_text: str, reference: datetime) -> dict[str, Any]:
    start, end = _extract_list_range(user_text, reference)
    metadata_filter = _extract_list_metadata_filter(user_text)
    if start is None or end is None:
        start, end = _default_list_range_for_filter(reference, metadata_filter)
    missing_fields = []
    if start is None or end is None:
        missing_fields.append("date")

    return {
        "intent": "list_events",
        "start": start.isoformat() if start is not None else None,
        "end": end.isoformat() if end is not None else None,
        "metadata_filter": metadata_filter,
        "missing_fields": missing_fields,
    }


def _briefing_intent(user_text: str, reference: datetime) -> dict[str, Any]:
    start, end, label = _extract_week_briefing_range(user_text, reference)
    return {
        "intent": "family_briefing",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "label": label,
        "missing_fields": [],
    }


def _extract_preparation_query(user_text: str) -> str | None:
    query = user_text
    query = re.sub(
        r"^\s*what\s+(?:should\s+we\s+)?(?:do\s+we\s+need\s+to\s+do\s+)?",
        "",
        query,
        flags=re.IGNORECASE,
    )
    query = re.sub(
        r"^\s*(?:can\s+you\s+)?prepare\s+(?:me|us)\s+for\s+",
        "",
        query,
        flags=re.IGNORECASE,
    )
    query = re.sub(
        r"^\s*(?:should\s+we\s+)?prepare\s+for\s+",
        "",
        query,
        flags=re.IGNORECASE,
    )
    query = re.sub(
        r"^\s*(?:needs?\s+action|needs?\s+preparation|needs?\s+prep|should\s+we\s+prepare)\b",
        "",
        query,
        flags=re.IGNORECASE,
    )
    query = re.sub(r"^\s*(?:for|before)\s+", "", query, flags=re.IGNORECASE)
    query = re.sub(
        r"\b(?:this\s+week|next\s+week|today|tomorrow|next\s+7\s+days|next\s+seven\s+days)\b",
        " ",
        query,
        flags=re.IGNORECASE,
    )
    query = re.sub(r"^\s*(?:the|our|my)\s+", "", query, flags=re.IGNORECASE)
    query = re.sub(r"\?$", "", query.strip())
    query = _clean_spaces(query.strip(" ."))
    if query.lower() in (
        "for",
        "before",
        "action",
        "preparation",
        "prep",
        "prepare for",
    ):
        return None

    return query or None


def _preparation_intent(user_text: str, reference: datetime) -> dict[str, Any]:
    start, end = _extract_list_range(user_text, reference)
    label = "upcoming"
    lowered = user_text.lower()
    if "this week" in lowered:
        start, end, label = _extract_week_briefing_range(user_text, reference)
    elif "next week" in lowered:
        start, end, label = _extract_week_briefing_range(user_text, reference)
    elif start is None or end is None:
        start = reference
        end = reference + timedelta(days=30)

    return {
        "intent": "preparation_checklist",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "label": label,
        "query": _extract_preparation_query(user_text),
        "missing_fields": [],
    }


def _extract_delete_query(user_text: str) -> str | None:
    query = re.sub(
        r"^\s*(cancel|delete|remove)\s+",
        "",
        user_text,
        flags=re.IGNORECASE,
    )
    query = re.sub(r"^\s*(my|the|an?|this)\s+", "", query, flags=re.IGNORECASE)
    query = re.sub(r"\btomorrow'?s\b", " ", query, flags=re.IGNORECASE)
    query = re.sub(r"\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", " ", query, flags=re.IGNORECASE)
    query = re.sub(r"\bnext\s+week\b|\bnext\s+7\s+days\b", " ", query, flags=re.IGNORECASE)
    query = re.sub(r"\b(event|reminder)\b", " ", query, flags=re.IGNORECASE)
    query = _clean_spaces(query.strip(" ."))
    return query or None


def _extract_update_parts(user_text: str) -> tuple[str, str]:
    push_delay_match = re.search(
        r"^\s*(push|delay)\b.+?\bby\s+(?:\d+|one|an?|half(?:\s+an?))\s+(?:minute|minutes|min|hour|hours|hr|hrs)\b",
        user_text,
        flags=re.IGNORECASE,
    )
    if push_delay_match is not None:
        by_match = re.search(r"\bby\b", user_text[push_delay_match.start() :], flags=re.IGNORECASE)
        if by_match is not None:
            by_index = push_delay_match.start() + by_match.start()
            return user_text[:by_index], user_text[by_index:]

    shift = _relative_shift_match(user_text)
    if shift is not None:
        _, span = shift
        return user_text[: span[0]], user_text[span[0] : span[1]]

    match = re.search(r"\bto\b", user_text, flags=re.IGNORECASE)
    if match is None:
        return user_text, ""

    return user_text[: match.start()], user_text[match.end() :]


def _extract_update_query(user_text: str) -> str | None:
    query, _ = _extract_update_parts(user_text)
    query = re.sub(
        r"^\s*(move|reschedule|change|push|delay)\s+",
        "",
        query,
        flags=re.IGNORECASE,
    )
    query = re.sub(r"^\s*(back|forward|up|later|earlier|sooner)\s+", "", query, flags=re.IGNORECASE)
    query = re.sub(r"^\s*(my|the|an?|this)\s+", "", query, flags=re.IGNORECASE)
    query = re.sub(r"\b(event|appointment|reminder)\b", " ", query, flags=re.IGNORECASE)
    query = _clean_spaces(query.strip(" ."))
    return query or None


def _update_intent(user_text: str, reference: datetime) -> dict[str, Any]:
    _, target = _extract_update_parts(user_text)
    target_text = target or user_text
    relative_delta_minutes = _extract_relative_delta_minutes(user_text)
    date, _ = _extract_date(target_text, reference)
    start_time = None
    if relative_delta_minutes is None:
        start_time = _extract_update_time(target_text, user_text)
    query = _extract_update_query(user_text)
    missing_fields = []
    if query is None:
        missing_fields.append("event")
    if start_time is None and relative_delta_minutes is None:
        missing_fields.append("time")

    return {
        "intent": "update_event",
        "query": query,
        "new_date": date,
        "new_start_time": start_time,
        "relative_delta_minutes": relative_delta_minutes,
        "duration_minutes": _extract_duration_minutes(target_text),
        "duration_specified": re.search(
            r"\bfor\s+\d+\s*(minute|minutes|min|hour|hours|hr|hrs)\b",
            target_text,
            flags=re.IGNORECASE,
        )
        is not None,
        "search_start": reference.isoformat(),
        "search_end": (reference + timedelta(days=30)).isoformat(),
        "timezone": DEFAULT_TIMEZONE,
        "missing_fields": missing_fields,
    }


def _delete_intent(user_text: str, reference: datetime) -> dict[str, Any]:
    search_start, search_end = _extract_delete_range(user_text, reference)
    query = _extract_delete_query(user_text)
    missing_fields = []
    if query is None:
        missing_fields.append("event")

    return {
        "intent": "delete_event",
        "query": query,
        "search_start": search_start.isoformat(),
        "search_end": search_end.isoformat(),
        "missing_fields": missing_fields,
    }


def _create_intent(user_text: str, reference: datetime) -> dict[str, Any]:
    event_text, assistant_metadata, assistant_description = _extract_assistant_help(
        user_text,
    )
    intent_text, attendees, missing_guest_contacts = _extract_guest_instruction(event_text or user_text)
    intent_text = intent_text or event_text or user_text
    recurrence = _extract_recurrence(intent_text, reference)
    date, _ = _extract_date(intent_text, reference)
    if recurrence is not None:
        date = recurrence["start_date"]
    start_time = _extract_time(intent_text) or _extract_standalone_time(
        intent_text,
        intent_text,
    )
    all_day = _is_all_day_request(intent_text)
    if all_day:
        start_time = None
    location = _extract_location(intent_text)
    purpose = _extract_purpose(intent_text)
    with_description = _extract_with_description(intent_text)
    flight_description = _extract_flight_description(intent_text, location)
    pickup = _extract_pickup_parts(intent_text)
    if pickup is not None and pickup["source"]:
        location = location or pickup["source"]
    metadata = _extract_metadata(intent_text)
    metadata.update(assistant_metadata)
    metadata = _normalize_metadata(metadata)
    target_calendar = _extract_target_calendar(intent_text)
    preparation_notes = metadata["preparation_notes"]
    pickup_notes = pickup["notes"] if pickup is not None else None
    description = (
        flight_description
        or purpose
        or with_description
        or pickup_notes
        or preparation_notes
        or None
    )
    description = _append_assistant_description(description, assistant_description)
    title = _title_from_text(intent_text, location, purpose)

    missing_fields = []
    if title is None:
        missing_fields.append("title")
    if date is None:
        missing_fields.append("date")
    if not all_day and start_time is None:
        missing_fields.append("time")
    if missing_guest_contacts:
        missing_fields.append("guest_contacts")

    return {
        "intent": "create_event",
        "title": title,
        "date": date,
        "start_time": start_time,
        "all_day": all_day,
        "duration_minutes": _extract_duration_minutes(user_text),
        "timezone": DEFAULT_TIMEZONE,
        "location": location,
        "description": description,
        "metadata": metadata,
        "attendees": attendees,
        "missing_guest_contacts": missing_guest_contacts,
        "target_calendar": target_calendar,
        "recurrence": [recurrence["rrule"]] if recurrence is not None else None,
        "recurrence_label": recurrence["label"] if recurrence is not None else None,
        "missing_fields": missing_fields,
    }


def _iso_date_from_slot(value: str, reference: datetime) -> str | None:
    cleaned = _clean_spaces(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        try:
            datetime.fromisoformat(f"{cleaned}T00:00:00")
        except ValueError:
            return None
        return cleaned
    date, _ = _extract_date(cleaned, reference)
    return date


def _time_from_slot(value: str) -> str | None:
    cleaned = _clean_spaces(value)
    if re.fullmatch(r"\d{1,2}:\d{2}", cleaned):
        hour, minute = (int(part) for part in cleaned.split(":", 1))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
        return None
    return _extract_time(cleaned) or _extract_standalone_time(cleaned, cleaned)


def _is_all_day_request(value: str) -> bool:
    return re.search(
        r"\b(?:all|full)\s+day\b|\bwhole\s+day\b",
        value,
        flags=re.IGNORECASE,
    ) is not None


def _refresh_create_missing_fields(intent: dict[str, Any]) -> None:
    missing_fields = []
    if not intent.get("title"):
        missing_fields.append("title")
    if not intent.get("date"):
        missing_fields.append("date")
    if not intent.get("all_day") and not intent.get("start_time"):
        missing_fields.append("time")
    if intent.get("missing_guest_contacts"):
        missing_fields.append("guest_contacts")
    intent["missing_fields"] = missing_fields


def _refresh_add_guests_missing_fields(intent: dict[str, Any]) -> None:
    missing_fields = []
    if intent.get("missing_guest_contacts"):
        missing_fields.append("guest_contacts")
    elif not intent.get("attendees"):
        missing_fields.append("guests")
    intent["missing_fields"] = missing_fields


def _can_ai_refine_calendar_action(
    current_action: str,
    ai_action: str,
    request: str,
    intent: dict[str, Any],
) -> bool:
    if ai_action == current_action:
        return True
    if ai_action != "add_guests" or current_action != "create_event":
        return False

    missing_fields = set(intent.get("missing_fields") or [])
    if not {"date", "time"}.issubset(missing_fields):
        return False

    return re.search(
        r"\b(?:add\s+)?guests?\s+to\s+(?:the\s+)?(?:invite|invitation|event)\b",
        request,
        flags=re.IGNORECASE,
    ) is not None


def _calendar_command_body(request: str) -> str:
    return re.sub(r"^\s*/(?:calendar|event|schedule)\b", " ", request, flags=re.IGNORECASE).strip()


def _primary_calendar_action_from_text(value: str, fallback: str) -> str:
    lowered = value.lower()
    if _primary_guest_update_from_text(value) is not None:
        return "add_guests"
    if _has_primary_create_command(lowered) and fallback in {"list_events", "family_briefing"}:
        return "create_event"
    if re.search(r"\b(?:briefing|brief|summary)\b", lowered):
        return "family_briefing"
    if re.search(r"\bplans?\s+for\b", lowered) and not re.search(
        r"\b(?:add|create|schedule|put|block)\b",
        lowered,
    ):
        return "family_briefing"
    if re.search(r"\b(?:what'?s|what\s+is|show|list|any)\b", lowered) and re.search(
        r"\b(?:calendar|events?|stuff|coming\s+up)\b",
        lowered,
    ) and not _has_primary_create_command(lowered):
        return "list_events"
    return fallback


def _has_primary_create_command(value: str) -> bool:
    return re.search(
        r"^\s*(?:please\s+|pls\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+)?"
        r"(?:creating|create|add|schedule|put|block)\b",
        value,
        flags=re.IGNORECASE,
    ) is not None


def _primary_date_from_text(
    value: str,
    reference: datetime,
    *,
    prefer_current_weekday: bool,
) -> str | None:
    lowered = value.lower()
    if re.search(r"\b(?:today|tonight)\b", lowered):
        return reference.date().isoformat()
    if re.search(r"\btomorrow(?:'?s)?\b", lowered):
        return (reference + timedelta(days=1)).date().isoformat()

    date, _ = _extract_date(value, reference)
    if _has_explicit_next_weekday(value):
        for name, weekday in _weekday_names_for_primary_parse().items():
            if re.search(rf"\b(?:next\s+{name}|{name}\s+next)\b", lowered):
                return _weekday_in_next_calendar_week(reference, weekday).date().isoformat()
    if date is not None:
        return date

    if prefer_current_weekday:
        for name, weekday in _weekday_names_for_primary_parse().items():
            if re.search(rf"\b{name}\b", lowered):
                return _current_or_next_weekday(reference, weekday).date().isoformat()
    return None


def _weekday_names_for_primary_parse() -> dict[str, int]:
    return {
        **WEEKDAYS,
        "mon": WEEKDAYS["monday"],
        "tue": WEEKDAYS["tuesday"],
        "tues": WEEKDAYS["tuesday"],
        "wed": WEEKDAYS["wednesday"],
        "thu": WEEKDAYS["thursday"],
        "thur": WEEKDAYS["thursday"],
        "thurs": WEEKDAYS["thursday"],
        "fri": WEEKDAYS["friday"],
        "sat": WEEKDAYS["saturday"],
        "sun": WEEKDAYS["sunday"],
    }


def _monthly_ordinal_recurrence_from_text(
    value: str,
) -> tuple[str, int, str, int, str] | None:
    ordinal_pattern = "|".join(
        re.escape(name)
        for name in sorted(ORDINAL_WEEKDAY_POSITIONS, key=len, reverse=True)
    )
    weekday_names = _weekday_names_for_primary_parse()
    weekday_pattern = "|".join(
        re.escape(name)
        for name in sorted(weekday_names, key=len, reverse=True)
    )
    match = re.search(
        rf"\bevery\s+(?P<ordinal>{ordinal_pattern})\s+(?P<weekday>{weekday_pattern})\b",
        value,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None

    weekday = weekday_names[match.group("weekday").lower()]
    weekday_name = next(name for name, day in WEEKDAYS.items() if day == weekday)
    weekday_code = WEEKDAY_RRULE_CODES[weekday_name]
    ordinal_text = match.group("ordinal").lower()
    return (
        weekday_name,
        weekday,
        weekday_code,
        ORDINAL_WEEKDAY_POSITIONS[ordinal_text],
        ordinal_text,
    )


def _current_or_next_ordinal_weekday(
    reference: datetime,
    weekday: int,
    position: int,
) -> datetime:
    candidate = _ordinal_weekday_in_month(
        reference.year,
        reference.month,
        weekday,
        position,
        reference.tzinfo,
    )
    if candidate.date() >= reference.date():
        return candidate
    year = reference.year + (1 if reference.month == 12 else 0)
    month = 1 if reference.month == 12 else reference.month + 1
    return _ordinal_weekday_in_month(year, month, weekday, position, reference.tzinfo)


def _ordinal_weekday_in_month(
    year: int,
    month: int,
    weekday: int,
    position: int,
    tzinfo: Any,
) -> datetime:
    first = datetime(year, month, 1, tzinfo=tzinfo)
    next_month = datetime(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1, tzinfo=tzinfo)
    days = []
    current = first
    while current < next_month:
        if current.weekday() == weekday:
            days.append(current)
        current += timedelta(days=1)
    return days[position - 1 if position > 0 else position]


def _has_explicit_next_weekday(value: str) -> bool:
    names = "|".join(re.escape(name) for name in _weekday_names_for_primary_parse())
    return re.search(
        rf"\b(?:next\s+(?:{names})|(?:{names})\s+next)\b",
        value,
        flags=re.IGNORECASE,
    ) is not None


def _recurrence_from_primary_text(value: str) -> list[str]:
    lowered = value.lower()
    if "every" not in lowered:
        return []
    ordinal_recurrence = _monthly_ordinal_recurrence_from_text(lowered)
    if ordinal_recurrence is not None:
        _, _, weekday_code, position, _ = ordinal_recurrence
        return [f"RRULE:FREQ=MONTHLY;BYDAY={weekday_code};BYSETPOS={position}"]
    matched_codes: list[tuple[int, str]] = []
    seen_codes = set()
    for name, weekday in sorted(
        _weekday_names_for_primary_parse().items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        code = WEEKDAY_RRULE_CODES[
            next(weekday_name for weekday_name, value in WEEKDAYS.items() if value == weekday)
        ]
        if re.search(rf"\b{name}\b", lowered) and code not in seen_codes:
            seen_codes.add(code)
            matched_codes.append((weekday, code))
    if not matched_codes:
        return []
    codes = [code for _, code in sorted(matched_codes)]
    return [f"RRULE:FREQ=WEEKLY;BYDAY={','.join(codes)}"]


def _recurrence_day_count(value: Any) -> int:
    if not isinstance(value, list):
        return 0
    days = set()
    for item in value:
        match = re.search(r"BYDAY=([A-Z,]+)", str(item).upper())
        if match is not None:
            days.update(day for day in match.group(1).split(",") if day)
    return len(days)


def _has_monthly_ordinal_recurrence(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    return any(
        "FREQ=MONTHLY" in str(item).upper() and "BYSETPOS=" in str(item).upper()
        for item in value
    )


def _description_from_primary_text(value: str) -> str | None:
    match = re.search(
        r"\b(?:notes?|description)\s*:?\s*(?P<description>.+?)\s*$",
        value,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return _clean_spaces(match.group("description").strip(" ,.;:")) or None


def _strip_description_label(value: str) -> str:
    return _clean_spaces(
        re.sub(r"^\s*(?:notes?|description)\s*:?\s*", "", value, flags=re.IGNORECASE).strip(" ,.;:")
    )


def _primary_guest_update_from_text(value: str) -> dict[str, Any] | None:
    match = re.search(
        rf"\b(?:add|invite)\s+(?:guests?\s+)?"
        rf"(?P<guests>(?:{GUEST_ALIAS_PATTERN})(?:\s*(?:,|and|&|\+|/|or)\s*(?:{GUEST_ALIAS_PATTERN}))*)"
        r"\s+(?:as\s+guests?\s+)?to\s+(?:the\s+|that\s+)?"
        r"(?P<target>.*?\b(?:invite|invitation|event)\b)",
        value,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None

    aliases = [
        alias
        for alias in re.split(r"\s*(?:,|and|&|\+|/|or)\s*", match.group("guests"), flags=re.IGNORECASE)
        if alias.strip()
    ]
    if not aliases:
        return None

    result: dict[str, Any] = {"guest_aliases": aliases}
    target_reference = _primary_guest_target_reference_from_text(value[match.end() :])
    if target_reference:
        result["target_reference"] = target_reference
    return result


def _primary_guest_target_reference_from_text(value: str) -> str | None:
    match = re.search(r"\bfor\s+(?P<target>.+?)\s*$", value, flags=re.IGNORECASE)
    if match is None:
        return None
    return _clean_spaces(match.group("target").strip(" ,.;:")) or None


def _primary_title_from_text(value: str) -> str | None:
    title = _strip_create_words(value)
    title = re.split(
        rf"\b(?:today|tomorrow|tonight|next\s+\w+|this\s+\w+|every\s+(?:\w+\s+)?\w+|"
        rf"on\s+(?:{MONTH_NAME_PATTERN})|\d{{1,2}}/\d{{1,2}}|"
        rf"(?:{MONTH_NAME_PATTERN})\.?\s+\d{{1,2}}|"
        r"at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?|all\s+day|starting\s+|through\s+|"
        r"to\s+.+?\s+calendar|on\s+.+?\s+calendar|notes?:|description)\b",
        title,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return _clean_spaces(title.strip(" ,.;:-")) or None


def _should_replace_primary_title(current: str, candidate: str) -> bool:
    current_key = _clean_spaces(current).lower()
    candidate_key = _clean_spaces(candidate).lower()
    if not current_key or not candidate_key or current_key == candidate_key:
        return False
    if not current_key.startswith(f"{candidate_key} "):
        return False
    return re.search(
        r"\b(?:all\s+day|every|starting|notes?|description)\b",
        current_key,
    ) is not None


def _repair_primary_ai_calendar_intent(
    intent: dict[str, Any],
    request: str,
    reference: datetime,
) -> None:
    body = _calendar_command_body(request)
    action = _primary_calendar_action_from_text(body, str(intent.get("intent") or "create_event"))
    intent["intent"] = action

    target_calendar = _extract_target_calendar(body)
    if target_calendar:
        intent["target_calendar"] = target_calendar

    if action == "family_briefing":
        intent["missing_fields"] = []
        return

    if action == "list_events":
        list_date = _primary_date_from_text(body, reference, prefer_current_weekday=True)
        if list_date:
            intent["date"] = list_date
        intent["missing_fields"] = []
        return

    if action == "add_guests":
        guest_update = _primary_guest_update_from_text(body)
        if guest_update is not None:
            attendees, missing_contacts = guest_contact_state_from_aliases(
                [str(alias) for alias in guest_update["guest_aliases"]],
            )
            intent["guest_aliases"] = guest_update["guest_aliases"]
            intent["attendees"] = attendees
            intent["missing_guest_contacts"] = missing_contacts
            if guest_update.get("target_reference"):
                intent["query"] = guest_update["target_reference"]
        intent["missing_fields"] = []
        return

    event_date = _primary_date_from_text(body, reference, prefer_current_weekday=False)
    if event_date and (not intent.get("date") or _has_explicit_next_weekday(body)):
        intent["date"] = event_date

    if re.search(r"\b(?:all|full)\s+day\b|\bwhole\s+day\b", body, flags=re.IGNORECASE):
        intent["all_day"] = True
        intent.pop("start_time", None)

    if not intent.get("location"):
        location = _extract_location(body)
        if location:
            intent["location"] = location

    recurrence = _recurrence_from_primary_text(body)
    if recurrence and (
        _has_monthly_ordinal_recurrence(recurrence)
        or _recurrence_day_count(recurrence) > _recurrence_day_count(intent.get("recurrence"))
    ):
        intent["recurrence"] = recurrence

    current_description = _clean_spaces(str(intent.get("description") or ""))
    if current_description:
        intent["description"] = _strip_description_label(current_description)
    else:
        description = _description_from_primary_text(body)
        if description:
            intent["description"] = description

    title = _primary_title_from_text(body)
    current_title = _clean_spaces(str(intent.get("title") or ""))
    if title and (
        not current_title
        or "event" in {str(field) for field in intent.get("missing_fields") or []}
        or _should_replace_primary_title(current_title, title)
    ):
        intent["title"] = title[:1].upper() + title[1:]
        intent["missing_fields"] = [
            field
            for field in intent.get("missing_fields") or []
            if str(field) not in {"event", "title"}
        ]


def merge_ai_calendar_fields(
    intent: dict[str, Any],
    ai_fields: dict[str, Any],
    request: str,
    now: datetime | None = None,
    *,
    primary: bool = False,
) -> dict[str, Any]:
    reference = _default_now(now)
    refined = dict(intent)
    slots = ai_fields.get("slots") if isinstance(ai_fields.get("slots"), dict) else {}
    current_action = str(refined.get("intent") or "create_event")
    action = str(ai_fields.get("action") or current_action)
    if action in {
        "create_event",
        "update_event",
        "delete_event",
        "list_events",
        "family_briefing",
        "preparation_checklist",
        "add_guests",
    } and (primary or _can_ai_refine_calendar_action(current_action, action, request, intent)):
        refined["intent"] = action

    title = _clean_spaces(str(slots.get("title") or ""))
    if title and (primary or not refined.get("title")):
        refined["title"] = title[:1].upper() + title[1:]

    date_value = _clean_spaces(str(slots.get("date") or slots.get("date_text") or ""))
    if date_value and (primary or not refined.get("date")):
        parsed_date = _iso_date_from_slot(date_value, reference)
        if parsed_date:
            refined["date"] = parsed_date

    time_value = _clean_spaces(str(slots.get("start_time") or slots.get("time_text") or ""))
    if time_value and (primary or not refined.get("start_time")):
        parsed_time = _time_from_slot(time_value)
        if parsed_time:
            refined["start_time"] = parsed_time

    duration = slots.get("duration_minutes")
    if isinstance(duration, int) and 1 <= duration <= 1440 and (primary or not refined.get("duration_minutes")):
        refined["duration_minutes"] = duration
    if slots.get("all_day") is True:
        refined["all_day"] = True
    recurrence = slots.get("recurrence")
    if isinstance(recurrence, list) and recurrence and (
        primary or not refined.get("recurrence")
    ):
        refined["recurrence"] = [str(value) for value in recurrence if str(value).strip()]

    for slot_key, intent_key in (
        ("location", "location"),
        ("description", "description"),
        ("target_reference", "query"),
    ):
        value = _clean_spaces(str(slots.get(slot_key) or ""))
        if value and (primary or not refined.get(intent_key)):
            refined[intent_key] = value

    calendar_name = _clean_spaces(str(slots.get("calendar_name") or ""))
    if primary and calendar_name:
        refined["target_calendar"] = calendar_name

    guest_aliases = slots.get("guest_aliases")
    if isinstance(guest_aliases, list):
        resolved_alias_keys = {
            key
            for alias in guest_aliases
            for key in GUEST_NAME_ALIASES.get(_alias_key(str(alias)), [])
            if os.environ.get(HOUSEHOLD_GUEST_EMAIL_ENV[key], "").strip()
        }
        attendees, missing_contacts = guest_contact_state_from_aliases(
            [str(alias) for alias in guest_aliases],
        )
        if attendees or missing_contacts:
            existing_attendees = list(refined.get("attendees") or [])
            seen_attendee_emails = {
                str(attendee.get("email") or "").lower()
                for attendee in existing_attendees
                if isinstance(attendee, dict)
            }
            for attendee in attendees:
                email = attendee["email"].lower()
                if email in seen_attendee_emails:
                    continue
                seen_attendee_emails.add(email)
                existing_attendees.append(attendee)

            existing_missing = [
                str(contact)
                for contact in refined.get("missing_guest_contacts") or []
                if _alias_key(str(contact)) not in resolved_alias_keys
            ]
            seen_missing = {contact.lower() for contact in existing_missing}
            for contact in missing_contacts:
                if contact.lower() in seen_missing:
                    continue
                seen_missing.add(contact.lower())
                existing_missing.append(contact)

            refined["attendees"] = existing_attendees
            refined["missing_guest_contacts"] = existing_missing

    if primary:
        _repair_primary_ai_calendar_intent(refined, request, reference)

    refined["ai_field_extraction"] = {
        "confidence": ai_fields.get("confidence"),
        "missing_fields": list(ai_fields.get("missing_fields") or []),
        "normalized_request": request,
    }
    if refined.get("intent") == "add_guests":
        refined.setdefault("attendees", [])
        refined.setdefault("missing_guest_contacts", [])
        _refresh_add_guests_missing_fields(refined)
    elif refined.get("intent") == "create_event":
        if not refined.get("all_day"):
            refined.setdefault("duration_minutes", 60)
        refined.setdefault("timezone", DEFAULT_TIMEZONE)
        refined.setdefault("metadata", dict(DEFAULT_METADATA))
        refined.setdefault("attendees", [])
        refined.setdefault("missing_guest_contacts", [])
        _refresh_create_missing_fields(refined)
    return refined


def extract_intent(user_text: str, now: datetime | None = None) -> dict[str, Any]:
    reference = _default_now(now)
    intent_text, _, _ = _extract_assistant_help(user_text)
    routing_text = intent_text or user_text
    guest_only = _guest_only_intent(routing_text)
    if guest_only is not None:
        return guest_only
    if _is_update_request(routing_text):
        return _update_intent(routing_text, reference)
    if _is_delete_request(routing_text):
        return _delete_intent(routing_text, reference)
    if _is_preparation_request(routing_text):
        return _preparation_intent(routing_text, reference)
    if _is_briefing_request(routing_text):
        return _briefing_intent(routing_text, reference)
    if _is_list_request(routing_text):
        return _list_intent(routing_text, reference)

    return _create_intent(user_text, reference)

from __future__ import annotations

from datetime import datetime, time, timedelta
import json
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

WEEKDAY_RRULE_CODES = {
    "monday": "MO",
    "tuesday": "TU",
    "wednesday": "WE",
    "thursday": "TH",
    "friday": "FR",
    "saturday": "SA",
    "sunday": "SU",
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
    r"cancel|downgrade|renew|call|text|email|message|book|schedule|pay|"
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


def _extract_date(user_text: str, reference: datetime) -> tuple[str | None, str]:
    lowered = user_text.lower()
    if "tomorrow" in lowered or "tomorrows" in lowered:
        return (reference + timedelta(days=1)).date().isoformat(), "tomorrow"
    if "today" in lowered:
        return reference.date().isoformat(), "today"

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

    if "next 7 days" in lowered or "next seven days" in lowered:
        return reference, reference + timedelta(days=7)
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

    return filters


def _default_list_range_for_filter(
    reference: datetime,
    metadata_filter: dict[str, Any],
) -> tuple[datetime | None, datetime | None]:
    if not metadata_filter:
        return None, None

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


def _extract_time(user_text: str) -> str | None:
    action_time = _extract_action_time(user_text)
    if action_time is not None:
        return action_time

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


def _strip_create_words(user_text: str) -> str:
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
    title = re.sub(r"\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:at|around)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:at|around)\s+noon\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\bnoon\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\bfor\s+\d+\s*(?:minute|minutes|min|hour|hours|hr|hrs)\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\bnext\s+(?=monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\bnext\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(
        rf"[, ]+\b(?:i|we|both|parents|{OWNER_ALIAS_PATTERN})\b.+?\b"
        rf"(?:will\s+)?(?:{OWNER_ACTION_WORD_PATTERN})\b.*$",
        " ",
        title,
        flags=re.IGNORECASE,
    )
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
    return lowered.startswith(("what ", "what's ", "whats ", "show ", "list ")) or (
        "coming up" in lowered
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
    intent_text = event_text or user_text
    recurrence = _extract_recurrence(intent_text, reference)
    date, _ = _extract_date(intent_text, reference)
    if recurrence is not None:
        date = recurrence["start_date"]
    start_time = _extract_time(intent_text) or _extract_standalone_time(
        intent_text,
        intent_text,
    )
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
    if start_time is None:
        missing_fields.append("time")

    return {
        "intent": "create_event",
        "title": title,
        "date": date,
        "start_time": start_time,
        "duration_minutes": _extract_duration_minutes(user_text),
        "timezone": DEFAULT_TIMEZONE,
        "location": location,
        "description": description,
        "metadata": metadata,
        "recurrence": [recurrence["rrule"]] if recurrence is not None else None,
        "recurrence_label": recurrence["label"] if recurrence is not None else None,
        "missing_fields": missing_fields,
    }


def extract_intent(user_text: str, now: datetime | None = None) -> dict[str, Any]:
    reference = _default_now(now)
    intent_text, _, _ = _extract_assistant_help(user_text)
    routing_text = intent_text or user_text
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

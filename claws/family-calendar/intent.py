from __future__ import annotations

from datetime import datetime, time, timedelta
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


def _clean_spaces(value: str) -> str:
    return " ".join(value.split()).strip()


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

    for name, weekday in WEEKDAYS.items():
        if re.search(rf"\b{name}\b", lowered):
            day = _current_or_next_weekday(reference, weekday)
            start = _start_of_day(day)
            return start, start + timedelta(days=1)

    return None, None


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
    match = re.search(
        r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)\b",
        user_text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None

    return _time_from_match(match, context)


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


def _title_from_text(user_text: str, location: str | None, purpose: str | None) -> str | None:
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
    title = re.sub(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\bfor\s+\d+\s*(?:minute|minutes|min|hour|hours|hr|hrs)\b", " ", title, flags=re.IGNORECASE)

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


def _is_delete_request(user_text: str) -> bool:
    lowered = user_text.lower().strip()
    return lowered.startswith(("cancel ", "delete ", "remove "))


def _is_update_request(user_text: str) -> bool:
    lowered = user_text.lower().strip()
    return lowered.startswith(("move ", "reschedule ", "change ", "push ", "delay "))


def _list_intent(user_text: str, reference: datetime) -> dict[str, Any]:
    start, end = _extract_list_range(user_text, reference)
    missing_fields = []
    if start is None or end is None:
        missing_fields.append("date")

    return {
        "intent": "list_events",
        "start": start.isoformat() if start is not None else None,
        "end": end.isoformat() if end is not None else None,
        "missing_fields": missing_fields,
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
    recurrence = _extract_recurrence(user_text, reference)
    date, _ = _extract_date(user_text, reference)
    if recurrence is not None:
        date = recurrence["start_date"]
    start_time = _extract_time(user_text) or _extract_standalone_time(
        user_text,
        user_text,
    )
    location = _extract_location(user_text)
    purpose = _extract_purpose(user_text)
    with_description = _extract_with_description(user_text)
    flight_description = _extract_flight_description(user_text, location)
    description = flight_description or purpose or with_description
    title = _title_from_text(user_text, location, purpose)

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
        "recurrence": [recurrence["rrule"]] if recurrence is not None else None,
        "recurrence_label": recurrence["label"] if recurrence is not None else None,
        "missing_fields": missing_fields,
    }


def extract_intent(user_text: str, now: datetime | None = None) -> dict[str, Any]:
    reference = _default_now(now)
    if _is_update_request(user_text):
        return _update_intent(user_text, reference)
    if _is_delete_request(user_text):
        return _delete_intent(user_text, reference)
    if _is_list_request(user_text):
        return _list_intent(user_text, reference)

    return _create_intent(user_text, reference)

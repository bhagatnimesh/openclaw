from __future__ import annotations

from datetime import date as Date, datetime, timedelta
import re
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_CHILD = "Nysha"
CHILDREN = ("Nysha", "Navya")
DEFAULT_TIMEZONE = "America/Los_Angeles"
HOMEWORK_STATUS_VALUES = {"assigned", "in_progress", "submitted", "archived"}


def _clean_text(value: str) -> str:
    return " ".join(value.strip().split())


def _local_date(now: datetime | None) -> Date:
    if now is None:
        return datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).date()
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
    return now.astimezone(ZoneInfo(DEFAULT_TIMEZONE)).date()


def _strip_capture_prefix(value: str) -> str:
    return re.sub(
        r"^\s*/?capture(?:@[A-Za-z0-9_]+)?\s+",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()


def _strip_homework_prefix(value: str) -> str:
    text = _strip_capture_prefix(value)
    return re.sub(
        r"^\s*/?(?:"
        r"(?:submitted|turned\s+in|done|completed)\s+homework|"
        r"homework(?:\s+(?:status|complete|completed|done|submit|submitted|turned\s+in))?|"
        r"assignment"
        r")\b\s*[:\-]?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()


def _children(text: str) -> list[str]:
    lowered = text.lower()
    children = [child for child in CHILDREN if re.search(rf"\b{child.lower()}\b", lowered)]
    return children or [DEFAULT_CHILD]


def _parse_date_candidate(value: str, today: Date) -> str | None:
    cleaned = value.strip().rstrip(".,")
    formats = [
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y-%m-%d",
        "%B %d, %Y",
        "%b %d, %Y",
        "%B %d %Y",
        "%b %d %Y",
        "%B %d",
        "%b %d",
    ]
    for date_format in formats:
        try:
            if "%Y" not in date_format and "%y" not in date_format:
                parsed = datetime.strptime(f"{today.year} {cleaned}", f"%Y {date_format}").date()
                if parsed < today - timedelta(days=30):
                    parsed = parsed.replace(year=today.year + 1)
            else:
                parsed = datetime.strptime(cleaned, date_format).date()
        except ValueError:
            continue
        return parsed.isoformat()
    return None


def _next_weekday(name: str, today: Date) -> str:
    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    target = weekdays[name.lower()]
    days_ahead = (target - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return (today + timedelta(days=days_ahead)).isoformat()


def _extract_due_date(text: str, today: Date) -> str | None:
    caption_text = re.split(r"\bimage text\s*:", text, maxsplit=1, flags=re.IGNORECASE)[0]
    weekday_date = re.search(
        r"\b(?:due|due date|due by)\s*[:\-]?\s*"
        r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday),?\s+"
        r"([A-Za-z]{3,9}\s+\d{1,2},?\s*\d{0,4})",
        text,
        flags=re.IGNORECASE,
    )
    if weekday_date:
        parsed = _parse_date_candidate(weekday_date.group(1), today)
        if parsed:
            return parsed

    relative = re.search(
        r"\b(?:due|due date|due by)\s*[:\-]?\s*(today|tomorrow|(?:next\s+)?monday|(?:next\s+)?tuesday|(?:next\s+)?wednesday|(?:next\s+)?thursday|(?:next\s+)?friday|(?:next\s+)?saturday|(?:next\s+)?sunday)\b",
        text,
        flags=re.IGNORECASE,
    )
    if relative:
        value = relative.group(1).lower()
        if value == "today":
            return today.isoformat()
        if value == "tomorrow":
            return (today + timedelta(days=1)).isoformat()
        value = value.removeprefix("next ").strip()
        return _next_weekday(value, today)

    standalone_next = re.search(
        r"\bclass\s+next\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        caption_text,
        flags=re.IGNORECASE,
    )
    if standalone_next:
        return _next_weekday(standalone_next.group(1), today)

    patterns = [
        r"\b(?:due date|due by|due)\s*[:\-]?\s*([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})",
        r"\b(?:due date|due by|due)\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{2,4})",
        r"\b(?:due date|due by|due)\s*[:\-]?\s*(\d{4}-\d{2}-\d{2})",
        r"\b(?:due date|due by|due)\s*[:\-]?\s*([A-Za-z]{3,9}\s+\d{1,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            parsed = _parse_date_candidate(match.group(1), today)
            if parsed:
                return parsed

    offset = re.search(
        r"\b(?:after|in)\s+(\d+|one|two|three|four)\s+weeks?\b",
        caption_text,
        flags=re.IGNORECASE,
    )
    if offset:
        value = {
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
        }.get(offset.group(1).lower())
        weeks = value if value is not None else int(offset.group(1))
        return (today + timedelta(days=7 * weeks)).isoformat()
    return None


def _extract_due_time(text: str) -> str | None:
    match = re.search(
        r"\b(?:due|due date|due by)\b.*?\b(?:at|by)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        match = re.search(
            r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
            text,
            flags=re.IGNORECASE,
        )
    if match is None:
        match = re.search(
            r"^\s*(?:homework\s+)?(?:due\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*$",
            text,
            flags=re.IGNORECASE,
        )
    if match is None:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3).lower()
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _extract_assigned_date(text: str, today: Date) -> str:
    match = re.search(
        r"\b(?:assigned|sent home|captured)\s*[:\-]?\s*(today|yesterday|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},?\s*\d{0,4})\b",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return today.isoformat()
    value = match.group(1).lower()
    if value == "today":
        return today.isoformat()
    if value == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    return _parse_date_candidate(match.group(1), today) or today.isoformat()


def _label_value(text: str, labels: tuple[str, ...]) -> str | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"^\s*(?:{label_pattern})\s*[:\-]\s*(.+?)\s*$",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if not match:
        return None
    return _clean_text(match.group(1).strip(" .,:;-\"'")) or None


def _extract_subject(text: str) -> str | None:
    caption_text = re.split(r"\bimage text:\b", text, maxsplit=1, flags=re.IGNORECASE)[0]
    class_patterns = (
        ("RSM Math", r"\brsm\s+math(?:\s+class)?\b"),
        ("After-school learning", r"\bafter[-\s]school\s+learning\b"),
        ("Math", r"\bmath\s+class\b"),
        ("Art", r"\bart\s+class\b"),
    )
    for label, pattern in class_patterns:
        if re.search(pattern, caption_text, flags=re.IGNORECASE):
            return label
    caption_match = re.search(
        r"\b(math|reading|writing|spelling|science|social studies|art|music)\b",
        caption_text,
        flags=re.IGNORECASE,
    )
    if caption_match:
        return caption_match.group(1).title()
    labeled = _label_value(text, ("subject", "class", "class/subject"))
    if labeled:
        return labeled
    for label, pattern in class_patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return label
    match = re.search(
        r"\b(math|reading|writing|spelling|science|social studies|art|music)\b",
        text,
        flags=re.IGNORECASE,
    )
    return match.group(1).title() if match else None


def _extract_grade(text: str) -> str | None:
    labeled = _label_value(text, ("grade",))
    if labeled:
        return labeled
    match = re.search(r"\b(\d)(?:st|nd|rd|th)\s+grade\b", text, flags=re.IGNORECASE)
    return f"{match.group(1)}nd grade" if match and match.group(1) == "2" else (match.group(0) if match else None)


def _extract_week_range(text: str) -> str | None:
    labeled = _label_value(text, ("week range", "week of", "week"))
    if labeled:
        return labeled
    match = re.search(
        r"\bweek\s+(?:of\s+)?([A-Za-z]{3,9}\s+\d{1,2}\s*(?:-|to|through)\s*[A-Za-z]{0,9}\s*\d{1,2})",
        text,
        flags=re.IGNORECASE,
    )
    return _clean_text(match.group(1)) if match else None


def _extract_daily_work(text: str) -> str | None:
    days = []
    for line in text.splitlines():
        cleaned = line.strip(" -*\t")
        if re.match(r"^(monday|tuesday|wednesday|thursday|friday)\b", cleaned, flags=re.IGNORECASE):
            days.append(cleaned)
    return "\n".join(days) if days else None


def _extract_title(text: str, subject: str | None) -> str:
    for labels in (
        ("homework title", "assignment title", "title"),
        ("packet", "worksheet"),
    ):
        labeled = _label_value(text, labels)
        if labeled:
            return labeled

    project = re.search(
        r"\b(?:the\s+)?([A-Za-z0-9][A-Za-z0-9 '&-]{2,80}?\s+project)\s+is\s+due\b",
        text,
        flags=re.IGNORECASE,
    )
    if project:
        return _clean_text(project.group(1))

    body = _strip_homework_prefix(text)
    body = re.split(r"\bimage text\s*:", body, maxsplit=1, flags=re.IGNORECASE)[0]
    body = re.sub(r"\b(?:nysha|navya)\b", "", body, flags=re.IGNORECASE)
    body = re.sub(r"\b(?:due|assigned)\b.+$", "", body, flags=re.IGNORECASE)
    body = re.sub(
        r"\bclass\s+next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        "class",
        body,
        flags=re.IGNORECASE,
    )
    body = re.sub(r"\b(?:math|reading|writing|spelling|science|social studies)\b", "", body, flags=re.IGNORECASE)
    body = _clean_text(body.strip(" .,:;-"))
    if body and len(body) <= 80:
        return body
    ocr = _ocr_text(text)
    if ocr:
        for line in ocr.splitlines():
            cleaned = _clean_text(line.strip(" .,:;-"))
            if re.match(
                r"^(?:assigned|class|due date|due|grade|student|subject|visible instructions|week range|week)\s*:",
                cleaned,
                flags=re.IGNORECASE,
            ):
                continue
            if cleaned and len(cleaned) <= 80:
                return cleaned
    if subject:
        return f"{subject} homework"
    return "Homework"


def _extract_notes(text: str) -> str | None:
    labeled = _label_value(text, ("notes", "instructions", "visible instructions"))
    if labeled:
        return labeled
    if re.search(r"\bparent\s+signature|required\s+signature|sign and return\b", text, flags=re.IGNORECASE):
        return "Parent signature required."
    return None


def _ocr_text(text: str) -> str | None:
    match = re.search(r"image text:\s*(.+)$", text, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else None


def _is_submission_request(text: str) -> bool:
    return bool(
        re.search(
            r"^\s*/?homework(?:@[A-Za-z0-9_]+)?\s+"
            r"(?:complete|completed|done|submit|submitted|turned\s+in)\b",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(
            r"^\s*/?capture(?:@[A-Za-z0-9_]+)?\s+(?:submitted|turned\s+in|done|completed)\s+homework\b",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(
            r"\bhomework\s+(?:is\s+)?(?:submitted|turned\s+in|done|completed)\b",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(
            r"^\s*(?:submitted|turned\s+in|done|completed)\s+homework\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def is_homework_capture(text: str) -> bool:
    lowered = text.lower()
    if re.search(r"^\s*/?lesson(?:@[A-Za-z0-9_]+)?\b", lowered):
        return True
    if re.search(r"^\s*/?capture(?:@[A-Za-z0-9_]+)?\s+(?:submitted\s+)?homework\b", lowered):
        return True
    if re.search(
        r"^\s*/?homework(?:@[a-z0-9_]+)?\s+"
        r"(?:complete|completed|done|submit|submitted|turned\s+in)\b",
        lowered,
    ):
        return True
    return bool(
        "image text:" in lowered
        and re.search(r"\b(homework|worksheet|assignment|student|grade|week of|parent signature)\b", lowered)
    )


def has_homework_terms(text: str) -> bool:
    return bool(
        re.search(
            r"\b(homework|lesson|worksheet|assignment|submitted\s+homework|turned\s+in|parent\s+signature)\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def is_learning_review(text: str) -> bool:
    return bool(re.search(r"^\s*/?learning(?:@[A-Za-z0-9_]+)?\s+(?:review|status)\b", text, flags=re.IGNORECASE))


def extract_intent(request: str, now: datetime | None = None, *, source: str = "telegram_text", photo_path: str | None = None) -> dict[str, Any]:
    raw = request.strip()
    today = _local_date(now)
    lowered = raw.lower()
    if re.search(r"^\s*/?capture(?:@[A-Za-z0-9_]+)?\s+homework\s+status\b", lowered):
        children = _children(raw)
        return {"intent": "homework_status", "child": children[0], "children": children}
    if re.search(r"\b(?:homework\s+status|list\s+homework|show\s+homework)\b", lowered):
        children = _children(raw)
        return {"intent": "homework_status", "child": children[0], "children": children}

    children = _children(raw)
    subject = _extract_subject(raw)
    is_lesson = bool(re.match(r"^\s*/?lesson(?:@[A-Za-z0-9_]+)?\b", raw, flags=re.IGNORECASE))
    status = "submitted" if _is_submission_request(raw) else "assigned"
    intent = "capture_submission" if status == "submitted" else "capture_assignment"
    return {
        "intent": intent,
        "child": children[0],
        "children": children,
        "title": _extract_title(raw, subject),
        "subject": subject,
        "assigned_date": _extract_assigned_date(raw, today),
        "due_date": _extract_due_date(raw, today),
        "due_time": _extract_due_time(raw),
        "status": status,
        "notes": _extract_notes(raw),
        "ocr_text": _ocr_text(raw),
        "grade": _extract_grade(raw),
        "week_range": _extract_week_range(raw),
        "daily_work": _extract_daily_work(raw),
        "source": source,
        "photo_path": photo_path,
        "raw_input": raw,
        "record_type": "lesson" if is_lesson else "homework",
        "lesson_identifier": _label_value(raw, ("lesson", "lesson number")) if is_lesson else None,
    }

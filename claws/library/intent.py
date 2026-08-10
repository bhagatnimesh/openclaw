from __future__ import annotations

from datetime import date as Date, datetime, timedelta
import re
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_CHILD = "Nysha"
CHILDREN = ("Nysha", "Navya")
DEFAULT_TIMEZONE = "America/Los_Angeles"
NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "twenty": 20,
}
BAD_BOOK_TITLE_PATTERNS = (
    r"^no visible",
    r"^no readable",
    r"^no book",
    r"^no title",
    r"checklist entries",
    r"unable to",
    r"cannot determine",
)


def _clean_text(value: str) -> str:
    return " ".join(value.strip().split())


def _strip_command(value: str) -> str:
    return re.sub(r"^\s*/(?:read|done|library|reading|checkout)\s+", "", value, flags=re.IGNORECASE).strip()


def _local_date(now: datetime | None) -> Date:
    if now is None:
        return datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).date()
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
    return now.astimezone(ZoneInfo(DEFAULT_TIMEZONE)).date()


def _today(now: datetime | None) -> str:
    return _local_date(now).isoformat()


def _number(pattern_value: str | None) -> int | None:
    if not pattern_value:
        return None
    lowered = pattern_value.lower()
    if lowered.isdigit():
        return int(lowered)
    return NUMBER_WORDS.get(lowered)


def _extract_measure(text: str, unit: str) -> int | None:
    match = re.search(
        rf"\b(\d{{1,3}}|{'|'.join(NUMBER_WORDS)})\s+{unit}s?\b",
        text,
        flags=re.IGNORECASE,
    )
    return _number(match.group(1)) if match else None


def _trim_book(value: str) -> str:
    cleaned = re.sub(
        r"\b(?:by herself|herself|independently|by himself|together|today|yesterday|before dinner|after dinner|last night|on\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b.*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+(?:and|but)\s+she\s+liked\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+to\s+(?:nysha|navya)\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+image text:.*$", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = cleaned.strip(" .,!?:;-\"'")
    return _clean_text(cleaned) if cleaned else "unknown book"


def _usable_book_title(value: str) -> bool:
    lowered = value.lower().strip()
    if lowered in {"", "this", "this book", "a book", "unknown book"}:
        return False
    return not any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in BAD_BOOK_TITLE_PATTERNS)


def _extract_book(text: str, status: str) -> str:
    commandless = _strip_command(text)
    image_text_match = re.search(r"image text:\s*(.+)$", commandless, flags=re.IGNORECASE | re.DOTALL)
    if image_text_match:
        for line in image_text_match.group(1).splitlines():
            cleaned_line = line.strip(" -*•\t")
            label_match = re.match(
                r"^(?:book\s+title|title|book)\s*[:\-]\s*(.+)$",
                cleaned_line,
                flags=re.IGNORECASE,
            )
            candidate = _trim_book(label_match.group(1) if label_match else cleaned_line)
            if _usable_book_title(candidate):
                return candidate
    patterns = [
        r"\bbook\s+title\s*[:\-]\s*(.+)",
        r"\bbook\s*[:\-]\s*(.+)",
        r"\btitle\s*[:\-]\s*(.+)",
        r"\bread\s+(?:\d{1,3}|" + "|".join(NUMBER_WORDS) + r")\s+pages?\s+of\s+(.+)",
        r"\bfinished\s+(.+)",
        r"\bread\s+(?:this\s+book|a\s+book)\s*(?:called|named|titled)?\s*(.*)",
        r"\bread\s+(.+?)\s+(?:by herself|herself|independently)\b",
        r"\bread\s+(.+)",
    ]
    if status == "completed":
        patterns.insert(0, r"/done\s+(.+)")
    for pattern in patterns:
        match = re.search(pattern, commandless, flags=re.IGNORECASE)
        if match:
            candidate = _trim_book(match.group(1))
            measure_prefix = r"^(?:for\s+)?(?:\d{1,3}|" + "|".join(NUMBER_WORDS) + r")\s+(?:minutes?|pages?)\b"
            if re.search(measure_prefix, candidate, re.IGNORECASE):
                continue
            if _usable_book_title(candidate):
                return candidate
    return "unknown book"


def _extract_reaction(text: str) -> str | None:
    match = re.search(
        r"\b(?:she\s+)?(?:liked|loved|laughed at|favorite part was)\s+(.+)$",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    reaction = _clean_text(match.group(0).strip(" ."))
    return reaction[:1].upper() + reaction[1:] if reaction else None


def _children(text: str) -> list[str]:
    lowered = text.lower()
    children = [child for child in CHILDREN if re.search(rf"\b{re.escape(child.lower())}\b", lowered)]
    if children:
        return children
    if re.search(r"\b(?:both kids|both girls|kids|girls|children|sisters|nysha and navya|navya and nysha)\b", lowered):
        return list(CHILDREN)
    return []


def _reading_mode(text: str, children: list[str]) -> str:
    if re.search(r"\b(?:by herself|herself|independently|i read it myself)\b", text, re.IGNORECASE):
        return "independent"
    if re.search(r"\b(?:we|together|with me|with us|family)\b.*\bread\b|\bread\b.*\btogether\b", text, re.IGNORECASE):
        return "read_together"
    if re.search(
        r"\b(?:dad|mom|mama|papa|adult|i)\s+read\b.*\bto\b|\bread\s+aloud\b",
        text,
        re.IGNORECASE,
    ):
        return "read_aloud"
    if children and re.search(r"\b(?:read|finished)\b", text, re.IGNORECASE):
        return "independent"
    return "unknown"


def _clearly_independent(text: str) -> bool:
    return bool(
        re.search(r"\b(?:by herself|herself|independently|i read it myself)\b", text, re.IGNORECASE)
        or re.search(r"\bnysha\s+(?:read|finished)\b", text, re.IGNORECASE)
    )


def _weekday_date(name: str, today: Date, *, last: bool = False) -> Date:
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
    days_back = (today.weekday() - target) % 7
    if last and days_back == 0:
        days_back = 7
    return today - timedelta(days=days_back)


def _parse_date_candidate(value: str, today: Date) -> Date | None:
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
            else:
                parsed = datetime.strptime(cleaned, date_format).date()
        except ValueError:
            continue
        if "%Y" not in date_format and "%y" not in date_format:
            if parsed > today + timedelta(days=7):
                parsed = parsed.replace(year=today.year - 1)
        return parsed
    return None


def _reading_date(text: str, now: datetime | None) -> str:
    today = _local_date(now)
    lowered = text.lower()
    if re.search(r"\b(?:yesterday|last night)\b", lowered):
        return (today - timedelta(days=1)).isoformat()
    if re.search(r"\btoday\b", lowered):
        return today.isoformat()

    weekday_match = re.search(
        r"\b(?P<last>last\s+)?(?P<weekday>monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        lowered,
    )
    if weekday_match:
        return _weekday_date(
            weekday_match.group("weekday"),
            today,
            last=bool(weekday_match.group("last")),
        ).isoformat()

    explicit_match = re.search(
        r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{0,4})\b",
        text,
    )
    if explicit_match:
        parsed = _parse_date_candidate(explicit_match.group(1), today)
        if parsed:
            return parsed.isoformat()
    return today.isoformat()


def _explicit_update_date(text: str, now: datetime | None) -> str | None:
    lowered = text.lower()
    if not re.search(r"\b(?:date|day|move|change|fix|update)\b", lowered):
        return None
    if re.search(r"\b(?:to|as|was|for)\s+(?:yesterday|last night)\b", lowered):
        return (_local_date(now) - timedelta(days=1)).isoformat()
    if re.search(r"\b(?:to|as|was|for)\s+today\b", lowered):
        return _local_date(now).isoformat()
    weekday_match = re.search(
        r"\b(?:to|as|was|for|on)\s+(?P<last>last\s+)?(?P<weekday>monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        lowered,
    )
    if weekday_match:
        return _weekday_date(
            weekday_match.group("weekday"),
            _local_date(now),
            last=bool(weekday_match.group("last")),
        ).isoformat()
    explicit_match = re.search(
        r"\b(?:to|as|was|for|on)\s+(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{0,4})\b",
        text,
    )
    if explicit_match:
        parsed = _parse_date_candidate(explicit_match.group(1), _local_date(now))
        if parsed:
            return parsed.isoformat()
    return None


def _reading_change_action(text: str) -> str | None:
    if re.search(r"\b(?:delete|remove|undo)\b.*\b(?:reading|book|moment|entry|log)\b", text, re.IGNORECASE):
        return "delete_reading"
    if re.search(r"\b(?:reading|book|moment|entry|log)\b.*\b(?:delete|remove|undo)\b", text, re.IGNORECASE):
        return "delete_reading"
    if re.search(r"\b(?:change|update|edit|fix|correct)\b.*\b(?:reading|book|moment|entry|log|title|date|day|pages?|minutes?)\b", text, re.IGNORECASE):
        return "update_reading"
    return None


def _target_book(text: str) -> str | None:
    patterns = [
        r"\b(?:entry|moment|log|book)\s+(?:for|called|named|titled)\s+(.+?)(?:\s+(?:to|as|was|from)\b|$)",
        r"\b(?:delete|remove|undo)\s+(.+?)(?:\s+(?:reading|entry|moment|log)\b|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = _trim_book(match.group(1))
        candidate = re.sub(
            r"\b(?:nysha|navya|latest|last|most recent)\b",
            "",
            candidate,
            flags=re.IGNORECASE,
        )
        candidate = _clean_text(candidate)
        if _usable_book_title(candidate) and candidate.lower() not in {"latest", "last", "most recent"}:
            return candidate
    return None


def _update_book(text: str) -> str | None:
    patterns = [
        r"\b(?:book\s+title|title|book)\s+(?:to|as|is|was)\s+(.+)",
        r"\b(?:change|update|edit|fix|correct)\b.*\b(?:to|as)\s+(.+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = _trim_book(match.group(1))
        if _usable_book_title(candidate) and not re.search(r"\b(?:today|yesterday|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", candidate, re.IGNORECASE):
            return candidate
    return None


def _reading_update_fields(text: str, now: datetime | None) -> dict[str, Any]:
    status = None
    if re.search(r"\b(?:finished|completed|done)\b", text, re.IGNORECASE):
        status = "completed"
    elif re.search(r"\b(?:not finished|still reading|in progress)\b", text, re.IGNORECASE):
        status = "in_progress"

    children = _children(text)
    fields: dict[str, Any] = {
        "children": children,
        "child": children[0] if len(children) == 1 else None,
        "target_book": _target_book(text),
        "book": _update_book(text),
        "date": _explicit_update_date(text, now),
        "pages": _extract_measure(text, "page"),
        "minutes": _extract_measure(text, "minute"),
        "status": status,
        "reading_mode": _reading_mode(text, children) if re.search(r"\b(?:together|aloud|herself|independently|myself)\b", text, re.IGNORECASE) else None,
    }
    return {key: value for key, value in fields.items() if value not in (None, [], "")}


def _library_checkout(text: str) -> bool:
    if _children(text) and re.search(r"\b(?:read|finished)\b", text, re.IGNORECASE):
        return False
    if re.search(r"\badd\s+to\s+(?:the\s+)?library\b", text, re.IGNORECASE):
        return True
    if re.search(r"\blibrary\b.*\bfamily\s+reading\b", text, re.IGNORECASE):
        return True
    if re.search(r"\b(?:checkout|checked out|library bag|library receipt)\b", text, re.IGNORECASE):
        return True
    if re.search(r"\b(?:due date|due by)\b", text, re.IGNORECASE) and re.search(
        r"\b(?:library|books?|titles?|borrowed|checkout|receipt)\b",
        text,
        re.IGNORECASE,
    ):
        return True
    return bool(
        re.search(r"\blibrary\b", text, re.IGNORECASE)
        and re.search(r"\b(?:books?|titles?|items?|borrowed|receipt)\b", text, re.IGNORECASE)
    )


def extract_intent(
    request: str,
    now: datetime | None = None,
    *,
    source: str = "telegram_text",
    photo_path: str | None = None,
) -> dict[str, Any]:
    raw = _clean_text(request)
    lowered = raw.lower()
    if source == "telegram_photo" and not raw:
        return {
            "intent": "clarify",
            "question": "Did Nysha read this herself, and what book is it?",
            "missing_fields": ["independent_reading", "book"],
        }
    if not raw:
        return {"intent": "unknown", "missing_fields": ["message"]}
    if re.fullmatch(r"/?status|/?reading status|/?garden status", lowered):
        return {"intent": "status", "children": list(CHILDREN)}
    reading_change_action = _reading_change_action(raw)
    if reading_change_action == "delete_reading":
        children = _children(raw)
        return {
            "intent": "delete_reading",
            "children": children,
            "child": children[0] if len(children) == 1 else None,
            "target_book": _target_book(raw),
            "latest": bool(re.search(r"\b(?:latest|last|most recent)\b", raw, re.IGNORECASE)),
            "raw_input": request.strip(),
        }
    if reading_change_action == "update_reading":
        fields = _reading_update_fields(raw, now)
        return {
            "intent": "update_reading",
            **fields,
            "latest": bool(re.search(r"\b(?:latest|last|most recent)\b", raw, re.IGNORECASE)),
            "raw_input": request.strip(),
        }
    if _library_checkout(raw):
        return {
            "intent": "record_checkout",
            "date": _today(now),
            "source": source,
            "raw_input": request.strip(),
        }
    children = _children(raw)
    if not children and not re.search(r"\b(read|finished)\b", lowered):
        return {"intent": "unknown", "missing_fields": ["reading_event"]}
    if not children:
        return {
            "intent": "clarify",
            "question": "Was this reading for Nysha, Navya, or both?",
            "missing_fields": ["child"],
        }

    status = "completed" if re.search(r"\b(?:finished|/done)\b", raw, re.IGNORECASE) else "in_progress"
    pages = _extract_measure(raw, "page")
    minutes = _extract_measure(raw, "minute")
    return {
        "intent": "record_reading",
        "children": children,
        "child": children[0],
        "date": _reading_date(raw, now),
        "book": _extract_book(raw, status),
        "minutes": minutes,
        "pages": pages,
        "reaction": _extract_reaction(raw),
        "status": status,
        "reading_mode": _reading_mode(raw, children),
        "source": source,
        "photo_path": photo_path,
        "raw_input": raw,
    }

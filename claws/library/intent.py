from __future__ import annotations

from datetime import datetime
import re
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_CHILD = "Nysha"
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


def _clean_text(value: str) -> str:
    return " ".join(value.strip().split())


def _strip_command(value: str) -> str:
    return re.sub(r"^\s*/(?:read|done|library|reading|checkout)\s+", "", value, flags=re.IGNORECASE).strip()


def _today(now: datetime | None) -> str:
    if now is None:
        return datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).date().isoformat()
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
    return now.astimezone(ZoneInfo(DEFAULT_TIMEZONE)).date().isoformat()


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
        r"\b(?:by herself|herself|independently|by himself|today|before dinner|after dinner|last night)\b.*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+(?:and|but)\s+she\s+liked\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip(" .,!?:;-\"'")
    return _clean_text(cleaned) if cleaned else "unknown book"


def _extract_book(text: str, status: str) -> str:
    commandless = _strip_command(text)
    patterns = [
        r"\bread\s+(?:\d{1,3}|" + "|".join(NUMBER_WORDS) + r")\s+pages?\s+of\s+(.+)",
        r"\bfinished\s+(.+)",
        r"\bread\s+(?:this\s+book|a\s+book)\s*(?:called|named|titled)?\s*(.*)",
        r"\bread\s+(.+?)\s+(?:by herself|herself|independently)\b",
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
            if candidate and candidate.lower() not in {"", "this", "this book", "a book"}:
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


def _adult_read_aloud(text: str) -> bool:
    return bool(
        re.search(r"\b(?:dad|mom|mama|papa|adult|we|i)\s+read\s+.+\bto\s+nysha\b", text, re.IGNORECASE)
        or re.search(r"\bread\s+aloud\s+to\s+nysha\b", text, re.IGNORECASE)
    )


def _clearly_independent(text: str) -> bool:
    return bool(
        re.search(r"\b(?:by herself|herself|independently|i read it myself)\b", text, re.IGNORECASE)
        or re.search(r"\bnysha\s+(?:read|finished)\b", text, re.IGNORECASE)
    )


def _library_checkout(text: str) -> bool:
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
        return {"intent": "status", "child": DEFAULT_CHILD}
    if _library_checkout(raw):
        return {
            "intent": "record_checkout",
            "date": _today(now),
            "source": source,
            "raw_input": request.strip(),
        }
    if _adult_read_aloud(raw):
        return {"intent": "not_counted", "reason": "adult_read_aloud"}
    if "nysha" not in lowered and not re.search(r"\b(read|finished)\b", lowered):
        return {"intent": "unknown", "missing_fields": ["reading_event"]}
    if not _clearly_independent(raw):
        return {
            "intent": "clarify",
            "question": "Did Nysha read this herself?",
            "missing_fields": ["independent_reading"],
        }

    status = "completed" if re.search(r"\b(?:finished|/done)\b", raw, re.IGNORECASE) else "in_progress"
    pages = _extract_measure(raw, "page")
    minutes = _extract_measure(raw, "minute")
    return {
        "intent": "record_reading",
        "child": DEFAULT_CHILD,
        "date": _today(now),
        "book": _extract_book(raw, status),
        "minutes": minutes,
        "pages": pages,
        "reaction": _extract_reaction(raw),
        "status": status,
        "source": source,
        "photo_path": photo_path,
        "raw_input": raw,
    }

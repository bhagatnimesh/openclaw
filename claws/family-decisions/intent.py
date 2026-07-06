from __future__ import annotations

from datetime import date, datetime, timedelta
import re
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_TIMEZONE = "America/Los_Angeles"
VALID_STATUSES = {
    "inbox",
    "clarifying",
    "researching",
    "ready",
    "deciding",
    "decided",
    "parked",
}
VALID_URGENCIES = {"low", "normal", "high", "critical"}
VALID_SIZES = {"small", "medium", "large"}
VALID_OWNERS = {"dad", "mom", "both", "grandmom", "family", "unknown"}
OWNER_ALIASES = {
    "dad": "dad",
    "nimesh": "dad",
    "namesh": "dad",
    "papa": "dad",
    "papu": "dad",
    "mom": "mom",
    "mum": "mom",
    "mummy": "mom",
    "niyati": "mom",
    "niyaati": "mom",
    "niyathi": "mom",
    "both": "both",
    "parents": "both",
    "grand mom": "grandmom",
    "grandmom": "grandmom",
    "dadi": "grandmom",
    "tarla": "grandmom",
    "family": "family",
    "everyone": "family",
}
OWNER_ALIAS_PATTERN = "|".join(
    re.escape(value)
    for value in sorted(OWNER_ALIASES, key=len, reverse=True)
)
BRIEF_WORD_RE = r"(?:brief|bried|breif|brif)"
DETAIL_MARKER_RE = re.compile(
    r"\b(?:options?|choices?)\b\s*(?:are|is|include|includes|:)|"
    r"\b(?:challenges?|concerns?|risks?|evidence|research|notes?|next\s+steps?|todo)\b"
    r"\s*(?:are|is|include|includes|:)?",
    re.IGNORECASE,
)
OPTION_MARKER_RE = re.compile(
    r"\b(?:options?|choices?)\b\s*(?:are|is|include|includes|:)",
    re.IGNORECASE,
)
EVIDENCE_MARKER_RE = re.compile(
    r"\b(?:challenges?|concerns?|risks?|evidence|research|notes?)\b\s*(?:are|is|include|includes|to|:)?",
    re.IGNORECASE,
)
NEXT_STEP_MARKER_RE = re.compile(
    r"\b(?:next\s+steps?|todo)\b\s*(?:are|is|include|includes|:)?",
    re.IGNORECASE,
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


def _title(value: str) -> str:
    cleaned = _clean_spaces(value)
    if not cleaned:
        return cleaned
    return cleaned[:1].upper() + cleaned[1:]


def normalize_decision_request_text(value: str) -> str:
    """Repair high-confidence dictation mistakes before intent parsing."""
    cleaned = _clean_spaces(value)
    cleaned = re.sub(
        r"\bdecision\s+(?:bried|breif|brif)\b",
        "decision brief",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(?:bried|breif|brif)\s+decision\b",
        "brief decision",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\bjet\s+lagged\b", "jetlagged", cleaned, flags=re.IGNORECASE)
    return cleaned


def _due_date(text: str, reference: datetime) -> str | None:
    lowered = text.lower()
    if re.search(r"\btoday\b", lowered):
        return reference.date().isoformat()
    if re.search(r"\btomorrow\b", lowered):
        return (reference + timedelta(days=1)).date().isoformat()
    if re.search(r"\bthis week\b", lowered):
        return (reference + timedelta(days=7 - reference.weekday())).date().isoformat()
    for name, weekday in WEEKDAYS.items():
        if re.search(rf"\bnext\s+{name}\b", lowered):
            days = (weekday - reference.weekday()) % 7 or 7
            return (reference + timedelta(days=days)).date().isoformat()
        if re.search(rf"\bby\s+{name}\b", lowered):
            days = (weekday - reference.weekday()) % 7
            return (reference + timedelta(days=days)).date().isoformat()
    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", lowered)
    if match:
        try:
            return date.fromisoformat(match.group(1)).isoformat()
        except ValueError:
            return None
    return None


def _owner(text: str) -> str:
    match = re.search(
        rf"\b(?:owner|owned by|for)\s+(?P<owner>{OWNER_ALIAS_PATTERN})\b",
        text,
        flags=re.IGNORECASE,
    )
    if match is not None:
        return OWNER_ALIASES[match.group("owner").lower()]
    return "unknown"


def _urgency(text: str) -> str:
    lowered = text.lower()
    if re.search(r"\b(critical|expedite|expedited|urgent|asap)\b", lowered):
        return "critical"
    if re.search(r"\b(important|soon|this week)\b", lowered):
        return "high"
    if re.search(r"\b(low priority|someday|whenever)\b", lowered):
        return "low"
    return "normal"


def _size(text: str) -> str:
    lowered = text.lower()
    if re.search(r"\b(school|camp|summer camp|childcare|move|house|medical)\b", lowered):
        return "large"
    if re.search(r"\b(birthday|party|travel|trip|activity|class)\b", lowered):
        return "medium"
    return "small"


def _status(text: str) -> str:
    lowered = text.lower()
    if re.search(r"\b(decided|decision is|we chose|we choose|final)\b", lowered):
        return "decided"
    if re.search(r"\b(research|look up|compare|find out)\b", lowered):
        return "researching"
    if re.search(r"\b(park|later|someday)\b", lowered):
        return "parked"
    return "inbox"


def _strip_decision_prefix(text: str) -> str:
    cleaned = re.sub(
        r"^\s*(?:please\s+)?(?:(?:add|create|capture|track|remember|open)\s+"
        r"(?:an?\s+)?(?:family\s+)?decision|captured\s+decision)\s*(?:about|for|on|to|:)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^\s*(?:i\s+had|there\s+is|this\s+is)\s+(?:a\s+)?"
        r"(?:family\s+)?decision\s*(?:about|for|on|to|:)?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^\s*(?:we\s+need\s+to\s+)?(?:decide|choose)\s+(?:whether\s+|if\s+|on\s+)?",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\bby\s+\d{4}-\d{2}-\d{2}\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bby\s+next\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bby\s+(today|tomorrow|this week|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        rf"\b(owner|owned by|for)\s+(?:{OWNER_ALIAS_PATTERN})\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return _clean_spaces(cleaned)


def _has_decision_capture_prefix(text: str) -> bool:
    detail_free_text = _strip_detail_segments(text)
    return _strip_decision_prefix(detail_free_text) != _clean_spaces(detail_free_text)


def _strip_detail_segments(text: str) -> str:
    match = DETAIL_MARKER_RE.search(text)
    if match is None:
        return text
    return _clean_spaces(text[:match.start()])


def _segment_after(
    text: str,
    marker: re.Pattern[str],
    stop: re.Pattern[str] | None = None,
) -> str | None:
    match = marker.search(text)
    if match is None:
        return None
    end = len(text)
    if stop is not None:
        stop_match = stop.search(text, match.end())
        if stop_match is not None:
            end = stop_match.start()
    return _clean_spaces(text[match.end():end])


def _split_options(value: str | None) -> list[str]:
    if not value:
        return []
    pieces = re.split(r"\s*(?:[,;]|\bor\b|\bversus\b|\bvs\.?\b)\s*", value)
    return [_title(piece) for piece in pieces if _clean_spaces(piece)]


def _option_texts(text: str) -> list[str]:
    segment = _segment_after(text, OPTION_MARKER_RE, EVIDENCE_MARKER_RE)
    if segment:
        return _split_options(segment)
    return [_option_text(text)] if _option_text(text) else []


def _evidence_texts(text: str) -> list[str]:
    segment = _segment_after(text, EVIDENCE_MARKER_RE, NEXT_STEP_MARKER_RE)
    if segment:
        return [_title(segment)]
    evidence = _evidence_text(text)
    return [evidence] if evidence else []


def _initial_details(text: str) -> dict[str, list[str]]:
    option_segment = _segment_after(text, OPTION_MARKER_RE, EVIDENCE_MARKER_RE)
    evidence_segment = _segment_after(text, EVIDENCE_MARKER_RE, NEXT_STEP_MARKER_RE)
    return {
        "options": _split_options(option_segment),
        "evidence": [_title(evidence_segment)] if evidence_segment else [],
    }


def _title_and_context(text: str) -> tuple[str, str]:
    cleaned = _clean_spaces(text)
    if not cleaned:
        return "", ""
    match = re.match(r"(.+?[.!?])\s+(.+)$", cleaned)
    if match is None:
        return _title(cleaned), ""
    title = _title(match.group(1).strip(" .!?"))
    return title, _clean_spaces(match.group(2))


def _decision_id(text: str) -> str | None:
    match = re.search(r"\b(?:decision\s+)?([0-9a-f]{6,32})\b", text, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _line_after_label(text: str, label: str) -> str | None:
    match = re.search(rf"\b{label}\s*:\s*(.+)$", text, flags=re.IGNORECASE | re.MULTILINE)
    return _clean_spaces(match.group(1)) if match else None


def _option_text(text: str) -> str:
    labeled = _line_after_label(text, "option")
    if labeled:
        return labeled
    value = re.sub(
        r"^\s*(?:add\s+)?options?\s*(?:for\s+\w+)?\s*(?:are|is|include|includes|:)?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return _clean_spaces(value)


def _evidence_text(text: str) -> str:
    labeled = _line_after_label(text, "evidence") or _line_after_label(text, "research")
    if labeled:
        return labeled
    value = re.sub(
        r"^\s*(?:add(?:ed)?\s+)?(?:evidence|research|note|notes?|challenges?|concerns?|risks?)\s*"
        r"(?:for\s+\w+)?\s*(?:are|is|include|includes|to|:)?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return _clean_spaces(value)


def _next_step_text(text: str) -> str:
    labeled = _line_after_label(text, "next step") or _line_after_label(text, "todo")
    if labeled:
        return labeled
    return _clean_spaces(re.sub(r"^\s*(?:add\s+)?(?:next\s+step|todo)\s*(?:for\s+\w+)?\s*:?", "", text, flags=re.IGNORECASE))


def _decided_text(text: str) -> str:
    match = re.search(
        r"\b(?:decision is|we decided|we chose|we choose|final(?:ly)? decided)\b\s*:?\s*(.+)$",
        text,
        flags=re.IGNORECASE,
    )
    value = match.group(1) if match else _strip_decision_prefix(text)
    value = re.sub(r"^\s*(?:decision\s+)?[0-9a-f]{6,32}\s*:?\s*", "", value, flags=re.IGNORECASE)
    return _clean_spaces(value)


def _decision_index(text: str) -> int | None:
    match = re.search(
        r"\b(?:close|complete|finish|resolve|mark)\s+(?:the\s+)?decision\s+#?\s*(\d+)\b",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        match = re.search(
            r"\bdecision\s+#?\s*(\d+)\b.*\bdone\b",
            text,
            flags=re.IGNORECASE,
        )
    if match is None:
        return None
    try:
        index = int(match.group(1))
    except ValueError:
        return None
    return index if index > 0 else None


def _closed_text(text: str) -> str:
    value = re.sub(
        r"^\s*(?:close|complete|finish|resolve|mark)\s+(?:the\s+)?decision\s+#?\s*\d+\s*(?:as|done|closed|complete|completed|resolved)?\s*[:.,-]?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"^\s*(?:decision\s+)?[0-9a-f]{6,32}\s*:?\s*", "", value, flags=re.IGNORECASE)
    return _clean_spaces(value) or "Done"


def _is_close_request(text: str) -> bool:
    return bool(
        re.search(
            r"\b(close|complete|finish|resolve|mark)\s+(?:the\s+)?decision\b",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(r"\bdecision\s+#?\s*\d+\b.*\bdone\b", text, flags=re.IGNORECASE)
    )


def _is_bulk_close_request(text: str) -> bool:
    return bool(
        re.search(
            r"\b(close|complete|finish|resolve|mark)\s+(?:all|every)\s+(?:family\s+)?decisions\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def _is_list_request(text: str) -> bool:
    return bool(
        re.search(
            r"\b(list|show|tell|what|review|open|pending)\b",
            text,
            flags=re.IGNORECASE,
        )
        and re.search(
            r"\b(?:decisions?|open decisions?|pending decisions?)\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def _is_brief_request(text: str) -> bool:
    return bool(
        re.search(
            rf"\b(?:decision\s+{BRIEF_WORD_RE}|{BRIEF_WORD_RE}\s+decision|summarize decision|review decision)\b",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(
            rf"^\s*(?:give|provide|send|show)\s+(?:me\s+)?(?:the\s+)?(?:latest\s+|open\s+)?(?:decision\s+)?{BRIEF_WORD_RE}\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def extract_intent(request: str, now: datetime | None = None) -> dict[str, Any]:
    reference = _default_now(now)
    text = normalize_decision_request_text(request)
    lowered = text.lower()
    decision_id = _decision_id(text)
    has_capture_prefix = _has_decision_capture_prefix(text)

    if _is_bulk_close_request(text):
        return {"intent": "bulk_record_decisions"}
    if _is_close_request(text):
        return {
            "intent": "record_decision",
            "decision_id": decision_id,
            "decision_index": _decision_index(text),
            "outcome": _closed_text(text),
        }
    if _is_list_request(text):
        return {"intent": "list_decisions", "status": None}
    if _is_brief_request(text):
        return {"intent": "decision_brief", "decision_id": decision_id}
    if not has_capture_prefix and (
        re.search(r"^\s*(?:add\s+)?options?\b", lowered)
        or OPTION_MARKER_RE.search(text)
    ):
        texts = _option_texts(text)
        return {
            "intent": "add_option",
            "decision_id": decision_id,
            "text": texts[0] if texts else "",
            "texts": texts,
        }
    if not has_capture_prefix and (
        re.search(r"\b(add(?:ed)?\s+)?(evidence|research|notes?)\b", lowered)
        or re.search(r"^\s*(?:challenges?|concerns?|risks?)\b", lowered)
    ):
        texts = _evidence_texts(text)
        return {
            "intent": "add_evidence",
            "decision_id": decision_id,
            "text": texts[0] if texts else "",
            "texts": texts,
        }
    if re.search(r"\b(add\s+)?(next\s+steps?|todo)\b", lowered):
        return {
            "intent": "add_next_step",
            "decision_id": decision_id,
            "text": _next_step_text(text),
            "owner": _owner(text),
            "due": _due_date(text, reference),
        }
    if re.search(r"\b(decision is|we decided|we chose|we choose|final(?:ly)? decided)\b", lowered):
        return {
            "intent": "record_decision",
            "decision_id": decision_id,
            "outcome": _decided_text(text),
        }

    detail_free_text = _strip_detail_segments(text)
    details = _initial_details(text)
    base_decision_text = _strip_decision_prefix(detail_free_text)
    title, context = _title_and_context(base_decision_text)
    missing = []
    owner = _owner(text)
    due = _due_date(text, reference)
    if owner == "unknown":
        missing.append("owner")
    if due is None and _urgency(text) in {"high", "critical"}:
        missing.append("timeline")

    return {
        "intent": "create_decision",
        "title": title,
        "context": context,
        "status": _status(text),
        "owner": owner,
        "urgency": _urgency(text),
        "size": _size(text),
        "due": due,
        "initial_options": details["options"],
        "initial_evidence": details["evidence"],
        "missing_fields": missing,
        "assistant_help_needed": bool(
            re.search(r"\b(ai|assistant|noah|research|look up|compare|options?)\b", lowered)
        ),
    }

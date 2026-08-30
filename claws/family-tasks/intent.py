from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta
import json
import re
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_TIMEZONE = "America/Los_Angeles"
METADATA_MARKER = "N4OS_METADATA:"
HASHTAG_RE = re.compile(r"(?<![\w/])#(?P<tag>[A-Za-z][A-Za-z0-9_-]*)")
TAG_ANNOTATION_RE = re.compile(
    r"\b(?:with|and)\s+(?:tag|label)\b\s*:?\s*(?P<single>#?[A-Za-z][A-Za-z0-9_-]*)\b|"
    r"\b(?:tag|label)\b\s*:\s*(?P<label_single>#?[A-Za-z][A-Za-z0-9_-]*)\b|"
    r"\b(?:with|and)\s+(?:tags|labels)\b\s+(?P<many_hash>#[A-Za-z][A-Za-z0-9_-]*"
    r"(?:[\s,]+#[A-Za-z][A-Za-z0-9_-]*)*)|"
    r"\b(?:tags|labels)\b\s*:\s*(?P<many>#?[A-Za-z][A-Za-z0-9_-]*"
    r"(?:[\s,]+#?[A-Za-z][A-Za-z0-9_-]*)*)",
    re.IGNORECASE,
)
TAG_FILTER_CLAUSE_RE = re.compile(
    r"\b(?:with\s+)?tags?\s*(?:(?:is|are|with)\s+|:\s*|\s+)"
    r"(?P<tags>#?[A-Za-z][A-Za-z0-9_-]*"
    r"(?:\s*,\s*#?[A-Za-z][A-Za-z0-9_-]*)*)",
    re.IGNORECASE,
)
LIST_FOR_TAG_RE = re.compile(
    r"\b(?P<head>(?:tasks?|todos?|to-dos?|open loops?)(?:\s+all)?)"
    r"\s+for\s+(?P<tag>#?[A-Za-z][A-Za-z0-9_-]*)\b",
    re.IGNORECASE,
)

VALID_LEVELS = {"low", "medium", "high", "unknown"}
VALID_CONTEXTS = {"home", "car", "computer", "phone", "outside", "errand"}
VALID_EFFORT_TYPES = {
    "physical",
    "cognitive",
    "communication",
    "errand",
    "paperwork",
    "research",
    "admin",
    "unknown",
}
VALID_REQUIREMENTS = {
    "computer",
    "phone",
    "car",
    "internet",
    "paperwork",
    "equipment",
    "quiet",
    "focus",
}
VALID_CAN_DO_WHILE = {
    "driving",
    "commuting",
    "walking",
    "waiting",
    "watching_kids",
}
VALID_LOCATIONS = {
    "home",
    "outside",
    "anywhere",
    "specific",
    "unknown",
}
VALID_OWNERS = {"dad", "mom", "both", "grandmom", "nysha", "navya", "unknown"}
OWNER_ALIASES = {
    "dad": "dad",
    "father": "dad",
    "nimesh": "dad",
    "namesh": "dad",
    "papa": "dad",
    "papu": "dad",
    "mom": "mom",
    "mother": "mom",
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
    "nysha": "nysha",
    "navya": "navya",
    "unknown": "unknown",
}
OWNER_ALIAS_PATTERN = "|".join(
    re.escape(value)
    for value in sorted(OWNER_ALIASES, key=len, reverse=True)
)
OWNER_FILTER_RE = re.compile(
    rf"\b(?:with\s+)?owner\s*(?:(?:is|=)\s*|:\s*|\s+)"
    rf"(?P<owner>{OWNER_ALIAS_PATTERN})\b|"
    rf"\b(?:owned\s+by|assigned\s+to)\s+"
    rf"(?P<assigned_owner>{OWNER_ALIAS_PATTERN})\b",
    re.IGNORECASE,
)
OWNER_TASK_LIST_RE = re.compile(
    rf"\b(?P<owner_task>{OWNER_ALIAS_PATTERN})\s+"
    r"(?:tasks?|todos?|to-dos?|open loops?)\b|"
    r"\b(?:tasks?|todos?|to-dos?|open loops?)\s+for\s+"
    rf"(?P<for_owner>{OWNER_ALIAS_PATTERN})\b",
    re.IGNORECASE,
)
LEGACY_MODE_TO_EFFORT_TYPE = {
    "call": "communication",
    "research": "research",
    "physical": "physical",
    "errand": "errand",
    "computer": "admin",
    "home": "physical",
    "unknown": "unknown",
}

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
WEEKDAY_PATTERN = "|".join(
    re.escape(value)
    for value in sorted(WEEKDAYS, key=len, reverse=True)
)
MONTHS = {}
for month_names in (calendar.month_name, calendar.month_abbr):
    MONTHS.update(
        {
            value.lower(): index
            for index, value in enumerate(month_names)
            if value
        },
    )
MONTHS["sept"] = 9
MONTH_PATTERN = "|".join(
    re.escape(value)
    for value in sorted(MONTHS, key=len, reverse=True)
)
DAY_ORDINALS = {
    value: index + 1
    for index, value in enumerate(
        (
            "first second third fourth fifth sixth seventh eighth ninth tenth "
            "eleventh twelfth thirteenth fourteenth fifteenth sixteenth "
            "seventeenth eighteenth nineteenth"
        ).split(),
    )
}
DAY_TENS = {"twentieth": 20, "thirtieth": 30}
DAY_TENS_PREFIXES = {"twenty": 20, "thirty": 30}
RELATIVE_DATE_NUMBERS = {
    "a": 1,
    "an": 1,
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
}
RELATIVE_DATE_PATTERN = (
    r"(?P<count>\d{1,2}|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    r"\s+(?P<unit>days?|weeks?)"
)
RELATIVE_DATE_TEXT_PATTERN = (
    r"(?:\d{1,2}|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    r"\s+(?:days?|weeks?)"
)
DAY_ONES_PATTERN = "|".join(
    re.escape(value)
    for value in sorted(DAY_ORDINALS, key=len, reverse=True)
    if DAY_ORDINALS[value] < 10
)
DAY_WORD_PATTERN = "|".join(
    [
        *(
            re.escape(value)
            for value in sorted(
                [*DAY_ORDINALS, *DAY_TENS],
                key=len,
                reverse=True,
            )
        ),
        rf"(?:twenty|thirty)[ -](?:{DAY_ONES_PATTERN})",
    ]
)
MONTH_DAY_PATTERN = (
    rf"(?:{MONTH_PATTERN})\s+"
    rf"(?:\d{{1,2}}(?:st|nd|rd|th)?|{DAY_WORD_PATTERN})"
)

DEFAULT_METADATA = {
    "tags": [],
    "context": [],
    "energy": "unknown",
    "duration_minutes": None,
    "urgency": "unknown",
    "complexity": "unknown",
    "effort_type": "unknown",
    "requires": [],
    "can_do_while": [],
    "location": "unknown",
    "owner": "unknown",
    "assistant_help_needed": False,
    "assistant_name": "",
    "assistant_help_request": "",
    "assistant_context": "",
}


TASK_COMMAND_ACTIONS = {
    "create_task",
    "complete_task",
    "delete_task",
    "update_task",
    "recommend_tasks",
    "run_assistant_help",
}
TASK_PREFIX_RE = re.compile(
    r"^\s*/?(?:tasks?|todos?|to-dos?)(?:@[A-Za-z0-9_]+)?(?:\s+|:\s*|$)",
    re.IGNORECASE,
)
TASK_READ_ACTION_RE = re.compile(
    r"^\s*(?:show|list|what|which|recommend|give)\b",
    re.IGNORECASE,
)
TASK_COMPLETE_ACTION_RE = re.compile(
    r"^\s*(?:complete|completed|finish|finished|done|"
    r"mark\s+(?:(?:task|todo|to-do)\s+)?(?:done\s+)?)\b",
    re.IGNORECASE,
)
TASK_DELETE_ACTION_RE = re.compile(
    r"^\s*(?:delete|remove)\b",
    re.IGNORECASE,
)
TASK_UPDATE_ACTION_RE = re.compile(
    r"^\s*(?:update|change|assign|set|make|put)\b",
    re.IGNORECASE,
)
TASK_HELP_ACTION_RE = re.compile(
    r"^\s*(?:help(?:\s+(?:how|with|on|for|what|commands?|\?))?|"
    r"how\s+do\s+i|how\s+to|what\s+command|commands?\b|\?)",
    re.IGNORECASE,
)
TASK_AS_TASK_RE = re.compile(
    r"\b(?:as|into)\s+(?:a\s+)?(?:task|todo|to-do|open loop)\b",
    re.IGNORECASE,
)

HOUSEHOLD_PHYSICAL_WORDS = (
    "change",
    "fix",
    "install",
    "organize",
    "filter",
    "trash",
    "laundry",
    "dishwasher",
    "garage",
    "clean",
    "repair",
    "water",
)

COMMUNICATION_WORDS = ("call", "text", "email", "message", "phone")
RESEARCH_WORDS = ("research", "look up", "lookup", "compare", "find")
PAPERWORK_WORDS = (
    "fill",
    "form",
    "forms",
    "visa",
    "passport",
    "paperwork",
    "application",
)
ADMIN_WORDS = ("book", "schedule", "reserve", "pay", "renew", "order")
ERRAND_WORDS = (
    "errand",
    "errands",
    "grocery",
    "groceries",
    "shopping",
    "store",
    "pickup",
    "pick up",
    "drop off",
    "dropoff",
)
TASK_ACTION_WORDS = (
    "pack",
    "cancel",
    "downgrade",
    "bring",
    "buy",
    "get",
    "return",
    "put",
    "take",
    "call",
    "text",
    "email",
    "message",
    "clean",
    "repair",
    "fix",
    "change",
    "replace",
    "research",
    "book",
    "order",
    "renew",
    "submit",
    "fill",
    "prepare",
    "send",
    "drop off",
    "pick up",
    "pickup",
)
TASK_ACTION_PATTERN = "|".join(
    re.escape(word).replace(r"\ ", r"\s+")
    for word in TASK_ACTION_WORDS
)
CREATE_TASK_START_RE = re.compile(
    rf"^\s*(?:to\s+)?(?:{TASK_ACTION_PATTERN})\b",
    re.IGNORECASE,
)
CREATE_TASK_REQUEST_RE = re.compile(
    r"^\s*(?:(?:/tasks?|tasks?)\s+)?(?:please\s+)?"
    r"(?:(?:(?:i|we)\s+)?(?:want|need|would\s+like)\s+to\s+)?"
    r"(?:add|create|capture|remember)\s+(?:an?\s+)?"
    r"(?:(?:task|todo|to-do|open loop)\b|"
    r"(?:tasks|todos|to-dos|open loops)\b(?=\s+to\b))",
    re.IGNORECASE,
)
CREATE_TASK_TRANSCRIPTION_RE = re.compile(
    r"^\s*and\s+(?=(?:an?\s+)?(?:task|todo|to-do|open loop)\b)",
    re.IGNORECASE,
)
LEADING_DUE_ACTION_RE = re.compile(
    rf"^\s*(?:for|on)\s+"
    r"(?:today|tomorrow|tonight|this\s+weekend|weekend|"
    r"(?:next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+next\s+week)"
    r"(?:\s+(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?))?"
    rf"\s+to\s+(?=(?:{TASK_ACTION_PATTERN})\b)",
    re.IGNORECASE,
)
EXPLICIT_CLOCK_TIME_RE = re.compile(
    r"\b(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\b|"
    r"\bat\s+\d{1,2}(?::\d{2})?\b",
    re.IGNORECASE,
)
FOLLOWUP_BLOB_RE = re.compile(
    r"^\s*(?:for\s+)?(?:below|following|this|the)\s+"
    r"(?P<object>email|message|text|note)\s+to\s+follow[- ]?up"
    r"(?P<timing>.*?)(?:[.:\n]\s+)(?P<body>.+)$",
    re.IGNORECASE | re.DOTALL,
)
TASK_OWNER_NOTE_RE = re.compile(
    r"\b(?:this\s+)?task\s+(?:is\s+)?for\s+[\w'.-]+\.?.*$",
    re.IGNORECASE,
)
TASK_OWNER_ANNOTATION_RE = re.compile(
    rf"\b(?:owner|owned\s+by|assign(?:ed)?(?:\s+the)?\s+task\s+to|"
    rf"assign(?:ed)?\s+to|(?:this\s+)?task\s+(?:is\s+)?for)"
    rf"(?:\s+(?:is|to|as)|\s*:)?\s*"
    rf"(?P<owner>{OWNER_ALIAS_PATTERN})\b\.?",
    re.IGNORECASE,
)
DUE_DATE_ANNOTATION_RE = re.compile(
    rf"\b(?:do\s+it\s+by|by|due)\s+{MONTH_DAY_PATTERN}\b\.?",
    re.IGNORECASE,
)
ASSISTANT_NAMES = ("Noah",)
ASSISTANT_NAME_PATTERN = "|".join(re.escape(name) for name in ASSISTANT_NAMES)
ASSISTANT_HELP_MARKER_RE = re.compile(
    rf"\b(?:(?:i\s+)?(?:want|need|could\s+use)\s+(?:an?\s+)?ai\s+assistant"
    rf"(?:\s+(?:help|support))?|ask\s+(?:{ASSISTANT_NAME_PATTERN})\s+"
    rf"(?:to\s+help|for\s+help)|(?:i\s+)?(?:want|need|would\s+like)\s+"
    rf"(?:{ASSISTANT_NAME_PATTERN})\s+to|(?:{ASSISTANT_NAME_PATTERN})\s*,?\s+help)\b",
    re.IGNORECASE,
)
ASSISTANT_HELP_MARKER_LINE_RE = re.compile(
    rf"^\s*(?:(?:i\s+)?(?:want|need|could\s+use)\s+(?:an?\s+)?ai\s+assistant"
    rf"(?:\s+(?:help|support))?|ask\s+(?:{ASSISTANT_NAME_PATTERN})\s+"
    rf"(?:to\s+help|for\s+help)|(?:i\s+)?(?:want|need|would\s+like)\s+"
    rf"(?:{ASSISTANT_NAME_PATTERN})\s+to\s+help|(?:{ASSISTANT_NAME_PATTERN})\s*,?\s+help)\.?\s*$",
    re.IGNORECASE,
)
ASSISTANT_DETAIL_LABEL_RE = re.compile(
    r"\b(?P<label>assistant\s+help|help|assistant\s+context|context|email|notes?)\s*:\s*",
    re.IGNORECASE,
)
TASK_DETAIL_LABEL_RE = re.compile(
    r"^\s*(?P<label>body|details?|notes?)\s*:\s*(?P<value>.*)$",
    re.IGNORECASE,
)
TASK_TITLE_LABEL_RE = re.compile(
    r"^\s*(?P<label>header|title)\s*:\s*(?P<value>.*)$",
    re.IGNORECASE,
)
INLINE_TASK_LABEL_RE = re.compile(
    r"\s+\b(?P<label>header|title|body|details?|notes?)\s*:",
    re.IGNORECASE,
)
ASSISTANT_GOAL_CONTEXT_RE = re.compile(
    r"\b(?:i\s+really\s+want|goal\s+is|the\s+goal\s+is)\b",
    re.IGNORECASE,
)
RUN_ASSISTANT_HELP_RE = re.compile(
    rf"\b(?:run|process|work|check|do|complete)\b.*"
    rf"\b(?:{ASSISTANT_NAME_PATTERN}|assistant(?:\s+help)?)\b.*"
    r"\b(?:tasks?|queue|research|help)\b|"
    rf"\b(?:{ASSISTANT_NAME_PATTERN}|assistant)\b.*"
    r"\b(?:run|process|work|check|research)\b",
    re.IGNORECASE,
)


def _default_now(now: datetime | None) -> datetime:
    if now is not None:
        if now.tzinfo is None:
            return now.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        return now

    return datetime.now(ZoneInfo(DEFAULT_TIMEZONE))


def _clean_spaces(value: str) -> str:
    return " ".join(value.split()).strip()


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
        goal_match = ASSISTANT_GOAL_CONTEXT_RE.search(value)
        if goal_match is None:
            return _normalize_assistant_help_request(value), ""

        help_request = _normalize_assistant_help_request(value[: goal_match.start()])
        assistant_context = _clean_spaces(value[goal_match.start() :].strip(" ."))
        return help_request, assistant_context

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


def _assistant_note_text(help_request: str, assistant_context: str) -> str | None:
    lines = ["Assistant help: " + (help_request or "requested")]
    if assistant_context:
        lines.append(f"Assistant context: {assistant_context}")
    return "\n".join(lines)


def _join_note_sections(*sections: str | None) -> str | None:
    cleaned = [section.strip() for section in sections if section and section.strip()]
    if not cleaned:
        return None
    return "\n\n".join(cleaned)


def _extract_task_detail_notes(user_text: str) -> tuple[str, str | None]:
    labeled_text = INLINE_TASK_LABEL_RE.sub(
        lambda match: f"\n{match.group('label')}:",
        user_text,
    )
    labeled_text = re.sub(
        r"\b(?P<label>details?|notes?|body)\s+"
        r"(?=(?:add|bring|buy|call|check|find|get|order|pick|prepare|visit)\b)",
        lambda match: f"\n{match.group('label')}: ",
        labeled_text,
        flags=re.IGNORECASE,
    )
    lines = [line.strip() for line in labeled_text.splitlines()]
    title_parts: list[str] = []
    leading_parts: list[str] = []
    note_parts: list[str] = []
    current_section: str | None = None

    for line in lines:
        if not line:
            continue

        title_match = TASK_TITLE_LABEL_RE.match(line)
        if title_match is not None:
            value = _clean_spaces(title_match.group("value").strip(" ."))
            if value:
                title_parts.append(value)
            current_section = "title"
            continue

        detail_match = TASK_DETAIL_LABEL_RE.match(line)
        if detail_match is not None:
            value = _clean_spaces(detail_match.group("value").strip())
            if value:
                note_parts.append(value)
            current_section = "notes"
            continue

        if TASK_OWNER_ANNOTATION_RE.fullmatch(line):
            continue

        if current_section == "notes":
            note_parts.append(_clean_spaces(line))
        elif current_section == "title":
            title_parts.append(_clean_spaces(line))
        else:
            leading_parts.append(_clean_spaces(line))

    if not note_parts:
        return user_text, None

    title_text = " ".join(title_parts or leading_parts).strip()
    notes = "\n".join(note_parts).strip()
    notes = re.sub(
        r"\s+\btime\s*:?\s*\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?\b"
        r"(?=\s*(?:\n|$))",
        "",
        notes,
        flags=re.IGNORECASE,
    ).strip()
    notes = TASK_OWNER_ANNOTATION_RE.sub("", notes).strip()
    if notes:
        notes = notes[:1].upper() + notes[1:]
    return title_text, notes or None


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
        _assistant_note_text(help_request, assistant_context),
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
        _assistant_note_text(help_request, assistant_context),
    )


def _clean_list(values: Any, allowed: set[str] | None = None) -> list[str]:
    if not isinstance(values, list):
        return []

    cleaned = []
    seen = set()
    for value in values:
        normalized = str(value).strip().lower()
        aliases: dict[str, str] = {}
        if allowed == VALID_CONTEXTS:
            aliases = {
                "driving": "car",
                "commute": "car",
                "commuting": "car",
                "laptop": "computer",
                "online": "computer",
                "call": "phone",
            }
        elif allowed == VALID_REQUIREMENTS:
            aliases = {
                "laptop": "computer",
                "online": "internet",
                "document": "paperwork",
                "documents": "paperwork",
            }
        elif allowed == VALID_CAN_DO_WHILE:
            aliases = {
                "drive": "driving",
                "car": "driving",
                "commute": "commuting",
            }
        normalized = aliases.get(normalized, normalized)
        if allowed is not None and normalized not in allowed:
            continue
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    return cleaned


def normalize_tags(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []

    cleaned = []
    seen = set()
    for value in values:
        normalized = str(value or "").strip().lower().lstrip("#")
        normalized = re.sub(r"[^a-z0-9_-]+", "-", normalized).strip("-_")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    return cleaned


def extract_tags(value: str | None) -> list[str]:
    if not value:
        return []
    tags = [match.group("tag") for match in HASHTAG_RE.finditer(value)]
    for match in TAG_ANNOTATION_RE.finditer(value):
        captured = (
            match.group("single")
            or match.group("label_single")
            or match.group("many_hash")
            or match.group("many")
            or ""
        )
        tags.extend(re.split(r"[\s,]+", captured))
    return normalize_tags(tags)


def _extract_tag_filter_text(user_text: str) -> tuple[list[str], str]:
    tags = extract_tags(user_text)
    reserved_for_tag_words = {
        "today",
        "tomorrow",
        "tonight",
        "week",
        "weekend",
        *WEEKDAYS,
    }

    def strip_clause(match: re.Match[str]) -> str:
        candidates = re.split(r"\s*,\s*", match.group("tags"))
        tags.extend(candidates)
        return " "

    semantic_text = TAG_FILTER_CLAUSE_RE.sub(strip_clause, user_text)

    def strip_list_for_tag(match: re.Match[str]) -> str:
        tag = normalize_tags([match.group("tag")])
        if not tag or tag[0] in reserved_for_tag_words:
            return match.group(0)
        tags.extend(tag)
        return match.group("head")

    semantic_text = LIST_FOR_TAG_RE.sub(strip_list_for_tag, semantic_text)
    return normalize_tags(tags), semantic_text


def _extract_owner_filter_text(user_text: str) -> tuple[str | None, str]:
    owner: str | None = None

    def keep_first_owner(candidate: str) -> str:
        nonlocal owner
        normalized = _owner_from_alias(candidate)
        if normalized != "unknown" and owner is None:
            owner = normalized
            return " "
        return ""

    def strip_clause(match: re.Match[str]) -> str:
        candidate = match.group("owner") or match.group("assigned_owner") or ""
        if keep_first_owner(candidate):
            return " "
        return match.group(0)

    semantic_text = OWNER_FILTER_RE.sub(strip_clause, user_text)

    def strip_task_list_clause(match: re.Match[str]) -> str:
        candidate = match.group("owner_task") or match.group("for_owner") or ""
        if keep_first_owner(candidate):
            return " tasks "
        return match.group(0)

    semantic_text = OWNER_TASK_LIST_RE.sub(strip_task_list_clause, semantic_text)
    return owner, semantic_text


def _clean_level(value: Any) -> str:
    normalized = str(value or "unknown").strip().lower()
    return normalized if normalized in VALID_LEVELS else "unknown"


def _clean_choice(value: Any, allowed: set[str]) -> str:
    normalized = str(value or "unknown").strip().lower()
    return normalized if normalized in allowed else "unknown"


def _clean_owner(value: Any) -> str:
    normalized = str(value or "unknown").strip().lower()
    return normalized if normalized in VALID_OWNERS else "unknown"


def _clean_duration(value: Any) -> int | None:
    if value is None:
        return None

    try:
        duration = int(value)
    except (TypeError, ValueError):
        return None

    return duration if duration > 0 else None


def _owner_from_alias(value: str) -> str:
    return OWNER_ALIASES.get(_clean_spaces(value).lower(), "unknown")


def _day_from_month_day_text(value: str) -> int | None:
    numeric_day = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?", value)
    if numeric_day is not None:
        return int(numeric_day.group(1))

    normalized = value.replace("-", " ")
    if normalized in DAY_ORDINALS:
        return DAY_ORDINALS[normalized]
    if normalized in DAY_TENS:
        return DAY_TENS[normalized]

    if " " not in normalized:
        return None

    prefix, suffix = normalized.split(" ", 1)
    tens = DAY_TENS_PREFIXES.get(prefix)
    ones = DAY_ORDINALS.get(suffix)
    if tens is None or ones is None or ones >= 10:
        return None
    return tens + ones


def _relative_date_count(value: str) -> int | None:
    normalized = value.strip().lower()
    if normalized.isdigit():
        count = int(normalized)
    else:
        count = RELATIVE_DATE_NUMBERS.get(normalized)
    if count is None or count <= 0:
        return None
    return count


def _extract_relative_due_date(user_text: str, reference: datetime) -> tuple[str | None, str]:
    patterns = [
        rf"\bin\s+{RELATIVE_DATE_PATTERN}\b",
        rf"\b{RELATIVE_DATE_PATTERN}\s+from\s+now\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, user_text, flags=re.IGNORECASE)
        if match is None:
            continue
        count = _relative_date_count(match.group("count"))
        if count is None:
            continue
        unit = match.group("unit").lower()
        days = count * 7 if unit.startswith("week") else count
        return (reference + timedelta(days=days)).date().isoformat(), match.group(0)
    return None, ""


def _extract_counted_weekday_due_date(
    user_text: str,
    reference: datetime,
) -> tuple[str | None, str]:
    match = re.search(
        rf"\b(?:after|in)\s+"
        rf"(?P<count>\d{{1,2}}|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
        rf"\s+(?P<weekday>{WEEKDAY_PATTERN})(?:s|'s)?\b",
        user_text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None, ""
    count = _relative_date_count(match.group("count"))
    if count is None:
        return None, ""
    weekday = WEEKDAYS[match.group("weekday").lower()]
    days_until = (weekday - reference.weekday()) % 7
    if days_until == 0:
        days_until = 7
    days = days_until + ((count - 1) * 7)
    return (reference + timedelta(days=days)).date().isoformat(), match.group(0)


def _contains_any_word(user_text: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(word)}\b", user_text) for word in words)


def _default_metadata() -> dict[str, Any]:
    return dict(DEFAULT_METADATA)


def normalize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    normalized = _default_metadata()
    if isinstance(metadata, dict):
        normalized.update(metadata)

    legacy_mode = str(normalized.pop("mode", "") or "").strip().lower()
    if normalized.get("effort_type") in (None, "", "unknown") and legacy_mode:
        normalized["effort_type"] = LEGACY_MODE_TO_EFFORT_TYPE.get(
            legacy_mode,
            "unknown",
        )

    normalized["context"] = _clean_list(normalized.get("context"), VALID_CONTEXTS)
    normalized["tags"] = normalize_tags(normalized.get("tags"))
    normalized["energy"] = _clean_level(normalized.get("energy"))
    normalized["duration_minutes"] = _clean_duration(
        normalized.get("duration_minutes"),
    )
    normalized["urgency"] = _clean_level(normalized.get("urgency"))
    normalized["complexity"] = _clean_level(normalized.get("complexity"))
    normalized["effort_type"] = _clean_choice(
        normalized.get("effort_type"),
        VALID_EFFORT_TYPES,
    )
    normalized["requires"] = _clean_list(
        normalized.get("requires"),
        VALID_REQUIREMENTS,
    )
    normalized["can_do_while"] = _clean_list(
        normalized.get("can_do_while"),
        VALID_CAN_DO_WHILE,
    )
    normalized["location"] = _clean_choice(normalized.get("location"), VALID_LOCATIONS)
    normalized["owner"] = _clean_owner(normalized.get("owner"))
    normalized["assistant_name"] = _clean_assistant_text(
        normalized.get("assistant_name"),
    )
    normalized["assistant_help_request"] = _clean_assistant_text(
        normalized.get("assistant_help_request"),
    )
    normalized["assistant_context"] = _clean_assistant_text(
        normalized.get("assistant_context"),
    )
    assistant_help_status = _clean_assistant_text(
        normalized.get("assistant_help_status"),
    ).lower()
    if assistant_help_status:
        normalized["assistant_help_status"] = assistant_help_status
    normalized["assistant_help_needed"] = bool(
        assistant_help_status != "completed"
        and (
            normalized.get("assistant_help_needed")
            or normalized["assistant_help_request"]
            or normalized["assistant_context"]
        ),
    )
    if normalized["assistant_help_needed"] and not normalized["assistant_name"]:
        normalized["assistant_name"] = ASSISTANT_NAMES[0]
    return normalized


def read_metadata_from_notes(notes: str | None) -> tuple[str, dict[str, Any]]:
    if not notes:
        return "", _default_metadata()

    marker_index = notes.find(METADATA_MARKER)
    if marker_index < 0:
        return notes.strip(), _default_metadata()

    human_notes = notes[:marker_index].strip()
    raw_metadata = notes[marker_index + len(METADATA_MARKER) :].strip()
    try:
        parsed = json.loads(raw_metadata)
    except json.JSONDecodeError:
        parsed = {}

    if not isinstance(parsed, dict):
        parsed = {}

    return human_notes, normalize_metadata(parsed)


def write_metadata_to_notes(
    notes: str | None,
    metadata: dict[str, Any] | None,
) -> str:
    human_notes, _ = read_metadata_from_notes(notes)
    metadata_json = json.dumps(normalize_metadata(metadata), indent=2)
    if human_notes:
        return f"{human_notes}\n\n{METADATA_MARKER}\n{metadata_json}"

    return f"{METADATA_MARKER}\n{metadata_json}"


def write_human_notes(notes: str | None) -> str | None:
    human_notes, _ = read_metadata_from_notes(notes)
    return human_notes or None


def _current_or_next_weekday(reference: datetime, weekday: int) -> date:
    return (reference + timedelta(days=(weekday - reference.weekday()) % 7)).date()


def _weekday_in_next_calendar_week(reference: datetime, weekday: int) -> date:
    days_until_next_monday = 7 - reference.weekday()
    next_monday = reference + timedelta(days=days_until_next_monday)
    return (next_monday + timedelta(days=weekday)).date()


def _week_end(reference: datetime) -> date:
    return (reference + timedelta(days=6 - reference.weekday())).date()


def _extract_due_date(user_text: str, reference: datetime) -> tuple[str | None, str]:
    lowered = user_text.lower()
    if "tomorrow" in lowered:
        return (reference + timedelta(days=1)).date().isoformat(), "tomorrow"
    if "today" in lowered:
        return reference.date().isoformat(), "today"
    if "this weekend" in lowered or re.search(r"\bweekend\b", lowered):
        return _current_or_next_weekday(reference, WEEKDAYS["saturday"]).isoformat(), "this weekend"

    relative_due, relative_anchor = _extract_relative_due_date(user_text, reference)
    if relative_due is not None:
        return relative_due, relative_anchor

    counted_weekday_due, counted_weekday_anchor = _extract_counted_weekday_due_date(
        user_text,
        reference,
    )
    if counted_weekday_due is not None:
        return counted_weekday_due, counted_weekday_anchor

    for name, weekday in WEEKDAYS.items():
        if (
            re.search(rf"\bnext\s+{name}\b", lowered)
            or re.search(rf"\b{name}\s+next week\b", lowered)
            or re.search(
            rf"\bnext week\s+{name}\b",
            lowered,
            )
        ):
            return _weekday_in_next_calendar_week(reference, weekday).isoformat(), name
        if re.search(rf"\b(?:due\s+|on\s+)?{name}\b", lowered):
            return _current_or_next_weekday(reference, weekday).isoformat(), name

    match = re.search(r"\bdue\s+(\d{4}-\d{2}-\d{2})\b", lowered)
    if match is not None:
        return match.group(1), "due date"

    month_day = re.search(
        rf"\b(?:by|due|on|do\s+it\s+by)?\s*"
        rf"(?P<month>{MONTH_PATTERN})\s+"
        rf"(?P<day>\d{{1,2}}(?:st|nd|rd|th)?|{DAY_WORD_PATTERN})\b",
        lowered,
    )
    if month_day is not None:
        month = MONTHS[month_day.group("month")]
        day = _day_from_month_day_text(month_day.group("day"))
        if day is None:
            return None, ""
        try:
            due_date = date(reference.year, month, day)
        except ValueError:
            return None, ""
        if due_date < reference.date():
            due_date = date(reference.year + 1, month, day)
        return due_date.isoformat(), "due date"

    return None, ""


def _extract_duration_minutes(user_text: str) -> int | None:
    match = re.search(
        r"\b(?:for|takes?|under|within|in|have)\s+(\d+)\s*(minutes?|mins?|hours?|hrs?)\b",
        user_text,
        flags=re.IGNORECASE,
    )
    if match is None:
        match = re.search(
            r"\b(\d+)\s*(minutes?|mins?|hours?|hrs?)\b",
            user_text,
            flags=re.IGNORECASE,
        )
    if match is None:
        return None

    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith(("hour", "hr")):
        return amount * 60
    return amount


def _extract_level(user_text: str, field: str) -> str | None:
    lowered = user_text.lower()
    for level in ("low", "medium", "high"):
        if re.search(rf"\b{level}\s+{field}\b", lowered) or re.search(
            rf"\b{field}\s+{level}\b",
            lowered,
        ):
            return level
    return None


def _extract_contexts(user_text: str) -> tuple[list[str], list[str], list[str], str]:
    lowered = user_text.lower()
    contexts: list[str] = []
    can_do_while: list[str] = []
    requirements: list[str] = []
    location = "unknown"

    def add_context(value: str) -> None:
        if value not in contexts:
            contexts.append(value)

    def add_can_do(value: str) -> None:
        if value not in can_do_while:
            can_do_while.append(value)

    def add_requirement(value: str) -> None:
        if value not in requirements:
            requirements.append(value)

    if re.search(r"\b(?:commute|commuting)\b", lowered):
        add_context("car")
        add_context("phone")
        add_can_do("commuting")
        add_requirement("phone")
    if re.search(r"\b(?:driving|drive|car)\b", lowered):
        add_context("car")
        add_context("phone")
        add_can_do("driving")
        add_requirement("phone")
    if re.search(r"\b(?:home|house)\b", lowered):
        add_context("home")
        location = "home"
    if re.search(r"\b(?:laptop|computer|online)\b", lowered):
        add_context("computer")
        add_requirement("computer")
        if re.search(r"\b(?:online|internet|web)\b", lowered):
            add_requirement("internet")
    if re.search(r"\bquiet\b", lowered):
        add_requirement("quiet")
    if _contains_any_word(lowered, ERRAND_WORDS):
        add_context("errand")
        location = "outside"
    if re.search(r"\b(?:phone|call|text|message)\b", lowered):
        add_context("phone")
        add_requirement("phone")
    if re.search(r"\bemail\b", lowered):
        add_context("computer")
        add_requirement("computer")
        add_requirement("internet")
    if re.search(r"\b(?:outside|grocery|groceries|shopping|store)\b", lowered):
        add_context("outside")
        location = "outside"
    if re.search(r"\b(?:paperwork|forms?|documents?|application|visa|passport)\b", lowered):
        add_requirement("paperwork")
    if re.search(r"\b(?:focus|focused)\b", lowered):
        add_requirement("focus")

    return contexts, can_do_while, requirements, location


def _infer_effort_type(user_text: str) -> str:
    lowered = user_text.lower()
    if _contains_any_word(lowered, COMMUNICATION_WORDS):
        return "communication"
    if _contains_any_word(lowered, PAPERWORK_WORDS):
        return "paperwork"
    if _contains_any_word(lowered, RESEARCH_WORDS):
        return "research"
    if _contains_any_word(lowered, ADMIN_WORDS):
        return "admin"
    if _contains_any_word(lowered, ERRAND_WORDS):
        return "errand"
    if _contains_any_word(lowered, HOUSEHOLD_PHYSICAL_WORDS):
        return "physical"
    if re.search(r"\b(?:think|plan|write|learn|study)\b", lowered):
        return "cognitive"
    return "unknown"


def _infer_owner(user_text: str) -> str:
    lowered = user_text.lower()
    explicit_owner = TASK_OWNER_ANNOTATION_RE.search(lowered)
    if explicit_owner is not None:
        return _owner_from_alias(explicit_owner.group("owner"))

    if re.search(r"\b(?:dad|father|nimesh|namesh|papa|papu)\s+will\b", lowered) or re.search(
        r"\b(?:i|me)\s+(?:will|can|should|need to|have to)\b",
        lowered,
    ):
        return "dad"
    if re.search(r"\b(?:mom|mother|mum|mummy|niyati|niyaati|niyathi)\s+will\b", lowered):
        return "mom"
    if re.search(r"\b(?:grand\s*mom|dadi|tarla)\s+will\b", lowered):
        return "grandmom"
    if re.search(r"\b(?:both|we|us|parents)\s+(?:will|can|should|need to|have to)\b", lowered):
        return "both"
    if re.search(r"\bnysha\s+(?:will|can|should|need to|has to|have to)\b", lowered):
        return "nysha"
    if re.search(r"\bnavya\s+(?:will|can|should|need to|has to|have to)\b", lowered):
        return "navya"
    return "unknown"


def _add_communication_requirements(user_text: str, requirements: list[str]) -> None:
    lowered = user_text.lower()

    if re.search(r"\bemail\b", lowered):
        for requirement in ("computer", "internet"):
            if requirement not in requirements:
                requirements.append(requirement)
        return

    if "phone" not in requirements:
        requirements.append("phone")


def _infer_metadata(
    user_text: str,
    due: str | None,
) -> dict[str, Any]:
    metadata = _default_metadata()
    tags = extract_tags(user_text)
    semantic_text = HASHTAG_RE.sub("", user_text)
    context, can_do_while, requirements, location = _extract_contexts(semantic_text)
    effort_type = _infer_effort_type(semantic_text)
    energy = _extract_level(semantic_text, "energy")
    urgency = _extract_level(semantic_text, "urgency")
    complexity = _extract_level(semantic_text, "complexity")
    duration = _extract_duration_minutes(semantic_text)

    lowered = semantic_text.lower()
    if urgency is None and re.search(r"\b(?:urgent|asap|soon)\b", lowered):
        urgency = "high"

    if energy is None:
        if effort_type == "communication":
            energy = "low"
        elif effort_type in ("physical", "errand", "admin", "research"):
            energy = "medium"
        elif effort_type == "paperwork":
            energy = "high"

    if duration is None:
        if effort_type == "communication":
            duration = 20
        elif effort_type == "research":
            duration = 45
        elif effort_type == "admin":
            duration = 30
        elif effort_type == "paperwork":
            duration = 60
        elif effort_type == "physical":
            duration = 15

    if complexity is None:
        if effort_type in ("research", "admin"):
            complexity = "medium"
        elif effort_type == "paperwork":
            complexity = "high"
        elif effort_type in ("communication", "physical"):
            complexity = "low"

    if urgency is None and due is not None:
        urgency = "medium"

    if effort_type == "physical":
        if "home" not in context:
            context.append("home")
        if "equipment" not in requirements:
            requirements.append("equipment")
        if location == "unknown":
            location = "home"
    if effort_type == "communication":
        _add_communication_requirements(user_text, requirements)
    if effort_type in ("research", "admin") and "computer" not in requirements:
        requirements.append("computer")
    if effort_type in ("research", "admin") and "internet" not in requirements:
        requirements.append("internet")
    if effort_type == "research" and "focus" not in requirements:
        requirements.append("focus")
    if effort_type == "paperwork":
        for requirement in ("computer", "paperwork", "focus"):
            if requirement not in requirements:
                requirements.append(requirement)
    if effort_type == "errand" and "car" not in requirements:
        requirements.append("car")
    if effort_type == "communication":
        for value in ("driving", "commuting"):
            if value not in can_do_while:
                can_do_while.append(value)
        if "phone" not in context:
            context.append("phone")
        if location == "unknown":
            location = "anywhere"
    if effort_type == "errand" and location == "unknown":
        location = "outside"
    if effort_type in ("research", "admin", "paperwork") and location == "unknown":
        location = "anywhere"

    metadata.update(
        {
            "tags": tags,
            "context": context,
            "energy": energy or "unknown",
            "duration_minutes": duration,
            "urgency": urgency or "unknown",
            "complexity": complexity or "unknown",
            "effort_type": effort_type,
            "requires": requirements,
            "can_do_while": can_do_while,
            "location": location,
            "owner": _infer_owner(user_text),
        }
    )
    return normalize_metadata(metadata)


def _strip_create_words(user_text: str) -> str:
    cleaned = re.sub(
        r"^\s*(?:(?:/tasks?|tasks?)\s+)?(?:please\s+)?(?:"
        r"(?:(?:(?:i|we)\s+)?(?:want|need|would\s+like)\s+to\s+)?"
        r"(?:add|create|capture|remember)\s+(?:an?\s+)?"
        r"(?:(?:(?:task|todo|to-do|open loop)\b[:\s-]*(?:to\s+)?|"
        r"(?:tasks|todos|to-dos|open loops)\b[:\s-]*to\s+))?"
        r"|to\s+)",
        "",
        user_text,
        flags=re.IGNORECASE,
    ).strip()
    cleaned = re.sub(
        r"^\s*(?:i\s+had|there\s+is|this\s+is)\s+(?:a\s+)?"
        r"(?:task|todo|to-do|open\s+loop)\s*(?:for|to)?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return LEADING_DUE_ACTION_RE.sub("", cleaned, count=1).strip()


def _strip_task_annotations(title: str) -> str:
    cleaned = title
    shopping_match = re.search(
        r"^\s*go\s+to\s+(?P<place>.+?)\s+in\s+order\s+b(?:uy|y)\s+"
        r"(?:the\s+)?(?P<item>.+?)(?:,\s*for\s+(?P<purpose>.+?))?"
        r"(?:,\s*the\s+owner\s+is)?\s*$",
        cleaned,
        flags=re.IGNORECASE,
    )
    if shopping_match is not None:
        item = _clean_spaces(shopping_match.group("item"))
        item = re.sub(r"\s+for\s+everyone$", "", item, flags=re.IGNORECASE)
        purpose = _clean_spaces(shopping_match.group("purpose") or "")
        cleaned = f"buy {item}{f' for {purpose}' if purpose else ''}"
    cleaned = TASK_OWNER_ANNOTATION_RE.sub("", cleaned)
    cleaned = TASK_OWNER_NOTE_RE.sub("", cleaned)
    cleaned = DUE_DATE_ANNOTATION_RE.sub("", cleaned)
    cleaned = re.sub(
        r"^\s*(?:for|on|due)\s+"
        r"(?:today|tomorrow|tonight|this\s+weekend|weekend)"
        r"(?:\s+(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?))?"
        r"\s*[,.:-]\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^\s*(?:for|on|due)\s+"
        r"(?:(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
        r"(?:\s+next week)?|next\s+"
        r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))"
        r"\s*[,.:-]\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = TAG_ANNOTATION_RE.sub("", cleaned)
    cleaned = HASHTAG_RE.sub("", cleaned)
    cleaned = re.sub(
        r"\btime\s*:?\s*\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\s*,?\s*(?:the\s+)?owner\s*(?:is|:)?\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s*,\s*challenge\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        rf"(?:^|[\s,.])(?:check|review|revisit|remind(?:\s+me)?|follow\s+up)\s+"
        rf"(?:in\s+{RELATIVE_DATE_TEXT_PATTERN}|{RELATIVE_DATE_TEXT_PATTERN}\s+from\s+now)\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        rf"\b(?:in\s+{RELATIVE_DATE_TEXT_PATTERN}|{RELATIVE_DATE_TEXT_PATTERN}\s+from\s+now)\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        rf"\b(?:time\s*:?\s*)?(?:after|in)\s+"
        rf"(?:\d{{1,2}}|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
        rf"\s+(?:{WEEKDAY_PATTERN})(?:s|'s)?\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\bcall\s+up\b", "call", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\s+to\s+check\s+how\s+to\s+handle\s+with\s+(.+)$",
        r" about \1",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s*,\s*(?:needs?|requires?).*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:this\s+weekend|today|tomorrow|tonight)\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:this\s+is\s+)?over\s+the\s+weekend\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\b(?:due\s+|on\s+|for\s+)?"
        r"(?:(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
        r"(?:\s+next week)?|next\s+"
        r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(?:during|while|when)\s+(?:the\s+)?(?:commute|commuting|driving|drive|car)\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(?:for|takes?|under|within|in)\s+\d+\s*(?:minutes?|mins?|hours?|hrs?)\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b(?:low|medium|high)\s+(?:energy|urgency|complexity)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:urgent|asap)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip(" ,.-")
    return _clean_spaces(cleaned)


def _title_from_request(user_text: str) -> str | None:
    title = _strip_task_annotations(_strip_create_words(user_text))
    if not title:
        return None

    return title[:1].upper() + title[1:]


def _extract_create_intent(
    user_text: str,
    reference: datetime,
) -> dict[str, Any]:
    task_text, assistant_metadata, assistant_notes = _extract_assistant_help(user_text)
    metadata_text = task_text or user_text
    intent_text, detail_notes = _extract_task_detail_notes(metadata_text)
    due, _ = _extract_due_date(intent_text, reference)
    if due is None and intent_text != metadata_text:
        due, _ = _extract_due_date(metadata_text, reference)
    title = _title_from_request(intent_text)
    missing_fields = []
    if title is None:
        missing_fields.append("title")

    metadata = _infer_metadata(metadata_text, due)
    metadata.update(assistant_metadata)

    return {
        "intent": "create_task",
        "title": title,
        "notes": _join_note_sections(detail_notes, assistant_notes),
        "due": due,
        "metadata": normalize_metadata(metadata),
        "missing_fields": missing_fields,
    }


def _end_of_day(value: date, timezone: str = DEFAULT_TIMEZONE) -> str:
    return datetime.combine(value, time.max, tzinfo=ZoneInfo(timezone)).isoformat()


def _start_of_day(value: date, timezone: str = DEFAULT_TIMEZONE) -> str:
    return datetime.combine(value, time.min, tzinfo=ZoneInfo(timezone)).isoformat()


def _extract_recommendation_filters(
    user_text: str,
    reference: datetime,
) -> dict[str, Any]:
    tags, semantic_text = _extract_tag_filter_text(user_text)
    owner, semantic_text = _extract_owner_filter_text(semantic_text)
    semantic_text = HASHTAG_RE.sub("", semantic_text)
    lowered = semantic_text.lower()
    contexts, can_do_while, requirements, location = _extract_contexts(semantic_text)
    filters: dict[str, Any] = {}

    if tags:
        filters["tags"] = tags
    if contexts:
        filters["context"] = contexts
    if can_do_while:
        filters["can_do_while"] = can_do_while
    if requirements:
        filters["available_resources"] = requirements
    if location != "unknown":
        filters["location"] = location
    if owner is not None:
        filters["owner"] = owner

    energy = _extract_level(semantic_text, "energy")
    if energy is not None:
        filters["energy"] = energy
    elif "bored" in lowered:
        filters["max_energy"] = "medium"
        filters["max_complexity"] = "medium"
        filters["exclude_requires"] = ["focus"]

    duration = _extract_duration_minutes(semantic_text)
    if duration is not None:
        filters["duration_minutes"] = duration

    if re.search(r"\b(?:urgent|asap)\b", lowered):
        filters["urgency"] = "high"

    effort_type = _infer_effort_type(semantic_text)
    if effort_type != "unknown":
        filters["effort_type"] = effort_type
    elif re.search(r"\bcalls?\b", lowered):
        filters["effort_type"] = "communication"
    elif re.search(r"\bphysical\s+tasks?\b|\bphysical\s+work\b", lowered):
        filters["effort_type"] = "physical"
    elif re.search(r"\bcognitive\s+(?:tasks?|work)\b", lowered):
        filters["effort_type"] = "cognitive"
    elif re.search(r"\bpaperwork\b", lowered):
        filters["effort_type"] = "paperwork"

    if filters.get("effort_type") == "communication":
        available = set(filters.get("available_resources", []))
        available.add("phone")
        filters["available_resources"] = sorted(available)
    elif filters.get("effort_type") == "paperwork":
        available = set(filters.get("available_resources", []))
        available.update(["computer", "internet", "paperwork", "focus"])
        filters["available_resources"] = sorted(available)

    if re.search(r"\b(?:driving|commuting)\b", lowered):
        filters["available_resources"] = ["phone", "car"]
        filters["unavailable_resources"] = [
            "computer",
            "paperwork",
            "equipment",
            "quiet",
            "focus",
        ]
    elif re.search(r"\b(?:laptop|computer)\b", lowered):
        available = set(filters.get("available_resources", []))
        available.update(["computer", "internet", "phone"])
        filters["available_resources"] = sorted(available)

    if "due this week" in lowered or "this week" in lowered:
        filters["due_min"] = _start_of_day(reference.date())
        filters["due_max"] = _end_of_day(_week_end(reference))
    elif "due today" in lowered or "today" in lowered:
        filters["due_min"] = _start_of_day(reference.date())
        filters["due_max"] = _end_of_day(reference.date())
    elif "due tomorrow" in lowered or "tomorrow" in lowered:
        day = (reference + timedelta(days=1)).date()
        filters["due_min"] = _start_of_day(day)
        filters["due_max"] = _end_of_day(day)

    if "context" in filters:
        filters["available_context"] = filters["context"]
    if "duration_minutes" in filters:
        filters["available_time_minutes"] = filters["duration_minutes"]
    if "effort_type" in filters:
        filters["preferred_effort_type"] = filters["effort_type"]

    return filters


def _extract_query_after_action(user_text: str) -> str | None:
    cleaned = re.sub(
        r"^\s*(?:(?:/tasks?|tasks?|todos?|to-dos?)(?:@[A-Za-z0-9_]+)?\s+)?"
        r"(?:(?:complete|completed|finish|finished|done|delete|remove|"
        r"update|change|assign|set|make|put)\s+"
        r"(?:(?:task|todo|to-do)\s+)?|mark\s+(?:(?:task|todo|to-do)\s+)?(?:done\s+)?)",
        "",
        user_text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\s+(?:(?:with\s+)?owner\s*(?::|is)?\s+|assigned?\s+to\s+)"
        rf"(?:{OWNER_ALIAS_PATTERN})\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+done$", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip(" .")
    return cleaned or None


def _task_command_parts(user_text: str) -> tuple[bool, str]:
    match = TASK_PREFIX_RE.match(user_text)
    if match is None:
        return False, user_text.strip()
    return True, user_text[match.end() :].strip()


def _as_task_create_body(text: str) -> str | None:
    match = TASK_AS_TASK_RE.search(text)
    if match is None:
        return None
    body = text[: match.start()].strip(" .,:;-")
    body = re.sub(r"^\s*help\s+me\s+", "", body, flags=re.IGNORECASE)
    body = re.sub(
        r"^\s*(?:please\s+)?(?:(?:i|we)\s+)?(?:want|need|would\s+like)\s+to\s+",
        "",
        body,
        flags=re.IGNORECASE,
    )
    return _clean_spaces(body) or None


def _task_create_request_for_action(user_text: str) -> str:
    has_prefix, body = _task_command_parts(user_text)
    as_task_body = _as_task_create_body(body)
    if as_task_body is None:
        as_task_body = _as_task_create_body(user_text)
    if as_task_body is not None:
        return f"add task: {as_task_body}"
    return _normalize_create_request_text(body if has_prefix else user_text)


def _can_default_create_from_task_prefix(body: str) -> bool:
    words = re.findall(r"[A-Za-z0-9]+", body)
    if len(words) < 3:
        return False
    return bool(
        _has_explicit_clock_time(body)
        or re.search(
            r"\b(today|tomorrow|tonight|weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday|next\s+week)\b",
            body,
            flags=re.IGNORECASE,
        )
    )


def score_task_command_candidates(user_text: str, now: datetime | None = None) -> list[dict[str, Any]]:
    del now
    has_prefix, body = _task_command_parts(user_text)
    target_text = body if has_prefix else user_text.strip()
    lowered = target_text.lower().strip()
    candidates: list[dict[str, Any]] = []

    def add(action: str, confidence: float, normalized_request: str, *evidence: str) -> None:
        candidates.append(
            {
                "action": action,
                "confidence": round(max(0.0, min(confidence, 1.0)), 2),
                "normalized_request": normalized_request,
                "evidence": tuple(value for value in evidence if value),
            }
        )

    if not lowered:
        return candidates

    if TASK_HELP_ACTION_RE.match(lowered):
        add(
            "help_task",
            0.98 if has_prefix else 0.72,
            user_text.strip(),
            "task prefix + help cue" if has_prefix else "task help cue",
        )

    if _is_run_assistant_help_request(target_text):
        add("run_assistant_help", 0.96, target_text, "assistant help run cue")

    if TASK_COMPLETE_ACTION_RE.match(lowered):
        add(
            "complete_task",
            0.96 if has_prefix else 0.9,
            target_text,
            "task prefix + completion cue" if has_prefix else "completion cue",
        )

    if TASK_DELETE_ACTION_RE.match(lowered):
        add(
            "delete_task",
            0.95 if has_prefix else 0.88,
            target_text,
            "task prefix + delete cue" if has_prefix else "delete cue",
        )

    if TASK_UPDATE_ACTION_RE.match(lowered):
        add(
            "update_task",
            0.92 if has_prefix else 0.72,
            target_text,
            "task prefix + update cue" if has_prefix else "update cue",
        )

    as_task_body = _as_task_create_body(target_text) or _as_task_create_body(user_text)
    has_create_request = CREATE_TASK_REQUEST_RE.search(lowered) is not None
    has_prefixed_create_action = has_prefix and re.search(
        r"^\s*(?:add|create|capture|remember)\b",
        lowered,
    ) is not None
    has_bare_task_start = CREATE_TASK_START_RE.search(lowered) is not None
    has_reminder = re.search(r"^\s*remind\s+me\s+to\b", lowered) is not None
    if (
        as_task_body is not None
        or has_create_request
        or has_prefixed_create_action
        or has_reminder
        or (
            has_bare_task_start
            and not _has_explicit_clock_time(target_text)
            and not TASK_READ_ACTION_RE.match(lowered)
        )
    ):
        add(
            "create_task",
            0.93 if as_task_body is not None else (0.92 if has_prefix or has_create_request else 0.86),
            _task_create_request_for_action(user_text),
            "as-task create cue" if as_task_body is not None else "create cue",
        )
    elif (
        has_prefix
        and not TASK_READ_ACTION_RE.match(lowered)
        and not TASK_HELP_ACTION_RE.match(lowered)
        and _can_default_create_from_task_prefix(body)
    ):
        add(
            "create_task",
            0.84,
            _normalize_create_request_text(body),
            "task prefix + task body",
        )

    if TASK_READ_ACTION_RE.match(lowered):
        add(
            "recommend_tasks",
            0.9 if has_prefix else 0.72,
            target_text,
            "task prefix + read cue" if has_prefix else "read cue",
        )

    return sorted(
        candidates,
        key=lambda candidate: (-float(candidate["confidence"]), str(candidate["action"])),
    )


def _normalize_create_request_text(user_text: str) -> str:
    followup_blob = FOLLOWUP_BLOB_RE.match(user_text)
    if followup_blob is not None:
        target = followup_blob.group("object").lower()
        timing = _clean_spaces(followup_blob.group("timing").strip(" .,:;-"))
        title = f"Follow up on {target}{f' {timing}' if timing else ''}"
        body = _clean_spaces(followup_blob.group("body").strip())
        return f"add task: {title}\nNotes: {body}"

    normalized = CREATE_TASK_TRANSCRIPTION_RE.sub("add ", user_text, count=1)
    return re.sub(
        r"^\s*(?:i\s+had|there\s+is|this\s+is)\s+(?:a\s+)?"
        r"(?:task|todo|to-do|open\s+loop)\s*(?:for|to)?\s*",
        "add task ",
        normalized,
        count=1,
        flags=re.IGNORECASE,
    )


def _is_run_assistant_help_request(user_text: str) -> bool:
    return RUN_ASSISTANT_HELP_RE.search(user_text) is not None


def _has_explicit_clock_time(user_text: str) -> bool:
    return EXPLICIT_CLOCK_TIME_RE.search(user_text) is not None


def extract_intent(
    user_text: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    reference = _default_now(now)
    intent_text, assistant_metadata, assistant_notes = _extract_assistant_help(user_text)
    action_text = intent_text or user_text
    candidates = score_task_command_candidates(action_text, now=reference)
    executable_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("action") in TASK_COMMAND_ACTIONS
    ]
    top_action = executable_candidates[0] if executable_candidates else None
    request_text = str(
        top_action.get("normalized_request")
        if top_action is not None
        else _normalize_create_request_text(action_text)
    )
    lowered = request_text.lower().strip()

    if top_action is not None and top_action.get("action") == "run_assistant_help":
        return {
            "intent": "run_assistant_help",
            "missing_fields": [],
        }

    if top_action is not None and top_action.get("action") == "complete_task":
        return {
            "intent": "complete_task",
            "query": _extract_query_after_action(request_text),
            "missing_fields": [],
        }

    if top_action is not None and top_action.get("action") == "delete_task":
        return {
            "intent": "delete_task",
            "query": _extract_query_after_action(request_text),
            "missing_fields": [],
        }

    if top_action is not None and top_action.get("action") == "update_task":
        return {
            "intent": "update_task",
            "query": _extract_query_after_action(request_text),
            "missing_fields": [],
        }

    has_assistant_help = bool(assistant_metadata.get("assistant_help_needed"))
    has_create_request = CREATE_TASK_REQUEST_RE.search(lowered) is not None
    has_bare_task_start = CREATE_TASK_START_RE.search(lowered) is not None
    if (
        top_action is not None
        and top_action.get("action") == "create_task"
    ) or (has_assistant_help and request_text) or has_create_request or (
        has_bare_task_start
        and not _has_explicit_clock_time(request_text)
    ):
        result = _extract_create_intent(request_text, reference)
        if assistant_metadata:
            metadata = dict(result.get("metadata") or {})
            metadata.update(assistant_metadata)
            result["metadata"] = normalize_metadata(metadata)
        if assistant_notes:
            result["notes"] = _join_note_sections(
                result.get("notes") if isinstance(result.get("notes"), str) else None,
                assistant_notes,
            )
        return result

    return {
        "intent": "recommend_tasks",
        "filters": _extract_recommendation_filters(request_text, reference),
        "missing_fields": [],
    }

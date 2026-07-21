from __future__ import annotations

import re
from difflib import SequenceMatcher


TASK_ACTION_WORDS = (
    "pack",
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

FILLER_RE = re.compile(r"\b(?:um|uh|erm)\b[,\s]*", re.IGNORECASE)
LEADING_POLITE_RE = re.compile(
    r"^\s*(?:(?:can|could|would|will)\s+you\s+|please\s+)+",
    re.IGNORECASE,
)
WAKE_PREFIX_RE = re.compile(
    r"^\s*(?:hey\s+)?(?:n4os|openclaw|noah)\s*[,.:;-]\s*",
    re.IGNORECASE,
)
NOAH_ASSISTANT_HELP_PREFIX_RE = re.compile(
    r"^\s*noah\s*[,.:;-]\s*help\b",
    re.IGNORECASE,
)
TASK_WORD_RE = re.compile(
    r"\b(?P<verb>add|create|capture|remember|complete|finish|delete|remove|mark)\s+"
    r"(?P<article>an?\s+)?tax\b(?!\s+returns?\b)",
    re.IGNORECASE,
)
TASKS_WORD_RE = re.compile(
    r"\b(?P<verb>list|show)\s+(?P<owner>my\s+|the\s+)?tax\b",
    re.IGNORECASE,
)
GIVE_ME_TASKS_RE = re.compile(
    r"\bgive\s+me\s+(?P<owner>my\s+|the\s+)?tax\b",
    re.IGNORECASE,
)
REMIND_ME_RE = re.compile(
    r"^\s*remind\s+(?:me|us)\s+to\s+",
    re.IGNORECASE,
)
REMEMBER_TO_RE = re.compile(
    r"^\s*remember\s+to\s+",
    re.IGNORECASE,
)
SLASH_COMMAND_RE = re.compile(
    r"^\s*/(?P<command>calendar|calender|event|schedule|tasks?|todos?|decisions?)"
    r"(?:@[A-Za-z0-9_]+)?"
    r"(?:\s+|:\s*)?(?P<body>.*)$",
    re.IGNORECASE,
)
TASK_LIST_BODY_RE = re.compile(
    r"^\s*(?:list|show|view|find|search|lookup|look\s+up)\b",
    re.IGNORECASE,
)
LEADING_ACTION_RE = re.compile(
    r"^\s*(?:add|create|capture|schedule)\b\s*",
    re.IGNORECASE,
)

FUZZY_DATE_VOCABULARY = {
    "today",
    "tomorrow",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
}

PHRASE_REPAIRS = (
    (re.compile(r"\bcalender\b", re.IGNORECASE), "calendar"),
    (re.compile(r"\bscheduel\b", re.IGNORECASE), "schedule"),
    (re.compile(r"\bevetn\b", re.IGNORECASE), "event"),
    (re.compile(r"\bFebuary\b", re.IGNORECASE), "February"),
    (re.compile(r"\bSeptemeber\b", re.IGNORECASE), "September"),
    (re.compile(r"\bdecision\s+(?:bried|breif|brif)\b", re.IGNORECASE), "decision brief"),
    (re.compile(r"\b(?:bried|breif|brif)\s+decision\b", re.IGNORECASE), "brief decision"),
    (re.compile(r"\bjet\s+lagged\b", re.IGNORECASE), "jetlagged"),
    (re.compile(r"\bhome\s+bored\b", re.IGNORECASE), "home board"),
    (re.compile(r"\bhouse\s+bored\b", re.IGNORECASE), "home board"),
    (re.compile(r"\bhomeboard\b", re.IGNORECASE), "home board"),
    (re.compile(r"\bNyshas\s+School\b", re.IGNORECASE), "Nysha's school"),
    (re.compile(r"\bNyshas\b", re.IGNORECASE), "Nysha's"),
    (re.compile(r"\bNisha\b", re.IGNORECASE), "Nysha"),
    (re.compile(r"\bNyshad\b", re.IGNORECASE), "Nysha"),
    (re.compile(r"\bNaavya\b", re.IGNORECASE), "Navya"),
    (re.compile(r"\bNiyaati\b", re.IGNORECASE), "Niyati"),
    (re.compile(r"\bNiyathi\b", re.IGNORECASE), "Niyati"),
    (re.compile(r"\bMonteserie\b", re.IGNORECASE), "Montessori"),
    (re.compile(r"\bFUSD\s+number\b", re.IGNORECASE), "FUSD phone number"),
    (re.compile(r"\bNamesh\b", re.IGNORECASE), "Nimesh"),
    (re.compile(r"\bNovah\b", re.IGNORECASE), "Noah"),
)


def improve_entered_text(text: str) -> str:
    """Make high-confidence voice-typed N4OS requests easier to route."""
    cleaned = _clean_lines(text)
    if not cleaned:
        return ""

    cleaned = _strip_wake_and_polite_prefix(cleaned)
    cleaned = _normalize_slash_command(cleaned)
    cleaned = _repair_common_phrases(cleaned)
    cleaned = _repair_closed_vocabulary_typos(cleaned)
    cleaned = _repair_task_word(cleaned)
    cleaned = _make_reminder_actionable(cleaned)
    return _clean_lines(cleaned)


def _clean_lines(text: str) -> str:
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        cleaned = FILLER_RE.sub("", line)
        cleaned = " ".join(cleaned.split()).strip(" ,")
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _strip_wake_and_polite_prefix(text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return text

    first = lines[0]
    if NOAH_ASSISTANT_HELP_PREFIX_RE.search(first) is None:
        first = WAKE_PREFIX_RE.sub("", first, count=1)
    first = LEADING_POLITE_RE.sub("", first, count=1)
    return "\n".join([first, *lines[1:]]).strip()


def _repair_common_phrases(text: str) -> str:
    repaired = text
    for pattern, replacement in PHRASE_REPAIRS:
        repaired = pattern.sub(replacement, repaired)
    return repaired


def _normalize_slash_command(text: str) -> str:
    match = SLASH_COMMAND_RE.match(text)
    if match is None:
        return text

    command = match.group("command").lower()
    body = match.group("body").strip()
    body_without_action = LEADING_ACTION_RE.sub("", body, count=1).strip()
    if command in ("calendar", "calender", "event", "schedule"):
        suffix = body_without_action or body
        return f"add event {suffix}".strip()
    if command in ("task", "tasks", "todo", "todos"):
        if TASK_LIST_BODY_RE.search(body):
            return _normalize_task_list_body(body)
        suffix = body_without_action or body
        return f"add task {suffix}".strip()
    if command in ("decision", "decisions"):
        suffix = body or "list pending decisions"
        return f"{command} {suffix}".strip()
    return text


def _normalize_task_list_body(body: str) -> str:
    cleaned = body.strip()
    if re.search(r"\b(tasks?|todos?|to-dos?|open loops?)\b", cleaned, re.IGNORECASE):
        return cleaned

    return re.sub(
        r"^\s*(list|show|view|find|search|lookup|look\s+up)\b",
        lambda match: f"{match.group(1)} tasks",
        cleaned,
        count=1,
        flags=re.IGNORECASE,
    ).strip()


def _repair_closed_vocabulary_typos(text: str) -> str:
    return re.sub(r"\b[A-Za-z]{4,10}\b", _repair_closed_vocabulary_token, text)


def _repair_closed_vocabulary_token(match: re.Match[str]) -> str:
    token = match.group(0)
    lowered = token.lower()
    if lowered in FUZZY_DATE_VOCABULARY:
        return token

    best = None
    best_score = 0.0
    for candidate in FUZZY_DATE_VOCABULARY:
        if candidate[0] != lowered[0]:
            continue
        if abs(len(candidate) - len(lowered)) > 2:
            continue
        score = SequenceMatcher(None, lowered, candidate).ratio()
        if score > best_score:
            best = candidate
            best_score = score

    if best is None or best_score < 0.78:
        return token
    return _preserve_case(token, best)


def _preserve_case(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement.capitalize()
    return replacement


def _repair_task_word(text: str) -> str:
    repaired = TASK_WORD_RE.sub(
        lambda match: f"{match.group('verb')} task",
        text,
    )
    repaired = TASKS_WORD_RE.sub(
        lambda match: f"{match.group('verb')} {match.group('owner') or ''}tasks",
        repaired,
    )
    return GIVE_ME_TASKS_RE.sub(
        lambda match: f"give me {match.group('owner') or ''}tasks",
        repaired,
    )


def _make_reminder_actionable(text: str) -> str:
    if REMIND_ME_RE.search(text):
        return REMIND_ME_RE.sub("add task ", text, count=1)
    if REMEMBER_TO_RE.search(text) and re.search(
        rf"^\s*remember\s+to\s+(?:{TASK_ACTION_PATTERN})\b",
        text,
        flags=re.IGNORECASE,
    ):
        return REMEMBER_TO_RE.sub("add task ", text, count=1)
    return text

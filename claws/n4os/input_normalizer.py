from __future__ import annotations

import re


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

PHRASE_REPAIRS = (
    (re.compile(r"\bcalender\b", re.IGNORECASE), "calendar"),
    (re.compile(r"\bdecision\s+(?:bried|breif|brif)\b", re.IGNORECASE), "decision brief"),
    (re.compile(r"\b(?:bried|breif|brif)\s+decision\b", re.IGNORECASE), "brief decision"),
    (re.compile(r"\bjet\s+lagged\b", re.IGNORECASE), "jetlagged"),
    (re.compile(r"\bhome\s+bored\b", re.IGNORECASE), "home board"),
    (re.compile(r"\bhouse\s+bored\b", re.IGNORECASE), "home board"),
    (re.compile(r"\bhomeboard\b", re.IGNORECASE), "home board"),
    (re.compile(r"\bNyshas\s+School\b", re.IGNORECASE), "Nysha's school"),
    (re.compile(r"\bNyshas\b", re.IGNORECASE), "Nysha's"),
    (re.compile(r"\bNyshad\b", re.IGNORECASE), "Nysha"),
    (re.compile(r"\bMonteserie\b", re.IGNORECASE), "Montessori"),
    (re.compile(r"\bFUSD\s+number\b", re.IGNORECASE), "FUSD phone number"),
)


def improve_entered_text(text: str) -> str:
    """Make high-confidence voice-typed N4OS requests easier to route."""
    cleaned = _clean_lines(text)
    if not cleaned:
        return ""

    cleaned = _strip_wake_and_polite_prefix(cleaned)
    cleaned = _repair_common_phrases(cleaned)
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

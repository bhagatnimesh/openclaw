from __future__ import annotations

import re

from n4os_goals_status import format_goals_status
from n4os_memory_status import format_memory_status
from n4os_review import format_n4os_review


STATUS_COMMAND_RE = re.compile(r"^\s*/status(?:@\w+)?(?:\s+(.+))?\s*$", re.I)


def is_n4os_status_message(text: str) -> bool:
    return bool(STATUS_COMMAND_RE.match(text.strip()))


def parse_status_target(text: str) -> str:
    match = STATUS_COMMAND_RE.match(text.strip())
    if not match:
        return "reading"
    return (match.group(1) or "reading").strip().lower()


def format_n4os_status(target: str) -> str | None:
    normalized = target.strip().lower()
    if normalized in {"nysha", "navya", "family"}:
        return format_memory_status(normalized)
    if normalized in {"goals", "goal", "priorities", "priority"}:
        return format_goals_status()
    if normalized in {"day", "today"}:
        return format_n4os_review("day")
    if normalized in {"week", "weekly", "this week"}:
        return format_n4os_review("week")
    if normalized in {"month", "monthly", "this month"}:
        return format_n4os_review("month")
    if normalized in {"reading", "garden"}:
        return None
    return (
        "Status options: /status Nysha, /status goals, /status reading, "
        "/status week."
    )

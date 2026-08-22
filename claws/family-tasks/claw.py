from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
import sys
from typing import Any

try:
    from ai_field_extraction import TaskAIFieldExtractor
except ImportError:
    TaskAIFieldExtractor = None

from constants import DEFAULT_TASK_LIST_ID
from intent import (
    OWNER_ALIAS_PATTERN,
    OWNER_ALIASES,
    extract_intent,
    extract_tags,
    normalize_metadata,
    normalize_tags,
    read_metadata_from_notes,
)
from matcher import match_tasks
from noah_assistant import (
    NoahResearchClient,
    NoahResearchResult,
    NoahSource,
    OpenClawNoahResearchClient,
)
from prompts import SYSTEM_PROMPT, TOOL_GUIDANCE
from tools import FamilyTaskTools, TasksProvider, build_default_tools


NOAH_ASSISTANT_DEFAULT_LIMIT = 3
NOAH_ASSISTANT_MAX_LIMIT = 20
IMAGE_TEXT_MARKER_RE = re.compile(
    r"(?im)^\s*(?:Image text:|\[Image text extraction[^\]]*\]:)\s*$",
)
BULK_IMAGE_TASK_CUE_RE = re.compile(
    r"\b(?:every|each|all)\b.*\b(?:entry|entries|items?|tasks?|todos?|to-dos?)\b.*\bimage\b|"
    r"\bimage\b.*\b(?:every|each|all)\b.*\b(?:entry|entries|items?|tasks?|todos?|to-dos?)\b",
    re.IGNORECASE,
)
IMAGE_TASK_HEADER_RE = re.compile(
    r"^\s*(?:image\s+text|tasks?|todos?|to-dos?|entries?|items?)\s*:?\s*$",
    re.IGNORECASE,
)
IMAGE_LIST_TITLE_RE = re.compile(
    r"^\s*(?:list\s+title|title|heading)\s*:\s*(?P<title>.+?)\s*$",
    re.IGNORECASE,
)
AFFIRMATIVE_RE = re.compile(r"^(?:yes|y|confirm)(?:\s*,?\s*(?:please|add\s+(?:it|them|all)))?[.!?]*$", re.I)
NEGATIVE_RE = re.compile(r"^(?:no|n|cancel)(?:\s+please)?[.!?]*$", re.I)
TASK_LIST_REQUEST_PATTERNS = (
    re.compile(
        r"^\s*(?:show|list|view)\s+(?:the\s+)?(?P<name>[A-Za-z0-9&' -]+?)\s+"
        r"(?:tasks?|todos?|to-dos?)(?:\s+list)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:in|into|on|to)\s+(?:the\s+)?(?P<name>[A-Za-z0-9&' -]+?)"
        r"(?P<suffix>\s+(?:task\s+)?list)\s*[.!?]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:use|choose|select)\s+(?:the\s+)?(?:task\s+)?list\s+"
        r"(?:named\s+|called\s+)?"
        r"(?P<name>[A-Za-z0-9&' -]+?)\s*[.!?]*$",
        re.IGNORECASE,
    ),
)


@dataclass
class PendingAction:
    action: str
    task: dict[str, Any] | None = None
    choices: list[dict[str, Any]] | None = None
    update: TaskUpdateRequest | None = None
    task_list_id: str = DEFAULT_TASK_LIST_ID
    create_intents: list[dict[str, Any]] | None = None
    reference_time: datetime | None = None


def _local_task_list_request(request: str) -> tuple[str | None, str]:
    for pattern in TASK_LIST_REQUEST_PATTERNS:
        match = pattern.search(request)
        if match is None:
            continue
        name = re.sub(
            r"\s+(?:task\s+)?list\s*$",
            "",
            match.group("name").strip(" .,:;-"),
            flags=re.IGNORECASE,
        ).strip()
        if (
            not name
            or name.lower() in {"all", "my", "open", "pending"}
            or name.lower() in OWNER_ALIASES
        ):
            continue
        cleaned = " ".join(f"{request[: match.start()]} {request[match.end() :]}".split())
        if pattern is TASK_LIST_REQUEST_PATTERNS[0]:
            cleaned = f"show tasks {cleaned}".strip()
        return name, cleaned
    return None, request


def _normalized_task_list_name(value: str) -> str:
    normalized = " ".join(value.lower().split())
    normalized = re.sub(r"^the\s+", "", normalized)
    return re.sub(r"\s+(?:task\s+)?list$", "", normalized).strip()


def _format_due(task: dict[str, Any]) -> str:
    due = task.get("due")
    if not due:
        return "no due date"
    return str(due)[:10]


def _format_task_choice(task: dict[str, Any]) -> str:
    title = task.get("title") or "Untitled task"
    _, metadata = _task_notes_and_metadata(task)
    parts = [title, _format_due(task)]
    duration = metadata.get("duration_minutes")
    if duration is not None:
        parts.append(f"{duration} min")
    energy = metadata.get("energy")
    if energy and energy != "unknown":
        parts.append(f"{energy} energy")
    effort_type = metadata.get("effort_type")
    if effort_type and effort_type != "unknown":
        parts.append(str(effort_type))
    return " | ".join(parts)


def _task_url(task: dict[str, Any]) -> str | None:
    for key in ("webViewLink", "selfLink"):
        value = task.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    links = task.get("links")
    if isinstance(links, list):
        for link in links:
            if not isinstance(link, dict):
                continue
            value = link.get("link")
            if isinstance(value, str) and value.strip():
                return value.strip()

    return None


def _format_created_task_message(task: dict[str, Any]) -> str:
    title = task.get("title") or "Untitled task"
    task_url = _task_url(task)
    notes, metadata = _task_notes_and_metadata(task)
    owner = str(metadata.get("owner") or "unknown")
    due = task.get("due")
    normalized_notes = notes.strip().lower()
    has_readable_notes = bool(
        notes
        and not normalized_notes.startswith("tags:")
        and not normalized_notes.startswith("assistant help:")
    )
    has_readable_metadata = has_readable_notes
    if has_readable_metadata:
        lines = [f"Created task: {title}"]
        if due:
            try:
                due_date = datetime.fromisoformat(str(due)[:10])
                lines.append(f"Due: {due_date.strftime('%a, %b %-d')}")
            except ValueError:
                lines.append(f"Due: {due}")
        if has_readable_notes:
            lines.append(f"Details: {notes}")
        if owner != "unknown":
            lines.append(f"Owner: {owner}")
        if task_url:
            lines.append(f"Open: {task_url}")
        else:
            task_id = task.get("id")
            if task_id:
                lines.append(f"Task id: {task_id}")
        return "\n".join(lines)

    if task_url:
        return f"Created task: {title} (open: {task_url})."

    task_id = task.get("id")
    suffix = f" (task id: {task_id})" if task_id else ""
    return f"Created task: {title}{suffix}."


def _assistant_action_sentence(value: Any) -> str:
    cleaned = str(value or "").strip(" .")
    if not cleaned:
        return "help with this task"
    return cleaned[:1].lower() + cleaned[1:]


def _format_assistant_acknowledgment(metadata: dict[str, Any] | None) -> str | None:
    if not isinstance(metadata, dict) or not metadata.get("assistant_help_needed"):
        return None

    assistant_name = str(metadata.get("assistant_name") or "Noah").strip() or "Noah"
    action = _assistant_action_sentence(metadata.get("assistant_help_request"))
    return (
        f"{assistant_name} queued: On your behalf, {assistant_name} should "
        f"{action}. Say 'Run {assistant_name} assistant help' to run queued help."
    )


def _assistant_run_limit_from_request(request: str) -> int | None:
    lowered = request.lower()
    if re.search(r"\ball\b", lowered):
        return None

    match = re.search(r"\b(\d{1,2})\b", lowered)
    if match is None:
        return NOAH_ASSISTANT_DEFAULT_LIMIT

    return max(1, min(NOAH_ASSISTANT_MAX_LIMIT, int(match.group(1))))


def _assistant_task_title(task: dict[str, Any]) -> str:
    return str(task.get("title") or "Untitled task").strip()


def _task_notes_and_metadata(task: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    notes, legacy_metadata = read_metadata_from_notes(task.get("notes"))
    metadata = task.get("_n4os_metadata")
    if isinstance(metadata, dict):
        return notes, metadata
    return notes, legacy_metadata


def _is_pending_assistant_help_task(task: dict[str, Any]) -> bool:
    if task.get("status") == "completed":
        return False

    _, metadata = _task_notes_and_metadata(task)
    return bool(metadata.get("assistant_help_needed")) and (
        metadata.get("assistant_help_status") != "completed"
    )


def _source_to_metadata(source: NoahSource) -> dict[str, str]:
    return {"title": source.title, "url": source.url}


def _summarize_result(value: str, max_chars: int = 240) -> str:
    cleaned = " ".join(value.split()).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "..."


def _format_note_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        return value.isoformat(timespec="minutes")
    return value.astimezone().strftime("%Y-%m-%d %H:%M %Z")


def _append_noah_result_notes(
    notes: Any,
    result: NoahResearchResult,
    completed_at: datetime,
) -> str:
    human_notes, _ = read_metadata_from_notes(notes)
    sections = []
    if human_notes.strip():
        sections.append(human_notes.strip())

    lines = [
        f"Noah result ({_format_note_timestamp(completed_at)}):",
        result.text.strip(),
    ]
    if result.sources:
        lines.append("Sources:")
        for source in result.sources:
            lines.append(f"- {source.title}: {source.url}")
    sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _completed_assistant_metadata(
    metadata: dict[str, Any],
    result: NoahResearchResult,
    completed_at: datetime,
) -> dict[str, Any]:
    updated = dict(metadata)
    updated["assistant_help_needed"] = False
    updated["assistant_help_status"] = "completed"
    updated["assistant_help_completed_at"] = completed_at.isoformat()
    updated["assistant_help_result_summary"] = _summarize_result(result.text)
    updated["assistant_help_result_sources"] = [
        _source_to_metadata(source)
        for source in result.sources
    ]
    updated.pop("assistant_help_error", None)
    updated.pop("assistant_help_last_attempt_at", None)
    return updated


def _errored_assistant_metadata(
    metadata: dict[str, Any],
    error: Exception,
    attempted_at: datetime,
) -> dict[str, Any]:
    updated = dict(metadata)
    updated["assistant_help_needed"] = True
    updated["assistant_help_status"] = "error"
    updated["assistant_help_error"] = _summarize_result(str(error), max_chars=500)
    updated["assistant_help_last_attempt_at"] = attempted_at.isoformat()
    return updated


def _default_now(reference_time: datetime | None) -> datetime:
    if reference_time is not None:
        return reference_time
    return datetime.now().astimezone()


def _format_recommendations(
    recommendations: list[dict[str, Any]],
    heading: str = "Recommended tasks:",
) -> str:
    if not recommendations:
        return "No matching open tasks found."

    lines = [heading]
    for recommendation in recommendations:
        task = recommendation.get("task", recommendation)
        reasons = recommendation.get("reasons", [])
        suffix = ""
        if reasons:
            suffix = " - " + "; ".join(str(reason) for reason in reasons)
        lines.append(f"- {_format_task_choice(task)}{suffix}")
    return "\n".join(lines)


def _clean_image_task_line(line: str) -> str | None:
    cleaned = line.strip()
    if not cleaned:
        return None
    cleaned = re.sub(r"^\s*(?:[-*•]|\d+[.)]|\[[ xX]?\]|☐|☑|✓)\s*", "", cleaned)
    cleaned = cleaned.strip(" \t-–—•☐☑✓")
    if not cleaned or IMAGE_TASK_HEADER_RE.match(cleaned):
        return None
    if IMAGE_LIST_TITLE_RE.match(cleaned):
        return None
    return cleaned[:1].upper() + cleaned[1:]


def _split_bulk_image_task_request(request: str) -> tuple[str, str] | None:
    marker = IMAGE_TEXT_MARKER_RE.search(request)
    if marker is None or BULK_IMAGE_TASK_CUE_RE.search(request) is None:
        return None
    return request[: marker.start()], request[marker.end() :]


def _image_list_title(image_text: str) -> str | None:
    for line in image_text.splitlines():
        match = IMAGE_LIST_TITLE_RE.match(line.strip())
        if match is not None:
            return match.group("title").strip()
    return None


def _bulk_image_task_titles(request: str) -> list[str]:
    split_request = _split_bulk_image_task_request(request)
    if split_request is None:
        return []

    _, image_text = split_request
    titles: list[str] = []
    seen: set[str] = set()
    for line in image_text.splitlines():
        title = _clean_image_task_line(line)
        if title is None:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        titles.append(title)
    return titles[:50]


def _bulk_image_task_caption(request: str) -> str:
    split_request = _split_bulk_image_task_request(request)
    return split_request[0].strip() if split_request is not None else request


MULTI_CREATE_TASK_RE = re.compile(
    r"(?P<prefix>(?:^|[.!?]\s+)(?:and\s+)?)"
    r"(?P<verb>add|create|capture|remember)\s+another\s+"
    r"(?P<noun>task|todo|to-do|open loop)\b",
    re.IGNORECASE,
)
MULTI_CREATE_TASK_COMMAND_START_RE = re.compile(
    r"(?:\A|(?<=[.!?])\s+|^[ \t]*)"
    r"(?P<command>(?:please\s+)?(?:add|create|capture|remember)\s+(?:an?\s+)?"
    r"(?:tasks?|todos?|to-dos?|open loops?)\b)",
    re.IGNORECASE | re.MULTILINE,
)
TASK_DETAIL_SECTION_RE = re.compile(
    r"(?m)^\s*(?:notes?|details?|description|context)\s*:",
    re.IGNORECASE,
)
HEADER_ONLY_MULTI_CREATE_RE = re.compile(
    r"^\s*(?:please\s+)?(?:add|create|capture|remember)\s+"
    r"(?:tasks|todos|to-dos|open loops)\s*:?\s*$",
    re.IGNORECASE,
)
TRAILING_SENTENCE_TAG_RE = re.compile(
    r"(?P<body>.+?)[.!?]\s+tag\s*:?\s*(?P<tag>#?[A-Za-z][A-Za-z0-9_-]*)\s*$",
    re.IGNORECASE | re.DOTALL,
)


def _split_repeated_create_task_commands(request: str) -> list[str] | None:
    matches = list(MULTI_CREATE_TASK_COMMAND_START_RE.finditer(request))
    if len(matches) < 2:
        return None

    starts = [match.start("command") for match in matches]
    leading_text = request[: starts[0]].strip()
    if leading_text:
        return None
    if any(
        TASK_DETAIL_SECTION_RE.search(request[starts[index] : starts[index + 1]])
        for index in range(len(starts) - 1)
    ):
        return None

    parts = [
        request[start : starts[index + 1]].strip()
        for index, start in enumerate(starts[:-1])
    ]
    parts.append(request[starts[-1] :].strip())
    return [
        part
        for part in parts
        if part and HEADER_ONLY_MULTI_CREATE_RE.fullmatch(part) is None
    ]


def _normalize_split_sentence_tag_annotations(requests: list[str]) -> list[str]:
    normalized_requests = []
    for request in requests:
        match = TRAILING_SENTENCE_TAG_RE.fullmatch(request)
        if match is None:
            normalized_requests.append(request)
            continue
        tags = normalize_tags([match.group("tag")])
        if not tags:
            normalized_requests.append(request)
            continue
        normalized_requests.append(f"{match.group('body').strip()} and tag {tags[0]}")
    return normalized_requests


def _split_multiple_create_task_requests(request: str) -> list[str]:
    repeated_commands = _split_repeated_create_task_commands(request)
    if repeated_commands is not None:
        return _normalize_split_sentence_tag_annotations(repeated_commands)

    matches = list(MULTI_CREATE_TASK_RE.finditer(request))
    if not matches:
        return [request]

    raw_parts: list[str] = []
    start = 0
    for match in matches:
        first_part = request[start : match.start()].strip()
        if first_part:
            raw_parts.append(first_part)

        prefix = match.group("prefix")
        start = match.start() + len(prefix)

    final_part = request[start:].strip()
    if final_part:
        raw_parts.append(final_part)

    parts = [
        re.sub(
            r"^\s*(?:and\s+)?(add|create|capture|remember)\s+another\s+"
            r"(task|todo|to-do|open loop)\b",
            r"\1 \2",
            part,
            count=1,
            flags=re.IGNORECASE,
        ).strip()
        for part in raw_parts
    ]
    return parts if len(parts) > 1 else [request]


def _bulk_image_fallback_tags(request: str) -> list[str]:
    split_request = _split_bulk_image_task_request(request)
    if split_request is None:
        return []
    _, image_text = split_request
    title = _image_list_title(image_text)
    return normalize_tags([title.replace(" ", "")]) if title else []


def _is_task_list_request(request: str) -> bool:
    lowered = request.lower()
    has_task_cue = re.search(r"\b(tasks?|todos?|to-dos?|open loops?)\b", lowered)
    has_list_verb = re.search(r"\b(show|list)\b", lowered) or re.search(
        r"\bgive\s+me\s+(?:a\s+)?list\b",
        lowered,
    )
    return has_task_cue is not None and has_list_verb is not None


OWNER_TARGET_RE = re.compile(
    rf"^\s*(?:assign|set|make|change|update|put)\s+"
    rf"(?P<target>.+?)\s+"
    rf"(?:to|for|owner\s+to|owner\s+as|as\s+owner)\s+"
    rf"(?P<owner>{OWNER_ALIAS_PATTERN})\b\.?\s*$",
    re.IGNORECASE,
)
OWNER_ONLY_RE = re.compile(
    rf"\b(?:owner(?:\s+of\s+(?:it|this|that|the\s+task))?|owned\s+by|"
    rf"assign(?:ed)?\s+to|belongs\s+to|for)\s*"
    rf"(?:is|:|to|as)?\s*(?P<owner>{OWNER_ALIAS_PATTERN})\b",
    re.IGNORECASE,
)
OWNER_AS_RE = re.compile(
    rf"\b(?P<owner>{OWNER_ALIAS_PATTERN})\s+as\s+(?:the\s+)?owner\b",
    re.IGNORECASE,
)
NOTE_UPDATE_RE = re.compile(
    r"^\s*(?:add|append|set|update|put)?\s*"
    r"(?:a\s+)?(?:note|notes|description|context)\s*"
    r"(?:is|are|to|:)?\s+(?P<note>.+?)\s*$",
    re.IGNORECASE,
)
TAG_UPDATE_RE = re.compile(
    r"^\s*(?:tags?|labels?)\s*:\s*"
    r"(?P<tags>#?[A-Za-z][A-Za-z0-9_-]*"
    r"(?:\s*,\s*#?[A-Za-z][A-Za-z0-9_-]*)*)\s*$",
    re.IGNORECASE,
)
ASSISTANT_UPDATE_RE = re.compile(
    r"\b(?:add|ask|have|queue|put|set\s+up)?\s*"
    r"(?P<assistant>noah|novah|ai\s+assistant|assistant)\b"
    r".*?\b(?:help|research|find|look\s+up|figure\s+out|call|email|draft)\b"
    r"(?P<help>.*)$",
    re.IGNORECASE,
)
PRONOUN_TARGETS = {"it", "this", "that", "this task", "that task", "the task"}
TASK_LIST_CONTEXT_KEY = "_n4os_task_list_id"


def _task_with_list_context(task: dict[str, Any], task_list_id: str) -> dict[str, Any]:
    contextual_task = dict(task)
    contextual_task[TASK_LIST_CONTEXT_KEY] = task_list_id
    return contextual_task


@dataclass(frozen=True)
class TaskUpdateRequest:
    title: str | None = None
    due: str | None = None
    owner: str | None = None
    note: str | None = None
    tags: list[str] = field(default_factory=list)
    assistant_help_request: str | None = None
    target: str | None = None


def _clean_task_target(value: str) -> str | None:
    cleaned = re.sub(r"\btask\b", "", value, flags=re.IGNORECASE).strip(" .")
    cleaned = re.sub(r"^(?:add|create|capture|remember)\s+", "", cleaned, flags=re.IGNORECASE)
    return cleaned or None


def _owner_from_request(request: str) -> tuple[str | None, str | None]:
    for pattern in (OWNER_ONLY_RE, OWNER_AS_RE):
        match = pattern.search(request)
        if match is not None:
            return OWNER_ALIASES.get(match.group("owner").strip().lower(), "unknown"), None

    target_match = OWNER_TARGET_RE.search(request)
    if target_match is not None:
        owner = OWNER_ALIASES.get(target_match.group("owner").strip().lower(), "unknown")
        target = " ".join(target_match.group("target").lower().split())
        if target in PRONOUN_TARGETS:
            return owner, None
        return owner, _clean_task_target(target_match.group("target"))

    return None, None


def _note_from_request(request: str) -> str | None:
    match = NOTE_UPDATE_RE.search(request)
    if match is None:
        return None
    note = match.group("note").strip(" .")
    return note or None


def _tags_from_request(request: str) -> list[str]:
    tags = extract_tags(request)
    match = TAG_UPDATE_RE.search(request)
    if match is not None:
        candidates = re.split(r"[\s,]+", match.group("tags"))
        tags = normalize_tags([*tags, *candidates])
    return tags


def _assistant_help_from_request(request: str) -> str | None:
    match = ASSISTANT_UPDATE_RE.search(request)
    if match is None:
        return None

    help_request = re.sub(
        r"^\s*(?:me\s+)?(?:(?:to|with|on|for)\s+)?"
        r"(?:(?:the|this|that)\s+task|it|this|that)?\s*",
        "",
        match.group("help"),
        flags=re.IGNORECASE,
    ).strip(" .,:;-")
    return help_request or "help with this task"


def _task_update_from_request(request: str) -> TaskUpdateRequest | None:
    owner, target = _owner_from_request(request)
    note = _note_from_request(request)
    tags = _tags_from_request(request)
    assistant_help = _assistant_help_from_request(request)
    if owner is None and note is None and not tags and assistant_help is None:
        return None
    return TaskUpdateRequest(
        owner=owner if owner != "unknown" else None,
        note=note,
        tags=tags,
        assistant_help_request=assistant_help,
        target=target,
    )


def _task_update_from_semantic_intent(intent: dict[str, Any]) -> TaskUpdateRequest | None:
    values = intent.get("update")
    if not isinstance(values, dict):
        return None
    title = str(values.get("title") or "").strip() or None
    due = str(values.get("due") or "").strip() or None
    owner = normalize_metadata({"owner": values.get("owner")}).get("owner")
    if owner == "unknown":
        owner = None
    note = str(values.get("notes") or "").strip() or None
    tags = normalize_tags(values.get("tags") if isinstance(values.get("tags"), list) else [])
    assistant_help = str(values.get("assistant_help_request") or "").strip() or None
    target = str(intent.get("query") or "").strip() or None
    if not any((title, due, owner, note, tags, assistant_help)):
        return None
    return TaskUpdateRequest(
        title=title,
        due=due,
        owner=owner,
        note=note,
        tags=tags,
        assistant_help_request=assistant_help,
        target=target,
    )


def _append_human_note(notes: str, note: str) -> str:
    if not notes.strip():
        return note
    return f"{notes.strip()}\n\n{note}"


def _format_tags(tags: list[str]) -> str:
    return " ".join(f"#{tag}" for tag in normalize_tags(tags))


def _set_note_tags(notes: str | None, tags: list[str]) -> str | None:
    merged_tags = normalize_tags(tags)
    if not notes and not merged_tags:
        return None

    kept_lines = []
    existing_tags = []
    for line in str(notes or "").splitlines():
        line_tags = extract_tags(line)
        if line.strip().lower().startswith("tags:") and line_tags:
            existing_tags.extend(line_tags)
            continue
        kept_lines.append(line)

    merged_tags = normalize_tags([*existing_tags, *merged_tags])
    body = "\n".join(kept_lines).strip()
    if not merged_tags:
        return body or None

    tag_line = f"Tags: {_format_tags(merged_tags)}"
    if body:
        return f"{body}\n\n{tag_line}"
    return tag_line


def _merge_task_tags(
    task: dict[str, Any],
    notes: str,
    metadata: dict[str, Any],
    new_tags: list[str],
) -> list[str]:
    return normalize_tags(
        [
            *list(metadata.get("tags") or []),
            *extract_tags(task.get("title")),
            *extract_tags(notes),
            *new_tags,
        ],
    )


def _merge_semantic_metadata(
    baseline: dict[str, Any] | None,
    extracted: dict[str, Any],
) -> dict[str, Any]:
    merged = normalize_metadata(baseline)
    semantic = normalize_metadata(extracted)
    for key in ("tags", "context", "requires", "can_do_while"):
        if semantic.get(key):
            merged[key] = list(dict.fromkeys([*merged.get(key, []), *semantic[key]]))
    for key in (
        "energy",
        "urgency",
        "complexity",
        "effort_type",
        "location",
        "owner",
        "assistant_name",
        "assistant_help_request",
        "assistant_context",
    ):
        value = semantic.get(key)
        if value not in (None, "", "unknown"):
            merged[key] = value
    if semantic.get("duration_minutes") is not None:
        merged["duration_minutes"] = semantic["duration_minutes"]
    if semantic.get("assistant_help_needed"):
        merged["assistant_help_needed"] = True
    return merged


@dataclass
class FamilyTasksClaw:
    """Small OpenClaw entry point for the Family Tasks claw."""

    tools: FamilyTaskTools
    system_prompt: str = SYSTEM_PROMPT
    tool_guidance: str = TOOL_GUIDANCE
    pending_action: PendingAction | None = None
    auto_run_assistant_help: bool = True
    last_created_task: dict[str, Any] | None = None
    last_result: dict[str, Any] | None = None
    undo_stack: list[dict[str, Any]] = field(default_factory=list)
    field_extractor: Any | None = None

    @classmethod
    def from_provider(cls, provider: TasksProvider) -> "FamilyTasksClaw":
        return cls(tools=FamilyTaskTools(provider))

    @classmethod
    def default(cls) -> "FamilyTasksClaw":
        extractor = TaskAIFieldExtractor.from_env_or_none() if TaskAIFieldExtractor is not None else None
        return cls(tools=build_default_tools(), field_extractor=extractor)

    def tool_map(self) -> dict[str, Any]:
        return {
            "list_task_lists": self.tools.list_task_lists,
            "create_task": self.tools.create_task,
            "list_tasks": self.tools.list_tasks,
            "update_task": self.tools.update_task,
            "complete_task": self.tools.complete_task,
            "delete_task": self.tools.delete_task,
            "recommend_tasks": self.tools.recommend_tasks,
            "run_assistant_help": self.run_noah_assistant_help,
            "undo_task_action": self.undo_last_action,
        }

    def add_task_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
        research_client: NoahResearchClient | None = None,
        *,
        require_confirmation: bool = False,
        semantic_intent: dict[str, Any] | None = None,
    ) -> str:
        bulk_titles = _bulk_image_task_titles(request)
        if bulk_titles:
            if require_confirmation:
                intents = self._bulk_image_task_intents(
                    request,
                    bulk_titles,
                    reference_time,
                    semantic_intent=semantic_intent,
                )
                return self._preview_task_creates(intents, reference_time=reference_time)
            return self._add_bulk_tasks_from_image_request(
                request,
                bulk_titles,
                reference_time=reference_time,
                semantic_intent=semantic_intent,
            )

        split_requests = _split_multiple_create_task_requests(request)
        if len(split_requests) > 1:
            if require_confirmation:
                intents = [self._extract_intent(item, reference_time) for item in split_requests]
                if semantic_intent is not None and semantic_intent.get("intent") == "create_task":
                    for intent in intents:
                        for key in ("notes", "due", "task_list_name", "task_list_id_hint"):
                            if semantic_intent.get(key) is not None:
                                intent[key] = semantic_intent[key]
                        if isinstance(semantic_intent.get("metadata"), dict):
                            intent["metadata"] = _merge_semantic_metadata(
                                intent.get("metadata"),
                                semantic_intent["metadata"],
                            )
                return self._preview_task_creates(intents, reference_time=reference_time)
            return self._add_multiple_tasks_from_requests(
                split_requests,
                reference_time=reference_time,
                research_client=research_client,
            )

        intent = (
            self._merge_create_intent(request, reference_time, semantic_intent)
            if semantic_intent is not None
            else self._extract_intent(request, reference_time)
        )
        if require_confirmation:
            return self._preview_task_creates([intent], reference_time=reference_time)
        message, _ = self._add_task_from_intent(
            intent,
            reference_time=reference_time,
            research_client=research_client,
        )
        return message

    def _extract_intent(
        self,
        request: str,
        reference_time: datetime | None,
    ) -> dict[str, Any]:
        extracted = self.interpret_request(request, reference_time=reference_time)
        return self._merge_create_intent(request, reference_time, extracted)

    def _merge_create_intent(
        self,
        request: str,
        reference_time: datetime | None,
        extracted: dict[str, Any],
    ) -> dict[str, Any]:
        baseline = extract_intent(request, now=reference_time)
        if extracted.get("intent") != "create_task":
            return baseline
        merged = dict(baseline)
        merged["intent"] = "create_task"
        for key in ("title", "notes", "due", "task_list_name", "task_list_id_hint"):
            if extracted.get(key) is not None:
                merged[key] = extracted[key]
        if isinstance(extracted.get("metadata"), dict):
            merged["metadata"] = _merge_semantic_metadata(
                baseline.get("metadata"),
                extracted["metadata"],
            )
        merged["missing_fields"] = list(extracted.get("missing_fields") or [])
        merged["assumptions"] = list(extracted.get("assumptions") or [])
        merged["clarification_question"] = extracted.get("clarification_question")
        merged["ai_field_extraction"] = extracted.get("ai_field_extraction")
        return merged

    def interpret_request(
        self,
        request: str,
        reference_time: datetime | None = None,
        *,
        semantic_image_path: str | None = None,
    ) -> dict[str, Any]:
        baseline = self.deterministic_intent(request, reference_time=reference_time)
        local_task_list_name = baseline.get("task_list_name")
        if self.field_extractor is None:
            return baseline
        try:
            extraction_context = {"last_created_task": self.last_created_task or {}}
            if semantic_image_path:
                extraction_context["semantic_image_path"] = semantic_image_path
            extracted = self.field_extractor.extract(
                request,
                now=reference_time,
                baseline_intent=baseline,
                context=extraction_context,
            )
            if local_task_list_name:
                extracted["task_list_name"] = local_task_list_name
                extracted["task_list_id_hint"] = None
            return extracted
        except Exception:
            return baseline

    def deterministic_intent(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> dict[str, Any]:
        local_task_list_name, local_request = _local_task_list_request(request)
        baseline = extract_intent(local_request, now=reference_time)
        if local_task_list_name:
            baseline["task_list_name"] = local_task_list_name
        return baseline

    def _bulk_image_task_intents(
        self,
        request: str,
        titles: list[str],
        reference_time: datetime | None,
        *,
        semantic_intent: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        caption = _bulk_image_task_caption(request)
        shared = self._extract_intent(f"Add task item {caption}", reference_time)
        if semantic_intent is not None and semantic_intent.get("intent") == "create_task":
            for key in ("notes", "due", "task_list_name", "task_list_id_hint"):
                if semantic_intent.get(key) is not None:
                    shared[key] = semantic_intent[key]
            if isinstance(semantic_intent.get("metadata"), dict):
                shared["metadata"] = _merge_semantic_metadata(
                    shared.get("metadata"),
                    semantic_intent["metadata"],
                )
        return [
            {
                **shared,
                "title": title,
                "missing_fields": [],
                "assumptions": sorted({*shared.get("assumptions", []), "image_text"}),
            }
            for title in titles
        ]

    def _preview_task_creates(
        self,
        intents: list[dict[str, Any]],
        *,
        reference_time: datetime | None = None,
    ) -> str:
        missing = sorted(
            {
                str(field)
                for intent in intents
                for field in intent.get("missing_fields", [])
                if field
            }
        )
        if missing:
            self.pending_action = PendingAction(
                action="clarify_create",
                create_intents=intents,
                reference_time=reference_time,
            )
            self.last_result = {"status": "needs_information"}
            question = next(
                (
                    str(intent.get("clarification_question"))
                    for intent in intents
                    if intent.get("clarification_question")
                ),
                None,
            )
            message = question or "Please provide: " + ", ".join(missing) + "."
            print(message)
            return message
        self.pending_action = PendingAction(
            action="confirm_create",
            create_intents=intents,
            reference_time=reference_time,
        )
        self.last_result = {"status": "needs_information"}
        lines = [f"I found {len(intents)} task{'s' if len(intents) != 1 else ''} to add:"]
        for index, intent in enumerate(intents, start=1):
            detail = str(intent.get("title") or "Untitled task")
            if intent.get("due"):
                detail += f" — due {str(intent['due'])[:10]}"
            if intent.get("task_list_name"):
                detail += f" — {intent['task_list_name']}"
            lines.append(f"{index}. {detail}")
        lines.append("Add all of these? yes/no")
        message = "\n".join(lines)
        print(message)
        return message

    def requires_create_confirmation(self, request: str) -> bool:
        return bool(
            _bulk_image_task_titles(request)
            or len(_split_multiple_create_task_requests(request)) > 1
        )

    def _correct_create_intent(
        self,
        intent: dict[str, Any],
        response: str,
        reference_time: datetime | None,
    ) -> dict[str, Any]:
        title = str(intent.get("title") or "task")
        revised = self._extract_intent(
            f"Add task {title}. Correction: {response}",
            reference_time,
        )
        merged = dict(intent)
        lowered = response.lower()
        if revised.get("due"):
            merged["due"] = revised["due"]
        for key in ("task_list_name", "task_list_id_hint"):
            if revised.get(key):
                merged[key] = revised[key]
        ai_revised_title = bool(revised.get("ai_field_extraction"))
        if (
            re.search(r"\b(?:title|rename|call it)\b", lowered) or ai_revised_title
        ) and revised.get("title"):
            merged["title"] = revised["title"]
        elif "instead" in lowered and not re.search(
            r"\b(?:due|list|owner|assign|tag|note|today|tomorrow|next|this|"
            r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|\d[/.-]\d",
            lowered,
        ):
            title_text = re.sub(r"\binstead\b", "", response, flags=re.IGNORECASE)
            title_text = re.sub(
                r"^\s*(?:change|make)\s+(?:it\s+)?(?:to\s+)?",
                "",
                title_text,
                flags=re.IGNORECASE,
            ).strip(" .,:;-")
            title_intent = extract_intent(f"Add task {title_text}", now=reference_time)
            if title_intent.get("title"):
                merged["title"] = title_intent["title"]
        if re.search(r"\b(?:note|notes|details)\b", lowered) and revised.get("notes"):
            merged["notes"] = revised["notes"]
        metadata = dict(intent.get("metadata") or {})
        revised_metadata = revised.get("metadata") or {}
        owner = revised_metadata.get("owner")
        if owner and owner != "unknown":
            metadata["owner"] = owner
        if re.search(r"\b(?:tag|tags|label|labels)\b", lowered):
            tags = revised_metadata.get("tags")
            if tags:
                metadata["tags"] = tags
        if revised_metadata.get("assistant_help_needed"):
            for key in (
                "assistant_help_needed",
                "assistant_name",
                "assistant_help_request",
                "assistant_context",
            ):
                if revised_metadata.get(key) not in (None, ""):
                    metadata[key] = revised_metadata[key]
        merged["metadata"] = metadata
        merged["missing_fields"] = list(revised.get("missing_fields") or [])
        merged["clarification_question"] = revised.get("clarification_question")
        return merged

    def _add_task_from_intent(
        self,
        intent: dict[str, Any],
        reference_time: datetime | None = None,
        research_client: NoahResearchClient | None = None,
        print_message: bool = True,
    ) -> tuple[str, dict[str, Any] | None]:
        missing = intent.get("missing_fields", [])
        if intent.get("intent") != "create_task":
            message = "That does not look like a task creation request."
            if print_message:
                print(message)
            return message, None
        if missing:
            message = "Please provide: " + ", ".join(missing) + "."
            if print_message:
                print(message)
            return message, None

        metadata = intent.get("metadata") or {}
        notes = _set_note_tags(intent.get("notes"), metadata.get("tags") or [])
        task_list_id, task_list_error = self._resolve_task_list(intent)
        if task_list_error is not None:
            self.last_result = {"status": "needs_information"}
            if print_message:
                print(task_list_error)
            return task_list_error, None
        response = self.tools.create_task(
            title=intent["title"],
            notes=notes,
            due=intent.get("due"),
            metadata=metadata,
            task_list_id=task_list_id,
        )
        if response["status"] != "ok":
            message = response["message"]
            if print_message:
                print(message)
            return message, None

        task = response.get("data", {}).get("task", {})
        self.last_created_task = _task_with_list_context(task, task_list_id) if task else None
        if task.get("id"):
            self.undo_stack.append(
                {
                    "action": "delete_task",
                    "task": dict(task),
                    "task_list_id": task_list_id,
                },
            )
        message = _format_created_task_message(task)
        if self.auto_run_assistant_help:
            assistant_result = self._run_noah_assistant_help_for_task(
                task,
                research_client=research_client,
                reference_time=reference_time,
                task_list_id=task_list_id,
            )
            if assistant_result:
                message = f"{message}\n{assistant_result}"
        else:
            assistant_acknowledgment = _format_assistant_acknowledgment(
                intent.get("metadata"),
            )
            if assistant_acknowledgment:
                message = f"{message}\n{assistant_acknowledgment}"
        if print_message:
            print(message)
        return message, dict(task) if task else None

    def _resolve_task_list(self, intent: dict[str, Any]) -> tuple[str, str | None]:
        hint = str(intent.get("task_list_id_hint") or "").strip()
        name = str(intent.get("task_list_name") or "").strip()
        if not hint and not name:
            return DEFAULT_TASK_LIST_ID, None
        response = self.tools.list_task_lists()
        if response["status"] != "ok":
            return DEFAULT_TASK_LIST_ID, response["message"]
        requested = _normalized_task_list_name(name or hint)
        matches = [
            item
            for item in response.get("data", {}).get("task_lists", [])
            if str(item.get("id") or "") == hint
            or _normalized_task_list_name(str(item.get("title") or "")) == requested
        ]
        if len(matches) != 1:
            return DEFAULT_TASK_LIST_ID, f"I couldn't uniquely find the task list {name or hint}."
        return str(matches[0]["id"]), None

    def _add_multiple_tasks_from_requests(
        self,
        requests: list[str],
        reference_time: datetime | None = None,
        research_client: NoahResearchClient | None = None,
    ) -> str:
        created_tasks: list[dict[str, Any]] = []
        messages: list[str] = []
        failed: list[str] = []
        undo_start = len(self.undo_stack)

        for request in requests:
            intent = self._extract_intent(request, reference_time)
            message, task = self._add_task_from_intent(
                intent,
                reference_time=reference_time,
                research_client=research_client,
                print_message=False,
            )
            if task is None:
                failed.append(message)
                continue
            created_tasks.append(task)
            messages.append(message)

        if created_tasks:
            created_undo = self.undo_stack[undo_start:]
            del self.undo_stack[undo_start:]
            undo_entries = [
                {
                    "task": dict(entry["task"]),
                    "task_list_id": entry.get("task_list_id", DEFAULT_TASK_LIST_ID),
                }
                for entry in created_undo
                if entry.get("action") == "delete_task" and isinstance(entry.get("task"), dict)
            ]
            if undo_entries:
                self.undo_stack.append(
                    {
                        "action": "delete_tasks",
                        "task_entries": undo_entries,
                    },
                )
            if undo_entries:
                self.last_created_task = _task_with_list_context(
                    created_tasks[-1],
                    undo_entries[-1]["task_list_id"],
                )

        if not created_tasks:
            message = "Could not create tasks:\n" + "\n".join(f"- {failure}" for failure in failed)
            print(message)
            return message

        lines = [f"Created {len(created_tasks)} tasks:"]
        lines.extend(f"- {task.get('title') or 'Untitled task'}" for task in created_tasks)
        if failed:
            lines.append("Some tasks failed:")
            lines.extend(f"- {failure}" for failure in failed)
        if any("\n" in message for message in messages):
            lines.append("")
            lines.extend(messages)
        message = "\n".join(lines)
        print(message)
        return message

    def _add_bulk_tasks_from_image_request(
        self,
        request: str,
        titles: list[str],
        reference_time: datetime | None = None,
        *,
        semantic_intent: dict[str, Any] | None = None,
    ) -> str:
        shared_intent = self._bulk_image_task_intents(
            request,
            titles,
            reference_time,
            semantic_intent=semantic_intent,
        )
        shared_intent = shared_intent[0]
        metadata = shared_intent.get("metadata") or {}
        if not metadata.get("tags"):
            metadata = dict(metadata)
            metadata["tags"] = _bulk_image_fallback_tags(request)
        notes = _set_note_tags(shared_intent.get("notes"), metadata.get("tags") or [])
        task_list_id, task_list_error = self._resolve_task_list(shared_intent)
        if task_list_error is not None:
            print(task_list_error)
            return task_list_error
        created_tasks: list[dict[str, Any]] = []
        failures: list[str] = []

        for title in titles:
            response = self.tools.create_task(
                title=title,
                notes=notes,
                due=shared_intent.get("due"),
                metadata=metadata,
                task_list_id=task_list_id,
            )
            if response["status"] != "ok":
                failures.append(f"{title}: {response['message']}")
                continue
            task = response.get("data", {}).get("task", {})
            if task:
                created_tasks.append(dict(task))

        if created_tasks:
            self.last_created_task = _task_with_list_context(created_tasks[-1], task_list_id)
            undo_tasks = [task for task in created_tasks if task.get("id")]
            if undo_tasks:
                self.undo_stack.append(
                    {
                        "action": "delete_tasks",
                        "tasks": undo_tasks,
                        "task_list_id": task_list_id,
                    },
                )

        if failures and not created_tasks:
            message = "Could not create image tasks:\n" + "\n".join(f"- {failure}" for failure in failures)
            print(message)
            return message

        message = f"Created {len(created_tasks)} tasks from the image."
        if failures:
            message += "\nSome entries failed:\n" + "\n".join(f"- {failure}" for failure in failures)
        print(message)
        return message

    def assign_owner_from_request(
        self,
        request: str,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
    ) -> str:
        return self.update_task_from_request(request, task_list_id=task_list_id)

    def update_task_from_request(
        self,
        request: str,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
        *,
        task_id: str | None = None,
        semantic_intent: dict[str, Any] | None = None,
    ) -> str:
        self.last_result = {"status": "needs_information"}
        semantic_update = (
            _task_update_from_semantic_intent(semantic_intent)
            if semantic_intent is not None
            else None
        )
        if semantic_update is not None and semantic_update.target is None and not task_id:
            message = "Please provide which task to update."
            print(message)
            return message
        update = semantic_update or _task_update_from_request(request)
        if update is None:
            message = "Please say what to update on the task."
            print(message)
            return message

        if task_id:
            response = self.tools.list_tasks(
                task_list_id=task_list_id,
                show_completed=True,
            )
            if response["status"] != "ok":
                self.last_result = response
                message = response["message"]
                print(message)
                return message
            task = next(
                (
                    candidate
                    for candidate in response.get("data", {}).get("tasks", [])
                    if str(candidate.get("id") or "") == task_id
                ),
                None,
            )
            if task is None:
                self.last_result = {"status": "error"}
                message = "I couldn't find the selected task."
                print(message)
                return message
            return self._update_task(task, update, task_list_id)

        if update.target is None:
            task = self.last_created_task
            if task is None:
                message = "I do not know which task to update."
                print(message)
                return message
            task_list_id = str(task.get(TASK_LIST_CONTEXT_KEY) or task_list_id)
            return self._update_task(task, update, task_list_id)

        response = self.tools.list_tasks(task_list_id=task_list_id)
        if response["status"] != "ok":
            self.last_result = response
            message = response["message"]
            print(message)
            return message

        matches = match_tasks(update.target, response.get("data", {}).get("tasks", []))
        if not matches:
            message = "I couldn't find a matching task. Try including more of the title."
            print(message)
            return message
        if len(matches) > 1:
            lines = ["Multiple matching tasks found. Which one should I update?"]
            for index, task in enumerate(matches, start=1):
                lines.append(f"{index}. {_format_task_choice(task)}")
            message = "\n".join(lines)
            self.pending_action = PendingAction(
                action="update",
                choices=matches,
                update=update,
                task_list_id=task_list_id,
            )
            print(message)
            return message
        return self._update_task(matches[0], update, task_list_id)

    def _update_task(
        self,
        task: dict[str, Any],
        update: TaskUpdateRequest,
        task_list_id: str,
    ) -> str:
        task_id = task.get("id")
        if not task_id:
            message = "Matching task has no Google Tasks id, so I did not update it."
            print(message)
            return message

        notes, metadata = _task_notes_and_metadata(task)
        changes = []
        if update.title is not None:
            changes.append("title")
        if update.due is not None:
            changes.append(f"due={update.due[:10]}")
        if update.owner is not None:
            metadata["owner"] = update.owner
            changes.append(f"owner={update.owner}")
        if update.note is not None:
            notes = _append_human_note(notes, update.note)
            changes.append("note")
        if update.tags:
            tags = _merge_task_tags(task, notes, metadata, update.tags)
            metadata["tags"] = tags
            notes = _set_note_tags(notes, tags) or ""
            changes.append(f"tags={_format_tags(update.tags)}")
        if update.assistant_help_request is not None:
            metadata["assistant_help_needed"] = True
            metadata["assistant_name"] = "Noah"
            metadata["assistant_help_request"] = update.assistant_help_request
            metadata["assistant_help_status"] = "queued"
            changes.append("Noah help")

        if not changes:
            message = "Please say what to update on the task."
            print(message)
            return message

        response = self.tools.update_task(
            task_id=task_id,
            title=update.title,
            due=update.due,
            notes=notes,
            metadata=metadata,
            task_list_id=task_list_id,
        )
        self.last_result = response
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message

        updated = response.get("data", {}).get("task", {})
        if updated:
            merged_task = dict(task)
            merged_task.update(updated)
            self.last_created_task = _task_with_list_context(merged_task, task_list_id)
        self.undo_stack.append(
            {
                "action": "restore_task",
                "task": dict(task),
                "task_list_id": task_list_id,
            },
        )
        title = updated.get("title") or task.get("title") or "task"
        message = f"Updated task ({', '.join(changes)}): {title}."
        print(message)
        return message

    def _run_noah_assistant_help_for_task(
        self,
        task: dict[str, Any],
        *,
        research_client: NoahResearchClient | None,
        reference_time: datetime | None,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
    ) -> str | None:
        if not _is_pending_assistant_help_task(task):
            return None

        task_id = str(task.get("id") or "").strip()
        title = _assistant_task_title(task)
        notes, metadata = _task_notes_and_metadata(task)
        if not task_id:
            return f"Noah could not complete assistant help for {title}: missing Google Tasks id."

        try:
            client = research_client or OpenClawNoahResearchClient.from_env()
        except RuntimeError as error:
            return (
                f"Noah could not start assistant help for {title}: {error} "
                "The task remains queued for Run Noah assistant help."
            )

        help_request = str(metadata.get("assistant_help_request") or title).strip()
        assistant_context = str(metadata.get("assistant_context") or "").strip()
        attempted_at = _default_now(reference_time)
        try:
            result = client.research(
                task_title=title,
                help_request=help_request,
                assistant_context=assistant_context,
            )
        except Exception as error:
            error_metadata = _errored_assistant_metadata(metadata, error, attempted_at)
            self.tools.update_task(
                task_id=task_id,
                notes=notes,
                metadata=error_metadata,
                task_list_id=task_list_id,
            )
            return f"Noah could not complete assistant help for {title}: {error}"

        completed_at = _default_now(reference_time)
        updated_notes = _append_noah_result_notes(
            task.get("notes"),
            result,
            completed_at,
        )
        update_response = self.tools.update_task(
            task_id=task_id,
            notes=updated_notes,
            metadata=_completed_assistant_metadata(metadata, result, completed_at),
            task_list_id=task_list_id,
        )
        if update_response["status"] != "ok":
            return f"Noah could not save assistant help for {title}: {update_response['message']}"

        summary = _summarize_result(result.text, max_chars=160)
        return f"Noah completed assistant help for {title}: {summary} Saved in task notes."

    def recommend_tasks_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
        *,
        semantic_intent: dict[str, Any] | None = None,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
    ) -> str:
        intent = semantic_intent or self.interpret_request(request, reference_time=reference_time)
        if task_list_id == DEFAULT_TASK_LIST_ID and (
            intent.get("task_list_name") or intent.get("task_list_id_hint")
        ):
            task_list_id, task_list_error = self._resolve_task_list(intent)
            if task_list_error is not None:
                print(task_list_error)
                return task_list_error
        filters = intent.get("filters", {})
        response = self.tools.recommend_tasks(filters=filters, task_list_id=task_list_id)
        self.last_result = response
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message

        data = response.get("data", {})
        heading = "Matching tasks:" if _is_task_list_request(request) else "Recommended tasks:"
        message = _format_recommendations(
            data.get("recommendations") or data.get("tasks", []),
            heading=heading,
        )
        print(message)
        return message

    def run_noah_assistant_help_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
    ) -> str:
        return self.run_noah_assistant_help(
            limit=_assistant_run_limit_from_request(request),
            reference_time=reference_time,
            task_list_id=task_list_id,
        )

    def run_noah_assistant_help(
        self,
        *,
        research_client: NoahResearchClient | None = None,
        limit: int | None = NOAH_ASSISTANT_DEFAULT_LIMIT,
        reference_time: datetime | None = None,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
    ) -> str:
        response = self.tools.list_tasks(task_list_id=task_list_id, show_completed=False)
        if response["status"] != "ok":
            self.last_result = response
            message = response["message"]
            print(message)
            return message

        tasks = response.get("data", {}).get("tasks", [])
        pending_tasks = [
            task
            for task in tasks
            if _is_pending_assistant_help_task(task)
        ]
        if not pending_tasks:
            self.last_result = {"status": "not_counted"}
            message = "No pending Noah assistant help tasks found."
            print(message)
            return message

        try:
            client = research_client or OpenClawNoahResearchClient.from_env()
        except RuntimeError as error:
            self.last_result = {"status": "error"}
            message = str(error)
            print(message)
            return message

        selected_tasks = pending_tasks if limit is None else pending_tasks[:limit]
        completed: list[tuple[str, NoahResearchResult]] = []
        failed: list[str] = []
        for task in selected_tasks:
            task_id = str(task.get("id") or "").strip()
            title = _assistant_task_title(task)
            notes, metadata = _task_notes_and_metadata(task)
            if not task_id:
                failed.append(f"{title}: missing Google Tasks id")
                continue

            help_request = str(metadata.get("assistant_help_request") or title).strip()
            assistant_context = str(metadata.get("assistant_context") or "").strip()
            attempted_at = _default_now(reference_time)
            try:
                result = client.research(
                    task_title=title,
                    help_request=help_request,
                    assistant_context=assistant_context,
                )
            except Exception as error:
                error_metadata = _errored_assistant_metadata(
                    metadata,
                    error,
                    attempted_at,
                )
                update_response = self.tools.update_task(
                    task_id=task_id,
                    notes=notes,
                    metadata=error_metadata,
                    task_list_id=task_list_id,
                )
                if update_response["status"] != "ok":
                    failed.append(f"{title}: {update_response['message']}")
                else:
                    failed.append(f"{title}: {error}")
                continue

            completed_at = _default_now(reference_time)
            updated_notes = _append_noah_result_notes(
                task.get("notes"),
                result,
                completed_at,
            )
            update_response = self.tools.update_task(
                task_id=task_id,
                notes=updated_notes,
                metadata=_completed_assistant_metadata(metadata, result, completed_at),
                task_list_id=task_list_id,
            )
            if update_response["status"] != "ok":
                failed.append(f"{title}: {update_response['message']}")
                continue

            completed.append((title, result))

        lines: list[str] = []
        if completed:
            lines.append(f"Noah completed {len(completed)} assistant help task(s).")
            for title, result in completed:
                lines.append(f"- {title}: {_summarize_result(result.text, max_chars=160)}")
        if failed:
            lines.append(f"Noah could not complete {len(failed)} assistant help task(s).")
            lines.extend(f"- {failure}" for failure in failed)

        remaining = len(pending_tasks) - len(selected_tasks)
        if remaining > 0:
            lines.append(f"{remaining} pending Noah assistant help task(s) still queued.")

        message = "\n".join(lines) if lines else "Noah had no assistant help updates."
        self.last_result = {"status": "ok" if completed else "error"}
        print(message)
        return message

    def complete_task_from_request(
        self,
        request: str,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
        *,
        task_id: str | None = None,
        query: str | None = None,
    ) -> str:
        return self._destructive_task_from_request(
            request=request,
            action="complete",
            task_list_id=task_list_id,
            task_id=task_id,
            query=query,
        )

    def delete_task_from_request(
        self,
        request: str,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
        *,
        task_id: str | None = None,
        query: str | None = None,
    ) -> str:
        return self._destructive_task_from_request(
            request=request,
            action="delete",
            task_list_id=task_list_id,
            task_id=task_id,
            query=query,
        )

    def _destructive_task_from_request(
        self,
        request: str,
        action: str,
        task_list_id: str,
        task_id: str | None = None,
        query: str | None = None,
    ) -> str:
        self.last_result = {"status": "needs_information"}
        intent = extract_intent(request)
        query = query or intent.get("query")
        normalized_query = " ".join(str(query or "").lower().split())
        if not task_id and normalized_query in PRONOUN_TARGETS and self.last_created_task:
            task_id = str(self.last_created_task.get("id") or "") or None
            task_list_id = str(
                self.last_created_task.get(TASK_LIST_CONTEXT_KEY) or task_list_id,
            )
        if not query and not task_id:
            message = f"Please provide which task to {action}."
            print(message)
            return message

        response = self.tools.list_tasks(
            task_list_id=task_list_id,
            show_completed=bool(task_id),
        )
        if response["status"] != "ok":
            self.last_result = response
            message = response["message"]
            print(message)
            return message

        tasks = response.get("data", {}).get("tasks", [])
        matches = (
            [task for task in tasks if str(task.get("id") or "") == task_id]
            if task_id
            else match_tasks(query, tasks)
        )
        if not matches:
            if task_id:
                self.last_result = {"status": "error"}
            message = "I couldn't find a matching task. Try including more of the title."
            print(message)
            return message

        if len(matches) > 1:
            lines = [f"Multiple matching tasks found. Which one should I {action}?"]
            for index, task in enumerate(matches, start=1):
                lines.append(f"{index}. {_format_task_choice(task)}")
            message = "\n".join(lines)
            self.pending_action = PendingAction(
                action=action,
                choices=matches,
                task_list_id=task_list_id,
            )
            print(message)
            return message

        task = matches[0]
        self.pending_action = PendingAction(
            action=action,
            task=task,
            task_list_id=task_list_id,
        )
        message = f"I found this task: {_format_task_choice(task)}. {action.title()} it? yes/no"
        print(message)
        return message

    def handle_pending_response(self, response: str) -> bool:
        if self.pending_action is None:
            return False

        command = response.strip().lower()
        affirmative = AFFIRMATIVE_RE.fullmatch(response.strip()) is not None
        negative = NEGATIVE_RE.fullmatch(response.strip()) is not None
        pending = self.pending_action
        if negative:
            self.pending_action = None
            self.last_result = {"status": "not_counted"}
            print("Okay, I did not change any tasks.")
            return True

        if pending.action == "clarify_create":
            if affirmative:
                self.last_result = {"status": "needs_information"}
                question = next(
                    (
                        str(intent.get("clarification_question"))
                        for intent in pending.create_intents or []
                        if intent.get("clarification_question")
                    ),
                    None,
                )
                print(question or "Please provide the missing task details before confirming.")
                return True
            corrected = []
            for intent in pending.create_intents or []:
                missing = set(intent.get("missing_fields") or [])
                if "title" in missing or "task" in missing:
                    revised = self._extract_intent(
                        f"Add task {response}",
                        pending.reference_time,
                    )
                    completed = dict(intent)
                    for key in (
                        "title",
                        "notes",
                        "due",
                        "task_list_name",
                        "task_list_id_hint",
                    ):
                        if revised.get(key) is not None:
                            completed[key] = revised[key]
                    if isinstance(revised.get("metadata"), dict):
                        completed["metadata"] = _merge_semantic_metadata(
                            intent.get("metadata"),
                            revised["metadata"],
                        )
                    missing_fields = set(intent.get("missing_fields") or [])
                    missing_fields.update(revised.get("missing_fields") or [])
                    supplied = {
                        "title": bool(completed.get("title")),
                        "task": bool(completed.get("title")),
                        "task_list": bool(
                            completed.get("task_list_name")
                            or completed.get("task_list_id_hint")
                        ),
                    }
                    completed["missing_fields"] = sorted(
                        field
                        for field in missing_fields
                        if not supplied.get(field, False)
                    )
                    completed["clarification_question"] = None
                    revised = completed
                elif "task_list" in missing:
                    revised = self._correct_create_intent(
                        intent,
                        f"use task list {response}",
                        pending.reference_time,
                    )
                else:
                    revised = self._correct_create_intent(
                        intent,
                        response,
                        pending.reference_time,
                    )
                corrected.append(revised)
            self.pending_action = None
            self._preview_task_creates(
                corrected,
                reference_time=pending.reference_time,
            )
            return True

        if pending.action == "confirm_create":
            if not affirmative:
                if re.search(
                    r"\b(?:instead|change|make|due|on|tomorrow|today|next|this|"
                    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
                    r"list|owner|assign|tag|note|title|noah|assistant|help)\b",
                    command,
                ):
                    corrected = [
                        self._correct_create_intent(intent, response, pending.reference_time)
                        for intent in pending.create_intents or []
                    ]
                    self.pending_action = None
                    self._preview_task_creates(
                        corrected,
                        reference_time=pending.reference_time,
                    )
                    return True
                print("Please answer yes to add the tasks, or no to cancel.")
                return True
            self.pending_action = None
            intents = list(pending.create_intents or [])
            created: list[dict[str, Any]] = []
            failures: list[str] = []
            undo_start = len(self.undo_stack)
            for intent in intents:
                message, task = self._add_task_from_intent(intent, print_message=False)
                if task is None:
                    failures.append(message)
                else:
                    created.append(task)
            created_undo = self.undo_stack[undo_start:]
            del self.undo_stack[undo_start:]
            undo_entries = [
                {
                    "task": dict(entry["task"]),
                    "task_list_id": entry.get("task_list_id", DEFAULT_TASK_LIST_ID),
                }
                for entry in created_undo
                if entry.get("action") == "delete_task" and isinstance(entry.get("task"), dict)
            ]
            if undo_entries:
                self.undo_stack.append(
                    {
                        "action": "delete_tasks",
                        "task_entries": undo_entries,
                    },
                )
            self.last_result = {"status": "ok" if created else "error"}
            lines = [f"Created {len(created)} task{'s' if len(created) != 1 else ''}."]
            if failures:
                lines.append(f"{len(failures)} could not be created.")
                lines.extend(f"- {failure}" for failure in failures)
            print("\n".join(lines))
            return True

        if pending.choices is not None and command.isdigit():
            index = int(command) - 1
            if index < 0 or index >= len(pending.choices):
                print("Please choose one of the listed task numbers.")
                return True
            pending.task = pending.choices[index]
            pending.choices = None
            if pending.action == "update" and pending.update is not None:
                self._update_task(pending.task, pending.update, pending.task_list_id)
                self.pending_action = None
                return True
            print(f"Selected {_format_task_choice(pending.task)}. Confirm yes/no.")
            return True

        if not affirmative:
            print("Please answer yes or no.")
            return True

        task = pending.task
        if task is None:
            print("Please choose a task number first.")
            return True

        task_id = task.get("id")
        if not task_id:
            self.pending_action = None
            self.last_result = {"status": "error"}
            print("Matching task has no Google Tasks id, so I did not change it.")
            return True

        if pending.action == "complete":
            result = self.tools.complete_task(
                task_id=task_id,
                task_list_id=pending.task_list_id,
                confirmed=True,
            )
            if result["status"] == "ok":
                self.undo_stack.append(
                    {
                        "action": "restore_task",
                        "task": dict(task),
                        "task_list_id": pending.task_list_id,
                    },
                )
        elif pending.action == "delete":
            result = self.tools.delete_task(
                task_id=task_id,
                task_list_id=pending.task_list_id,
                confirmed=True,
            )
            if result["status"] == "ok":
                self.undo_stack.append(
                    {
                        "action": "recreate_task",
                        "task": dict(task),
                        "task_list_id": pending.task_list_id,
                    },
                )
        else:
            result = {"status": "error", "message": "Unknown pending task action."}

        self.pending_action = None
        self.last_result = result
        print(result["message"])
        return True

    def undo_last_action(self) -> str:
        if not self.undo_stack:
            message = "Nothing to undo for Family Tasks."
            print(message)
            return message

        undo = self.undo_stack.pop()
        task = undo.get("task", {})
        task_id = task.get("id")
        task_list_id = undo.get("task_list_id", DEFAULT_TASK_LIST_ID)
        if undo.get("action") == "delete_task":
            response = self.tools.delete_task(
                task_id=task_id,
                task_list_id=task_list_id,
                confirmed=True,
            )
            message = (
                f"Undid task creation: deleted {_assistant_task_title(task)}."
                if response["status"] == "ok"
                else response["message"]
            )
            print(message)
            return message

        if undo.get("action") == "delete_tasks":
            task_entries = undo.get("task_entries")
            if not isinstance(task_entries, list):
                task_entries = [
                    {"task": item, "task_list_id": task_list_id}
                    for item in undo.get("tasks", [])
                ]
            task_entries = [
                entry
                for entry in task_entries
                if isinstance(entry, dict)
                and isinstance(entry.get("task"), dict)
                and entry["task"].get("id")
            ]
            failed = []
            for entry in task_entries:
                undo_task = entry["task"]
                response = self.tools.delete_task(
                    task_id=undo_task.get("id"),
                    task_list_id=entry.get("task_list_id", DEFAULT_TASK_LIST_ID),
                    confirmed=True,
                )
                if response["status"] != "ok":
                    failed.append(response["message"])
            if failed:
                message = "Some image-created tasks could not be deleted:\n" + "\n".join(
                    f"- {failure}" for failure in failed
                )
            else:
                message = f"Undid image task creation: deleted {len(task_entries)} tasks."
            print(message)
            return message

        if undo.get("action") == "restore_task":
            response = self.tools.update_task(
                task_id=task_id,
                title=task.get("title"),
                notes=task.get("notes"),
                due=task.get("due"),
                status=task.get("status") or "needsAction",
                task_list_id=task_list_id,
            )
            message = (
                f"Undid task completion: restored {_assistant_task_title(task)}."
                if response["status"] == "ok"
                else response["message"]
            )
            print(message)
            return message

        if undo.get("action") == "recreate_task":
            response = self.tools.create_task(
                title=task.get("title"),
                notes=task.get("notes"),
                due=task.get("due"),
                task_list_id=task_list_id,
            )
            message = (
                f"Undid task deletion: recreated {_assistant_task_title(task)}."
                if response["status"] == "ok"
                else response["message"]
            )
            print(message)
            return message

        message = "I do not know how to undo that Family Tasks action."
        print(message)
        return message


def handle_task_request(claw: FamilyTasksClaw, request: str) -> None:
    if claw.handle_pending_response(request):
        return

    intent = claw.interpret_request(request)
    task_list_id, task_list_error = claw._resolve_task_list(intent)
    if task_list_error is not None:
        print(task_list_error)
        return
    if intent["intent"] == "create_task":
        claw.add_task_from_request(request, semantic_intent=intent)
    elif intent["intent"] == "complete_task":
        claw.complete_task_from_request(
            request,
            task_list_id=task_list_id,
            query=intent.get("query"),
        )
    elif intent["intent"] == "delete_task":
        claw.delete_task_from_request(
            request,
            task_list_id=task_list_id,
            query=intent.get("query"),
        )
    elif intent["intent"] == "update_task":
        claw.update_task_from_request(
            request,
            task_list_id=task_list_id,
            semantic_intent=intent,
        )
    elif intent["intent"] == "run_assistant_help":
        claw.run_noah_assistant_help_from_request(request, task_list_id=task_list_id)
    else:
        claw.recommend_tasks_from_request(
            request,
            semantic_intent=intent,
            task_list_id=task_list_id,
        )


def run_interactive(claw: FamilyTasksClaw | None = None) -> None:
    active_claw = claw
    print("Family Tasks Claw. Type a task request, or 'exit' to quit.")
    while True:
        try:
            request = input("> ").strip()
        except EOFError:
            print()
            return

        if not request:
            continue
        if request.lower() in ("exit", "quit"):
            return

        if active_claw is None:
            active_claw = FamilyTasksClaw.default()

        handle_task_request(active_claw, request)


def run_cli(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    request = " ".join(args).strip()
    if not request:
        run_interactive()
        return

    claw = FamilyTasksClaw.default()
    handle_task_request(claw, request)


if __name__ == "__main__":
    run_cli()

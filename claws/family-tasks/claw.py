from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
import sys
from typing import Any

from constants import DEFAULT_TASK_LIST_ID
from intent import (
    OWNER_ALIAS_PATTERN,
    OWNER_ALIASES,
    extract_intent,
    extract_tags,
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
IMAGE_TEXT_MARKER = "Image text:"
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


@dataclass
class PendingAction:
    action: str
    task: dict[str, Any] | None = None
    choices: list[dict[str, Any]] | None = None
    update: TaskUpdateRequest | None = None
    task_list_id: str = DEFAULT_TASK_LIST_ID


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
    if IMAGE_TEXT_MARKER.lower() not in request.lower() or BULK_IMAGE_TASK_CUE_RE.search(request) is None:
        return None
    _, image_text = re.split(
        re.escape(IMAGE_TEXT_MARKER),
        request,
        maxsplit=1,
        flags=re.IGNORECASE,
    )
    caption = re.split(
        re.escape(IMAGE_TEXT_MARKER),
        request,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return caption, image_text


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
    rf"\b(?:owner|owned\s+by|assign(?:ed)?\s+to|belongs\s+to|for)\s*"
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


@dataclass(frozen=True)
class TaskUpdateRequest:
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
    target_match = OWNER_TARGET_RE.search(request)
    if target_match is not None:
        owner = OWNER_ALIASES.get(target_match.group("owner").strip().lower(), "unknown")
        target = " ".join(target_match.group("target").lower().split())
        if target in PRONOUN_TARGETS:
            return owner, None
        return owner, _clean_task_target(target_match.group("target"))

    for pattern in (OWNER_ONLY_RE, OWNER_AS_RE):
        match = pattern.search(request)
        if match is not None:
            return OWNER_ALIASES.get(match.group("owner").strip().lower(), "unknown"), None
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


@dataclass
class FamilyTasksClaw:
    """Small OpenClaw entry point for the Family Tasks claw."""

    tools: FamilyTaskTools
    system_prompt: str = SYSTEM_PROMPT
    tool_guidance: str = TOOL_GUIDANCE
    pending_action: PendingAction | None = None
    auto_run_assistant_help: bool = True
    last_created_task: dict[str, Any] | None = None
    undo_stack: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_provider(cls, provider: TasksProvider) -> "FamilyTasksClaw":
        return cls(tools=FamilyTaskTools(provider))

    @classmethod
    def default(cls) -> "FamilyTasksClaw":
        return cls(tools=build_default_tools())

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
    ) -> str:
        bulk_titles = _bulk_image_task_titles(request)
        if bulk_titles:
            return self._add_bulk_tasks_from_image_request(
                request,
                bulk_titles,
                reference_time=reference_time,
            )

        intent = extract_intent(request, now=reference_time)
        missing = intent.get("missing_fields", [])
        if intent.get("intent") != "create_task":
            message = "That does not look like a task creation request."
            print(message)
            return message
        if missing:
            message = "Please provide: " + ", ".join(missing) + "."
            print(message)
            return message

        metadata = intent.get("metadata") or {}
        notes = _set_note_tags(intent.get("notes"), metadata.get("tags") or [])
        response = self.tools.create_task(
            title=intent["title"],
            notes=notes,
            due=intent.get("due"),
            metadata=metadata,
        )
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message

        task = response.get("data", {}).get("task", {})
        self.last_created_task = dict(task) if task else None
        if task.get("id"):
            self.undo_stack.append(
                {
                    "action": "delete_task",
                    "task": dict(task),
                    "task_list_id": DEFAULT_TASK_LIST_ID,
                },
            )
        message = _format_created_task_message(task)
        if self.auto_run_assistant_help:
            assistant_result = self._run_noah_assistant_help_for_task(
                task,
                research_client=research_client,
                reference_time=reference_time,
            )
            if assistant_result:
                message = f"{message}\n{assistant_result}"
        else:
            assistant_acknowledgment = _format_assistant_acknowledgment(
                intent.get("metadata"),
            )
            if assistant_acknowledgment:
                message = f"{message}\n{assistant_acknowledgment}"
        print(message)
        return message

    def _add_bulk_tasks_from_image_request(
        self,
        request: str,
        titles: list[str],
        reference_time: datetime | None = None,
    ) -> str:
        caption = _bulk_image_task_caption(request)
        shared_intent = extract_intent(
            f"Add task {titles[0]} {caption}",
            now=reference_time,
        )
        metadata = shared_intent.get("metadata") or {}
        if not metadata.get("tags"):
            metadata = dict(metadata)
            metadata["tags"] = _bulk_image_fallback_tags(request)
        notes = _set_note_tags(None, metadata.get("tags") or [])
        created_tasks: list[dict[str, Any]] = []
        failures: list[str] = []

        for title in titles:
            response = self.tools.create_task(
                title=title,
                notes=notes,
                due=shared_intent.get("due"),
                metadata=metadata,
            )
            if response["status"] != "ok":
                failures.append(f"{title}: {response['message']}")
                continue
            task = response.get("data", {}).get("task", {})
            if task:
                created_tasks.append(dict(task))

        if created_tasks:
            self.last_created_task = dict(created_tasks[-1])
            undo_tasks = [task for task in created_tasks if task.get("id")]
            if undo_tasks:
                self.undo_stack.append(
                    {
                        "action": "delete_tasks",
                        "tasks": undo_tasks,
                        "task_list_id": DEFAULT_TASK_LIST_ID,
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
    ) -> str:
        update = _task_update_from_request(request)
        if update is None:
            message = "Please say what to update on the task."
            print(message)
            return message

        if update.target is None:
            task = self.last_created_task
            if task is None:
                message = "I do not know which task to update."
                print(message)
                return message
            return self._update_task(task, update, task_list_id)

        response = self.tools.list_tasks(task_list_id=task_list_id)
        if response["status"] != "ok":
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
            notes=notes,
            metadata=metadata,
            task_list_id=task_list_id,
        )
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message

        updated = response.get("data", {}).get("task", {})
        if updated:
            merged_task = dict(task)
            merged_task.update(updated)
            self.last_created_task = merged_task
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
    ) -> str:
        intent = extract_intent(request, now=reference_time)
        filters = intent.get("filters", {})
        response = self.tools.recommend_tasks(filters=filters)
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
            message = "No pending Noah assistant help tasks found."
            print(message)
            return message

        try:
            client = research_client or OpenClawNoahResearchClient.from_env()
        except RuntimeError as error:
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
        print(message)
        return message

    def complete_task_from_request(
        self,
        request: str,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
    ) -> str:
        return self._destructive_task_from_request(
            request=request,
            action="complete",
            task_list_id=task_list_id,
        )

    def delete_task_from_request(
        self,
        request: str,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
    ) -> str:
        return self._destructive_task_from_request(
            request=request,
            action="delete",
            task_list_id=task_list_id,
        )

    def _destructive_task_from_request(
        self,
        request: str,
        action: str,
        task_list_id: str,
    ) -> str:
        intent = extract_intent(request)
        query = intent.get("query")
        if not query:
            message = f"Please provide which task to {action}."
            print(message)
            return message

        response = self.tools.list_tasks(task_list_id=task_list_id)
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message

        matches = match_tasks(query, response.get("data", {}).get("tasks", []))
        if not matches:
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
        pending = self.pending_action
        if command in ("no", "n", "cancel"):
            self.pending_action = None
            print("Okay, I did not change any tasks.")
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

        if command not in ("yes", "y", "confirm"):
            print("Please answer yes or no.")
            return True

        task = pending.task
        if task is None:
            print("Please choose a task number first.")
            return True

        task_id = task.get("id")
        if not task_id:
            self.pending_action = None
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
            tasks = [item for item in undo.get("tasks", []) if item.get("id")]
            failed = []
            for undo_task in tasks:
                response = self.tools.delete_task(
                    task_id=undo_task.get("id"),
                    task_list_id=task_list_id,
                    confirmed=True,
                )
                if response["status"] != "ok":
                    failed.append(response["message"])
            if failed:
                message = "Some image-created tasks could not be deleted:\n" + "\n".join(
                    f"- {failure}" for failure in failed
                )
            else:
                message = f"Undid image task creation: deleted {len(tasks)} tasks."
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

    intent = extract_intent(request)
    if intent["intent"] == "create_task":
        claw.add_task_from_request(request)
    elif intent["intent"] == "complete_task":
        claw.complete_task_from_request(request)
    elif intent["intent"] == "delete_task":
        claw.delete_task_from_request(request)
    elif intent["intent"] == "run_assistant_help":
        claw.run_noah_assistant_help_from_request(request)
    else:
        claw.recommend_tasks_from_request(request)


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

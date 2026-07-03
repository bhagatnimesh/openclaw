from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import sys
from typing import Any

from constants import DEFAULT_TASK_LIST_ID
from intent import extract_intent, read_metadata_from_notes
from matcher import match_tasks
from prompts import SYSTEM_PROMPT, TOOL_GUIDANCE
from tools import FamilyTaskTools, TasksProvider, build_default_tools


@dataclass
class PendingAction:
    action: str
    task: dict[str, Any] | None = None
    choices: list[dict[str, Any]] | None = None
    task_list_id: str = DEFAULT_TASK_LIST_ID


def _format_due(task: dict[str, Any]) -> str:
    due = task.get("due")
    if not due:
        return "no due date"
    return str(due)[:10]


def _format_task_choice(task: dict[str, Any]) -> str:
    title = task.get("title") or "Untitled task"
    _, metadata = read_metadata_from_notes(task.get("notes"))
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
    if task_url:
        return f"Created task: {title} (open: {task_url})."

    task_id = task.get("id")
    suffix = f" (task id: {task_id})" if task_id else ""
    return f"Created task: {title}{suffix}."


def _format_recommendations(recommendations: list[dict[str, Any]]) -> str:
    if not recommendations:
        return "No matching open tasks found."

    lines = ["Recommended tasks:"]
    for recommendation in recommendations:
        task = recommendation.get("task", recommendation)
        reasons = recommendation.get("reasons", [])
        suffix = ""
        if reasons:
            suffix = " - " + "; ".join(str(reason) for reason in reasons)
        lines.append(f"- {_format_task_choice(task)}{suffix}")
    return "\n".join(lines)


@dataclass
class FamilyTasksClaw:
    """Small OpenClaw entry point for the Family Tasks claw."""

    tools: FamilyTaskTools
    system_prompt: str = SYSTEM_PROMPT
    tool_guidance: str = TOOL_GUIDANCE
    pending_action: PendingAction | None = None

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
        }

    def add_task_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> str:
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

        response = self.tools.create_task(
            title=intent["title"],
            notes=intent.get("notes"),
            due=intent.get("due"),
            metadata=intent.get("metadata"),
        )
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message

        message = _format_created_task_message(response.get("data", {}).get("task", {}))
        print(message)
        return message

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
        message = _format_recommendations(
            data.get("recommendations") or data.get("tasks", []),
        )
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
        elif pending.action == "delete":
            result = self.tools.delete_task(
                task_id=task_id,
                task_list_id=pending.task_list_id,
                confirmed=True,
            )
        else:
            result = {"status": "error", "message": "Unknown pending task action."}

        self.pending_action = None
        print(result["message"])
        return True


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

from __future__ import annotations

from typing import Any, Literal, Protocol, TypedDict

from constants import DEFAULT_TASK_LIST_ID
from intent import normalize_metadata, write_human_notes, write_metadata_to_notes
from matcher import recommend_task_matches


class TasksProvider(Protocol):
    def list_task_lists(self) -> list[dict[str, Any]]:
        ...

    def create_task(
        self,
        title: str,
        notes: str | None = None,
        due: str | None = None,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
    ) -> dict[str, Any]:
        ...

    def list_tasks(
        self,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
        show_completed: bool = False,
    ) -> list[dict[str, Any]]:
        ...

    def update_task(
        self,
        task_id: str,
        title: str | None = None,
        notes: str | None = None,
        due: str | None = None,
        status: str | None = None,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
    ) -> dict[str, Any]:
        ...

    def complete_task(
        self,
        task_id: str,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
    ) -> dict[str, Any]:
        ...

    def delete_task(
        self,
        task_id: str,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
    ) -> None:
        ...


class ToolResponse(TypedDict, total=False):
    status: Literal["ok", "needs_information", "needs_confirmation", "error"]
    message: str
    data: dict[str, Any]


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = value.strip()
    return cleaned or None


def _missing_response(fields: list[str]) -> ToolResponse:
    return {
        "status": "needs_information",
        "message": "Missing required task information: " + ", ".join(fields) + ".",
        "data": {"missing_fields": fields},
    }


def _has_meaningful_metadata(metadata: dict[str, Any] | None) -> bool:
    if metadata is None:
        return False
    normalized = normalize_metadata(metadata)
    return bool(
        normalized.get("owner") != "unknown"
        or normalized.get("assistant_help_needed")
        or normalized.get("assistant_help_request")
        or normalized.get("assistant_context")
        or normalized.get("assistant_help_status")
    )


def _confirmation_response(action: str, task_id: str) -> ToolResponse:
    return {
        "status": "needs_confirmation",
        "message": f"Confirm before I {action} task {task_id}.",
        "data": {"task_id": task_id, "action": action},
    }


def _provider_error_response(error: Exception) -> ToolResponse:
    error_text = str(error)
    if "invalid_scope" in error_text:
        message = (
            "Google OAuth token is missing the Tasks scope. Run "
            "`python get_google_token.py`, complete the browser consent, then retry."
        )
    else:
        message = f"Google Tasks request failed: {error_text}"

    return {
        "status": "error",
        "message": message,
        "data": {"error_type": error.__class__.__name__},
    }


class FamilyTaskTools:
    """OpenClaw tool layer for family tasks."""

    def __init__(self, provider: TasksProvider):
        self.provider = provider

    def list_task_lists(self) -> ToolResponse:
        try:
            task_lists = self.provider.list_task_lists()
        except Exception as error:
            return _provider_error_response(error)

        return {
            "status": "ok",
            "message": "Task lists returned from Google Tasks.",
            "data": {"task_lists": task_lists},
        }

    def create_task(
        self,
        title: str | None = None,
        notes: str | None = None,
        due: str | None = None,
        metadata: dict[str, Any] | None = None,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
    ) -> ToolResponse:
        cleaned_title = _clean_optional(title)
        if cleaned_title is None:
            return _missing_response(["title"])

        try:
            has_metadata = _has_meaningful_metadata(metadata)
            task_notes = (
                write_metadata_to_notes(notes, metadata)
                if has_metadata
                else write_human_notes(notes)
            )
            task = self.provider.create_task(
                title=cleaned_title,
                notes=task_notes,
                due=_clean_optional(due),
                task_list_id=task_list_id,
            )
        except Exception as error:
            return _provider_error_response(error)

        if metadata is not None:
            task["_n4os_metadata"] = normalize_metadata(metadata)

        return {
            "status": "ok",
            "message": "Task created.",
            "data": {"task": task},
        }

    def list_tasks(
        self,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
        show_completed: bool = False,
    ) -> ToolResponse:
        try:
            tasks = self.provider.list_tasks(
                task_list_id=task_list_id,
                show_completed=show_completed,
            )
        except Exception as error:
            return _provider_error_response(error)

        return {
            "status": "ok",
            "message": "Tasks returned from Google Tasks.",
            "data": {"tasks": tasks},
        }

    def update_task(
        self,
        task_id: str | None = None,
        title: str | None = None,
        notes: str | None = None,
        due: str | None = None,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
    ) -> ToolResponse:
        cleaned_task_id = _clean_optional(task_id)
        if cleaned_task_id is None:
            return _missing_response(["task_id"])

        has_notes_update = notes is not None or metadata is not None
        cleaned_status = _clean_optional(status)
        if (
            _clean_optional(title) is None
            and _clean_optional(due) is None
            and cleaned_status is None
            and not has_notes_update
        ):
            return _missing_response(["title, notes, due, status, or metadata"])

        try:
            has_metadata = _has_meaningful_metadata(metadata)
            task_notes = (
                write_metadata_to_notes(notes, metadata)
                if has_metadata
                else write_human_notes(notes)
                if has_notes_update
                else None
            )
            task = self.provider.update_task(
                task_id=cleaned_task_id,
                title=_clean_optional(title),
                notes=task_notes,
                due=_clean_optional(due),
                status=cleaned_status,
                task_list_id=task_list_id,
            )
        except Exception as error:
            return _provider_error_response(error)

        if metadata is not None:
            task["_n4os_metadata"] = normalize_metadata(metadata)

        return {
            "status": "ok",
            "message": "Task updated.",
            "data": {"task": task},
        }

    def complete_task(
        self,
        task_id: str | None = None,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
        confirmed: bool = False,
    ) -> ToolResponse:
        cleaned_task_id = _clean_optional(task_id)
        if cleaned_task_id is None:
            return _missing_response(["task_id"])
        if not confirmed:
            return _confirmation_response("complete", cleaned_task_id)

        try:
            task = self.provider.complete_task(
                task_id=cleaned_task_id,
                task_list_id=task_list_id,
            )
        except Exception as error:
            return _provider_error_response(error)

        return {
            "status": "ok",
            "message": "Task completed.",
            "data": {"task": task},
        }

    def delete_task(
        self,
        task_id: str | None = None,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
        confirmed: bool = False,
    ) -> ToolResponse:
        cleaned_task_id = _clean_optional(task_id)
        if cleaned_task_id is None:
            return _missing_response(["task_id"])
        if not confirmed:
            return _confirmation_response("delete", cleaned_task_id)

        try:
            self.provider.delete_task(
                task_id=cleaned_task_id,
                task_list_id=task_list_id,
            )
        except Exception as error:
            return _provider_error_response(error)

        return {
            "status": "ok",
            "message": "Task deleted.",
            "data": {"task_id": cleaned_task_id},
        }

    def recommend_tasks(
        self,
        filters: dict[str, Any] | None = None,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
    ) -> ToolResponse:
        try:
            tasks = self.provider.list_tasks(task_list_id=task_list_id, show_completed=False)
        except Exception as error:
            return _provider_error_response(error)

        normalized_filters = filters or {}
        recommendations = recommend_task_matches(tasks, normalized_filters)
        return {
            "status": "ok",
            "message": "Task recommendations returned.",
            "data": {
                "tasks": [
                    recommendation["task"]
                    for recommendation in recommendations
                ],
                "recommendations": recommendations,
                "filters": normalized_filters,
            },
        }


def build_default_tools() -> FamilyTaskTools:
    from provider import GoogleTasksProvider

    return FamilyTaskTools(GoogleTasksProvider())

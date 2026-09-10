from __future__ import annotations

import errno
import ssl
from typing import Any, Literal, Protocol, TypedDict

from google.auth.exceptions import TransportError
from httplib2 import HttpLib2Error

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


def _is_transport_error(error: Exception) -> bool:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (ConnectionError, TimeoutError, ssl.SSLError, TransportError, HttpLib2Error)):
            return True
        if isinstance(current, OSError) and current.errno in {
            errno.ECONNABORTED,
            errno.ECONNREFUSED,
            errno.ECONNRESET,
            errno.EPIPE,
            errno.ETIMEDOUT,
        }:
            return True
        current = current.__cause__ or current.__context__
    return False


def _http_status(error: Exception) -> int | None:
    status = getattr(getattr(error, "resp", None), "status", None)
    return status if isinstance(status, int) else None


def _provider_error_response(
    error: Exception,
    *,
    operation: Literal["read", "create", "change"] = "read",
) -> ToolResponse:
    error_text = str(error)
    if "invalid_grant" in error_text:
        message = (
            "Google Tasks needs to be reconnected. Run python3 get_google_token.py, "
            "complete the browser sign-in, then restart the Telegram bot."
        )
    elif "invalid_scope" in error_text:
        message = (
            "Google OAuth token is missing the Tasks scope. Run "
            "python3 get_google_token.py, complete the browser consent, then retry."
        )
    elif _is_transport_error(error) or _http_status(error) in {429, 500, 502, 503, 504}:
        # A transport failure can arrive after Google accepted a write. Do not
        # invite a blind retry because Tasks inserts have no idempotency key.
        if operation == "create":
            message = (
                "I couldn't confirm whether Google Tasks created it. Check Google "
                "Tasks before retrying so you don't create a duplicate."
            )
        elif operation == "change":
            message = (
                "I couldn't confirm the change with Google Tasks. Check the task's "
                "current state before retrying."
            )
        else:
            message = "I couldn't reach Google Tasks just now. Please try again."
    elif _http_status(error) in {401, 403}:
        message = "Google Tasks access needs attention. Reconnect Google, then try again."
    elif _http_status(error) == 404:
        message = "Google Tasks couldn't find that task or list. Refresh your tasks and try again."
    elif _http_status(error) == 400:
        message = "Google Tasks couldn't accept that request. Check the task details and try again."
    else:
        message = "Google Tasks couldn't complete that request. Please try again."

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
            return _provider_error_response(error, operation="create")

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
            return _provider_error_response(error, operation="change")

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
            return _provider_error_response(error, operation="change")

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
            return _provider_error_response(error, operation="change")

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

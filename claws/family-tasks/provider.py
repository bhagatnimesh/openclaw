from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from constants import DEFAULT_TASK_LIST_ID


SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
]

ROOT = Path(__file__).resolve().parents[2]

CLIENT_FILE = ROOT / "secrets" / "google_client_secret.json"
TOKEN_FILE = ROOT / "secrets" / "google_token.json"


def _normalize_due(due: str | date | datetime | None) -> str | None:
    if due is None:
        return None

    if isinstance(due, datetime):
        return due.date().isoformat() + "T00:00:00.000Z"

    if isinstance(due, date):
        return due.isoformat() + "T00:00:00.000Z"

    value = due.strip()
    if not value:
        return None

    try:
        parsed_date = date.fromisoformat(value)
    except ValueError:
        return value

    return parsed_date.isoformat() + "T00:00:00.000Z"


class GoogleTasksProvider:
    """Google Tasks API provider for the Family Tasks claw."""

    def __init__(self, service: Any | None = None):
        self.service = service or self._build_service()

    def _build_service(self):
        with open(CLIENT_FILE, "r") as f:
            client = json.load(f)["installed"]

        with open(TOKEN_FILE, "r") as f:
            token = json.load(f)

        creds = Credentials(
            token=None,
            refresh_token=token["refresh_token"],
            token_uri=client["token_uri"],
            client_id=client["client_id"],
            client_secret=client["client_secret"],
            scopes=SCOPES,
        )

        return build("tasks", "v1", credentials=creds)

    def list_task_lists(self) -> list[dict[str, Any]]:
        task_lists: list[dict[str, Any]] = []
        page_token = None
        while True:
            request = self.service.tasklists().list(
                maxResults=1000,
                pageToken=page_token,
            )
            response = request.execute()
            task_lists.extend(response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return task_lists

    def create_task(
        self,
        title: str,
        notes: str | None = None,
        due: str | date | datetime | None = None,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"title": title}
        if notes:
            body["notes"] = notes

        normalized_due = _normalize_due(due)
        if normalized_due:
            body["due"] = normalized_due

        return (
            self.service.tasks()
            .insert(tasklist=task_list_id, body=body)
            .execute()
        )

    def list_tasks(
        self,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
        show_completed: bool = False,
    ) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        page_token = None
        while True:
            response = (
                self.service.tasks()
                .list(
                    tasklist=task_list_id,
                    maxResults=100,
                    pageToken=page_token,
                    showCompleted=show_completed,
                    showDeleted=False,
                    showHidden=show_completed,
                )
                .execute()
            )
            tasks.extend(response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return tasks

    def update_task(
        self,
        task_id: str,
        title: str | None = None,
        notes: str | None = None,
        due: str | date | datetime | None = None,
        status: str | None = None,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if notes is not None:
            body["notes"] = notes

        normalized_due = _normalize_due(due)
        if normalized_due is not None:
            body["due"] = normalized_due

        if status is not None:
            body["status"] = status

        return (
            self.service.tasks()
            .patch(tasklist=task_list_id, task=task_id, body=body)
            .execute()
        )

    def complete_task(
        self,
        task_id: str,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
    ) -> dict[str, Any]:
        return self.update_task(
            task_id=task_id,
            status="completed",
            task_list_id=task_list_id,
        )

    def delete_task(
        self,
        task_id: str,
        task_list_id: str = DEFAULT_TASK_LIST_ID,
    ) -> None:
        self.service.tasks().delete(tasklist=task_list_id, task=task_id).execute()

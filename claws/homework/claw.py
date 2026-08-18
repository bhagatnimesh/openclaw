from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .intent import DEFAULT_CHILD, extract_intent, is_homework_capture
from .tools import HomeworkProvider, HomeworkTools, ToolResponse, build_default_tools


@dataclass
class HomeworkClaw:
    tools: HomeworkTools
    last_result: ToolResponse | None = None
    pending_action: dict[str, Any] | None = None

    @classmethod
    def from_provider(cls, provider: HomeworkProvider) -> "HomeworkClaw":
        return cls(HomeworkTools(provider))

    @classmethod
    def default(cls) -> "HomeworkClaw":
        return cls(build_default_tools())

    def tool_map(self) -> dict[str, Any]:
        return {
            "capture_assignment": self.tools.capture_assignment,
            "capture_submission": self.tools.capture_submission,
            "list_homework": self.tools.list_homework,
            "list_class_schedules": self.tools.list_class_schedules,
            "homework_status": self.tools.homework_status,
        }

    def capture_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
        *,
        source: str = "telegram_text",
        photo_path: str | None = None,
        photo_sha256: str | None = None,
    ) -> str:
        if self.pending_action is not None:
            pending_action = self.pending_action
            if pending_action.get("action") != "fill_homework_due_date" or not is_homework_capture(request):
                response = self.tools.resolve_pending_action(pending_action, request, now=reference_time)
                if response.get("status") == "needs_information":
                    pending = response.get("data", {}).get("pending_action")
                    self.pending_action = pending if isinstance(pending, dict) else self.pending_action
                else:
                    self.pending_action = None
                self.last_result = response
                message = response["message"]
                print(message)
                return message
            self.pending_action = None

        intent = extract_intent(request, now=reference_time, source=source, photo_path=photo_path)
        if intent.get("intent") == "capture_submission":
            response = self.tools.capture_submission(
                request,
                now=reference_time,
                source=source,
                photo_path=photo_path,
            )
        else:
            response = self.tools.capture_assignment(
                request,
                now=reference_time,
                source=source,
                photo_path=photo_path,
                photo_sha256=photo_sha256,
            )
        pending = response.get("data", {}).get("pending_action")
        self.pending_action = pending if isinstance(pending, dict) else None
        self.last_result = response
        message = response["message"]
        print(message)
        return message

    def status_from_request(
        self,
        request: str = "",
        reference_time: datetime | None = None,
    ) -> str:
        intent = extract_intent(request, now=reference_time)
        response = self.tools.homework_status(child=str(intent.get("child") or DEFAULT_CHILD))
        self.last_result = response
        message = response["message"]
        print(message)
        return message

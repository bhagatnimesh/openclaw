from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import sys
from typing import Any

try:
    from .intent_router import (
        CALENDAR_ROOT,
        LOW_CONFIDENCE_THRESHOLD,
        TASKS_ROOT,
        load_scoped_module,
        module_scope,
        route_request,
    )
    from .prompts import CLARIFICATION_PROMPT, SYSTEM_PROMPT
except ImportError:
    from intent_router import (
        CALENDAR_ROOT,
        LOW_CONFIDENCE_THRESHOLD,
        TASKS_ROOT,
        load_scoped_module,
        module_scope,
        route_request,
    )
from prompts import CLARIFICATION_PROMPT, SYSTEM_PROMPT


def _is_google_dependency_error(error: ModuleNotFoundError) -> bool:
    missing_name = error.name or ""
    return missing_name == "google" or missing_name.startswith("google")


def _missing_google_dependency_message(surface: str) -> str:
    return (
        f"{surface} needs the Google Python client libraries before it can "
        "talk to Google APIs. Create and activate a virtualenv, then install "
        "them with `python3 -m pip install google-api-python-client google-auth`."
    )


def _calendar_module() -> Any:
    return load_scoped_module("_n4os_family_calendar_claw", CALENDAR_ROOT, "claw.py")


def _tasks_module() -> Any:
    return load_scoped_module("_n4os_family_tasks_claw", TASKS_ROOT, "claw.py")


@dataclass
class N4OSClaw:
    """Top-level N4OS router over family calendar and tasks claws."""

    calendar_claw: Any | None = None
    tasks_claw: Any | None = None
    system_prompt: str = SYSTEM_PROMPT

    def route(self, request: str, reference_time: datetime | None = None) -> dict[str, Any]:
        return route_request(request, now=reference_time)

    def handle_request(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> dict[str, Any]:
        calendar = self.calendar_claw
        if calendar is not None and calendar.handle_pending_response(request):
            return {
                "route": "calendar",
                "intent_summary": "Handled pending family-calendar response.",
                "confidence": 1.0,
            }

        tasks = self.tasks_claw
        if tasks is not None and tasks.handle_pending_response(request):
            return {
                "route": "tasks",
                "intent_summary": "Handled pending family-tasks response.",
                "confidence": 1.0,
            }

        decision = self.route(request, reference_time=reference_time)
        if (
            decision["route"] == "unknown"
            or decision["confidence"] < LOW_CONFIDENCE_THRESHOLD
        ):
            print(CLARIFICATION_PROMPT)
            return decision

        if decision["route"] in ("calendar", "both"):
            self._handle_calendar_request(request, reference_time)
        if decision["route"] in ("tasks", "both"):
            self._handle_tasks_request(request, reference_time)

        return decision

    def _calendar(self) -> Any:
        if self.calendar_claw is None:
            with module_scope(CALENDAR_ROOT):
                try:
                    self.calendar_claw = _calendar_module().FamilyCalendarClaw.default()
                except ModuleNotFoundError as error:
                    if _is_google_dependency_error(error):
                        print(_missing_google_dependency_message("Family Calendar"))
                        return None
                    raise
        return self.calendar_claw

    def _tasks(self) -> Any:
        if self.tasks_claw is None:
            with module_scope(TASKS_ROOT):
                try:
                    self.tasks_claw = _tasks_module().FamilyTasksClaw.default()
                except ModuleNotFoundError as error:
                    if _is_google_dependency_error(error):
                        print(_missing_google_dependency_message("Family Tasks"))
                        return None
                    raise
        return self.tasks_claw

    def _handle_calendar_request(
        self,
        request: str,
        reference_time: datetime | None,
    ) -> None:
        module = _calendar_module()
        claw = self._calendar()
        if claw is None:
            return
        intent = module.extract_intent(request, now=reference_time)
        if intent["intent"] == "preparation_checklist":
            claw.preparation_from_request(request, reference_time=reference_time)
        elif intent["intent"] == "family_briefing":
            claw.briefing_from_request(request, reference_time=reference_time)
        elif intent["intent"] == "list_events":
            claw.list_events_from_request(request, reference_time=reference_time)
        elif intent["intent"] == "delete_event":
            claw.delete_event_from_request(request, reference_time=reference_time)
        elif intent["intent"] == "update_event":
            claw.update_event_from_request(request, reference_time=reference_time)
        else:
            claw.create_event_from_request(request, reference_time=reference_time)

    def _handle_tasks_request(
        self,
        request: str,
        reference_time: datetime | None,
    ) -> None:
        module = _tasks_module()
        claw = self._tasks()
        if claw is None:
            return
        intent = module.extract_intent(request, now=reference_time)
        if intent["intent"] == "create_task":
            claw.add_task_from_request(request, reference_time=reference_time)
        elif intent["intent"] == "complete_task":
            claw.complete_task_from_request(request)
        elif intent["intent"] == "delete_task":
            claw.delete_task_from_request(request)
        else:
            claw.recommend_tasks_from_request(request, reference_time=reference_time)


def run_cli(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    claw = N4OSClaw()
    request = " ".join(args).strip()
    if request:
        claw.handle_request(request)
        return

    print("N4OS Router. Type a request, or 'exit' to quit.")
    while True:
        try:
            command = input("> ").strip()
        except EOFError:
            print()
            return

        if not command:
            continue
        if command.lower() in ("exit", "quit"):
            return

        claw.handle_request(command)


if __name__ == "__main__":
    run_cli()

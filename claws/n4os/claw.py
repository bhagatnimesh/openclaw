from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
import re
import sys
from typing import Any
from zoneinfo import ZoneInfo

try:
    from .intent_router import (
        CALENDAR_ROOT,
        DECISIONS_ROOT,
        HOME_BOARD_ROOT,
        LIBRARY_ROOT,
        LOW_CONFIDENCE_THRESHOLD,
        N4OSIntentFrame,
        SCIENCE_LAB_ROOT,
        TASKS_ROOT,
        interpret_request,
        load_scoped_module,
        module_scope,
        route_request,
    )
    from .input_normalizer import improve_entered_text
    from .prompts import CLARIFICATION_PROMPT, SYSTEM_PROMPT
except ImportError:
    from intent_router import (
        CALENDAR_ROOT,
        DECISIONS_ROOT,
        HOME_BOARD_ROOT,
        LIBRARY_ROOT,
        LOW_CONFIDENCE_THRESHOLD,
        N4OSIntentFrame,
        SCIENCE_LAB_ROOT,
        TASKS_ROOT,
        interpret_request,
        load_scoped_module,
        module_scope,
        route_request,
    )
    from input_normalizer import improve_entered_text
    from prompts import CLARIFICATION_PROMPT, SYSTEM_PROMPT


DEFAULT_TIMEZONE = "America/Los_Angeles"
OVERLOADED_EVENT_COUNT = 4


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


def _home_board_module() -> Any:
    return load_scoped_module("_n4os_home_board_claw", HOME_BOARD_ROOT, "claw.py")


def _decisions_module() -> Any:
    return load_scoped_module("_n4os_family_decisions_claw", DECISIONS_ROOT, "claw.py")


def _science_lab_module() -> Any:
    return load_scoped_module("_n4os_science_lab_claw", SCIENCE_LAB_ROOT, "claw.py")


def _library_module() -> Any:
    return load_scoped_module("_n4os_library_claw", LIBRARY_ROOT, "claw.py")


def _is_day_briefing_request(request: str) -> bool:
    lowered = request.lower()
    return (
        "today" in lowered
        or "day briefing" in lowered
        or "daily briefing" in lowered
    )


def _default_now(reference_time: datetime | None) -> datetime:
    if reference_time is None:
        return datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
    if reference_time.tzinfo is None:
        return reference_time.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
    return reference_time


def _start_of_day(value: datetime) -> datetime:
    return datetime.combine(
        value.date(),
        time.min,
        tzinfo=ZoneInfo(DEFAULT_TIMEZONE),
    )


def _format_event_line(event: dict[str, Any], calendar_module: Any) -> str:
    title = event.get("summary") or "Untitled event"
    start_label = calendar_module._format_event_time(event.get("start", {}))
    end_label = calendar_module._format_event_time(event.get("end", {}))
    location = event.get("location")
    suffix = f" at {location}" if location else ""
    return f"- {start_label}-{end_label}: {title}{suffix}"


def _prep_line(event: dict[str, Any], calendar_module: Any) -> str:
    title = event.get("summary") or "Untitled event"
    _, metadata = calendar_module.read_metadata_from_event(event)
    notes = metadata.get("preparation_notes") or "prep needed"
    return f"- {title}: {notes}"


def _parse_due_date(task: dict[str, Any]) -> datetime | None:
    due = task.get("due")
    if not due:
        return None

    normalized = str(due).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    try:
        parsed = datetime.fromisoformat(str(due)[:10])
    except ValueError:
        return None
    return parsed.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))


def _task_title(task: dict[str, Any]) -> str:
    return task.get("title") or "Untitled task"


def _urgent_or_due_tasks(
    tasks: list[dict[str, Any]],
    now: datetime,
    tasks_module: Any,
) -> list[dict[str, Any]]:
    selected = []
    today = now.date()
    for task in tasks:
        due = _parse_due_date(task)
        _, metadata = tasks_module.read_metadata_from_notes(task.get("notes"))
        is_urgent = metadata.get("urgency") == "high"
        is_due = due is not None and due.date() <= today
        if is_urgent or is_due:
            selected.append(task)
    return selected


def _format_task_line(task: dict[str, Any], tasks_module: Any) -> str:
    return f"- {tasks_module._format_task_choice(task)}"


def _planning_window(now: datetime) -> tuple[datetime, datetime]:
    start = max(now, datetime.combine(now.date(), time(8), tzinfo=now.tzinfo))
    end = datetime.combine(now.date(), time(18), tzinfo=now.tzinfo)
    if start >= end:
        return now, now
    return start, end


def _largest_available_gap_minutes(
    events: list[dict[str, Any]],
    now: datetime,
    calendar_module: Any,
) -> int:
    window_start, window_end = _planning_window(now)
    if window_start >= window_end:
        return 15

    busy_ranges = []
    for event in events:
        start = calendar_module._event_start(event)
        end = calendar_module._event_end(event)
        if start == datetime.max or end == datetime.max:
            continue
        if end <= window_start or start >= window_end:
            continue
        busy_ranges.append((max(start, window_start), min(end, window_end)))

    current = window_start
    largest = 0
    for start, end in sorted(busy_ranges):
        if start > current:
            largest = max(largest, int((start - current).total_seconds() / 60))
        if end > current:
            current = end
    if current < window_end:
        largest = max(largest, int((window_end - current).total_seconds() / 60))
    return max(15, min(largest, 120))


def _warning_lines(events: list[dict[str, Any]], calendar_module: Any) -> list[str]:
    warnings = []
    ordered = sorted(events, key=calendar_module._event_start)
    for previous, current in zip(ordered, ordered[1:]):
        previous_end = calendar_module._event_end(previous)
        current_start = calendar_module._event_start(current)
        if previous_end != datetime.max and current_start < previous_end:
            previous_title = previous.get("summary") or "Untitled event"
            current_title = current.get("summary") or "Untitled event"
            warnings.append(f"- Conflict: {previous_title} overlaps {current_title}")

    if len(events) >= OVERLOADED_EVENT_COUNT:
        warnings.append(f"- Overloaded day: {len(events)} calendar commitments")
    return warnings


def _format_day_briefing(
    events: list[dict[str, Any]],
    open_tasks: list[dict[str, Any]],
    recommended_tasks: list[dict[str, Any]],
    now: datetime,
    calendar_module: Any,
    tasks_module: Any,
) -> str:
    prep_events = [
        event
        for event in events
        if calendar_module._event_needs_preparation(event)
    ]
    urgent_tasks = _urgent_or_due_tasks(open_tasks, now, tasks_module)
    warnings = _warning_lines(events, calendar_module)

    lines = [f"N4OS day briefing for {now.strftime('%A, %B %-d')}:"]
    lines.append("1. Today's calendar commitments")
    lines.extend([_format_event_line(event, calendar_module) for event in events] or ["- None"])
    lines.append("2. Prep-needed calendar items")
    lines.extend([_prep_line(event, calendar_module) for event in prep_events] or ["- None"])
    lines.append("3. Open urgent/due tasks")
    lines.extend([_format_task_line(task, tasks_module) for task in urgent_tasks] or ["- None"])
    lines.append("4. Suggested focus tasks based on available gaps")
    lines.extend([_format_task_line(task, tasks_module) for task in recommended_tasks[:3]] or ["- None"])
    lines.append("5. Warnings for conflicts or overloaded days")
    lines.extend(warnings or ["- None"])
    return "\n".join(lines)


@dataclass
class PendingRouteClarification:
    request: str
    reference_time: datetime | None


@dataclass
class RouteContext:
    last_route: str | None = None
    last_action: str | None = None
    last_request: str | None = None
    last_mutation_route: str | None = None
    mutation_route_stack: list[str] = field(default_factory=list)
    recent_decision_indexes: dict[int, str] = field(default_factory=dict)
    last_artifact: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, pending_owner: str | None = None) -> dict[str, Any]:
        return {
            "last_route": self.last_route,
            "last_action": self.last_action,
            "last_request": self.last_request,
            "last_mutation_route": self.last_mutation_route,
            "pending_owner": pending_owner,
            "recent_decision_indexes": dict(self.recent_decision_indexes),
            "last_artifact": dict(self.last_artifact),
        }


def _clarified_route(request: str) -> str | None:
    normalized = request.lower().strip(" .!?")
    if normalized in ("calendar", "cal", "use calendar"):
        return "calendar"
    if normalized in ("task", "tasks", "todo", "to-do", "use tasks"):
        return "tasks"
    if normalized in ("home board", "today at home", "house board", "use home board"):
        return "home_board"
    if normalized in ("decision", "decisions", "family decisions", "use decisions"):
        return "decisions"
    if normalized in ("science lab", "science", "experiments", "use science lab"):
        return "science_lab"
    if normalized in ("library", "reading garden", "use library"):
        return "library"
    if normalized in ("both", "calendar and tasks", "tasks and calendar"):
        return "both"
    return None


def _has_pending_action(claw: Any | None) -> bool:
    return claw is not None and getattr(claw, "pending_action", None) is not None


def _clear_pending_action(claw: Any | None) -> None:
    if claw is not None and hasattr(claw, "pending_action"):
        setattr(claw, "pending_action", None)


def _is_undo_request(request: str) -> bool:
    normalized = " ".join(request.lower().strip(" .!?").split())
    return normalized in {
        "undo",
        "undo that",
        "undo last",
        "undo the last thing",
        "revert",
        "revert that",
        "revert last",
        "cancel",
        "cancel that",
        "cancel last",
        "nevermind",
        "never mind",
    }


def _undo_depth(claw: Any | None) -> int:
    stack = getattr(claw, "undo_stack", None)
    return len(stack) if isinstance(stack, list) else 0


def _pending_owner(
    calendar: Any | None,
    tasks: Any | None,
    home_board: Any | None = None,
    decisions: Any | None = None,
) -> str | None:
    if _has_pending_action(calendar):
        return "calendar"
    if _has_pending_action(tasks):
        return "tasks"
    if _has_pending_action(home_board):
        return "home_board"
    if _has_pending_action(decisions):
        return "decisions"
    return None


def _is_confident_new_route(decision: dict[str, Any], pending_owner: str) -> bool:
    route = decision.get("route")
    confidence = float(decision.get("confidence") or 0)
    return (
        route in ("calendar", "tasks", "home_board", "decisions", "science_lab", "library", "both")
        and route != pending_owner
        and confidence >= LOW_CONFIDENCE_THRESHOLD
    )


def _clarified_route_action(
    route: str,
    request: str,
    reference_time: datetime | None,
) -> str:
    if route == "calendar":
        return str(_calendar_module().extract_intent(request, now=reference_time)["intent"])
    if route == "tasks":
        task_intent = _tasks_module().extract_intent(request, now=reference_time)
        if task_intent.get("intent") != "recommend_tasks":
            return str(task_intent["intent"])
        create_intent = _tasks_module().extract_intent(
            f"add task {request}",
            now=reference_time,
        )
        if create_intent.get("intent") == "create_task":
            return "create_task"
        return str(task_intent["intent"])
    if route == "home_board":
        return str(_home_board_module().extract_intent(request, now=reference_time)["intent"])
    if route == "decisions":
        return str(_decisions_module().extract_intent(request, now=reference_time)["intent"])
    if route == "science_lab":
        return "science_lab"
    if route == "library":
        return str(_library_module().extract_intent(request, now=reference_time)["intent"])
    if route == "both":
        return "calendar_and_tasks"
    return "unknown"


def _clarified_dispatch_request(
    route: str,
    action: str,
    request: str,
    reference_time: datetime | None,
) -> str:
    if route == "tasks" and action == "create_task":
        task_intent = _tasks_module().extract_intent(request, now=reference_time)
        if task_intent.get("intent") == "recommend_tasks":
            return f"add task {request}"
    return request


@dataclass
class N4OSClaw:
    """Top-level N4OS router over family operations and Science Lab requests."""

    calendar_claw: Any | None = None
    tasks_claw: Any | None = None
    home_board_claw: Any | None = None
    decisions_claw: Any | None = None
    science_lab_claw: Any | None = None
    library_claw: Any | None = None
    system_prompt: str = SYSTEM_PROMPT
    intent_interpreter: Any | None = None
    pending_route_clarification: PendingRouteClarification | None = None
    route_context: RouteContext = field(default_factory=RouteContext)

    def route(self, request: str, reference_time: datetime | None = None) -> dict[str, Any]:
        request = improve_entered_text(request)
        return route_request(
            request,
            now=reference_time,
            context=self._context_payload(),
            interpreter=self.intent_interpreter,
        )

    def interpret(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> N4OSIntentFrame:
        request = improve_entered_text(request)
        return interpret_request(
            request,
            now=reference_time,
            context=self._context_payload(),
            interpreter=self.intent_interpreter,
        )

    def _context_payload(self) -> dict[str, Any]:
        return self.route_context.to_dict(
            pending_owner=_pending_owner(
                self.calendar_claw,
                self.tasks_claw,
                self.home_board_claw,
                self.decisions_claw,
            ),
        )

    def _remember_route(
        self,
        request: str,
        frame: N4OSIntentFrame,
    ) -> None:
        self.route_context.last_route = frame.route
        self.route_context.last_action = frame.action
        self.route_context.last_request = request
        self.route_context.last_artifact = {
            "followup_kind": frame.followup_kind,
            "target": dict(frame.target),
            "slots": dict(frame.slots),
        }

    def handle_request(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> dict[str, Any]:
        request = improve_entered_text(request)
        calendar = self.calendar_claw
        tasks = self.tasks_claw
        home_board = self.home_board_claw
        decisions = self.decisions_claw
        pending_owner = _pending_owner(calendar, tasks, home_board, decisions)
        if pending_owner is not None:
            frame = self.interpret(request, reference_time=reference_time)
            decision = frame.to_route_decision()
            if _is_confident_new_route(decision, pending_owner):
                _clear_pending_action(calendar)
                _clear_pending_action(tasks)
                _clear_pending_action(home_board)
                _clear_pending_action(decisions)
                self.pending_route_clarification = None
                self._dispatch_decision(request, decision, reference_time, frame=frame)
                self._remember_route(request, frame)
                return decision

        if calendar is not None and calendar.handle_pending_response(request):
            frame = N4OSIntentFrame(
                route="calendar",
                action="pending_response",
                confidence=1.0,
                followup_kind="pending_response",
                normalized_request=request,
            )
            self._remember_route(request, frame)
            return {
                "route": "calendar",
                "intent_summary": "Handled pending family-calendar response.",
                "confidence": 1.0,
            }

        if tasks is not None and tasks.handle_pending_response(request):
            frame = N4OSIntentFrame(
                route="tasks",
                action="pending_response",
                confidence=1.0,
                followup_kind="pending_response",
                normalized_request=request,
            )
            self._remember_route(request, frame)
            return {
                "route": "tasks",
                "intent_summary": "Handled pending family-tasks response.",
                "confidence": 1.0,
            }

        if _is_undo_request(request):
            return self._undo_last_action(request)

        clarified_route = _clarified_route(request)
        pending_route = self.pending_route_clarification
        if clarified_route is not None and pending_route is not None:
            self.pending_route_clarification = None
            clarified_action = _clarified_route_action(
                clarified_route,
                pending_route.request,
                pending_route.reference_time,
            )
            dispatch_request = _clarified_dispatch_request(
                clarified_route,
                clarified_action,
                pending_route.request,
                pending_route.reference_time,
            )
            frame = N4OSIntentFrame(
                route=clarified_route,
                action=clarified_action,
                confidence=1.0,
                followup_kind="clarification",
                normalized_request=dispatch_request,
            )
            decision = frame.to_route_decision()
            self._dispatch_decision(
                dispatch_request,
                decision,
                pending_route.reference_time,
                frame=frame,
            )
            self._remember_route(dispatch_request, frame)
            return decision

        frame = self.interpret(request, reference_time=reference_time)
        decision = frame.to_route_decision()
        if (
            decision["route"] == "unknown"
            or decision["confidence"] < LOW_CONFIDENCE_THRESHOLD
        ):
            self.pending_route_clarification = PendingRouteClarification(
                request=request,
                reference_time=reference_time,
            )
            print(frame.clarification_question or CLARIFICATION_PROMPT)
            return decision

        self.pending_route_clarification = None
        self._dispatch_decision(request, decision, reference_time, frame=frame)
        self._remember_route(request, frame)
        return decision

    def _dispatch_decision(
        self,
        request: str,
        decision: dict[str, Any],
        reference_time: datetime | None,
        frame: N4OSIntentFrame | None = None,
    ) -> None:
        dispatch_request = (frame.normalized_request if frame else request) or request
        action = frame.action if frame else None
        if decision["route"] == "both" and _is_day_briefing_request(dispatch_request):
            self._handle_day_briefing(dispatch_request, reference_time)
            return

        if decision["route"] in ("calendar", "both"):
            before = _undo_depth(self.calendar_claw)
            self._handle_calendar_request(dispatch_request, reference_time, action=action)
            self._remember_mutation_route("calendar", before, self.calendar_claw)
        if decision["route"] in ("tasks", "both"):
            before = _undo_depth(self.tasks_claw)
            self._handle_tasks_request(dispatch_request, reference_time, action=action)
            self._remember_mutation_route("tasks", before, self.tasks_claw)
        if decision["route"] == "home_board":
            before = _undo_depth(self.home_board_claw)
            self._handle_home_board_request(dispatch_request, reference_time, action=action)
            self._remember_mutation_route("home_board", before, self.home_board_claw)
        if decision["route"] == "decisions":
            before = _undo_depth(self.decisions_claw)
            self._handle_decision_request(dispatch_request, reference_time, action=action)
            self._remember_mutation_route("decisions", before, self.decisions_claw)
        if decision["route"] == "science_lab":
            self._handle_science_lab_request(dispatch_request)
        if decision["route"] == "library":
            self._handle_library_request(dispatch_request, reference_time, action=action)

    def _remember_mutation_route(
        self,
        route: str,
        before_depth: int,
        claw: Any | None,
    ) -> None:
        if _undo_depth(claw) > before_depth:
            self.route_context.last_mutation_route = route
            self.route_context.mutation_route_stack.append(route)

    def _undo_last_action(self, request: str) -> dict[str, Any]:
        route = self.route_context.mutation_route_stack[-1] if self.route_context.mutation_route_stack else None
        claw_by_route = {
            "calendar": self.calendar_claw,
            "tasks": self.tasks_claw,
            "home_board": self.home_board_claw,
            "decisions": self.decisions_claw,
        }
        claw = claw_by_route.get(route)
        if claw is None or not hasattr(claw, "undo_last_action"):
            message = "Nothing to undo."
            print(message)
            return {"route": "unknown", "intent_summary": message, "confidence": 1.0}

        message = claw.undo_last_action()
        if self.route_context.mutation_route_stack:
            self.route_context.mutation_route_stack.pop()
        self.route_context.last_mutation_route = (
            self.route_context.mutation_route_stack[-1]
            if self.route_context.mutation_route_stack
            else None
        )
        frame = N4OSIntentFrame(
            route=route or "unknown",
            action="undo",
            confidence=1.0,
            followup_kind="none",
            normalized_request=request,
        )
        self._remember_route(request, frame)
        return {
            "route": route or "unknown",
            "intent_summary": message,
            "confidence": 1.0,
        }

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

    def _home_board(self) -> Any:
        if self.home_board_claw is None:
            with module_scope(HOME_BOARD_ROOT):
                self.home_board_claw = _home_board_module().HomeBoardClaw.default()
        return self.home_board_claw

    def _decisions(self) -> Any:
        if self.decisions_claw is None:
            with module_scope(DECISIONS_ROOT):
                self.decisions_claw = _decisions_module().FamilyDecisionsClaw.default()
        return self.decisions_claw

    def _science_lab(self) -> Any:
        if self.science_lab_claw is None:
            with module_scope(SCIENCE_LAB_ROOT):
                self.science_lab_claw = _science_lab_module().ScienceLabClaw.default()
        return self.science_lab_claw

    def _library(self) -> Any:
        if self.library_claw is None:
            with module_scope(LIBRARY_ROOT):
                self.library_claw = _library_module().LibraryClaw.default()
        return self.library_claw

    def _handle_calendar_request(
        self,
        request: str,
        reference_time: datetime | None,
        action: str | None = None,
    ) -> None:
        module = _calendar_module()
        claw = self._calendar()
        if claw is None:
            return
        intent = module.extract_intent(request, now=reference_time)
        action = action or intent["intent"]
        if action == "preparation_checklist":
            claw.preparation_from_request(request, reference_time=reference_time)
        elif action == "family_briefing":
            claw.briefing_from_request(request, reference_time=reference_time)
        elif action == "list_events":
            claw.list_events_from_request(request, reference_time=reference_time)
        elif action == "delete_event":
            claw.delete_event_from_request(request, reference_time=reference_time)
        elif action == "update_event":
            if hasattr(claw, "assign_owner_from_request") and re.search(
                r"\b(?:assign|assigned|owner|owned|belongs)\b|"
                r"\b(?:set|make|change|update|put)\b.*\b(?:owner|as\s+owner)\b",
                request,
                flags=re.IGNORECASE,
            ):
                claw.assign_owner_from_request(request, reference_time=reference_time)
            elif re.search(
                r"^\s*(?:add|append|set|update|put)?\s*(?:a\s+)?(?:note|notes|description|context|fyi)\b",
                request,
                flags=re.IGNORECASE,
            ):
                claw.create_event_from_request(request, reference_time=reference_time)
            else:
                claw.update_event_from_request(request, reference_time=reference_time)
        else:
            claw.create_event_from_request(request, reference_time=reference_time)

    def _handle_tasks_request(
        self,
        request: str,
        reference_time: datetime | None,
        action: str | None = None,
    ) -> None:
        module = _tasks_module()
        claw = self._tasks()
        if claw is None:
            return
        intent = module.extract_intent(request, now=reference_time)
        action = action or intent["intent"]
        if action == "create_task":
            claw.add_task_from_request(request, reference_time=reference_time)
        elif action == "update_task":
            if hasattr(claw, "update_task_from_request"):
                claw.update_task_from_request(request)
            else:
                claw.assign_owner_from_request(request)
        elif action == "complete_task":
            claw.complete_task_from_request(request)
        elif action == "delete_task":
            claw.delete_task_from_request(request)
        elif action == "run_assistant_help":
            claw.run_noah_assistant_help_from_request(
                request,
                reference_time=reference_time,
            )
        else:
            claw.recommend_tasks_from_request(request, reference_time=reference_time)

    def _handle_home_board_request(
        self,
        request: str,
        reference_time: datetime | None,
        action: str | None = None,
    ) -> None:
        module = _home_board_module()
        claw = self._home_board()
        intent = module.extract_intent(request, now=reference_time)
        action = action or intent["intent"]
        if action == "list_items":
            claw.list_items_from_request(request, reference_time=reference_time)
        elif action == "mark_done":
            claw.mark_done_from_request(request)
        else:
            claw.add_item_from_request(request, reference_time=reference_time)

    def _handle_decision_request(
        self,
        request: str,
        reference_time: datetime | None,
        action: str | None = None,
    ) -> None:
        claw = self._decisions()
        if action == "list_decisions":
            claw.list_decisions_from_request(request)
        elif action == "decision_brief":
            claw.decision_brief_from_request(request)
        elif action == "add_option":
            claw.add_option_from_request(request, reference_time=reference_time)
        elif action == "add_evidence":
            claw.add_evidence_from_request(request, reference_time=reference_time)
        elif action == "add_next_step":
            claw.add_next_step_from_request(request, reference_time=reference_time)
        elif action == "record_decision":
            claw.record_decision_from_request(request, reference_time=reference_time)
        else:
            claw.handle_request(request, reference_time=reference_time)

    def _handle_science_lab_request(self, request: str) -> None:
        self._science_lab().plan_from_request(request)

    def _handle_library_request(
        self,
        request: str,
        reference_time: datetime | None,
        action: str | None = None,
    ) -> None:
        claw = self._library()
        if action == "status":
            claw.status_from_request(request, reference_time=reference_time)
        elif action == "record_checkout":
            claw.checkout_from_request(request, reference_time=reference_time)
        else:
            claw.record_from_request(request, reference_time=reference_time)

    def _handle_day_briefing(
        self,
        request: str,
        reference_time: datetime | None,
    ) -> None:
        calendar = self._calendar()
        tasks = self._tasks()
        if calendar is None or tasks is None:
            return

        calendar_module = _calendar_module()
        tasks_module = _tasks_module()
        now = _default_now(reference_time)
        day_start = _start_of_day(now)
        day_end = day_start + timedelta(days=1)

        event_response = calendar.tools.list_calendar_events(
            time_min=day_start.isoformat(),
            time_max=day_end.isoformat(),
            max_results=100,
        )
        if event_response["status"] != "ok":
            print(event_response["message"])
            return

        task_response = tasks.tools.list_tasks(show_completed=False)
        if task_response["status"] != "ok":
            print(task_response["message"])
            return

        events = sorted(
            event_response.get("data", {}).get("events", []),
            key=calendar_module._event_start,
        )
        open_tasks = [
            task
            for task in task_response.get("data", {}).get("tasks", [])
            if task.get("status") != "completed"
        ]
        gap_minutes = _largest_available_gap_minutes(events, now, calendar_module)
        recommend_response = tasks.tools.recommend_tasks(
            filters={"duration_minutes": gap_minutes},
        )
        if recommend_response["status"] != "ok":
            print(recommend_response["message"])
            return

        recommended_tasks = recommend_response.get("data", {}).get("tasks", [])
        message = _format_day_briefing(
            events=events,
            open_tasks=open_tasks,
            recommended_tasks=recommended_tasks,
            now=now,
            calendar_module=calendar_module,
            tasks_module=tasks_module,
        )
        print(message)


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

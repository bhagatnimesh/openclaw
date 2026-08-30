from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta
from io import StringIO
import inspect
import os
import re
import sys
from typing import Any, cast
from zoneinfo import ZoneInfo

try:
    from .ai_refinement import OpenAIN4OSIntentInterpreter
    from .intent_router import (
        CALENDAR_ROOT,
        DECISIONS_ROOT,
        HOME_BOARD_ROOT,
        LIBRARY_ROOT,
        LOW_CONFIDENCE_THRESHOLD,
        N4OSIntentFrame,
        SCIENCE_LAB_ROOT,
        SHOPPING_ROOT,
        TASKS_ROOT,
        _is_object_update_request,
        interpret_request,
        load_scoped_module,
        module_scope,
        route_request,
    )
    from .input_normalizer import improve_entered_text
    from .note_capture import capture_note
    from .prompts import CLARIFICATION_PROMPT, SYSTEM_PROMPT
    from .routing_contracts import (
        OperationResult,
        PreparedCommand,
        ROUTE_REGISTRY,
        RouteId,
        TurnDecision,
        is_valid_route_action,
        parse_explicit_route,
    )
except ImportError:
    from ai_refinement import OpenAIN4OSIntentInterpreter
    from intent_router import (
        CALENDAR_ROOT,
        DECISIONS_ROOT,
        HOME_BOARD_ROOT,
        LIBRARY_ROOT,
        LOW_CONFIDENCE_THRESHOLD,
        N4OSIntentFrame,
        SCIENCE_LAB_ROOT,
        SHOPPING_ROOT,
        TASKS_ROOT,
        _is_object_update_request,
        interpret_request,
        load_scoped_module,
        module_scope,
        route_request,
    )
    from input_normalizer import improve_entered_text
    from note_capture import capture_note
    from prompts import CLARIFICATION_PROMPT, SYSTEM_PROMPT
    from routing_contracts import (
        OperationResult,
        PreparedCommand,
        ROUTE_REGISTRY,
        RouteId,
        TurnDecision,
        is_valid_route_action,
        parse_explicit_route,
    )


DEFAULT_TIMEZONE = "America/Los_Angeles"
OVERLOADED_EVENT_COUNT = 4
DEFAULT_OWNER_VALUES = {"dad", "mom", "both", "grandmom"}
FAMILY_CALENDAR_ID_ENV = "N4OS_FAMILY_CALENDAR_ID"


def _is_google_dependency_error(error: ModuleNotFoundError) -> bool:
    missing_name = error.name or ""
    return missing_name == "google" or missing_name.startswith("google")


def _missing_google_dependency_message(surface: str) -> str:
    return (
        f"{surface} needs the Google Python client libraries before it can "
        "talk to Google APIs. Create and activate a virtualenv, then install "
        "them with `python3 -m pip install google-api-python-client google-auth`."
    )


def _request_with_default_owner(
    request: str,
    intent: dict[str, Any],
    default_owner: str | None,
) -> str:
    owner = (default_owner or "").strip().lower()
    if owner not in DEFAULT_OWNER_VALUES:
        return request
    metadata = intent.get("metadata")
    if isinstance(metadata, dict) and str(metadata.get("owner") or "unknown").lower() != "unknown":
        return request
    return f"{request}\nOwner: {owner}"


def _supports_keyword(callable_value: Any, keyword: str) -> bool:
    try:
        parameters = inspect.signature(callable_value).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == keyword or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _calendar_module() -> Any:
    return load_scoped_module("_n4os_family_calendar_claw", CALENDAR_ROOT, "claw.py")


def _tasks_module() -> Any:
    return load_scoped_module("_n4os_family_tasks_claw", TASKS_ROOT, "claw.py")


def _home_board_module() -> Any:
    return load_scoped_module("_n4os_home_board_claw", HOME_BOARD_ROOT, "claw.py")


def _shopping_module() -> Any:
    return load_scoped_module("_n4os_shopping_list_claw", SHOPPING_ROOT, "claw.py")


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


def _combined_calendar_request(request: str) -> str:
    lowered = request.lower()
    if "tomorrow" in lowered:
        return "calendar briefing tomorrow"
    if "next week" in lowered:
        return "calendar briefing next week"
    named_window = re.search(
        r"\b(?:(?:this|next)\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|weekend)\b",
        lowered,
    )
    if named_window is not None:
        return f"calendar briefing {named_window.group(0)}"
    return "calendar briefing this week"


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
    if normalized in (
        "task",
        "tasks",
        "todo",
        "to-do",
        "create task",
        "task create",
        "use task",
        "use tasks",
    ):
        return "tasks"
    if normalized in ("shopping", "shopping list", "cart", "shop", "use shopping"):
        return "shopping"
    if normalized in ("home board", "today at home", "house board", "use home board"):
        return "home_board"
    if normalized in ("decision", "decisions", "family decisions", "use decisions"):
        return "decisions"
    if normalized in ("science lab", "science", "experiments", "use science lab"):
        return "science_lab"
    if normalized in ("library", "reading garden", "use library"):
        return "library"
    if normalized in (
        "both",
        "calendar and tasks",
        "tasks and calendar",
        "combined planning",
    ):
        return "both"
    return None


def _explicit_library_command(request: str) -> bool:
    return re.match(r"^\s*/library(?:@[A-Za-z0-9_]+)?(?:\s+|:\s*|$)", request, flags=re.IGNORECASE) is not None


def _has_pending_action(claw: Any | None) -> bool:
    return claw is not None and getattr(claw, "pending_action", None) is not None


def _clear_pending_action(claw: Any | None) -> None:
    if claw is not None and hasattr(claw, "pending_action"):
        setattr(claw, "pending_action", None)


def _handle_pending_response(
    claw: Any,
    request: str,
    reference_time: datetime | None = None,
) -> bool:
    try:
        return bool(claw.handle_pending_response(request, reference_time=reference_time))
    except TypeError as error:
        if "unexpected keyword argument" not in str(error):
            raise
        return bool(claw.handle_pending_response(request))


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


def _clear_domain_result(claw: Any | None) -> None:
    if claw is not None and hasattr(claw, "last_result"):
        setattr(claw, "last_result", None)


def _pending_owner(
    calendar: Any | None,
    tasks: Any | None,
    shopping: Any | None = None,
    home_board: Any | None = None,
    decisions: Any | None = None,
) -> str | None:
    if _has_pending_action(calendar):
        return "calendar"
    if _has_pending_action(tasks):
        return "tasks"
    if _has_pending_action(shopping):
        return "shopping"
    if _has_pending_action(home_board):
        return "home_board"
    if _has_pending_action(decisions):
        return "decisions"
    return None


def _is_confident_new_route(decision: dict[str, Any], pending_owner: str) -> bool:
    route = decision.get("route")
    confidence = float(decision.get("confidence") or 0)
    return (
        route in ("capture", "calendar", "tasks", "shopping", "home_board", "decisions", "science_lab", "library", "both")
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
        has_dangling_owner_annotation = (
            re.search(
                r"\b(?:the\s+)?owner\s*(?:is|:)?\s*$",
                request,
                flags=re.IGNORECASE,
            )
            is not None
        )
        task_intent = _tasks_module().extract_intent(request, now=reference_time)
        if task_intent.get("intent") == "create_task":
            return "create_task"
        if _is_object_update_request(request) and not has_dangling_owner_annotation:
            return "update_task"
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
    if route == "shopping":
        return str(_shopping_module().extract_intent(request, now=reference_time)["intent"])
    if route == "decisions":
        return str(_decisions_module().extract_intent(request, now=reference_time)["intent"])
    if route == "science_lab":
        return "plan_experiments"
    if route == "library":
        return str(_library_module().extract_intent(request, now=reference_time)["intent"])
    if route == "both":
        return "combined_planning"
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


def _prepared_target_id(fields: dict[str, Any] | None, *keys: str) -> str | None:
    target = (fields or {}).get("target")
    if not isinstance(target, dict):
        return None
    for key in keys:
        value = str(target.get(key) or "").strip()
        if value:
            return value
    return None


def _prepared_target_value(fields: dict[str, Any] | None, *keys: str) -> str | None:
    return _prepared_target_id(fields, *keys)


def _prepared_owner(fields: dict[str, Any] | None) -> str | None:
    slots = (fields or {}).get("slots")
    if not isinstance(slots, dict):
        return None
    owner = str(slots.get("owner") or "").strip().lower()
    return owner if owner in DEFAULT_OWNER_VALUES else None


@dataclass
class N4OSClaw:
    """Top-level N4OS router over family operations and Science Lab requests."""

    calendar_claw: Any | None = None
    tasks_claw: Any | None = None
    shopping_claw: Any | None = None
    home_board_claw: Any | None = None
    decisions_claw: Any | None = None
    science_lab_claw: Any | None = None
    library_claw: Any | None = None
    system_prompt: str = SYSTEM_PROMPT
    intent_interpreter: Any | None = None
    pending_route_clarification: PendingRouteClarification | None = None
    route_context: RouteContext = field(default_factory=RouteContext)
    last_turn_decision: TurnDecision | None = None
    last_domain_status: str | None = None
    active_semantic_image_path: str | None = field(default=None, repr=False)

    @classmethod
    def default(cls) -> "N4OSClaw":
        return cls(intent_interpreter=OpenAIN4OSIntentInterpreter.from_env_or_none())

    def route(self, request: str, reference_time: datetime | None = None) -> dict[str, Any]:
        request = request if parse_explicit_route(request) is not None else improve_entered_text(request)
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
        *,
        source: str = "telegram_text",
        semantic_image_path: str | None = None,
    ) -> N4OSIntentFrame:
        request = request if parse_explicit_route(request) is not None else improve_entered_text(request)
        context = self._context_payload()
        context["input_modality"] = source.split(":", 1)[0]
        if semantic_image_path:
            context["semantic_image_path"] = semantic_image_path
        return interpret_request(
            request,
            now=reference_time,
            context=context,
            interpreter=self.intent_interpreter,
            prefer_interpreter=source.startswith(("telegram_voice", "telegram_photo")),
        )

    def recognize(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> N4OSIntentFrame:
        """Recognize standalone actions for conversation-state arbitration."""

        request = request if parse_explicit_route(request) is not None else improve_entered_text(request)
        return interpret_request(
            request,
            now=reference_time,
            # Chat replies must not inherit an earlier router target. Only a
            # standalone high-confidence action may interrupt active chat.
            context=None,
            interpreter=None,
        )

    def _context_payload(self) -> dict[str, Any]:
        return self.route_context.to_dict(
            pending_owner=_pending_owner(
                self.calendar_claw,
                self.tasks_claw,
                self.shopping_claw,
                self.home_board_claw,
                self.decisions_claw,
            ),
        )

    def clear_pending_actions(self, *, keep_calendar: bool = False) -> None:
        """Clear stale domain confirmations before a new explicit workflow starts."""
        domains = (
            self.tasks_claw,
            self.shopping_claw,
            self.home_board_claw,
            self.decisions_claw,
        )
        if not keep_calendar:
            domains = (self.calendar_claw, *domains)
        for domain in domains:
            _clear_pending_action(domain)
        self.pending_route_clarification = None

    def _remember_route(
        self,
        request: str,
        frame: N4OSIntentFrame,
    ) -> None:
        self.last_turn_decision = frame.to_turn_decision(request)
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
        source: str = "telegram_text",
        default_owner: str | None = None,
        photo_path: str | None = None,
        semantic_image_path: str | None = None,
    ) -> dict[str, Any]:
        self.active_semantic_image_path = semantic_image_path
        explicit_route = parse_explicit_route(request)
        request = request if explicit_route is not None else improve_entered_text(request)
        calendar = self.calendar_claw
        tasks = self.tasks_claw
        home_board = self.home_board_claw
        decisions = self.decisions_claw
        if _explicit_library_command(request):
            frame = interpret_request(
                request,
                now=reference_time,
                context=self._context_payload(),
                interpreter=None,
            )
            if frame.route == "library":
                decision = frame.to_route_decision()
                _clear_pending_action(calendar)
                _clear_pending_action(tasks)
                _clear_pending_action(self.shopping_claw)
                _clear_pending_action(home_board)
                _clear_pending_action(decisions)
                self.pending_route_clarification = None
                response = self._dispatch_decision(
                    request,
                    decision,
                    reference_time,
                    frame=frame,
                    source=source,
                    default_owner=default_owner,
                    photo_path=photo_path,
                )
                decision["response"] = response
                self._remember_route(request, frame)
                return decision

        pending_owner = _pending_owner(calendar, tasks, self.shopping_claw, home_board, decisions)
        if pending_owner is not None:
            explicit_reply_request = explicit_route.body.strip() if explicit_route else ""
            explicit_reply_body = explicit_route.body.strip().lower().strip(" .!?") if explicit_route else ""
            explicit_confirmation_reply = (
                explicit_route is not None
                and explicit_route.route == pending_owner
                and re.fullmatch(
                    r"(?:yes|y|no|n|cancel)(?:\s+please)?",
                    explicit_reply_body,
                )
                is not None
            )
            explicit_value_reply = (
                explicit_route is not None
                and explicit_route.route == pending_owner
                and re.fullmatch(
                    r"(?:\d+|\d{1,2}(?::\d{2})?\s*(?:am|pm))",
                    explicit_reply_body,
                )
                is not None
            )
            if explicit_confirmation_reply or explicit_value_reply:
                request = explicit_reply_request
                explicit_route = None
            frame = interpret_request(
                request,
                now=reference_time,
                context=self._context_payload(),
                interpreter=None,
            )
            decision = frame.to_route_decision()
            # A slash command starts a new operation even within the same domain.
            # Otherwise stale clarification state consumes the explicit command.
            if explicit_route is not None or _is_confident_new_route(decision, pending_owner):
                _clear_pending_action(calendar)
                _clear_pending_action(tasks)
                _clear_pending_action(self.shopping_claw)
                _clear_pending_action(home_board)
                _clear_pending_action(decisions)
                self.pending_route_clarification = None
                response = self._dispatch_decision(
                    request,
                    decision,
                    reference_time,
                    frame=frame,
                    source=source,
                    default_owner=default_owner,
                    photo_path=photo_path,
                )
                decision["response"] = response
                self._remember_route(request, frame)
                return decision

        if calendar is not None and _handle_pending_response(calendar, request, reference_time):
            self._adopt_domain_status(calendar)
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
                "action": "pending_response",
                "intent_summary": "Handled pending family-calendar response.",
                "confidence": 1.0,
            }

        if tasks is not None and tasks.handle_pending_response(request):
            self._adopt_domain_status(tasks)
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
                "action": "pending_response",
                "intent_summary": "Handled pending family-tasks response.",
                "confidence": 1.0,
            }

        handle_pending_decision = getattr(decisions, "handle_pending_response", None)
        if callable(handle_pending_decision) and handle_pending_decision(request):
            self._adopt_domain_status(decisions)
            frame = N4OSIntentFrame(
                route="decisions",
                action="pending_response",
                confidence=1.0,
                followup_kind="pending_response",
                normalized_request=request,
            )
            self._remember_route(request, frame)
            return {
                "route": "decisions",
                "action": "pending_response",
                "intent_summary": "Handled pending family backlog response.",
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
            response = self._dispatch_decision(
                dispatch_request,
                decision,
                pending_route.reference_time,
                frame=frame,
                source=source,
                default_owner=default_owner,
                photo_path=photo_path,
            )
            decision["response"] = response
            self._remember_route(dispatch_request, frame)
            return decision

        frame = self.interpret(
            request,
            reference_time=reference_time,
            source=source,
            semantic_image_path=semantic_image_path,
        )
        decision = frame.to_route_decision()
        if (
            decision["route"] == "unknown"
            or decision["confidence"] < LOW_CONFIDENCE_THRESHOLD
        ):
            self.pending_route_clarification = PendingRouteClarification(
                request=request,
                reference_time=reference_time,
            )
            response = frame.clarification_question or CLARIFICATION_PROMPT
            decision["response"] = response
            self.last_turn_decision = frame.to_turn_decision(request)
            print(response)
            return decision

        self.pending_route_clarification = None
        response = self._dispatch_decision(
            request,
            decision,
            reference_time,
            frame=frame,
            source=source,
            default_owner=default_owner,
            photo_path=photo_path,
        )
        decision["response"] = response
        self._remember_route(request, frame)
        return decision

    def handle_turn(
        self,
        request: str,
        reference_time: datetime | None = None,
        source: str = "telegram_text",
        default_owner: str | None = None,
        photo_path: str | None = None,
        semantic_image_path: str | None = None,
    ) -> OperationResult:
        """Return one structured turn result while legacy domain CLIs still print."""

        self.last_turn_decision = None
        self.last_domain_status = None
        before_mutations = len(self.route_context.mutation_route_stack)
        output = StringIO()
        with redirect_stdout(output):
            decision = self.handle_request(
                request,
                reference_time=reference_time,
                source=source,
                default_owner=default_owner,
                photo_path=photo_path,
                semantic_image_path=semantic_image_path,
            )

        route = cast(RouteId, str(decision.get("route") or "unknown"))
        action = str(decision.get("action") or "unknown")
        response = str(
            decision.get("response")
            or output.getvalue().strip()
            or decision.get("intent_summary")
            or "Done."
        )
        after_mutations = len(self.route_context.mutation_route_stack)
        mutation_recorded = after_mutations > before_mutations
        awaiting_domain_response = _pending_owner(
            self.calendar_claw,
            self.tasks_claw,
            self.shopping_claw,
            self.home_board_claw,
            self.decisions_claw,
        ) is not None

        spec = ROUTE_REGISTRY.get(route, ROUTE_REGISTRY["unknown"])
        reported_mutation_succeeded = (
            action in spec.mutating_actions and self.last_domain_status == "ok"
        )
        decision_missing_fields = bool(
            self.last_turn_decision and self.last_turn_decision.missing_fields
        )
        effect_is_mutation = mutation_recorded or reported_mutation_succeeded
        if self.last_domain_status == "error":
            status = "failure"
        elif self.last_domain_status in {"needs_information"}:
            status = "clarification"
        elif self.last_domain_status == "not_counted":
            status = "noop"
        elif route == "unknown" or awaiting_domain_response:
            status = "clarification"
        elif effect_is_mutation:
            status = "success"
        elif action in spec.mutating_actions and decision_missing_fields:
            status = "clarification"
        elif action in spec.mutating_actions:
            # A mutating owner must prove an effect through its undo stack or
            # structured domain status. No proof means the mutation failed.
            status = "failure"
        else:
            status = "success"

        source_value = str(decision.get("source") or "rules")
        if source_value not in {"explicit", "rules", "llm", "clarification"}:
            source_value = "rules"
        turn_decision = self.last_turn_decision
        if turn_decision is None:
            turn_decision = TurnDecision(
                route=route,
                action=action,
                confidence=float(decision.get("confidence") or 0),
                source=cast(Any, source_value),
                original_input=request,
                normalized_input=request,
                clarification=response if status == "clarification" else None,
            )
        else:
            turn_decision = replace(
                turn_decision,
                original_input=request,
                clarification=response if status == "clarification" else None,
            )
        return OperationResult(
            status=cast(Any, status),
            route=route,
            action=action,
            response=response,
            effect="mutation" if effect_is_mutation else ("read" if status == "success" else "none"),
            mutation_reference=self.route_context.last_mutation_route if mutation_recorded else None,
            undoable=mutation_recorded,
            decision=turn_decision,
        )

    def _dispatch_decision(
        self,
        request: str,
        decision: dict[str, Any],
        reference_time: datetime | None,
        frame: N4OSIntentFrame | None = None,
        source: str = "telegram_text",
        default_owner: str | None = None,
        photo_path: str | None = None,
    ) -> str:
        prepared = self._prepare_command(request, frame)
        dispatch_request = prepared.original_input
        action = prepared.action
        prepared_owner = _prepared_owner(prepared.fields) or default_owner
        if decision["route"] == "both" and _is_day_briefing_request(dispatch_request):
            return self._handle_day_briefing(dispatch_request, reference_time)

        responses: list[str] = []
        if decision["route"] == "capture":
            captured = capture_note(dispatch_request, now=reference_time, source=source)
            self.last_domain_status = "ok"
            return (
                f"Captured {captured.kind} note: {captured.title} "
                f"-> {captured.path.relative_to(captured.path.parents[2])}"
            )
        if decision["route"] in ("calendar", "both"):
            _clear_domain_result(self.calendar_claw)
            before = _undo_depth(self.calendar_claw)
            response = self._handle_calendar_request(
                (
                    _combined_calendar_request(dispatch_request)
                    if decision["route"] == "both"
                    else dispatch_request
                ),
                reference_time,
                action="family_briefing" if decision["route"] == "both" else action,
                default_owner=prepared_owner,
                prepared_fields=prepared.fields,
                source=source,
            )
            if response:
                responses.append(response)
            self._adopt_domain_status(self.calendar_claw)
            self._remember_mutation_route("calendar", before, self.calendar_claw)
        if decision["route"] in ("tasks", "both"):
            _clear_domain_result(self.tasks_claw)
            before = _undo_depth(self.tasks_claw)
            response = self._handle_tasks_request(
                dispatch_request,
                reference_time,
                action="recommend_tasks" if decision["route"] == "both" else action,
                default_owner=prepared_owner,
                prepared_fields=prepared.fields,
                source=source,
            )
            if response:
                responses.append(response)
            self._adopt_domain_status(self.tasks_claw)
            self._remember_mutation_route("tasks", before, self.tasks_claw)
        if decision["route"] == "shopping":
            _clear_domain_result(self.shopping_claw)
            before = _undo_depth(self.shopping_claw)
            response = self._handle_shopping_request(dispatch_request, reference_time, action=action)
            if response:
                responses.append(response)
            self._adopt_domain_status(self.shopping_claw)
            self._remember_mutation_route("shopping", before, self.shopping_claw)
        if decision["route"] == "home_board":
            _clear_domain_result(self.home_board_claw)
            before = _undo_depth(self.home_board_claw)
            response = self._handle_home_board_request(
                dispatch_request,
                reference_time,
                action=action,
                prepared_fields=prepared.fields,
            )
            if response:
                responses.append(response)
            self._adopt_domain_status(self.home_board_claw)
            self._remember_mutation_route("home_board", before, self.home_board_claw)
        if decision["route"] == "decisions":
            _clear_domain_result(self.decisions_claw)
            before = _undo_depth(self.decisions_claw)
            response = self._handle_decision_request(
                dispatch_request,
                reference_time,
                action=action,
                source=source,
                default_owner=prepared_owner,
            )
            if response:
                responses.append(response)
            self._adopt_domain_status(self.decisions_claw)
            self._remember_mutation_route("decisions", before, self.decisions_claw)
        if decision["route"] == "science_lab":
            _clear_domain_result(self.science_lab_claw)
            response = self._handle_science_lab_request(dispatch_request)
            if response:
                responses.append(response)
            self._adopt_domain_status(self.science_lab_claw)
        if decision["route"] == "library":
            _clear_domain_result(self.library_claw)
            response = self._handle_library_request(
                dispatch_request,
                reference_time,
                action=action,
                source=source,
                photo_path=photo_path,
            )
            if response:
                responses.append(response)
            self._adopt_domain_status(self.library_claw)
        return "\n".join(responses).strip()

    def _prepare_command(self, request: str, frame: N4OSIntentFrame | None) -> PreparedCommand:
        if frame is None:
            return PreparedCommand(
                route="unknown",
                action="unknown",
                original_input=request,
                fields={},
            )
        if not is_valid_route_action(frame.route, frame.action):
            raise ValueError(f"invalid prepared action for {frame.route}: {frame.action}")
        # Deterministic normalization is owner-controlled. LLM frames retain the
        # original input and contribute only typed target/slot hints.
        domain_input = frame.normalized_request or request
        target = dict(frame.target)
        if frame.decision_source == "llm":
            prior_target = self.route_context.last_artifact.get("target", {})
            trusted_ids = {
                str(value)
                for value in prior_target.values()
                if isinstance(value, str) and value
            }
            target = {
                key: value
                for key, value in target.items()
                if key not in {"id", "event_id", "task_id", "item_id", "calendar_id", "calendarId"}
                or (isinstance(value, str) and (value in trusted_ids or value in request))
            }
        return PreparedCommand(
            route=cast(RouteId, frame.route),
            action=frame.action,
            original_input=domain_input,
            fields={
                "target": target,
                "slots": dict(frame.slots),
                "decision_source": frame.decision_source,
            },
        )

    def _remember_mutation_route(
        self,
        route: str,
        before_depth: int,
        claw: Any | None,
    ) -> str:
        if _undo_depth(claw) > before_depth:
            self.route_context.last_mutation_route = route
            self.route_context.mutation_route_stack.append(route)

    def _adopt_domain_status(self, claw: Any | None) -> None:
        result = getattr(claw, "last_result", None)
        if not isinstance(result, dict):
            return
        status = str(result.get("status") or "")
        priority = {
            "ok": 1,
            "not_counted": 2,
            "needs_information": 3,
            "error": 4,
        }
        current_priority = priority.get(self.last_domain_status or "", 0)
        if priority.get(status, 0) >= current_priority:
            self.last_domain_status = status

    def _undo_last_action(self, request: str) -> dict[str, Any]:
        route = self.route_context.mutation_route_stack[-1] if self.route_context.mutation_route_stack else None
        claw_by_route = {
            "calendar": self.calendar_claw,
            "tasks": self.tasks_claw,
            "shopping": self.shopping_claw,
            "home_board": self.home_board_claw,
            "decisions": self.decisions_claw,
        }
        claw = claw_by_route.get(route)
        if claw is None or not hasattr(claw, "undo_last_action"):
            message = "Nothing to undo."
            print(message)
            return {
                "route": "unknown",
                "action": "undo",
                "intent_summary": message,
                "response": message,
                "confidence": 1.0,
            }

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
            "action": "undo",
            "intent_summary": message,
            "response": message,
            "confidence": 1.0,
        }

    def _calendar(self) -> Any:
        if self.calendar_claw is None:
            with module_scope(CALENDAR_ROOT):
                try:
                    calendar_id = os.environ.get(FAMILY_CALENDAR_ID_ENV) or "primary"
                    self.calendar_claw = _calendar_module().FamilyCalendarClaw.default(
                        calendar_id=calendar_id,
                    )
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

    def _shopping(self) -> Any:
        if self.shopping_claw is None:
            with module_scope(SHOPPING_ROOT):
                self.shopping_claw = _shopping_module().ShoppingClaw.default()
        return self.shopping_claw

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
        default_owner: str | None = None,
        prepared_fields: dict[str, Any] | None = None,
        source: str = "telegram_text",
    ) -> str:
        module = _calendar_module()
        claw = self._calendar()
        if claw is None:
            self.last_domain_status = "error"
            return _missing_google_dependency_message("Family Calendar")
        extract_calendar_intent = getattr(claw, "_extract_intent_from_request", None)
        if callable(extract_calendar_intent):
            extract_kwargs = {}
            if _supports_keyword(extract_calendar_intent, "semantic_image_path"):
                extract_kwargs["semantic_image_path"] = self.active_semantic_image_path
            intent = extract_calendar_intent(request, reference_time, **extract_kwargs)
        else:
            intent = module.extract_intent(request, now=reference_time)
        if intent.get("intent") == "add_guests" and action in {None, "create_event", "update_event"}:
            action = "add_guests"
        action = action or intent["intent"]
        event_id = _prepared_target_id(prepared_fields, "event_id", "id")
        calendar_id = _prepared_target_value(prepared_fields, "calendar_id", "calendarId")
        if action == "preparation_checklist":
            return claw.preparation_from_request(request, reference_time=reference_time)
        elif action == "family_briefing":
            return claw.briefing_from_request(request, reference_time=reference_time)
        elif action == "list_events":
            return claw.list_events_from_request(request, reference_time=reference_time)
        elif action == "delete_event":
            return claw.delete_event_from_request(
                request,
                reference_time=reference_time,
                event_id=event_id,
                calendar_id=calendar_id,
            )
        elif action == "update_event":
            if intent["intent"] == "add_guests" and hasattr(claw, "add_guests_from_request"):
                return claw.add_guests_from_request(
                    request,
                    reference_time=reference_time,
                    event_id=event_id,
                    calendar_id=calendar_id,
                )
            elif hasattr(claw, "assign_owner_from_request") and re.search(
                r"\b(?:assign|assigned|owner|owned|belongs)\b|"
                r"\b(?:set|make|change|update|put)\b.*\b(?:owner|as\s+owner)\b",
                request,
                flags=re.IGNORECASE,
            ):
                return claw.assign_owner_from_request(
                    request,
                    reference_time=reference_time,
                    event_id=event_id,
                    calendar_id=calendar_id,
                )
            elif re.search(
                r"^\s*(?:add|append|set|update|put)?\s*(?:a\s+)?(?:note|notes|description|context|fyi)\b",
                request,
                flags=re.IGNORECASE,
            ):
                return claw.create_event_from_request(
                    request,
                    reference_time=reference_time,
                    event_id=event_id,
                    calendar_id=calendar_id,
                )
            else:
                return claw.update_event_from_request(
                    request,
                    reference_time=reference_time,
                    event_id=event_id,
                    calendar_id=calendar_id,
                )
        elif action == "add_guests" and hasattr(claw, "add_guests_from_request"):
            if intent.get("intent") == "add_guests" and hasattr(claw, "add_guests_from_intent"):
                return claw.add_guests_from_intent(
                    intent,
                    event_id=event_id,
                    calendar_id=calendar_id,
                    **(
                        {"reference_time": reference_time}
                        if _supports_keyword(claw.add_guests_from_intent, "reference_time")
                        else {}
                    ),
                )
            return claw.add_guests_from_request(
                request,
                reference_time=reference_time,
                event_id=event_id,
                calendar_id=calendar_id,
            )
        else:
            if event_id and re.search(
                r"^\s*(?:add|append|set|update|put)?\s*(?:a\s+)?(?:note|notes|description|context|fyi)\b",
                request,
                flags=re.IGNORECASE,
            ):
                return claw.create_event_from_request(
                    request,
                    reference_time=reference_time,
                    event_id=event_id,
                    calendar_id=calendar_id,
                )
            if event_id and action == "add_guests" and hasattr(claw, "add_guests_from_request"):
                return claw.add_guests_from_request(
                    request,
                    reference_time=reference_time,
                    event_id=event_id,
                    calendar_id=calendar_id,
                )
            create_request = _request_with_default_owner(request, intent, default_owner)
            create_intent = dict(intent)
            owner = (default_owner or "").strip().lower()
            metadata = dict(create_intent.get("metadata") or {})
            if owner in DEFAULT_OWNER_VALUES and str(metadata.get("owner") or "unknown") == "unknown":
                metadata["owner"] = owner
                create_intent["metadata"] = metadata
            if source.startswith(("telegram_voice", "telegram_photo")) and _supports_keyword(
                claw.create_event_from_request,
                "require_confirmation",
            ):
                create_kwargs = {
                    "reference_time": reference_time,
                    "require_confirmation": True,
                }
                if _supports_keyword(claw.create_event_from_request, "semantic_intent"):
                    create_kwargs["semantic_intent"] = create_intent
                return claw.create_event_from_request(create_request, **create_kwargs)
            create_kwargs = {"reference_time": reference_time}
            if _supports_keyword(claw.create_event_from_request, "semantic_intent"):
                create_kwargs["semantic_intent"] = create_intent
            return claw.create_event_from_request(create_request, **create_kwargs)

    def _handle_tasks_request(
        self,
        request: str,
        reference_time: datetime | None,
        action: str | None = None,
        default_owner: str | None = None,
        prepared_fields: dict[str, Any] | None = None,
        source: str = "telegram_text",
    ) -> str:
        module = _tasks_module()
        claw = self._tasks()
        if claw is None:
            self.last_domain_status = "error"
            return _missing_google_dependency_message("Family Tasks")
        interpret_tasks = getattr(claw, "interpret_request", None)
        intent = (
            interpret_tasks(
                request,
                reference_time=reference_time,
                **(
                    {"semantic_image_path": self.active_semantic_image_path}
                    if _supports_keyword(interpret_tasks, "semantic_image_path")
                    else {}
                ),
            )
            if callable(interpret_tasks)
            else module.extract_intent(request, now=reference_time)
        )
        action = action or intent["intent"]
        if intent.get("intent") != action:
            semantic_destination = {
                key: intent.get(key)
                for key in ("task_list_name", "task_list_id_hint")
                if intent.get(key)
            }
            deterministic_tasks = getattr(claw, "deterministic_intent", None)
            intent = (
                deterministic_tasks(request, reference_time=reference_time)
                if callable(deterministic_tasks)
                else module.extract_intent(request, now=reference_time)
            )
            intent.update(semantic_destination)
        task_id = _prepared_target_id(prepared_fields, "task_id", "id")
        task_list_id = (
            _prepared_target_value(prepared_fields, "task_list_id", "taskListId")
            or "@default"
        )
        resolve_task_list = getattr(claw, "_resolve_task_list", None)
        if callable(resolve_task_list) and (
            intent.get("task_list_name") or intent.get("task_list_id_hint")
        ):
            task_list_id, task_list_error = resolve_task_list(intent)
            if task_list_error is not None:
                claw.last_result = {"status": "needs_information"}
                return task_list_error
        if action == "create_task":
            create_request = _request_with_default_owner(request, intent, default_owner)
            should_confirm = bool(
                source.startswith(("telegram_voice", "telegram_photo"))
                or "Image text:" in request
                or len(re.findall(r"(?im)^\s*/tasks?\b", request)) > 1
                or (
                    callable(getattr(claw, "requires_create_confirmation", None))
                    and claw.requires_create_confirmation(request)
                )
            )
            if should_confirm and _supports_keyword(
                claw.add_task_from_request,
                "require_confirmation",
            ):
                return claw.add_task_from_request(
                    create_request,
                    reference_time=reference_time,
                    require_confirmation=True,
                    semantic_intent=intent,
                )
            create_kwargs = {"reference_time": reference_time}
            if _supports_keyword(claw.add_task_from_request, "semantic_intent"):
                create_kwargs["semantic_intent"] = intent
            return claw.add_task_from_request(create_request, **create_kwargs)
        elif action == "update_task":
            if hasattr(claw, "update_task_from_request"):
                update_kwargs = {"task_id": task_id}
                if _supports_keyword(claw.update_task_from_request, "task_list_id"):
                    update_kwargs["task_list_id"] = task_list_id
                if _supports_keyword(claw.update_task_from_request, "semantic_intent"):
                    update_kwargs["semantic_intent"] = intent
                return claw.update_task_from_request(request, **update_kwargs)
            else:
                return claw.assign_owner_from_request(request)
        elif action == "complete_task":
            complete_kwargs = {"task_id": task_id}
            if _supports_keyword(claw.complete_task_from_request, "task_list_id"):
                complete_kwargs["task_list_id"] = task_list_id
            if _supports_keyword(claw.complete_task_from_request, "query"):
                complete_kwargs["query"] = intent.get("query")
            return claw.complete_task_from_request(request, **complete_kwargs)
        elif action == "delete_task":
            delete_kwargs = {"task_id": task_id}
            if _supports_keyword(claw.delete_task_from_request, "task_list_id"):
                delete_kwargs["task_list_id"] = task_list_id
            if _supports_keyword(claw.delete_task_from_request, "query"):
                delete_kwargs["query"] = intent.get("query")
            return claw.delete_task_from_request(request, **delete_kwargs)
        elif action == "run_assistant_help":
            assistant_kwargs = {"reference_time": reference_time}
            if _supports_keyword(claw.run_noah_assistant_help_from_request, "task_list_id"):
                assistant_kwargs["task_list_id"] = task_list_id
            return claw.run_noah_assistant_help_from_request(request, **assistant_kwargs)
        else:
            recommend_kwargs = {"reference_time": reference_time}
            if _supports_keyword(claw.recommend_tasks_from_request, "semantic_intent"):
                recommend_kwargs.update(
                    semantic_intent=intent,
                    task_list_id=task_list_id,
                )
            return claw.recommend_tasks_from_request(request, **recommend_kwargs)

    def _handle_home_board_request(
        self,
        request: str,
        reference_time: datetime | None,
        action: str | None = None,
        prepared_fields: dict[str, Any] | None = None,
    ) -> str:
        module = _home_board_module()
        claw = self._home_board()
        intent = module.extract_intent(request, now=reference_time)
        action = action or intent["intent"]
        item_id = _prepared_target_id(prepared_fields, "item_id", "id")
        if action == "list_items":
            return claw.list_items_from_request(request, reference_time=reference_time)
        elif action == "mark_done":
            return claw.mark_done_from_request(request, item_id=item_id)
        else:
            return claw.add_item_from_request(request, reference_time=reference_time)

    def _handle_shopping_request(
        self,
        request: str,
        reference_time: datetime | None,
        action: str | None = None,
    ) -> str:
        claw = self._shopping()
        # Some injected shopping implementations still expose only the public
        # aggregate handler. Keep dispatch typed while honoring that boundary.
        if not hasattr(claw, "add_items_from_request"):
            return claw.handle_request(request, reference_time=reference_time)
        if action == "list_lists":
            message = claw.list_lists_from_request(request, reference_time=reference_time)
        elif action == "list_items":
            message = claw.list_items_from_request(request, reference_time=reference_time)
        elif action in {"add_item", "add_items"}:
            message = claw.add_items_from_request(request, reference_time=reference_time)
        elif action == "check_item":
            message = claw.check_item_from_request(request, checked=True)
        elif action == "uncheck_item":
            message = claw.check_item_from_request(request, checked=False)
        elif action == "delete_item":
            message = claw.delete_item_from_request(request)
        elif action == "clear_list":
            message = claw.clear_list_from_request(request, reference_time=reference_time)
        elif action == "move_item":
            message = claw.move_item_from_request(request)
        else:
            message = claw.handle_request(request, reference_time=reference_time)
        result = getattr(claw, "last_result", None)
        self.last_domain_status = str(result.get("status")) if isinstance(result, dict) else None
        return message

    def _handle_decision_request(
        self,
        request: str,
        reference_time: datetime | None,
        action: str | None = None,
        source: str = "telegram_text",
        default_owner: str | None = None,
    ) -> str:
        claw = self._decisions()
        if action == "list_decisions":
            return claw.list_decisions_from_request(request)
        elif action == "decision_brief":
            return claw.decision_brief_from_request(request)
        elif action == "add_option":
            return claw.add_option_from_request(request, reference_time=reference_time)
        elif action == "add_evidence":
            return claw.add_evidence_from_request(request, reference_time=reference_time)
        elif action == "add_next_step":
            return claw.add_next_step_from_request(request, reference_time=reference_time)
        elif action == "record_decision":
            return claw.record_decision_from_request(request, reference_time=reference_time)
        else:
            try:
                return claw.handle_request(
                    request,
                    reference_time=reference_time,
                    source=source,
                    default_owner=default_owner,
                )
            except TypeError as error:
                if "unexpected keyword argument" not in str(error):
                    raise
                return claw.handle_request(request, reference_time=reference_time)

    def _handle_science_lab_request(self, request: str) -> str:
        return self._science_lab().plan_from_request(request)

    def _handle_library_request(
        self,
        request: str,
        reference_time: datetime | None,
        action: str | None = None,
        source: str = "telegram_text",
        photo_path: str | None = None,
    ) -> str:
        claw = self._library()
        if action == "status":
            message = claw.status_from_request(request, reference_time=reference_time)
        elif action == "record_checkout":
            message = claw.checkout_from_request(request, reference_time=reference_time, source=source)
        elif action == "update_reading":
            message = claw.update_from_request(request, reference_time=reference_time)
        elif action == "delete_reading":
            message = claw.delete_from_request(request, reference_time=reference_time)
        else:
            message = claw.record_from_request(
                request,
                reference_time=reference_time,
                source=source,
                photo_path=photo_path,
            )
        result = getattr(claw, "last_result", None)
        self.last_domain_status = str(result.get("status")) if isinstance(result, dict) else None
        return message

    def _handle_day_briefing(
        self,
        request: str,
        reference_time: datetime | None,
    ) -> str:
        calendar = self._calendar()
        tasks = self._tasks()
        if calendar is None or tasks is None:
            self.last_domain_status = "error"
            return "Calendar and Tasks need their Google dependencies before I can build a briefing."

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
            self.last_domain_status = str(event_response["status"])
            print(event_response["message"])
            return event_response["message"]

        task_response = tasks.tools.list_tasks(show_completed=False)
        if task_response["status"] != "ok":
            self.last_domain_status = str(task_response["status"])
            print(task_response["message"])
            return task_response["message"]

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
            self.last_domain_status = str(recommend_response["status"])
            print(recommend_response["message"])
            return recommend_response["message"]

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
        return message


def run_cli(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    claw = N4OSClaw.default()
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

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import importlib.util
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Any, Iterator, Literal


Route = Literal["calendar", "tasks", "home_board", "both", "unknown"]

LOW_CONFIDENCE_THRESHOLD = 0.6

CALENDAR_INTENTS = {
    "create_event",
    "list_events",
    "update_event",
    "delete_event",
    "family_briefing",
    "preparation_checklist",
}
TASK_INTENTS = {
    "create_task",
    "recommend_tasks",
    "complete_task",
    "delete_task",
}
HOME_BOARD_INTENTS = {
    "add_item",
    "add_items",
    "list_items",
    "mark_done",
}

LOCAL_MODULES = ("constants", "intent", "matcher", "prompts", "provider", "tools")
MISSING = object()

CLAW_ROOT = Path(__file__).resolve().parents[1]
CALENDAR_ROOT = CLAW_ROOT / "family-calendar"
TASKS_ROOT = CLAW_ROOT / "family-tasks"
HOME_BOARD_ROOT = CLAW_ROOT / "home-board"


@dataclass(frozen=True)
class RouteDecision:
    route: Route
    intent_summary: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "intent_summary": self.intent_summary,
            "confidence": self.confidence,
        }


@contextmanager
def module_scope(module_root: Path) -> Iterator[None]:
    original_path = list(sys.path)
    saved_modules = {
        name: sys.modules.get(name, MISSING)
        for name in LOCAL_MODULES
    }
    for name in LOCAL_MODULES:
        sys.modules.pop(name, None)

    sys.path.insert(0, str(module_root))
    try:
        yield
    finally:
        sys.path[:] = original_path
        for name, module in saved_modules.items():
            if module is MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def load_scoped_module(unique_name: str, module_root: Path, filename: str) -> ModuleType:
    module_path = module_root / filename
    spec = importlib.util.spec_from_file_location(unique_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = module
    with module_scope(module_root):
        spec.loader.exec_module(module)
    return module


def _calendar_intent_module() -> ModuleType:
    return load_scoped_module("_n4os_family_calendar_intent", CALENDAR_ROOT, "intent.py")


def _tasks_intent_module() -> ModuleType:
    return load_scoped_module("_n4os_family_tasks_intent", TASKS_ROOT, "intent.py")


def _home_board_intent_module() -> ModuleType:
    return load_scoped_module("_n4os_home_board_intent", HOME_BOARD_ROOT, "intent.py")


def _extract_intents(
    request: str,
    now: datetime | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    calendar_intent = _calendar_intent_module().extract_intent(request, now=now)
    task_intent = _tasks_intent_module().extract_intent(request, now=now)
    home_board_intent = _home_board_intent_module().extract_intent(request, now=now)
    return calendar_intent, task_intent, home_board_intent


def _has_time_anchor(text: str) -> bool:
    return bool(
        re.search(
            r"\b(today|tomorrow|tonight|weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            text,
        )
        or re.search(r"\b\d{1,2}(?::\d{2})?\s*(am|pm)\b", text)
        or re.search(r"\bat\s+\d{1,2}(?::\d{2})?\b", text)
    )


def _score_calendar(text: str, calendar_intent: dict[str, Any]) -> float:
    score = 0.0
    intent = calendar_intent.get("intent")
    has_explicit_task_word = bool(re.search(r"\b(task|todo|to-do|open loop|open loops)\b", text))
    if intent == "create_event" and _has_time_anchor(text) and not has_explicit_task_word:
        score += 0.45
    elif intent in CALENDAR_INTENTS and intent != "create_event":
        score += 0.45

    if re.search(r"\b(calendar|schedule|event|appointment|appt|meeting)\b", text):
        score += 0.35
    if re.search(r"\b(dentist|dental|doctor|flight|pickup|dinner|reservation)\b", text):
        score += 0.25
    if _has_time_anchor(text):
        score += 0.2
    if re.search(r"^\s*(add|create|schedule|go to)\b", text) and _has_time_anchor(text):
        score += 0.2
    if re.search(r"^\s*(what|what's|whats|show|list)\b", text) and re.search(
        r"\b(have|calendar|schedule|coming up)\b",
        text,
    ):
        score += 0.35
    if re.search(r"^\s*(cancel|move|reschedule|change|push|delay)\b", text):
        score += 0.2

    return min(score, 1.0)


def _score_tasks(text: str, task_intent: dict[str, Any]) -> float:
    score = 0.0
    intent = task_intent.get("intent")
    if intent in {"create_task", "complete_task", "delete_task"}:
        score += 0.35
    elif intent == "recommend_tasks" and re.search(r"\b(what|which|recommend|do)\b", text):
        score += 0.2
        if task_intent.get("filters"):
            score += 0.2

    if re.search(r"\b(task|todo|to-do|open loop|open loops)\b", text):
        score += 0.45
    if re.search(r"^\s*(complete|finish|mark)\b", text):
        score += 0.35
    if re.search(r"\b(done|what can i do|what should i do|recommend|driving|commute|energy)\b", text):
        score += 0.3
    if re.search(r"\b(filter|trash|laundry|clean|repair|call|research)\b", text):
        score += 0.2
    if re.search(r"^\s*(add|create|capture|remember)\b", text) and "task" in text:
        score += 0.2

    return min(score, 1.0)


def _score_home_board(text: str, home_board_intent: dict[str, Any]) -> float:
    score = 0.0
    intent = home_board_intent.get("intent")
    if intent in HOME_BOARD_INTENTS:
        score += 0.45
    if re.search(r"\b(home board|today at home|house board)\b", text):
        score += 0.45
    if re.search(r"\bbefore\b", text) and re.search(r"\b(leaves?|leaving|leave)\b", text):
        score += 0.45
    if re.search(r"\b(helper|nysha|nimesh|dad|mom|family|everyone)\b", text):
        score += 0.25
    if re.search(r"\b(journal|form|payment|fridge|food|passport|lunch|library book|permission slip)\b", text):
        score += 0.25
    if re.search(r"\b(task|todo|to-do|open loop|open loops)\b", text) and not re.search(
        r"\b(home board|today at home)\b",
        text,
    ):
        score -= 0.3
    return max(0.0, min(score, 1.0))


def _is_combined_planning(text: str) -> bool:
    return bool(
        re.search(
            r"\b(briefing|plan my day|day plan|daily plan|daily briefing|focus on today|today look like)\b",
            text,
        )
        or re.search(r"\bwhat should i focus\b", text)
        or (
            re.search(r"\b(plan|brief|overview|look ahead)\b", text)
            and re.search(r"\b(day|today|tomorrow|week)\b", text)
        )
    )


def _summary(route: Route, calendar_intent: dict[str, Any], task_intent: dict[str, Any]) -> str:
    if route == "calendar":
        return f"Route to family-calendar for {calendar_intent.get('intent', 'calendar request')}."
    if route == "tasks":
        return f"Route to family-tasks for {task_intent.get('intent', 'task request')}."
    if route == "home_board":
        return "Route to home-board for household notice."
    if route == "both":
        return (
            "Route to family-calendar and family-tasks for combined planning or briefing."
        )
    return "Could not confidently choose Calendar, Tasks, Home Board, or both."


def route_request(
    request: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    calendar_intent, task_intent, home_board_intent = _extract_intents(request, now)
    text = request.lower().strip()
    if not text:
        return RouteDecision(
            route="unknown",
            intent_summary="Empty request.",
            confidence=0.0,
        ).to_dict()

    if _is_combined_planning(text):
        return RouteDecision(
            route="both",
            intent_summary=_summary("both", calendar_intent, task_intent),
            confidence=0.88,
        ).to_dict()

    calendar_score = _score_calendar(text, calendar_intent)
    task_score = _score_tasks(text, task_intent)
    home_board_score = _score_home_board(text, home_board_intent)

    scores: dict[Route, float] = {
        "calendar": calendar_score,
        "tasks": task_score,
        "home_board": home_board_score,
    }
    best_route = max(scores, key=scores.get)
    confidence = scores[best_route]
    second_best = max(score for route, score in scores.items() if route != best_route)

    if confidence < LOW_CONFIDENCE_THRESHOLD:
        route: Route = "unknown"
    elif (
        best_route in ("calendar", "tasks")
        and second_best >= LOW_CONFIDENCE_THRESHOLD
        and abs(confidence - second_best) < 0.15
    ):
        route = "both"
    else:
        route = best_route

    return RouteDecision(
        route=route,
        intent_summary=_summary(route, calendar_intent, task_intent),
        confidence=round(confidence, 2),
    ).to_dict()

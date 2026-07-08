from __future__ import annotations

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import importlib
import json
from pathlib import Path
import re
import sys
import threading
from typing import Any, Callable, Iterable, Optional


ROOT = Path(__file__).resolve().parent
DEFAULT_TIMEZONE = "America/Los_Angeles"
METADATA_MARKER = "N4OS_METADATA:"
METADATA_EXTENDED_PROPERTY = "n4os_metadata"

CalendarMetadataReader = Callable[[Any], tuple[str, dict[str, Any]]]
TaskMetadataReader = Callable[[Optional[str]], tuple[str, dict[str, Any]]]
TaskRecommender = Callable[
    [list[dict[str, Any]], dict[str, Any], int],
    list[dict[str, Any]],
]


@dataclass(frozen=True)
class DashboardSources:
    calendar_tools: Any
    task_tools: Any
    read_event_metadata: CalendarMetadataReader
    read_task_metadata: TaskMetadataReader
    recommend_task_matches: TaskRecommender
    home_board_tools: Any | None = None
    decision_tools: Any | None = None
    reading_garden_tools: Any | None = None


_DEFAULT_SOURCES: DashboardSources | None = None
_DEFAULT_SOURCES_LOCK = threading.Lock()


class _UnavailableSourceTools:
    unavailable = True

    def __init__(self, label: str, error: Exception):
        self.label = label
        self.error = error

    def _response(self) -> dict[str, Any]:
        return {
            "status": "error",
            "message": f"{self.label} source unavailable: {self.error.__class__.__name__}.",
            "data": {"error_type": self.error.__class__.__name__},
        }


class _UnavailableCalendarTools(_UnavailableSourceTools):
    def list_calendar_events(self, **_kwargs: Any) -> dict[str, Any]:
        return self._response()


class _UnavailableTaskTools(_UnavailableSourceTools):
    def list_tasks(self, **_kwargs: Any) -> dict[str, Any]:
        return self._response()

    def complete_task(self, **_kwargs: Any) -> dict[str, Any]:
        return self._response()


class _UnavailableHomeBoardTools(_UnavailableSourceTools):
    def list_items(self, **_kwargs: Any) -> dict[str, Any]:
        return self._response()


class _UnavailableDecisionTools(_UnavailableSourceTools):
    def list_decisions(self, **_kwargs: Any) -> dict[str, Any]:
        return self._response()


class _UnavailableLibraryTools(_UnavailableSourceTools):
    def status(self, **_kwargs: Any) -> dict[str, Any]:
        return self._response()


@contextmanager
def _isolated_claw_import(claw_dir: Path) -> Iterable[None]:
    module_names = ("tools", "provider", "intent", "matcher", "constants", "prompts", "claw")
    saved_modules = {name: sys.modules.get(name) for name in module_names}
    old_path = list(sys.path)
    for name in module_names:
        sys.modules.pop(name, None)

    sys.path.insert(0, str(claw_dir))
    try:
        yield
    finally:
        for name in module_names:
            sys.modules.pop(name, None)
        for name, module in saved_modules.items():
            if module is not None:
                sys.modules[name] = module
        sys.path = old_path


def build_default_sources() -> DashboardSources:
    calendar_dir = ROOT / "claws" / "family-calendar"
    tasks_dir = ROOT / "claws" / "family-tasks"

    try:
        with _isolated_claw_import(calendar_dir):
            calendar_tools_module = importlib.import_module("tools")
            calendar_provider_module = importlib.import_module("provider")
            calendar_intent_module = importlib.import_module("intent")
            calendar_tools = calendar_tools_module.CalendarTools(
                calendar_provider_module.GoogleCalendarProvider(),
            )
            read_event_metadata = calendar_intent_module.read_metadata_from_event
    except Exception as error:
        calendar_tools = _UnavailableCalendarTools("Calendar", error)
        read_event_metadata = fallback_event_metadata

    try:
        with _isolated_claw_import(tasks_dir):
            task_tools_module = importlib.import_module("tools")
            task_provider_module = importlib.import_module("provider")
            task_intent_module = importlib.import_module("intent")
            task_matcher_module = importlib.import_module("matcher")
            task_tools = task_tools_module.FamilyTaskTools(
                task_provider_module.GoogleTasksProvider(),
            )
            read_task_metadata = task_intent_module.read_metadata_from_notes
            recommend_task_matches = task_matcher_module.recommend_task_matches
    except Exception as error:
        task_tools = _UnavailableTaskTools("Tasks", error)
        read_task_metadata = fallback_task_metadata
        recommend_task_matches = fallback_recommend_task_matches

    try:
        home_board_tools = build_default_home_board_tools()
    except Exception as error:
        home_board_tools = _UnavailableHomeBoardTools("Home Board", error)

    try:
        decision_tools = build_default_decision_tools()
    except Exception as error:
        decision_tools = _UnavailableDecisionTools("Decisions", error)

    try:
        reading_garden_tools = build_default_library_tools()
    except Exception as error:
        reading_garden_tools = _UnavailableLibraryTools("Reading Garden", error)

    return DashboardSources(
        calendar_tools=calendar_tools,
        task_tools=task_tools,
        read_event_metadata=read_event_metadata,
        read_task_metadata=read_task_metadata,
        recommend_task_matches=recommend_task_matches,
        home_board_tools=home_board_tools,
        decision_tools=decision_tools,
        reading_garden_tools=reading_garden_tools,
    )


def build_default_home_board_tools() -> Any:
    home_board_dir = ROOT / "claws" / "home-board"
    with _isolated_claw_import(home_board_dir):
        home_board_tools_module = importlib.import_module("tools")
        home_board_provider_module = importlib.import_module("provider")
        return home_board_tools_module.HomeBoardTools(
            home_board_provider_module.SQLiteHomeBoardProvider(),
        )


def build_default_decision_tools() -> Any:
    decisions_dir = ROOT / "claws" / "family-decisions"
    with _isolated_claw_import(decisions_dir):
        decision_tools_module = importlib.import_module("tools")
        decision_provider_module = importlib.import_module("provider")
        return decision_tools_module.FamilyDecisionTools(
            decision_provider_module.SQLiteFamilyDecisionProvider(),
        )


def build_default_library_tools() -> Any:
    library_dir = ROOT / "claws" / "library"
    with _isolated_claw_import(library_dir):
        library_tools_module = importlib.import_module("tools")
        library_provider_module = importlib.import_module("provider")
        return library_tools_module.LibraryTools(
            library_provider_module.SQLiteLibraryProvider(),
        )


def default_sources() -> DashboardSources:
    global _DEFAULT_SOURCES
    with _DEFAULT_SOURCES_LOCK:
        if _DEFAULT_SOURCES is None:
            sources = build_default_sources()
            if not _has_unavailable_source(sources):
                _DEFAULT_SOURCES = sources
            return sources
        return _DEFAULT_SOURCES


def _has_unavailable_source(sources: DashboardSources) -> bool:
    return any(
        getattr(tools, "unavailable", False)
        for tools in (
            sources.calendar_tools,
            sources.task_tools,
            sources.home_board_tools,
            sources.decision_tools,
            sources.reading_garden_tools,
        )
    )


def _clean_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    cleaned = " ".join(str(value).split()).strip()
    return cleaned or fallback


def fallback_event_metadata(event_or_description: Any) -> tuple[str, dict[str, Any]]:
    defaults = {
        "owner": "unknown",
        "person": "family",
        "category": "",
        "preparation_needed": False,
        "preparation_notes": "",
    }
    description = event_or_description
    if isinstance(event_or_description, dict):
        description = event_or_description.get("description")
        extended_properties = event_or_description.get("extendedProperties")
        if isinstance(extended_properties, dict):
            private_properties = extended_properties.get("private")
            if isinstance(private_properties, dict):
                raw_metadata = private_properties.get(METADATA_EXTENDED_PROPERTY)
                if isinstance(raw_metadata, str):
                    try:
                        parsed = json.loads(raw_metadata)
                    except json.JSONDecodeError:
                        parsed = {}
                    if isinstance(parsed, dict):
                        notes, _ = fallback_event_metadata(description)
                        defaults.update(parsed)
                        return notes, defaults

    if not description:
        return "", defaults
    marker_index = description.find(METADATA_MARKER)
    if marker_index < 0:
        return description.strip(), defaults
    notes = description[:marker_index].strip()
    raw_metadata = description[marker_index + len(METADATA_MARKER) :].strip()
    try:
        parsed = json.loads(raw_metadata)
    except json.JSONDecodeError:
        parsed = {}
    if isinstance(parsed, dict):
        defaults.update(parsed)
    return notes, defaults


def fallback_task_metadata(notes: str | None) -> tuple[str, dict[str, Any]]:
    defaults = {
        "context": [],
        "energy": "unknown",
        "duration_minutes": None,
        "urgency": "unknown",
        "complexity": "unknown",
        "effort_type": "unknown",
        "requires": [],
        "can_do_while": [],
        "location": "unknown",
        "owner": "unknown",
    }
    if not notes:
        return "", defaults
    marker_index = notes.find(METADATA_MARKER)
    if marker_index < 0:
        return notes.strip(), defaults
    human_notes = notes[:marker_index].strip()
    raw_metadata = notes[marker_index + len(METADATA_MARKER) :].strip()
    try:
        parsed = json.loads(raw_metadata)
    except json.JSONDecodeError:
        parsed = {}
    if isinstance(parsed, dict):
        defaults.update(parsed)
    return human_notes, defaults


def fallback_recommend_task_matches(
    tasks: list[dict[str, Any]],
    filters: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    available_minutes = filters.get("duration_minutes")
    context = {str(value) for value in filters.get("context") or []}
    ranked: list[tuple[int, dict[str, Any], list[str]]] = []
    for task in tasks:
        if task.get("status") == "completed":
            continue
        _, metadata = fallback_task_metadata(task.get("notes"))
        duration = metadata.get("duration_minutes")
        if available_minutes is not None and duration is not None and int(duration) > int(available_minutes):
            continue
        reasons = []
        score = 0
        task_context = set(str(value) for value in metadata.get("context") or [])
        task_context.update(str(value) for value in metadata.get("can_do_while") or [])
        matches = context & task_context
        if matches:
            score += len(matches) * 3
            reasons.append("matches " + ", ".join(sorted(matches)))
        if duration is not None and available_minutes is not None:
            score += 2
            reasons.append(f"fits in {available_minutes} minutes")
        if str(metadata.get("urgency")) == "high":
            score += 3
            reasons.append("high urgency")
        ranked.append((score, task, reasons or ["matches the current window"]))

    ranked.sort(key=lambda item: (-item[0], _clean_text(item[1].get("title")).lower()))
    return [
        {"task": task, "score": score, "reasons": reasons}
        for score, task, reasons in ranked[:limit]
    ]

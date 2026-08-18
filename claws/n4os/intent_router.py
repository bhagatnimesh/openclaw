from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
import importlib.util
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Any, Callable, Iterator, Literal, Protocol, Union, cast

try:
    from .input_normalizer import improve_entered_text
    from .prompts import CLARIFICATION_PROMPT
    from .routing_contracts import (
        DecisionSource,
        ROUTE_REGISTRY,
        RouteCandidate,
        TurnDecision,
        is_valid_model_route_action,
        is_valid_route_action,
        parse_explicit_route,
    )
except ImportError:
    from input_normalizer import improve_entered_text
    from prompts import CLARIFICATION_PROMPT
    from routing_contracts import (
        DecisionSource,
        ROUTE_REGISTRY,
        RouteCandidate,
        TurnDecision,
        is_valid_model_route_action,
        is_valid_route_action,
        parse_explicit_route,
    )


Route = Literal[
    "capture",
    "calendar",
    "tasks",
    "shopping",
    "home_board",
    "decisions",
    "science_lab",
    "library",
    "both",
    "unknown",
]
FollowupKind = Literal[
    "none",
    "clarification",
    "pending_response",
    "modify_previous",
    "status_previous",
    "complete_previous",
    "add_note",
    "select_target",
]

LOW_CONFIDENCE_THRESHOLD = 0.6
DETERMINISTIC_CONFIDENCE_THRESHOLD = 0.85
DETERMINISTIC_MARGIN_THRESHOLD = 0.15
LLM_CONFIDENCE_THRESHOLD = 0.8
VALID_ROUTES = {
    route
    for route, spec in ROUTE_REGISTRY.items()
    if spec.model_routable
}
VALID_FOLLOWUP_KINDS = {
    "none",
    "clarification",
    "pending_response",
    "modify_previous",
    "status_previous",
    "complete_previous",
    "add_note",
    "select_target",
}

CALENDAR_INTENTS = set(ROUTE_REGISTRY["calendar"].actions)
TASK_INTENTS = set(ROUTE_REGISTRY["tasks"].actions)
SHOPPING_INTENTS = set(ROUTE_REGISTRY["shopping"].actions)
HOME_BOARD_INTENTS = set(ROUTE_REGISTRY["home_board"].actions)
DECISION_INTENTS = set(ROUTE_REGISTRY["decisions"].actions)
DECISION_UPDATE_INTENTS = {
    "add_backlog_note",
    "set_backlog_position",
    "move_backlog_item",
    "pin_backlog_item",
    "park_backlog_item",
    "close_backlog_item",
    "decision_brief",
    "add_option",
    "add_evidence",
    "add_next_step",
    "record_decision",
}
LIBRARY_INTENTS = set(ROUTE_REGISTRY["library"].actions)

LOCAL_MODULES = (
    "constants",
    "intent",
    "matcher",
    "noah_assistant",
    "prompts",
    "provider",
    "tools",
)
MISSING = object()

CLAW_ROOT = Path(__file__).resolve().parents[1]
CALENDAR_ROOT = CLAW_ROOT / "family-calendar"
TASKS_ROOT = CLAW_ROOT / "family-tasks"
HOME_BOARD_ROOT = CLAW_ROOT / "home-board"
SHOPPING_ROOT = CLAW_ROOT / "shopping-list"
DECISIONS_ROOT = CLAW_ROOT / "family-decisions"
SCIENCE_LAB_ROOT = CLAW_ROOT / "science-lab"
LIBRARY_ROOT = CLAW_ROOT / "library"
TASK_CUE_RE = re.compile(r"\b(tasks?|todos?|to-dos?|open loops?)\b")
SHOPPING_LIST_CUE_RE = re.compile(
    r"\b(costco|indian(?:\s+grocer(?:y|ies))?|whole\s*foods?|wholefoods|amazon|others?|shopping\s+list|cart)\b",
    re.IGNORECASE,
)
EXPLICIT_SHOPPING_COMMAND_RE = re.compile(
    r"^\s*/(?:cart|shop|shopping)(?:@[A-Za-z0-9_]+)?(?:\s+|:\s*|$)",
    re.IGNORECASE,
)
EXPLICIT_LIBRARY_TARGET_RE = re.compile(
    r"^\s*(?:/(?:library|reading)(?:@[A-Za-z0-9_]+)?|add\s+to\s+(?:the\s+)?library|library\b)",
    re.IGNORECASE,
)
EXPLICIT_CLOCK_TIME_RE = re.compile(
    r"\b(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)\b|"
    r"\bat\s+\d{1,2}(?::\d{2})?\b"
)
ASSISTANT_NAMES = ("Noah",)
ASSISTANT_NAME_PATTERN = "|".join(re.escape(name) for name in ASSISTANT_NAMES)
ASSISTANT_HELP_MARKER_LINE_RE = re.compile(
    rf"^\s*(?:(?:i\s+)?(?:want|need|could\s+use)\s+(?:an?\s+)?ai\s+assistant"
    rf"(?:\s+(?:help|support))?|ask\s+(?:{ASSISTANT_NAME_PATTERN})\s+"
    rf"(?:to\s+help|for\s+help)|(?:{ASSISTANT_NAME_PATTERN})\s*,?\s+help)\.?\s*$",
    re.IGNORECASE,
)
OBJECT_UPDATE_RE = re.compile(
    r"\b(?:assign|make|set|put|change|update)\b.*\b(?:to|for)\s+[\w'. -]+\s*$|"
    r"\b(?:assign|add|append|modify|edit|update|set|make|change|put)\b"
    r".*\b(?:owner|owned|for|note|notes|description|context|"
    r"noah|novah|assistant|help)\b|"
    r"\b(?:owner|owned\s+by|notes?|description|context)\s*"
    r"(?:(?:is|are)\b|:)|"
    r"\b(?:assign(?:ed)?\s+to|belongs\s+to)\b",
    re.IGNORECASE,
)
TAG_UPDATE_TEXT_RE = re.compile(
    r"^\s*(?:(?:add|append|set|update|put)\s+#[A-Za-z][A-Za-z0-9_-]*\b|"
    r"(?:tags?|labels?)\s*:|"
    r"(?:add|append|set|update|put|change)\b.*\b(?:tags?|labels?)\b.*"
    r"#[A-Za-z][A-Za-z0-9_-]*\b)",
    re.IGNORECASE,
)
EXPLICIT_TASK_CREATE_TEXT_RE = re.compile(
    r"^\s*(?:/task\s+|(?:please\s+)?"
    r"(?:(?:(?:i|we)\s+)?(?:want|need|would\s+like)\s+to\s+)?"
    r"(?:add|create|capture|remember)\s+(?:an?\s+)?"
    r"(?:task|todo|to-do|open loop)\b)",
    re.IGNORECASE,
)
EXPLICIT_CALENDAR_CREATE_TEXT_RE = re.compile(
    r"^\s*(?:(?:please\s+)?"
    r"(?:(?:(?:i|we)\s+)?(?:want|need|would\s+like)\s+to\s+)?"
    r"(?:add|create|schedule|put)\s+(?:an?\s+)?"
    r"(?:event|calendar event)\b)",
    re.IGNORECASE,
)
REFINABLE_CONTEXT_ACTIONS = {
    "calendar": {"create_event", "update_event"},
    "tasks": {"create_task", "update_task"},
}


@dataclass(frozen=True)
class RouteDecision:
    route: Route
    action: str
    intent_summary: str
    confidence: float
    source: DecisionSource = "rules"

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "action": self.action,
            "intent_summary": self.intent_summary,
            "confidence": self.confidence,
            "source": self.source,
        }


@dataclass(frozen=True)
class N4OSIntentFrame:
    route: Route
    action: str
    confidence: float
    followup_kind: FollowupKind = "none"
    target: dict[str, Any] = field(default_factory=dict)
    slots: dict[str, Any] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)
    normalized_request: str = ""
    clarification_question: str | None = None
    decision_source: DecisionSource = "rules"
    candidates: tuple[RouteCandidate, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "action": self.action,
            "confidence": self.confidence,
            "followup_kind": self.followup_kind,
            "target": dict(self.target),
            "slots": dict(self.slots),
            "missing_fields": list(self.missing_fields),
            "normalized_request": self.normalized_request,
            "clarification_question": self.clarification_question,
            "decision_source": self.decision_source,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }

    def to_route_decision(self) -> dict[str, Any]:
        return RouteDecision(
            route=self.route,
            action=self.action,
            intent_summary=_frame_summary(self),
            confidence=self.confidence,
            source=self.decision_source,
        ).to_dict()

    def to_turn_decision(self, original_input: str) -> TurnDecision:
        return TurnDecision(
            route=self.route,
            action=self.action,
            confidence=self.confidence,
            source=self.decision_source,
            original_input=original_input,
            normalized_input=self.normalized_request or original_input,
            slots=dict(self.slots),
            missing_fields=tuple(self.missing_fields),
            clarification=self.clarification_question,
            candidates=self.candidates,
        )


class IntentInterpreter(Protocol):
    def interpret(
        self,
        request: str,
        *,
        now: datetime | None = None,
        context: dict[str, Any] | None = None,
    ) -> N4OSIntentFrame | dict[str, Any]:
        ...


InterpreterCallable = Callable[..., Union[N4OSIntentFrame, dict[str, Any]]]


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


def _shopping_intent_module() -> ModuleType:
    return load_scoped_module("_n4os_shopping_list_intent", SHOPPING_ROOT, "intent.py")


def _decisions_intent_module() -> ModuleType:
    return load_scoped_module("_n4os_family_decisions_intent", DECISIONS_ROOT, "intent.py")


def _library_intent_module() -> ModuleType:
    return load_scoped_module("_n4os_library_intent", LIBRARY_ROOT, "intent.py")


def _extract_intents(
    request: str,
    now: datetime | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    calendar_intent = _calendar_intent_module().extract_intent(request, now=now)
    task_intent = _tasks_intent_module().extract_intent(request, now=now)
    shopping_intent = _shopping_intent_module().extract_intent(request, now=now)
    home_board_intent = _home_board_intent_module().extract_intent(request, now=now)
    decision_intent = _decisions_intent_module().extract_intent(request, now=now)
    library_intent = _library_intent_module().extract_intent(request, now=now)
    return calendar_intent, task_intent, shopping_intent, home_board_intent, decision_intent, library_intent


def _has_time_anchor(text: str) -> bool:
    return bool(
        re.search(
            r"\b(today|tomorrow|tonight|weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            text,
        )
        or re.search(r"\b\d{1,2}(?::\d{2})?\s*(am|pm)\b", text)
        or re.search(r"\bat\s+\d{1,2}(?::\d{2})?\b", text)
    )


def _routing_text(request: str) -> str:
    lines = [
        line.strip()
        for line in request.splitlines()
        if line.strip() and ASSISTANT_HELP_MARKER_LINE_RE.match(line.strip()) is None
    ]
    return "\n".join(lines).lower().strip() or request.lower().strip()


def _looks_like_task_capture(text: str) -> bool:
    return bool(
        re.search(
            r"^\s*(?:to\s+)?(?:"
            r"pack|bring|buy|get|return|put|take|call|text|email|message|"
            r"clean|repair|fix|change|replace|research|book|order|renew|"
            r"submit|fill|prepare|send|drop\s+off|pick\s+up|pickup"
            r")\b",
            text,
        )
    )


def _has_explicit_clock_time(text: str) -> bool:
    return EXPLICIT_CLOCK_TIME_RE.search(text) is not None


def _has_task_cue(text: str) -> bool:
    return TASK_CUE_RE.search(text) is not None


def _has_explicit_home_board_target(text: str) -> bool:
    return re.search(r"\b(home board|today at home|house board)\b", text) is not None


def _has_assistant_help_metadata(task_intent: dict[str, Any]) -> bool:
    metadata = task_intent.get("metadata")
    return isinstance(metadata, dict) and bool(metadata.get("assistant_help_needed"))


def _is_object_update_request(text: str) -> bool:
    return OBJECT_UPDATE_RE.search(text) is not None


def _is_tag_update_request(text: str) -> bool:
    return TAG_UPDATE_TEXT_RE.search(text) is not None


def _is_create_task_request(task_intent: dict[str, Any]) -> bool:
    return task_intent.get("intent") == "create_task" and not task_intent.get(
        "missing_fields",
    )


def _is_create_calendar_request(calendar_intent: dict[str, Any]) -> bool:
    return calendar_intent.get("intent") == "create_event" and not calendar_intent.get(
        "missing_fields",
    )


def _is_explicit_task_create_text(text: str) -> bool:
    return EXPLICIT_TASK_CREATE_TEXT_RE.search(text) is not None


def _is_explicit_calendar_create_text(text: str) -> bool:
    return EXPLICIT_CALENDAR_CREATE_TEXT_RE.search(text) is not None


def _can_refine_previous_object(context: dict[str, Any], route: str) -> bool:
    # Only object mutations establish a safe implicit target for short follow-ups.
    # List/recommend/status routes leave "it" ambiguous and must reroute normally.
    action = str(context.get("last_action") or "")
    return action in REFINABLE_CONTEXT_ACTIONS.get(route, set())


def _score_calendar(text: str, calendar_intent: dict[str, Any]) -> float:
    score = 0.0
    intent = calendar_intent.get("intent")
    has_explicit_task_word = _has_task_cue(text)
    looks_like_task_capture = (
        _looks_like_task_capture(text)
        and not _has_explicit_clock_time(text)
    )
    if (
        intent == "create_event"
        and _has_time_anchor(text)
        and not has_explicit_task_word
        and not looks_like_task_capture
    ):
        score += 0.45
    elif intent == "add_guests":
        score += 0.95
    elif (
        intent in CALENDAR_INTENTS
        and intent != "create_event"
        and not has_explicit_task_word
    ):
        score += 0.45

    if re.search(
        r"\b(calendar|schedule|events?|appointment|appt|meeting|school|"
        r"holidays?|break|vacation|no\s+school|upcoming)\b",
        text,
    ):
        score += 0.35
    if re.search(r"\b(dentist|dental|doctor|flight|pickup|dinner|reservation)\b", text):
        score += 0.25
    if _has_time_anchor(text) and not looks_like_task_capture:
        score += 0.2
    if (
        re.search(r"^\s*(add|create|schedule|go to)\b", text)
        and _has_time_anchor(text)
        and not looks_like_task_capture
    ):
        score += 0.2
    if re.search(r"^\s*(what|what's|whats|when|show|list)\b", text) and re.search(
        r"\b(have|calendar|schedule|coming up|upcoming|school|day|break|"
        r"conference|holidays?|vacation|no\s+school)\b",
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
        score += 0.4
    elif intent == "run_assistant_help":
        score += 0.65
    elif intent == "recommend_tasks" and re.search(
        r"\b(what|which|recommend|do|show|list|give)\b",
        text,
    ):
        score += 0.35
        if task_intent.get("filters"):
            score += 0.2
    if intent == "recommend_tasks" and task_intent.get("filters", {}).get("tags"):
        score += 0.4 if score > 0 else 0.9

    if _has_task_cue(text):
        score += 0.45
    if intent == "create_task" and _looks_like_task_capture(text):
        score += 0.45
    if intent == "create_task" and _has_assistant_help_metadata(task_intent):
        score += 0.45
    if re.search(r"^\s*(complete|finish|mark)\b", text):
        score += 0.35
    if re.search(r"\b(done|what can i do|what should i do|recommend|driving|commute|energy)\b", text):
        score += 0.3
    if re.search(r"\b(filter|trash|laundry|clean|repair|call|research)\b", text):
        score += 0.2
    if re.search(r"^\s*(add|create|capture|remember)\b", text) and "task" in text:
        score += 0.2
    if _is_object_update_request(text) and re.search(r"\b(tasks?|todos?|to-dos?)\b", text):
        score += 0.65

    return min(score, 1.0)


def _score_shopping(text: str, shopping_intent: dict[str, Any]) -> float:
    score = 0.0
    intent = shopping_intent.get("intent")
    explicit_command = EXPLICIT_SHOPPING_COMMAND_RE.search(text) is not None
    if explicit_command:
        score += 0.9
    if intent in SHOPPING_INTENTS and not shopping_intent.get("missing_fields"):
        score += 0.45
    elif intent in SHOPPING_INTENTS:
        score += 0.2
    if SHOPPING_LIST_CUE_RE.search(text):
        score += 0.4
    if re.search(r"\b(shopping|cart|grocer(?:y|ies)|buy)\b", text):
        score += 0.25
    if re.search(r"^\s*(?:add|buy|need|get|put|cross\s+off|check\s+off|remove|delete|move|clear|done)\b", text):
        score += 0.2
    if SHOPPING_LIST_CUE_RE.search(text) and re.search(r"\b(done|clear)\b", text):
        score += 0.35
    if _has_task_cue(text) or re.search(r"\bremind\s+me\s+to\b", text):
        score -= 0.5
    if _is_time_bound_store_trip(text):
        score -= 0.7
    return max(0.0, min(score, 1.0))


def _is_ambiguous_shopping_item_request(text: str, shopping_intent: dict[str, Any]) -> bool:
    if "#" in text:
        return False
    if SHOPPING_LIST_CUE_RE.search(text) or _has_task_cue(text) or _has_time_anchor(text):
        return False
    return bool(
        shopping_intent.get("intent") in {"add_item", "add_items"}
        and "list_name" in list(shopping_intent.get("missing_fields") or [])
        and re.search(r"^\s*(?:buy|need|get|add)\b", text)
    )


def _is_multiline_calendar_event_request(text: str) -> bool:
    if not re.search(r"^\s*(?:add|create|schedule)\b", text, flags=re.IGNORECASE):
        return False
    if re.search(r"\b(?:events?|calendar|schedule)\b", text, flags=re.IGNORECASE) is None:
        return False

    date_lines = [
        line
        for line in text.splitlines()
        if re.match(r"^\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?\s*$", line)
    ]
    return len(date_lines) >= 2


def _is_time_bound_store_trip(text: str) -> bool:
    return bool(
        _has_time_anchor(text)
        and SHOPPING_LIST_CUE_RE.search(text)
        and re.search(r"\b(trip|go\s+to|visit|appointment|meeting|pickup|dinner|call)\b", text)
    )


def _score_home_board(text: str, home_board_intent: dict[str, Any]) -> float:
    score = 0.0
    intent = home_board_intent.get("intent")
    if intent in HOME_BOARD_INTENTS:
        score += 0.45
    explicit_home_board_target = _has_explicit_home_board_target(text)
    if explicit_home_board_target:
        score += 0.45
    if re.search(r"\bbefore\b", text) and re.search(r"\b(leaves?|leaving|leave)\b", text):
        score += 0.45
    if re.search(r"\b(helper|nysha|nimesh|dad|mom|family|everyone)\b", text):
        score += 0.25
    if _is_object_update_request(text) and not explicit_home_board_target:
        score -= 0.35
    if re.search(r"\b(journal|form|payment|fridge|food|passport|lunch|library book|permission slip)\b", text):
        score += 0.25
    if _has_task_cue(text) and not re.search(
        r"\b(home board|today at home)\b",
        text,
    ):
        score -= 0.3
    return max(0.0, min(score, 1.0))


def _score_decisions(text: str, decision_intent: dict[str, Any]) -> float:
    score = 0.0
    intent = decision_intent.get("intent")
    if intent in DECISION_UPDATE_INTENTS:
        score += 0.6
    elif intent in DECISION_INTENTS:
        score += 0.25
    if re.search(r"\b(decisions?|decide|decided|decision brief)\b", text):
        score += 0.5
    if re.search(r"^\s*(?:discussion|planning|plan|decision)\s*[:\-]|\bfamily backlog\b|^\s*/?backlog\b", text):
        score += 0.7
    if re.search(r"\b(options?\s+(?:are|include|includes)|choices?\s+(?:are|include|includes)|add(?:ed)?\s+note|next step|challenges?|concerns?|risks?)\b", text):
        score += 0.25
    if re.search(r"\b(choose|whether|should we|are we going to|did we decide)\b", text):
        score += 0.35
    if re.search(r"\b(summer camp|school choice|birthday party|camp plan)\b", text):
        score += 0.25
    if re.search(r"\b(?:my|our|his|her|their)\s+position\b", text):
        score += 0.3
    if re.search(r"\b(tasks?|todos?|calendar|event|appointment|home board|today at home)\b", text):
        score -= 0.25
    return max(0.0, min(score, 1.0))


def _score_science_lab(text: str) -> float:
    score = 0.0
    if re.search(r"\b(science\s+lab|science\s+experiments?|experiment\s+guide)\b", text):
        score += 0.7
    if re.search(
        r"\b(experiments?|materials?|inventory|amazon|guide|plan|script|quiz|reflection)\b",
        text,
    ):
        score += 0.25
    if re.search(r"\b(kids?|children|parent|parents?|six|seven|four)\b", text):
        score += 0.15
    if re.search(r"\b(calendar|tasks?|todos?|home board|today at home|decisions?)\b", text):
        score -= 0.2
    return max(0.0, min(score, 1.0))


def _score_library(text: str, library_intent: dict[str, Any]) -> float:
    score = 0.0
    intent = library_intent.get("intent")
    explicit_task_create = _is_explicit_task_create_text(text) or (
        _has_task_cue(text) and re.search(r"\b(?:add|create|capture|remember)\b", text)
    )
    if intent in LIBRARY_INTENTS:
        score += 0.5
    if intent in {"update_reading", "delete_reading"}:
        score += 0.35
    if re.search(r"\b(reading garden|read it myself|library claw|reading status)\b", text):
        score += 0.45
    if re.search(r"\b(?:change|update|edit|fix|correct|delete|remove|undo)\b.*\b(?:reading|book|moment|entry|log)\b", text):
        score += 0.45
    if re.search(r"\b(library bag|library checkout|checked out|checkout receipt|library receipt)\b", text):
        score += 0.5
    elif re.search(r"\b(?:due date|due by)\b", text) and re.search(
        r"\b(?:library|books?|titles?|borrowed|checkout|receipt)\b",
        text,
    ):
        score += 0.5
    if intent == "record_checkout":
        score += 0.25
    if re.search(r"\b(?:nysha|navya|kids|girls|children)\b", text) and re.search(r"\b(read|finished|pages?|minutes?|book)\b", text):
        score += 0.45
    if re.search(r"\b(by herself|herself|independently|i read it myself)\b", text):
        score += 0.25
    if re.search(r"\b(dad|mom|adult)\s+read\b.*\bto\s+nysha\b", text):
        score += 0.35
    if re.search(
        r"\b(tasks?|todos?|calendar|event|appointment|home board|today at home|decisions?|science lab)\b",
        text,
    ):
        score -= 0.25
    if explicit_task_create and intent not in {"update_reading", "delete_reading"}:
        score -= 0.45
    return max(0.0, min(score, 1.0))


def _is_explicit_decision_request(text: str, decision_intent: dict[str, Any]) -> bool:
    intent = decision_intent.get("intent")
    if intent not in DECISION_INTENTS:
        return False
    if re.search(
        r"\b(captured decision|track decision|capture decision|family decision|decision brief|decisions?|family backlog)\b|"
        r"^\s*(?:discussion|planning|plan|decision)\s*[:\-]|"
        r"^\s*(?:add|create|capture|track|open)\s+(?:an?\s+)?(?:discussion|planning|plan)\b|"
        r"^\s*/?backlog\b",
        text,
    ):
        return True
    return bool(
        intent in DECISION_UPDATE_INTENTS
        and re.search(
            r"^\s*(?:give|provide|send|show)\s+(?:me\s+)?(?:the\s+)?(?:latest\s+|open\s+)?(?:decision\s+)?brief\b|"
            r"^\s*(?:add(?:ed)?\s+)?(?:options?|evidence|research|notes?|next\s+steps?)\b|"
            r"^\s*(?:challenges?|concerns?|risks?)\b",
            text,
        )
    )


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


def _summary(
    route: Route,
    calendar_intent: dict[str, Any],
    task_intent: dict[str, Any],
    shopping_intent: dict[str, Any] | None = None,
    decision_intent: dict[str, Any] | None = None,
    library_intent: dict[str, Any] | None = None,
) -> str:
    if route == "calendar":
        return f"Route to family-calendar for {calendar_intent.get('intent', 'calendar request')}."
    if route == "tasks":
        return f"Route to family-tasks for {task_intent.get('intent', 'task request')}."
    if route == "shopping":
        intent = (shopping_intent or {}).get("intent", "shopping request")
        return f"Route to shopping-list for {intent}."
    if route == "home_board":
        return "Route to home-board for household notice."
    if route == "decisions":
        intent = (decision_intent or {}).get("intent", "decision request")
        return f"Route to family-decisions for {intent}."
    if route == "science_lab":
        return "Route to science-lab for experiment planning."
    if route == "library":
        intent = (library_intent or {}).get("intent", "reading request")
        return f"Route to library for {intent}."
    if route == "both":
        return (
            "Route to family-calendar and family-tasks for combined planning or briefing."
        )
    return "Could not confidently choose Capture, Calendar, Tasks, Shopping, Home Board, Decisions, Science Lab, Library, or Calendar + Tasks."


def _action_for_route(
    route: Route,
    calendar_intent: dict[str, Any],
    task_intent: dict[str, Any],
    shopping_intent: dict[str, Any],
    home_board_intent: dict[str, Any],
    decision_intent: dict[str, Any],
    library_intent: dict[str, Any],
) -> str:
    if route == "calendar":
        return str(calendar_intent.get("intent") or "calendar_request")
    if route == "tasks":
        return str(task_intent.get("intent") or "task_request")
    if route == "shopping":
        return str(shopping_intent.get("intent") or "shopping_request")
    if route == "home_board":
        return str(home_board_intent.get("intent") or "home_board_request")
    if route == "decisions":
        return str(decision_intent.get("intent") or "decision_request")
    if route == "science_lab":
        return "plan_experiments"
    if route == "library":
        return str(library_intent.get("intent") or "library_request")
    if route == "both":
        return "combined_planning"
    return "unknown"


def _frame_summary(frame: N4OSIntentFrame) -> str:
    if frame.route == "calendar":
        return f"Route to family-calendar for {frame.action}."
    if frame.route == "tasks":
        return f"Route to family-tasks for {frame.action}."
    if frame.route == "shopping":
        return f"Route to shopping-list for {frame.action}."
    if frame.route == "home_board":
        return f"Route to home-board for {frame.action}."
    if frame.route == "decisions":
        return f"Route to family-decisions for {frame.action}."
    if frame.route == "science_lab":
        return f"Route to science-lab for {frame.action}."
    if frame.route == "library":
        return f"Route to library for {frame.action}."
    if frame.route == "both":
        return "Route to family-calendar and family-tasks for combined planning or briefing."
    return frame.clarification_question or "Could not confidently choose Capture, Calendar, Tasks, Shopping, Home Board, Decisions, Science Lab, Library, or Calendar + Tasks."


def _round_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(confidence, 1.0)), 2)


def _clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned = []
    for item in value:
        text = str(item).strip()
        if text:
            cleaned.append(text)
    return cleaned


def _coerce_record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _coerce_intent_frame(
    raw: N4OSIntentFrame | dict[str, Any],
    request: str,
) -> N4OSIntentFrame:
    if isinstance(raw, N4OSIntentFrame):
        if not is_valid_model_route_action(raw.route, raw.action):
            raise ValueError(
                f"intent interpreter returned invalid action for {raw.route}: {raw.action}"
            )
        return N4OSIntentFrame(
            **{
                **raw.to_dict(),
                "normalized_request": request,
                "decision_source": "llm",
                "candidates": raw.candidates,
            }
        )
    if not isinstance(raw, dict):
        raise ValueError("intent interpreter returned a non-object frame")

    route = str(raw.get("route") or "unknown")
    if route not in VALID_ROUTES:
        raise ValueError(f"intent interpreter returned invalid route: {route}")

    followup_kind = str(raw.get("followup_kind") or "none")
    if followup_kind not in VALID_FOLLOWUP_KINDS:
        raise ValueError(
            f"intent interpreter returned invalid followup_kind: {followup_kind}"
        )

    action = str(raw.get("action") or "unknown").strip() or "unknown"
    if not is_valid_model_route_action(route, action):
        raise ValueError(f"intent interpreter returned invalid action for {route}: {action}")
    clarification = raw.get("clarification_question")
    return N4OSIntentFrame(
        route=cast(Route, route),
        action=action,
        confidence=_round_confidence(raw.get("confidence")),
        followup_kind=cast(FollowupKind, followup_kind),
        target=_coerce_record(raw.get("target")),
        slots=_coerce_record(raw.get("slots")),
        missing_fields=_clean_string_list(raw.get("missing_fields")),
        normalized_request=str(raw.get("normalized_request") or request),
        clarification_question=str(clarification).strip() if clarification else None,
        decision_source="llm",
    )


def _call_interpreter(
    interpreter: IntentInterpreter | InterpreterCallable,
    request: str,
    now: datetime | None,
    context: dict[str, Any] | None,
) -> N4OSIntentFrame | dict[str, Any]:
    interpret = getattr(interpreter, "interpret", None)
    if callable(interpret):
        return interpret(request, now=now, context=context)
    return interpreter(request, now=now, context=context)


def _candidate_for_route(
    route: Route,
    score: float,
    *,
    calendar_intent: dict[str, Any],
    task_intent: dict[str, Any],
    shopping_intent: dict[str, Any],
    home_board_intent: dict[str, Any],
    decision_intent: dict[str, Any],
    library_intent: dict[str, Any],
    evidence: tuple[str, ...] = (),
) -> RouteCandidate:
    intent_by_route = {
        "calendar": calendar_intent,
        "tasks": task_intent,
        "shopping": shopping_intent,
        "home_board": home_board_intent,
        "decisions": decision_intent,
        "library": library_intent,
    }
    intent = intent_by_route.get(route, {})
    slots = {
        key: value
        for key, value in intent.items()
        if key not in {"intent", "missing_fields"}
    }
    missing_fields = tuple(str(value) for value in intent.get("missing_fields", []) if value)
    return RouteCandidate(
        route=route,
        action=_action_for_route(
            route,
            calendar_intent,
            task_intent,
            shopping_intent,
            home_board_intent,
            decision_intent,
            library_intent,
        ),
        confidence=round(max(0.0, min(score, 1.0)), 2),
        evidence=evidence or ("domain recognizer",),
        slots=slots,
        missing_fields=missing_fields,
    )


def recognize_route_candidates(
    request: str,
    now: datetime | None = None,
    context: dict[str, Any] | None = None,
) -> tuple[RouteCandidate, ...]:
    (
        calendar_intent,
        task_intent,
        shopping_intent,
        home_board_intent,
        decision_intent,
        library_intent,
    ) = _extract_intents(request, now)
    text = _routing_text(request)
    if not text:
        return ()

    context_frame = _contextual_followup_frame(
        request,
        context,
        calendar_intent=calendar_intent,
        task_intent=task_intent,
    )
    candidates: list[RouteCandidate] = []
    if context_frame is not None:
        candidates.append(
            RouteCandidate(
                route=context_frame.route,
                action=context_frame.action,
                confidence=context_frame.confidence,
                evidence=(f"compatible {context_frame.followup_kind} follow-up",),
            )
        )

    scores: dict[Route, float] = {
        "calendar": _score_calendar(text, calendar_intent),
        "tasks": _score_tasks(text, task_intent),
        "shopping": _score_shopping(text, shopping_intent),
        "home_board": _score_home_board(text, home_board_intent),
        "decisions": _score_decisions(text, decision_intent),
        "science_lab": _score_science_lab(text),
        "library": _score_library(text, library_intent),
    }
    claimed_route: Route | None = None
    if _is_multiline_calendar_event_request(text):
        scores["calendar"] = max(scores["calendar"], 0.9)
        claimed_route = "calendar"
    if _is_explicit_calendar_create_text(text):
        scores["calendar"] = max(scores["calendar"], 0.95)
        claimed_route = "calendar"
    if _is_combined_planning(text):
        candidates.append(
            RouteCandidate(
                route="both",
                action="combined_planning",
                confidence=0.88,
                evidence=("combined planning request",),
            )
        )
    if _is_explicit_decision_request(text, decision_intent):
        scores["decisions"] = max(scores["decisions"], 0.95)
        claimed_route = "decisions"
    if _has_explicit_home_board_target(text):
        scores["home_board"] = max(scores["home_board"], 0.95)
        claimed_route = "home_board"
    if (
        EXPLICIT_LIBRARY_TARGET_RE.search(text) is not None
        and library_intent.get("intent") in LIBRARY_INTENTS
    ):
        scores["library"] = max(scores["library"], 0.95)
        claimed_route = "library"
    if task_intent.get("intent") == "create_task" and _has_assistant_help_metadata(task_intent):
        scores["tasks"] = max(scores["tasks"], 0.95)
        claimed_route = claimed_route or "tasks"
    if task_intent.get("intent") == "run_assistant_help":
        scores["tasks"] = max(scores["tasks"], 0.95)
        claimed_route = claimed_route or "tasks"
    ambiguous_shopping_item = _is_ambiguous_shopping_item_request(text, shopping_intent)
    if ambiguous_shopping_item:
        scores["tasks"] = min(scores["tasks"], 0.4)
    store_qualified_shopping = bool(
        shopping_intent.get("list_slug")
        and shopping_intent.get("intent") in {"add_item", "add_items"}
        and re.search(r"^\s*(?:add|buy|get|need|put)\b", text)
        and not _has_task_cue(text)
        and not _is_time_bound_store_trip(text)
    )
    if store_qualified_shopping:
        scores["shopping"] = max(scores["shopping"], 0.95)
        claimed_route = claimed_route or "shopping"
    if (
        task_intent.get("intent") == "create_task"
        and _looks_like_task_capture(text)
        and not ambiguous_shopping_item
    ):
        scores["tasks"] = max(scores["tasks"], 0.9)
    if task_intent.get("intent") == "recommend_tasks" and task_intent.get("filters", {}).get("tags"):
        scores["tasks"] = max(scores["tasks"], 0.9)
        claimed_route = claimed_route or "tasks"
    if re.search(r"^\s*call\b", text) and _has_explicit_clock_time(text):
        scores["calendar"] = max(scores["calendar"], 0.9)
    if library_intent.get("intent") in {"update_reading", "delete_reading"}:
        scores["library"] = max(scores["library"], 0.95)
        claimed_route = "library"

    # Strong owner-specific syntax suppresses generic sibling recognizers. The
    # owner still returns missing fields and performs final preparation.
    if claimed_route is not None:
        scores = {
            route: score if route == claimed_route else 0.0
            for route, score in scores.items()
        }
        candidates = [candidate for candidate in candidates if candidate.route == claimed_route]

    for route, score in scores.items():
        if score <= 0:
            continue
        candidates.append(
            _candidate_for_route(
                route,
                score,
                calendar_intent=calendar_intent,
                task_intent=task_intent,
                shopping_intent=shopping_intent,
                home_board_intent=home_board_intent,
                decision_intent=decision_intent,
                library_intent=library_intent,
            )
        )

    best_by_route: dict[Route, RouteCandidate] = {}
    for candidate in candidates:
        current = best_by_route.get(candidate.route)
        if current is None or candidate.confidence > current.confidence:
            best_by_route[candidate.route] = candidate
    return tuple(
        sorted(
            best_by_route.values(),
            key=lambda candidate: (-candidate.confidence, candidate.route),
        )
    )


def _explicit_intent_frame(
    request: str,
    now: datetime | None,
) -> N4OSIntentFrame | None:
    explicit = parse_explicit_route(request)
    if explicit is None:
        return None

    body = explicit.body
    if explicit.route == "capture":
        if not body:
            return N4OSIntentFrame(
                route="unknown",
                action="unknown",
                confidence=0.0,
                followup_kind="clarification",
                missing_fields=["note"],
                normalized_request=request,
                clarification_question="What note should I capture?",
                decision_source="clarification",
            )
        intent = {
            "intent": "capture_note",
            "body": body,
            "missing_fields": [],
        }
        domain_request = body
    elif explicit.route == "calendar":
        body_calendar_intent = _calendar_intent_module().extract_intent(body, now=now)
        direct_calendar_action = re.match(
            r"^\s*(?:delete|remove|cancel|move|reschedule|update|change|list|show|brief|prepare)\b",
            body,
            flags=re.IGNORECASE,
        )
        calendar_action_by_verb = {
            "delete": "delete_event",
            "remove": "delete_event",
            "cancel": "delete_event",
            "move": "update_event",
            "reschedule": "update_event",
            "update": "update_event",
            "change": "update_event",
            "list": "list_events",
            "show": "list_events",
            "brief": "family_briefing",
            "prepare": "preparation_checklist",
        }
        direct_verb = direct_calendar_action.group(0).strip().lower() if direct_calendar_action else None
        is_calendar_read = direct_verb in {"list", "show", "brief", "prepare"}
        if direct_verb == "brief":
            detail = body[len(direct_calendar_action.group(0)) :].strip()
            domain_request = f"calendar briefing {detail or 'this week'}"
        elif direct_verb == "prepare":
            detail = body[len(direct_calendar_action.group(0)) :].strip()
            domain_request = f"calendar preparation checklist {detail or 'this week'}"
        elif body_calendar_intent.get("intent") == "add_guests":
            domain_request = body
        elif direct_calendar_action is None or is_calendar_read:
            domain_request = improve_entered_text(request)
        else:
            domain_request = body
        intent = (
            body_calendar_intent
            if domain_request == body and body_calendar_intent.get("intent") == "add_guests"
            else _calendar_intent_module().extract_intent(domain_request, now=now)
        )
        if direct_verb is not None:
            intent = {**intent, "intent": calendar_action_by_verb[direct_verb]}
    elif explicit.route == "tasks":
        direct_task_action = re.match(
            r"^\s*(?:complete|finish|done|delete|remove|update|change|assign|run)\b",
            body,
            flags=re.IGNORECASE,
        )
        domain_request = body if direct_task_action else improve_entered_text(request)
        intent = _tasks_intent_module().extract_intent(domain_request, now=now)
        if re.match(r"^\s*(?:update|change|assign)\b", body, flags=re.IGNORECASE):
            intent = {**intent, "intent": "update_task"}
    elif explicit.route == "shopping":
        domain_request = request
        intent = _shopping_intent_module().extract_intent(domain_request, now=now)
    elif explicit.route == "home_board":
        domain_request = f"home board {body}".strip()
        intent = _home_board_intent_module().extract_intent(domain_request, now=now)
    elif explicit.route == "decisions":
        domain_request = improve_entered_text(request)
        intent = _decisions_intent_module().extract_intent(domain_request, now=now)
    elif explicit.route == "library":
        domain_request = request
        library_parse_request = body
        if re.match(r"^\s*status\b", body, flags=re.IGNORECASE):
            library_parse_request = re.sub(
                r"^\s*status\b",
                "show reading status",
                body,
                flags=re.IGNORECASE,
            )
        intent = _library_intent_module().extract_intent(library_parse_request, now=now)
    elif explicit.route == "science_lab":
        domain_request = body or request
        intent = {"intent": "plan_experiments", "missing_fields": []}
    elif explicit.route == "both":
        domain_request = body or request
        intent = {"intent": "combined_planning", "missing_fields": []}
    else:
        return None

    action = str(intent.get("intent") or "unknown")
    if not is_valid_route_action(explicit.route, action):
        return N4OSIntentFrame(
            route="unknown",
            action="unknown",
            confidence=0.0,
            followup_kind="clarification",
            missing_fields=["request"],
            normalized_request=request,
            clarification_question=(
                f"What would you like me to do in {explicit.route.replace('_', ' ')}?"
            ),
            decision_source="clarification",
        )
    missing_fields = [str(value) for value in intent.get("missing_fields", []) if value]
    candidate = RouteCandidate(
        route=explicit.route,
        action=action,
        confidence=1.0,
        evidence=(f"explicit /{explicit.command} command",),
        slots={
            key: value
            for key, value in intent.items()
            if key not in {"intent", "missing_fields"}
        },
        missing_fields=tuple(missing_fields),
    )
    return N4OSIntentFrame(
        route=explicit.route,
        action=action,
        confidence=1.0,
        slots=dict(candidate.slots),
        missing_fields=missing_fields,
        normalized_request=domain_request,
        decision_source="explicit",
        candidates=(candidate,),
    )


def _targeted_clarification(candidates: tuple[RouteCandidate, ...]) -> str:
    plausible = [candidate for candidate in candidates if candidate.confidence >= 0.35]
    if len(plausible) >= 2:
        first, second = plausible[:2]
        return (
            f"Should I treat that as {first.route.replace('_', ' ')} "
            f"({first.action.replace('_', ' ')}) or {second.route.replace('_', ' ')} "
            f"({second.action.replace('_', ' ')})?"
        )
    if plausible:
        candidate = plausible[0]
        if candidate.missing_fields:
            fields = " and ".join(field.replace("_", " ") for field in candidate.missing_fields)
            return f"What {fields} should I use for that {candidate.route.replace('_', ' ')} request?"
        return f"Should I handle that as {candidate.route.replace('_', ' ')}?"
    return CLARIFICATION_PROMPT


def _contextual_followup_frame(
    request: str,
    context: dict[str, Any] | None,
    *,
    calendar_intent: dict[str, Any] | None = None,
    task_intent: dict[str, Any] | None = None,
) -> N4OSIntentFrame | None:
    if not context:
        return None

    text = request.lower().strip(" .!?")
    last_route = str(context.get("last_route") or "")
    if (
        _is_tag_update_request(text)
        and last_route == "tasks"
        and _can_refine_previous_object(context, "tasks")
    ):
        return N4OSIntentFrame(
            route="tasks",
            action="update_task",
            confidence=0.92,
            followup_kind="modify_previous",
            normalized_request=request,
        )

    if (
        _is_object_update_request(text)
        and last_route in {"calendar", "tasks"}
        and _can_refine_previous_object(context, last_route)
    ):
        if (
            last_route == "tasks"
            and task_intent is not None
            and _is_create_task_request(task_intent)
            and _is_explicit_task_create_text(text)
        ):
            return None
        if (
            last_route == "calendar"
            and calendar_intent is not None
            and _is_create_calendar_request(calendar_intent)
        ):
            return None
        return N4OSIntentFrame(
            route=cast(Route, last_route),
            action="update_event" if last_route == "calendar" else "update_task",
            confidence=0.92,
            followup_kind="modify_previous",
            normalized_request=request,
        )

    if last_route == "home_board" and text in {
        "done",
        "complete",
        "completed",
        "mark done",
        "finished",
    }:
        return N4OSIntentFrame(
            route="home_board",
            action="mark_done",
            confidence=0.9,
            followup_kind="complete_previous",
            normalized_request=request,
        )

    if last_route == "decisions" and text in {
        "status",
        "update",
        "updates",
        "what is the status",
        "what's the status",
    }:
        return N4OSIntentFrame(
            route="decisions",
            action="decision_brief",
            confidence=0.9,
            followup_kind="status_previous",
            normalized_request="decision brief",
        )

    if last_route == "calendar" and re.search(r"^(?:add\s+)?(?:note|context|fyi)\b", text):
        return N4OSIntentFrame(
            route="calendar",
            action="create_event",
            confidence=0.86,
            followup_kind="add_note",
            normalized_request=request,
        )

    return None


def _rule_intent_frame(
    request: str,
    now: datetime | None = None,
    context: dict[str, Any] | None = None,
) -> N4OSIntentFrame:
    (
        calendar_intent,
        task_intent,
        shopping_intent,
        home_board_intent,
        decision_intent,
        library_intent,
    ) = _extract_intents(request, now)
    text = _routing_text(request)
    if not text:
        return N4OSIntentFrame(
            route="unknown",
            action="unknown",
            confidence=0.0,
            missing_fields=["request"],
            normalized_request=request,
            clarification_question="Empty request.",
        )

    context_frame = _contextual_followup_frame(
        request,
        context,
        calendar_intent=calendar_intent,
        task_intent=task_intent,
    )
    if context_frame is not None:
        return context_frame

    library_score = _score_library(text, library_intent)
    if (
        EXPLICIT_LIBRARY_TARGET_RE.search(text) is not None
        and library_score >= LOW_CONFIDENCE_THRESHOLD
        and library_intent.get("intent") in LIBRARY_INTENTS
    ):
        return N4OSIntentFrame(
            route="library",
            action=_action_for_route(
                "library",
                calendar_intent,
                task_intent,
                shopping_intent,
                home_board_intent,
                decision_intent,
                library_intent,
            ),
            confidence=round(library_score, 2),
            normalized_request=request,
        )

    shopping_score = _score_shopping(text, shopping_intent)
    if (
        EXPLICIT_SHOPPING_COMMAND_RE.search(text) is not None
        or (
            shopping_score >= LOW_CONFIDENCE_THRESHOLD
            and not _has_task_cue(text)
            and not shopping_intent.get("missing_fields")
            and not _is_time_bound_store_trip(text)
            and shopping_intent.get("intent") in SHOPPING_INTENTS
        )
    ):
        return N4OSIntentFrame(
            route="shopping",
            action=_action_for_route(
                "shopping",
                calendar_intent,
                task_intent,
                shopping_intent,
                home_board_intent,
                decision_intent,
                library_intent,
            ),
            confidence=round(max(shopping_score, 0.92 if EXPLICIT_SHOPPING_COMMAND_RE.search(text) else shopping_score), 2),
            normalized_request=request,
            clarification_question=(
                "Which shopping list: Indian, Costco, Whole Foods, Amazon, or Others?"
                if shopping_intent.get("missing_fields") == ["list_name"]
                else None
            ),
        )

    if _is_multiline_calendar_event_request(text):
        return N4OSIntentFrame(
            route="calendar",
            action="create_event",
            confidence=0.9,
            normalized_request=request,
        )

    if _is_explicit_calendar_create_text(text):
        return N4OSIntentFrame(
            route="calendar",
            action="create_event",
            confidence=0.95,
            missing_fields=[
                str(value)
                for value in calendar_intent.get("missing_fields", [])
                if value
            ],
            normalized_request=request,
        )

    if _is_ambiguous_shopping_item_request(text, shopping_intent):
        return N4OSIntentFrame(
            route="unknown",
            action="unknown",
            confidence=0.55,
            missing_fields=["list_name"],
            normalized_request=request,
            clarification_question="Which shopping list: Indian, Costco, Whole Foods, Amazon, or Others?",
        )

    if (
        _is_tag_update_request(text)
        and task_intent.get("intent") == "recommend_tasks"
        and task_intent.get("filters", {}).get("tags")
    ):
        return N4OSIntentFrame(
            route="tasks",
            action="recommend_tasks",
            confidence=0.86,
            normalized_request=request,
        )

    if _is_explicit_decision_request(text, decision_intent):
        return N4OSIntentFrame(
            route="decisions",
            action=_action_for_route(
                "decisions",
                calendar_intent,
                task_intent,
                shopping_intent,
                home_board_intent,
                decision_intent,
                library_intent,
            ),
            confidence=0.95,
            normalized_request=request,
        )

    if _is_combined_planning(text):
        return N4OSIntentFrame(
            route="both",
            action="combined_planning",
            confidence=0.88,
            normalized_request=request,
        )

    if (
        _has_explicit_home_board_target(text)
        and home_board_intent.get("intent") in HOME_BOARD_INTENTS
        and not home_board_intent.get("missing_fields")
    ):
        return N4OSIntentFrame(
            route="home_board",
            action=_action_for_route(
                "home_board",
                calendar_intent,
                task_intent,
                shopping_intent,
                home_board_intent,
                decision_intent,
                library_intent,
            ),
            confidence=0.95,
            normalized_request=request,
        )

    if (
        _is_object_update_request(text)
        and not _is_create_task_request(task_intent)
        and not _is_create_calendar_request(calendar_intent)
    ):
        if re.search(r"\b(calendar|event|appointment)\b", text):
            return N4OSIntentFrame(
                route="calendar",
                action="update_event",
                confidence=0.86,
                followup_kind="modify_previous",
                normalized_request=request,
            )
        if re.search(r"\b(tasks?|todos?|to-dos?)\b", text):
            return N4OSIntentFrame(
                route="tasks",
                action="update_task",
                confidence=0.86,
                followup_kind="modify_previous",
                normalized_request=request,
            )

    science_lab_score = _score_science_lab(text)
    if science_lab_score >= LOW_CONFIDENCE_THRESHOLD:
        return N4OSIntentFrame(
            route="science_lab",
            action="plan_experiments",
            confidence=round(science_lab_score, 2),
            normalized_request=request,
        )

    library_score = _score_library(text, library_intent)
    if library_score >= LOW_CONFIDENCE_THRESHOLD:
        return N4OSIntentFrame(
            route="library",
            action=_action_for_route(
                "library",
                calendar_intent,
                task_intent,
                shopping_intent,
                home_board_intent,
                decision_intent,
                library_intent,
            ),
            confidence=round(library_score, 2),
            normalized_request=request,
        )

    if (
        task_intent.get("intent") == "create_task"
        and _has_assistant_help_metadata(task_intent)
    ):
        return N4OSIntentFrame(
            route="tasks",
            action=_action_for_route(
                "tasks",
                calendar_intent,
                task_intent,
                shopping_intent,
                home_board_intent,
                decision_intent,
                library_intent,
            ),
            confidence=0.95,
            normalized_request=request,
        )

    calendar_score = _score_calendar(text, calendar_intent)
    task_score = _score_tasks(text, task_intent)
    shopping_score = _score_shopping(text, shopping_intent)
    home_board_score = _score_home_board(text, home_board_intent)
    decision_score = _score_decisions(text, decision_intent)
    science_lab_score = _score_science_lab(text)
    library_score = _score_library(text, library_intent)

    scores: dict[Route, float] = {
        "calendar": calendar_score,
        "tasks": task_score,
        "shopping": shopping_score,
        "home_board": home_board_score,
        "decisions": decision_score,
        "science_lab": science_lab_score,
        "library": library_score,
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

    return N4OSIntentFrame(
        route=route,
        action=_action_for_route(
            route,
            calendar_intent,
            task_intent,
            shopping_intent,
            home_board_intent,
            decision_intent,
            library_intent,
        ),
        confidence=round(confidence, 2),
        normalized_request=request,
        clarification_question=(
            CLARIFICATION_PROMPT
            if route == "unknown"
            else None
        ),
    )


def interpret_request(
    request: str,
    now: datetime | None = None,
    context: dict[str, Any] | None = None,
    interpreter: IntentInterpreter | InterpreterCallable | None = None,
) -> N4OSIntentFrame:
    explicit_frame = _explicit_intent_frame(request, now)
    if explicit_frame is not None:
        return explicit_frame

    fallback = _rule_intent_frame(request, now=now, context=context)
    candidates = list(recognize_route_candidates(request, now=now, context=context))
    if fallback.route != "unknown":
        fallback_candidate = RouteCandidate(
            route=fallback.route,
            action=fallback.action,
            confidence=fallback.confidence,
            evidence=("domain-owned rule decision",),
            slots=dict(fallback.slots),
            missing_fields=tuple(fallback.missing_fields),
        )
        existing_index = next(
            (index for index, candidate in enumerate(candidates) if candidate.route == fallback.route),
            None,
        )
        if existing_index is None:
            candidates.append(fallback_candidate)
        elif (
            fallback_candidate.confidence >= DETERMINISTIC_CONFIDENCE_THRESHOLD
            or fallback_candidate.confidence > candidates[existing_index].confidence
        ):
            candidates[existing_index] = fallback_candidate
    ordered = tuple(
        sorted(candidates, key=lambda candidate: (-candidate.confidence, candidate.route))
    )

    top = ordered[0] if ordered else None
    runner_up_confidence = ordered[1].confidence if len(ordered) > 1 else 0.0
    has_decisive_candidate = bool(
        top is not None
        and top.confidence >= DETERMINISTIC_CONFIDENCE_THRESHOLD
        and top.confidence - runner_up_confidence >= DETERMINISTIC_MARGIN_THRESHOLD
    )
    fallback_requires_clarification = bool(
        top is not None
        and top.route == "decisions"
        and top.action == "add_evidence"
        and re.fullmatch(r"\s*add\s+(?:note|evidence)\s*", request, flags=re.IGNORECASE)
    )
    if has_decisive_candidate and not fallback_requires_clarification:
        assert top is not None
        return N4OSIntentFrame(
            route=top.route,
            action=top.action,
            confidence=top.confidence,
            slots=dict(top.slots),
            missing_fields=list(top.missing_fields),
            normalized_request=request,
            decision_source="rules",
            candidates=ordered,
        )

    if interpreter is not None:
        interpreter_context = dict(context or {})
        interpreter_context["route_candidates"] = [candidate.to_dict() for candidate in ordered]
        try:
            frame = _coerce_intent_frame(
                _call_interpreter(interpreter, request, now, interpreter_context),
                request,
            )
            if frame.confidence >= LLM_CONFIDENCE_THRESHOLD and frame.route != "unknown":
                return N4OSIntentFrame(
                    **{
                        **frame.to_dict(),
                        "normalized_request": request,
                        "decision_source": "llm",
                        "candidates": ordered,
                    }
                )
        except Exception:
            pass

    clarification = fallback.clarification_question or _targeted_clarification(ordered)
    missing_fields = fallback.missing_fields or (
        list(top.missing_fields) if top is not None else ["request"]
    )
    return N4OSIntentFrame(
        route="unknown",
        action="unknown",
        confidence=top.confidence if top is not None else 0.0,
        followup_kind="clarification",
        missing_fields=list(missing_fields),
        normalized_request=request,
        clarification_question=clarification,
        decision_source="clarification",
        candidates=ordered,
    )


def route_request(
    request: str,
    now: datetime | None = None,
    context: dict[str, Any] | None = None,
    interpreter: IntentInterpreter | InterpreterCallable | None = None,
) -> dict[str, Any]:
    return interpret_request(
        request,
        now=now,
        context=context,
        interpreter=interpreter,
    ).to_route_decision()

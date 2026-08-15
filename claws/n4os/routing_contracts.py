from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
import re


RouteId = Literal[
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
DecisionSource = Literal["explicit", "rules", "llm", "clarification"]
OperationStatus = Literal["success", "clarification", "failure", "noop"]


@dataclass(frozen=True)
class RouteSpec:
    route: RouteId
    label: str
    actions: frozenset[str]
    command_aliases: tuple[str, ...] = ()
    mutating_actions: frozenset[str] = frozenset()
    model_routable: bool = True


ROUTE_SPECS: tuple[RouteSpec, ...] = (
    RouteSpec(
        route="capture",
        label="Capture",
        actions=frozenset({"capture_note"}),
        command_aliases=("capture", "note", "mem", "mem-inbox"),
        mutating_actions=frozenset({"capture_note"}),
        model_routable=False,
    ),
    RouteSpec(
        route="calendar",
        label="Calendar",
        actions=frozenset(
            {
                "create_event",
                "list_events",
                "update_event",
                "delete_event",
                "add_guests",
                "family_briefing",
                "preparation_checklist",
            }
        ),
        command_aliases=("calendar", "calender", "calnedar", "event", "schedule"),
        mutating_actions=frozenset({"create_event", "update_event", "delete_event", "add_guests"}),
    ),
    RouteSpec(
        route="tasks",
        label="Tasks",
        actions=frozenset(
            {
                "create_task",
                "recommend_tasks",
                "update_task",
                "complete_task",
                "delete_task",
                "run_assistant_help",
            }
        ),
        command_aliases=("task", "tasks", "todo", "todos"),
        mutating_actions=frozenset(
            {"create_task", "update_task", "complete_task", "delete_task", "run_assistant_help"}
        ),
    ),
    RouteSpec(
        route="shopping",
        label="Shopping",
        actions=frozenset(
            {
                "list_lists",
                "list_items",
                "add_item",
                "add_items",
                "check_item",
                "uncheck_item",
                "delete_item",
                "move_item",
                "clear_list",
            }
        ),
        command_aliases=("cart", "shop", "shopping"),
        mutating_actions=frozenset(
            {
                "add_item",
                "add_items",
                "check_item",
                "uncheck_item",
                "delete_item",
                "move_item",
                "clear_list",
            }
        ),
    ),
    RouteSpec(
        route="home_board",
        label="Home Board",
        actions=frozenset({"add_item", "add_items", "list_items", "mark_done"}),
        command_aliases=("home", "homeboard", "home-board"),
        mutating_actions=frozenset({"add_item", "add_items", "mark_done"}),
    ),
    RouteSpec(
        route="decisions",
        label="Decisions",
        actions=frozenset(
            {
                "create_backlog_item",
                "list_backlog",
                "add_backlog_note",
                "set_backlog_position",
                "move_backlog_item",
                "pin_backlog_item",
                "park_backlog_item",
                "close_backlog_item",
                "create_decision",
                "list_decisions",
                "decision_brief",
                "add_option",
                "add_evidence",
                "add_next_step",
                "record_decision",
                "bulk_record_decisions",
            }
        ),
        command_aliases=("decision", "decisions", "backlog"),
        mutating_actions=frozenset(
            {
                "create_backlog_item",
                "add_backlog_note",
                "set_backlog_position",
                "move_backlog_item",
                "pin_backlog_item",
                "park_backlog_item",
                "close_backlog_item",
                "create_decision",
                "add_option",
                "add_evidence",
                "add_next_step",
                "record_decision",
                "bulk_record_decisions",
            }
        ),
    ),
    RouteSpec(
        route="science_lab",
        label="Science Lab",
        actions=frozenset({"plan_experiments"}),
        command_aliases=("science", "science-lab", "experiment", "experiments"),
    ),
    RouteSpec(
        route="library",
        label="Library",
        actions=frozenset(
            {
                "record_reading",
                "record_checkout",
                "update_reading",
                "delete_reading",
                "status",
                "clarify",
                "not_counted",
            }
        ),
        command_aliases=("library", "reading"),
        mutating_actions=frozenset(
            {"record_reading", "record_checkout", "update_reading", "delete_reading"}
        ),
    ),
    RouteSpec(
        route="both",
        label="Calendar + Tasks",
        actions=frozenset({"combined_planning", "calendar_and_tasks"}),
    ),
    RouteSpec(route="unknown", label="Unknown", actions=frozenset({"unknown"})),
)

ROUTE_REGISTRY = {spec.route: spec for spec in ROUTE_SPECS}
COMMAND_ROUTES = {
    alias: spec.route
    for spec in ROUTE_SPECS
    for alias in spec.command_aliases
}
COMMAND_RE = re.compile(
    r"^\s*/(?P<command>[A-Za-z][A-Za-z0-9-]*)(?:@[A-Za-z0-9_]+)?"
    r"(?:\s+|:\s*)?(?P<body>.*)$",
    re.DOTALL,
)


@dataclass(frozen=True)
class ExplicitRoute:
    route: RouteId
    command: str
    body: str


@dataclass(frozen=True)
class RouteCandidate:
    route: RouteId
    action: str
    confidence: float
    evidence: tuple[str, ...] = ()
    slots: dict[str, Any] = field(default_factory=dict)
    missing_fields: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.missing_fields

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "action": self.action,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "slots": dict(self.slots),
            "missing_fields": list(self.missing_fields),
        }


@dataclass(frozen=True)
class TurnDecision:
    route: RouteId
    action: str
    confidence: float
    source: DecisionSource
    original_input: str
    normalized_input: str
    slots: dict[str, Any] = field(default_factory=dict)
    missing_fields: tuple[str, ...] = ()
    clarification: str | None = None
    candidates: tuple[RouteCandidate, ...] = ()


@dataclass(frozen=True)
class PreparedCommand:
    route: RouteId
    action: str
    original_input: str
    fields: dict[str, Any]


@dataclass(frozen=True)
class OperationResult:
    status: OperationStatus
    route: RouteId
    action: str
    response: str
    effect: Literal["read", "mutation", "none"] = "none"
    affected_ids: tuple[str, ...] = ()
    mutation_reference: str | None = None
    undoable: bool = False
    decision: TurnDecision | None = None


def parse_explicit_route(text: str) -> ExplicitRoute | None:
    match = COMMAND_RE.match(text)
    if match is None:
        return None
    command = match.group("command").lower()
    route = COMMAND_ROUTES.get(command)
    if route is None:
        return None
    return ExplicitRoute(route=route, command=command, body=match.group("body").strip())


def is_valid_route_action(route: str, action: str) -> bool:
    spec = ROUTE_REGISTRY.get(route)
    return spec is not None and action in spec.actions


def is_valid_model_route_action(route: str, action: str) -> bool:
    spec = ROUTE_REGISTRY.get(route)
    return spec is not None and spec.model_routable and action in spec.actions


def route_action_prompt() -> str:
    return "; ".join(
        f"{spec.route}({','.join(sorted(spec.actions))})"
        for spec in ROUTE_SPECS
        if spec.model_routable
    )

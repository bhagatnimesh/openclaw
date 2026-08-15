from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Iterable, Literal

try:
    from .input_normalizer import improve_entered_text
    from .intent_router import IntentInterpreter, interpret_request
    from .routing_contracts import parse_explicit_route
except ImportError:
    from input_normalizer import improve_entered_text
    from intent_router import IntentInterpreter, interpret_request
    from routing_contracts import parse_explicit_route


Modality = Literal["explicit", "natural", "clarification"]
Split = Literal["development", "holdout"]


@dataclass(frozen=True)
class RoutingEvaluationCase:
    case_id: str
    utterance: str
    expected_route: str
    expected_action: str
    modality: Modality
    split: Split
    origin: str


@dataclass(frozen=True)
class RoutingEvaluationReport:
    count: int
    route_accuracy: float
    route_action_accuracy: float
    macro_route_action_accuracy: float
    modality_accuracy: dict[str, float]
    route_accuracy_by_route: dict[str, float]
    failed_case_ids: tuple[str, ...]


ACCEPTANCE_THRESHOLDS = {
    "macro_route_action_accuracy": 0.85,
    "explicit": 1.0,
    "natural": 0.9,
    "clarification": 1.0,
}


def load_evaluation_cases(path: Path | None = None) -> tuple[RoutingEvaluationCase, ...]:
    corpus_path = path or Path(__file__).with_name("routing_evaluation_cases.json")
    payload = json.loads(corpus_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("routing evaluation corpus must be a JSON array")
    return tuple(RoutingEvaluationCase(**item) for item in payload)


def evaluate_routing(
    cases: Iterable[RoutingEvaluationCase],
    *,
    now: datetime | None = None,
    interpreter: IntentInterpreter | None = None,
) -> RoutingEvaluationReport:
    evaluated = list(cases)
    outcomes: list[tuple[RoutingEvaluationCase, bool, bool]] = []
    for case in evaluated:
        request = case.utterance
        if parse_explicit_route(request) is None:
            request = improve_entered_text(request)
        frame = interpret_request(request, now=now, interpreter=interpreter)
        route_matches = frame.route == case.expected_route
        route_action_matches = route_matches and frame.action == case.expected_action
        outcomes.append((case, route_matches, route_action_matches))

    route_accuracy = _accuracy(match for _, match, _ in outcomes)
    route_action_accuracy = _accuracy(match for _, _, match in outcomes)
    routes = sorted({case.expected_route for case, _, _ in outcomes})
    route_accuracy_by_route = {
        route: _accuracy(
            match
            for case, _, match in outcomes
            if case.expected_route == route
        )
        for route in routes
    }
    modalities = sorted({case.modality for case, _, _ in outcomes})
    modality_accuracy = {
        modality: _accuracy(
            match
            for case, _, match in outcomes
            if case.modality == modality
        )
        for modality in modalities
    }
    return RoutingEvaluationReport(
        count=len(outcomes),
        route_accuracy=route_accuracy,
        route_action_accuracy=route_action_accuracy,
        macro_route_action_accuracy=_accuracy(route_accuracy_by_route.values()),
        modality_accuracy=modality_accuracy,
        route_accuracy_by_route=route_accuracy_by_route,
        failed_case_ids=tuple(case.case_id for case, _, match in outcomes if not match),
    )


def acceptance_failures(report: RoutingEvaluationReport) -> tuple[str, ...]:
    failures: list[str] = []
    macro_threshold = ACCEPTANCE_THRESHOLDS["macro_route_action_accuracy"]
    if report.macro_route_action_accuracy < macro_threshold:
        failures.append(
            f"macro route/action accuracy {report.macro_route_action_accuracy:.3f} < {macro_threshold:.3f}"
        )
    for modality in ("explicit", "natural", "clarification"):
        threshold = ACCEPTANCE_THRESHOLDS[modality]
        actual = report.modality_accuracy.get(modality, 0.0)
        if actual < threshold:
            failures.append(f"{modality} accuracy {actual:.3f} < {threshold:.3f}")
    return tuple(failures)


def _accuracy(values: Iterable[bool | float]) -> float:
    numbers = [float(value) for value in values]
    if not numbers:
        return 0.0
    return sum(numbers) / len(numbers)

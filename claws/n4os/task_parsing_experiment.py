from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
import hashlib
import json
import os
import re
import time as time_module
from typing import Any, Protocol
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

try:
    from .input_normalizer import improve_entered_text
    from .intent_router import _tasks_intent_module, interpret_request
    from .routing_contracts import parse_explicit_route
except ImportError:
    from input_normalizer import improve_entered_text
    from intent_router import _tasks_intent_module, interpret_request
    from routing_contracts import parse_explicit_route


DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASE_PATH = Path(__file__).with_name("task_parsing_evaluation_cases.json")
DEFAULT_CACHE_PATH = DEFAULT_REPO_ROOT / ".artifacts" / "n4os-task-ai-field-cache.json"
DEFAULT_REFERENCE_TIME = datetime(2026, 8, 13, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_API_CONTEXT_MODEL = "gpt-5.4-mini"
TASK_ROUTES = {"tasks"}
MUTATING_TASK_ACTIONS = {
    "create_task",
    "update_task",
    "complete_task",
    "delete_task",
    "run_assistant_help",
}


class TaskFieldExtractor(Protocol):
    model: str

    def extract(
        self,
        request: str,
        *,
        now: datetime | None = None,
        baseline_intent: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class TaskParsingCase:
    case_id: str
    utterance: str
    expected_route: str | None = None
    expected_action: str | None = None
    expected_slots: dict[str, Any] = field(default_factory=dict)
    origin: str = "synthetic"

    @property
    def scored(self) -> bool:
        return self.expected_route is not None and self.expected_action is not None


@dataclass(frozen=True)
class ParserOutput:
    status: str
    route: str
    action: str
    confidence: float
    task_intent: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "route": self.route,
            "action": self.action,
            "confidence": self.confidence,
            "task_intent": self.task_intent,
            "error": self.error,
        }


@dataclass(frozen=True)
class ScoredParserOutput:
    output: ParserOutput
    scored: bool
    success: bool | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": self.output.to_dict(),
            "scored": self.scored,
            "success": self.success,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ExperimentOutcome:
    case: TaskParsingCase
    current: ScoredParserOutput
    proposed: ScoredParserOutput

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": {
                "case_id": self.case.case_id,
                "utterance": self.case.utterance,
                "expected_route": self.case.expected_route,
                "expected_action": self.case.expected_action,
                "expected_slots": self.case.expected_slots,
                "origin": self.case.origin,
            },
            "current": self.current.to_dict(),
            "proposed": self.proposed.to_dict(),
        }


@dataclass(frozen=True)
class ExperimentReport:
    outcomes: tuple[ExperimentOutcome, ...]

    def summary(self) -> dict[str, Any]:
        task_outcomes = [
            outcome
            for outcome in self.outcomes
            if outcome.case.expected_route in TASK_ROUTES
        ]
        return {
            "cases": len(self.outcomes),
            "scored_cases": sum(1 for outcome in self.outcomes if outcome.case.scored),
            "unscored_cases": sum(1 for outcome in self.outcomes if not outcome.case.scored),
            "current": _summarize_side(outcome.current for outcome in self.outcomes),
            "proposed": _summarize_side(outcome.proposed for outcome in self.outcomes),
            "task_labeled_cases": {
                "cases": len(task_outcomes),
                "current": _summarize_side(outcome.current for outcome in task_outcomes),
                "proposed": _summarize_side(outcome.proposed for outcome in task_outcomes),
            },
            "origins": _count_by(outcome.case.origin for outcome in self.outcomes),
        }

    def to_dict(self, *, include_cases: bool = False) -> dict[str, Any]:
        payload = self.summary()
        if include_cases:
            payload["outcomes"] = [outcome.to_dict() for outcome in self.outcomes]
        return payload


class TaskAIFieldCache:
    def __init__(self, path: Path):
        self.path = path
        self._items: dict[str, dict[str, Any]] | None = None

    def get_or_extract(
        self,
        extractor: TaskFieldExtractor,
        request: str,
        *,
        now: datetime | None,
        baseline_intent: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        key = self._key(extractor, request, now, baseline_intent, context)
        items = self._load()
        cached = items.get(key)
        if cached is not None:
            return dict(cached)
        extracted = extractor.extract(
            request,
            now=now,
            baseline_intent=baseline_intent,
            context=context,
        )
        items[key] = dict(extracted)
        self._save()
        return extracted

    def _key(
        self,
        extractor: TaskFieldExtractor,
        request: str,
        now: datetime | None,
        baseline_intent: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        model = str(getattr(extractor, "model", extractor.__class__.__name__))
        payload = {
            "model": model,
            "request": request,
            "now": now.isoformat() if now else None,
            "baseline_intent": baseline_intent,
            "context": context,
        }
        serialized = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._items is not None:
            return self._items
        if not self.path.exists():
            self._items = {}
            return self._items
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self._items = {
            str(key): dict(value)
            for key, value in payload.items()
            if isinstance(value, dict)
        }
        return self._items

    def _save(self) -> None:
        if self._items is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._items, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


class TaskApiContextFieldExtractor:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_API_CONTEXT_MODEL,
        timeout_seconds: int = 20,
    ):
        cleaned_key = api_key.strip()
        if not cleaned_key:
            raise RuntimeError("Task API context extraction needs OPENAI_API_KEY.")
        self.api_key = cleaned_key
        self.model = model.strip() or DEFAULT_API_CONTEXT_MODEL
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls) -> "TaskApiContextFieldExtractor":
        return cls(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            model=os.environ.get("N4OS_TASK_API_CONTEXT_MODEL", DEFAULT_API_CONTEXT_MODEL),
        )

    def extract(
        self,
        request: str,
        *,
        now: datetime | None = None,
        baseline_intent: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = {
            "model": self.model,
            "store": False,
            "max_output_tokens": 1000,
            "input": [
                {"role": "system", "content": _task_api_context_prompt()},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "request": request,
                            "reference_time": now.isoformat() if now else "not provided",
                            "baseline_intent": baseline_intent or {},
                            "context": context or {},
                            "output_schema": _task_api_context_schema(),
                        },
                        sort_keys=True,
                    ),
                },
            ],
        }
        api_request = urllib.request.Request(
            OPENAI_RESPONSES_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "n4os-task-api-context-experiment/0.1",
            },
            method="POST",
        )
        payload = self._post_json(api_request)
        raw = _json_object_from_response(payload)
        return _api_context_fields_to_legacy_fields(raw, request)

    def _post_json(self, api_request: urllib.request.Request) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(api_request, timeout=self.timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except (TimeoutError, urllib.error.URLError) as error:
                last_error = error
                if attempt == 2:
                    break
                time_module.sleep(0.25 * (attempt + 1))
        if last_error is not None:
            raise last_error
        raise RuntimeError("Task API context extraction failed without an error")


def load_task_cases(path: Path | None = None) -> tuple[TaskParsingCase, ...]:
    case_path = path or DEFAULT_CASE_PATH
    payload = json.loads(case_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("task parsing evaluation corpus must be a JSON array")
    return tuple(
        TaskParsingCase(
            case_id=str(item.get("case_id")),
            utterance=_clean_block(item.get("utterance")),
            expected_route=_clean_optional_string(item.get("expected_route")),
            expected_action=_clean_optional_string(item.get("expected_action")),
            expected_slots=_clean_dict(item.get("expected_slots")),
            origin=str(item.get("origin") or "synthetic"),
        )
        for item in payload
        if isinstance(item, dict) and _clean_block(item.get("utterance"))
    )


def run_experiment(
    cases: tuple[TaskParsingCase, ...],
    *,
    extractor: TaskFieldExtractor | None = None,
    cache: TaskAIFieldCache | None = None,
    reference_time: datetime | None = None,
) -> ExperimentReport:
    experiment_reference_time = reference_time or DEFAULT_REFERENCE_TIME
    outcomes = []
    for case in cases:
        current = run_current_parser(case.utterance, now=experiment_reference_time)
        proposed = run_proposed_ai_parser(
            case.utterance,
            now=experiment_reference_time,
            current=current,
            extractor=extractor,
            cache=cache,
        )
        outcomes.append(
            ExperimentOutcome(
                case=case,
                current=score_output(case, current),
                proposed=score_output(case, proposed),
            )
        )
    return ExperimentReport(tuple(outcomes))


def run_current_parser(request: str, *, now: datetime | None = None) -> ParserOutput:
    runtime_request = _runtime_request(request)
    frame = interpret_request(runtime_request, now=now)
    task_intent: dict[str, Any] = {}
    action = frame.action
    if frame.route in TASK_ROUTES:
        explicit_body = _task_command_body(runtime_request)
        task_body = explicit_body if parse_explicit_route(runtime_request) is not None else _task_command_body(
            frame.normalized_request or runtime_request
        )
        task_parse_body = _normalize_tagged_create_request(task_body)
        task_intent = _tasks_intent_module().extract_intent(
            task_parse_body,
            now=now,
        )
        if (
            frame.action == "update_task"
            and _looks_like_task_update_request(task_body)
        ) or _looks_like_followup_update_request(task_body):
            action = "update_task"
            task_intent = _update_task_intent_from_request(task_body)
        else:
            action = str(task_intent.get("intent") or frame.action)
    return ParserOutput(
        status="ok",
        route=frame.route,
        action=action,
        confidence=frame.confidence,
        task_intent=_normalize_task_intent(task_intent),
    )


def run_proposed_ai_parser(
    request: str,
    *,
    now: datetime | None,
    current: ParserOutput | None = None,
    extractor: TaskFieldExtractor | None,
    cache: TaskAIFieldCache | None = None,
) -> ParserOutput:
    baseline = current or run_current_parser(request, now=now)
    if extractor is None:
        return ParserOutput(
            status="not_run",
            route=baseline.route,
            action=baseline.action,
            confidence=baseline.confidence,
            task_intent=baseline.task_intent,
        )
    if baseline.route not in TASK_ROUTES:
        runtime_request = _runtime_request(request)
        if baseline.route == "unknown" and extractor is not None and _looks_like_task_candidate(runtime_request):
            body = _task_command_body(runtime_request)
            baseline_intent = (
                _update_task_intent_from_request(body)
                if _looks_like_context_update_request(body) or _looks_like_task_update_request(body)
                else _tasks_intent_module().extract_intent(
                    body,
                    now=now,
                )
            )
            baseline = ParserOutput(
                status="ok",
                route="tasks",
                action=str(baseline_intent.get("intent") or "create_task"),
                confidence=baseline.confidence,
                task_intent=_normalize_task_intent(baseline_intent),
            )
        else:
            return ParserOutput(
                status="not_task",
                route=baseline.route,
                action=baseline.action,
                confidence=baseline.confidence,
            )
    runtime_request = _runtime_request(request)
    baseline_intent = dict(baseline.task_intent) if baseline.task_intent else _tasks_intent_module().extract_intent(
        runtime_request,
        now=now,
    )
    if baseline.action == "update_task" or _looks_like_followup_update_request(_task_command_body(runtime_request)):
        baseline_intent = _update_task_intent_from_request(_task_command_body(runtime_request))
    context: dict[str, Any] = {}
    try:
        if cache is not None:
            ai_fields = cache.get_or_extract(
                extractor,
                runtime_request,
                now=now,
                baseline_intent=baseline_intent,
                context=context,
            )
        else:
            ai_fields = extractor.extract(
                runtime_request,
                now=now,
                baseline_intent=baseline_intent,
                context=context,
            )
        task_intent = _intent_from_ai_fields(
            ai_fields,
            runtime_request,
            now=now,
            baseline_intent=baseline_intent,
        )
    except Exception as error:
        task_intent = _repair_ai_task_intent(
            dict(baseline.task_intent),
            runtime_request,
            now=now,
            baseline_intent=baseline.task_intent,
        )
        if baseline.action == "update_task" or _looks_like_followup_update_request(_task_command_body(runtime_request)):
            task_intent["intent"] = "update_task"
        return ParserOutput(
            status="recovered",
            route=baseline.route,
            action=str(task_intent.get("intent") or baseline.action),
            confidence=baseline.confidence,
            task_intent=_normalize_task_intent(task_intent or baseline.task_intent),
            error=f"{error.__class__.__name__}: {error}",
        )

    ai_recovered_fields = _ai_recovered_fields(task_intent)
    return ParserOutput(
        status="recovered" if ai_recovered_fields else "ok",
        route="tasks",
        action=str(task_intent.get("intent") or baseline.action),
        confidence=_safe_float(ai_fields.get("confidence"), baseline.confidence),
        task_intent=_normalize_task_intent(task_intent),
        error=f"AI extraction repaired required fields: {', '.join(ai_recovered_fields)}"
        if ai_recovered_fields
        else None,
    )


def score_output(case: TaskParsingCase, output: ParserOutput) -> ScoredParserOutput:
    if not case.scored:
        return ScoredParserOutput(output, False, None, "unlabeled")
    if output.status == "not_run":
        return ScoredParserOutput(output, False, None, output.status)
    if output.status == "recovered":
        return ScoredParserOutput(output, True, False, output.status)
    if output.status == "error":
        return ScoredParserOutput(output, True, False, output.status)
    expected_route = case.expected_route or "unknown"
    expected_action = case.expected_action or "unknown"
    if output.route != expected_route:
        return ScoredParserOutput(output, True, False, f"route {output.route} != {expected_route}")
    if output.action != expected_action:
        return ScoredParserOutput(output, True, False, f"action {output.action} != {expected_action}")
    if expected_route not in TASK_ROUTES:
        return ScoredParserOutput(output, True, True, "route/action match")

    slot_failure = _slot_failure(case.expected_slots, output.task_intent)
    if slot_failure is not None:
        return ScoredParserOutput(output, True, False, slot_failure)

    actionable_failure = _actionable_failure(output.action, output.task_intent)
    if actionable_failure is not None:
        return ScoredParserOutput(output, True, False, actionable_failure)
    return ScoredParserOutput(output, True, True, "actionable")


def _intent_from_ai_fields(
    ai_fields: dict[str, Any],
    request: str,
    *,
    now: datetime | None,
    baseline_intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    module = _tasks_intent_module()
    slots = ai_fields.get("slots") if isinstance(ai_fields.get("slots"), dict) else {}
    baseline_action = str(baseline_intent.get("intent") or "create_task") if baseline_intent else "create_task"
    action = str(ai_fields.get("action") or baseline_action)
    if action not in {
        "create_task",
        "recommend_tasks",
        "update_task",
        "complete_task",
        "delete_task",
        "run_assistant_help",
    }:
        raise ValueError(f"Task API context extraction returned invalid action: {action}")
    intent: dict[str, Any] = {
        "intent": action,
        "missing_fields": [str(value) for value in ai_fields.get("missing_fields") or [] if value],
        "ai_field_extraction": {
            "confidence": ai_fields.get("confidence"),
            "missing_fields": [str(value) for value in ai_fields.get("missing_fields") or [] if value],
            "normalized_request": request,
        },
    }
    required_before_repair: dict[str, Any] = {"action": action}

    if action == "create_task":
        for slot_key, intent_key in (("title", "title"), ("notes", "notes"), ("due", "due")):
            value = _clean_optional_string(slots.get(slot_key))
            if value is not None:
                intent[intent_key] = value[:1].upper() + value[1:] if intent_key == "title" else value
        required_before_repair["title"] = intent.get("title")
        required_before_repair["due"] = intent.get("due")
        required_before_repair["notes"] = intent.get("notes")
        metadata = slots.get("metadata")
        intent["metadata"] = module.normalize_metadata(metadata if isinstance(metadata, dict) else {})
        required_before_repair["metadata"] = intent["metadata"]
    elif action == "recommend_tasks":
        filters = slots.get("filters")
        intent["filters"] = _normalize_recommendation_filters(
            module,
            dict(filters) if isinstance(filters, dict) else {},
            request=request,
        )
        required_before_repair["filters"] = intent["filters"]
    elif action in {"complete_task", "delete_task"}:
        query = _clean_optional_string(slots.get("query"))
        target = _clean_optional_string(slots.get("target"))
        if target is not None and _is_implicit_task_target(target):
            intent["target"] = "last_task"
        elif query is not None and _is_implicit_task_target(query):
            intent["target"] = "last_task"
        elif query is not None:
            intent["query"] = query
        required_before_repair["query"] = intent.get("query")
        required_before_repair["target"] = intent.get("target")
    elif action == "update_task":
        query = _clean_optional_string(slots.get("query"))
        target = _clean_optional_string(slots.get("target"))
        if target is not None and _is_implicit_task_target(target):
            intent["target"] = "last_task"
        elif query is not None and _is_implicit_task_target(query):
            intent["target"] = "last_task"
        elif query is not None:
            intent["query"] = query
        elif target is not None:
            intent["query"] = target
        update = slots.get("update")
        if isinstance(update, dict):
            required_before_repair["raw_update"] = dict(update)
            intent["update"] = _normalize_update_payload(module, update)
        required_before_repair["query"] = intent.get("query")
        required_before_repair["target"] = intent.get("target")
        required_before_repair["update"] = intent.get("update")

    _repair_ai_task_intent(intent, request, now=now, baseline_intent=baseline_intent)
    repaired_fields = _required_fields_repaired(required_before_repair, intent)
    if repaired_fields:
        intent["ai_field_extraction"]["recovered_fields"] = repaired_fields
    _refresh_missing_fields(intent)
    return intent


def _task_api_context_prompt() -> str:
    return (
        "You parse family task requests into a Google Tasks v1-style draft plus N4OS "
        "task metadata. Return only compact JSON. Do not call tools, create tasks, "
        "invent task ids, or include secrets. Google Tasks task creation uses tasks.insert "
        "with a task resource; relevant fields here are title, notes, due, status, and "
        "links. Use due as a date-only YYYY-MM-DD value unless the user explicitly needs "
        "a timestamp. N4OS metadata classifies tasks by tags, context, energy, duration, "
        "urgency, complexity, effort_type, requires, can_do_while, location, owner, and "
        "assistant help. Resolve relative dates against reference_time in America/Los_Angeles. "
        "Map family aliases only to dad, mom, both, grandmom, nysha, navya, or unknown. "
        "Use operation recommend_tasks for list/recommend/filter requests, complete_task "
        "or delete_task for destructive task target requests, run_assistant_help for queued "
        "Noah assistant work without a new task, update_task for edits to an existing task, and clarify only "
        "when the request is genuinely missing the task title or target. Preserve the "
        "user's intended title and notes; do not stuff due dates, owners, tags, or metadata "
        "annotations into the title. For 'Add task ... Ask Noah/help ...' requests, use "
        "create_task with assistant metadata rather than run_assistant_help. For "
        "recommend_tasks, return all grounded filters directly, including context, "
        "available_context, can_do_while, due_min, and due_max when the request implies them."
    )


def _task_api_context_schema() -> dict[str, Any]:
    return {
        "operation": "create_task | recommend_tasks | update_task | complete_task | delete_task | run_assistant_help | clarify",
        "confidence": "number 0..1",
        "taskList": {
            "id_hint": "optional task list id only if supplied by user",
            "title": "optional user-visible list title",
        },
        "task": {
            "title": "task title",
            "notes": "optional human-readable notes",
            "due": "YYYY-MM-DD or RFC3339 due timestamp",
            "status": "needsAction | completed",
            "n4os_metadata": {
                "tags": ["lowercase labels without #"],
                "context": ["home | car | computer | phone | outside | errand"],
                "energy": "low | medium | high | unknown",
                "duration_minutes": "positive integer or null",
                "urgency": "low | medium | high | unknown",
                "complexity": "low | medium | high | unknown",
                "effort_type": "physical | cognitive | communication | errand | paperwork | research | admin | unknown",
                "requires": ["computer | phone | car | internet | paperwork | equipment | quiet | focus"],
                "can_do_while": ["driving | commuting | walking | waiting | watching_kids"],
                "location": "home | outside | anywhere | specific | unknown",
                "owner": "dad | mom | both | grandmom | nysha | navya | unknown",
                "assistant_help_needed": "boolean",
                "assistant_name": "Noah when requested",
                "assistant_help_request": "optional concise assistant task",
                "assistant_context": "optional assistant context",
            },
        },
        "filters": {
            "tags": ["optional tag filters"],
            "owner": "optional owner filter",
            "context": ["optional context filters such as car, phone, computer, home"],
            "available_context": ["optional current contexts available to the user"],
            "available_resources": ["optional resources available now"],
            "unavailable_resources": ["optional resources unavailable now"],
            "can_do_while": ["optional compatible activities such as driving or commuting"],
            "energy": "optional energy filter",
            "effort_type": "optional effort type filter",
            "preferred_effort_type": "optional preferred effort type",
            "due_min": "optional ISO lower due bound",
            "due_max": "optional ISO upper due bound",
            "duration_minutes": "optional available minutes",
        },
        "target": {
            "query": "task title/search text for update/complete/delete",
        },
        "update": "optional update fields for update_task",
        "missing_fields": "array of title/task",
        "clarification_question": "optional concise question",
    }


def _api_context_fields_to_legacy_fields(raw: dict[str, Any], request: str) -> dict[str, Any]:
    action = _operation_to_legacy_action(_clean_optional_string(raw.get("operation")))
    confidence = _safe_float(raw.get("confidence"), 0.0)
    if confidence < 0.8:
        raise ValueError("Task API context extraction confidence below threshold")

    slots: dict[str, Any] = {}
    task = raw.get("task") if isinstance(raw.get("task"), dict) else {}
    target = raw.get("target") if isinstance(raw.get("target"), dict) else {}
    task = task if isinstance(task, dict) else {}
    target = target if isinstance(target, dict) else {}

    for api_key, slot_key in (("title", "title"), ("notes", "notes")):
        value = _clean_optional_string(task.get(api_key))
        if value:
            slots[slot_key] = value
    due = _date_from_api_due(task.get("due"))
    if due is not None:
        slots["due"] = due
    metadata = task.get("n4os_metadata") if isinstance(task.get("n4os_metadata"), dict) else {}
    if metadata:
        slots["metadata"] = metadata
    filters = raw.get("filters") if isinstance(raw.get("filters"), dict) else {}
    if filters:
        slots["filters"] = filters
    query = _clean_optional_string(target.get("query"))
    if query is not None:
        if _is_implicit_task_target(query):
            slots["target"] = "last_task"
        else:
            slots["query"] = query
    update = raw.get("update") if isinstance(raw.get("update"), dict) else {}
    if update:
        slots["update"] = update

    missing_fields = [
        field
        for field in (str(value).strip() for value in raw.get("missing_fields") or [])
        if field
    ]
    return {
        "action": action,
        "confidence": confidence,
        "slots": slots,
        "missing_fields": missing_fields,
        "clarification_question": _clean_optional_string(raw.get("clarification_question")),
        "normalized_request": request,
    }


def _operation_to_legacy_action(operation: str | None) -> str:
    if operation in {
        "create_task",
        "recommend_tasks",
        "update_task",
        "complete_task",
        "delete_task",
        "run_assistant_help",
    }:
        return operation
    if operation == "clarify":
        return "create_task"
    raise ValueError(f"Task API context extraction returned invalid operation: {operation}")


def _repair_ai_task_intent(
    intent: dict[str, Any],
    request: str,
    *,
    now: datetime | None,
    baseline_intent: dict[str, Any] | None,
) -> dict[str, Any]:
    if not intent:
        return intent
    module = _tasks_intent_module()
    body = _task_command_body(request)
    baseline = baseline_intent or module.extract_intent(body, now=now)
    fallback_action = str(intent.get("intent") or baseline.get("intent") or "create_task")
    if baseline.get("intent") == "update_task" and _looks_like_task_update_request(body):
        fallback_action = "update_task"
    action = _task_action_from_text(body, fallback=fallback_action)
    intent["intent"] = action

    if action == "create_task":
        if not intent.get("title") and baseline.get("title"):
            intent["title"] = baseline["title"]
        request_title = module._title_from_request(module._normalize_create_request_text(body))
        if request_title and _has_explicit_task_title_shape(body):
            intent["title"] = request_title
        if intent.get("title"):
            intent["title"] = _clean_task_title(str(intent["title"]))
        if not intent.get("due") or _has_relative_due_text(body) or _has_explicit_due_text(body):
            due, _ = module._extract_due_date(body, module._default_now(now))
            if due is None and re.search(r"\btonight\b", body, flags=re.IGNORECASE):
                due = module._default_now(now).date().isoformat()
            if due is not None:
                intent["due"] = due
        if not intent.get("notes") and baseline.get("notes"):
            intent["notes"] = baseline["notes"]
        if (
            intent.get("notes")
            and not _has_explicit_task_notes(body)
            and not _has_explicit_assistant_text(body)
        ):
            intent.pop("notes", None)
        if (
            intent.get("due")
            and not baseline.get("due")
            and not (_has_relative_due_text(body) or _has_explicit_due_text(body))
        ):
            intent.pop("due", None)
        metadata = intent.get("metadata") if isinstance(intent.get("metadata"), dict) else {}
        baseline_metadata = baseline.get("metadata") if isinstance(baseline.get("metadata"), dict) else {}
        intent["metadata"] = _merge_task_metadata(
            module,
            baseline_metadata,
            metadata,
            request=body,
        )
    elif action == "recommend_tasks":
        filters = intent.get("filters") if isinstance(intent.get("filters"), dict) else {}
        baseline_filters = baseline.get("filters") if isinstance(baseline.get("filters"), dict) else {}
        intent["filters"] = _merge_recommendation_filters(module, baseline_filters, filters, request=body)
    elif action in {"complete_task", "delete_task"}:
        if not intent.get("query") and baseline.get("query"):
            intent["query"] = baseline["query"]
    elif action == "update_task":
        if not intent.get("query") and baseline.get("query"):
            intent["query"] = baseline["query"]
        if not intent.get("target") and baseline.get("target"):
            intent["target"] = baseline["target"]
        baseline_update = baseline.get("update") if isinstance(baseline.get("update"), dict) else {}
        update = intent.get("update") if isinstance(intent.get("update"), dict) else {}
        intent["update"] = _normalize_update_payload(module, {**baseline_update, **update})
    elif action == "run_assistant_help":
        intent["missing_fields"] = []
    return intent


def _update_task_intent_from_request(request: str) -> dict[str, Any]:
    body = _task_command_body(request)
    owner, owner_target = _owner_update_from_text(body)
    update: dict[str, Any] = {}
    query = owner_target
    if owner is not None:
        update["owner"] = owner
    note = _note_update_from_text(body)
    if note is not None:
        update["note"] = note
    tags = _tags_update_from_text(body)
    if tags:
        update["tags"] = tags
    assistant_help = _assistant_update_from_text(body)
    if assistant_help is not None:
        update["assistant_help_request"] = assistant_help
    target = None
    if query is None:
        query = _explicit_update_target_from_text(body)
    if query is None and _uses_implicit_last_task_target(body):
        target = "last_task"
    if query is None and target is None:
        query = _target_from_update_text(body)
    intent = {
        "intent": "update_task",
        "query": query,
        "update": update,
        "missing_fields": [] if query or target else ["task"],
    }
    if target is not None:
        intent["target"] = target
    return intent


def _normalize_update_payload(module: Any, update: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    unsupported = []
    for key, value in update.items():
        normalized_key = {
            "notes": "note",
            "description": "note",
            "context": "note",
        }.get(str(key), str(key))
        if normalized_key == "unsupported_update_fields":
            unsupported.extend(str(item) for item in value or [])
            continue
        if normalized_key not in {"owner", "note", "tags", "assistant_help_request"}:
            unsupported.append(str(key))
            continue
        normalized[normalized_key] = value
    if "owner" in normalized:
        owner = str(normalized.get("owner") or "").strip().lower()
        normalized_owner = module.OWNER_ALIASES.get(owner, owner)
        normalized["owner"] = normalized_owner if normalized_owner in module.VALID_OWNERS else owner
    if "tags" in normalized:
        normalized["tags"] = module.normalize_tags(normalized.get("tags"))
    if unsupported:
        normalized["unsupported_update_fields"] = sorted(set(unsupported))
    return normalized


def _owner_update_from_text(value: str) -> tuple[str | None, str | None]:
    module = _tasks_intent_module()
    owner_pattern = module.OWNER_ALIAS_PATTERN
    target_match = re.search(
        rf"^\s*(?:assign|set|make|change|update|put)\s+"
        rf"(?P<target>.+?)\s+"
        rf"(?:to|for|owner\s+to|owner\s+as|as\s+owner)\s+"
        rf"(?P<owner>{owner_pattern})\b\.?\s*$",
        value,
        flags=re.IGNORECASE,
    )
    if target_match is not None:
        owner = module.OWNER_ALIASES.get(target_match.group("owner").strip().lower(), "unknown")
        return owner if owner != "unknown" else None, _clean_update_target(target_match.group("target"))
    owner_match = re.search(
        rf"\b(?:owner(?:\s+of\s+(?:it|this|that|the\s+task))?|owned\s+by|"
        rf"assign(?:ed)?\s+to|belongs\s+to|for)\s*"
        rf"(?:is|:|to|as)?\s*(?P<owner>{owner_pattern})\b",
        value,
        flags=re.IGNORECASE,
    )
    if owner_match is None:
        owner_as_match = re.search(
            rf"\b(?P<owner>{owner_pattern})\s+as\s+(?:the\s+)?owner\b",
            value,
            flags=re.IGNORECASE,
        )
        if owner_as_match is None:
            return None, None
        owner = module.OWNER_ALIASES.get(owner_as_match.group("owner").strip().lower(), "unknown")
        return owner if owner != "unknown" else None, None
    owner = module.OWNER_ALIASES.get(owner_match.group("owner").strip().lower(), "unknown")
    return owner if owner != "unknown" else None, None


def _note_update_from_text(value: str) -> str | None:
    explicit_match = re.search(
        r"^\s*add\s+(?:a\s+)?(?:note|notes|description|context)\s+"
        r"(?P<note>.+?)\s+to\s+\S.*$",
        value,
        flags=re.IGNORECASE,
    )
    if explicit_match is not None:
        return _clean_optional_string(explicit_match.group("note").strip(" ."))
    match = re.search(
        r"^\s*(?:add|append|set|update|put)?\s*"
        r"(?:a\s+)?(?:note|notes|description|context)\s*"
        r"(?:is|are|to|:)?\s+(?P<note>.+?)\s*$",
        value,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return _clean_optional_string(match.group("note").strip(" ."))


def _tags_update_from_text(value: str) -> list[str]:
    module = _tasks_intent_module()
    return module.extract_tags(value)


def _assistant_update_from_text(value: str) -> str | None:
    match = re.search(
        r"\b(?:add|ask|have|queue|put|set\s+up)?\s*"
        r"(?P<assistant>noah|novah|ai\s+assistant|assistant)\b"
        r".*?\b(?:help|research|find|look\s+up|figure\s+out|call|email|draft)\b"
        r"(?P<help>.*)$",
        value,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    cleaned = re.sub(
        r"^\s*(?:me\s+)?(?:(?:to|with|on|for)\s+)?"
        r"(?:(?:the|this|that)\s+task|it|this|that)?\s*",
        "",
        match.group("help"),
        flags=re.IGNORECASE,
    ).strip(" .,:;-")
    return cleaned or "help with this task"


def _target_from_update_text(value: str) -> str | None:
    explicit_target = _explicit_update_target_from_text(value)
    if explicit_target is not None:
        return explicit_target
    cleaned = re.sub(
        r"^\s*(?:assign|set|make|change|update|put|add|append)\s+",
        "",
        value,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\s+\b(?:owner|owned\s+by|assign(?:ed)?\s+to|for|note|notes|description|context|tags?|labels?)\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return _clean_update_target(cleaned)


def _explicit_update_target_from_text(value: str) -> str | None:
    match = re.search(
        r"^\s*add\s+(?:note\b.+?|#[A-Za-z][A-Za-z0-9_-]*(?:\s+#[A-Za-z][A-Za-z0-9_-]*)*)\s+to\s+(?P<target>.+)$",
        value,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return _clean_update_target(match.group("target"))


def _clean_update_target(value: str) -> str | None:
    cleaned = re.sub(r"\btask\b", "", value, flags=re.IGNORECASE).strip(" .")
    if cleaned.lower() in {"it", "this", "that", "the", "this task", "that task", "the task"}:
        return None
    return _clean_optional_string(cleaned)


def _merge_task_metadata(
    module: Any,
    baseline_metadata: dict[str, Any],
    ai_metadata: dict[str, Any],
    *,
    request: str,
) -> dict[str, Any]:
    baseline = module.normalize_metadata(baseline_metadata)
    ai = module.normalize_metadata(ai_metadata)
    default = module.normalize_metadata({})
    merged = dict(baseline)
    for key, value in ai.items():
        if value == default.get(key):
            continue
        if key == "tags" and not baseline.get("tags") and not _is_explicit_filter_key("tags", request):
            continue
        if baseline.get(key) == default.get(key) and not _is_grounded_task_metadata(key, value, request):
            continue
        merged[key] = value
    if merged.get("effort_type") == "errand":
        requires = list(merged.get("requires") or [])
        if "car" not in requires:
            requires.append("car")
        merged["requires"] = requires
        if merged.get("location") == "unknown":
            merged["location"] = "outside"
    return module.normalize_metadata(merged)


def _is_grounded_task_metadata(key: str, value: Any, request: str) -> bool:
    text = request.lower()
    if key == "tags":
        return _is_explicit_filter_key("tags", request)
    if key == "duration_minutes":
        return re.search(r"\b\d+\s*(?:m|mins?|minutes?|h|hrs?|hours?)\b", text) is not None
    if key == "energy":
        return re.search(r"\b(?:low|medium|high)\s+energy\b|\benergy\s+(?:low|medium|high)\b", text) is not None
    if key == "effort_type":
        return _is_grounded_effort_type(str(value or ""), text)
    if key == "requires":
        values = [str(item).lower() for item in value] if isinstance(value, list) else [str(value).lower()]
        return all(_is_grounded_resource(item, text) for item in values if item)
    if key == "context":
        values = [str(item).lower() for item in value] if isinstance(value, list) else [str(value).lower()]
        return all(_is_grounded_context(item, text) for item in values if item)
    if key == "can_do_while":
        return any(str(item).replace("_", " ") in text for item in value) if isinstance(value, list) else str(value) in text
    if key == "location":
        return str(value).lower() in text or _is_grounded_effort_type("errand", text)
    if key in {"owner"}:
        return re.search(r"\b(?:owner|owned\s+by|assign(?:ed)?\s+to)\b", text) is not None
    if key.startswith("assistant_"):
        return _has_explicit_assistant_text(request)
    if key in {"urgency", "complexity"}:
        return str(value).lower() in text
    return False


def _is_grounded_effort_type(value: str, text: str) -> bool:
    if value == "communication":
        return re.search(r"\b(?:call|text|email|message|follow[- ]?up|draft email)\b", text) is not None
    if value == "errand":
        return re.search(r"\b(?:errand|buy|return|drop\s+off|pick\s+up|pickup|costco|library)\b", text) is not None
    if value == "admin":
        return re.search(r"\b(?:admin|renew|registration|order|online)\b", text) is not None
    if value == "paperwork":
        return re.search(r"\b(?:paperwork|forms?|passport|visa)\b", text) is not None
    if value == "research":
        return re.search(r"\b(?:research|compare|options?)\b", text) is not None
    if value == "physical":
        return re.search(r"\b(?:change|clean|repair|fix|replace|filter|garage)\b", text) is not None
    if value == "cognitive":
        return re.search(r"\b(?:write|draft|plan|think|focus)\b", text) is not None
    return False


def _is_grounded_resource(value: str, text: str) -> bool:
    if value == "computer":
        return re.search(r"\b(?:computer|laptop|online|internet|email|research|forms?|registration|order)\b", text) is not None
    if value == "internet":
        return re.search(r"\b(?:internet|online|laptop|computer|email|research|registration|order)\b", text) is not None
    if value == "phone":
        return re.search(r"\b(?:phone|call|text|message)\b", text) is not None
    if value == "car":
        return re.search(r"\b(?:car|driving|commut|errand|costco|library|drop\s+off|pick\s+up|pickup|return|buy)\b", text) is not None
    if value == "paperwork":
        return re.search(r"\b(?:paperwork|forms?|passport|visa)\b", text) is not None
    if value == "equipment":
        return re.search(r"\b(?:equipment|filter|repair|fix|replace)\b", text) is not None
    if value in {"quiet", "focus"}:
        return re.search(r"\b(?:quiet|focus)\b", text) is not None
    return False


def _is_grounded_context(value: str, text: str) -> bool:
    if value == "home":
        return re.search(r"\b(?:home|garage|filter)\b", text) is not None
    if value == "car":
        return re.search(r"\b(?:car|driving|commut)\b", text) is not None
    if value == "phone":
        return re.search(r"\b(?:phone|call|text|message|driving|commut)\b", text) is not None
    if value == "computer":
        return re.search(r"\b(?:computer|laptop|online|email|research|registration|order)\b", text) is not None
    if value in {"outside", "errand"}:
        return _is_grounded_effort_type("errand", text)
    return False


def _merge_recommendation_filters(
    module: Any,
    baseline_filters: dict[str, Any],
    ai_filters: dict[str, Any],
    *,
    request: str,
) -> dict[str, Any]:
    merged = dict(baseline_filters)
    for key, value in _normalize_recommendation_filters(module, ai_filters, request=request).items():
        if key not in baseline_filters and not _is_explicit_filter_key(key, request):
            continue
        if isinstance(value, list):
            merged[key] = _merge_lists(merged.get(key), value)
        elif value not in (None, "", []):
            merged[key] = value
    return merged


def _normalize_recommendation_filters(
    module: Any,
    filters: dict[str, Any],
    *,
    request: str,
) -> dict[str, Any]:
    normalized = dict(filters)
    if "tags" in normalized:
        normalized["tags"] = module.normalize_tags(normalized.get("tags"))
    if "owner" in normalized:
        owner = str(normalized.get("owner") or "").strip().lower()
        normalized_owner = module.OWNER_ALIASES.get(owner, owner)
        normalized["owner"] = normalized_owner if normalized_owner in module.VALID_OWNERS else owner

    metadata = module.normalize_metadata(
        {
            "context": normalized.get("context") or normalized.get("available_context"),
            "requires": normalized.get("available_resources"),
            "can_do_while": normalized.get("can_do_while"),
            "energy": normalized.get("energy"),
            "effort_type": normalized.get("effort_type") or normalized.get("preferred_effort_type"),
        }
    )
    if normalized.get("context") is not None:
        normalized["context"] = metadata["context"]
    if normalized.get("available_context") is not None:
        normalized["available_context"] = metadata["context"]
    if normalized.get("available_resources") is not None:
        normalized["available_resources"] = metadata["requires"]
    if normalized.get("can_do_while") is not None:
        normalized["can_do_while"] = metadata["can_do_while"]
    if normalized.get("energy") is not None:
        normalized["energy"] = metadata["energy"]
    if normalized.get("effort_type") is not None:
        normalized["effort_type"] = metadata["effort_type"]
    if normalized.get("preferred_effort_type") is not None:
        normalized["preferred_effort_type"] = metadata["effort_type"]
    for key in ("due_min", "due_max"):
        if key in normalized:
            normalized[key] = _normalize_filter_due_bound(key, normalized.get(key))
    return normalized


def _is_explicit_filter_key(key: str, request: str) -> bool:
    text = request.lower()
    if key == "tags":
        return "#" in request or re.search(r"\b(?:tag|tags|label|labels)\b", text) is not None
    if key == "unavailable_resources":
        return re.search(r"\b(?:without|no|unavailable|can't|cannot)\b", text) is not None
    if key in {"energy", "effort_type", "preferred_effort_type"}:
        return re.search(
            r"\b(?:low|medium|high)\s+energy|energy\s+(?:low|medium|high)|"
            r"paperwork|calls?|communication|research|errands?|physical|cognitive\b",
            text,
        ) is not None
    return True


def _normalize_filter_due_bound(key: str, value: Any) -> Any:
    cleaned = _clean_optional_string(value)
    if cleaned is None:
        return value
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        parsed_date = datetime.fromisoformat(cleaned).date()
        bound_time = time.min if key == "due_min" else time.max
        return datetime.combine(
            parsed_date,
            bound_time,
            tzinfo=ZoneInfo("America/Los_Angeles"),
        ).isoformat()
    return value


def _merge_lists(left: Any, right: Any) -> list[Any]:
    values = []
    seen = set()
    for value in [
        *(left if isinstance(left, list) else []),
        *(right if isinstance(right, list) else []),
    ]:
        key = str(value).lower()
        if key in seen:
            continue
        seen.add(key)
        values.append(value)
    return values


def _task_action_from_text(value: str, *, fallback: str) -> str:
    module = _tasks_intent_module()
    if fallback == "update_task" and (
        _looks_like_task_update_request(value) or _looks_like_followup_update_request(value)
    ):
        return "update_task"
    local = module.extract_intent(_task_command_body(value))
    action = str(local.get("intent") or "")
    if action in {"create_task", "complete_task", "delete_task", "run_assistant_help"}:
        return action
    if action == "recommend_tasks" and _looks_like_recommendation_request(value):
        return action
    return fallback


def _task_command_body(request: str) -> str:
    explicit_route = parse_explicit_route(request)
    if explicit_route is not None:
        return explicit_route.body
    return request


def _looks_like_task_candidate(value: str) -> bool:
    body = _task_command_body(value)
    if _looks_like_followup_update_request(body) or _looks_like_complete_or_delete_task_followup(body):
        return True
    if _looks_like_timed_create_candidate(body):
        return True
    return re.search(
        r"\b(?:task|todo|to-do|open loop|remind me|follow[- ]?up|follow up|"
        r"to follow[- ]?up|noah|assistant|owner|owned\s+by|assign(?:ed)?\s+to|"
        r"assign|make\s+it\s+for|"
        r"renew|fill|research|clean|repair|fix|replace|prepare|"
        r"drop\s+off|pick\s+up|pickup)\b",
        body,
        flags=re.IGNORECASE,
    ) is not None


def _looks_like_timed_create_candidate(value: str) -> bool:
    return re.search(r"\b(?:call|text|email|message|order)\b", value, flags=re.IGNORECASE) is not None and (
        _has_relative_due_text(value) or _has_explicit_due_text(value)
    )


def _looks_like_complete_or_delete_task_followup(value: str) -> bool:
    return re.search(r"^\s*(?:complete|finish|delete|remove)\s+\S", value, flags=re.IGNORECASE) is not None


def _normalize_tagged_create_request(value: str) -> str:
    match = re.match(
        r"^\s*add\s+(?P<tags>(?:#[A-Za-z][A-Za-z0-9_-]*\s+)+)(?P<title>\S.*)$",
        value,
        flags=re.IGNORECASE,
    )
    if match is None:
        return value
    tags = " ".join(match.group("tags").split())
    title = match.group("title").strip()
    if not title or title.startswith("#"):
        return value
    if re.search(r"\s+to\s+\S", title, flags=re.IGNORECASE):
        return value
    return f"add task {title} {tags}"


def _is_implicit_task_target(value: str) -> bool:
    normalized = re.sub(r"[_-]+", " ", value).strip().lower()
    return normalized in {
        "it",
        "this",
        "that",
        "last task",
        "previous task",
        "prior task",
        "current task",
        "this task",
        "that task",
        "the task",
    }


def _looks_like_task_update_request(value: str) -> bool:
    return re.search(
        r"\b(?:assign|assigned|owner|owned by|note|notes|description|context|"
        r"tag|tags|label|labels|noah|assistant|update|set)\b",
        value,
        flags=re.IGNORECASE,
    ) is not None


def _looks_like_followup_update_request(value: str) -> bool:
    return re.search(
        r"^\s*add\s+(?:note\b|#[A-Za-z][A-Za-z0-9_-]*(?:\s+#[A-Za-z][A-Za-z0-9_-]*)*(?:\s*$|\s+to\s+)|"
        r"(?:noah|novah|ai\s+assistant|assistant)\b.*\bhelp\b)|"
        r"^\s*update\s+(?:the\s+)?task\s+with\s+tags?\b|"
        r"^\s*(?:tags?|labels?)\s*:|"
        r"^\s*(?:owner\s+(?:is|:)|make\s+it\s+for)\b|"
        r"\b(?:dad|mom|mother|father|nimesh|niyati|nysha|navya|grandmom|dadi)\s+as\s+(?:the\s+)?owner\b",
        value,
        flags=re.IGNORECASE,
    ) is not None


def _looks_like_context_update_request(value: str) -> bool:
    return _looks_like_followup_update_request(value) or re.search(
        r"^\s*(?:owner\s+(?:is|:)|make\s+it\s+for)\b",
        value,
        flags=re.IGNORECASE,
    ) is not None


def _uses_implicit_last_task_target(value: str) -> bool:
    return re.search(
        r"^\s*add\s+(?:note\b|#[A-Za-z][A-Za-z0-9_-]*\b|"
        r"(?:noah|novah|ai\s+assistant|assistant)\b.*\bhelp\b)|"
        r"^\s*(?:tags?|labels?)\s*:|"
        r"^\s*(?:owner\s+(?:is|:)|make\s+it\s+for)\b|"
        r"\b(?:dad|mom|mother|father|nimesh|niyati|nysha|navya|grandmom|dadi)\s+as\s+(?:the\s+)?owner\b|"
        r"\b(?:it|this\s+task|that\s+task|the\s+task)\b",
        value,
        flags=re.IGNORECASE,
    ) is not None


def _looks_like_recommendation_request(value: str) -> bool:
    return re.search(
        r"^\s*(?:what|which|show|list|give|recommend)\b|"
        r"\b(?:tasks?|todos?|to-dos?|open loops?)\b.*\b(?:can|should|show|list|recommend)\b",
        value,
        flags=re.IGNORECASE,
    ) is not None


def _clean_task_title(value: str) -> str:
    cleaned = re.sub(
        r"\s*,?\s*(?:by|due|on|for)\s+"
        r"(?:today|tonight|tomorrow|this\s+weekend|weekend|"
        r"next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
        r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
        r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2})\b.*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\s*,?\s*(?:for|takes?|under|within|in|have)?\s*"
        r"\d+\s*(?:minutes?|mins?|hours?|hrs?)\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return _clean_optional_string(cleaned.strip(" ,.-")) or value


def _has_explicit_task_title_shape(value: str) -> bool:
    return re.search(r"\b(?:title|header)\s*:", value, flags=re.IGNORECASE) is not None


def _has_explicit_due_text(value: str) -> bool:
    return re.search(
        r"\b(?:today|tonight|tomorrow|weekend|next\s+\w+|by|due|on\s+"
        r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)|"
        r"in\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+(?:days?|weeks?))\b",
        value,
        flags=re.IGNORECASE,
    ) is not None


def _has_relative_due_text(value: str) -> bool:
    return re.search(
        r"\b(?:in\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
        r"(?:days?|weeks?)|(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
        r"(?:days?|weeks?)\s+from\s+now)\b",
        value,
        flags=re.IGNORECASE,
    ) is not None


def _has_explicit_assistant_text(value: str) -> bool:
    return re.search(r"\b(?:noah|assistant|ai assistant)\b", value, flags=re.IGNORECASE) is not None


def _has_explicit_task_notes(value: str) -> bool:
    return re.search(
        r"\b(?:details?|notes?|body)\s*:|"
        r"\b(?:below\s+(?:email|message|text)|following\s+(?:email|message|text))\b",
        value,
        flags=re.IGNORECASE,
    ) is not None


def _ai_recovered_fields(intent: dict[str, Any]) -> list[str]:
    extraction = intent.get("ai_field_extraction") if isinstance(intent.get("ai_field_extraction"), dict) else {}
    return [str(value) for value in extraction.get("recovered_fields") or [] if value]


def _required_fields_repaired(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    repaired: list[str] = []
    before_action = str(before.get("action") or "")
    after_action = str(after.get("intent") or "")
    if before_action and after_action and before_action != after_action:
        repaired.append("action")
    if after_action == "create_task":
        before_title = before.get("title")
        after_title = after.get("title")
        if not before_title and after_title:
            repaired.append("title")
        elif before_title and after_title and _normalized_text(before_title) != _normalized_text(after_title):
            repaired.append("title")
        for field_name in ("due", "notes"):
            before_value = before.get(field_name)
            after_value = after.get(field_name)
            if not before_value and after_value:
                repaired.append(field_name)
            elif before_value and not after_value:
                repaired.append(field_name)
            elif before_value and after_value and _normalized_text(before_value) != _normalized_text(after_value):
                repaired.append(field_name)
        before_metadata = before.get("metadata") if isinstance(before.get("metadata"), dict) else {}
        after_metadata = after.get("metadata") if isinstance(after.get("metadata"), dict) else {}
        if _non_default_metadata(before_metadata) != _non_default_metadata(after_metadata):
            repaired.append("metadata")
    elif after_action == "recommend_tasks":
        before_filters = before.get("filters") if isinstance(before.get("filters"), dict) else {}
        after_filters = after.get("filters") if isinstance(after.get("filters"), dict) else {}
        if _normalized_structure(before_filters) != _normalized_structure(after_filters):
            repaired.append("filters")
    elif after_action in {"complete_task", "delete_task"}:
        if not (before.get("query") or before.get("target")) and (after.get("query") or after.get("target")):
            repaired.append("task")
    elif after_action == "update_task":
        before_target = before.get("target") or before.get("query")
        after_target = after.get("target") or after.get("query")
        if not before_target and after_target:
            repaired.append("task")
        elif before_target and after_target and _normalized_text(before_target) != _normalized_text(after_target):
            repaired.append("task")
        before_update = before.get("raw_update") if isinstance(before.get("raw_update"), dict) else before.get("update")
        before_update = before_update if isinstance(before_update, dict) else {}
        after_update = after.get("update") if isinstance(after.get("update"), dict) else {}
        if _normalized_structure(before_update) != _normalized_structure(after_update):
            repaired.append("update")
    return repaired


def _non_default_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    default = _tasks_intent_module().normalize_metadata({})
    return {key: value for key, value in metadata.items() if value != default.get(key)}


def _normalized_structure(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalized_structure(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return sorted(_normalized_structure(item) for item in value)
    if isinstance(value, str):
        return _normalized_text(value)
    return value


def _refresh_missing_fields(intent: dict[str, Any]) -> None:
    action = str(intent.get("intent") or "")
    missing = [
        str(value)
        for value in intent.get("missing_fields") or []
        if value
        and not (
            (value == "title" and intent.get("title"))
            or value == "due"
            or (value == "notes" and intent.get("notes"))
            or (value == "task" and intent.get("query"))
            or (value == "task" and intent.get("target") == "last_task")
        )
    ]
    if action == "create_task" and not intent.get("title") and "title" not in missing:
        missing.append("title")
    if (
        action in {"update_task", "complete_task", "delete_task"}
        and not intent.get("query")
        and intent.get("target") != "last_task"
        and "task" not in missing
    ):
        missing.append("task")
    intent["missing_fields"] = missing


def _normalize_task_intent(intent: dict[str, Any]) -> dict[str, Any]:
    if not intent:
        return {}
    keys = (
        "intent",
        "title",
        "notes",
        "due",
        "metadata",
        "filters",
        "query",
        "target",
        "update",
        "missing_fields",
        "ai_field_extraction",
    )
    return {key: intent[key] for key in keys if key in intent and intent[key] not in (None, "", [])}


def _actionable_failure(action: str, intent: dict[str, Any]) -> str | None:
    missing = [str(value) for value in intent.get("missing_fields") or [] if value]
    if action not in MUTATING_TASK_ACTIONS:
        return None
    if missing:
        return f"missing {', '.join(missing)}"
    if action == "create_task" and not intent.get("title"):
        return "missing title"
    if action == "update_task" and intent.get("update", {}).get("unsupported_update_fields"):
        fields = ", ".join(str(value) for value in intent["update"]["unsupported_update_fields"])
        return f"unsupported update {fields}"
    if action == "update_task" and not intent.get("update"):
        return "missing update"
    if (
        action in {"update_task", "complete_task", "delete_task"}
        and not intent.get("query")
        and intent.get("target") != "last_task"
    ):
        return "missing task"
    return None


def _slot_failure(expected_slots: dict[str, Any], actual: dict[str, Any]) -> str | None:
    unexpected = _unexpected_mutating_slot_failure(expected_slots, actual)
    if unexpected is not None:
        return unexpected
    return _match_expected(expected_slots, actual, path="slot")


def _unexpected_mutating_slot_failure(expected_slots: dict[str, Any], actual: dict[str, Any]) -> str | None:
    action = str(actual.get("intent") or "")
    if action == "create_task":
        if "due" not in expected_slots and actual.get("due"):
            return "slot extra due"
        if (
            "notes" not in expected_slots
            and actual.get("notes")
            and not _is_duplicate_assistant_note(actual)
        ):
            return "slot extra notes"
    expected_metadata = expected_slots.get("metadata") if isinstance(expected_slots.get("metadata"), dict) else {}
    actual_metadata = actual.get("metadata") if isinstance(actual.get("metadata"), dict) else {}
    if not isinstance(actual_metadata, dict):
        return None
    if action == "create_task":
        extra_metadata = _extra_non_default_metadata_keys(expected_metadata, actual_metadata)
        if extra_metadata:
            return f"slot metadata extra {', '.join(extra_metadata)}"
    if "owner" not in expected_metadata and actual_metadata.get("owner") not in (None, "", "unknown"):
        return "slot metadata extra owner"
    if "tags" not in expected_metadata and actual_metadata.get("tags"):
        return "slot metadata extra tags"
    if "assistant_help_needed" not in expected_metadata and actual_metadata.get("assistant_help_needed"):
        return "slot metadata extra assistant_help_needed"
    if action == "update_task":
        expected_update = expected_slots.get("update") if isinstance(expected_slots.get("update"), dict) else {}
        actual_update = actual.get("update") if isinstance(actual.get("update"), dict) else {}
        extra_update = [
            key
            for key, value in actual_update.items()
            if key not in expected_update and key != "unsupported_update_fields" and value not in (None, "", [])
        ]
        if extra_update:
            return f"slot update extra {', '.join(sorted(extra_update))}"
    return None


def _is_duplicate_assistant_note(actual: dict[str, Any]) -> bool:
    metadata = actual.get("metadata") if isinstance(actual.get("metadata"), dict) else {}
    notes = _normalized_text(actual.get("notes"))
    help_request = _normalized_text(metadata.get("assistant_help_request"))
    if not bool(metadata.get("assistant_help_needed")) or not help_request:
        return False
    allowed = [f"assistant help: {help_request}"]
    assistant_context = _normalized_text(metadata.get("assistant_context"))
    if assistant_context:
        allowed.append(f"assistant help: {help_request} assistant context: {assistant_context}")
    return notes in allowed


def _extra_non_default_metadata_keys(expected_metadata: dict[str, Any], actual_metadata: dict[str, Any]) -> list[str]:
    default = _tasks_intent_module().normalize_metadata({})
    return sorted(
        key
        for key, value in actual_metadata.items()
        if key not in expected_metadata and value != default.get(key)
    )


def _match_expected(expected: Any, actual: Any, *, path: str) -> str | None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return f"{path} missing"
        if path == "slot filters":
            extra_keys = [
                key
                for key, value in actual.items()
                if key not in expected and value not in (None, "", [])
            ]
            if extra_keys:
                return f"{path} extra {', '.join(sorted(extra_keys))}"
        for key, expected_value in expected.items():
            failure = _match_expected(expected_value, actual.get(key), path=f"{path} {key}")
            if failure is not None:
                return failure
        return None
    if isinstance(expected, list):
        actual_values = _normalized_list(actual)
        expected_values = _normalized_list(expected)
        if actual_values != expected_values:
            return f"{path} mismatch"
        return None
    if isinstance(expected, bool):
        if bool(actual) != expected:
            return f"{path} {actual!r} != {expected!r}"
        return None
    if isinstance(expected, int):
        if _safe_int(actual) != expected:
            return f"{path} {actual!r} != {expected!r}"
        return None
    if _normalized_text(actual) != _normalized_text(expected):
        return f"{path} {actual!r} != {expected!r}"
    return None


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _normalized_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({str(item).lower() for item in value})


def _json_object_from_response(payload: dict[str, Any]) -> dict[str, Any]:
    text = payload.get("output_text")
    if not isinstance(text, str) or not text.strip():
        chunks = []
        for item in payload.get("output", []):
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    chunks.append(content["text"])
        text = "\n".join(chunks)
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("Task API context extraction returned non-object JSON")
    return parsed


def _runtime_request(request: str) -> str:
    return request if parse_explicit_route(request) is not None else improve_entered_text(request)


def _date_from_api_due(value: Any) -> str | None:
    cleaned = _clean_optional_string(value)
    if cleaned is None:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}", cleaned):
        return cleaned[:10]
    parsed = _parse_datetime(cleaned)
    if parsed is not None:
        return parsed.date().isoformat()
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _clean_block(value: Any) -> str:
    return str(value or "").strip()


def _clean_optional_string(value: Any) -> str | None:
    cleaned = " ".join(str(value or "").split()).strip()
    return cleaned or None


def _clean_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _safe_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _summarize_side(outputs: Any) -> dict[str, Any]:
    collected = list(outputs)
    scored = [item for item in collected if item.scored]
    successes = [item for item in scored if item.success is True]
    failures = [item for item in scored if item.success is False]
    return {
        "scored": len(scored),
        "successes": len(successes),
        "failures": len(failures),
        "success_rate": round(len(successes) / len(scored), 3) if scored else None,
        "unscored": len(collected) - len(scored),
        "reasons": _count_by(item.reason for item in collected),
    }


def _count_by(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _build_api_context_extractor() -> TaskFieldExtractor:
    return TaskApiContextFieldExtractor.from_env()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate N4OS Telegram task parsing strategies.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASE_PATH)
    parser.add_argument("--include-cases", action="store_true")
    parser.add_argument("--api-context-ai", action="store_true")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument(
        "--reference-time",
        default=DEFAULT_REFERENCE_TIME.isoformat(),
        help="ISO timestamp used to resolve relative due dates in the fixed corpus.",
    )
    args = parser.parse_args(argv)

    extractor = _build_api_context_extractor() if args.api_context_ai else None
    cache = TaskAIFieldCache(args.cache) if extractor is not None else None
    report = run_experiment(
        load_task_cases(args.cases),
        extractor=extractor,
        cache=cache,
        reference_time=_parse_reference_time(args.reference_time),
    )
    print(json.dumps(report.to_dict(include_cases=args.include_cases), indent=2, sort_keys=True))
    return 0


def _parse_reference_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())

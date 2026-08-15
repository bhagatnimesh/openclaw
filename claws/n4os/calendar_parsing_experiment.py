from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import json
import os
import re
import sqlite3
from textwrap import dedent
from typing import Any, Protocol
import urllib.request

try:
    from .input_normalizer import improve_entered_text
    from .intent_router import _calendar_intent_module, interpret_request
    from .routing_contracts import parse_explicit_route
except ImportError:
    from input_normalizer import improve_entered_text
    from intent_router import _calendar_intent_module, interpret_request
    from routing_contracts import parse_explicit_route


DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_PATH = DEFAULT_REPO_ROOT / ".artifacts" / "n4os-calendar-ai-field-cache.json"
TELEGRAM_SOURCE_PREFIX = "telegram_"
CALENDAR_ROUTES = {"calendar", "both"}
MUTATING_CALENDAR_ACTIONS = {"create_event", "update_event", "delete_event", "add_guests"}
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_API_CONTEXT_MODEL = "gpt-5.4-mini"


class CalendarFieldExtractor(Protocol):
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
class CalendarParsingCase:
    case_id: str
    utterance: str
    source: str
    origin: str
    captured_at: datetime | None = None
    expected_route: str | None = None
    expected_action: str | None = None
    expected_slots: dict[str, Any] = field(default_factory=dict)

    @property
    def scored(self) -> bool:
        return self.expected_route is not None and self.expected_action is not None


@dataclass(frozen=True)
class ParserOutput:
    status: str
    route: str
    action: str
    confidence: float
    calendar_intent: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "route": self.route,
            "action": self.action,
            "confidence": self.confidence,
            "calendar_intent": self.calendar_intent,
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
    case: CalendarParsingCase
    current: ScoredParserOutput
    proposed: ScoredParserOutput

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": {
                "case_id": self.case.case_id,
                "utterance": self.case.utterance,
                "source": self.case.source,
                "origin": self.case.origin,
                "captured_at": self.case.captured_at.isoformat() if self.case.captured_at else None,
                "expected_route": self.case.expected_route,
                "expected_action": self.case.expected_action,
                "expected_slots": self.case.expected_slots,
            },
            "current": self.current.to_dict(),
            "proposed": self.proposed.to_dict(),
        }


@dataclass(frozen=True)
class ExperimentReport:
    outcomes: tuple[ExperimentOutcome, ...]

    def summary(self) -> dict[str, Any]:
        calendar_outcomes = [
            outcome
            for outcome in self.outcomes
            if outcome.case.expected_route in CALENDAR_ROUTES
        ]
        return {
            "cases": len(self.outcomes),
            "scored_cases": sum(1 for outcome in self.outcomes if outcome.case.scored),
            "unscored_cases": sum(1 for outcome in self.outcomes if not outcome.case.scored),
            "current": _summarize_side(outcome.current for outcome in self.outcomes),
            "proposed": _summarize_side(outcome.proposed for outcome in self.outcomes),
            "calendar_labeled_cases": {
                "cases": len(calendar_outcomes),
                "current": _summarize_side(outcome.current for outcome in calendar_outcomes),
                "proposed": _summarize_side(outcome.proposed for outcome in calendar_outcomes),
            },
            "origins": _count_by(outcome.case.origin for outcome in self.outcomes),
        }

    def to_dict(self, *, include_cases: bool = False) -> dict[str, Any]:
        payload = self.summary()
        if include_cases:
            payload["outcomes"] = [outcome.to_dict() for outcome in self.outcomes]
        return payload


class CalendarAIFieldCache:
    def __init__(self, path: Path):
        self.path = path
        self._items: dict[str, dict[str, Any]] | None = None

    def get_or_extract(
        self,
        extractor: CalendarFieldExtractor,
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
        extractor: CalendarFieldExtractor,
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


class CalendarApiContextFieldExtractor:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_API_CONTEXT_MODEL,
        timeout_seconds: int = 20,
    ):
        cleaned_key = api_key.strip()
        if not cleaned_key:
            raise RuntimeError("Calendar API context extraction needs OPENAI_API_KEY.")
        self.api_key = cleaned_key
        self.model = model.strip() or DEFAULT_API_CONTEXT_MODEL
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls) -> "CalendarApiContextFieldExtractor":
        return cls(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            model=os.environ.get("N4OS_CALENDAR_API_CONTEXT_MODEL", DEFAULT_API_CONTEXT_MODEL),
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
                {"role": "system", "content": _calendar_api_context_prompt()},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "request": request,
                            "reference_time": now.isoformat() if now else "not provided",
                            "baseline_intent": baseline_intent or {},
                            "context": context or {},
                            "output_schema": _calendar_api_context_schema(),
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
                "User-Agent": "n4os-calendar-api-context-experiment/0.1",
            },
            method="POST",
        )
        with urllib.request.urlopen(api_request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        raw = _json_object_from_response(payload)
        return _api_context_fields_to_legacy_fields(raw, request)


def load_local_cases(repo_root: Path = DEFAULT_REPO_ROOT) -> tuple[CalendarParsingCase, ...]:
    cases: list[CalendarParsingCase] = []
    cases.extend(load_routing_cases(repo_root / "claws" / "n4os" / "routing_evaluation_cases.json"))
    cases.extend(load_trajectory_cases(repo_root / "n4os" / "trajectories"))
    cases.extend(load_sqlite_raw_input_cases(repo_root / "data" / "n4os.db"))
    return _dedupe_cases(cases)


def load_routing_cases(path: Path) -> tuple[CalendarParsingCase, ...]:
    if not path.exists():
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        utterance = _clean_block(item.get("utterance"))
        if not utterance:
            continue
        cases.append(
            CalendarParsingCase(
                case_id=str(item.get("case_id") or _stable_case_id("routing", utterance)),
                utterance=utterance,
                source="routing_evaluation",
                origin=str(item.get("origin") or "routing_evaluation"),
                expected_route=_clean_optional_string(item.get("expected_route")),
                expected_action=_clean_optional_string(item.get("expected_action")),
                expected_slots=_clean_dict(item.get("expected_slots")),
            )
        )
    return tuple(cases)


def load_trajectory_cases(trajectories_root: Path) -> tuple[CalendarParsingCase, ...]:
    if not trajectories_root.exists():
        return ()
    records: list[CalendarParsingCase] = []
    for path in sorted(trajectories_root.glob("*.md")):
        records.extend(_trajectory_cases_from_text(path.read_text(encoding="utf-8"), path.stem))
    return tuple(records)


def load_sqlite_raw_input_cases(db_path: Path) -> tuple[CalendarParsingCase, ...]:
    if not db_path.exists():
        return ()
    cases: list[CalendarParsingCase] = []
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        table_names = [
            row[0]
            for row in db.execute(
                "select name from sqlite_master where type = 'table' order by name"
            )
        ]
        for table_name in table_names:
            columns = _table_columns(db, table_name)
            if "raw_input" not in columns or "source" not in columns:
                continue
            selected = ["id", "raw_input", "source", "created_at"]
            available = [column for column in selected if column in columns]
            quoted_columns = ", ".join(_quote_identifier(column) for column in available)
            for row in db.execute(
                f"select {quoted_columns} from {_quote_identifier(table_name)} order by rowid"
            ):
                values = dict(zip(available, row))
                source = str(values.get("source") or "")
                if not source.startswith(TELEGRAM_SOURCE_PREFIX):
                    continue
                utterance = _clean_block(values.get("raw_input"))
                if not utterance:
                    continue
                record_id = _clean_optional_string(values.get("id")) or _stable_case_id(table_name, utterance)
                cases.append(
                    CalendarParsingCase(
                        case_id=f"sqlite-{table_name}-{record_id}",
                        utterance=utterance,
                        source=source,
                        origin=f"sqlite:{table_name}",
                        captured_at=_parse_datetime(values.get("created_at")),
                    )
                )
    return tuple(cases)


def run_experiment(
    cases: tuple[CalendarParsingCase, ...],
    *,
    extractor: CalendarFieldExtractor | None = None,
    cache: CalendarAIFieldCache | None = None,
    reference_time: datetime | None = None,
) -> ExperimentReport:
    outcomes = []
    for case in cases:
        now = case.captured_at or reference_time
        current = run_current_parser(case.utterance, now=now)
        proposed = run_proposed_ai_parser(
            case.utterance,
            now=now,
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
    calendar_intent: dict[str, Any] = {}
    if frame.route in CALENDAR_ROUTES:
        calendar_intent = _calendar_intent_module().extract_intent(
            frame.normalized_request or runtime_request,
            now=now,
        )
    return ParserOutput(
        status="ok",
        route=frame.route,
        action=frame.action,
        confidence=frame.confidence,
        calendar_intent=_normalize_calendar_intent(calendar_intent),
    )


def run_proposed_ai_parser(
    request: str,
    *,
    now: datetime | None,
    current: ParserOutput | None = None,
    extractor: CalendarFieldExtractor | None,
    cache: CalendarAIFieldCache | None = None,
) -> ParserOutput:
    baseline = current or run_current_parser(request, now=now)
    if baseline.route not in CALENDAR_ROUTES:
        return ParserOutput(
            status="not_calendar",
            route=baseline.route,
            action=baseline.action,
            confidence=baseline.confidence,
        )
    if extractor is None:
        return ParserOutput(
            status="not_run",
            route=baseline.route,
            action=baseline.action,
            confidence=baseline.confidence,
            calendar_intent=baseline.calendar_intent,
        )

    runtime_request = _runtime_request(request)
    baseline_intent = baseline.calendar_intent or _calendar_intent_module().extract_intent(
        runtime_request,
        now=now,
    )
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
        calendar_intent = _intent_from_ai_fields(
            ai_fields,
            runtime_request,
            now=now,
            baseline_intent=baseline_intent,
        )
    except Exception as error:
        calendar_intent = _repair_ai_calendar_intent(
            dict(baseline.calendar_intent),
            runtime_request,
            now=now,
            baseline_intent=baseline.calendar_intent,
        )
        return ParserOutput(
            status="ok" if calendar_intent else "error",
            route=baseline.route,
            action=str(calendar_intent.get("intent") or baseline.action),
            confidence=baseline.confidence,
            calendar_intent=_normalize_calendar_intent(calendar_intent or baseline.calendar_intent),
            error=f"{error.__class__.__name__}: {error}",
        )

    action = str(calendar_intent.get("intent") or baseline.action)
    return ParserOutput(
        status="ok",
        route="calendar",
        action=action,
        confidence=_safe_float(ai_fields.get("confidence"), baseline.confidence),
        calendar_intent=_normalize_calendar_intent(calendar_intent),
    )


def score_output(case: CalendarParsingCase, output: ParserOutput) -> ScoredParserOutput:
    if not case.scored:
        return ScoredParserOutput(output, False, None, "unlabeled")
    if output.status in {"not_run", "error"}:
        return ScoredParserOutput(output, False, None, output.status)
    expected_route = case.expected_route or "unknown"
    expected_action = case.expected_action or "unknown"
    if output.route != expected_route:
        return ScoredParserOutput(output, True, False, f"route {output.route} != {expected_route}")
    if output.action != expected_action:
        return ScoredParserOutput(output, True, False, f"action {output.action} != {expected_action}")
    if expected_route not in CALENDAR_ROUTES:
        return ScoredParserOutput(output, True, True, "route/action match")

    slot_failure = _slot_failure(case.expected_slots, output.calendar_intent)
    if slot_failure is not None:
        return ScoredParserOutput(output, True, False, slot_failure)

    actionable_failure = _actionable_failure(output.action, output.calendar_intent)
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
    module = _calendar_intent_module()
    slots = ai_fields.get("slots") if isinstance(ai_fields.get("slots"), dict) else {}
    action = str(ai_fields.get("action") or "create_event")
    reference = module._default_now(now)
    intent: dict[str, Any] = {
        "intent": action,
        "missing_fields": [str(value) for value in ai_fields.get("missing_fields") or [] if value],
        "ai_field_extraction": {
            "confidence": ai_fields.get("confidence"),
            "missing_fields": [str(value) for value in ai_fields.get("missing_fields") or [] if value],
            "normalized_request": request,
        },
    }

    title = _clean_optional_string(slots.get("title"))
    if title is not None:
        intent["title"] = title[:1].upper() + title[1:]
    date_text = _clean_optional_string(slots.get("date")) or _clean_optional_string(slots.get("date_text"))
    if date_text is not None:
        parsed_date = module._iso_date_from_slot(date_text, reference)
        if parsed_date:
            intent["date"] = parsed_date
    time_text = _clean_optional_string(slots.get("start_time")) or _clean_optional_string(slots.get("time_text"))
    if time_text is not None:
        parsed_time = module._time_from_slot(time_text)
        if parsed_time:
            intent["start_time"] = parsed_time
    duration = _safe_int(slots.get("duration_minutes"))
    if duration is not None:
        intent["duration_minutes"] = duration
    if slots.get("all_day") is True:
        intent["all_day"] = True
    recurrence = slots.get("recurrence")
    if isinstance(recurrence, list):
        recurrence_values = [str(value) for value in recurrence if str(value).strip()]
        if recurrence_values:
            intent["recurrence"] = recurrence_values
    calendar_name = _clean_optional_string(slots.get("calendar_name"))
    if calendar_name is not None:
        intent["target_calendar"] = calendar_name
    for slot_key, intent_key in (
        ("target_reference", "query"),
        ("location", "location"),
        ("description", "description"),
    ):
        value = _clean_optional_string(slots.get(slot_key))
        if value is not None:
            intent[intent_key] = value
            if slot_key == "target_reference":
                intent["target_reference"] = value

    aliases = slots.get("guest_aliases")
    if isinstance(aliases, list):
        guest_aliases = _canonical_guest_aliases([str(alias) for alias in aliases if str(alias).strip()])
        attendees, missing_contacts = module.guest_contact_state_from_aliases(guest_aliases)
        intent["guest_aliases"] = guest_aliases
        if attendees:
            intent["attendees"] = attendees
        if missing_contacts and not guest_aliases:
            intent["missing_guest_contacts"] = missing_contacts

    _repair_ai_calendar_intent(
        intent,
        request,
        now=now,
        baseline_intent=baseline_intent,
    )
    _refresh_missing_fields(intent)
    return intent


def _calendar_api_context_prompt() -> str:
    return (
        "You parse family-calendar requests into a Google Calendar v3-style draft. "
        "Return only compact JSON. Do not call tools, create events, invent calendar ids, "
        "or include raw email addresses. Google Calendar event creation uses events.insert "
        "with a calendarId plus an event resource; timed events use start.dateTime and "
        "end.dateTime with timeZone, while all-day events use start.date and end.date. "
        "Event resource fields relevant here include summary, description, location, "
        "start, end, attendees, recurrence, and extendedProperties.private. Attendees "
        "normally contain email addresses, but this experiment must use aliases only "
        "for mom, dad, family, parents, or both. Notification intent maps to sendUpdates, "
        "usually all when guests are present and none otherwise. For list requests, use "
        "operation list_events with a time window. For family planning summaries, use "
        "operation family_briefing. Use operation clarify when required date, time, "
        "event target, or guest contact information is missing. Resolve relative dates "
        "against the provided reference_time and America/Los_Angeles. If the user says "
        "morning, afternoon, evening, or night without a clock time, mark time missing "
        "instead of guessing. For bare clock numbers like 'at 3', infer the most likely "
        "AM/PM from ordinary family scheduling context and confidence. Preserve user "
        "meaning over literal filler words in summary."
    )


def _calendar_api_context_schema() -> dict[str, Any]:
    return {
        "operation": "create_event | list_events | family_briefing | clarify",
        "confidence": "number 0..1",
        "calendar": {
            "name": "optional user-visible calendar name",
            "id_hint": "optional explicit calendar id only if provided by user",
        },
        "event": {
            "summary": "event title",
            "description": "optional notes",
            "location": "optional location",
            "start": {
                "dateTime": "RFC3339 timestamp for timed events",
                "date": "YYYY-MM-DD for all-day events",
                "timeZone": "America/Los_Angeles",
            },
            "end": {
                "dateTime": "RFC3339 timestamp for timed events",
                "date": "YYYY-MM-DD for all-day events",
                "timeZone": "America/Los_Angeles",
            },
            "attendees": [{"alias": "mom | dad | family | parents | both"}],
            "recurrence": ["optional RRULE strings only when explicit"],
        },
        "list": {
            "timeMin": "RFC3339 inclusive lower bound",
            "timeMax": "RFC3339 exclusive upper bound",
        },
        "sendUpdates": "all | externalOnly | none",
        "missing_fields": "array of title/date/time/event/guest_contacts/guests",
        "clarification_question": "optional concise question",
    }


def _repair_ai_calendar_intent(
    intent: dict[str, Any],
    request: str,
    *,
    now: datetime | None,
    baseline_intent: dict[str, Any] | None,
) -> dict[str, Any]:
    if not intent:
        return intent
    module = _calendar_intent_module()
    reference = module._default_now(now)
    body = _calendar_command_body(request)
    current_action = str(intent.get("intent") or "create_event")
    action = _calendar_action_from_text(body, fallback=current_action)
    intent["intent"] = action
    target_calendar = _target_calendar_from_text(body)

    if action == "family_briefing":
        intent["missing_fields"] = []
        return intent

    if action == "list_events":
        if target_calendar:
            intent["target_calendar"] = target_calendar
        if not intent.get("date"):
            list_date = _calendar_date_from_text(body, reference, prefer_current_weekday=True)
            if list_date:
                intent["date"] = list_date
        intent["missing_fields"] = []
        return intent

    if action == "add_guests":
        if target_calendar:
            intent["target_calendar"] = target_calendar
        guest_update = _guest_update_from_text(body)
        if guest_update is not None:
            module = _calendar_intent_module()
            guest_aliases = _canonical_guest_aliases(guest_update["guest_aliases"])
            attendees, missing_contacts = module.guest_contact_state_from_aliases(guest_aliases)
            intent["guest_aliases"] = guest_update["guest_aliases"]
            if attendees:
                intent["attendees"] = attendees
            if missing_contacts and not guest_aliases:
                intent["missing_guest_contacts"] = missing_contacts
            if guest_update.get("target_reference"):
                intent["query"] = guest_update["target_reference"]
                intent["target_reference"] = guest_update["target_reference"]
        intent["missing_fields"] = []
        return intent

    if baseline_intent:
        for key in (
            "title",
            "date",
            "start_time",
            "duration_minutes",
            "target_calendar",
            "location",
            "description",
        ):
            if not intent.get(key) and baseline_intent.get(key):
                intent[key] = baseline_intent[key]

    event_date = _calendar_date_from_text(body, reference, prefer_current_weekday=False)
    if event_date and (not intent.get("date") or _has_explicit_next_weekday(body)):
        intent["date"] = event_date
    if not intent.get("start_time"):
        parsed_time = module._time_from_slot(body)
        if parsed_time:
            intent["start_time"] = parsed_time
    if target_calendar:
        intent["target_calendar"] = target_calendar
    if _is_all_day_request(body):
        intent["all_day"] = True
        intent["missing_fields"] = [
            field
            for field in intent.get("missing_fields") or []
            if str(field) != "time"
        ]
    if not intent.get("location"):
        location = _location_from_text(body)
        if location:
            intent["location"] = location
    recurrence = _recurrence_from_text(body)
    if recurrence and (
        _has_monthly_ordinal_recurrence(recurrence)
        or _recurrence_day_count(recurrence) > _recurrence_day_count(intent.get("recurrence"))
    ):
        intent["recurrence"] = recurrence
    current_description = _clean_optional_string(intent.get("description"))
    if current_description:
        intent["description"] = _strip_description_label(current_description)
    else:
        description = _description_from_text(body)
        if description:
            intent["description"] = description
    title = _title_from_calendar_text(body)
    current_title = _clean_optional_string(intent.get("title"))
    if title and (
        current_title is None
        or "event" in {str(field) for field in intent.get("missing_fields") or []}
        or _should_replace_title(current_title, title)
    ):
        intent["title"] = title
        intent["missing_fields"] = [
            field
            for field in intent.get("missing_fields") or []
            if str(field) not in {"event", "title"}
        ]
    if not intent.get("guest_aliases"):
        guest_aliases = _guest_aliases_from_text(body)
        if guest_aliases:
            attendees, missing_contacts = module.guest_contact_state_from_aliases(guest_aliases)
            intent["guest_aliases"] = guest_aliases
            if attendees:
                intent["attendees"] = attendees
            if missing_contacts and not guest_aliases:
                intent["missing_guest_contacts"] = missing_contacts
    if intent.get("guest_aliases"):
        intent.pop("missing_guest_contacts", None)
        intent["missing_fields"] = [
            field
            for field in intent.get("missing_fields") or []
            if str(field) != "guest_contacts"
        ]
    return intent


def _calendar_command_body(request: str) -> str:
    explicit_route = parse_explicit_route(request)
    if explicit_route is not None:
        return explicit_route.body
    return request


def _is_all_day_request(value: str) -> bool:
    return re.search(r"\ball\s+day\b", value, flags=re.IGNORECASE) is not None


def _calendar_action_from_text(value: str, *, fallback: str) -> str:
    text = value.lower()
    if _guest_update_from_text(value) is not None:
        return "add_guests"
    if _has_create_command(text) and fallback in {"list_events", "family_briefing"}:
        return "create_event"
    if re.search(r"\b(?:briefing|brief|summary)\b", text):
        return "family_briefing"
    if re.search(r"\bplans?\s+for\b", text) and not re.search(r"\b(?:add|create|schedule|put)\b", text):
        return "family_briefing"
    if re.search(r"\b(?:what'?s|what\s+is|show|list|any)\b", text) and re.search(
        r"\b(?:calendar|events?|stuff|coming\s+up)\b",
        text,
    ) and not _has_create_command(text):
        return "list_events"
    return fallback


def _has_create_command(value: str) -> bool:
    return re.search(
        r"^\s*(?:please\s+|pls\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+)?"
        r"(?:creating|create|add|schedule|put|block)\b",
        value,
        flags=re.IGNORECASE,
    ) is not None


def _calendar_date_from_text(
    value: str,
    reference: datetime,
    *,
    prefer_current_weekday: bool,
) -> str | None:
    text = value.lower()
    if re.search(r"\b(?:today|tonight)\b", text):
        return reference.date().isoformat()
    if re.search(r"\btomorrow(?:'?s)?\b", text):
        return (reference + timedelta(days=1)).date().isoformat()

    month_name_pattern = _calendar_intent_module().MONTH_NAME_PATTERN
    month_name_match = re.search(
        rf"\b(?P<month>{month_name_pattern})\.?\s+"
        r"(?P<day>\d{1,2})(?:st|nd|rd|th)?"
        r"(?:,?\s+(?P<year>\d{2,4}))?\b",
        text,
    )
    if month_name_match is not None:
        return _date_for_month_day_text(
            month_name_match.group("month").rstrip("."),
            month_name_match.group("day"),
            month_name_match.group("year"),
            reference,
        )

    numeric_date_match = re.search(
        r"(?<!\d)(?P<month>\d{1,2})/(?P<day>\d{1,2})"
        r"(?:/(?P<year>\d{2,4}))?(?!\d)",
        text,
    )
    if numeric_date_match is not None:
        return _date_for_month_day_text(
            numeric_date_match.group("month"),
            numeric_date_match.group("day"),
            numeric_date_match.group("year"),
            reference,
        )

    module = _calendar_intent_module()
    weekday_names = {
        **module.WEEKDAYS,
        "mon": module.WEEKDAYS["monday"],
        "tue": module.WEEKDAYS["tuesday"],
        "tues": module.WEEKDAYS["tuesday"],
        "wed": module.WEEKDAYS["wednesday"],
        "thu": module.WEEKDAYS["thursday"],
        "thur": module.WEEKDAYS["thursday"],
        "thurs": module.WEEKDAYS["thursday"],
        "fri": module.WEEKDAYS["friday"],
        "sat": module.WEEKDAYS["saturday"],
        "sun": module.WEEKDAYS["sunday"],
    }
    for name, weekday in sorted(weekday_names.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"\bnext\s+{name}\b", text) or re.search(rf"\b{name}\s+next\b", text):
            return module._weekday_in_next_calendar_week(reference, weekday).date().isoformat()
        if re.search(rf"\b{name}\b", text):
            resolver = module._current_or_next_weekday if prefer_current_weekday else module._next_weekday
            return resolver(reference, weekday).date().isoformat()

    return None


def _has_explicit_next_weekday(value: str) -> bool:
    names = (
        "monday",
        "mon",
        "tuesday",
        "tue",
        "tues",
        "wednesday",
        "wed",
        "thursday",
        "thu",
        "thur",
        "thurs",
        "friday",
        "fri",
        "saturday",
        "sat",
        "sunday",
        "sun",
    )
    weekday_pattern = "|".join(re.escape(name) for name in names)
    return re.search(
        rf"\b(?:next\s+(?:{weekday_pattern})|(?:{weekday_pattern})\s+next)\b",
        value,
        flags=re.IGNORECASE,
    ) is not None


def _date_for_month_day_text(
    month_text: str,
    day_text: str,
    year_text: str | None,
    reference: datetime,
) -> str | None:
    module = _calendar_intent_module()
    month = module.MONTHS.get(str(month_text).lower())
    if month is None:
        try:
            month = int(month_text)
        except (TypeError, ValueError):
            return None
    try:
        day = int(day_text)
    except (TypeError, ValueError):
        return None
    return module._date_for_month_day(month, day, year_text, reference)


def _target_calendar_from_text(value: str) -> str | None:
    calendar_names = (
        "shared family",
        "nysha school",
        "navya school",
        "family",
        "kids",
        "sports",
        "work",
        "home",
        "medical",
        "personal",
        "our",
        "my",
    )
    name_pattern = "|".join(re.escape(name) for name in calendar_names)
    matches = list(
        re.finditer(
            rf"\b(?P<calendar>(?:my\s+)?(?:{name_pattern})\s+calendar)\b",
            value,
            flags=re.IGNORECASE,
        )
    )
    if not matches:
        return None
    calendar = _clean_optional_string(matches[-1].group("calendar"))
    if calendar is None:
        return None
    calendar = re.sub(r"^my\s+(?!(?:calendar)$)", "", calendar, flags=re.IGNORECASE)
    return _clean_optional_string(calendar)


def _location_from_text(value: str) -> str | None:
    labeled = re.search(
        r"\blocation\s+(?P<location>.+?)(?=$|[,.;]\s*(?:invite|notes?|description)\b)",
        value,
        flags=re.IGNORECASE,
    )
    if labeled is not None:
        return _clean_location(labeled.group("location"))

    locations = []
    for match in re.finditer(
        r"\bat\s+(?!\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b)(?P<location>.+?)(?=$|[,.;]\s*(?:invite|notes?|description)\b|\s+\b(?:to|on)\s+(?:.+?\s+)?calendar\b)",
        value,
        flags=re.IGNORECASE,
    ):
        location = _clean_location(match.group("location"))
        if location:
            locations.append(location)
    return locations[-1] if locations else None


def _clean_location(value: str) -> str | None:
    cleaned = re.sub(r"\b(?:invite|notes?|description)\b.*$", "", value, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:to|on)\s+(?:.+?\s+)?calendar\b.*$", "", cleaned, flags=re.IGNORECASE)
    return _clean_optional_string(cleaned.strip(" ,.;:"))


def _recurrence_from_text(value: str) -> list[str]:
    lowered = value.lower()
    if "every" not in lowered:
        return []
    ordinal_recurrence = _monthly_ordinal_recurrence_from_text(lowered)
    if ordinal_recurrence is not None:
        weekday_code, position = ordinal_recurrence
        return [f"RRULE:FREQ=MONTHLY;BYDAY={weekday_code};BYSETPOS={position}"]
    weekday_codes = []
    for name, code in (
        ("monday", "MO"),
        ("mon", "MO"),
        ("tuesday", "TU"),
        ("tue", "TU"),
        ("tues", "TU"),
        ("wednesday", "WE"),
        ("wed", "WE"),
        ("thursday", "TH"),
        ("thu", "TH"),
        ("thur", "TH"),
        ("thurs", "TH"),
        ("friday", "FR"),
        ("fri", "FR"),
        ("saturday", "SA"),
        ("sat", "SA"),
        ("sunday", "SU"),
        ("sun", "SU"),
    ):
        if re.search(rf"\b{name}\b", lowered) and code not in weekday_codes:
            weekday_codes.append(code)
    if not weekday_codes:
        return []
    return [f"RRULE:FREQ=WEEKLY;BYDAY={','.join(weekday_codes)}"]


def _monthly_ordinal_recurrence_from_text(value: str) -> tuple[str, int] | None:
    module = _calendar_intent_module()
    ordinal_positions = {
        "first": 1,
        "1st": 1,
        "second": 2,
        "2nd": 2,
        "third": 3,
        "3rd": 3,
        "fourth": 4,
        "4th": 4,
        "last": -1,
    }
    weekday_names = {
        **module.WEEKDAYS,
        "mon": module.WEEKDAYS["monday"],
        "tue": module.WEEKDAYS["tuesday"],
        "tues": module.WEEKDAYS["tuesday"],
        "wed": module.WEEKDAYS["wednesday"],
        "thu": module.WEEKDAYS["thursday"],
        "thur": module.WEEKDAYS["thursday"],
        "thurs": module.WEEKDAYS["thursday"],
        "fri": module.WEEKDAYS["friday"],
        "sat": module.WEEKDAYS["saturday"],
        "sun": module.WEEKDAYS["sunday"],
    }
    ordinal_pattern = "|".join(
        re.escape(name)
        for name in sorted(ordinal_positions, key=len, reverse=True)
    )
    weekday_pattern = "|".join(
        re.escape(name)
        for name in sorted(weekday_names, key=len, reverse=True)
    )
    match = re.search(
        rf"\bevery\s+(?P<ordinal>{ordinal_pattern})\s+(?P<weekday>{weekday_pattern})\b",
        value,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    weekday = weekday_names[match.group("weekday").lower()]
    weekday_name = next(name for name, day in module.WEEKDAYS.items() if day == weekday)
    return module.WEEKDAY_RRULE_CODES[weekday_name], ordinal_positions[match.group("ordinal").lower()]


def _recurrence_day_count(value: Any) -> int:
    if not isinstance(value, list):
        return 0
    days = set()
    for item in value:
        match = re.search(r"BYDAY=([A-Z,]+)", str(item).upper())
        if match is None:
            continue
        days.update(day for day in match.group(1).split(",") if day)
    return len(days)


def _has_monthly_ordinal_recurrence(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    return any(
        "FREQ=MONTHLY" in str(item).upper() and "BYSETPOS=" in str(item).upper()
        for item in value
    )


def _description_from_text(value: str) -> str | None:
    match = re.search(
        r"\b(?:notes?|description)\s*:?\s*(?P<description>.+?)\s*$",
        value,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return _clean_optional_string(match.group("description").strip(" ,.;:"))


def _strip_description_label(value: str) -> str:
    return _clean_optional_string(
        re.sub(r"^\s*(?:notes?|description)\s*:?\s*", "", value, flags=re.IGNORECASE).strip(" ,.;:")
    ) or ""


def _title_from_calendar_text(value: str) -> str | None:
    title = re.sub(r"^\s*(?:add|create|schedule|put|block)\s+", "", value, flags=re.IGNORECASE)
    title = re.split(
        r"\b(?:today|tomorrow|tonight|next\s+\w+|this\s+\w+|every\s+(?:\w+\s+)?\w+|on\s+"
        rf"(?:{_calendar_intent_module().MONTH_NAME_PATTERN})|\d{{1,2}}/\d{{1,2}}|"
        r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\.?\s+\d{1,2}|"
        r"at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?|all\s+day|starting\s+|through\s+|to\s+.+?\s+calendar|on\s+.+?\s+calendar|notes?:|description)\b",
        title,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return _clean_optional_string(title.strip(" ,.;:-"))


def _should_replace_title(current: str, candidate: str) -> bool:
    current_key = _clean_optional_string(current)
    candidate_key = _clean_optional_string(candidate)
    if current_key is None or candidate_key is None:
        return False
    current_key = current_key.lower()
    candidate_key = candidate_key.lower()
    if current_key == candidate_key or not current_key.startswith(f"{candidate_key} "):
        return False
    return re.search(
        r"\b(?:all\s+day|every|starting|notes?|description)\b",
        current_key,
    ) is not None


def _guest_aliases_from_text(value: str) -> list[str]:
    module = _calendar_intent_module()
    guest_text = re.sub(
        r"\b(?:shared\s+)?family\s+calendar\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    keys = module._guest_keys_from_text(guest_text)
    return _canonical_guest_aliases(keys)


def _guest_update_from_text(value: str) -> dict[str, Any] | None:
    module = _calendar_intent_module()
    match = re.search(
        rf"\b(?:add|invite)\s+(?:guests?\s+)?"
        rf"(?P<guests>(?:{module.GUEST_ALIAS_PATTERN})(?:\s*(?:,|and|&|\+|/|or)\s*(?:{module.GUEST_ALIAS_PATTERN}))*)"
        r"\s+(?:as\s+guests?\s+)?to\s+(?:the\s+|that\s+)?"
        r"(?P<target>.*?\b(?:invite|invitation|event)\b)",
        value,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    aliases = [
        alias
        for alias in re.split(r"\s*(?:,|and|&|\+|/|or)\s*", match.group("guests"), flags=re.IGNORECASE)
        if alias.strip()
    ]
    if not aliases:
        return None
    result: dict[str, Any] = {"guest_aliases": aliases}
    target_reference = _guest_target_reference_from_text(value[match.end() :])
    if target_reference:
        result["target_reference"] = target_reference
    return result


def _guest_target_reference_from_text(value: str) -> str | None:
    match = re.search(r"\bfor\s+(?P<target>.+?)\s*$", value, flags=re.IGNORECASE)
    if match is None:
        return None
    return _clean_optional_string(match.group("target").strip(" ,.;:"))


def _canonical_guest_aliases(aliases: list[str]) -> list[str]:
    module = _calendar_intent_module()
    keys = []
    seen = set()
    for alias in aliases:
        for key in module.GUEST_NAME_ALIASES.get(module._alias_key(str(alias)), []):
            if key in seen:
                continue
            seen.add(key)
            keys.append(key)
    return keys


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
        raise ValueError("Calendar API context extraction returned non-object JSON")
    return parsed


def _api_context_fields_to_legacy_fields(raw: dict[str, Any], request: str) -> dict[str, Any]:
    operation = _operation_to_legacy_action(_clean_optional_string(raw.get("operation")))
    confidence = _safe_float(raw.get("confidence"), 0.0)
    if confidence < 0.8:
        raise ValueError("Calendar API context extraction confidence below threshold")

    slots: dict[str, Any] = {}
    event = raw.get("event") if isinstance(raw.get("event"), dict) else {}
    calendar = raw.get("calendar") if isinstance(raw.get("calendar"), dict) else {}
    event = event if isinstance(event, dict) else {}
    calendar = calendar if isinstance(calendar, dict) else {}

    summary = _clean_optional_string(event.get("summary"))
    if summary:
        slots["title"] = summary
    calendar_name = _clean_optional_string(calendar.get("name")) or _clean_optional_string(calendar.get("id_hint"))
    if calendar_name:
        slots["calendar_name"] = calendar_name
    for api_key, slot_key in (("location", "location"), ("description", "description")):
        value = _clean_optional_string(event.get(api_key))
        if value:
            slots[slot_key] = value

    start = event.get("start") if isinstance(event.get("start"), dict) else {}
    end = event.get("end") if isinstance(event.get("end"), dict) else {}
    start = start if isinstance(start, dict) else {}
    end = end if isinstance(end, dict) else {}
    start_dt = _parse_datetime(start.get("dateTime"))
    end_dt = _parse_datetime(end.get("dateTime"))
    if start_dt is not None:
        slots["date"] = start_dt.date().isoformat()
        slots["start_time"] = f"{start_dt.hour:02d}:{start_dt.minute:02d}"
    elif _clean_optional_string(start.get("date")):
        slots["date"] = _clean_optional_string(start.get("date"))
        slots["all_day"] = True
    if start_dt is not None and end_dt is not None and end_dt > start_dt:
        duration = int((end_dt - start_dt).total_seconds() / 60)
        if 1 <= duration <= 1440:
            slots["duration_minutes"] = duration

    list_window = raw.get("list") if isinstance(raw.get("list"), dict) else {}
    list_window = list_window if isinstance(list_window, dict) else {}
    list_start = _parse_datetime(list_window.get("timeMin"))
    if operation == "list_events" and list_start is not None:
        slots["date"] = list_start.date().isoformat()

    attendees = event.get("attendees")
    if isinstance(attendees, list):
        aliases = []
        for attendee in attendees:
            if not isinstance(attendee, dict):
                continue
            alias = _clean_optional_string(attendee.get("alias"))
            if alias:
                aliases.append(alias.lower())
        if aliases:
            slots["guest_aliases"] = aliases

    recurrence = event.get("recurrence")
    if isinstance(recurrence, list):
        recurrence_values = [str(value) for value in recurrence if str(value).strip()]
        if recurrence_values:
            slots["recurrence"] = recurrence_values

    missing_fields = [
        field
        for field in (str(value).strip() for value in raw.get("missing_fields") or [])
        if field
    ]
    return {
        "action": operation,
        "confidence": confidence,
        "slots": slots,
        "missing_fields": missing_fields,
        "clarification_question": _clean_optional_string(raw.get("clarification_question")),
        "normalized_request": request,
    }


def _operation_to_legacy_action(operation: str | None) -> str:
    if operation in {"create_event", "list_events", "family_briefing", "add_guests"}:
        return operation
    if operation == "clarify":
        return "create_event"
    return "create_event"


def _refresh_missing_fields(intent: dict[str, Any]) -> None:
    action = str(intent.get("intent") or "")
    missing = [
        str(value)
        for value in intent.get("missing_fields") or []
        if value
        and not (
            (value == "title" and intent.get("title"))
            or (value == "date" and intent.get("date"))
            or (value == "time" and (intent.get("start_time") or intent.get("all_day")))
            or (value == "guest_contacts" and intent.get("guest_aliases"))
            or (value == "guests" and (intent.get("guest_aliases") or intent.get("attendees")))
        )
    ]
    if action == "create_event":
        required_fields = [("title", "title"), ("date", "date")]
        if not intent.get("all_day"):
            required_fields.append(("time", "start_time"))
        for field_name, key in required_fields:
            if not intent.get(key) and field_name not in missing:
                missing.append(field_name)
        if (
            intent.get("missing_guest_contacts")
            and not intent.get("guest_aliases")
            and "guest_contacts" not in missing
        ):
            missing.append("guest_contacts")
    elif action == "add_guests":
        if intent.get("missing_guest_contacts") and "guest_contacts" not in missing:
            missing.append("guest_contacts")
        if (
            not intent.get("attendees")
            and not intent.get("guest_aliases")
            and not intent.get("missing_guest_contacts")
            and "guests" not in missing
        ):
            missing.append("guests")
    elif action in {"update_event", "delete_event"} and not intent.get("query"):
        if "event" not in missing:
            missing.append("event")
    intent["missing_fields"] = missing


def _normalize_calendar_intent(intent: dict[str, Any]) -> dict[str, Any]:
    if not intent:
        return {}
    keys = (
        "intent",
        "title",
        "date",
        "start_time",
        "duration_minutes",
        "all_day",
        "target_calendar",
        "target_reference",
        "query",
        "location",
        "description",
        "recurrence",
        "guest_aliases",
        "attendees",
        "missing_guest_contacts",
        "missing_fields",
        "ai_field_extraction",
    )
    return {key: intent[key] for key in keys if key in intent and intent[key] not in (None, "", [])}


def _actionable_failure(action: str, intent: dict[str, Any]) -> str | None:
    missing = [str(value) for value in intent.get("missing_fields") or [] if value]
    if action not in MUTATING_CALENDAR_ACTIONS:
        return None
    if missing:
        return f"missing {', '.join(missing)}"
    if action == "create_event":
        required = [field for field in ("title", "date") if not intent.get(field)]
        if not intent.get("all_day") and not intent.get("start_time"):
            required.append("start_time")
        if required:
            return f"missing {', '.join(required)}"
    if action == "add_guests" and not (intent.get("attendees") or intent.get("guest_aliases")):
        return "missing guests"
    if action in {"update_event", "delete_event"} and not intent.get("query"):
        return "missing event"
    return None


def _slot_failure(expected_slots: dict[str, Any], actual: dict[str, Any]) -> str | None:
    for key, expected in expected_slots.items():
        actual_value = actual.get(key)
        if key == "guest_aliases":
            actual_values = _canonical_guest_aliases([str(value) for value in actual_value or []])
            expected_values = _canonical_guest_aliases([str(value) for value in expected or []])
            if sorted(actual_values) != sorted(expected_values):
                return f"slot {key} mismatch"
        elif key == "recurrence":
            if _canonical_recurrence_values(actual_value) != _canonical_recurrence_values(expected):
                return f"slot {key} mismatch"
        elif isinstance(expected, list):
            actual_values = sorted({str(value).lower() for value in actual_value or []})
            expected_values = sorted({str(value).lower() for value in expected})
            if actual_values != expected_values:
                return f"slot {key} mismatch"
        elif str(actual_value or "").lower() != str(expected or "").lower():
            return f"slot {key} {actual_value!r} != {expected!r}"
    return None


def _canonical_recurrence_values(value: Any) -> list[tuple[tuple[str, str], ...]]:
    if not isinstance(value, list):
        return []
    canonical = []
    for item in value:
        text = str(item or "").upper().strip()
        text = text.removeprefix("RRULE:")
        parts: dict[str, str] = {}
        for token in text.split(";"):
            if "=" not in token:
                continue
            key, raw_value = token.split("=", 1)
            if key == "BYDAY":
                raw_value = ",".join(sorted(day for day in raw_value.split(",") if day))
            parts[key] = raw_value
        canonical.append(tuple(sorted(parts.items())))
    return sorted(canonical)


def _trajectory_cases_from_text(text: str, month: str) -> tuple[CalendarParsingCase, ...]:
    cases: list[CalendarParsingCase] = []
    current_heading = ""
    current_source = ""
    current_user_lines: list[str] = []
    in_user = False

    def flush() -> None:
        nonlocal current_heading, current_source, current_user_lines
        user_text = _clean_block("\n".join(current_user_lines))
        if current_source.startswith(TELEGRAM_SOURCE_PREFIX) and user_text:
            captured_at = _parse_datetime(current_heading)
            cases.append(
                CalendarParsingCase(
                    case_id=f"trajectory-{month}-{_stable_case_id(current_heading, user_text)}",
                    utterance=user_text,
                    source=current_source,
                    origin="trajectory",
                    captured_at=captured_at,
                )
            )
        current_user_lines = []

    for line in text.splitlines():
        if line.startswith("## "):
            flush()
            current_heading = line.removeprefix("## ").strip()
            current_source = ""
            in_user = False
            continue
        if line.startswith("- Source: "):
            current_source = line.removeprefix("- Source: ").strip()
            continue
        if line.strip() == "User:":
            current_user_lines = []
            in_user = True
            continue
        if line.strip() == "Assistant:":
            in_user = False
            continue
        if in_user:
            current_user_lines.append(line[2:] if line.startswith("  ") else line)
    flush()
    return tuple(cases)


def _runtime_request(request: str) -> str:
    return request if parse_explicit_route(request) is not None else improve_entered_text(request)


def _dedupe_cases(cases: list[CalendarParsingCase]) -> tuple[CalendarParsingCase, ...]:
    deduped: dict[str, CalendarParsingCase] = {}
    for case in cases:
        key = re.sub(r"\s+", " ", case.utterance.lower()).strip()
        existing = deduped.get(key)
        if existing is None or (case.scored and not existing.scored):
            deduped[key] = case
    return tuple(deduped.values())


def _table_columns(db: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f"pragma table_info({_quote_identifier(table_name)})")}


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _clean_block(value: Any) -> str:
    return dedent(str(value or "")).strip()


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
    if 1 <= parsed <= 1440:
        return parsed
    return None


def _stable_case_id(prefix: str, text: str) -> str:
    digest = hashlib.sha1(f"{prefix}\n{text}".encode("utf-8")).hexdigest()[:12]
    return digest


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


def _build_live_extractor() -> CalendarFieldExtractor | None:
    calendar_module_path = DEFAULT_REPO_ROOT / "claws" / "family-calendar"
    import sys

    original_path = list(sys.path)
    sys.path.insert(0, str(calendar_module_path))
    try:
        from ai_field_extraction import CalendarAIFieldExtractor

        return CalendarAIFieldExtractor.from_env()
    finally:
        sys.path[:] = original_path


def _build_api_context_extractor() -> CalendarFieldExtractor:
    return CalendarApiContextFieldExtractor.from_env()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate N4OS Telegram calendar parsing strategies.")
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--include-cases", action="store_true")
    parser.add_argument("--live-ai", action="store_true")
    parser.add_argument("--api-context-ai", action="store_true")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    args = parser.parse_args(argv)

    if args.live_ai and args.api_context_ai:
        parser.error("--live-ai and --api-context-ai are mutually exclusive")
    extractor = None
    if args.live_ai:
        extractor = _build_live_extractor()
    elif args.api_context_ai:
        extractor = _build_api_context_extractor()
    cache = CalendarAIFieldCache(args.cache) if extractor is not None else None
    report = run_experiment(
        load_local_cases(args.repo_root),
        extractor=extractor,
        cache=cache,
    )
    print(json.dumps(report.to_dict(include_cases=args.include_cases), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

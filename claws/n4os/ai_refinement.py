from __future__ import annotations

from datetime import datetime
import json
import os
import re
from typing import Any, Callable
import urllib.error
import urllib.request


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_N4OS_INTENT_REFINEMENT_MODEL = "gpt-5.4-mini"
DEFAULT_TIMEOUT_SECONDS = 8
LOW_CONFIDENCE_THRESHOLD = 0.6
MAX_NORMALIZED_REQUEST_CHARS = 1200

VALID_ACTIONS_BY_ROUTE = {
    "calendar": {
        "create_event",
        "list_events",
        "update_event",
        "delete_event",
        "family_briefing",
        "preparation_checklist",
    },
    "tasks": {
        "create_task",
        "recommend_tasks",
        "update_task",
        "complete_task",
        "delete_task",
        "run_assistant_help",
    },
    "home_board": {"add_item", "add_items", "list_items", "mark_done"},
    "decisions": {
        "create_decision",
        "list_decisions",
        "decision_brief",
        "add_option",
        "add_evidence",
        "add_next_step",
        "record_decision",
        "bulk_record_decisions",
    },
    "both": {"combined_planning", "calendar_and_tasks"},
    "unknown": {"unknown"},
}
VALID_ROUTES = set(VALID_ACTIONS_BY_ROUTE)
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

UNSAFE_NORMALIZED_PATTERNS = (
    re.compile(r"\b(?:ignore|disregard)\s+(?:all\s+)?(?:previous|system)\s+instructions?\b", re.I),
    re.compile(r"\b(?:system|developer)\s+prompt\b", re.I),
    re.compile(r"\b(?:tool_call|function_call|assistant to=|functions\.)\b", re.I),
    re.compile(r"\b[A-Z][A-Z0-9_]{4,}\s*=\s*['\"]?[^'\"\s]+", re.I),
    re.compile(r"\b(?:OPENAI|ANTHROPIC|GOOGLE|GEMINI|GITHUB|SLACK|TELEGRAM)_[A-Z0-9_]*(?:KEY|TOKEN)\b"),
)

UrlOpen = Callable[..., Any]


def _clean_string(value: Any) -> str:
    return str(value or "").strip()


def _round_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(confidence, 1.0)), 2)


def _clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (_clean_string(item) for item in value) if item]


def _clean_record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _extract_response_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    chunks = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
    return "\n".join(chunks).strip()


def _json_object_from_text(value: str) -> dict[str, Any]:
    cleaned = value.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("AI refinement returned non-object JSON")
    return parsed


def _validate_normalized_request(value: Any) -> str:
    normalized = _clean_string(value)
    if not normalized:
        raise ValueError("AI refinement returned an empty normalized_request")
    if len(normalized) > MAX_NORMALIZED_REQUEST_CHARS:
        raise ValueError("AI refinement returned an oversized normalized_request")
    for pattern in UNSAFE_NORMALIZED_PATTERNS:
        if pattern.search(normalized):
            raise ValueError("AI refinement returned unsafe normalized_request text")
    return normalized


def validate_ai_intent_frame(raw: dict[str, Any], request: str) -> dict[str, Any]:
    route = _clean_string(raw.get("route")) or "unknown"
    if route not in VALID_ROUTES:
        raise ValueError(f"AI refinement returned invalid route: {route}")

    action = _clean_string(raw.get("action")) or "unknown"
    if action not in VALID_ACTIONS_BY_ROUTE[route]:
        raise ValueError(f"AI refinement returned invalid action for {route}: {action}")

    followup_kind = _clean_string(raw.get("followup_kind")) or "none"
    if followup_kind not in VALID_FOLLOWUP_KINDS:
        raise ValueError(f"AI refinement returned invalid followup_kind: {followup_kind}")

    normalized_request = _validate_normalized_request(raw.get("normalized_request") or request)
    confidence = _round_confidence(raw.get("confidence"))
    if confidence < LOW_CONFIDENCE_THRESHOLD:
        raise ValueError("AI refinement confidence below routing threshold")

    return {
        "route": route,
        "action": action,
        "confidence": confidence,
        "followup_kind": followup_kind,
        "target": _clean_record(raw.get("target")),
        "slots": _clean_record(raw.get("slots")),
        "missing_fields": _clean_string_list(raw.get("missing_fields")),
        "normalized_request": normalized_request,
        "clarification_question": _clean_string(raw.get("clarification_question")) or None,
    }


def _reference_time_text(now: datetime | None) -> str:
    if now is None:
        return "not provided"
    if now.tzinfo is None:
        return now.isoformat()
    return now.astimezone().isoformat()


def _system_prompt() -> str:
    return (
        "You are the N4OS intent refinement layer. Return only compact JSON. "
        "Normalize household requests into commands for existing local parsers; "
        "do not execute actions, call tools, invent external facts, or include secrets. "
        "Allowed routes/actions: calendar(create_event,list_events,update_event,delete_event,"
        "family_briefing,preparation_checklist); tasks(create_task,recommend_tasks,update_task,"
        "complete_task,delete_task,run_assistant_help); home_board(add_item,add_items,list_items,"
        "mark_done); decisions(create_decision,list_decisions,decision_brief,add_option,"
        "add_evidence,add_next_step,record_decision,bulk_record_decisions); "
        "both(combined_planning,calendar_and_tasks); unknown(unknown). "
        "For create/update requests, clean titles/messages, convert relative dates using the "
        "reference time, preserve explicit owner/person/assistant-help intent, and put the result "
        "in normalized_request as a natural command the local parser can understand. For tasks "
        "with long dictated context, use this shape: `Add task: short readable title` followed by "
        "`Notes: readable supporting body`; keep assistant research requests as assistant-help "
        "text, not as the title."
    )


class OpenAIN4OSIntentInterpreter:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_N4OS_INTENT_REFINEMENT_MODEL,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        urlopen: UrlOpen = urllib.request.urlopen,
    ):
        cleaned_key = api_key.strip()
        if not cleaned_key:
            raise RuntimeError("N4OS AI refinement needs OPENAI_API_KEY.")
        self.api_key = cleaned_key
        self.model = model.strip() or DEFAULT_N4OS_INTENT_REFINEMENT_MODEL
        self.timeout_seconds = timeout_seconds
        self.urlopen = urlopen

    @classmethod
    def from_env(cls) -> "OpenAIN4OSIntentInterpreter":
        return cls(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            model=os.environ.get(
                "N4OS_INTENT_REFINEMENT_MODEL",
                DEFAULT_N4OS_INTENT_REFINEMENT_MODEL,
            ),
        )

    @classmethod
    def from_env_or_none(cls) -> "OpenAIN4OSIntentInterpreter | None":
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            return None
        return cls.from_env()

    def interpret(
        self,
        request: str,
        *,
        now: datetime | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = {
            "model": self.model,
            "store": False,
            "max_output_tokens": 450,
            "input": [
                {"role": "system", "content": _system_prompt()},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "request": request,
                            "reference_time": _reference_time_text(now),
                            "context": context or {},
                            "output_schema": {
                                "route": "string",
                                "action": "string",
                                "confidence": "number 0..1",
                                "normalized_request": "string",
                                "followup_kind": "optional string",
                                "target": "optional object",
                                "slots": "optional object",
                                "missing_fields": "optional string array",
                                "clarification_question": "optional string",
                            },
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
                "User-Agent": "n4os-intent-refinement/0.1",
            },
            method="POST",
        )

        with self.urlopen(api_request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        text = _extract_response_text(payload)
        if not text:
            raise RuntimeError("OpenAI returned no N4OS refinement text.")
        return validate_ai_intent_frame(_json_object_from_text(text), request)

from __future__ import annotations

from datetime import date, datetime
import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Callable
import urllib.request


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_TASK_AI_MODEL = "gpt-5.4-mini"
DEFAULT_TIMEOUT_SECONDS = 20
MIN_CONFIDENCE = 0.8
INTENT_REFINEMENT_ENABLED_ENV = "N4OS_INTENT_REFINEMENT_ENABLED"

VALID_ACTIONS = {
    "create_task",
    "recommend_tasks",
    "update_task",
    "complete_task",
    "delete_task",
    "run_assistant_help",
}
VALID_MISSING_FIELDS = {"title", "task", "task_list"}
VALID_ASSUMPTIONS = {
    "inferred_due_date",
    "inferred_owner",
    "image_text",
    "voice_transcript",
    "implicit_target",
}

UrlOpen = Callable[..., Any]


TASK_AI_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "operation": {"type": "string", "enum": sorted(VALID_ACTIONS)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "task_list": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title": {"type": ["string", "null"]},
                "id_hint": {"type": ["string", "null"]},
            },
            "required": ["title", "id_hint"],
        },
        "task": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title": {"type": ["string", "null"]},
                "notes": {"type": ["string", "null"]},
                "due": {"type": ["string", "null"]},
                "metadata": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "context": {"type": "array", "items": {"type": "string"}},
                        "energy": {"type": "string"},
                        "duration_minutes": {"type": ["integer", "null"], "minimum": 1},
                        "urgency": {"type": "string"},
                        "complexity": {"type": "string"},
                        "effort_type": {"type": "string"},
                        "requires": {"type": "array", "items": {"type": "string"}},
                        "can_do_while": {"type": "array", "items": {"type": "string"}},
                        "location": {"type": "string"},
                        "owner": {"type": "string"},
                        "assistant_help_needed": {"type": "boolean"},
                        "assistant_name": {"type": ["string", "null"]},
                        "assistant_help_request": {"type": ["string", "null"]},
                        "assistant_context": {"type": ["string", "null"]},
                    },
                    "required": [
                        "tags",
                        "context",
                        "energy",
                        "duration_minutes",
                        "urgency",
                        "complexity",
                        "effort_type",
                        "requires",
                        "can_do_while",
                        "location",
                        "owner",
                        "assistant_help_needed",
                        "assistant_name",
                        "assistant_help_request",
                        "assistant_context",
                    ],
                },
            },
            "required": ["title", "notes", "due", "metadata"],
        },
        "target": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"query": {"type": ["string", "null"]}},
            "required": ["query"],
        },
        "update": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title": {"type": ["string", "null"]},
                "notes": {"type": ["string", "null"]},
                "due": {"type": ["string", "null"]},
                "owner": {"type": ["string", "null"]},
                "tags": {"type": "array", "items": {"type": "string"}},
                "assistant_help_request": {"type": ["string", "null"]},
            },
            "required": ["title", "notes", "due", "owner", "tags", "assistant_help_request"],
        },
        "filters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tags": {"type": "array", "items": {"type": "string"}},
                "owner": {"type": ["string", "null"]},
                "context": {"type": "array", "items": {"type": "string"}},
                "available_resources": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "unavailable_resources": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "can_do_while": {"type": "array", "items": {"type": "string"}},
                "energy": {"type": ["string", "null"]},
                "effort_type": {"type": ["string", "null"]},
                "due_min": {"type": ["string", "null"]},
                "due_max": {"type": ["string", "null"]},
                "duration_minutes": {"type": ["integer", "null"], "minimum": 1},
            },
            "required": [
                "tags",
                "owner",
                "context",
                "available_resources",
                "unavailable_resources",
                "can_do_while",
                "energy",
                "effort_type",
                "due_min",
                "due_max",
                "duration_minutes",
            ],
        },
        "missing_fields": {"type": "array", "items": {"type": "string"}},
        "clarification_question": {"type": ["string", "null"]},
        "assumptions": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(VALID_ASSUMPTIONS)},
        },
    },
    "required": [
        "operation",
        "confidence",
        "task_list",
        "task",
        "target",
        "update",
        "filters",
        "missing_fields",
        "clarification_question",
        "assumptions",
    ],
}


def _clean_string(value: Any) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [cleaned for item in value if (cleaned := _clean_string(item))]


def _clean_due(value: Any) -> str | None:
    cleaned = _clean_string(value)
    if cleaned is None:
        return None
    try:
        return date.fromisoformat(cleaned[:10]).isoformat()
    except ValueError as error:
        raise ValueError("Task AI extraction returned an invalid due date") from error


def _response_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    chunks = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                chunks.append(content["text"].strip())
    return "\n".join(chunk for chunk in chunks if chunk)


def validate_task_ai_fields(raw: dict[str, Any], request: str) -> dict[str, Any]:
    action = _clean_string(raw.get("operation"))
    if action not in VALID_ACTIONS:
        raise ValueError(f"Task AI extraction returned invalid operation: {action}")
    try:
        confidence = float(raw.get("confidence"))
    except (TypeError, ValueError) as error:
        raise ValueError("Task AI extraction returned invalid confidence") from error
    if confidence < MIN_CONFIDENCE:
        raise ValueError("Task AI extraction confidence below threshold")

    task = raw.get("task") if isinstance(raw.get("task"), dict) else {}
    task_list = raw.get("task_list") if isinstance(raw.get("task_list"), dict) else {}
    target = raw.get("target") if isinstance(raw.get("target"), dict) else {}
    update = raw.get("update") if isinstance(raw.get("update"), dict) else {}
    normalized_update = dict(update)
    if update.get("due") is not None:
        normalized_update["due"] = _clean_due(update.get("due"))
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    missing_fields = [
        value for value in _clean_string_list(raw.get("missing_fields")) if value in VALID_MISSING_FIELDS
    ]
    assumptions = [
        value for value in _clean_string_list(raw.get("assumptions")) if value in VALID_ASSUMPTIONS
    ]
    title = _clean_string(task.get("title"))
    target_query = _clean_string(target.get("query"))
    if action in {"update_task", "complete_task", "delete_task"} and target_query is None:
        raise ValueError("Task AI extraction returned a mutation without a target query")
    if action == "create_task" and title is None and "title" not in missing_fields:
        missing_fields.append("title")
    supplied = {
        "title": title is not None,
        "task": title is not None if action == "create_task" else target_query is not None,
        "task_list": _clean_string(task_list.get("title")) is not None
        or _clean_string(task_list.get("id_hint")) is not None,
    }
    missing_fields = [field for field in missing_fields if not supplied[field]]

    return {
        "intent": action,
        "title": title,
        "notes": _clean_string(task.get("notes")),
        "due": _clean_due(task.get("due")),
        "metadata": dict(metadata),
        "task_list_name": _clean_string(task_list.get("title")),
        "task_list_id_hint": _clean_string(task_list.get("id_hint")),
        "query": target_query,
        "update": normalized_update,
        "filters": dict(raw.get("filters")) if isinstance(raw.get("filters"), dict) else {},
        "missing_fields": missing_fields,
        "clarification_question": (
            _clean_string(raw.get("clarification_question")) if missing_fields else None
        ),
        "assumptions": assumptions,
        "ai_field_extraction": {
            "confidence": round(min(confidence, 1.0), 2),
            "normalized_request": request,
        },
    }


def _system_prompt() -> str:
    return (
        "Interpret family task requests into the supplied strict schema. Do not call tools, "
        "create tasks, invent ids, or include secrets. Preserve the user's title and notes; "
        "keep dates, owners, tags, durations, and metadata out of the title. Resolve relative "
        "dates against reference_time in America/Los_Angeles. A due date is date-only. Use "
        "task-list titles exactly as the user names them. Use a target query for update, "
        "complete, and delete. Mark voice_transcript or image_text in assumptions when that "
        "modality supplied task fields. Ask one concise clarification only when a required "
        "title, target, or task list cannot be grounded. Return JSON only."
    )


class TaskAIFieldExtractor:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_TASK_AI_MODEL,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        urlopen: UrlOpen = urllib.request.urlopen,
    ):
        if not api_key.strip():
            raise RuntimeError("Task AI extraction needs OPENAI_API_KEY.")
        self.api_key = api_key.strip()
        self.model = model.strip() or DEFAULT_TASK_AI_MODEL
        self.timeout_seconds = timeout_seconds
        self.urlopen = urlopen

    @classmethod
    def from_env_or_none(cls) -> TaskAIFieldExtractor | None:
        enabled = os.environ.get(INTENT_REFINEMENT_ENABLED_ENV, "").strip().lower()
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if enabled not in {"1", "true", "yes", "on"} or not api_key:
            return None
        return cls(
            api_key=api_key,
            model=os.environ.get("N4OS_INTENT_REFINEMENT_MODEL", DEFAULT_TASK_AI_MODEL),
        )

    def extract(
        self,
        request: str,
        *,
        now: datetime | None = None,
        baseline_intent: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context_payload = {
            key: value
            for key, value in (context or {}).items()
            if key != "semantic_image_path"
        }
        user_payload = json.dumps(
            {
                "request": request,
                "reference_time": now.isoformat() if now else "not provided",
                "baseline_intent": baseline_intent or {},
                "context": context_payload,
            },
            sort_keys=True,
        )
        user_content: str | list[dict[str, str]] = user_payload
        image_path = _clean_string((context or {}).get("semantic_image_path"))
        if image_path and Path(image_path).is_file():
            path = Path(image_path)
            mime_type = mimetypes.guess_type(str(path))[0] or "image/jpeg"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            user_content = [
                {"type": "input_text", "text": user_payload},
                {
                    "type": "input_image",
                    "image_url": f"data:{mime_type};base64,{encoded}",
                    "detail": "high",
                },
            ]
        body = {
            "model": self.model,
            "store": False,
            "max_output_tokens": 1200,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "n4os_task_operation",
                    "strict": True,
                    "schema": TASK_AI_SCHEMA,
                }
            },
            "input": [
                {"role": "system", "content": _system_prompt()},
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
        }
        api_request = urllib.request.Request(
            OPENAI_RESPONSES_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "n4os-task-operation/1.0",
            },
            method="POST",
        )
        with self.urlopen(api_request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        text = _response_text(payload)
        if not text:
            raise RuntimeError("OpenAI returned no task operation text.")
        raw = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError("Task AI extraction returned non-object JSON")
        return validate_task_ai_fields(raw, request)

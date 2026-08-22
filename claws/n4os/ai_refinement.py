from __future__ import annotations

from datetime import datetime
import base64
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
from typing import Any, Callable
import urllib.error
import urllib.request

try:
    from .routing_contracts import is_valid_model_route_action, route_action_prompt
except ImportError:
    from routing_contracts import is_valid_model_route_action, route_action_prompt


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_N4OS_INTENT_REFINEMENT_MODEL = "gpt-5.4-mini"
DEFAULT_TIMEOUT_SECONDS = 8
LOW_CONFIDENCE_THRESHOLD = 0.8
INTENT_REFINEMENT_ENABLED_ENV = "N4OS_INTENT_REFINEMENT_ENABLED"
LOGGER = logging.getLogger(__name__)
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


def validate_ai_intent_frame(raw: dict[str, Any], request: str) -> dict[str, Any]:
    route = _clean_string(raw.get("route")) or "unknown"
    action = _clean_string(raw.get("action")) or "unknown"
    if not is_valid_model_route_action(route, action):
        raise ValueError(f"AI refinement returned invalid action for {route}: {action}")

    followup_kind = _clean_string(raw.get("followup_kind")) or "none"
    if followup_kind not in VALID_FOLLOWUP_KINDS:
        raise ValueError(f"AI refinement returned invalid followup_kind: {followup_kind}")

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
        # The model selects typed semantics only. Domain owners prepare commands
        # from the original request so model-generated prose cannot become an
        # executable mutation payload.
        "normalized_request": request,
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
        "You are the N4OS intent selection layer. Return only compact JSON. "
        "do not execute actions, call tools, invent external facts, or include secrets. "
        f"Allowed routes/actions: {route_action_prompt()}. "
        "Choose among the supplied deterministic candidates when possible. Return optional "
        "slot hints copied or directly derived from the request, but never rewrite the request "
        "into a command. Use unknown/unknown with a targeted clarification when the meaning "
        "or required target is genuinely ambiguous."
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
        enabled = os.environ.get(INTENT_REFINEMENT_ENABLED_ENV, "").strip().lower()
        if enabled not in {"1", "true", "yes", "on"}:
            if os.environ.get("OPENAI_API_KEY", "").strip():
                LOGGER.warning(
                    "N4OS intent refinement is disabled; set %s=true to opt in to sending unresolved requests to OpenAI.",
                    INTENT_REFINEMENT_ENABLED_ENV,
                )
            return None
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
        user_payload = json.dumps(
            {
                "request": request,
                "reference_time": _reference_time_text(now),
                "context": {
                    key: value
                    for key, value in (context or {}).items()
                    if key != "semantic_image_path"
                },
                "output_schema": {
                    "route": "string",
                    "action": "string",
                    "confidence": "number 0..1",
                    "followup_kind": "optional string",
                    "target": "optional object",
                    "slots": "optional object",
                    "missing_fields": "optional string array",
                    "clarification_question": "optional string",
                },
            },
            sort_keys=True,
        )
        user_content: str | list[dict[str, str]] = user_payload
        image_path = _clean_string((context or {}).get("semantic_image_path"))
        if image_path:
            path = Path(image_path)
            if path.is_file():
                mime_type = mimetypes.guess_type(str(path))[0] or "image/jpeg"
                image_data = base64.b64encode(path.read_bytes()).decode("ascii")
                user_content = [
                    {"type": "input_text", "text": user_payload},
                    {
                        "type": "input_image",
                        "image_url": f"data:{mime_type};base64,{image_data}",
                        "detail": "high",
                    },
                ]
        body = {
            "model": self.model,
            "store": False,
            "max_output_tokens": 450,
            "input": [
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": user_content},
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

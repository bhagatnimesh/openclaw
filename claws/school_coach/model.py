from __future__ import annotations

import json
import os
from typing import Any, Callable, Literal, cast
import urllib.request

from .contracts import (
    BeliefChange,
    CoachProvenance,
    CoachStateUpdate,
    DecisionSummary,
    EvidenceReference,
    InteractionDraft,
    ObservationDraft,
    RelationshipChange,
    StrategyChange,
)


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_SCHOOL_COACH_MODEL = "gpt-5.4-mini"
DEFAULT_TIMEOUT_SECONDS = 20
UrlOpen = Callable[..., Any]


class SchoolCoachModelError(ValueError):
    pass


class OpenAISchoolCoachModel:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_SCHOOL_COACH_MODEL,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        urlopen: UrlOpen = urllib.request.urlopen,
    ):
        if not api_key.strip():
            raise RuntimeError("School coach model needs OPENAI_API_KEY.")
        self.api_key = api_key.strip()
        self.model = model.strip() or DEFAULT_SCHOOL_COACH_MODEL
        self.timeout_seconds = timeout_seconds
        self.urlopen = urlopen

    @classmethod
    def from_env_or_none(cls) -> "OpenAISchoolCoachModel | None":
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            return None
        return cls(
            api_key=api_key,
            model=os.environ.get("N4OS_SCHOOL_COACH_MODEL", DEFAULT_SCHOOL_COACH_MODEL),
        )

    def decide(
        self,
        request: str,
        *,
        current_state: dict[str, Any] | None,
        relationships: list[dict[str, Any]],
        context_sources: dict[str, str],
        provenance: CoachProvenance,
    ) -> CoachStateUpdate:
        body = {
            "model": self.model,
            "store": False,
            "max_output_tokens": 1800,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "school_coach_state_update",
                    "strict": True,
                    "schema": SCHOOL_COACH_UPDATE_SCHEMA,
                }
            },
            "input": [
                {"role": "system", "content": _system_prompt()},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "request": request,
                            "trigger_provenance": {
                                "kind": provenance.kind,
                                "ref": provenance.ref,
                                "reported_by": provenance.reported_by,
                            },
                            "relationships": relationships,
                            "current_state": current_state,
                            "available_context_sources": [
                                {"ref": ref, "text": text}
                                for ref, text in context_sources.items()
                            ],
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
                "User-Agent": "n4os-school-coach/0.1",
            },
            method="POST",
        )
        with self.urlopen(api_request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        text = _extract_response_text(payload)
        if not text:
            raise RuntimeError("OpenAI returned no school coach update.")
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise SchoolCoachModelError("School coach output must be an object.")
        return validate_coach_update(parsed)


def validate_coach_update(raw: dict[str, Any]) -> CoachStateUpdate:
    _keys(
        raw,
        {"relationship", "interaction", "observations", "belief_changes", "strategy_change", "decision"},
        "update",
    )
    relationship_raw = _object(raw["relationship"], "relationship")
    _keys(
        relationship_raw,
        {"action", "relationship_id", "person_name", "role", "child", "school", "desired_state"},
        "relationship",
    )
    relationship_action = _choice(relationship_raw["action"], {"none", "create", "update"}, "relationship.action")
    relationship = RelationshipChange(
        action=cast(Literal["none", "create", "update"], relationship_action),
        relationship_id=_nullable_string(relationship_raw["relationship_id"], "relationship_id"),
        person_name=_nullable_string(relationship_raw["person_name"], "person_name"),
        role=_nullable_string(relationship_raw["role"], "role"),
        child=_nullable_string(relationship_raw["child"], "child"),
        school=_nullable_string(relationship_raw["school"], "school"),
        desired_state=_nullable_string(relationship_raw["desired_state"], "desired_state"),
    )
    if relationship.action == "create" and not relationship.person_name:
        raise SchoolCoachModelError("A new relationship needs person_name.")
    if relationship.action in {"none", "update"} and not relationship.relationship_id:
        raise SchoolCoachModelError("An existing relationship needs relationship_id.")

    interaction = _parse_interaction(raw["interaction"])
    observations_raw = _array(raw["observations"], "observations")
    observations = tuple(_parse_observation(item) for item in observations_raw)
    if len({item.key for item in observations}) != len(observations):
        raise SchoolCoachModelError("Observation keys must be unique.")

    belief_changes_raw = _array(raw["belief_changes"], "belief_changes")
    belief_changes = tuple(_parse_belief_change(item) for item in belief_changes_raw)
    strategy_change = _parse_strategy_change(raw["strategy_change"])
    decision_raw = _object(raw["decision"], "decision")
    _keys(decision_raw, {"observed", "concluded", "decided", "confidence"}, "decision")
    decision = DecisionSummary(
        observed=_string(decision_raw["observed"], "decision.observed"),
        concluded=_nullable_string(decision_raw["concluded"], "decision.concluded"),
        decided=_nullable_string(decision_raw["decided"], "decision.decided"),
        confidence=_confidence(decision_raw["confidence"], "decision.confidence"),
    )
    return CoachStateUpdate(
        relationship=relationship,
        interaction=interaction,
        observations=observations,
        belief_changes=belief_changes,
        strategy_change=strategy_change,
        decision=decision,
    )


def _parse_interaction(value: Any) -> InteractionDraft | None:
    if value is None:
        return None
    raw = _object(value, "interaction")
    _keys(raw, {"occurred_at", "channel", "summary", "outcome"}, "interaction")
    return InteractionDraft(
        occurred_at=_nullable_string(raw["occurred_at"], "interaction.occurred_at"),
        channel=_nullable_string(raw["channel"], "interaction.channel"),
        summary=_string(raw["summary"], "interaction.summary"),
        outcome=_nullable_string(raw["outcome"], "interaction.outcome"),
    )


def _parse_observation(value: Any) -> ObservationDraft:
    raw = _object(value, "observation")
    _keys(raw, {"key", "statement", "observed_at", "source_kind", "source_ref"}, "observation")
    source_kind = _choice(raw["source_kind"], {"trigger", "interaction", "context"}, "source_kind")
    source_ref = _nullable_string(raw["source_ref"], "source_ref")
    if source_kind == "context" and not source_ref:
        raise SchoolCoachModelError("Context observations need source_ref.")
    return ObservationDraft(
        key=_string(raw["key"], "observation.key"),
        statement=_string(raw["statement"], "observation.statement"),
        observed_at=_nullable_string(raw["observed_at"], "observation.observed_at"),
        source_kind=cast(Literal["trigger", "interaction", "context"], source_kind),
        source_ref=source_ref,
    )


def _parse_belief_change(value: Any) -> BeliefChange:
    raw = _object(value, "belief_change")
    _keys(
        raw,
        {"action", "topic_key", "previous_belief_id", "statement", "confidence", "reason", "evidence"},
        "belief_change",
    )
    action = _choice(raw["action"], {"create", "revise", "retract"}, "belief_change.action")
    evidence_items = []
    for item in _array(raw["evidence"], "belief_change.evidence"):
        evidence = _object(item, "evidence")
        _keys(evidence, {"observation_ref", "stance"}, "evidence")
        stance = _choice(evidence["stance"], {"supports", "contradicts"}, "evidence.stance")
        evidence_items.append(
            EvidenceReference(
                observation_ref=_string(evidence["observation_ref"], "evidence.observation_ref"),
                stance=cast(Literal["supports", "contradicts"], stance),
            )
        )
    statement = _nullable_string(raw["statement"], "belief_change.statement")
    confidence = _nullable_confidence(raw["confidence"], "belief_change.confidence")
    previous = _nullable_string(raw["previous_belief_id"], "previous_belief_id")
    if action == "create" and previous is not None:
        raise SchoolCoachModelError("A new belief cannot have previous_belief_id.")
    if action in {"revise", "retract"} and previous is None:
        raise SchoolCoachModelError("Belief revision needs previous_belief_id.")
    if action != "retract" and (not statement or confidence is None or not evidence_items):
        raise SchoolCoachModelError("Belief creation or revision needs statement, confidence, and evidence.")
    return BeliefChange(
        action=cast(Literal["create", "revise", "retract"], action),
        topic_key=_string(raw["topic_key"], "belief_change.topic_key"),
        previous_belief_id=previous,
        statement=statement,
        confidence=confidence,
        reason=_string(raw["reason"], "belief_change.reason"),
        evidence=tuple(evidence_items),
    )


def _parse_strategy_change(value: Any) -> StrategyChange:
    raw = _object(value, "strategy_change")
    _keys(
        raw,
        {"action", "previous_strategy_id", "goal", "plan", "rationale", "confidence", "reason", "belief_refs"},
        "strategy_change",
    )
    action = _choice(raw["action"], {"none", "create", "revise", "retire"}, "strategy_change.action")
    previous = _nullable_string(raw["previous_strategy_id"], "previous_strategy_id")
    plan = _nullable_string(raw["plan"], "strategy_change.plan")
    rationale = _nullable_string(raw["rationale"], "strategy_change.rationale")
    confidence = _nullable_confidence(raw["confidence"], "strategy_change.confidence")
    reason = _nullable_string(raw["reason"], "strategy_change.reason")
    if action in {"revise", "retire"} and previous is None:
        raise SchoolCoachModelError("Strategy revision needs previous_strategy_id.")
    if action == "create" and previous is not None:
        raise SchoolCoachModelError("A new strategy cannot have previous_strategy_id.")
    if action in {"create", "revise"} and (not plan or not rationale or confidence is None or not reason):
        raise SchoolCoachModelError("Strategy creation or revision needs plan, rationale, confidence, and reason.")
    return StrategyChange(
        action=cast(Literal["none", "create", "revise", "retire"], action),
        previous_strategy_id=previous,
        goal=_nullable_string(raw["goal"], "strategy_change.goal"),
        plan=plan,
        rationale=rationale,
        confidence=confidence,
        reason=reason,
        belief_refs=tuple(
            _string(item, "strategy_change.belief_refs")
            for item in _array(raw["belief_refs"], "strategy_change.belief_refs")
        ),
    )


def _system_prompt() -> str:
    return (
        "You are the N4OS School Relationship Coach state-update layer. Return only the supplied "
        "structured schema. Preserve distinctions: observations are observed or reported; beliefs are "
        "tentative inferences with confidence and evidence; strategies state what to do and why; "
        "interactions are real-world events. Never invent missing names, roles, children, schools, dates, "
        "events, or preferences. Null is valid. User corrections are observations, not facts silently "
        "converted into beliefs. Create or revise beliefs only when evidence warrants it. Contradictory "
        "evidence should lower confidence, revise the statement, or retract the belief. Preserve history by "
        "using the supplied active IDs for revisions. Reference context only by an exact available source ref. "
        "When there is no current relationship and the user explicitly names the person to focus on, create "
        "a sparse relationship for that person and leave every unstated field null. The decision fields are "
        "concise audit summaries, not private reasoning. Do not propose notifications, "
        "messages, candidate interventions, scheduling, or multi-coach actions."
    )


def _extract_response_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    chunks = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    return "\n".join(chunks).strip()


def _keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise SchoolCoachModelError(f"{label} has unexpected or missing fields.")


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchoolCoachModelError(f"{label} must be an object.")
    return value


def _array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise SchoolCoachModelError(f"{label} must be an array.")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchoolCoachModelError(f"{label} must be a non-empty string.")
    return value.strip()


def _nullable_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _string(value, label)


def _choice(value: Any, choices: set[str], label: str) -> str:
    text = _string(value, label)
    if text not in choices:
        raise SchoolCoachModelError(f"{label} has an unsupported value.")
    return text


def _confidence(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchoolCoachModelError(f"{label} must be a number.")
    result = float(value)
    if not 0 <= result <= 1:
        raise SchoolCoachModelError(f"{label} must be between 0 and 1.")
    return result


def _nullable_confidence(value: Any, label: str) -> float | None:
    return None if value is None else _confidence(value, label)


def _nullable_schema(kind: str) -> dict[str, Any]:
    return {"type": [kind, "null"]}


STRING_NULL = _nullable_schema("string")
NUMBER_NULL = _nullable_schema("number")

SCHOOL_COACH_UPDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["relationship", "interaction", "observations", "belief_changes", "strategy_change", "decision"],
    "properties": {
        "relationship": {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "relationship_id", "person_name", "role", "child", "school", "desired_state"],
            "properties": {
                "action": {"type": "string", "enum": ["none", "create", "update"]},
                "relationship_id": STRING_NULL,
                "person_name": STRING_NULL,
                "role": STRING_NULL,
                "child": STRING_NULL,
                "school": STRING_NULL,
                "desired_state": STRING_NULL,
            },
        },
        "interaction": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["occurred_at", "channel", "summary", "outcome"],
                    "properties": {
                        "occurred_at": STRING_NULL,
                        "channel": STRING_NULL,
                        "summary": {"type": "string"},
                        "outcome": STRING_NULL,
                    },
                },
            ]
        },
        "observations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key", "statement", "observed_at", "source_kind", "source_ref"],
                "properties": {
                    "key": {"type": "string"},
                    "statement": {"type": "string"},
                    "observed_at": STRING_NULL,
                    "source_kind": {"type": "string", "enum": ["trigger", "interaction", "context"]},
                    "source_ref": STRING_NULL,
                },
            },
        },
        "belief_changes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action", "topic_key", "previous_belief_id", "statement", "confidence", "reason", "evidence"],
                "properties": {
                    "action": {"type": "string", "enum": ["create", "revise", "retract"]},
                    "topic_key": {"type": "string"},
                    "previous_belief_id": STRING_NULL,
                    "statement": STRING_NULL,
                    "confidence": NUMBER_NULL,
                    "reason": {"type": "string"},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["observation_ref", "stance"],
                            "properties": {
                                "observation_ref": {"type": "string"},
                                "stance": {"type": "string", "enum": ["supports", "contradicts"]},
                            },
                        },
                    },
                },
            },
        },
        "strategy_change": {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "previous_strategy_id", "goal", "plan", "rationale", "confidence", "reason", "belief_refs"],
            "properties": {
                "action": {"type": "string", "enum": ["none", "create", "revise", "retire"]},
                "previous_strategy_id": STRING_NULL,
                "goal": STRING_NULL,
                "plan": STRING_NULL,
                "rationale": STRING_NULL,
                "confidence": NUMBER_NULL,
                "reason": STRING_NULL,
                "belief_refs": {"type": "array", "items": {"type": "string"}},
            },
        },
        "decision": {
            "type": "object",
            "additionalProperties": False,
            "required": ["observed", "concluded", "decided", "confidence"],
            "properties": {
                "observed": {"type": "string"},
                "concluded": STRING_NULL,
                "decided": STRING_NULL,
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
        },
    },
}

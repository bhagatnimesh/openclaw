from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re
from typing import Any, Protocol

from .context import discover_teacher_candidates, retrieve_school_coach_sources
from .contracts import CoachProvenance, CoachStateUpdate
from .model import OpenAISchoolCoachModel
from .provider import DEFAULT_DB_FILE, SQLiteSchoolCoachProvider, SchoolCoachStateError


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_N4OS_ROOT = ROOT / "n4os"
SCHOOL_COACH_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"/(?:school[-_]coach(?:@\w+)?|school(?:@\w+)?\s+coach)"
    r"|(?:please\s+)?(?:ask|open|start|use|talk\s+to)\s+(?:the\s+)?school\s+coach"
    r"|(?:the\s+)?school\s+coach"
    r")(?:\s*[:,.-]\s*|\s+|$)",
    re.I,
)
SCHOOL_COACH_NATURAL_INTENT_RE = re.compile(
    r"(?:"
    r"\bwhat(?:'s|\s+is)\s+(?:your|the)\s+current\s+plan\s+for"
    r"|\bshow\s+me\s+(?:your|the)\s+(?:school\s+)?strategy\s+for"
    r"|\bwhy\s+do\s+you\s+think\s+that\s+about"
    r"|\b(?:help\s+me|i\s+want\s+help)\s+(?:build|improve|manage)\b.*\brelationship\s+with"
    r")\s+(?:mr|mrs|ms|miss|dr)\.?\s+[a-z]",
    re.I,
)


class SchoolCoachModel(Protocol):
    model: str

    def decide(
        self,
        request: str,
        *,
        current_state: dict[str, Any] | None,
        relationships: list[dict[str, Any]],
        context_sources: dict[str, str],
        provenance: CoachProvenance,
    ) -> CoachStateUpdate: ...


class SchoolCoachClaw:
    def __init__(
        self,
        provider: SQLiteSchoolCoachProvider,
        *,
        model: SchoolCoachModel | None,
        n4os_root: Path = DEFAULT_N4OS_ROOT,
    ):
        self.provider = provider
        self.model = model
        self.n4os_root = n4os_root

    @classmethod
    def default(cls, *, n4os_root: Path = DEFAULT_N4OS_ROOT) -> "SchoolCoachClaw":
        return cls(
            SQLiteSchoolCoachProvider(DEFAULT_DB_FILE),
            model=OpenAISchoolCoachModel.from_env_or_none(),
            n4os_root=n4os_root,
        )

    def handle_request(self, request: str, *, provenance: CoachProvenance) -> str:
        cleaned = strip_school_coach_prefix(request)
        relationships = self.provider.list_relationships()
        if not cleaned:
            return self._day_zero(relationships)

        relationship, ambiguous = self.provider.resolve_relationship(cleaned)
        lowered = cleaned.casefold()
        if _is_plan_query(lowered):
            return self._with_relationship(relationship, ambiguous, self.format_current_plan)
        if _is_why_query(lowered):
            return self._with_relationship(relationship, ambiguous, self.format_why)
        if _is_history_query(lowered):
            return self._with_relationship(relationship, ambiguous, self.format_history)

        if ambiguous:
            names = ", ".join(item["person_name"] for item in relationships)
            return f"Which relationship do you mean: {names}?"

        if self.model is None:
            return "I can read saved school-coach state, but updating it requires OPENAI_API_KEY."
        if _looks_like_correction(cleaned):
            provenance = replace(provenance, kind="user_correction")
        current_state = self.provider.current_state(relationship["id"]) if relationship else None
        context_sources = retrieve_school_coach_sources(
            self.n4os_root,
            request=cleaned,
            relationship=relationship,
        )
        try:
            update = self.model.decide(
                cleaned,
                current_state=current_state,
                relationships=relationships,
                context_sources=context_sources,
                provenance=provenance,
            )
            result = self.provider.apply_update(
                update,
                provenance=provenance,
                context_sources=context_sources,
                model=getattr(self.model, "model", None),
                expected_relationship_id=relationship["id"] if relationship else None,
            )
        except (SchoolCoachStateError, ValueError) as error:
            return f"I did not update the coach state: {error}"
        return self._format_update(result)

    def _day_zero(self, relationships: list[dict[str, Any]]) -> str:
        if len(relationships) == 1:
            return self.format_current_plan(relationships[0])
        if len(relationships) > 1:
            names = ", ".join(item["person_name"] for item in relationships)
            return f"Who should we focus on? I have relationships for {names}."
        candidates = discover_teacher_candidates(self.n4os_root)
        if len(candidates) == 1:
            candidate = candidates[0]
            return (
                f"I found {candidate['person_name']} in {candidate['child']}'s school notes. "
                f"Is that the teacher you want to focus on? Reply: /school-coach Yes, focus on "
                f"{candidate['person_name']}."
            )
        return "Which teacher or school adult should we focus on?"

    def _with_relationship(self, relationship, ambiguous, formatter) -> str:
        if relationship is not None:
            return formatter(relationship)
        if ambiguous:
            names = ", ".join(item["person_name"] for item in self.provider.list_relationships())
            return f"Which relationship do you mean: {names}?"
        return "I do not have that relationship yet. Start with: /school-coach Focus on Ms. Name."

    def format_current_plan(self, relationship: dict[str, Any]) -> str:
        state = self.provider.current_state(relationship["id"])
        strategy = state["strategy"]
        lines = [f"Here's the current plan for {relationship['person_name']}."]
        known = [
            value
            for value in (
                relationship.get("role"),
                f"Child: {relationship['child']}" if relationship.get("child") else None,
                f"School: {relationship['school']}" if relationship.get("school") else None,
            )
            if value
        ]
        if known:
            lines.append("Known context: " + "; ".join(known))
        if strategy is None:
            lines.extend(
                [
                    "Current plan: No strategy yet.",
                    "What remains unknown: I need at least one observation or a stated relationship goal.",
                ]
            )
            return "\n".join(lines)
        if strategy.get("goal"):
            lines.append(f"Goal: {strategy['goal']}")
        lines.append(f"Current plan: {strategy['plan']}")
        lines.append(f"Why: {strategy['rationale']}")
        lines.append(f"Confidence: {_confidence_label(strategy['confidence'])}")
        lines.append(
            "Review/Capture: After the next interaction, tell me what happened so I can update the plan."
        )
        return "\n".join(lines)

    def format_why(self, relationship: dict[str, Any]) -> str:
        state = self.provider.current_state(relationship["id"])
        lines = [f"Why I currently think this about {relationship['person_name']}:"]
        if not state["beliefs"]:
            lines.append("I do not have an evidence-backed belief yet.")
        for belief in state["beliefs"][:3]:
            lines.append(
                f"Belief: {belief['statement']} ({_confidence_label(belief['confidence'])} confidence)"
            )
            for evidence in self.provider.evidence_for_belief(belief["id"])[:3]:
                marker = "Supports" if evidence["stance"] == "supports" else "Contradicts"
                lines.append(
                    f"- {marker}: {evidence['statement']} (source: {_source_label(evidence)})"
                )
        strategy = state["strategy"]
        if strategy is not None:
            lines.append(f"Strategy rationale: {strategy['rationale']}")
        return "\n".join(lines)

    def format_history(self, relationship: dict[str, Any]) -> str:
        beliefs = self.provider.belief_history(relationship["id"])
        strategies = self.provider.strategy_history(relationship["id"])
        lines = [f"Here's the saved coach history for {relationship['person_name']}.", "Beliefs:"]
        if not beliefs:
            lines.append("- None yet.")
        for belief in beliefs[-6:]:
            lines.append(
                f"- {belief['topic_key']} v{belief['version']} [{belief['status']}]: "
                f"{belief['statement']} ({belief['confidence']:.2f}). Change: {belief['change_reason']}"
            )
        lines.append("Strategies:")
        if not strategies:
            lines.append("- None yet.")
        for strategy in strategies[-6:]:
            lines.append(
                f"- v{strategy['version']} [{strategy['status']}]: {strategy['plan']} "
                f"Change: {strategy['change_reason']}"
            )
        return "\n".join(lines)

    def _format_update(self, result: dict[str, Any]) -> str:
        relationship = result["relationship"]
        state = self.provider.current_state(relationship["id"])
        lines = [f"Updated coach state for {relationship['person_name']}."]
        if result["observation_ids"]:
            lines.append(f"Observations captured: {len(result['observation_ids'])}")
        if result["belief_ids"]:
            current_by_id = {belief["id"]: belief for belief in state["beliefs"]}
            for belief_id in result["belief_ids"].values():
                belief = current_by_id.get(belief_id)
                if belief:
                    lines.append(f"Current belief: {belief['statement']}")
        if result["strategy_id"] and state["strategy"]:
            lines.append(f"Current plan: {state['strategy']['plan']}")
            lines.append("Review/Capture: Tell me what happens after the next interaction.")
        if len(lines) == 1:
            lines.append("No belief or strategy was invented from the available evidence.")
        return "\n".join(lines)


def is_school_coach_message(text: str) -> bool:
    return bool(SCHOOL_COACH_PREFIX_RE.match(text) or SCHOOL_COACH_NATURAL_INTENT_RE.search(text))


def strip_school_coach_prefix(text: str) -> str:
    return SCHOOL_COACH_PREFIX_RE.sub("", text, count=1).strip()


def _is_plan_query(lowered: str) -> bool:
    return any(phrase in lowered for phrase in ("current plan", "show me your strategy", "what is your plan", "what's your plan"))


def _is_why_query(lowered: str) -> bool:
    return "why do you think" in lowered or lowered.startswith("why ") or lowered == "why"


def _is_history_query(lowered: str) -> bool:
    return any(phrase in lowered for phrase in ("history", "believe before", "previous belief", "previous strategy"))


def _looks_like_correction(text: str) -> bool:
    return bool(re.match(r"^\s*(?:no[, ]|actually\b|correction\b|that's wrong\b|that is wrong\b)", text, re.I))


def _confidence_label(value: float) -> str:
    if value >= 0.75:
        return "high"
    if value >= 0.45:
        return "medium"
    return "low"


def _source_label(evidence: dict[str, Any]) -> str:
    kind = evidence.get("provenance_kind")
    return {
        "user_report": "user report",
        "user_correction": "user correction",
        "interaction": "reported interaction",
        "school_markdown": "school notes",
        "journal_markdown": "journal",
    }.get(kind, "saved evidence")

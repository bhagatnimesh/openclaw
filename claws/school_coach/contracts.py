from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ProvenanceKind = Literal[
    "user_report",
    "user_correction",
    "interaction",
    "school_markdown",
    "journal_markdown",
]


@dataclass(frozen=True)
class CoachProvenance:
    kind: ProvenanceKind
    ref: str
    reported_by: str | None = None


@dataclass(frozen=True)
class RelationshipChange:
    action: Literal["none", "create", "update"]
    relationship_id: str | None
    person_name: str | None
    role: str | None
    child: str | None
    school: str | None
    desired_state: str | None


@dataclass(frozen=True)
class InteractionDraft:
    occurred_at: str | None
    channel: str | None
    summary: str
    outcome: str | None


@dataclass(frozen=True)
class ObservationDraft:
    key: str
    statement: str
    observed_at: str | None
    source_kind: Literal["trigger", "interaction", "context"]
    source_ref: str | None


@dataclass(frozen=True)
class EvidenceReference:
    observation_ref: str
    stance: Literal["supports", "contradicts"]


@dataclass(frozen=True)
class BeliefChange:
    action: Literal["create", "revise", "retract"]
    topic_key: str
    previous_belief_id: str | None
    statement: str | None
    confidence: float | None
    reason: str
    evidence: tuple[EvidenceReference, ...]


@dataclass(frozen=True)
class StrategyChange:
    action: Literal["none", "create", "revise", "retire"]
    previous_strategy_id: str | None
    goal: str | None
    plan: str | None
    rationale: str | None
    confidence: float | None
    reason: str | None
    belief_refs: tuple[str, ...]


@dataclass(frozen=True)
class DecisionSummary:
    observed: str
    concluded: str | None
    decided: str | None
    confidence: float


@dataclass(frozen=True)
class CoachStateUpdate:
    relationship: RelationshipChange
    interaction: InteractionDraft | None
    observations: tuple[ObservationDraft, ...]
    belief_changes: tuple[BeliefChange, ...]
    strategy_change: StrategyChange
    decision: DecisionSummary

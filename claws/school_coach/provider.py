from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator
from uuid import uuid4

from .contracts import CoachProvenance, CoachStateUpdate
from .schema import ensure_school_coach_schema


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_FILE = ROOT / "data" / "n4os.db"


class SchoolCoachStateError(ValueError):
    pass


class SQLiteSchoolCoachProvider:
    def __init__(self, db_path: str | Path = DEFAULT_DB_FILE):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._connection() as connection:
            ensure_school_coach_schema(connection)

    def list_relationships(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM school_coach_relationships WHERE status = 'active' ORDER BY person_name"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_relationship(self, relationship_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM school_coach_relationships WHERE id = ?",
                (relationship_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def resolve_relationship(self, request: str) -> tuple[dict[str, Any] | None, bool]:
        relationships = self.list_relationships()
        lowered = _name_key(request)
        matches = [
            item for item in relationships if _contains_normalized_name(lowered, item["person_name_key"])
        ]
        if len(matches) == 1:
            return matches[0], False
        if len(matches) > 1:
            return None, True
        short_name = _short_named_person_token(request)
        if short_name:
            short_matches = [
                item
                for item in relationships
                if item["person_name_key"].split()[-1] == short_name
            ]
            if len(short_matches) == 1:
                return short_matches[0], False
            if len(short_matches) > 1:
                return None, True
        if _contains_named_person(request):
            return None, False
        if len(relationships) == 1:
            return relationships[0], False
        return None, len(relationships) > 1

    def current_state(self, relationship_id: str) -> dict[str, Any]:
        relationship = self.get_relationship(relationship_id)
        if relationship is None:
            raise SchoolCoachStateError("Unknown relationship.")
        with self._connection() as connection:
            belief_rows = connection.execute(
                """
                SELECT * FROM school_coach_beliefs
                WHERE relationship_id = ? AND status = 'active'
                ORDER BY topic_key
                """,
                (relationship_id,),
            ).fetchall()
            strategy_row = connection.execute(
                """
                SELECT * FROM school_coach_strategies
                WHERE relationship_id = ? AND status = 'active'
                """,
                (relationship_id,),
            ).fetchone()
            observation_rows = connection.execute(
                """
                SELECT * FROM school_coach_observations
                WHERE relationship_id = ? ORDER BY created_at DESC LIMIT 20
                """,
                (relationship_id,),
            ).fetchall()
        beliefs = [dict(row) for row in belief_rows]
        for belief in beliefs:
            belief["evidence"] = self.evidence_for_belief(belief["id"])
        strategy = dict(strategy_row) if strategy_row is not None else None
        if strategy is not None:
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT belief_id FROM school_coach_strategy_beliefs
                    WHERE strategy_id = ? ORDER BY belief_id
                    """,
                    (strategy["id"],),
                ).fetchall()
            strategy["belief_refs"] = [row["belief_id"] for row in rows]
        return {
            "relationship": relationship,
            "beliefs": beliefs,
            "strategy": strategy,
            "observations": [dict(row) for row in observation_rows],
        }

    def belief_history(self, relationship_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM school_coach_beliefs
                WHERE relationship_id = ? ORDER BY topic_key, version
                """,
                (relationship_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def strategy_history(self, relationship_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM school_coach_strategies
                WHERE relationship_id = ? ORDER BY version
                """,
                (relationship_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def interaction_history(self, relationship_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM school_coach_interactions
                WHERE relationship_id = ? ORDER BY created_at
                """,
                (relationship_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def decision_history(self, relationship_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM school_coach_decision_records
                WHERE relationship_id = ? ORDER BY created_at
                """,
                (relationship_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def evidence_for_belief(self, belief_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT o.*, e.stance
                FROM school_coach_belief_evidence e
                JOIN school_coach_observations o ON o.id = e.observation_id
                WHERE e.belief_id = ? ORDER BY o.created_at
                """,
                (belief_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def apply_update(
        self,
        update: CoachStateUpdate,
        *,
        provenance: CoachProvenance,
        context_sources: dict[str, str],
        model: str | None,
        expected_relationship_id: str | None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        target_id = update.relationship.relationship_id
        if expected_relationship_id is None:
            if update.relationship.action != "create":
                raise SchoolCoachStateError("A new person must create a relationship first.")
        elif update.relationship.action == "create" or target_id != expected_relationship_id:
            raise SchoolCoachStateError("The update targeted a different relationship.")
        timestamp = created_at or datetime.now().astimezone().isoformat()
        with self._connection() as connection:
            relationship = self._apply_relationship(connection, update, timestamp)
            relationship_id = str(relationship["id"])
            decision_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO school_coach_decision_records (
                    id, relationship_id, trigger_kind, trigger_ref, observed_summary,
                    conclusion_summary, decision_summary, confidence, model, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    relationship_id,
                    provenance.kind,
                    provenance.ref,
                    update.decision.observed,
                    update.decision.concluded,
                    update.decision.decided,
                    update.decision.confidence,
                    model,
                    timestamp,
                ),
            )

            interaction_id = self._insert_interaction(
                connection,
                update,
                relationship_id=relationship_id,
                decision_id=decision_id,
                provenance=provenance,
                timestamp=timestamp,
            )
            observation_ids = self._insert_observations(
                connection,
                update,
                relationship_id=relationship_id,
                decision_id=decision_id,
                interaction_id=interaction_id,
                provenance=provenance,
                context_sources=context_sources,
                timestamp=timestamp,
            )
            belief_ids = self._apply_beliefs(
                connection,
                update,
                relationship_id=relationship_id,
                decision_id=decision_id,
                observation_ids=observation_ids,
                timestamp=timestamp,
            )
            strategy_id = self._apply_strategy(
                connection,
                update,
                relationship_id=relationship_id,
                decision_id=decision_id,
                belief_ids=belief_ids,
                timestamp=timestamp,
            )
        return {
            "relationship": relationship,
            "decision_id": decision_id,
            "observation_ids": observation_ids,
            "belief_ids": belief_ids,
            "strategy_id": strategy_id,
        }

    def _apply_relationship(
        self,
        connection: sqlite3.Connection,
        update: CoachStateUpdate,
        timestamp: str,
    ) -> dict[str, Any]:
        change = update.relationship
        if change.action == "create":
            if not change.person_name:
                raise SchoolCoachStateError("A new relationship needs a person name.")
            relationship_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO school_coach_relationships (
                    id, person_name, person_name_key, role, child, school,
                    desired_state, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    relationship_id,
                    change.person_name,
                    _name_key(change.person_name),
                    change.role,
                    change.child,
                    change.school,
                    change.desired_state,
                    timestamp,
                    timestamp,
                ),
            )
        elif change.action == "update":
            if not change.relationship_id:
                raise SchoolCoachStateError("A relationship update needs an id.")
            current = connection.execute(
                "SELECT * FROM school_coach_relationships WHERE id = ? AND status = 'active'",
                (change.relationship_id,),
            ).fetchone()
            if current is None:
                raise SchoolCoachStateError("The relationship to update does not exist.")
            person_name = change.person_name or current["person_name"]
            connection.execute(
                """
                UPDATE school_coach_relationships SET
                    person_name = ?, person_name_key = ?,
                    role = COALESCE(?, role), child = COALESCE(?, child),
                    school = COALESCE(?, school), desired_state = COALESCE(?, desired_state),
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    person_name,
                    _name_key(person_name),
                    change.role,
                    change.child,
                    change.school,
                    change.desired_state,
                    timestamp,
                    change.relationship_id,
                ),
            )
            relationship_id = change.relationship_id
        else:
            if not change.relationship_id:
                raise SchoolCoachStateError("A coach update needs a relationship id.")
            relationship_id = change.relationship_id

        row = connection.execute(
            "SELECT * FROM school_coach_relationships WHERE id = ? AND status = 'active'",
            (relationship_id,),
        ).fetchone()
        if row is None:
            raise SchoolCoachStateError("The relationship does not exist.")
        return dict(row)

    def _insert_interaction(
        self,
        connection: sqlite3.Connection,
        update: CoachStateUpdate,
        *,
        relationship_id: str,
        decision_id: str,
        provenance: CoachProvenance,
        timestamp: str,
    ) -> str | None:
        interaction = update.interaction
        if interaction is None:
            return None
        interaction_id = uuid4().hex
        connection.execute(
            """
            INSERT INTO school_coach_interactions (
                id, relationship_id, decision_id, occurred_at, channel, summary,
                outcome, provenance_kind, provenance_ref, reported_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                interaction_id,
                relationship_id,
                decision_id,
                interaction.occurred_at,
                interaction.channel,
                interaction.summary,
                interaction.outcome,
                provenance.kind,
                provenance.ref,
                provenance.reported_by,
                timestamp,
            ),
        )
        return interaction_id

    def _insert_observations(
        self,
        connection: sqlite3.Connection,
        update: CoachStateUpdate,
        *,
        relationship_id: str,
        decision_id: str,
        interaction_id: str | None,
        provenance: CoachProvenance,
        context_sources: dict[str, str],
        timestamp: str,
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        for observation in update.observations:
            if observation.key in result:
                raise SchoolCoachStateError(f"Duplicate observation key: {observation.key}")
            if observation.source_kind == "trigger":
                source_kind = provenance.kind
                source_ref = provenance.ref
                source_interaction_id = None
                reported_by = provenance.reported_by
            elif observation.source_kind == "interaction":
                if interaction_id is None:
                    raise SchoolCoachStateError("Interaction evidence needs an interaction.")
                source_kind = "interaction"
                source_ref = interaction_id
                source_interaction_id = interaction_id
                reported_by = provenance.reported_by
            else:
                if not observation.source_ref or observation.source_ref not in context_sources:
                    raise SchoolCoachStateError("Observation referenced unavailable context.")
                source_kind = (
                    "journal_markdown"
                    if "/journal/" in observation.source_ref
                    else "school_markdown"
                )
                source_ref = observation.source_ref
                source_interaction_id = None
                reported_by = None
            observation_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO school_coach_observations (
                    id, relationship_id, interaction_id, decision_id, statement,
                    observed_at, provenance_kind, provenance_ref, reported_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation_id,
                    relationship_id,
                    source_interaction_id,
                    decision_id,
                    observation.statement,
                    observation.observed_at,
                    source_kind,
                    source_ref,
                    reported_by,
                    timestamp,
                ),
            )
            result[observation.key] = observation_id
        return result

    def _apply_beliefs(
        self,
        connection: sqlite3.Connection,
        update: CoachStateUpdate,
        *,
        relationship_id: str,
        decision_id: str,
        observation_ids: dict[str, str],
        timestamp: str,
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        for change in update.belief_changes:
            previous = None
            if change.previous_belief_id:
                previous = connection.execute(
                    "SELECT * FROM school_coach_beliefs WHERE id = ?",
                    (change.previous_belief_id,),
                ).fetchone()
            if change.action in {"revise", "retract"}:
                if (
                    previous is None
                    or previous["relationship_id"] != relationship_id
                    or previous["topic_key"] != change.topic_key
                    or previous["status"] != "active"
                ):
                    raise SchoolCoachStateError("Belief change does not target the active version.")
            if change.action == "retract":
                connection.execute(
                    "UPDATE school_coach_beliefs SET status = 'retracted' WHERE id = ?",
                    (change.previous_belief_id,),
                )
                continue
            if not change.statement or change.confidence is None or not change.evidence:
                raise SchoolCoachStateError("A belief needs a statement, confidence, and evidence.")
            if change.action == "create":
                active = connection.execute(
                    """
                    SELECT id FROM school_coach_beliefs
                    WHERE relationship_id = ? AND topic_key = ? AND status = 'active'
                    """,
                    (relationship_id, change.topic_key),
                ).fetchone()
                if active is not None:
                    raise SchoolCoachStateError("Use revise for an existing belief topic.")
                version = 1
            else:
                version = int(previous["version"]) + 1
                connection.execute(
                    "UPDATE school_coach_beliefs SET status = 'superseded' WHERE id = ?",
                    (change.previous_belief_id,),
                )
            belief_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO school_coach_beliefs (
                    id, relationship_id, topic_key, version, previous_belief_id,
                    statement, confidence, status, change_reason, decision_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    belief_id,
                    relationship_id,
                    change.topic_key,
                    version,
                    change.previous_belief_id,
                    change.statement,
                    change.confidence,
                    change.reason,
                    decision_id,
                    timestamp,
                ),
            )
            for evidence in change.evidence:
                observation_id = observation_ids.get(evidence.observation_ref, evidence.observation_ref)
                row = connection.execute(
                    """
                    SELECT id FROM school_coach_observations
                    WHERE id = ? AND relationship_id = ?
                    """,
                    (observation_id, relationship_id),
                ).fetchone()
                if row is None:
                    raise SchoolCoachStateError("Belief referenced unknown evidence.")
                connection.execute(
                    """
                    INSERT INTO school_coach_belief_evidence (belief_id, observation_id, stance)
                    VALUES (?, ?, ?)
                    """,
                    (belief_id, observation_id, evidence.stance),
                )
            result[change.topic_key] = belief_id
        return result

    def _apply_strategy(
        self,
        connection: sqlite3.Connection,
        update: CoachStateUpdate,
        *,
        relationship_id: str,
        decision_id: str,
        belief_ids: dict[str, str],
        timestamp: str,
    ) -> str | None:
        change = update.strategy_change
        if change.action == "none":
            return None
        previous = None
        if change.previous_strategy_id:
            previous = connection.execute(
                "SELECT * FROM school_coach_strategies WHERE id = ?",
                (change.previous_strategy_id,),
            ).fetchone()
        if change.action in {"revise", "retire"}:
            if (
                previous is None
                or previous["relationship_id"] != relationship_id
                or previous["status"] != "active"
            ):
                raise SchoolCoachStateError("Strategy change does not target the active version.")
        if change.action == "retire":
            connection.execute(
                "UPDATE school_coach_strategies SET status = 'retired' WHERE id = ?",
                (change.previous_strategy_id,),
            )
            return None
        if not change.plan or not change.rationale or change.confidence is None or not change.reason:
            raise SchoolCoachStateError("A strategy needs a plan, rationale, confidence, and reason.")
        if change.action == "create":
            active = connection.execute(
                """
                SELECT id FROM school_coach_strategies
                WHERE relationship_id = ? AND status = 'active'
                """,
                (relationship_id,),
            ).fetchone()
            if active is not None:
                raise SchoolCoachStateError("Use revise for an existing strategy.")
            version = 1
        else:
            version = int(previous["version"]) + 1
            connection.execute(
                "UPDATE school_coach_strategies SET status = 'superseded' WHERE id = ?",
                (change.previous_strategy_id,),
            )
        strategy_id = uuid4().hex
        connection.execute(
            """
            INSERT INTO school_coach_strategies (
                id, relationship_id, version, previous_strategy_id, goal, plan,
                rationale, confidence, status, change_reason, decision_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (
                strategy_id,
                relationship_id,
                version,
                change.previous_strategy_id,
                change.goal,
                change.plan,
                change.rationale,
                change.confidence,
                change.reason,
                decision_id,
                timestamp,
            ),
        )
        for belief_ref in change.belief_refs:
            belief_id = belief_ids.get(belief_ref, belief_ref)
            belief = connection.execute(
                """
                SELECT id, status FROM school_coach_beliefs
                WHERE id = ? AND relationship_id = ?
                """,
                (belief_id, relationship_id),
            ).fetchone()
            if belief is not None and belief["status"] != "active":
                belief = connection.execute(
                    """
                    SELECT id, status FROM school_coach_beliefs
                    WHERE previous_belief_id = ? AND decision_id = ? AND status = 'active'
                    """,
                    (belief_id, decision_id),
                ).fetchone()
                if belief is not None:
                    belief_id = belief["id"]
            if belief is None or belief["status"] != "active":
                raise SchoolCoachStateError("Strategy referenced an unknown belief.")
            connection.execute(
                """
                INSERT INTO school_coach_strategy_beliefs (strategy_id, belief_id)
                VALUES (?, ?)
                """,
                (strategy_id, belief_id),
            )
        return strategy_id


def _name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _contains_named_person(value: str) -> bool:
    return bool(re.search(r"\b(?:mr|mrs|ms|miss|dr)\.?\s+[a-z]", value, re.I))


def _contains_normalized_name(request_key: str, person_name_key: str) -> bool:
    return bool(re.search(rf"(?:^| ){re.escape(person_name_key)}(?: |$)", request_key))


def _short_named_person_token(value: str) -> str | None:
    match = re.search(r"\b(?:mr|mrs|ms|miss|dr)\.?\s+([a-z][a-z'-]*)", value, re.I)
    return match.group(1).casefold() if match else None

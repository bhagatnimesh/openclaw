from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from claws.school_coach.contracts import (
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
from claws.school_coach.provider import SQLiteSchoolCoachProvider, SchoolCoachStateError


class SchoolCoachProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "n4os.db"
        self.provider = SQLiteSchoolCoachProvider(self.db_path)

    def test_invalid_context_reference_rolls_back_everything(self):
        update = CoachStateUpdate(
            relationship=RelationshipChange("create", None, "Ms. X", None, None, None, None),
            interaction=None,
            observations=(ObservationDraft("o1", "Reported item.", None, "context", "n4os/missing.md"),),
            belief_changes=(),
            strategy_change=StrategyChange("none", None, None, None, None, None, None, ()),
            decision=DecisionSummary("A source was considered.", None, None, 0.2),
        )

        with self.assertRaises(SchoolCoachStateError):
            self.provider.apply_update(
                update,
                provenance=CoachProvenance("user_report", "request:1"),
                context_sources={},
                model="fake",
                expected_relationship_id=None,
            )

        self.assertEqual(self.provider.list_relationships(), [])
        with sqlite3.connect(self.db_path) as connection:
            count = connection.execute("SELECT COUNT(*) FROM school_coach_decision_records").fetchone()[0]
        self.assertEqual(count, 0)

    def test_model_cannot_target_a_different_resolved_relationship(self):
        first = self.provider.apply_update(
            _relationship_only_update("Ms. First"),
            provenance=CoachProvenance("user_report", "request:first"),
            context_sources={},
            model="fake",
            expected_relationship_id=None,
        )
        second = self.provider.apply_update(
            _relationship_only_update("Ms. Second"),
            provenance=CoachProvenance("user_report", "request:second"),
            context_sources={},
            model="fake",
            expected_relationship_id=None,
        )
        wrong_target = CoachStateUpdate(
            relationship=RelationshipChange(
                "none",
                second["relationship"]["id"],
                None,
                None,
                None,
                None,
                None,
            ),
            interaction=None,
            observations=(),
            belief_changes=(),
            strategy_change=StrategyChange("none", None, None, None, None, None, None, ()),
            decision=DecisionSummary("No new evidence.", None, None, 0.1),
        )

        with self.assertRaises(SchoolCoachStateError):
            self.provider.apply_update(
                wrong_target,
                provenance=CoachProvenance("user_report", "request:wrong"),
                context_sources={},
                model="fake",
                expected_relationship_id=first["relationship"]["id"],
            )

        self.assertEqual(len(self.provider.decision_history(first["relationship"]["id"])), 1)
        self.assertEqual(len(self.provider.decision_history(second["relationship"]["id"])), 1)

    def test_resolves_full_teacher_name_from_unambiguous_last_name(self):
        created = self.provider.apply_update(
            _relationship_only_update("Mrs. Suzanne Thompson"),
            provenance=CoachProvenance("user_report", "request:teacher"),
            context_sources={},
            model="fake",
            expected_relationship_id=None,
        )

        relationship, ambiguous = self.provider.resolve_relationship(
            "What's your current plan for Mrs. Thompson?"
        )

        self.assertFalse(ambiguous)
        self.assertEqual(relationship["id"], created["relationship"]["id"])

    def test_unknown_evidence_rolls_back_relationship_and_decision(self):
        update = CoachStateUpdate(
            relationship=RelationshipChange("create", None, "Ms. X", None, None, None, None),
            interaction=None,
            observations=(),
            belief_changes=(
                BeliefChange(
                    "create",
                    "communication_style",
                    None,
                    "Ms. X may prefer email.",
                    0.4,
                    "Tentative inference.",
                    (EvidenceReference("missing", "supports"),),
                ),
            ),
            strategy_change=StrategyChange("none", None, None, None, None, None, None, ()),
            decision=DecisionSummary("A report was received.", "Email may work.", None, 0.4),
        )

        with self.assertRaises(SchoolCoachStateError):
            self.provider.apply_update(
                update,
                provenance=CoachProvenance("user_report", "request:2"),
                context_sources={},
                model="fake",
                expected_relationship_id=None,
            )

        self.assertEqual(self.provider.list_relationships(), [])

    def test_interaction_and_derived_observation_remain_distinct(self):
        update = CoachStateUpdate(
            relationship=RelationshipChange("create", None, "Ms. X", "Teacher", None, None, None),
            interaction=InteractionDraft(
                "2026-08-20T15:00:00-07:00",
                "pickup",
                "The parent spoke with Ms. X at pickup.",
                "Ms. X answered briefly before returning to dismissal duties.",
            ),
            observations=(
                ObservationDraft(
                    "brief_pickup",
                    "Ms. X gave a brief answer during pickup.",
                    "2026-08-20T15:00:00-07:00",
                    "interaction",
                    None,
                ),
            ),
            belief_changes=(),
            strategy_change=StrategyChange("none", None, None, None, None, None, None, ()),
            decision=DecisionSummary(
                "A brief pickup interaction was reported.",
                None,
                "Store the interaction and observation without inferring a preference.",
                0.9,
            ),
        )

        result = self.provider.apply_update(
            update,
            provenance=CoachProvenance("user_report", "telegram:1:2:3", "dad"),
            context_sources={},
            model="fake",
            expected_relationship_id=None,
        )

        relationship_id = result["relationship"]["id"]
        interactions = self.provider.interaction_history(relationship_id)
        state = self.provider.current_state(relationship_id)
        decisions = self.provider.decision_history(relationship_id)
        self.assertEqual(len(interactions), 1)
        self.assertEqual(len(state["observations"]), 1)
        self.assertEqual(state["observations"][0]["interaction_id"], interactions[0]["id"])
        self.assertEqual(state["observations"][0]["provenance_kind"], "interaction")
        self.assertEqual(state["beliefs"], [])
        self.assertEqual(decisions[0]["decision_summary"], update.decision.decided)


def _relationship_only_update(person_name: str) -> CoachStateUpdate:
    return CoachStateUpdate(
        relationship=RelationshipChange("create", None, person_name, None, None, None, None),
        interaction=None,
        observations=(),
        belief_changes=(),
        strategy_change=StrategyChange("none", None, None, None, None, None, None, ()),
        decision=DecisionSummary("The user named a person to focus on.", None, None, 1.0),
    )

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from claws.school_coach.claw import SchoolCoachClaw, is_school_coach_message, strip_school_coach_prefix
from claws.school_coach.contracts import (
    BeliefChange,
    CoachProvenance,
    CoachStateUpdate,
    DecisionSummary,
    EvidenceReference,
    ObservationDraft,
    RelationshipChange,
    StrategyChange,
)
from claws.school_coach.provider import SQLiteSchoolCoachProvider


class LearningModel:
    model = "fake-school-coach"

    def decide(self, request: str, *, current_state, relationships, context_sources, provenance):
        del relationships, context_sources, provenance
        if current_state is None:
            return CoachStateUpdate(
                relationship=RelationshipChange(
                    action="create",
                    relationship_id=None,
                    person_name="Ms. Rivera",
                    role=None,
                    child=None,
                    school=None,
                    desired_state=None,
                ),
                interaction=None,
                observations=(
                    ObservationDraft(
                        key="short_replies",
                        statement="Ms. Rivera replied with two brief messages.",
                        observed_at=None,
                        source_kind="trigger",
                        source_ref=None,
                    ),
                ),
                belief_changes=(
                    BeliefChange(
                        action="create",
                        topic_key="communication_style",
                        previous_belief_id=None,
                        statement="Ms. Rivera may prefer concise messages.",
                        confidence=0.55,
                        reason="Two brief replies provide limited evidence for a tentative preference.",
                        evidence=(EvidenceReference("short_replies", "supports"),),
                    ),
                ),
                strategy_change=StrategyChange(
                    action="create",
                    previous_strategy_id=None,
                    goal="Learn how Ms. Rivera prefers to communicate.",
                    plan="Keep messages short and ask one question at a time.",
                    rationale="This tests the tentative concise-message belief without overcommitting to it.",
                    confidence=0.55,
                    reason="Initial strategy based on limited communication evidence.",
                    belief_refs=("communication_style",),
                ),
                decision=DecisionSummary(
                    observed="The user reported two brief replies.",
                    concluded="A concise-message preference is possible but uncertain.",
                    decided="Try concise messages while gathering more evidence.",
                    confidence=0.55,
                ),
            )

        belief = current_state["beliefs"][0]
        strategy = current_state["strategy"]
        old_observation = current_state["observations"][0]
        return CoachStateUpdate(
            relationship=RelationshipChange(
                action="none",
                relationship_id=current_state["relationship"]["id"],
                person_name=None,
                role=None,
                child=None,
                school=None,
                desired_state=None,
            ),
            interaction=None,
            observations=(
                ObservationDraft(
                    key="detailed_email",
                    statement="Ms. Rivera sent a long, detailed email with several follow-up questions.",
                    observed_at=None,
                    source_kind="trigger",
                    source_ref=None,
                ),
            ),
            belief_changes=(
                BeliefChange(
                    action="revise",
                    topic_key="communication_style",
                    previous_belief_id=belief["id"],
                    statement="Ms. Rivera's preferred message length is unclear and may depend on context.",
                    confidence=0.35,
                    reason="The detailed email contradicts the earlier inference from two brief replies.",
                    evidence=(
                        EvidenceReference(old_observation["id"], "supports"),
                        EvidenceReference("detailed_email", "contradicts"),
                    ),
                ),
            ),
            strategy_change=StrategyChange(
                action="revise",
                previous_strategy_id=strategy["id"],
                goal="Learn how communication preferences vary by channel and context.",
                plan="Match the channel and context instead of optimizing only for brevity.",
                rationale="The earlier concise-only strategy no longer fits the mixed evidence.",
                confidence=0.45,
                reason="The communication-style belief changed.",
                belief_refs=(belief["id"],),
            ),
            decision=DecisionSummary(
                observed="The user corrected the record with a detailed teacher email.",
                concluded="Message length alone does not establish a stable preference.",
                decided="Revise the belief and adapt the strategy to context.",
                confidence=0.45,
            ),
        )


class SchoolCoachClawTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.db_path = root / "n4os.db"
        self.n4os_root = root / "n4os"
        self.n4os_root.mkdir()
        self.provider = SQLiteSchoolCoachProvider(self.db_path)
        self.claw = SchoolCoachClaw(
            self.provider,
            model=LearningModel(),
            n4os_root=self.n4os_root,
        )

    def test_observation_belief_strategy_then_contradictory_correction(self):
        first = self.claw.handle_request(
            "/school-coach Focus on Ms. Rivera. She replied with two brief messages.",
            provenance=CoachProvenance("user_report", "telegram:99:123:1", "dad"),
        )

        relationship = self.provider.list_relationships()[0]
        state = self.provider.current_state(relationship["id"])
        self.assertIn("Current belief: Ms. Rivera may prefer concise messages.", first)
        self.assertIsNone(relationship["role"])
        self.assertIsNone(relationship["child"])
        self.assertIsNone(relationship["school"])
        self.assertEqual(state["beliefs"][0]["confidence"], 0.55)
        self.assertEqual(
            state["strategy"]["plan"],
            "Keep messages short and ask one question at a time.",
        )
        evidence = self.provider.evidence_for_belief(state["beliefs"][0]["id"])
        self.assertEqual(evidence[0]["provenance_ref"], "telegram:99:123:1")
        self.assertEqual(evidence[0]["provenance_kind"], "user_report")

        restarted = SchoolCoachClaw(
            SQLiteSchoolCoachProvider(self.db_path),
            model=None,
            n4os_root=self.n4os_root,
        )
        plan = restarted.handle_request(
            "/school-coach What's your current plan for Ms. Rivera?",
            provenance=CoachProvenance("user_report", "telegram:99:123:2"),
        )
        self.assertIn("Keep messages short", plan)

        second = self.claw.handle_request(
            "/school-coach Actually, Ms. Rivera sent a long detailed email with several questions.",
            provenance=CoachProvenance("user_report", "telegram:99:123:3", "dad"),
        )

        state = self.provider.current_state(relationship["id"])
        self.assertIn("preferred message length is unclear", second)
        self.assertEqual(state["beliefs"][0]["confidence"], 0.35)
        self.assertEqual(
            state["strategy"]["plan"],
            "Match the channel and context instead of optimizing only for brevity.",
        )
        self.assertEqual(state["strategy"]["belief_refs"], [state["beliefs"][0]["id"]])
        history = self.provider.belief_history(relationship["id"])
        self.assertEqual([item["status"] for item in history], ["superseded", "active"])
        self.assertEqual(history[1]["previous_belief_id"], history[0]["id"])
        strategy_history = self.provider.strategy_history(relationship["id"])
        self.assertEqual([item["status"] for item in strategy_history], ["superseded", "active"])
        self.assertEqual(strategy_history[1]["previous_strategy_id"], strategy_history[0]["id"])
        revised_evidence = self.provider.evidence_for_belief(history[1]["id"])
        self.assertEqual(
            {item["stance"] for item in revised_evidence},
            {"supports", "contradicts"},
        )
        correction = next(
            item for item in revised_evidence if item["provenance_ref"] == "telegram:99:123:3"
        )
        self.assertEqual(correction["provenance_kind"], "user_correction")

        why = restarted.handle_request(
            "/school-coach Why do you think that about Ms. Rivera?",
            provenance=CoachProvenance("user_report", "telegram:99:123:4"),
        )
        self.assertIn("Supports: Ms. Rivera replied with two brief messages.", why)
        self.assertIn("Contradicts: Ms. Rivera sent a long, detailed email", why)
        self.assertNotIn("telegram:", why)
        self.assertNotIn("n4os/", why)
        self.assertLessEqual(len(why.splitlines()), 16)

        history_reply = restarted.handle_request(
            "/school-coach Show belief and strategy history for Ms. Rivera",
            provenance=CoachProvenance("user_report", "telegram:99:123:5"),
        )
        self.assertIn("communication_style v1 [superseded]", history_reply)
        self.assertIn("communication_style v2 [active]", history_reply)
        self.assertIn("v1 [superseded]", history_reply)
        self.assertIn("v2 [active]", history_reply)

    def test_day_zero_discovers_one_teacher_without_persisting(self):
        school_root = self.n4os_root / "school" / "Nysha" / "2026-2027"
        school_root.mkdir(parents=True)
        (school_root / "School Knowledge.md").write_text(
            "### People And Relationships\n\n- Mrs. Suzanne Thompson\n- Teacher\n",
            encoding="utf-8",
        )

        reply = self.claw.handle_request(
            "/school-coach",
            provenance=CoachProvenance("user_report", "telegram:99:123:6"),
        )

        self.assertIn("I found Mrs. Suzanne Thompson", reply)
        self.assertEqual(self.provider.list_relationships(), [])

    def test_command_and_spoken_school_coach_prefixes_are_equivalent(self):
        cases = {
            "/school coach": "",
            "/school_coach current plan for Ms. X": "current plan for Ms. X",
            "/school-coach current plan for Ms. X": "current plan for Ms. X",
            "School coach, current plan for Ms. X": "current plan for Ms. X",
            "Ask the school coach about Ms. X": "about Ms. X",
            "Please talk to the school coach: history for Ms. X": "history for Ms. X",
        }
        for request, expected in cases.items():
            with self.subTest(request=request):
                self.assertTrue(is_school_coach_message(request))
                self.assertEqual(strip_school_coach_prefix(request), expected)

        self.assertFalse(is_school_coach_message("Please coach me through a work decision"))

        natural_requests = (
            "What's your current plan for Mrs. Thompson?",
            "Show me your strategy for Ms. Rivera",
            "Why do you think that about Dr. Shah?",
            "Help me build a better relationship with Miss Kelly",
        )
        for request in natural_requests:
            with self.subTest(request=request):
                self.assertTrue(is_school_coach_message(request))
                self.assertEqual(strip_school_coach_prefix(request), request)

        self.assertFalse(is_school_coach_message("What's your current plan for retirement?"))

    def test_unknown_relationship_query_does_not_call_model(self):
        reply = self.claw.handle_request(
            "/school-coach What's your current plan for Ms. Unknown?",
            provenance=CoachProvenance("user_report", "telegram:99:123:7"),
        )

        self.assertIn("do not have that relationship", reply)

    def test_named_request_does_not_fall_back_to_only_saved_relationship(self):
        self.claw.handle_request(
            "/school-coach Focus on Ms. Rivera. She replied with two brief messages.",
            provenance=CoachProvenance("user_report", "telegram:99:123:8"),
        )

        reply = self.claw.handle_request(
            "/school-coach What's your current plan for Ms. Thompson?",
            provenance=CoachProvenance("user_report", "telegram:99:123:9"),
        )

        self.assertIn("do not have that relationship", reply)
        self.assertNotIn("Keep messages short", reply)

    def test_prefix_name_does_not_match_a_different_named_teacher(self):
        prefix_model = LearningModel()
        original_decide = prefix_model.decide

        def create_ms_ann(request, **kwargs):
            update = original_decide(request, **kwargs)
            return CoachStateUpdate(
                relationship=RelationshipChange(
                    action=update.relationship.action,
                    relationship_id=update.relationship.relationship_id,
                    person_name="Ms. Ann",
                    role=update.relationship.role,
                    child=update.relationship.child,
                    school=update.relationship.school,
                    desired_state=update.relationship.desired_state,
                ),
                interaction=update.interaction,
                observations=update.observations,
                belief_changes=update.belief_changes,
                strategy_change=update.strategy_change,
                decision=update.decision,
            )

        prefix_model.decide = create_ms_ann
        claw = SchoolCoachClaw(self.provider, model=prefix_model, n4os_root=self.n4os_root)
        claw.handle_request(
            "/school-coach Focus on Ms. Ann. She replied with two brief messages.",
            provenance=CoachProvenance("user_report", "telegram:99:123:10"),
        )

        reply = claw.handle_request(
            "/school-coach What's your current plan for Ms. Anne?",
            provenance=CoachProvenance("user_report", "telegram:99:123:11"),
        )

        self.assertIn("do not have that relationship", reply)

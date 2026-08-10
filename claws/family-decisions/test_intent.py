import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from intent import extract_intent


REFERENCE_TIME = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))


class FamilyDecisionIntentTest(unittest.TestCase):
    def test_extracts_decision_capture(self):
        intent = extract_intent(
            "Track decision about summer camp plan by next Monday owner mom",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["intent"], "create_decision")
        self.assertEqual(intent["title"], "Summer camp plan")
        self.assertEqual(intent["owner"], "mom")
        self.assertEqual(intent["due"], "2026-07-06")
        self.assertEqual(intent["size"], "large")

    def test_extracts_household_owner_aliases(self):
        cases = {
            "mummy": "mom",
            "namesh": "dad",
            "niyaati": "mom",
            "papu": "dad",
            "dadi": "grandmom",
        }

        for alias, owner in cases.items():
            with self.subTest(alias=alias):
                intent = extract_intent(
                    f"Track decision about weekend logistics owner {alias}",
                    now=REFERENCE_TIME,
                )
                self.assertEqual(intent["owner"], owner)
                self.assertNotIn(alias, intent["title"].lower())

    def test_extracts_captured_decision_prefix(self):
        intent = extract_intent(
            "Captured decision: Summer camp plan for Nysha for the last week. "
            "Options are stay at home, go to ICC, challenges she will be jetlagged",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["intent"], "create_decision")
        self.assertTrue(intent["title"].startswith("Summer camp plan"))
        self.assertNotIn("Captured decision", intent["title"])
        self.assertNotIn("Options are", intent["title"])
        self.assertEqual(intent["initial_options"], ["Stay at home", "Go to ICC"])
        self.assertEqual(intent["initial_evidence"], ["She will be jetlagged"])

    def test_thought_like_decision_capture_uses_clean_title(self):
        intent = extract_intent(
            "I had a decision to choose summer camp owner dad by next Monday "
            "options are ICC or home",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["intent"], "create_decision")
        self.assertEqual(intent["title"], "Summer camp")
        self.assertEqual(intent["owner"], "dad")
        self.assertEqual(intent["due"], "2026-07-06")
        self.assertEqual(intent["initial_options"], ["ICC", "Home"])

    def test_marks_researching_when_ai_help_requested(self):
        intent = extract_intent(
            "Decide school option, ask Noah to compare waitlist and commute",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["intent"], "create_decision")
        self.assertEqual(intent["status"], "researching")
        self.assertTrue(intent["assistant_help_needed"])

    def test_treats_common_brief_typo_as_decision_brief(self):
        intent = extract_intent("give me decision bried", now=REFERENCE_TIME)

        self.assertEqual(intent["intent"], "decision_brief")

    def test_treats_pending_decisions_as_list_request(self):
        for request in (
            "tell me the pending decisions",
            "pending decisions",
            "what are the pending decisions",
        ):
            with self.subTest(request=request):
                intent = extract_intent(request, now=REFERENCE_TIME)
                self.assertEqual(intent["intent"], "list_decisions")

    def test_close_decision_number_wins_over_brief_typo(self):
        intent = extract_intent(
            "Close the decision 2. Give me decision bried done",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["intent"], "record_decision")
        self.assertEqual(intent["decision_index"], 2)
        self.assertEqual(intent["outcome"], "Give me decision brief done")

    def test_treats_follow_up_options_as_latest_decision_update(self):
        intent = extract_intent("options are stay at home, go to ICC", now=REFERENCE_TIME)

        self.assertEqual(intent["intent"], "add_option")
        self.assertIsNone(intent["decision_id"])
        self.assertEqual(intent["texts"], ["Stay at home", "Go to ICC"])

    def test_treats_voice_note_challenge_as_evidence_update(self):
        intent = extract_intent("challenges she will be jet lagged", now=REFERENCE_TIME)

        self.assertEqual(intent["intent"], "add_evidence")
        self.assertIsNone(intent["decision_id"])
        self.assertEqual(intent["text"], "She will be jetlagged")

    def test_does_not_treat_plain_option_in_title_as_option_update(self):
        intent = extract_intent("Decide school option for Nysha", now=REFERENCE_TIME)

        self.assertEqual(intent["intent"], "create_decision")
        self.assertEqual(intent["title"], "School option for Nysha")

    def test_extracts_option_evidence_next_step_and_outcome(self):
        self.assertEqual(
            extract_intent("Add option abc123: Camp A")["intent"],
            "add_option",
        )
        self.assertEqual(
            extract_intent("Add evidence abc123: Camp A costs $500")["intent"],
            "add_evidence",
        )
        next_step = extract_intent(
            "Add next step abc123: call camp owner papa by tomorrow",
            now=REFERENCE_TIME,
        )
        self.assertEqual(next_step["intent"], "add_next_step")
        self.assertEqual(next_step["owner"], "dad")
        self.assertEqual(next_step["due"], "2026-07-04")

        decided = extract_intent("We decided abc123: choose Camp A")
        self.assertEqual(decided["intent"], "record_decision")
        self.assertEqual(decided["outcome"], "choose Camp A")

    def test_classifies_explicit_family_backlog_examples(self):
        discussion = extract_intent("Discussion: Should we attend the birthday?", now=REFERENCE_TIME)
        planning = extract_intent("Planning: Camping trip September 12", now=REFERENCE_TIME)
        decision = extract_intent("Decision: Choose Nysha's school next year", now=REFERENCE_TIME)

        self.assertEqual((discussion["intent"], discussion["kind"]), ("create_backlog_item", "discussion"))
        self.assertEqual(discussion["title"], "Should we attend the birthday?")
        self.assertEqual((planning["kind"], planning["title"], planning["due"]), ("planning", "Camping trip", "2026-09-12"))
        self.assertEqual((decision["kind"], decision["title"]), ("decision", "Choose Nysha's school next year"))

    def test_natural_questions_default_to_discussion_unless_consequential(self):
        lightweight = extract_intent("Should we go to the birthday this weekend?", now=REFERENCE_TIME)
        consequential = extract_intent("Should we change Nysha's school?", now=REFERENCE_TIME)

        self.assertEqual(lightweight["kind"], "discussion")
        self.assertEqual(consequential["kind"], "decision")


if __name__ == "__main__":
    unittest.main()

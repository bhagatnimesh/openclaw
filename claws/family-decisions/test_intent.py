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
            "Add next step abc123: call camp owner dad by tomorrow",
            now=REFERENCE_TIME,
        )
        self.assertEqual(next_step["intent"], "add_next_step")
        self.assertEqual(next_step["owner"], "dad")
        self.assertEqual(next_step["due"], "2026-07-04")

        decided = extract_intent("We decided abc123: choose Camp A")
        self.assertEqual(decided["intent"], "record_decision")
        self.assertEqual(decided["outcome"], "choose Camp A")


if __name__ == "__main__":
    unittest.main()

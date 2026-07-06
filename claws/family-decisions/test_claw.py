import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

from claw import FamilyDecisionsClaw
from provider import SQLiteFamilyDecisionProvider


REFERENCE_TIME = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))


class FamilyDecisionsClawTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        provider = SQLiteFamilyDecisionProvider(Path(self.tmp.name) / "decisions.sqlite")
        self.claw = FamilyDecisionsClaw.from_provider(provider)

    def tearDown(self):
        self.tmp.cleanup()

    def test_captures_decision_from_request(self):
        output = StringIO()

        with redirect_stdout(output):
            message = self.claw.capture_decision_from_request(
                "Track decision about summer camp plan owner mom by next Monday",
                reference_time=REFERENCE_TIME,
            )

        self.assertIn("Captured decision: Summer camp plan", message)
        self.assertIn("Owner: mom", output.getvalue())

    def test_brief_shows_ai_missing_fields(self):
        self.claw.capture_decision_from_request(
            "Track decision about school choice",
            reference_time=REFERENCE_TIME,
        )
        decision_id = self.claw.tools.list_decisions()["data"]["decisions"][0]["id"]

        message = self.claw.decision_brief_from_request(f"Decision brief {decision_id[:8]}")

        self.assertIn("AI assist:", message)
        self.assertIn("Missing:", message)

    def test_brief_without_id_uses_latest_open_decision(self):
        self.claw.capture_decision_from_request(
            "Captured decision: Summer camp plan for Nysha for the last week",
            reference_time=REFERENCE_TIME,
        )

        message = self.claw.decision_brief_from_request("provide decision brief")

        self.assertIn("Using latest open decision: Summer camp plan", message)
        self.assertIn("Decision brief: Summer camp plan", message)

    def test_pending_decisions_request_lists_instead_of_captures(self):
        self.claw.capture_decision_from_request(
            "Captured decision: Summer camp plan",
            reference_time=REFERENCE_TIME,
        )

        message = self.claw.handle_request(
            "tell me the pending decisions",
            reference_time=REFERENCE_TIME,
        )

        self.assertIn("Pending family decisions (1):", message)
        self.assertIn("Summer camp plan", message)
        self.assertIn("Owner: unassigned | Due: not set | Status: inbox", message)
        self.assertIn("Missing: owner, timeline, options, next_step", message)
        self.assertIn("Ref:", message)
        self.assertNotIn("owner=unknown due=no due date", message)
        self.assertNotIn("Captured decision: Tell me the pending decisions", message)

    def test_list_flags_accidental_command_captures(self):
        self.claw.tools.create_decision("Give me decision bried")

        message = self.claw.handle_request(
            "what are pending decisions?",
            reference_time=REFERENCE_TIME,
        )

        self.assertIn("Give me decision bried", message)
        self.assertIn("Note: this looks like an accidental command capture.", message)

    def test_close_decision_by_display_number(self):
        self.claw.tools.create_decision("Summer camp plan", urgency="high")
        junk = self.claw.tools.create_decision("Give me decision bried")["data"]["decision"]

        message = self.claw.handle_request(
            "Close the decision 2. Give me decision bried done",
            reference_time=REFERENCE_TIME,
        )
        open_list = self.claw.handle_request(
            "what are pending decisions?",
            reference_time=REFERENCE_TIME,
        )
        closed = self.claw.tools.read_decision(junk["id"])["data"]["decision"]

        self.assertIn("Recorded decision.", message)
        self.assertIn("Outcome: Give me decision brief done", message)
        self.assertEqual(closed["status"], "decided")
        self.assertNotIn("Give me decision bried", open_list)
        self.assertIn("Summer camp plan", open_list)

    def test_close_all_decisions_clarifies_without_capturing(self):
        self.claw.tools.create_decision("Summer camp plan")

        message = self.claw.handle_request(
            "close all decisions",
            reference_time=REFERENCE_TIME,
        )
        open_list = self.claw.tools.list_decisions()["data"]["decisions"]

        self.assertIn("one decision at a time", message)
        self.assertEqual(len(open_list), 1)
        self.assertEqual(open_list[0]["title"], "Summer camp plan")

    def test_undo_reverts_accidental_decision_capture(self):
        self.claw.capture_decision_from_request(
            "Captured decision: Summer camp plan",
            reference_time=REFERENCE_TIME,
        )

        message = self.claw.undo_last_action()
        open_list = self.claw.tools.list_decisions()["data"]["decisions"]

        self.assertIn("Undid decision capture", message)
        self.assertEqual(open_list, [])

    def test_undo_reverts_decision_update(self):
        decision = self.claw.tools.create_decision("Summer camp plan")["data"]["decision"]

        self.claw.add_option_from_request(
            f"Option {decision['id'][:8]}: stay home",
            reference_time=REFERENCE_TIME,
        )
        message = self.claw.undo_last_action()
        restored = self.claw.tools.read_decision(decision["id"])["data"]["decision"]

        self.assertIn("Undid decision update", message)
        self.assertEqual(restored["options"], [])

    def test_capture_splits_voice_note_options_and_evidence(self):
        message = self.claw.capture_decision_from_request(
            "Captured decision: Summer camp plan for Nysha for the last week. "
            "Options are stay at home, go to ICC, challenges she will be jet lagged",
            reference_time=REFERENCE_TIME,
        )

        self.assertIn("Captured details: 2 options, 1 evidence note.", message)
        brief = self.claw.decision_brief_from_request("give me decision bried")
        self.assertIn("Decision brief: Summer camp plan for Nysha for the last week", brief)
        self.assertIn("- Stay at home", brief)
        self.assertIn("- Go to ICC", brief)
        self.assertIn("- She will be jetlagged", brief)

    def test_follow_up_option_and_note_attach_to_latest_decision(self):
        self.claw.capture_decision_from_request(
            "Captured decision: Summer camp plan",
            reference_time=REFERENCE_TIME,
        )

        options_message = self.claw.handle_request(
            "options are stay at home, go to ICC",
            reference_time=REFERENCE_TIME,
        )
        note_message = self.claw.handle_request(
            "Added note to call FUSD to get the waitlist number",
            reference_time=REFERENCE_TIME,
        )

        self.assertIn("Added options.", options_message)
        self.assertIn("- Stay at home", note_message)
        self.assertIn("- Go to ICC", note_message)
        self.assertIn("- Call FUSD to get the waitlist number", note_message)


if __name__ == "__main__":
    unittest.main()

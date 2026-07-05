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

import tempfile
import unittest
from pathlib import Path

from provider import SQLiteFamilyDecisionProvider
from tools import FamilyDecisionTools, build_decision_brief


class FamilyDecisionToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.provider = SQLiteFamilyDecisionProvider(Path(self.tmp.name) / "decisions.sqlite")
        self.tools = FamilyDecisionTools(self.provider)

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_decision_reports_ai_action_gaps(self):
        response = self.tools.create_decision("Summer camp plan", urgency="high")

        self.assertEqual(response["status"], "ok")
        decision = response["data"]["decision"]
        self.assertEqual(decision["title"], "Summer camp plan")
        self.assertIn("owner", response["data"]["gaps"])
        self.assertIn("next_step", response["data"]["gaps"])

    def test_adds_option_evidence_next_step_and_brief(self):
        decision = self.tools.create_decision(
            "Birthday party",
            owner="both",
            due="2026-07-08",
        )["data"]["decision"]

        self.tools.add_option(decision["id"], "Go to the party")
        self.tools.add_evidence(decision["id"], "It overlaps nap time")
        updated = self.tools.add_next_step(
            decision["id"],
            "Ask host about end time",
            owner="mom",
            due="2026-07-05",
        )["data"]["decision"]

        brief = build_decision_brief(updated)
        self.assertIn("Go to the party", brief)
        self.assertIn("It overlaps nap time", brief)
        self.assertIn("Ask host about end time", brief)

    def test_brief_without_id_uses_latest_open_decision(self):
        self.tools.create_decision("Older decision")
        latest = self.tools.create_decision("Summer camp plan")["data"]["decision"]

        response = self.tools.decision_brief(None)

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["decision"]["id"], latest["id"])
        self.assertIn("Summer camp plan", response["message"])

    def test_decide_closes_decision_with_rationale(self):
        decision = self.tools.create_decision("School choice")["data"]["decision"]

        response = self.tools.decide(
            decision["id"],
            "Choose Mission Valley Montessori",
            rationale="Best fit for friendship and commute.",
        )

        self.assertEqual(response["status"], "ok")
        decided = response["data"]["decision"]
        self.assertEqual(decided["status"], "decided")
        self.assertEqual(decided["outcome"], "Choose Mission Valley Montessori")
        self.assertEqual(decided["rationale"], "Best fit for friendship and commute.")


if __name__ == "__main__":
    unittest.main()

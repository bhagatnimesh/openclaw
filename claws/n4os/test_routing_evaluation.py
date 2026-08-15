import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from routing_evaluation import acceptance_failures, evaluate_routing, load_evaluation_cases


REFERENCE_TIME = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))


class RoutingEvaluationTest(unittest.TestCase):
    def test_corpus_has_private_holdout_and_all_modalities(self):
        cases = load_evaluation_cases()

        self.assertGreaterEqual(len(cases), 25)
        self.assertEqual({case.modality for case in cases}, {"explicit", "natural", "clarification"})
        self.assertTrue(any(case.split == "holdout" for case in cases))
        self.assertTrue(
            all(
                case.origin == "history_redacted"
                for case in cases
                if case.split == "holdout"
            )
        )

    def test_router_meets_acceptance_thresholds(self):
        report = evaluate_routing(load_evaluation_cases(), now=REFERENCE_TIME)

        self.assertEqual(acceptance_failures(report), (), report)
        self.assertEqual(report.failed_case_ids, (), report)


if __name__ == "__main__":
    unittest.main()

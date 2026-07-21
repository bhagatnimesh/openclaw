from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest

from n4os_review import (
    format_n4os_review,
    is_n4os_review_message,
    parse_review_period,
)


class N4OSReviewTest(unittest.TestCase):
    def test_detects_review_commands(self):
        self.assertTrue(is_n4os_review_message("/review week"))
        self.assertEqual(parse_review_period("/review today"), "day")
        self.assertEqual(parse_review_period("/review month"), "month")
        self.assertFalse(is_n4os_review_message("/capture review this"))

    def test_week_review_summarizes_without_promotion_mutation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            (n4os_root / "journal").mkdir(parents=True)
            (n4os_root / "family" / "observations").mkdir(parents=True)
            (n4os_root / "journal" / "2026-07-20.md").write_text(
                "\n".join(
                    [
                        "---",
                        "type: journal",
                        "date: 2026-07-20",
                        "---",
                        "# Journal - 2026-07-20",
                        "## Captures",
                        "- I slept badly and felt scattered.",
                        "  Topics: [[playbooks/Health|Health]], [[Attention]]",
                    ]
                ),
                encoding="utf-8",
            )
            (n4os_root / "family" / "observations" / "2026-07.md").write_text(
                "\n".join(
                    [
                        "# Family Observations - 2026-07",
                        "## 2026-07-21",
                        "### [[family/Nysha|Nysha]]",
                        "- Observation: nervous about [[School Transition|new classmates]]",
                        "  Topics: [[Confidence]], [[School Transition]]",
                    ]
                ),
                encoding="utf-8",
            )

            review = format_n4os_review(
                "week",
                n4os_root=n4os_root,
                reference_date=date(2026, 7, 21),
            )

        self.assertIn("N4OS week review", review)
        self.assertIn("Repeated signals:", review)
        self.assertIn("Promotion candidates:", review)
        self.assertIn("No stable N4OS files were changed", review)


if __name__ == "__main__":
    unittest.main()

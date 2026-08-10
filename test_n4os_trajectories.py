from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from n4os_trajectories import (
    read_recent_trajectory_summaries,
    record_n4os_trajectory,
    trajectory_review_signals,
)


class N4OSTrajectoriesTest(unittest.TestCase):
    def test_records_full_ask_trajectory_with_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            record_n4os_trajectory(
                mode="ask",
                user_text="How do I approach Nysha's first week at school?",
                assistant_text="Use a calm first-week plan and watch for confidence signals.",
                context_labels=["SOUL", "Nysha", "Parenting"],
                summary="Nysha school transition: use calm practice and watch confidence signals.",
                n4os_root=n4os_root,
                captured_at=datetime(2026, 8, 8, 21, 15),
                model="gpt-5.4-mini",
            )

            path = n4os_root / "trajectories" / "2026-08.md"
            text = path.read_text(encoding="utf-8")

        self.assertIn("type: trajectory", text)
        self.assertIn("- Mode: ask", text)
        self.assertIn("- Context: SOUL, Nysha, Parenting", text)
        self.assertIn("User:", text)
        self.assertIn("How do I approach Nysha", text)
        self.assertIn("Assistant:", text)
        self.assertIn("calm first-week plan", text)

    def test_reads_recent_matching_summaries_for_future_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            record_n4os_trajectory(
                mode="chat",
                user_text="school transition",
                assistant_text="School transition summary",
                context_labels=[],
                summary="Nysha school transition needs practice and safety.",
                n4os_root=n4os_root,
                captured_at=datetime(2026, 8, 8, 21, 15),
            )
            record_n4os_trajectory(
                mode="chat",
                user_text="career",
                assistant_text="Career summary",
                context_labels=[],
                summary="Career decision should optimize learning.",
                n4os_root=n4os_root,
                captured_at=datetime(2026, 8, 8, 22, 15),
            )

            summaries = read_recent_trajectory_summaries(
                n4os_root / "trajectories",
                lowered_request="Nysha school",
            )

        self.assertEqual(len(summaries), 1)
        self.assertIn("school transition", summaries[0])

    def test_review_signals_parse_trajectory_summaries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            record_n4os_trajectory(
                mode="ask",
                user_text="Nysha school",
                assistant_text="Nysha needs a bridge plan.",
                context_labels=[],
                summary="Nysha school transition needs a bridge plan.",
                n4os_root=n4os_root,
                captured_at=datetime(2026, 8, 8, 21, 15),
            )

            signals = trajectory_review_signals(
                n4os_root / "trajectories",
                start=datetime(2026, 8, 1).date(),
                end=datetime(2026, 8, 9).date(),
            )

        self.assertEqual(len(signals), 1)
        captured_on, summary, topics = signals[0]
        self.assertEqual(captured_on.isoformat(), "2026-08-08")
        self.assertIn("school transition", summary)
        self.assertIn("School Transition", topics)


if __name__ == "__main__":
    unittest.main()

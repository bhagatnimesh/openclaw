from __future__ import annotations

import unittest

from n4os_goals_status import format_goals_status, is_goals_status_message


class N4OSGoalsStatusTest(unittest.TestCase):
    def test_detects_current_goal_questions(self):
        self.assertTrue(is_goals_status_message("/goals"))
        self.assertTrue(is_goals_status_message("what are my current goals?"))
        self.assertTrue(is_goals_status_message("what are my priorities?"))
        self.assertFalse(is_goals_status_message("how do I add a goal?"))

    def test_format_goals_status_includes_2026_and_2036(self):
        status = format_goals_status()

        self.assertIn("N4OS current goals", status)
        self.assertIn("2026 theme", status)
        self.assertIn("Health:", status)
        self.assertIn("Family:", status)
        self.assertIn("AI:", status)
        self.assertIn("2036 north star", status)
        self.assertIn("n4os/goals/2026.md", status)


if __name__ == "__main__":
    unittest.main()

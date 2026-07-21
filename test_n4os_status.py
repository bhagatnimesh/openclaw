from __future__ import annotations

import unittest
from unittest.mock import patch

from n4os_status import (
    format_n4os_status,
    is_n4os_status_message,
    parse_status_target,
)


class N4OSStatusTest(unittest.TestCase):
    def test_detects_status_commands(self):
        self.assertTrue(is_n4os_status_message("/status Nysha"))
        self.assertTrue(is_n4os_status_message("/status goals"))
        self.assertEqual(parse_status_target("/status week"), "week")
        self.assertEqual(parse_status_target("/status"), "reading")
        self.assertFalse(is_n4os_status_message("/memory-status Nysha"))

    def test_formats_known_status_targets(self):
        with (
            patch("n4os_status.format_memory_status", return_value="nysha status") as memory,
            patch("n4os_status.format_goals_status", return_value="goals status") as goals,
            patch("n4os_status.format_n4os_review", return_value="week review") as review,
        ):
            self.assertEqual(format_n4os_status("Nysha"), "nysha status")
            self.assertEqual(format_n4os_status("goals"), "goals status")
            self.assertEqual(format_n4os_status("week"), "week review")
            self.assertIsNone(format_n4os_status("reading"))

        memory.assert_called_once_with("nysha")
        goals.assert_called_once_with()
        review.assert_called_once_with("week")


if __name__ == "__main__":
    unittest.main()

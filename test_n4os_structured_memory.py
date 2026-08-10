from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest

from n4os_structured_memory import (
    format_structured_memory_query,
    is_structured_memory_query,
    is_structured_remember_message,
    remember_structured_memory,
)


class N4OSStructuredMemoryTest(unittest.TestCase):
    def test_remember_records_last_dinner_pickups_in_date_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Niyati picked up dinner tonight",
                n4os_root=n4os_root,
                today=date(2026, 8, 9),
            )
            remember_structured_memory(
                "/remember Nimesh picked up dinner yesterday",
                n4os_root=n4os_root,
                today=date(2026, 8, 9),
            )
            remember_structured_memory(
                "/remember Niyati picked up dinner today",
                n4os_root=n4os_root,
                today=date(2026, 8, 7),
            )

            reply = format_structured_memory_query(
                "who picked the last 3 dinners?",
                n4os_root=n4os_root,
            )

        self.assertEqual(
            reply,
            "Last 3 dinner pickups:\n"
            "1. 2026-08-09: Niyati\n"
            "2. 2026-08-08: Nimesh\n"
            "3. 2026-08-07: Niyati",
        )

    def test_remember_records_next_dinner_pickup_assignment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            result = remember_structured_memory(
                "remember Niyati has the next dinner pickup",
                n4os_root=n4os_root,
                today=date(2026, 8, 9),
            )
            reply = format_structured_memory_query(
                "whose turn is it to pick up dinner?",
                n4os_root=n4os_root,
            )

        self.assertEqual(result.reply, "Remembered. Next dinner pickup: Niyati.")
        self.assertEqual(reply, "Next dinner pickup: Niyati.\nSource: Telegram.")

    def test_unresolved_pickup_note_answers_without_inventing_owner(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember dinner pickup is unresolved; do not assume the same person",
                n4os_root=n4os_root,
            )
            reply = format_structured_memory_query(
                "whose turn is it to pick up dinner?",
                n4os_root=n4os_root,
            )

        self.assertEqual(
            reply,
            "I do not have a locked dinner pickup owner yet.\n"
            "\n"
            "Current memory:\n"
            "- dinner pickup is unresolved; do not assume the same person",
        )

    def test_message_detection(self):
        self.assertTrue(is_structured_remember_message("/remember Niyati picked up dinner"))
        self.assertTrue(is_structured_remember_message("remember Niyati picked up dinner"))
        self.assertFalse(is_structured_remember_message("remember to add milk"))
        self.assertTrue(is_structured_memory_query("who picked the last 3 dinners?"))


if __name__ == "__main__":
    unittest.main()

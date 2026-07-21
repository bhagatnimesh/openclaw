from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest

from n4os_memory_inbox import (
    format_memory_ingest_reply,
    ingest_memory_inbox_notes,
    is_memory_inbox_message,
    parse_memory_inbox_notes,
)


class N4OSMemoryInboxTest(unittest.TestCase):
    def test_parse_memory_inbox_notes_uses_dates_and_people(self):
        observations = parse_memory_inbox_notes(
            "\n".join(
                [
                    "/mem-inbox",
                    "2026-07-21",
                    "Nysha liked teaching younger kids",
                    "Navya: said I love Maths",
                    "2026-07-22 Family: both kids enjoyed little kids company",
                ]
            ),
            default_date=date(2026, 7, 20),
        )

        self.assertEqual(
            [
                (item.observed_on.isoformat(), item.person, item.observation)
                for item in observations
            ],
            [
                ("2026-07-21", "Nysha", "liked teaching younger kids"),
                ("2026-07-21", "Navya", "said I love Maths"),
                ("2026-07-22", "Family", "both kids enjoyed little kids company"),
            ],
        )

    def test_ingest_appends_month_file_and_skips_duplicates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "observations"
            text = "\n".join(
                [
                    "N4OS Memory Inbox",
                    "2026-07-21",
                    "Nysha: loved gymnastics and climbing",
                    "Nysha: loved gymnastics and climbing",
                    "Navya: enjoys reflection through books",
                ]
            )

            first = ingest_memory_inbox_notes(
                text,
                observations_root=root,
                default_date=date(2026, 7, 20),
            )
            second = ingest_memory_inbox_notes(
                text,
                observations_root=root,
                default_date=date(2026, 7, 20),
            )

            month_text = (root / "2026-07.md").read_text(encoding="utf-8")

        self.assertEqual(len(first.added), 2)
        self.assertEqual(len(first.skipped_duplicates), 1)
        self.assertEqual(len(second.added), 0)
        self.assertEqual(len(second.skipped_duplicates), 3)
        self.assertIn("# Family Observations - 2026-07", month_text)
        self.assertIn("### Nysha", month_text)
        self.assertIn("- Observation: loved gymnastics and climbing", month_text)
        self.assertIn("### Navya", month_text)
        self.assertIn("- Observation: enjoys reflection through books", month_text)

    def test_message_detection_and_empty_reply(self):
        self.assertTrue(is_memory_inbox_message("/mem Nysha: liked teaching"))
        self.assertTrue(is_memory_inbox_message("N4OS Memory Inbox\nNysha: liked teaching"))
        self.assertFalse(is_memory_inbox_message("/memory-status family"))
        self.assertFalse(is_memory_inbox_message("Add task buy milk"))

        result = ingest_memory_inbox_notes(
            "/mem-inbox",
            observations_root=Path(tempfile.mkdtemp()),
            default_date=date(2026, 7, 21),
        )

        self.assertIn("No memory notes found", format_memory_ingest_reply(result))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest

from n4os_capture import (
    format_capture_reply,
    ingest_capture_notes,
    is_capture_message,
)


class N4OSCaptureTest(unittest.TestCase):
    def test_detects_capture_aliases(self):
        self.assertTrue(is_capture_message("/capture Nysha was nervous"))
        self.assertTrue(is_capture_message("capture Nysha was nervous"))
        self.assertTrue(is_capture_message("/note I felt scattered"))
        self.assertTrue(is_capture_message("note I felt scattered"))
        self.assertTrue(is_capture_message("remember Nysha liked teaching"))
        self.assertTrue(is_capture_message("/mem Nysha liked teaching"))
        self.assertTrue(is_capture_message("/mem-inbox\nNysha liked teaching"))
        self.assertFalse(is_capture_message("/status Nysha"))
        self.assertFalse(is_capture_message("remember to buy milk"))

    def test_family_capture_writes_obsidian_observation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            result = ingest_capture_notes(
                "/capture Nysha was nervous about new classmates",
                n4os_root=n4os_root,
                default_date=date(2026, 7, 21),
            )

            month_text = (n4os_root / "family" / "observations" / "2026-07.md").read_text(
                encoding="utf-8",
            )

        self.assertEqual(len(result.family.added), 1)
        self.assertEqual(len(result.journal_entries), 0)
        self.assertIn('  - "n4os/family"', month_text)
        self.assertIn('  - "n4os/memory"', month_text)
        self.assertIn('  - "[[playbooks/Parenting|Parenting]]"', month_text)
        self.assertIn('  - "[[family/Nysha|Nysha]]"', month_text)
        self.assertIn('  - "[[Confidence]]"', month_text)
        self.assertIn('  - "[[School Transition]]"', month_text)
        self.assertIn("### [[family/Nysha|Nysha]]", month_text)
        self.assertIn("[[School Transition|new classmates]]", month_text)
        self.assertIn("Topics: [[Confidence]], [[School Transition]]", month_text)

    def test_bare_voice_capture_strips_prefix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            result = ingest_capture_notes(
                "Capture Nysha asked why we do not travel business class.",
                n4os_root=n4os_root,
                default_date=date(2026, 7, 21),
            )

            month_text = (n4os_root / "family" / "observations" / "2026-07.md").read_text(
                encoding="utf-8",
            )

        self.assertEqual(len(result.family.added), 1)
        self.assertIn("asked why we do not travel business class", month_text)

    def test_personal_capture_writes_journal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            result = ingest_capture_notes(
                "/note I felt impatient because I slept badly and work felt scattered",
                n4os_root=n4os_root,
                default_date=date(2026, 7, 21),
            )

            journal_text = (n4os_root / "journal" / "2026-07-21.md").read_text(
                encoding="utf-8",
            )

        self.assertEqual(len(result.family.added), 0)
        self.assertEqual(len(result.journal_entries), 1)
        self.assertIn('  - "n4os/journal"', journal_text)
        self.assertIn('  - "n4os/capture"', journal_text)
        self.assertIn('  - "[[daily/Evening|Evening]]"', journal_text)
        self.assertIn('  - "[[reviews/Weekly|Weekly Review]]"', journal_text)
        self.assertIn('  - "[[playbooks/Health|Health]]"', journal_text)
        self.assertIn('  - "[[Attention]]"', journal_text)
        self.assertIn('  - "[[playbooks/Career|Work]]"', journal_text)
        self.assertIn("type: journal", journal_text)
        self.assertIn("[[Attention|impatient]]", journal_text)
        self.assertIn("[[playbooks/Health|slept]]", journal_text)
        self.assertIn("[[playbooks/Career|work]]", journal_text)
        self.assertIn("Topics: [[playbooks/Health|Health]], [[Attention]], [[playbooks/Career|Work]]", journal_text)

    def test_mixed_capture_writes_family_and_journal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            result = ingest_capture_notes(
                "/capture Nysha was nervous about school. I felt unsure how to help.",
                n4os_root=n4os_root,
                default_date=date(2026, 7, 21),
            )

            month_text = (n4os_root / "family" / "observations" / "2026-07.md").read_text(
                encoding="utf-8",
            )
            journal_text = (n4os_root / "journal" / "2026-07-21.md").read_text(
                encoding="utf-8",
            )

        self.assertEqual(len(result.family.added), 1)
        self.assertEqual(len(result.journal_entries), 1)
        self.assertIn("was nervous about school", month_text)
        self.assertIn("[[family/Nysha|Nysha]] was nervous", journal_text)
        self.assertIn("[[playbooks/Fear|unsure]]", journal_text)

    def test_capture_updates_existing_journal_frontmatter_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            ingest_capture_notes(
                "/note I felt low energy",
                n4os_root=n4os_root,
                default_date=date(2026, 7, 21),
            )
            ingest_capture_notes(
                "/note I felt scattered about work",
                n4os_root=n4os_root,
                default_date=date(2026, 7, 21),
            )

            journal_text = (n4os_root / "journal" / "2026-07-21.md").read_text(
                encoding="utf-8",
            )

        self.assertIn('  - "[[playbooks/Health|Health]]"', journal_text)
        self.assertIn('  - "[[Attention]]"', journal_text)
        self.assertIn('  - "[[playbooks/Career|Work]]"', journal_text)
        self.assertEqual(journal_text.count('  - "[[daily/Evening|Evening]]"'), 1)

    def test_dated_batch_and_duplicates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            text = "\n".join(
                [
                    "/capture",
                    "2026-07-20",
                    "Nysha liked teaching",
                    "2026-07-21 I felt low energy",
                ]
            )

            first = ingest_capture_notes(text, n4os_root=n4os_root)
            second = ingest_capture_notes(text, n4os_root=n4os_root)

        self.assertEqual(len(first.family.added), 1)
        self.assertEqual(len(first.journal_entries), 1)
        self.assertEqual(len(second.family.added), 0)
        self.assertEqual(len(second.family.skipped_duplicates), 1)
        self.assertEqual(len(second.journal_entries), 0)
        self.assertEqual(len(second.skipped_journal_duplicates), 1)

    def test_capture_reply_reports_destinations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            result = ingest_capture_notes(
                "/capture Nysha was nervous. I felt unsure.",
                n4os_root=n4os_root,
                default_date=date(2026, 7, 21),
            )

        reply = format_capture_reply(result)

        self.assertIn("Captured.", reply)
        self.assertIn("Family observation: Nysha", reply)
        self.assertIn("Journal reflection:", reply)
        self.assertIn("No profiles, playbooks, or goals were promoted", reply)


if __name__ == "__main__":
    unittest.main()

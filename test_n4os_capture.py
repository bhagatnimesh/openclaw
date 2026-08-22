from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import n4os_memory_inbox
from n4os_capture import (
    format_capture_reply,
    ingest_capture_notes,
    is_capture_message,
    undo_capture_ingest,
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

    def test_possessive_child_name_is_not_stripped_from_observation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            result = ingest_capture_notes(
                "Capture Navya's showing interesting skill creating stories.",
                n4os_root=n4os_root,
                default_date=date(2026, 7, 21),
            )

            month_text = (n4os_root / "family" / "observations" / "2026-07.md").read_text(
                encoding="utf-8",
            )

        self.assertEqual(len(result.family.added), 1)
        self.assertIn("Navya's showing interesting skill", month_text)
        self.assertNotIn("Observation: 's showing", month_text)

    def test_capture_enriches_link_context_for_future_patterns(self):
        html = """
        <html>
          <head>
            <title>60 Brain Teasers for kids {With Answers}</title>
            <meta name="description" content="Brain teasers for kids help build problem-solving skills and memory.">
          </head>
          <body>
            <h1>Easy Brain Teasers (With Answers) for Kids</h1>
            <h2>Fun riddles for kids</h2>
          </body>
        </html>
        """

        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            result = ingest_capture_notes(
                "/capture kids like silly puzzles here https://www.littleladoo.com/brain-teasers-for-kids/",
                n4os_root=n4os_root,
                default_date=date(2026, 7, 21),
                url_fetcher=lambda _: html,
            )

            month_text = (n4os_root / "family" / "observations" / "2026-07.md").read_text(
                encoding="utf-8",
            )

        self.assertEqual(len(result.family.added), 1)
        self.assertIn("kids like silly puzzles", month_text)
        self.assertIn("title: 60 Brain Teasers for kids {With Answers}", month_text)
        self.assertIn("problem-solving skills and memory", month_text)
        self.assertIn("Easy Brain Teasers (With Answers) for Kids", month_text)

    def test_capture_enrichment_skips_urls_with_existing_previews(self):
        html = "<html><head><title>New Title</title></head></html>"
        calls: list[str] = []

        def fetch(url: str) -> str | None:
            calls.append(url)
            return html

        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            result = ingest_capture_notes(
                (
                    "/capture I saved https://new.example and https://keep.example "
                    "[Link: https://keep.example; title: Keep Title]"
                ),
                n4os_root=n4os_root,
                default_date=date(2026, 7, 21),
                url_fetcher=fetch,
            )

        self.assertEqual(calls, ["https://new.example"])
        self.assertEqual(result.notes[0].text.count("title: Keep Title"), 1)
        self.assertIn("[Link: https://new.example; title: New Title]", result.notes[0].text)

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

    def test_ingest_rolls_back_family_write_when_journal_write_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            with patch("n4os_capture._write_text_atomic", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    ingest_capture_notes(
                        "/capture Nysha was nervous about school. I felt proud.",
                        n4os_root=n4os_root,
                        default_date=date(2026, 7, 21),
                    )

            month_path = n4os_root / "family" / "observations" / "2026-07.md"
            month_text = month_path.read_text(encoding="utf-8") if month_path.exists() else ""
            month_exists = month_path.exists()
            journal_path = n4os_root / "journal" / "2026-07-21.md"
            journal_exists = journal_path.exists()

        self.assertNotIn("was nervous about school", month_text)
        self.assertFalse(month_exists)
        self.assertFalse(journal_exists)

    def test_family_capture_does_not_leave_file_when_atomic_write_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            with patch("n4os_memory_inbox._write_text_atomic", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    ingest_capture_notes(
                        "/capture Nysha was nervous about school.",
                        n4os_root=n4os_root,
                        default_date=date(2026, 7, 21),
                    )

            month_path = n4os_root / "family" / "observations" / "2026-07.md"
            month_exists = month_path.exists()

        self.assertFalse(month_exists)

    def test_family_batch_rolls_back_first_observation_when_second_write_fails(self):
        original_append = n4os_memory_inbox._append_observation
        calls = 0

        def append_then_fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("boom")
            return original_append(*args, **kwargs)

        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            with patch("n4os_memory_inbox._append_observation", side_effect=append_then_fail_second):
                with self.assertRaises(RuntimeError):
                    ingest_capture_notes(
                        "/capture Nysha was nervous.\nNavya was focused.",
                        n4os_root=n4os_root,
                        default_date=date(2026, 7, 21),
                    )

            month_path = n4os_root / "family" / "observations" / "2026-07.md"
            month_text = month_path.read_text(encoding="utf-8") if month_path.exists() else ""
            month_exists = month_path.exists()

        self.assertEqual(month_text, "")
        self.assertFalse(month_exists)

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
        self.assertIn("Summary: Nysha was nervous. You felt unsure.", reply)
        self.assertIn("Captured text:", reply)
        self.assertIn("Nysha was nervous. I felt unsure.", reply)
        self.assertIn("Family observation: Nysha", reply)
        self.assertIn("Journal reflection:", reply)
        self.assertIn("No profiles, playbooks, or goals were promoted", reply)

    def test_capture_reply_summarizes_voice_capture(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            result = ingest_capture_notes(
                (
                    "Capture I'm excited about the N4OS work I'm doing, especially how "
                    "it is evolving into a memory for the family which can compound and "
                    "grow over a period of time."
                ),
                n4os_root=n4os_root,
                default_date=date(2026, 7, 21),
            )

        reply = format_capture_reply(result)

        self.assertIn(
            (
                "Summary: You're excited that the N4OS work you're doing is becoming "
                "a family memory that can compound and grow."
            ),
            reply,
        )
        self.assertIn("Captured text:", reply)
        self.assertEqual(len(result.family.added), 0)
        self.assertEqual(len(result.journal_entries), 1)
        self.assertIn("Journal reflection:", reply)
        self.assertNotIn("Family observation: Family", reply)

    def test_capture_reply_shows_full_captured_text(self):
        long_note = (
            "Capture Nysha homework time was difficult because she started strong, "
            "then got distracted by wanting another snack, and we had to reset the "
            "table twice before she could finish the reading worksheet with steady "
            "attention and less frustration than last week."
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            result = ingest_capture_notes(
                long_note,
                n4os_root=n4os_root,
                default_date=date(2026, 7, 21),
            )

        reply = format_capture_reply(result)

        captured_text = long_note.removeprefix("Capture ")
        self.assertIn(f"- {captured_text}", reply)
        self.assertNotIn(f"- {captured_text[:179].rstrip()}...", reply)

    def test_undo_capture_removes_added_family_and_journal_blocks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            result = ingest_capture_notes(
                "/capture Nysha was nervous. I felt unsure.",
                n4os_root=n4os_root,
                default_date=date(2026, 7, 21),
            )

            undo = undo_capture_ingest(result, n4os_root=n4os_root)
            month_text = (n4os_root / "family" / "observations" / "2026-07.md").read_text(
                encoding="utf-8",
            )
            journal_text = (n4os_root / "journal" / "2026-07-21.md").read_text(
                encoding="utf-8",
            )

        self.assertEqual(undo.family_observations_removed, 1)
        self.assertEqual(undo.journal_entries_removed, 1)
        self.assertNotIn("was nervous", month_text)
        self.assertNotIn("I felt", journal_text)


if __name__ == "__main__":
    unittest.main()

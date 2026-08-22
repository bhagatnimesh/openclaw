from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from claws.n4os.second_brain_importer import SecondBrainImporter, SecondBrainImportUserError


SLIDES_URL = "https://docs.google.com/presentation/d/backtoschool123/edit?usp=sharing"
SCHOOL_GUIDE_TEXT = """
Welcome to Back-to-School Night
2026-2027
Mrs. Suzanne Thompson
2nd Grade

Room 13 Daily Schedule
8:30 calendar math
language arts
Benchmark reading
Structured Literacy phonics
Room 13 Prep Schedule
PE Tuesday and Thursday
Library Friday

Positive Behavior Intervention & Supports
Respect includes welcoming others and accepting differences.
The students are learning about personal accountability.
Growth Mindset and Goal Setting

Common Core Standards for Mathematics
Students are comfortable with basic addition and subtraction facts.
Science of Reading
Structured Literacy is a district-created phonics program.
Science Amplify Science
Social Science My Community

Homework is designed to review and practice skills.
Students will bring home their new homework each Friday.
The Homework Folder needs to be kept in the student's backpack.
Parent signature is required.
2nd grade homework will be approximately 0-25 minutes daily.

Lexia Core 5
IXL
iReady
Typing.com
Scholastic Reading Club
Class Code - LLM3X
"""


class SecondBrainImporterTest(unittest.TestCase):
    def test_shortened_display_url_gets_user_error(self) -> None:
        importer = SecondBrainImporter(fetch_text=lambda url: SCHOOL_GUIDE_TEXT)

        with self.assertRaisesRegex(SecondBrainImportUserError, "shortened"):
            importer.preview_from_message(
                "/import second brain https://docs.google.com/presentation/d/...\n"
                "Instructions: This is Nysha's Back-to-School guide.",
                key="telegram:test",
            )

    def test_school_guide_plan_writes_markdown_after_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            n4os_root = Path(tmp) / "n4os"
            family_root = n4os_root / "family"
            family_root.mkdir(parents=True)
            (family_root / "Nysha.md").write_text("# Nysha\n", encoding="utf-8")
            importer = SecondBrainImporter(
                n4os_root=n4os_root,
                fetch_text=lambda url: SCHOOL_GUIDE_TEXT,
                now=lambda: datetime(2026, 8, 18, tzinfo=ZoneInfo("America/Los_Angeles")),
            )

            preview = importer.preview_from_message(
                "/import second brain "
                f"{SLIDES_URL}\n"
                "Instructions: This is Nysha's Back-to-School guide. Use it as part of the second brain.",
                key="telegram:test",
            )

            self.assertIn("N4OS import preview: Welcome to Back-to-School Night", preview)
            self.assertIn("n4os/school/Nysha/2026-2027/School Knowledge.md", preview)
            self.assertIn("n4os/school/Nysha/2026-2027/Room 13.md", preview)
            self.assertIn("n4os/family/Nysha.md", preview)
            self.assertIn("Answer future family questions with source-backed context", preview)
            self.assertIn("Connect this source to the right N4OS people, domains, decisions, goals, and playbooks", preview)
            self.assertIn("For this school source, also support learning context", preview)

            result = importer.save_pending(key="telegram:test", response="save")

            self.assertIn("Saved second brain import.", result.message)
            room = n4os_root / "school" / "Nysha" / "2026-2027" / "Room 13.md"
            school_knowledge = n4os_root / "school" / "Nysha" / "2026-2027" / "School Knowledge.md"
            curriculum = n4os_root / "school" / "Nysha" / "2026-2027" / "Curriculum Map.md"
            resources = n4os_root / "school" / "Nysha" / "2026-2027" / "Resources.md"
            self.assertTrue(room.exists())
            self.assertTrue(school_knowledge.exists())
            self.assertTrue(curriculum.exists())
            self.assertTrue(resources.exists())
            school_knowledge_text = school_knowledge.read_text(encoding="utf-8")
            self.assertIn("# School Knowledge", school_knowledge_text)
            self.assertIn("## Imported Source: Welcome to Back-to-School Night", school_knowledge_text)
            self.assertIn("### People And Relationships", school_knowledge_text)
            self.assertIn("### Recurring Routines", school_knowledge_text)
            self.assertIn("### Learning Context", school_knowledge_text)
            self.assertIn("### Guardrails", school_knowledge_text)
            self.assertIn("Mrs. Suzanne Thompson", school_knowledge_text)
            self.assertIn("PE Tuesday and Thursday", school_knowledge_text)
            self.assertIn("Structured Literacy", school_knowledge_text)
            self.assertIn("0-25 minutes", school_knowledge_text)
            self.assertIn("Stable class guide imported for Nysha", room.read_text(encoding="utf-8"))
            self.assertIn("Structured Literacy", curriculum.read_text(encoding="utf-8"))
            self.assertIn("LLM3X", resources.read_text(encoding="utf-8"))
            self.assertIn(
                "[[school/Nysha/2026-2027/Room 13|2026-2027 Room 13 school guide]]",
                (family_root / "Nysha.md").read_text(encoding="utf-8"),
            )

    def test_cancel_drops_pending_import_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            n4os_root = Path(tmp) / "n4os"
            importer = SecondBrainImporter(
                n4os_root=n4os_root,
                fetch_text=lambda url: SCHOOL_GUIDE_TEXT,
                now=lambda: datetime(2026, 8, 18, tzinfo=ZoneInfo("America/Los_Angeles")),
            )
            importer.preview_from_message(
                f"/import second brain {SLIDES_URL} Instructions: Nysha school guide",
                key="telegram:test",
            )

            result = importer.save_pending(key="telegram:test", response="cancel")

            self.assertEqual(result.message, "Canceled second brain import.")
            self.assertFalse((n4os_root / "school").exists())

    def test_school_knowledge_upserts_same_source_and_appends_new_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            n4os_root = Path(tmp) / "n4os"
            importer = SecondBrainImporter(
                n4os_root=n4os_root,
                fetch_text=lambda url: SCHOOL_GUIDE_TEXT,
                now=lambda: datetime(2026, 8, 18, tzinfo=ZoneInfo("America/Los_Angeles")),
            )

            importer.preview_from_message(
                f"/import second brain {SLIDES_URL} Instructions: Nysha school guide",
                key="telegram:test",
            )
            importer.save_pending(key="telegram:test", response="save")
            school_knowledge = n4os_root / "school" / "Nysha" / "2026-2027" / "School Knowledge.md"
            first_text = school_knowledge.read_text(encoding="utf-8")

            importer.preview_from_message(
                f"/import second brain {SLIDES_URL} Instructions: Nysha school guide",
                key="telegram:test",
            )
            importer.save_pending(key="telegram:test", response="save")
            second_text = school_knowledge.read_text(encoding="utf-8")

            self.assertEqual(first_text, second_text)

            importer = SecondBrainImporter(
                n4os_root=n4os_root,
                fetch_text=lambda url: SCHOOL_GUIDE_TEXT.replace("Welcome to Back-to-School Night", "September Class Update"),
                now=lambda: datetime(2026, 9, 2, tzinfo=ZoneInfo("America/Los_Angeles")),
            )
            importer.preview_from_message(
                " /import second brain https://docs.google.com/presentation/d/september456/edit "
                "Instructions: Nysha school update",
                key="telegram:test",
            )
            importer.save_pending(key="telegram:test", response="save")
            appended_text = school_knowledge.read_text(encoding="utf-8")

            self.assertIn("## Imported Source: Welcome to Back-to-School Night", appended_text)
            self.assertIn("## Imported Source: September Class Update", appended_text)
            self.assertEqual(appended_text.count("## Imported Source:"), 2)

    def test_adjust_replaces_pending_plan_before_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            n4os_root = Path(tmp) / "n4os"
            importer = SecondBrainImporter(
                n4os_root=n4os_root,
                fetch_text=lambda url: "# Useful Note\n\nGeneral reference material.",
                now=lambda: datetime(2026, 8, 18, tzinfo=ZoneInfo("America/Los_Angeles")),
            )
            importer.preview_from_message(
                f"/import second brain {SLIDES_URL} Instructions: Store as general reference.",
                key="telegram:test",
            )

            adjusted = importer.save_pending(
                key="telegram:test",
                response="adjust: This is Nysha school material for parent prep.",
            )

            self.assertIn("n4os/school/Nysha/2026-2027/Room 13.md", adjusted.message)

    def test_local_text_file_import_uses_generic_import_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "teacher-note.md"
            source_path.write_text(
                "# Teacher Note\n\nUse this for future prep and parent questions.",
                encoding="utf-8",
            )
            n4os_root = root / "n4os"
            importer = SecondBrainImporter(
                n4os_root=n4os_root,
                now=lambda: datetime(2026, 8, 18, tzinfo=ZoneInfo("America/Los_Angeles")),
            )

            preview = importer.preview_from_message(
                f"/import second brain {source_path} Instructions: Store for future prep.",
                key="telegram:test",
            )
            result = importer.save_pending(key="telegram:test", response="save")

            self.assertIn("n4os/imports/teacher-note/Source.md", preview)
            self.assertIn("Saved second brain import.", result.message)
            self.assertTrue((n4os_root / "imports" / "teacher-note" / "Source.md").exists())


if __name__ == "__main__":
    unittest.main()

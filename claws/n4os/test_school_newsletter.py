from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from claws.homework.provider import SQLiteHomeworkProvider
from claws.homework.tools import HomeworkTools
from claws.homework.tools import _write_markdown
from claws.n4os.school_newsletter import (
    SchoolNewsletterImporter,
    SQLiteSchoolNewsletterStore,
    _time_range,
    is_school_newsletter_message,
    parse_newsletter_text,
)


NEWSLETTER_URL = "https://docs.google.com/presentation/d/newsletter123/edit?usp=sharing"
NEWSLETTER_TEXT = """
Room 13 Newsletter
Mrs. Thompson
August 14, 2026

Back to School Night is Tuesday, August 18, 2026.
4:30 - 5:00 Meet the Principal in the MPR
5:00 - 6:00 Teacher Presentations in classrooms

First Days of School
At the beginning of the school year, we focus on building a classroom community:
Our classroom is a family.  Our Class is a Family Read Aloud
We communicate respectfully with each other.
We demonstrate the 8 Great Traits (character education) to be great citizens.

Language Arts
Each morning we edit sentences (capitals, ending punctuation, spelling) and practice grammar. We reviewed common and proper nouns, and compound words.

Math
This month we are reviewing Place Value. We talked about ones, tens, hundreds, quick sketches, and even and odd.

Homework
All About Me: project due Friday, August 28, 2026
Please help your student complete the All About Me book. Your student may draw pictures, add photographs, or glue on printed images. Your student may wish to practice reading aloud the book before presenting the book in the classroom.

Growth Mindset and Art
We read The Dot. This story reminds us to persevere and to be brave when we try new things.
Directed drawing activities involve creativity, following directions, listening skills, and fine motor skills.

Wear Layers
Please remind your student to wear layers to school. Please remind your student to bring a sweater.

Headphones for Chromebooks
Please send comfortable headphones (for Chromebooks) to school with your student on Monday. Headphones may connect with a USB-A or an audio jack.

Dismissal
All students (grades 1-5) will wait at the lunch picnic tables after school. Please wait for your student by the picnic tables.

Reminders
Friday, August 28: All About Me project due
Monday, September 7 (Labor Day): NO school
Friday, September 11 (Patriot Day): wear red, white, blue
Tuesday, September 15 (Dot Day): wear dots
""".strip()

NEWSLETTER_TEXT_AUGUST_21 = """
Room 13 Newsletter
Mrs. Thompson
August 21, 2026

Behavior Assembly with Mr. Wood
The principal reviewed behavior expectations and playground rules.

Circle Meeting
Students shared positive qualities of a friend and practiced public speaking skills.

8 Great Traits and Kindness
We read Lilly's Purple Plastic Purse. Lilly learns how to be responsible.
We read Penny and Her Marble. We discussed the importance of honesty and integrity.
We read Sheila Rae, the Brave. Sheila Rae reminds us to plan ahead and make good decisions.
We read A Weekend with Wendell. Wendell is not a respectful guest.
We watched a Zen Den video and learned how a traffic light can help us make better decisions.
We practiced Belly Breathing to help us calm down when we feel strong emotions.

Language Arts and Grammar
We practiced phonics drills and read phonics poems with short vowel sounds.

Poetry
Every week we practice a poem or song. On Fridays the students present the poem.
The students stand at the front of the class and speak in a clear presentation voice.
The students wrote a concrete poem about summer.

Math
We reviewed place value, standard form, expanded form, and word form.
We practiced writing money, observed patterns in the 100 grid, and worked with Math Mountains.

LEXIA
The students completed the LEXIA diagnostic and can work on LEXIA lessons at home or at school.
LEXIA recommends about 40 minutes of practice per week.

Fire Drill
The students participated in their first fire drill.

Art
The students worked on self portraits and learned the elements of art.
Mouse Paint by Ellen Stoll Walsh | Kids Book Read Aloud
Primary Colors Song (Sesame Studios)
The students colored a color wheel and practiced drawing different types of lines.
""".strip()


class FakeCalendarTools:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.list_calls: list[dict[str, object]] = []

    def list_calendar_events(self, **kwargs):
        self.list_calls.append(kwargs)
        return {"status": "ok", "data": {"events": []}}

    def create_calendar_event(self, **kwargs):
        self.created.append(kwargs)
        return {"status": "ok", "data": {"event": {"id": f"event-{len(self.created)}"}}}


class FakeTaskTools:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []

    def list_tasks(self, **kwargs):
        del kwargs
        return {"status": "ok", "data": {"tasks": []}}

    def create_task(self, **kwargs):
        self.created.append(kwargs)
        return {"status": "ok", "data": {"task": {"id": f"task-{len(self.created)}"}}}


class FakeNamedCalendarService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.events_by_calendar: dict[str, list[dict[str, object]]] = {}

    def events(self):
        return self

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return self

    def execute(self):
        calendar_id = str(self.calls[-1].get("calendarId") or "")
        items = self.events_by_calendar.get(
            calendar_id,
            [
                {
                    "id": "back-to-school-night",
                    "summary": "Back to School Night",
                    "start": {"dateTime": "2026-08-18T16:30:00-07:00"},
                }
            ],
        )
        return {"items": items}


class FakeNamedCalendarProvider:
    def __init__(self) -> None:
        self.service = FakeNamedCalendarService()
        self.calendar_id = "primary"

    def _calendar_id_for_name(self, calendar_name: str) -> str:
        return f"id:{calendar_name}"


class FakeNamedCalendarTools(FakeCalendarTools):
    def __init__(self) -> None:
        super().__init__()
        self.provider = FakeNamedCalendarProvider()


class FakeMultiListTaskTools(FakeTaskTools):
    def __init__(self) -> None:
        super().__init__()
        self.listed_task_list_ids: list[str] = []

    def list_task_lists(self):
        return {
            "status": "ok",
            "data": {"task_lists": [{"id": "default"}, {"id": "school"}]},
        }

    def list_tasks(self, **kwargs):
        task_list_id = str(kwargs.get("task_list_id") or "")
        self.listed_task_list_ids.append(task_list_id)
        tasks = []
        if task_list_id == "school":
            tasks.append({"id": "task-1", "title": "Send comfortable headphones for Chromebook"})
        return {"status": "ok", "data": {"tasks": tasks}}


class FakeFailingListTaskTools(FakeMultiListTaskTools):
    def list_tasks(self, **kwargs):
        task_list_id = str(kwargs.get("task_list_id") or "")
        self.listed_task_list_ids.append(task_list_id)
        if task_list_id == "school":
            return {"status": "error", "message": "School tasks unavailable.", "data": {}}
        return {"status": "ok", "data": {"tasks": []}}


class SchoolNewsletterTest(unittest.TestCase):
    def test_prompt_detection_accepts_slides_newsletter_command(self):
        self.assertTrue(
            is_school_newsletter_message(
                f"/import school newsletter for Nysha {NEWSLETTER_URL}",
            )
        )

    def test_parser_extracts_homework_calendar_tasks_and_context(self):
        parsed = parse_newsletter_text(
            NEWSLETTER_TEXT,
            child="Nysha",
            source_url=NEWSLETTER_URL,
        )

        self.assertEqual(parsed.title, "Room 13 Newsletter")
        self.assertEqual(parsed.teacher, "Mrs. Thompson")
        self.assertEqual(parsed.newsletter_date, "2026-08-14")
        self.assertEqual(parsed.homework[0].title, "All About Me project")
        self.assertEqual(parsed.homework[0].due_date, "2026-08-28")
        self.assertIn("Back to School Night", [item.title for item in parsed.calendar])
        self.assertIn("Send comfortable headphones for Chromebook", [item.title for item in parsed.tasks])
        self.assertIn("The Dot", [item.label for item in parsed.knowledge.resources])
        self.assertIn("Growth mindset and perseverance", parsed.knowledge.topics)

    def test_parser_extracts_new_books_and_learning_topics_without_title_whitelist(self):
        parsed = parse_newsletter_text(
            NEWSLETTER_TEXT_AUGUST_21,
            child="Nysha",
            source_url=NEWSLETTER_URL,
        )

        self.assertEqual(
            tuple(item.label for item in parsed.knowledge.resources if item.kind == "book"),
            (
                "Lilly's Purple Plastic Purse",
                "Penny and Her Marble",
                "Sheila Rae, the Brave",
                "A Weekend with Wendell",
                "Mouse Paint by Ellen Stoll Walsh",
            ),
        )
        self.assertNotIn(
            "phonics poems with short vowel sounds",
            [item.label for item in parsed.knowledge.resources],
        )
        self.assertIn(
            "Social-emotional check-ins, friendship, and calming strategies",
            parsed.knowledge.topics,
        )
        self.assertIn("Language arts: grammar, phonics, and short vowel sounds", parsed.knowledge.topics)
        self.assertIn("Math: number forms, money, number-grid patterns, and Math Mountains", parsed.knowledge.topics)
        self.assertIn("School safety and fire-drill procedures", parsed.knowledge.topics)
        self.assertIn("Art: self-portraits, color theory, and line", parsed.knowledge.topics)
        self.assertIn(
            "LEXIA recommends about 40 minutes of practice per week at home or school",
            parsed.knowledge.recommendations,
        )
        self.assertIn(
            "The class practices a poem or song weekly and presents it on Fridays",
            parsed.knowledge.routines,
        )
        self.assertIn("Zen Den", [item.label for item in parsed.knowledge.resources])
        self.assertIn("Primary Colors Song (Sesame Studios)", [item.label for item in parsed.knowledge.resources])
        self.assertEqual(len(parsed.knowledge.conversation_prompts), 5)

    def test_time_range_only_shifts_explicit_evening_context_to_pm(self):
        self.assertEqual(_time_range("Back to School Night 4:30 - 5:00"), ("16:30", "17:00"))
        self.assertEqual(_time_range("Morning assembly 7:30 - 8:00"), ("07:30", "08:00"))

    def test_save_routes_newsletter_context_without_creating_optional_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tasks = FakeTaskTools()
            importer = SchoolNewsletterImporter(
                store=SQLiteSchoolNewsletterStore(root / "n4os.db"),
                homework_tools=HomeworkTools(SQLiteHomeworkProvider(root / "n4os.db")),
                calendar_tools=FakeCalendarTools(),
                task_tools=tasks,
                n4os_root=root / "n4os",
                homework_root=root / "n4os" / "homework",
                fetch_text=lambda _url: NEWSLETTER_TEXT_AUGUST_21,
            )

            preview = importer.preview_from_message(
                f"/import school newsletter for Nysha {NEWSLETTER_URL}",
                key="telegram:test",
            )
            result = importer.save_pending(key="telegram:test", response="save")

            self.assertIn("Optional routines (saved as context, not created as tasks)", preview)
            self.assertEqual(tasks.created, [])
            school_root = root / "n4os" / "school" / "Nysha" / "2026-2027"
            school_knowledge = (school_root / "School Knowledge.md").read_text(encoding="utf-8")
            curriculum = (school_root / "Curriculum Map.md").read_text(encoding="utf-8")
            resources = (school_root / "Resources.md").read_text(encoding="utf-8")
            prompts = (school_root / "Conversation Starters.md").read_text(encoding="utf-8")
            observations = (root / "n4os" / "family" / "observations" / "2026-08.md").read_text(
                encoding="utf-8"
            )
            self.assertLess(school_knowledge.index("## Newsletter Updates"), 500)
            self.assertIn("LEXIA recommends about 40 minutes", school_knowledge)
            self.assertIn("Math: money notation", curriculum)
            self.assertIn("Video: Zen Den", resources)
            self.assertIn("What did someone share during circle meeting?", prompts)
            self.assertIn("School newsletter imported:", observations)
            self.assertNotIn("- Observation:", observations)
            self.assertIn("School knowledge:", result.message)

    def test_saved_import_backfills_knowledge_without_recreating_actions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = SQLiteSchoolNewsletterStore(root / "n4os.db")
            parsed = parse_newsletter_text(
                NEWSLETTER_TEXT_AUGUST_21,
                child="Nysha",
                source_url=NEWSLETTER_URL,
            )
            store.upsert(parsed, saved={"tasks": ["created prior reminder"]}, status="saved")
            tasks = FakeTaskTools()
            calendar = FakeCalendarTools()
            importer = SchoolNewsletterImporter(
                store=store,
                homework_tools=HomeworkTools(SQLiteHomeworkProvider(root / "n4os.db")),
                calendar_tools=calendar,
                task_tools=tasks,
                n4os_root=root / "n4os",
                homework_root=root / "n4os" / "homework",
                fetch_text=lambda _url: NEWSLETTER_TEXT_AUGUST_21,
            )

            importer.preview_from_message(
                f"/import school newsletter for Nysha {NEWSLETTER_URL}",
                key="telegram:test",
            )
            first = importer.save_pending(key="telegram:test", response="save")
            importer.preview_from_message(
                f"/import school newsletter for Nysha {NEWSLETTER_URL}",
                key="telegram:test",
            )
            second = importer.save_pending(key="telegram:test", response="save")

            self.assertIn("School knowledge:", first.message)
            self.assertIn("No new items were added.", second.message)
            self.assertEqual(tasks.created, [])
            self.assertEqual(calendar.created, [])
            school_knowledge = root / "n4os" / "school" / "Nysha" / "2026-2027" / "School Knowledge.md"
            self.assertEqual(school_knowledge.read_text(encoding="utf-8").count("n4os-school-newsletter:"), 2)
            stored = store.find(
                child=parsed.child,
                source_type=parsed.source_type,
                source_id=parsed.source_id,
                content_fingerprint=parsed.content_fingerprint,
            )
            self.assertIn("created prior reminder", str(stored["saved_json"] if stored else ""))

    def test_save_creates_real_homework_and_import_ledger(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            provider = SQLiteHomeworkProvider(root / "n4os.db")
            homework_tools = HomeworkTools(provider, homework_root=root / "n4os" / "homework")
            calendar = FakeCalendarTools()
            tasks = FakeTaskTools()
            importer = SchoolNewsletterImporter(
                store=SQLiteSchoolNewsletterStore(root / "n4os.db"),
                homework_tools=homework_tools,
                calendar_tools=calendar,
                task_tools=tasks,
                n4os_root=root / "n4os",
                homework_root=root / "n4os" / "homework",
                fetch_text=lambda _url: NEWSLETTER_TEXT,
            )

            preview = importer.preview_from_message(
                f"/import school newsletter for Nysha {NEWSLETTER_URL}",
                key="telegram:test",
            )
            self.assertIn("New: All About Me project", preview)

            result = importer.save_pending(key="telegram:test", response="save")

            self.assertIn("created All About Me project", result.message)
            homework_items = provider.list_items(child="Nysha")
            self.assertEqual(len(homework_items), 1)
            self.assertEqual(homework_items[0]["title"], "All About Me project")
            self.assertEqual(homework_items[0]["due_date"], "2026-08-28")
            self.assertTrue((root / "n4os" / "homework" / "Nysha.md").exists())
            self.assertGreaterEqual(len(calendar.created), 3)
            self.assertIn("Homework due: All About Me project", [str(item.get("title")) for item in calendar.created])
            self.assertEqual(len(tasks.created), 3)

    def test_save_enriches_matching_homework_instead_of_duplicate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            provider = SQLiteHomeworkProvider(root / "n4os.db")
            existing = provider.capture_assignment(
                child="Nysha",
                title="Second Grade Homework",
                subject="Reading",
                assigned_date="2026-08-14",
                due_date="2026-08-28",
                status="assigned",
                notes="All About Me Book 25 minutes. Practice reading aloud your book.",
                raw_input="Homework title: Second Grade Homework. All About Me Book due Friday, August 28.",
                source="telegram_text",
            )
            homework_tools = HomeworkTools(provider, homework_root=root / "n4os" / "homework")
            importer = SchoolNewsletterImporter(
                store=SQLiteSchoolNewsletterStore(root / "n4os.db"),
                homework_tools=homework_tools,
                calendar_tools=FakeCalendarTools(),
                task_tools=FakeTaskTools(),
                n4os_root=root / "n4os",
                homework_root=root / "n4os" / "homework",
                fetch_text=lambda _url: NEWSLETTER_TEXT,
            )

            preview = importer.preview_from_message(
                f"Parse this teacher newsletter for Nysha {NEWSLETTER_URL}",
                key="telegram:test",
            )
            self.assertIn("Already present: Second Grade Homework", preview)

            result = importer.save_pending(key="telegram:test", response="save")

            self.assertIn("updated Second Grade Homework", result.message)
            homework_items = provider.list_items(child="Nysha")
            self.assertEqual(len(homework_items), 1)
            self.assertEqual(homework_items[0]["id"], existing["id"])
            self.assertIn("practice reading aloud", homework_items[0]["notes"])

    def test_reimported_newsletter_does_not_rewrite_unchanged_homework(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            provider = SQLiteHomeworkProvider(root / "n4os.db")
            importer = SchoolNewsletterImporter(
                store=SQLiteSchoolNewsletterStore(root / "n4os.db"),
                homework_tools=HomeworkTools(provider, homework_root=root / "n4os" / "homework"),
                calendar_tools=FakeCalendarTools(),
                task_tools=FakeTaskTools(),
                n4os_root=root / "n4os",
                homework_root=root / "n4os" / "homework",
                fetch_text=lambda _url: NEWSLETTER_TEXT,
            )

            importer.preview_from_message(f"/import school newsletter for Nysha {NEWSLETTER_URL}", key="telegram:test")
            importer.save_pending(key="telegram:test", response="save")
            first_item = provider.list_items(child="Nysha")[0]
            first_events = provider.list_events(first_item["id"])

            importer.preview_from_message(f"/import school newsletter for Nysha {NEWSLETTER_URL}", key="telegram:test")
            result = importer.save_pending(key="telegram:test", response="save")
            second_item = provider.list_items(child="Nysha")[0]
            second_events = provider.list_events(second_item["id"])

            self.assertIn("No new items were added.", result.message)
            self.assertEqual(second_item["updated_at"], first_item["updated_at"])
            self.assertEqual(len(second_events), len(first_events))

    def test_calendar_dedupe_uses_child_calendar_and_rfc3339_bounds(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            provider = SQLiteHomeworkProvider(root / "n4os.db")
            calendar = FakeNamedCalendarTools()
            importer = SchoolNewsletterImporter(
                store=SQLiteSchoolNewsletterStore(root / "n4os.db"),
                homework_tools=HomeworkTools(provider, homework_root=root / "n4os" / "homework"),
                calendar_tools=calendar,
                task_tools=FakeTaskTools(),
                n4os_root=root / "n4os",
                homework_root=root / "n4os" / "homework",
                fetch_text=lambda _url: NEWSLETTER_TEXT,
            )

            preview = importer.build_preview(
                parse_newsletter_text(NEWSLETTER_TEXT, child="Nysha", source_url=NEWSLETTER_URL)
            )

            self.assertIn("Back to School Night", [match.label for match in preview.calendar_matches])
            call = calendar.provider.service.calls[0]
            self.assertEqual(call["calendarId"], "id:Nysha School Calendar")
            self.assertEqual(call["timeMin"], "2026-08-18T00:00:00-07:00")
            self.assertEqual(call["timeMax"], "2026-08-19T00:00:00-07:00")
            self.assertEqual(calendar.provider.service.calls[1]["calendarId"], "primary")

    def test_preview_does_not_show_homework_due_as_calendar_item(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            importer = SchoolNewsletterImporter(
                store=SQLiteSchoolNewsletterStore(root / "n4os.db"),
                homework_tools=HomeworkTools(SQLiteHomeworkProvider(root / "n4os.db")),
                calendar_tools=FakeCalendarTools(),
                task_tools=FakeTaskTools(),
                n4os_root=root / "n4os",
                homework_root=root / "n4os" / "homework",
                fetch_text=lambda _url: NEWSLETTER_TEXT,
            )

            preview = importer.preview_from_message(
                f"/import school newsletter for Nysha {NEWSLETTER_URL}",
                key="telegram:test",
            )

            calendar_section = preview.split("Calendar:", 1)[1].split("Reminders:", 1)[0]
            self.assertNotIn("All About Me project due", calendar_section)
            self.assertIn("New: All About Me project", preview)

    def test_calendar_dedupe_also_checks_default_calendar(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            calendar = FakeNamedCalendarTools()
            calendar.provider.service.events_by_calendar = {
                "id:Nysha School Calendar": [],
                "primary": [
                    {
                        "id": "primary-back-to-school-night",
                        "summary": "Back to School Night",
                        "start": {"dateTime": "2026-08-18T17:00:00-07:00"},
                    }
                ],
            }
            importer = SchoolNewsletterImporter(
                store=SQLiteSchoolNewsletterStore(root / "n4os.db"),
                homework_tools=HomeworkTools(SQLiteHomeworkProvider(root / "n4os.db")),
                calendar_tools=calendar,
                task_tools=FakeTaskTools(),
                n4os_root=root / "n4os",
                homework_root=root / "n4os" / "homework",
                fetch_text=lambda _url: NEWSLETTER_TEXT,
            )

            preview = importer.build_preview(
                parse_newsletter_text(NEWSLETTER_TEXT, child="Nysha", source_url=NEWSLETTER_URL)
            )

            matches = {match.label: match.status for match in preview.calendar_matches}
            self.assertEqual(matches["Back to School Night"], "match")

    def test_calendar_dedupe_matches_short_title_inside_existing_event(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            calendar = FakeNamedCalendarTools()
            calendar.provider.service.events_by_calendar = {
                "id:Nysha School Calendar": [
                    {
                        "id": "labor-day-no-school",
                        "summary": "Labor Day - No School",
                        "start": {"dateTime": "2026-09-07T00:00:00-07:00"},
                    }
                ],
                "primary": [],
            }
            importer = SchoolNewsletterImporter(
                store=SQLiteSchoolNewsletterStore(root / "n4os.db"),
                homework_tools=HomeworkTools(SQLiteHomeworkProvider(root / "n4os.db")),
                calendar_tools=calendar,
                task_tools=FakeTaskTools(),
                n4os_root=root / "n4os",
                homework_root=root / "n4os" / "homework",
                fetch_text=lambda _url: NEWSLETTER_TEXT,
            )

            preview = importer.build_preview(
                parse_newsletter_text(NEWSLETTER_TEXT, child="Nysha", source_url=NEWSLETTER_URL)
            )

            matches = {match.label: match.status for match in preview.calendar_matches}
            self.assertEqual(matches["Labor Day - No School"], "match")

    def test_task_dedupe_searches_all_task_lists(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tasks = FakeMultiListTaskTools()
            importer = SchoolNewsletterImporter(
                store=SQLiteSchoolNewsletterStore(root / "n4os.db"),
                homework_tools=HomeworkTools(SQLiteHomeworkProvider(root / "n4os.db")),
                calendar_tools=FakeCalendarTools(),
                task_tools=tasks,
                n4os_root=root / "n4os",
                homework_root=root / "n4os" / "homework",
                fetch_text=lambda _url: NEWSLETTER_TEXT,
            )

            preview = importer.build_preview(
                parse_newsletter_text(NEWSLETTER_TEXT, child="Nysha", source_url=NEWSLETTER_URL)
            )

            self.assertIn("default", tasks.listed_task_list_ids)
            self.assertIn("school", tasks.listed_task_list_ids)
            self.assertIn(
                "Send comfortable headphones for Chromebook",
                [match.label for match in preview.task_matches if match.status == "match"],
            )

    def test_task_dedupe_marks_unchecked_when_any_task_list_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tasks = FakeFailingListTaskTools()
            importer = SchoolNewsletterImporter(
                store=SQLiteSchoolNewsletterStore(root / "n4os.db"),
                homework_tools=HomeworkTools(SQLiteHomeworkProvider(root / "n4os.db")),
                calendar_tools=FakeCalendarTools(),
                task_tools=tasks,
                n4os_root=root / "n4os",
                homework_root=root / "n4os" / "homework",
                fetch_text=lambda _url: NEWSLETTER_TEXT,
            )

            preview = importer.build_preview(
                parse_newsletter_text(NEWSLETTER_TEXT, child="Nysha", source_url=NEWSLETTER_URL)
            )

            self.assertIn("School tasks unavailable.", "\n".join(preview.warnings))
            self.assertTrue(all(match.status == "unchecked" for match in preview.task_matches))

    def test_homework_markdown_removes_stale_subject_pages(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            provider = SQLiteHomeworkProvider(root / "n4os.db")
            item = provider.capture_assignment(
                child="Nysha",
                title="All About Me project",
                subject=None,
                assigned_date="2026-08-14",
                due_date="2026-08-28",
                status="assigned",
                raw_input="All About Me",
                source="telegram_text",
            )
            homework_root = root / "n4os" / "homework"
            _write_markdown(provider, child="Nysha", homework_root=homework_root)
            self.assertTrue((homework_root / "Nysha" / "unsorted.md").exists())
            handwritten = homework_root / "Nysha" / "parent-note.md"
            handwritten.write_text("# Parent note\n\nKeep this.\n", encoding="utf-8")

            provider.update_assignment_details(
                homework_item_id=item["id"],
                subject="School",
            )
            _write_markdown(provider, child="Nysha", homework_root=homework_root)

            self.assertFalse((homework_root / "Nysha" / "unsorted.md").exists())
            self.assertTrue((homework_root / "Nysha" / "school.md").exists())
            self.assertTrue(handwritten.exists())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest
from zoneinfo import ZoneInfo

from claws.homework.claw import HomeworkClaw
from claws.homework.provider import SQLiteHomeworkProvider
from claws.homework.tools import CHERRY_BLOSSOM_EVENT_LABEL_COLOR, HomeworkTools, homework_content_fingerprint


REFERENCE_TIME = datetime(2026, 8, 14, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))


class FakeCalendarTools:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []

    def create_calendar_event(self, **kwargs):
        self.created.append(kwargs)
        return {
            "status": "ok",
            "message": "Calendar event created.",
            "data": {"event": {"id": "event-1"}},
        }


class HomeworkToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.provider = SQLiteHomeworkProvider(root / "n4os.db")
        self.calendar = FakeCalendarTools()
        self.tools = HomeworkTools(
            self.provider,
            homework_root=root / "homework",
            calendar_tools=self.calendar,
        )

    def test_creates_homework_item_asset_event_and_markdown(self):
        response = self.tools.capture_assignment(
            "/capture homework Nysha math due 2026-08-21\n\nImage text:\nHomework title: All About Me",
            source="telegram_photo",
            photo_path="/static/dashboard/uploads/homework/one.jpg",
        )

        self.assertEqual(response["status"], "ok")
        item = response["data"]["item"]
        self.assertEqual(item["child"], "Nysha")
        self.assertEqual(item["status"], "assigned")
        self.assertEqual(len(self.provider.list_assets(item["id"])), 1)
        self.assertEqual(self.provider.list_events(item["id"])[0]["event_type"], "assigned")
        summary = (Path(self.tmp.name) / "homework" / "Nysha.md").read_text(encoding="utf-8")
        self.assertIn("# Nysha Homework", summary)
        self.assertIn("/static/dashboard/uploads/homework/one.jpg", summary)
        self.assertEqual(len(self.calendar.created), 1)
        self.assertEqual(self.calendar.created[0]["calendar_name"], "Nysha School Calendar")
        self.assertEqual(
            self.calendar.created[0]["event_label_background_color"],
            CHERRY_BLOSSOM_EVENT_LABEL_COLOR,
        )
        self.assertEqual(self.calendar.created[0]["start_time"], "2026-08-21T07:00:00-07:00")
        self.assertEqual(self.calendar.created[0]["end_time"], "2026-08-21T07:30:00-07:00")
        self.assertIn("Added due-date reminder", response["message"])

    def test_explicit_due_time_overrides_default_calendar_time(self):
        self.tools.capture_assignment(
            "/capture homework Nysha math due August 18 at 8 pm",
            now=REFERENCE_TIME,
        )

        self.assertEqual(self.calendar.created[0]["start_time"], "2026-08-18T20:00:00-07:00")
        self.assertEqual(self.calendar.created[0]["end_time"], "2026-08-18T20:30:00-07:00")

    def test_missing_due_date_does_not_create_calendar_event(self):
        response = self.tools.capture_assignment("/capture homework Nysha spelling worksheet")

        self.assertEqual(response["status"], "ok")
        self.assertEqual(self.calendar.created, [])

    def test_lists_current_homework_by_child_and_due_date(self):
        self.tools.capture_assignment("/capture homework Nysha reading due 2026-08-22")
        self.tools.capture_assignment("/capture homework Nysha math due 2026-08-21")

        response = self.tools.list_homework(child="Nysha")

        self.assertEqual(response["status"], "ok")
        titles = [item["title"] for item in response["data"]["items"]]
        self.assertEqual(titles, ["Math homework", "Reading homework"])

    def test_submission_links_to_best_open_assignment(self):
        self.tools.capture_assignment("/capture homework Nysha All About Me writing due 2026-08-21")

        response = self.tools.capture_submission(
            "/capture submitted homework Nysha All About Me",
            source="telegram_photo",
            photo_path="/static/dashboard/uploads/homework/submitted.jpg",
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["item"]["status"], "submitted")
        assets = self.provider.list_assets(response["data"]["item"]["id"])
        self.assertEqual(assets[-1]["kind"], "submission_photo")

    def test_submission_returns_clarification_for_ambiguous_match(self):
        self.tools.capture_assignment("/capture homework Nysha Math Packet math due 2026-08-21")
        self.tools.capture_assignment("/capture homework Nysha Math Review math due 2026-08-21")

        response = self.tools.capture_submission("/capture submitted homework Nysha math")

        self.assertEqual(response["status"], "needs_information")
        self.assertIn("Which homework", response["message"])

    def test_generic_second_grade_title_does_not_define_identity(self):
        first = homework_content_fingerprint(
            "Homework title: Second Grade Homework\nMonday: Read All About Me Book 25 minutes"
        )
        second = homework_content_fingerprint(
            "Homework title: Second Grade Homework\nMonday: Practice subtraction facts"
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first, second)

    def test_similar_assignment_capture_asks_before_duplicate(self):
        self.tools.capture_assignment(
            "/capture homework Nysha\n\n"
            "Image text:\n"
            "Homework title: Second Grade Homework\n"
            "Week range: 8/17 - 8/21\n"
            "Due date: 2026-08-28\n"
            "Monday: All About Me Book 25 minutes",
            source="telegram_photo",
            photo_path="/static/dashboard/uploads/homework/one.jpg",
            photo_sha256="photo-one",
        )

        response = self.tools.capture_assignment(
            "/capture homework Nysha\n\n"
            "Image text:\n"
            "Homework title: Second Grade Homework\n"
            "Week range: 8/17 - 8/21\n"
            "Due date: 2026-08-28\n"
            "Monday: All About Me Book 25 minutes",
            source="telegram_photo",
            photo_path="/static/dashboard/uploads/homework/two.jpg",
            photo_sha256="photo-two",
        )

        self.assertEqual(response["status"], "needs_information")
        self.assertIn("Reply `attach`", response["message"])
        self.assertEqual(len(self.provider.list_items(child="Nysha")), 1)

    def test_different_body_under_generic_title_creates_new_item(self):
        self.tools.capture_assignment(
            "/capture homework Nysha\n\n"
            "Image text:\n"
            "Homework title: Second Grade Homework\n"
            "Monday: All About Me Book 25 minutes",
            source="telegram_photo",
            photo_path="/static/dashboard/uploads/homework/one.jpg",
        )

        response = self.tools.capture_assignment(
            "/capture homework Nysha\n\n"
            "Image text:\n"
            "Homework title: Second Grade Homework\n"
            "Monday: Practice subtraction facts",
            source="telegram_photo",
            photo_path="/static/dashboard/uploads/homework/two.jpg",
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(len(self.provider.list_items(child="Nysha")), 2)

    def test_pending_duplicate_attach_adds_asset_without_calendar_event(self):
        self.tools.capture_assignment(
            "/capture homework Nysha\n\nImage text:\nHomework title: Second Grade Homework\nMonday: Read aloud",
            source="telegram_photo",
            photo_path="/static/dashboard/uploads/homework/one.jpg",
        )
        response = self.tools.capture_assignment(
            "/capture homework Nysha\n\nImage text:\nHomework title: Second Grade Homework\nMonday: Read aloud",
            source="telegram_photo",
            photo_path="/static/dashboard/uploads/homework/two.jpg",
        )
        pending = response["data"]["pending_action"]

        resolved = self.tools.resolve_duplicate_assignment(pending, "attach")

        self.assertEqual(resolved["status"], "ok")
        items = self.provider.list_items(child="Nysha")
        self.assertEqual(len(items), 1)
        self.assertEqual(len(self.provider.list_assets(items[0]["id"])), 2)
        self.assertEqual(len(self.calendar.created), 0)

    def test_pending_duplicate_new_records_similarity_metadata(self):
        self.tools.capture_assignment(
            "/capture homework Nysha\n\nImage text:\nHomework title: Second Grade Homework\nMonday: Read aloud",
            source="telegram_photo",
            photo_path="/static/dashboard/uploads/homework/one.jpg",
        )
        response = self.tools.capture_assignment(
            "/capture homework Nysha\n\nImage text:\nHomework title: Second Grade Homework\nMonday: Read aloud",
            source="telegram_photo",
            photo_path="/static/dashboard/uploads/homework/two.jpg",
        )
        pending = response["data"]["pending_action"]

        resolved = self.tools.resolve_duplicate_assignment(pending, "new")

        self.assertEqual(resolved["status"], "ok")
        items = self.provider.list_items(child="Nysha")
        self.assertEqual(len(items), 2)
        created = resolved["data"]["item"]
        self.assertIn("similar_to_item_id", created["metadata_json"])

    def test_claw_resolves_pending_duplicate_followup(self):
        claw = HomeworkClaw(self.tools)
        claw.capture_from_request(
            "/capture homework Nysha\n\nImage text:\nHomework title: Second Grade Homework\nMonday: Read aloud",
            source="telegram_photo",
            photo_path="/static/dashboard/uploads/homework/one.jpg",
        )
        reply = claw.capture_from_request(
            "/capture homework Nysha\n\nImage text:\nHomework title: Second Grade Homework\nMonday: Read aloud",
            source="telegram_photo",
            photo_path="/static/dashboard/uploads/homework/two.jpg",
        )
        self.assertIn("looks similar", reply)
        self.assertIsNotNone(claw.pending_action)

        resolved = claw.capture_from_request("attach")

        self.assertIn("Attached homework photo", resolved)
        self.assertIsNone(claw.pending_action)

    def test_provider_migrates_metadata_columns(self):
        db_path = Path(self.tmp.name) / "legacy.db"
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                """
                CREATE TABLE homework_items (
                    id TEXT PRIMARY KEY,
                    child TEXT NOT NULL,
                    title TEXT NOT NULL,
                    subject TEXT,
                    assigned_date TEXT NOT NULL,
                    due_date TEXT,
                    status TEXT NOT NULL,
                    notes TEXT,
                    grade TEXT,
                    week_range TEXT,
                    daily_work TEXT,
                    raw_input TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE homework_assets (
                    id TEXT PRIMARY KEY,
                    homework_item_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    path TEXT,
                    ocr_text TEXT,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
        provider = SQLiteHomeworkProvider(db_path)

        with sqlite3.connect(db_path) as connection:
            item_columns = {row[1] for row in connection.execute("PRAGMA table_info(homework_items)").fetchall()}
            asset_columns = {row[1] for row in connection.execute("PRAGMA table_info(homework_assets)").fetchall()}
        self.assertIn("metadata_json", item_columns)
        self.assertIn("content_fingerprint", item_columns)
        self.assertIn("photo_sha256", asset_columns)


if __name__ == "__main__":
    unittest.main()

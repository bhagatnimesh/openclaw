from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from claws.homework.claw import HomeworkClaw
from claws.homework.provider import SQLiteHomeworkProvider
from claws.homework.tools import (
    CHERRY_BLOSSOM_EVENT_LABEL_COLOR,
    HomeworkTools,
    ensure_default_class_schedules,
    homework_content_fingerprint,
)


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


class FakeHomeworkFieldExtractor:
    def __init__(
        self,
        fields: dict[str, object] | None = None,
        *,
        action: str = "capture_assignment",
        missing_fields: list[str] | None = None,
        clarification_question: str | None = None,
        fail: bool = False,
    ) -> None:
        self.fields = fields or {}
        self.action = action
        self.missing_fields = missing_fields or []
        self.clarification_question = clarification_question
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    def extract(self, request: str, **kwargs):
        self.calls.append({"request": request, **kwargs})
        if self.fail:
            raise RuntimeError("ai unavailable")
        return {
            "action": self.action,
            "confidence": 0.94,
            "slots": self.fields,
            "missing_fields": self.missing_fields,
            "clarification_question": self.clarification_question,
            "normalized_request": request,
        }


class SequencedHomeworkFieldExtractor:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def extract(self, request: str, **kwargs):
        self.calls.append({"request": request, **kwargs})
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


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
        class_summary = (Path(self.tmp.name) / "homework" / "Nysha" / "math.md").read_text(encoding="utf-8")
        self.assertIn("# Nysha Math Homework", class_summary)
        self.assertIn("All About Me", class_summary)
        self.assertEqual(len(self.calendar.created), 1)
        self.assertEqual(self.calendar.created[0]["calendar_name"], "Nysha School Calendar")
        self.assertEqual(
            self.calendar.created[0]["event_label_background_color"],
            CHERRY_BLOSSOM_EVENT_LABEL_COLOR,
        )
        self.assertEqual(self.calendar.created[0]["start_time"], "2026-08-21T07:00:00-07:00")
        self.assertEqual(self.calendar.created[0]["end_time"], "2026-08-21T07:30:00-07:00")
        self.assertIn("Added due-date reminder", response["message"])

    def test_lesson_capture_keeps_all_pages_and_learning_observations(self):
        response = self.tools.capture_assignment(
            "/lesson Nysha RSM Math lesson 01\n\nImage text:\nHomework title: Pattern practice\nVisible instructions: Solve addition equations",
            source="telegram_photo",
            photo_assets=[
                {"path": "/static/dashboard/uploads/homework/one.jpg", "ocr_text": "Pattern practice", "photo_sha256": "one"},
                {"path": "/static/dashboard/uploads/homework/two.jpg", "ocr_text": "Solve addition equations", "photo_sha256": "two"},
            ],
        )

        self.assertEqual(response["status"], "ok")
        item = response["data"]["item"]
        self.assertEqual(item["record_type"], "lesson")
        self.assertEqual(len(self.provider.list_assets(item["id"])), 2)
        self.assertTrue(self.provider.list_learning_observations(item["id"]))

    def test_parent_note_updates_learning_record_and_review(self):
        captured = self.tools.capture_assignment("/lesson Nysha math\n\nImage text:\nVisible instructions: addition")
        item = captured["data"]["item"]

        note = self.tools.add_parent_note(str(item["id"]), "She enjoyed pictures but needed help starting.")
        review = self.tools.learning_review(child="Nysha", subject="math")

        self.assertEqual(note["status"], "ok")
        self.assertIn("needed help starting", self.provider.list_items(child="Nysha")[0]["parent_notes"])
        self.assertEqual(review["status"], "ok")
        self.assertIn("Learning review for Nysha", review["message"])

    def test_explicit_due_time_overrides_default_calendar_time(self):
        self.tools.capture_assignment(
            "/capture homework Nysha math due August 18 at 8 pm",
            now=REFERENCE_TIME,
        )

        self.assertEqual(self.calendar.created[0]["start_time"], "2026-08-18T20:00:00-07:00")
        self.assertEqual(self.calendar.created[0]["end_time"], "2026-08-18T20:30:00-07:00")

    def test_missing_due_date_stores_item_and_asks_for_followup(self):
        from claws.homework import tools as homework_tools

        with patch.object(homework_tools, "DEFAULT_CLASS_SCHEDULES", ()):
            response = self.tools.capture_assignment("/capture homework Nysha spelling worksheet")

        self.assertEqual(response["status"], "needs_information")
        self.assertIn("What due date", response["message"])
        self.assertEqual(response["data"]["item"]["child"], "Nysha")
        self.assertEqual(self.calendar.created, [])

    def test_due_date_followup_updates_item_then_time_followup_creates_calendar_event(self):
        from claws.homework import tools as homework_tools

        claw = HomeworkClaw(self.tools)
        with patch.object(homework_tools, "DEFAULT_CLASS_SCHEDULES", ()):
            first = claw.capture_from_request(
                "/capture homework art class",
                reference_time=REFERENCE_TIME,
            )

        self.assertIn("What due date", first)
        self.assertIsNotNone(claw.pending_action)

        second = claw.capture_from_request("Due next Saturday", reference_time=REFERENCE_TIME)

        self.assertIn("Added due-date reminder", second)
        item = self.provider.list_items(child="Nysha")[0]
        self.assertEqual(item["due_date"], "2026-08-15")
        self.assertIsNone(claw.pending_action)
        self.assertEqual(self.calendar.created[0]["calendar_name"], "Nysha School Calendar")
        self.assertEqual(self.calendar.created[0]["start_time"], "2026-08-15T07:00:00-07:00")

    def test_due_date_followup_accepts_bare_date_answer(self):
        from claws.homework import tools as homework_tools

        claw = HomeworkClaw(self.tools)
        with patch.object(homework_tools, "DEFAULT_CLASS_SCHEDULES", ()):
            first = claw.capture_from_request(
                "/capture homework spelling worksheet",
                reference_time=REFERENCE_TIME,
            )
        self.assertIn("What due date", first)

        second = claw.capture_from_request("tomorrow", reference_time=REFERENCE_TIME)

        self.assertIn("Added due-date reminder", second)
        self.assertIsNone(claw.pending_action)
        item = self.provider.list_items(child="Nysha")[0]
        self.assertEqual(item["due_date"], "2026-08-15")

    def test_due_date_followup_preserves_original_due_time(self):
        from claws.homework import tools as homework_tools

        claw = HomeworkClaw(self.tools)
        with patch.object(homework_tools, "DEFAULT_CLASS_SCHEDULES", ()):
            first = claw.capture_from_request(
                "/capture homework art class at 8 am",
                reference_time=REFERENCE_TIME,
            )
        self.assertIn("What due date", first)

        second = claw.capture_from_request("tomorrow", reference_time=REFERENCE_TIME)

        self.assertIn("Added due-date reminder", second)
        self.assertEqual(self.calendar.created[0]["start_time"], "2026-08-15T08:00:00-07:00")

    def test_pending_due_date_does_not_consume_new_capture_request(self):
        from claws.homework import tools as homework_tools

        claw = HomeworkClaw(self.tools)
        with patch.object(homework_tools, "DEFAULT_CLASS_SCHEDULES", ()):
            first = claw.capture_from_request(
                "/capture homework art class",
                reference_time=REFERENCE_TIME,
            )
            second = claw.capture_from_request(
                "/capture homework Navya art class due 2026-08-21",
                reference_time=REFERENCE_TIME,
            )

        self.assertIn("What due date", first)
        self.assertIn("Captured homework for Navya", second)
        self.assertEqual(len(self.provider.list_items(child="Nysha")), 1)
        self.assertEqual(len(self.provider.list_items(child="Navya")), 1)

    def test_homework_without_child_defaults_to_nysha(self):
        response = self.tools.capture_assignment(
            "/capture homework art class due next Saturday",
            now=REFERENCE_TIME,
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["item"]["child"], "Nysha")
        self.assertEqual(self.calendar.created[0]["calendar_name"], "Nysha School Calendar")

    def test_ai_field_extractor_refines_homework_fields_before_calendar_event(self):
        extractor = FakeHomeworkFieldExtractor(
            {
                "child": "Navya",
                "title": "Practice writing your name",
                "class_name": "Art",
                "due_date": "2026-08-15",
                "due_time": "10:00",
                "notes": "Detected from homework photo.",
            }
        )
        tools = HomeworkTools(
            self.provider,
            homework_root=Path(self.tmp.name) / "homework",
            calendar_tools=self.calendar,
            field_extractor=extractor,
        )

        response = tools.capture_assignment(
            "/capture homework art class for Navya practice writing your name",
            now=REFERENCE_TIME,
            source="telegram_photo",
        )

        self.assertEqual(response["status"], "ok")
        item = response["data"]["item"]
        self.assertEqual(item["child"], "Navya")
        self.assertEqual(item["title"], "Practice writing your name")
        self.assertEqual(item["subject"], "Art")
        self.assertEqual(item["due_date"], "2026-08-15")
        self.assertEqual(self.calendar.created[0]["calendar_name"], "Navya School Calendar")
        self.assertEqual(self.calendar.created[0]["start_time"], "2026-08-15T10:00:00-07:00")
        self.assertEqual(extractor.calls[0]["baseline_intent"]["child"], "Navya")
        self.assertIn("class_schedules", extractor.calls[0]["context"])

    def test_ai_field_extractor_failure_falls_back_to_deterministic_parse(self):
        tools = HomeworkTools(
            self.provider,
            homework_root=Path(self.tmp.name) / "homework",
            calendar_tools=self.calendar,
            field_extractor=FakeHomeworkFieldExtractor(fail=True),
        )

        response = tools.capture_assignment(
            "/capture homework Nysha art class",
            now=REFERENCE_TIME,
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["item"]["child"], "Nysha")
        self.assertEqual(response["data"]["item"]["due_date"], "2026-08-15")

    def test_ai_clarify_response_asks_before_storing_default_assignment(self):
        tools = HomeworkTools(
            self.provider,
            homework_root=Path(self.tmp.name) / "homework",
            calendar_tools=self.calendar,
            field_extractor=FakeHomeworkFieldExtractor(
                action="clarify",
                missing_fields=["child", "due_date"],
                clarification_question="Who is this homework for and when is it due?",
            ),
        )

        response = tools.capture_assignment("/capture homework blurry worksheet", now=REFERENCE_TIME)

        self.assertEqual(response["status"], "needs_information")
        self.assertEqual(response["message"], "Who is this homework for and when is it due?")
        self.assertEqual(response["data"]["pending_action"]["action"], "clarify_homework_capture")
        self.assertEqual(self.provider.list_items(child="Nysha"), [])
        self.assertEqual(self.calendar.created, [])

    def test_ai_clarify_followup_completes_original_capture(self):
        extractor = SequencedHomeworkFieldExtractor(
            [
                {
                    "action": "clarify",
                    "confidence": 0.91,
                    "slots": {},
                    "missing_fields": ["child", "due_date"],
                    "clarification_question": "Who is this for and when is it due?",
                    "normalized_request": "/capture homework blurry worksheet",
                },
                {
                    "action": "capture_assignment",
                    "confidence": 0.94,
                    "slots": {
                        "child": "Navya",
                        "title": "Blurry worksheet",
                        "class_name": "Art",
                        "due_date": "2026-08-15",
                    },
                    "missing_fields": [],
                    "normalized_request": "/capture homework blurry worksheet Navya due Saturday",
                },
            ],
        )
        tools = HomeworkTools(
            self.provider,
            homework_root=Path(self.tmp.name) / "homework",
            calendar_tools=self.calendar,
            field_extractor=extractor,
        )
        claw = HomeworkClaw(tools)

        first = claw.capture_from_request("/capture homework blurry worksheet", reference_time=REFERENCE_TIME)
        second = claw.capture_from_request("Navya due Saturday", reference_time=REFERENCE_TIME)

        self.assertIn("Who is this for", first)
        self.assertIn("Captured homework for Navya", second)
        self.assertIsNone(claw.pending_action)
        self.assertEqual(self.provider.list_items(child="Navya")[0]["title"], "Blurry worksheet")

    def test_class_schedule_infers_art_due_date(self):
        self.provider.upsert_class_schedule(
            child="Nysha",
            class_name="Art",
            weekday=5,
            start_time="10:00",
            due_rule="next_class",
            calendar_name="Nysha School Calendar",
            source="manual",
        )

        response = self.tools.capture_assignment(
            "/capture homework art class",
            now=REFERENCE_TIME,
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["item"]["due_date"], "2026-08-15")
        self.assertIn("due_date_inference", response["data"]["item"]["metadata_json"])
        self.assertEqual(self.calendar.created[0]["calendar_name"], "Nysha School Calendar")
        self.assertEqual(self.calendar.created[0]["start_time"], "2026-08-15T10:00:00-07:00")

    def test_generic_math_uses_school_schedule_not_rsm_math(self):
        ensure_default_class_schedules(self.provider, Path(self.tmp.name) / "homework")

        response = self.tools.capture_assignment(
            "/capture homework Nysha math worksheet",
            now=REFERENCE_TIME,
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["item"]["due_date"], "2026-08-14")
        self.assertEqual(self.calendar.created[0]["start_time"], "2026-08-14T07:00:00-07:00")

    def test_school_homework_uses_friday_schedule_when_class_not_named(self):
        self.provider.upsert_class_schedule(
            child="Nysha",
            class_name="School",
            weekday=4,
            due_rule="friday",
            calendar_name="Nysha School Calendar",
            source="manual",
        )

        response = self.tools.capture_assignment(
            "/capture homework spelling worksheet",
            now=datetime(2026, 8, 12, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles")),
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["item"]["child"], "Nysha")
        self.assertEqual(response["data"]["item"]["due_date"], "2026-08-14")

    def test_unknown_class_does_not_fall_back_to_school_schedule(self):
        response = self.tools.capture_assignment(
            "/capture homework music class",
            now=REFERENCE_TIME,
        )

        self.assertEqual(response["status"], "needs_information")
        self.assertIn("What due date", response["message"])
        self.assertEqual(self.calendar.created, [])

    def test_after_school_learning_does_not_match_generic_school_schedule(self):
        response = self.tools.capture_assignment(
            "/capture homework Navya after-school learning",
            now=REFERENCE_TIME,
        )

        self.assertEqual(response["status"], "needs_information")
        self.assertIn("What due date", response["message"])
        self.assertEqual(self.calendar.created, [])

    def test_school_friday_due_rule_can_use_today(self):
        self.provider.upsert_class_schedule(
            child="Nysha",
            class_name="School",
            weekday=4,
            due_rule="friday",
            calendar_name="Nysha School Calendar",
            source="manual",
        )

        response = self.tools.capture_assignment(
            "/capture homework spelling worksheet",
            now=REFERENCE_TIME,
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["item"]["due_date"], "2026-08-14")

    def test_schedule_calendar_name_is_used_for_due_reminder(self):
        self.provider.upsert_class_schedule(
            child="Nysha",
            class_name="Art",
            weekday=5,
            start_time="10:00",
            due_rule="next_class",
            calendar_name="Weekend Classes",
            source="manual",
        )

        response = self.tools.capture_assignment(
            "/capture homework art class",
            now=REFERENCE_TIME,
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(self.calendar.created[0]["calendar_name"], "Weekend Classes")

    def test_explicit_due_date_uses_schedule_time_and_calendar(self):
        self.provider.upsert_class_schedule(
            child="Nysha",
            class_name="Art",
            weekday=5,
            start_time="10:00",
            due_rule="next_class",
            calendar_name="Weekend Classes",
            source="manual",
        )

        response = self.tools.capture_assignment(
            "/capture homework art class due 2026-08-15",
            now=REFERENCE_TIME,
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["item"]["due_date"], "2026-08-15")
        self.assertEqual(self.calendar.created[0]["start_time"], "2026-08-15T10:00:00-07:00")
        self.assertEqual(self.calendar.created[0]["calendar_name"], "Weekend Classes")

    def test_provider_schedule_overrides_ai_due_date_for_scheduled_class(self):
        self.provider.upsert_class_schedule(
            child="Nysha",
            class_name="Art",
            weekday=6,
            start_time="11:00",
            due_rule="next_class",
            calendar_name="Updated Art Calendar",
            source="manual",
        )
        extractor = FakeHomeworkFieldExtractor(
            {
                "child": "Nysha",
                "title": "Draw flowers",
                "class_name": "Art",
                "due_date": "2026-08-15",
                "due_time": "10:00",
                "calendar_name": "Nysha School Calendar",
            }
        )
        tools = HomeworkTools(
            self.provider,
            homework_root=Path(self.tmp.name) / "homework",
            calendar_tools=self.calendar,
            field_extractor=extractor,
        )

        response = tools.capture_assignment(
            "/capture homework Nysha art class",
            now=REFERENCE_TIME,
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["item"]["due_date"], "2026-08-16")
        self.assertEqual(self.calendar.created[0]["start_time"], "2026-08-16T11:00:00-07:00")
        self.assertEqual(self.calendar.created[0]["calendar_name"], "Updated Art Calendar")

    def test_ai_refinement_preserves_explicit_user_due_date_over_schedule(self):
        self.provider.upsert_class_schedule(
            child="Nysha",
            class_name="Art",
            weekday=5,
            start_time="10:00",
            due_rule="next_class",
            calendar_name="Nysha School Calendar",
            source="manual",
        )
        extractor = FakeHomeworkFieldExtractor(
            {
                "child": "Nysha",
                "title": "Draw flowers",
                "class_name": "Art",
            }
        )
        tools = HomeworkTools(
            self.provider,
            homework_root=Path(self.tmp.name) / "homework",
            calendar_tools=self.calendar,
            field_extractor=extractor,
        )

        response = tools.capture_assignment(
            "/capture homework Nysha art class due 2026-08-30",
            now=REFERENCE_TIME,
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["item"]["due_date"], "2026-08-30")
        self.assertEqual(self.calendar.created[0]["start_time"], "2026-08-30T10:00:00-07:00")

    def test_ai_refinement_preserves_natural_user_due_date_over_schedule(self):
        self.provider.upsert_class_schedule(
            child="Nysha",
            class_name="Art",
            weekday=6,
            start_time="11:00",
            due_rule="next_class",
            calendar_name="Updated Art Calendar",
            source="manual",
        )
        extractor = FakeHomeworkFieldExtractor(
            {
                "child": "Nysha",
                "title": "Draw flowers",
                "class_name": "Art",
                "due_date": "2026-08-15",
            }
        )
        tools = HomeworkTools(
            self.provider,
            homework_root=Path(self.tmp.name) / "homework",
            calendar_tools=self.calendar,
            field_extractor=extractor,
        )

        response = tools.capture_assignment(
            "/capture homework Nysha art class next Saturday",
            now=REFERENCE_TIME,
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["item"]["due_date"], "2026-08-15")
        self.assertEqual(self.calendar.created[0]["start_time"], "2026-08-15T11:00:00-07:00")

    def test_ai_refinement_preserves_tomorrow_due_date_over_schedule(self):
        self.provider.upsert_class_schedule(
            child="Nysha",
            class_name="Art",
            weekday=6,
            start_time="11:00",
            due_rule="next_class",
            calendar_name="Updated Art Calendar",
            source="manual",
        )
        extractor = FakeHomeworkFieldExtractor(
            {
                "child": "Nysha",
                "title": "Draw flowers",
                "class_name": "Art",
                "due_date": "2026-08-15",
            }
        )
        tools = HomeworkTools(
            self.provider,
            homework_root=Path(self.tmp.name) / "homework",
            calendar_tools=self.calendar,
            field_extractor=extractor,
        )

        response = tools.capture_assignment(
            "/capture homework Nysha art class tomorrow",
            now=REFERENCE_TIME,
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["item"]["due_date"], "2026-08-15")

    def test_ai_refinement_preserves_explicit_due_time_over_schedule_time(self):
        self.provider.upsert_class_schedule(
            child="Nysha",
            class_name="Art",
            weekday=5,
            start_time="10:00",
            due_rule="next_class",
            calendar_name="Nysha School Calendar",
            source="manual",
        )
        extractor = FakeHomeworkFieldExtractor(
            {
                "child": "Nysha",
                "title": "Draw flowers",
                "class_name": "Art",
            }
        )
        tools = HomeworkTools(
            self.provider,
            homework_root=Path(self.tmp.name) / "homework",
            calendar_tools=self.calendar,
            field_extractor=extractor,
        )

        response = tools.capture_assignment(
            "/capture homework Nysha art class due 2026-08-15 at 8 am",
            now=REFERENCE_TIME,
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(self.calendar.created[0]["start_time"], "2026-08-15T08:00:00-07:00")

    def test_default_class_schedules_are_stored_and_written_to_markdown(self):
        ensure_default_class_schedules(self.provider, Path(self.tmp.name) / "homework")

        response = self.tools.list_class_schedules(child="Nysha")

        self.assertEqual(response["status"], "ok")
        self.assertIn("Nysha Art", response["message"])
        self.assertIn("Nysha RSM Math", response["message"])
        schedule_text = (Path(self.tmp.name) / "homework" / "class-schedules.md").read_text(encoding="utf-8")
        self.assertIn("Nysha - Art", schedule_text)
        self.assertIn("Tuesday", schedule_text)

    def test_schedule_markdown_includes_effective_default_schedules(self):
        response = self.tools.capture_assignment(
            "/capture homework music class due 2026-08-21",
            now=REFERENCE_TIME,
        )

        self.assertEqual(response["status"], "ok")
        schedule_text = (Path(self.tmp.name) / "homework" / "class-schedules.md").read_text(encoding="utf-8")
        self.assertIn("Nysha - Art", schedule_text)
        self.assertIn("Navya - Art", schedule_text)
        nysha_schedule = (Path(self.tmp.name) / "homework" / "Nysha" / "class-schedule.md").read_text(encoding="utf-8")
        navya_schedule = (Path(self.tmp.name) / "homework" / "Navya" / "class-schedule.md").read_text(encoding="utf-8")
        self.assertIn("Nysha - Art", nysha_schedule)
        self.assertIn("Navya - Art", navya_schedule)

    def test_build_default_tools_does_not_seed_schedules_or_rewrite_markdown(self):
        from claws.homework import tools as homework_tools

        with (
            patch.object(homework_tools, "SQLiteHomeworkProvider", return_value=self.provider),
            patch.object(homework_tools, "ensure_default_class_schedules") as ensure_defaults,
        ):
            default_tools = homework_tools.build_default_tools()

        self.assertIs(default_tools.provider, self.provider)
        ensure_defaults.assert_not_called()

    def test_lists_current_homework_by_child_and_due_date(self):
        self.tools.capture_assignment("/capture homework Nysha reading due 2026-08-22")
        self.tools.capture_assignment("/capture homework Nysha math due 2026-08-21")

        response = self.tools.list_homework(child="Nysha")

        self.assertEqual(response["status"], "ok")
        titles = [item["title"] for item in response["data"]["items"]]
        self.assertEqual(titles, ["Math homework", "Reading homework"])

    def test_complete_homework_marks_item_submitted(self):
        captured = self.tools.capture_assignment("/capture homework Nysha reading due 2026-08-22")
        item_id = captured["data"]["item"]["id"]

        response = self.tools.complete_homework(item_id, now=REFERENCE_TIME)

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["item"]["status"], "submitted")
        open_items = self.tools.list_homework(child="Nysha")["data"]["items"]
        self.assertEqual(open_items, [])

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

    def test_homework_complete_command_does_not_create_new_assignment(self):
        captured = self.tools.capture_assignment(
            "/capture homework Nysha art class due 2026-08-22",
            now=REFERENCE_TIME,
        )

        response = self.tools.capture_submission(
            "/homework complete art class Nysha\n\nImage text:\nSpring season",
            now=REFERENCE_TIME,
            source="telegram_photo",
            photo_path="/static/dashboard/uploads/homework/completed-art.jpg",
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["item"]["id"], captured["data"]["item"]["id"])
        self.assertEqual(response["data"]["item"]["status"], "submitted")
        self.assertEqual(len(self.provider.list_items(child="Nysha", limit=20)), 1)
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
            "Due date: 2026-08-28\n"
            "Monday: All About Me Book 25 minutes",
            source="telegram_photo",
            photo_path="/static/dashboard/uploads/homework/one.jpg",
        )

        response = self.tools.capture_assignment(
            "/capture homework Nysha\n\n"
            "Image text:\n"
            "Homework title: Second Grade Homework\n"
            "Due date: 2026-08-28\n"
            "Monday: Practice subtraction facts",
            source="telegram_photo",
            photo_path="/static/dashboard/uploads/homework/two.jpg",
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(len(self.provider.list_items(child="Nysha")), 2)

    def test_pending_duplicate_attach_adds_asset_without_calendar_event(self):
        from claws.homework import tools as homework_tools

        with patch.object(homework_tools, "DEFAULT_CLASS_SCHEDULES", ()):
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
        from claws.homework import tools as homework_tools

        with patch.object(homework_tools, "DEFAULT_CLASS_SCHEDULES", ()):
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

        self.assertEqual(resolved["status"], "needs_information")
        items = self.provider.list_items(child="Nysha")
        self.assertEqual(len(items), 2)
        created = resolved["data"]["item"]
        self.assertIn("similar_to_item_id", created["metadata_json"])

    def test_claw_resolves_pending_duplicate_followup(self):
        claw = HomeworkClaw(self.tools)
        claw.capture_from_request(
            "/capture homework Nysha due 2026-08-21\n\nImage text:\nHomework title: Second Grade Homework\nMonday: Read aloud",
            source="telegram_photo",
            photo_path="/static/dashboard/uploads/homework/one.jpg",
        )
        reply = claw.capture_from_request(
            "/capture homework Nysha due 2026-08-21\n\nImage text:\nHomework title: Second Grade Homework\nMonday: Read aloud",
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

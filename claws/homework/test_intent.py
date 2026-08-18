from __future__ import annotations

from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from claws.homework.intent import extract_intent


REFERENCE_TIME = datetime(2026, 8, 14, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))


class HomeworkIntentTest(unittest.TestCase):
    def test_ocr_packet_extracts_second_grade_homework_fields(self):
        intent = extract_intent(
            "/capture homework Nysha\n\n"
            "Image text:\n"
            "Homework title: All About Me\n"
            "Student: Nysha\n"
            "Grade: 2nd grade\n"
            "Week range: August 17 - August 21\n"
            "Due date: August 21\n"
            "Subject: Writing\n"
            "Visible instructions: Complete one box each day. Parent signature required.\n"
            "Monday: Draw your family\n"
            "Tuesday: Write three facts",
            now=REFERENCE_TIME,
            source="telegram_photo",
            photo_path="/static/dashboard/uploads/homework/photo.jpg",
        )

        self.assertEqual(intent["intent"], "capture_assignment")
        self.assertEqual(intent["child"], "Nysha")
        self.assertEqual(intent["title"], "All About Me")
        self.assertEqual(intent["grade"], "2nd grade")
        self.assertEqual(intent["week_range"], "August 17 - August 21")
        self.assertEqual(intent["due_date"], "2026-08-21")
        self.assertEqual(intent["subject"], "Writing")
        self.assertIn("Monday: Draw your family", intent["daily_work"])
        self.assertIn("Homework title: All About Me", intent["ocr_text"])

    def test_caption_overrides_ocr_metadata(self):
        intent = extract_intent(
            "/capture homework Nysha math due Friday\n\n"
            "Image text:\n"
            "Homework title: Weekly Packet\n"
            "Subject: Reading\n"
            "Due date: August 28",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["subject"], "Math")
        self.assertEqual(intent["due_date"], "2026-08-21")

    def test_return_completed_packet_instruction_stays_assignment(self):
        intent = extract_intent(
            "/capture homework\n\n"
            "Image text:\n"
            "Second Grade Homework\n"
            "The All About Me project is due Friday, August 28\n"
            "Please return completed packet with all assignments "
            "completed according to directions provided.\n"
            "Parent signature",
            now=REFERENCE_TIME,
            source="telegram_photo",
        )

        self.assertEqual(intent["intent"], "capture_assignment")
        self.assertEqual(intent["status"], "assigned")
        self.assertEqual(intent["title"], "All About Me project")
        self.assertEqual(intent["due_date"], "2026-08-28")
        self.assertEqual(intent["notes"], "Parent signature required.")

    def test_explicit_submitted_homework_stays_submission(self):
        intent = extract_intent("/capture submitted homework Nysha All About Me", now=REFERENCE_TIME)

        self.assertEqual(intent["intent"], "capture_submission")
        self.assertEqual(intent["status"], "submitted")

    def test_missing_due_date_still_creates_assignment_intent(self):
        intent = extract_intent("/capture homework Nysha spelling worksheet", now=REFERENCE_TIME)

        self.assertEqual(intent["intent"], "capture_assignment")
        self.assertEqual(intent["child"], "Nysha")
        self.assertEqual(intent["subject"], "Spelling")
        self.assertIsNone(intent["due_date"])

    def test_next_weekday_due_date_is_extracted(self):
        intent = extract_intent(
            "/capture homework art class due next Saturday",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["child"], "Nysha")
        self.assertEqual(intent["subject"], "Art")
        self.assertEqual(intent["due_date"], "2026-08-15")

    def test_standalone_next_weekday_due_date_is_extracted(self):
        intent = extract_intent(
            "/capture homework Nysha art class next Saturday\n\n"
            "Image text: Draw same flower & color both",
            now=datetime(2026, 8, 15, 14, 2, tzinfo=ZoneInfo("America/Los_Angeles")),
        )

        self.assertEqual(intent["child"], "Nysha")
        self.assertEqual(intent["subject"], "Art")
        self.assertEqual(intent["due_date"], "2026-08-22")
        self.assertNotIn("next Saturday", intent["title"])
        self.assertNotIn("Image text", intent["title"])

    def test_next_weekday_inside_ocr_text_does_not_invent_due_date(self):
        intent = extract_intent(
            "/capture homework Nysha\n\n"
            "Image text: Read the story Next Saturday and answer the questions",
            now=datetime(2026, 8, 15, 14, 2, tzinfo=ZoneInfo("America/Los_Angeles")),
        )

        self.assertIsNone(intent["due_date"])

    def test_next_weekday_inside_caption_title_does_not_invent_due_date(self):
        intent = extract_intent(
            "/capture homework Nysha Read the story Next Saturday and answer the questions",
            now=datetime(2026, 8, 15, 14, 2, tzinfo=ZoneInfo("America/Los_Angeles")),
        )

        self.assertIsNone(intent["due_date"])
        self.assertIn("Next Saturday", intent["title"])

    def test_ocr_only_capture_uses_visible_text_as_fallback_title(self):
        intent = extract_intent(
            "/capture homework Nysha\n\n"
            "Image text: Draw same flower & color both",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["title"], "Draw same flower & color both")

    def test_ocr_title_fallback_skips_metadata_lines(self):
        intent = extract_intent(
            "/capture homework Nysha\n\n"
            "Image text:\n"
            "Due date: August 28\n"
            "Practice sight words",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["title"], "Practice sight words")

    def test_bare_learning_word_is_not_classified_as_subject(self):
        intent = extract_intent(
            "/capture homework Nysha keep learning your sight words",
            now=REFERENCE_TIME,
        )

        self.assertIsNone(intent["subject"])

    def test_after_two_weeks_due_date_is_extracted(self):
        intent = extract_intent(
            "/capture homework art class after two weeks",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["due_date"], "2026-08-28")

    def test_explicit_due_date_wins_over_unrelated_week_offset_text(self):
        intent = extract_intent(
            "/capture homework\n\n"
            "Image text:\n"
            "Homework title: Science project\n"
            "Due date: September 4\n"
            "You have two weeks to work on it.",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["due_date"], "2026-09-04")

    def test_week_offset_inside_ocr_text_does_not_invent_due_date(self):
        intent = extract_intent(
            "/capture homework Nysha\n\n"
            "Image text: You have two weeks to work on it.",
            now=datetime(2026, 8, 15, 14, 2, tzinfo=ZoneInfo("America/Los_Angeles")),
        )

        self.assertIsNone(intent["due_date"])

    def test_bare_time_followup_is_extracted(self):
        intent = extract_intent("homework 8 am", now=REFERENCE_TIME)

        self.assertEqual(intent["due_time"], "08:00")

    def test_after_school_learning_class_is_subject(self):
        intent = extract_intent(
            "/capture homework Navya after-school learning due Monday",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["child"], "Navya")
        self.assertEqual(intent["subject"], "After-school learning")


if __name__ == "__main__":
    unittest.main()

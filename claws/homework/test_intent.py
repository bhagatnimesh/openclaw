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


if __name__ == "__main__":
    unittest.main()

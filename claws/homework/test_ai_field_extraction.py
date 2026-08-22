from __future__ import annotations

from datetime import datetime
import json
import os
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from claws.homework.ai_field_extraction import (
    DEFAULT_TIMEZONE,
    HomeworkAIFieldExtractor,
    merge_ai_homework_fields,
    validate_homework_ai_fields,
)


class FakeOpenAIResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class HomeworkAIFieldExtractionTest(unittest.TestCase):
    def test_ai_cannot_turn_explicit_submission_into_new_assignment(self):
        refined = merge_ai_homework_fields(
            {
                "intent": "capture_submission",
                "status": "submitted",
                "child": "Nysha",
                "subject": "Art",
            },
            {
                "action": "capture_assignment",
                "confidence": 0.94,
                "slots": {"status": "assigned", "title": "Spring season"},
            },
            "/homework complete art class Nysha",
        )

        self.assertEqual(refined["intent"], "capture_submission")
        self.assertEqual(refined["status"], "submitted")
        self.assertEqual(refined["title"], "Spring season")

    def test_validate_accepts_homework_schema_valid_fields(self):
        frame = validate_homework_ai_fields(
            {
                "action": "capture_assignment",
                "confidence": 0.93,
                "slots": {
                    "child": "Navya",
                    "title": "Practice writing your name",
                    "class_name": "Art",
                    "due_date": "2026-08-15",
                    "due_time": "10:00",
                },
                "missing_fields": [],
            },
            "capture homework art class for Navya",
        )

        self.assertEqual(frame["action"], "capture_assignment")
        self.assertEqual(frame["slots"]["child"], "Navya")
        self.assertEqual(frame["slots"]["class_name"], "Art")
        self.assertEqual(frame["normalized_request"], "capture homework art class for Navya")

    def test_validate_rejects_invalid_action_low_confidence_child_and_raw_email(self):
        with self.assertRaises(ValueError):
            validate_homework_ai_fields(
                {"action": "send_email", "confidence": 0.99, "slots": {}},
                "request",
            )
        with self.assertRaises(ValueError):
            validate_homework_ai_fields(
                {"action": "capture_assignment", "confidence": 0.4, "slots": {}},
                "request",
            )
        with self.assertRaises(ValueError):
            validate_homework_ai_fields(
                {"action": "capture_assignment", "confidence": 0.99, "slots": {"child": "Someone"}},
                "request",
            )
        with self.assertRaises(ValueError):
            validate_homework_ai_fields(
                {
                    "action": "capture_assignment",
                    "confidence": 0.99,
                    "slots": {"notes": "send to teacher@example.test"},
                },
                "request",
            )

    def test_validate_drops_malformed_ai_date_time_fields(self):
        frame = validate_homework_ai_fields(
            {
                "action": "capture_assignment",
                "confidence": 0.91,
                "slots": {
                    "child": "Nysha",
                    "title": "Math worksheet",
                    "due_date": "Friday after school",
                    "assigned_date": "yesterday",
                    "due_time": "tomorrow",
                },
                "missing_fields": [],
            },
            "request",
        )

        self.assertEqual(
            frame["slots"],
            {
                "child": "Nysha",
                "title": "Math worksheet",
            },
        )

    def test_validate_maps_homework_and_calendar_api_context_fields(self):
        frame = validate_homework_ai_fields(
            {
                "operation": "capture_assignment",
                "confidence": 0.96,
                "homework": {
                    "child": "Nysha",
                    "title": "Draw same flower and color both",
                    "class_name": "Art",
                },
                "calendar": {
                    "create_due_event": True,
                    "calendar_name": "Nysha School Calendar",
                    "event": {
                        "summary": "Homework due: Draw same flower and color both",
                        "start": {
                            "dateTime": "2026-08-22T10:00:00-07:00",
                            "timeZone": DEFAULT_TIMEZONE,
                        },
                    },
                },
                "missing_fields": [],
            },
            "/capture homework Nysha art class next Saturday",
        )

        self.assertEqual(frame["action"], "capture_assignment")
        self.assertEqual(frame["slots"]["child"], "Nysha")
        self.assertEqual(frame["slots"]["title"], "Draw same flower and color both")
        self.assertEqual(frame["slots"]["class_name"], "Art")
        self.assertEqual(frame["slots"]["due_date"], "2026-08-22")
        self.assertEqual(frame["slots"]["due_time"], "10:00")
        self.assertNotIn("calendar_name", frame["slots"])

    def test_extract_posts_store_false_schema_and_uses_timeout(self):
        calls = []

        def fake_urlopen(request, timeout):
            calls.append(
                {
                    "timeout": timeout,
                    "body": json.loads(request.data.decode("utf-8")),
                }
            )
            return FakeOpenAIResponse(
                {
                    "output": [
                        {
                            "content": [
                                {
                                    "text": json.dumps(
                                        {
                                            "action": "capture_assignment",
                                            "confidence": 0.93,
                                            "slots": {"child": "Nysha", "class_name": "Art"},
                                            "missing_fields": ["due_date"],
                                        }
                                    )
                                }
                            ]
                        }
                    ]
                }
            )

        extractor = HomeworkAIFieldExtractor(api_key="test-key", urlopen=fake_urlopen)

        frame = extractor.extract(
            "/capture homework art class",
            now=datetime(2026, 8, 15, 14, 2, tzinfo=ZoneInfo(DEFAULT_TIMEZONE)),
            baseline_intent={"intent": "capture_assignment"},
            context={"class_schedules": []},
        )

        self.assertEqual(frame["action"], "capture_assignment")
        self.assertEqual(frame["missing_fields"], ["due_date"])
        self.assertEqual(calls[0]["timeout"], 8)
        self.assertFalse(calls[0]["body"]["store"])
        user_payload = json.loads(calls[0]["body"]["input"][1]["content"])
        self.assertIn("homework", user_payload["output_schema"])
        self.assertIn("calendar", user_payload["output_schema"])

    def test_from_env_or_none_is_explicit_opt_in(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            self.assertIsNone(HomeworkAIFieldExtractor.from_env_or_none())

        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test-key",
                "N4OS_HOMEWORK_AI_FIELD_EXTRACTION_ENABLED": "true",
            },
            clear=True,
        ):
            self.assertIsInstance(
                HomeworkAIFieldExtractor.from_env_or_none(),
                HomeworkAIFieldExtractor,
            )


if __name__ == "__main__":
    unittest.main()

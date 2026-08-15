import json
import os
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from ai_field_extraction import (
    CalendarAIFieldExtractor,
    validate_calendar_ai_fields,
)
from tools import DEFAULT_TIMEZONE


class FakeOpenAIResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class CalendarAIFieldExtractionTest(unittest.TestCase):
    def test_validate_accepts_schema_valid_fields(self):
        frame = validate_calendar_ai_fields(
            {
                "action": "create_event",
                "confidence": 0.91,
                "slots": {
                    "title": "Dinner",
                    "date": "2026-08-13",
                    "start_time": "18:00",
                    "guest_aliases": ["mom", "dad"],
                },
                "missing_fields": [],
            },
            "invite mom and dad to dinner tomorrow at 6",
        )

        self.assertEqual(frame["action"], "create_event")
        self.assertEqual(frame["slots"]["guest_aliases"], ["mom", "dad"])
        self.assertEqual(frame["normalized_request"], "invite mom and dad to dinner tomorrow at 6")

    def test_validate_rejects_invalid_action_low_confidence_and_raw_email(self):
        with self.assertRaises(ValueError):
            validate_calendar_ai_fields(
                {"action": "send_email", "confidence": 0.99, "slots": {}},
                "request",
            )
        with self.assertRaises(ValueError):
            validate_calendar_ai_fields(
                {"action": "create_event", "confidence": 0.4, "slots": {}},
                "request",
            )
        with self.assertRaises(ValueError):
            validate_calendar_ai_fields(
                {
                    "action": "add_guests",
                    "confidence": 0.99,
                    "slots": {"description": "invite dad@example.test"},
                },
                "request",
            )

    def test_extract_posts_store_false_and_uses_timeout(self):
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
                                            "action": "add_guests",
                                            "confidence": 0.93,
                                            "slots": {"guest_aliases": ["mom", "dad"]},
                                            "missing_fields": [],
                                        }
                                    )
                                }
                            ]
                        }
                    ]
                }
            )

        extractor = CalendarAIFieldExtractor(api_key="test-key", urlopen=fake_urlopen)

        frame = extractor.extract(
            "Add guest mom and dad to the invite",
            now=datetime(2026, 8, 12, 18, 4, tzinfo=ZoneInfo(DEFAULT_TIMEZONE)),
            baseline_intent={"intent": "create_event"},
        )

        self.assertEqual(frame["action"], "add_guests")
        self.assertEqual(calls[0]["timeout"], 8)
        self.assertFalse(calls[0]["body"]["store"])

    def test_validate_maps_google_calendar_api_context_fields(self):
        frame = validate_calendar_ai_fields(
            {
                "operation": "create_event",
                "confidence": 0.96,
                "calendar": {"name": "kids calendar"},
                "event": {
                    "summary": "Trash pickup",
                    "start": {"date": "2026-08-13", "timeZone": DEFAULT_TIMEZONE},
                    "end": {"date": "2026-08-14", "timeZone": DEFAULT_TIMEZONE},
                    "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=TH"],
                    "attendees": [{"alias": "parents"}],
                },
                "missing_fields": [],
            },
            "Trash pickup every Thursday all day starting Aug 13 on kids calendar",
        )

        self.assertEqual(frame["action"], "create_event")
        self.assertEqual(
            frame["slots"],
            {
                "title": "Trash pickup",
                "date": "2026-08-13",
                "all_day": True,
                "calendar_name": "kids calendar",
                "guest_aliases": ["parents"],
                "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=TH"],
            },
        )

    def test_from_env_or_none_is_explicit_opt_in(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            self.assertIsNone(CalendarAIFieldExtractor.from_env_or_none())

        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test-key",
                "N4OS_CALENDAR_AI_FIELD_EXTRACTION_ENABLED": "true",
            },
            clear=True,
        ):
            self.assertIsInstance(
                CalendarAIFieldExtractor.from_env_or_none(),
                CalendarAIFieldExtractor,
            )

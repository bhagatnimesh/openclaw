import json
import os
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from ai_field_extraction import TaskAIFieldExtractor, validate_task_ai_fields


def _valid_payload(**overrides):
    payload = {
        "operation": "create_task",
        "confidence": 0.96,
        "task_list": {"title": "School", "id_hint": None},
        "task": {
            "title": "Call FUSD",
            "notes": "Ask about the waitlist",
            "due": "2026-08-24",
            "metadata": {
                "tags": ["school"],
                "context": ["phone"],
                "energy": "low",
                "duration_minutes": 15,
                "urgency": "high",
                "complexity": "low",
                "effort_type": "communication",
                "requires": ["phone"],
                "can_do_while": [],
                "location": "anywhere",
                "owner": "dad",
                "assistant_help_needed": False,
                "assistant_name": None,
                "assistant_help_request": None,
                "assistant_context": None,
            },
        },
        "target": {"query": None},
        "update": {
            "title": None,
            "notes": None,
            "due": None,
            "owner": None,
            "tags": [],
            "assistant_help_request": None,
        },
        "filters": {
            "tags": [],
            "owner": None,
            "context": [],
            "available_resources": [],
            "unavailable_resources": [],
            "can_do_while": [],
            "energy": None,
            "effort_type": None,
            "due_min": None,
            "due_max": None,
            "duration_minutes": None,
        },
        "missing_fields": [],
        "clarification_question": None,
        "assumptions": ["voice_transcript", "inferred_due_date"],
    }
    payload.update(overrides)
    return payload


class FakeOpenAIResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class TaskAIFieldExtractionTest(unittest.TestCase):
    def test_validate_normalizes_task_fields(self):
        result = validate_task_ai_fields(_valid_payload(), "call FUSD next Monday")

        self.assertEqual(result["intent"], "create_task")
        self.assertEqual(result["due"], "2026-08-24")
        self.assertEqual(result["task_list_name"], "School")
        self.assertEqual(result["metadata"]["owner"], "dad")
        self.assertEqual(result["assumptions"], ["voice_transcript", "inferred_due_date"])

    def test_validate_removes_contradictory_missing_fields(self):
        payload = _valid_payload()
        payload["missing_fields"] = ["title", "task", "task_list"]
        payload["clarification_question"] = "What task and list?"

        result = validate_task_ai_fields(payload, "call FUSD next Monday")

        self.assertEqual(result["missing_fields"], [])
        self.assertIsNone(result["clarification_question"])

    def test_validate_rejects_low_confidence_invalid_action_and_due(self):
        with self.assertRaises(ValueError):
            validate_task_ai_fields(_valid_payload(confidence=0.4), "request")
        with self.assertRaises(ValueError):
            validate_task_ai_fields(_valid_payload(operation="send_email"), "request")
        payload = _valid_payload()
        payload["task"]["due"] = "next someday"
        with self.assertRaises(ValueError):
            validate_task_ai_fields(payload, "request")

    def test_validate_rejects_targetless_mutation_even_with_task_title(self):
        payload = _valid_payload(operation="update_task")
        payload["target"] = {"query": None}

        with self.assertRaises(ValueError):
            validate_task_ai_fields(payload, "update old school form")

        payload = _valid_payload(operation="update_task")
        payload["update"]["due"] = "next Friday"
        with self.assertRaises(ValueError):
            validate_task_ai_fields(payload, "request")

    def test_extract_uses_strict_schema_and_reference_time(self):
        calls = []

        def fake_urlopen(request, timeout):
            calls.append({"body": json.loads(request.data), "timeout": timeout})
            return FakeOpenAIResponse({"output_text": json.dumps(_valid_payload())})

        result = TaskAIFieldExtractor(api_key="test-key", urlopen=fake_urlopen).extract(
            "Call FUSD next Monday",
            now=datetime(2026, 8, 21, 10, tzinfo=ZoneInfo("America/Los_Angeles")),
        )

        self.assertEqual(result["title"], "Call FUSD")
        self.assertFalse(calls[0]["body"]["store"])
        schema_format = calls[0]["body"]["text"]["format"]
        self.assertEqual(schema_format["type"], "json_schema")
        self.assertTrue(schema_format["strict"])
        self.assertFalse(schema_format["schema"]["additionalProperties"])
        self.assertFalse(schema_format["schema"]["properties"]["filters"]["additionalProperties"])
        user_payload = json.loads(calls[0]["body"]["input"][1]["content"])
        self.assertIn("2026-08-21T10:00:00", user_payload["reference_time"])

    def test_from_env_is_explicit_opt_in(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            self.assertIsNone(TaskAIFieldExtractor.from_env_or_none())
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "test-key", "N4OS_INTENT_REFINEMENT_ENABLED": "true"},
            clear=True,
        ):
            self.assertIsInstance(TaskAIFieldExtractor.from_env_or_none(), TaskAIFieldExtractor)


if __name__ == "__main__":
    unittest.main()

import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from claw import N4OSClaw
from intent_router import route_request


REFERENCE_TIME = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))


class FakeCalendarClaw:
    def __init__(self):
        self.calls = []

    def handle_pending_response(self, request):
        return False

    def create_event_from_request(self, request, reference_time=None):
        self.calls.append(("create", request, reference_time))

    def list_events_from_request(self, request, reference_time=None):
        self.calls.append(("list", request, reference_time))

    def briefing_from_request(self, request, reference_time=None):
        self.calls.append(("briefing", request, reference_time))

    def preparation_from_request(self, request, reference_time=None):
        self.calls.append(("preparation", request, reference_time))

    def delete_event_from_request(self, request, reference_time=None):
        self.calls.append(("delete", request, reference_time))

    def update_event_from_request(self, request, reference_time=None):
        self.calls.append(("update", request, reference_time))


class FakeTasksClaw:
    def __init__(self):
        self.calls = []

    def handle_pending_response(self, request):
        return False

    def add_task_from_request(self, request, reference_time=None):
        self.calls.append(("create", request, reference_time))

    def recommend_tasks_from_request(self, request, reference_time=None):
        self.calls.append(("recommend", request, reference_time))

    def complete_task_from_request(self, request):
        self.calls.append(("complete", request, None))

    def delete_task_from_request(self, request):
        self.calls.append(("delete", request, None))


class MissingGoogleTasksClaw:
    @classmethod
    def default(cls):
        raise ModuleNotFoundError("No module named 'google'", name="google")


class IntentRouterTest(unittest.TestCase):
    def test_routes_calendar_creation(self):
        decision = route_request("Add dentist tomorrow at 3pm", now=REFERENCE_TIME)

        self.assertEqual(decision["route"], "calendar")
        self.assertGreaterEqual(decision["confidence"], 0.6)
        self.assertIn("family-calendar", decision["intent_summary"])

    def test_routes_task_creation(self):
        decision = route_request(
            "Add task change water filter this weekend",
            now=REFERENCE_TIME,
        )

        self.assertEqual(decision["route"], "tasks")
        self.assertGreaterEqual(decision["confidence"], 0.6)
        self.assertIn("family-tasks", decision["intent_summary"])

    def test_routes_calendar_query(self):
        decision = route_request("What do I have tomorrow?", now=REFERENCE_TIME)

        self.assertEqual(decision["route"], "calendar")
        self.assertGreaterEqual(decision["confidence"], 0.6)

    def test_routes_task_recommendation(self):
        decision = route_request("What can I do while driving?", now=REFERENCE_TIME)

        self.assertEqual(decision["route"], "tasks")
        self.assertGreaterEqual(decision["confidence"], 0.6)

    def test_routes_day_briefing_to_both(self):
        decision = route_request("Give me my day briefing", now=REFERENCE_TIME)

        self.assertEqual(decision["route"], "both")
        self.assertGreaterEqual(decision["confidence"], 0.6)

    def test_low_confidence_asks_for_clarification(self):
        calendar = FakeCalendarClaw()
        tasks = FakeTasksClaw()
        claw = N4OSClaw(calendar_claw=calendar, tasks_claw=tasks)

        output = StringIO()
        with redirect_stdout(output):
            decision = claw.handle_request("hmm maybe later", reference_time=REFERENCE_TIME)

        self.assertEqual(decision["route"], "unknown")
        self.assertIn("Should I use Calendar, Tasks, or both?", output.getvalue())
        self.assertEqual(calendar.calls, [])
        self.assertEqual(tasks.calls, [])

    def test_dispatches_both_for_briefing(self):
        calendar = FakeCalendarClaw()
        tasks = FakeTasksClaw()
        claw = N4OSClaw(calendar_claw=calendar, tasks_claw=tasks)

        with redirect_stdout(StringIO()):
            decision = claw.handle_request(
                "Give me my day briefing",
                reference_time=REFERENCE_TIME,
            )

        self.assertEqual(decision["route"], "both")
        self.assertEqual(calendar.calls[0][0], "briefing")
        self.assertEqual(tasks.calls[0][0], "recommend")

    def test_missing_google_tasks_dependency_prints_actionable_message(self):
        module = SimpleNamespace(
            extract_intent=lambda request, now=None: {
                "intent": "create_task",
            },
            FamilyTasksClaw=MissingGoogleTasksClaw,
        )
        claw = N4OSClaw()

        output = StringIO()
        with patch("claw._tasks_module", return_value=module):
            with redirect_stdout(output):
                decision = claw.handle_request(
                    "Add task clean the car",
                    reference_time=REFERENCE_TIME,
                )

        self.assertEqual(decision["route"], "tasks")
        self.assertIn("Family Tasks needs the Google Python client libraries", output.getvalue())
        self.assertIsNone(claw.tasks_claw)


if __name__ == "__main__":
    unittest.main()

import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
import json
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from claw import N4OSClaw
from intent_router import route_request


REFERENCE_TIME = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))


def _metadata_blob(metadata):
    return "N4OS_METADATA:\n" + json.dumps(metadata)


def _event(summary, start, end, description=None, location=None):
    return {
        "id": summary.lower().replace(" ", "-"),
        "summary": summary,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
        "description": description,
        "location": location,
    }


def _task(title, metadata, due=None):
    return {
        "id": title.lower().replace(" ", "-"),
        "title": title,
        "notes": _metadata_blob(metadata),
        "due": due,
        "status": "needsAction",
    }


class FakeCalendarTools:
    def __init__(self, events=None):
        self.events = events or []
        self.list_calls = []

    def list_calendar_events(self, time_min=None, time_max=None, max_results=10):
        self.list_calls.append(
            {
                "time_min": time_min,
                "time_max": time_max,
                "max_results": max_results,
            }
        )
        return {
            "status": "ok",
            "message": "Calendar events returned.",
            "data": {"events": self.events[:max_results]},
        }


class FakeTaskTools:
    def __init__(self, tasks=None, recommended=None):
        self.tasks = tasks or []
        self.recommended = recommended or []
        self.list_calls = []
        self.recommend_calls = []

    def list_tasks(self, task_list_id="@default", show_completed=False):
        self.list_calls.append(
            {
                "task_list_id": task_list_id,
                "show_completed": show_completed,
            }
        )
        return {
            "status": "ok",
            "message": "Tasks returned.",
            "data": {"tasks": self.tasks},
        }

    def recommend_tasks(self, filters=None, task_list_id="@default"):
        self.recommend_calls.append(
            {
                "filters": filters or {},
                "task_list_id": task_list_id,
            }
        )
        return {
            "status": "ok",
            "message": "Task recommendations returned.",
            "data": {"tasks": self.recommended, "filters": filters or {}},
        }


class FakeCalendarClaw:
    def __init__(self, events=None):
        self.calls = []
        self.tools = FakeCalendarTools(events)

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
    def __init__(self, tasks=None, recommended=None):
        self.calls = []
        self.tools = FakeTaskTools(tasks, recommended)

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


class FakeHomeBoardClaw:
    def __init__(self):
        self.calls = []

    def add_item_from_request(self, request, reference_time=None):
        self.calls.append(("add", request, reference_time))

    def list_items_from_request(self, request, reference_time=None):
        self.calls.append(("list", request, reference_time))

    def mark_done_from_request(self, request):
        self.calls.append(("done", request, None))


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

    def test_routes_go_to_time_request_to_calendar(self):
        decision = route_request("Go to aaja at 3 PM today", now=REFERENCE_TIME)

        self.assertEqual(decision["route"], "calendar")
        self.assertGreaterEqual(decision["confidence"], 0.6)

    def test_routes_task_creation(self):
        decision = route_request(
            "Add task change water filter this weekend",
            now=REFERENCE_TIME,
        )

        self.assertEqual(decision["route"], "tasks")
        self.assertGreaterEqual(decision["confidence"], 0.6)
        self.assertIn("family-tasks", decision["intent_summary"])

    def test_routes_home_board_notice(self):
        decision = route_request(
            "Before Nysha leaves today remind her to take the form",
            now=REFERENCE_TIME,
        )

        self.assertEqual(decision["route"], "home_board")
        self.assertGreaterEqual(decision["confidence"], 0.6)

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

    def test_routes_focus_and_today_shape_to_both(self):
        focus = route_request("What should I focus on today?", now=REFERENCE_TIME)
        today = route_request("What does today look like?", now=REFERENCE_TIME)

        self.assertEqual(focus["route"], "both")
        self.assertEqual(today["route"], "both")

    def test_low_confidence_asks_for_clarification(self):
        calendar = FakeCalendarClaw()
        tasks = FakeTasksClaw()
        claw = N4OSClaw(calendar_claw=calendar, tasks_claw=tasks)

        output = StringIO()
        with redirect_stdout(output):
            decision = claw.handle_request("hmm maybe later", reference_time=REFERENCE_TIME)

        self.assertEqual(decision["route"], "unknown")
        self.assertIn("Should I use Calendar, Tasks, Home Board, or both?", output.getvalue())
        self.assertEqual(calendar.calls, [])
        self.assertEqual(tasks.calls, [])

    def test_route_clarification_dispatches_original_request(self):
        calendar = FakeCalendarClaw()
        tasks = FakeTasksClaw()
        claw = N4OSClaw(calendar_claw=calendar, tasks_claw=tasks)

        with redirect_stdout(StringIO()):
            first_decision = claw.handle_request(
                "hmm maybe later",
                reference_time=REFERENCE_TIME,
            )
            clarified_decision = claw.handle_request("Calendar")

        self.assertEqual(first_decision["route"], "unknown")
        self.assertEqual(clarified_decision["route"], "calendar")
        self.assertEqual(calendar.calls, [("create", "hmm maybe later", REFERENCE_TIME)])
        self.assertEqual(tasks.calls, [])

    def test_home_board_notice_dispatches_to_home_board_claw(self):
        calendar = FakeCalendarClaw()
        tasks = FakeTasksClaw()
        home_board = FakeHomeBoardClaw()
        claw = N4OSClaw(
            calendar_claw=calendar,
            tasks_claw=tasks,
            home_board_claw=home_board,
        )

        with redirect_stdout(StringIO()):
            decision = claw.handle_request(
                "Helper should put the food in the fridge today",
                reference_time=REFERENCE_TIME,
            )

        self.assertEqual(decision["route"], "home_board")
        self.assertEqual(
            home_board.calls,
            [("add", "Helper should put the food in the fridge today", REFERENCE_TIME)],
        )
        self.assertEqual(calendar.calls, [])
        self.assertEqual(tasks.calls, [])

    def test_bulk_home_board_notice_dispatches_to_home_board_claw(self):
        calendar = FakeCalendarClaw()
        tasks = FakeTasksClaw()
        home_board = FakeHomeBoardClaw()
        claw = N4OSClaw(
            calendar_claw=calendar,
            tasks_claw=tasks,
            home_board_claw=home_board,
        )

        request = "Today at home: Nysha take journal, Helper put food in fridge"
        with redirect_stdout(StringIO()):
            decision = claw.handle_request(request, reference_time=REFERENCE_TIME)

        self.assertEqual(decision["route"], "home_board")
        self.assertEqual(home_board.calls, [("add", request, REFERENCE_TIME)])

    def test_day_briefing_reads_calendar_and_tasks(self):
        calendar = FakeCalendarClaw(
            events=[
                _event(
                    "Dentist",
                    "2026-07-03T10:00:00-07:00",
                    "2026-07-03T11:00:00-07:00",
                    _metadata_blob(
                        {
                            "preparation_needed": True,
                            "preparation_notes": "Bring insurance card",
                        }
                    ),
                ),
                _event(
                    "Standup",
                    "2026-07-03T10:30:00-07:00",
                    "2026-07-03T11:15:00-07:00",
                ),
                _event(
                    "School pickup",
                    "2026-07-03T15:00:00-07:00",
                    "2026-07-03T16:00:00-07:00",
                ),
                _event(
                    "Dinner",
                    "2026-07-03T17:00:00-07:00",
                    "2026-07-03T18:00:00-07:00",
                ),
            ]
        )
        tasks = FakeTasksClaw(
            tasks=[
                _task(
                    "Submit school forms",
                    {
                        "urgency": "high",
                        "duration_minutes": 30,
                        "effort_type": "paperwork",
                    },
                    due="2026-07-03T00:00:00.000Z",
                ),
                _task(
                    "Buy gift",
                    {
                        "urgency": "low",
                        "duration_minutes": 20,
                    },
                    due="2026-07-08T00:00:00.000Z",
                ),
            ],
            recommended=[
                _task(
                    "Call mom",
                    {
                        "duration_minutes": 20,
                        "energy": "low",
                        "effort_type": "communication",
                    },
                )
            ],
        )
        claw = N4OSClaw(calendar_claw=calendar, tasks_claw=tasks)

        output = StringIO()
        with redirect_stdout(output):
            decision = claw.handle_request("Give me my day briefing", reference_time=REFERENCE_TIME)

        self.assertEqual(decision["route"], "both")
        briefing = output.getvalue()
        self.assertIn("1. Today's calendar commitments", briefing)
        self.assertIn("Dentist", briefing)
        self.assertIn("2. Prep-needed calendar items", briefing)
        self.assertIn("Bring insurance card", briefing)
        self.assertIn("3. Open urgent/due tasks", briefing)
        self.assertIn("Submit school forms", briefing)
        self.assertIn("4. Suggested focus tasks based on available gaps", briefing)
        self.assertIn("Call mom", briefing)
        self.assertIn("5. Warnings for conflicts or overloaded days", briefing)
        self.assertIn("Conflict: Dentist overlaps Standup", briefing)
        self.assertIn("Overloaded day: 4 calendar commitments", briefing)
        self.assertEqual(calendar.calls, [])
        self.assertEqual(tasks.calls, [])
        self.assertEqual(calendar.tools.list_calls[0]["max_results"], 100)
        self.assertFalse(tasks.tools.list_calls[0]["show_completed"])
        self.assertEqual(tasks.tools.recommend_calls[0]["filters"]["duration_minutes"], 120)

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

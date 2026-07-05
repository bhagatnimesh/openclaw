import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
import json
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from claw import N4OSClaw
from input_normalizer import improve_entered_text
from intent_router import route_request


REFERENCE_TIME = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))


def _fusd_waitlist_request():
    return "\n".join(
        [
            "I want to add a task for Monday at 2 p.m. to call FUSD "
            "to follow up on Nyshas School waiting",
            "list for Chad Bond. This task is for Namesh. "
            "I want AI assistant to find out FUSD number to call",
            "and the key talking points. I really want",
            "Nyshad to meet Chad Bond from overflow",
            "on ASS School to Mission Valley Monteserie.",
        ]
    )


def _dropped_subject_fusd_waitlist_request():
    return "\n".join(
        [
            "want to add a task for Monday at 3 p.m. to call FUSD "
            "to follow up on Nyshas School waiting",
            "list for Chad Bond. This task is for Namesh. "
            "I want Noah to find out FUSD number to call",
            "and the key talking points. I really want",
            "Nyshad to meet Chad Bond from overflow",
            "on ASS School to Mission Valley Monteserie",
        ]
    )


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


class PendingCalendarClaw(FakeCalendarClaw):
    def __init__(self):
        super().__init__()
        self.pending_action = {"intent": "create_event"}
        self.pending_responses = []

    def handle_pending_response(self, request):
        self.pending_responses.append(request)
        print(f"Calendar pending consumed: {request}")
        return True


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

    def run_noah_assistant_help_from_request(self, request, reference_time=None):
        self.calls.append(("run_assistant_help", request, reference_time))


class FakeHomeBoardClaw:
    def __init__(self):
        self.calls = []

    def add_item_from_request(self, request, reference_time=None):
        self.calls.append(("add", request, reference_time))

    def list_items_from_request(self, request, reference_time=None):
        self.calls.append(("list", request, reference_time))

    def mark_done_from_request(self, request):
        self.calls.append(("done", request, None))


class FakeDecisionsClaw:
    def __init__(self):
        self.calls = []

    def handle_request(self, request, reference_time=None):
        self.calls.append(("handle", request, reference_time))


class MissingGoogleTasksClaw:
    @classmethod
    def default(cls):
        raise ModuleNotFoundError("No module named 'google'", name="google")


class IntentRouterTest(unittest.TestCase):
    def test_input_improvement_keeps_plain_to_do_phrase(self):
        self.assertEqual(
            improve_entered_text("what do I need to do today"),
            "what do I need to do today",
        )

    def test_input_improvement_keeps_tax_return_phrase(self):
        self.assertEqual(
            improve_entered_text("add tax return reminder tomorrow"),
            "add tax return reminder tomorrow",
        )

    def test_input_improvement_preserves_noah_help_marker(self):
        self.assertEqual(
            improve_entered_text("Noah, help look up the FUSD phone number"),
            "Noah, help look up the FUSD phone number",
        )

    def test_input_improvement_repairs_decision_brief_typo(self):
        self.assertEqual(
            improve_entered_text("give me decision bried"),
            "give me decision brief",
        )

    def test_input_improvement_repairs_decision_voice_note_terms(self):
        self.assertEqual(
            improve_entered_text("challenges she will be jet lagged"),
            "challenges she will be jetlagged",
        )

    def test_input_improvement_repairs_family_task_dictation(self):
        self.assertEqual(
            improve_entered_text(_fusd_waitlist_request()),
            "\n".join(
                [
                    "I want to add a task for Monday at 2 p.m. to call FUSD "
                    "to follow up on Nysha's school waiting",
                    "list for Chad Bond. This task is for Namesh. "
                    "I want AI assistant to find out FUSD phone number to call",
                    "and the key talking points. I really want",
                    "Nysha to meet Chad Bond from overflow",
                    "on ASS School to Mission Valley Montessori.",
                ]
            ),
        )

    def test_routes_calendar_creation(self):
        decision = route_request("Add dentist tomorrow at 3pm", now=REFERENCE_TIME)

        self.assertEqual(decision["route"], "calendar")
        self.assertGreaterEqual(decision["confidence"], 0.6)
        self.assertIn("family-calendar", decision["intent_summary"])

    def test_routes_go_to_time_request_to_calendar(self):
        decision = route_request("Go to aaja at 3 PM today", now=REFERENCE_TIME)

        self.assertEqual(decision["route"], "calendar")
        self.assertGreaterEqual(decision["confidence"], 0.6)

    def test_routes_time_bound_call_to_calendar(self):
        decision = route_request("Call Rahul tomorrow at 5pm", now=REFERENCE_TIME)

        self.assertEqual(decision["route"], "calendar")
        self.assertIn("create_event", decision["intent_summary"])
        self.assertGreaterEqual(decision["confidence"], 0.6)

    def test_routes_task_creation(self):
        decision = route_request(
            "Add task change water filter this weekend",
            now=REFERENCE_TIME,
        )

        self.assertEqual(decision["route"], "tasks")
        self.assertGreaterEqual(decision["confidence"], 0.6)
        self.assertIn("family-tasks", decision["intent_summary"])

    def test_routes_ai_assistant_marked_task_creation(self):
        request = "\n".join(
            [
                "I want AI assistant",
                "call FUSD for following up on Nysha's waitlist status for Chadbourne",
                "Help: look up the FUSD phone number and draft quick talking points",
            ]
        )

        decision = route_request(request, now=REFERENCE_TIME)

        self.assertEqual(decision["route"], "tasks")
        self.assertGreaterEqual(decision["confidence"], 0.6)

    def test_routes_noah_marked_task_creation(self):
        request = "\n".join(
            [
                "Ask Noah to help",
                "call FUSD for following up on Nysha's waitlist status for Chadbourne",
                "Help: look up the FUSD phone number and draft quick talking points",
            ]
        )

        decision = route_request(request, now=REFERENCE_TIME)

        self.assertEqual(decision["route"], "tasks")
        self.assertGreaterEqual(decision["confidence"], 0.6)

    def test_routes_inline_noah_assistant_task_creation(self):
        decision = route_request(
            "I want Noah to call FUSD for following up on Nysha waitlist and find the phone number",
            now=REFERENCE_TIME,
        )

        self.assertEqual(decision["route"], "tasks")
        self.assertIn("create_task", decision["intent_summary"])
        self.assertGreaterEqual(decision["confidence"], 0.6)

    def test_routes_direct_noah_research_request_to_tasks(self):
        decision = route_request(
            "Ask Noah to help look up the FUSD phone number",
            now=REFERENCE_TIME,
        )

        self.assertEqual(decision["route"], "tasks")
        self.assertIn("create_task", decision["intent_summary"])
        self.assertGreaterEqual(decision["confidence"], 0.6)

    def test_routes_noah_assistant_run_to_tasks(self):
        decision = route_request("Run Noah assistant help", now=REFERENCE_TIME)

        self.assertEqual(decision["route"], "tasks")
        self.assertIn("run_assistant_help", decision["intent_summary"])
        self.assertGreaterEqual(decision["confidence"], 0.6)

    def test_routes_natural_packing_list_to_tasks(self):
        decision = route_request(
            "to pack beach matt, sunc screen, fruits, water for the trip tomorrow",
            now=REFERENCE_TIME,
        )

        self.assertEqual(decision["route"], "tasks")
        self.assertGreaterEqual(decision["confidence"], 0.6)

    def test_routes_home_board_notice(self):
        decision = route_request(
            "Before Nysha leaves today remind her to take the form",
            now=REFERENCE_TIME,
        )

        self.assertEqual(decision["route"], "home_board")
        self.assertGreaterEqual(decision["confidence"], 0.6)

    def test_routes_family_decision_capture(self):
        decision = route_request(
            "Track decision about summer camp plan owner mom by next Monday",
            now=REFERENCE_TIME,
        )

        self.assertEqual(decision["route"], "decisions")
        self.assertGreaterEqual(decision["confidence"], 0.6)
        self.assertIn("family-decisions", decision["intent_summary"])

    def test_routes_captured_decision_text_to_decisions_before_day_planning(self):
        decision = route_request(
            "Captured decision: Summer camp plan for Nysha for the last week. "
            "Options are stay at home, go to ICC, challenges she will be jetlagged",
            now=REFERENCE_TIME,
        )

        self.assertEqual(decision["route"], "decisions")
        self.assertGreaterEqual(decision["confidence"], 0.6)

    def test_routes_noah_decision_research_to_decisions_before_task_assistant(self):
        decision = route_request(
            "Captured decision: school choice. I want Noah to compare commute and waitlist",
            now=REFERENCE_TIME,
        )

        self.assertEqual(decision["route"], "decisions")
        self.assertGreaterEqual(decision["confidence"], 0.6)

    def test_routes_decision_brief_typo_to_decision_brief(self):
        decision = route_request(
            improve_entered_text("give me decision bried"),
            now=REFERENCE_TIME,
        )

        self.assertEqual(decision["route"], "decisions")
        self.assertIn("decision_brief", decision["intent_summary"])

    def test_routes_decision_follow_up_options_to_decisions(self):
        decision = route_request(
            "options are stay at home, go to ICC",
            now=REFERENCE_TIME,
        )

        self.assertEqual(decision["route"], "decisions")
        self.assertIn("add_option", decision["intent_summary"])

    def test_routes_decision_follow_up_note_to_decisions(self):
        decision = route_request(
            "Added note to Call FUSD to get Nysha waiting list number",
            now=REFERENCE_TIME,
        )

        self.assertEqual(decision["route"], "decisions")
        self.assertIn("add_evidence", decision["intent_summary"])

    def test_routes_family_decision_question(self):
        decision = route_request(
            "Are we going to Rahul's birthday party?",
            now=REFERENCE_TIME,
        )

        self.assertEqual(decision["route"], "decisions")
        self.assertGreaterEqual(decision["confidence"], 0.6)

    def test_routes_calendar_query(self):
        decision = route_request("What do I have tomorrow?", now=REFERENCE_TIME)

        self.assertEqual(decision["route"], "calendar")
        self.assertGreaterEqual(decision["confidence"], 0.6)

    def test_routes_task_recommendation(self):
        decision = route_request("What can I do while driving?", now=REFERENCE_TIME)

        self.assertEqual(decision["route"], "tasks")
        self.assertGreaterEqual(decision["confidence"], 0.6)

    def test_routes_task_list_for_tomorrow_to_tasks(self):
        decision = route_request("Give me list of tasks tomorrow", now=REFERENCE_TIME)

        self.assertEqual(decision["route"], "tasks")
        self.assertGreaterEqual(decision["confidence"], 0.6)

    def test_routes_voice_transcription_and_a_task_to_tasks(self):
        decision = route_request(
            "and a task for tomorrow to order the lock",
            now=REFERENCE_TIME,
        )

        self.assertEqual(decision["route"], "tasks")
        self.assertIn("create_task", decision["intent_summary"])
        self.assertGreaterEqual(decision["confidence"], 0.6)

    def test_dispatches_task_list_for_tomorrow_only_to_tasks(self):
        calendar = FakeCalendarClaw()
        tasks = FakeTasksClaw(
            recommended=[
                _task(
                    "Return water filter",
                    {},
                    due="2026-07-04T00:00:00.000Z",
                )
            ],
        )
        claw = N4OSClaw(calendar_claw=calendar, tasks_claw=tasks)

        with redirect_stdout(StringIO()):
            decision = claw.handle_request(
                "Give me list of tasks tomorrow",
                reference_time=REFERENCE_TIME,
            )

        self.assertEqual(decision["route"], "tasks")
        self.assertEqual(calendar.calls, [])
        self.assertEqual(
            tasks.calls,
            [("recommend", "Give me list of tasks tomorrow", REFERENCE_TIME)],
        )

    def test_dispatches_ai_assistant_marked_task_to_tasks(self):
        calendar = FakeCalendarClaw()
        tasks = FakeTasksClaw()
        claw = N4OSClaw(calendar_claw=calendar, tasks_claw=tasks)
        request = "\n".join(
            [
                "I want AI assistant",
                "call FUSD for following up on Nysha's waitlist status for Chadbourne",
                "Help: look up the FUSD phone number and draft quick talking points",
            ]
        )

        with redirect_stdout(StringIO()):
            decision = claw.handle_request(request, reference_time=REFERENCE_TIME)

        self.assertEqual(decision["route"], "tasks")
        self.assertEqual(calendar.calls, [])
        self.assertEqual(tasks.calls, [("create", request, REFERENCE_TIME)])

    def test_dispatches_inline_noah_assistant_task_to_tasks(self):
        calendar = FakeCalendarClaw()
        tasks = FakeTasksClaw()
        claw = N4OSClaw(calendar_claw=calendar, tasks_claw=tasks)
        request = (
            "I want Noah to call FUSD for following up on Nysha waitlist "
            "and find the phone number"
        )

        with redirect_stdout(StringIO()):
            decision = claw.handle_request(request, reference_time=REFERENCE_TIME)

        self.assertEqual(decision["route"], "tasks")
        self.assertEqual(calendar.calls, [])
        self.assertEqual(tasks.calls, [("create", request, REFERENCE_TIME)])

    def test_dispatches_direct_noah_research_request_to_tasks(self):
        calendar = FakeCalendarClaw()
        tasks = FakeTasksClaw()
        claw = N4OSClaw(calendar_claw=calendar, tasks_claw=tasks)
        request = "Ask Noah to help look up the FUSD phone number"

        with redirect_stdout(StringIO()):
            decision = claw.handle_request(request, reference_time=REFERENCE_TIME)

        self.assertEqual(decision["route"], "tasks")
        self.assertEqual(calendar.calls, [])
        self.assertEqual(tasks.calls, [("create", request, REFERENCE_TIME)])

    def test_dispatches_noah_help_wake_phrase_to_tasks(self):
        calendar = FakeCalendarClaw()
        tasks = FakeTasksClaw()
        claw = N4OSClaw(calendar_claw=calendar, tasks_claw=tasks)
        request = "Noah, help look up the FUSD phone number"

        with redirect_stdout(StringIO()):
            decision = claw.handle_request(request, reference_time=REFERENCE_TIME)

        self.assertEqual(decision["route"], "tasks")
        self.assertEqual(calendar.calls, [])
        self.assertEqual(tasks.calls, [("create", request, REFERENCE_TIME)])

    def test_dispatches_noah_assistant_run_to_tasks(self):
        calendar = FakeCalendarClaw()
        tasks = FakeTasksClaw()
        claw = N4OSClaw(calendar_claw=calendar, tasks_claw=tasks)
        request = "Run Noah assistant help"

        with redirect_stdout(StringIO()):
            decision = claw.handle_request(request, reference_time=REFERENCE_TIME)

        self.assertEqual(decision["route"], "tasks")
        self.assertEqual(calendar.calls, [])
        self.assertEqual(tasks.calls, [("run_assistant_help", request, REFERENCE_TIME)])

    def test_dispatches_polite_timed_task_request_to_task_creation(self):
        calendar = FakeCalendarClaw()
        tasks = FakeTasksClaw()
        claw = N4OSClaw(calendar_claw=calendar, tasks_claw=tasks)
        request = _fusd_waitlist_request()

        with redirect_stdout(StringIO()):
            decision = claw.handle_request(request, reference_time=REFERENCE_TIME)

        self.assertEqual(decision["route"], "tasks")
        self.assertIn("create_task", decision["intent_summary"])
        self.assertEqual(calendar.calls, [])
        self.assertEqual(
            tasks.calls,
            [("create", improve_entered_text(request), REFERENCE_TIME)],
        )

    def test_dispatches_dropped_subject_task_request_to_task_creation(self):
        calendar = FakeCalendarClaw()
        tasks = FakeTasksClaw()
        claw = N4OSClaw(calendar_claw=calendar, tasks_claw=tasks)
        request = _dropped_subject_fusd_waitlist_request()

        with redirect_stdout(StringIO()):
            decision = claw.handle_request(request, reference_time=REFERENCE_TIME)

        self.assertEqual(decision["route"], "tasks")
        self.assertIn("create_task", decision["intent_summary"])
        self.assertEqual(calendar.calls, [])
        self.assertEqual(
            tasks.calls,
            [("create", improve_entered_text(request), REFERENCE_TIME)],
        )

    def test_dispatches_dated_noah_task_to_tasks_only(self):
        calendar = FakeCalendarClaw()
        tasks = FakeTasksClaw()
        claw = N4OSClaw(calendar_claw=calendar, tasks_claw=tasks)
        request = (
            "Call FUSD to get Nysha waiting list number on Monday at 2 PM. "
            "I want Noah to help me get FUSD phone number for the call and "
            "some talking points to get Nysha to Chadbourne in home school asap"
        )

        with redirect_stdout(StringIO()):
            decision = claw.handle_request(request, reference_time=REFERENCE_TIME)

        self.assertEqual(decision["route"], "tasks")
        self.assertIn("create_task", decision["intent_summary"])
        self.assertEqual(calendar.calls, [])
        self.assertEqual(tasks.calls, [("create", request, REFERENCE_TIME)])

    def test_dispatches_voice_transcription_and_a_task_to_task_creation(self):
        calendar = FakeCalendarClaw()
        tasks = FakeTasksClaw()
        claw = N4OSClaw(calendar_claw=calendar, tasks_claw=tasks)
        request = "and a task for tomorrow to order the lock"

        with redirect_stdout(StringIO()):
            decision = claw.handle_request(request, reference_time=REFERENCE_TIME)

        self.assertEqual(decision["route"], "tasks")
        self.assertIn("create_task", decision["intent_summary"])
        self.assertEqual(calendar.calls, [])
        self.assertEqual(tasks.calls, [("create", request, REFERENCE_TIME)])

    def test_dispatch_improves_voice_typed_task_text(self):
        calendar = FakeCalendarClaw()
        tasks = FakeTasksClaw()
        claw = N4OSClaw(calendar_claw=calendar, tasks_claw=tasks)

        with redirect_stdout(StringIO()):
            decision = claw.handle_request(
                "Can you add tax buy milk",
                reference_time=REFERENCE_TIME,
            )

        self.assertEqual(decision["route"], "tasks")
        self.assertEqual(calendar.calls, [])
        self.assertEqual(tasks.calls, [("create", "add task buy milk", REFERENCE_TIME)])

    def test_dispatch_improves_home_board_mishearing(self):
        calendar = FakeCalendarClaw()
        tasks = FakeTasksClaw()
        home_board = FakeHomeBoardClaw()
        claw = N4OSClaw(
            calendar_claw=calendar,
            tasks_claw=tasks,
            home_board_claw=home_board,
        )

        with redirect_stdout(StringIO()):
            decision = claw.handle_request("show home bored", reference_time=REFERENCE_TIME)

        self.assertEqual(decision["route"], "home_board")
        self.assertEqual(home_board.calls, [("list", "show home board", REFERENCE_TIME)])
        self.assertEqual(calendar.calls, [])
        self.assertEqual(tasks.calls, [])

    def test_dispatches_family_decision_to_decisions_claw(self):
        calendar = FakeCalendarClaw()
        tasks = FakeTasksClaw()
        home_board = FakeHomeBoardClaw()
        decisions = FakeDecisionsClaw()
        claw = N4OSClaw(
            calendar_claw=calendar,
            tasks_claw=tasks,
            home_board_claw=home_board,
            decisions_claw=decisions,
        )
        request = "Track decision about summer camp plan owner mom by next Monday"

        with redirect_stdout(StringIO()):
            decision = claw.handle_request(request, reference_time=REFERENCE_TIME)

        self.assertEqual(decision["route"], "decisions")
        self.assertEqual(decisions.calls, [("handle", request, REFERENCE_TIME)])
        self.assertEqual(calendar.calls, [])
        self.assertEqual(tasks.calls, [])
        self.assertEqual(home_board.calls, [])

    def test_dispatches_time_bound_call_to_calendar(self):
        calendar = FakeCalendarClaw()
        tasks = FakeTasksClaw()
        claw = N4OSClaw(calendar_claw=calendar, tasks_claw=tasks)
        request = "Call Rahul tomorrow at 5pm"

        with redirect_stdout(StringIO()):
            decision = claw.handle_request(request, reference_time=REFERENCE_TIME)

        self.assertEqual(decision["route"], "calendar")
        self.assertEqual(calendar.calls, [("create", request, REFERENCE_TIME)])
        self.assertEqual(tasks.calls, [])

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
        self.assertIn(
            "Should I use Calendar, Tasks, Home Board, Decisions, or Calendar + Tasks?",
            output.getvalue(),
        )
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

    def test_new_task_bypasses_pending_calendar_followup(self):
        calendar = PendingCalendarClaw()
        tasks = FakeTasksClaw()
        claw = N4OSClaw(calendar_claw=calendar, tasks_claw=tasks)
        request = "to pack beach matt, sunc screen, fruits, water for the trip tomorrow"

        with redirect_stdout(StringIO()):
            decision = claw.handle_request(request, reference_time=REFERENCE_TIME)

        self.assertEqual(decision["route"], "tasks")
        self.assertEqual(calendar.pending_responses, [])
        self.assertIsNone(calendar.pending_action)
        self.assertEqual(tasks.calls, [("create", request, REFERENCE_TIME)])

    def test_bare_home_board_command_lists_without_clarification_loop(self):
        calendar = FakeCalendarClaw()
        tasks = FakeTasksClaw()
        home_board = FakeHomeBoardClaw()
        claw = N4OSClaw(
            calendar_claw=calendar,
            tasks_claw=tasks,
            home_board_claw=home_board,
        )

        with redirect_stdout(StringIO()):
            decision = claw.handle_request("Home board", reference_time=REFERENCE_TIME)

        self.assertEqual(decision["route"], "home_board")
        self.assertEqual(home_board.calls, [("list", "Home board", REFERENCE_TIME)])
        self.assertEqual(calendar.calls, [])
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

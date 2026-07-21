import unittest
from datetime import datetime
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import dashboard_sources
from dashboard_data import (
    build_dashboard_data,
    complete_dashboard_task,
)
from dashboard_server import DashboardRequestHandler
from dashboard_sources import (
    DashboardSources,
    fallback_event_metadata,
    fallback_recommend_task_matches,
    fallback_task_metadata,
)


def event(
    title,
    start,
    end,
    metadata=None,
    description="",
    location="",
    event_id=None,
):
    notes = description
    if metadata is not None:
        import json

        notes = notes + "\n\nN4OS_METADATA:\n" + json.dumps(metadata)
    return {
        "id": event_id or title.lower().replace(" ", "-"),
        "summary": title,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
        "description": notes,
        "location": location,
    }


def task(title, due=None, metadata=None, notes="", task_id=None):
    if metadata is not None:
        import json

        notes = notes + "\n\nN4OS_METADATA:\n" + json.dumps(metadata)
    result = {
        "id": task_id or title.lower().replace(" ", "-"),
        "title": title,
        "notes": notes,
        "status": "needsAction",
    }
    if due is not None:
        result["due"] = due
    return result


class FakeCalendarTools:
    def __init__(self, events):
        self.events = events

    def list_calendar_events(self, time_min=None, time_max=None, max_results=100):
        return {
            "status": "ok",
            "message": "ok",
            "data": {"events": self.events[:max_results]},
        }


class FakeTaskTools:
    def __init__(self, tasks):
        self.tasks = tasks
        self.completed = []

    def list_tasks(self, show_completed=False):
        return {
            "status": "ok",
            "message": "ok",
            "data": {"tasks": self.tasks},
        }

    def complete_task(self, task_id=None, task_list_id="@default", confirmed=False):
        if not confirmed:
            return {
                "status": "needs_confirmation",
                "message": f"Confirm before I complete task {task_id}.",
                "data": {"task_id": task_id, "action": "complete"},
            }
        self.completed.append((task_list_id, task_id))
        for task in self.tasks:
            if task.get("id") == task_id:
                task["status"] = "completed"
                return {
                    "status": "ok",
                    "message": "Task completed.",
                    "data": {"task": task},
                }
        return {
            "status": "error",
            "message": "task not found",
            "data": {"task_id": task_id},
        }


class FailingCalendarTools:
    def list_calendar_events(self, time_min=None, time_max=None, max_results=100):
        raise ConnectionResetError("calendar reset")


class FailingTaskTools:
    def list_tasks(self, show_completed=False):
        raise ConnectionResetError("tasks reset")


class StartupUnavailableCalendarTools(FailingCalendarTools):
    unavailable = True


class FakeHomeBoardTools:
    def __init__(self, items=None):
        self.items = items or []
        self.list_calls = []

    def list_items(
        self,
        date=None,
        status="pending",
        include_expired=False,
        now=None,
    ):
        self.list_calls.append(
            {
                "date": date,
                "status": status,
                "include_expired": include_expired,
                "now": now,
            }
        )
        return {
            "status": "ok",
            "message": "Home Board items returned.",
            "data": {"items": self.items},
        }


class FakeDecisionTools:
    def __init__(self, decisions=None):
        self.decisions = decisions or []

    def list_decisions(self, status=None, include_decided=False):
        return {
            "status": "ok",
            "message": "Family decisions returned.",
            "data": {"decisions": self.decisions},
        }


class FakeReadingGardenTools:
    def __init__(self, summary=None):
        self.summary = summary or {
            "title": "Nysha’s Reading Garden: I Read It Myself",
            "child": "Nysha",
            "today": {"read": False, "label": "Not yet today"},
            "current_book": "unknown book",
            "week": {"reading_moments": 0, "pages": 0, "minutes": 0},
            "finished": {"count": 0, "recent_books": []},
            "favorite_reaction": "",
            "recent_photos": [],
            "garden": {"sprouts": 0, "leaves": 0, "flowers": 0, "butterflies": 0},
            "recent_events": [],
            "library_visit": {
                "has_visit": False,
                "last_visit_date": "",
                "days_since_visit": None,
                "state": "empty",
                "label": "Paste a library checkout email to start your library bag.",
                "due_date": "",
            },
            "current_bag": {"count": 0, "titles": [], "due_date": ""},
        }

    def status(self, now=None):
        return {
            "status": "ok",
            "message": "Reading Garden returned.",
            "data": {"summary": self.summary},
        }


def sources(events=None, tasks=None, home_board_items=None, decisions=None, reading_garden=None):
    return DashboardSources(
        calendar_tools=FakeCalendarTools(events or []),
        task_tools=FakeTaskTools(tasks or []),
        read_event_metadata=fallback_event_metadata,
        read_task_metadata=fallback_task_metadata,
        recommend_task_matches=fallback_recommend_task_matches,
        home_board_tools=FakeHomeBoardTools(home_board_items),
        decision_tools=FakeDecisionTools(decisions),
        reading_garden_tools=FakeReadingGardenTools(reading_garden),
    )


class DashboardDataTest(unittest.TestCase):
    def test_best_next_action_prefers_prep_needed_upcoming_event(self):
        data = build_dashboard_data(
            sources(
                events=[
                    event(
                        "Passport appointment",
                        "2026-07-03T10:00:00-07:00",
                        "2026-07-03T10:30:00-07:00",
                        metadata={
                            "owner": "unknown",
                            "person": "nysha",
                            "category": "paperwork",
                            "preparation_needed": True,
                            "preparation_notes": "Bring birth certificate and photos",
                        },
                    ),
                ],
                tasks=[
                    task(
                        "Pay utility bill",
                        due="2026-07-04T00:00:00.000Z",
                        metadata={
                            "owner": "dad",
                            "duration_minutes": 10,
                            "context": ["computer"],
                            "energy": "low",
                            "effort_type": "admin",
                        },
                    ),
                ],
            ),
            now=datetime.fromisoformat("2026-07-03T09:20:00-07:00"),
        )

        self.assertEqual(
            data["best_next_action"]["title"],
            "Bring birth certificate and photos",
        )
        self.assertEqual(data["summary"]["prep_needed_count"], 1)
        self.assertEqual(data["summary"]["unassigned_count"], 1)

    def test_calendar_private_metadata_feeds_dashboard_without_visible_json(self):
        import json

        data = build_dashboard_data(
            sources(
                events=[
                    {
                        "id": "fox-subscription",
                        "summary": "Cancel Fox 1 subscription",
                        "start": {"dateTime": "2026-07-03T19:00:00-07:00"},
                        "end": {"dateTime": "2026-07-03T20:00:00-07:00"},
                        "description": "",
                        "extendedProperties": {
                            "private": {
                                "n4os_metadata": json.dumps(
                                    {
                                        "owner": "dad",
                                        "person": "family",
                                        "category": "admin",
                                        "preparation_needed": True,
                                        "preparation_notes": "Cancel before renewal",
                                    },
                                ),
                            },
                        },
                    },
                ],
            ),
            now=datetime.fromisoformat("2026-07-03T08:00:00-07:00"),
        )

        event_data = data["calendar"]["today"][0]
        self.assertEqual(event_data["title"], "Cancel Fox 1 subscription")
        self.assertEqual(event_data["notes"], "")
        self.assertEqual(event_data["owner"], "dad")
        self.assertTrue(event_data["preparation_needed"])
        self.assertEqual(event_data["preparation_notes"], "Cancel before renewal")

    def test_recommendations_include_contextual_task_groups(self):
        data = build_dashboard_data(
            sources(
                tasks=[
                    task(
                        "Call school office",
                        due="2026-07-03T00:00:00.000Z",
                        metadata={
                            "duration_minutes": 15,
                            "context": ["phone"],
                            "can_do_while": ["driving"],
                            "energy": "low",
                            "effort_type": "communication",
                            "requires": ["phone"],
                        },
                    ),
                    task(
                        "Upload passport form",
                        due="2026-07-05T00:00:00.000Z",
                        metadata={
                            "duration_minutes": 30,
                            "context": ["computer"],
                            "energy": "medium",
                            "effort_type": "paperwork",
                            "requires": ["computer", "paperwork"],
                        },
                    ),
                    task("Clean garage shelf"),
                ],
            ),
            now=datetime.fromisoformat("2026-07-03T09:00:00-07:00"),
        )

        labels = [group["label"] for group in data["tasks"]["groups"]]
        self.assertIn("Calls while driving", labels)
        self.assertIn("Paperwork tasks", labels)
        recommended_titles = [
            recommendation["task"]["title"]
            for recommendation in data["tasks"]["recommended"]
        ]
        self.assertIn("Call school office", recommended_titles)
        pending_titles = [task["title"] for task in data["tasks"]["pending"]]
        self.assertEqual(
            pending_titles,
            ["Call school office", "Upload passport form", "Clean garage shelf"],
        )

    def test_dashboard_tasks_expose_tags_for_filtering(self):
        data = build_dashboard_data(
            sources(
                tasks=[
                    task(
                        "Buy water filter",
                        metadata={"tags": ["shopping", "#home", "shopping"]},
                    ),
                    task(
                        "File receipt",
                        metadata={"tags": "finance, home"},
                    ),
                    task("Clean garage shelf"),
                ],
            ),
            now=datetime.fromisoformat("2026-07-03T09:00:00-07:00"),
        )

        self.assertEqual(data["tasks"]["tags"], ["finance", "home", "shopping"])
        tasks_by_title = {
            task_data["title"]: task_data["tags"]
            for task_data in data["tasks"]["pending"]
        }
        self.assertEqual(tasks_by_title["Buy water filter"], ["shopping", "home"])
        self.assertEqual(tasks_by_title["File receipt"], ["finance", "home"])
        self.assertEqual(tasks_by_title["Clean garage shelf"], [])

    def test_dashboard_tasks_expose_visible_note_tags(self):
        data = build_dashboard_data(
            sources(
                tasks=[
                    task("Pack some food", notes="Tags: #packing"),
                    task("Add documents", notes="Tags: #paperwork #packing"),
                ],
            ),
            now=datetime.fromisoformat("2026-07-07T09:00:00-07:00"),
        )

        self.assertEqual(data["tasks"]["tags"], ["packing", "paperwork"])
        tasks_by_title = {
            task_data["title"]: task_data["tags"]
            for task_data in data["tasks"]["pending"]
        }
        self.assertEqual(tasks_by_title["Pack some food"], ["packing"])
        self.assertEqual(tasks_by_title["Add documents"], ["paperwork", "packing"])

    def test_planning_view_links_important_event_to_action_item(self):
        data = build_dashboard_data(
            sources(
                events=[
                    event(
                        "Family Trip Japan",
                        "2026-07-10T08:00:00-07:00",
                        "2026-07-10T09:00:00-07:00",
                        metadata={
                            "owner": "both",
                            "person": "family",
                            "category": "travel",
                            "preparation_needed": True,
                            "preparation_notes": "Finish passport packet",
                            "prep_progress": 50,
                        },
                    ),
                ],
                tasks=[
                    task(
                        "Japan passport packet",
                        due="2026-07-06T00:00:00.000Z",
                        metadata={
                            "duration_minutes": 45,
                            "context": ["computer"],
                            "effort_type": "paperwork",
                        },
                    ),
                ],
            ),
            now=datetime.fromisoformat("2026-07-03T09:00:00-07:00"),
        )

        planning = data["planning"]["items"][0]
        self.assertEqual(planning["title"], "Family Trip Japan")
        self.assertEqual(planning["days_until"], 7)
        self.assertEqual(planning["prep_progress"], 50)
        self.assertEqual(planning["action_items"][0]["title"], "Japan passport packet")

    def test_empty_sources_return_graceful_empty_sections(self):
        data = build_dashboard_data(
            sources(),
            now=datetime.fromisoformat("2026-07-03T09:00:00-07:00"),
        )

        self.assertEqual(data["calendar"]["today"], [])
        self.assertEqual(data["tasks"]["open_loops"], [])
        self.assertEqual(data["tasks"]["pending"], [])
        self.assertEqual(data["planning"]["items"], [])
        self.assertEqual(data["best_next_action"]["source"], "empty")

    def test_dashboard_includes_reading_garden_summary(self):
        data = build_dashboard_data(
            sources(
                reading_garden={
                    "title": "Nysha’s Reading Garden: I Read It Myself",
                    "child": "Nysha",
                    "today": {"read": True, "label": "I read myself today"},
                    "current_book": "Mercy Watson",
                    "week": {"reading_moments": 2, "pages": 8, "minutes": 12},
                    "finished": {"count": 1, "recent_books": ["Elephant and Piggie"]},
                    "favorite_reaction": "She liked the funny pig.",
                    "recent_photos": [{"path": "uploads/reading/cover.jpg", "book": "Mercy Watson"}],
                    "garden": {"sprouts": 1, "leaves": 1, "flowers": 1, "butterflies": 1},
                    "recent_events": [],
                    "library_visit": {
                        "has_visit": True,
                        "last_visit_date": "2026-07-01",
                        "days_since_visit": 2,
                        "state": "enjoy",
                        "label": "Enjoy this library bag.",
                        "due_date": "2026-07-22",
                    },
                    "current_bag": {
                        "count": 2,
                        "titles": ["Mercy Watson", "Frog and Toad"],
                        "due_date": "2026-07-22",
                    },
                },
            ),
            now=datetime.fromisoformat("2026-07-03T09:00:00-07:00"),
        )

        garden = data["reading_garden"]
        self.assertEqual(garden["today"]["label"], "I read myself today")
        self.assertEqual(garden["current_book"], "Mercy Watson")
        self.assertEqual(garden["week"]["pages"], 8)
        self.assertEqual(garden["finished"]["recent_books"], ["Elephant and Piggie"])
        self.assertEqual(garden["library_visit"]["label"], "Enjoy this library bag.")
        self.assertEqual(garden["current_bag"]["count"], 2)

    def test_complete_dashboard_task_updates_google_task_source(self):
        task_tools = FakeTaskTools(
            [
                task(
                    "Return library book",
                    due="2026-07-03T00:00:00.000Z",
                    metadata={"owner": "dad"},
                    task_id="task-1",
                ),
            ],
        )
        dashboard_sources = DashboardSources(
            calendar_tools=FakeCalendarTools([]),
            task_tools=task_tools,
            read_event_metadata=fallback_event_metadata,
            read_task_metadata=fallback_task_metadata,
            recommend_task_matches=fallback_recommend_task_matches,
            home_board_tools=FakeHomeBoardTools(),
            decision_tools=FakeDecisionTools(),
        )

        response = complete_dashboard_task(
            "task-1",
            sources=dashboard_sources,
            now=datetime.fromisoformat("2026-07-03T09:00:00-07:00"),
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(task_tools.completed, [("@default", "task-1")])
        self.assertEqual(response["data"]["task"]["status"], "completed")

    def test_dashboard_includes_today_home_board_items(self):
        data = build_dashboard_data(
            sources(
                home_board_items=[
                    {
                        "id": "home-1",
                        "person_or_group": "Nysha",
                        "message": "Take journal",
                        "date": "2026-07-03",
                        "context": "school",
                        "trigger": None,
                        "status": "pending",
                        "priority": "high",
                        "expires_at": "2026-07-04T00:00:00-07:00",
                    }
                ],
            ),
            now=datetime.fromisoformat("2026-07-03T09:00:00-07:00"),
        )

        self.assertEqual(data["summary"]["home_board_count"], 1)
        self.assertEqual(data["home_board"]["today"][0]["person_or_group"], "Nysha")
        self.assertEqual(data["home_board"]["today"][0]["context_label"], "School")

    def test_dashboard_includes_open_family_decisions(self):
        data = build_dashboard_data(
            sources(
                decisions=[
                    {
                        "id": "05d837f2abcdef",
                        "title": "Summer camp plan",
                        "context": "",
                        "status": "inbox",
                        "owner": "unknown",
                        "urgency": "high",
                        "size": "large",
                        "due": None,
                        "options": [{"text": "Stay home"}, {"text": "Go to ICC"}],
                        "evidence": [{"text": "Jetlagged"}],
                        "next_steps": [],
                        "updated_at": "2026-07-03T09:00:00-07:00",
                    }
                ],
            ),
            now=datetime.fromisoformat("2026-07-03T09:00:00-07:00"),
        )

        self.assertEqual(data["summary"]["open_decision_count"], 1)
        decision = data["decisions"]["open"][0]
        self.assertEqual(decision["short_id"], "05d837f2")
        self.assertEqual(decision["title"], "Summer camp plan")
        self.assertEqual(decision["option_count"], 2)
        self.assertEqual(decision["evidence_count"], 1)
        self.assertEqual(decision["next_step"], "Assign one clear next step")
        self.assertIn("owner", decision["missing_fields"])
        self.assertIn("timeline", decision["missing_fields"])
        self.assertIn("next step", decision["missing_fields"])

    def test_calendar_failure_preserves_task_and_home_board_data(self):
        data = build_dashboard_data(
            DashboardSources(
                calendar_tools=FailingCalendarTools(),
                task_tools=FakeTaskTools(
                    [
                        task(
                            "Pay utility bill",
                            due="2026-07-03T00:00:00.000Z",
                            metadata={
                                "owner": "dad",
                                "duration_minutes": 10,
                                "context": ["computer"],
                                "urgency": "high",
                            },
                        ),
                    ],
                ),
                read_event_metadata=fallback_event_metadata,
                read_task_metadata=fallback_task_metadata,
                recommend_task_matches=fallback_recommend_task_matches,
                home_board_tools=FakeHomeBoardTools(
                    [
                        {
                            "id": "home-1",
                            "person_or_group": "Family",
                            "message": "Pack snacks",
                            "date": "2026-07-03",
                            "context": "kitchen",
                            "trigger": None,
                            "status": "pending",
                            "priority": "medium",
                            "expires_at": None,
                        },
                    ],
                ),
                decision_tools=FakeDecisionTools(),
            ),
            now=datetime.fromisoformat("2026-07-03T09:00:00-07:00"),
        )

        self.assertEqual(data["source_status"], "partial")
        self.assertEqual(data["summary"]["open_loop_count"], 1)
        self.assertEqual(data["summary"]["home_board_count"], 1)
        self.assertEqual(data["tasks"]["open_loops"][0]["title"], "Pay utility bill")
        self.assertEqual(data["home_board"]["today"][0]["message"], "Pack snacks")
        self.assertTrue(
            any("Calendar source unavailable: ConnectionResetError." in warning["detail"] for warning in data["warnings"]),
        )

    def test_single_source_failure_does_not_claim_both_sources_unavailable(self):
        data = build_dashboard_data(
            DashboardSources(
                calendar_tools=FailingCalendarTools(),
                task_tools=FakeTaskTools([]),
                read_event_metadata=fallback_event_metadata,
                read_task_metadata=fallback_task_metadata,
                recommend_task_matches=fallback_recommend_task_matches,
                home_board_tools=FakeHomeBoardTools(),
                decision_tools=FakeDecisionTools(),
            ),
            now=datetime.fromisoformat("2026-07-03T09:00:00-07:00"),
        )

        self.assertEqual(data["source_status"], "partial")
        self.assertNotEqual(data["best_next_action"]["title"], "Reconnect Google sources")
        self.assertTrue(
            any("Calendar source unavailable: ConnectionResetError." in warning["detail"] for warning in data["warnings"]),
        )

    def test_task_failure_preserves_calendar_data(self):
        data = build_dashboard_data(
            DashboardSources(
                calendar_tools=FakeCalendarTools(
                    [
                        event(
                            "Passport appointment",
                            "2026-07-03T11:00:00-07:00",
                            "2026-07-03T12:00:00-07:00",
                            metadata={
                                "owner": "unknown",
                                "person": "family",
                                "category": "paperwork",
                                "preparation_needed": True,
                                "preparation_notes": "Bring documents",
                            },
                        ),
                    ],
                ),
                task_tools=FailingTaskTools(),
                read_event_metadata=fallback_event_metadata,
                read_task_metadata=fallback_task_metadata,
                recommend_task_matches=fallback_recommend_task_matches,
                home_board_tools=FakeHomeBoardTools(),
            ),
            now=datetime.fromisoformat("2026-07-03T09:00:00-07:00"),
        )

        self.assertEqual(data["source_status"], "partial")
        self.assertEqual(data["summary"]["prep_needed_count"], 1)
        self.assertEqual(data["calendar"]["today"][0]["title"], "Passport appointment")
        self.assertEqual(data["best_next_action"]["title"], "Bring documents")
        self.assertTrue(
            any("Tasks source unavailable: ConnectionResetError." in warning["detail"] for warning in data["warnings"]),
        )

    def test_calendar_and_task_failure_surfaces_reconnect_action(self):
        data = build_dashboard_data(
            DashboardSources(
                calendar_tools=FailingCalendarTools(),
                task_tools=FailingTaskTools(),
                read_event_metadata=fallback_event_metadata,
                read_task_metadata=fallback_task_metadata,
                recommend_task_matches=fallback_recommend_task_matches,
                home_board_tools=FakeHomeBoardTools(
                    [
                        {
                            "id": "home-1",
                            "person_or_group": "Family",
                            "message": "Pack snacks",
                            "date": "2026-07-03",
                            "context": "kitchen",
                            "trigger": None,
                            "status": "pending",
                            "priority": "medium",
                            "expires_at": None,
                        },
                    ],
                ),
                decision_tools=FakeDecisionTools(),
            ),
            now=datetime.fromisoformat("2026-07-03T09:00:00-07:00"),
        )

        self.assertEqual(data["source_status"], "partial")
        self.assertEqual(data["best_next_action"]["title"], "Reconnect Google sources")
        self.assertEqual(data["best_next_action"]["source"], "source-warning")
        self.assertEqual(data["summary"]["home_board_count"], 1)

    def test_default_sources_retries_startup_unavailable_sources(self):
        original_builder = dashboard_sources.build_default_sources
        original_default = dashboard_sources._DEFAULT_SOURCES
        calls = []

        def fake_builder():
            calls.append(None)
            if len(calls) == 1:
                return DashboardSources(
                    calendar_tools=StartupUnavailableCalendarTools(),
                    task_tools=FakeTaskTools([]),
                    read_event_metadata=fallback_event_metadata,
                    read_task_metadata=fallback_task_metadata,
                    recommend_task_matches=fallback_recommend_task_matches,
                    home_board_tools=FakeHomeBoardTools(),
                    decision_tools=FakeDecisionTools(),
                )
            return sources()

        try:
            dashboard_sources._DEFAULT_SOURCES = None
            dashboard_sources.build_default_sources = fake_builder
            first = dashboard_sources.default_sources()
            second = dashboard_sources.default_sources()
            third = dashboard_sources.default_sources()
        finally:
            dashboard_sources.build_default_sources = original_builder
            dashboard_sources._DEFAULT_SOURCES = original_default

        self.assertIsInstance(first.calendar_tools, StartupUnavailableCalendarTools)
        self.assertIs(second, third)
        self.assertEqual(len(calls), 2)


class DashboardServerRouteTest(unittest.TestCase):
    def test_dashboard_route_accepts_trailing_slash(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"

        try:
            for path in ("/dashboard", "/dashboard/"):
                with self.subTest(path=path):
                    with urlopen(base_url + path, timeout=5) as response:
                        body = response.read().decode("utf-8")
                    self.assertEqual(response.status, 200)
                    self.assertIn("N4OS Family Chief of Staff", body)
                    self.assertNotIn("{{ACTION_TOKEN}}", body)
                    self.assertIn('href="#decisions"', body)
                    self.assertIn('href="#library"', body)
                    self.assertIn('id="library"', body)
                    self.assertIn('id="library-visit-label"', body)
                    self.assertIn('id="library-bag-list"', body)
                    self.assertIn('data-dashboard-card="library-bag"', body)
                    self.assertIn('data-dashboard-card="reading-photos"', body)
                    self.assertIn('data-dashboard-card="decision-attention"', body)
                    self.assertIn('data-dashboard-card="family-members"', body)
                    self.assertIn('id="decision-items"', body)
                    self.assertIn('id="pending-task-items"', body)
                    self.assertIn('id="task-lanes-heading"', body)
                    self.assertLess(body.index('id="tasks"'), body.index('id="library"'))
                    self.assertLess(body.index('id="library"'), body.index('id="family"'))
                    self.assertIn('id="screen-status"', body)
                    self.assertIn('id="screen-wake-button"', body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_decisions_api_route_returns_open_decisions(self):
        import dashboard_server

        original_get_dashboard_data = dashboard_server.get_dashboard_data

        def fake_dashboard_data():
            return {"decisions": {"open": [{"title": "Summer camp plan"}]}}

        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        try:
            dashboard_server.get_dashboard_data = fake_dashboard_data
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            with urlopen(base_url + "/api/decisions/open", timeout=5) as response:
                body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("Summer camp plan", body)
        finally:
            dashboard_server.get_dashboard_data = original_get_dashboard_data
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_tasks_complete_api_route_completes_task(self):
        import dashboard_server

        original_complete_dashboard_task = dashboard_server.complete_dashboard_task
        calls = []

        def fake_complete_dashboard_task(task_id=None, task_list_id=None):
            calls.append({"task_id": task_id, "task_list_id": task_list_id})
            return {
                "status": "ok",
                "message": "Task completed.",
                "data": {"task": {"id": task_id, "status": "completed"}},
            }

        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        try:
            dashboard_server.complete_dashboard_task = fake_complete_dashboard_task
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            request = Request(
                base_url + "/api/tasks/complete",
                data=b'{"task_id":"task-1"}',
                headers={
                    "Content-Type": "application/json",
                    "X-N4OS-Dashboard-Action-Token": dashboard_server.ACTION_TOKEN,
                },
                method="POST",
            )
            with urlopen(request, timeout=5) as response:
                body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("Task completed", body)
            self.assertEqual(calls, [{"task_id": "task-1", "task_list_id": None}])
        finally:
            dashboard_server.complete_dashboard_task = original_complete_dashboard_task
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_tasks_complete_api_route_accepts_https_same_origin(self):
        import dashboard_server

        original_complete_dashboard_task = dashboard_server.complete_dashboard_task
        calls = []

        def fake_complete_dashboard_task(task_id=None, task_list_id=None):
            calls.append({"task_id": task_id, "task_list_id": task_list_id})
            return {
                "status": "ok",
                "message": "Task completed.",
                "data": {"task": {"id": task_id, "status": "completed"}},
            }

        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        try:
            dashboard_server.complete_dashboard_task = fake_complete_dashboard_task
            thread.start()
            host = f"127.0.0.1:{server.server_port}"
            request = Request(
                f"http://{host}/api/tasks/complete",
                data=b'{"task_id":"task-1"}',
                headers={
                    "Content-Type": "application/json",
                    "Origin": f"https://{host}",
                    "X-N4OS-Dashboard-Action-Token": dashboard_server.ACTION_TOKEN,
                },
                method="POST",
            )
            with urlopen(request, timeout=5) as response:
                body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("Task completed", body)
            self.assertEqual(calls, [{"task_id": "task-1", "task_list_id": None}])
        finally:
            dashboard_server.complete_dashboard_task = original_complete_dashboard_task
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_tasks_complete_api_route_rejects_missing_action_token(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        try:
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            request = Request(
                base_url + "/api/tasks/complete",
                data=b'{"task_id":"task-1"}',
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as context:
                urlopen(request, timeout=5)
            self.assertEqual(context.exception.code, 403)
            context.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_dashboard_static_js_includes_wake_lock_controller(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"

        try:
            with urlopen(base_url + "/static/dashboard/dashboard.js", timeout=5) as response:
                body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn('navigator.wakeLock.request("screen")', body)
            self.assertIn("startVideoKeepAlive", body)
            self.assertIn("canvas.captureStream(1)", body)
            self.assertIn("defaultWakeLockWindow", body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()

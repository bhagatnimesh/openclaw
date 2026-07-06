import unittest
from datetime import datetime
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.request import urlopen

import dashboard_sources
from dashboard_data import (
    build_dashboard_data,
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

    def list_tasks(self, show_completed=False):
        return {
            "status": "ok",
            "message": "ok",
            "data": {"tasks": self.tasks},
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


def sources(events=None, tasks=None, home_board_items=None, decisions=None):
    return DashboardSources(
        calendar_tools=FakeCalendarTools(events or []),
        task_tools=FakeTaskTools(tasks or []),
        read_event_metadata=fallback_event_metadata,
        read_task_metadata=fallback_task_metadata,
        recommend_task_matches=fallback_recommend_task_matches,
        home_board_tools=FakeHomeBoardTools(home_board_items),
        decision_tools=FakeDecisionTools(decisions),
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
        self.assertEqual(data["planning"]["items"], [])
        self.assertEqual(data["best_next_action"]["source"], "empty")

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
                    self.assertIn('href="#decisions"', body)
                    self.assertIn('id="decision-items"', body)
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

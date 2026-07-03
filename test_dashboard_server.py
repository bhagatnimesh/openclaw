import unittest
from datetime import datetime
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.request import urlopen

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


def sources(events=None, tasks=None, home_board_items=None):
    return DashboardSources(
        calendar_tools=FakeCalendarTools(events or []),
        task_tools=FakeTaskTools(tasks or []),
        read_event_metadata=fallback_event_metadata,
        read_task_metadata=fallback_task_metadata,
        recommend_task_matches=fallback_recommend_task_matches,
        home_board_tools=FakeHomeBoardTools(home_board_items),
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
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()

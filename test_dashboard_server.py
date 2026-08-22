import unittest
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import dashboard_sources
from dashboard_data import (
    build_dashboard_data,
    clear_dashboard_shopping_list,
    complete_dashboard_decision,
    complete_dashboard_homework,
    complete_dashboard_shopping_item,
    complete_dashboard_task,
    create_dashboard_backlog_item,
    delete_dashboard_reading_event,
    perform_dashboard_backlog_action,
    update_dashboard_reading_event,
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
    def __init__(self, tasks, task_lists=None):
        self.task_lists = task_lists or [{"id": "@default", "title": "My Tasks"}]
        self.tasks_by_list = {
            entry["id"]: [dict(task, task_list_id=entry["id"]) for task in (tasks or [])]
            for entry in self.task_lists
        }
        if task_lists is not None:
            self.tasks_by_list = {
                entry["id"]: [
                    dict(task, task_list_id=entry["id"])
                    for task in entry.get("tasks", [])
                ]
                for entry in task_lists
            }
        self.tasks = self.tasks_by_list.get("@default", [])
        self.completed = []
        self.created = []

    def list_task_lists(self):
        return {
            "status": "ok",
            "message": "Task lists returned from Google Tasks.",
            "data": {
                "task_lists": [
                    {"id": entry["id"], "title": entry.get("title", entry["id"])}
                    for entry in self.task_lists
                ],
            },
        }

    def list_tasks(self, task_list_id="@default", show_completed=False):
        return {
            "status": "ok",
            "message": "ok",
            "data": {"tasks": self.tasks_by_list.get(task_list_id, [])},
        }

    def complete_task(self, task_id=None, task_list_id="@default", confirmed=False):
        if not confirmed:
            return {
                "status": "needs_confirmation",
                "message": f"Confirm before I complete task {task_id}.",
                "data": {"task_id": task_id, "action": "complete"},
            }
        self.completed.append((task_list_id, task_id))
        for task in self.tasks_by_list.get(task_list_id, []):
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

    def create_task(
        self,
        title=None,
        notes=None,
        due=None,
        metadata=None,
        task_list_id="@default",
    ):
        task = {
            "id": f"task-{len(self.created) + 1}",
            "title": title,
            "notes": notes,
            "due": due,
            "status": "needsAction",
        }
        self.created.append(
            {
                "title": title,
                "notes": notes,
                "due": due,
                "metadata": metadata,
                "task_list_id": task_list_id,
            }
        )
        self.tasks_by_list.setdefault(task_list_id, []).append(task)
        if task_list_id == "@default":
            self.tasks = self.tasks_by_list["@default"]
        return {"status": "ok", "message": "Task created.", "data": {"task": task}}


class FailingCalendarTools:
    def list_calendar_events(self, time_min=None, time_max=None, max_results=100):
        raise ConnectionResetError("calendar reset")


class FailingTaskTools:
    def list_tasks(self, show_completed=False):
        raise ConnectionResetError("tasks reset")


class PartiallyFailingTaskTools(FakeTaskTools):
    def __init__(self, *args, failing_task_list_id: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.failing_task_list_id = failing_task_list_id

    def list_tasks(self, task_list_id="@default", show_completed=False):
        if task_list_id == self.failing_task_list_id:
            return {
                "status": "error",
                "message": "Task list unavailable.",
                "data": {"task_list_id": task_list_id},
            }
        return super().list_tasks(task_list_id=task_list_id, show_completed=show_completed)


class FailingTaskListDiscoveryTools(FakeTaskTools):
    def list_task_lists(self):
        return {
            "status": "error",
            "message": "Google Tasks request failed: [SSL: RECORD_LAYER_FAILURE] record layer failure (_ssl.c:2713)",
            "data": {"error_type": "SSLError"},
        }


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
        items = [
            item for item in self.items
            if (date is None or item.get("date") == date)
            and (status is None or item.get("status", "pending") == status)
        ]
        return {
            "status": "ok",
            "message": "Home Board items returned.",
            "data": {"items": items},
        }


class FakeDecisionTools:
    def __init__(self, decisions=None):
        self.decisions = decisions or []
        self.decided = []

    def list_decisions(self, status=None, include_decided=False):
        return {
            "status": "ok",
            "message": "Family decisions returned.",
            "data": {"decisions": self.decisions},
        }

    def decide(self, decision_id=None, outcome=None, rationale=None):
        self.decided.append((decision_id, outcome, rationale))
        for decision in self.decisions:
            if decision.get("id") == decision_id:
                decision["status"] = "decided"
                decision["outcome"] = outcome
                return {
                    "status": "ok",
                    "message": "Family decision recorded.",
                    "data": {"decision": decision},
                }
        return {
            "status": "error",
            "message": "Decision not found.",
            "data": {"decision_id": decision_id},
        }


class FakeBacklogTools(FakeDecisionTools):
    def __init__(self, items=None):
        super().__init__([])
        self.items = items or []
        self.calls = []

    def list_backlog_items(self, kind=None, include_closed=False):
        items = [item for item in self.items if kind is None or item.get("kind") == kind]
        return {"status": "ok", "message": "Family backlog returned.", "data": {"items": items}}

    def create_backlog_item(self, **kwargs):
        self.calls.append(("create", kwargs))
        return {"status": "ok", "message": "Added.", "data": {"item": kwargs}}

    def move_backlog_item(self, item_id, kind, **kwargs):
        self.calls.append(("move", {"item_id": item_id, "kind": kind, **kwargs}))
        return {"status": "ok", "message": "Moved.", "data": {"item": {"id": item_id, "kind": kind}}}

    def link_backlog_item(self, item_id, **kwargs):
        self.calls.append(("link", {"item_id": item_id, **kwargs}))
        return {"status": "ok", "message": "Source linked.", "data": {"item": {"id": item_id}}}

    def close_backlog_item(self, item_id, outcome, **kwargs):
        self.calls.append(("close", {"item_id": item_id, "outcome": outcome, **kwargs}))
        return {"status": "ok", "message": "Backlog item closed.", "data": {"item": {"id": item_id, "status": "closed"}}}


class FakeShoppingTools:
    def __init__(self, items_by_list=None):
        self.items_by_list = items_by_list or {}
        self.checked = []
        self.cleared = []

    def list_shopping_lists(self):
        return {
            "status": "ok",
            "message": "Shopping lists returned.",
            "data": {
                "lists": [
                    {"slug": "indian", "name": "Indian"},
                    {"slug": "costco", "name": "Costco"},
                    {"slug": "whole-foods", "name": "Whole Foods"},
                    {"slug": "amazon", "name": "Amazon"},
                    {"slug": "others", "name": "Others"},
                ],
            },
        }

    def list_items(self, list_slug=None, include_checked=False):
        items = [
            item for item in self.items_by_list.get(list_slug, [])
            if include_checked or not item.get("checked")
        ]
        return {
            "status": "ok",
            "message": "Shopping items returned.",
            "data": {"items": items},
        }

    def set_checked_by_id(self, item_id=None, checked=True, list_slug=None):
        self.checked.append((item_id, checked, list_slug))
        for items in self.items_by_list.values():
            for item in items:
                if item.get("id") == item_id:
                    item["checked"] = checked
                    return {
                        "status": "ok",
                        "message": "Shopping item checked off.",
                        "data": {"item": item},
                    }
        return {
            "status": "error",
            "message": "Shopping item not found.",
            "data": {"item_id": item_id},
        }

    def clear_list(self, list_slug=None):
        self.cleared.append(list_slug)
        items = self.items_by_list.get(list_slug, [])
        cleared = []
        for item in items:
            if not item.get("checked"):
                item["checked"] = True
                cleared.append(item)
        return {
            "status": "ok",
            "message": f"Cleared {len(cleared)} pending item(s).",
            "data": {"items": cleared},
        }


class FakeHomeworkTools:
    def __init__(self, items=None):
        self.items = items or []
        self.completed = []

    def list_homework(self, child=None, limit=10):
        rows = [
            item for item in self.items
            if child is None or item.get("child") == child
        ][:limit]
        return {
            "status": "ok",
            "message": "Homework returned.",
            "data": {"items": rows},
        }

    def complete_homework(self, homework_item_id, now=None):
        self.completed.append(homework_item_id)
        for item in self.items:
            if item.get("id") == homework_item_id:
                item["status"] = "submitted"
                return {
                    "status": "ok",
                    "message": "Homework marked complete.",
                    "data": {"item": item},
                }
        return {
            "status": "error",
            "message": "Homework item not found.",
            "data": {"homework_item_id": homework_item_id},
        }


class PartiallyFailingHomeworkTools(FakeHomeworkTools):
    def __init__(self, items=None, failing_child="Navya"):
        super().__init__(items)
        self.failing_child = failing_child

    def list_homework(self, child=None, limit=10):
        if child == self.failing_child:
            raise RuntimeError("homework unavailable")
        return super().list_homework(child=child, limit=limit)


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
        self.updated = []
        self.deleted = []

    def status(self, now=None):
        return {
            "status": "ok",
            "message": "Reading Garden returned.",
            "data": {"summary": self.summary},
        }

    def update_reading(self, **kwargs):
        self.updated.append(kwargs)
        event = {
            "id": kwargs.get("event_id"),
            "child": kwargs.get("child") or "Nysha",
            "date": kwargs.get("date") or "2026-07-07",
            "book": kwargs.get("book") or "Mercy Watson",
            "minutes": kwargs.get("minutes"),
            "pages": kwargs.get("pages"),
            "reaction": kwargs.get("reaction"),
            "status": kwargs.get("status") or "in_progress",
            "reading_mode": kwargs.get("reading_mode") or "independent",
        }
        return {
            "status": "ok",
            "message": "Updated reading moment.",
            "data": {"event": event},
        }

    def delete_reading(self, **kwargs):
        self.deleted.append(kwargs)
        return {
            "status": "ok",
            "message": "Deleted reading moment.",
            "data": {"event": {"id": kwargs.get("event_id")}},
        }


def sources(events=None, tasks=None, home_board_items=None, decisions=None, backlog_items=None, shopping_items=None, reading_garden=None, homework_items=None):
    return DashboardSources(
        calendar_tools=FakeCalendarTools(events or []),
        task_tools=FakeTaskTools(tasks or []),
        read_event_metadata=fallback_event_metadata,
        read_task_metadata=fallback_task_metadata,
        recommend_task_matches=fallback_recommend_task_matches,
        home_board_tools=FakeHomeBoardTools(home_board_items),
        decision_tools=FakeBacklogTools(backlog_items) if backlog_items is not None else FakeDecisionTools(decisions),
        shopping_tools=FakeShoppingTools(shopping_items),
        reading_garden_tools=FakeReadingGardenTools(reading_garden),
        homework_tools=FakeHomeworkTools(homework_items),
    )


class DashboardDataTest(unittest.TestCase):
    def test_task_source_failure_keeps_task_unavailable_state_visible(self):
        data = build_dashboard_data(
            DashboardSources(
                calendar_tools=FakeCalendarTools([]),
                task_tools=FailingTaskTools(),
                read_event_metadata=fallback_event_metadata,
                read_task_metadata=fallback_task_metadata,
                recommend_task_matches=fallback_recommend_task_matches,
                home_board_tools=FakeHomeBoardTools([]),
                decision_tools=FakeDecisionTools([]),
                shopping_tools=FakeShoppingTools([]),
                reading_garden_tools=FakeReadingGardenTools({}),
            ),
            now=datetime.fromisoformat("2026-07-03T09:20:00-07:00"),
        )

        self.assertFalse(data["tasks"]["available"])
        self.assertEqual(data["tasks"]["pending"], [])
        self.assertIn("Tasks source unavailable", data["tasks"]["message"])

    def test_dashboard_reads_open_tasks_from_all_google_task_lists(self):
        data = build_dashboard_data(
            DashboardSources(
                calendar_tools=FakeCalendarTools([]),
                task_tools=FakeTaskTools(
                    [],
                    task_lists=[
                        {
                            "id": "@default",
                            "title": "My Tasks",
                            "tasks": [
                                task("Default task", task_id="default-task"),
                            ],
                        },
                        {
                            "id": "finance-list",
                            "title": "Finance",
                            "tasks": [
                                task(
                                    "Set order for SaaS",
                                    due="2026-07-03T00:00:00.000Z",
                                    task_id="finance-task",
                                ),
                            ],
                        },
                        {
                            "id": "shopping-list",
                            "title": "Shopping",
                            "tasks": [
                                task("Buy apples", task_id="shopping-task"),
                            ],
                        },
                    ],
                ),
                read_event_metadata=fallback_event_metadata,
                read_task_metadata=fallback_task_metadata,
                recommend_task_matches=fallback_recommend_task_matches,
                home_board_tools=FakeHomeBoardTools([]),
                decision_tools=FakeDecisionTools([]),
                shopping_tools=FakeShoppingTools([]),
                reading_garden_tools=FakeReadingGardenTools({}),
            ),
            now=datetime.fromisoformat("2026-07-03T09:20:00-07:00"),
        )

        self.assertEqual(
            [(entry["id"], entry["title"], entry["count"]) for entry in data["tasks"]["lists"]],
            [("@default", "My Tasks", 1), ("finance-list", "Finance", 1)],
        )
        self.assertNotIn(
            "Buy apples",
            [task["title"] for task in data["tasks"]["pending"]],
        )
        finance_tasks = [
            task
            for task in data["tasks"]["pending"]
            if task["task_list_id"] == "finance-list"
        ]
        self.assertEqual([task["title"] for task in finance_tasks], ["Set order for SaaS"])

    def test_dashboard_keeps_tasks_from_readable_lists_when_one_list_fails(self):
        data = build_dashboard_data(
            DashboardSources(
                calendar_tools=FakeCalendarTools([]),
                task_tools=PartiallyFailingTaskTools(
                    [],
                    task_lists=[
                        {
                            "id": "@default",
                            "title": "My Tasks",
                            "tasks": [
                                task("Default task", task_id="default-task"),
                            ],
                        },
                        {
                            "id": "finance-list",
                            "title": "Finance",
                            "tasks": [],
                        },
                    ],
                    failing_task_list_id="finance-list",
                ),
                read_event_metadata=fallback_event_metadata,
                read_task_metadata=fallback_task_metadata,
                recommend_task_matches=fallback_recommend_task_matches,
                home_board_tools=FakeHomeBoardTools([]),
                decision_tools=FakeDecisionTools([]),
                shopping_tools=FakeShoppingTools([]),
                reading_garden_tools=FakeReadingGardenTools({}),
            ),
            now=datetime.fromisoformat("2026-07-03T09:20:00-07:00"),
        )

        self.assertTrue(data["tasks"]["available"])
        self.assertIn("Default task", [item["title"] for item in data["tasks"]["pending"]])
        self.assertNotIn(
            "Finance task list unavailable",
            [item["title"] for item in data["tasks"]["pending"]],
        )
        self.assertTrue(
            any("Finance task list unavailable" in warning["detail"] for warning in data["warnings"])
        )

    def test_dashboard_keeps_queue_empty_when_task_list_discovery_fails(self):
        data = build_dashboard_data(
            DashboardSources(
                calendar_tools=FakeCalendarTools([]),
                task_tools=FailingTaskListDiscoveryTools([]),
                read_event_metadata=fallback_event_metadata,
                read_task_metadata=fallback_task_metadata,
                recommend_task_matches=fallback_recommend_task_matches,
                home_board_tools=FakeHomeBoardTools([]),
                decision_tools=FakeDecisionTools([]),
                shopping_tools=FakeShoppingTools([]),
                reading_garden_tools=FakeReadingGardenTools({}),
            ),
            now=datetime.fromisoformat("2026-07-03T09:20:00-07:00"),
        )

        self.assertEqual(data["source_status"], "partial")
        self.assertFalse(data["tasks"]["available"])
        self.assertEqual(data["tasks"]["pending"], [])
        self.assertEqual(data["tasks"]["lists"], [])
        self.assertTrue(
            any("Google Tasks request failed" in warning["detail"] for warning in data["warnings"])
        )

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

    def test_dashboard_tasks_expose_owners_for_filtering(self):
        data = build_dashboard_data(
            sources(
                tasks=[
                    task(
                        "Pack school bag",
                        due="2026-07-03T00:00:00.000Z",
                        metadata={"owner": "nysha"},
                    ),
                    task("File receipt", due="2026-07-04T00:00:00.000Z", metadata={"owner": "dad"}),
                    task("Clean garage shelf"),
                ],
            ),
            now=datetime.fromisoformat("2026-07-03T09:00:00-07:00"),
        )

        self.assertEqual(
            data["tasks"]["owners"],
            [
                {"owner": "dad", "label": "Dad", "count": 1, "today_count": 0},
                {"owner": "nysha", "label": "Nysha", "count": 1, "today_count": 1},
                {"owner": "unknown", "label": "Unassigned", "count": 1, "today_count": 0},
            ],
        )
        owners_by_title = {
            task_data["title"]: task_data["owner"]
            for task_data in data["tasks"]["pending"]
        }
        self.assertEqual(owners_by_title["Pack school bag"], "nysha")
        self.assertEqual(owners_by_title["Clean garage shelf"], "unknown")

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

    def test_dashboard_updates_reading_event(self):
        reading_tools = FakeReadingGardenTools()
        active_sources = DashboardSources(
            calendar_tools=FakeCalendarTools([]),
            task_tools=FakeTaskTools([]),
            read_event_metadata=fallback_event_metadata,
            read_task_metadata=fallback_task_metadata,
            recommend_task_matches=fallback_recommend_task_matches,
            home_board_tools=FakeHomeBoardTools(),
            decision_tools=FakeDecisionTools(),
            shopping_tools=FakeShoppingTools(),
            reading_garden_tools=reading_tools,
        )

        response = update_dashboard_reading_event(
            "event-1",
            book="Frog and Toad",
            date="2026-07-08",
            pages=12,
            reading_mode="read_together",
            sources=active_sources,
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["event"]["book"], "Frog and Toad")
        self.assertEqual(reading_tools.updated[0]["event_id"], "event-1")
        self.assertEqual(reading_tools.updated[0]["book"], "Frog and Toad")
        self.assertEqual(reading_tools.updated[0]["date"], "2026-07-08")
        self.assertEqual(reading_tools.updated[0]["pages"], 12)
        self.assertEqual(reading_tools.updated[0]["reading_mode"], "read_together")

    def test_dashboard_deletes_reading_event(self):
        reading_tools = FakeReadingGardenTools()
        active_sources = DashboardSources(
            calendar_tools=FakeCalendarTools([]),
            task_tools=FakeTaskTools([]),
            read_event_metadata=fallback_event_metadata,
            read_task_metadata=fallback_task_metadata,
            recommend_task_matches=fallback_recommend_task_matches,
            home_board_tools=FakeHomeBoardTools(),
            decision_tools=FakeDecisionTools(),
            shopping_tools=FakeShoppingTools(),
            reading_garden_tools=reading_tools,
        )

        response = delete_dashboard_reading_event("event-1", sources=active_sources)

        self.assertEqual(response["status"], "ok")
        self.assertEqual(reading_tools.deleted, [{"event_id": "event-1"}])

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

    def test_complete_dashboard_homework_marks_item_submitted(self):
        homework_tools = FakeHomeworkTools([
            {
                "id": "hw-1",
                "child": "Nysha",
                "title": "Reading homework",
                "subject": "Reading",
                "due_date": "2026-08-21",
                "status": "assigned",
            },
        ])
        dashboard_sources = DashboardSources(
            calendar_tools=FakeCalendarTools([]),
            task_tools=FakeTaskTools([]),
            read_event_metadata=fallback_event_metadata,
            read_task_metadata=fallback_task_metadata,
            recommend_task_matches=fallback_recommend_task_matches,
            home_board_tools=FakeHomeBoardTools(),
            decision_tools=FakeDecisionTools(),
            homework_tools=homework_tools,
        )

        response = complete_dashboard_homework(
            "hw-1",
            sources=dashboard_sources,
            now=datetime(2026, 8, 18, 12, 0, 0),
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(homework_tools.completed, ["hw-1"])
        self.assertEqual(response["data"]["item"]["status"], "submitted")
        self.assertEqual(response["data"]["item"]["due_label"], "Fri, Aug 21")

    def test_complete_dashboard_decision_marks_decision_done(self):
        decision_tools = FakeDecisionTools(
            [
                {
                    "id": "decision-1",
                    "title": "Summer camp plan",
                    "status": "inbox",
                    "owner": "unknown",
                    "urgency": "normal",
                    "size": "small",
                    "due": "",
                    "options": [],
                    "evidence": [],
                    "next_steps": [],
                },
            ],
        )
        dashboard_sources = DashboardSources(
            calendar_tools=FakeCalendarTools([]),
            task_tools=FakeTaskTools([]),
            read_event_metadata=fallback_event_metadata,
            read_task_metadata=fallback_task_metadata,
            recommend_task_matches=fallback_recommend_task_matches,
            home_board_tools=FakeHomeBoardTools(),
            decision_tools=decision_tools,
        )

        response = complete_dashboard_decision(
            "decision-1",
            sources=dashboard_sources,
            now=datetime.fromisoformat("2026-07-03T09:00:00-07:00"),
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(decision_tools.decided, [("decision-1", "Marked done from dashboard.", None)])
        self.assertEqual(response["data"]["decision"]["status"], "decided")
        self.assertEqual(response["data"]["decision"]["outcome"], "Marked done from dashboard.")

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

    def test_dashboard_includes_tomorrow_home_board_items_for_next_day_prep(self):
        home_board_tools = FakeHomeBoardTools(
            [
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
                },
                {
                    "id": "home-2",
                    "person_or_group": "Navya",
                    "message": "Pack swim bag",
                    "date": "2026-07-04",
                    "context": "before_leave",
                    "trigger": None,
                    "status": "pending",
                    "priority": "medium",
                    "expires_at": "2026-07-05T00:00:00-07:00",
                },
            ],
        )
        data = build_dashboard_data(
            DashboardSources(
                calendar_tools=FakeCalendarTools([]),
                task_tools=FakeTaskTools([]),
                read_event_metadata=fallback_event_metadata,
                read_task_metadata=fallback_task_metadata,
                recommend_task_matches=fallback_recommend_task_matches,
                home_board_tools=home_board_tools,
                decision_tools=FakeDecisionTools(),
                shopping_tools=FakeShoppingTools(),
                reading_garden_tools=FakeReadingGardenTools(),
            ),
            now=datetime.fromisoformat("2026-07-03T09:00:00-07:00"),
        )

        self.assertEqual(data["summary"]["home_board_count"], 1)
        self.assertEqual([item["message"] for item in data["home_board"]["today"]], ["Take journal"])
        self.assertEqual([item["message"] for item in data["home_board"]["tomorrow"]], ["Pack swim bag"])
        self.assertEqual(data["home_board"]["tomorrow"][0]["context_label"], "Before leaving")
        self.assertEqual(
            [call["date"] for call in home_board_tools.list_calls],
            ["2026-07-03", "2026-07-04"],
        )

    def test_dashboard_includes_shopping_lists(self):
        data = build_dashboard_data(
            sources(
                shopping_items={
                    "indian": [
                        {"id": "item-1", "title": "paneer", "list_slug": "indian", "checked": False},
                    ],
                    "costco": [
                        {"id": "item-2", "title": "milk", "list_slug": "costco", "checked": False},
                    ],
                },
            ),
            now=datetime.fromisoformat("2026-07-03T09:00:00-07:00"),
        )

        self.assertEqual(data["summary"]["shopping_count"], 2)
        self.assertEqual(
            [(item["list_name"], item["title"]) for item in data["shopping"]["pending"]],
            [("Indian", "paneer"), ("Costco", "milk")],
        )
        by_list = {shopping_list["slug"]: shopping_list for shopping_list in data["shopping"]["by_list"]}
        self.assertEqual(by_list["indian"]["pending_count"], 1)
        self.assertEqual(by_list["costco"]["items"][0]["title"], "milk")

    def test_dashboard_includes_homework_by_child_and_class(self):
        data = build_dashboard_data(
            sources(
                homework_items=[
                    {
                        "id": "hw-1",
                        "child": "Nysha",
                        "title": "Fractions packet",
                        "subject": "Math",
                        "assigned_date": "2026-08-14",
                        "due_date": "2026-08-15",
                        "status": "assigned",
                    },
                    {
                        "id": "hw-2",
                        "child": "Navya",
                        "title": "Color wheel",
                        "subject": "Art",
                        "assigned_date": "2026-08-14",
                        "due_date": "2026-08-18",
                        "status": "assigned",
                    },
                ],
            ),
            now=datetime.fromisoformat("2026-08-15T09:00:00-07:00"),
        )

        self.assertEqual(data["summary"]["homework_count"], 2)
        self.assertEqual(data["homework"]["due_now_count"], 1)
        by_child = {entry["child"]: entry for entry in data["homework"]["children"]}
        self.assertEqual(by_child["Nysha"]["open_count"], 1)
        self.assertEqual(by_child["Nysha"]["classes"][0]["class_name"], "Math")
        self.assertEqual(by_child["Navya"]["classes"][0]["class_name"], "Art")
        self.assertEqual(data["homework"]["upcoming"][0]["due_label"], "Due today")

    def test_dashboard_homework_partial_failure_keeps_loaded_upcoming_items(self):
        data = build_dashboard_data(
            DashboardSources(
                calendar_tools=FakeCalendarTools([]),
                task_tools=FakeTaskTools([]),
                read_event_metadata=fallback_event_metadata,
                read_task_metadata=fallback_task_metadata,
                recommend_task_matches=fallback_recommend_task_matches,
                home_board_tools=FakeHomeBoardTools([]),
                decision_tools=FakeDecisionTools([]),
                shopping_tools=FakeShoppingTools(),
                reading_garden_tools=FakeReadingGardenTools(),
                homework_tools=PartiallyFailingHomeworkTools(
                    [
                        {
                            "id": "hw-1",
                            "child": "Nysha",
                            "title": "Reading log",
                            "subject": "Reading",
                            "assigned_date": "2026-08-14",
                            "due_date": "2026-08-21",
                            "status": "assigned",
                        },
                    ],
                    failing_child="Navya",
                ),
            ),
            now=datetime.fromisoformat("2026-08-15T09:00:00-07:00"),
        )

        self.assertEqual(data["source_status"], "partial")
        self.assertTrue(data["homework"]["available"])
        self.assertTrue(data["homework"]["partial"])
        self.assertEqual(data["homework"]["upcoming"][0]["title"], "Reading log")

    def test_complete_dashboard_shopping_item_updates_source(self):
        shopping_tools = FakeShoppingTools(
            {
                "costco": [
                    {"id": "item-1", "title": "milk", "list_slug": "costco", "checked": False},
                ],
            },
        )
        dashboard_sources = DashboardSources(
            calendar_tools=FakeCalendarTools([]),
            task_tools=FakeTaskTools([]),
            read_event_metadata=fallback_event_metadata,
            read_task_metadata=fallback_task_metadata,
            recommend_task_matches=fallback_recommend_task_matches,
            home_board_tools=FakeHomeBoardTools(),
            decision_tools=FakeDecisionTools(),
            shopping_tools=shopping_tools,
        )

        response = complete_dashboard_shopping_item(
            "item-1",
            list_slug="costco",
            sources=dashboard_sources,
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(shopping_tools.checked, [("item-1", True, "costco")])
        self.assertTrue(shopping_tools.items_by_list["costco"][0]["checked"])

    def test_clear_dashboard_shopping_list_updates_source(self):
        shopping_tools = FakeShoppingTools(
            {
                "indian": [
                    {"id": "item-1", "title": "paneer", "list_slug": "indian", "checked": False},
                    {"id": "item-2", "title": "curry leaves", "list_slug": "indian", "checked": False},
                ],
            },
        )
        dashboard_sources = DashboardSources(
            calendar_tools=FakeCalendarTools([]),
            task_tools=FakeTaskTools([]),
            read_event_metadata=fallback_event_metadata,
            read_task_metadata=fallback_task_metadata,
            recommend_task_matches=fallback_recommend_task_matches,
            home_board_tools=FakeHomeBoardTools(),
            decision_tools=FakeDecisionTools(),
            shopping_tools=shopping_tools,
        )

        response = clear_dashboard_shopping_list("indian", sources=dashboard_sources)

        self.assertEqual(response["status"], "ok")
        self.assertEqual(shopping_tools.cleared, ["indian"])
        self.assertTrue(all(item["checked"] for item in shopping_tools.items_by_list["indian"]))

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

    def test_dashboard_builds_ranked_backlog_and_link_progress(self):
        base = {
            "context": "",
            "status": "open",
            "owner": "both",
            "urgency": "normal",
            "priority": 0,
            "pinned": False,
            "review_on": None,
            "due": None,
            "notes": [],
            "positions": [],
            "links": [],
            "options": [],
            "evidence": [],
            "next_steps": [],
            "updated_at": "2026-07-01T09:00:00-07:00",
        }
        backlog_items = [
            {**base, "id": "discussion-1", "kind": "discussion", "title": "Birthday", "pinned": True},
            {
                **base,
                "id": "planning-1",
                "kind": "planning",
                "title": "Camping",
                "status": "preparing",
                "due": "2026-07-04",
                "links": [{"id": "link-1", "source_type": "google_task", "external_id": "pack-1", "container_id": "@default", "title": "Pack tent"}],
            },
            {**base, "id": "decision-1", "kind": "decision", "title": "Choose school", "status": "inbox", "urgency": "high"},
        ]
        completed_task = task("Pack tent", task_id="pack-1")
        completed_task["status"] = "completed"

        data = build_dashboard_data(
            sources(tasks=[completed_task], backlog_items=backlog_items),
            now=datetime.fromisoformat("2026-07-03T09:00:00-07:00"),
        )

        self.assertEqual(data["backlog"]["counts"], {"discussion": 1, "planning": 1, "decision": 1})
        self.assertEqual(data["backlog"]["lanes"]["discussion"][0]["id"], "discussion-1")
        self.assertTrue(data["backlog"]["lanes"]["planning"][0]["ready_to_close"])
        self.assertTrue(data["backlog"]["lanes"]["planning"][0]["links"][0]["completed"])
        self.assertLessEqual(len(data["backlog"]["attention"]), 5)

    def test_dashboard_backlog_mutations_use_dashboard_actor_and_confirm_move(self):
        tools = FakeBacklogTools()
        active_sources = sources()
        active_sources = DashboardSources(
            **{**active_sources.__dict__, "decision_tools": tools},
        )

        created = create_dashboard_backlog_item(
            kind="discussion",
            title="Birthday",
            owner="mom",
            date_value="2026-07-05",
            sources=active_sources,
        )
        unconfirmed = perform_dashboard_backlog_action(
            action="move",
            item_id="item-1",
            payload={"kind": "planning"},
            sources=active_sources,
        )
        moved = perform_dashboard_backlog_action(
            action="move",
            item_id="item-1",
            payload={"kind": "planning", "confirmed": True},
            sources=active_sources,
        )
        closed = perform_dashboard_backlog_action(
            action="close",
            item_id="item-1",
            payload={"outcome": "Done", "confirmed": True},
            sources=active_sources,
        )
        default_closed = perform_dashboard_backlog_action(
            action="close",
            item_id="item-2",
            payload={"confirmed": True},
            sources=active_sources,
        )
        invalid = perform_dashboard_backlog_action(
            action="invent",
            item_id="item-1",
            sources=active_sources,
        )

        self.assertEqual(created["status"], "ok")
        self.assertEqual(tools.calls[0][1]["actor"], "family dashboard")
        self.assertEqual(tools.calls[0][1]["review_on"], "2026-07-05")
        self.assertEqual(unconfirmed["status"], "needs_confirmation")
        self.assertEqual(moved["status"], "ok")
        self.assertTrue(tools.calls[1][1]["confirmed"])
        self.assertEqual(tools.calls[1][1]["actor"], "family dashboard")
        self.assertEqual(closed["status"], "ok")
        self.assertEqual(tools.calls[2][0], "close")
        self.assertEqual(tools.calls[2][1]["outcome"], "Done")
        self.assertTrue(tools.calls[2][1]["confirmed"])
        self.assertEqual(default_closed["status"], "ok")
        self.assertEqual(tools.calls[3][0], "close")
        self.assertEqual(tools.calls[3][1]["outcome"], "Closed from dashboard.")
        self.assertEqual(invalid["status"], "error")

    def test_dashboard_backlog_action_creates_and_links_follow_up_task(self):
        backlog_tools = FakeBacklogTools()
        task_tools = FakeTaskTools([])
        active_sources = sources()
        active_sources = DashboardSources(
            **{
                **active_sources.__dict__,
                "decision_tools": backlog_tools,
                "task_tools": task_tools,
            },
        )

        response = perform_dashboard_backlog_action(
            action="create_task",
            item_id="decision-1",
            payload={"title": "Buy Nest subscription", "due": "2026-08-15"},
            sources=active_sources,
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["message"], "Follow-up task created and linked.")
        self.assertEqual(task_tools.created[0]["title"], "Buy Nest subscription")
        self.assertEqual(task_tools.created[0]["due"], "2026-08-15")
        self.assertEqual(
            task_tools.created[0]["metadata"],
            {"source": "family_backlog", "backlog_item_id": "decision-1"},
        )
        self.assertEqual(backlog_tools.calls[0][0], "link")
        self.assertEqual(backlog_tools.calls[0][1]["item_id"], "decision-1")
        self.assertEqual(backlog_tools.calls[0][1]["source_type"], "google_task")
        self.assertEqual(backlog_tools.calls[0][1]["external_id"], "task-1")

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
                    self.assertIn('href="#backlog"', body)
                    self.assertIn('href="#shopping"', body)
                    self.assertIn('href="#library"', body)
                    self.assertIn('id="shopping-list-tabs"', body)
                    self.assertIn('id="shopping-list-items"', body)
                    self.assertIn('id="library"', body)
                    self.assertIn('id="library-visit-label"', body)
                    self.assertIn('id="library-bag-list"', body)
                    self.assertIn('data-dashboard-card="library-bag"', body)
                    self.assertIn('data-dashboard-card="reading-photos"', body)
                    self.assertIn('data-dashboard-card="backlog-attention"', body)
                    self.assertIn('data-dashboard-card="family-members"', body)
                    self.assertIn('id="discussion-items"', body)
                    self.assertIn('id="planning-items"', body)
                    self.assertIn('id="decision-backlog-items"', body)
                    self.assertIn('id="backlog-add-dialog"', body)
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

    def test_backlog_api_returns_lanes(self):
        import dashboard_server

        original_get_dashboard_data = dashboard_server.get_dashboard_data
        dashboard_server.get_dashboard_data = lambda: {
            "backlog": {"counts": {"discussion": 1, "planning": 0, "decision": 0}, "lanes": {}}
        }
        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        try:
            thread.start()
            with urlopen(f"http://127.0.0.1:{server.server_port}/api/backlog", timeout=5) as response:
                body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn('"discussion": 1', body)
        finally:
            dashboard_server.get_dashboard_data = original_get_dashboard_data
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_backlog_create_route_requires_token_and_accepts_authorized_request(self):
        import dashboard_server

        original_create = dashboard_server.create_dashboard_backlog_item
        calls = []

        def fake_create(**kwargs):
            calls.append(kwargs)
            return {"status": "ok", "message": "Added.", "data": {"item": {"id": "item-1"}}}

        dashboard_server.create_dashboard_backlog_item = fake_create
        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        try:
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}/api/backlog/items"
            unauthorized = Request(url, data=b'{}', headers={"Content-Type": "application/json"}, method="POST")
            with self.assertRaises(HTTPError) as rejected:
                urlopen(unauthorized, timeout=5)
            self.assertEqual(rejected.exception.code, 403)
            rejected.exception.close()

            authorized = Request(
                url,
                data=b'{"kind":"discussion","title":"Birthday","owner":"mom"}',
                headers={
                    "Content-Type": "application/json",
                    "X-N4OS-Dashboard-Action-Token": dashboard_server.ACTION_TOKEN,
                },
                method="POST",
            )
            with urlopen(authorized, timeout=5) as response:
                response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(calls[0]["kind"], "discussion")
            self.assertEqual(calls[0]["title"], "Birthday")
        finally:
            dashboard_server.create_dashboard_backlog_item = original_create
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_backlog_action_route_requires_token_and_uses_closed_action_code(self):
        import dashboard_server

        original_action = dashboard_server.perform_dashboard_backlog_action
        calls = []

        def fake_action(**kwargs):
            calls.append(kwargs)
            return {"status": "ok", "message": "Pinned.", "data": {}}

        dashboard_server.perform_dashboard_backlog_action = fake_action
        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        try:
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}/api/backlog/actions"
            unauthorized = Request(url, data=b'{}', headers={"Content-Type": "application/json"}, method="POST")
            with self.assertRaises(HTTPError) as rejected:
                urlopen(unauthorized, timeout=5)
            self.assertEqual(rejected.exception.code, 403)
            rejected.exception.close()

            authorized = Request(
                url,
                data=b'{"action":"pin","item_id":"item-1","pinned":true}',
                headers={
                    "Content-Type": "application/json",
                    "X-N4OS-Dashboard-Action-Token": dashboard_server.ACTION_TOKEN,
                },
                method="POST",
            )
            with urlopen(authorized, timeout=5) as response:
                response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(calls[0]["action"], "pin")
            self.assertEqual(calls[0]["item_id"], "item-1")
        finally:
            dashboard_server.perform_dashboard_backlog_action = original_action
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

    def test_shopping_api_route_returns_lists(self):
        import dashboard_server

        original_get_dashboard_data = dashboard_server.get_dashboard_data

        def fake_dashboard_data():
            return {"shopping": {"pending": [{"title": "paneer"}], "by_list": []}}

        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        try:
            dashboard_server.get_dashboard_data = fake_dashboard_data
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            with urlopen(base_url + "/api/shopping", timeout=5) as response:
                body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("paneer", body)
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

    def test_homework_complete_api_route_completes_homework(self):
        import dashboard_server

        original_complete_dashboard_homework = dashboard_server.complete_dashboard_homework
        calls = []

        def fake_complete_dashboard_homework(homework_item_id=None):
            calls.append({"homework_item_id": homework_item_id})
            return {
                "status": "ok",
                "message": "Homework marked complete.",
                "data": {"item": {"id": homework_item_id, "status": "submitted"}},
            }

        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        try:
            dashboard_server.complete_dashboard_homework = fake_complete_dashboard_homework
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            request = Request(
                base_url + "/api/homework/complete",
                data=b'{"homework_item_id":"hw-1"}',
                headers={
                    "Content-Type": "application/json",
                    "X-N4OS-Dashboard-Action-Token": dashboard_server.ACTION_TOKEN,
                },
                method="POST",
            )
            with urlopen(request, timeout=5) as response:
                body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("Homework marked complete", body)
            self.assertEqual(calls, [{"homework_item_id": "hw-1"}])
        finally:
            dashboard_server.complete_dashboard_homework = original_complete_dashboard_homework
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_shopping_check_api_route_checks_item(self):
        import dashboard_server

        original_complete_dashboard_shopping_item = dashboard_server.complete_dashboard_shopping_item
        calls = []

        def fake_complete_dashboard_shopping_item(item_id=None, list_slug=None):
            calls.append({"item_id": item_id, "list_slug": list_slug})
            return {
                "status": "ok",
                "message": "Shopping item checked off.",
                "data": {"item": {"id": item_id, "checked": True}},
            }

        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        try:
            dashboard_server.complete_dashboard_shopping_item = fake_complete_dashboard_shopping_item
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            request = Request(
                base_url + "/api/shopping/items/check",
                data=b'{"item_id":"item-1","list_slug":"costco"}',
                headers={
                    "Content-Type": "application/json",
                    "X-N4OS-Dashboard-Action-Token": dashboard_server.ACTION_TOKEN,
                },
                method="POST",
            )
            with urlopen(request, timeout=5) as response:
                body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("Shopping item checked off", body)
            self.assertEqual(calls, [{"item_id": "item-1", "list_slug": "costco"}])
        finally:
            dashboard_server.complete_dashboard_shopping_item = original_complete_dashboard_shopping_item
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_shopping_clear_api_route_clears_list(self):
        import dashboard_server

        original_clear_dashboard_shopping_list = dashboard_server.clear_dashboard_shopping_list
        calls = []

        def fake_clear_dashboard_shopping_list(list_slug=None):
            calls.append({"list_slug": list_slug})
            return {
                "status": "ok",
                "message": "Cleared 2 pending item(s).",
                "data": {"items": []},
            }

        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        try:
            dashboard_server.clear_dashboard_shopping_list = fake_clear_dashboard_shopping_list
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            request = Request(
                base_url + "/api/shopping/lists/clear",
                data=b'{"list_slug":"indian"}',
                headers={
                    "Content-Type": "application/json",
                    "X-N4OS-Dashboard-Action-Token": dashboard_server.ACTION_TOKEN,
                },
                method="POST",
            )
            with urlopen(request, timeout=5) as response:
                body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("Cleared 2 pending", body)
            self.assertEqual(calls, [{"list_slug": "indian"}])
        finally:
            dashboard_server.clear_dashboard_shopping_list = original_clear_dashboard_shopping_list
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_library_reading_update_api_route_updates_event(self):
        import dashboard_server

        original_update_dashboard_reading_event = dashboard_server.update_dashboard_reading_event
        calls = []

        def fake_update_dashboard_reading_event(event_id=None, **kwargs):
            calls.append({"event_id": event_id, **kwargs})
            return {
                "status": "ok",
                "message": "Updated reading moment.",
                "data": {"event": {"id": event_id, "book": kwargs.get("book")}},
            }

        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        try:
            dashboard_server.update_dashboard_reading_event = fake_update_dashboard_reading_event
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            request = Request(
                base_url + "/api/library/reading/update",
                data=b'{"event_id":"event-1","book":"Frog and Toad","pages":12}',
                headers={
                    "Content-Type": "application/json",
                    "X-N4OS-Dashboard-Action-Token": dashboard_server.ACTION_TOKEN,
                },
                method="POST",
            )
            with urlopen(request, timeout=5) as response:
                body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("Updated reading moment", body)
            self.assertEqual(calls[0]["event_id"], "event-1")
            self.assertEqual(calls[0]["book"], "Frog and Toad")
            self.assertEqual(calls[0]["pages"], 12)
        finally:
            dashboard_server.update_dashboard_reading_event = original_update_dashboard_reading_event
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_library_reading_delete_api_route_deletes_event(self):
        import dashboard_server

        original_delete_dashboard_reading_event = dashboard_server.delete_dashboard_reading_event
        calls = []

        def fake_delete_dashboard_reading_event(event_id=None):
            calls.append({"event_id": event_id})
            return {
                "status": "ok",
                "message": "Deleted reading moment.",
                "data": {"event": {"id": event_id}},
            }

        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        try:
            dashboard_server.delete_dashboard_reading_event = fake_delete_dashboard_reading_event
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            request = Request(
                base_url + "/api/library/reading/delete",
                data=b'{"event_id":"event-1"}',
                headers={
                    "Content-Type": "application/json",
                    "X-N4OS-Dashboard-Action-Token": dashboard_server.ACTION_TOKEN,
                },
                method="POST",
            )
            with urlopen(request, timeout=5) as response:
                body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("Deleted reading moment", body)
            self.assertEqual(calls, [{"event_id": "event-1"}])
        finally:
            dashboard_server.delete_dashboard_reading_event = original_delete_dashboard_reading_event
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_decisions_complete_api_route_marks_decision_done(self):
        import dashboard_server

        original_complete_dashboard_decision = dashboard_server.complete_dashboard_decision
        calls = []

        def fake_complete_dashboard_decision(decision_id=None, outcome=None):
            calls.append({"decision_id": decision_id, "outcome": outcome})
            return {
                "status": "ok",
                "message": "Family decision recorded.",
                "data": {"decision": {"id": decision_id, "status": "decided"}},
            }

        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        try:
            dashboard_server.complete_dashboard_decision = fake_complete_dashboard_decision
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            request = Request(
                base_url + "/api/decisions/complete",
                data=b'{"decision_id":"decision-1"}',
                headers={
                    "Content-Type": "application/json",
                    "X-N4OS-Dashboard-Action-Token": dashboard_server.ACTION_TOKEN,
                },
                method="POST",
            )
            with urlopen(request, timeout=5) as response:
                body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("Family decision recorded", body)
            self.assertEqual(calls, [{"decision_id": "decision-1", "outcome": None}])
        finally:
            dashboard_server.complete_dashboard_decision = original_complete_dashboard_decision
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

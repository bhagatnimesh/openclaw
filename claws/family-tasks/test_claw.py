import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from claw import (
    FamilyTasksClaw,
    _format_created_task_message,
    handle_task_request,
    run_cli,
    run_interactive,
)
from intent import DEFAULT_TIMEZONE, read_metadata_from_notes, write_metadata_to_notes


class FakeProvider:
    def __init__(self):
        self.created = []
        self.completed = []
        self.deleted = []
        self.tasks = []

    def list_task_lists(self):
        return [{"id": "@default", "title": "My Tasks"}]

    def create_task(self, title, notes=None, due=None, task_list_id="@default"):
        task = {
            "id": "task-123",
            "title": title,
            "notes": notes,
            "due": due,
            "status": "needsAction",
        }
        self.created.append(task)
        return task

    def list_tasks(self, task_list_id="@default", show_completed=False):
        return self.tasks

    def update_task(
        self,
        task_id,
        title=None,
        notes=None,
        due=None,
        status=None,
        task_list_id="@default",
    ):
        return {"id": task_id, "status": status or "needsAction"}

    def complete_task(self, task_id, task_list_id="@default"):
        self.completed.append(task_id)
        return {"id": task_id, "status": "completed"}

    def delete_task(self, task_id, task_list_id="@default"):
        self.deleted.append(task_id)


class FailingProvider(FakeProvider):
    def create_task(self, title, notes=None, due=None, task_list_id="@default"):
        raise RuntimeError("invalid_scope: Bad Request")


def _task(title, metadata, due=None):
    return {
        "id": title.lower().replace(" ", "-"),
        "title": title,
        "notes": write_metadata_to_notes(None, metadata),
        "due": due,
        "status": "needsAction",
    }


def _situational_tasks():
    return [
        _task(
            "Call mom",
            {
                "context": ["phone"],
                "energy": "low",
                "complexity": "low",
                "duration_minutes": 20,
                "effort_type": "communication",
                "requires": ["phone"],
                "can_do_while": ["driving", "commuting"],
                "location": "anywhere",
            },
        ),
        _task(
            "Change water filter",
            {
                "context": ["home"],
                "energy": "medium",
                "complexity": "low",
                "duration_minutes": 15,
                "effort_type": "physical",
                "requires": ["equipment"],
                "location": "home",
            },
        ),
        _task(
            "Book flight",
            {
                "context": ["computer"],
                "energy": "medium",
                "complexity": "medium",
                "duration_minutes": 30,
                "effort_type": "admin",
                "requires": ["computer", "internet"],
                "location": "anywhere",
            },
        ),
        _task(
            "Tidy desk",
            {
                "context": ["home"],
                "energy": "low",
                "complexity": "low",
                "duration_minutes": 10,
                "effort_type": "physical",
                "requires": [],
                "location": "home",
            },
        ),
        _task(
            "Fill visa form",
            {
                "context": ["computer"],
                "energy": "high",
                "complexity": "high",
                "duration_minutes": 60,
                "effort_type": "paperwork",
                "requires": ["computer", "paperwork", "focus"],
                "location": "anywhere",
            },
        ),
        _task(
            "Research art class",
            {
                "context": ["computer"],
                "energy": "medium",
                "complexity": "medium",
                "duration_minutes": 45,
                "effort_type": "research",
                "requires": ["computer", "internet", "focus"],
                "location": "anywhere",
            },
        ),
        _task(
            "Go grocery shopping",
            {
                "context": ["errand", "outside"],
                "energy": "medium",
                "complexity": "low",
                "duration_minutes": 30,
                "effort_type": "errand",
                "requires": ["car"],
                "location": "outside",
            },
        ),
    ]


class FamilyTasksClawTest(unittest.TestCase):
    def test_created_task_message_prefers_web_view_link(self):
        message = _format_created_task_message(
            {
                "id": "task-123",
                "title": "Call Rahul",
                "webViewLink": "https://tasks.google.com/task-web-view",
                "selfLink": "https://tasks.googleapis.com/tasks/v1/task-api",
            }
        )

        self.assertEqual(
            message,
            "Created task: Call Rahul (open: https://tasks.google.com/task-web-view).",
        )
        self.assertNotIn("task id", message)

    def test_created_task_message_falls_back_to_self_link(self):
        message = _format_created_task_message(
            {
                "id": "task-123",
                "title": "Call Rahul",
                "selfLink": "https://tasks.googleapis.com/tasks/v1/task-api",
            }
        )

        self.assertEqual(
            message,
            "Created task: Call Rahul (open: https://tasks.googleapis.com/tasks/v1/task-api).",
        )

    def test_created_task_message_falls_back_to_links_collection(self):
        message = _format_created_task_message(
            {
                "id": "task-123",
                "title": "Call Rahul",
                "links": [{"link": "https://example.com/task"}],
            }
        )

        self.assertEqual(
            message,
            "Created task: Call Rahul (open: https://example.com/task).",
        )

    def test_created_task_message_falls_back_to_task_id_without_url(self):
        message = _format_created_task_message(
            {
                "id": "task-123",
                "title": "Call Rahul",
            }
        )

        self.assertEqual(message, "Created task: Call Rahul (task id: task-123).")

    def test_add_task_from_request(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(
                "Add task call Rahul during commute",
                reference_time=now,
            )

        self.assertEqual(message, "Created task: Call Rahul (task id: task-123).")
        created = provider.created[0]
        self.assertEqual(created["title"], "Call Rahul")
        _, metadata = read_metadata_from_notes(created["notes"])
        self.assertEqual(metadata["context"], ["car", "phone"])
        self.assertEqual(metadata["effort_type"], "communication")
        self.assertEqual(metadata["requires"], ["phone"])

    def test_add_task_from_request_reports_invalid_scope(self):
        claw = FamilyTasksClaw.from_provider(FailingProvider())

        output = StringIO()
        with redirect_stdout(output):
            message = claw.add_task_from_request(
                "Add task change water filter in refrigerator",
            )

        self.assertIn("missing the Tasks scope", message)
        self.assertIn("missing the Tasks scope", output.getvalue())

    def test_recommend_tasks_from_request(self):
        provider = FakeProvider()
        provider.tasks = [
            {
                "id": "task-1",
                "title": "Call Rahul",
                "notes": write_metadata_to_notes(
                    None,
                    {
                        "context": ["car", "phone"],
                        "energy": "low",
                        "duration_minutes": 20,
                        "effort_type": "communication",
                        "requires": ["phone"],
                        "can_do_while": ["driving"],
                    },
                ),
                "status": "needsAction",
            },
            {
                "id": "task-2",
                "title": "Research summer camps",
                "notes": write_metadata_to_notes(
                    None,
                    {
                        "context": ["computer"],
                        "energy": "medium",
                        "duration_minutes": 45,
                        "effort_type": "research",
                        "requires": ["computer", "internet", "focus"],
                    },
                ),
                "status": "needsAction",
            },
        ]
        claw = FamilyTasksClaw.from_provider(provider)
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.recommend_tasks_from_request(
                "What can I do while driving?",
                reference_time=now,
            )

        self.assertIn("Call Rahul", message)
        self.assertNotIn("Research summer camps", message)
        self.assertIn("can do while driving", message)

    def test_situational_recommendation_examples_use_fake_tasks(self):
        cases = [
            (
                "I'm driving, what can I do?",
                ["Call mom"],
                ["can do while driving"],
            ),
            (
                "I have 20 minutes and low energy",
                ["Tidy desk", "Call mom"],
                ["fits low energy", "fits in 20 minutes"],
            ),
            (
                "I have my laptop and 30 minutes",
                ["Book flight"],
                ["matches computer", "fits in 30 minutes"],
            ),
            (
                "I feel bored, give me something easy",
                ["Tidy desk"],
                ["low effort"],
            ),
            (
                "I can do paperwork now",
                ["Fill visa form"],
                ["paperwork task"],
            ),
            (
                "I'm at home and want physical tasks",
                ["Tidy desk", "Change water filter"],
                ["matches home", "physical task"],
            ),
        ]

        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        for prompt, expected_titles, expected_reasons in cases:
            with self.subTest(prompt=prompt):
                provider = FakeProvider()
                provider.tasks = _situational_tasks()
                claw = FamilyTasksClaw.from_provider(provider)

                with redirect_stdout(StringIO()):
                    message = claw.recommend_tasks_from_request(prompt, reference_time=now)

                for title in expected_titles:
                    self.assertIn(title, message)
                for reason in expected_reasons:
                    self.assertIn(reason, message)
                self.assertEqual(provider.created, [])
                self.assertEqual(provider.completed, [])
                self.assertEqual(provider.deleted, [])

    def test_complete_task_requires_followup_confirmation(self):
        provider = FakeProvider()
        provider.tasks = [
            {
                "id": "task-1",
                "title": "Change water filter",
                "notes": write_metadata_to_notes(None, {"context": ["home"]}),
                "status": "needsAction",
            }
        ]
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            message = claw.complete_task_from_request("Complete change water filter")

        self.assertIn("Complete it? yes/no", message)
        self.assertEqual(provider.completed, [])

        with redirect_stdout(StringIO()):
            handled = claw.handle_pending_response("yes")

        self.assertTrue(handled)
        self.assertEqual(provider.completed, ["task-1"])

    def test_handle_task_request_uses_pending_confirmation(self):
        provider = FakeProvider()
        provider.tasks = [
            {
                "id": "task-1",
                "title": "Change water filter",
                "notes": write_metadata_to_notes(None, {"context": ["home"]}),
                "status": "needsAction",
            }
        ]
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            handle_task_request(claw, "Complete change water filter")
            handle_task_request(claw, "yes")

        self.assertEqual(provider.completed, ["task-1"])

    def test_run_cli_without_args_starts_interactive_mode(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with patch("claw.FamilyTasksClaw.default", return_value=claw):
            with patch("builtins.input", side_effect=["exit"]):
                output = StringIO()
                with redirect_stdout(output):
                    run_cli([])

        self.assertIn("Family Tasks Claw", output.getvalue())

    def test_run_cli_without_args_can_exit_without_default_provider(self):
        with patch("claw.FamilyTasksClaw.default") as default:
            with patch("builtins.input", side_effect=["exit"]):
                with redirect_stdout(StringIO()):
                    run_cli([])

        default.assert_not_called()

    def test_run_interactive_dispatches_request(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with patch(
            "builtins.input",
            side_effect=["Add task call Rahul during commute", "exit"],
        ):
            with redirect_stdout(StringIO()):
                run_interactive(claw)

        self.assertEqual(provider.created[0]["title"], "Call Rahul")


if __name__ == "__main__":
    unittest.main()

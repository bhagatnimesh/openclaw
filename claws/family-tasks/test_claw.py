import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from claw import (
    FamilyTasksClaw,
    _format_created_task_message,
    _task_notes_and_metadata,
    handle_task_request,
    run_cli,
    run_interactive,
)
from intent import DEFAULT_TIMEZONE, read_metadata_from_notes, write_metadata_to_notes
from noah_assistant import NoahResearchResult, NoahSource


class FakeProvider:
    def __init__(self):
        self.created = []
        self.completed = []
        self.deleted = []
        self.updated = []
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
        updated = None
        for task in self.tasks:
            if task.get("id") != task_id:
                continue
            if title is not None:
                task["title"] = title
            if notes is not None:
                task["notes"] = notes
            if due is not None:
                task["due"] = due
            if status is not None:
                task["status"] = status
            updated = task
            break

        if updated is None:
            updated = {
                "id": task_id,
                "title": title,
                "notes": notes,
                "due": due,
                "status": status or "needsAction",
            }

        self.updated.append(updated)
        return updated

    def complete_task(self, task_id, task_list_id="@default"):
        self.completed.append(task_id)
        return {"id": task_id, "status": "completed"}

    def delete_task(self, task_id, task_list_id="@default"):
        self.deleted.append(task_id)


class FailingProvider(FakeProvider):
    def create_task(self, title, notes=None, due=None, task_list_id="@default"):
        raise RuntimeError("invalid_scope: Bad Request")


class NamedListProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.created_list_ids = []
        self.updated_list_ids = []
        self.deleted_list_ids = []
        self.listed_list_ids = []

    def list_task_lists(self):
        return [
            {"id": "default-id", "title": "My Tasks"},
            {"id": "school-id", "title": "School"},
        ]

    def create_task(self, title, notes=None, due=None, task_list_id="@default"):
        self.created_list_ids.append(task_list_id)
        return super().create_task(title, notes=notes, due=due, task_list_id=task_list_id)

    def list_tasks(self, task_list_id="@default", show_completed=False):
        self.listed_list_ids.append(task_list_id)
        return super().list_tasks(task_list_id=task_list_id, show_completed=show_completed)

    def update_task(
        self,
        task_id,
        title=None,
        notes=None,
        due=None,
        status=None,
        task_list_id="@default",
    ):
        self.updated_list_ids.append(task_list_id)
        return super().update_task(
            task_id,
            title=title,
            notes=notes,
            due=due,
            status=status,
            task_list_id=task_list_id,
        )

    def delete_task(self, task_id, task_list_id="@default"):
        self.deleted_list_ids.append(task_list_id)
        return super().delete_task(task_id, task_list_id=task_list_id)


class FakeFieldExtractor:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def extract(self, request, now=None, baseline_intent=None, context=None):
        self.calls.append({"request": request, "now": now, "baseline": baseline_intent})
        return dict(self.result)


class SequenceFieldExtractor(FakeFieldExtractor):
    def __init__(self, *results):
        super().__init__({})
        self.results = list(results)

    def extract(self, request, now=None, baseline_intent=None, context=None):
        self.calls.append({"request": request, "now": now, "baseline": baseline_intent})
        return dict(self.results.pop(0))


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


class FakeResearchClient:
    def __init__(self, result=None, error=None):
        self.result = result or NoahResearchResult(
            text="FUSD main line is 510-657-2350. Ask about the waitlist status.",
            sources=[
                NoahSource(
                    title="Fremont Unified School District",
                    url="https://www.fremont.k12.ca.us/",
                )
            ],
        )
        self.error = error
        self.calls = []

    def research(self, *, task_title, help_request, assistant_context):
        self.calls.append(
            {
                "task_title": task_title,
                "help_request": help_request,
                "assistant_context": assistant_context,
            }
        )
        if self.error is not None:
            raise self.error
        return self.result


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
        _, metadata = _task_notes_and_metadata(created)
        self.assertEqual(metadata["context"], ["car", "phone"])
        self.assertEqual(metadata["effort_type"], "communication")
        self.assertEqual(metadata["requires"], ["phone"])

    def test_add_task_from_voice_request_creates_multiple_tasks(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        now = datetime(2026, 8, 10, 19, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(
                "Add a task to clean up the car games owner Nimesh. "
                "This is over the weekend. Add another task to configure the "
                "digital lock over the weekend on Saturday, owner Nimesh.",
                reference_time=now,
            )

        self.assertIn("Created 2 tasks:", message)
        self.assertEqual(
            [task["title"] for task in provider.created],
            ["Clean up the car games", "Configure the digital lock"],
        )
        for created in provider.created:
            _, metadata = _task_notes_and_metadata(created)
            self.assertEqual(metadata["owner"], "dad")

    def test_repeated_add_task_commands_create_multiple_fresh_tasks(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        now = datetime(2026, 8, 18, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        request = (
            "Add tasks to complete the school donation contribution. Tag school\n\n"
            "Add task to email Ms. Thompson thanking and then asking about the computer piece. Tag school\n\n"
            " Add task to review presentation and extract out information such as common words, "
            "books, how things will get graded, different techniques which will be taught, "
            "and start and have a plan to internalize it. Tag school"
        )
        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(request, reference_time=now)

        self.assertIn("Created 3 tasks:", message)
        self.assertEqual(
            [task["title"] for task in provider.created],
            [
                "Complete the school donation contribution",
                "Email Ms. Thompson thanking and then asking about the computer piece",
                "Review presentation and extract out information such as common words, books, how things will get graded, different techniques which will be taught, and start and have a plan to internalize it",
            ],
        )
        for created in provider.created:
            _, metadata = _task_notes_and_metadata(created)
            self.assertEqual(metadata["tags"], ["school"])
            self.assertIn("Tags: #school", created["notes"])

    def test_repeated_add_task_commands_preserve_named_lists(self):
        provider = NamedListProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.add_task_from_request(
                "Add task buy milk in School list. "
                "Add task email teacher in School list"
            )

        self.assertEqual(provider.created_list_ids, ["school-id", "school-id"])

    def test_repeated_add_task_commands_survive_collapsed_blank_lines(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(
                "Add tasks to complete the school donation contribution. Tag school\n"
                "Add task to email Ms. Thompson thanking and then asking about the computer piece. Tag school",
            )

        self.assertIn("Created 2 tasks:", message)
        self.assertEqual(
            [task["title"] for task in provider.created],
            [
                "Complete the school donation contribution",
                "Email Ms. Thompson thanking and then asking about the computer piece",
            ],
        )
        for created in provider.created:
            _, metadata = _task_notes_and_metadata(created)
            self.assertEqual(metadata["tags"], ["school"])

    def test_repeated_add_task_commands_split_voice_sentence_boundaries(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(
                "Add task to complete the school donation contribution. "
                "Add task to email Ms. Thompson thanking and then asking about the computer piece.\n\n"
                "Add task to review presentation and extract out information such as common words, "
                "books, how things will get graded, different techniques which will be taught, "
                "and start and have a plan to internalize it.",
            )

        self.assertIn("Created 3 tasks:", message)
        self.assertEqual(
            [task["title"] for task in provider.created],
            [
                "Complete the school donation contribution",
                "Email Ms. Thompson thanking and then asking about the computer piece",
                "Review presentation and extract out information such as common words, books, how things will get graded, different techniques which will be taught, and start and have a plan to internalize it",
            ],
        )

    def test_header_only_plural_add_tasks_line_is_not_created(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(
                "Add tasks:\n"
                "Add task buy milk. Tag errands\n"
                "Add task email teacher. Tag school",
            )

        self.assertNotIn("Some tasks failed:", message)
        self.assertIn("Created 2 tasks:", message)
        self.assertEqual(
            [task["title"] for task in provider.created],
            ["Buy milk", "Email teacher"],
        )

    def test_plural_add_tasks_bullet_header_is_not_one_combined_task(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(
                "Add tasks:\n"
                "- buy milk\n"
                "- email teacher",
            )

        self.assertEqual(provider.created, [])
        self.assertEqual(message, "That does not look like a task creation request.")

    def test_repeated_add_task_commands_keep_different_sentence_tags(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(
                "Add task buy milk. Tag errands\n\n"
                "Add task email teacher. Tag school",
            )

        self.assertIn("Created 2 tasks:", message)
        self.assertEqual(
            [task["title"] for task in provider.created],
            ["Buy milk", "Email teacher"],
        )
        self.assertEqual(
            [
                _task_notes_and_metadata(task)[1]["tags"]
                for task in provider.created
            ],
            [["errands"], ["school"]],
        )

    def test_add_tasks_plural_command_creates_single_task(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request("Add tasks to complete donation and tag school")

        self.assertEqual(message, "Created task: Complete donation (task id: task-123).")
        self.assertEqual([task["title"] for task in provider.created], ["Complete donation"])
        _, metadata = _task_notes_and_metadata(provider.created[0])
        self.assertEqual(metadata["tags"], ["school"])

    def test_repeated_create_split_does_not_split_add_inside_title(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(
                "Add task review presentation and add notes about grading and tag school",
            )

        self.assertEqual(len(provider.created), 1)
        self.assertEqual(
            provider.created[0]["title"],
            "Review presentation and add notes about grading",
        )
        self.assertEqual(
            message,
            "Created task: Review presentation and add notes about grading (task id: task-123).",
        )

    def test_tag_and_label_title_verbs_remain_task_titles(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            first = claw.add_task_from_request("Add task tag clothes")
            second = claw.add_task_from_request("Add task label boxes")
            third = claw.add_task_from_request("Add task organize closet. Label boxes")
            fourth = claw.add_task_from_request("Add task organize closet. Tag clothes")

        self.assertEqual(
            [task["title"] for task in provider.created],
            [
                "Tag clothes",
                "Label boxes",
                "Organize closet. Label boxes",
                "Organize closet. Tag clothes",
            ],
        )
        self.assertEqual(first, "Created task: Tag clothes (task id: task-123).")
        self.assertEqual(second, "Created task: Label boxes (task id: task-123).")
        self.assertEqual(third, "Created task: Organize closet. Label boxes (task id: task-123).")
        self.assertEqual(fourth, "Created task: Organize closet. Tag clothes (task id: task-123).")

    def test_add_task_from_prose_details_uses_readable_title_notes_and_reply(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        now = datetime(2026, 8, 9, 10, 20, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(
                "Add a task for Monday. Visit Nysha's school. "
                "Details add driver, check on school supplies and things to get, "
                "other dogs and donuts for the first day. Time 9 am\n"
                "Owner: dad",
                reference_time=now,
            )

        self.assertEqual(
            message,
            "Created task: Visit Nysha's school\n"
            "Due: Mon, Aug 10\n"
            "Details: Add driver, check on school supplies and things to get, "
            "other dogs and donuts for the first day.\n"
            "Owner: dad\n"
            "Task id: task-123",
        )
        created = provider.created[0]
        self.assertEqual(created["title"], "Visit Nysha's school")
        human_notes, metadata = read_metadata_from_notes(created["notes"])
        self.assertEqual(
            human_notes,
            "Add driver, check on school supplies and things to get, "
            "other dogs and donuts for the first day.",
        )
        self.assertEqual(created["due"], "2026-08-10")
        self.assertEqual(metadata["owner"], "dad")

    def test_add_task_from_refined_header_and_body(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(
                "\n".join(
                    [
                        "Add task: Call FUSD about Nysha waitlist",
                        "Notes: Follow up with Chadbourne about the overflow waitlist.",
                        "Ask for the right contact and next steps.",
                    ]
                ),
            )

        self.assertIn("Created task: Call FUSD about Nysha waitlist", message)
        created = provider.created[0]
        self.assertEqual(created["title"], "Call FUSD about Nysha waitlist")
        self.assertEqual(
            created["notes"],
            "Follow up with Chadbourne about the overflow waitlist.\n"
            "Ask for the right contact and next steps.",
        )

    def test_multiline_notes_starting_with_add_task_do_not_split(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(
                "\n".join(
                    [
                        "Add task: Call FUSD about Nysha waitlist",
                        "Notes:",
                        "Add task should include overflow status.",
                        "Ask for the right contact and next steps.",
                    ]
                ),
            )

        self.assertEqual(len(provider.created), 1)
        self.assertIn("Created task: Call FUSD about Nysha waitlist", message)
        self.assertEqual(provider.created[0]["title"], "Call FUSD about Nysha waitlist")
        self.assertEqual(
            provider.created[0]["notes"],
            "Add task should include overflow status.\n"
            "Ask for the right contact and next steps.",
        )

    def test_add_task_from_request_persists_visible_tags(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        now = datetime(2026, 7, 7, 9, 31, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(
                "Add task buy new water filter #shopping #home",
                reference_time=now,
            )

        self.assertEqual(
            message,
            "Created task: Buy new water filter (task id: task-123).",
        )
        created = provider.created[0]
        self.assertEqual(created["title"], "Buy new water filter")
        human_notes, _ = read_metadata_from_notes(created["notes"])
        self.assertEqual(human_notes, "Tags: #shopping #home")
        _, metadata = _task_notes_and_metadata(created)
        self.assertEqual(metadata["tags"], ["shopping", "home"])

    def test_add_task_from_image_entries_creates_individual_tasks(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        now = datetime(2026, 7, 21, 20, 39, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(
                "\n".join(
                    [
                        "Create a task for every entry in the image with due date august first "
                        "and tag IndiaTrip",
                        "",
                        "Image text:",
                        "List title: India trip",
                        "Check letters",
                        "Clarify this year taxes paper work with davendera",
                        "Meet Bandish",
                    ]
                ),
                reference_time=now,
            )

        self.assertEqual(message, "Created 3 tasks from the image.")
        self.assertEqual(
            [task["title"] for task in provider.created],
            [
                "Check letters",
                "Clarify this year taxes paper work with davendera",
                "Meet Bandish",
            ],
        )
        self.assertEqual([task["due"] for task in provider.created], ["2026-08-01"] * 3)
        for created in provider.created:
            human_notes, _ = read_metadata_from_notes(created["notes"])
            self.assertEqual(human_notes, "Tags: #indiatrip")
            _, metadata = _task_notes_and_metadata(created)
            self.assertEqual(metadata["tags"], ["indiatrip"])

    def test_image_entries_starting_with_add_task_stay_bulk_image_tasks(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        now = datetime(2026, 7, 21, 20, 39, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(
                "\n".join(
                    [
                        "Create tasks from every entry in the image with due date august first "
                        "and tag school",
                        "",
                        "Image text:",
                        "Add task to email teacher",
                        "Add task to buy supplies",
                    ]
                ),
                reference_time=now,
            )

        self.assertEqual(message, "Created 2 tasks from the image.")
        self.assertEqual(
            [task["title"] for task in provider.created],
            ["Add task to email teacher", "Add task to buy supplies"],
        )
        self.assertEqual([task["due"] for task in provider.created], ["2026-08-01"] * 2)
        for created in provider.created:
            _, metadata = _task_notes_and_metadata(created)
            self.assertEqual(metadata["tags"], ["school"])

    def test_bulk_image_default_path_preserves_named_task_list(self):
        provider = NamedListProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        request = (
            "Add every item in this image as a task in School list\n"
            "Image text:\n- Buy milk\n- Email teacher"
        )
        semantic_intent = {
            "intent": "create_task",
            "task_list_name": "School",
            "metadata": {},
            "missing_fields": [],
        }

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(
                request,
                semantic_intent=semantic_intent,
            )

        self.assertEqual(message, "Created 2 tasks from the image.")
        self.assertEqual(provider.created_list_ids, ["school-id", "school-id"])

    def test_bulk_image_shared_fields_do_not_come_from_first_title(self):
        claw = FamilyTasksClaw.from_provider(FakeProvider())
        request = (
            "Add every item in this image as a task\n"
            "Image text:\n- Pay rent tomorrow\n- Book dentist"
        )

        intents = claw._bulk_image_task_intents(
            request,
            ["Pay rent tomorrow", "Book dentist"],
            datetime(2026, 8, 21, 12, tzinfo=ZoneInfo(DEFAULT_TIMEZONE)),
        )

        self.assertEqual([intent["due"] for intent in intents], [None, None])

    def test_add_task_from_image_entries_uses_list_title_as_fallback_tag(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        now = datetime(2026, 7, 21, 20, 39, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(
                "\n".join(
                    [
                        "Create a task for every entry in the image with due date august first",
                        "",
                        "Image text:",
                        "List title: India trip",
                        "Check letters",
                        "Bank locker",
                    ]
                ),
                reference_time=now,
            )

        self.assertEqual(message, "Created 2 tasks from the image.")
        self.assertEqual([task["due"] for task in provider.created], ["2026-08-01"] * 2)
        for created in provider.created:
            human_notes, _ = read_metadata_from_notes(created["notes"])
            self.assertEqual(human_notes, "Tags: #indiatrip")
            _, metadata = _task_notes_and_metadata(created)
            self.assertEqual(metadata["tags"], ["indiatrip"])

    def test_add_task_from_voice_chatter_creates_clean_google_task(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        now = datetime(2026, 7, 5, 22, 4, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(
                "Add a task for tomorrow at 2pm to call up home warranty to check "
                "how to handle with the solar panel, challenge, assign the task to Namesh",
                reference_time=now,
            )

        self.assertEqual(
            message,
            "Created task: Call home warranty about the solar panel (task id: task-123).",
        )
        created = provider.created[0]
        self.assertEqual(created["title"], "Call home warranty about the solar panel")
        human_notes, metadata = read_metadata_from_notes(created["notes"])
        self.assertEqual(human_notes, "")
        self.assertEqual(created["due"], "2026-07-06")
        self.assertEqual(metadata["owner"], "dad")

    def test_add_task_from_request_stores_ai_assistant_help(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        claw.auto_run_assistant_help = False
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(
                "\n".join(
                    [
                        "call FUSD for following up on Nysha's waitlist status for Chadbourne",
                        "I want AI assistant",
                        "Help: look up the FUSD phone number and draft quick talking points",
                        "Email: waitlist@example.com",
                    ]
                ),
                reference_time=now,
            )

        self.assertIn(
            "Created task: Call FUSD for following up on Nysha's waitlist status for Chadbourne (task id: task-123).",
            message,
        )
        self.assertIn(
            "Noah queued: On your behalf, Noah should look up the FUSD phone number and draft quick talking points. Say 'Run Noah assistant help' to run queued help.",
            message,
        )
        created = provider.created[0]
        notes, metadata = _task_notes_and_metadata(created)
        self.assertIn("Assistant help: Look up the FUSD phone number", notes)
        self.assertIn("Assistant context: Email: waitlist@example.com", notes)
        self.assertTrue(metadata["assistant_help_needed"])
        self.assertEqual(metadata["assistant_name"], "Noah")
        self.assertEqual(
            metadata["assistant_help_request"],
            "Look up the FUSD phone number and draft quick talking points",
        )
        self.assertEqual(
            metadata["assistant_context"],
            "Email: waitlist@example.com",
        )

    def test_add_task_from_polite_timed_request_creates_task(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        claw.auto_run_assistant_help = False
        now = datetime(2026, 7, 3, 21, 56, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(
                "\n".join(
                    [
                        "I want to add a task for Monday at 2 p.m. to call FUSD "
                        "to follow up on Nyshas School waiting",
                        "list for Chad Bond. This task is for Namesh. "
                        "I want AI assistant to find out FUSD number to call",
                        "and the key talking points. I really want",
                        "Nyshad to meet Chad Bond from overflow",
                        "on ASS School to Mission Valley Monteserie.",
                    ]
                ),
                reference_time=now,
            )

        self.assertIn(
            "Created task: Call FUSD to follow up on Nyshas School waiting list for Chad Bond (task id: task-123).",
            message,
        )
        self.assertIn(
            "Noah queued: On your behalf, Noah should find out FUSD number to call and the key talking points. Say 'Run Noah assistant help' to run queued help.",
            message,
        )
        self.assertNotIn("I really want", message)
        created = provider.created[0]
        notes, metadata = _task_notes_and_metadata(created)
        self.assertEqual(
            created["title"],
            "Call FUSD to follow up on Nyshas School waiting list for Chad Bond",
        )
        self.assertEqual(created["due"], "2026-07-06")
        self.assertIn("Assistant help: Find out FUSD number", notes)
        self.assertTrue(metadata["assistant_help_needed"])
        self.assertIn(
            "Find out FUSD number to call",
            metadata["assistant_help_request"],
        )
        self.assertIn("Nyshad to meet Chad Bond", metadata["assistant_context"])

    def test_add_task_from_noah_request_stores_assistant_metadata(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        claw.auto_run_assistant_help = False
        now = datetime(2026, 7, 3, 22, 6, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(
                "\n".join(
                    [
                        "I want to add a task for Monday at 2 p.m. to call FUSD "
                        "to follow up on Nyshas School waiting",
                        "list for Chad Bond. This task is for Namesh. "
                        "I want Noah to find out FUSD number to call",
                        "and the key talking points. I really want",
                        "Nyshad to meet Chad Bond from overflow",
                        "on ASS School to Mission Valley Monteserie",
                    ]
                ),
                reference_time=now,
            )

        self.assertIn(
            "Created task: Call FUSD to follow up on Nyshas School waiting list for Chad Bond (task id: task-123).",
            message,
        )
        self.assertIn(
            "Noah queued: On your behalf, Noah should find out FUSD number to call and the key talking points. Say 'Run Noah assistant help' to run queued help.",
            message,
        )
        self.assertNotIn("I really want", message)
        created = provider.created[0]
        notes, metadata = _task_notes_and_metadata(created)
        self.assertEqual(
            created["title"],
            "Call FUSD to follow up on Nyshas School waiting list for Chad Bond",
        )
        self.assertEqual(created["due"], "2026-07-06")
        self.assertIn("Assistant help: Find out FUSD number", notes)
        self.assertTrue(metadata["assistant_help_needed"])
        self.assertEqual(metadata["assistant_name"], "Noah")
        self.assertIn(
            "Find out FUSD number to call",
            metadata["assistant_help_request"],
        )
        self.assertIn("Nyshad to meet Chad Bond", metadata["assistant_context"])

    def test_add_task_from_noah_request_runs_assistant_help_immediately(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        now = datetime(2026, 7, 3, 22, 6, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        client = FakeResearchClient()

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(
                "Call FUSD to get Nysha waiting list number. "
                "I want Noah to help me get FUSD number for the question",
                reference_time=now,
                research_client=client,
            )

        self.assertIn(
            "Created task: Call FUSD to get Nysha waiting list number (task id: task-123).",
            message,
        )
        self.assertIn(
            "Noah completed assistant help for Call FUSD to get Nysha waiting list number: "
            "FUSD main line is 510-657-2350.",
            message,
        )
        self.assertIn("Saved in task notes.", message)
        self.assertEqual(
            client.calls,
            [
                {
                    "task_title": "Call FUSD to get Nysha waiting list number",
                    "help_request": "Help me get FUSD number for the question",
                    "assistant_context": "",
                }
            ],
        )
        self.assertEqual(provider.updated[0]["id"], "task-123")
        notes, metadata = _task_notes_and_metadata(provider.updated[0])
        self.assertIn("Noah result (2026-07-03 22:06 PDT):", notes)
        self.assertIn("FUSD main line is 510-657-2350", notes)
        self.assertFalse(metadata["assistant_help_needed"])
        self.assertEqual(metadata["assistant_help_status"], "completed")
        self.assertIn("510-657-2350", metadata["assistant_help_result_summary"])

    def test_run_noah_assistant_help_writes_result_to_task(self):
        provider = FakeProvider()
        provider.tasks = [
            _task(
                "Call FUSD about Chadbourne waitlist",
                {
                    "assistant_help_needed": True,
                    "assistant_name": "Noah",
                    "assistant_help_request": (
                        "Find out FUSD number to call and the key talking points"
                    ),
                    "assistant_context": "Nysha is on the Chadbourne waitlist.",
                    "effort_type": "communication",
                },
            )
        ]
        claw = FamilyTasksClaw.from_provider(provider)
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        client = FakeResearchClient()

        with redirect_stdout(StringIO()):
            message = claw.run_noah_assistant_help(
                research_client=client,
                reference_time=now,
            )

        self.assertIn("Noah completed 1 assistant help task", message)
        self.assertEqual(
            client.calls,
            [
                {
                    "task_title": "Call FUSD about Chadbourne waitlist",
                    "help_request": "Find out FUSD number to call and the key talking points",
                    "assistant_context": "Nysha is on the Chadbourne waitlist.",
                }
            ],
        )
        notes, metadata = _task_notes_and_metadata(provider.tasks[0])
        self.assertIn("Noah result (2026-07-03 09:00 PDT):", notes)
        self.assertIn("FUSD main line is 510-657-2350", notes)
        self.assertIn("Sources:", notes)
        self.assertIn("https://www.fremont.k12.ca.us/", notes)
        self.assertFalse(metadata["assistant_help_needed"])
        self.assertEqual(metadata["assistant_help_status"], "completed")
        self.assertEqual(
            metadata["assistant_help_completed_at"],
            "2026-07-03T09:00:00-07:00",
        )
        self.assertIn("510-657-2350", metadata["assistant_help_result_summary"])
        self.assertEqual(
            metadata["assistant_help_result_sources"],
            [
                {
                    "title": "Fremont Unified School District",
                    "url": "https://www.fremont.k12.ca.us/",
                }
            ],
        )

    def test_run_noah_assistant_help_records_retryable_error(self):
        provider = FakeProvider()
        provider.tasks = [
            _task(
                "Research school transfer window",
                {
                    "assistant_help_needed": True,
                    "assistant_name": "Noah",
                    "assistant_help_request": "Find the transfer window.",
                },
            )
        ]
        claw = FamilyTasksClaw.from_provider(provider)
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        client = FakeResearchClient(error=RuntimeError("temporary OpenAI error"))

        with redirect_stdout(StringIO()):
            message = claw.run_noah_assistant_help(
                research_client=client,
                reference_time=now,
            )

        self.assertIn("Noah could not complete 1 assistant help task", message)
        _, metadata = _task_notes_and_metadata(provider.tasks[0])
        self.assertTrue(metadata["assistant_help_needed"])
        self.assertEqual(metadata["assistant_help_status"], "error")
        self.assertEqual(
            metadata["assistant_help_last_attempt_at"],
            "2026-07-03T09:00:00-07:00",
        )
        self.assertIn("temporary OpenAI error", metadata["assistant_help_error"])

    def test_run_noah_assistant_help_skips_when_no_pending_tasks(self):
        provider = FakeProvider()
        provider.tasks = [
            _task(
                "Call FUSD about Chadbourne waitlist",
                {
                    "assistant_help_needed": False,
                    "assistant_help_status": "completed",
                },
            )
        ]
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            message = claw.run_noah_assistant_help(research_client=FakeResearchClient())

        self.assertEqual(message, "No pending Noah assistant help tasks found.")
        self.assertEqual(provider.updated, [])

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

    def test_direct_recommendation_uses_named_task_list(self):
        provider = NamedListProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.recommend_tasks_from_request("Show School tasks")

        self.assertEqual(provider.listed_list_ids, ["school-id"])

    def test_recommend_tasks_from_request_filters_by_visible_tag(self):
        provider = FakeProvider()
        provider.tasks = [
            {
                "id": "task-1",
                "title": "Buy water filter",
                "notes": "Tags: #shopping #home",
                "status": "needsAction",
            },
            {
                "id": "task-2",
                "title": "Pay utility bill",
                "notes": "Tags: #finance #home",
                "status": "needsAction",
            },
        ]
        claw = FamilyTasksClaw.from_provider(provider)
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.recommend_tasks_from_request(
                "Show me #shopping tasks",
                reference_time=now,
            )

        self.assertIn("Buy water filter", message)
        self.assertIn("tagged shopping", message)
        self.assertNotIn("Pay utility bill", message)

    def test_recommend_tasks_from_request_filters_by_tag_phrase(self):
        provider = FakeProvider()
        provider.tasks = [
            {
                "id": "task-1",
                "title": "Look at diversification on jul 21st",
                "notes": "Tags: #finance",
                "status": "needsAction",
            },
            {
                "id": "task-2",
                "title": "Buy water filter",
                "notes": "Tags: #shopping",
                "status": "needsAction",
            },
        ]
        claw = FamilyTasksClaw.from_provider(provider)
        now = datetime(2026, 7, 7, 11, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.recommend_tasks_from_request(
                "list all tasks with tag finance",
                reference_time=now,
            )

        self.assertIn("Look at diversification on jul 21st", message)
        self.assertIn("tagged finance", message)
        self.assertNotIn("Buy water filter", message)

    def test_recommend_tasks_from_request_filters_by_owner_phrase(self):
        provider = FakeProvider()
        provider.tasks = [
            _task(
                "Pack bag for day 1",
                {"owner": "nysha"},
                due="2026-08-10T00:00:00.000Z",
            ),
            _task(
                "Prepare Nysha's and Navya's bag for first day of school",
                {"owner": "navya"},
                due="2026-08-09T00:00:00.000Z",
            ),
        ]
        claw = FamilyTasksClaw.from_provider(provider)
        now = datetime(2026, 8, 10, 19, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.recommend_tasks_from_request(
                "List tasks with owner Nysha",
                reference_time=now,
            )

        self.assertIn("Pack bag for day 1", message)
        self.assertIn("owned by nysha", message)
        self.assertNotIn("Prepare Nysha's and Navya's bag", message)

    def test_recommend_tasks_from_request_filters_by_owner_task_phrase(self):
        provider = FakeProvider()
        provider.tasks = [
            _task("Pack bag for day 1", {"owner": "nysha"}),
            _task(
                "Prepare Nysha's and Navya's bag for first day of school",
                {"owner": "navya"},
            ),
        ]
        claw = FamilyTasksClaw.from_provider(provider)
        now = datetime(2026, 8, 10, 19, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.recommend_tasks_from_request(
                "list Nysha tasks",
                reference_time=now,
            )

        self.assertIn("Pack bag for day 1", message)
        self.assertNotIn("Prepare Nysha's and Navya's bag", message)

    def test_recommend_tasks_for_drive_filters_visible_drive_tag(self):
        provider = FakeProvider()
        provider.tasks = [
            {
                "id": "task-1",
                "title": "Return library book",
                "notes": "Tags: #drive",
                "status": "needsAction",
            },
            {
                "id": "task-2",
                "title": "Return amazon",
                "notes": "Tags: #drive",
                "status": "needsAction",
            },
            {
                "id": "task-3",
                "title": "Call home warranty",
                "notes": write_metadata_to_notes(
                    None,
                    {
                        "context": ["phone"],
                        "can_do_while": ["driving"],
                        "effort_type": "communication",
                        "requires": ["phone"],
                    },
                ),
                "status": "needsAction",
            },
        ]
        claw = FamilyTasksClaw.from_provider(provider)
        now = datetime(2026, 7, 7, 12, 1, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.recommend_tasks_from_request(
                "list all tasks for drive",
                reference_time=now,
            )

        self.assertIn("Return library book", message)
        self.assertIn("Return amazon", message)
        self.assertIn("tagged drive", message)
        self.assertNotIn("Call home warranty", message)

    def test_task_list_request_uses_matching_heading(self):
        provider = FakeProvider()
        provider.tasks = [
            _task("Return water filter", {}, due="2026-07-04T00:00:00.000Z"),
            _task("Change furnace filter", {}, due="2026-07-05T00:00:00.000Z"),
        ]
        claw = FamilyTasksClaw.from_provider(provider)
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.recommend_tasks_from_request(
                "Give me list of tasks tomorrow",
                reference_time=now,
            )

        self.assertTrue(message.startswith("Matching tasks:"))
        self.assertIn("Return water filter", message)
        self.assertNotIn("Change furnace filter", message)

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

    def test_handle_task_request_uses_named_list(self):
        provider = NamedListProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            handle_task_request(claw, "Add task buy milk in School list")

        self.assertEqual(provider.created_list_ids, ["school-id"])

    def test_undo_reverts_created_task(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.add_task_from_request("Add task change water filter")
            message = claw.undo_last_action()

        self.assertIn("Undid task creation", message)
        self.assertEqual(provider.deleted, ["task-123"])

    def test_assign_owner_followup_updates_last_created_task(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.add_task_from_request("Add task add a phone screen to my phone tomorrow")
            provider.tasks = [provider.created[-1]]
            message = claw.assign_owner_from_request("assign it to nimesh")

        self.assertIn("Updated task (owner=dad)", message)
        _, metadata = _task_notes_and_metadata(provider.updated[-1])
        self.assertEqual(metadata["owner"], "dad")

    def test_assign_owner_by_title_updates_matching_task(self):
        provider = FakeProvider()
        provider.tasks = [
            _task("Add a phone screen to my phone", {"owner": "unknown"}),
        ]
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            message = claw.assign_owner_from_request(
                "Assign Add a phone screen to my phone task to nimesh",
            )

        self.assertIn("Updated task (owner=dad)", message)
        _, metadata = _task_notes_and_metadata(provider.updated[-1])
        self.assertEqual(metadata["owner"], "dad")

    def test_assign_owner_by_ambiguous_title_updates_selected_task(self):
        provider = FakeProvider()
        provider.tasks = [
            _task("Return amazon", {"owner": "unknown"}, due="2026-07-07"),
            _task("Return library book", {"owner": "unknown"}, due="2026-07-07"),
        ]
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            first = claw.assign_owner_from_request("assign return library to nimesh")
            second_choice = claw.pending_action.choices[1]
            handled = claw.handle_pending_response("2")

        self.assertIn("Multiple matching tasks found", first)
        self.assertTrue(handled)
        self.assertEqual(provider.updated[-1]["id"], second_choice["id"])
        _, metadata = _task_notes_and_metadata(provider.updated[-1])
        self.assertEqual(metadata["owner"], "dad")

    def test_update_task_owner_accepts_non_assign_wording(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.add_task_from_request("Add task add a phone screen to my phone tomorrow")
            first = claw.update_task_from_request("owner is nimesh")
            second = claw.update_task_from_request("make it for niyati")

        self.assertIn("Updated task (owner=dad)", first)
        self.assertIn("Updated task (owner=mom)", second)
        _, metadata = _task_notes_and_metadata(provider.updated[-1])
        self.assertEqual(metadata["owner"], "mom")

    def test_update_task_owner_accepts_owner_of_task_wording_for_child(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.add_task_from_request("Add task pack bag for day 1")
            message = claw.update_task_from_request(
                "Change the owner of the task to Nysha",
            )

        self.assertIn("Updated task (owner=nysha)", message)
        _, metadata = _task_notes_and_metadata(provider.updated[-1])
        self.assertEqual(metadata["owner"], "nysha")

    def test_update_task_followup_appends_note_to_last_created_task(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.add_task_from_request("Add task add a phone screen to my phone tomorrow")
            message = claw.update_task_from_request(
                "add note warranty expires next week",
            )

        self.assertIn("Updated task (note)", message)
        notes, _ = read_metadata_from_notes(provider.updated[-1]["notes"])
        self.assertIn("warranty expires next week", notes)

    def test_update_task_followup_adds_dynamic_tag_to_last_created_task(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.add_task_from_request("Add task buy new water filter #home")
            message = claw.update_task_from_request("add #cleanup")

        self.assertIn("Updated task (tags=#cleanup)", message)
        updated = provider.updated[-1]
        self.assertEqual(updated["notes"], "Tags: #home #cleanup")
        _, metadata = _task_notes_and_metadata(updated)
        self.assertEqual(metadata["tags"], ["home", "cleanup"])

    def test_update_task_with_tags_phrase_updates_last_created_task(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.add_task_from_request("Add task research India trip restaurants")
            message = claw.update_task_from_request(
                "Update the task with tags #commute #india",
            )

        self.assertIn("Updated task (tags=#commute #india)", message)
        updated = provider.updated[-1]
        self.assertEqual(updated["notes"], "Tags: #commute #india")
        _, metadata = _task_notes_and_metadata(updated)
        self.assertEqual(metadata["tags"], ["commute", "india"])

    def test_update_task_followup_queues_noah_help_on_last_created_task(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.add_task_from_request("Add task add a phone screen to my phone tomorrow")
            message = claw.update_task_from_request(
                "add Noah to help me find the right phone screen",
            )

        self.assertIn("Updated task (Noah help)", message)
        _, metadata = _task_notes_and_metadata(provider.updated[-1])
        self.assertTrue(metadata["assistant_help_needed"])
        self.assertEqual(metadata["assistant_name"], "Noah")
        self.assertEqual(
            metadata["assistant_help_request"],
            "find the right phone screen",
        )

    def test_undo_reverts_completed_task(self):
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
            claw.complete_task_from_request("Complete change water filter")
            claw.handle_pending_response("yes")
            message = claw.undo_last_action()

        self.assertIn("Undid task completion", message)
        self.assertEqual(provider.updated[-1]["status"], "needsAction")

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

    def test_ai_create_resolves_named_task_list_and_previews_voice_write(self):
        provider = NamedListProvider()
        extractor = FakeFieldExtractor(
            {
                "intent": "create_task",
                "title": "Submit field trip form",
                "notes": None,
                "due": "2026-08-24",
                "metadata": {"owner": "dad", "tags": ["school"]},
                "task_list_name": "School",
                "task_list_id_hint": None,
                "missing_fields": [],
                "assumptions": ["voice_transcript", "inferred_due_date"],
                "clarification_question": None,
                "ai_field_extraction": {"confidence": 0.96},
            }
        )
        claw = FamilyTasksClaw.from_provider(provider)
        claw.field_extractor = extractor
        claw.auto_run_assistant_help = False

        with redirect_stdout(StringIO()):
            preview = claw.add_task_from_request(
                "put the field trip thing in school for next Monday",
                reference_time=datetime(2026, 8, 21, 10, tzinfo=ZoneInfo(DEFAULT_TIMEZONE)),
                require_confirmation=True,
            )

        self.assertIn("Submit field trip form", preview)
        self.assertIn("due 2026-08-24", preview)
        self.assertIn("School", preview)
        self.assertEqual(provider.created, [])

        with redirect_stdout(StringIO()):
            self.assertTrue(claw.handle_pending_response("yes"))

        self.assertEqual(provider.created[0]["title"], "Submit field trip form")
        self.assertEqual(provider.created_list_ids, ["school-id"])

    def test_named_list_create_runs_noah_update_in_same_list(self):
        provider = NamedListProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        intent = {
            "intent": "create_task",
            "title": "Compare camps",
            "due": None,
            "metadata": {
                "assistant_help_needed": True,
                "assistant_name": "Noah",
                "assistant_help_request": "Compare camp options",
            },
            "task_list_name": "School",
            "missing_fields": [],
        }

        with redirect_stdout(StringIO()):
            claw.add_task_from_request(
                "Add task compare camps in School list",
                semantic_intent=intent,
                research_client=FakeResearchClient(),
            )

        self.assertEqual(provider.created_list_ids, ["school-id"])
        self.assertEqual(provider.updated_list_ids, ["school-id"])

    def test_named_list_create_keeps_list_for_targetless_update(self):
        provider = NamedListProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.add_task_from_request("Add task buy milk in School list")
            message = claw.update_task_from_request("owner is nimesh")

        self.assertIn("Updated task", message)
        self.assertEqual(provider.updated_list_ids, ["school-id"])

    def test_named_list_create_keeps_list_for_pronoun_delete(self):
        provider = NamedListProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.add_task_from_request("Add task buy milk in School list")
            provider.tasks = [provider.created[-1]]
            claw.delete_task_from_request("delete it")
            handled = claw.handle_pending_response("yes")

        self.assertTrue(handled)
        self.assertEqual(provider.listed_list_ids, ["school-id"])
        self.assertEqual(provider.deleted_list_ids, ["school-id"])

    def test_local_create_resolves_named_task_list_without_ai(self):
        provider = NamedListProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request("Add task buy milk in School list")

        self.assertIn("Buy milk", message)
        self.assertEqual(provider.created[0]["title"], "Buy milk")
        self.assertEqual(provider.created_list_ids, ["school-id"])

    def test_local_create_does_not_treat_to_school_as_a_list(self):
        provider = NamedListProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.add_task_from_request("Add task return folder to School")

        self.assertEqual(provider.created[0]["title"], "Return folder to School")
        self.assertEqual(provider.created_list_ids, ["@default"])

    def test_local_list_request_preserves_named_task_list_without_ai(self):
        claw = FamilyTasksClaw.from_provider(NamedListProvider())

        intent = claw.interpret_request("Show School tasks")

        self.assertEqual(intent["intent"], "recommend_tasks")
        self.assertEqual(intent["task_list_name"], "School")

    def test_explicit_local_task_list_overrides_ai_guess(self):
        claw = FamilyTasksClaw.from_provider(NamedListProvider())
        claw.field_extractor = FakeFieldExtractor(
            {
                "intent": "recommend_tasks",
                "task_list_name": "Personal",
                "task_list_id_hint": "wrong-id",
                "filters": {},
                "missing_fields": [],
            }
        )

        intent = claw.interpret_request("Show School tasks")

        self.assertEqual(intent["task_list_name"], "School")
        self.assertIsNone(intent["task_list_id_hint"])

    def test_task_list_resolution_accepts_list_suffix(self):
        claw = FamilyTasksClaw.from_provider(NamedListProvider())

        task_list_id, error = claw._resolve_task_list({"task_list_name": "School list"})

        self.assertIsNone(error)
        self.assertEqual(task_list_id, "school-id")

    def test_ai_default_task_list_hint_uses_default_alias(self):
        provider = NamedListProvider()
        extractor = FakeFieldExtractor(
            {
                "intent": "create_task",
                "title": "Add sticker to the Sienna license",
                "due": "2026-09-19",
                "task_list_id_hint": "@default",
                "metadata": {"owner": "unknown"},
                "missing_fields": [],
            }
        )
        claw = FamilyTasksClaw.from_provider(provider)
        claw.field_extractor = extractor

        with redirect_stdout(StringIO()):
            message = claw.add_task_from_request(
                "/task add sticker to the sienna license time: after 4 Saturdays"
            )

        self.assertIn("Created task", message)
        self.assertEqual(provider.created[0]["title"], "Add sticker to the Sienna license")
        self.assertEqual(provider.created_list_ids, ["@default"])

    def test_ai_create_preserves_default_owner_annotation(self):
        provider = FakeProvider()
        extractor = FakeFieldExtractor(
            {
                "intent": "create_task",
                "title": "Buy milk",
                "due": None,
                "metadata": {"owner": "unknown"},
                "missing_fields": [],
            }
        )
        claw = FamilyTasksClaw.from_provider(provider)
        claw.field_extractor = extractor

        with redirect_stdout(StringIO()):
            claw.add_task_from_request("Add task buy milk\nOwner: dad")

        _, metadata = read_metadata_from_notes(provider.created[0]["notes"])
        self.assertEqual(metadata["owner"], "dad")

    def test_ai_create_preserves_local_tags_and_assistant_help(self):
        extractor = FakeFieldExtractor(
            {
                "intent": "create_task",
                "title": "Research trips",
                "due": None,
                "metadata": {
                    "tags": [],
                    "context": [],
                    "requires": [],
                    "can_do_while": [],
                    "owner": "unknown",
                    "assistant_help_needed": False,
                },
                "missing_fields": [],
            }
        )
        claw = FamilyTasksClaw.from_provider(FakeProvider())
        claw.field_extractor = extractor

        intent = claw._extract_intent(
            "Add task research trips #school\nAsk Noah to help compare options",
            None,
        )

        self.assertIn("school", intent["metadata"]["tags"])
        self.assertTrue(intent["metadata"]["assistant_help_needed"])

    def test_create_clarification_retains_draft_until_preview_confirmation(self):
        missing = {
            "intent": "create_task",
            "title": None,
            "due": "2026-08-24",
            "task_list_name": "School",
            "metadata": {"owner": "dad", "tags": ["school"]},
            "missing_fields": ["title"],
            "clarification_question": "What task should I add?",
        }
        complete = {
            "intent": "create_task",
            "title": "Buy milk",
            "due": "2026-09-01",
            "task_list_name": "My Tasks",
            "metadata": {"owner": "mom"},
            "missing_fields": [],
        }
        provider = NamedListProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        claw.field_extractor = SequenceFieldExtractor(missing, complete)

        with redirect_stdout(StringIO()):
            claw.add_task_from_request("add a task", require_confirmation=True)
            self.assertEqual(claw.pending_action.action, "clarify_create")
            claw.handle_pending_response("Buy milk Sep 1 in My Tasks list for mom")

        self.assertEqual(provider.created, [])
        self.assertEqual(claw.pending_action.action, "confirm_create")
        draft = claw.pending_action.create_intents[0]
        self.assertEqual(draft["due"], "2026-09-01")
        self.assertEqual(draft["task_list_name"], "My Tasks")
        self.assertEqual(draft["metadata"]["owner"], "mom")

        with redirect_stdout(StringIO()):
            claw.handle_pending_response("yes")

        self.assertEqual(provider.created[0]["title"], "Buy milk")
        self.assertEqual(provider.created_list_ids, ["default-id"])

    def test_bulk_image_preview_keeps_router_semantic_shared_fields(self):
        provider = NamedListProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        request = "Add every item in this image as a task\nImage text:\n- Buy milk\n- Call dentist"
        semantic_intent = {
            "intent": "create_task",
            "title": "Checklist item",
            "due": "2026-08-24",
            "task_list_name": "School",
            "metadata": {"owner": "dad", "tags": ["errands"]},
            "missing_fields": [],
        }

        with redirect_stdout(StringIO()):
            claw.add_task_from_request(
                request,
                require_confirmation=True,
                semantic_intent=semantic_intent,
            )

        intents = claw.pending_action.create_intents
        self.assertEqual([item["title"] for item in intents], ["Buy milk", "Call dentist"])
        self.assertEqual([item["due"] for item in intents], ["2026-08-24"] * 2)
        self.assertEqual([item["task_list_name"] for item in intents], ["School"] * 2)
        self.assertTrue(all(item["metadata"]["owner"] == "dad" for item in intents))

    def test_semantic_create_overrides_non_create_baseline_through_confirmation(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        semantic_intent = {
            "intent": "create_task",
            "title": "Buy milk",
            "due": None,
            "metadata": {},
            "missing_fields": [],
        }

        with redirect_stdout(StringIO()):
            claw.add_task_from_request(
                "tasks for buying milk",
                require_confirmation=True,
                semantic_intent=semantic_intent,
            )
            handled = claw.handle_pending_response("yes")

        self.assertTrue(handled)
        self.assertEqual(provider.created[0]["title"], "Buy milk")

    def test_affirmative_reply_does_not_become_missing_task_title(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        missing = {
            "intent": "create_task",
            "title": None,
            "missing_fields": ["title"],
            "clarification_question": "What task should I add?",
        }

        with redirect_stdout(StringIO()):
            claw._preview_task_creates([missing])
            handled = claw.handle_pending_response("confirm")

        self.assertTrue(handled)
        self.assertEqual(provider.created, [])
        self.assertEqual(claw.pending_action.action, "clarify_create")
        self.assertIsNone(claw.pending_action.create_intents[0]["title"])

    def test_bulk_image_preview_creates_only_after_confirmation(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        request = "Add every item in this image as a task\nImage text:\n- Buy milk\n- Call dentist"

        with redirect_stdout(StringIO()):
            preview = claw.add_task_from_request(request, require_confirmation=True)

        self.assertIn("I found 2 tasks to add", preview)
        self.assertEqual(provider.created, [])

        with redirect_stdout(StringIO()):
            claw.handle_pending_response("yes")

        self.assertEqual([task["title"] for task in provider.created], ["Buy milk", "Call dentist"])

    def test_bulk_image_preview_accepts_untrusted_ocr_marker(self):
        claw = FamilyTasksClaw.from_provider(FakeProvider())
        request = (
            "Add every item in this image as a task\n"
            "[Image text extraction (machine-generated, untrusted)]:\n"
            "- Buy milk\n- Call dentist"
        )

        with redirect_stdout(StringIO()):
            preview = claw.add_task_from_request(request, require_confirmation=True)

        self.assertIn("I found 2 tasks to add", preview)
        self.assertEqual(
            [intent["title"] for intent in claw.pending_action.create_intents],
            ["Buy milk", "Call dentist"],
        )

    def test_plain_text_multi_create_requires_confirmation_at_router_boundary(self):
        claw = FamilyTasksClaw.from_provider(FakeProvider())

        self.assertTrue(
            claw.requires_create_confirmation(
                "Add task buy milk. Add task email the teacher.",
            )
        )

    def test_repeated_create_preview_keeps_router_semantic_shared_fields(self):
        provider = NamedListProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        semantic_intent = {
            "intent": "create_task",
            "title": "Shared title is ignored",
            "due": "2026-08-24",
            "task_list_name": "School",
            "metadata": {"owner": "dad", "tags": ["school"]},
            "missing_fields": [],
        }

        with redirect_stdout(StringIO()):
            claw.add_task_from_request(
                "Add task buy milk. Add task email teacher.",
                require_confirmation=True,
                semantic_intent=semantic_intent,
            )

        intents = claw.pending_action.create_intents
        self.assertEqual([item["title"] for item in intents], ["Buy milk", "Email teacher"])
        self.assertEqual([item["due"] for item in intents], ["2026-08-24"] * 2)
        self.assertEqual([item["task_list_name"] for item in intents], ["School"] * 2)

    def test_confirmed_multi_list_create_undo_uses_each_original_list(self):
        provider = NamedListProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        intents = [
            {
                "intent": "create_task",
                "title": "Email teacher",
                "metadata": {},
                "task_list_name": "School",
                "missing_fields": [],
            },
            {
                "intent": "create_task",
                "title": "Buy milk",
                "metadata": {},
                "task_list_name": "My Tasks",
                "missing_fields": [],
            },
        ]

        with redirect_stdout(StringIO()):
            claw._preview_task_creates(intents)
            claw.handle_pending_response("yes, add them")
            claw.undo_last_action()

        self.assertEqual(provider.deleted_list_ids, ["school-id", "default-id"])

    def test_semantic_update_changes_title_and_due_in_named_list(self):
        provider = NamedListProvider()
        provider.tasks = [
            {
                "id": "task-1",
                "title": "Old school form",
                "notes": None,
                "due": None,
                "status": "needsAction",
            }
        ]
        claw = FamilyTasksClaw.from_provider(provider)
        semantic_intent = {
            "intent": "update_task",
            "query": "old school form",
            "update": {
                "title": "Submit school form",
                "due": "2026-08-28",
                "notes": None,
                "owner": None,
                "tags": [],
                "assistant_help_request": None,
            },
        }

        with redirect_stdout(StringIO()):
            message = claw.update_task_from_request(
                "make that the submit form for next Friday",
                task_list_id="school-id",
                semantic_intent=semantic_intent,
            )

        self.assertIn("title", message)
        self.assertIn("due=2026-08-28", message)
        self.assertEqual(provider.updated[-1]["title"], "Submit school form")
        self.assertEqual(provider.updated[-1]["due"], "2026-08-28")
        self.assertEqual(provider.updated_list_ids, ["school-id"])

    def test_targetless_semantic_update_does_not_mutate_last_created_task(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        claw.last_created_task = {
            "id": "task-1",
            "title": "Existing task",
            "status": "needsAction",
        }
        semantic_intent = {
            "intent": "update_task",
            "query": None,
            "update": {"due": "2026-08-28"},
        }

        with redirect_stdout(StringIO()):
            message = claw.update_task_from_request(
                "move it",
                semantic_intent=semantic_intent,
            )

        self.assertEqual(message, "Please provide which task to update.")
        self.assertEqual(provider.updated, [])

    def test_pending_create_accepts_date_correction_before_write(self):
        first = {
            "intent": "create_task",
            "title": "Submit form",
            "due": "2026-08-28",
            "metadata": {"owner": "dad", "tags": ["school"]},
            "task_list_name": "School",
            "missing_fields": [],
        }
        corrected = {**first, "due": "2026-09-01"}
        provider = NamedListProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        claw.field_extractor = SequenceFieldExtractor(first, corrected)
        reference = datetime(2026, 8, 21, 10, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.add_task_from_request(
                "submit the form next Friday",
                reference_time=reference,
                require_confirmation=True,
            )
            self.assertTrue(claw.handle_pending_response("Sep 1 instead"))

        self.assertEqual(provider.created, [])
        self.assertEqual(claw.pending_action.create_intents[0]["due"], "2026-09-01")
        self.assertEqual(claw.pending_action.create_intents[0]["task_list_name"], "School")
        self.assertEqual(claw.pending_action.create_intents[0]["metadata"]["owner"], "dad")
        self.assertEqual(claw.field_extractor.calls[1]["now"], reference)

        with redirect_stdout(StringIO()):
            claw.handle_pending_response("yes please")

        self.assertEqual(provider.created[0]["due"], "2026-09-01")
        self.assertEqual(provider.created_list_ids, ["school-id"])

    def test_pending_create_accepts_reparsed_list_and_owner_correction(self):
        first = {
            "intent": "create_task",
            "title": "Buy milk",
            "due": None,
            "metadata": {"owner": "dad"},
            "task_list_name": "My Tasks",
            "missing_fields": [],
        }
        corrected = {
            **first,
            "metadata": {"owner": "nysha"},
            "task_list_name": "School",
        }
        claw = FamilyTasksClaw.from_provider(NamedListProvider())
        claw.field_extractor = SequenceFieldExtractor(first, corrected)

        with redirect_stdout(StringIO()):
            claw.add_task_from_request("buy milk", require_confirmation=True)
            handled = claw.handle_pending_response(
                "change to School and make this task for Nysha"
            )

        self.assertTrue(handled)
        revised = claw.pending_action.create_intents[0]
        self.assertEqual(revised["task_list_name"], "School")
        self.assertEqual(revised["metadata"]["owner"], "nysha")

    def test_pending_create_accepts_natural_title_correction(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.add_task_from_request("Add task call the teacher", require_confirmation=True)
            handled = claw.handle_pending_response("email the teacher instead")

        self.assertTrue(handled)
        self.assertEqual(provider.created, [])
        self.assertEqual(
            claw.pending_action.create_intents[0]["title"],
            "Email the teacher",
        )

    def test_pending_create_accepts_noah_help_correction(self):
        provider = FakeProvider()
        claw = FamilyTasksClaw.from_provider(provider)
        claw.auto_run_assistant_help = False

        with redirect_stdout(StringIO()):
            claw.add_task_from_request("Add task compare camps", require_confirmation=True)
            handled = claw.handle_pending_response("Ask Noah to help compare options")

        self.assertTrue(handled)
        self.assertEqual(provider.created, [])
        metadata = claw.pending_action.create_intents[0]["metadata"]
        self.assertTrue(metadata["assistant_help_needed"])
        self.assertIn("compare options", metadata["assistant_help_request"].lower())


if __name__ == "__main__":
    unittest.main()

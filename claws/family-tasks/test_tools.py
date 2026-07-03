import unittest

from intent import read_metadata_from_notes
from tools import FamilyTaskTools


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
            "id": "task-1",
            "title": title,
            "notes": notes,
            "due": due,
            "task_list_id": task_list_id,
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
        return {
            "id": task_id,
            "title": title,
            "notes": notes,
            "due": due,
            "status": status or "needsAction",
        }

    def complete_task(self, task_id, task_list_id="@default"):
        self.completed.append((task_list_id, task_id))
        return {"id": task_id, "status": "completed"}

    def delete_task(self, task_id, task_list_id="@default"):
        self.deleted.append((task_list_id, task_id))


class FailingProvider(FakeProvider):
    def create_task(self, title, notes=None, due=None, task_list_id="@default"):
        raise RuntimeError("invalid_scope: Bad Request")


class FamilyTaskToolsTest(unittest.TestCase):
    def test_create_task_writes_metadata_to_notes(self):
        provider = FakeProvider()
        tools = FamilyTaskTools(provider)

        response = tools.create_task(
            title="Call Rahul",
            notes="Call after school drop-off.",
            due="2026-07-04",
            metadata={
                "context": ["car", "phone"],
                "energy": "low",
                "duration_minutes": 20,
                "effort_type": "communication",
                "requires": ["phone"],
                "can_do_while": ["driving", "commuting"],
                "location": "anywhere",
                "owner": "dad",
            },
        )

        self.assertEqual(response["status"], "ok")
        created = provider.created[0]
        self.assertEqual(created["title"], "Call Rahul")
        self.assertEqual(created["due"], "2026-07-04")
        notes, metadata = read_metadata_from_notes(created["notes"])
        self.assertEqual(notes, "Call after school drop-off.")
        self.assertEqual(metadata["context"], ["car", "phone"])
        self.assertEqual(metadata["effort_type"], "communication")
        self.assertEqual(metadata["requires"], ["phone"])
        self.assertEqual(metadata["can_do_while"], ["driving", "commuting"])

    def test_complete_task_requires_confirmation(self):
        provider = FakeProvider()
        tools = FamilyTaskTools(provider)

        response = tools.complete_task(task_id="task-1")

        self.assertEqual(response["status"], "needs_confirmation")
        self.assertEqual(provider.completed, [])

    def test_complete_task_when_confirmed(self):
        provider = FakeProvider()
        tools = FamilyTaskTools(provider)

        response = tools.complete_task(task_id="task-1", confirmed=True)

        self.assertEqual(response["status"], "ok")
        self.assertEqual(provider.completed, [("@default", "task-1")])

    def test_delete_task_requires_confirmation(self):
        provider = FakeProvider()
        tools = FamilyTaskTools(provider)

        response = tools.delete_task(task_id="task-1")

        self.assertEqual(response["status"], "needs_confirmation")
        self.assertEqual(provider.deleted, [])

    def test_update_task_requires_update_fields(self):
        tools = FamilyTaskTools(FakeProvider())

        response = tools.update_task(task_id="task-1")

        self.assertEqual(response["status"], "needs_information")
        self.assertEqual(
            response["data"]["missing_fields"],
            ["title, notes, due, or metadata"],
        )

    def test_create_task_formats_invalid_scope_error(self):
        tools = FamilyTaskTools(FailingProvider())

        response = tools.create_task(title="Change water filter")

        self.assertEqual(response["status"], "error")
        self.assertIn("missing the Tasks scope", response["message"])
        self.assertIn("python get_google_token.py", response["message"])


if __name__ == "__main__":
    unittest.main()

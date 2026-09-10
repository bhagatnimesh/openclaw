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


class RevokedTokenProvider(FakeProvider):
    def create_task(self, title, notes=None, due=None, task_list_id="@default"):
        raise RuntimeError("invalid_grant: Token has been expired or revoked.")


class UnreachableProvider(FakeProvider):
    def create_task(self, title, notes=None, due=None, task_list_id="@default"):
        raise ConnectionResetError(54, "Connection reset by peer")

    def list_tasks(self, task_list_id="@default", show_completed=False):
        raise ConnectionResetError(54, "Connection reset by peer")

    def update_task(self, *args, **kwargs):
        raise ConnectionResetError(54, "Connection reset by peer")

    def complete_task(self, task_id, task_list_id="@default"):
        raise ConnectionResetError(54, "Connection reset by peer")

    def delete_task(self, task_id, task_list_id="@default"):
        raise ConnectionResetError(54, "Connection reset by peer")


class FakeResponse:
    status = 404


class MissingTaskError(Exception):
    resp = FakeResponse()


class MissingTaskProvider(FakeProvider):
    def update_task(self, *args, **kwargs):
        raise MissingTaskError("verbose upstream details")


class FamilyTaskToolsTest(unittest.TestCase):
    def test_create_task_keeps_google_notes_human_readable(self):
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
        human_notes, metadata = read_metadata_from_notes(created["notes"])
        self.assertEqual(human_notes, "Call after school drop-off.")
        self.assertEqual(metadata["owner"], "dad")
        task = response["data"]["task"]
        self.assertEqual(task["_n4os_metadata"]["context"], ["car", "phone"])
        self.assertEqual(task["_n4os_metadata"]["effort_type"], "communication")
        self.assertEqual(task["_n4os_metadata"]["requires"], ["phone"])
        self.assertEqual(task["_n4os_metadata"]["can_do_while"], ["driving", "commuting"])

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
            ["title, notes, due, status, or metadata"],
        )

    def test_create_task_formats_invalid_scope_error(self):
        tools = FamilyTaskTools(FailingProvider())

        response = tools.create_task(title="Change water filter")

        self.assertEqual(response["status"], "error")
        self.assertIn("missing the Tasks scope", response["message"])
        self.assertIn("python3 get_google_token.py", response["message"])

    def test_create_task_reports_revoked_google_auth(self):
        tools = FamilyTaskTools(RevokedTokenProvider())

        response = tools.create_task(title="Change water filter")

        self.assertEqual(response["status"], "error")
        self.assertIn("Google Tasks needs to be reconnected", response["message"])
        self.assertIn("python3 get_google_token.py", response["message"])
        self.assertNotIn("invalid_grant", response["message"])

    def test_create_task_hides_transport_details_and_warns_about_duplicates(self):
        tools = FamilyTaskTools(UnreachableProvider())

        response = tools.create_task(title="Add mobile screen")

        self.assertEqual(response["status"], "error")
        self.assertEqual(
            response["message"],
            "I couldn't confirm whether Google Tasks created it. Check Google "
            "Tasks before retrying so you don't create a duplicate.",
        )
        self.assertNotIn("Errno", response["message"])

    def test_other_task_changes_use_state_check_guidance(self):
        tools = FamilyTaskTools(UnreachableProvider())

        responses = [
            tools.update_task(task_id="task-1", title="Updated"),
            tools.complete_task(task_id="task-1", confirmed=True),
            tools.delete_task(task_id="task-1", confirmed=True),
        ]

        for response in responses:
            self.assertEqual(response["status"], "error")
            self.assertEqual(
                response["message"],
                "I couldn't confirm the change with Google Tasks. Check the task's "
                "current state before retrying.",
            )
            self.assertNotIn("duplicate", response["message"])

    def test_deterministic_api_error_is_not_reported_as_transport_failure(self):
        tools = FamilyTaskTools(MissingTaskProvider())

        response = tools.update_task(task_id="missing", title="Updated")

        self.assertEqual(response["status"], "error")
        self.assertEqual(
            response["message"],
            "Google Tasks couldn't find that task or list. Refresh your tasks and try again.",
        )
        self.assertNotIn("verbose upstream details", response["message"])

    def test_list_tasks_hides_transport_details_and_invites_retry(self):
        tools = FamilyTaskTools(UnreachableProvider())

        response = tools.list_tasks()

        self.assertEqual(response["status"], "error")
        self.assertEqual(
            response["message"],
            "I couldn't reach Google Tasks just now. Please try again.",
        )
        self.assertNotIn("Errno", response["message"])


if __name__ == "__main__":
    unittest.main()

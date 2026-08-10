import sys
import tempfile
import unittest
from pathlib import Path

from provider import (
    CommandOurGroceriesProvider,
    GoogleTasksShoppingProvider,
    SQLiteShoppingProvider,
    SQLiteShoppingStore,
)
from tools import ShoppingTools


class FakeGoogleTasksProvider:
    def __init__(self):
        self.task_lists = [
            {"id": "tasks-indian", "title": "Grocery - Indian"},
            {"id": "tasks-costco", "title": "Grocery - Costco"},
            {"id": "tasks-wholefoods", "title": "Grocery - Wholefoods"},
            {"id": "tasks-amazon", "title": "Shopping - Amazon"},
            {"id": "tasks-shopping", "title": "Shopping"},
        ]
        self.tasks_by_list = {
            "tasks-indian": [],
            "tasks-costco": [],
            "tasks-wholefoods": [],
            "tasks-amazon": [],
            "tasks-shopping": [],
        }
        self.next_id = 1

    def list_task_lists(self):
        return list(self.task_lists)

    def create_task(self, title, notes=None, due=None, task_list_id="@default"):
        task = {
            "id": f"task-{self.next_id}",
            "title": title,
            "notes": notes,
            "due": due,
            "status": "needsAction",
        }
        self.next_id += 1
        self.tasks_by_list.setdefault(task_list_id, []).append(task)
        return dict(task)

    def list_tasks(self, task_list_id="@default", show_completed=False):
        tasks = self.tasks_by_list.get(task_list_id, [])
        if show_completed:
            return [dict(task) for task in tasks]
        return [dict(task) for task in tasks if task.get("status") != "completed"]

    def update_task(
        self,
        task_id,
        title=None,
        notes=None,
        due=None,
        status=None,
        task_list_id="@default",
    ):
        for task in self.tasks_by_list.get(task_list_id, []):
            if task["id"] != task_id:
                continue
            if title is not None:
                task["title"] = title
            if notes is not None:
                task["notes"] = notes
            if due is not None:
                task["due"] = due
            if status is not None:
                task["status"] = status
            return dict(task)
        raise KeyError(task_id)

    def complete_task(self, task_id, task_list_id="@default"):
        return self.update_task(
            task_id=task_id,
            status="completed",
            task_list_id=task_list_id,
        )

    def delete_task(self, task_id, task_list_id="@default"):
        self.tasks_by_list[task_list_id] = [
            task for task in self.tasks_by_list.get(task_list_id, []) if task["id"] != task_id
        ]


class ShoppingToolsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "n4os.db"
        self.store = SQLiteShoppingStore(self.db_path)
        self.provider = SQLiteShoppingProvider(self.store)
        self.tools = ShoppingTools(self.provider, self.store)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_add_list_and_check_item_with_history(self):
        add_response = self.tools.add_item(list_slug="costco", item="milk")
        self.assertEqual(add_response["status"], "ok")

        list_response = self.tools.list_items("costco")
        self.assertEqual(list_response["status"], "ok")
        self.assertEqual(
            [item["title"] for item in list_response["data"]["items"]],
            ["milk"],
        )

        check_response = self.tools.set_checked("costco", "milk", True)
        self.assertEqual(check_response["status"], "ok")

        with self.store._connection() as connection:
            events = connection.execute(
                "SELECT action, status, item_title FROM shopping_events ORDER BY created_at, rowid"
            ).fetchall()

        self.assertEqual(
            [(row["action"], row["status"], row["item_title"]) for row in events],
            [("add_item", "ok", "milk"), ("check_item", "ok", "milk")],
        )

    def test_ambiguous_match_needs_confirmation(self):
        self.tools.add_item(list_slug="indian", item="coconut milk")
        self.tools.add_item(list_slug="indian", item="coconut powder")

        response = self.tools.set_checked("indian", "coconut", True)

        self.assertEqual(response["status"], "needs_confirmation")
        self.assertIn("Which item", response["message"])

    def test_failed_provider_action_logs_error(self):
        class FailingProvider(SQLiteShoppingProvider):
            name = "failing"

            def add_item(self, *args, **kwargs):
                raise RuntimeError("provider unavailable")

        tools = ShoppingTools(FailingProvider(self.store), self.store)

        response = tools.add_item(list_slug="amazon", item="batteries")

        self.assertEqual(response["status"], "error")
        with self.store._connection() as connection:
            event = connection.execute(
                "SELECT provider, action, status, item_title, message FROM shopping_events"
            ).fetchone()
        self.assertEqual(event["provider"], "failing")
        self.assertEqual(event["action"], "add_item")
        self.assertEqual(event["status"], "error")
        self.assertEqual(event["item_title"], "batteries")
        self.assertIn("provider unavailable", event["message"])

    def test_add_item_infers_list_from_history(self):
        first = self.tools.add_item(list_slug="indian", item="paneer")
        self.assertEqual(first["status"], "ok")

        second = self.tools.add_item(item="paneer")

        self.assertEqual(second["status"], "ok")
        self.assertTrue(second["data"]["inferred_list"])
        self.assertEqual(second["data"]["item"]["list_slug"], "indian")
        self.assertIn("based on shopping history", second["message"])

    def test_add_item_without_list_or_history_asks_for_list(self):
        response = self.tools.add_item(item="paneer")

        self.assertEqual(response["status"], "needs_information")
        self.assertEqual(response["data"]["missing_fields"], ["list_name"])

    def test_conflicting_history_does_not_infer_list(self):
        self.tools.add_item(list_slug="indian", item="coconut milk")
        self.tools.add_item(list_slug="costco", item="coconut milk")

        response = self.tools.add_item(item="coconut milk")

        self.assertEqual(response["status"], "needs_information")
        self.assertEqual(response["data"]["missing_fields"], ["list_name"])

    def test_clear_list_checks_pending_items_and_logs_history(self):
        self.tools.add_item(list_slug="indian", item="paneer")
        self.tools.add_item(list_slug="indian", item="curry leaves")

        response = self.tools.clear_list("indian")

        self.assertEqual(response["status"], "ok")
        self.assertEqual(len(response["data"]["items"]), 2)
        pending = self.tools.list_items("indian")
        self.assertEqual(pending["data"]["items"], [])
        checked = self.tools.list_items("indian", include_checked=True)
        self.assertEqual(
            [(item["title"], bool(item["checked"])) for item in checked["data"]["items"]],
            [("curry leaves", True), ("paneer", True)],
        )
        with self.store._connection() as connection:
            events = connection.execute(
                "SELECT action, status, item_title FROM shopping_events ORDER BY created_at, rowid"
            ).fetchall()
        self.assertEqual(
            [(row["action"], row["status"], row["item_title"]) for row in events],
            [
                ("add_item", "ok", "paneer"),
                ("add_item", "ok", "curry leaves"),
                ("check_item", "ok", "curry leaves"),
                ("check_item", "ok", "paneer"),
                ("clear_list", "ok", None),
            ],
        )

    def test_command_provider_calls_json_bridge(self):
        bridge = Path(self.temp_dir.name) / "fake_bridge.py"
        bridge.write_text(
            "\n".join(
                [
                    "import json, sys",
                    "request = json.load(sys.stdin)",
                    "action = request['action']",
                    "params = request['params']",
                    "if action == 'add_item':",
                    "    print(json.dumps({'result': {'id': 'item-1', 'title': params['item'], 'list_slug': params['list_slug'], 'checked': False}}))",
                    "elif action == 'list_items':",
                    "    print(json.dumps({'result': [{'id': 'item-1', 'title': 'milk', 'list_slug': params['list_slug'], 'checked': False}]}))",
                    "else:",
                    "    print(json.dumps({'error': 'unexpected action ' + action}))",
                    "    sys.exit(1)",
                ]
            ),
            encoding="utf-8",
        )
        provider = CommandOurGroceriesProvider(f"{sys.executable} {bridge}")

        created = provider.add_item("costco", "milk")
        items = provider.list_items("costco")

        self.assertEqual(created["title"], "milk")
        self.assertEqual(created["list_slug"], "costco")
        self.assertEqual(items[0]["title"], "milk")

    def test_google_tasks_provider_maps_fixed_shopping_lists(self):
        provider = GoogleTasksShoppingProvider(FakeGoogleTasksProvider())

        lists = provider.list_lists()

        self.assertEqual(
            [(item["slug"], item["google_task_list_id"]) for item in lists],
            [
                ("indian", "tasks-indian"),
                ("costco", "tasks-costco"),
                ("whole-foods", "tasks-wholefoods"),
                ("amazon", "tasks-amazon"),
                ("others", "tasks-shopping"),
            ],
        )

    def test_google_tasks_add_writes_to_mapped_task_list_and_sqlite(self):
        fake_google = FakeGoogleTasksProvider()
        provider = GoogleTasksShoppingProvider(fake_google)
        tools = ShoppingTools(provider, self.store)

        response = tools.add_item(list_slug="costco", item="milk", quantity="2")

        self.assertEqual(response["status"], "ok")
        self.assertEqual(fake_google.tasks_by_list["tasks-costco"][0]["title"], "milk")
        self.assertIn("N4OS_SHOPPING:", fake_google.tasks_by_list["tasks-costco"][0]["notes"])
        with self.store._connection() as connection:
            snapshot = connection.execute(
                "SELECT provider, list_slug, title, quantity FROM shopping_item_snapshots"
            ).fetchone()
        self.assertEqual(snapshot["provider"], "google-tasks")
        self.assertEqual(snapshot["list_slug"], "costco")
        self.assertEqual(snapshot["title"], "milk")
        self.assertEqual(snapshot["quantity"], "2")

    def test_google_tasks_external_add_and_complete_sync_back_to_sqlite(self):
        fake_google = FakeGoogleTasksProvider()
        fake_google.tasks_by_list["tasks-indian"].append(
            {
                "id": "external-1",
                "title": "Paneer",
                "notes": None,
                "status": "needsAction",
            }
        )
        provider = GoogleTasksShoppingProvider(fake_google)
        tools = ShoppingTools(provider, self.store)

        first_sync = tools.list_items("indian")
        self.assertEqual(first_sync["status"], "ok")
        self.assertEqual(first_sync["data"]["items"][0]["title"], "Paneer")

        fake_google.tasks_by_list["tasks-indian"][0]["status"] = "completed"
        second_sync = tools.list_items("indian")

        self.assertEqual(second_sync["status"], "ok")
        self.assertEqual(second_sync["data"]["items"], [])
        with self.store._connection() as connection:
            snapshot = connection.execute(
                "SELECT title, checked FROM shopping_item_snapshots WHERE list_slug = 'indian'"
            ).fetchone()
        self.assertEqual(snapshot["title"], "Paneer")
        self.assertEqual(snapshot["checked"], 1)

    def test_google_tasks_clear_list_completes_google_tasks(self):
        fake_google = FakeGoogleTasksProvider()
        provider = GoogleTasksShoppingProvider(fake_google)
        tools = ShoppingTools(provider, self.store)
        tools.add_item(list_slug="indian", item="paneer")
        tools.add_item(list_slug="indian", item="curry leaves")

        response = tools.clear_list("indian")

        self.assertEqual(response["status"], "ok")
        self.assertEqual(
            [task["status"] for task in fake_google.tasks_by_list["tasks-indian"]],
            ["completed", "completed"],
        )


if __name__ == "__main__":
    unittest.main()

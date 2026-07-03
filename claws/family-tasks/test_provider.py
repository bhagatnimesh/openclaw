import os
import unittest


@unittest.skipUnless(
    os.environ.get("OPENCLAW_LIVE_TEST") == "1",
    "set OPENCLAW_LIVE_TEST=1 to run the live Google Tasks smoke test",
)
class GoogleTasksProviderLiveTest(unittest.TestCase):
    def test_create_list_complete_delete_task(self):
        from provider import GoogleTasksProvider

        provider = GoogleTasksProvider()
        created = provider.create_task(
            title="N4OS Provider Task Test",
            notes="temporary provider smoke test",
        )

        tasks = provider.list_tasks()
        self.assertIn(created["id"], {task.get("id") for task in tasks})

        completed = provider.complete_task(created["id"])
        self.assertEqual(completed["status"], "completed")

        provider.delete_task(created["id"])


if __name__ == "__main__":
    unittest.main()

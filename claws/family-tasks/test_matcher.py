import unittest

from intent import write_metadata_to_notes
from matcher import recommend_tasks


def _task(title, metadata, due=None):
    return {
        "id": title.lower().replace(" ", "-"),
        "title": title,
        "notes": write_metadata_to_notes(None, metadata),
        "due": due,
        "status": "needsAction",
    }


class TaskRecommendationTest(unittest.TestCase):
    def test_recommendation_by_driving_commute(self):
        tasks = [
            _task(
                "Call Rahul",
                {
                    "context": ["car", "phone"],
                    "energy": "low",
                    "duration_minutes": 20,
                    "effort_type": "communication",
                    "requires": ["phone"],
                    "can_do_while": ["driving", "commuting"],
                },
            ),
            _task(
                "Research summer camps",
                {
                    "context": ["computer"],
                    "energy": "medium",
                    "duration_minutes": 45,
                    "effort_type": "research",
                    "requires": ["computer", "internet", "focus"],
                },
            ),
        ]

        recommended = recommend_tasks(
            tasks,
            {
                "context": ["car", "phone"],
                "can_do_while": ["driving"],
                "available_resources": ["phone", "car"],
                "unavailable_resources": [
                    "computer",
                    "paperwork",
                    "equipment",
                    "quiet",
                    "focus",
                ],
            },
        )

        self.assertEqual([task["title"] for task in recommended], ["Call Rahul"])

    def test_recommendation_by_low_energy_short_duration(self):
        tasks = [
            _task(
                "Change water filter",
                {
                    "context": ["home"],
                    "energy": "low",
                    "duration_minutes": 15,
                    "effort_type": "physical",
                    "requires": ["equipment"],
                    "urgency": "medium",
                },
            ),
            _task(
                "Research summer camps",
                {
                    "context": ["computer"],
                    "energy": "high",
                    "duration_minutes": 45,
                    "effort_type": "research",
                    "requires": ["computer", "internet", "focus"],
                },
            ),
        ]

        recommended = recommend_tasks(
            tasks,
            {"energy": "low", "duration_minutes": 20},
        )

        self.assertEqual(
            [task["title"] for task in recommended],
            ["Change water filter"],
        )

    def test_recommendation_by_urgent_due_this_week(self):
        tasks = [
            _task(
                "Submit camp forms",
                {
                    "energy": "medium",
                    "duration_minutes": 30,
                    "urgency": "high",
                    "effort_type": "admin",
                    "requires": ["computer", "internet"],
                },
                due="2026-07-05T00:00:00.000Z",
            ),
            _task(
                "Buy shoes",
                {
                    "energy": "low",
                    "duration_minutes": 30,
                    "urgency": "high",
                    "effort_type": "errand",
                    "requires": ["car"],
                },
                due="2026-07-09T00:00:00.000Z",
            ),
            _task(
                "File receipts",
                {
                    "energy": "low",
                    "duration_minutes": 10,
                    "urgency": "low",
                    "effort_type": "admin",
                    "requires": ["computer"],
                },
                due="2026-07-04T00:00:00.000Z",
            ),
        ]

        recommended = recommend_tasks(
            tasks,
            {
                "urgency": "high",
                "due_min": "2026-07-03T00:00:00-07:00",
                "due_max": "2026-07-05T23:59:59.999999-07:00",
            },
        )

        self.assertEqual([task["title"] for task in recommended], ["Submit camp forms"])

    def test_more_situational_matches_rank_first(self):
        tasks = [
            _task(
                "Call Rahul",
                {
                    "context": ["phone"],
                    "energy": "low",
                    "duration_minutes": 20,
                    "effort_type": "communication",
                    "requires": ["phone"],
                    "can_do_while": ["driving"],
                },
            ),
            _task(
                "File receipt",
                {
                    "context": ["computer"],
                    "energy": "low",
                    "duration_minutes": 10,
                    "effort_type": "admin",
                    "requires": ["computer"],
                },
            ),
        ]

        recommended = recommend_tasks(
            tasks,
            {
                "context": ["phone"],
                "can_do_while": ["driving"],
                "available_resources": ["phone"],
                "duration_minutes": 20,
            },
        )

        self.assertEqual([task["title"] for task in recommended], ["Call Rahul"])

    def test_physical_task_recommendation(self):
        tasks = [
            _task(
                "Change water filter",
                {
                    "context": ["home"],
                    "energy": "medium",
                    "duration_minutes": 15,
                    "effort_type": "physical",
                    "requires": ["equipment"],
                },
            ),
            _task(
                "Book flight",
                {
                    "context": ["computer"],
                    "energy": "medium",
                    "duration_minutes": 30,
                    "effort_type": "admin",
                    "requires": ["computer", "internet"],
                },
            ),
        ]

        recommended = recommend_tasks(tasks, {"effort_type": "physical"})

        self.assertEqual([task["title"] for task in recommended], ["Change water filter"])

    def test_laptop_duration_excludes_unavailable_resources(self):
        tasks = [
            _task(
                "Book flight",
                {
                    "context": ["computer"],
                    "energy": "medium",
                    "duration_minutes": 30,
                    "effort_type": "admin",
                    "requires": ["computer", "internet"],
                },
            ),
            _task(
                "Go to grocery store",
                {
                    "context": ["errand", "outside"],
                    "energy": "medium",
                    "duration_minutes": 30,
                    "effort_type": "errand",
                    "requires": ["car"],
                    "location": "outside",
                },
            ),
            _task(
                "Fill visa form",
                {
                    "context": ["computer"],
                    "energy": "high",
                    "duration_minutes": 60,
                    "effort_type": "paperwork",
                    "requires": ["computer", "paperwork", "focus"],
                },
            ),
        ]

        recommended = recommend_tasks(
            tasks,
            {
                "context": ["computer"],
                "available_resources": ["computer", "internet", "phone"],
                "duration_minutes": 30,
            },
        )

        self.assertEqual([task["title"] for task in recommended], ["Book flight"])

    def test_bored_excludes_high_focus_research(self):
        tasks = [
            _task(
                "Tidy desk",
                {
                    "context": ["home"],
                    "energy": "low",
                    "complexity": "low",
                    "duration_minutes": 10,
                    "effort_type": "physical",
                    "requires": [],
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
                },
            ),
        ]

        recommended = recommend_tasks(
            tasks,
            {
                "max_energy": "medium",
                "max_complexity": "medium",
                "exclude_requires": ["focus"],
            },
        )

        self.assertEqual([task["title"] for task in recommended], ["Tidy desk"])

    def test_paperwork_recommendation(self):
        tasks = [
            _task(
                "Fill visa form",
                {
                    "context": ["computer"],
                    "energy": "high",
                    "duration_minutes": 60,
                    "effort_type": "paperwork",
                    "requires": ["computer", "paperwork", "focus"],
                },
            ),
            _task(
                "Call Rahul",
                {
                    "context": ["phone"],
                    "energy": "low",
                    "duration_minutes": 20,
                    "effort_type": "communication",
                    "requires": ["phone"],
                },
            ),
        ]

        recommended = recommend_tasks(tasks, {"effort_type": "paperwork"})

        self.assertEqual([task["title"] for task in recommended], ["Fill visa form"])

    def test_home_recommendation(self):
        tasks = [
            _task(
                "Change water filter",
                {
                    "context": ["home"],
                    "energy": "medium",
                    "duration_minutes": 15,
                    "effort_type": "physical",
                    "requires": ["equipment"],
                    "location": "home",
                },
            ),
            _task(
                "Go to grocery store",
                {
                    "context": ["errand", "outside"],
                    "energy": "medium",
                    "duration_minutes": 30,
                    "effort_type": "errand",
                    "requires": ["car"],
                    "location": "outside",
                },
            ),
        ]

        recommended = recommend_tasks(tasks, {"context": ["home"], "location": "home"})

        self.assertEqual([task["title"] for task in recommended], ["Change water filter"])


if __name__ == "__main__":
    unittest.main()

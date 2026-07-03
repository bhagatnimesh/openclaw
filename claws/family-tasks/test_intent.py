import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from intent import (
    DEFAULT_TIMEZONE,
    extract_intent,
    read_metadata_from_notes,
    write_metadata_to_notes,
)


class TaskIntentTest(unittest.TestCase):
    def test_change_water_filter_this_weekend(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Add task change water filter this weekend", now=now)

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(intent["title"], "Change water filter")
        self.assertEqual(intent["due"], "2026-07-04")
        self.assertEqual(intent["metadata"]["context"], ["home"])
        self.assertEqual(intent["metadata"]["energy"], "medium")
        self.assertEqual(intent["metadata"]["duration_minutes"], 15)
        self.assertEqual(intent["metadata"]["urgency"], "medium")
        self.assertEqual(intent["metadata"]["complexity"], "low")
        self.assertEqual(intent["metadata"]["effort_type"], "physical")
        self.assertEqual(intent["metadata"]["requires"], ["equipment"])
        self.assertEqual(intent["metadata"]["location"], "home")
        self.assertEqual(intent["metadata"]["owner"], "unknown")

    def test_call_during_commute_metadata(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Add task call Rahul during commute", now=now)

        self.assertEqual(intent["title"], "Call Rahul")
        self.assertEqual(intent["metadata"]["context"], ["car", "phone"])
        self.assertEqual(intent["metadata"]["can_do_while"], ["commuting", "driving"])
        self.assertEqual(intent["metadata"]["effort_type"], "communication")
        self.assertEqual(intent["metadata"]["requires"], ["phone"])
        self.assertEqual(intent["metadata"]["location"], "anywhere")
        self.assertEqual(intent["metadata"]["energy"], "low")
        self.assertEqual(intent["metadata"]["duration_minutes"], 20)

    def test_call_mom_does_not_assign_owner_to_mom(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Add task call mom", now=now)

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(intent["title"], "Call mom")
        self.assertEqual(intent["metadata"]["context"], ["phone"])
        self.assertEqual(intent["metadata"]["can_do_while"], ["driving", "commuting"])
        self.assertEqual(intent["metadata"]["effort_type"], "communication")
        self.assertEqual(intent["metadata"]["requires"], ["phone"])
        self.assertEqual(intent["metadata"]["energy"], "low")
        self.assertEqual(intent["metadata"]["duration_minutes"], 20)
        self.assertEqual(intent["metadata"]["owner"], "unknown")

    def test_change_water_filter_infers_household_physical_metadata(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Add task change water filter", now=now)

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(intent["title"], "Change water filter")
        self.assertEqual(intent["metadata"]["context"], ["home"])
        self.assertEqual(intent["metadata"]["effort_type"], "physical")
        self.assertEqual(intent["metadata"]["requires"], ["equipment"])
        self.assertEqual(intent["metadata"]["energy"], "medium")
        self.assertEqual(intent["metadata"]["duration_minutes"], 15)
        self.assertEqual(intent["metadata"]["location"], "home")

    def test_research_art_class_infers_research_metadata(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Add task research art class", now=now)

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(intent["title"], "Research art class")
        self.assertEqual(intent["metadata"]["effort_type"], "research")
        self.assertEqual(intent["metadata"]["requires"], ["computer", "internet", "focus"])
        self.assertEqual(intent["metadata"]["energy"], "medium")
        self.assertEqual(intent["metadata"]["duration_minutes"], 45)
        self.assertEqual(intent["metadata"]["location"], "anywhere")

    def test_research_task_metadata(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "Add task research summer camps, needs high energy",
            now=now,
        )

        self.assertEqual(intent["title"], "Research summer camps")
        self.assertEqual(intent["metadata"]["effort_type"], "research")
        self.assertEqual(intent["metadata"]["requires"], ["computer", "internet", "focus"])
        self.assertEqual(intent["metadata"]["energy"], "high")
        self.assertEqual(intent["metadata"]["duration_minutes"], 45)
        self.assertEqual(intent["metadata"]["complexity"], "medium")

    def test_book_flight_metadata(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Add task book flight", now=now)

        self.assertEqual(intent["title"], "Book flight")
        self.assertEqual(intent["metadata"]["effort_type"], "admin")
        self.assertEqual(intent["metadata"]["requires"], ["computer", "internet"])
        self.assertEqual(intent["metadata"]["energy"], "medium")
        self.assertEqual(intent["metadata"]["duration_minutes"], 30)

    def test_fill_visa_form_metadata(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Add task fill visa form", now=now)

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(intent["title"], "Fill visa form")
        self.assertEqual(intent["metadata"]["effort_type"], "paperwork")
        self.assertEqual(
            intent["metadata"]["requires"],
            ["paperwork", "computer", "focus"],
        )
        self.assertEqual(intent["metadata"]["energy"], "high")
        self.assertEqual(intent["metadata"]["duration_minutes"], 60)

    def test_go_grocery_shopping_infers_errand_metadata(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Add task go grocery shopping", now=now)

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(intent["title"], "Go grocery shopping")
        self.assertEqual(intent["metadata"]["effort_type"], "errand")
        self.assertEqual(intent["metadata"]["requires"], ["car"])
        self.assertEqual(intent["metadata"]["context"], ["errand", "outside"])
        self.assertEqual(intent["metadata"]["location"], "outside")
        self.assertEqual(intent["metadata"]["energy"], "medium")

    def test_go_to_grocery_store_metadata(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Add task go to grocery store", now=now)

        self.assertEqual(intent["title"], "Go to grocery store")
        self.assertEqual(intent["metadata"]["effort_type"], "errand")
        self.assertEqual(intent["metadata"]["requires"], ["car"])
        self.assertEqual(intent["metadata"]["context"], ["errand", "outside"])
        self.assertEqual(intent["metadata"]["location"], "outside")
        self.assertEqual(intent["metadata"]["energy"], "medium")

    def test_low_energy_short_duration_recommendation_intent(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "I have 20 minutes and low energy. What should I do?",
            now=now,
        )

        self.assertEqual(intent["intent"], "recommend_tasks")
        self.assertEqual(intent["filters"]["duration_minutes"], 20)
        self.assertEqual(intent["filters"]["energy"], "low")

    def test_driving_recommendation_intent(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("What can I do while driving?", now=now)

        self.assertEqual(intent["filters"]["context"], ["car", "phone"])
        self.assertEqual(intent["filters"]["can_do_while"], ["driving"])
        self.assertEqual(intent["filters"]["available_resources"], ["phone", "car"])
        self.assertIn("focus", intent["filters"]["unavailable_resources"])

    def test_physical_recommendation_intent(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("I want to do physical tasks", now=now)

        self.assertEqual(intent["filters"]["effort_type"], "physical")

    def test_laptop_thirty_minutes_recommendation_intent(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("I have my laptop and 30 minutes", now=now)

        self.assertEqual(intent["filters"]["context"], ["computer"])
        self.assertEqual(intent["filters"]["available_context"], ["computer"])
        self.assertEqual(intent["filters"]["available_resources"], ["computer", "internet", "phone"])
        self.assertEqual(intent["filters"]["duration_minutes"], 30)
        self.assertEqual(intent["filters"]["available_time_minutes"], 30)

    def test_bored_recommendation_intent(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("I'm bored", now=now)

        self.assertEqual(intent["filters"]["max_energy"], "medium")
        self.assertEqual(intent["filters"]["max_complexity"], "medium")
        self.assertEqual(intent["filters"]["exclude_requires"], ["focus"])

    def test_paperwork_recommendation_intent(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("I can do paperwork now", now=now)

        self.assertEqual(intent["filters"]["effort_type"], "paperwork")
        self.assertEqual(
            intent["filters"]["available_resources"],
            ["computer", "focus", "internet", "paperwork"],
        )

    def test_can_make_calls_recommendation_intent(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("I can make calls", now=now)

        self.assertEqual(intent["filters"]["effort_type"], "communication")
        self.assertEqual(intent["filters"]["available_resources"], ["phone"])

    def test_can_run_errands_recommendation_intent(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("I can run errands", now=now)

        self.assertEqual(intent["filters"]["context"], ["errand"])
        self.assertEqual(intent["filters"]["effort_type"], "errand")
        self.assertEqual(intent["filters"]["location"], "outside")

    def test_cognitive_work_recommendation_intent(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("I want cognitive work", now=now)

        self.assertEqual(intent["filters"]["effort_type"], "cognitive")

    def test_home_recommendation_intent(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("I am at home", now=now)

        self.assertEqual(intent["filters"]["context"], ["home"])
        self.assertEqual(intent["filters"]["available_context"], ["home"])
        self.assertEqual(intent["filters"]["location"], "home")

    def test_home_physical_situation_filter_names(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("I'm at home and want physical tasks", now=now)

        self.assertEqual(intent["intent"], "recommend_tasks")
        self.assertEqual(intent["filters"]["available_context"], ["home"])
        self.assertEqual(intent["filters"]["preferred_effort_type"], "physical")
        self.assertEqual(intent["filters"]["location"], "home")

    def test_urgent_due_this_week_intent(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Show urgent tasks due this week", now=now)

        self.assertEqual(intent["filters"]["urgency"], "high")
        self.assertEqual(intent["filters"]["due_min"], "2026-07-03T00:00:00-07:00")
        self.assertEqual(intent["filters"]["due_max"], "2026-07-05T23:59:59.999999-07:00")

    def test_metadata_helpers_preserve_human_notes(self):
        notes = write_metadata_to_notes(
            "Call after school drop-off.",
            {
                "context": ["driving", "commute"],
                "energy": "low",
                "duration_minutes": 20,
                "urgency": "low",
                "complexity": "low",
                "effort_type": "communication",
                "requires": ["phone"],
                "owner": "dad",
            },
        )

        human_notes, metadata = read_metadata_from_notes(notes)

        self.assertEqual(human_notes, "Call after school drop-off.")
        self.assertIn("N4OS_METADATA:", notes)
        self.assertEqual(metadata["context"], ["car"])
        self.assertEqual(metadata["duration_minutes"], 20)
        self.assertEqual(metadata["effort_type"], "communication")
        self.assertEqual(metadata["requires"], ["phone"])
        self.assertEqual(metadata["owner"], "dad")

    def test_legacy_mode_metadata_maps_to_effort_type(self):
        notes = write_metadata_to_notes(
            None,
            {
                "context": ["driving"],
                "mode": "call",
                "can_do_while": ["commute"],
            },
        )

        _, metadata = read_metadata_from_notes(notes)

        self.assertEqual(metadata["context"], ["car"])
        self.assertEqual(metadata["can_do_while"], ["commuting"])
        self.assertEqual(metadata["effort_type"], "communication")


if __name__ == "__main__":
    unittest.main()

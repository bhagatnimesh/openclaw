import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from intent import (
    DEFAULT_TIMEZONE,
    extract_intent,
    read_metadata_from_description,
    write_metadata_to_description,
)


class IntentExtractionTest(unittest.TestCase):
    def test_great_mall_example(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "Add an event I want to go to Great Mall tomorrow to get some tshirts "
            "for India trip. I want to go in the afternoon around 1PM. I dont "
            "want to spend a lot of time.",
            now=now,
        )

        self.assertEqual(intent["intent"], "create_event")
        self.assertEqual(intent["title"], "Great Mall shopping for India trip")
        self.assertEqual(intent["date"], "2026-07-03")
        self.assertEqual(intent["start_time"], "13:00")
        self.assertEqual(intent["duration_minutes"], 60)
        self.assertEqual(intent["timezone"], DEFAULT_TIMEZONE)
        self.assertEqual(intent["location"], "Great Mall")
        self.assertEqual(intent["description"], "get some tshirts for India trip")
        self.assertEqual(intent["missing_fields"], [])

    def test_dinner_friday(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Add dinner Friday at 7 with Rahul", now=now)

        self.assertEqual(intent["intent"], "create_event")
        self.assertEqual(intent["title"], "Dinner")
        self.assertEqual(intent["date"], "2026-07-03")
        self.assertEqual(intent["start_time"], "19:00")
        self.assertEqual(intent["description"], "with Rahul")
        self.assertEqual(intent["missing_fields"], [])

    def test_leave_for_flight_uses_action_time(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "I have a flight at 2PM from SFO, So I need to leave at 11:30 "
            "on Friday next week",
            now=now,
        )

        self.assertEqual(intent["intent"], "create_event")
        self.assertEqual(intent["title"], "Leave for SFO flight")
        self.assertEqual(intent["date"], "2026-07-10")
        self.assertEqual(intent["start_time"], "11:30")
        self.assertEqual(intent["duration_minutes"], 60)
        self.assertEqual(intent["location"], "SFO")
        self.assertEqual(intent["description"], "Flight from SFO at 2:00 PM")
        self.assertEqual(intent["missing_fields"], [])

    def test_friday_next_week_uses_following_calendar_week(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Add appointment Friday next week at 9am", now=now)

        self.assertEqual(intent["date"], "2026-07-10")

    def test_next_week_friday_uses_following_calendar_week(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Add appointment next week Friday at 9am", now=now)

        self.assertEqual(intent["date"], "2026-07-10")

    def test_plain_friday_uses_next_upcoming_friday(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Add appointment Friday at 9am", now=now)

        self.assertEqual(intent["date"], "2026-07-03")

    def test_missing_time(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Add dinner tomorrow with Rahul", now=now)

        self.assertEqual(intent["intent"], "create_event")
        self.assertIn("time", intent["missing_fields"])

    def test_missing_date(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Add dinner at 7 with Rahul", now=now)

        self.assertEqual(intent["intent"], "create_event")
        self.assertIn("date", intent["missing_fields"])

    def test_list_tomorrow_intent(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("What do we have tomorrow?", now=now)

        self.assertEqual(intent["intent"], "list_events")
        self.assertEqual(intent["start"], "2026-07-03T00:00:00-07:00")
        self.assertEqual(intent["end"], "2026-07-04T00:00:00-07:00")
        self.assertEqual(intent["metadata_filter"], {})
        self.assertEqual(intent["missing_fields"], [])

    def test_list_responsibility_filter_intent(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("What am I responsible for tomorrow?", now=now)

        self.assertEqual(intent["intent"], "list_events")
        self.assertEqual(intent["start"], "2026-07-03T00:00:00-07:00")
        self.assertEqual(intent["metadata_filter"], {"owner": "dad"})

    def test_list_mom_weekend_filter_intent(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("What is mom handling this weekend?", now=now)

        self.assertEqual(intent["intent"], "list_events")
        self.assertEqual(intent["start"], "2026-07-04T00:00:00-07:00")
        self.assertEqual(intent["end"], "2026-07-06T00:00:00-07:00")
        self.assertEqual(intent["metadata_filter"], {"owner": "mom"})

    def test_list_preparation_next_week_filter_intent(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("What needs preparation next week?", now=now)

        self.assertEqual(intent["intent"], "preparation_checklist")
        self.assertEqual(intent["start"], "2026-07-06T00:00:00-07:00")
        self.assertEqual(intent["end"], "2026-07-13T00:00:00-07:00")
        self.assertEqual(intent["label"], "next week")
        self.assertIsNone(intent["query"])

    def test_prepare_for_specific_event_intent(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Prepare me for passport renewal", now=now)

        self.assertEqual(intent["intent"], "preparation_checklist")
        self.assertEqual(intent["start"], "2026-07-02T12:00:00-07:00")
        self.assertEqual(intent["end"], "2026-08-01T12:00:00-07:00")
        self.assertEqual(intent["label"], "upcoming")
        self.assertEqual(intent["query"], "passport renewal")

    def test_action_this_week_intent(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("What needs action this week?", now=now)

        self.assertEqual(intent["intent"], "preparation_checklist")
        self.assertEqual(intent["start"], "2026-06-29T00:00:00-07:00")
        self.assertEqual(intent["end"], "2026-07-06T00:00:00-07:00")
        self.assertEqual(intent["label"], "this week")
        self.assertIsNone(intent["query"])

    def test_before_trip_preparation_query_intent(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("What do we need to do before the India trip?", now=now)

        self.assertEqual(intent["intent"], "preparation_checklist")
        self.assertEqual(intent["query"], "India trip")

    def test_this_week_briefing_intent(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Give me this week's family calendar briefing", now=now)

        self.assertEqual(intent["intent"], "family_briefing")
        self.assertEqual(intent["start"], "2026-06-29T00:00:00-07:00")
        self.assertEqual(intent["end"], "2026-07-06T00:00:00-07:00")
        self.assertEqual(intent["label"], "this week")

    def test_plan_this_week_briefing_intent(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("What should we plan for this week?", now=now)

        self.assertEqual(intent["intent"], "family_briefing")
        self.assertEqual(intent["start"], "2026-06-29T00:00:00-07:00")
        self.assertEqual(intent["label"], "this week")

    def test_next_week_briefing_intent(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("What is coming up next week?", now=now)

        self.assertEqual(intent["intent"], "family_briefing")
        self.assertEqual(intent["start"], "2026-07-06T00:00:00-07:00")
        self.assertEqual(intent["end"], "2026-07-13T00:00:00-07:00")
        self.assertEqual(intent["label"], "next week")

    def test_next_week_schedule_summary_intent(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Can you summarize our schedule next week?", now=now)

        self.assertEqual(intent["intent"], "family_briefing")
        self.assertEqual(intent["start"], "2026-07-06T00:00:00-07:00")
        self.assertEqual(intent["end"], "2026-07-13T00:00:00-07:00")
        self.assertEqual(intent["label"], "next week")

    def test_delete_event_intent(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Cancel dinner with Rahul", now=now)

        self.assertEqual(intent["intent"], "delete_event")
        self.assertEqual(intent["query"], "dinner with Rahul")
        self.assertEqual(intent["search_start"], "2026-07-02T12:00:00-07:00")
        self.assertEqual(intent["search_end"], "2026-08-01T12:00:00-07:00")
        self.assertEqual(intent["missing_fields"], [])

    def test_delete_event_intent_with_date_constraint(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Remove tomorrow's shopping event", now=now)

        self.assertEqual(intent["intent"], "delete_event")
        self.assertEqual(intent["query"], "shopping")
        self.assertEqual(intent["search_start"], "2026-07-03T00:00:00-07:00")
        self.assertEqual(intent["search_end"], "2026-07-04T00:00:00-07:00")
        self.assertEqual(intent["missing_fields"], [])

    def test_update_event_intent(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Move dinner with Rahul to Saturday at 7", now=now)

        self.assertEqual(intent["intent"], "update_event")
        self.assertEqual(intent["query"], "dinner with Rahul")
        self.assertEqual(intent["new_date"], "2026-07-04")
        self.assertEqual(intent["new_start_time"], "19:00")
        self.assertFalse(intent["duration_specified"])
        self.assertEqual(intent["missing_fields"], [])

    def test_recurring_specific_weekday_intent(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Add Navya gymnastics every Saturday at 10am", now=now)

        self.assertEqual(intent["intent"], "create_event")
        self.assertEqual(intent["title"], "Navya gymnastics")
        self.assertEqual(intent["date"], "2026-07-04")
        self.assertEqual(intent["start_time"], "10:00")
        self.assertEqual(intent["recurrence"], ["RRULE:FREQ=WEEKLY;BYDAY=SA"])
        self.assertEqual(intent["recurrence_label"], "every Saturday")
        self.assertEqual(intent["missing_fields"], [])

    def test_nysha_dentist_metadata(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "Add Nysha dentist appointment tomorrow at 3pm, I will take her",
            now=now,
        )

        self.assertEqual(intent["intent"], "create_event")
        self.assertEqual(intent["title"], "Nysha dentist appointment")
        self.assertEqual(intent["date"], "2026-07-03")
        self.assertEqual(intent["start_time"], "15:00")
        self.assertEqual(intent["metadata"]["owner"], "dad")
        self.assertEqual(intent["metadata"]["person"], "Nysha")
        self.assertEqual(intent["metadata"]["category"], "medical")
        self.assertFalse(intent["metadata"]["preparation_needed"])
        self.assertEqual(intent["missing_fields"], [])

    def test_navya_gymnastics_metadata(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "Add Navya gymnastics every Saturday at 10am, mom will take her",
            now=now,
        )

        self.assertEqual(intent["intent"], "create_event")
        self.assertEqual(intent["title"], "Navya gymnastics")
        self.assertEqual(intent["date"], "2026-07-04")
        self.assertEqual(intent["start_time"], "10:00")
        self.assertEqual(intent["recurrence"], ["RRULE:FREQ=WEEKLY;BYDAY=SA"])
        self.assertEqual(intent["metadata"]["owner"], "mom")
        self.assertEqual(intent["metadata"]["person"], "Navya")
        self.assertEqual(intent["metadata"]["category"], "activity")
        self.assertFalse(intent["metadata"]["preparation_needed"])
        self.assertEqual(intent["missing_fields"], [])

    def test_passport_renewal_metadata(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "Add passport renewal appointment next Friday at 11am, need documents",
            now=now,
        )

        self.assertEqual(intent["intent"], "create_event")
        self.assertEqual(intent["title"], "Passport renewal appointment")
        self.assertEqual(intent["date"], "2026-07-03")
        self.assertEqual(intent["start_time"], "11:00")
        self.assertEqual(intent["description"], "need documents")
        self.assertEqual(intent["metadata"]["owner"], "unknown")
        self.assertEqual(intent["metadata"]["person"], "family")
        self.assertEqual(intent["metadata"]["category"], "travel")
        self.assertTrue(intent["metadata"]["preparation_needed"])
        self.assertEqual(intent["metadata"]["preparation_notes"], "need documents")
        self.assertEqual(intent["missing_fields"], [])

    def test_pickup_phrase_metadata_and_title(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "Niyati picks up Nysha on Monday at 6 PM from art class",
            now=now,
        )

        self.assertEqual(intent["intent"], "create_event")
        self.assertEqual(intent["title"], "Nysha art class pickup")
        self.assertEqual(intent["date"], "2026-07-06")
        self.assertEqual(intent["start_time"], "18:00")
        self.assertEqual(intent["location"], "art class")
        self.assertEqual(intent["description"], "Niyati picks up Nysha from art class")
        self.assertEqual(intent["metadata"]["owner"], "mom")
        self.assertEqual(intent["metadata"]["person"], "Nysha")
        self.assertEqual(intent["metadata"]["category"], "school")
        self.assertFalse(intent["metadata"]["preparation_needed"])
        self.assertEqual(intent["missing_fields"], [])

    def test_named_person_query_defaults_to_next_thirty_days(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("what events are for Niyati", now=now)

        self.assertEqual(intent["intent"], "list_events")
        self.assertEqual(intent["start"], "2026-07-02T12:00:00-07:00")
        self.assertEqual(intent["end"], "2026-08-01T12:00:00-07:00")
        self.assertEqual(
            intent["metadata_filter"],
            {"person": "Niyati", "text_query": "Niyati"},
        )
        self.assertEqual(intent["missing_fields"], [])

    def test_metadata_description_helpers_preserve_notes(self):
        description = write_metadata_to_description(
            "Buy T-shirts for India trip.",
            {
                "owner": "dad",
                "person": "family",
                "category": "shopping",
                "preparation_needed": False,
                "preparation_notes": "",
            },
        )

        notes, metadata = read_metadata_from_description(description)

        self.assertEqual(notes, "Buy T-shirts for India trip.")
        self.assertIn("N4OS_METADATA:", description)
        self.assertEqual(metadata["owner"], "dad")
        self.assertEqual(metadata["category"], "shopping")

    def test_recurring_natural_order_with_count(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "creating recurring event nysha gymnastics 10 AM saturday for next 12 weeks",
            now=now,
        )

        self.assertEqual(intent["intent"], "create_event")
        self.assertEqual(intent["title"], "Nysha gymnastics")
        self.assertEqual(intent["date"], "2026-07-04")
        self.assertEqual(intent["start_time"], "10:00")
        self.assertEqual(intent["duration_minutes"], 60)
        self.assertEqual(intent["recurrence"], ["RRULE:FREQ=WEEKLY;COUNT=12;BYDAY=SA"])
        self.assertEqual(intent["recurrence_label"], "every Saturday")
        self.assertEqual(intent["missing_fields"], [])

    def test_recurring_natural_order_without_count(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "creating recurring event nysha gymnastics 10 AM saturday",
            now=now,
        )

        self.assertEqual(intent["intent"], "create_event")
        self.assertEqual(intent["title"], "Nysha gymnastics")
        self.assertEqual(intent["date"], "2026-07-04")
        self.assertEqual(intent["start_time"], "10:00")
        self.assertEqual(intent["recurrence"], ["RRULE:FREQ=WEEKLY;BYDAY=SA"])
        self.assertEqual(intent["missing_fields"], [])

    def test_recurring_every_saturday_natural_time(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("add Nysha gymnastics every Saturday at 10 AM", now=now)

        self.assertEqual(intent["title"], "Nysha gymnastics")
        self.assertEqual(intent["start_time"], "10:00")
        self.assertEqual(intent["recurrence"], ["RRULE:FREQ=WEEKLY;BYDAY=SA"])
        self.assertEqual(intent["missing_fields"], [])

    def test_recurring_every_week_after_weekday_and_time(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("add Nysha gymnastics Saturday 10 AM every week", now=now)

        self.assertEqual(intent["title"], "Nysha gymnastics")
        self.assertEqual(intent["date"], "2026-07-04")
        self.assertEqual(intent["start_time"], "10:00")
        self.assertEqual(intent["recurrence"], ["RRULE:FREQ=WEEKLY;BYDAY=SA"])
        self.assertEqual(intent["missing_fields"], [])

    def test_recurring_weekday_intent(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Add school pickup every weekday at 3pm", now=now)

        self.assertEqual(intent["title"], "School pickup")
        self.assertEqual(intent["date"], "2026-07-02")
        self.assertEqual(intent["start_time"], "15:00")
        self.assertEqual(intent["recurrence"], ["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"])
        self.assertEqual(intent["recurrence_label"], "every weekday")
        self.assertEqual(intent["missing_fields"], [])

    def test_recurring_daily_intent(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Add medication every day at 8am", now=now)

        self.assertEqual(intent["title"], "Medication")
        self.assertEqual(intent["date"], "2026-07-02")
        self.assertEqual(intent["start_time"], "08:00")
        self.assertEqual(intent["recurrence"], ["RRULE:FREQ=DAILY"])
        self.assertEqual(intent["recurrence_label"], "every day")
        self.assertEqual(intent["missing_fields"], [])

    def test_recurring_event_requires_time(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Add trash reminder every Sunday evening", now=now)

        self.assertEqual(intent["intent"], "create_event")
        self.assertIn("time", intent["missing_fields"])

    def test_relative_update_event_intent_later(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("move great mall visit event by 1 hour later", now=now)

        self.assertEqual(intent["intent"], "update_event")
        self.assertEqual(intent["query"], "great mall visit")
        self.assertIsNone(intent["new_start_time"])
        self.assertEqual(intent["relative_delta_minutes"], 60)
        self.assertEqual(intent["missing_fields"], [])

    def test_relative_update_event_intent_up_by_one_hour(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("move great mall visit up by 1 hour", now=now)

        self.assertEqual(intent["intent"], "update_event")
        self.assertEqual(intent["query"], "great mall visit")
        self.assertIsNone(intent["new_start_time"])
        self.assertEqual(intent["relative_delta_minutes"], -60)
        self.assertEqual(intent["missing_fields"], [])

    def test_relative_update_event_intent_later_by_one_hour(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("move great mall visit later by 1 hour", now=now)

        self.assertEqual(intent["query"], "great mall visit")
        self.assertEqual(intent["relative_delta_minutes"], 60)
        self.assertEqual(intent["missing_fields"], [])

    def test_relative_update_event_intent_earlier(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("move great mall visit event 30 minutes earlier", now=now)

        self.assertEqual(intent["intent"], "update_event")
        self.assertEqual(intent["query"], "great mall visit")
        self.assertEqual(intent["relative_delta_minutes"], -30)
        self.assertEqual(intent["missing_fields"], [])

    def test_relative_update_event_intent_push(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("push dentist by 15 minutes", now=now)

        self.assertEqual(intent["intent"], "update_event")
        self.assertEqual(intent["query"], "dentist")
        self.assertEqual(intent["relative_delta_minutes"], 15)
        self.assertEqual(intent["missing_fields"], [])

    def test_relative_update_event_intent_push_back_one_hour(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("push back dinner by one hour", now=now)

        self.assertEqual(intent["query"], "dinner")
        self.assertEqual(intent["relative_delta_minutes"], 60)
        self.assertEqual(intent["missing_fields"], [])

    def test_relative_update_delay_by_an_hour_is_not_missing_time(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Delay the great mall shopping by an hour", now=now)

        self.assertEqual(intent["intent"], "update_event")
        self.assertEqual(intent["query"], "great mall shopping")
        self.assertEqual(intent["relative_delta_minutes"], 60)
        self.assertEqual(intent["missing_fields"], [])

    def test_relative_update_event_intent_half_hour_earlier(self):
        now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("move piano half an hour earlier", now=now)

        self.assertEqual(intent["query"], "piano")
        self.assertEqual(intent["relative_delta_minutes"], -30)
        self.assertEqual(intent["missing_fields"], [])


if __name__ == "__main__":
    unittest.main()

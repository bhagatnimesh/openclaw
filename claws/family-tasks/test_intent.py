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

    def test_next_weekday_task_uses_next_calendar_week(self):
        now = datetime(2026, 7, 7, 9, 31, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "Add task buy new water filter for next Wednesday",
            now=now,
        )

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(intent["title"], "Buy new water filter")
        self.assertEqual(intent["due"], "2026-07-15")

    def test_leading_next_weekday_task_keeps_action_title(self):
        now = datetime(2026, 7, 7, 9, 31, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "Add task for next Wednesday to buy new water filter",
            now=now,
        )

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(intent["title"], "Buy new water filter")
        self.assertEqual(intent["due"], "2026-07-15")

    def test_relative_week_from_now_task_uses_clean_title(self):
        now = datetime(2026, 7, 8, 10, 33, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "Add task follow up if solar response comes from the builder. "
            "Check one week from now",
            now=now,
        )

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(
            intent["title"],
            "Follow up if solar response comes from the builder",
        )
        self.assertEqual(intent["due"], "2026-07-15")

    def test_relative_in_two_weeks_task_uses_clean_title(self):
        now = datetime(2026, 7, 8, 10, 33, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Add task call builder in two weeks", now=now)

        self.assertEqual(intent["title"], "Call builder")
        self.assertEqual(intent["due"], "2026-07-22")

    def test_refined_task_header_and_body_create_title_and_notes(self):
        now = datetime(2026, 7, 8, 10, 33, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "\n".join(
                [
                    "Add task: Call FUSD about Nysha waitlist",
                    "Notes: Follow up with Chadbourne about the overflow waitlist.",
                    "Ask for the right contact and next steps.",
                ]
            ),
            now=now,
        )

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(intent["title"], "Call FUSD about Nysha waitlist")
        self.assertEqual(
            intent["notes"],
            "Follow up with Chadbourne about the overflow waitlist.\n"
            "Ask for the right contact and next steps.",
        )

    def test_refined_inline_task_header_and_body_create_title_and_notes(self):
        now = datetime(2026, 7, 8, 10, 33, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "Add task: Call builder Notes: Ask whether the solar response came in.",
            now=now,
        )

        self.assertEqual(intent["title"], "Call builder")
        self.assertEqual(intent["notes"], "Ask whether the solar response came in.")

    def test_below_email_followup_creates_readable_title_and_notes(self):
        now = datetime(2026, 7, 9, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "For below email to follow-up next Wednesday. Hello Nimesh Your close "
            "of escrow was back in April of 2021. When is it that the powering "
            "down occurred? I included the roofing contractor number.",
            now=now,
        )

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(intent["title"], "Follow up on email")
        self.assertEqual(intent["due"], "2026-07-15")
        self.assertEqual(
            intent["notes"],
            "Hello Nimesh Your close of escrow was back in April of 2021. "
            "When is it that the powering down occurred? I included the "
            "roofing contractor number.",
        )

    def test_create_task_extracts_dynamic_hashtag_tags(self):
        now = datetime(2026, 7, 7, 9, 31, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "Add task buy new water filter #Shopping #home #shopping",
            now=now,
        )

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(intent["title"], "Buy new water filter")
        self.assertEqual(intent["metadata"]["tags"], ["shopping", "home"])

    def test_create_task_extracts_plain_tag_annotation(self):
        now = datetime(2026, 7, 21, 20, 39, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "Add task check letters due august first and tag IndiaTrip",
            now=now,
        )

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(intent["title"], "Check letters")
        self.assertEqual(intent["due"], "2026-08-01")
        self.assertEqual(intent["metadata"]["tags"], ["indiatrip"])

    def test_numeric_hash_in_title_is_not_a_tag(self):
        now = datetime(2026, 7, 7, 9, 31, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Add task buy #2 pencils #kids", now=now)

        self.assertEqual(intent["title"], "Buy #2 pencils")
        self.assertEqual(intent["metadata"]["tags"], ["kids"])

    def test_url_fragment_in_title_is_not_a_tag(self):
        now = datetime(2026, 7, 7, 9, 31, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "Add task read https://docs.example.com/#install #research",
            now=now,
        )

        self.assertEqual(intent["title"], "Read https://docs.example.com/#install")
        self.assertEqual(intent["metadata"]["tags"], ["research"])

    def test_bare_packing_list_creates_due_task(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "to pack beach matt, sunc screen, fruits, water for the trip tomorrow",
            now=now,
        )

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(
            intent["title"],
            "Pack beach matt, sunc screen, fruits, water for the trip",
        )
        self.assertEqual(intent["due"], "2026-07-04")
        self.assertEqual(intent["metadata"]["urgency"], "medium")

    def test_voice_transcription_and_a_task_creates_task(self):
        now = datetime(2026, 7, 3, 20, 5, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "and a task for tomorrow to order the lock",
            now=now,
        )

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(intent["title"], "Order the lock")
        self.assertEqual(intent["due"], "2026-07-04")
        self.assertEqual(intent["metadata"]["effort_type"], "admin")

    def test_thought_like_task_capture_uses_clean_title(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "I had a task to cancel Fox 1 subscription owner unknown "
            "due September first",
            now=now,
        )

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(intent["title"], "Cancel Fox 1 subscription")
        self.assertEqual(intent["due"], "2026-09-01")
        self.assertEqual(intent["metadata"]["owner"], "unknown")

    def test_voice_task_assignment_chatter_is_removed_from_title(self):
        now = datetime(2026, 7, 5, 22, 4, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "Add a task for tomorrow at 2pm to call up home warranty to check "
            "how to handle with the solar panel, challenge, assign the task to Namesh",
            now=now,
        )

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(intent["title"], "Call home warranty about the solar panel")
        self.assertEqual(intent["due"], "2026-07-06")
        self.assertEqual(intent["metadata"]["owner"], "dad")
        self.assertIsNone(intent["notes"])

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

    def test_explicit_niyati_owner_and_month_day_due_date(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "Add task cancel or downgrade gemini. owner niyati. "
            "Do it by september first",
            now=now,
        )

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(intent["title"], "Cancel or downgrade gemini")
        self.assertEqual(intent["due"], "2026-09-01")
        self.assertEqual(intent["metadata"]["owner"], "mom")

    def test_explicit_household_owner_aliases(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        cases = {
            "mum": "mom",
            "mummy": "mom",
            "namesh": "dad",
            "niyaati": "mom",
            "niyathi": "mom",
            "papa": "dad",
            "papu": "dad",
            "dadi": "grandmom",
        }

        for alias, owner in cases.items():
            with self.subTest(alias=alias):
                intent = extract_intent(
                    f"Add task return library books owner {alias}",
                    now=now,
                )
                self.assertEqual(intent["metadata"]["owner"], owner)

    def test_bare_time_bound_call_is_not_task_creation(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Call Rahul tomorrow at 5pm", now=now)

        self.assertEqual(intent["intent"], "recommend_tasks")

    def test_call_task_with_ai_assistant_help_metadata(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "\n".join(
                [
                    "call FUSD for following up on Nysha's waitlist status for Chadbourne",
                    "I want AI assistant",
                    "Help: look up the FUSD phone number and draft quick talking points",
                    "Email: waitlist@example.com",
                ]
            ),
            now=now,
        )

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(
            intent["title"],
            "Call FUSD for following up on Nysha's waitlist status for Chadbourne",
        )
        self.assertEqual(
            intent["notes"],
            "Assistant help: Look up the FUSD phone number and draft quick talking points\n"
            "Assistant context: Email: waitlist@example.com",
        )
        self.assertTrue(intent["metadata"]["assistant_help_needed"])
        self.assertEqual(
            intent["metadata"]["assistant_help_request"],
            "Look up the FUSD phone number and draft quick talking points",
        )
        self.assertEqual(
            intent["metadata"]["assistant_context"],
            "Email: waitlist@example.com",
        )
        self.assertEqual(intent["metadata"]["effort_type"], "communication")

    def test_call_task_with_noah_assistant_help_metadata(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "\n".join(
                [
                    "Ask Noah to help",
                    "call FUSD for following up on Nysha's waitlist status for Chadbourne",
                    "Help: look up the FUSD phone number and draft quick talking points",
                ]
            ),
            now=now,
        )

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(
            intent["title"],
            "Call FUSD for following up on Nysha's waitlist status for Chadbourne",
        )
        self.assertTrue(intent["metadata"]["assistant_help_needed"])
        self.assertEqual(intent["metadata"]["assistant_name"], "Noah")
        self.assertEqual(
            intent["metadata"]["assistant_help_request"],
            "Look up the FUSD phone number and draft quick talking points",
        )

    def test_ask_noah_to_help_prefix_creates_task(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "Ask Noah to help call FUSD about Nysha's waitlist",
            now=now,
        )

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(intent["title"], "Call FUSD about Nysha's waitlist")
        self.assertTrue(intent["metadata"]["assistant_help_needed"])
        self.assertEqual(intent["metadata"]["assistant_name"], "Noah")

    def test_run_noah_assistant_help_intent(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Run Noah assistant help", now=now)

        self.assertEqual(intent["intent"], "run_assistant_help")

    def test_direct_noah_research_request_creates_assistant_task(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "Ask Noah to help look up the FUSD phone number",
            now=now,
        )

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(intent["title"], "Look up the FUSD phone number")
        self.assertTrue(intent["metadata"]["assistant_help_needed"])
        self.assertEqual(
            intent["metadata"]["assistant_help_request"],
            "Look up the FUSD phone number",
        )

    def test_direct_noah_find_out_request_creates_assistant_task(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "I want Noah to find out the FUSD number to call",
            now=now,
        )

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(intent["title"], "Find out the FUSD number to call")
        self.assertTrue(intent["metadata"]["assistant_help_needed"])
        self.assertEqual(
            intent["metadata"]["assistant_help_request"],
            "Find out the FUSD number to call",
        )

    def test_polite_timed_task_request_with_inline_ai_help_creates_task(self):
        now = datetime(2026, 7, 3, 21, 56, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
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
            now=now,
        )

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(
            intent["title"],
            "Call FUSD to follow up on Nyshas School waiting list for Chad Bond",
        )
        self.assertEqual(intent["due"], "2026-07-06")
        self.assertIn("Assistant help: Find out FUSD number", intent["notes"])
        self.assertNotIn("This task is for Namesh", intent["title"])
        self.assertTrue(intent["metadata"]["assistant_help_needed"])
        self.assertIn(
            "Find out FUSD number to call",
            intent["metadata"]["assistant_help_request"],
        )
        self.assertIn(
            "Nyshad to meet Chad Bond",
            intent["metadata"]["assistant_context"],
        )
        self.assertEqual(intent["metadata"]["effort_type"], "communication")

    def test_polite_timed_task_request_with_noah_help_creates_task(self):
        now = datetime(2026, 7, 3, 22, 6, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
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
            now=now,
        )

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(
            intent["title"],
            "Call FUSD to follow up on Nyshas School waiting list for Chad Bond",
        )
        self.assertEqual(intent["due"], "2026-07-06")
        self.assertIn("Assistant help: Find out FUSD number", intent["notes"])
        self.assertTrue(intent["metadata"]["assistant_help_needed"])
        self.assertEqual(intent["metadata"]["assistant_name"], "Noah")
        self.assertIn(
            "Find out FUSD number to call",
            intent["metadata"]["assistant_help_request"],
        )
        self.assertIn(
            "Nyshad to meet Chad Bond",
            intent["metadata"]["assistant_context"],
        )

    def test_dropped_subject_timed_task_request_creates_task(self):
        now = datetime(2026, 7, 3, 22, 20, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent(
            "\n".join(
                [
                    "want to add a task for Monday at 3 p.m. to call FUSD "
                    "to follow up on Nyshas School waiting",
                    "list for Chad Bond. This task is for Namesh. "
                    "I want Noah to find out FUSD number to call",
                    "and the key talking points. I really want",
                    "Nyshad to meet Chad Bond from overflow",
                    "on ASS School to Mission Valley Monteserie",
                ]
            ),
            now=now,
        )

        self.assertEqual(intent["intent"], "create_task")
        self.assertEqual(
            intent["title"],
            "Call FUSD to follow up on Nyshas School waiting list for Chad Bond",
        )
        self.assertEqual(intent["due"], "2026-07-06")
        self.assertTrue(intent["metadata"]["assistant_help_needed"])
        self.assertEqual(intent["metadata"]["assistant_name"], "Noah")
        self.assertIn(
            "Nyshad to meet Chad Bond",
            intent["metadata"]["assistant_context"],
        )

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

    def test_hashtag_recommendation_intent(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("Show me #finance #home tasks", now=now)

        self.assertEqual(intent["intent"], "recommend_tasks")
        self.assertEqual(intent["filters"]["tags"], ["finance", "home"])

    def test_tag_phrase_recommendation_intent(self):
        now = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("List all tasks with tag finance", now=now)

        self.assertEqual(intent["intent"], "recommend_tasks")
        self.assertEqual(intent["filters"], {"tags": ["finance"]})

    def test_tag_colon_recommendation_intent(self):
        now = datetime(2026, 7, 7, 12, 3, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("List tasks for tag:drive", now=now)

        self.assertEqual(intent["intent"], "recommend_tasks")
        self.assertEqual(intent["filters"], {"tags": ["drive"]})

    def test_list_tasks_for_word_uses_tag_filter(self):
        now = datetime(2026, 7, 7, 12, 1, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        intent = extract_intent("List all tasks for drive", now=now)

        self.assertEqual(intent["intent"], "recommend_tasks")
        self.assertEqual(intent["filters"], {"tags": ["drive"]})

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
        self.assertFalse(metadata["assistant_help_needed"])

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

    def test_metadata_helpers_preserve_assistant_help(self):
        notes = write_metadata_to_notes(
            "Assistant help: Look up the FUSD phone number",
            {
                "assistant_help_needed": True,
                "assistant_help_request": "Look up the FUSD phone number",
                "assistant_context": "Email: waitlist@example.com",
            },
        )

        human_notes, metadata = read_metadata_from_notes(notes)

        self.assertEqual(human_notes, "Assistant help: Look up the FUSD phone number")
        self.assertTrue(metadata["assistant_help_needed"])
        self.assertEqual(metadata["assistant_help_request"], "Look up the FUSD phone number")
        self.assertEqual(metadata["assistant_context"], "Email: waitlist@example.com")


if __name__ == "__main__":
    unittest.main()

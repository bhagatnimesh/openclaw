import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from calendar_parsing_experiment import (
    CalendarAIFieldCache,
    CalendarParsingCase,
    _api_context_fields_to_legacy_fields,
    _intent_from_ai_fields,
    load_sqlite_raw_input_cases,
    load_trajectory_cases,
    run_current_parser,
    run_proposed_ai_parser,
    run_experiment,
    score_output,
)


REFERENCE_TIME = datetime(2026, 8, 12, 18, 0, tzinfo=ZoneInfo("America/Los_Angeles"))


class FakeExtractor:
    model = "fake-calendar-fields"

    def __init__(self):
        self.calls = 0

    def extract(self, request, *, now=None, baseline_intent=None, context=None):
        self.calls += 1
        return {
            "action": "create_event",
            "confidence": 0.94,
            "slots": {
                "title": "Dinner",
                "date": "2026-08-13",
                "start_time": "18:00",
                "duration_minutes": 60,
                "guest_aliases": ["mom"],
                "calendar_name": "Family calendar",
            },
            "missing_fields": [],
        }


class StaticExtractor:
    model = "static-test-fields"

    def __init__(self, fields):
        self.fields = fields

    def extract(self, request, *, now=None, baseline_intent=None, context=None):
        return dict(self.fields)


class CalendarParsingExperimentTest(unittest.TestCase):
    def test_trajectory_loader_reads_telegram_user_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "2026-08.md").write_text(
                "\n".join(
                    [
                        "## 2026-08-12T18:04:00",
                        "",
                        "- Source: telegram_text:nimesh",
                        "",
                        "User:",
                        "",
                        "  invite mom to dinner tomorrow at 6",
                        "",
                        "Assistant:",
                        "",
                        "  Created calendar event.",
                        "",
                        "## 2026-08-12T18:05:00",
                        "",
                        "- Source: web",
                        "",
                        "User:",
                        "",
                        "  ignore this",
                    ]
                ),
                encoding="utf-8",
            )

            cases = load_trajectory_cases(root)

        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].source, "telegram_text:nimesh")
        self.assertEqual(cases[0].utterance, "invite mom to dinner tomorrow at 6")

    def test_sqlite_loader_reads_telegram_raw_inputs_only(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "n4os.db"
            with sqlite3.connect(db_path) as db:
                db.execute(
                    "create table library_reading_events "
                    "(id text, raw_input text, source text, created_at text)"
                )
                db.execute(
                    "insert into library_reading_events values (?, ?, ?, ?)",
                    (
                        "one",
                        "Nysha read 8 pages",
                        "telegram_text",
                        "2026-08-12T18:04:00",
                    ),
                )
                db.execute(
                    "insert into library_reading_events values (?, ?, ?, ?)",
                    ("two", "browser input", "web", "2026-08-12T18:05:00"),
                )

            cases = load_sqlite_raw_input_cases(db_path)

        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].case_id, "sqlite-library_reading_events-one")
        self.assertEqual(cases[0].utterance, "Nysha read 8 pages")

    def test_current_parser_scores_actionable_calendar_create(self):
        case = CalendarParsingCase(
            case_id="calendar-create",
            utterance="Add dentist tomorrow at 3 PM",
            source="test",
            origin="test",
            expected_route="calendar",
            expected_action="create_event",
        )

        scored = score_output(case, run_current_parser(case.utterance, now=REFERENCE_TIME))

        self.assertTrue(scored.success, scored)
        self.assertEqual(scored.reason, "actionable")

    def test_proposed_ai_parser_scores_calendar_slots_and_cache(self):
        case = CalendarParsingCase(
            case_id="calendar-ai",
            utterance="invite mom to dinner tomorrow at 6",
            source="test",
            origin="test",
            expected_route="calendar",
            expected_action="create_event",
            expected_slots={
                "title": "Dinner",
                "date": "2026-08-13",
                "start_time": "18:00",
                "target_calendar": "Family calendar",
            },
        )
        extractor = FakeExtractor()

        with tempfile.TemporaryDirectory() as temp:
            cache = CalendarAIFieldCache(Path(temp) / "cache.json")
            with patch.dict("os.environ", {"N4OS_CALENDAR_MOM_GUEST_EMAIL": "mom@example.test"}):
                first = run_experiment((case,), extractor=extractor, cache=cache, reference_time=REFERENCE_TIME)
                second = run_experiment((case,), extractor=extractor, cache=cache, reference_time=REFERENCE_TIME)
            cached_payload = json.loads((Path(temp) / "cache.json").read_text(encoding="utf-8"))

        self.assertEqual(extractor.calls, 1)
        self.assertEqual(len(cached_payload), 1)
        self.assertEqual(first.summary()["proposed"]["success_rate"], 1.0)
        self.assertEqual(second.summary()["proposed"]["success_rate"], 1.0)

    def test_api_context_response_maps_google_event_draft_to_fields(self):
        fields = _api_context_fields_to_legacy_fields(
            {
                "operation": "create_event",
                "confidence": 0.96,
                "calendar": {"name": "kids calendar"},
                "event": {
                    "summary": "Soccer practice",
                    "start": {
                        "dateTime": "2026-08-20T17:30:00-07:00",
                        "timeZone": "America/Los_Angeles",
                    },
                    "end": {
                        "dateTime": "2026-08-20T19:00:00-07:00",
                        "timeZone": "America/Los_Angeles",
                    },
                    "attendees": [{"alias": "mom"}, {"alias": "dad"}],
                },
                "sendUpdates": "all",
                "missing_fields": [],
            },
            "Add soccer practice Aug 20 at 5:30pm for 90 mins to kids calendar",
        )

        self.assertEqual(fields["action"], "create_event")
        self.assertEqual(
            fields["slots"],
            {
                "title": "Soccer practice",
                "calendar_name": "kids calendar",
                "date": "2026-08-20",
                "start_time": "17:30",
                "duration_minutes": 90,
                "guest_aliases": ["mom", "dad"],
            },
        )

    def test_api_context_response_maps_list_window_to_date(self):
        fields = _api_context_fields_to_legacy_fields(
            {
                "operation": "list_events",
                "confidence": 0.96,
                "list": {
                    "timeMin": "2026-08-13T00:00:00-07:00",
                    "timeMax": "2026-08-14T00:00:00-07:00",
                },
                "missing_fields": [],
            },
            "show me tomorrow's events pls",
        )

        self.assertEqual(fields["action"], "list_events")
        self.assertEqual(fields["slots"], {"date": "2026-08-13"})

    def test_api_context_response_maps_all_day_and_recurrence(self):
        fields = _api_context_fields_to_legacy_fields(
            {
                "operation": "create_event",
                "confidence": 0.96,
                "event": {
                    "summary": "Trash pickup",
                    "start": {"date": "2026-08-13", "timeZone": "America/Los_Angeles"},
                    "end": {"date": "2026-08-14", "timeZone": "America/Los_Angeles"},
                    "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=TH"],
                },
                "missing_fields": [],
            },
            "Trash pickup every Thursday all day starting Aug 13",
        )

        self.assertEqual(fields["action"], "create_event")
        self.assertEqual(
            fields["slots"],
            {
                "title": "Trash pickup",
                "date": "2026-08-13",
                "all_day": True,
                "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=TH"],
            },
        )

    def test_ai_intent_keeps_guest_aliases_without_requiring_configured_emails(self):
        intent = _intent_from_ai_fields(
            {
                "action": "create_event",
                "confidence": 0.96,
                "slots": {
                    "title": "Family brunch",
                    "date": "2026-08-16",
                    "start_time": "10:00",
                    "guest_aliases": ["mom", "dad"],
                },
                "missing_fields": [],
            },
            "/calendar Family brunch this Sunday 10:00am, invite mom and dad",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["guest_aliases"], ["mom", "dad"])
        self.assertNotIn("guest_contacts", intent["missing_fields"])

    def test_ai_path_repairs_calendar_list_action_from_request_text(self):
        case = CalendarParsingCase(
            case_id="calendar-list",
            utterance="/calendar Any events on Aug 18?",
            source="test",
            origin="test",
            expected_route="calendar",
            expected_action="list_events",
            expected_slots={"date": "2026-08-18"},
        )
        extractor = StaticExtractor(
            {
                "action": "create_event",
                "confidence": 0.97,
                "slots": {},
                "missing_fields": ["time", "title", "date"],
            }
        )

        scored = score_output(
            case,
            run_proposed_ai_parser(
                case.utterance,
                now=REFERENCE_TIME,
                extractor=extractor,
            ),
        )

        self.assertTrue(scored.success, scored)

    def test_ai_path_repairs_missing_next_weekday_date_from_request_text(self):
        case = CalendarParsingCase(
            case_id="calendar-create-next-weekday",
            utterance="/calendar Game night next Sat 7pm at the house, put on family calendar",
            source="test",
            origin="test",
            expected_route="calendar",
            expected_action="create_event",
            expected_slots={
                "date": "2026-08-22",
                "start_time": "19:00",
                "target_calendar": "family calendar",
            },
        )
        extractor = StaticExtractor(
            {
                "action": "create_event",
                "confidence": 0.97,
                "slots": {"title": "Game night"},
                "missing_fields": ["date"],
            }
        )

        scored = score_output(
            case,
            run_proposed_ai_parser(
                case.utterance,
                now=REFERENCE_TIME,
                extractor=extractor,
            ),
        )

        self.assertTrue(scored.success, scored)

    def test_ai_path_overrides_wrong_ai_date_for_explicit_next_weekday(self):
        case = CalendarParsingCase(
            case_id="calendar-create-next-thursday",
            utterance="/calendar Kids back to school photos next Thursday 5pm, 1 hour",
            source="test",
            origin="test",
            expected_route="calendar",
            expected_action="create_event",
            expected_slots={
                "date": "2026-08-20",
                "start_time": "17:00",
            },
        )
        extractor = StaticExtractor(
            {
                "action": "create_event",
                "confidence": 0.99,
                "slots": {
                    "title": "Kids back to school photos",
                    "date": "2026-08-13",
                    "start_time": "17:00",
                },
                "missing_fields": [],
            }
        )

        scored = score_output(
            case,
            run_proposed_ai_parser(
                case.utterance,
                now=REFERENCE_TIME,
                extractor=extractor,
            ),
        )

        self.assertTrue(scored.success, scored)

    def test_ai_path_prefers_explicit_calendar_target_over_ai_guess(self):
        case = CalendarParsingCase(
            case_id="calendar-create-target",
            utterance="/calendar Anniversary dinner Aug 12 at 8pm, on our calendar and family can come",
            source="test",
            origin="test",
            expected_route="calendar",
            expected_action="create_event",
            expected_slots={
                "target_calendar": "our calendar",
                "guest_aliases": ["mom", "dad"],
            },
        )
        extractor = StaticExtractor(
            {
                "action": "create_event",
                "confidence": 0.98,
                "slots": {
                    "title": "Anniversary dinner",
                    "date": "2026-08-12",
                    "start_time": "20:00",
                    "calendar_name": "family calendar",
                    "guest_aliases": ["family"],
                },
                "missing_fields": [],
            }
        )

        scored = score_output(
            case,
            run_proposed_ai_parser(
                case.utterance,
                now=REFERENCE_TIME,
                extractor=extractor,
            ),
        )

        self.assertTrue(scored.success, scored)

    def test_ai_path_repairs_multi_day_recurrence_and_description_from_request(self):
        intent = _intent_from_ai_fields(
            {
                "action": "create_event",
                "confidence": 0.95,
                "slots": {
                    "title": "Math tutor",
                    "date": "2026-08-17",
                    "start_time": "17:00",
                    "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=MO"],
                },
                "missing_fields": [],
            },
            "/calendar Schedule math tutor every Monday and Wednesday at 5pm starting Aug 17 for 1 hour, description bring workbook",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["recurrence"], ["RRULE:FREQ=WEEKLY;BYDAY=MO,WE"])
        self.assertEqual(intent["description"], "bring workbook")

    def test_ai_path_keeps_create_action_when_notes_contain_list(self):
        case = CalendarParsingCase(
            case_id="calendar-notes-list",
            utterance="/calendar Add spelling test prep every Tuesday and Thursday at 6pm "
            "starting Aug 18 for 45 minutes on Nysha school calendar, notes bring word list",
            source="test",
            origin="test",
            expected_route="calendar",
            expected_action="create_event",
            expected_slots={
                "description": "bring word list",
                "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=TU,TH"],
            },
        )
        extractor = StaticExtractor(
            {
                "action": "list_events",
                "confidence": 0.91,
                "slots": {
                    "title": "Spelling test prep",
                    "date": "2026-08-18",
                    "start_time": "18:00",
                    "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=TU"],
                    "calendar_name": "Nysha school calendar",
                },
                "missing_fields": [],
            }
        )

        scored = score_output(
            case,
            run_proposed_ai_parser(case.utterance, now=REFERENCE_TIME, extractor=extractor),
        )

        self.assertTrue(scored.success, scored)

    def test_ai_path_repairs_guest_only_invite_update(self):
        case = CalendarParsingCase(
            case_id="calendar-add-guests",
            utterance="/calendar Add dad to the invite for parent teacher conference",
            source="test",
            origin="test",
            expected_route="calendar",
            expected_action="add_guests",
            expected_slots={"guest_aliases": ["dad"], "target_reference": "parent teacher conference"},
        )
        extractor = StaticExtractor(
            {
                "action": "create_event",
                "confidence": 0.88,
                "slots": {"guest_aliases": ["dad"]},
                "missing_fields": ["title", "date", "time"],
            }
        )

        scored = score_output(
            case,
            run_proposed_ai_parser(case.utterance, now=REFERENCE_TIME, extractor=extractor),
        )

        self.assertTrue(scored.success, scored)

    def test_ai_path_repairs_ordinal_monthly_recurrence(self):
        case = CalendarParsingCase(
            case_id="calendar-monthly-ordinal",
            utterance="/calendar Add school assembly every first Monday at 8:30am "
            "starting Sep 7 for 45 minutes on Nysha school calendar",
            source="test",
            origin="test",
            expected_route="calendar",
            expected_action="create_event",
            expected_slots={"recurrence": ["RRULE:FREQ=MONTHLY;BYDAY=MO;BYSETPOS=1"]},
        )
        extractor = StaticExtractor(
            {
                "action": "create_event",
                "confidence": 0.95,
                "slots": {
                    "title": "School assembly",
                    "date": "2026-09-07",
                    "start_time": "08:30",
                    "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=MO"],
                    "calendar_name": "Nysha school calendar",
                },
                "missing_fields": [],
            }
        )

        scored = score_output(
            case,
            run_proposed_ai_parser(case.utterance, now=REFERENCE_TIME, extractor=extractor),
        )

        self.assertTrue(scored.success, scored)


if __name__ == "__main__":
    unittest.main()

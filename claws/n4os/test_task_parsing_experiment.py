import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

from task_parsing_experiment import (
    DEFAULT_CASE_PATH,
    TaskAIFieldCache,
    TaskParsingCase,
    ParserOutput,
    _api_context_fields_to_legacy_fields,
    _intent_from_ai_fields,
    load_task_cases,
    main,
    run_current_parser,
    run_experiment,
    run_proposed_ai_parser,
    score_output,
)


REFERENCE_TIME = datetime(2026, 8, 13, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))


class FakeExtractor:
    model = "fake-task-fields"

    def __init__(self):
        self.calls = 0

    def extract(self, request, *, now=None, baseline_intent=None, context=None):
        self.calls += 1
        return {
            "action": "create_task",
            "confidence": 0.94,
            "slots": {
                "title": "Compare solar quotes",
                "due": "2026-08-20",
                "notes": "Summarize warranty differences.",
                "metadata": {
                    "tags": ["home"],
                    "effort_type": "research",
                    "assistant_help_needed": True,
                    "assistant_name": "Noah",
                    "assistant_help_request": "Summarize warranty differences",
                },
            },
            "missing_fields": [],
        }


class StaticExtractor:
    model = "static-task-fields"

    def __init__(self, fields):
        self.fields = fields

    def extract(self, request, *, now=None, baseline_intent=None, context=None):
        return dict(self.fields)


class FailingExtractor:
    model = "failing-task-fields"

    def extract(self, request, *, now=None, baseline_intent=None, context=None):
        raise RuntimeError("boom")


class TaskParsingExperimentTest(unittest.TestCase):
    def test_task_corpus_has_expected_scored_examples(self):
        cases = load_task_cases()
        task_cases = [case for case in cases if case.expected_route == "tasks"]
        negative_controls = [case for case in cases if case.expected_route != "tasks"]

        self.assertEqual(len(task_cases), 32)
        self.assertGreaterEqual(len(negative_controls), 3)
        self.assertTrue(all(case.scored for case in cases))

    def test_current_parser_scores_basic_task_create(self):
        case = TaskParsingCase(
            case_id="task-create",
            utterance="/task change water filter this weekend",
            expected_route="tasks",
            expected_action="create_task",
            expected_slots={
                "title": "Change water filter",
                "due": "2026-08-15",
                "metadata": {
                    "context": ["home"],
                    "energy": "medium",
                    "duration_minutes": 15,
                    "urgency": "medium",
                    "complexity": "low",
                    "effort_type": "physical",
                    "requires": ["equipment"],
                    "location": "home",
                },
            },
        )

        scored = score_output(case, run_current_parser(case.utterance, now=REFERENCE_TIME))

        self.assertTrue(scored.success, scored)
        self.assertEqual(scored.reason, "actionable")

    def test_run_experiment_defaults_to_fixed_corpus_reference_time(self):
        case = TaskParsingCase(
            case_id="task-create-water-filter",
            utterance="/task change water filter this weekend",
            expected_route="tasks",
            expected_action="create_task",
            expected_slots={"due": "2026-08-15"},
        )

        default_report = run_experiment((case,))
        explicit_report = run_experiment((case,), reference_time=REFERENCE_TIME)
        future_report = run_experiment(
            (case,),
            reference_time=datetime(2026, 8, 20, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles")),
        )

        self.assertEqual(
            default_report.outcomes[0].current.output.task_intent["due"],
            explicit_report.outcomes[0].current.output.task_intent["due"],
        )
        self.assertNotEqual(
            default_report.outcomes[0].current.output.task_intent["due"],
            future_report.outcomes[0].current.output.task_intent["due"],
        )

    def test_current_parser_preserves_explicit_update_action(self):
        output = run_current_parser("/task assign water filter task to dad", now=REFERENCE_TIME)

        self.assertEqual(output.route, "tasks")
        self.assertEqual(output.action, "update_task")
        self.assertEqual(output.task_intent["query"], "water filter")
        self.assertEqual(output.task_intent["update"], {"owner": "dad"})

    def test_current_parser_preserves_followup_tag_update(self):
        output = run_current_parser("/task add #cleanup", now=REFERENCE_TIME)

        self.assertEqual(output.route, "tasks")
        self.assertEqual(output.action, "update_task")
        self.assertEqual(output.task_intent["target"], "last_task")
        self.assertEqual(output.task_intent["update"], {"tags": ["cleanup"]})

    def test_current_parser_preserves_explicit_tagged_create(self):
        output = run_current_parser("/task add #kids birthday party plan", now=REFERENCE_TIME)

        self.assertEqual(output.route, "tasks")
        self.assertEqual(output.action, "create_task")
        self.assertEqual(output.task_intent["title"], "Birthday party plan")
        self.assertEqual(output.task_intent["metadata"]["tags"], ["kids"])

    def test_current_parser_preserves_explicit_note_update_target(self):
        output = run_current_parser(
            "/task add note buy the 20x25x1 size to water filter",
            now=REFERENCE_TIME,
        )

        self.assertEqual(output.route, "tasks")
        self.assertEqual(output.action, "update_task")
        self.assertEqual(output.task_intent["query"], "water filter")
        self.assertEqual(output.task_intent["update"], {"note": "buy the 20x25x1 size"})

    def test_current_parser_preserves_explicit_tag_update_target(self):
        output = run_current_parser("/task add #cleanup to water filter", now=REFERENCE_TIME)

        self.assertEqual(output.route, "tasks")
        self.assertEqual(output.action, "update_task")
        self.assertEqual(output.task_intent["query"], "water filter")
        self.assertEqual(output.task_intent["update"], {"tags": ["cleanup"]})

    def test_current_parser_preserves_pronoun_tag_update(self):
        output = run_current_parser(
            "/task update the task with tags #commute #india",
            now=REFERENCE_TIME,
        )

        self.assertEqual(output.route, "tasks")
        self.assertEqual(output.action, "update_task")
        self.assertEqual(output.task_intent["target"], "last_task")
        self.assertNotIn("query", output.task_intent)
        self.assertEqual(output.task_intent["update"], {"tags": ["commute", "india"]})

    def test_current_parser_preserves_tags_colon_followup_update(self):
        output = run_current_parser("/task tags: finance, school", now=REFERENCE_TIME)

        self.assertEqual(output.route, "tasks")
        self.assertEqual(output.action, "update_task")
        self.assertEqual(output.task_intent["target"], "last_task")
        self.assertEqual(output.task_intent["update"], {"tags": ["finance", "school"]})

    def test_current_parser_preserves_noah_followup_update(self):
        output = run_current_parser(
            "/task add Noah to help me find the right phone screen",
            now=REFERENCE_TIME,
        )

        self.assertEqual(output.route, "tasks")
        self.assertEqual(output.action, "update_task")
        self.assertEqual(output.task_intent["target"], "last_task")
        self.assertEqual(
            output.task_intent["update"],
            {"assistant_help_request": "find the right phone screen"},
        )

    def test_current_parser_keeps_this_weekend_update_query_explicit(self):
        output = run_current_parser(
            "/task assign this weekend water filter task to dad",
            now=REFERENCE_TIME,
        )

        self.assertEqual(output.action, "update_task")
        self.assertEqual(output.task_intent["query"], "this weekend water filter")
        self.assertNotIn("target", output.task_intent)
        self.assertEqual(output.task_intent["update"], {"owner": "dad"})

    def test_proposed_parser_runs_context_only_owner_update(self):
        output = run_proposed_ai_parser(
            "owner is nimesh",
            now=REFERENCE_TIME,
            extractor=StaticExtractor(
                {
                    "action": "update_task",
                    "confidence": 0.94,
                    "slots": {"update": {"owner": "dad"}},
                    "missing_fields": [],
                }
            ),
        )

        self.assertEqual(output.status, "recovered")
        self.assertEqual(output.route, "tasks")
        self.assertEqual(output.action, "update_task")
        self.assertEqual(output.task_intent["target"], "last_task")
        self.assertEqual(output.task_intent["update"], {"owner": "dad"})

    def test_proposed_parser_runs_natural_owner_assignment_update(self):
        output = run_proposed_ai_parser(
            "assign water filter task to dad",
            now=REFERENCE_TIME,
            extractor=StaticExtractor(
                {
                    "action": "update_task",
                    "confidence": 0.94,
                    "slots": {},
                    "missing_fields": [],
                }
            ),
        )

        self.assertEqual(output.status, "recovered")
        self.assertEqual(output.route, "tasks")
        self.assertEqual(output.action, "update_task")
        self.assertEqual(output.task_intent["query"], "water filter")
        self.assertEqual(output.task_intent["update"], {"owner": "dad"})

    def test_proposed_parser_retries_plain_task_action_on_router_miss(self):
        output = run_proposed_ai_parser(
            "repair water filter this weekend",
            now=REFERENCE_TIME,
            current=ParserOutput(
                status="ok",
                route="unknown",
                action="unknown",
                confidence=0.4,
            ),
            extractor=StaticExtractor(
                {
                    "action": "create_task",
                    "confidence": 0.94,
                    "slots": {"title": "Repair water filter", "due": "2026-08-15"},
                    "missing_fields": [],
                }
            ),
        )

        self.assertEqual(output.status, "recovered")
        self.assertEqual(output.route, "tasks")
        self.assertEqual(output.action, "create_task")

    def test_proposed_parser_does_not_hijack_unknown_generic_change_request(self):
        output = run_proposed_ai_parser(
            "change dentist appointment to Friday",
            now=REFERENCE_TIME,
            current=ParserOutput(
                status="ok",
                route="unknown",
                action="unknown",
                confidence=0.4,
            ),
            extractor=StaticExtractor(
                {
                    "action": "create_task",
                    "confidence": 0.94,
                    "slots": {"title": "Change dentist appointment"},
                    "missing_fields": [],
                }
            ),
        )

        self.assertEqual(output.status, "not_task")
        self.assertEqual(output.route, "unknown")

    def test_proposed_parser_retries_timed_create_on_router_miss(self):
        output = run_proposed_ai_parser(
            "call builder in two weeks",
            now=REFERENCE_TIME,
            current=ParserOutput(
                status="ok",
                route="unknown",
                action="unknown",
                confidence=0.4,
            ),
            extractor=StaticExtractor(
                {
                    "action": "create_task",
                    "confidence": 0.94,
                    "slots": {"title": "Call builder", "due": "2026-08-27"},
                    "missing_fields": [],
                }
            ),
        )

        self.assertEqual(output.route, "tasks")
        self.assertEqual(output.action, "create_task")

    def test_proposed_parser_does_not_hijack_resolved_non_task_route(self):
        output = run_proposed_ai_parser(
            "buy milk at Costco",
            now=REFERENCE_TIME,
            current=ParserOutput(
                status="ok",
                route="shopping",
                action="add_item",
                confidence=0.9,
            ),
            extractor=StaticExtractor(
                {
                    "action": "create_task",
                    "confidence": 0.94,
                    "slots": {"title": "Buy milk at Costco"},
                    "missing_fields": [],
                }
            ),
        )

        self.assertEqual(output.status, "not_task")
        self.assertEqual(output.route, "shopping")

    def test_proposed_parser_does_not_hijack_unknown_shopping_like_request(self):
        output = run_proposed_ai_parser(
            "buy milk at Costco",
            now=REFERENCE_TIME,
            current=ParserOutput(
                status="ok",
                route="unknown",
                action="unknown",
                confidence=0.4,
            ),
            extractor=StaticExtractor(
                {
                    "action": "create_task",
                    "confidence": 0.94,
                    "slots": {"title": "Buy milk at Costco"},
                    "missing_fields": [],
                }
            ),
        )

        self.assertEqual(output.status, "not_task")
        self.assertEqual(output.route, "unknown")

    def test_proposed_parser_recovers_unknown_route_update_followup(self):
        output = run_proposed_ai_parser(
            "tags: finance, school",
            now=REFERENCE_TIME,
            current=ParserOutput(
                status="ok",
                route="unknown",
                action="unknown",
                confidence=0.4,
            ),
            extractor=StaticExtractor(
                {
                    "action": "update_task",
                    "confidence": 0.95,
                    "slots": {
                        "target": "last_task",
                        "update": {"tags": ["finance", "school"]},
                    },
                    "missing_fields": [],
                }
            ),
        )

        self.assertEqual(output.status, "ok")
        self.assertEqual(output.route, "tasks")
        self.assertEqual(output.action, "update_task")
        self.assertEqual(output.task_intent["update"], {"tags": ["finance", "school"]})

    def test_proposed_parser_recovers_unknown_route_complete_followup(self):
        output = run_proposed_ai_parser(
            "complete water filter",
            now=REFERENCE_TIME,
            current=ParserOutput(
                status="ok",
                route="unknown",
                action="unknown",
                confidence=0.4,
            ),
            extractor=StaticExtractor(
                {
                    "action": "complete_task",
                    "confidence": 0.95,
                    "slots": {"query": "water filter"},
                    "missing_fields": [],
                }
            ),
        )

        self.assertEqual(output.status, "ok")
        self.assertEqual(output.route, "tasks")
        self.assertEqual(output.action, "complete_task")
        self.assertEqual(output.task_intent["query"], "water filter")

    def test_current_parser_preserves_owner_as_update(self):
        output = run_current_parser("/task mom as owner", now=REFERENCE_TIME)

        self.assertEqual(output.route, "tasks")
        self.assertEqual(output.action, "update_task")
        self.assertEqual(output.task_intent["target"], "last_task")
        self.assertEqual(output.task_intent["update"], {"owner": "mom"})

    def test_skipped_ai_runs_are_unscored(self):
        case = TaskParsingCase(
            case_id="task-not-run",
            utterance="/task buy filter",
            expected_route="tasks",
            expected_action="create_task",
        )

        scored = score_output(
            case,
            ParserOutput(
                status="not_run",
                route="tasks",
                action="create_task",
                confidence=0.0,
            ),
        )

        self.assertFalse(scored.scored)
        self.assertIsNone(scored.success)
        self.assertEqual(scored.reason, "not_run")

    def test_parser_errors_count_as_scored_failures(self):
        case = TaskParsingCase(
            case_id="task-error",
            utterance="/task buy filter",
            expected_route="tasks",
            expected_action="create_task",
        )

        scored = score_output(
            case,
            ParserOutput(
                status="error",
                route="tasks",
                action="create_task",
                confidence=0.0,
            ),
        )

        self.assertTrue(scored.scored)
        self.assertFalse(scored.success)
        self.assertEqual(scored.reason, "error")

    def test_proposed_ai_parser_scores_task_slots_and_cache(self):
        case = TaskParsingCase(
            case_id="task-ai",
            utterance="/task compare solar quotes by Aug 20 #home. Ask Noah to summarize warranty differences.",
            expected_route="tasks",
            expected_action="create_task",
            expected_slots={
                "title": "Compare solar quotes",
                "due": "2026-08-20",
                "notes": "Summarize warranty differences.",
                "metadata": {
                    "tags": ["home"],
                    "effort_type": "research",
                    "assistant_help_needed": True,
                    "assistant_name": "Noah",
                    "assistant_help_request": "Summarize warranty differences",
                },
            },
        )
        extractor = FakeExtractor()

        with tempfile.TemporaryDirectory() as temp:
            cache = TaskAIFieldCache(Path(temp) / "cache.json")
            first = run_experiment((case,), extractor=extractor, cache=cache, reference_time=REFERENCE_TIME)
            second = run_experiment((case,), extractor=extractor, cache=cache, reference_time=REFERENCE_TIME)
            cached_payload = json.loads((Path(temp) / "cache.json").read_text(encoding="utf-8"))

        self.assertEqual(extractor.calls, 1)
        self.assertEqual(len(cached_payload), 1)
        self.assertEqual(first.summary()["proposed"]["success_rate"], 1.0)
        self.assertEqual(second.summary()["proposed"]["success_rate"], 1.0)

    def test_proposed_ai_extractor_failure_counts_as_recovered_failure(self):
        case = TaskParsingCase(
            case_id="task-ai-error",
            utterance="/task change water filter this weekend",
            expected_route="tasks",
            expected_action="create_task",
        )

        scored = score_output(
            case,
            run_proposed_ai_parser(
                case.utterance,
                now=REFERENCE_TIME,
                extractor=FailingExtractor(),
            ),
        )

        self.assertTrue(scored.scored)
        self.assertFalse(scored.success)
        self.assertEqual(scored.reason, "recovered")

    def test_update_task_without_update_fields_is_not_actionable(self):
        case = TaskParsingCase(
            case_id="task-empty-update",
            utterance="/task update water filter",
            expected_route="tasks",
            expected_action="update_task",
        )

        scored = score_output(
            case,
            ParserOutput(
                status="ok",
                route="tasks",
                action="update_task",
                confidence=0.9,
                task_intent={
                    "intent": "update_task",
                    "query": "water filter",
                    "missing_fields": [],
                },
            ),
        )

        self.assertTrue(scored.scored)
        self.assertFalse(scored.success)
        self.assertEqual(scored.reason, "missing update")

    def test_hallucinated_owner_metadata_fails_when_not_expected(self):
        case = TaskParsingCase(
            case_id="task-extra-owner",
            utterance="/task buy milk",
            expected_route="tasks",
            expected_action="create_task",
            expected_slots={"title": "Buy milk"},
        )

        scored = score_output(
            case,
            ParserOutput(
                status="ok",
                route="tasks",
                action="create_task",
                confidence=0.9,
                task_intent={
                    "intent": "create_task",
                    "title": "Buy milk",
                    "metadata": {"owner": "dad"},
                    "missing_fields": [],
                },
            ),
        )

        self.assertFalse(scored.success)
        self.assertEqual(scored.reason, "slot metadata extra owner")

    def test_hallucinated_create_due_and_notes_fail_when_not_expected(self):
        case = TaskParsingCase(
            case_id="task-extra-due-notes",
            utterance="/task buy milk",
            expected_route="tasks",
            expected_action="create_task",
            expected_slots={"title": "Buy milk"},
        )

        scored = score_output(
            case,
            ParserOutput(
                status="ok",
                route="tasks",
                action="create_task",
                confidence=0.95,
                task_intent={
                    "intent": "create_task",
                    "title": "Buy milk",
                    "due": "2026-08-20",
                    "notes": "Bring a cooler.",
                    "missing_fields": [],
                },
            ),
        )

        self.assertFalse(scored.success, scored)
        self.assertEqual(scored.reason, "slot extra due")

    def test_assistant_help_metadata_does_not_require_duplicate_notes(self):
        case = TaskParsingCase(
            case_id="task-assistant-metadata-only",
            utterance="Add task compare solar quotes. Noah help: summarize warranty differences.",
            expected_route="tasks",
            expected_action="create_task",
            expected_slots={
                "title": "Compare solar quotes",
                "metadata": {
                    "assistant_help_needed": True,
                    "assistant_name": "Noah",
                    "assistant_help_request": "Summarize warranty differences",
                },
            },
        )
        scored = score_output(
            case,
            ParserOutput(
                status="ok",
                route="tasks",
                action="create_task",
                confidence=0.95,
                task_intent={
                    "intent": "create_task",
                    "title": "Compare solar quotes",
                    "metadata": {
                        "assistant_help_needed": True,
                        "assistant_name": "Noah",
                        "assistant_help_request": "Summarize warranty differences",
                    },
                    "missing_fields": [],
                },
            ),
        )

        self.assertTrue(scored.success, scored)

    def test_duplicate_assistant_help_notes_do_not_fail_metadata_scoring(self):
        case = TaskParsingCase(
            case_id="task-assistant-duplicate-note",
            utterance="Add task compare solar quotes. Noah help: summarize warranty differences.",
            expected_route="tasks",
            expected_action="create_task",
            expected_slots={
                "title": "Compare solar quotes",
                "metadata": {
                    "assistant_help_needed": True,
                    "assistant_name": "Noah",
                    "assistant_help_request": "Summarize warranty differences",
                },
            },
        )
        scored = score_output(
            case,
            ParserOutput(
                status="ok",
                route="tasks",
                action="create_task",
                confidence=0.95,
                task_intent={
                    "intent": "create_task",
                    "title": "Compare solar quotes",
                    "notes": "Assistant help: Summarize warranty differences",
                    "metadata": {
                        "assistant_help_needed": True,
                        "assistant_name": "Noah",
                        "assistant_help_request": "Summarize warranty differences",
                    },
                    "missing_fields": [],
                },
            ),
        )

        self.assertTrue(scored.success, scored)

    def test_assistant_help_notes_with_extra_text_fail_scoring(self):
        case = TaskParsingCase(
            case_id="task-assistant-extra-note",
            utterance="Add task compare solar quotes. Noah help: summarize warranty differences.",
            expected_route="tasks",
            expected_action="create_task",
            expected_slots={
                "title": "Compare solar quotes",
                "metadata": {
                    "assistant_help_needed": True,
                    "assistant_name": "Noah",
                    "assistant_help_request": "Summarize warranty differences",
                },
            },
        )
        scored = score_output(
            case,
            ParserOutput(
                status="ok",
                route="tasks",
                action="create_task",
                confidence=0.95,
                task_intent={
                    "intent": "create_task",
                    "title": "Compare solar quotes",
                    "notes": "Assistant help: Summarize warranty differences. Also call the vendor tomorrow.",
                    "metadata": {
                        "assistant_help_needed": True,
                        "assistant_name": "Noah",
                        "assistant_help_request": "Summarize warranty differences",
                    },
                    "missing_fields": [],
                },
            ),
        )

        self.assertFalse(scored.success, scored)
        self.assertEqual(scored.reason, "slot extra notes")

    def test_ai_only_material_metadata_fails_when_not_expected(self):
        case = TaskParsingCase(
            case_id="task-extra-material-metadata",
            utterance="/task buy milk",
            expected_route="tasks",
            expected_action="create_task",
            expected_slots={"title": "Buy milk"},
        )
        scored = score_output(
            case,
            ParserOutput(
                status="ok",
                route="tasks",
                action="create_task",
                confidence=0.95,
                task_intent={
                    "intent": "create_task",
                    "title": "Buy milk",
                    "metadata": {
                        "requires": ["computer"],
                        "duration_minutes": 60,
                    },
                    "missing_fields": [],
                },
            ),
        )

        self.assertFalse(scored.success, scored)
        self.assertEqual(scored.reason, "slot metadata extra duration_minutes, requires")

    def test_extra_list_metadata_values_fail_exact_scoring(self):
        case = TaskParsingCase(
            case_id="task-extra-requires",
            utterance="/task renew registration",
            expected_route="tasks",
            expected_action="create_task",
            expected_slots={
                "title": "Renew registration",
                "metadata": {"requires": ["computer", "internet"]},
            },
        )
        scored = score_output(
            case,
            ParserOutput(
                status="ok",
                route="tasks",
                action="create_task",
                confidence=0.95,
                task_intent={
                    "intent": "create_task",
                    "title": "Renew registration",
                    "metadata": {
                        "requires": ["computer", "internet", "phone"],
                    },
                    "missing_fields": [],
                },
            ),
        )

        self.assertFalse(scored.success, scored)
        self.assertEqual(scored.reason, "slot metadata requires mismatch")

    def test_update_extra_payload_keys_fail_when_not_expected(self):
        case = TaskParsingCase(
            case_id="task-extra-update-note",
            utterance="/task assign water filter task to dad",
            expected_route="tasks",
            expected_action="update_task",
            expected_slots={
                "query": "water filter",
                "update": {"owner": "dad"},
            },
        )
        scored = score_output(
            case,
            ParserOutput(
                status="ok",
                route="tasks",
                action="update_task",
                confidence=0.95,
                task_intent={
                    "intent": "update_task",
                    "query": "water filter",
                    "update": {
                        "owner": "dad",
                        "note": "Invented note.",
                    },
                    "missing_fields": [],
                },
            ),
        )

        self.assertFalse(scored.success, scored)
        self.assertEqual(scored.reason, "slot update extra note")

    def test_ai_repaired_required_fields_count_as_proposed_failure(self):
        case = TaskParsingCase(
            case_id="task-ai-empty-create",
            utterance="/task change water filter this weekend",
            expected_route="tasks",
            expected_action="create_task",
            expected_slots={
                "title": "Change water filter",
                "due": "2026-08-15",
            },
        )
        output = run_proposed_ai_parser(
            case.utterance,
            now=REFERENCE_TIME,
            extractor=StaticExtractor(
                {
                    "action": "create_task",
                    "confidence": 0.95,
                    "slots": {},
                    "missing_fields": [],
                }
            ),
        )
        scored = score_output(case, output)

        self.assertEqual(output.status, "recovered")
        self.assertFalse(scored.success, scored)
        self.assertEqual(scored.reason, "recovered")

    def test_ai_repaired_due_and_metadata_count_as_proposed_failure(self):
        case = TaskParsingCase(
            case_id="task-ai-partial-create",
            utterance="Add task call pediatrician while driving tomorrow",
            expected_route="tasks",
            expected_action="create_task",
        )
        output = run_proposed_ai_parser(
            case.utterance,
            now=REFERENCE_TIME,
            extractor=StaticExtractor(
                {
                    "action": "create_task",
                    "confidence": 0.95,
                    "slots": {"title": "Call pediatrician"},
                    "missing_fields": [],
                }
            ),
        )
        scored = score_output(case, output)

        self.assertEqual(output.status, "recovered")
        self.assertFalse(scored.success, scored)
        self.assertEqual(scored.reason, "recovered")

    def test_ai_cleaned_up_create_fields_count_as_proposed_failure(self):
        case = TaskParsingCase(
            case_id="task-ai-cleanup-create",
            utterance="Add task buy milk",
            expected_route="tasks",
            expected_action="create_task",
        )
        output = run_proposed_ai_parser(
            case.utterance,
            now=REFERENCE_TIME,
            extractor=StaticExtractor(
                {
                    "action": "create_task",
                    "confidence": 0.95,
                    "slots": {
                        "title": "Buy milk",
                        "due": "2026-08-20",
                        "notes": "Bring a cooler.",
                        "metadata": {"requires": ["computer"]},
                    },
                    "missing_fields": [],
                }
            ),
        )
        scored = score_output(case, output)

        self.assertEqual(output.status, "recovered")
        self.assertFalse(scored.success, scored)
        self.assertEqual(scored.reason, "recovered")

    def test_ai_repaired_recommendation_filters_count_as_proposed_failure(self):
        case = TaskParsingCase(
            case_id="task-ai-partial-filters",
            utterance="show dad tasks due today",
            expected_route="tasks",
            expected_action="recommend_tasks",
        )
        output = run_proposed_ai_parser(
            case.utterance,
            now=REFERENCE_TIME,
            extractor=StaticExtractor(
                {
                    "action": "recommend_tasks",
                    "confidence": 0.95,
                    "slots": {"filters": {"owner": "dad"}},
                    "missing_fields": [],
                }
            ),
        )
        scored = score_output(case, output)

        self.assertEqual(output.status, "recovered")
        self.assertFalse(scored.success, scored)
        self.assertEqual(scored.reason, "recovered")

    def test_ai_partial_update_payload_repair_counts_as_proposed_failure(self):
        case = TaskParsingCase(
            case_id="task-ai-partial-update",
            utterance="owner is nimesh",
            expected_route="tasks",
            expected_action="update_task",
        )
        output = run_proposed_ai_parser(
            case.utterance,
            now=REFERENCE_TIME,
            extractor=StaticExtractor(
                {
                    "action": "update_task",
                    "confidence": 0.95,
                    "slots": {
                        "update": {"owner": "dad"},
                    },
                    "missing_fields": [],
                }
            ),
        )
        scored = score_output(case, output)

        self.assertEqual(output.status, "recovered")
        self.assertFalse(scored.success, scored)
        self.assertEqual(scored.reason, "recovered")

    def test_note_slot_scoring_normalizes_whitespace(self):
        case = TaskParsingCase(
            case_id="task-note-whitespace",
            utterance="/task draft email",
            expected_route="tasks",
            expected_action="create_task",
            expected_slots={
                "title": "Draft email",
                "notes": "Assistant help: Make it concise Assistant context: ask about pool key access",
            },
        )
        scored = score_output(
            case,
            ParserOutput(
                status="ok",
                route="tasks",
                action="create_task",
                confidence=0.95,
                task_intent={
                    "intent": "create_task",
                    "title": "Draft email",
                    "notes": "Assistant help: Make it concise\nAssistant context: ask about pool key access",
                    "missing_fields": [],
                },
            ),
        )

        self.assertTrue(scored.success, scored)

    def test_proposed_ai_failure_preserves_update_task_fallback(self):
        output = run_proposed_ai_parser(
            "/task assign water filter task to dad",
            now=REFERENCE_TIME,
            extractor=FailingExtractor(),
        )

        self.assertEqual(output.status, "recovered")
        self.assertEqual(output.route, "tasks")
        self.assertEqual(output.action, "update_task")

    def test_invalid_ai_action_becomes_recovered_failure(self):
        case = TaskParsingCase(
            case_id="task-invalid-ai-action",
            utterance="/task change water filter this weekend",
            expected_route="tasks",
            expected_action="create_task",
        )
        output = run_proposed_ai_parser(
            case.utterance,
            now=REFERENCE_TIME,
            extractor=StaticExtractor(
                {
                    "action": "bogus",
                    "confidence": 0.94,
                    "slots": {},
                    "missing_fields": [],
                }
            ),
        )

        scored = score_output(case, output)

        self.assertEqual(output.status, "recovered")
        self.assertTrue(scored.scored)
        self.assertFalse(scored.success)
        self.assertEqual(scored.reason, "recovered")

    def test_proposed_ai_success_preserves_update_task_action(self):
        output = run_proposed_ai_parser(
            "/task assign water filter task to dad",
            now=REFERENCE_TIME,
            extractor=StaticExtractor(
                {
                    "action": "recommend_tasks",
                    "confidence": 0.94,
                    "slots": {"filters": {"tags": ["home"]}},
                    "missing_fields": [],
                }
            ),
        )

        self.assertEqual(output.status, "recovered")
        self.assertEqual(output.action, "update_task")
        self.assertEqual(output.task_intent["query"], "water filter")
        self.assertEqual(output.task_intent["update"], {"owner": "dad"})

    def test_ai_update_owner_is_canonicalized(self):
        output = run_proposed_ai_parser(
            "/task assign water filter task to dad",
            now=REFERENCE_TIME,
            extractor=StaticExtractor(
                {
                    "action": "update_task",
                    "confidence": 0.94,
                    "slots": {
                        "query": "water filter",
                        "update": {"owner": "Nimesh"},
                    },
                    "missing_fields": [],
                }
            ),
        )

        self.assertEqual(output.status, "recovered")
        self.assertEqual(output.task_intent["update"], {"owner": "dad"})

    def test_ai_update_notes_alias_is_canonicalized(self):
        output = run_proposed_ai_parser(
            "/task update water filter",
            now=REFERENCE_TIME,
            extractor=StaticExtractor(
                {
                    "action": "update_task",
                    "confidence": 0.94,
                    "slots": {
                        "query": "water filter",
                        "update": {"notes": "Buy the 20x25x1 size."},
                    },
                    "missing_fields": [],
                }
            ),
        )

        self.assertEqual(output.status, "recovered")
        self.assertEqual(output.task_intent["update"], {"note": "Buy the 20x25x1 size."})

    def test_ai_update_unsupported_fields_are_not_actionable(self):
        case = TaskParsingCase(
            case_id="task-unsupported-update",
            utterance="/task update water filter",
            expected_route="tasks",
            expected_action="update_task",
        )
        output = run_proposed_ai_parser(
            case.utterance,
            now=REFERENCE_TIME,
            extractor=StaticExtractor(
                {
                    "action": "update_task",
                    "confidence": 0.94,
                    "slots": {
                        "query": "water filter",
                        "update": {"due": "2026-08-14"},
                    },
                    "missing_fields": [],
                }
            ),
        )

        scored = score_output(case, output)

        self.assertEqual(output.status, "recovered")
        self.assertFalse(scored.success)
        self.assertEqual(scored.reason, "recovered")

    def test_api_context_response_maps_google_task_draft_to_fields(self):
        fields = _api_context_fields_to_legacy_fields(
            {
                "operation": "create_task",
                "confidence": 0.96,
                "task": {
                    "title": "Renew car registration",
                    "notes": "Check smog first.",
                    "due": "2026-08-14T23:59:00-07:00",
                    "n4os_metadata": {
                        "tags": ["car"],
                        "effort_type": "admin",
                        "requires": ["computer", "internet"],
                        "owner": "dad",
                    },
                },
                "missing_fields": [],
            },
            "Remind me to renew the car registration next Friday",
        )

        self.assertEqual(fields["action"], "create_task")
        self.assertEqual(
            fields["slots"],
            {
                "title": "Renew car registration",
                "notes": "Check smog first.",
                "due": "2026-08-14",
                "metadata": {
                    "tags": ["car"],
                    "effort_type": "admin",
                    "requires": ["computer", "internet"],
                    "owner": "dad",
                },
            },
        )

    def test_api_context_due_timestamp_uses_google_tasks_date_component(self):
        fields = _api_context_fields_to_legacy_fields(
            {
                "operation": "create_task",
                "confidence": 0.96,
                "task": {
                    "title": "Submit form",
                    "due": "2026-08-14T00:30:00Z",
                },
                "missing_fields": [],
            },
            "Add task submit form tonight",
        )

        self.assertEqual(fields["slots"]["due"], "2026-08-14")

    def test_api_context_previous_task_target_is_not_literal_query(self):
        fields = _api_context_fields_to_legacy_fields(
            {
                "operation": "update_task",
                "confidence": 0.96,
                "target": {"query": "last_task"},
                "update": {"tags": ["finance"]},
                "missing_fields": [],
            },
            "tags: finance",
        )
        intent = _intent_from_ai_fields(fields, "tags: finance", now=REFERENCE_TIME)

        self.assertEqual(fields["slots"]["target"], "last_task")
        self.assertNotIn("query", fields["slots"])
        self.assertEqual(intent["target"], "last_task")
        self.assertNotIn("query", intent)

    def test_ai_recommendation_due_filter_uses_standard_time_offset(self):
        case = TaskParsingCase(
            case_id="task-standard-time-filter",
            utterance="show dad tasks due today",
            expected_route="tasks",
            expected_action="recommend_tasks",
            expected_slots={
                "filters": {
                    "owner": "dad",
                    "due_min": "2026-12-01T00:00:00-08:00",
                    "due_max": "2026-12-01T23:59:59.999999-08:00",
                },
            },
        )
        output = run_proposed_ai_parser(
            case.utterance,
            now=datetime(2026, 12, 1, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles")),
            extractor=StaticExtractor(
                {
                    "action": "recommend_tasks",
                    "confidence": 0.94,
                    "slots": {
                        "filters": {
                            "owner": "Nimesh",
                            "due_min": "2026-12-01",
                            "due_max": "2026-12-01",
                        },
                    },
                    "missing_fields": [],
                }
            ),
        )

        scored = score_output(case, output)

        self.assertTrue(scored.success, scored)

    def test_api_context_rejects_unknown_operation(self):
        with self.assertRaises(ValueError):
            _api_context_fields_to_legacy_fields(
                {
                    "operation": "bogus",
                    "confidence": 0.96,
                    "task": {"title": "Submit form"},
                    "missing_fields": [],
                },
                "Add task submit form",
            )

    def test_api_context_response_maps_recommendation_filters(self):
        fields = _api_context_fields_to_legacy_fields(
            {
                "operation": "recommend_tasks",
                "confidence": 0.95,
                "filters": {
                    "tags": ["finance"],
                    "owner": "dad",
                    "available_resources": ["phone"],
                },
                "missing_fields": [],
            },
            "show dad finance tasks I can do on my phone",
        )

        self.assertEqual(fields["action"], "recommend_tasks")
        self.assertEqual(
            fields["slots"],
            {
                "filters": {
                    "tags": ["finance"],
                    "owner": "dad",
                    "available_resources": ["phone"],
                },
            },
        )

    def test_ai_recommendation_filters_merge_with_deterministic_constraints(self):
        case = TaskParsingCase(
            case_id="task-recommend-merge",
            utterance="show dad tasks with tag finance on my phone",
            expected_route="tasks",
            expected_action="recommend_tasks",
            expected_slots={
                "filters": {
                    "tags": ["finance"],
                    "owner": "dad",
                    "context": ["phone"],
                    "available_resources": ["phone"],
                    "effort_type": "communication",
                    "available_context": ["phone"],
                    "preferred_effort_type": "communication",
                },
            },
        )
        extractor = StaticExtractor(
            {
                "action": "recommend_tasks",
                "confidence": 0.94,
                "slots": {"filters": {"tags": ["finance"]}},
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

        self.assertFalse(scored.success, scored)
        self.assertEqual(scored.reason, "recovered")

    def test_ai_recommendation_filters_are_canonicalized(self):
        case = TaskParsingCase(
            case_id="task-recommend-normalize",
            utterance="show dad tasks on my computer",
            expected_route="tasks",
            expected_action="recommend_tasks",
            expected_slots={
                "filters": {
                    "owner": "dad",
                    "context": ["computer"],
                    "available_resources": ["computer", "internet", "phone"],
                    "available_context": ["computer"],
                },
            },
        )
        extractor = StaticExtractor(
            {
                "action": "recommend_tasks",
                "confidence": 0.94,
                "slots": {
                    "filters": {
                        "owner": "Nimesh",
                        "available_resources": ["laptop"],
                        "can_do_while": ["drive"],
                    }
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

        self.assertFalse(scored.success, scored)
        self.assertEqual(scored.reason, "recovered")

    def test_ai_recommendation_filter_aliases_are_canonicalized(self):
        case = TaskParsingCase(
            case_id="task-recommend-normalize-clean",
            utterance="show dad tasks on my computer",
            expected_route="tasks",
            expected_action="recommend_tasks",
            expected_slots={
                "filters": {
                    "owner": "dad",
                    "context": ["computer"],
                    "available_resources": ["computer", "internet", "phone"],
                    "available_context": ["computer"],
                },
            },
        )
        extractor = StaticExtractor(
            {
                "action": "recommend_tasks",
                "confidence": 0.94,
                "slots": {
                    "filters": {
                        "owner": "Nimesh",
                        "available_resources": ["laptop"],
                    }
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

        self.assertFalse(scored.success, scored)
        self.assertEqual(scored.reason, "recovered")

    def test_ai_intent_repairs_missing_due_and_metadata_from_request(self):
        intent = _intent_from_ai_fields(
            {
                "action": "create_task",
                "confidence": 0.96,
                "slots": {
                    "title": "Call pediatrician",
                    "metadata": {"effort_type": "communication"},
                },
                "missing_fields": ["due"],
            },
            "/task call pediatrician while driving tomorrow",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["due"], "2026-08-14")
        self.assertEqual(intent["metadata"]["context"], ["car", "phone"])
        self.assertEqual(intent["metadata"]["can_do_while"], ["driving", "commuting"])
        self.assertNotIn("due", intent["missing_fields"])

    def test_ai_intent_drops_inferred_tags_without_explicit_request(self):
        intent = _intent_from_ai_fields(
            {
                "action": "create_task",
                "confidence": 0.96,
                "slots": {
                    "title": "Buy Costco diapers",
                    "metadata": {
                        "effort_type": "errand",
                        "tags": ["costco", "diapers"],
                    },
                },
                "missing_fields": [],
            },
            "Add task buy Costco diapers tomorrow",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["metadata"]["effort_type"], "errand")
        self.assertEqual(intent["metadata"]["tags"], [])

    def test_ai_intent_drops_inferred_due_and_notes_without_explicit_request(self):
        intent = _intent_from_ai_fields(
            {
                "action": "create_task",
                "confidence": 0.96,
                "slots": {
                    "title": "Write teacher appreciation note",
                    "due": "2026-08-20",
                    "notes": "Needs quiet focus.",
                },
                "missing_fields": [],
            },
            "Add task write teacher appreciation note, needs quiet focus",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["title"], "Write teacher appreciation note")
        self.assertNotIn("due", intent)
        self.assertNotIn("notes", intent)

    def test_ai_intent_drops_ungrounded_material_metadata(self):
        intent = _intent_from_ai_fields(
            {
                "action": "create_task",
                "confidence": 0.96,
                "slots": {
                    "title": "Buy milk",
                    "metadata": {
                        "requires": ["computer"],
                        "duration_minutes": 60,
                    },
                },
                "missing_fields": [],
            },
            "Add task buy milk",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["title"], "Buy milk")
        self.assertEqual(intent["metadata"]["requires"], [])
        self.assertIsNone(intent["metadata"]["duration_minutes"])

    def test_ai_intent_overrides_wrong_explicit_due_date(self):
        intent = _intent_from_ai_fields(
            {
                "action": "create_task",
                "confidence": 0.96,
                "slots": {
                    "title": "Renew car registration",
                    "due": "2026-08-14",
                },
                "missing_fields": [],
            },
            "Add task renew car registration next Friday",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["due"], "2026-08-21")

    def test_ai_path_maps_complete_task_query(self):
        case = TaskParsingCase(
            case_id="task-complete",
            utterance="/task complete water filter",
            expected_route="tasks",
            expected_action="complete_task",
            expected_slots={"query": "water filter"},
        )
        extractor = StaticExtractor(
            {
                "action": "complete_task",
                "confidence": 0.94,
                "slots": {"query": "water filter"},
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

    def test_ai_path_maps_complete_task_implicit_target(self):
        case = TaskParsingCase(
            case_id="task-complete-last-task",
            utterance="/task complete it",
            expected_route="tasks",
            expected_action="complete_task",
            expected_slots={"target": "last_task"},
        )
        fields = _api_context_fields_to_legacy_fields(
            {
                "operation": "complete_task",
                "confidence": 0.95,
                "target": {"query": "last_task"},
                "missing_fields": [],
            },
            case.utterance,
        )
        intent = _intent_from_ai_fields(fields, case.utterance, now=REFERENCE_TIME)

        scored = score_output(
            case,
            ParserOutput(
                status="ok",
                route="tasks",
                action="complete_task",
                confidence=0.95,
                task_intent=intent,
            ),
        )

        self.assertEqual(intent["target"], "last_task")
        self.assertTrue(scored.success, scored)

    def test_cli_uses_fixed_default_reference_time(self):
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(["--cases", str(DEFAULT_CASE_PATH), "--include-cases"])

        payload = json.loads(output.getvalue())
        car_registration = next(
            item
            for item in payload["outcomes"]
            if item["case"]["case_id"] == "task-create-car-registration"
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            car_registration["current"]["output"]["task_intent"]["due"],
            "2026-08-21",
        )


if __name__ == "__main__":
    unittest.main()

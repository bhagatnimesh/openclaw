from __future__ import annotations

import json
import unittest

from claws.school_coach.contracts import CoachProvenance
from claws.school_coach.model import OpenAISchoolCoachModel, SchoolCoachModelError, validate_coach_update


def valid_update() -> dict:
    return {
        "relationship": {
            "action": "create",
            "relationship_id": None,
            "person_name": "Ms. X",
            "role": None,
            "child": None,
            "school": None,
            "desired_state": None,
        },
        "interaction": None,
        "observations": [
            {
                "key": "o1",
                "statement": "Ms. X sent a brief reply.",
                "observed_at": None,
                "source_kind": "trigger",
                "source_ref": None,
            }
        ],
        "belief_changes": [
            {
                "action": "create",
                "topic_key": "communication_style",
                "previous_belief_id": None,
                "statement": "Ms. X may prefer concise messages.",
                "confidence": 0.4,
                "reason": "One brief reply is weak evidence.",
                "evidence": [{"observation_ref": "o1", "stance": "supports"}],
            }
        ],
        "strategy_change": {
            "action": "none",
            "previous_strategy_id": None,
            "goal": None,
            "plan": None,
            "rationale": None,
            "confidence": None,
            "reason": None,
            "belief_refs": [],
        },
        "decision": {
            "observed": "One brief reply was reported.",
            "concluded": "A preference is possible.",
            "decided": None,
            "confidence": 0.4,
        },
    }


class SchoolCoachModelTest(unittest.TestCase):
    def test_validates_sparse_structured_update(self):
        update = validate_coach_update(valid_update())

        self.assertIsNone(update.relationship.child)
        self.assertEqual(update.belief_changes[0].confidence, 0.4)

    def test_rejects_out_of_range_confidence(self):
        raw = valid_update()
        raw["belief_changes"][0]["confidence"] = 1.2

        with self.assertRaises(SchoolCoachModelError):
            validate_coach_update(raw)

    def test_rejects_belief_without_evidence(self):
        raw = valid_update()
        raw["belief_changes"][0]["evidence"] = []

        with self.assertRaises(SchoolCoachModelError):
            validate_coach_update(raw)

    def test_responses_request_uses_strict_json_schema(self):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return json.dumps({"output_text": json.dumps(valid_update())}).encode()

        def fake_urlopen(request, timeout):
            captured["body"] = json.loads(request.data.decode())
            captured["timeout"] = timeout
            return FakeResponse()

        model = OpenAISchoolCoachModel(api_key="test-key", urlopen=fake_urlopen)
        update = model.decide(
            "Focus on Ms. X.",
            current_state=None,
            relationships=[],
            context_sources={},
            provenance=CoachProvenance("user_report", "request:3"),
        )

        response_format = captured["body"]["text"]["format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertTrue(response_format["strict"])
        self.assertFalse(response_format["schema"]["additionalProperties"])
        self.assertEqual(update.relationship.person_name, "Ms. X")

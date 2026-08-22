from __future__ import annotations

import json
import unittest

from n4os_research import (
    RESEARCH_FAILURE_MESSAGE,
    RESEARCH_HELP_MESSAGE,
    RESEARCH_SETUP_MESSAGE,
    N4OSResearchSource,
    format_n4os_research_sources,
    generate_n4os_research,
    is_n4os_research_message,
    parse_n4os_research_request,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class N4OSResearchTest(unittest.TestCase):
    def test_research_help_is_phone_sized_and_explains_every_mode(self):
        self.assertIn("Balanced (default)", RESEARCH_HELP_MESSAGE)
        self.assertIn("/research balanced <question>", RESEARCH_HELP_MESSAGE)
        self.assertIn("/research fast <question>", RESEARCH_HELP_MESSAGE)
        self.assertIn("/research deep <question>", RESEARCH_HELP_MESSAGE)
        self.assertIn("N4OS memory is withheld from web search", RESEARCH_HELP_MESSAGE)
        self.assertNotIn("**", RESEARCH_HELP_MESSAGE)
        self.assertNotIn("###", RESEARCH_HELP_MESSAGE)
        self.assertNotIn("Loaded:", RESEARCH_HELP_MESSAGE)
        self.assertLessEqual(len(RESEARCH_HELP_MESSAGE.splitlines()), 16)

    def test_detects_command_and_parses_optional_mode(self):
        self.assertTrue(is_n4os_research_message("/research compare school options"))
        self.assertFalse(is_n4os_research_message("research this later"))
        self.assertEqual(
            parse_n4os_research_request("/research compare school options"),
            ("balanced", "compare school options"),
        )
        self.assertEqual(
            parse_n4os_research_request("/research deep compare school options"),
            ("deep", "compare school options"),
        )
        self.assertEqual(parse_n4os_research_request("/research"), ("balanced", ""))
        self.assertEqual(parse_n4os_research_request("/research help"), ("balanced", "help"))

    def test_missing_key_returns_setup_guidance(self):
        result = generate_n4os_research(
            "/research current school enrollment guidance",
            context={"files": [], "observations": [], "journal": [], "trajectories": []},
            api_key="",
        )

        self.assertEqual(result.reply, RESEARCH_SETUP_MESSAGE)
        self.assertIsNone(result.model)
        self.assertEqual(result.sources, [])
        self.assertIn("Web: enabled", result.knowledge_preview)

    def test_research_separates_web_search_from_private_memory(self):
        requests = []
        web_payload = {
            "output_text": "Current enrollment guidance is published by the district.",
            "output": [
                {
                    "type": "web_search_call",
                    "action": {
                        "sources": [
                            {
                                "type": "url_citation",
                                "title": "District enrollment",
                                "url": "https://district.example/enrollment",
                            }
                        ]
                    },
                }
            ],
        }
        synthesis_payload = {
            "output_text": json.dumps(
                {
                    "reasoning_summary": "Combined current district guidance with the selected school context.",
                    "answer": "The district guidance supports calling enrollment first [1].\nDecision: verify eligibility.\nNext action: call today.\nReview: revisit after the call.",
                }
            ),
            "output": [
                {
                    "type": "reasoning",
                    "summary": [
                        {
                            "type": "summary_text",
                            "text": "Prioritized the official district source and current family constraints.",
                        }
                    ],
                }
            ],
        }

        def fake_urlopen(request, timeout):
            body = json.loads(request.data.decode("utf-8"))
            requests.append((body, timeout))
            return FakeResponse(web_payload if len(requests) == 1 else synthesis_payload)

        private_context = {
            "files": [{"path": "n4os/school/Nysha/private.md", "content": "PRIVATE SIGNAL"}],
            "observations": ["PRIVATE OBSERVATION"],
            "journal": [],
            "trajectories": [],
        }
        result = generate_n4os_research(
            "/research deep current school enrollment guidance",
            context=private_context,
            api_key="sk-test",
            urlopen=fake_urlopen,
        )

        self.assertEqual(len(requests), 2)
        web_body = requests[0][0]
        synthesis_body = requests[1][0]
        self.assertEqual(web_body["model"], "gpt-5.6-sol")
        self.assertEqual(web_body["reasoning"]["effort"], "high")
        self.assertEqual(web_body["tool_choice"], "required")
        self.assertEqual(web_body["tools"], [{"type": "web_search", "search_context_size": "high"}])
        self.assertNotIn("PRIVATE SIGNAL", json.dumps(web_body))
        self.assertNotIn("PRIVATE OBSERVATION", json.dumps(web_body))
        self.assertNotIn("tools", synthesis_body)
        self.assertIn("PRIVATE SIGNAL", json.dumps(synthesis_body))
        self.assertEqual(result.model, "gpt-5.6-sol")
        self.assertEqual(result.reasoning_effort, "high")
        self.assertEqual(result.sources[0].title, "District enrollment")
        self.assertIn("calling enrollment first [1]", result.reply)
        self.assertIn("Live web search", result.context_labels)

    def test_failure_does_not_claim_sources(self):
        def fail_urlopen(*args, **kwargs):
            raise OSError("offline")

        result = generate_n4os_research(
            "/research current guidance",
            context={"files": [], "observations": [], "journal": [], "trajectories": []},
            api_key="sk-test",
            urlopen=fail_urlopen,
        )

        self.assertEqual(result.reply, RESEARCH_FAILURE_MESSAGE)
        self.assertEqual(result.sources, [])
        self.assertIn("No web sources", format_n4os_research_sources(result.sources))

    def test_source_output_rejects_non_web_urls_and_normalizes_titles(self):
        requests = []
        web_payload = {
            "output_text": "Public research notes.",
            "output": [
                {
                    "type": "web_search_call",
                    "action": {
                        "sources": [
                            {"title": "Safe\n source", "url": "https://example.com/source"},
                            {"title": "Unsafe", "url": "javascript:alert(1)"},
                        ]
                    },
                }
            ],
        }
        synthesis_payload = {
            "output_text": json.dumps(
                {
                    "reasoning_summary": "Used the safe source.",
                    "answer": "Use the verified result [1].",
                }
            )
        }

        def fake_urlopen(request, timeout):
            requests.append((request, timeout))
            return FakeResponse(web_payload if len(requests) == 1 else synthesis_payload)

        result = generate_n4os_research(
            "/research verify this",
            context={"files": [], "observations": [], "journal": [], "trajectories": []},
            api_key="sk-test",
            urlopen=fake_urlopen,
        )

        self.assertEqual(
            result.sources,
            [
                N4OSResearchSource(
                    title="Safe source",
                    url="https://example.com/source",
                )
            ],
        )
        synthesis_body = json.loads(requests[1][0].data.decode("utf-8"))
        self.assertIn("untrusted evidence", synthesis_body["input"][0]["content"])


if __name__ == "__main__":
    unittest.main()

import json
import subprocess
import unittest
from unittest.mock import patch

from noah_assistant import (
    OPENAI_RESPONSES_URL,
    OpenAINoahResearchClient,
    OpenClawNoahResearchClient,
)


class FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class OpenClawNoahResearchClientTest(unittest.TestCase):
    def test_research_uses_openclaw_web_search_and_model_run(self):
        calls = []
        search_payload = {
            "outputs": [
                {
                    "result": {
                        "results": [
                            {
                                "title": "Fremont Unified School District",
                                "url": "https://www.fremont.k12.ca.us/",
                                "description": "Main district website.",
                            }
                        ]
                    }
                }
            ]
        }
        model_payload = {
            "outputs": [
                {
                    "text": (
                        "Call FUSD at 510-657-2350 and ask for enrollment.\n\n"
                        "Sources:\n"
                        "- Fremont Unified School District: https://www.fremont.k12.ca.us/"
                    )
                }
            ]
        }

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            if "web" in command:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(search_payload),
                    stderr="",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(model_payload),
                stderr="",
            )

        client = OpenClawNoahResearchClient(
            command=("openclaw", "infer", "model", "run", "--json", "--prompt", "{{prompt}}"),
            web_search_command=(
                "openclaw",
                "infer",
                "web",
                "search",
                "--json",
                "--query",
                "{{query}}",
                "--limit",
                "2",
            ),
            timeout_seconds=30,
        )

        with patch("subprocess.run", side_effect=fake_run):
            result = client.research(
                task_title="Call FUSD",
                help_request="Find the phone number.",
                assistant_context="Chadbourne waitlist.",
            )

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0][6], "Call FUSD Find the phone number. Chadbourne waitlist.")
        self.assertEqual(calls[0][1]["timeout"], 12)
        self.assertIn("OpenClaw web search results:", calls[1][0][6])
        self.assertIn("Fremont Unified School District", calls[1][0][6])
        self.assertEqual(calls[1][1]["timeout"], 30)
        self.assertEqual(
            result.text,
            (
                "Call FUSD at 510-657-2350 and ask for enrollment.\n\n"
                "Sources:\n"
                "- Fremont Unified School District: https://www.fremont.k12.ca.us/"
            ),
        )
        self.assertEqual(len(result.sources), 1)
        self.assertEqual(result.sources[0].title, "Fremont Unified School District")
        self.assertEqual(result.sources[0].url, "https://www.fremont.k12.ca.us/")

    def test_research_continues_when_web_search_fails(self):
        calls = []
        model_payload = {"outputs": [{"text": "Verify the current transfer window before calling."}]}

        def fake_run(command, **kwargs):
            calls.append(command)
            if "web" in command:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="no web")
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(model_payload), stderr="")

        client = OpenClawNoahResearchClient(
            command=("openclaw", "infer", "model", "run", "--json", "--prompt", "{{prompt}}"),
            web_search_command=(
                "openclaw",
                "infer",
                "web",
                "search",
                "--json",
                "--query",
                "{{query}}",
            ),
        )

        with patch("subprocess.run", side_effect=fake_run):
            result = client.research(
                task_title="Research transfer window",
                help_request="Find the transfer deadline.",
                assistant_context="",
            )

        self.assertEqual(len(calls), 2)
        self.assertIn("none available", calls[1][6])
        self.assertEqual(result.text, "Verify the current transfer window before calling.")
        self.assertEqual(result.sources, [])

    def test_from_env_uses_openclaw_default_without_openai_api_key(self):
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("shutil.which", return_value="/usr/local/bin/openclaw"),
        ):
            client = OpenClawNoahResearchClient.from_env()

        self.assertEqual(
            client.command,
            (
                "/usr/local/bin/openclaw",
                "infer",
                "model",
                "run",
                "--json",
                "--prompt",
                "{{prompt}}",
            ),
        )


class OpenAINoahResearchClientTest(unittest.TestCase):
    def test_research_uses_openai_responses_web_search(self):
        captured = {}
        payload = {
            "output_text": "Call FUSD at 510-657-2350 and ask for enrollment.",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Call FUSD at 510-657-2350.",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "title": "Fremont Unified School District",
                                    "url": "https://www.fremont.k12.ca.us/",
                                }
                            ],
                        }
                    ],
                }
            ],
        }

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["authorization"] = request.get_header("Authorization")
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeHTTPResponse(payload)

        client = OpenAINoahResearchClient(
            api_key="sk-test",
            model="gpt-test-mini",
            search_context_size="low",
            timeout_seconds=12,
        )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = client.research(
                task_title="Call FUSD",
                help_request="Find the phone number.",
                assistant_context="Chadbourne waitlist.",
            )

        self.assertEqual(captured["url"], OPENAI_RESPONSES_URL)
        self.assertEqual(captured["timeout"], 12)
        self.assertEqual(captured["authorization"], "Bearer sk-test")
        self.assertEqual(captured["body"]["model"], "gpt-test-mini")
        self.assertFalse(captured["body"]["store"])
        self.assertEqual(captured["body"]["tool_choice"], "required")
        self.assertEqual(
            captured["body"]["tools"],
            [{"type": "web_search", "search_context_size": "low"}],
        )
        self.assertEqual(
            captured["body"]["include"],
            ["web_search_call.action.sources"],
        )
        self.assertIn("Call FUSD", captured["body"]["input"][1]["content"])
        self.assertEqual(
            result.text,
            "Call FUSD at 510-657-2350 and ask for enrollment.",
        )
        self.assertEqual(len(result.sources), 1)
        self.assertEqual(result.sources[0].title, "Fremont Unified School District")
        self.assertEqual(result.sources[0].url, "https://www.fremont.k12.ca.us/")


if __name__ == "__main__":
    unittest.main()

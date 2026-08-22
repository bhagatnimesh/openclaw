from __future__ import annotations

from datetime import datetime, timedelta
import json
import unittest

from n4os_chat import (
    N4OSChatSessionStore,
    format_n4os_chat,
    is_n4os_chat_message,
    parse_n4os_chat_control,
    strip_n4os_chat_prefix,
)


class N4OSChatTest(unittest.TestCase):
    def test_detects_chat_commands_and_controls(self):
        self.assertTrue(is_n4os_chat_message("/chat How should I think about this?"))
        self.assertEqual(strip_n4os_chat_prefix("/chat topic"), "topic")
        self.assertEqual(parse_n4os_chat_control("/chat reset"), "reset")
        self.assertEqual(parse_n4os_chat_control("/chat help"), "help")
        self.assertIsNone(parse_n4os_chat_control("/chat school transition"))

    def test_missing_key_returns_setup_message_with_context_labels(self):
        result = format_n4os_chat("/chat Nysha school transition", api_key="")

        self.assertIn("needs OPENAI_API_KEY", result.reply)
        self.assertIn("SOUL", result.context_labels)
        self.assertIn("Nysha", result.context_labels)
        self.assertIsNone(result.model)

    def test_openai_chat_payload_includes_history_and_memory(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(
                    {
                        "output_text": json.dumps(
                            {
                                "reasoning_summary": "Used current school and chat signals.",
                                "answer": (
                                    "**Start gently.** Use practice, teacher partnership, and one small "
                                    "confidence bridge.\n\nWhat feels hardest right now?"
                                ),
                            }
                        ),
                        "output": [
                            {
                                "type": "reasoning",
                                "summary": [
                                    {
                                        "type": "summary_text",
                                        "text": "Connected the current question to recent history.",
                                    }
                                ],
                            }
                        ],
                    }
                ).encode("utf-8")

        seen_body = {}

        def fake_urlopen(request, timeout):
            seen_body["payload"] = json.loads(request.data.decode("utf-8"))
            return Response()

        store = N4OSChatSessionStore()
        store.append(
            "u1",
            user_text="Earlier question",
            assistant_text="Earlier answer",
            now=datetime(2026, 8, 8, 20, 0),
        )
        result = format_n4os_chat(
            "/chat How do I approach Nysha's first week at school?",
            api_key="test-key",
            history=store.history("u1", now=datetime(2026, 8, 8, 20, 5)),
            urlopen=fake_urlopen,
        )

        self.assertIn("Start gently.", result.reply)
        self.assertNotIn("**", result.reply)
        payload = json.loads(seen_body["payload"]["input"][1]["content"])
        self.assertEqual(payload["history"][0]["user"], "Earlier question")
        memory_paths = [item["path"] for item in payload["memory"]["files"]]
        self.assertIn("n4os/SOUL.md", memory_paths)
        self.assertIn("n4os/family/Nysha.md", memory_paths)
        self.assertGreater(seen_body["payload"]["max_output_tokens"], 420)
        self.assertEqual(seen_body["payload"]["reasoning"], {"summary": "concise"})
        self.assertEqual(
            seen_body["payload"]["text"]["format"]["type"],
            "json_schema",
        )
        self.assertEqual(
            result.reasoning_summary,
            "Connected the current question to recent history.",
        )
        self.assertIn("Chat history: 1 turn", result.knowledge_preview)

    def test_session_store_expires_old_history(self):
        store = N4OSChatSessionStore(ttl=timedelta(minutes=10))
        store.append(
            "u1",
            user_text="hello",
            assistant_text="hi",
            now=datetime(2026, 8, 8, 20, 0),
        )

        self.assertTrue(store.active("u1", now=datetime(2026, 8, 8, 20, 5)))
        self.assertFalse(store.active("u1", now=datetime(2026, 8, 8, 20, 11)))


if __name__ == "__main__":
    unittest.main()

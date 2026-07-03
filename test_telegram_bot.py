from __future__ import annotations

import unittest
from pathlib import Path
import tempfile

from telegram_bot import (
    ERROR_MESSAGE,
    HELP_MESSAGE,
    SETUP_USER_MESSAGE,
    UNAUTHORIZED_MESSAGE,
    N4OSTelegramBot,
    TelegramConfig,
    load_config,
)


class FakeMessage:
    def __init__(self, text: str | None = None, caption: str | None = None) -> None:
        self.text = text
        self.caption = caption
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class FakeUpdate:
    def __init__(self, user_id: int, message: FakeMessage) -> None:
        self.effective_user = FakeUser(user_id)
        self.effective_message = message


class FakeClaw:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def handle_request(self, request: str):
        self.requests.append(request)
        print(f"router replied to: {request}")
        return {
            "route": "tasks",
            "intent_summary": "Route to family-tasks.",
            "confidence": 0.9,
        }


class FailingClaw:
    def handle_request(self, request: str):
        raise RuntimeError(f"boom: {request}")


class QuietLogger:
    def info(self, *args, **kwargs) -> None:
        pass

    def exception(self, *args, **kwargs) -> None:
        pass


class TelegramBotTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_allowed_user_id_replies_with_setup_instruction(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=None),
            FakeClaw(),
            logger=QuietLogger(),
        )
        message = FakeMessage("Add task buy milk")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(
            message.replies,
            [SETUP_USER_MESSAGE.format(user_id=12345)],
        )

    async def test_unauthorized_user_is_denied_without_routing(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("Add task buy milk")

        await bot.handle_message(FakeUpdate(999, message), None)

        self.assertEqual(message.replies, [UNAUTHORIZED_MESSAGE])
        self.assertEqual(claw.requests, [])

    async def test_authorized_message_routes_to_n4os_and_replies_with_output(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("Add task buy milk")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(claw.requests, ["Add task buy milk"])
        self.assertEqual(message.replies, ["router replied to: Add task buy milk"])

    async def test_help_uses_same_authorization_gate(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        message = FakeMessage("/help")

        await bot.handle_help(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies, [HELP_MESSAGE])

    async def test_router_errors_are_reported_gracefully(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FailingClaw(),
            logger=QuietLogger(),
        )
        message = FakeMessage("Add task buy milk")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies, [ERROR_MESSAGE])


class TelegramConfigTest(unittest.TestCase):
    def test_load_config_reads_token_and_allowed_user_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "TELEGRAM_BOT_TOKEN=token\nALLOWED_TELEGRAM_USER_ID=12345\n",
                encoding="utf-8",
            )

            config = load_config(env_path=env_path)

        self.assertEqual(config.token, "token")
        self.assertEqual(config.allowed_user_id, 12345)

    def test_load_config_treats_missing_allowed_user_id_as_setup_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("TELEGRAM_BOT_TOKEN=token\n", encoding="utf-8")

            config = load_config(env_path=env_path)

        self.assertEqual(config.allowed_user_id, None)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import unittest
from pathlib import Path
import sys
import tempfile
import textwrap
from typing import Any
from unittest.mock import patch

import telegram_audio
from telegram_audio import (
    CommandAudioTranscriber,
    VoiceTranscriptionTimeout,
    VoiceTranscriptionUnavailable,
    WhisperCliAudioTranscriber,
    create_default_audio_transcriber,
)
from telegram_bot import (
    ERROR_MESSAGE,
    HELP_MESSAGE,
    SETUP_USER_MESSAGE,
    UNAUTHORIZED_MESSAGE,
    N4OSTelegramBot,
    TelegramConfig,
    VOICE_TRANSCRIPTION_EMPTY_MESSAGE,
    VOICE_TRANSCRIPTION_FAILED_MESSAGE,
    VOICE_TRANSCRIPTION_RESULT_MESSAGE,
    VOICE_TRANSCRIPTION_STARTED_MESSAGE,
    VOICE_TRANSCRIPTION_TIMEOUT_MESSAGE,
    VOICE_TRANSCRIPTION_UNAVAILABLE_MESSAGE,
    build_application,
    load_config,
)


class FakeMessage:
    def __init__(
        self,
        text: str | None = None,
        caption: str | None = None,
        voice: Any | None = None,
        audio: Any | None = None,
        document: Any | None = None,
    ) -> None:
        self.text = text
        self.caption = caption
        self.voice = voice
        self.audio = audio
        self.document = document
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


class FakeFilters:
    ALL = object()

    class CommandFilter:
        def __invert__(self):
            raise AssertionError("message handler must not exclude Telegram commands")

    COMMAND = CommandFilter()


class FakeApplicationBuilder:
    def __init__(self) -> None:
        self.token_value: str | None = None

    def token(self, value: str) -> "FakeApplicationBuilder":
        self.token_value = value
        return self

    def build(self) -> "FakeApplication":
        return FakeApplication(self.token_value)


class FakeApplication:
    latest: "FakeApplication | None" = None

    def __init__(self, token: str | None) -> None:
        self.token = token
        self.handlers: list[Any] = []
        FakeApplication.latest = self

    @classmethod
    def builder(cls) -> FakeApplicationBuilder:
        return FakeApplicationBuilder()

    def add_handler(self, handler: Any) -> None:
        self.handlers.append(handler)


class FailingClaw:
    def handle_request(self, request: str):
        raise RuntimeError(f"boom: {request}")


class FakeVoice:
    mime_type = "audio/ogg"


class FakeTelegramFile:
    def __init__(self) -> None:
        self.downloads: list[Path] = []

    async def download_to_drive(self, path: Path) -> Path:
        self.downloads.append(path)
        path.write_bytes(b"audio bytes")
        return path


class FakeTelegramVoice:
    mime_type = "audio/ogg"

    def __init__(self, telegram_file: FakeTelegramFile) -> None:
        self.telegram_file = telegram_file

    async def get_file(self) -> FakeTelegramFile:
        return self.telegram_file


class FakeAudioTranscriber:
    def __init__(self, transcript: str) -> None:
        self.transcript = transcript
        self.messages: list[FakeMessage] = []
        self.replies_at_start: list[str] = []

    async def transcribe(self, message: FakeMessage) -> str:
        self.messages.append(message)
        self.replies_at_start = list(message.replies)
        return self.transcript


class UnavailableAudioTranscriber:
    async def transcribe(self, message: FakeMessage) -> str:
        raise VoiceTranscriptionUnavailable("not configured")


class TimeoutAudioTranscriber:
    async def transcribe(self, message: FakeMessage) -> str:
        raise VoiceTranscriptionTimeout("timed out")


class FailingAudioTranscriber:
    async def transcribe(self, message: FakeMessage) -> str:
        raise RuntimeError("transcription failed")


class QuietLogger:
    def info(self, *args, **kwargs) -> None:
        pass

    def warning(self, *args, **kwargs) -> None:
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

    async def test_unauthorized_voice_is_denied_without_transcription(self):
        claw = FakeClaw()
        transcriber = FakeAudioTranscriber("Add task buy milk")
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
            audio_transcriber=transcriber,
        )
        message = FakeMessage(voice=FakeVoice())

        await bot.handle_message(FakeUpdate(999, message), None)

        self.assertEqual(message.replies, [UNAUTHORIZED_MESSAGE])
        self.assertEqual(claw.requests, [])
        self.assertEqual(transcriber.messages, [])

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

    async def test_authorized_slash_calendar_routes_to_n4os_as_text(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("/calendar add for Tuesday 8 PM to cancel fox 1")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(
            claw.requests,
            ["add event for Tuesday 8 PM to cancel fox 1"],
        )
        self.assertEqual(
            message.replies,
            ["router replied to: add event for Tuesday 8 PM to cancel fox 1"],
        )

    async def test_authorized_message_improves_voice_typed_text_before_routing(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("Can you add tax buy milk")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(claw.requests, ["add task buy milk"])
        self.assertEqual(message.replies, ["router replied to: add task buy milk"])

    async def test_authorized_voice_routes_transcript_to_n4os(self):
        claw = FakeClaw()
        transcriber = FakeAudioTranscriber("Add task buy milk")
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
            audio_transcriber=transcriber,
        )
        message = FakeMessage(voice=FakeVoice())

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(transcriber.messages, [message])
        self.assertEqual(transcriber.replies_at_start, [VOICE_TRANSCRIPTION_STARTED_MESSAGE])
        self.assertEqual(claw.requests, ["Add task buy milk"])
        self.assertEqual(
            message.replies,
            [
                VOICE_TRANSCRIPTION_STARTED_MESSAGE,
                VOICE_TRANSCRIPTION_RESULT_MESSAGE.format(text="Add task buy milk"),
                "router replied to: Add task buy milk",
            ],
        )

    async def test_authorized_voice_improves_transcript_before_routing(self):
        claw = FakeClaw()
        transcript = "Um can you remind me to order the lock tomorrow"
        transcriber = FakeAudioTranscriber(transcript)
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
            audio_transcriber=transcriber,
        )
        message = FakeMessage(voice=FakeVoice())

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(claw.requests, ["add task order the lock tomorrow"])
        self.assertEqual(
            message.replies,
            [
                VOICE_TRANSCRIPTION_STARTED_MESSAGE,
                VOICE_TRANSCRIPTION_RESULT_MESSAGE.format(text=transcript),
                "router replied to: add task order the lock tomorrow",
            ],
        )

    async def test_authorized_voice_repairs_family_task_dictation_before_routing(self):
        claw = FakeClaw()
        transcript = "\n".join(
            [
                "want to add a task for Monday at 4 p.m. to call FUSD "
                "to follow up on Nyshas School waiting",
                "list for Chad Bond. This task is for Namesh. "
                "I want Noah to find out FUSD number to call",
                "and the key talking points. I really want",
                "Nyshad to meet Chad Bond from overflow",
                "on ASS School to Mission Valley Monteserie",
            ]
        )
        transcriber = FakeAudioTranscriber(transcript)
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
            audio_transcriber=transcriber,
        )
        message = FakeMessage(voice=FakeVoice())

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(
            claw.requests,
            [
                "\n".join(
                    [
                        "want to add a task for Monday at 4 p.m. to call FUSD "
                        "to follow up on Nysha's school waiting",
                        "list for Chad Bond. This task is for Namesh. "
                        "I want Noah to find out FUSD phone number to call",
                        "and the key talking points. I really want",
                        "Nysha to meet Chad Bond from overflow",
                        "on ASS School to Mission Valley Montessori",
                    ]
                )
            ],
        )

    async def test_voice_requires_transcription_configuration(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
            audio_transcriber=UnavailableAudioTranscriber(),
        )
        message = FakeMessage(voice=FakeVoice())

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(
            message.replies,
            [VOICE_TRANSCRIPTION_STARTED_MESSAGE, VOICE_TRANSCRIPTION_UNAVAILABLE_MESSAGE],
        )
        self.assertEqual(claw.requests, [])

    async def test_empty_voice_transcript_does_not_route(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
            audio_transcriber=FakeAudioTranscriber("  "),
        )
        message = FakeMessage(voice=FakeVoice())

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(
            message.replies,
            [VOICE_TRANSCRIPTION_STARTED_MESSAGE, VOICE_TRANSCRIPTION_EMPTY_MESSAGE],
        )
        self.assertEqual(claw.requests, [])

    async def test_voice_timeout_replies_without_routing(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
            audio_transcriber=TimeoutAudioTranscriber(),
        )
        message = FakeMessage(voice=FakeVoice())

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(
            message.replies,
            [VOICE_TRANSCRIPTION_STARTED_MESSAGE, VOICE_TRANSCRIPTION_TIMEOUT_MESSAGE],
        )
        self.assertEqual(claw.requests, [])

    async def test_voice_transcription_error_replies_without_routing(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
            audio_transcriber=FailingAudioTranscriber(),
        )
        message = FakeMessage(voice=FakeVoice())

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(
            message.replies,
            [VOICE_TRANSCRIPTION_STARTED_MESSAGE, VOICE_TRANSCRIPTION_FAILED_MESSAGE],
        )
        self.assertEqual(claw.requests, [])

    async def test_command_audio_transcriber_downloads_voice_and_parses_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "fake_stt.py"
            script.write_text(
                "import json\n"
                "import pathlib\n"
                "import sys\n"
                "path = pathlib.Path(sys.argv[1])\n"
                "assert path.read_bytes() == b'audio bytes'\n"
                "print('build log before json')\n"
                "payload = {'outputs': [{'text': 'Add task from voice'}]}\n"
                "print(json.dumps(payload, indent=2))\n",
                encoding="utf-8",
            )
            telegram_file = FakeTelegramFile()
            message = FakeMessage(voice=FakeTelegramVoice(telegram_file))
            transcriber = CommandAudioTranscriber(
                command=(sys.executable, str(script), "{{path}}"),
                cwd=Path(tmpdir),
            )

            transcript = await transcriber.transcribe(message)

        self.assertEqual(transcript, "Add task from voice")
        self.assertEqual(len(telegram_file.downloads), 1)
        self.assertEqual(telegram_file.downloads[0].suffix, ".ogg")

    async def test_command_audio_transcriber_finds_common_node_when_path_is_narrow(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_node = Path(tmpdir) / "node"
            fake_node.write_text("#!/bin/sh\n", encoding="utf-8")
            fake_node.chmod(0o755)

            with (
                patch("telegram_audio.shutil.which", return_value=None),
                patch.object(
                    telegram_audio,
                    "COMMON_NODE_CANDIDATES",
                    (str(fake_node),),
                ),
            ):
                transcriber = CommandAudioTranscriber()

        command = transcriber.command
        local_entry = Path(telegram_audio.__file__).resolve().with_name("openclaw.mjs")
        self.assertIsNotNone(command)
        self.assertEqual(command[:2], (str(fake_node), str(local_entry)))

    async def test_command_audio_transcriber_times_out_slow_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "slow_stt.py"
            script.write_text(
                textwrap.dedent(
                    """
                    import time

                    time.sleep(5)
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            telegram_file = FakeTelegramFile()
            message = FakeMessage(voice=FakeTelegramVoice(telegram_file))
            transcriber = CommandAudioTranscriber(
                command=(sys.executable, str(script), "{{path}}"),
                cwd=Path(tmpdir),
                timeout_seconds=0.05,
            )

            with self.assertRaisesRegex(VoiceTranscriptionTimeout, "timed out"):
                await transcriber.transcribe(message)

    async def test_whisper_cli_audio_transcriber_reads_text_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            whisper = Path(tmpdir) / "whisper"
            env_path = Path(tmpdir) / "path.txt"
            whisper.write_text(
                textwrap.dedent(
                    f"""
                    #!{sys.executable}
                    import os
                    import pathlib
                    import sys

                    output_dir = pathlib.Path(sys.argv[sys.argv.index("--output_dir") + 1])
                    audio_path = pathlib.Path(sys.argv[-1])
                    assert "--model" in sys.argv
                    assert "tiny" in sys.argv
                    assert audio_path.read_bytes() == b"audio bytes"
                    pathlib.Path({str(env_path)!r}).write_text(os.environ["PATH"], encoding="utf-8")
                    (output_dir / f"{{audio_path.stem}}.txt").write_text(
                        "Add task from local whisper\\n",
                        encoding="utf-8",
                    )
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            whisper.chmod(0o755)
            telegram_file = FakeTelegramFile()
            message = FakeMessage(voice=FakeTelegramVoice(telegram_file))
            transcriber = WhisperCliAudioTranscriber(str(whisper))

            transcript = await transcriber.transcribe(message)
            first_path_entry = env_path.read_text(encoding="utf-8").split(os.pathsep)[0]

        self.assertEqual(transcript, "Add task from local whisper")
        self.assertEqual(Path(first_path_entry).resolve(), Path(tmpdir).resolve())

    async def test_whisper_cli_audio_transcriber_rejects_skipped_audio(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            whisper = Path(tmpdir) / "whisper"
            whisper.write_text(
                textwrap.dedent(
                    f"""
                    #!{sys.executable}
                    print("Skipping message.m4a due to FileNotFoundError: ffmpeg")
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            whisper.chmod(0o755)
            telegram_file = FakeTelegramFile()
            message = FakeMessage(voice=FakeTelegramVoice(telegram_file))
            transcriber = WhisperCliAudioTranscriber(str(whisper))

            with self.assertRaisesRegex(RuntimeError, "Skipping message"):
                await transcriber.transcribe(message)

    def test_default_audio_transcriber_prefers_local_whisper(self):
        with patch("telegram_audio._resolve_whisper_command", return_value="/tmp/whisper"):
            transcriber = create_default_audio_transcriber()

        self.assertIsInstance(transcriber, WhisperCliAudioTranscriber)

    def test_default_audio_transcriber_uses_explicit_command(self):
        transcriber = create_default_audio_transcriber(("fake-stt", "{{path}}"))

        self.assertIsInstance(transcriber, CommandAudioTranscriber)
        self.assertEqual(transcriber.command, ("fake-stt", "{{path}}"))

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

    def test_load_config_reads_voice_transcribe_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "TELEGRAM_BOT_TOKEN=token\n"
                "ALLOWED_TELEGRAM_USER_ID=12345\n"
                "N4OS_VOICE_TRANSCRIBE_COMMAND='fake-stt --json {{path}}'\n",
                encoding="utf-8",
            )

            config = load_config(env_path=env_path)

        self.assertEqual(
            config.voice_transcribe_command,
            ("fake-stt", "--json", "{{path}}"),
        )

    def test_load_config_treats_missing_allowed_user_id_as_setup_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("TELEGRAM_BOT_TOKEN=token\n", encoding="utf-8")

            config = load_config(env_path=env_path)

        self.assertEqual(config.allowed_user_id, None)


class TelegramApplicationTest(unittest.TestCase):
    def test_message_handler_accepts_unknown_slash_commands(self):
        def fake_command_handler(command: str, callback: Any) -> tuple[str, str, Any]:
            return ("command", command, callback)

        def fake_message_handler(filter_value: Any, callback: Any) -> tuple[str, Any, Any]:
            return ("message", filter_value, callback)

        with (
            patch("telegram_bot.Application", FakeApplication),
            patch("telegram_bot.CommandHandler", fake_command_handler),
            patch("telegram_bot.MessageHandler", fake_message_handler),
            patch("telegram_bot.filters", FakeFilters),
        ):
            application = build_application(TelegramConfig(token="token", allowed_user_id=12345))

        self.assertIs(application, FakeApplication.latest)
        self.assertEqual(application.handlers[0][0:2], ("command", "start"))
        self.assertEqual(application.handlers[1][0:2], ("command", "help"))
        self.assertEqual(application.handlers[2][0], "message")
        self.assertIs(application.handlers[2][1], FakeFilters.ALL)


if __name__ == "__main__":
    unittest.main()

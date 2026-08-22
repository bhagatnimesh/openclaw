from __future__ import annotations

import os
import json
import unittest
from datetime import date
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
    HOMEWORK_PHOTO_UPLOAD_DIR,
    HOW_TO_HELP,
    OpenAIImageTextExtractor,
    OpenAIN4OSHelpAnswerer,
    READING_PHOTO_UPLOAD_DIR,
    SETUP_USER_MESSAGE,
    UNAUTHORIZED_MESSAGE,
    N4OSTelegramBot,
    TelegramConfig,
    TelegramRecentCapture,
    TelegramUndoEntry,
    TelegramSenderProfile,
    VOICE_TRANSCRIPTION_EMPTY_MESSAGE,
    VOICE_TRANSCRIPTION_FAILED_MESSAGE,
    VOICE_TRANSCRIPTION_RESULT_MESSAGE,
    VOICE_TRANSCRIPTION_STARTED_MESSAGE,
    VOICE_TRANSCRIPTION_TIMEOUT_MESSAGE,
    VOICE_TRANSCRIPTION_UNAVAILABLE_MESSAGE,
    build_application,
    load_config,
    _build_help_catalog,
    _apply_capture_correction,
    _conversation_key,
)
from claws.n4os.claw import N4OSClaw
from claws.n4os.second_brain_importer import SecondBrainImportUserError
from n4os_capture import CaptureIngestResult, CaptureNote, JournalEntry, ingest_capture_notes
from n4os_capture import CaptureUndoResult
from n4os_advice import N4OSAdviceResult
from n4os_chat import N4OSChatResult
from n4os_research import RESEARCH_HELP_MESSAGE, N4OSResearchResult, N4OSResearchSource
from n4os_memory_inbox import MemoryIngestResult, MemoryObservation


def advice_result(reply: str, *, model: str | None = None) -> N4OSAdviceResult:
    return N4OSAdviceResult(
        reply=reply,
        reasoning_summary="Used the selected knowledge to choose the response.",
        context_labels=["SOUL"],
        knowledge_preview="Knowledge selected\nSources: SOUL",
        model=model,
    )


def assert_single_memory_reply(
    test: unittest.TestCase,
    replies: list[str],
    remembered: str,
    source: str,
    *,
    updated: bool = False,
) -> None:
    test.assertEqual(len(replies), 1)
    lines = replies[0].splitlines()
    test.assertEqual(lines[0], remembered)
    test.assertEqual(lines[1], source)
    test.assertRegex(lines[2], r"^Stored: [A-Z][a-z]{2} \d{1,2}, \d{4} \d{1,2}:\d{2} [AP]M\.$")
    if updated:
        test.assertRegex(lines[3], r"^Updated: [A-Z][a-z]{2} \d{1,2}, \d{4} \d{1,2}:\d{2} [AP]M\.$")
        test.assertEqual(len(lines), 4)
    else:
        test.assertEqual(len(lines), 3)


class FakeMessage:
    _next_message_id = 1000

    def __init__(
        self,
        text: str | None = None,
        caption: str | None = None,
        voice: Any | None = None,
        audio: Any | None = None,
        document: Any | None = None,
        photo: list[Any] | None = None,
        message_id: int | None = None,
        reply_to_message: Any | None = None,
    ) -> None:
        self.text = text
        self.caption = caption
        self.voice = voice
        self.audio = audio
        self.document = document
        self.photo = photo or []
        self.message_id = message_id if message_id is not None else self._next_id()
        self.reply_to_message = reply_to_message
        self.replies: list[str] = []
        self.sent_messages: list[Any] = []

    @classmethod
    def _next_id(cls) -> int:
        value = cls._next_message_id
        cls._next_message_id += 1
        return value

    async def reply_text(self, text: str) -> Any:
        self.replies.append(text)
        sent = type("FakeSentMessage", (), {"message_id": self._next_id(), "text": text})()
        self.sent_messages.append(sent)
        return sent


class FakeHelpAnswerer:
    def __init__(self, reply: str | None = None, *, fail: bool = False) -> None:
        self.reply = reply
        self.fail = fail
        self.questions: list[str] = []

    def answer(self, question: str) -> str | None:
        self.questions.append(question)
        if self.fail:
            raise RuntimeError("help unavailable")
        return self.reply


class FakeSchoolCoachClaw:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def handle_request(self, request: str, *, provenance) -> str:
        self.calls.append({"request": request, "provenance": provenance})
        return "Current plan: Keep the next interaction short and positive."


class FakeUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class FakeChat:
    def __init__(self, chat_id: int) -> None:
        self.id = chat_id


class FakeUpdate:
    def __init__(self, user_id: int, message: FakeMessage, chat_id: int | None = None) -> None:
        self.effective_user = FakeUser(user_id)
        self.effective_message = message
        self.effective_chat = FakeChat(chat_id) if chat_id is not None else None


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


class RecordingTasksClaw:
    def __init__(self) -> None:
        self.requests: list[str] = []
        self.undo_stack: list[dict[str, str]] = []
        self.last_result: dict[str, str] | None = None
        self.last_created_task: dict[str, Any] | None = None
        self.pending_action = None

    def handle_pending_response(self, request: str) -> bool:
        del request
        return False

    def add_task_from_request(self, request: str, reference_time=None) -> str:
        del reference_time
        self.requests.append(request)
        self.undo_stack.append({"action": "create"})
        self.last_result = {"status": "ok"}
        self.last_created_task = {
            "id": "task-123",
            "title": "Sign up for the parent-teacher meeting",
            "due": "2026-08-11T00:00:00.000Z",
            "webViewLink": "https://tasks.google.com/task/task-123",
            "_n4os_metadata": {"owner": "mom"},
        }
        return "Created task: Sign up for the parent-teacher meeting."


class FakeTelegramBot:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> None:
        self.messages.append(kwargs)


class FakeContext:
    def __init__(self) -> None:
        self.bot = FakeTelegramBot()


class FakeLibraryRouteClaw(N4OSClaw):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None, str | None]] = []

    def handle_request(
        self,
        request: str,
        reference_time=None,
        source: str = "telegram_text",
        default_owner: str | None = None,
        photo_path: str | None = None,
    ):
        del reference_time
        self.calls.append((request, source, default_owner, photo_path))
        print("Saved library photo.")
        return {
            "route": "library",
            "intent_summary": "Route to library for record_reading.",
            "response": "Saved.",
            "confidence": 0.9,
        }


class FakeLibraryCheckoutRouteClaw(FakeLibraryRouteClaw):
    def handle_request(
        self,
        request: str,
        reference_time=None,
        source: str = "telegram_text",
        default_owner: str | None = None,
        photo_path: str | None = None,
    ):
        del reference_time
        self.calls.append((request, source, default_owner, photo_path))
        print("Saved this library bag with 1 book at home.")
        return {
            "route": "library",
            "action": "record_checkout",
            "intent_summary": "Route to library for record_checkout.",
            "confidence": 0.9,
        }


class FailedLegacyLibraryRouteClaw(FakeLibraryRouteClaw):
    def handle_request(
        self,
        request: str,
        reference_time=None,
        source: str = "telegram_text",
        default_owner: str | None = None,
        photo_path: str | None = None,
    ):
        del reference_time, default_owner
        self.calls.append((request, source, None, photo_path))
        return {
            "route": "library",
            "action": "record_reading",
            "response": "Reading Garden storage failed.",
            "confidence": 0.9,
        }


class FakeLibraryDomainClaw:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.last_result: dict[str, str] | None = None

    def record_from_request(self, request: str, **kwargs):
        del kwargs
        self.calls.append(("record", request))
        self.last_result = {"status": "ok"}
        return "Saved. Nysha's Reading Garden grew a new leaf."


class NonDeepcopyableLibraryDomainClaw(FakeLibraryDomainClaw):
    def __deepcopy__(self, memo):
        del memo
        raise TypeError("provider client cannot be deep-copied")


class FailedLibraryDomainClaw(FakeLibraryDomainClaw):
    def record_from_request(self, request: str, **kwargs):
        self.calls.append(("record", request))
        self.photo_path = kwargs.get("photo_path")
        self.last_result = {"status": "error"}
        return "Reading Garden storage failed."


class FakeHomeworkClaw:
    def __init__(self, status: str = "ok") -> None:
        self.status = status
        self.calls: list[tuple[str, str | None, str | None, str | None]] = []
        self.last_result: dict[str, Any] | None = None
        self.pending_action: dict[str, Any] | None = None

    def capture_from_request(self, request: str, **kwargs):
        self.calls.append(
            (
                request,
                kwargs.get("source"),
                kwargs.get("photo_path"),
                kwargs.get("photo_sha256"),
            )
        )
        self.last_result = {"status": self.status}
        return "Captured homework for Nysha: All About Me - assigned, due 2026-08-21."


class FakeSchoolNewsletterImporter:
    def __init__(self) -> None:
        self.pending = False
        self.preview_calls: list[tuple[str, str]] = []
        self.save_calls: list[tuple[str, str]] = []

    def has_pending(self, key: str) -> bool:
        del key
        return self.pending

    def preview_from_message(self, text: str, *, key: str) -> str:
        self.preview_calls.append((text, key))
        self.pending = True
        return "School newsletter parsed for Nysha. Reply `save` to add only new/enrichment items, or `cancel`."

    def save_pending(self, *, key: str, response: str):
        self.save_calls.append((key, response))
        self.pending = False
        return type("Result", (), {"message": "Saved school newsletter updates."})()


class FakeSecondBrainImporter:
    def __init__(self) -> None:
        self.pending = False
        self.preview_calls: list[tuple[str, str]] = []
        self.save_calls: list[tuple[str, str]] = []

    def has_pending(self, key: str) -> bool:
        del key
        return self.pending

    def preview_from_message(self, text: str, *, key: str) -> str:
        self.preview_calls.append((text, key))
        self.pending = True
        return "N4OS import preview: Back-to-School Night\n\nReply `save` to approve this plan, or `cancel`."

    def save_pending(self, *, key: str, response: str):
        self.save_calls.append((key, response))
        self.pending = False
        return type("Result", (), {"message": "Saved second brain import."})()


class FailingSecondBrainImporter(FakeSecondBrainImporter):
    def preview_from_message(self, text: str, *, key: str) -> str:
        self.preview_calls.append((text, key))
        raise SecondBrainImportUserError("Paste the full Google Slides URL.")


class PendingHomeworkClaw(FakeHomeworkClaw):
    def __init__(self) -> None:
        super().__init__(status="needs_information")
        self.pending_action = None

    def capture_from_request(self, request: str, **kwargs):
        self.calls.append(
            (
                request,
                kwargs.get("source"),
                kwargs.get("photo_path"),
                kwargs.get("photo_sha256"),
            )
        )
        if request == "cancel":
            photo_path = self.pending_action.get("photo_path") if self.pending_action else None
            self.pending_action = None
            self.last_result = {"status": "ok", "data": {"cleanup_photo_path": photo_path}}
            return "Canceled homework capture."
        self.pending_action = {"photo_path": kwargs.get("photo_path")}
        self.last_result = {"status": "needs_information", "data": {"pending_action": self.pending_action}}
        return "This looks similar to an existing homework. Reply `attach`, `new`, or `cancel`."


class FakeShoppingRouteClaw:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object | None]] = []
        self.undo_stack: list[dict[str, str]] = []

    def handle_request(self, request: str, reference_time=None):
        self.calls.append((request, reference_time))
        self.undo_stack.append({"action": "shopping"})
        print(f"Added milk to Costco.")
        return f"Added milk to Costco."


class UndoableRouterClaw:
    def __init__(self) -> None:
        self.requests: list[str] = []
        self.route_context = type("RouteContext", (), {"mutation_route_stack": []})()

    def handle_request(self, request: str):
        self.requests.append(request)
        if request == "undo":
            if self.route_context.mutation_route_stack:
                self.route_context.mutation_route_stack.pop()
            print("Undid Home Board add: removed 1 item(s).")
            return {
                "route": "home_board",
                "intent_summary": "Undid Home Board add: removed 1 item(s).",
                "confidence": 1.0,
            }

        self.route_context.mutation_route_stack.append("home_board")
        print("Added to Today at Home: Family: passports (airport)")
        return {
            "route": "home_board",
            "intent_summary": "Route to home-board for add_item.",
            "confidence": 0.95,
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


class FailingLibraryRouteClaw(N4OSClaw):
    def __init__(self) -> None:
        self.photo_path: str | None = None

    def handle_request(
        self,
        request: str,
        reference_time=None,
        source: str = "telegram_text",
        default_owner: str | None = None,
        photo_path: str | None = None,
    ):
        del request, reference_time, source, default_owner
        self.photo_path = photo_path
        raise RuntimeError("library write failed")


class PendingClarificationClaw(FakeClaw):
    def __init__(self, request: str) -> None:
        super().__init__()
        self.pending_route_clarification = type(
            "Pending",
            (),
            {"request": request},
        )()


class FakeVoice:
    mime_type = "audio/ogg"


class FakeTelegramFile:
    def __init__(self, content: bytes = b"audio bytes") -> None:
        self.content = content
        self.downloads: list[Path] = []

    async def download_to_drive(self, path: Path) -> Path:
        self.downloads.append(path)
        path.write_bytes(self.content)
        return path


class FakeTelegramVoice:
    mime_type = "audio/ogg"

    def __init__(self, telegram_file: FakeTelegramFile) -> None:
        self.telegram_file = telegram_file

    async def get_file(self) -> FakeTelegramFile:
        return self.telegram_file


class FakeTelegramPhoto:
    def __init__(self, telegram_file: FakeTelegramFile, file_size: int = 1) -> None:
        self.telegram_file = telegram_file
        self.file_size = file_size
        self.width = 100
        self.height = 100

    async def get_file(self) -> FakeTelegramFile:
        return self.telegram_file


class FakeImageTextExtractor:
    def __init__(self, text: str) -> None:
        self.text = text
        self.paths: list[Path] = []

    def extract_text(self, image_path: Path) -> str:
        self.paths.append(image_path)
        return self.text


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
    async def test_school_coach_command_bypasses_router_with_message_provenance(self):
        router = FakeClaw()
        school_coach = FakeSchoolCoachClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(
                token="token",
                allowed_user_id=12345,
                sender_profiles=(TelegramSenderProfile(12345, "dad", "dad"),),
            ),
            router,
            logger=QuietLogger(),
            school_coach_claw=school_coach,
        )
        message = FakeMessage(
            "/school-coach What's your current plan for Ms. Rivera?",
            message_id=42,
        )

        await bot.handle_message(FakeUpdate(12345, message, chat_id=99), FakeContext())

        self.assertEqual(router.requests, [])
        self.assertEqual(
            message.replies,
            ["Current plan: Keep the next interaction short and positive."],
        )
        self.assertEqual(len(school_coach.calls), 1)
        provenance = school_coach.calls[0]["provenance"]
        self.assertEqual(provenance.kind, "user_report")
        self.assertEqual(provenance.ref, "telegram:99:12345:42")
        self.assertEqual(provenance.reported_by, "dad")

    async def test_school_coach_space_command_and_spoken_followup_stay_in_coach_mode(self):
        router = FakeClaw()
        school_coach = FakeSchoolCoachClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            router,
            logger=QuietLogger(),
            school_coach_claw=school_coach,
        )
        first = FakeMessage("/school coach", message_id=43)
        followup = FakeMessage("Yes, focus on Mrs. Thompson", message_id=44)
        reset = FakeMessage("new session", message_id=45)
        task = FakeMessage("add task email the school", message_id=45)

        await bot.handle_message(FakeUpdate(12345, first, chat_id=99), FakeContext())
        await bot.handle_message(FakeUpdate(12345, followup, chat_id=99), FakeContext())
        await bot.handle_message(FakeUpdate(12345, reset, chat_id=99), FakeContext())
        await bot.handle_message(FakeUpdate(12345, task, chat_id=99), FakeContext())

        self.assertEqual(
            [call["request"] for call in school_coach.calls],
            ["/school coach", "Yes, focus on Mrs. Thompson"],
        )
        self.assertEqual(reset.replies, ["Started a new N4OS session."])
        self.assertEqual(task.replies, ["router replied to: add task email the school"])

    async def test_spoken_school_coach_trigger_routes_voice_transcript(self):
        router = FakeClaw()
        school_coach = FakeSchoolCoachClaw()
        transcript = "What's your current plan for Mrs. Thompson?"
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            router,
            logger=QuietLogger(),
            audio_transcriber=FakeAudioTranscriber(transcript),
            school_coach_claw=school_coach,
        )
        message = FakeMessage(voice=FakeVoice(), message_id=46)

        await bot.handle_message(FakeUpdate(12345, message, chat_id=99), FakeContext())

        self.assertEqual(router.requests, [])
        self.assertEqual([call["request"] for call in school_coach.calls], [transcript])
        self.assertEqual(
            message.replies,
            [
                VOICE_TRANSCRIPTION_STARTED_MESSAGE,
                VOICE_TRANSCRIPTION_RESULT_MESSAGE.format(text=transcript),
                "Current plan: Keep the next interaction short and positive.",
            ],
        )

    async def test_second_brain_import_previews_then_save_confirms(self):
        importer = FakeSecondBrainImporter()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
            second_brain_importer=importer,
        )
        context = FakeContext()
        link = "https://docs.google.com/presentation/d/backtoschool123/edit?usp=sharing"
        first = FakeMessage(
            "/import second brain "
            f"{link}\n"
            "Instructions: This is Nysha's Back-to-School guide. Use it as second brain context."
        )

        await bot.handle_message(FakeUpdate(12345, first, chat_id=99), context)

        self.assertEqual(len(importer.preview_calls), 1)
        self.assertIn("N4OS import preview", first.replies[0])

        second = FakeMessage("save")
        await bot.handle_message(FakeUpdate(12345, second, chat_id=99), context)

        self.assertEqual(len(importer.save_calls), 1)
        self.assertEqual(second.replies, ["Saved second brain import."])

    async def test_second_brain_import_user_error_is_shown_directly(self):
        importer = FailingSecondBrainImporter()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
            second_brain_importer=importer,
        )
        message = FakeMessage(
            "/import second brain https://docs.google.com/presentation/d/...\n"
            "Instructions: This is Nysha's Back-to-School guide."
        )

        await bot.handle_message(FakeUpdate(12345, message, chat_id=99), FakeContext())

        self.assertEqual(message.replies, ["Paste the full Google Slides URL."])

    async def test_school_newsletter_prompt_previews_then_save_confirms(self):
        importer = FakeSchoolNewsletterImporter()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
            school_newsletter_importer=importer,
        )
        context = FakeContext()
        link = "https://docs.google.com/presentation/d/newsletter123/edit?usp=sharing"
        first = FakeMessage(f"/import school newsletter for Nysha {link}")

        await bot.handle_message(FakeUpdate(12345, first, chat_id=99), context)

        self.assertEqual(len(importer.preview_calls), 1)
        self.assertIn("School newsletter parsed for Nysha", first.replies[0])

        second = FakeMessage("save")
        await bot.handle_message(FakeUpdate(12345, second, chat_id=99), context)

        self.assertEqual(len(importer.save_calls), 1)
        self.assertEqual(second.replies, ["Saved school newsletter updates."])

    async def test_task_assignment_notifies_other_owner_with_summary_and_link(self):
        tasks = RecordingTasksClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(
                token="token",
                allowed_user_id=12345,
                allowed_user_ids=frozenset({12345, 67890}),
                sender_profiles=(
                    TelegramSenderProfile(12345, "dad", "dad"),
                    TelegramSenderProfile(67890, "mom", "mom"),
                ),
            ),
            N4OSClaw(tasks_claw=tasks),
            logger=QuietLogger(),
        )
        context = FakeContext()
        message = FakeMessage(
            "create task sign up for parent teacher meeting today owner mom"
        )

        await bot.handle_message(FakeUpdate(12345, message), context)

        self.assertEqual(
            context.bot.messages,
            [
                {
                    "chat_id": 67890,
                    "text": (
                        "Dad assigned you a task:\n"
                        "Sign up for the parent-teacher meeting\n"
                        "Due: 2026-08-11\n"
                        "Open: https://tasks.google.com/task/task-123"
                    ),
                }
            ],
        )

    async def test_task_assignment_does_not_notify_assigner(self):
        tasks = RecordingTasksClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(
                token="token",
                allowed_user_id=67890,
                sender_profiles=(TelegramSenderProfile(67890, "mom", "mom"),),
            ),
            N4OSClaw(tasks_claw=tasks),
            logger=QuietLogger(),
        )
        context = FakeContext()

        await bot.handle_message(
            FakeUpdate(67890, FakeMessage("add task sign up owner mom")),
            context,
        )

        self.assertEqual(context.bot.messages, [])

    async def test_native_router_normalizes_multiline_task_slash_command(self):
        tasks = RecordingTasksClaw()
        claw = N4OSClaw(tasks_claw=tasks)
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        result = bot.route_message(
            "/task create to schedule parent teacher at learning bee owner:mom today at 5 PM\n"
            "Details:\nDear Parents,\nAttached is our weekly schedule.\n"
            "Please pick a time slot.",
            claw=claw,
        )

        self.assertEqual(
            tasks.requests,
            [
                "add task to schedule parent teacher at learning bee owner:mom today at 5 PM\n"
                "Details:\nDear Parents\nAttached is our weekly schedule.\n"
                "Please pick a time slot."
            ],
        )
        self.assertIsNone(claw.pending_route_clarification)
        self.assertEqual(
            result.response,
            "Created task: Sign up for the parent-teacher meeting.",
        )

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

    async def test_secondary_allowed_user_can_route_messages(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(
                token="token",
                allowed_user_id=12345,
                allowed_user_ids=frozenset({12345, 67890}),
            ),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("Add task buy milk")

        await bot.handle_message(FakeUpdate(67890, message), None)

        self.assertEqual(claw.requests, ["Add task buy milk"])
        self.assertNotEqual(message.replies, [UNAUTHORIZED_MESSAGE])

    async def test_sender_profile_tags_routed_message_source_and_default_owner(self):
        claw = FakeLibraryRouteClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(
                token="token",
                allowed_user_id=12345,
                allowed_user_ids=frozenset({12345, 67890}),
                sender_profiles=(TelegramSenderProfile(67890, "niyati", "mom"),),
            ),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("Nysha read 8 pages")

        await bot.handle_message(FakeUpdate(67890, message), None)

        self.assertEqual(len(claw.calls), 1)
        request, source, default_owner, photo_path = claw.calls[0]
        self.assertEqual(request, "Nysha read 8 pages")
        self.assertEqual(source, "telegram_text:niyati")
        self.assertEqual(default_owner, "mom")
        self.assertIsNone(photo_path)

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

    async def test_task_command_with_assistant_help_routes_to_n4os(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        text = (
            "/task add task research next 3 places for weekend trips to take "
            "the kids. Need Noah assistant help to find suggestions."
        )
        message = FakeMessage(text)

        await bot.handle_message(FakeUpdate(12345, message), None)

        normalized = (
            "add task research next 3 places for weekend trips to take the kids. "
            "Need Noah assistant help to find suggestions."
        )
        self.assertEqual(claw.requests, [normalized])
        self.assertEqual(message.replies, [f"router replied to: {normalized}"])

    async def test_authorized_cart_command_routes_to_shopping(self):
        shopping = FakeShoppingRouteClaw()
        claw = N4OSClaw(shopping_claw=shopping)
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("/cart add milk to Costco")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(
            shopping.calls,
            [("/cart add milk to Costco", None)],
        )
        self.assertEqual(message.replies, ["Added milk to Costco."])

    async def test_photo_caption_routes_with_extracted_image_text(self):
        claw = FakeClaw()
        extractor = FakeImageTextExtractor("List title: India trip\nCheck letters\nBank locker")
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
            image_text_extractor=extractor,
        )
        telegram_file = FakeTelegramFile(b"image bytes")
        message = FakeMessage(
            caption=(
                "Create a task for every entry in the image with due date august first "
                "and tag IndiaTrip"
            ),
            photo=[FakeTelegramPhoto(telegram_file)],
        )

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(
            claw.requests,
            [
                "Create a task for every entry in the image with due date august first "
                "and tag IndiaTrip\n"
                "Image text:\n"
                "List title: India trip\n"
                "Check letters\n"
                "Bank locker",
            ],
        )
        self.assertEqual(message.replies, ["router replied to: " + claw.requests[0]])
        self.assertEqual(len(extractor.paths), 1)
        self.assertFalse(extractor.paths[0].exists())

    async def test_library_photo_is_preserved_as_dashboard_snap(self):
        claw = FakeLibraryRouteClaw()
        extractor = FakeImageTextExtractor("Book title: Mercy Watson")
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
            image_text_extractor=extractor,
        )
        telegram_file = FakeTelegramFile(b"book cover bytes")
        message = FakeMessage(
            caption="Nysha read this",
            photo=[FakeTelegramPhoto(telegram_file)],
        )

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(len(claw.calls), 1)
        request, source, default_owner, photo_path = claw.calls[0]
        self.assertIn("Book title: Mercy Watson", request)
        self.assertEqual(source, "telegram_photo")
        self.assertIsNone(default_owner)
        self.assertIsNotNone(photo_path)
        self.assertTrue(photo_path.startswith("/static/dashboard/uploads/reading/"))
        stored_file = READING_PHOTO_UPLOAD_DIR / Path(photo_path).name
        try:
            self.assertTrue(stored_file.exists())
            self.assertEqual(stored_file.read_bytes(), b"book cover bytes")
        finally:
            stored_file.unlink(missing_ok=True)
        self.assertEqual(message.replies, ["Saved library photo."])

    async def test_slash_library_add_parent_reading_photo_is_preserved(self):
        claw = FakeLibraryRouteClaw()
        extractor = FakeImageTextExtractor(
            "Book title: EARL & WORM THE BIG MESS STORIES Author: GREG PIZZOLI",
        )
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
            image_text_extractor=extractor,
        )
        telegram_file = FakeTelegramFile(b"book cover bytes")
        message = FakeMessage(
            caption="/library add dad read to nysha",
            photo=[FakeTelegramPhoto(telegram_file)],
        )

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(len(claw.calls), 1)
        request, source, default_owner, photo_path = claw.calls[0]
        self.assertIn("/library add dad read to nysha", request)
        self.assertIn("Book title: EARL & WORM THE BIG MESS STORIES", request)
        self.assertEqual(source, "telegram_photo")
        self.assertIsNone(default_owner)
        self.assertIsNotNone(photo_path)
        self.assertTrue(photo_path.startswith("/static/dashboard/uploads/reading/"))
        stored_file = READING_PHOTO_UPLOAD_DIR / Path(photo_path).name
        try:
            self.assertTrue(stored_file.exists())
            self.assertEqual(stored_file.read_bytes(), b"book cover bytes")
        finally:
            stored_file.unlink(missing_ok=True)
        self.assertEqual(message.replies, ["Saved library photo."])

    async def test_family_library_checkout_photo_is_not_preserved_as_reading_snap(self):
        claw = FakeLibraryCheckoutRouteClaw()
        extractor = FakeImageTextExtractor(
            "Book title: Earl & Worm: The Big Mess and Other Stories\n"
            "Author: Greg Pizzoli",
        )
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
            image_text_extractor=extractor,
        )
        telegram_file = FakeTelegramFile(b"checkout receipt bytes")
        message = FakeMessage(
            caption="Add to library family reading read by Dad",
            photo=[FakeTelegramPhoto(telegram_file)],
        )

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(len(claw.calls), 1)
        request, source, default_owner, photo_path = claw.calls[0]
        self.assertIn("Book title: Earl & Worm: The Big Mess and Other Stories", request)
        self.assertEqual(source, "telegram_photo")
        self.assertIsNone(default_owner)
        self.assertIsNotNone(photo_path)
        stored_file = READING_PHOTO_UPLOAD_DIR / Path(photo_path).name
        self.assertFalse(stored_file.exists())
        self.assertEqual(message.replies, ["Saved this library bag with 1 book at home."])

    async def test_homework_photo_capture_commits_homework_upload(self):
        homework = FakeHomeworkClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
            image_text_extractor=FakeImageTextExtractor("Homework title: All About Me\nDue date: 2026-08-21"),
            homework_claw=homework,
        )
        message = FakeMessage(
            caption="/capture homework Nysha",
            photo=[FakeTelegramPhoto(FakeTelegramFile(b"homework image"))],
        )

        await bot.handle_message(FakeUpdate(12345, message), None)

        request, source, photo_path, photo_sha256 = homework.calls[0]
        self.assertIn("Image text:", request)
        self.assertEqual(source, "telegram_photo")
        self.assertIsNotNone(photo_path)
        self.assertEqual(
            photo_sha256,
            "77a4a9bdc60913709eb309dc8a66dafc8f116a9d78802dc2ba96609ef86163e7",
        )
        self.assertTrue(photo_path.startswith("/static/dashboard/uploads/homework/"))
        stored_file = HOMEWORK_PHOTO_UPLOAD_DIR / Path(photo_path).name
        try:
            self.assertTrue(stored_file.exists())
            self.assertEqual(stored_file.read_bytes(), b"homework image")
        finally:
            stored_file.unlink(missing_ok=True)
        self.assertEqual(message.replies, ["Captured homework for Nysha: All About Me - assigned, due 2026-08-21."])

    async def test_failed_homework_capture_removes_staged_photo(self):
        homework = FakeHomeworkClaw(status="error")
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
            image_text_extractor=FakeImageTextExtractor("Homework title: All About Me"),
            homework_claw=homework,
        )
        message = FakeMessage(
            caption="/capture homework Nysha",
            photo=[FakeTelegramPhoto(FakeTelegramFile(b"failed homework image"))],
        )

        await bot.handle_message(FakeUpdate(12345, message), None)

        photo_path = homework.calls[0][2]
        self.assertIsNotNone(photo_path)
        self.assertFalse((HOMEWORK_PHOTO_UPLOAD_DIR / Path(photo_path).name).exists())
        self.assertEqual(message.replies, ["Captured homework for Nysha: All About Me - assigned, due 2026-08-21."])

    async def test_homework_photo_caption_routes_even_without_ocr_text(self):
        homework = FakeHomeworkClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
            image_text_extractor=FakeImageTextExtractor(""),
            homework_claw=homework,
        )
        message = FakeMessage(
            caption="Nysha math homework due Friday",
            photo=[FakeTelegramPhoto(FakeTelegramFile(b"homework caption image"))],
        )

        await bot.handle_message(FakeUpdate(12345, message), None)

        request, source, photo_path, photo_sha256 = homework.calls[0]
        self.assertEqual(request, "Nysha math homework due Friday")
        self.assertEqual(source, "telegram_photo")
        self.assertIsNotNone(photo_path)
        self.assertEqual(
            photo_sha256,
            "ea7351fec4e9f206eb5a583063348b53fd05d24a66064d76b55fbacb2b1f800b",
        )
        stored_file = HOMEWORK_PHOTO_UPLOAD_DIR / Path(photo_path).name
        try:
            self.assertTrue(stored_file.exists())
        finally:
            stored_file.unlink(missing_ok=True)
        self.assertEqual(message.replies, ["Captured homework for Nysha: All About Me - assigned, due 2026-08-21."])

    async def test_homework_complete_command_routes_without_photo(self):
        homework = FakeHomeworkClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
            homework_claw=homework,
        )
        message = FakeMessage("/homework complete art class Nysha")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(homework.calls[0][0], "/homework complete art class Nysha")
        self.assertEqual(homework.calls[0][1], "telegram_text")
        self.assertIsNone(homework.calls[0][2])
        self.assertEqual(message.replies, ["Captured homework for Nysha: All About Me - assigned, due 2026-08-21."])

    async def test_pending_homework_duplicate_preserves_photo_until_cancel(self):
        homework = PendingHomeworkClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
            image_text_extractor=FakeImageTextExtractor("Homework title: Second Grade Homework\nMonday: Read aloud"),
            homework_claw=homework,
        )
        message = FakeMessage(
            caption="/capture homework Nysha",
            photo=[FakeTelegramPhoto(FakeTelegramFile(b"duplicate homework image"))],
        )

        await bot.handle_message(FakeUpdate(12345, message), None)

        photo_path = homework.calls[0][2]
        self.assertIsNotNone(photo_path)
        stored_file = HOMEWORK_PHOTO_UPLOAD_DIR / Path(photo_path).name
        self.assertTrue(stored_file.exists())
        self.assertIn("Reply `attach`", message.replies[0])

        cancel_message = FakeMessage("cancel")
        await bot.handle_message(FakeUpdate(12345, cancel_message), None)

        self.assertEqual(homework.calls[-1][0], "cancel")
        self.assertFalse(stored_file.exists())
        self.assertEqual(cancel_message.replies, ["Canceled homework capture."])

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

    async def test_authorized_capture_alias_is_captured_without_routing(self):
        claw = FakeClaw()
        result = CaptureIngestResult(
            family=MemoryIngestResult(
                added=[
                    MemoryObservation(
                        observed_on=date(2026, 7, 21),
                        person="Nysha",
                        observation="liked teaching younger kids",
                        source="Telegram",
                    )
                ],
                skipped_duplicates=[],
            ),
            journal_entries=[
                JournalEntry(
                    captured_on=date(2026, 7, 21),
                    text="I felt proud.",
                    topics=["Parenting"],
                    source="Telegram",
                )
            ],
            skipped_journal_duplicates=[],
        )
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("/capture Nysha liked teaching younger kids. I felt proud.")

        with patch("telegram_bot.ingest_capture_notes", return_value=result) as ingest:
            await bot.handle_message(FakeUpdate(12345, message), None)

        ingest.assert_called_once_with(
            "/capture Nysha liked teaching younger kids. I felt proud.",
            n4os_root=bot.n4os_root,
            source="Telegram",
        )
        self.assertEqual(claw.requests, [])
        self.assertEqual(
            message.replies,
            [
                "Captured.\n"
                "\n"
                "Summary: Saved 2 notes: Nysha: liked teaching younger kids\n"
                "\n"
                "Captured text:\n"
                "- Nysha: liked teaching younger kids\n"
                "- I felt proud.\n"
                "\n"
                "- Family observation: Nysha (1)\n"
                "- Journal reflection: Parenting (1)\n"
                "\n"
                "No profiles, playbooks, or goals were promoted automatically."
            ],
        )

    async def test_sender_profile_tags_capture_origin(self):
        claw = FakeClaw()
        result = CaptureIngestResult(
            family=MemoryIngestResult(added=[], skipped_duplicates=[]),
            journal_entries=[],
            skipped_journal_duplicates=[],
        )
        bot = N4OSTelegramBot(
            TelegramConfig(
                token="token",
                allowed_user_id=12345,
                allowed_user_ids=frozenset({12345, 67890}),
                sender_profiles=(TelegramSenderProfile(67890, "niyati", "mom"),),
            ),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("/capture Niyati journal note")

        with patch("telegram_bot.ingest_capture_notes", return_value=result) as ingest:
            await bot.handle_message(FakeUpdate(67890, message), None)

        ingest.assert_called_once_with(
            "/capture Niyati journal note",
            n4os_root=bot.n4os_root,
            source="Telegram/Niyati",
        )
        self.assertEqual(claw.requests, [])

    async def test_authorized_bare_voice_capture_is_captured_without_routing(self):
        claw = FakeClaw()
        result = CaptureIngestResult(
            family=MemoryIngestResult(
                added=[
                    MemoryObservation(
                        observed_on=date(2026, 7, 21),
                        person="Nysha",
                        observation="asked why we do not travel business class",
                        source="Telegram",
                    )
                ],
                skipped_duplicates=[],
            ),
            journal_entries=[],
            skipped_journal_duplicates=[],
        )
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
            audio_transcriber=FakeAudioTranscriber(
                "Capture Nysha asked why we do not travel business class."
            ),
        )
        message = FakeMessage(voice=FakeVoice())

        with patch("telegram_bot.ingest_capture_notes", return_value=result) as ingest:
            await bot.handle_message(FakeUpdate(12345, message), None)

        ingest.assert_called_once_with(
            "Capture Nysha asked why we do not travel business class.",
            n4os_root=bot.n4os_root,
            source="telegram_voice",
        )
        self.assertEqual(claw.requests, [])
        self.assertEqual(message.replies[0], VOICE_TRANSCRIPTION_STARTED_MESSAGE)
        self.assertEqual(
            message.replies[1],
            "Transcribed: Capture Nysha asked why we do not travel business class.",
        )
        self.assertIn("Captured.", message.replies[2])

    async def test_capture_clarification_captures_pending_request(self):
        claw = PendingClarificationClaw("Nysha asked why we do not travel business class.")
        result = CaptureIngestResult(
            family=MemoryIngestResult(
                added=[
                    MemoryObservation(
                        observed_on=date(2026, 7, 21),
                        person="Nysha",
                        observation="asked why we do not travel business class",
                        source="Telegram",
                    )
                ],
                skipped_duplicates=[],
            ),
            journal_entries=[],
            skipped_journal_duplicates=[],
        )
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("a capture")

        with patch("telegram_bot.ingest_capture_notes", return_value=result) as ingest:
            await bot.handle_message(FakeUpdate(12345, message), None)

        ingest.assert_called_once_with(
            "Nysha asked why we do not travel business class.",
            n4os_root=bot.n4os_root,
            source="Telegram",
        )
        self.assertIsNone(claw.pending_route_clarification)
        self.assertEqual(claw.requests, [])
        self.assertIn("Captured.", message.replies[0])

    async def test_undo_after_capture_uses_live_capture_undo_stack(self):
        claw = FakeClaw()
        result = CaptureIngestResult(
            family=MemoryIngestResult(
                added=[
                    MemoryObservation(
                        observed_on=date(2026, 7, 21),
                        person="Nysha",
                        observation="liked teaching younger kids",
                        source="Telegram",
                    )
                ],
                skipped_duplicates=[],
            ),
            journal_entries=[
                JournalEntry(
                    captured_on=date(2026, 7, 21),
                    text="I felt proud.",
                    topics=["Parenting"],
                    source="Telegram",
                )
            ],
            skipped_journal_duplicates=[],
        )
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        capture_message = FakeMessage("/capture Nysha liked teaching younger kids. I felt proud.")
        undo_message = FakeMessage("undo")

        with (
            patch("telegram_bot.ingest_capture_notes", return_value=result),
            patch(
                "telegram_bot.undo_capture_ingest",
                return_value=CaptureUndoResult(
                    family_observations_removed=1,
                    journal_entries_removed=1,
                ),
            ) as undo,
        ):
            await bot.handle_message(FakeUpdate(12345, capture_message), None)
            await bot.handle_message(FakeUpdate(12345, undo_message), None)

        undo.assert_called_once_with(result, n4os_root=bot.n4os_root)
        self.assertEqual(claw.requests, [])
        self.assertEqual(
            undo_message.replies,
            ["Undid capture: removed 1 family observation and 1 journal entry."],
        )

    def test_capture_correction_parses_deterministic_replacements(self):
        text = "Capture for Damage, it was been fun."

        corrected, error = _apply_capture_correction(
            text,
            'replace "Damage" with "Dimage"',
        )
        self.assertIsNone(error)
        self.assertEqual(corrected, "Capture for Dimage, it was been fun.")

        corrected, error = _apply_capture_correction(
            text,
            "Dimage, not Damage; it has been fun, not it was been fun",
        )
        self.assertIsNone(error)
        self.assertEqual(corrected, "Capture for Dimage, it has been fun.")

        corrected, error = _apply_capture_correction(text, "Damage -> Dimage")
        self.assertIsNone(error)
        self.assertEqual(corrected, "Capture for Dimage, it was been fun.")

    def test_capture_correction_reports_unmatched_replacement(self):
        corrected, error = _apply_capture_correction(
            "Capture for Dimage.",
            'replace "Damage" with "Dimage"',
        )

        self.assertIsNone(corrected)
        self.assertEqual(error, 'I could not find "Damage" in the saved capture.')

    def test_capture_correction_replaces_repeated_typos(self):
        corrected, error = _apply_capture_correction(
            "Capture Damage proposal. Damage follow-up.",
            "Dimage, not Damage",
        )

        self.assertIsNone(error)
        self.assertEqual(corrected, "Capture Dimage proposal. Dimage follow-up.")

    def test_capture_correction_allows_semicolons_inside_quoted_replacements(self):
        corrected, error = _apply_capture_correction(
            "Capture A; B proposal. Damage follow-up.",
            'replace "A; B" with "A and B"; Dimage, not Damage',
        )

        self.assertIsNone(error)
        self.assertEqual(corrected, "Capture A and B proposal. Dimage follow-up.")

    def test_capture_correction_allows_semicolons_inside_single_quoted_replacements(self):
        corrected, error = _apply_capture_correction(
            "Capture A; B proposal. Damage follow-up.",
            "replace 'A; B' with 'A and B'; Dimage, not Damage",
        )

        self.assertIsNone(error)
        self.assertEqual(corrected, "Capture A and B proposal. Dimage follow-up.")

    def test_capture_correction_splits_bare_apostrophes_normally(self):
        corrected, error = _apply_capture_correction(
            "Capture Navyas proud mood.",
            "Navya's, not Navyas; calm, not proud",
        )

        self.assertIsNone(error)
        self.assertEqual(corrected, "Capture Navya's calm mood.")

    def test_capture_correction_inserts_replacement_text_literally(self):
        corrected, error = _apply_capture_correction(
            "Capture path is Damage.",
            'replace "Damage" with "C:\\tmp\\1"',
        )

        self.assertIsNone(error)
        self.assertEqual(corrected, "Capture path is C:\\tmp\\1.")

    async def test_capture_correction_preserves_original_default_date(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                FakeClaw(),
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            result = ingest_capture_notes(
                "Capture I felt Damage.",
                n4os_root=n4os_root,
                default_date=date(2026, 8, 17),
                source="Telegram",
            )
            session = bot.sessions.get("telegram:12345")
            session.undo_stack.append(TelegramUndoEntry(kind="capture", capture_result=result))
            session.recent_captures.append(
                TelegramRecentCapture(
                    text="Capture I felt Damage.",
                    source="Telegram",
                    result=result,
                )
            )
            correction = FakeMessage("fix last capture: Dimage, not Damage")

            await bot.handle_message(FakeUpdate(12345, correction), None)

            original_date_path = n4os_root / "journal" / "2026-08-17.md"
            today_path = n4os_root / "journal" / f"{date.today().isoformat()}.md"

            self.assertIn("Dimage", original_date_path.read_text(encoding="utf-8"))
            if today_path != original_date_path:
                self.assertFalse(today_path.exists())

    async def test_capture_correction_refuses_when_edit_creates_multiple_notes(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        original = CaptureIngestResult(
            family=MemoryIngestResult(added=[], skipped_duplicates=[]),
            journal_entries=[
                JournalEntry(
                    captured_on=date(2026, 8, 17),
                    text="I felt Damage.",
                    topics=[],
                    source="Telegram",
                )
            ],
            skipped_journal_duplicates=[],
            notes=[
                CaptureNote(
                    captured_on=date(2026, 8, 17),
                    text="I felt Damage.",
                    source="Telegram",
                )
            ],
        )
        session = bot.sessions.get("telegram:12345")
        session.undo_stack.append(TelegramUndoEntry(kind="capture", capture_result=original))
        session.recent_captures.append(
            TelegramRecentCapture(
                text="Capture I felt Damage.",
                source="Telegram",
                result=original,
            )
        )
        message = FakeMessage('fix last capture: replace "Damage" with "Dimage.\nI felt proud"')

        with (
            patch("telegram_bot.undo_capture_ingest") as undo,
            patch("telegram_bot.ingest_capture_notes") as ingest,
        ):
            await bot.handle_message(FakeUpdate(12345, message), None)

        undo.assert_not_called()
        ingest.assert_not_called()
        self.assertEqual(
            message.replies,
            [
                "I can only fix a single saved capture note right now. Keep the correction to one note, or send undo and resend the corrected capture."
            ],
        )

    async def test_capture_correction_replays_saved_link_preview_without_enrichment(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        original = CaptureIngestResult(
            family=MemoryIngestResult(added=[], skipped_duplicates=[]),
            journal_entries=[
                JournalEntry(
                    captured_on=date(2026, 8, 17),
                    text="I saved Damage link.",
                    topics=[],
                    source="Telegram",
                )
            ],
            skipped_journal_duplicates=[],
            notes=[
                CaptureNote(
                    captured_on=date(2026, 8, 17),
                    text=(
                        "I saved Damage link https://example.test "
                        "and https://second.example "
                        "[Link: https://example.test; title: Original Title] "
                        "[Link: https://second.example; title: Second Title]"
                    ),
                    source="Telegram",
                )
            ],
        )
        corrected = CaptureIngestResult(
            family=MemoryIngestResult(added=[], skipped_duplicates=[]),
            journal_entries=[
                JournalEntry(
                    captured_on=date(2026, 8, 17),
                    text="I saved Dimage link.",
                    topics=[],
                    source="Telegram",
                )
            ],
            skipped_journal_duplicates=[],
            notes=[
                CaptureNote(
                    captured_on=date(2026, 8, 17),
                    text=(
                        "I saved Dimage link https://example.test "
                        "[Link: https://example.test; title: Original Title]"
                    ),
                    source="Telegram",
                )
            ],
        )
        session = bot.sessions.get("telegram:12345")
        session.undo_stack.append(TelegramUndoEntry(kind="capture", capture_result=original))
        session.recent_captures.append(
            TelegramRecentCapture(
                text="/capture I saved Damage link https://example.test and https://second.example",
                source="Telegram",
                result=original,
            )
        )
        message = FakeMessage("fix last capture: Dimage, not Damage")

        with (
            patch(
                "telegram_bot.undo_capture_ingest",
                return_value=CaptureUndoResult(
                    family_observations_removed=0,
                    journal_entries_removed=1,
                ),
            ),
            patch("telegram_bot.ingest_capture_notes", return_value=corrected) as ingest,
        ):
            await bot.handle_message(FakeUpdate(12345, message), None)

        ingest.assert_called_once()
        args, kwargs = ingest.call_args
        self.assertIn("title: Original Title", args[0])
        self.assertIn("title: Second Title", args[0])
        self.assertIn("Dimage", args[0])
        self.assertFalse(kwargs["enrich_links"])

    async def test_capture_correction_preserves_preview_with_closing_bracket(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        original = CaptureIngestResult(
            family=MemoryIngestResult(added=[], skipped_duplicates=[]),
            journal_entries=[
                JournalEntry(
                    captured_on=date(2026, 8, 17),
                    text="I saved Damage link.",
                    topics=[],
                    source="Telegram",
                )
            ],
            skipped_journal_duplicates=[],
            notes=[
                CaptureNote(
                    captured_on=date(2026, 8, 17),
                    text=(
                        "I saved Damage link https://example.test "
                        "[Link: https://example.test; title: Docs [v2]]"
                    ),
                    source="Telegram",
                )
            ],
        )
        corrected = CaptureIngestResult(
            family=MemoryIngestResult(added=[], skipped_duplicates=[]),
            journal_entries=[
                JournalEntry(
                    captured_on=date(2026, 8, 17),
                    text="I saved Dimage link.",
                    topics=[],
                    source="Telegram",
                )
            ],
            skipped_journal_duplicates=[],
        )
        session = bot.sessions.get("telegram:12345")
        session.undo_stack.append(TelegramUndoEntry(kind="capture", capture_result=original))
        session.recent_captures.append(
            TelegramRecentCapture(
                text="/capture I saved Damage link https://example.test",
                source="Telegram",
                result=original,
            )
        )
        message = FakeMessage("fix last capture: Dimage, not Damage")

        with (
            patch(
                "telegram_bot.undo_capture_ingest",
                return_value=CaptureUndoResult(
                    family_observations_removed=0,
                    journal_entries_removed=1,
                ),
            ),
            patch("telegram_bot.ingest_capture_notes", return_value=corrected) as ingest,
        ):
            await bot.handle_message(FakeUpdate(12345, message), None)

        ingest.assert_called_once()
        args, kwargs = ingest.call_args
        self.assertIn("title: Docs [v2]", args[0])
        self.assertFalse(kwargs["enrich_links"])

    async def test_capture_correction_strips_stale_preview_when_url_changes(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        original = CaptureIngestResult(
            family=MemoryIngestResult(added=[], skipped_duplicates=[]),
            journal_entries=[
                JournalEntry(
                    captured_on=date(2026, 8, 17),
                    text="I saved https://old.example.",
                    topics=[],
                    source="Telegram",
                )
            ],
            skipped_journal_duplicates=[],
            notes=[
                CaptureNote(
                    captured_on=date(2026, 8, 17),
                    text=(
                        "I saved https://old.example "
                        "[Link: https://old.example; title: Old Title]"
                    ),
                    source="Telegram",
                )
            ],
        )
        corrected = CaptureIngestResult(
            family=MemoryIngestResult(added=[], skipped_duplicates=[]),
            journal_entries=[
                JournalEntry(
                    captured_on=date(2026, 8, 17),
                    text="I saved https://new.example.",
                    topics=[],
                    source="Telegram",
                )
            ],
            skipped_journal_duplicates=[],
        )
        session = bot.sessions.get("telegram:12345")
        session.undo_stack.append(TelegramUndoEntry(kind="capture", capture_result=original))
        session.recent_captures.append(
            TelegramRecentCapture(
                text="/capture I saved https://old.example",
                source="Telegram",
                result=original,
            )
        )
        message = FakeMessage(
            'fix last capture: https://old.example -> https://new.example'
        )

        with (
            patch(
                "telegram_bot.undo_capture_ingest",
                return_value=CaptureUndoResult(
                    family_observations_removed=0,
                    journal_entries_removed=1,
                ),
            ),
            patch("telegram_bot.ingest_capture_notes", return_value=corrected) as ingest,
        ):
            await bot.handle_message(FakeUpdate(12345, message), None)

        ingest.assert_called_once()
        args, kwargs = ingest.call_args
        self.assertIn("https://new.example", args[0])
        self.assertNotIn("Old Title", args[0])
        self.assertTrue(kwargs["enrich_links"])

    async def test_capture_correction_preserves_unchanged_preview_when_one_url_changes(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        original = CaptureIngestResult(
            family=MemoryIngestResult(added=[], skipped_duplicates=[]),
            journal_entries=[
                JournalEntry(
                    captured_on=date(2026, 8, 17),
                    text="I saved https://old.example and https://keep.example.",
                    topics=[],
                    source="Telegram",
                )
            ],
            skipped_journal_duplicates=[],
            notes=[
                CaptureNote(
                    captured_on=date(2026, 8, 17),
                    text=(
                        "I saved https://old.example and https://keep.example "
                        "[Link: https://old.example; title: Old Title] "
                        "[Link: https://keep.example; title: Keep Title]"
                    ),
                    source="Telegram",
                )
            ],
        )
        corrected = CaptureIngestResult(
            family=MemoryIngestResult(added=[], skipped_duplicates=[]),
            journal_entries=[
                JournalEntry(
                    captured_on=date(2026, 8, 17),
                    text=(
                        "I saved https://new.example and https://keep.example "
                        "[Link: https://keep.example; title: Keep Title]"
                    ),
                    topics=[],
                    source="Telegram",
                )
            ],
            skipped_journal_duplicates=[],
        )
        session = bot.sessions.get("telegram:12345")
        session.undo_stack.append(TelegramUndoEntry(kind="capture", capture_result=original))
        session.recent_captures.append(
            TelegramRecentCapture(
                text="/capture I saved https://old.example and https://keep.example",
                source="Telegram",
                result=original,
            )
        )
        message = FakeMessage(
            'fix last capture: https://old.example -> https://new.example'
        )

        with (
            patch(
                "telegram_bot.undo_capture_ingest",
                return_value=CaptureUndoResult(
                    family_observations_removed=0,
                    journal_entries_removed=1,
                ),
            ),
            patch("telegram_bot.ingest_capture_notes", return_value=corrected) as ingest,
        ):
            await bot.handle_message(FakeUpdate(12345, message), None)

        ingest.assert_called_once()
        args, kwargs = ingest.call_args
        self.assertIn("https://new.example", args[0])
        self.assertIn("title: Keep Title", args[0])
        self.assertNotIn("title: Old Title", args[0])
        self.assertTrue(kwargs["enrich_links"])

    async def test_capture_correction_preserves_inline_dated_preview(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        original = CaptureIngestResult(
            family=MemoryIngestResult(added=[], skipped_duplicates=[]),
            journal_entries=[
                JournalEntry(
                    captured_on=date(2026, 8, 17),
                    text="I saved Damage https://example.test.",
                    topics=[],
                    source="Telegram",
                )
            ],
            skipped_journal_duplicates=[],
            notes=[
                CaptureNote(
                    captured_on=date(2026, 8, 17),
                    text=(
                        "I saved Damage https://example.test "
                        "[Link: https://example.test; title: Original Title]"
                    ),
                    source="Telegram",
                )
            ],
        )
        corrected = CaptureIngestResult(
            family=MemoryIngestResult(added=[], skipped_duplicates=[]),
            journal_entries=[
                JournalEntry(
                    captured_on=date(2026, 8, 17),
                    text="I saved Dimage https://example.test.",
                    topics=[],
                    source="Telegram",
                )
            ],
            skipped_journal_duplicates=[],
        )
        session = bot.sessions.get("telegram:12345")
        session.undo_stack.append(TelegramUndoEntry(kind="capture", capture_result=original))
        session.recent_captures.append(
            TelegramRecentCapture(
                text="/capture 2026-08-17 I saved Damage https://example.test",
                source="Telegram",
                result=original,
            )
        )
        message = FakeMessage("fix last capture: Dimage, not Damage")

        with (
            patch(
                "telegram_bot.undo_capture_ingest",
                return_value=CaptureUndoResult(
                    family_observations_removed=0,
                    journal_entries_removed=1,
                ),
            ),
            patch("telegram_bot.ingest_capture_notes", return_value=corrected) as ingest,
        ):
            await bot.handle_message(FakeUpdate(12345, message), None)

        ingest.assert_called_once()
        args, kwargs = ingest.call_args
        self.assertIn("title: Original Title", args[0])
        self.assertFalse(kwargs["enrich_links"])

    async def test_fix_last_capture_corrects_voice_journal_and_undoes_corrected_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            transcript = "Capture I was been fun working on a Damage proposal."
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                FakeClaw(),
                logger=QuietLogger(),
                audio_transcriber=FakeAudioTranscriber(transcript),
                n4os_root=n4os_root,
            )
            capture = FakeMessage(voice=FakeVoice())
            correction = FakeMessage(
                "fix last capture: Dimage, not Damage; it has been fun, not I was been fun"
            )
            undo = FakeMessage("undo")
            followup_correction = FakeMessage("fix last capture: Design, not Damage")

            await bot.handle_message(FakeUpdate(12345, capture), None)
            await bot.handle_message(FakeUpdate(12345, correction), None)

            journal_path = next((n4os_root / "journal").glob("*.md"))
            journal_text = journal_path.read_text(encoding="utf-8")
            self.assertIn("Dimage", journal_text)
            self.assertIn("it has been fun", journal_text)
            self.assertNotIn("Damage", journal_text)
            self.assertNotIn("I was been fun", journal_text)
            self.assertIn("Source: telegram_voice", journal_text)
            self.assertEqual(correction.replies[0].splitlines()[0], "Updated captured note.")

            await bot.handle_message(FakeUpdate(12345, undo), None)

            self.assertEqual(
                undo.replies,
                ["Undid capture correction: restored the previous captured note."],
            )
            restored_text = journal_path.read_text(encoding="utf-8")
            self.assertIn("Damage", restored_text)
            self.assertIn("I was been fun", restored_text)
            self.assertNotIn("Dimage", restored_text)

            await bot.handle_message(FakeUpdate(12345, followup_correction), None)
            self.assertEqual(followup_correction.replies[0].splitlines()[0], "Updated captured note.")
            self.assertIn("Design", journal_path.read_text(encoding="utf-8"))

    async def test_reply_to_captured_message_corrects_that_capture(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                FakeClaw(),
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            first = FakeMessage("/capture Nysha liked Damage puzzles.")
            second = FakeMessage("/capture I felt proud after work.")

            await bot.handle_message(FakeUpdate(12345, first), None)
            first_capture_reply = first.sent_messages[-1]
            await bot.handle_message(FakeUpdate(12345, second), None)
            correction = FakeMessage(
                'replace "Damage" with "Dimage"',
                reply_to_message=first_capture_reply,
            )

            await bot.handle_message(FakeUpdate(12345, correction), None)
            followup = FakeMessage(
                'replace "Dimage" with "Design"',
                reply_to_message=correction.sent_messages[-1],
            )
            await bot.handle_message(FakeUpdate(12345, followup), None)

            observations_path = next((n4os_root / "family" / "observations").glob("*.md"))
            observations_text = observations_path.read_text(encoding="utf-8")
            journal_path = next((n4os_root / "journal").glob("*.md"))
            journal_text = journal_path.read_text(encoding="utf-8")
            self.assertIn("Design puzzles", observations_text)
            self.assertNotIn("Dimage puzzles", observations_text)
            self.assertNotIn("Damage puzzles", observations_text)
            self.assertIn("I felt proud after [[playbooks/Career|work]]", journal_text)
            self.assertEqual(correction.replies[0].splitlines()[0], "Updated captured note.")
            self.assertEqual(followup.replies[0].splitlines()[0], "Updated captured note.")

    async def test_reply_fix_last_capture_prefers_replied_capture(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                FakeClaw(),
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            first = FakeMessage("/capture Nysha liked Damage puzzles.")
            second = FakeMessage("/capture I felt proud.")

            await bot.handle_message(FakeUpdate(12345, first), None)
            await bot.handle_message(FakeUpdate(12345, second), None)
            correction = FakeMessage(
                "fix last capture: Dimage, not Damage",
                reply_to_message=first.sent_messages[-1],
            )
            await bot.handle_message(FakeUpdate(12345, correction), None)

            observations_path = next((n4os_root / "family" / "observations").glob("*.md"))
            journal_path = next((n4os_root / "journal").glob("*.md"))
            observations_text = observations_path.read_text(encoding="utf-8")
            journal_text = journal_path.read_text(encoding="utf-8")

        self.assertIn("Dimage puzzles", observations_text)
        self.assertIn("I felt proud", journal_text)
        self.assertEqual(correction.replies[0].splitlines()[0], "Updated captured note.")

    async def test_reply_fix_last_capture_refuses_unknown_reply_target(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                FakeClaw(),
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            capture = FakeMessage("/capture I felt Damage.")

            await bot.handle_message(FakeUpdate(12345, capture), None)
            correction = FakeMessage(
                "fix last capture: Dimage, not Damage",
                reply_to_message=FakeMessage("old bot reply", message_id=999999),
            )
            await bot.handle_message(FakeUpdate(12345, correction), None)

            journal_path = next((n4os_root / "journal").glob("*.md"))
            journal_text = journal_path.read_text(encoding="utf-8")

        self.assertEqual(correction.replies, ["No recent capture found to fix."])
        self.assertIn("Damage", journal_text)
        self.assertNotIn("Dimage", journal_text)

    async def test_bare_not_reply_to_capture_does_not_force_correction(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            claw = FakeClaw()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            capture = FakeMessage("/capture I felt proud.")

            await bot.handle_message(FakeUpdate(12345, capture), None)
            reply = FakeMessage(
                "later, not today",
                reply_to_message=capture.sent_messages[-1],
            )
            await bot.handle_message(FakeUpdate(12345, reply), None)

        self.assertEqual(claw.requests, ["later, not today"])
        self.assertEqual(reply.replies, ["router replied to: later, not today"])

    async def test_batch_capture_correction_is_refused_for_v1(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                FakeClaw(),
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            capture = FakeMessage(
                "\n".join(
                    [
                        "/capture",
                        "Nysha liked Damage puzzles.",
                        "I felt proud after work.",
                    ]
                )
            )

            await bot.handle_message(FakeUpdate(12345, capture), None)
            correction = FakeMessage("fix last capture: Dimage, not Damage")
            await bot.handle_message(FakeUpdate(12345, correction), None)

            observations_path = next((n4os_root / "family" / "observations").glob("*.md"))
            journal_path = next((n4os_root / "journal").glob("*.md"))
            observations_text = observations_path.read_text(encoding="utf-8")
            journal_text = journal_path.read_text(encoding="utf-8")

        self.assertIn("Damage puzzles", observations_text)
        self.assertNotIn("Dimage puzzles", observations_text)
        self.assertIn("I felt proud after [[playbooks/Career|work]]", journal_text)
        self.assertEqual(
            correction.replies,
            [
                "I can only fix a single saved capture note right now. For batch captures, send undo and resend the corrected capture."
            ],
        )

    async def test_batch_capture_correction_is_refused_even_with_one_new_block(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                FakeClaw(),
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            await bot.handle_message(
                FakeUpdate(12345, FakeMessage("/capture I felt proud.")),
                None,
            )
            capture = FakeMessage(
                "\n".join(
                    [
                        "/capture",
                        "I felt proud.",
                        "I felt Damage.",
                    ]
                )
            )

            await bot.handle_message(FakeUpdate(12345, capture), None)
            correction = FakeMessage("fix last capture: Dimage, not Damage")
            await bot.handle_message(FakeUpdate(12345, correction), None)

            journal_path = next((n4os_root / "journal").glob("*.md"))
            journal_text = journal_path.read_text(encoding="utf-8")

        self.assertIn("I felt Damage.", journal_text)
        self.assertNotIn("I felt Dimage.", journal_text)
        self.assertEqual(
            correction.replies,
            [
                "I can only fix a single saved capture note right now. For batch captures, send undo and resend the corrected capture."
            ],
        )

    async def test_reply_to_later_captured_reply_chunk_corrects_capture(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                FakeClaw(),
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            capture = FakeMessage("/capture I felt Damage " + ("after work " * 500))

            await bot.handle_message(FakeUpdate(12345, capture), None)
            self.assertGreater(len(capture.sent_messages), 1)
            correction = FakeMessage(
                "fix: Dimage, not Damage",
                reply_to_message=capture.sent_messages[-1],
            )
            await bot.handle_message(FakeUpdate(12345, correction), None)

            journal_path = next((n4os_root / "journal").glob("*.md"))
            journal_text = journal_path.read_text(encoding="utf-8")

        self.assertIn("Dimage", journal_text)
        self.assertNotIn("Damage", journal_text)
        self.assertEqual(correction.replies[0].splitlines()[0], "Updated captured note.")

    async def test_capture_correction_without_recent_capture_refuses(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        message = FakeMessage("fix last capture: Dimage, not Damage")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies, ["No recent capture found to fix."])

    async def test_capture_undo_prunes_recent_correction_target(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                FakeClaw(),
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            capture = FakeMessage("/capture I felt proud.")
            undo = FakeMessage("undo")
            correction = FakeMessage("fix last capture: calm, not proud")

            await bot.handle_message(FakeUpdate(12345, capture), None)
            await bot.handle_message(FakeUpdate(12345, undo), None)
            await bot.handle_message(FakeUpdate(12345, correction), None)

        self.assertEqual(correction.replies, ["No recent capture found to fix."])

    async def test_undo_after_correction_then_undo_removes_restored_capture(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                FakeClaw(),
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            capture = FakeMessage("/capture I felt Damage.")
            correction = FakeMessage("fix last capture: Dimage, not Damage")
            undo_correction = FakeMessage("undo")
            undo_capture = FakeMessage("undo")

            await bot.handle_message(FakeUpdate(12345, capture), None)
            await bot.handle_message(FakeUpdate(12345, correction), None)
            await bot.handle_message(FakeUpdate(12345, undo_correction), None)
            await bot.handle_message(FakeUpdate(12345, undo_capture), None)

            journal_path = next((n4os_root / "journal").glob("*.md"))
            journal_text = journal_path.read_text(encoding="utf-8")

        self.assertEqual(
            undo_correction.replies,
            ["Undid capture correction: restored the previous captured note."],
        )
        self.assertIn("Undid capture", undo_capture.replies[0])
        self.assertNotIn("Damage", journal_text)
        self.assertNotIn("Dimage", journal_text)

    async def test_undo_correction_aborts_when_corrected_capture_partially_removed(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        corrected = CaptureIngestResult(
            family=MemoryIngestResult(
                added=[
                    MemoryObservation(
                        observed_on=date(2026, 8, 17),
                        person="Nysha",
                        observation="liked Dimage puzzles",
                        source="Telegram",
                    )
                ],
                skipped_duplicates=[],
            ),
            journal_entries=[
                JournalEntry(
                    captured_on=date(2026, 8, 17),
                    text="I felt proud.",
                    topics=[],
                    source="Telegram",
                )
            ],
            skipped_journal_duplicates=[],
        )
        bot.undo_stack.append(
            TelegramUndoEntry(
                kind="capture_correction",
                capture_result=corrected,
                capture_current_text="/capture Nysha liked Dimage puzzles.\nI felt proud.",
                capture_restore_text="/capture Nysha liked Damage puzzles.\nI felt proud.",
                capture_restore_source="Telegram",
                capture_restore_default_date=date(2026, 8, 17),
            )
        )

        with (
            patch(
                "telegram_bot.undo_capture_ingest",
                return_value=CaptureUndoResult(
                    family_observations_removed=1,
                    journal_entries_removed=0,
                ),
            ),
            patch("telegram_bot.ingest_capture_notes") as ingest,
        ):
            reply = bot.undo_last_action()

        self.assertEqual(
            reply,
            "I could not safely remove the full corrected capture, so I left it unchanged.",
        )
        ingest.assert_called_once()
        self.assertIn("Dimage puzzles", ingest.call_args.args[0])
        self.assertEqual(len(bot.undo_stack), 1)

    async def test_undo_correction_preserves_undo_stack_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            claw = FakeClaw()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            capture = FakeMessage("/capture I felt Damage.")

            await bot.handle_message(FakeUpdate(12345, capture), None)
            session = bot.sessions.get("telegram:12345")
            session.undo_stack.append(TelegramUndoEntry(kind="router"))
            correction = FakeMessage("fix last capture: Dimage, not Damage")
            undo_correction = FakeMessage("undo")
            undo_router = FakeMessage("undo")

            await bot.handle_message(FakeUpdate(12345, correction), None)
            await bot.handle_message(FakeUpdate(12345, undo_correction), None)
            await bot.handle_message(FakeUpdate(12345, undo_router), None)

            journal_path = next((n4os_root / "journal").glob("*.md"))
            journal_text = journal_path.read_text(encoding="utf-8")

        self.assertEqual(claw.requests, ["undo"])
        self.assertIn("Damage", journal_text)
        self.assertEqual(undo_router.replies, ["router replied to: undo"])

    async def test_undo_correction_preserves_reply_targets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                FakeClaw(),
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            capture = FakeMessage("/capture I felt Damage.")

            await bot.handle_message(FakeUpdate(12345, capture), None)
            correction = FakeMessage("fix last capture: Dimage, not Damage")
            undo_correction = FakeMessage("undo")

            await bot.handle_message(FakeUpdate(12345, correction), None)
            correction_reply = correction.sent_messages[-1]
            await bot.handle_message(FakeUpdate(12345, undo_correction), None)
            reply_correction = FakeMessage(
                'replace "Damage" with "Design"',
                reply_to_message=correction_reply,
            )
            await bot.handle_message(FakeUpdate(12345, reply_correction), None)

            journal_path = next((n4os_root / "journal").glob("*.md"))
            journal_text = journal_path.read_text(encoding="utf-8")

        self.assertEqual(reply_correction.replies[0].splitlines()[0], "Updated captured note.")
        self.assertIn("Design", journal_text)

    def test_undo_correction_restores_corrected_capture_when_previous_restore_fails(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        corrected = CaptureIngestResult(
            family=MemoryIngestResult(added=[], skipped_duplicates=[]),
            journal_entries=[
                JournalEntry(
                    captured_on=date(2026, 8, 17),
                    text="I felt Dimage.",
                    topics=[],
                    source="Telegram",
                )
            ],
            skipped_journal_duplicates=[],
            notes=[
                CaptureNote(
                    captured_on=date(2026, 8, 17),
                    text="I felt Dimage.",
                    source="Telegram",
                )
            ],
        )
        replacement = CaptureIngestResult(
            family=MemoryIngestResult(added=[], skipped_duplicates=[]),
            journal_entries=[
                JournalEntry(
                    captured_on=date(2026, 8, 17),
                    text="I felt Dimage.",
                    topics=[],
                    source="Telegram",
                )
            ],
            skipped_journal_duplicates=[],
            notes=[
                CaptureNote(
                    captured_on=date(2026, 8, 17),
                    text="I felt Dimage.",
                    source="Telegram",
                )
            ],
        )
        session = bot.sessions.get("telegram:12345")
        session.undo_stack.append(
            TelegramUndoEntry(
                kind="capture_correction",
                capture_result=corrected,
                capture_current_text="/capture I felt Dimage.",
                capture_restore_text="/capture I felt Damage.",
                capture_restore_editable_text="/capture I felt Damage.",
                capture_restore_source="Telegram",
                capture_restore_default_date=date(2026, 8, 17),
            )
        )

        with (
            patch(
                "telegram_bot.undo_capture_ingest",
                return_value=CaptureUndoResult(
                    family_observations_removed=0,
                    journal_entries_removed=1,
                ),
            ),
            patch(
                "telegram_bot.ingest_capture_notes",
                side_effect=[RuntimeError("restore failed"), replacement],
            ) as ingest,
        ):
            reply = bot.undo_last_action(
                undo_stack=session.undo_stack,
                recent_captures=session.recent_captures,
            )

        self.assertEqual(
            reply,
            "I could not restore the previous capture, so I kept the corrected capture available for undo.",
        )
        self.assertEqual(len(session.undo_stack), 1)
        self.assertIs(session.undo_stack[-1].capture_result, replacement)
        self.assertIs(session.recent_captures[-1].result, replacement)
        self.assertEqual(ingest.call_count, 2)

    async def test_duplicate_capture_correction_removes_bad_original_and_can_undo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                FakeClaw(),
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            original = FakeMessage("/capture I felt Damage.")
            duplicate = FakeMessage("/capture I felt Dimage.")

            await bot.handle_message(FakeUpdate(12345, original), None)
            await bot.handle_message(FakeUpdate(12345, duplicate), None)
            correction = FakeMessage(
                "fix: Dimage, not Damage",
                reply_to_message=original.sent_messages[-1],
            )
            await bot.handle_message(FakeUpdate(12345, correction), None)

            journal_path = next((n4os_root / "journal").glob("*.md"))
            journal_text = journal_path.read_text(encoding="utf-8")
            undo = FakeMessage("undo")
            await bot.handle_message(FakeUpdate(12345, undo), None)
            restored_text = journal_path.read_text(encoding="utf-8")

        self.assertEqual(
            correction.replies,
            [
                "Updated captured note. The corrected version was already saved elsewhere, so I removed the older duplicate."
            ],
        )
        self.assertNotIn("I felt Damage.", journal_text)
        self.assertIn("I felt Dimage.", journal_text)
        self.assertEqual(
            undo.replies,
            ["Undid capture correction: restored the previous captured note."],
        )
        self.assertIn("I felt Damage.", restored_text)
        self.assertIn("I felt Dimage.", restored_text)

    def test_duplicate_capture_correction_undo_keeps_entry_when_restore_fails(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        session = bot.sessions.get("telegram:12345")
        entry = TelegramUndoEntry(
            kind="capture_correction_duplicate",
            capture_restore_text="/capture I felt Damage.",
            capture_restore_editable_text="/capture I felt Damage.",
            capture_restore_source="Telegram",
            capture_restore_default_date=date(2026, 8, 17),
        )
        session.undo_stack.append(entry)

        with patch("telegram_bot.ingest_capture_notes", side_effect=RuntimeError("boom")):
            reply = bot.undo_last_action(
                undo_stack=session.undo_stack,
                recent_captures=session.recent_captures,
            )

        self.assertEqual(
            reply,
            "I could not restore the previous capture, so I left the undo available.",
        )
        self.assertEqual(session.undo_stack, [entry])

    async def test_capture_correction_aborts_when_original_is_partially_removed(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        original = CaptureIngestResult(
            family=MemoryIngestResult(
                added=[
                    MemoryObservation(
                        observed_on=date(2026, 8, 17),
                        person="Nysha",
                        observation="liked Damage puzzles",
                        source="Telegram",
                    )
                ],
                skipped_duplicates=[],
            ),
            journal_entries=[
                JournalEntry(
                    captured_on=date(2026, 8, 17),
                    text="I felt proud.",
                    topics=[],
                    source="Telegram",
                )
            ],
            skipped_journal_duplicates=[],
            notes=[
                CaptureNote(
                    captured_on=date(2026, 8, 17),
                    text="Nysha liked Damage puzzles.",
                    source="Telegram",
                ),
                CaptureNote(
                    captured_on=date(2026, 8, 17),
                    text="I felt proud.",
                    source="Telegram",
                ),
            ],
        )
        session = bot.sessions.get("telegram:12345")
        session.undo_stack.append(TelegramUndoEntry(kind="capture", capture_result=original))
        session.recent_captures.append(
            TelegramRecentCapture(
                text="/capture Nysha liked Damage puzzles.\nI felt proud.",
                source="Telegram",
                result=original,
            )
        )
        message = FakeMessage("fix last capture: Dimage, not Damage")

        with patch("telegram_bot.undo_capture_ingest") as undo, patch("telegram_bot.ingest_capture_notes") as ingest:
            await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(
            message.replies,
            [
                "I can only fix a single saved capture note right now. For batch captures, send undo and resend the corrected capture."
            ],
        )
        undo.assert_not_called()
        ingest.assert_not_called()

    async def test_capture_correction_partial_rollback_preserves_full_undo_result(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        original = CaptureIngestResult(
            family=MemoryIngestResult(
                added=[
                    MemoryObservation(
                        observed_on=date(2026, 8, 17),
                        person="Nysha",
                        observation="liked Damage puzzles",
                        source="Telegram",
                    )
                ],
                skipped_duplicates=[],
            ),
            journal_entries=[
                JournalEntry(
                    captured_on=date(2026, 8, 17),
                    text="Nysha liked Damage puzzles. I felt proud.",
                    topics=[],
                    source="Telegram",
                )
            ],
            skipped_journal_duplicates=[],
            notes=[
                CaptureNote(
                    captured_on=date(2026, 8, 17),
                    text="Nysha liked Damage puzzles. I felt proud.",
                    source="Telegram",
                )
            ],
        )
        replayed_subset = CaptureIngestResult(
            family=MemoryIngestResult(
                added=[],
                skipped_duplicates=[
                    MemoryObservation(
                        observed_on=date(2026, 8, 17),
                        person="Nysha",
                        observation="liked Damage puzzles",
                        source="Telegram",
                    )
                ],
            ),
            journal_entries=[
                JournalEntry(
                    captured_on=date(2026, 8, 17),
                    text="Nysha liked Damage puzzles. I felt proud.",
                    topics=[],
                    source="Telegram",
                )
            ],
            skipped_journal_duplicates=[],
            notes=[
                CaptureNote(
                    captured_on=date(2026, 8, 17),
                    text="Nysha liked Damage puzzles. I felt proud.",
                    source="Telegram",
                )
            ],
        )
        session = bot.sessions.get("telegram:12345")
        undo_entry = TelegramUndoEntry(kind="capture", capture_result=original)
        session.undo_stack.append(undo_entry)
        session.recent_captures.append(
            TelegramRecentCapture(
                text="/capture Nysha liked Damage puzzles. I felt proud.",
                source="Telegram",
                result=original,
            )
        )
        message = FakeMessage("fix last capture: Dimage, not Damage")

        with (
            patch(
                "telegram_bot.undo_capture_ingest",
                return_value=CaptureUndoResult(
                    family_observations_removed=1,
                    journal_entries_removed=0,
                ),
            ),
            patch("telegram_bot.ingest_capture_notes", return_value=replayed_subset),
        ):
            await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertIs(undo_entry.capture_result, original)
        self.assertIs(session.recent_captures[-1].result, original)
        self.assertEqual(
            message.replies,
            ["I could not safely remove the full original capture, so I left it unchanged."],
        )

    async def test_duplicate_only_capture_is_not_registered_as_last_capture(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            ingest_capture_notes(
                "/capture I felt Dimage.",
                n4os_root=n4os_root,
                source="Telegram",
            )
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                FakeClaw(),
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            duplicate = FakeMessage("/capture I felt Dimage.")
            correction = FakeMessage("fix last capture: Damage, not Dimage")

            await bot.handle_message(FakeUpdate(12345, duplicate), None)
            await bot.handle_message(FakeUpdate(12345, correction), None)

        self.assertEqual(correction.replies, ["No recent capture found to fix."])

    async def test_note_quick_from_telegram_appends_markdown_quick_note(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            n4os_root = Path(temp_dir) / "n4os"
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                FakeClaw(),
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            message = FakeMessage("/note quick Patrick Collison: learning still matters")

            with patch("telegram_bot.ingest_capture_notes") as old_capture:
                await bot.handle_message(FakeUpdate(12345, message), None)

            old_capture.assert_not_called()
            quick_notes = n4os_root / "learnings" / "Quick Notes.md"
            saved = quick_notes.read_text(encoding="utf-8")
            self.assertIn("### Patrick Collison", saved)
            self.assertIn("learning still matters", saved)
            self.assertIn("Source: telegram_text", saved)
            self.assertEqual(
                message.replies,
                ["Captured quick note: Patrick Collison -> n4os/learnings/Quick Notes.md"],
            )

    async def test_undo_after_router_mutation_uses_live_router_undo_stack(self):
        claw = UndoableRouterClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        add_message = FakeMessage("today at home passports")
        undo_message = FakeMessage("undo")

        await bot.handle_message(FakeUpdate(12345, add_message), None)
        await bot.handle_message(FakeUpdate(12345, undo_message), None)

        self.assertEqual(claw.requests, ["today at home passports", "undo"])
        self.assertEqual(
            add_message.replies,
            ["Added to Today at Home: Family: passports (airport)"],
        )
        self.assertEqual(
            undo_message.replies,
            ["Undid Home Board add: removed 1 item(s)."],
        )

    async def test_empty_undo_is_terminal(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("undo")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies, ["Nothing to undo."])
        self.assertEqual(claw.requests, [])

    async def test_conversations_have_isolated_router_state(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            N4OSClaw(),
            logger=QuietLogger(),
        )

        first = bot.sessions.get("telegram:12345")
        first.claw.pending_route_clarification = object()
        first.undo_stack.append(type("Undo", (), {"kind": "router"})())
        second = bot.sessions.get("telegram:67890")

        self.assertIsNot(first.claw, second.claw)
        self.assertIsNone(second.claw.pending_route_clarification)
        self.assertEqual(second.undo_stack, [])

    async def test_session_fallback_preserves_dependencies_and_isolates_state(self):
        library = NonDeepcopyableLibraryDomainClaw()
        library.last_item = {"id": "prior-session-item"}
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            N4OSClaw(library_claw=library),
            logger=QuietLogger(),
        )

        first = bot.sessions.get("telegram:12345")
        first.claw.library_claw.calls.append(("record", "first"))
        second = bot.sessions.get("telegram:67890")

        self.assertIsNot(first.claw, second.claw)
        self.assertIsNot(first.claw.library_claw, second.claw.library_claw)
        self.assertEqual(second.claw.library_claw.calls, [])
        self.assertIsNone(second.claw.library_claw.last_item)

    async def test_chat_reset_clears_conversation_router_state(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            N4OSClaw(),
            logger=QuietLogger(),
        )
        before = bot.sessions.get("telegram:12345")
        before.claw.handle_request("hmm maybe later")
        before.undo_stack.append(TelegramUndoEntry(kind="router"))
        message = FakeMessage("/chat reset")

        await bot.handle_message(FakeUpdate(12345, message), None)

        after = bot.sessions.get("telegram:12345")
        self.assertIsNot(after.claw, before.claw)
        self.assertIsNone(after.claw.pending_route_clarification)
        self.assertEqual(after.undo_stack, [])
        self.assertEqual(message.replies, ["Started a new N4OS session."])

    async def test_new_session_command_resets_only_current_conversation(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            N4OSClaw(),
            logger=QuietLogger(),
        )
        current = bot.sessions.get("telegram:12345")
        current.claw.handle_request("hmm maybe later")
        current.undo_stack.append(TelegramUndoEntry(kind="router"))
        other = bot.sessions.get("telegram:67890")
        other.claw.handle_request("hmm maybe later")
        other.undo_stack.append(TelegramUndoEntry(kind="router"))
        message = FakeMessage("/new")

        await bot.handle_message(FakeUpdate(12345, message), None)

        reset = bot.sessions.get("telegram:12345")
        unchanged = bot.sessions.get("telegram:67890")
        self.assertIsNot(reset.claw, current.claw)
        self.assertIsNone(reset.claw.pending_route_clarification)
        self.assertEqual(reset.undo_stack, [])
        self.assertIs(unchanged.claw, other.claw)
        self.assertIsNotNone(unchanged.claw.pending_route_clarification)
        self.assertEqual(len(unchanged.undo_stack), 1)
        self.assertEqual(message.replies, ["Started a new N4OS session."])

    async def test_reset_command_starts_new_session(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            N4OSClaw(),
            logger=QuietLogger(),
        )
        before = bot.sessions.get("telegram:12345")
        before.claw.handle_request("hmm maybe later")
        before.undo_stack.append(TelegramUndoEntry(kind="router"))
        message = FakeMessage("/reset")

        await bot.handle_message(FakeUpdate(12345, message), None)

        after = bot.sessions.get("telegram:12345")
        self.assertIsNot(after.claw, before.claw)
        self.assertIsNone(after.claw.pending_route_clarification)
        self.assertEqual(after.undo_stack, [])
        self.assertEqual(message.replies, ["Started a new N4OS session."])

    async def test_group_conversation_key_includes_sender(self):
        first = FakeUpdate(12345, FakeMessage("one"), chat_id=-100)
        second = FakeUpdate(67890, FakeMessage("two"), chat_id=-100)

        self.assertEqual(_conversation_key(first, 12345), "telegram:-100:12345")
        self.assertEqual(_conversation_key(second, 67890), "telegram:-100:67890")

    async def test_authorized_memory_status_reports_without_routing_or_capture(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("/memory-status family")

        with (
            patch("telegram_bot.format_memory_status", return_value="family memory summary") as status,
            patch("telegram_bot.ingest_capture_notes") as ingest,
        ):
            await bot.handle_message(FakeUpdate(12345, message), None)

        status.assert_called_once_with("family")
        ingest.assert_not_called()
        self.assertEqual(claw.requests, [])
        self.assertEqual(message.replies, ["family memory summary"])

    async def test_authorized_remember_records_structured_memory_without_routing_or_capture(self):
        today = date.today()
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            claw = FakeClaw()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            message = FakeMessage("/remember Niyati picked up dinner tonight")

            with patch("telegram_bot.ingest_capture_notes") as ingest:
                await bot.handle_message(FakeUpdate(12345, message), None)

        ingest.assert_not_called()
        self.assertEqual(claw.requests, [])
        self.assertEqual(
            message.replies,
            [f"Remembered. Dinner pickup: Niyati on {today.isoformat()}."],
        )

    async def test_authorized_dinner_pickup_query_reads_structured_memory(self):
        today = date.today()
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            claw = FakeClaw()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await bot.handle_message(
                FakeUpdate(12345, FakeMessage("/remember Niyati picked up dinner tonight")),
                None,
            )
            await bot.handle_message(
                FakeUpdate(12345, FakeMessage("/remember Nimesh picked up dinner yesterday")),
                None,
            )
            query = FakeMessage("who picked the last 2 dinners?")

            await bot.handle_message(FakeUpdate(12345, query), None)

        self.assertEqual(claw.requests, [])
        self.assertEqual(
            query.replies,
            [
                "Last 2 dinner pickups:\n"
                f"1. {today.isoformat()}: Niyati\n"
                f"2. {(today - date.resolution).isoformat()}: Nimesh"
            ],
        )

    async def test_authorized_generic_memory_query_reads_matching_structured_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            claw = FakeClaw()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await bot.handle_message(
                FakeUpdate(12345, FakeMessage("/remember learning code 0816")),
                None,
            )
            query = FakeMessage("What is the learning code?")

            await bot.handle_message(FakeUpdate(12345, query), None)

        self.assertEqual(claw.requests, [])
        assert_single_memory_reply(
            self,
            query.replies,
            "Remembered note: learning code 0816",
            "Source: Telegram.",
        )

    async def test_authorized_recent_remember_query_lists_structured_memories_without_saving(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            claw = FakeClaw()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await bot.handle_message(
                FakeUpdate(12345, FakeMessage("/remember learning code 0816")),
                None,
            )
            query = FakeMessage("/remember last 5 days")

            with patch("telegram_bot.ingest_capture_notes") as ingest:
                await bot.handle_message(FakeUpdate(12345, query), None)

        ingest.assert_not_called()
        self.assertEqual(claw.requests, [])
        self.assertEqual(len(query.replies), 1)
        self.assertIn("Remembered in the last 5 days:", query.replies[0])
        self.assertIn("learning code 0816", query.replies[0])

    async def test_undo_after_structured_remember_deletes_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            claw = FakeClaw()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            remember = FakeMessage("/remember learning code 0816")
            undo = FakeMessage("Undo")
            query = FakeMessage("What is the learning code?")

            await bot.handle_message(FakeUpdate(12345, remember), None)
            await bot.handle_message(FakeUpdate(12345, undo), None)
            await bot.handle_message(FakeUpdate(12345, query), None)

        self.assertEqual(claw.requests, ["What is the learning code?"])
        self.assertEqual(remember.replies, ["Remembered. Structured note saved."])
        self.assertEqual(undo.replies, ["Undid remembered memory: learning code 0816."])
        self.assertEqual(query.replies, ["router replied to: What is the learning code?"])

    async def test_authorized_forget_structured_memory_removes_matching_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            claw = FakeClaw()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await bot.handle_message(
                FakeUpdate(12345, FakeMessage("/remember learning code 0816")),
                None,
            )
            forget = FakeMessage("forget learning code")
            query = FakeMessage("What is the learning code?")

            await bot.handle_message(FakeUpdate(12345, forget), None)
            await bot.handle_message(FakeUpdate(12345, query), None)

        self.assertEqual(claw.requests, ["What is the learning code?"])
        self.assertEqual(forget.replies, ["Forgot structured memory: learning code 0816."])
        self.assertEqual(query.replies, ["router replied to: What is the learning code?"])

    async def test_undo_after_forget_restores_structured_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            claw = FakeClaw()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await bot.handle_message(
                FakeUpdate(12345, FakeMessage("/remember learning code 0816")),
                None,
            )
            forget = FakeMessage("forget learning code")
            undo = FakeMessage("Undo")
            query = FakeMessage("What is the learning code?")

            await bot.handle_message(FakeUpdate(12345, forget), None)
            await bot.handle_message(FakeUpdate(12345, undo), None)
            await bot.handle_message(FakeUpdate(12345, query), None)

        self.assertEqual(claw.requests, [])
        self.assertEqual(undo.replies, ["Restored structured memory: learning code 0816."])
        assert_single_memory_reply(
            self,
            query.replies,
            "Remembered note: learning code 0816",
            "Source: Telegram.",
        )

    async def test_undo_after_forget_does_not_restore_over_recreated_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            first_claw = FakeClaw()
            second_claw = FakeClaw()
            first_bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                first_claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            second_bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=67890),
                second_claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await first_bot.handle_message(
                FakeUpdate(12345, FakeMessage("/remember learning code 0816")),
                None,
            )
            await first_bot.handle_message(
                FakeUpdate(12345, FakeMessage("forget learning code")),
                None,
            )
            await second_bot.handle_message(
                FakeUpdate(67890, FakeMessage("/remember learning code 2222")),
                None,
            )
            undo = FakeMessage("Undo")
            query = FakeMessage("What is the learning code?")

            await first_bot.handle_message(FakeUpdate(12345, undo), None)
            await first_bot.handle_message(FakeUpdate(12345, query), None)

        self.assertEqual(
            undo.replies,
            ["That structured memory changed after this action, so I did not undo it."],
        )
        assert_single_memory_reply(
            self,
            query.replies,
            "Remembered note: learning code 2222",
            "Source: Telegram.",
        )

    async def test_refused_structured_memory_undo_keeps_undo_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            first_bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                FakeClaw(),
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            second_bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=67890),
                FakeClaw(),
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await first_bot.handle_message(
                FakeUpdate(12345, FakeMessage("/remember learning code 0816")),
                None,
            )
            await first_bot.handle_message(
                FakeUpdate(12345, FakeMessage("forget learning code")),
                None,
            )
            await second_bot.handle_message(
                FakeUpdate(67890, FakeMessage("/remember learning code 2222")),
                None,
            )
            first_undo = FakeMessage("Undo")
            second_undo = FakeMessage("Undo")

            await first_bot.handle_message(FakeUpdate(12345, first_undo), None)
            await first_bot.handle_message(FakeUpdate(12345, second_undo), None)

        self.assertEqual(
            first_undo.replies,
            ["That structured memory changed after this action, so I did not undo it."],
        )
        self.assertEqual(
            second_undo.replies,
            ["That structured memory changed after this action, so I did not undo it."],
        )

    async def test_undo_after_forget_restores_when_new_memory_has_extra_qualifier(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            first_bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                FakeClaw(),
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            second_bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=67890),
                FakeClaw(),
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await first_bot.handle_message(
                FakeUpdate(12345, FakeMessage("/remember learning code 0816")),
                None,
            )
            await first_bot.handle_message(
                FakeUpdate(12345, FakeMessage("forget learning code")),
                None,
            )
            await second_bot.handle_message(
                FakeUpdate(67890, FakeMessage("/remember math learning code 2222")),
                None,
            )
            undo = FakeMessage("Undo")
            query = FakeMessage("What is the learning code?")

            await first_bot.handle_message(FakeUpdate(12345, undo), None)
            await first_bot.handle_message(FakeUpdate(12345, query), None)

        self.assertEqual(undo.replies, ["Restored structured memory: learning code 0816."])
        self.assertEqual(
            query.replies,
            [
                "I found multiple matching structured memories. Ask with more detail:\n"
                "1. math learning code 2222\n"
                "2. learning code 0816"
            ],
        )

    async def test_undo_after_forget_restores_when_new_memory_has_trailing_extra_qualifier(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            first_bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                FakeClaw(),
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            second_bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=67890),
                FakeClaw(),
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await first_bot.handle_message(
                FakeUpdate(12345, FakeMessage("/remember learning code 0816")),
                None,
            )
            await first_bot.handle_message(
                FakeUpdate(12345, FakeMessage("forget learning code")),
                None,
            )
            await second_bot.handle_message(
                FakeUpdate(67890, FakeMessage("/remember learning code for math 2222")),
                None,
            )
            undo = FakeMessage("Undo")
            query = FakeMessage("What is the learning code?")

            await first_bot.handle_message(FakeUpdate(12345, undo), None)
            await first_bot.handle_message(FakeUpdate(12345, query), None)

        self.assertEqual(undo.replies, ["Restored structured memory: learning code 0816."])
        self.assertEqual(
            query.replies,
            [
                "I found multiple matching structured memories. Ask with more detail:\n"
                "1. learning code for math 2222\n"
                "2. learning code 0816"
            ],
        )

    async def test_structured_memory_undo_wins_over_unrelated_pending_domain(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            claw = FakeClaw()
            claw.tasks_claw = type("PendingTasks", (), {"pending_action": object()})()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await bot.handle_message(
                FakeUpdate(12345, FakeMessage("/remember learning code 0816")),
                None,
            )
            undo = FakeMessage("Undo")
            query = FakeMessage("What is the learning code?")

            await bot.handle_message(FakeUpdate(12345, undo), None)
            await bot.handle_message(FakeUpdate(12345, query), None)

        self.assertEqual(undo.replies, ["Undid remembered memory: learning code 0816."])
        self.assertEqual(claw.requests, ["What is the learning code?"])
        self.assertEqual(query.replies, ["router replied to: What is the learning code?"])

    async def test_undo_after_forget_does_not_restore_over_recreated_freeform_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            first_claw = FakeClaw()
            second_claw = FakeClaw()
            first_bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                first_claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            second_bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=67890),
                second_claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await first_bot.handle_message(
                FakeUpdate(12345, FakeMessage("/remember Navya is allergic to cashews")),
                None,
            )
            await first_bot.handle_message(
                FakeUpdate(12345, FakeMessage("forget Navya allergy")),
                None,
            )
            await second_bot.handle_message(
                FakeUpdate(67890, FakeMessage("/remember Navya is allergic to peanuts")),
                None,
            )
            undo = FakeMessage("Undo")
            query = FakeMessage("What do you remember about Navya allergy?")

            await first_bot.handle_message(FakeUpdate(12345, undo), None)
            await first_bot.handle_message(FakeUpdate(12345, query), None)

        self.assertEqual(
            undo.replies,
            ["That structured memory changed after this action, so I did not undo it."],
        )
        assert_single_memory_reply(
            self,
            query.replies,
            "Remembered note: Navya is allergic to peanuts",
            "Source: Telegram.",
        )

    async def test_authorized_update_structured_memory_replaces_matching_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            claw = FakeClaw()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await bot.handle_message(
                FakeUpdate(12345, FakeMessage("/remember learning code 0816")),
                None,
            )
            update = FakeMessage("update learning code to 9911")
            query = FakeMessage("What is the learning code?")

            await bot.handle_message(FakeUpdate(12345, update), None)
            await bot.handle_message(FakeUpdate(12345, query), None)

        self.assertEqual(claw.requests, [])
        self.assertEqual(update.replies, ["Updated structured memory: learning code 9911."])
        assert_single_memory_reply(
            self,
            query.replies,
            "Remembered note: learning code 9911",
            "Source: Telegram.",
            updated=True,
        )

    async def test_undo_after_update_restores_previous_structured_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            claw = FakeClaw()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await bot.handle_message(
                FakeUpdate(12345, FakeMessage("/remember learning code 0816")),
                None,
            )
            update = FakeMessage("update learning code to 9911")
            undo = FakeMessage("Undo")
            query = FakeMessage("What is the learning code?")

            await bot.handle_message(FakeUpdate(12345, update), None)
            await bot.handle_message(FakeUpdate(12345, undo), None)
            await bot.handle_message(FakeUpdate(12345, query), None)

        self.assertEqual(claw.requests, [])
        self.assertEqual(undo.replies, ["Restored structured memory: learning code 0816."])
        assert_single_memory_reply(
            self,
            query.replies,
            "Remembered note: learning code 0816",
            "Source: Telegram.",
        )

    async def test_undo_after_update_does_not_overwrite_newer_memory_change(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            first_claw = FakeClaw()
            second_claw = FakeClaw()
            first_bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                first_claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            second_bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=67890),
                second_claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await first_bot.handle_message(
                FakeUpdate(12345, FakeMessage("/remember learning code 0816")),
                None,
            )
            await first_bot.handle_message(
                FakeUpdate(12345, FakeMessage("update learning code to 9911")),
                None,
            )
            await second_bot.handle_message(
                FakeUpdate(67890, FakeMessage("update learning code to 2222")),
                None,
            )
            undo = FakeMessage("Undo")
            query = FakeMessage("What is the learning code?")

            await first_bot.handle_message(FakeUpdate(12345, undo), None)
            await first_bot.handle_message(FakeUpdate(12345, query), None)

        self.assertEqual(
            undo.replies,
            ["That structured memory changed after this action, so I did not undo it."],
        )
        assert_single_memory_reply(
            self,
            query.replies,
            "Remembered note: learning code 2222",
            "Source: Telegram.",
            updated=True,
        )

    async def test_undo_after_update_does_not_restore_over_new_conflicting_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            first_claw = FakeClaw()
            second_claw = FakeClaw()
            first_bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                first_claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            second_bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=67890),
                second_claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await first_bot.handle_message(
                FakeUpdate(12345, FakeMessage("/remember learning code 0816")),
                None,
            )
            await first_bot.handle_message(
                FakeUpdate(12345, FakeMessage("update learning code to 9911")),
                None,
            )
            await second_bot.handle_message(
                FakeUpdate(67890, FakeMessage("/remember learning code 2222")),
                None,
            )
            undo = FakeMessage("Undo")

            await first_bot.handle_message(FakeUpdate(12345, undo), None)

        self.assertEqual(
            undo.replies,
            ["That structured memory changed after this action, so I did not undo it."],
        )

    async def test_active_chat_followup_skips_structured_memory_probe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            claw = FakeClaw()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await bot.handle_message(
                FakeUpdate(12345, FakeMessage("/remember learning code 0816")),
                None,
            )
            bot.chat_sessions.append(
                "telegram:12345",
                user_text="topic",
                assistant_text="answer",
            )
            followup = FakeMessage("What is the learning code?")

            with patch(
                "telegram_bot.format_n4os_chat",
                return_value=N4OSChatResult("chat answer", ["SOUL"], "gpt-5.4-mini"),
            ) as chat, patch("telegram_bot.record_n4os_trajectory"):
                await bot.handle_message(FakeUpdate(12345, followup), None)

        chat.assert_called_once()
        self.assertEqual(claw.requests, [])
        self.assertEqual(followup.replies[-1], "chat answer")

    async def test_active_chat_allows_explicit_structured_memory_lookup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            claw = FakeClaw()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await bot.handle_message(
                FakeUpdate(12345, FakeMessage("/remember learning code 0816")),
                None,
            )
            bot.chat_sessions.append(
                "telegram:12345",
                user_text="topic",
                assistant_text="answer",
            )
            lookup = FakeMessage("find memory learning code")

            with patch("telegram_bot.format_n4os_chat") as chat:
                await bot.handle_message(FakeUpdate(12345, lookup), None)

        chat.assert_not_called()
        self.assertEqual(claw.requests, [])
        assert_single_memory_reply(
            self,
            lookup.replies,
            "Remembered note: learning code 0816",
            "Source: Telegram.",
        )

    async def test_explicit_structured_memory_lookup_miss_does_not_fall_through_to_router(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        lookup = FakeMessage("find remembered memory passport")

        await bot.handle_message(FakeUpdate(12345, lookup), None)

        self.assertEqual(claw.requests, [])
        self.assertEqual(
            lookup.replies,
            ["I do not have a structured memory matching that yet. Use /remember to save it."],
        )

    async def test_plain_structured_memory_lookup_keeps_specificity_guard(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            claw = FakeClaw()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await bot.handle_message(
                FakeUpdate(12345, FakeMessage("/remember Navya passport appointment confirmation is PX92")),
                None,
            )
            lookup = FakeMessage("show me memory passport")

            await bot.handle_message(FakeUpdate(12345, lookup), None)

        self.assertEqual(claw.requests, ["show me memory passport"])
        self.assertEqual(lookup.replies, ["router replied to: show me memory passport"])

    async def test_broad_memory_like_phrases_fall_through_to_router(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        usage = FakeMessage("show memory usage")
        notes_app = FakeMessage("find notes app")

        await bot.handle_message(FakeUpdate(12345, usage), None)
        await bot.handle_message(FakeUpdate(12345, notes_app), None)

        self.assertEqual(claw.requests, ["show memory usage", "find notes app"])
        self.assertEqual(usage.replies, ["router replied to: show memory usage"])
        self.assertEqual(notes_app.replies, ["router replied to: find notes app"])

    async def test_active_chat_explicit_structured_memory_lookup_miss_does_not_continue_chat(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        bot.chat_sessions.append(
            "telegram:12345",
            user_text="topic",
            assistant_text="answer",
        )
        lookup = FakeMessage("find remembered memory passport")

        with patch("telegram_bot.format_n4os_chat") as chat:
            await bot.handle_message(FakeUpdate(12345, lookup), None)

        chat.assert_not_called()
        self.assertEqual(claw.requests, [])
        self.assertEqual(
            lookup.replies,
            ["I do not have a structured memory matching that yet. Use /remember to save it."],
        )

    async def test_active_chat_allows_matching_remember_about_probe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            claw = FakeClaw()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await bot.handle_message(
                FakeUpdate(12345, FakeMessage("/remember Navya is allergic to cashews")),
                None,
            )
            bot.chat_sessions.append(
                "telegram:12345",
                user_text="topic",
                assistant_text="answer",
            )
            lookup = FakeMessage("What do you remember about Navya allergy?")

            with patch("telegram_bot.format_n4os_chat") as chat:
                await bot.handle_message(FakeUpdate(12345, lookup), None)

        chat.assert_not_called()
        self.assertEqual(claw.requests, [])
        assert_single_memory_reply(
            self,
            lookup.replies,
            "Remembered note: Navya is allergic to cashews",
            "Source: Telegram.",
        )

    async def test_active_chat_allows_matching_natural_structured_memory_question(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            claw = FakeClaw()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await bot.handle_message(
                FakeUpdate(
                    12345,
                    FakeMessage("/remember learning bee teacher name Ms. Kelly and Ms. Peggy."),
                ),
                None,
            )
            bot.chat_sessions.append(
                "telegram:12345",
                user_text="topic",
                assistant_text="answer",
            )
            lookup = FakeMessage("What it learning bee teacher name")

            with patch("telegram_bot.format_n4os_chat") as chat:
                await bot.handle_message(FakeUpdate(12345, lookup), None)

        chat.assert_not_called()
        self.assertEqual(claw.requests, [])
        assert_single_memory_reply(
            self,
            lookup.replies,
            "Remembered note: learning bee teacher name Ms. Kelly and Ms. Peggy",
            "Source: Telegram.",
        )

    async def test_active_chat_memory_lookup_matches_learn_learning_variant(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            claw = FakeClaw()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await bot.handle_message(
                FakeUpdate(
                    12345,
                    FakeMessage("/remember learning bee teacher name Ms. Kelly and Ms. Peggy."),
                ),
                None,
            )
            bot.chat_sessions.append(
                "telegram:12345",
                user_text="topic",
                assistant_text="answer",
            )
            lookup = FakeMessage("Find memory learn bee teacher name")

            with patch("telegram_bot.format_n4os_chat") as chat:
                await bot.handle_message(FakeUpdate(12345, lookup), None)

        chat.assert_not_called()
        self.assertEqual(claw.requests, [])
        assert_single_memory_reply(
            self,
            lookup.replies,
            "Remembered note: learning bee teacher name Ms. Kelly and Ms. Peggy",
            "Source: Telegram.",
        )

    async def test_single_subject_find_memory_reads_structured_memory_before_router(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            claw = FakeClaw()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await bot.handle_message(
                FakeUpdate(12345, FakeMessage("/remember Sriram suggested taking the earlier flight.")),
                None,
            )
            lookup = FakeMessage("Find memory sriram")

            await bot.handle_message(FakeUpdate(12345, lookup), None)

        self.assertEqual(claw.requests, [])
        assert_single_memory_reply(
            self,
            lookup.replies,
            "Remembered note: Sriram suggested taking the earlier flight",
            "Source: Telegram.",
        )

    async def test_single_subject_find_memory_miss_does_not_fall_through_to_router(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            claw = FakeClaw()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            lookup = FakeMessage("Find memory sriram")

            await bot.handle_message(FakeUpdate(12345, lookup), None)

        self.assertEqual(claw.requests, [])
        self.assertEqual(
            lookup.replies,
            ["I do not have a structured memory matching that yet. Use /remember to save it."],
        )

    async def test_natural_past_person_question_reads_matching_memory_before_router(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            claw = FakeClaw()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )

            await bot.handle_message(
                FakeUpdate(12345, FakeMessage("/remember Sriram suggested taking the earlier flight.")),
                None,
            )
            lookup = FakeMessage("What did Sriram suggest?")

            await bot.handle_message(FakeUpdate(12345, lookup), None)

        self.assertEqual(claw.requests, [])
        assert_single_memory_reply(
            self,
            lookup.replies,
            "Remembered note: Sriram suggested taking the earlier flight",
            "Source: Telegram.",
        )

    async def test_authorized_generic_memory_probe_errors_fall_through_to_router(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("What is the learning code?")

        with patch("telegram_bot.has_structured_memory_query_match", side_effect=OSError("db")):
            await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(claw.requests, ["What is the learning code?"])
        self.assertEqual(message.replies, ["router replied to: What is the learning code?"])

    async def test_authorized_status_target_reports_without_routing(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("/status Nysha")

        with patch("telegram_bot.format_n4os_status", return_value="nysha status") as status:
            await bot.handle_message(FakeUpdate(12345, message), None)

        status.assert_called_once_with("nysha")
        self.assertEqual(claw.requests, [])
        self.assertEqual(message.replies, ["nysha status"])

    async def test_authorized_goals_question_reports_from_n4os_memory(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("what are my current goals?")

        with patch("telegram_bot.format_goals_status", return_value="current goals summary") as goals:
            await bot.handle_message(FakeUpdate(12345, message), None)

        goals.assert_called_once_with()
        self.assertEqual(claw.requests, [])
        self.assertEqual(message.replies, ["current goals summary"])

    async def test_authorized_status_alias_routes_to_reading_status(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("/status")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(claw.requests, ["reading status"])
        self.assertEqual(message.replies, ["router replied to: reading status"])

    async def test_authorized_review_week_reports_without_routing(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("/review week")

        with patch("telegram_bot.format_n4os_review", return_value="weekly review") as review:
            await bot.handle_message(FakeUpdate(12345, message), None)

        review.assert_called_once_with("week")
        self.assertEqual(claw.requests, [])
        self.assertEqual(message.replies, ["weekly review"])

    async def test_authorized_n4os_advice_question_uses_memory_advice(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("/ask How should we approach Nysha's reading?")

        with patch("telegram_bot.generate_n4os_advice", return_value=advice_result("memory-backed advice")) as advice, patch(
            "telegram_bot.record_n4os_trajectory"
        ) as record:
            await bot.handle_message(FakeUpdate(12345, message), None)

        advice.assert_called_once()
        self.assertEqual(advice.call_args.args, ("/ask How should we approach Nysha's reading?",))
        self.assertIn("context", advice.call_args.kwargs)
        record.assert_called_once()
        self.assertEqual(claw.requests, [])
        self.assertEqual(message.replies[-1], "memory-backed advice")
        self.assertTrue(message.replies[0].startswith("Knowledge selected"))
        self.assertTrue(message.replies[1].startswith("N4OS decision path"))

    async def test_ask_question_with_help_word_still_uses_advice(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("/ask how should I help Nysha with school transition?")

        with patch("telegram_bot.generate_n4os_advice", return_value=advice_result("school advice")) as advice, patch(
            "telegram_bot.record_n4os_trajectory"
        ) as record:
            await bot.handle_message(FakeUpdate(12345, message), None)

        advice.assert_called_once()
        record.assert_called_once()
        self.assertEqual(claw.requests, [])
        self.assertEqual(message.replies[-1], "school advice")

    async def test_morning_checkin_uses_n4os_advice_before_router(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("Run morning check-in.")

        with patch("telegram_bot.generate_n4os_advice", return_value=advice_result("morning prompt")) as advice, patch(
            "telegram_bot.record_n4os_trajectory"
        ) as record:
            await bot.handle_message(FakeUpdate(12345, message), None)

        advice.assert_called_once()
        record.assert_called_once()
        self.assertEqual(claw.requests, [])
        self.assertEqual(message.replies[-1], "morning prompt")

    async def test_help_me_plan_morning_uses_advice_not_command_help(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("Help me plan tomorrow morning.")

        with patch("telegram_bot.generate_n4os_advice", return_value=advice_result("tomorrow plan")) as advice, patch(
            "telegram_bot.record_n4os_trajectory"
        ) as record:
            await bot.handle_message(FakeUpdate(12345, message), None)

        advice.assert_called_once()
        record.assert_called_once()
        self.assertEqual(claw.requests, [])
        self.assertEqual(message.replies[-1], "tomorrow plan")

    async def test_ask_question_stores_trajectory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir(parents=True)
            (n4os_root / "SOUL.md").write_text("Be warm.\n", encoding="utf-8")
            claw = FakeClaw()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            message = FakeMessage("/ask How should I approach Nysha school?")

            with patch("telegram_bot.generate_n4os_advice", return_value=advice_result("school advice")):
                await bot.handle_message(FakeUpdate(12345, message), None)

            trajectory_path = next((n4os_root / "trajectories").glob("*.md"))
            trajectory = trajectory_path.read_text(encoding="utf-8")

        self.assertIn("- Mode: ask", trajectory)
        self.assertIn("How should I approach Nysha school?", trajectory)
        self.assertIn("school advice", trajectory)

    async def test_chat_command_routes_to_rich_chat_and_stores_trajectory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir(parents=True)
            (n4os_root / "SOUL.md").write_text("Be warm.\n", encoding="utf-8")
            claw = FakeClaw()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            message = FakeMessage("/chat How do I approach Nysha's first week at school?")

            with patch(
                "telegram_bot.format_n4os_chat",
                return_value=N4OSChatResult(
                    reply="rich school conversation",
                    context_labels=["SOUL", "Nysha"],
                    model="gpt-5.4-mini",
                ),
            ) as chat:
                await bot.handle_message(FakeUpdate(12345, message), None)

            trajectory_path = next((n4os_root / "trajectories").glob("*.md"))
            trajectory = trajectory_path.read_text(encoding="utf-8")

        chat.assert_called_once()
        self.assertEqual(claw.requests, [])
        self.assertEqual(message.replies[-1], "rich school conversation")
        self.assertTrue(message.replies[0].startswith("Knowledge selected"))
        self.assertTrue(message.replies[1].startswith("Model reasoning summary"))
        self.assertIn("- Mode: chat", trajectory)
        self.assertIn("rich school conversation", trajectory)

    async def test_research_command_shows_web_sources_before_answer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir(parents=True)
            (n4os_root / "SOUL.md").write_text("Be warm.\n", encoding="utf-8")
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                FakeClaw(),
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            message = FakeMessage("/research deep current school enrollment guidance")
            result = N4OSResearchResult(
                reply="Call the district first [1].",
                reasoning_summary="Used the current district source and selected N4OS context.",
                context_labels=["SOUL", "Live web search"],
                knowledge_preview="Research setup",
                model="gpt-5.6-sol",
                mode="deep",
                reasoning_effort="high",
                sources=[
                    N4OSResearchSource(
                        title="District enrollment",
                        url="https://district.example/enrollment",
                    )
                ],
            )

            with patch("telegram_bot.generate_n4os_research", return_value=result), patch(
                "telegram_bot.record_n4os_trajectory"
            ) as record:
                await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies[0].splitlines()[0], "Research setup")
        self.assertIn("gpt-5.6-sol", message.replies[0])
        self.assertEqual(message.replies[1].splitlines()[0], "Research evidence")
        self.assertIn("https://district.example/enrollment", message.replies[1])
        self.assertTrue(message.replies[2].startswith("Model reasoning summary"))
        self.assertEqual(message.replies[3], "Call the district first [1].")
        self.assertEqual(record.call_args.kwargs["mode"], "research")

    async def test_research_help_does_not_start_web_research(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        message = FakeMessage("/research help")

        with patch("telegram_bot.generate_n4os_research") as research:
            await bot.handle_message(FakeUpdate(12345, message), None)

        research.assert_not_called()
        self.assertEqual(message.replies, [RESEARCH_HELP_MESSAGE])

    async def test_help_research_uses_dedicated_research_help(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        message = FakeMessage("/help research")

        await bot.handle_help(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies, [RESEARCH_HELP_MESSAGE])

    async def test_chat_followup_continues_active_session(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        first = FakeMessage("/chat first topic")
        second = FakeMessage("what about day two?")

        with patch(
            "telegram_bot.format_n4os_chat",
            side_effect=[
                N4OSChatResult("first answer", ["SOUL"], "gpt-5.4-mini"),
                N4OSChatResult("second answer", ["SOUL"], "gpt-5.4-mini"),
            ],
        ) as chat, patch("telegram_bot.record_n4os_trajectory"):
            await bot.handle_message(FakeUpdate(12345, first), None)
            await bot.handle_message(FakeUpdate(12345, second), None)

        self.assertEqual(chat.call_count, 2)
        self.assertEqual(first.replies[-1], "first answer")
        self.assertEqual(second.replies[-1], "second answer")
        self.assertEqual(claw.requests, [])

    async def test_reply_capture_to_reasoning_saves_linked_tuning_feedback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                FakeClaw(),
                logger=QuietLogger(),
                n4os_root=n4os_root,
            )
            question = FakeMessage("/chat How should we approach the first school week?")
            result = N4OSChatResult(
                reply="Use a calm first-week plan.",
                context_labels=["SOUL", "Nysha School Knowledge"],
                model="gpt-5.4-mini",
                reasoning_summary="Prioritized current school knowledge over general guidance.",
            )

            with patch("telegram_bot.format_n4os_chat", return_value=result), patch(
                "telegram_bot.record_n4os_trajectory"
            ):
                await bot.handle_message(FakeUpdate(12345, question), None)

            feedback = FakeMessage(
                "capture: Room 13 is outdated; prefer School Knowledge.",
                reply_to_message=question.sent_messages[1],
            )
            with patch("telegram_bot.capture_markdown_note") as capture:
                await bot.handle_message(FakeUpdate(12345, feedback), None)

        capture.assert_called_once()
        captured_text = capture.call_args.args[0]
        self.assertIn("Room 13 is outdated", captured_text)
        self.assertIn("How should we approach the first school week?", captured_text)
        self.assertIn("SOUL, Nysha School Knowledge", captured_text)
        self.assertIn("Prioritized current school knowledge", captured_text)
        self.assertEqual(
            feedback.replies,
            ["Captured N4OS tuning feedback with the related answer trace."],
        )

    async def test_active_chat_does_not_capture_mutation_commands(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        bot.chat_sessions.append(
            "telegram:12345",
            user_text="topic",
            assistant_text="answer",
        )
        message = FakeMessage("Add task buy milk")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(claw.requests, ["Add task buy milk"])
        self.assertEqual(message.replies, ["router replied to: Add task buy milk"])

    async def test_high_confidence_natural_action_interrupts_active_chat(self):
        library = FakeLibraryDomainClaw()
        claw = N4OSClaw(library_claw=library)
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        bot.chat_sessions.append(
            "telegram:12345",
            user_text="topic",
            assistant_text="answer",
        )
        message = FakeMessage("Nysha read 8 pages")

        with patch("telegram_bot.format_n4os_chat") as chat:
            await bot.handle_message(FakeUpdate(12345, message), None)

        chat.assert_not_called()
        self.assertEqual(library.calls[0][0], "record")
        self.assertIn("Reading Garden", message.replies[0])

    async def test_active_chat_ignores_stale_router_followup_context(self):
        claw = N4OSClaw()
        claw.route_context.last_route = "decisions"
        claw.route_context.last_action = "create_decision"
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        bot.chat_sessions.append(
            "telegram:12345",
            user_text="topic",
            assistant_text="answer",
        )
        message = FakeMessage("status")

        with (
            patch(
                "telegram_bot.format_n4os_chat",
                return_value=N4OSChatResult("chat status reply", ["SOUL"], "gpt-5.4-mini"),
            ) as chat,
            patch("telegram_bot.record_n4os_trajectory"),
        ):
            await bot.handle_message(FakeUpdate(12345, message), None)

        chat.assert_called_once()
        self.assertEqual(message.replies[-1], "chat status reply")

    async def test_long_chat_reply_is_chunked(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("/chat long topic")
        long_reply = "A" * 3900 + "\n\n" + "B" * 3900

        with patch(
            "telegram_bot.format_n4os_chat",
            return_value=N4OSChatResult(long_reply, ["SOUL"], "gpt-5.4-mini"),
        ), patch("telegram_bot.record_n4os_trajectory"):
            await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertGreaterEqual(len(message.replies), 2)
        self.assertTrue(all(len(reply) <= 3800 for reply in message.replies))

    async def test_authorized_how_to_memory_gets_direct_help(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("how do I add a memory?")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(claw.requests, [])
        self.assertEqual(message.replies, [HOW_TO_HELP["capture"]])
        self.assertIn("undo", HOW_TO_HELP["capture"])
        self.assertIn("fix last capture", HOW_TO_HELP["capture"])
        self.assertIn('replace "old" with "new"', HOW_TO_HELP["capture"])

    async def test_authorized_commands_question_gets_general_help(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("what commands can I use?")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(claw.requests, [])
        self.assertEqual(message.replies, [HELP_MESSAGE])

    async def test_help_open_question_uses_ai_answerer_for_session_reset(self):
        answerer = FakeHelpAnswerer(
            "To restart the current N4OS session, send /new or /reset.\n"
            "For rich chat only, send /chat reset."
        )
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
            help_answerer=answerer,
        )
        message = FakeMessage("/help what is command to refresh or restart a session")

        await bot.handle_help(FakeUpdate(12345, message), None)

        self.assertEqual(
            message.replies,
            [
                "To restart the current N4OS session, send /new or /reset.\n"
                "For rich chat only, send /chat reset."
            ],
        )
        self.assertEqual(answerer.questions, ["what is command to refresh or restart a session"])
        self.assertNotIn("**", message.replies[0])
        self.assertNotIn("###", message.replies[0])
        self.assertNotIn("Loaded:", message.replies[0])
        self.assertNotIn("n4os/", message.replies[0])

    async def test_natural_help_question_uses_ai_answerer_for_session_reset(self):
        claw = FakeClaw()
        answerer = FakeHelpAnswerer("Use /new or /reset to start a new N4OS session.")
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
            help_answerer=answerer,
        )
        message = FakeMessage("what command restarts a session?")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(claw.requests, [])
        self.assertEqual(message.replies, ["Use /new or /reset to start a new N4OS session."])
        self.assertEqual(answerer.questions, ["what command restarts a session?"])

    async def test_ai_help_failure_falls_back_to_general_help(self):
        claw = FakeClaw()
        answerer = FakeHelpAnswerer(fail=True)
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
            help_answerer=answerer,
        )
        message = FakeMessage("/help what command restarts a session")

        await bot.handle_help(FakeUpdate(12345, message), None)

        self.assertEqual(claw.requests, [])
        self.assertEqual(message.replies, [HELP_MESSAGE])
        self.assertEqual(answerer.questions, ["what command restarts a session"])

    async def test_authorized_how_to_before_leaving_portal_gets_direct_help(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage(
            "how do I add things to carry before leaving which appears on portal"
        )

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(claw.requests, [])
        self.assertEqual(message.replies, [HOW_TO_HELP["before_leave"]])

    async def test_authorized_can_i_add_date_for_home_board_gets_direct_help(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("can I add a date for the item for home board?")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(claw.requests, [])
        self.assertEqual(message.replies, [HOW_TO_HELP["home_board"]])

    async def test_authorized_how_to_library_gets_direct_help(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("how do I use library?")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(claw.requests, [])
        self.assertEqual(message.replies, [HOW_TO_HELP["library"]])

    async def test_authorized_how_to_science_lab_gets_direct_help(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("how do I use the science experiment area?")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(claw.requests, [])
        self.assertEqual(message.replies, [HOW_TO_HELP["science_lab"]])

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

    async def test_authorized_voice_remember_punctuation_records_structured_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()
            claw = FakeClaw()
            transcript = "Remember, learning bee teacher name Ms. Kelly and Ms. Peggy."
            transcriber = FakeAudioTranscriber(transcript)
            bot = N4OSTelegramBot(
                TelegramConfig(token="token", allowed_user_id=12345),
                claw,
                logger=QuietLogger(),
                audio_transcriber=transcriber,
                n4os_root=n4os_root,
            )
            message = FakeMessage(voice=FakeVoice())

            await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(transcriber.messages, [message])
        self.assertEqual(claw.requests, [])
        self.assertEqual(
            message.replies,
            [
                VOICE_TRANSCRIPTION_STARTED_MESSAGE,
                VOICE_TRANSCRIPTION_RESULT_MESSAGE.format(text=transcript),
                "Remembered. Structured note saved.",
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
                        "list for Chad Bond. This task is for Nimesh. "
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

    def test_default_audio_transcriber_prefers_openclaw_command(self):
        with (
            patch(
                "telegram_audio._default_openclaw_transcribe_command",
                return_value=("openclaw", "infer", "audio", "transcribe"),
            ),
            patch("telegram_audio._resolve_whisper_command", return_value="/tmp/whisper"),
        ):
            transcriber = create_default_audio_transcriber()

        self.assertIsInstance(transcriber, CommandAudioTranscriber)
        self.assertEqual(
            transcriber.command,
            ("openclaw", "infer", "audio", "transcribe"),
        )

    def test_default_audio_transcriber_uses_local_whisper_as_fallback(self):
        with (
            patch("telegram_audio._default_openclaw_transcribe_command", return_value=None),
            patch("telegram_audio._resolve_whisper_command", return_value="/tmp/whisper"),
        ):
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
        self.assertIn("/capture Nysha", HELP_MESSAGE)
        self.assertIn("/ask How should", HELP_MESSAGE)
        self.assertIn("/chat Let's think", HELP_MESSAGE)
        self.assertIn("/review week", HELP_MESSAGE)
        self.assertIn("/status Nysha", HELP_MESSAGE)
        self.assertIn("/status reading", HELP_MESSAGE)
        self.assertIn("/event create", HELP_MESSAGE)
        self.assertIn("add task call FUSD", HELP_MESSAGE)
        self.assertIn("/cart add milk", HELP_MESSAGE)
        self.assertIn("today's briefing", HELP_MESSAGE)
        self.assertIn("/goals", HELP_MESSAGE)
        self.assertIn("Nysha read 8 pages", HELP_MESSAGE)
        self.assertIn("science lab experiments", HELP_MESSAGE)
        self.assertIn("Discussion: Should we attend", HELP_MESSAGE)
        self.assertIn("Planning: Camping trip", HELP_MESSAGE)
        self.assertIn("Decision: Choose Nysha's school", HELP_MESSAGE)
        self.assertIn("add home board item", HELP_MESSAGE)
        self.assertIn("how do I add a memory?", HELP_MESSAGE)
        self.assertIn("how do I use shopping?", HELP_MESSAGE)
        self.assertLessEqual(len(HELP_MESSAGE.splitlines()), 16)
        self.assertNotIn("**", HELP_MESSAGE)
        self.assertNotIn("###", HELP_MESSAGE)
        self.assertNotIn("Loaded:", HELP_MESSAGE)
        self.assertNotIn("n4os/", HELP_MESSAGE)

    def test_ai_help_catalog_includes_routes_and_session_controls(self):
        catalog = _build_help_catalog()

        route_commands = catalog["route_commands"]
        self.assertTrue(
            any(
                "/task" in entry["command_aliases"] and "create_task" in entry["actions"]
                for entry in route_commands
            )
        )
        self.assertIn(
            {
                "topic": "session",
                "commands": ["/new", "/reset", "new session"],
                "purpose": "Start a new N4OS conversation session and clear current router state.",
            },
            catalog["conversation_controls"],
        )

    def test_openai_help_answerer_payload_contains_catalog(self):
        captured: dict[str, Any] = {}

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps({"output_text": "Use /new or /reset to start fresh."}).encode(
                    "utf-8"
                )

        def fake_urlopen(request: Any, timeout: int) -> FakeResponse:
            captured["timeout"] = timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        answerer = OpenAIN4OSHelpAnswerer(
            api_key="test-key",
            model="test-help-model",
            timeout_seconds=3,
            urlopen=fake_urlopen,
        )

        self.assertEqual(
            answerer.answer("what command restarts a session?"),
            "Use /new or /reset to start fresh.",
        )

        self.assertEqual(captured["timeout"], 3)
        payload = captured["payload"]
        self.assertEqual(payload["model"], "test-help-model")
        user_content = json.loads(payload["input"][1]["content"])
        self.assertEqual(user_content["question"], "what command restarts a session?")
        self.assertTrue(
            any(
                control["commands"] == ["/new", "/reset", "new session"]
                for control in user_content["catalog"]["conversation_controls"]
            )
        )
        self.assertTrue(
            any(
                "/task" in command["command_aliases"] and "create_task" in command["actions"]
                for command in user_content["catalog"]["route_commands"]
            )
        )

    def test_openai_image_text_extractor_prompt_preserves_schedule_tables(self):
        captured: dict[str, Any] = {}

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(
                    {
                        "output_text": (
                            "School Day Date Time Attendance\n"
                            "Fremont Fri Sep 18th, 2026 6:00PM Mark absent"
                        )
                    }
                ).encode("utf-8")

        def fake_urlopen(request: Any, timeout: int) -> FakeResponse:
            del timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        with tempfile.NamedTemporaryFile(suffix=".jpg") as image:
            image.write(b"calendar image")
            image.flush()
            extractor = OpenAIImageTextExtractor(
                api_key="test-key",
                model="test-image-model",
                urlopen=fake_urlopen,
            )

            self.assertIn("Fremont Fri Sep 18th", extractor.extract_text(Path(image.name)))

        prompt = captured["payload"]["input"][0]["content"][0]["text"]
        self.assertFalse(captured["payload"]["store"])
        self.assertIn("calendar, class, appointment, or schedule tables", prompt)
        self.assertIn("one row per visible scheduled entry", prompt)

    async def test_help_command_with_library_topic_gets_library_help(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        message = FakeMessage("/help library")

        await bot.handle_help(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies, [HOW_TO_HELP["library"]])
        self.assertIn("Send one of these:", HOW_TO_HELP["library"])
        self.assertIn("Add reading:", HOW_TO_HELP["library"])
        self.assertIn("Change:", HOW_TO_HELP["library"])
        self.assertIn("Delete:", HOW_TO_HELP["library"])
        self.assertIn("See:", HOW_TO_HELP["library"])
        self.assertIn("Change Nysha latest reading book", HOW_TO_HELP["library"])
        self.assertIn("/status reading", HOW_TO_HELP["library"])

    async def test_help_command_with_event_topic_gets_event_help(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        message = FakeMessage("/help event")

        await bot.handle_help(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies, [HOW_TO_HELP["event"]])
        self.assertIn("Send one of these:", HOW_TO_HELP["event"])
        self.assertIn("Add:", HOW_TO_HELP["event"])
        self.assertIn("Move:", HOW_TO_HELP["event"])
        self.assertIn("Cancel:", HOW_TO_HELP["event"])
        self.assertIn("See:", HOW_TO_HELP["event"])
        self.assertIn("Move dinner with Rahul", HOW_TO_HELP["event"])
        self.assertIn("give me today's briefing", HOW_TO_HELP["event"])

    async def test_help_command_with_task_topic_gets_task_help(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        message = FakeMessage("/help task")

        await bot.handle_help(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies, [HOW_TO_HELP["task"]])
        self.assertIn("Send one of these:", HOW_TO_HELP["task"])
        self.assertIn("Add:", HOW_TO_HELP["task"])
        self.assertIn("Done:", HOW_TO_HELP["task"])
        self.assertIn("Delete:", HOW_TO_HELP["task"])
        self.assertIn("See:", HOW_TO_HELP["task"])
        self.assertIn("complete task call FUSD", HOW_TO_HELP["task"])
        self.assertIn("show urgent tasks", HOW_TO_HELP["task"])

    async def test_help_command_with_homework_topic_gets_homework_help(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        message = FakeMessage("/help homework")

        await bot.handle_help(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies, [HOW_TO_HELP["homework"]])
        self.assertIn("/capture homework Nysha", HOW_TO_HELP["homework"])
        self.assertIn("/capture submitted homework", HOW_TO_HELP["homework"])
        self.assertIn("n4os/homework/*.md", HOW_TO_HELP["homework"])

    async def test_homework_slash_help_message_gets_homework_help_without_capture(self):
        claw = FakeClaw()
        homework = FakeHomeworkClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
            homework_claw=homework,
        )
        message = FakeMessage("/homework help")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies, [HOW_TO_HELP["homework"]])
        self.assertEqual(homework.calls, [])
        self.assertEqual(claw.requests, [])

    async def test_remember_slash_help_message_gets_remember_help_without_memory_write(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("/remember help")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies, [HOW_TO_HELP["remember"]])
        self.assertIn("/remember Nysha school gate code", HOW_TO_HELP["remember"])
        self.assertIn("data/n4os.db", HOW_TO_HELP["remember"])
        self.assertEqual(claw.requests, [])

    async def test_notes_slash_help_message_gets_note_help_without_markdown_capture(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("/notes help")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies, [HOW_TO_HELP["note"]])
        self.assertIn("/note quick", HOW_TO_HELP["note"])
        self.assertIn("n4os/learnings/Quick Notes.md", HOW_TO_HELP["note"])
        self.assertIn("n4os/learnings/YYYY-MM-DD-<title>.md", HOW_TO_HELP["note"])
        self.assertIn("n4os/learnings/Inbox.md", HOW_TO_HELP["note"])
        self.assertEqual(claw.requests, [])

    async def test_task_slash_help_message_mentions_noah_assistant_help(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("/task help")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies, [HOW_TO_HELP["task"]])
        self.assertIn("Noah assistant help", HOW_TO_HELP["task"])
        self.assertEqual(claw.requests, [])

    async def test_task_slash_help_question_gets_task_help_without_creating_task(self):
        claw = FakeClaw()
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
        )
        message = FakeMessage("/task help how do I mark a task done")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies, [HOW_TO_HELP["task"]])
        self.assertIn("Done: complete task call FUSD", HOW_TO_HELP["task"])
        self.assertEqual(claw.requests, [])

    async def test_school_newsletter_slash_help_message_gets_import_help(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        message = FakeMessage("/school-newsletter help")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies, [HOW_TO_HELP["school_newsletter"]])
        self.assertIn("/import school newsletter", HOW_TO_HELP["school_newsletter"])

    async def test_help_command_with_shop_topic_gets_shopping_help(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        message = FakeMessage("/help shop")

        await bot.handle_help(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies, [HOW_TO_HELP["shopping"]])
        self.assertIn("Send one of these:", HOW_TO_HELP["shopping"])
        self.assertIn("Add:", HOW_TO_HELP["shopping"])
        self.assertIn("Cross off:", HOW_TO_HELP["shopping"])
        self.assertIn("Move:", HOW_TO_HELP["shopping"])
        self.assertIn("See:", HOW_TO_HELP["shopping"])
        self.assertIn("/shop move coconut milk", HOW_TO_HELP["shopping"])
        self.assertIn("what's on my Whole Foods list?", HOW_TO_HELP["shopping"])

    async def test_help_command_with_status_topic_gets_status_help(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        message = FakeMessage("/help status")

        await bot.handle_help(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies, [HOW_TO_HELP["memory_status"]])

    async def test_help_command_with_chat_topic_gets_advice_help(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FakeClaw(),
            logger=QuietLogger(),
        )
        message = FakeMessage("/help chat")

        await bot.handle_help(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies, [HOW_TO_HELP["n4os_advice"]])

    async def test_router_errors_are_reported_gracefully(self):
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            FailingClaw(),
            logger=QuietLogger(),
        )
        message = FakeMessage("Add task buy milk")

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies, [ERROR_MESSAGE])

    async def test_failed_library_write_does_not_commit_photo(self):
        claw = FailingLibraryRouteClaw()
        extractor = FakeImageTextExtractor("Book title: unique failed upload")
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
            image_text_extractor=extractor,
        )
        message = FakeMessage(
            caption="Nysha read this",
            photo=[FakeTelegramPhoto(FakeTelegramFile(b"unique failed library upload"))],
        )

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies, [ERROR_MESSAGE])
        self.assertIsNotNone(claw.photo_path)
        stored_file = READING_PHOTO_UPLOAD_DIR / Path(claw.photo_path).name
        self.assertFalse(stored_file.exists())
        self.assertTrue(all(not path.exists() for path in extractor.paths))

    async def test_returned_library_storage_error_does_not_commit_photo(self):
        library = FailedLibraryDomainClaw()
        claw = N4OSClaw(library_claw=library)
        extractor = FakeImageTextExtractor("Book title: failed result upload")
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
            image_text_extractor=extractor,
        )
        message = FakeMessage(
            caption="Nysha read this",
            photo=[FakeTelegramPhoto(FakeTelegramFile(b"failed result library upload"))],
        )

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies, ["Reading Garden storage failed."])
        self.assertTrue(all(not path.exists() for path in extractor.paths))
        self.assertIsNotNone(library.photo_path)
        self.assertFalse((READING_PHOTO_UPLOAD_DIR / Path(library.photo_path).name).exists())

    async def test_wrapped_library_failure_does_not_commit_photo(self):
        claw = FailedLegacyLibraryRouteClaw()
        extractor = FakeImageTextExtractor("Book title: wrapped failed upload")
        bot = N4OSTelegramBot(
            TelegramConfig(token="token", allowed_user_id=12345),
            claw,
            logger=QuietLogger(),
            image_text_extractor=extractor,
        )
        message = FakeMessage(
            caption="Nysha read this",
            photo=[FakeTelegramPhoto(FakeTelegramFile(b"wrapped failed library upload"))],
        )

        await bot.handle_message(FakeUpdate(12345, message), None)

        self.assertEqual(message.replies, ["Reading Garden storage failed."])
        photo_path = claw.calls[0][3]
        self.assertIsNotNone(photo_path)
        self.assertFalse((READING_PHOTO_UPLOAD_DIR / Path(photo_path).name).exists())


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

    def test_load_config_reads_multiple_allowed_user_ids(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "TELEGRAM_BOT_TOKEN=token\nALLOWED_TELEGRAM_USER_IDS=12345,67890\n",
                encoding="utf-8",
            )

            config = load_config(env_path=env_path)

        self.assertEqual(config.token, "token")
        self.assertEqual(config.allowed_user_ids, frozenset({12345, 67890}))

    def test_load_config_reads_sender_profiles(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "TELEGRAM_BOT_TOKEN=token\n"
                "ALLOWED_TELEGRAM_USER_IDS=12345,67890\n"
                "TELEGRAM_USER_PROFILES=12345:Nimesh:dad,67890:Niyati:mom\n",
                encoding="utf-8",
            )

            config = load_config(env_path=env_path)

        self.assertEqual(
            config.sender_profiles,
            (
                TelegramSenderProfile(12345, "nimesh", "dad"),
                TelegramSenderProfile(67890, "niyati", "mom"),
            ),
        )

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

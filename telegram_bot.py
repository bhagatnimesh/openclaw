from __future__ import annotations

import asyncio
import base64
from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any
import urllib.request

from dotenv import dotenv_values, load_dotenv

try:
    from telegram import Update
    from telegram.ext import (
        Application,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )
except ModuleNotFoundError as error:  # pragma: no cover - exercised by startup.
    Update = Any  # type: ignore[assignment]
    Application = None  # type: ignore[assignment]
    CommandHandler = None  # type: ignore[assignment]
    ContextTypes = Any  # type: ignore[assignment]
    MessageHandler = None  # type: ignore[assignment]
    filters = None  # type: ignore[assignment]
    TELEGRAM_IMPORT_ERROR: ModuleNotFoundError | None = error
else:
    TELEGRAM_IMPORT_ERROR = None

from claws.n4os.claw import N4OSClaw
from claws.n4os.input_normalizer import improve_entered_text
from n4os_capture import (
    CaptureIngestResult,
    format_capture_undo_reply,
    format_capture_reply,
    ingest_capture_notes,
    is_capture_message,
    undo_capture_ingest,
)
from n4os_memory_status import (
    format_memory_status,
    is_memory_status_message,
    parse_memory_status_target,
)
from n4os_review import (
    format_n4os_review,
    is_n4os_review_message,
    parse_review_period,
)
from n4os_status import (
    format_n4os_status,
    is_n4os_status_message,
    parse_status_target,
)
from n4os_goals_status import (
    format_goals_status,
    is_goals_status_message,
)
from n4os_advice import (
    format_n4os_advice,
    is_n4os_advice_message,
)
from telegram_audio import (
    AudioTranscriber,
    VOICE_TRANSCRIBE_COMMAND_ENV,
    VOICE_TRANSCRIPTION_UNAVAILABLE_MESSAGE,
    VoiceTranscriptionTimeout,
    VoiceTranscriptionUnavailable,
    create_default_audio_transcriber,
    has_audio,
    parse_voice_transcribe_command,
)


LOGGER = logging.getLogger("n4os.telegram")
SETUP_USER_MESSAGE = (
    "Your Telegram user id is: {user_id}. "
    "Add ALLOWED_TELEGRAM_USER_ID={user_id} to .env and restart."
)
UNAUTHORIZED_MESSAGE = "Unauthorized."
HELP_MESSAGE = (
    "N4OS Telegram help\n\n"
    "You can usually speak naturally. Examples:\n"
    "- Capture: /capture Nysha was nervous about school. I felt unsure how to help\n"
    "- Ask: /ask How should I approach Nysha's reading?\n"
    "- Review: /review week\n"
    "- Status: /status Nysha, /status goals, or /status reading\n"
    "- Add event: /event create dinner with Rahul next Tuesday at 7 PM\n"
    "- Add task: add task call FUSD tomorrow morning\n"
    "- Day briefing: give me today's briefing\n"
    "- Legacy memory: /mem Nysha liked teaching younger kids today\n"
    "- Goals status: /goals or what are my current goals?\n"
    "- Library: Nysha read 8 pages of Mercy Watson by herself\n"
    "- Library status: /status or reading status\n"
    "- Science Lab: plan the next 4 science lab experiments\n"
    "- Family decision: create decision for summer camp options\n"
    "- Home board: add home board item buy milk\n\n"
    "If you forget the exact command, ask in plain English, for example: "
    "how do I add a memory? or how do I add an event?"
)
ERROR_MESSAGE = "Sorry, N4OS hit an error while handling that."
UNSUPPORTED_MESSAGE = "Please send a text or voice message."
IMAGE_TEXT_MARKER = "Image text:"
OPENAI_IMAGE_TEXT_MODEL = "gpt-5.4-mini"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
VOICE_TRANSCRIPTION_STARTED_MESSAGE = "Got it, transcribing that voice message."
VOICE_TRANSCRIPTION_RESULT_MESSAGE = "Transcribed: {text}"
VOICE_TRANSCRIPTION_EMPTY_MESSAGE = "I could not hear any speech in that voice message."
VOICE_TRANSCRIPTION_FAILED_MESSAGE = "Sorry, I could not transcribe that voice message."
VOICE_TRANSCRIPTION_TIMEOUT_MESSAGE = (
    "Sorry, voice transcription took too long. Please try a shorter voice message."
)
VOICE_TRANSCRIPTION_HANDLER_TIMEOUT_SECONDS = 90
CAPTURE_CLARIFICATION_RE = re.compile(
    r"^\s*(?:a\s+)?(?:capture|note|memory|remember(?:\s+this)?)\s*[.!?]?\s*$",
    re.IGNORECASE,
)
HOW_TO_HELP = {
    "capture": (
        "To capture anything worth remembering, send:\n"
        "capture Nysha liked teaching younger kids today. I felt proud.\n\n"
        "For a batch, send:\n"
        "capture\n"
        "2026-07-21\n"
        "Nysha playing games very well\n"
        "I felt scattered after poor sleep\n\n"
        "Old /capture, /mem, and /mem-inbox commands still work as capture aliases."
    ),
    "event": (
        "To add an event, speak naturally or use /event:\n"
        "/event create dinner with Rahul next Tuesday at 7 PM\n\n"
        "You can also say:\n"
        "add event dentist appointment tomorrow at 4 PM"
    ),
    "task": (
        "To add a task, speak naturally:\n"
        "add task call FUSD tomorrow morning\n\n"
        "For richer tasks, include owner, timing, and why it matters."
    ),
    "memory_status": (
        "To see current N4OS status, send:\n"
        "/status family\n"
        "/status Nysha\n"
        "/status Navya\n"
        "/status goals\n"
        "/status reading"
    ),
    "goals": (
        "To see your current N4OS goals, send:\n"
        "/goals\n"
        "or\n"
        "what are my current goals?"
    ),
    "n4os_advice": (
        "For memory-backed N4OS advice, use:\n"
        "/ask How should we approach Nysha's reading?\n"
        "/n4os How should we approach Nysha's reading?\n"
        "/coach What should I focus on this week?\n"
        "/advice How should I handle this career decision?\n\n"
        "N4OS will load relevant markdown memory and use OpenAI enrichment when configured."
    ),
    "review": (
        "To review patterns, send:\n"
        "/review day\n"
        "/review week\n"
        "/review month\n\n"
        "Reviews suggest promotion candidates but do not change stable N4OS files."
    ),
    "decision": (
        "To track a family decision, say:\n"
        "create decision for summer camp options\n\n"
        "Then add options or evidence as you learn more."
    ),
    "library": (
        "Library is Nysha's Reading Garden.\n\n"
        "Record reading:\n"
        "Nysha read 8 pages of Mercy Watson by herself\n"
        "Nysha finished Elephant and Piggie herself\n\n"
        "Record checkout:\n"
        "library checkout: Mercy Watson, Frog and Toad, Narwhal\n\n"
        "Check status:\n"
        "/status\n"
        "reading status"
    ),
    "science_lab": (
        "Science Lab helps plan kid-friendly experiments and materials.\n\n"
        "Ask:\n"
        "plan the next 4 science lab experiments\n"
        "plan 2 science experiments\n\n"
        "It will return experiment ideas plus materials already at home, "
        "materials to confirm, and recommended Amazon orders."
    ),
    "home_board": (
        "To add a home board item, say:\n"
        "add home board item buy milk\n"
        "or\n"
        "add home board item school forms\n\n"
        "You can include dates:\n"
        "add home board item tomorrow before leaving put passports by the door"
    ),
    "before_leave": (
        "To add something to the portal's Before leaving section, use Home Board:\n"
        "add home board item before leaving take water bottles\n\n"
        "More examples:\n"
        "- before we leave, take jackets and snacks\n"
        "- add home board item tomorrow before leaving put passports by the door\n\n"
        "It will appear on the portal under Today at Home / Before leaving."
    ),
}


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    allowed_user_id: int | None
    voice_transcribe_command: tuple[str, ...] | None = None


@dataclass(frozen=True)
class RouterResult:
    response: str
    route: str
    elapsed_ms: float


@dataclass(frozen=True)
class TelegramUndoEntry:
    kind: str
    capture_result: CaptureIngestResult | None = None


def _extract_response_text(payload: dict[str, Any]) -> str:
    output = payload.get("output", [])
    if not isinstance(output, list):
        return ""

    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts).strip()


class OpenAIImageTextExtractor:
    def __init__(
        self,
        api_key: str,
        model: str = OPENAI_IMAGE_TEXT_MODEL,
        urlopen: Any = urllib.request.urlopen,
    ) -> None:
        if not api_key.strip():
            raise RuntimeError("Image text extraction needs OPENAI_API_KEY.")
        self.api_key = api_key.strip()
        self.model = model.strip() or OPENAI_IMAGE_TEXT_MODEL
        self.urlopen = urlopen

    @classmethod
    def from_env_or_none(cls) -> "OpenAIImageTextExtractor | None":
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            return None
        return cls(api_key=api_key, model=os.environ.get("N4OS_IMAGE_TEXT_MODEL", OPENAI_IMAGE_TEXT_MODEL))

    def extract_text(self, image_path: Path) -> str:
        mime_type = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
        image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
        payload = {
            "model": self.model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Extract task/checklist entries visible in this image. "
                                "If a list title is visible, return it first as "
                                "`List title: <title>`. Then return each task entry, one per line. "
                                "Do not include checkbox symbols, bullets, numbering, explanations, or guesses."
                            ),
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:{mime_type};base64,{image_data}",
                            "detail": "high",
                        },
                    ],
                }
            ],
        }
        request = urllib.request.Request(
            OPENAI_RESPONSES_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with self.urlopen(request, timeout=20) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
        return _extract_response_text(response_payload)


def _is_image_document(document: Any) -> bool:
    mime_type = str(getattr(document, "mime_type", "") or "")
    return mime_type.startswith("image/")


def _has_image(message: Any) -> bool:
    photos = getattr(message, "photo", None)
    if photos:
        return True
    return _is_image_document(getattr(message, "document", None))


def _largest_photo(message: Any) -> Any | None:
    photos = list(getattr(message, "photo", None) or [])
    if photos:
        return max(
            photos,
            key=lambda photo: (
                int(getattr(photo, "file_size", 0) or 0),
                int(getattr(photo, "width", 0) or 0) * int(getattr(photo, "height", 0) or 0),
            ),
        )

    document = getattr(message, "document", None)
    if _is_image_document(document):
        return document
    return None


def _combine_text_and_image_text(text: str, image_text: str) -> str:
    cleaned_image_text = image_text.strip()
    if not cleaned_image_text:
        return text
    cleaned_text = text.strip()
    if not cleaned_text:
        return f"{IMAGE_TEXT_MARKER}\n{cleaned_image_text}"
    return f"{cleaned_text}\n\n{IMAGE_TEXT_MARKER}\n{cleaned_image_text}"


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def load_config(env_path: str | Path = ".env") -> TelegramConfig:
    env_values = dotenv_values(env_path)
    load_dotenv(env_path)

    token = _env_value(env_values, "TELEGRAM_BOT_TOKEN") or os.getenv(
        "TELEGRAM_BOT_TOKEN",
        "",
    ).strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing from .env.")

    raw_voice_command = _env_value(env_values, VOICE_TRANSCRIBE_COMMAND_ENV)
    if not raw_voice_command:
        raw_voice_command = os.getenv(VOICE_TRANSCRIBE_COMMAND_ENV, "").strip()
    voice_transcribe_command = parse_voice_transcribe_command(raw_voice_command)

    raw_allowed_user_id = _env_value(env_values, "ALLOWED_TELEGRAM_USER_ID")
    if not raw_allowed_user_id:
        return TelegramConfig(
            token=token,
            allowed_user_id=None,
            voice_transcribe_command=voice_transcribe_command,
        )

    try:
        allowed_user_id = int(raw_allowed_user_id)
    except ValueError as error:
        raise RuntimeError("ALLOWED_TELEGRAM_USER_ID must be an integer.") from error

    return TelegramConfig(
        token=token,
        allowed_user_id=allowed_user_id,
        voice_transcribe_command=voice_transcribe_command,
    )


def _env_value(env_values: dict[str, str | None], key: str) -> str:
    value = env_values.get(key)
    return value.strip() if isinstance(value, str) else ""


def _telegram_how_to_reply(text: str) -> str | None:
    lowered = text.lower().strip()
    if not any(cue in lowered for cue in ("how do i", "how to", "can i", "what command", "commands", "help")):
        return None

    if "memory-status" in lowered or ("memory" in lowered and "status" in lowered):
        return HOW_TO_HELP["memory_status"]
    if "capture" in lowered or "note" in lowered or "memory" in lowered or "observation" in lowered:
        return HOW_TO_HELP["capture"]
    if "review" in lowered or "pattern" in lowered:
        return HOW_TO_HELP["review"]
    if "goal" in lowered or "priority" in lowered:
        return HOW_TO_HELP["goals"]
    if "n4os" in lowered or "coach" in lowered or "advice" in lowered:
        return HOW_TO_HELP["n4os_advice"]
    if (
        "before leaving" in lowered
        or "before leave" in lowered
        or "carry" in lowered
        or "portal" in lowered
    ):
        return HOW_TO_HELP["before_leave"]
    if "event" in lowered or "calendar" in lowered:
        return HOW_TO_HELP["event"]
    if (
        "library" in lowered
        or "reading garden" in lowered
        or "reading" in lowered
        or "book" in lowered
        or "checkout" in lowered
    ):
        return HOW_TO_HELP["library"]
    if "science" in lowered or "experiment" in lowered:
        return HOW_TO_HELP["science_lab"]
    if "task" in lowered or "todo" in lowered or "to-do" in lowered:
        return HOW_TO_HELP["task"]
    if "decision" in lowered:
        return HOW_TO_HELP["decision"]
    if "home board" in lowered:
        return HOW_TO_HELP["home_board"]
    return HELP_MESSAGE


def _library_status_alias(text: str) -> str | None:
    lowered = text.strip().lower()
    if lowered in {"/status", "reading status", "/reading status", "garden status", "/garden status"}:
        return "reading status"
    return None


def _pending_capture_request(claw: N4OSClaw, text: str) -> str | None:
    if CAPTURE_CLARIFICATION_RE.match(text) is None:
        return None

    pending = getattr(claw, "pending_route_clarification", None)
    request = getattr(pending, "request", None)
    return request if isinstance(request, str) and request.strip() else None


def _is_undo_message(text: str) -> bool:
    normalized = " ".join(text.lower().strip(" .!?").split())
    return normalized in {
        "undo",
        "undo that",
        "undo last",
        "undo the last thing",
        "revert",
        "revert that",
        "revert last",
        "cancel",
        "cancel that",
        "cancel last",
        "nevermind",
        "never mind",
    }


def _n4os_mutation_depth(claw: Any) -> int:
    route_context = getattr(claw, "route_context", None)
    stack = getattr(route_context, "mutation_route_stack", None)
    return len(stack) if isinstance(stack, list) else 0


class N4OSTelegramBot:
    def __init__(
        self,
        config: TelegramConfig,
        claw: N4OSClaw | None = None,
        logger: logging.Logger | None = None,
        audio_transcriber: AudioTranscriber | None = None,
        image_text_extractor: Any | None = None,
    ) -> None:
        self.config = config
        self.claw = claw or N4OSClaw()
        self.logger = logger or LOGGER
        self.audio_transcriber = audio_transcriber or create_default_audio_transcriber(
            config.voice_transcribe_command,
        )
        self.image_text_extractor = image_text_extractor or OpenAIImageTextExtractor.from_env_or_none()
        self.undo_stack: list[TelegramUndoEntry] = []

    def route_message(self, text: str) -> RouterResult:
        started = time.perf_counter()
        improved_text = improve_entered_text(text)
        output = StringIO()
        before_mutation_depth = _n4os_mutation_depth(self.claw)
        # Existing N4OS claws print their user-facing messages; keep the
        # Telegram transport thin by capturing that router output verbatim.
        with redirect_stdout(output):
            decision = self.claw.handle_request(improved_text) or {}
        if _n4os_mutation_depth(self.claw) > before_mutation_depth:
            self.undo_stack.append(TelegramUndoEntry(kind="router"))

        elapsed_ms = (time.perf_counter() - started) * 1000
        route = str(decision.get("route", "unknown"))
        response = output.getvalue().strip()
        if not response:
            response = str(decision.get("intent_summary") or "Done.")

        return RouterResult(response=response, route=route, elapsed_ms=elapsed_ms)

    def undo_last_action(self) -> str | None:
        if not self.undo_stack:
            return None

        entry = self.undo_stack.pop()
        if entry.kind == "capture" and entry.capture_result is not None:
            result = undo_capture_ingest(entry.capture_result)
            return format_capture_undo_reply(result)

        if entry.kind == "router":
            output = StringIO()
            with redirect_stdout(output):
                decision = self.claw.handle_request("undo") or {}
            response = output.getvalue().strip()
            return response or str(decision.get("intent_summary") or "Undone.")

        return "I do not know how to undo that."

    def _authorization_reply(self, user_id: int | None) -> str | None:
        if user_id is None:
            return UNAUTHORIZED_MESSAGE
        if self.config.allowed_user_id is None:
            return SETUP_USER_MESSAGE.format(user_id=user_id)
        if user_id != self.config.allowed_user_id:
            return UNAUTHORIZED_MESSAGE
        return None

    async def _extract_image_text(self, message: Any) -> str:
        if self.image_text_extractor is None:
            return ""

        photo = _largest_photo(message)
        if photo is None:
            return ""

        suffix = ".jpg"
        document = getattr(message, "document", None)
        file_name = getattr(document, "file_name", "") if document is not None else ""
        if file_name:
            guessed_suffix = Path(file_name).suffix
            if guessed_suffix:
                suffix = guessed_suffix

        image_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="n4os-telegram-image-", suffix=suffix, delete=False) as temp:
                image_path = Path(temp.name)
            telegram_file = await photo.get_file()
            await telegram_file.download_to_drive(image_path)
            return self.image_text_extractor.extract_text(image_path).strip()
        finally:
            if image_path is not None:
                try:
                    image_path.unlink(missing_ok=True)
                except OSError:
                    self.logger.warning("could not remove temporary Telegram image %s", image_path)

    async def handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        message = update.effective_message
        if message is None:
            return

        user_id = _effective_user_id(update)
        text = (getattr(message, "text", None) or "").strip()
        self.logger.info("incoming message user_id=%s text=%r", user_id, text)

        auth_reply = self._authorization_reply(user_id)
        if auth_reply is not None:
            await message.reply_text(auth_reply)
            self.logger.info("chosen route=unauthorized execution_ms=0.00")
            return

        self.logger.info("chosen route=help execution_ms=0.00")
        await message.reply_text(HELP_MESSAGE)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        message = update.effective_message
        if message is None:
            return

        user_id = _effective_user_id(update)
        text = (
            getattr(message, "text", None)
            or getattr(message, "caption", None)
            or ""
        ).strip()
        has_image = _has_image(message)
        message_kind = "text" if text else ("image" if has_image else ("audio" if has_audio(message) else "unsupported"))
        self.logger.info(
            "incoming message user_id=%s kind=%s text=%r",
            user_id,
            message_kind,
            text,
        )

        auth_reply = self._authorization_reply(user_id)
        if auth_reply is not None:
            await message.reply_text(auth_reply)
            self.logger.info("chosen route=unauthorized execution_ms=0.00")
            return

        if not text and has_audio(message):
            await message.reply_text(VOICE_TRANSCRIPTION_STARTED_MESSAGE)
            transcription_started = time.perf_counter()
            try:
                transcript = await asyncio.wait_for(
                    self.audio_transcriber.transcribe(message),
                    timeout=VOICE_TRANSCRIPTION_HANDLER_TIMEOUT_SECONDS,
                )
                text = transcript.strip()
            except VoiceTranscriptionUnavailable:
                await message.reply_text(VOICE_TRANSCRIPTION_UNAVAILABLE_MESSAGE)
                self.logger.info("chosen route=unsupported execution_ms=0.00")
                return
            except (VoiceTranscriptionTimeout, asyncio.TimeoutError):
                elapsed_ms = (time.perf_counter() - transcription_started) * 1000
                self.logger.warning(
                    "Telegram audio transcription timed out execution_ms=%.2f",
                    elapsed_ms,
                )
                await message.reply_text(VOICE_TRANSCRIPTION_TIMEOUT_MESSAGE)
                return
            except Exception:
                self.logger.exception("error while transcribing Telegram audio")
                await message.reply_text(VOICE_TRANSCRIPTION_FAILED_MESSAGE)
                return

            if not text:
                await message.reply_text(VOICE_TRANSCRIPTION_EMPTY_MESSAGE)
                self.logger.info("chosen route=unsupported execution_ms=0.00")
                return
            await message.reply_text(VOICE_TRANSCRIPTION_RESULT_MESSAGE.format(text=text))
            elapsed_ms = (time.perf_counter() - transcription_started) * 1000
            self.logger.info(
                "transcribed Telegram audio chars=%d execution_ms=%.2f",
                len(text),
                elapsed_ms,
            )

        if has_image:
            try:
                image_text = await self._extract_image_text(message)
            except Exception:
                self.logger.exception("error while extracting Telegram image text")
                image_text = ""
            if image_text:
                text = _combine_text_and_image_text(text, image_text)

        if not text:
            await message.reply_text(UNSUPPORTED_MESSAGE)
            self.logger.info("chosen route=unsupported execution_ms=0.00")
            return

        if _is_undo_message(text):
            started = time.perf_counter()
            undo_reply = self.undo_last_action()
            elapsed_ms = (time.perf_counter() - started) * 1000
            if undo_reply is not None:
                self.logger.info("chosen route=undo execution_ms=%.2f", elapsed_ms)
                await message.reply_text(undo_reply)
                return

        if is_memory_status_message(text):
            target = parse_memory_status_target(text)
            self.logger.info("chosen route=memory_status target=%s execution_ms=0.00", target)
            await message.reply_text(format_memory_status(target))
            return

        if is_n4os_status_message(text):
            target = parse_status_target(text)
            status_reply = format_n4os_status(target)
            if status_reply is None:
                text = "reading status"
            else:
                self.logger.info("chosen route=n4os_status target=%s execution_ms=0.00", target)
                await message.reply_text(status_reply)
                return

        pending_capture_text = _pending_capture_request(self.claw, text)
        capture_text = pending_capture_text or text
        if pending_capture_text is not None or is_capture_message(capture_text):
            started = time.perf_counter()
            try:
                result = ingest_capture_notes(capture_text, source="Telegram")
            except Exception:
                elapsed_ms = (time.perf_counter() - started) * 1000
                self.logger.exception(
                    "error while ingesting N4OS capture execution_ms=%.2f",
                    elapsed_ms,
                )
                await message.reply_text(ERROR_MESSAGE)
                return

            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.info(
                "chosen route=n4os_capture family_added=%d journal_added=%d execution_ms=%.2f",
                len(result.family.added),
                len(result.journal_entries),
                elapsed_ms,
            )
            if pending_capture_text is not None:
                setattr(self.claw, "pending_route_clarification", None)
            if result.family.added or result.journal_entries:
                self.undo_stack.append(
                    TelegramUndoEntry(kind="capture", capture_result=result),
                )
            await message.reply_text(format_capture_reply(result))
            return

        if is_goals_status_message(text):
            self.logger.info("chosen route=goals_status execution_ms=0.00")
            await message.reply_text(format_goals_status())
            return

        if is_n4os_review_message(text):
            period = parse_review_period(text)
            self.logger.info("chosen route=n4os_review period=%s execution_ms=0.00", period)
            await message.reply_text(format_n4os_review(period))
            return

        library_status_request = _library_status_alias(text)
        if library_status_request is not None:
            text = library_status_request

        if is_n4os_advice_message(text):
            started = time.perf_counter()
            reply = format_n4os_advice(text)
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.info("chosen route=n4os_advice execution_ms=%.2f", elapsed_ms)
            await message.reply_text(reply)
            return

        how_to_reply = _telegram_how_to_reply(text)
        if how_to_reply is not None:
            self.logger.info("chosen route=telegram_help execution_ms=0.00")
            await message.reply_text(how_to_reply)
            return

        started = time.perf_counter()
        try:
            result = self.route_message(text)
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.exception(
                "error while routing Telegram message execution_ms=%.2f",
                elapsed_ms,
            )
            await message.reply_text(ERROR_MESSAGE)
            return

        self.logger.info(
            "chosen route=%s execution_ms=%.2f",
            result.route,
            result.elapsed_ms,
        )
        await message.reply_text(result.response)


def _effective_user_id(update: Update) -> int | None:
    user = getattr(update, "effective_user", None)
    user_id = getattr(user, "id", None)
    return user_id if isinstance(user_id, int) else None


def build_application(config: TelegramConfig) -> Any:
    if TELEGRAM_IMPORT_ERROR is not None or Application is None:
        raise RuntimeError(
            "python-telegram-bot is not installed in this Python environment."
        ) from TELEGRAM_IMPORT_ERROR

    bot = N4OSTelegramBot(config)
    application = Application.builder().token(config.token).build()
    application.add_handler(CommandHandler("start", bot.handle_help))
    application.add_handler(CommandHandler("help", bot.handle_help))
    application.add_handler(MessageHandler(filters.ALL, bot.handle_message))
    return application


def main() -> None:
    configure_logging()
    config = load_config()
    LOGGER.info("starting N4OS Telegram bot with long polling")
    build_application(config).run_polling()


if __name__ == "__main__":
    main()

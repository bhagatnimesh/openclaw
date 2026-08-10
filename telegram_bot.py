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
from uuid import uuid4

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
from n4os_structured_memory import (
    format_structured_memory_query,
    is_structured_memory_query,
    is_structured_remember_message,
    remember_structured_memory,
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
    _build_context as build_n4os_advice_context,
    _strip_advice_prefix as strip_n4os_advice_prefix,
    context_labels_from_context,
    format_n4os_advice,
    is_n4os_advice_message,
)
from n4os_chat import (
    N4OSChatSessionStore,
    format_n4os_chat,
    is_n4os_chat_message,
    parse_n4os_chat_control,
    strip_n4os_chat_prefix,
)
from n4os_trajectories import record_n4os_trajectory
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
    "You can speak naturally. Use these when you want precision:\n"
    "1. Remember: /capture Nysha was nervous about school. I felt unsure how to help\n"
    "2. Ask or chat: /ask How should I approach Nysha's reading? or /chat Let's think through school\n"
    "3. Review/status: /review week, /status Nysha, /status reading, /goals\n"
    "4. Calendar/tasks: /event create dinner with Rahul next Tuesday at 7 PM; add task call FUSD tomorrow morning\n"
    "5. Day plan: give me today's briefing\n"
    "6. Shopping: /cart add milk to Costco; /shop Indian\n"
    "7. Reading: Nysha read 8 pages of Mercy Watson by herself; reading status\n"
    "8. Science: plan the next 4 science lab experiments\n"
    "9. Backlog/home: Discussion: Should we attend the birthday?; Planning: Camping trip September 12; Decision: Choose Nysha's school next year; add home board item buy milk\n\n"
    "More help: ask how do I add a memory? how do I add an event? how do I use shopping?"
)
ERROR_MESSAGE = "Sorry, N4OS hit an error while handling that."
UNSUPPORTED_MESSAGE = "Please send a text or voice message."
IMAGE_TEXT_MARKER = "Image text:"
OPENAI_IMAGE_TEXT_MODEL = "gpt-5.4-mini"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
ROOT = Path(__file__).resolve().parent
READING_PHOTO_UPLOAD_DIR = ROOT / "static" / "dashboard" / "uploads" / "reading"
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
        "Calendar helps add, move, cancel, and review family events.\n\n"
        "Send one of these:\n"
        "Add: /event create dinner with Rahul next Tuesday at 7 PM\n"
        "Add: add event dentist appointment tomorrow at 4 PM\n"
        "Move: Move dinner with Rahul to Saturday at 7\n"
        "Cancel: Cancel dinner with Rahul\n"
        "See: show tomorrow's calendar\n"
        "See: give me today's briefing"
    ),
    "task": (
        "Tasks track open loops and reminders.\n\n"
        "Send one of these:\n"
        "Add: add task call FUSD tomorrow morning\n"
        "Add: Add task: Call FUSD about Nysha waitlist. Notes: Follow up with Chadbourne.\n"
        "Done: complete task call FUSD\n"
        "Delete: delete task call FUSD\n"
        "See: show urgent tasks due this week\n"
        "See: list all tasks for drive"
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
        "For quick memory-backed N4OS advice, use:\n"
        "/ask How should we approach Nysha's reading?\n"
        "/n4os How should we approach Nysha's reading?\n"
        "/coach What should I focus on this week?\n"
        "/advice How should I handle this career decision?\n\n"
        "For deeper ongoing conversation, use:\n"
        "/chat Let's think through Nysha's first week at school\n\n"
        "N4OS stores ask/chat trajectories for later review without changing stable memory automatically."
    ),
    "review": (
        "To review patterns, send:\n"
        "/review day\n"
        "/review week\n"
        "/review month\n\n"
        "Reviews suggest promotion candidates but do not change stable N4OS files."
    ),
    "decision": (
        "The Family Backlog tracks discussions, plans, and important decisions.\n\n"
        "Send one of these:\n"
        "Discuss: Discussion: Should we attend the birthday?\n"
        "Plan: Planning: Camping trip September 12\n"
        "Decide: Decision: Choose Nysha's school next year\n"
        "Review: /backlog review\n"
        "Note: add note to birthday: Niyati prefers Sunday\n"
        "Position: my position on birthday is yes\n"
        "Move: move birthday to planning, then confirm\n"
        "Add option: add option Camp A to summer camp options\n"
        "Add evidence: add evidence summer camp: Camp A costs $500\n"
        "Close: close decision 2 done\n"
        "See: list family backlog\n"
        "See: decision brief for summer camp"
    ),
    "library": (
        "Library is Nysha's Reading Garden.\n\n"
        "Send one of these:\n"
        "Add reading: Nysha read 8 pages of Mercy Watson by herself\n"
        "Finish book: Nysha finished Elephant and Piggie herself\n"
        "Checkout: library checkout: Mercy Watson, Frog and Toad, Narwhal\n"
        "Change: Change Nysha latest reading book to Frog and Toad\n"
        "Delete: Delete Nysha latest reading entry\n"
        "See: reading status\n"
        "See: /status reading"
    ),
    "science_lab": (
        "Science Lab helps plan kid-friendly experiments and materials.\n\n"
        "Send one of these:\n"
        "Plan: plan the next 4 science lab experiments\n"
        "Plan: plan 2 science experiments\n"
        "See: show science lab plan\n"
        "See: what materials do we need for science lab?"
    ),
    "home_board": (
        "Home Board is for Today at Home and before-leaving reminders.\n\n"
        "Send one of these:\n"
        "Add: add home board item buy milk\n"
        "Add: add home board item tomorrow before leaving put passports by the door\n"
        "Done: mark home board item buy milk done\n"
        "See: show home board\n"
        "See: show today at home"
    ),
    "before_leave": (
        "To add something to the portal's Before leaving section, use Home Board:\n"
        "add home board item before leaving take water bottles\n\n"
        "More examples:\n"
        "- before we leave, take jackets and snacks\n"
        "- add home board item tomorrow before leaving put passports by the door\n\n"
        "It will appear on the portal under Today at Home / Before leaving."
    ),
    "shopping": (
        "Shopping lists are Indian, Costco, Whole Foods, Amazon, and Others.\n\n"
        "Send one of these:\n"
        "Add: /cart add milk to Costco\n"
        "Add: add milk to Costco\n"
        "Cross off: /cart cross off paneer from Indian\n"
        "Move: /shop move coconut milk from Costco to Indian\n"
        "Clear done: Indian grocery done\n"
        "See: /shop Indian\n"
        "See: what's on my Whole Foods list?"
    ),
}


@dataclass(frozen=True)
class TelegramSenderProfile:
    user_id: int
    name: str
    owner: str | None


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    allowed_user_id: int | None
    allowed_user_ids: frozenset[int] | None = None
    sender_profiles: tuple[TelegramSenderProfile, ...] = ()
    voice_transcribe_command: tuple[str, ...] | None = None


@dataclass(frozen=True)
class RouterResult:
    response: str
    route: str
    action: str | None
    elapsed_ms: float


@dataclass(frozen=True)
class TelegramImageInput:
    text: str
    path: Path


@dataclass(frozen=True)
class TelegramUndoEntry:
    kind: str
    capture_result: CaptureIngestResult | None = None


TELEGRAM_CHAT_CHUNK_LIMIT = 3800


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
                                "Extract useful visible text for N4OS. If a children's book cover is visible, "
                                "prioritize the cover and return `Book title: <title>` plus `Author: <author>` "
                                "when readable. For a library receipt, return each visible title one per line. "
                                "For task/checklist images, return `List title: <title>` when visible, then "
                                "each task entry one per line. If no useful text is readable, return an empty "
                                "string. Do not include checkbox symbols, bullets, numbering, explanations, "
                                "guesses, or phrases like no visible checklist entries."
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


def _store_reading_photo(image_path: Path) -> tuple[str, Path]:
    READING_PHOTO_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = image_path.suffix or ".jpg"
    stored_path = READING_PHOTO_UPLOAD_DIR / f"{uuid4().hex}{suffix}"
    image_path.replace(stored_path)
    relative = stored_path.relative_to(ROOT / "static" / "dashboard")
    return f"/static/dashboard/{relative.as_posix()}", stored_path


def _remove_path(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        LOGGER.warning("could not remove temporary Telegram image %s", path)


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

    raw_allowed_user_ids = _env_value(env_values, "ALLOWED_TELEGRAM_USER_IDS")
    raw_allowed_user_id = _env_value(env_values, "ALLOWED_TELEGRAM_USER_ID")
    raw_allowlist = raw_allowed_user_ids or raw_allowed_user_id
    if not raw_allowlist:
        return TelegramConfig(
            token=token,
            allowed_user_id=None,
            voice_transcribe_command=voice_transcribe_command,
        )

    try:
        allowed_user_ids = frozenset(
            int(part.strip())
            for part in raw_allowlist.split(",")
            if part.strip()
        )
    except ValueError as error:
        raise RuntimeError("ALLOWED_TELEGRAM_USER_IDS must be comma-separated integers.") from error
    if not allowed_user_ids:
        raise RuntimeError("ALLOWED_TELEGRAM_USER_IDS must include at least one integer.")

    return TelegramConfig(
        token=token,
        allowed_user_id=next(iter(allowed_user_ids)),
        allowed_user_ids=allowed_user_ids,
        sender_profiles=_parse_sender_profiles(
            _env_value(env_values, "TELEGRAM_USER_PROFILES"),
        ),
        voice_transcribe_command=voice_transcribe_command,
    )


def _env_value(env_values: dict[str, str | None], key: str) -> str:
    value = env_values.get(key)
    return value.strip() if isinstance(value, str) else ""


def _parse_sender_profiles(raw: str) -> tuple[TelegramSenderProfile, ...]:
    profiles: list[TelegramSenderProfile] = []
    for entry in raw.split(","):
        parts = [part.strip() for part in entry.split(":")]
        if len(parts) < 2 or not parts[0]:
            continue
        try:
            user_id = int(parts[0])
        except ValueError as error:
            raise RuntimeError("TELEGRAM_USER_PROFILES entries must start with an integer user id.") from error
        name = re.sub(r"[^a-z0-9_-]+", "-", parts[1].lower()).strip("-")
        if not name:
            raise RuntimeError("TELEGRAM_USER_PROFILES entries must include a sender name.")
        owner = parts[2].lower() if len(parts) >= 3 and parts[2] else None
        if owner is not None and owner not in {"dad", "mom", "both", "grandmom"}:
            raise RuntimeError("TELEGRAM_USER_PROFILES owner must be dad, mom, both, or grandmom.")
        profiles.append(TelegramSenderProfile(user_id=user_id, name=name, owner=owner))
    return tuple(profiles)


def _profile_for_user(
    profiles: tuple[TelegramSenderProfile, ...],
    user_id: int | None,
) -> TelegramSenderProfile | None:
    if user_id is None:
        return None
    for profile in profiles:
        if profile.user_id == user_id:
            return profile
    return None


def _source_with_sender(source: str, profile: TelegramSenderProfile | None) -> str:
    return f"{source}:{profile.name}" if profile is not None else source


def _capture_source(profile: TelegramSenderProfile | None) -> str:
    if profile is None:
        return "Telegram"
    return f"Telegram/{profile.name.title()}"


def _telegram_how_to_reply(text: str) -> str | None:
    lowered = text.lower().strip()
    if not any(cue in lowered for cue in ("how do i", "how to", "can i", "what command", "commands", "help")):
        return None

    if "memory-status" in lowered or "status" in lowered or ("memory" in lowered and "status" in lowered):
        return HOW_TO_HELP["memory_status"]
    if "capture" in lowered or "note" in lowered or "memory" in lowered or "observation" in lowered:
        return HOW_TO_HELP["capture"]
    if "review" in lowered or "pattern" in lowered:
        return HOW_TO_HELP["review"]
    if "goal" in lowered or "priority" in lowered:
        return HOW_TO_HELP["goals"]
    if re.search(r"\b(?:n4os|ask|chat|coach|advice)\b", lowered):
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
    if "cart" in lowered or "shop" in lowered or "shopping" in lowered or "groceries" in lowered:
        return HOW_TO_HELP["shopping"]
    if any(word in lowered for word in ("decision", "backlog", "discussion", "planning")):
        return HOW_TO_HELP["decision"]
    if "home board" in lowered:
        return HOW_TO_HELP["home_board"]
    return HELP_MESSAGE


def _decision_action(decision: dict[str, Any]) -> str | None:
    action = decision.get("action")
    if isinstance(action, str) and action.strip():
        return action.strip()
    summary = decision.get("intent_summary")
    if not isinstance(summary, str):
        return None
    match = re.search(r"\bfor\s+([A-Za-z0-9_]+)\b", summary)
    return match.group(1) if match else None


def _telegram_reply_chunks(text: str, limit: int = TELEGRAM_CHAT_CHUNK_LIMIT) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return [""]
    if len(cleaned) <= limit:
        return [cleaned]

    chunks: list[str] = []
    remaining = cleaned
    while len(remaining) > limit:
        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        chunk = remaining[:split_at].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


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


def _is_active_chat_bypass_message(text: str) -> bool:
    lowered = text.strip().lower()
    return bool(
        re.match(
            r"^/?(?:capture|note|mem|memory|remember|review|status|goals|goal|event|calendar|cart|shop)\b",
            lowered,
        )
        or re.match(
            r"^(?:add|create|capture|remember|complete|finish|delete|remove|mark|schedule|cancel|move|reschedule)\b",
            lowered,
        )
        or _is_undo_message(text)
    )


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
        chat_sessions: N4OSChatSessionStore | None = None,
        n4os_root: Path | None = None,
    ) -> None:
        self.config = config
        self.claw = claw or N4OSClaw()
        self.logger = logger or LOGGER
        self.audio_transcriber = audio_transcriber or create_default_audio_transcriber(
            config.voice_transcribe_command,
        )
        self.image_text_extractor = image_text_extractor or OpenAIImageTextExtractor.from_env_or_none()
        self.undo_stack: list[TelegramUndoEntry] = []
        self.chat_sessions = chat_sessions or N4OSChatSessionStore()
        self.n4os_root = n4os_root or ROOT / "n4os"

    def route_message(
        self,
        text: str,
        *,
        source: str = "telegram_text",
        default_owner: str | None = None,
        photo_path: str | None = None,
    ) -> RouterResult:
        started = time.perf_counter()
        improved_text = improve_entered_text(text)
        output = StringIO()
        before_mutation_depth = _n4os_mutation_depth(self.claw)
        # Existing N4OS claws print their user-facing messages; keep the
        # Telegram transport thin by capturing that router output verbatim.
        with redirect_stdout(output):
            if isinstance(self.claw, N4OSClaw):
                decision = self.claw.handle_request(
                    improved_text,
                    source=source,
                    default_owner=default_owner,
                    photo_path=photo_path,
                ) or {}
            else:
                decision = self.claw.handle_request(improved_text) or {}
        if _n4os_mutation_depth(self.claw) > before_mutation_depth:
            self.undo_stack.append(TelegramUndoEntry(kind="router"))

        elapsed_ms = (time.perf_counter() - started) * 1000
        route = str(decision.get("route", "unknown"))
        action = _decision_action(decision)
        response = output.getvalue().strip()
        if not response:
            response = str(decision.get("intent_summary") or "Done.")

        return RouterResult(response=response, route=route, action=action, elapsed_ms=elapsed_ms)

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
        allowed_user_ids = self.config.allowed_user_ids
        if allowed_user_ids is None and self.config.allowed_user_id is not None:
            allowed_user_ids = frozenset({self.config.allowed_user_id})
        if not allowed_user_ids:
            return SETUP_USER_MESSAGE.format(user_id=user_id)
        if user_id not in allowed_user_ids:
            return UNAUTHORIZED_MESSAGE
        return None

    async def _extract_image_text(self, message: Any) -> TelegramImageInput | None:
        photo = _largest_photo(message)
        if photo is None:
            return None

        suffix = ".jpg"
        document = getattr(message, "document", None)
        file_name = getattr(document, "file_name", "") if document is not None else ""
        if file_name:
            guessed_suffix = Path(file_name).suffix
            if guessed_suffix:
                suffix = guessed_suffix

        with tempfile.NamedTemporaryFile(prefix="n4os-telegram-image-", suffix=suffix, delete=False) as temp:
            image_path = Path(temp.name)
        telegram_file = await photo.get_file()
        await telegram_file.download_to_drive(image_path)
        image_text = ""
        if self.image_text_extractor is not None:
            image_text = self.image_text_extractor.extract_text(image_path).strip()
        return TelegramImageInput(text=image_text, path=image_path)

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

        reply = _telegram_how_to_reply(text) or HELP_MESSAGE
        self.logger.info("chosen route=help execution_ms=0.00")
        await message.reply_text(reply)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        message = update.effective_message
        if message is None:
            return

        user_id = _effective_user_id(update)
        sender_profile = _profile_for_user(self.config.sender_profiles, user_id)
        text = (
            getattr(message, "text", None)
            or getattr(message, "caption", None)
            or ""
        ).strip()
        message_source = "telegram_text"
        image_input: TelegramImageInput | None = None
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
                message_source = "telegram_voice"
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
                image_input = await self._extract_image_text(message)
            except Exception:
                self.logger.exception("error while extracting Telegram image text")
                image_input = None
            if image_input is not None:
                message_source = "telegram_photo"
            if image_input is not None and image_input.text:
                text = _combine_text_and_image_text(text, image_input.text)

        def cleanup_image_input() -> None:
            nonlocal image_input
            if image_input is not None:
                _remove_path(image_input.path)
                image_input = None

        async def reply_chat_chunks(reply: str) -> None:
            for chunk in _telegram_reply_chunks(reply):
                await message.reply_text(chunk)

        if not text:
            cleanup_image_input()
            await message.reply_text(UNSUPPORTED_MESSAGE)
            self.logger.info("chosen route=unsupported execution_ms=0.00")
            return

        if _is_undo_message(text):
            started = time.perf_counter()
            undo_reply = self.undo_last_action()
            elapsed_ms = (time.perf_counter() - started) * 1000
            if undo_reply is not None:
                self.logger.info("chosen route=undo execution_ms=%.2f", elapsed_ms)
                cleanup_image_input()
                await message.reply_text(undo_reply)
                return

        if is_memory_status_message(text):
            target = parse_memory_status_target(text)
            self.logger.info("chosen route=memory_status target=%s execution_ms=0.00", target)
            cleanup_image_input()
            await message.reply_text(format_memory_status(target))
            return

        if is_structured_remember_message(text):
            started = time.perf_counter()
            try:
                result = remember_structured_memory(
                    text,
                    n4os_root=self.n4os_root,
                    source=_capture_source(sender_profile),
                )
            except Exception:
                elapsed_ms = (time.perf_counter() - started) * 1000
                self.logger.exception(
                    "error while remembering N4OS structured memory execution_ms=%.2f",
                    elapsed_ms,
                )
                await message.reply_text(ERROR_MESSAGE)
                return

            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.info(
                "chosen route=n4os_structured_remember kind=%s subject=%s execution_ms=%.2f",
                result.item.kind,
                result.item.subject,
                elapsed_ms,
            )
            cleanup_image_input()
            await message.reply_text(result.reply)
            return

        if is_structured_memory_query(text):
            started = time.perf_counter()
            try:
                reply = format_structured_memory_query(text, n4os_root=self.n4os_root)
            except Exception:
                elapsed_ms = (time.perf_counter() - started) * 1000
                self.logger.exception(
                    "error querying N4OS structured memory execution_ms=%.2f",
                    elapsed_ms,
                )
                await message.reply_text(ERROR_MESSAGE)
                return

            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.info("chosen route=n4os_structured_memory execution_ms=%.2f", elapsed_ms)
            cleanup_image_input()
            await message.reply_text(reply)
            return

        if is_n4os_status_message(text):
            target = parse_status_target(text)
            status_reply = format_n4os_status(target)
            if status_reply is None:
                text = "reading status"
            else:
                self.logger.info("chosen route=n4os_status target=%s execution_ms=0.00", target)
                cleanup_image_input()
                await message.reply_text(status_reply)
                return

        pending_capture_text = _pending_capture_request(self.claw, text)
        capture_text = pending_capture_text or text
        if pending_capture_text is not None or is_capture_message(capture_text):
            started = time.perf_counter()
            try:
                result = ingest_capture_notes(
                    capture_text,
                    source=_capture_source(sender_profile),
                )
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
            cleanup_image_input()
            await message.reply_text(format_capture_reply(result))
            return

        if is_goals_status_message(text):
            self.logger.info("chosen route=goals_status execution_ms=0.00")
            cleanup_image_input()
            await message.reply_text(format_goals_status())
            return

        if is_n4os_review_message(text):
            period = parse_review_period(text)
            self.logger.info("chosen route=n4os_review period=%s execution_ms=0.00", period)
            cleanup_image_input()
            await message.reply_text(format_n4os_review(period))
            return

        library_status_request = _library_status_alias(text)
        if library_status_request is not None:
            text = library_status_request

        chat_key = f"telegram:{user_id}"
        chat_control = parse_n4os_chat_control(text)
        if chat_control == "help":
            cleanup_image_input()
            await message.reply_text(HOW_TO_HELP["n4os_advice"])
            self.logger.info("chosen route=n4os_chat_help execution_ms=0.00")
            return
        if chat_control == "reset":
            self.chat_sessions.reset(chat_key)
            cleanup_image_input()
            await message.reply_text("N4OS chat context reset.")
            self.logger.info("chosen route=n4os_chat_reset execution_ms=0.00")
            return

        starts_chat = is_n4os_chat_message(text)
        continues_chat = self.chat_sessions.active(chat_key) and not _is_active_chat_bypass_message(text)
        if starts_chat or continues_chat:
            started = time.perf_counter()
            chat_request = strip_n4os_chat_prefix(text) if starts_chat else text
            if not chat_request:
                cleanup_image_input()
                await message.reply_text(HOW_TO_HELP["n4os_advice"])
                self.logger.info("chosen route=n4os_chat_help execution_ms=0.00")
                return
            chat_result = format_n4os_chat(
                chat_request,
                history=self.chat_sessions.history(chat_key),
                n4os_root=self.n4os_root,
            )
            self.chat_sessions.append(
                chat_key,
                user_text=chat_request,
                assistant_text=chat_result.reply,
            )
            record_n4os_trajectory(
                mode="chat",
                user_text=chat_request,
                assistant_text=chat_result.reply,
                context_labels=chat_result.context_labels,
                source=_source_with_sender(message_source, sender_profile),
                n4os_root=self.n4os_root,
                model=chat_result.model,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.info("chosen route=n4os_chat execution_ms=%.2f", elapsed_ms)
            cleanup_image_input()
            await reply_chat_chunks(chat_result.reply)
            return

        if is_n4os_advice_message(text):
            started = time.perf_counter()
            reply = format_n4os_advice(text, n4os_root=self.n4os_root)
            advice_context = build_n4os_advice_context(
                strip_n4os_advice_prefix(text),
                self.n4os_root,
            )
            record_n4os_trajectory(
                mode="ask",
                user_text=text,
                assistant_text=reply,
                context_labels=context_labels_from_context(advice_context),
                source=_source_with_sender(message_source, sender_profile),
                n4os_root=self.n4os_root,
                model=os.environ.get("N4OS_ADVICE_MODEL") or "gpt-5.4-mini",
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.info("chosen route=n4os_advice execution_ms=%.2f", elapsed_ms)
            cleanup_image_input()
            await message.reply_text(reply)
            return

        how_to_reply = _telegram_how_to_reply(text)
        if how_to_reply is not None:
            self.logger.info("chosen route=telegram_help execution_ms=0.00")
            cleanup_image_input()
            await message.reply_text(how_to_reply)
            return

        started = time.perf_counter()
        stored_photo_url: str | None = None
        stored_photo_file: Path | None = None
        if image_input is not None:
            stored_photo_url, stored_photo_file = _store_reading_photo(image_input.path)
            image_input = None
        try:
            result = self.route_message(
                text,
                source=_source_with_sender(message_source, sender_profile),
                default_owner=sender_profile.owner if sender_profile is not None else None,
                photo_path=stored_photo_url,
            )
        except Exception:
            _remove_path(stored_photo_file)
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.exception(
                "error while routing Telegram message execution_ms=%.2f",
                elapsed_ms,
            )
            await message.reply_text(ERROR_MESSAGE)
            return
        if result.route != "library" or result.action != "record_reading":
            _remove_path(stored_photo_file)

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

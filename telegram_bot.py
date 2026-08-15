from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from io import StringIO
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Literal
import urllib.request
import uuid

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
from claws.homework import HomeworkClaw
from claws.homework.intent import has_homework_terms, is_homework_capture
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
    MemoryItem,
    delete_structured_memory_item,
    format_structured_memory_query,
    get_structured_memory_item,
    has_structured_memory_mutation_match,
    has_structured_memory_conflict,
    has_structured_memory_query_match,
    is_structured_memory_mutation_message,
    is_structured_memory_query,
    is_structured_remember_message,
    mutate_structured_memory,
    remember_structured_memory,
    restore_structured_memory_item,
    same_structured_memory_item,
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
HOMEWORK_PHOTO_UPLOAD_DIR = ROOT / "static" / "dashboard" / "uploads" / "homework"
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
    status: str
    can_commit_media: bool
    elapsed_ms: float


@dataclass(frozen=True)
class TelegramImageInput:
    text: str
    path: Path


@dataclass(frozen=True)
class TelegramUndoEntry:
    kind: str
    capture_result: CaptureIngestResult | None = None
    structured_memory_item_id: str | None = None
    structured_memory_current_item: MemoryItem | None = None
    structured_memory_previous_item: MemoryItem | None = None


@dataclass
class TelegramConversationSession:
    claw: Any
    undo_stack: list[TelegramUndoEntry] = field(default_factory=list)
    mode: Literal["idle", "chat_active", "awaiting_clarification"] = "idle"


def _fallback_session_clone(value: Any) -> Any:
    """Clone conversation-owned state while retaining shared provider clients."""

    clone = copy.copy(value)
    for name, current in vars(value).items():
        if isinstance(current, list):
            setattr(clone, name, list(current))
        elif isinstance(current, dict):
            setattr(clone, name, dict(current))
        elif isinstance(current, set):
            setattr(clone, name, set(current))

    if isinstance(value, N4OSClaw):
        clone.pending_route_clarification = None
        clone.route_context = type(value.route_context)()
        clone.last_turn_decision = None
        clone.last_domain_status = None
        for name in (
            "calendar_claw",
            "tasks_claw",
            "shopping_claw",
            "home_board_claw",
            "decisions_claw",
            "science_lab_claw",
            "library_claw",
        ):
            owner = getattr(value, name)
            if owner is None:
                continue
            owner_clone = _fallback_session_clone(owner)
            if hasattr(owner_clone, "pending_action"):
                owner_clone.pending_action = None
            if hasattr(owner_clone, "undo_stack"):
                owner_clone.undo_stack = []
            if hasattr(owner_clone, "last_result"):
                owner_clone.last_result = None
            for selector in ("last_created_event", "last_created_task", "last_item"):
                if hasattr(owner_clone, selector):
                    setattr(owner_clone, selector, None)
            setattr(clone, name, owner_clone)
    return clone


def _clone_session_claw(claw: Any) -> Any:
    try:
        return copy.deepcopy(claw)
    except Exception:
        # API clients often cannot be deep-copied. Preserve those immutable
        # dependencies while isolating every conversation-owned mutable field.
        return _fallback_session_clone(claw)


class TelegramSessionStore:
    def __init__(self, base_claw: Any, base_undo_stack: list[TelegramUndoEntry]) -> None:
        self.base_claw = base_claw
        self.base_undo_stack = base_undo_stack
        self.prototype = _clone_session_claw(base_claw)
        self.sessions: dict[str, TelegramConversationSession] = {}

    def get(self, key: str) -> TelegramConversationSession:
        existing = self.sessions.get(key)
        if existing is not None:
            return existing
        if not self.sessions:
            session = TelegramConversationSession(
                claw=self.base_claw,
                undo_stack=self.base_undo_stack,
            )
        else:
            claw = self._new_claw()
            session = TelegramConversationSession(claw=claw)
        self.sessions[key] = session
        return session

    def _new_claw(self) -> Any:
        return _clone_session_claw(self.prototype)

    def reset(self, key: str) -> TelegramConversationSession:
        session = TelegramConversationSession(claw=self._new_claw())
        self.sessions[key] = session
        return session


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
                                "each task entry one per line. For homework sheets, return labeled lines for "
                                "`Homework title`, `Student`, `Grade`, `Week range`, `Due date`, "
                                "`Subject`, `Visible instructions`, and daily assignments such as "
                                "`Monday: ...` when readable; include parent-signature requirements. "
                                "If no useful text is readable, return an empty "
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


def _stage_reading_photo(image_path: Path) -> tuple[str, Path]:
    return _stage_dashboard_photo(image_path, READING_PHOTO_UPLOAD_DIR, "reading")


def _stage_homework_photo(image_path: Path) -> tuple[str, Path]:
    return _stage_dashboard_photo(image_path, HOMEWORK_PHOTO_UPLOAD_DIR, "homework")


def _stage_dashboard_photo(image_path: Path, upload_dir: Path, label: str) -> tuple[str, Path]:
    upload_dir.mkdir(parents=True, exist_ok=True)
    suffix = image_path.suffix or ".jpg"
    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
    for candidate in upload_dir.iterdir():
        try:
            candidate_digest = (
                hashlib.sha256(candidate.read_bytes()).hexdigest()
                if candidate.is_file()
                else None
            )
        except OSError:
            LOGGER.warning("could not inspect existing %s photo %s", label, candidate)
            continue
        if candidate_digest == digest:
            relative = candidate.relative_to(ROOT / "static" / "dashboard")
            return f"/static/dashboard/{relative.as_posix()}", candidate
    stored_path = upload_dir / f"{uuid.uuid4().hex}{suffix.lower()}"
    relative = stored_path.relative_to(ROOT / "static" / "dashboard")
    return f"/static/dashboard/{relative.as_posix()}", stored_path


def _commit_reading_photo(staged_path: Path, stored_path: Path) -> None:
    _commit_staged_photo(staged_path, stored_path)


def _commit_homework_photo(staged_path: Path, stored_path: Path) -> None:
    _commit_staged_photo(staged_path, stored_path)


def _commit_staged_photo(staged_path: Path, stored_path: Path) -> None:
    if stored_path.exists():
        _remove_path(staged_path)
        return
    staged_path.replace(stored_path)


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


def _task_owner(task: dict[str, Any] | None) -> str | None:
    if not isinstance(task, dict):
        return None
    metadata = task.get("_n4os_metadata")
    if not isinstance(metadata, dict):
        return None
    owner = metadata.get("owner")
    return str(owner) if isinstance(owner, str) and owner != "unknown" else None


def _latest_created_task(claw: Any) -> dict[str, Any] | None:
    tasks_claw = getattr(claw, "tasks_claw", None)
    task = getattr(tasks_claw, "last_created_task", None)
    return task if isinstance(task, dict) else None


def _task_assignment_message(
    task: dict[str, Any],
    *,
    assigner: TelegramSenderProfile | None,
) -> str:
    assigner_name = assigner.name.title() if assigner is not None else "Someone"
    title = str(task.get("title") or "Untitled task")
    lines = [f"{assigner_name} assigned you a task:", title]
    due = task.get("due")
    if due:
        lines.append(f"Due: {str(due)[:10]}")
    for key in ("webViewLink", "selfLink"):
        task_url = task.get(key)
        if isinstance(task_url, str) and task_url.strip():
            lines.append(f"Open: {task_url.strip()}")
            break
    return "\n".join(lines)


def _source_with_sender(source: str, profile: TelegramSenderProfile | None) -> str:
    return f"{source}:{profile.name}" if profile is not None else source


def _capture_source(profile: TelegramSenderProfile | None) -> str:
    if profile is None:
        return "Telegram"
    return f"Telegram/{profile.name.title()}"


def _telegram_how_to_reply(text: str) -> str | None:
    lowered = text.lower().strip()
    if re.match(
        r"^/(?:task|tasks|todo|todos)(?:@[a-z0-9_]+)?(?:\s+|:\s*)\S",
        lowered,
    ):
        return None
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
    if isinstance(summary, str):
        match = re.search(r"\bfor\s+([a-z][a-z0-9_]*)\.?\s*$", summary.strip())
        if match is not None:
            return match.group(1)
    return None


def _legacy_media_response_succeeded(decision: dict[str, Any]) -> bool:
    response = decision.get("response")
    if not isinstance(response, str):
        return True
    if "?" in response:
        return False
    return re.search(
        r"\b(?:failed|failure|error|unable|could not|couldn't|did not save|not counted|please provide|which|clarify)\b",
        response,
        re.I,
    ) is None


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


def _is_new_session_command(text: str) -> bool:
    normalized = " ".join(text.strip().lower().split())
    return normalized in {"/new", "/reset", "new session"}


def _pending_capture_request(claw: N4OSClaw, text: str) -> str | None:
    if CAPTURE_CLARIFICATION_RE.match(text) is None:
        return None

    pending = getattr(claw, "pending_route_clarification", None)
    request = getattr(pending, "request", None)
    return request if isinstance(request, str) and request.strip() else None


def _has_homework_pending(homework_claw: HomeworkClaw | None) -> bool:
    return bool(getattr(homework_claw, "pending_action", None))


def _homework_photo_file_from_url(photo_path: str | None) -> Path | None:
    if not photo_path or not photo_path.startswith("/static/dashboard/uploads/homework/"):
        return None
    return HOMEWORK_PHOTO_UPLOAD_DIR / Path(photo_path).name


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


def _top_undo_is_structured_memory(undo_stack: list[TelegramUndoEntry]) -> bool:
    return bool(undo_stack and undo_stack[-1].kind.startswith("structured_memory_"))


def _is_active_chat_bypass_message(text: str) -> bool:
    lowered = text.strip().lower()
    return bool(
        re.match(
            r"^/?(?:capture|note|mem|memory|remember|review|goals|goal|event|calendar|cart|shop)\b",
            lowered,
        )
        or re.match(
            r"^(?:add|create|capture|remember|complete|finish|delete|remove|mark|schedule|cancel|move|reschedule)\b",
            lowered,
        )
        or _is_undo_message(text)
    )


def _looks_like_structured_memory_probe(text: str) -> bool:
    lowered = text.strip().lower()
    return lowered.startswith("what do you remember about ") or lowered.startswith("do you remember about ")


def _looks_like_explicit_structured_memory_alias_lookup(text: str) -> bool:
    lowered = text.strip().lower()
    return bool(
        re.match(
            r"^(?:find|look\s+up|lookup|search|show(?:\s+me)?)\s+"
            r"(?:my\s+)?(?:remembered|structured)\s+(?:note|notes|memory|memories)\b.+",
            lowered,
        )
    )


def _is_high_confidence_action(claw: Any, text: str) -> bool:
    if not isinstance(claw, N4OSClaw):
        return _is_active_chat_bypass_message(text)
    frame = claw.recognize(text)
    return frame.route != "unknown" and frame.confidence >= 0.85


def _has_router_followup(claw: Any) -> bool:
    if getattr(claw, "pending_route_clarification", None) is not None:
        return True
    return any(
        getattr(getattr(claw, name, None), "pending_action", None) is not None
        for name in (
            "calendar_claw",
            "tasks_claw",
            "shopping_claw",
            "home_board_claw",
            "decisions_claw",
        )
    )


def _has_domain_pending(claw: Any) -> bool:
    return any(
        getattr(getattr(claw, name, None), "pending_action", None) is not None
        for name in (
            "calendar_claw",
            "tasks_claw",
            "shopping_claw",
            "home_board_claw",
            "decisions_claw",
        )
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
        homework_claw: HomeworkClaw | None = None,
    ) -> None:
        self.config = config
        self.claw = claw or N4OSClaw.default()
        self.logger = logger or LOGGER
        self.audio_transcriber = audio_transcriber or create_default_audio_transcriber(
            config.voice_transcribe_command,
        )
        self.image_text_extractor = image_text_extractor or OpenAIImageTextExtractor.from_env_or_none()
        self.undo_stack: list[TelegramUndoEntry] = []
        self.sessions = TelegramSessionStore(self.claw, self.undo_stack)
        self.chat_sessions = chat_sessions or N4OSChatSessionStore()
        self.n4os_root = n4os_root or ROOT / "n4os"
        self.homework_claw = homework_claw

    def _homework_claw(self) -> HomeworkClaw:
        if self.homework_claw is None:
            self.homework_claw = HomeworkClaw.default()
        return self.homework_claw

    def route_message(
        self,
        text: str,
        *,
        source: str = "telegram_text",
        default_owner: str | None = None,
        photo_path: str | None = None,
        claw: Any | None = None,
        undo_stack: list[TelegramUndoEntry] | None = None,
    ) -> RouterResult:
        started = time.perf_counter()
        active_claw = claw or self.claw
        active_undo_stack = undo_stack if undo_stack is not None else self.undo_stack
        improved_text = improve_entered_text(text)
        before_mutation_depth = _n4os_mutation_depth(active_claw)
        uses_native_router = (
            isinstance(active_claw, N4OSClaw)
            and type(active_claw).handle_request is N4OSClaw.handle_request
        )
        if uses_native_router:
            operation = active_claw.handle_turn(
                text,
                source=source,
                default_owner=default_owner,
                photo_path=photo_path,
            )
            decision = {
                "route": operation.route,
                "action": operation.action,
                "response": operation.response,
            }
            response = operation.response
            status = operation.status
            can_commit_media = status == "success"
        else:
            output = StringIO()
            with redirect_stdout(output):
                if isinstance(active_claw, N4OSClaw):
                    decision = active_claw.handle_request(
                        improved_text,
                        source=source,
                        default_owner=default_owner,
                        photo_path=photo_path,
                    ) or {}
                else:
                    decision = active_claw.handle_request(improved_text) or {}
            response = output.getvalue().strip()
            if not response:
                response = str(decision.get("response") or decision.get("intent_summary") or "Done.")
            explicit_status = str(decision.get("status") or "")
            status = (
                explicit_status
                if explicit_status in {"success", "clarification", "failure", "noop"}
                else ("clarification" if decision.get("route") == "unknown" else "success")
            )
            # Route/action was the legacy success contract. Preserve it unless
            # the compatibility router returns a structured response without
            # explicitly proving success; that shape can represent a rejected write.
            legacy_compatible_success = not explicit_status and _legacy_media_response_succeeded(decision)
            can_commit_media = explicit_status == "success" or (
                status == "success" and legacy_compatible_success
            )
        if _n4os_mutation_depth(active_claw) > before_mutation_depth:
            active_undo_stack.append(TelegramUndoEntry(kind="router"))

        elapsed_ms = (time.perf_counter() - started) * 1000
        route = str(decision.get("route", "unknown"))
        action = _decision_action(decision)

        return RouterResult(
            response=response,
            route=route,
            action=action,
            status=status,
            can_commit_media=can_commit_media,
            elapsed_ms=elapsed_ms,
        )

    def undo_last_action(
        self,
        *,
        claw: Any | None = None,
        undo_stack: list[TelegramUndoEntry] | None = None,
    ) -> str | None:
        active_claw = claw or self.claw
        active_undo_stack = undo_stack if undo_stack is not None else self.undo_stack
        if not active_undo_stack:
            return "Nothing to undo."

        entry = active_undo_stack.pop()
        if entry.kind == "capture" and entry.capture_result is not None:
            result = undo_capture_ingest(entry.capture_result)
            return format_capture_undo_reply(result)

        if entry.kind == "structured_memory_add" and entry.structured_memory_item_id is not None:
            current = get_structured_memory_item(
                entry.structured_memory_item_id,
                n4os_root=self.n4os_root,
            )
            if entry.structured_memory_previous_item is not None and not same_structured_memory_item(
                current,
                entry.structured_memory_previous_item,
            ):
                active_undo_stack.append(entry)
                return "That structured memory changed after this action, so I did not undo it."
            deleted = delete_structured_memory_item(
                entry.structured_memory_item_id,
                n4os_root=self.n4os_root,
            )
            if deleted is None:
                return "That structured memory was already gone."
            return f"Undid remembered memory: {deleted.value}."

        if entry.kind == "structured_memory_forget" and entry.structured_memory_previous_item is not None:
            current = get_structured_memory_item(
                entry.structured_memory_previous_item.id,
                n4os_root=self.n4os_root,
            )
            if current is not None:
                active_undo_stack.append(entry)
                return "That structured memory changed after this action, so I did not undo it."
            if has_structured_memory_conflict(
                entry.structured_memory_previous_item,
                n4os_root=self.n4os_root,
            ):
                active_undo_stack.append(entry)
                return "That structured memory changed after this action, so I did not undo it."
            restore_structured_memory_item(
                entry.structured_memory_previous_item,
                n4os_root=self.n4os_root,
            )
            return f"Restored structured memory: {entry.structured_memory_previous_item.value}."

        if entry.kind == "structured_memory_update" and entry.structured_memory_previous_item is not None:
            if entry.structured_memory_item_id is not None:
                current = get_structured_memory_item(
                    entry.structured_memory_item_id,
                    n4os_root=self.n4os_root,
                )
                if entry.structured_memory_current_item is not None:
                    if not same_structured_memory_item(current, entry.structured_memory_current_item):
                        active_undo_stack.append(entry)
                        return "That structured memory changed after this action, so I did not undo it."
                elif same_structured_memory_item(current, entry.structured_memory_previous_item):
                    return "That structured memory was already restored."
            if has_structured_memory_conflict(
                entry.structured_memory_previous_item,
                n4os_root=self.n4os_root,
            ):
                active_undo_stack.append(entry)
                return "That structured memory changed after this action, so I did not undo it."
            restore_structured_memory_item(
                entry.structured_memory_previous_item,
                n4os_root=self.n4os_root,
            )
            return f"Restored structured memory: {entry.structured_memory_previous_item.value}."

        if entry.kind == "router":
            output = StringIO()
            with redirect_stdout(output):
                decision = active_claw.handle_request("undo") or {}
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

        session_key = _conversation_key(update, user_id)
        session = self.sessions.get(session_key)

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

        if (
            _is_undo_message(text)
            and (_has_domain_pending(session.claw) or _has_homework_pending(self.homework_claw))
            and not _top_undo_is_structured_memory(session.undo_stack)
        ):
            text = "cancel"
        elif _is_undo_message(text):
            started = time.perf_counter()
            undo_reply = self.undo_last_action(
                claw=session.claw,
                undo_stack=session.undo_stack,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.info("chosen route=undo execution_ms=%.2f", elapsed_ms)
            session.mode = "idle"
            cleanup_image_input()
            await message.reply_text(undo_reply)
            return

        if is_memory_status_message(text):
            target = parse_memory_status_target(text)
            self.logger.info("chosen route=memory_status target=%s execution_ms=0.00", target)
            cleanup_image_input()
            await message.reply_text(format_memory_status(target))
            return

        structured_memory_mutation = is_structured_memory_mutation_message(text)
        if not structured_memory_mutation:
            try:
                structured_memory_mutation = has_structured_memory_mutation_match(
                    text,
                    n4os_root=self.n4os_root,
                )
            except Exception:
                self.logger.exception("error probing N4OS structured memory mutation")
                structured_memory_mutation = False

        if structured_memory_mutation:
            started = time.perf_counter()
            try:
                result = mutate_structured_memory(text, n4os_root=self.n4os_root)
            except Exception:
                elapsed_ms = (time.perf_counter() - started) * 1000
                self.logger.exception(
                    "error mutating N4OS structured memory execution_ms=%.2f",
                    elapsed_ms,
                )
                await message.reply_text(ERROR_MESSAGE)
                return

            if result.previous_item is not None and result.item is None:
                session.undo_stack.append(
                    TelegramUndoEntry(
                        kind="structured_memory_forget",
                        structured_memory_previous_item=result.previous_item,
                    ),
                )
            elif result.previous_item is not None and result.item is not None:
                session.undo_stack.append(
                    TelegramUndoEntry(
                        kind="structured_memory_update",
                        structured_memory_item_id=result.item.id,
                        structured_memory_current_item=result.item,
                        structured_memory_previous_item=result.previous_item,
                    ),
                )
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.info("chosen route=n4os_structured_memory_mutation execution_ms=%.2f", elapsed_ms)
            cleanup_image_input()
            await message.reply_text(result.reply)
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

            session.undo_stack.append(
                TelegramUndoEntry(
                    kind="structured_memory_add",
                    structured_memory_item_id=result.item.id,
                    structured_memory_previous_item=result.item,
                ),
            )
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

        active_chat_continuation = self.chat_sessions.active(session_key) and not _has_router_followup(
            session.claw
        ) and not (
            _is_active_chat_bypass_message(text)
            or _is_high_confidence_action(session.claw, text)
        )
        structured_memory_query = is_structured_memory_query(text)
        explicit_structured_memory_lookup = _looks_like_explicit_structured_memory_alias_lookup(text)
        if (
            structured_memory_query
            or explicit_structured_memory_lookup
            or not active_chat_continuation
            or _looks_like_structured_memory_probe(text)
        ):
            started = time.perf_counter()
            if explicit_structured_memory_lookup:
                structured_memory_query = True
            if not structured_memory_query:
                try:
                    structured_memory_query = has_structured_memory_query_match(
                        text,
                        n4os_root=self.n4os_root,
                    )
                except Exception:
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    self.logger.exception(
                        "error probing N4OS structured memory execution_ms=%.2f",
                        elapsed_ms,
                    )
                    structured_memory_query = False

            if structured_memory_query:
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

        pending_capture_text = _pending_capture_request(session.claw, text)
        capture_text = pending_capture_text or text
        homework_pending = _has_homework_pending(self.homework_claw)
        if homework_pending or is_homework_capture(capture_text) or (has_image and has_homework_terms(capture_text)):
            started = time.perf_counter()
            staged_photo_file: Path | None = None
            stored_photo_url: str | None = None
            stored_photo_file: Path | None = None
            photo_sha256: str | None = None
            if image_input is not None:
                staged_photo_file = image_input.path
                photo_sha256 = hashlib.sha256(staged_photo_file.read_bytes()).hexdigest()
                stored_photo_url, stored_photo_file = _stage_homework_photo(staged_photo_file)
                image_input = None
            try:
                homework_claw = self._homework_claw()
                reply = homework_claw.capture_from_request(
                    capture_text,
                    source=_source_with_sender(message_source, sender_profile),
                    photo_path=stored_photo_url,
                    photo_sha256=photo_sha256,
                )
            except Exception:
                _remove_path(staged_photo_file)
                elapsed_ms = (time.perf_counter() - started) * 1000
                self.logger.exception(
                    "error while capturing homework execution_ms=%.2f",
                    elapsed_ms,
                )
                await message.reply_text(ERROR_MESSAGE)
                return

            last_result = self._homework_claw().last_result or {}
            last_data = last_result.get("data") if isinstance(last_result.get("data"), dict) else {}
            keep_for_pending = last_result.get("status") == "needs_information" and _has_homework_pending(
                self.homework_claw
            )
            if (
                (last_result.get("status") == "ok" or keep_for_pending)
                and staged_photo_file is not None
                and stored_photo_file is not None
            ):
                _commit_homework_photo(staged_photo_file, stored_photo_file)
            else:
                _remove_path(staged_photo_file)
            cleanup_path = _homework_photo_file_from_url(str(last_data.get("cleanup_photo_path") or ""))
            if cleanup_path is not None:
                _remove_path(cleanup_path)
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.info("chosen route=homework execution_ms=%.2f", elapsed_ms)
            if pending_capture_text is not None:
                setattr(session.claw, "pending_route_clarification", None)
            session.mode = "idle"
            await message.reply_text(reply)
            return

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
                setattr(session.claw, "pending_route_clarification", None)
            if result.family.added or result.journal_entries:
                session.undo_stack.append(
                    TelegramUndoEntry(kind="capture", capture_result=result),
                )
            session.mode = "idle"
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

        chat_key = session_key
        chat_control = parse_n4os_chat_control(text)
        if chat_control == "help":
            cleanup_image_input()
            await message.reply_text(HOW_TO_HELP["n4os_advice"])
            self.logger.info("chosen route=n4os_chat_help execution_ms=0.00")
            return
        if chat_control == "reset" or _is_new_session_command(text):
            self.chat_sessions.reset(chat_key)
            self.sessions.reset(chat_key)
            cleanup_image_input()
            await message.reply_text("Started a new N4OS session.")
            self.logger.info("chosen route=n4os_session_reset execution_ms=0.00")
            return

        starts_chat = is_n4os_chat_message(text)
        continues_chat = self.chat_sessions.active(chat_key) and not _has_router_followup(
            session.claw
        ) and not (
            _is_active_chat_bypass_message(text)
            or _is_high_confidence_action(session.claw, text)
        )
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
            session.mode = "chat_active"
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
        staged_photo_file: Path | None = None
        if image_input is not None:
            staged_photo_file = image_input.path
            stored_photo_url, stored_photo_file = _stage_reading_photo(staged_photo_file)
            image_input = None
        try:
            prior_task = _latest_created_task(session.claw)
            prior_task_id = prior_task.get("id") if prior_task is not None else None
            prior_task_owner = _task_owner(prior_task)
            result = self.route_message(
                text,
                source=_source_with_sender(message_source, sender_profile),
                default_owner=sender_profile.owner if sender_profile is not None else None,
                photo_path=stored_photo_url,
                claw=session.claw,
                undo_stack=session.undo_stack,
            )
        except Exception:
            _remove_path(staged_photo_file)
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.exception(
                "error while routing Telegram message execution_ms=%.2f",
                elapsed_ms,
            )
            await message.reply_text(ERROR_MESSAGE)
            return
        if (
            result.route == "library"
            and result.action == "record_reading"
            and result.status == "success"
            and result.can_commit_media
            and staged_photo_file is not None
            and stored_photo_file is not None
        ):
            _commit_reading_photo(staged_photo_file, stored_photo_file)
        else:
            _remove_path(staged_photo_file)

        session.mode = "awaiting_clarification" if result.route == "unknown" else "idle"

        self.logger.info(
            "chosen route=%s execution_ms=%.2f",
            result.route,
            result.elapsed_ms,
        )
        await message.reply_text(result.response)
        assigned_task = _latest_created_task(session.claw)
        assigned_owner = _task_owner(assigned_task)
        is_new_assignment = (
            result.route == "tasks"
            and result.status == "success"
            and assigned_task is not None
            and assigned_owner is not None
            and (
                assigned_task.get("id") != prior_task_id
                or assigned_owner != prior_task_owner
            )
        )
        if not is_new_assignment:
            return
        telegram = getattr(context, "bot", None)
        send_message = getattr(telegram, "send_message", None)
        if not callable(send_message):
            return
        notification = _task_assignment_message(assigned_task, assigner=sender_profile)
        for profile in self.config.sender_profiles:
            if profile.owner == assigned_owner and profile.user_id != user_id:
                try:
                    await send_message(chat_id=profile.user_id, text=notification)
                except Exception:
                    # The task write already succeeded; a transient Telegram
                    # delivery failure must not make the original request fail.
                    self.logger.exception(
                        "failed task assignment notification recipient=%s",
                        profile.user_id,
                    )


def _conversation_key(update: Update, user_id: int | None) -> str:
    chat = getattr(update, "effective_chat", None)
    chat_id = getattr(chat, "id", None)
    if chat_id is not None and user_id is not None:
        return f"telegram:{chat_id}:{user_id}"
    identity = chat_id if chat_id is not None else user_id
    return f"telegram:{identity if identity is not None else 'unknown'}"


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

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from datetime import date
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
from claws.n4os.routing_contracts import ROUTE_SPECS
from claws.homework import HomeworkClaw
from claws.homework.intent import has_homework_terms, is_homework_capture
from claws.school_coach import CoachProvenance, SchoolCoachClaw
from claws.school_coach.claw import is_school_coach_message
from claws.n4os.input_normalizer import improve_entered_text
from claws.n4os.note_capture import capture_note as capture_markdown_note
from claws.n4os.school_newsletter import (
    SchoolNewsletterImporter,
    is_school_newsletter_followup,
    is_school_newsletter_message,
)
from claws.n4os.second_brain_importer import (
    SecondBrainImporter,
    SecondBrainImportUserError,
    is_second_brain_import_followup,
    is_second_brain_import_message,
)
from n4os_capture import (
    CaptureIngestResult,
    count_capture_notes,
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
    looks_like_natural_structured_memory_query,
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
    format_n4os_knowledge_preview,
    format_n4os_reasoning_preview,
    generate_n4os_advice,
    is_n4os_advice_message,
)
from n4os_chat import (
    N4OSChatSessionStore,
    format_n4os_chat,
    is_n4os_chat_message,
    parse_n4os_chat_control,
    strip_n4os_chat_prefix,
)
from n4os_research import (
    RESEARCH_HELP_MESSAGE,
    format_n4os_research_setup,
    format_n4os_research_sources,
    generate_n4os_research,
    is_n4os_research_message,
    parse_n4os_research_request,
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
    "1. Remember: /remember Nysha school gate code is 4812; /capture Nysha was nervous about school\n"
    "2. Ask, chat, or research: /ask How should I approach Nysha's reading?; /chat Let's think through school; /research compare current options\n"
    "3. Review/status: /review week, /status Nysha, /status reading, /goals\n"
    "4. Calendar/tasks/homework: /event create dinner with Rahul next Tuesday at 7 PM; add task call FUSD tomorrow morning; /homework help\n"
    "5. Day plan: give me today's briefing\n"
    "6. Shopping: /cart add milk to Costco; /shop Indian\n"
    "7. Reading: Nysha read 8 pages of Mercy Watson by herself; reading status\n"
    "8. Science: plan the next 4 science lab experiments\n"
    "9. Imports: /import second brain <link> Instructions: use this as reusable N4OS context; /import school newsletter for Nysha <Google Slides link>\n"
    "10. Backlog/home: Discussion: Should we attend the birthday?; Planning: Camping trip September 12; Decision: Choose Nysha's school next year; add home board item buy milk\n"
    "11. Quick notes: /note quick Patrick Collison: learning still matters; school coach: say 'school coach' or send /school_coach\n\n"
    "More help: /remember help, /task help, /shop help, /help school coach, or ask how do I add a memory? how do I use shopping?"
)
ERROR_MESSAGE = "Sorry, N4OS hit an error while handling that."
UNSUPPORTED_MESSAGE = "Please send a text or voice message."
IMAGE_TEXT_MARKER = "Image text:"
OPENAI_IMAGE_TEXT_MODEL = "gpt-5.4-mini"
OPENAI_HELP_MODEL = "gpt-5.4-mini"
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
CAPTURE_CORRECTION_HISTORY_LIMIT = 10
TRACE_HISTORY_LIMIT = 8
TRACE_FEEDBACK_RE = re.compile(
    r"^\s*capture\s*:\s*(?P<body>.+)$",
    re.IGNORECASE | re.DOTALL,
)
FIX_LAST_CAPTURE_RE = re.compile(
    r"^\s*(?:fix|correct|edit|update)\s+last\s+capture\s*[:,-]\s*(?P<body>.+)$",
    re.IGNORECASE | re.DOTALL,
)
REPLY_CAPTURE_FIX_RE = re.compile(
    r"^\s*(?:fix|correct|edit|update)\s*:\s*(?P<body>.+)$",
    re.IGNORECASE | re.DOTALL,
)
REPLACE_CAPTURE_TEXT_RE = re.compile(
    r"""^\s*replace\s+["'“”‘’](?P<old>.+?)["'“”‘’]\s+with\s+["'“”‘’](?P<new>.+?)["'“”‘’]\s*$""",
    re.IGNORECASE | re.DOTALL,
)
NOT_CAPTURE_TEXT_RE = re.compile(
    r"^\s*(?P<new>.+?)\s*,\s*not\s+(?P<old>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
ARROW_CAPTURE_TEXT_RE = re.compile(
    r"^\s*(?P<old>.+?)\s*->\s*(?P<new>.+?)\s*$",
    re.DOTALL,
)
CAPTURE_URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
CAPTURE_DATE_LINE_RE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}(?:\s*[:|-])?\s*$")
MARKDOWN_NOTE_CAPTURE_RE = re.compile(
    r"^\s*/(?:note|mem|mem-inbox|capture)(?:@\w+)?\s+"
    r"(?:quick|learning|inbox)\b",
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
        "\n\nTo fix a single saved capture note, send:\n"
        "- fix last capture: Dimage, not damage\n"
        "- Reply to the bot's Captured. message with: replace \"old\" with \"new\"\n\n"
        "For batch captures, send undo and resend the corrected capture."
        "\n\nFor learning snippets that should go to Obsidian Markdown, send:\n"
        "/note quick Patrick Collison: learning still matters"
    ),
    "remember": (
        "Structured memory is for small facts you may need to look up later.\n\n"
        "Use cases:\n"
        "- Codes: /remember Nysha school gate code is 4812\n"
        "- Health/safety: /remember Navya is allergic to cashews\n"
        "- Family logistics: /remember Niyati has the next dinner pickup\n\n"
        "Find and maintain:\n"
        "- Recent: /remember recent\n"
        "- Recent: /remember last 7 days\n"
        "- Look up: What do you remember about Nysha school gate code?\n"
        "- Update: update remembered note Nysha school gate code to 9999\n"
        "- Forget: forget remembered note Nysha school gate code\n"
        "- Undo last memory change: undo\n\n"
        "Where it goes: data/n4os.db in the n4os_memory_items table."
    ),
    "note": (
        "Notes are for ideas, learning snippets, and rough inbox items you want saved to Markdown.\n\n"
        "Use cases:\n"
        "- Quick learning: /note quick Patrick Collison: learning still matters\n"
        "- Longer learning note: /note learning Book idea: agency compounds\n"
        "- Inbox item to sort later: /note inbox Review this school idea later\n\n"
        "Where they go:\n"
        "- quick notes -> n4os/learnings/Quick Notes.md\n"
        "- learning notes -> n4os/learnings/YYYY-MM-DD-<title>.md\n"
        "- inbox notes -> n4os/learnings/Inbox.md\n\n"
        "Use /remember help for facts you want to search later, like codes, allergies, or pickup turns."
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
        "Noah help: add task research weekend trips. Need Noah assistant help.\n"
        "Run Noah help: Run Noah assistant help\n"
        "Done: complete task call FUSD\n"
        "Delete: delete task call FUSD\n"
        "See: show urgent tasks due this week\n"
        "See: list all tasks for drive"
    ),
    "homework": (
        "Homework captures assignments, due dates, worksheet photos, and submissions.\n\n"
        "Use cases:\n"
        "- Assignment: /capture homework Nysha math due Friday\n"
        "- Photo/OCR: send a homework photo with caption /capture homework Nysha\n"
        "- Complete with photo: /homework complete art class Nysha\n"
        "- Submission: /capture submitted homework Nysha All About Me\n"
        "- Status: homework status\n"
        "- Cancel a pending duplicate prompt: cancel\n\n"
        "Where it goes: n4os/homework/*.md plus homework records in the local homework SQLite store."
    ),
    "school_newsletter": (
        "School newsletter imports pull useful school dates and notes from a newsletter link.\n\n"
        "Send:\n"
        "/import school newsletter for Nysha <Google Slides link>\n\n"
        "N4OS previews the changes first. Reply save to apply them or cancel to drop the import."
    ),
    "school_coach": (
        "The school coach keeps an evidence-backed plan for teacher and school relationships.\n\n"
        "Say or send:\n"
        "School coach, focus on Mrs. Thompson.\n"
        "School coach, what's your current plan for Mrs. Thompson?\n"
        "Ask the school coach why it thinks that.\n\n"
        "You can also use /school_coach. Spoken follow-ups continue in school-coach mode until you start another action or a new session."
    ),
    "second_brain_import": (
        "Second brain imports turn a file, link, document, or pasted source into reusable N4OS Markdown.\n\n"
        "Send:\n"
        "/import second brain <link>\n"
        "Instructions: This is Nysha's Back-to-School guide. Use it to explain what she is learning, design prep material, and create conversation starters.\n\n"
        "N4OS previews the file plan first. Reply save to write the Markdown files, adjust: <changes> to revise it, or cancel to drop the import."
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
        "For current source-backed research, use:\n"
        "/research What are the current recommendations?\n"
        "/research fast <question>\n"
        "/research balanced <question>\n"
        "/research deep <question>\n\n"
        "Send /research help to choose the right mode.\n\n"
        "Before each answer, N4OS shows the knowledge selected and a concise high-level reasoning summary. "
        "Reply to any transparency message with capture: <feedback> to save linked tuning feedback.\n\n"
        "N4OS stores ask/chat/research trajectories for later review without changing stable memory automatically."
    ),
    "n4os_research": RESEARCH_HELP_MESSAGE,
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
    capture_current_text: str | None = None
    capture_restore_text: str | None = None
    capture_restore_editable_text: str | None = None
    capture_restore_source: str | None = None
    capture_restore_default_date: date | None = None
    capture_restore_reply_message_ids: tuple[int, ...] = ()
    capture_restore_undo_entry: TelegramUndoEntry | None = None
    structured_memory_item_id: str | None = None
    structured_memory_current_item: MemoryItem | None = None
    structured_memory_previous_item: MemoryItem | None = None


@dataclass(frozen=True)
class TelegramRecentCapture:
    text: str
    source: str
    result: CaptureIngestResult
    stable_text: str | None = None
    reply_message_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class TelegramRecentTrace:
    mode: Literal["ask", "chat", "research"]
    question: str
    context_labels: tuple[str, ...]
    reasoning_summary: str
    reply_message_ids: tuple[int, ...]


@dataclass
class TelegramConversationSession:
    claw: Any
    undo_stack: list[TelegramUndoEntry] = field(default_factory=list)
    recent_captures: list[TelegramRecentCapture] = field(default_factory=list)
    recent_traces: list[TelegramRecentTrace] = field(default_factory=list)
    mode: Literal["idle", "chat_active", "school_coach_active", "awaiting_clarification"] = "idle"


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
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

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
            "store": False,
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
                                "each task entry one per line. For calendar, class, appointment, or schedule "
                                "tables, preserve the table columns and return one row per visible scheduled "
                                "entry with the date, day, start time, end time when visible, location/school, "
                                "and title or activity. For homework sheets, return labeled lines for "
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


HELP_CONVERSATION_CONTROLS: tuple[dict[str, Any], ...] = (
    {
        "topic": "session",
        "commands": ["/new", "/reset", "new session"],
        "purpose": "Start a new N4OS conversation session and clear current router state.",
    },
    {
        "topic": "chat session",
        "commands": ["/chat reset", "/chat stop", "/chat clear"],
        "purpose": "Clear rich /chat history and start fresh.",
    },
    {
        "topic": "chat help",
        "commands": ["/chat help"],
        "purpose": "Show rich chat usage help.",
    },
)
HELP_FORBIDDEN_OUTPUT_RE = re.compile(r"(\*\*|###|Loaded:|\bn4os/|data/n4os\.db)", re.IGNORECASE)


def _build_help_catalog() -> dict[str, Any]:
    route_commands = [
        {
            "route": spec.route,
            "label": spec.label,
            "command_aliases": [f"/{alias}" for alias in spec.command_aliases],
            "actions": sorted(spec.actions),
            "mutating_actions": sorted(spec.mutating_actions),
        }
        for spec in ROUTE_SPECS
        if spec.command_aliases
    ]
    topics = [
        {
            "topic": key,
            "aliases": sorted(alias for alias, target in HELP_TOPIC_ALIASES.items() if target == key),
            "help": value,
        }
        for key, value in HOW_TO_HELP.items()
    ]
    return {
        "general_help": HELP_MESSAGE,
        "topics": topics,
        "route_commands": route_commands,
        "conversation_controls": list(HELP_CONVERSATION_CONTROLS),
    }


def _clean_ai_help_answer(value: str) -> str | None:
    cleaned_lines = [" ".join(line.split()) for line in value.strip().splitlines()]
    cleaned = "\n".join(line for line in cleaned_lines if line).strip()
    if not cleaned:
        return None
    if len(cleaned.splitlines()) > 6 or len(cleaned) > 900:
        return None
    if HELP_FORBIDDEN_OUTPUT_RE.search(cleaned):
        return None
    return cleaned


class OpenAIN4OSHelpAnswerer:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = OPENAI_HELP_MODEL,
        timeout_seconds: int = 8,
        urlopen: Any = urllib.request.urlopen,
    ) -> None:
        cleaned_key = api_key.strip()
        if not cleaned_key:
            raise RuntimeError("N4OS help answering needs OPENAI_API_KEY.")
        self.api_key = cleaned_key
        self.model = model.strip() or OPENAI_HELP_MODEL
        self.timeout_seconds = timeout_seconds
        self.urlopen = urlopen

    @classmethod
    def from_env_or_none(cls) -> "OpenAIN4OSHelpAnswerer | None":
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            return None
        return cls(
            api_key=api_key,
            model=os.environ.get("N4OS_HELP_MODEL", OPENAI_HELP_MODEL),
        )

    def answer(self, question: str) -> str | None:
        cleaned_question = question.strip()
        if not cleaned_question:
            return None

        payload = {
            "model": self.model,
            "store": False,
            "max_output_tokens": 220,
            "input": [
                {
                    "role": "system",
                    "content": (
                        "You answer N4OS Telegram command-help questions. Use only the supplied catalog. "
                        "Do not invent commands, execute actions, mention internal files, or include Markdown. "
                        "Reply in plain text for a phone chat, 1-5 short lines. If the catalog does not answer "
                        "the question, say: I do not know that command yet. Try /help."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": cleaned_question,
                            "catalog": _build_help_catalog(),
                        },
                        sort_keys=True,
                    ),
                },
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
        with self.urlopen(request, timeout=self.timeout_seconds) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
        return _clean_ai_help_answer(_extract_response_text(response_payload))


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


def _n4os_capture_source(message_source: str, profile: TelegramSenderProfile | None) -> str:
    if message_source == "telegram_text":
        return _capture_source(profile)
    return _source_with_sender(message_source, profile)


HELP_TOPIC_ALIASES = {
    "advice": "n4os_advice",
    "ask": "n4os_advice",
    "backlog": "decision",
    "calendar": "event",
    "calender": "event",
    "calnedar": "event",
    "capture": "capture",
    "cart": "shopping",
    "chat": "n4os_advice",
    "coach": "n4os_advice",
    "decision": "decision",
    "decisions": "decision",
    "event": "event",
    "experiments": "science_lab",
    "goals": "goals",
    "home": "home_board",
    "home-board": "home_board",
    "homeboard": "home_board",
    "homework": "homework",
    "import": "second_brain_import",
    "library": "library",
    "mem-inbox": "note",
    "memory": "remember",
    "memory-status": "memory_status",
    "note": "note",
    "notes": "note",
    "n4os": "n4os_advice",
    "remember": "remember",
    "review": "review",
    "research": "n4os_research",
    "schedule": "event",
    "school": "school_newsletter",
    "school coach": "school_coach",
    "school-coach": "school_coach",
    "school_coach": "school_coach",
    "school-newsletter": "school_newsletter",
    "second-brain": "second_brain_import",
    "science": "science_lab",
    "science-lab": "science_lab",
    "shop": "shopping",
    "shopping": "shopping",
    "status": "memory_status",
    "task": "task",
    "tasks": "task",
    "todo": "task",
    "todos": "task",
}


def _help_topic_reply(topic: str) -> str | None:
    key = HELP_TOPIC_ALIASES.get(topic.lower().strip())
    return HOW_TO_HELP.get(key) if key is not None else None


def _ai_help_reply(text: str, help_answerer: Any | None) -> str | None:
    if help_answerer is None:
        return None
    try:
        answer = help_answerer.answer(text)
    except Exception:
        return None
    return answer if isinstance(answer, str) and answer.strip() else None


def _telegram_slash_help_reply(text: str, help_answerer: Any | None = None) -> str | None:
    match = re.match(
        r"^\s*/(?P<command>[a-z][a-z0-9_-]*)(?:@[a-z0-9_]+)?(?:\s+(?P<body>.*?))?\s*$",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None

    command = match.group("command").lower()
    body = (match.group("body") or "").strip()
    if command in {"help", "start"}:
        if not body:
            return HELP_MESSAGE
        return _help_topic_reply(body) or _ai_help_reply(body, help_answerer) or HELP_MESSAGE

    normalized_body = " ".join(body.lower().split())
    if normalized_body in {"help", "-h", "--help", "?"}:
        return _help_topic_reply(command)
    if not normalized_body.startswith("help "):
        return None
    return _help_topic_reply(command)


def _telegram_how_to_reply(text: str, help_answerer: Any | None = None) -> str | None:
    slash_help = _telegram_slash_help_reply(text, help_answerer)
    if slash_help is not None:
        return slash_help

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
    if "homework" in lowered:
        return HOW_TO_HELP["homework"]
    if "school newsletter" in lowered:
        return HOW_TO_HELP["school_newsletter"]
    if "remember" in lowered or "structured memory" in lowered:
        return HOW_TO_HELP["remember"]
    if "quick note" in lowered or re.search(r"\b(?:markdown|obsidian)\s+note\b", lowered):
        return HOW_TO_HELP["note"]
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
    return _ai_help_reply(text, help_answerer) or HELP_MESSAGE


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


def _is_markdown_note_capture(text: str) -> bool:
    return MARKDOWN_NOTE_CAPTURE_RE.match(text) is not None


def _markdown_note_body(text: str) -> str:
    return re.sub(
        r"^\s*/(?:note|mem|mem-inbox|capture)(?:@\w+)?\s+",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    ).strip()


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


def _message_id(value: Any) -> int | None:
    message_id = getattr(value, "message_id", None)
    return message_id if isinstance(message_id, int) else None


def _reply_to_message_id(message: Any) -> int | None:
    return _message_id(getattr(message, "reply_to_message", None))


def _capture_reply_target(
    session: TelegramConversationSession,
    reply_message_id: int | None,
) -> TelegramRecentCapture | None:
    if reply_message_id is None:
        return None
    for capture in reversed(session.recent_captures):
        if reply_message_id in capture.reply_message_ids:
            return capture
    return None


def _trace_reply_target(
    session: TelegramConversationSession,
    reply_message_id: int | None,
) -> TelegramRecentTrace | None:
    if reply_message_id is None:
        return None
    for trace in reversed(session.recent_traces):
        if reply_message_id in trace.reply_message_ids:
            return trace
    return None


def _latest_capture(session: TelegramConversationSession) -> TelegramRecentCapture | None:
    return session.recent_captures[-1] if session.recent_captures else None


def _capture_correction_target(
    text: str,
    message: Any,
    session: TelegramConversationSession,
) -> tuple[TelegramRecentCapture | None, str] | None:
    reply_target = _capture_reply_target(session, _reply_to_message_id(message))
    last_match = FIX_LAST_CAPTURE_RE.match(text)
    if last_match:
        if _reply_to_message_id(message) is not None and reply_target is None:
            return (None, last_match.group("body").strip())
        return (reply_target or _latest_capture(session), last_match.group("body").strip())

    if reply_target is None:
        return None

    reply_match = REPLY_CAPTURE_FIX_RE.match(text)
    if reply_match:
        return (reply_target, reply_match.group("body").strip())
    if _looks_like_reply_capture_correction(text):
        return (reply_target, text.strip())
    return None


def _looks_like_reply_capture_correction(text: str) -> bool:
    stripped = text.strip()
    return bool(
        REPLACE_CAPTURE_TEXT_RE.match(stripped)
        or ARROW_CAPTURE_TEXT_RE.match(stripped)
    )


def _looks_like_capture_correction(text: str) -> bool:
    stripped = text.strip()
    return bool(
        REPLACE_CAPTURE_TEXT_RE.match(stripped)
        or NOT_CAPTURE_TEXT_RE.match(stripped)
        or ARROW_CAPTURE_TEXT_RE.match(stripped)
    )


def _apply_capture_correction(text: str, instruction: str) -> tuple[str | None, str | None]:
    updated = text
    for raw_part in _split_capture_correction_parts(instruction):
        part = raw_part.strip()
        if not part:
            continue
        old, new = _capture_replacement_pair(part)
        if old is None or new is None:
            return (None, "I could not understand that correction. Use `replace \"old\" with \"new\"` or `right text, not wrong text`.")
        next_text = _replace_capture_text(updated, old, new)
        if next_text == updated:
            return (None, f'I could not find "{old}" in the saved capture.')
        updated = next_text

    if updated == text:
        return (None, "I could not understand that correction. Use `fix last capture: right text, not wrong text`.")
    return (updated, None)


def _split_capture_correction_parts(instruction: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    matching_quotes = {"“": "”", "‘": "’"}
    for index, char in enumerate(instruction):
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"\"", "“"} or (
            char in {"'", "‘"}
            and (index == 0 or instruction[index - 1].isspace())
        ):
            quote = matching_quotes.get(char, char)
            current.append(char)
            continue
        if char == ";":
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return parts


def _capture_replacement_pair(instruction: str) -> tuple[str | None, str | None]:
    replace_match = REPLACE_CAPTURE_TEXT_RE.match(instruction)
    if replace_match:
        return (_clean_capture_replacement(replace_match.group("old")), _clean_capture_replacement(replace_match.group("new")))

    not_match = NOT_CAPTURE_TEXT_RE.match(instruction)
    if not_match:
        return (_clean_capture_replacement(not_match.group("old")), _clean_capture_replacement(not_match.group("new")))

    arrow_match = ARROW_CAPTURE_TEXT_RE.match(instruction)
    if arrow_match:
        return (_clean_capture_replacement(arrow_match.group("old")), _clean_capture_replacement(arrow_match.group("new")))

    return (None, None)


def _clean_capture_replacement(value: str) -> str:
    return value.strip().strip("\"'“”‘’").strip()


def _replace_capture_text(text: str, old: str, new: str) -> str:
    if not old:
        return text
    return re.sub(re.escape(old), lambda _: new, text, flags=re.IGNORECASE)


def _prepare_corrected_capture_text(replay_text: str, corrected_text: str) -> tuple[str, bool]:
    before_urls = _capture_main_text_urls(replay_text)
    after_urls = _capture_main_text_urls(corrected_text)
    if before_urls != after_urls:
        return (_apply_unchanged_capture_previews(corrected_text, replay_text), True)
    return (_apply_stable_capture_previews(corrected_text, replay_text), False)


def _capture_main_text_urls(text: str) -> tuple[str, ...]:
    stripped = _strip_capture_link_previews(text)
    urls = [match.group(0).rstrip(".,;:!?)]}'\"") for match in CAPTURE_URL_RE.finditer(stripped)]
    return tuple(_dedupe_text_values(urls))


def _strip_capture_link_previews(text: str) -> str:
    return "\n".join(_strip_capture_line_link_previews(line) for line in text.splitlines())


def _strip_capture_line_link_previews(line: str) -> str:
    match = re.search(r"\s+\[Link:\s+https?://", line, flags=re.IGNORECASE)
    return line[: match.start()] if match else line


def _apply_stable_capture_previews(corrected_text: str, stable_text: str) -> str:
    stable_lines = stable_text.splitlines()
    stable_index = 0
    updated_lines: list[str] = []
    for line in corrected_text.splitlines():
        if _is_capture_control_line(line):
            updated_lines.append(line)
            continue
        while stable_index < len(stable_lines) and _is_capture_control_line(stable_lines[stable_index]):
            stable_index += 1
        stable_line = stable_lines[stable_index] if stable_index < len(stable_lines) else ""
        stable_index += 1
        preview_suffix = _capture_link_preview_suffix(stable_line)
        updated_lines.append(_strip_capture_link_previews(line) + preview_suffix)
    return "\n".join(updated_lines)


def _apply_unchanged_capture_previews(corrected_text: str, stable_text: str) -> str:
    stable_lines = stable_text.splitlines()
    stable_index = 0
    updated_lines: list[str] = []
    for line in corrected_text.splitlines():
        if _is_capture_control_line(line):
            updated_lines.append(line)
            continue
        while stable_index < len(stable_lines) and _is_capture_control_line(stable_lines[stable_index]):
            stable_index += 1
        stable_line = stable_lines[stable_index] if stable_index < len(stable_lines) else ""
        stable_index += 1

        base_line = _strip_capture_line_link_previews(line)
        stable_previews = _capture_link_preview_blocks_by_url(stable_line)
        preserved_suffix = "".join(
            stable_previews[url]
            for url in _capture_line_urls(base_line)
            if url in stable_previews
        )
        updated_lines.append(base_line + preserved_suffix)
    return "\n".join(updated_lines)


def _is_capture_control_line(line: str) -> bool:
    stripped = line.strip()
    return (
        stripped == "/capture"
        or bool(CAPTURE_DATE_LINE_RE.match(stripped))
    )


def _capture_link_preview_suffix(line: str) -> str:
    match = re.search(r"\s+\[Link:\s+https?://", line, flags=re.IGNORECASE)
    return line[match.start() :] if match else ""


def _capture_link_preview_blocks_by_url(line: str) -> dict[str, str]:
    matches = list(
        re.finditer(
            r"\s+\[Link:\s+(?P<url>https?://[^\s;\]]+)",
            line,
            flags=re.IGNORECASE,
        )
    )
    previews: dict[str, str] = {}
    for index, match in enumerate(matches):
        url = match.group("url").rstrip(".,;:!?)]}'\"")
        if not url or url in previews:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
        previews[url] = line[match.start() : end]
    return previews


def _capture_line_urls(line: str) -> tuple[str, ...]:
    urls = [match.group(0).rstrip(".,;:!?)]}'\"") for match in CAPTURE_URL_RE.finditer(line)]
    return tuple(_dedupe_text_values(urls))


def _dedupe_text_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _capture_undo_entry(
    undo_stack: list[TelegramUndoEntry],
    result: CaptureIngestResult,
) -> TelegramUndoEntry | None:
    for index in range(len(undo_stack) - 1, -1, -1):
        entry = undo_stack[index]
        if entry.kind in {"capture", "capture_correction"} and entry.capture_result is result:
            return entry
    return None


def _capture_saved_block_count(result: CaptureIngestResult) -> int:
    return len(result.family.added) + len(result.journal_entries)


def _capture_note_count(result: CaptureIngestResult) -> int:
    return len(result.notes) if result.notes else _capture_saved_block_count(result)


def _remember_recent_capture(
    session: TelegramConversationSession,
    capture: TelegramRecentCapture,
) -> None:
    session.recent_captures = [
        existing
        for existing in session.recent_captures
        if existing.result is not capture.result
    ]
    session.recent_captures.append(capture)
    if len(session.recent_captures) > CAPTURE_CORRECTION_HISTORY_LIMIT:
        del session.recent_captures[:-CAPTURE_CORRECTION_HISTORY_LIMIT]


def _remember_recent_trace(
    session: TelegramConversationSession,
    trace: TelegramRecentTrace,
) -> None:
    session.recent_traces.append(trace)
    if len(session.recent_traces) > TRACE_HISTORY_LIMIT:
        del session.recent_traces[:-TRACE_HISTORY_LIMIT]


def _linked_trace_feedback_note(trace: TelegramRecentTrace, feedback: str) -> str:
    knowledge = ", ".join(trace.context_labels) if trace.context_labels else "None"
    return "\n".join(
        [
            "learning N4OS answer feedback:",
            f"Feedback: {feedback.strip()}",
            f"Mode: {trace.mode}",
            f"Question: {trace.question}",
            f"Knowledge used: {knowledge}",
            f"Reasoning summary: {trace.reasoning_summary}",
        ]
    )


def _replace_recent_capture(
    session: TelegramConversationSession,
    old_capture: TelegramRecentCapture,
    new_capture: TelegramRecentCapture,
) -> None:
    session.recent_captures = [
        capture
        for capture in session.recent_captures
        if capture is not old_capture
    ]
    _remember_recent_capture(session, new_capture)


def _restore_capture_references(
    session: TelegramConversationSession,
    old_capture: TelegramRecentCapture,
    restored_result: CaptureIngestResult,
    *,
    source: str,
    undo_entry: TelegramUndoEntry | None,
) -> None:
    restored_text = _capture_replay_text(restored_result, old_capture.text)
    tracked_result = old_capture.result
    _replace_recent_capture(
        session,
        old_capture,
        TelegramRecentCapture(
            text=old_capture.text,
            source=source,
            result=tracked_result,
            stable_text=restored_text,
            reply_message_ids=old_capture.reply_message_ids,
        ),
    )
    if undo_entry is not None:
        object.__setattr__(undo_entry, "capture_result", tracked_result)


def _capture_replay_text(result: CaptureIngestResult, fallback: str) -> str:
    if not result.notes:
        return fallback
    lines = ["/capture"]
    for note in result.notes:
        lines.append(note.captured_on.isoformat())
        lines.append(note.text)
    return "\n".join(lines)


def _capture_source_from_result(result: CaptureIngestResult, fallback: str) -> str:
    for note in result.notes:
        if note.source:
            return note.source
    for observation in [*result.family.added, *result.family.skipped_duplicates]:
        if observation.source:
            return observation.source
    for entry in [*result.journal_entries, *result.skipped_journal_duplicates]:
        if entry.source:
            return entry.source
    return fallback


def _capture_default_date_from_result(result: CaptureIngestResult) -> date | None:
    for note in result.notes:
        return note.captured_on
    for observation in [*result.family.added, *result.family.skipped_duplicates]:
        return observation.observed_on
    for entry in [*result.journal_entries, *result.skipped_journal_duplicates]:
        return entry.captured_on
    return None


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
        school_newsletter_importer: SchoolNewsletterImporter | None = None,
        second_brain_importer: SecondBrainImporter | None = None,
        school_coach_claw: SchoolCoachClaw | None = None,
        help_answerer: Any | None = None,
    ) -> None:
        self.config = config
        self.claw = claw or N4OSClaw.default()
        self.logger = logger or LOGGER
        self.audio_transcriber = audio_transcriber or create_default_audio_transcriber(
            config.voice_transcribe_command,
        )
        self.image_text_extractor = image_text_extractor or OpenAIImageTextExtractor.from_env_or_none()
        self.help_answerer = help_answerer or OpenAIN4OSHelpAnswerer.from_env_or_none()
        self.undo_stack: list[TelegramUndoEntry] = []
        self.sessions = TelegramSessionStore(self.claw, self.undo_stack)
        self.chat_sessions = chat_sessions or N4OSChatSessionStore()
        self.n4os_root = n4os_root or ROOT / "n4os"
        self.homework_claw = homework_claw
        self.school_coach_claw = school_coach_claw
        self.school_newsletter_importer = school_newsletter_importer or SchoolNewsletterImporter(
            n4os_root=self.n4os_root,
        )
        self.second_brain_importer = second_brain_importer or SecondBrainImporter(
            n4os_root=self.n4os_root,
        )

    def _homework_claw(self) -> HomeworkClaw:
        if self.homework_claw is None:
            self.homework_claw = HomeworkClaw.default()
        return self.homework_claw

    def _school_coach_claw(self) -> SchoolCoachClaw:
        if self.school_coach_claw is None:
            self.school_coach_claw = SchoolCoachClaw.default(n4os_root=self.n4os_root)
        return self.school_coach_claw

    def route_message(
        self,
        text: str,
        *,
        source: str = "telegram_text",
        default_owner: str | None = None,
        photo_path: str | None = None,
        semantic_image_path: str | None = None,
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
                semantic_image_path=semantic_image_path,
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
        recent_captures: list[TelegramRecentCapture] | None = None,
    ) -> str | None:
        active_claw = claw or self.claw
        active_undo_stack = undo_stack if undo_stack is not None else self.undo_stack
        if not active_undo_stack:
            return "Nothing to undo."

        entry = active_undo_stack.pop()
        if entry.kind == "capture_correction_duplicate":
            if entry.capture_restore_text is None:
                return "I could not undo that capture correction."
            try:
                restored = ingest_capture_notes(
                    entry.capture_restore_text,
                    n4os_root=self.n4os_root,
                    default_date=entry.capture_restore_default_date,
                    source=entry.capture_restore_source or "Telegram",
                    enrich_links=False,
                )
            except Exception:
                active_undo_stack.append(entry)
                self.logger.exception("error while undoing duplicate capture correction")
                return "I could not restore the previous capture, so I left the undo available."
            if _capture_saved_block_count(restored):
                if recent_captures is not None:
                    recent_captures.append(
                        TelegramRecentCapture(
                            text=entry.capture_restore_editable_text or entry.capture_restore_text,
                            source=entry.capture_restore_source or "Telegram",
                            result=restored,
                            stable_text=_capture_replay_text(restored, entry.capture_restore_text),
                            reply_message_ids=entry.capture_restore_reply_message_ids,
                        )
                    )
                if entry.capture_restore_undo_entry is not None:
                    object.__setattr__(
                        entry.capture_restore_undo_entry,
                        "capture_result",
                        restored,
                    )
                else:
                    active_undo_stack.append(
                        TelegramUndoEntry(kind="capture", capture_result=restored),
                    )
            return "Undid capture correction: restored the previous captured note."

        if entry.kind == "capture_correction" and entry.capture_result is not None:
            result = undo_capture_ingest(entry.capture_result, n4os_root=self.n4os_root)
            if result.removed == 0:
                return format_capture_undo_reply(result)
            if result.removed != _capture_saved_block_count(entry.capture_result):
                corrected_replay_text = entry.capture_current_text or _capture_replay_text(entry.capture_result, "")
                if corrected_replay_text:
                    ingest_capture_notes(
                        corrected_replay_text,
                        n4os_root=self.n4os_root,
                        default_date=_capture_default_date_from_result(entry.capture_result),
                        source=_capture_source_from_result(
                            entry.capture_result,
                            entry.capture_restore_source or "Telegram",
                        ),
                        enrich_links=False,
                    )
                active_undo_stack.append(entry)
                return "I could not safely remove the full corrected capture, so I left it unchanged."
            if recent_captures is not None:
                recent_captures[:] = [
                    capture
                    for capture in recent_captures
                    if capture.result is not entry.capture_result
                ]
            if entry.capture_restore_text is None:
                return format_capture_undo_reply(result)
            try:
                restored = ingest_capture_notes(
                    entry.capture_restore_text,
                    n4os_root=self.n4os_root,
                    default_date=entry.capture_restore_default_date,
                    source=entry.capture_restore_source or "Telegram",
                    enrich_links=False,
                )
            except Exception:
                corrected_replay_text = entry.capture_current_text or _capture_replay_text(entry.capture_result, "")
                try:
                    replacement = ingest_capture_notes(
                        corrected_replay_text,
                        n4os_root=self.n4os_root,
                        default_date=_capture_default_date_from_result(entry.capture_result),
                        source=_capture_source_from_result(
                            entry.capture_result,
                            entry.capture_restore_source or "Telegram",
                        ),
                        enrich_links=False,
                    )
                    if _capture_saved_block_count(replacement):
                        object.__setattr__(entry, "capture_result", replacement)
                        if recent_captures is not None:
                            recent_captures.append(
                                TelegramRecentCapture(
                                    text=corrected_replay_text,
                                    source=_capture_source_from_result(
                                        replacement,
                                        entry.capture_restore_source or "Telegram",
                                    ),
                                    result=replacement,
                                    stable_text=_capture_replay_text(replacement, corrected_replay_text),
                                    reply_message_ids=entry.capture_restore_reply_message_ids,
                                )
                            )
                except Exception:
                    self.logger.exception("error while restoring corrected capture after undo restore failure")
                active_undo_stack.append(entry)
                return "I could not restore the previous capture, so I kept the corrected capture available for undo."
            if _capture_saved_block_count(restored):
                if recent_captures is not None:
                    recent_captures.append(
                        TelegramRecentCapture(
                            text=entry.capture_restore_editable_text or entry.capture_restore_text,
                            source=entry.capture_restore_source or "Telegram",
                            result=restored,
                            stable_text=_capture_replay_text(restored, entry.capture_restore_text),
                            reply_message_ids=entry.capture_restore_reply_message_ids,
                        )
                    )
                if entry.capture_restore_undo_entry is not None:
                    object.__setattr__(
                        entry.capture_restore_undo_entry,
                        "capture_result",
                        restored,
                    )
                else:
                    active_undo_stack.append(
                        TelegramUndoEntry(kind="capture", capture_result=restored),
                    )
            return "Undid capture correction: restored the previous captured note."

        if entry.kind == "capture" and entry.capture_result is not None:
            result = undo_capture_ingest(entry.capture_result, n4os_root=self.n4os_root)
            if recent_captures is not None and result.removed:
                recent_captures[:] = [
                    capture
                    for capture in recent_captures
                    if capture.result is not entry.capture_result
                ]
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

        reply = _telegram_how_to_reply(text, self.help_answerer) or HELP_MESSAGE
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

        async def reply_chat_chunks(reply: str) -> list[Any]:
            sent_messages: list[Any] = []
            for chunk in _telegram_reply_chunks(reply):
                sent_messages.append(await message.reply_text(chunk))
            return sent_messages

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
            await reply_chat_chunks(VOICE_TRANSCRIPTION_RESULT_MESSAGE.format(text=text))
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

        if not text:
            cleanup_image_input()
            await message.reply_text(UNSUPPORTED_MESSAGE)
            self.logger.info("chosen route=unsupported execution_ms=0.00")
            return

        starts_school_coach = is_school_coach_message(text)
        continues_school_coach = (
            session.mode == "school_coach_active"
            and not text.lstrip().startswith("/")
            and not _is_new_session_command(text)
            and not _is_active_chat_bypass_message(text)
            and not _is_high_confidence_action(session.claw, text)
        )
        if starts_school_coach or continues_school_coach:
            started = time.perf_counter()
            message_id = getattr(message, "message_id", "unknown")
            chat = getattr(update, "effective_chat", None)
            chat_id = getattr(chat, "id", "unknown")
            provenance = CoachProvenance(
                kind="user_report",
                ref=f"telegram:{chat_id}:{user_id}:{message_id}",
                reported_by=sender_profile.name if sender_profile is not None else None,
            )
            try:
                reply = self._school_coach_claw().handle_request(text, provenance=provenance)
            except Exception:
                elapsed_ms = (time.perf_counter() - started) * 1000
                self.logger.exception(
                    "error while updating school coach execution_ms=%.2f",
                    elapsed_ms,
                )
                cleanup_image_input()
                await message.reply_text(ERROR_MESSAGE)
                return
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.info("chosen route=school_coach execution_ms=%.2f", elapsed_ms)
            session.mode = "school_coach_active"
            cleanup_image_input()
            await reply_chat_chunks(reply)
            return

        slash_help_reply = _telegram_slash_help_reply(text)
        if slash_help_reply is not None:
            cleanup_image_input()
            await message.reply_text(slash_help_reply)
            self.logger.info("chosen route=telegram_slash_help execution_ms=0.00")
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
                recent_captures=session.recent_captures,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.info("chosen route=undo execution_ms=%.2f", elapsed_ms)
            session.mode = "idle"
            cleanup_image_input()
            await message.reply_text(undo_reply)
            return

        capture_correction = _capture_correction_target(text, message, session)
        if capture_correction is not None:
            started = time.perf_counter()
            target_capture, instruction = capture_correction
            if target_capture is None:
                cleanup_image_input()
                await message.reply_text("No recent capture found to fix.")
                self.logger.info("chosen route=n4os_capture_correction_missing execution_ms=0.00")
                return

            replay_text = target_capture.stable_text or _capture_replay_text(
                target_capture.result,
                target_capture.text,
            )
            corrected_editable_text, correction_error = _apply_capture_correction(
                target_capture.text,
                instruction,
            )
            if corrected_editable_text is None:
                cleanup_image_input()
                await message.reply_text(correction_error or "I could not apply that capture correction.")
                self.logger.info("chosen route=n4os_capture_correction_noop execution_ms=0.00")
                return
            corrected_text, enrich_corrected_links = _prepare_corrected_capture_text(
                replay_text,
                corrected_editable_text,
            )

            source = _capture_source_from_result(target_capture.result, target_capture.source)
            default_date = _capture_default_date_from_result(target_capture.result)
            restore_undo_entry = _capture_undo_entry(session.undo_stack, target_capture.result)
            if _capture_note_count(target_capture.result) != 1:
                cleanup_image_input()
                await message.reply_text(
                    "I can only fix a single saved capture note right now. For batch captures, send undo and resend the corrected capture."
                )
                self.logger.info("chosen route=n4os_capture_correction_batch_refused execution_ms=0.00")
                return
            if count_capture_notes(
                corrected_text,
                default_date=default_date,
                source=source,
            ) != 1:
                cleanup_image_input()
                await message.reply_text(
                    "I can only fix a single saved capture note right now. Keep the correction to one note, or send undo and resend the corrected capture."
                )
                self.logger.info("chosen route=n4os_capture_correction_multi_note_refused execution_ms=0.00")
                return
            try:
                undo_result = undo_capture_ingest(
                    target_capture.result,
                    n4os_root=self.n4os_root,
                )
                if undo_result.removed == 0:
                    cleanup_image_input()
                    await message.reply_text("I could not find the original saved capture, so I left it unchanged.")
                    self.logger.info("chosen route=n4os_capture_correction_stale execution_ms=0.00")
                    return
                if undo_result.removed != _capture_saved_block_count(target_capture.result):
                    restored_result = ingest_capture_notes(
                        replay_text,
                        n4os_root=self.n4os_root,
                        default_date=default_date,
                        source=source,
                        enrich_links=False,
                    )
                    _restore_capture_references(
                        session,
                        target_capture,
                        restored_result,
                        source=source,
                        undo_entry=restore_undo_entry,
                    )
                    cleanup_image_input()
                    await message.reply_text("I could not safely remove the full original capture, so I left it unchanged.")
                    self.logger.info("chosen route=n4os_capture_correction_partial_undo execution_ms=0.00")
                    return
                result = ingest_capture_notes(
                    corrected_text,
                    n4os_root=self.n4os_root,
                    default_date=default_date,
                    source=source,
                    enrich_links=enrich_corrected_links,
                )
                if not result.family.added and not result.journal_entries:
                    session.recent_captures = [
                        capture
                        for capture in session.recent_captures
                        if capture is not target_capture
                    ]
                    session.undo_stack.append(
                        TelegramUndoEntry(
                            kind="capture_correction_duplicate",
                            capture_restore_text=replay_text,
                            capture_restore_editable_text=target_capture.text,
                            capture_restore_source=source,
                            capture_restore_default_date=default_date,
                            capture_restore_reply_message_ids=target_capture.reply_message_ids,
                            capture_restore_undo_entry=restore_undo_entry,
                        ),
                    )
                    cleanup_image_input()
                    await message.reply_text(
                        "Updated captured note. The corrected version was already saved elsewhere, so I removed the older duplicate."
                    )
                    self.logger.info("chosen route=n4os_capture_correction_duplicate execution_ms=0.00")
                    return
            except Exception:
                elapsed_ms = (time.perf_counter() - started) * 1000
                try:
                    restored_result = ingest_capture_notes(
                        replay_text,
                        n4os_root=self.n4os_root,
                        default_date=default_date,
                        source=source,
                        enrich_links=False,
                    )
                    _restore_capture_references(
                        session,
                        target_capture,
                        restored_result,
                        source=source,
                        undo_entry=restore_undo_entry,
                    )
                except Exception:
                    self.logger.exception("error while restoring original N4OS capture")
                self.logger.exception(
                    "error while correcting N4OS capture execution_ms=%.2f",
                    elapsed_ms,
                )
                cleanup_image_input()
                await message.reply_text(ERROR_MESSAGE)
                return

            if result.family.added or result.journal_entries:
                session.undo_stack.append(
                    TelegramUndoEntry(
                        kind="capture_correction",
                        capture_result=result,
                        capture_current_text=_capture_replay_text(result, corrected_text),
                        capture_restore_text=replay_text,
                        capture_restore_editable_text=target_capture.text,
                        capture_restore_source=source,
                        capture_restore_default_date=default_date,
                        capture_restore_reply_message_ids=target_capture.reply_message_ids,
                        capture_restore_undo_entry=restore_undo_entry,
                    ),
                )
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.info(
                "chosen route=n4os_capture_correction family_added=%d journal_added=%d execution_ms=%.2f",
                len(result.family.added),
                len(result.journal_entries),
                elapsed_ms,
            )
            session.mode = "idle"
            cleanup_image_input()
            sent_messages = await reply_chat_chunks("Updated captured note.\n\n" + format_capture_reply(result))
            reply_message_ids = tuple(
                message_id
                for message_id in (_message_id(sent_message) for sent_message in sent_messages)
                if message_id is not None
            )
            _replace_recent_capture(
                session,
                target_capture,
                TelegramRecentCapture(
                    text=corrected_editable_text,
                    source=source,
                    result=result,
                    stable_text=_capture_replay_text(result, corrected_text),
                    reply_message_ids=target_capture.reply_message_ids + reply_message_ids,
                ),
            )
            if session.undo_stack and session.undo_stack[-1].kind == "capture_correction":
                object.__setattr__(
                    session.undo_stack[-1],
                    "capture_restore_reply_message_ids",
                    target_capture.reply_message_ids + reply_message_ids,
                )
            return

        if (
            self.second_brain_importer.has_pending(session_key)
            and is_second_brain_import_followup(text)
        ) or is_second_brain_import_message(text):
            started = time.perf_counter()
            try:
                if self.second_brain_importer.has_pending(session_key) and is_second_brain_import_followup(text):
                    result = self.second_brain_importer.save_pending(
                        key=session_key,
                        response=text,
                    )
                    reply = result.message
                else:
                    reply = self.second_brain_importer.preview_from_message(
                        text,
                        key=session_key,
                    )
            except SecondBrainImportUserError as error:
                elapsed_ms = (time.perf_counter() - started) * 1000
                self.logger.info(
                    "second brain import needs user input execution_ms=%.2f error=%s",
                    elapsed_ms,
                    error,
                )
                cleanup_image_input()
                await message.reply_text(str(error))
                return
            except Exception:
                elapsed_ms = (time.perf_counter() - started) * 1000
                self.logger.exception(
                    "error while importing second brain material execution_ms=%.2f",
                    elapsed_ms,
                )
                cleanup_image_input()
                await message.reply_text(ERROR_MESSAGE)
                return
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.info("chosen route=second_brain_import execution_ms=%.2f", elapsed_ms)
            session.mode = "idle"
            cleanup_image_input()
            await reply_chat_chunks(reply)
            return

        if (
            self.school_newsletter_importer.has_pending(session_key)
            and is_school_newsletter_followup(text)
        ) or is_school_newsletter_message(text):
            started = time.perf_counter()
            try:
                if self.school_newsletter_importer.has_pending(session_key) and is_school_newsletter_followup(text):
                    result = self.school_newsletter_importer.save_pending(
                        key=session_key,
                        response=text,
                    )
                    reply = result.message
                else:
                    reply = self.school_newsletter_importer.preview_from_message(
                        text,
                        key=session_key,
                    )
            except Exception:
                elapsed_ms = (time.perf_counter() - started) * 1000
                self.logger.exception(
                    "error while importing school newsletter execution_ms=%.2f",
                    elapsed_ms,
                )
                cleanup_image_input()
                await message.reply_text(ERROR_MESSAGE)
                return
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.info("chosen route=school_newsletter execution_ms=%.2f", elapsed_ms)
            session.mode = "idle"
            cleanup_image_input()
            await message.reply_text(reply)
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
        natural_structured_memory_probe = looks_like_natural_structured_memory_query(text)
        if (
            structured_memory_query
            or explicit_structured_memory_lookup
            or natural_structured_memory_probe
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

        feedback_match = TRACE_FEEDBACK_RE.match(text)
        feedback_trace = _trace_reply_target(session, _reply_to_message_id(message))
        if feedback_match and feedback_trace is not None:
            started = time.perf_counter()
            try:
                capture_markdown_note(
                    _linked_trace_feedback_note(
                        feedback_trace,
                        feedback_match.group("body"),
                    ),
                    source=_source_with_sender(message_source, sender_profile),
                    n4os_root=self.n4os_root,
                )
            except Exception:
                elapsed_ms = (time.perf_counter() - started) * 1000
                self.logger.exception(
                    "error while capturing N4OS trace feedback execution_ms=%.2f",
                    elapsed_ms,
                )
                cleanup_image_input()
                await message.reply_text(ERROR_MESSAGE)
                return
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.info(
                "chosen route=n4os_trace_feedback execution_ms=%.2f",
                elapsed_ms,
            )
            cleanup_image_input()
            await message.reply_text("Captured N4OS tuning feedback with the related answer trace.")
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

        if pending_capture_text is None and _is_markdown_note_capture(text):
            started = time.perf_counter()
            try:
                captured = capture_markdown_note(
                    _markdown_note_body(text),
                    source=_source_with_sender(message_source, sender_profile),
                    n4os_root=self.n4os_root,
                )
            except Exception:
                elapsed_ms = (time.perf_counter() - started) * 1000
                self.logger.exception(
                    "error while capturing Markdown note execution_ms=%.2f",
                    elapsed_ms,
                )
                await message.reply_text(ERROR_MESSAGE)
                return

            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.info(
                "chosen route=n4os_markdown_note kind=%s execution_ms=%.2f",
                captured.kind,
                elapsed_ms,
            )
            session.mode = "idle"
            cleanup_image_input()
            relative_path = captured.path.relative_to(self.n4os_root.parent)
            await message.reply_text(
                f"Captured {captured.kind} note: {captured.title} -> {relative_path}"
            )
            return

        if pending_capture_text is not None or is_capture_message(capture_text):
            started = time.perf_counter()
            capture_source = _n4os_capture_source(message_source, sender_profile)
            try:
                result = ingest_capture_notes(
                    capture_text,
                    n4os_root=self.n4os_root,
                    source=capture_source,
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
            sent_messages = await reply_chat_chunks(format_capture_reply(result))
            reply_message_ids = tuple(
                message_id
                for message_id in (_message_id(sent_message) for sent_message in sent_messages)
                if message_id is not None
            )
            if result.family.added or result.journal_entries:
                _remember_recent_capture(
                    session,
                    TelegramRecentCapture(
                        text=capture_text,
                        source=capture_source,
                        result=result,
                        stable_text=_capture_replay_text(result, capture_text),
                        reply_message_ids=reply_message_ids,
                    ),
                )
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

        if is_n4os_research_message(text):
            started = time.perf_counter()
            research_mode, research_question = parse_n4os_research_request(text)
            if not research_question or research_question.lower() == "help":
                cleanup_image_input()
                await message.reply_text(RESEARCH_HELP_MESSAGE)
                self.logger.info("chosen route=n4os_research_help execution_ms=0.00")
                return
            research_context = build_n4os_advice_context(research_question, self.n4os_root)
            setup_message = await message.reply_text(
                format_n4os_research_setup(research_context, mode=research_mode)
            )
            research_result = generate_n4os_research(
                text,
                context=research_context,
                n4os_root=self.n4os_root,
            )
            sources_message = await message.reply_text(
                format_n4os_research_sources(research_result.sources)
            )
            reasoning_message = await message.reply_text(
                format_n4os_reasoning_preview(
                    research_result.reasoning_summary,
                    model=research_result.model,
                )
            )
            trace_message_ids = tuple(
                message_id
                for message_id in (
                    _message_id(setup_message),
                    _message_id(sources_message),
                    _message_id(reasoning_message),
                )
                if message_id is not None
            )
            _remember_recent_trace(
                session,
                TelegramRecentTrace(
                    mode="research",
                    question=research_question,
                    context_labels=tuple(research_result.context_labels),
                    reasoning_summary=research_result.reasoning_summary,
                    reply_message_ids=trace_message_ids,
                ),
            )
            record_n4os_trajectory(
                mode="research",
                user_text=research_question,
                assistant_text=research_result.reply,
                context_labels=research_result.context_labels,
                source=_source_with_sender(message_source, sender_profile),
                n4os_root=self.n4os_root,
                model=research_result.model,
                knowledge_preview=research_result.knowledge_preview,
                reasoning_summary=research_result.reasoning_summary,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.info(
                "chosen route=n4os_research mode=%s execution_ms=%.2f",
                research_result.mode,
                elapsed_ms,
            )
            cleanup_image_input()
            await reply_chat_chunks(research_result.reply)
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
            history = self.chat_sessions.history(chat_key)
            prepared_context = build_n4os_advice_context(chat_request, self.n4os_root)
            knowledge_preview = format_n4os_knowledge_preview(
                prepared_context,
                history_turns=len(history),
            )
            knowledge_message = await message.reply_text(knowledge_preview)
            chat_result = format_n4os_chat(
                chat_request,
                history=history,
                context=prepared_context,
                n4os_root=self.n4os_root,
            )
            reasoning_preview = format_n4os_reasoning_preview(
                chat_result.reasoning_summary,
                model=chat_result.model,
            )
            reasoning_message = await message.reply_text(reasoning_preview)
            trace_message_ids = tuple(
                message_id
                for message_id in (
                    _message_id(knowledge_message),
                    _message_id(reasoning_message),
                )
                if message_id is not None
            )
            _remember_recent_trace(
                session,
                TelegramRecentTrace(
                    mode="chat",
                    question=chat_request,
                    context_labels=tuple(chat_result.context_labels),
                    reasoning_summary=chat_result.reasoning_summary,
                    reply_message_ids=trace_message_ids,
                ),
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
                knowledge_preview=chat_result.knowledge_preview or knowledge_preview,
                reasoning_summary=chat_result.reasoning_summary,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.info("chosen route=n4os_chat execution_ms=%.2f", elapsed_ms)
            cleanup_image_input()
            await reply_chat_chunks(chat_result.reply)
            return

        if is_n4os_advice_message(text):
            started = time.perf_counter()
            advice_context = build_n4os_advice_context(
                strip_n4os_advice_prefix(text),
                self.n4os_root,
            )
            knowledge_preview = format_n4os_knowledge_preview(advice_context)
            knowledge_message = await message.reply_text(knowledge_preview)
            advice_result = generate_n4os_advice(
                text,
                context=advice_context,
                n4os_root=self.n4os_root,
            )
            reasoning_preview = format_n4os_reasoning_preview(
                advice_result.reasoning_summary,
                model=advice_result.model,
            )
            reasoning_message = await message.reply_text(reasoning_preview)
            trace_message_ids = tuple(
                message_id
                for message_id in (
                    _message_id(knowledge_message),
                    _message_id(reasoning_message),
                )
                if message_id is not None
            )
            _remember_recent_trace(
                session,
                TelegramRecentTrace(
                    mode="ask",
                    question=strip_n4os_advice_prefix(text),
                    context_labels=tuple(advice_result.context_labels),
                    reasoning_summary=advice_result.reasoning_summary,
                    reply_message_ids=trace_message_ids,
                ),
            )
            record_n4os_trajectory(
                mode="ask",
                user_text=text,
                assistant_text=advice_result.reply,
                context_labels=advice_result.context_labels,
                source=_source_with_sender(message_source, sender_profile),
                n4os_root=self.n4os_root,
                model=advice_result.model,
                knowledge_preview=advice_result.knowledge_preview,
                reasoning_summary=advice_result.reasoning_summary,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.info("chosen route=n4os_advice execution_ms=%.2f", elapsed_ms)
            cleanup_image_input()
            await message.reply_text(advice_result.reply)
            return

        how_to_reply = _telegram_how_to_reply(text, self.help_answerer)
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
                semantic_image_path=str(staged_photo_file) if staged_photo_file is not None else None,
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

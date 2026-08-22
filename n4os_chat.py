from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
import json
import os
import re
import urllib.request
from typing import Any, Callable

from n4os_advice import (
    DEFAULT_MODEL,
    DEFAULT_N4OS_ROOT,
    OPENAI_RESPONSES_URL,
    _build_context,
    _collapse_excess_blank_lines,
    _extract_reasoning_summary,
    _extract_transparent_response,
    _strip_basic_markdown,
    context_labels_from_context,
    format_n4os_knowledge_preview,
    N4OS_TRANSPARENT_RESPONSE_FORMAT,
)


CHAT_COMMAND_RE = re.compile(r"^\s*/chat(?:@\w+)?(?:\s+(.+))?\s*$", re.I | re.S)
CHAT_CONTROL_RE = re.compile(r"^\s*/chat(?:@\w+)?\s+(reset|stop|clear|help)\s*$", re.I)
DEFAULT_CHAT_MODEL = DEFAULT_MODEL
CHAT_SESSION_TTL = timedelta(hours=6)
MAX_HISTORY_TURNS = 8
MAX_HISTORY_CHARS = 6000
RICH_CHAT_SETUP_MESSAGE = (
    "Rich N4OS chat needs OPENAI_API_KEY. /ask still works for compact offline advice, "
    "but /chat needs the model so it can hold a deeper conversation with memory."
)

UrlOpen = Callable[..., Any]


@dataclass(frozen=True)
class N4OSChatTurn:
    user: str
    assistant: str
    captured_at: datetime


@dataclass
class N4OSChatSession:
    turns: list[N4OSChatTurn] = field(default_factory=list)
    last_active: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class N4OSChatResult:
    reply: str
    context_labels: list[str]
    model: str | None
    reasoning_summary: str = ""
    knowledge_preview: str = ""


class N4OSChatSessionStore:
    def __init__(
        self,
        *,
        ttl: timedelta = CHAT_SESSION_TTL,
        max_turns: int = MAX_HISTORY_TURNS,
    ) -> None:
        self.ttl = ttl
        self.max_turns = max_turns
        self._sessions: dict[str, N4OSChatSession] = {}

    def history(self, key: str, *, now: datetime | None = None) -> list[N4OSChatTurn]:
        session = self._fresh_session(key, now=now)
        return list(session.turns) if session is not None else []

    def append(
        self,
        key: str,
        *,
        user_text: str,
        assistant_text: str,
        now: datetime | None = None,
    ) -> None:
        current_time = now or datetime.now()
        session = self._fresh_session(key, now=current_time) or N4OSChatSession()
        session.turns.append(
            N4OSChatTurn(
                user=user_text.strip(),
                assistant=assistant_text.strip(),
                captured_at=current_time,
            )
        )
        session.turns = session.turns[-self.max_turns :]
        session.last_active = current_time
        self._sessions[key] = session

    def active(self, key: str, *, now: datetime | None = None) -> bool:
        return self._fresh_session(key, now=now) is not None

    def reset(self, key: str) -> None:
        self._sessions.pop(key, None)

    def _fresh_session(self, key: str, *, now: datetime | None = None) -> N4OSChatSession | None:
        session = self._sessions.get(key)
        if session is None:
            return None
        current_time = now or datetime.now()
        if current_time - session.last_active > self.ttl:
            self._sessions.pop(key, None)
            return None
        return session


def is_n4os_chat_message(text: str) -> bool:
    return bool(CHAT_COMMAND_RE.match(text.strip()))


def parse_n4os_chat_control(text: str) -> str | None:
    match = CHAT_CONTROL_RE.match(text.strip())
    if not match:
        return None
    raw = match.group(1).lower()
    if raw in {"reset", "stop", "clear"}:
        return "reset"
    return "help"


def strip_n4os_chat_prefix(text: str) -> str:
    match = CHAT_COMMAND_RE.match(text.strip())
    if not match:
        return text.strip()
    return (match.group(1) or "").strip()


def format_n4os_chat(
    request: str,
    *,
    history: list[N4OSChatTurn] | None = None,
    context: dict[str, Any] | None = None,
    n4os_root: Path = DEFAULT_N4OS_ROOT,
    api_key: str | None = None,
    model: str | None = None,
    urlopen: UrlOpen = urllib.request.urlopen,
) -> N4OSChatResult:
    cleaned_request = strip_n4os_chat_prefix(request)
    prepared_context = context if context is not None else _build_context(cleaned_request, n4os_root)
    prepared_history = history or []
    labels = context_labels_from_context(prepared_context)
    knowledge_preview = format_n4os_knowledge_preview(
        prepared_context,
        history_turns=len(prepared_history),
    )
    resolved_model = (model or os.environ.get("N4OS_ADVICE_MODEL") or DEFAULT_CHAT_MODEL).strip()
    resolved_key = (api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")).strip()
    if not resolved_key:
        return N4OSChatResult(
            reply=RICH_CHAT_SETUP_MESSAGE,
            context_labels=labels,
            model=None,
            reasoning_summary="The model was not called because OPENAI_API_KEY is not configured.",
            knowledge_preview=knowledge_preview,
        )

    body = {
        "model": resolved_model,
        "store": False,
        "max_output_tokens": 1600,
        "reasoning": {"summary": "concise"},
        "text": {"format": N4OS_TRANSPARENT_RESPONSE_FORMAT},
        "input": [
            {
                "role": "system",
                "content": (
                    "You are N4OS in rich conversational mode. Use the supplied memory, recent trajectory "
                    "summaries, and short-term chat history. Be better than a generic chatbot: personal, "
                    "direct, warm, concrete, and willing to reason with nuance. Telegram can split long "
                    "answers, so do not compress important guidance into a tiny card. Still avoid rambling. "
                    "Write plain text: no Markdown headings, no bold markers, no raw file paths, and no "
                    "links unless the user asks. For family memory, use current-pattern wording, not fixed "
                    "identity claims. End with 1-2 genuine follow-up questions when the topic would benefit "
                    "from continued conversation. Return a concise reasoning_summary as 2-4 short "
                    "newline-separated statements that name the relevant signals, assumptions, and why they "
                    "support the answer. This is a high-level decision rationale, not hidden chain-of-thought."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": cleaned_request,
                        "memory": prepared_context,
                        "history": _history_payload(prepared_history),
                        "format": "rich Telegram conversation",
                    },
                    sort_keys=True,
                ),
            },
        ],
    }
    request_obj = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {resolved_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request_obj, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return N4OSChatResult(
            reply=(
                "I could not reach the model for rich chat right now. Use /ask for a compact fallback, "
                "or try /chat again in a moment."
            ),
            context_labels=labels,
            model=resolved_model,
            reasoning_summary="The model request failed before a reasoning summary was available.",
            knowledge_preview=knowledge_preview,
        )

    text, disclosed_summary = _extract_transparent_response(payload)
    if not text:
        text = "I did not get a useful model response. Try again with the same topic."
    return N4OSChatResult(
        reply=_normalize_chat_output(text),
        context_labels=labels,
        model=resolved_model,
        reasoning_summary=(
            _extract_reasoning_summary(payload)
            or disclosed_summary
            or "The model did not return a reasoning summary."
        ),
        knowledge_preview=knowledge_preview,
    )


def _history_payload(history: list[N4OSChatTurn]) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    used_chars = 0
    for turn in reversed(history):
        user = turn.user.strip()
        assistant = turn.assistant.strip()
        used_chars += len(user) + len(assistant)
        if used_chars > MAX_HISTORY_CHARS:
            break
        payload.append(
            {
                "user": user,
                "assistant": assistant,
                "captured_at": turn.captured_at.isoformat(timespec="seconds"),
            }
        )
    payload.reverse()
    return payload


def _normalize_chat_output(text: str) -> str:
    cleaned = _strip_basic_markdown(text)
    cleaned = _collapse_excess_blank_lines(cleaned)
    cleaned = re.sub(r"n4os/[A-Za-z0-9_./ -]+\.md", "N4OS memory", cleaned)
    return cleaned.strip()

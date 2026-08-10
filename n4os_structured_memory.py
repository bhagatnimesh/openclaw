from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import re
import sqlite3
from typing import Iterator, Literal
from uuid import uuid4


ROOT = Path(__file__).resolve().parent
DEFAULT_N4OS_ROOT = ROOT / "n4os"
DEFAULT_DB_FILE = ROOT / "data" / "n4os.db"

MemoryKind = Literal["dinner_pickup_event", "dinner_pickup_assignment", "note"]

REMEMBER_RE = re.compile(r"^\s*/?remember(?:@\w+)?(?!\s+to\b)(?=\s|$)", re.I)
DINNER_PICKUP_RE = re.compile(
    r"\b(dinner|food)\b.*\b(pick(?:ed)?\s*up|pickup|pick\s*up|turn)\b|"
    r"\b(pick(?:ed)?\s*up|pickup|pick\s*up|turn)\b.*\b(dinner|food)\b",
    re.I,
)
PICKED_BY_RE = re.compile(
    r"\b(?P<person>[A-Z][A-Za-z'.-]+)\s+(?:"
    r"picked\s+up|picked|did|handled|got|collected"
    r")\s+(?:the\s+)?(?:dinner|food)(?:\s+pickup)?\b",
)
PICKUP_BY_RE = re.compile(
    r"\b(?:dinner|food)\s+(?:pickup\s+)?(?:was\s+)?"
    r"(?:picked\s+up|handled|done)\s+by\s+(?P<person>[A-Z][A-Za-z'.-]+)\b",
)
NEXT_BY_RE = re.compile(
    r"\b(?P<person>[A-Z][A-Za-z'.-]+)\s+"
    r"(?:has|gets|is\s+doing|will\s+do|owns)\s+"
    r"(?:the\s+)?(?:next\s+)?(?:dinner|food)\s+(?:pickup|turn)\b|"
    r"\b(?:next\s+)?(?:dinner|food)\s+(?:pickup\s+)?(?:turn\s+)?"
    r"(?:is|goes\s+to|belongs\s+to)\s+(?P<person2>[A-Z][A-Za-z'.-]+)\b",
)
UNRESOLVED_RE = re.compile(
    r"\b(unresolved|unknown|not\s+sure|confirm|ask\s+the\s+family|"
    r"do\s+not\s+assume|don't\s+assume)\b",
    re.I,
)
LAST_PICKUPS_RE = re.compile(r"\blast\s+(?P<count>\d+)\s+(?:dinner|food)(?:\s+pickup)?s?\b", re.I)
PICKUP_QUERY_RE = re.compile(
    r"\b(?:who|whose|what)\b.*\b(?:dinner|food)\b.*\b(?:pickup|pick\s+up|picked|turn)\b|"
    r"\b(?:dinner|food)\b.*\b(?:pickup|turn)\b.*\b(?:who|whose|next|last)\b",
    re.I,
)


@dataclass(frozen=True)
class MemoryItem:
    id: str
    kind: MemoryKind
    subject: str
    actor: str | None
    value: str
    happened_on: str | None
    applies_on: str | None
    status: str
    confidence: str
    source: str
    text: str
    created_at: str


@dataclass(frozen=True)
class RememberResult:
    item: MemoryItem
    reply: str


class SQLiteStructuredMemoryStore:
    def __init__(self, db_path: str | Path = DEFAULT_DB_FILE):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS n4os_memory_items (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    actor TEXT,
                    value TEXT NOT NULL,
                    happened_on TEXT,
                    applies_on TEXT,
                    status TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    source TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """,
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_n4os_memory_items_subject_kind_date
                ON n4os_memory_items(subject, kind, happened_on, applies_on, created_at)
                """,
            )

    def add_item(
        self,
        *,
        kind: MemoryKind,
        subject: str,
        actor: str | None,
        value: str,
        happened_on: str | None,
        applies_on: str | None,
        status: str,
        confidence: str,
        source: str,
        text: str,
        created_at: datetime | None = None,
    ) -> MemoryItem:
        item = MemoryItem(
            id=uuid4().hex,
            kind=kind,
            subject=subject,
            actor=actor,
            value=value,
            happened_on=happened_on,
            applies_on=applies_on,
            status=status,
            confidence=confidence,
            source=source,
            text=text,
            created_at=(created_at or datetime.now().astimezone()).isoformat(timespec="seconds"),
        )
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO n4os_memory_items (
                    id, kind, subject, actor, value, happened_on, applies_on,
                    status, confidence, source, text, created_at
                )
                VALUES (
                    :id, :kind, :subject, :actor, :value, :happened_on, :applies_on,
                    :status, :confidence, :source, :text, :created_at
                )
                """,
                item.__dict__,
            )
        return item

    def dinner_pickup_events(self, *, limit: int) -> list[MemoryItem]:
        return self._items(
            """
            SELECT * FROM n4os_memory_items
            WHERE subject = 'dinner_pickup' AND kind = 'dinner_pickup_event'
            ORDER BY happened_on DESC, created_at DESC
            LIMIT :limit
            """,
            {"limit": limit},
        )

    def latest_dinner_pickup_assignment(self) -> MemoryItem | None:
        items = self._items(
            """
            SELECT * FROM n4os_memory_items
            WHERE subject = 'dinner_pickup' AND kind = 'dinner_pickup_assignment'
            ORDER BY applies_on DESC, created_at DESC
            LIMIT 1
            """,
            {},
        )
        return items[0] if items else None

    def dinner_pickup_notes(self, *, limit: int) -> list[MemoryItem]:
        return self._items(
            """
            SELECT * FROM n4os_memory_items
            WHERE subject = 'dinner_pickup' AND kind = 'note'
            ORDER BY created_at DESC
            LIMIT :limit
            """,
            {"limit": limit},
        )

    def _items(self, query: str, params: dict[str, object]) -> list[MemoryItem]:
        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [MemoryItem(**dict(row)) for row in rows]


def is_structured_remember_message(text: str) -> bool:
    return bool(REMEMBER_RE.match(text.strip()))


def is_structured_memory_query(text: str) -> bool:
    stripped = text.strip()
    return bool(LAST_PICKUPS_RE.search(stripped) or PICKUP_QUERY_RE.search(stripped))


def remember_structured_memory(
    text: str,
    *,
    n4os_root: Path = DEFAULT_N4OS_ROOT,
    source: str = "Telegram",
    today: date | None = None,
) -> RememberResult:
    store = SQLiteStructuredMemoryStore(_db_path(n4os_root))
    body = _strip_remember_prefix(text)
    current_date = today or date.today()
    kind, actor, happened_on, applies_on, status, value = _classify_memory(body, current_date)
    item = store.add_item(
        kind=kind,
        subject="dinner_pickup" if DINNER_PICKUP_RE.search(body) else "general",
        actor=actor,
        value=value,
        happened_on=happened_on.isoformat() if happened_on else None,
        applies_on=applies_on.isoformat() if applies_on else None,
        status=status,
        confidence="high" if actor else "low",
        source=source,
        text=body,
    )
    return RememberResult(item=item, reply=_remember_reply(item))


def format_structured_memory_query(
    text: str,
    *,
    n4os_root: Path = DEFAULT_N4OS_ROOT,
) -> str:
    store = SQLiteStructuredMemoryStore(_db_path(n4os_root))
    count_match = LAST_PICKUPS_RE.search(text)
    if count_match:
        count = max(1, min(10, int(count_match.group("count"))))
        events = store.dinner_pickup_events(limit=count)
        if not events:
            return "I do not have any dinner pickups recorded yet. Use /remember after each pickup."
        lines = [f"Last {min(count, len(events))} dinner pickups:"]
        for index, event in enumerate(events, start=1):
            lines.append(f"{index}. {event.happened_on}: {event.actor or event.value}")
        if len(events) < count:
            lines.append("")
            lines.append(f"I only have {len(events)} dinner pickup record(s) saved.")
        return "\n".join(lines)

    assignment = store.latest_dinner_pickup_assignment()
    if assignment and assignment.status == "active" and assignment.actor:
        label = "Next dinner pickup"
        if assignment.applies_on:
            label += f" ({assignment.applies_on})"
        return f"{label}: {assignment.actor}.\nSource: {assignment.source}."

    notes = store.dinner_pickup_notes(limit=3)
    if notes:
        lines = ["I do not have a locked dinner pickup owner yet.", "", "Current memory:"]
        for note in notes:
            lines.append(f"- {note.value}")
        return "\n".join(lines)

    events = store.dinner_pickup_events(limit=1)
    if events:
        latest = events[0]
        return (
            "I do not have the next dinner pickup owner locked.\n"
            f"Most recent recorded pickup: {latest.happened_on}: {latest.actor or latest.value}."
        )
    return "I do not have dinner pickup memory yet. Use /remember to save who picked up dinner or who has the next turn."


def _db_path(n4os_root: Path) -> Path:
    return n4os_root.parent / "data" / "n4os.db"


def _strip_remember_prefix(text: str) -> str:
    return REMEMBER_RE.sub("", text.strip(), count=1).strip(" :-")


def _classify_memory(
    text: str,
    today: date,
) -> tuple[MemoryKind, str | None, date | None, date | None, str, str]:
    actor = _pickup_actor(text)
    mentioned_date, has_explicit_date = _mentioned_date(text, today)
    if actor and _is_historical_pickup(text):
        return ("dinner_pickup_event", actor, mentioned_date, None, "recorded", actor)
    if actor and DINNER_PICKUP_RE.search(text):
        applies_on = mentioned_date if has_explicit_date else None
        return ("dinner_pickup_assignment", actor, None, applies_on, "active", actor)
    if DINNER_PICKUP_RE.search(text) and UNRESOLVED_RE.search(text):
        return ("note", None, None, None, "active", text)
    return ("note", None, None, None, "active", text)


def _pickup_actor(text: str) -> str | None:
    for pattern in (PICKED_BY_RE, PICKUP_BY_RE, NEXT_BY_RE):
        match = pattern.search(text)
        if not match:
            continue
        person = match.groupdict().get("person") or match.groupdict().get("person2")
        if person:
            return person
    return None


def _is_historical_pickup(text: str) -> bool:
    lowered = text.lower()
    return bool(
        re.search(r"\bpicked\s+up|picked|did|handled|got|collected\b", lowered)
        and not re.search(r"\bnext|turn|will|has\b", lowered)
    )


def _mentioned_date(text: str, today: date) -> tuple[date, bool]:
    lowered = text.lower()
    if "yesterday" in lowered:
        return today - timedelta(days=1), True
    if "tomorrow" in lowered:
        return today + timedelta(days=1), True
    if "tonight" in lowered or "today" in lowered:
        return today, True
    return today, False


def _remember_reply(item: MemoryItem) -> str:
    if item.kind == "dinner_pickup_event":
        return f"Remembered. Dinner pickup: {item.actor or item.value} on {item.happened_on}."
    if item.kind == "dinner_pickup_assignment":
        date_part = f" for {item.applies_on}" if item.applies_on else ""
        return f"Remembered. Next dinner pickup{date_part}: {item.actor or item.value}."
    if item.subject == "dinner_pickup":
        return "Remembered. Dinner pickup note saved."
    return "Remembered. Structured note saved."

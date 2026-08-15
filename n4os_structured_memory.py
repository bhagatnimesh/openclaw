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
FORGET_RE = re.compile(
    r"^\s*/?(?:forget|delete|remove)\s+(?:the\s+)?(?:(?:remembered|structured)\s+)?"
    r"(?:(?:note|notes|memory|memories)\s+)?(?P<query>.+)",
    re.I,
)
UPDATE_RE = re.compile(
    r"^\s*/?(?:update|change|correct)\s+(?:the\s+)?(?:(?:remembered|structured)\s+)?"
    r"(?:(?:note|memory)\s+)?(?P<query>.+?)\s+(?:to|as|is)\s+(?P<value>.+)",
    re.I,
)
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
    r"\b(?:who|whose|what)\b.*\b(?:pickup|pick\s+up|picked|turn)\b.*\b(?:dinner|food)\b|"
    r"\b(?:dinner|food)\b.*\b(?:pickup|turn)\b.*\b(?:who|whose|next|last)\b",
    re.I,
)
GENERIC_VALUE_QUERY_RE = re.compile(
    r"^\s*(?:what(?:'s|\s+is|\s+was)|whats)\b.*\b"
    r"(?:code|combination|confirmation|email|ending|login|number|passcode|password|pin|username)\b",
    re.I,
)
EXPLICIT_MEMORY_QUERY_RE = re.compile(
    r"^\s*(?:"
    r"(?:find|look\s+up|lookup|search|show(?:\s+me)?)\s+"
    r"(?:my\s+)?(?:remembered\s+|structured\s+)(?:note|notes|memory|memories)"
    r")\b.+",
    re.I,
)
REMEMBER_ABOUT_QUERY_RE = re.compile(r"^\s*(?:what\s+)?do\s+you\s+remember\s+about\b.+", re.I)
MEMORY_SEARCH_COMMAND_RE = re.compile(
    r"^\s*(?:find|look\s+up|lookup|search|show(?:\s+me)?)\s+"
    r"(?:my\s+)?(?:note|notes|memory|memories)\b.+",
    re.I,
)
MEMORY_QUERY_STOP_WORDS = frozenset(
    {
        "a",
        "about",
    "an",
    "and",
    "any",
    "again",
    "current",
    "currently",
    "do",
    "does",
    "find",
    "for",
        "have",
        "i",
        "is",
        "it",
        "look",
        "lookup",
        "me",
    "memory",
    "memories",
    "my",
    "now",
    "note",
    "notes",
    "of",
    "please",
    "remember",
    "remembered",
    "saved",
    "search",
    "show",
    "still",
    "stored",
    "structured",
    "that",
    "the",
    "today",
    "tomorrow",
    "to",
    "up",
    "was",
    "what",
    "whats",
    "yesterday",
    "you",
    }
)
NON_MEMORY_VALUE_TERMS = frozenset({"area", "auth", "barcode", "qr", "verification", "zip", "zipcode"})
HARD_NON_MEMORY_MUTATION_TERMS = frozenset({"calendar", "task", "todo"})
SOFT_NON_MEMORY_MUTATION_TERMS = frozenset({"appointment", "event"})
GENERIC_LOOKUP_TERMS = frozenset(
    {
        "code",
        "combination",
        "confirmation",
        "email",
        "ending",
        "login",
        "number",
        "passcode",
        "password",
        "pin",
        "username",
    }
)
PICKUP_OWNER_QUERY_TERMS = frozenset({"last", "next", "owner", "picked", "turn", "who", "whose"})
LOOKUP_TERM_ALIASES = {
    "code": frozenset({"code", "combination", "passcode", "pin"}),
    "combination": frozenset({"code", "combination", "passcode", "pin"}),
    "confirmation": frozenset({"confirmation"}),
    "email": frozenset({"email"}),
    "ending": frozenset({"ending"}),
    "login": frozenset({"login", "username"}),
    "number": frozenset({"number"}),
    "passcode": frozenset({"code", "combination", "passcode", "pin"}),
    "password": frozenset({"password"}),
    "pin": frozenset({"code", "combination", "passcode", "pin"}),
    "username": frozenset({"login", "username"}),
}


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


@dataclass(frozen=True)
class StructuredMemoryMutationResult:
    reply: str
    item: MemoryItem | None = None
    previous_item: MemoryItem | None = None


class SQLiteStructuredMemoryStore:
    def __init__(self, db_path: str | Path = DEFAULT_DB_FILE, *, read_only: bool = False):
        self.db_path = Path(db_path)
        self.read_only = read_only
        if not read_only:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        if self.read_only:
            connection = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        else:
            connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            if not self.read_only:
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
            created_at=(created_at or datetime.now().astimezone()).isoformat(timespec="microseconds"),
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

    def restore_item(self, item: MemoryItem) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO n4os_memory_items (
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

    def get_item(self, item_id: str) -> MemoryItem | None:
        items = self._items(
            """
            SELECT * FROM n4os_memory_items
            WHERE id = :id
            LIMIT 1
            """,
            {"id": item_id},
        )
        return items[0] if items else None

    def delete_item(self, item_id: str) -> MemoryItem | None:
        item = self.get_item(item_id)
        if item is None:
            return None
        with self._connection() as connection:
            connection.execute(
                """
                DELETE FROM n4os_memory_items
                WHERE id = :id
                """,
                {"id": item_id},
            )
        return item

    def replace_item(self, item: MemoryItem, *, text: str, value: str) -> MemoryItem:
        updated = MemoryItem(
            id=item.id,
            kind=item.kind,
            subject=item.subject,
            actor=item.actor,
            value=value,
            happened_on=item.happened_on,
            applies_on=item.applies_on,
            status=item.status,
            confidence=item.confidence,
            source=item.source,
            text=text,
            created_at=datetime.now().astimezone().isoformat(timespec="microseconds"),
        )
        self.restore_item(updated)
        return updated

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

    def has_dinner_pickup_memory(self) -> bool:
        items = self._items(
            """
            SELECT * FROM n4os_memory_items
            WHERE subject = 'dinner_pickup'
            LIMIT 1
            """,
            {},
        )
        return bool(items)

    def search_notes(self, *, query: str, limit: int) -> list[MemoryItem]:
        return self._search_items(query=query, limit=limit, notes_only=True)

    def search_memories(self, *, query: str, limit: int) -> list[MemoryItem]:
        return self._search_items(query=query, limit=limit, notes_only=False)

    def _search_items(self, *, query: str, limit: int, notes_only: bool) -> list[MemoryItem]:
        terms = _memory_query_terms(query)
        if not terms:
            return []
        where_clause = "WHERE kind = 'note'" if notes_only else ""
        candidates = self._items(
            f"""
            SELECT * FROM n4os_memory_items
            {where_clause}
            ORDER BY created_at DESC
            """,
            {},
        )
        scored: list[tuple[int, str, str, MemoryItem]] = []
        specific_terms = [term for term in terms if term not in GENERIC_LOOKUP_TERMS]
        lookup_terms = [term for term in terms if term in GENERIC_LOOKUP_TERMS]
        for item in candidates:
            haystack_terms = set(_memory_query_terms(item.text))
            if lookup_terms and not _has_lookup_term_match(lookup_terms, haystack_terms):
                continue
            specific_score = sum(1 for term in specific_terms if term in haystack_terms)
            if specific_terms and specific_score < len(specific_terms):
                continue
            score = sum(1 for term in terms if term in haystack_terms)
            if score:
                scored.append((score, item.created_at, item.id, item))
        scored.sort(key=lambda entry: (entry[0], entry[1], entry[2]), reverse=True)
        return [item for _, _, _, item in scored[:limit]]

    def _items(self, query: str, params: dict[str, object]) -> list[MemoryItem]:
        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [MemoryItem(**dict(row)) for row in rows]


def is_structured_remember_message(text: str) -> bool:
    return bool(REMEMBER_RE.match(text.strip()))


def is_structured_memory_mutation_message(text: str) -> bool:
    stripped = text.strip()
    lowered = stripped.lower()
    forget_match = FORGET_RE.match(stripped)
    if forget_match:
        if _has_explicit_memory_mutation_wrapper(lowered):
            return True
        if lowered.startswith(("delete ", "/delete ", "remove ", "/remove ")):
            return False
        query_terms = _memory_query_terms(forget_match.group("query"))
        has_lookup_term = any(term in GENERIC_LOOKUP_TERMS for term in query_terms)
        if _looks_like_non_memory_mutation(query_terms, has_lookup_term):
            return False
        specific_terms = [term for term in query_terms if term not in GENERIC_LOOKUP_TERMS]
        return len(specific_terms) >= 2 or (
            bool(specific_terms) and has_lookup_term
        )
    update_match = UPDATE_RE.match(stripped)
    if not update_match:
        return False
    if _has_explicit_memory_mutation_wrapper(lowered):
        return True
    query_terms = _memory_query_terms(update_match.group("query"))
    has_lookup_term = any(term in GENERIC_LOOKUP_TERMS for term in query_terms)
    if _looks_like_non_memory_mutation(query_terms, has_lookup_term):
        return False
    specific_terms = [term for term in query_terms if term not in GENERIC_LOOKUP_TERMS]
    return len(specific_terms) >= 2 or (
        bool(specific_terms) and has_lookup_term
    )


def has_structured_memory_mutation_match(
    text: str,
    *,
    n4os_root: Path = DEFAULT_N4OS_ROOT,
) -> bool:
    stripped = text.strip()
    lowered = stripped.lower()
    if lowered.startswith(("delete ", "/delete ", "remove ", "/remove ")) and not re.search(
        r"^/?(?:forget|delete|remove)\s+(?:the\s+)?"
        r"(?:(?:remembered|structured)\s+)?(?:note|notes|memories|memory(?!\s+card\b))\b",
        lowered,
    ):
        return False
    match = FORGET_RE.match(stripped) or UPDATE_RE.match(stripped)
    if not match:
        return False
    db_path = _db_path(n4os_root)
    if not db_path.exists() or not _memory_table_exists(db_path):
        return False
    query = match.group("query").strip()
    query_terms = _memory_query_terms(query)
    has_lookup_term = any(term in GENERIC_LOOKUP_TERMS for term in query_terms)
    specific_terms = [term for term in query_terms if term not in GENERIC_LOOKUP_TERMS]
    if len(specific_terms) < 2 and not (specific_terms and has_lookup_term):
        return False
    has_explicit_memory_word = _has_explicit_memory_mutation_wrapper(lowered)
    if _looks_like_non_memory_mutation(query_terms, has_lookup_term) and not has_explicit_memory_word:
        return False
    store = SQLiteStructuredMemoryStore(db_path, read_only=True)
    return bool(store.search_memories(query=query, limit=1))


def is_structured_memory_query(text: str) -> bool:
    stripped = text.strip()
    return bool(
        LAST_PICKUPS_RE.search(stripped)
        or PICKUP_QUERY_RE.search(stripped)
        or _is_explicit_memory_search_query(stripped)
        or _is_memory_search_command_query(stripped)
    )


def has_structured_memory_query_match(
    text: str,
    *,
    n4os_root: Path = DEFAULT_N4OS_ROOT,
) -> bool:
    stripped = text.strip()
    if _looks_like_explicit_memory_query(stripped) and not _is_explicit_memory_search_query(stripped):
        return False
    if (
        not _looks_like_generic_memory_query(stripped)
        and not _is_memory_search_command_query(stripped)
        and not _looks_like_remember_about_query(stripped)
    ):
        return False
    db_path = _db_path(n4os_root)
    if not db_path.exists() or not _memory_table_exists(db_path):
        return False
    store = SQLiteStructuredMemoryStore(db_path, read_only=True)
    if (
        _looks_like_explicit_memory_query(stripped)
        or _is_memory_search_command_query(stripped)
        or _looks_like_remember_about_query(stripped)
    ):
        if _looks_like_remember_about_query(stripped) and not _has_specific_remember_about_target(stripped):
            return False
        matches = store.search_memories(query=stripped, limit=1)
    else:
        matches = store.search_notes(query=stripped, limit=1)
    if matches:
        return True
    return bool(
        MEMORY_SEARCH_COMMAND_RE.search(stripped)
        and DINNER_PICKUP_RE.search(stripped)
        and store.has_dinner_pickup_memory()
    )


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


def mutate_structured_memory(
    text: str,
    *,
    n4os_root: Path = DEFAULT_N4OS_ROOT,
) -> StructuredMemoryMutationResult:
    stripped = text.strip()
    forget_match = FORGET_RE.match(stripped)
    if forget_match:
        query = forget_match.group("query").strip()
        if not _has_specific_memory_terms(query):
            return StructuredMemoryMutationResult(reply=_mutation_needs_more_detail_reply())
        store = SQLiteStructuredMemoryStore(_db_path(n4os_root))
        matches = store.search_memories(query=query, limit=3)
        if not matches:
            return StructuredMemoryMutationResult(
                reply="I do not have a structured memory matching that yet. Use /remember to save it.",
            )
        if len(matches) > 1:
            return StructuredMemoryMutationResult(reply=_format_ambiguous_memory_matches(matches))
        deleted = store.delete_item(matches[0].id)
        if deleted is None:
            return StructuredMemoryMutationResult(
                reply="I do not have a structured memory matching that yet. Use /remember to save it.",
            )
        return StructuredMemoryMutationResult(
            reply=f"Forgot structured memory: {_memory_display_value(deleted)}.",
            previous_item=deleted,
        )

    update_match = UPDATE_RE.match(stripped)
    if update_match:
        query = update_match.group("query").strip()
        new_value = update_match.group("value").strip(" .")
        if not _has_specific_memory_terms(query):
            return StructuredMemoryMutationResult(reply=_mutation_needs_more_detail_reply())
        store = SQLiteStructuredMemoryStore(_db_path(n4os_root))
        matches = store.search_memories(query=query, limit=3)
        if not matches:
            return StructuredMemoryMutationResult(
                reply="I do not have a structured memory matching that yet. Use /remember to save it.",
            )
        if len(matches) > 1:
            return StructuredMemoryMutationResult(reply=_format_ambiguous_memory_matches(matches))
        previous = matches[0]
        if previous.kind != "note" or (
            previous.subject == "dinner_pickup" and UNRESOLVED_RE.search(previous.text)
        ):
            return StructuredMemoryMutationResult(
                reply="I can only update structured notes right now. Use forget and remember to replace dinner pickup records.",
            )
        updated_text = _updated_memory_text(previous, query=query, new_value=new_value)
        updated = store.replace_item(previous, text=updated_text, value=updated_text)
        return StructuredMemoryMutationResult(
            reply=f"Updated structured memory: {_memory_display_value(updated)}.",
            item=updated,
            previous_item=previous,
        )

    return StructuredMemoryMutationResult(reply="I do not know which structured memory to change.")


def delete_structured_memory_item(
    item_id: str,
    *,
    n4os_root: Path = DEFAULT_N4OS_ROOT,
) -> MemoryItem | None:
    store = SQLiteStructuredMemoryStore(_db_path(n4os_root))
    return store.delete_item(item_id)


def get_structured_memory_item(
    item_id: str,
    *,
    n4os_root: Path = DEFAULT_N4OS_ROOT,
) -> MemoryItem | None:
    db_path = _db_path(n4os_root)
    if not db_path.exists() or not _memory_table_exists(db_path):
        return None
    store = SQLiteStructuredMemoryStore(db_path, read_only=True)
    return store.get_item(item_id)


def has_structured_memory_conflict(
    item: MemoryItem,
    *,
    n4os_root: Path = DEFAULT_N4OS_ROOT,
) -> bool:
    db_path = _db_path(n4os_root)
    if not db_path.exists() or not _memory_table_exists(db_path):
        return False
    store = SQLiteStructuredMemoryStore(db_path, read_only=True)
    conflict_query = _memory_conflict_query(item)
    query_terms = set(_memory_query_terms(conflict_query))
    matches = store.search_memories(query=conflict_query, limit=5)
    for match in matches:
        if match.id == item.id:
            continue
        if _conflict_extra_qualifiers(match.text, query_terms):
            continue
        return True
    return False


def restore_structured_memory_item(
    item: MemoryItem,
    *,
    n4os_root: Path = DEFAULT_N4OS_ROOT,
) -> None:
    store = SQLiteStructuredMemoryStore(_db_path(n4os_root))
    store.restore_item(item)


def same_structured_memory_item(left: MemoryItem | None, right: MemoryItem | None) -> bool:
    return left == right


def format_structured_memory_query(
    text: str,
    *,
    n4os_root: Path = DEFAULT_N4OS_ROOT,
) -> str:
    count_match = LAST_PICKUPS_RE.search(text)
    is_pickup_query = bool(count_match or PICKUP_QUERY_RE.search(text) or DINNER_PICKUP_RE.search(text))
    if _looks_like_explicit_memory_query(text) and not _is_explicit_memory_search_query(text):
        return "I do not have a structured memory matching that yet. Use /remember to save it."
    if MEMORY_SEARCH_COMMAND_RE.search(text) and not _is_memory_search_command_query(text):
        return "I do not have a structured memory matching that yet. Use /remember to save it."
    is_explicit_memory_query = (
        _is_explicit_memory_search_query(text)
        or _is_memory_search_command_query(text)
        or _looks_like_remember_about_query(text)
    )
    db_path = _db_path(n4os_root)
    read_only_query = (
        is_explicit_memory_query
        or _looks_like_generic_memory_query(text)
        or _looks_like_remember_about_query(text)
    )
    if read_only_query and (not db_path.exists() or not _memory_table_exists(db_path)):
        return "I do not have a structured memory matching that yet. Use /remember to save it."
    store = SQLiteStructuredMemoryStore(db_path, read_only=read_only_query)
    if is_explicit_memory_query and not count_match:
        if _has_specific_memory_terms(text):
            matches = store.search_memories(query=text, limit=3)
            if matches:
                return _format_memory_matches(matches)
            if is_pickup_query and _is_generic_pickup_owner_memory_search(text):
                assignment = store.latest_dinner_pickup_assignment()
                if assignment and assignment.status == "active" and assignment.actor:
                    return _format_memory_matches([assignment])
                events = store.dinner_pickup_events(limit=1)
                if events:
                    return _format_memory_matches(events)
        return "I do not have a structured memory matching that yet. Use /remember to save it."
    is_generic_value_query = _looks_like_generic_memory_query(text) and any(
        term in GENERIC_LOOKUP_TERMS for term in _memory_query_terms(text)
    )
    if is_generic_value_query:
        matches = store.search_notes(query=text, limit=3)
        if matches:
            if len(matches) > 1 and _generic_value_query_is_ambiguous(text, matches):
                return _format_ambiguous_memory_matches(matches)
            matches = matches[:1]
            return _format_memory_matches(matches)
        return "I do not have a structured memory matching that yet. Use /remember to save it."
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
    if is_pickup_query and assignment and assignment.status == "active" and assignment.actor:
        label = "Next dinner pickup"
        if assignment.applies_on:
            label += f" ({assignment.applies_on})"
        return f"{label}: {assignment.actor}.\nSource: {assignment.source}."

    notes = [
        note
        for note in store.dinner_pickup_notes(limit=3)
        if UNRESOLVED_RE.search(note.text)
    ]
    if is_pickup_query and notes:
        lines = ["I do not have a locked dinner pickup owner yet.", "", "Current memory:"]
        for note in notes:
            lines.append(f"- {note.value}")
        return "\n".join(lines)

    events = store.dinner_pickup_events(limit=1)
    if is_pickup_query and events:
        latest = events[0]
        return (
            "I do not have the next dinner pickup owner locked.\n"
            f"Most recent recorded pickup: {latest.happened_on}: {latest.actor or latest.value}."
        )
    if is_pickup_query:
        return "I do not have dinner pickup memory yet. Use /remember to save who picked up dinner or who has the next turn."

    matches = store.search_notes(query=text, limit=3)
    if not matches:
        return "I do not have a structured memory matching that yet. Use /remember to save it."
    return _format_memory_matches(matches)


def _format_memory_matches(matches: list[MemoryItem]) -> str:
    if len(matches) == 1:
        match = matches[0]
        label = "note" if match.kind == "note" else "memory"
        return f"Remembered {label}: {_memory_display_value(match)}\nSource: {match.source}."
    lines = ["Matching structured memories:"]
    for index, match in enumerate(matches, start=1):
        lines.append(f"{index}. {_memory_display_value(match)}")
    return "\n".join(lines)


def _format_ambiguous_memory_matches(matches: list[MemoryItem]) -> str:
    lines = ["I found multiple matching structured memories. Ask with more detail:"]
    for index, match in enumerate(matches, start=1):
        lines.append(f"{index}. {_memory_display_value(match)}")
    return "\n".join(lines)


def _memory_display_value(match: MemoryItem) -> str:
    if match.kind == "dinner_pickup_assignment":
        label = "Next dinner pickup"
        if match.applies_on:
            label += f" ({match.applies_on})"
        return f"{label}: {match.actor or match.value}"
    if match.kind == "dinner_pickup_event":
        date_label = f"{match.happened_on}: " if match.happened_on else ""
        return f"Dinner pickup: {date_label}{match.actor or match.value}"
    return match.value


def _updated_memory_text(previous: MemoryItem, *, query: str, new_value: str) -> str:
    previous_text = previous.text.strip()
    lookup_terms = [term for term in _memory_query_terms(query) if term in GENERIC_LOOKUP_TERMS]
    alias_terms = sorted(
        {
            alias
            for term in lookup_terms
            for alias in LOOKUP_TERM_ALIASES.get(term, frozenset({term}))
        },
        key=len,
        reverse=True,
    )
    if alias_terms:
        lookup_pattern = "|".join(re.escape(term) for term in alias_terms)
        match = re.search(rf"\b(?:{lookup_pattern})\b", previous_text, re.I)
        if match:
            original_suffix = previous_text[match.end() :]
            suffix_match = re.search(r"(?:\b(?:is|to|as)\b|[=:])", original_suffix, re.I)
            if suffix_match:
                prefix = previous_text[: match.end() + suffix_match.end()].rstrip()
                return f"{prefix} {new_value}"
            prefix = previous_text[: match.end()].rstrip()
            return f"{prefix} {new_value}"
    query_terms = _memory_query_terms(query)
    for term in reversed(query_terms):
        if term in GENERIC_LOOKUP_TERMS:
            continue
        term_pattern = r"allerg(?:y|ic|ies)?(?:\s+to)?" if term == "allerg" else re.escape(term)
        match = re.search(rf"\b{term_pattern}\b", previous_text, re.I)
        if not match:
            continue
        if term == "allerg" and previous_text[: match.end()].lower().endswith(" to"):
            prefix = previous_text[: match.end()].rstrip()
            return f"{prefix} {new_value}"
        original_suffix = previous_text[match.end() :]
        suffix_match = re.search(r"(?:\b(?:is|to|as)\b|[=:])", original_suffix, re.I)
        if suffix_match:
            prefix = previous_text[: match.end() + suffix_match.end()].rstrip()
            return f"{prefix} {new_value}"
    connector_matches = list(re.finditer(r"(?:\b(?:is|to|as)\b|[=:])", previous_text, re.I))
    if connector_matches:
        prefix = previous_text[: connector_matches[-1].end()].rstrip()
        return f"{prefix} {new_value}"
    prefix = previous_text.rstrip()
    return f"{prefix} {new_value}"


def _mutation_needs_more_detail_reply() -> str:
    return "Please include which structured memory to change, like `forget learning code` or `update guest wifi password to ...`."


def _has_explicit_memory_mutation_wrapper(lowered: str) -> bool:
    return bool(
        re.match(
            r"^/?(?:forget|delete|remove|update|change|correct)\s+(?:the\s+)?"
            r"(?:(?:remembered|structured)\s+)?(?:note|notes|memories|memory(?!\s+card\b))\b",
            lowered,
        )
    )


def _looks_like_non_memory_mutation(query_terms: list[str], has_lookup_term: bool) -> bool:
    if any(term in HARD_NON_MEMORY_MUTATION_TERMS for term in query_terms):
        return True
    return any(term in SOFT_NON_MEMORY_MUTATION_TERMS for term in query_terms) and not has_lookup_term


def _memory_conflict_query(item: MemoryItem) -> str:
    terms = _memory_query_terms(item.text)
    lookup_indexes = [index for index, term in enumerate(terms) if term in GENERIC_LOOKUP_TERMS]
    if lookup_indexes:
        last_lookup_index = lookup_indexes[-1]
        context_terms = [
            term
            for term in terms[: last_lookup_index + 1]
            if term in GENERIC_LOOKUP_TERMS or not re.fullmatch(r"\d+", term)
        ]
        return " ".join(context_terms)
    raw_words = re.findall(r"[a-z0-9]+", _normalize_memory_query_text(item.text))
    connector_indexes = [index for index, term in enumerate(raw_words) if term in {"is", "to", "as"}]
    if connector_indexes:
        context_terms = [
            term
            for term in raw_words[: connector_indexes[-1]]
            if term not in MEMORY_QUERY_STOP_WORDS and not re.fullmatch(r"\d+", term)
        ]
        if context_terms:
            return " ".join(context_terms)
    return item.text


def _conflict_extra_qualifiers(text: str, query_terms: set[str]) -> set[str]:
    terms = _memory_query_terms(text)
    if any(term in GENERIC_LOOKUP_TERMS for term in terms):
        return {
            term
            for term in terms
            if term not in query_terms and term not in GENERIC_LOOKUP_TERMS and not re.fullmatch(r"\d+", term)
        }
    raw_words = re.findall(r"[a-z0-9]+", _normalize_memory_query_text(text))
    connector_indexes = [index for index, term in enumerate(raw_words) if term in {"is", "to", "as"}]
    if connector_indexes:
        return {
            term
            for term in raw_words[: connector_indexes[-1]]
            if term not in query_terms
            and term not in MEMORY_QUERY_STOP_WORDS
            and not re.fullmatch(r"\d+", term)
        }
    return set()


def _db_path(n4os_root: Path) -> Path:
    return n4os_root.parent / "data" / "n4os.db"


def _memory_table_exists(db_path: Path) -> bool:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        row = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'n4os_memory_items'
            LIMIT 1
            """,
        ).fetchone()
    except sqlite3.Error:
        return False
    finally:
        if connection is not None:
            connection.close()
    return row is not None


def _strip_remember_prefix(text: str) -> str:
    return REMEMBER_RE.sub("", text.strip(), count=1).strip(" :-")


def _memory_query_terms(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", _normalize_memory_query_text(text))
    terms: list[str] = []
    for word in words:
        if len(word) < 2 or word in MEMORY_QUERY_STOP_WORDS:
            continue
        if word not in terms:
            terms.append(word)
    return terms


def _normalize_memory_query_text(text: str) -> str:
    normalized = text.lower()
    normalized = normalized.replace("\u2010", "-").replace("\u2011", "-").replace("\u2013", "-").replace(
        "\u2014",
        "-",
    )
    normalized = re.sub(r"\bwi[\s\-_]*fi\b", "wifi", normalized)
    normalized = re.sub(r"\be[\s\-_]*mail\b", "email", normalized)
    normalized = re.sub(r"\ballerg(?:y|ic|ies)\b", "allerg", normalized)
    normalized = re.sub(r"\bpick(?:ed|ing)?\s+up\b", "pickup", normalized)
    normalized = re.sub(r"\bpicked\b", "pickup", normalized)
    return normalized


def _looks_like_generic_memory_query(text: str) -> bool:
    if _looks_like_explicit_memory_query(text) or _is_memory_search_command_query(text):
        return bool(_memory_query_terms(text))
    if not GENERIC_VALUE_QUERY_RE.search(text):
        return False
    terms = _memory_query_terms(text)
    if any(term in NON_MEMORY_VALUE_TERMS for term in terms):
        return False
    return any(term in GENERIC_LOOKUP_TERMS for term in terms) and any(
        term not in GENERIC_LOOKUP_TERMS for term in terms
    )


def _looks_like_explicit_memory_query(text: str) -> bool:
    return bool(EXPLICIT_MEMORY_QUERY_RE.search(text))


def _is_explicit_memory_search_query(text: str) -> bool:
    return _looks_like_explicit_memory_query(text) and _has_search_command_specificity(text)


def _looks_like_remember_about_query(text: str) -> bool:
    return bool(REMEMBER_ABOUT_QUERY_RE.search(text))


def _has_lookup_term_match(lookup_terms: list[str], haystack_terms: set[str]) -> bool:
    return any(LOOKUP_TERM_ALIASES.get(term, frozenset({term})) & haystack_terms for term in lookup_terms)


def _generic_value_query_is_ambiguous(query: str, matches: list[MemoryItem]) -> bool:
    query_terms = set(_memory_query_terms(query))
    match_values = {match.value for match in matches}
    if len(match_values) > 1:
        return True
    specific_terms = [term for term in query_terms if term not in GENERIC_LOOKUP_TERMS]
    for term in specific_terms:
        values_for_term = {
            _term_context_after(term, match.text)
            for match in matches
            if term in _memory_query_terms(match.text)
        }
        if len(values_for_term) > 1:
            return True
    match_extra_terms = {
        tuple(_qualifier_terms_before_lookup(match.text, query_terms))
        for match in matches
    }
    return len(match_extra_terms) > 1


def _qualifier_terms_before_lookup(text: str, query_terms: set[str]) -> list[str]:
    terms = _memory_query_terms(text)
    first_lookup_index = next(
        (index for index, term in enumerate(terms) if term in GENERIC_LOOKUP_TERMS),
        len(terms),
    )
    return [
        term
        for term in terms[:first_lookup_index]
        if term not in query_terms and not re.fullmatch(r"\d+", term)
    ]


def _term_context_after(term: str, text: str) -> str:
    words = _memory_query_terms(text)
    try:
        index = words.index(term)
    except ValueError:
        return ""
    return words[index + 1] if index + 1 < len(words) else ""


def _is_memory_search_command_query(text: str) -> bool:
    if not MEMORY_SEARCH_COMMAND_RE.search(text):
        return False
    return _has_search_command_specificity(text)


def _has_search_command_specificity(text: str) -> bool:
    terms = _memory_query_terms(text)
    specific_terms = _specific_memory_terms(text)
    if any(term in GENERIC_LOOKUP_TERMS for term in terms):
        return bool(specific_terms)
    return len(specific_terms) >= 2


def _has_specific_memory_terms(text: str) -> bool:
    return bool(_specific_memory_terms(text))


def _has_specific_remember_about_target(text: str) -> bool:
    terms = _memory_query_terms(text)
    specific_terms = [term for term in terms if term not in GENERIC_LOOKUP_TERMS]
    lookup_terms = [term for term in terms if term in GENERIC_LOOKUP_TERMS]
    return len(specific_terms) >= 2 or (bool(specific_terms) and bool(lookup_terms))


def _specific_memory_terms(text: str) -> list[str]:
    return [term for term in _memory_query_terms(text) if term not in GENERIC_LOOKUP_TERMS]


def _is_pickup_owner_query(text: str) -> bool:
    terms = set(_memory_query_terms(text))
    return bool(terms & PICKUP_OWNER_QUERY_TERMS)


PICKUP_SEARCH_CONTEXT_TERMS = frozenset({"dinner", "food", "pickup"} | set(PICKUP_OWNER_QUERY_TERMS))


def _is_generic_pickup_owner_memory_search(text: str) -> bool:
    terms = set(_memory_query_terms(text))
    return bool(terms & PICKUP_OWNER_QUERY_TERMS) and terms <= PICKUP_SEARCH_CONTEXT_TERMS


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

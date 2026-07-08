from __future__ import annotations

from contextlib import contextmanager
from datetime import date as Date, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_FILE = ROOT / "data" / "n4os.db"


class SQLiteLibraryProvider:
    """SQLite provider for Nysha's independent-reading events and family library bags."""

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
                CREATE TABLE IF NOT EXISTS library_reading_events (
                    id TEXT PRIMARY KEY,
                    child TEXT NOT NULL,
                    date TEXT NOT NULL,
                    book TEXT NOT NULL,
                    minutes INTEGER,
                    pages INTEGER,
                    reaction TEXT,
                    status TEXT NOT NULL CHECK (status IN ('in_progress', 'completed', 'unknown')),
                    source TEXT NOT NULL CHECK (source IN ('telegram_text', 'telegram_voice', 'telegram_photo')),
                    photo_path TEXT,
                    raw_input TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """,
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_library_reading_events_date
                ON library_reading_events(date, created_at)
                """,
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS library_visits (
                    id TEXT PRIMARY KEY,
                    visit_date TEXT NOT NULL,
                    due_date TEXT,
                    titles_json TEXT NOT NULL,
                    source TEXT NOT NULL CHECK (source IN ('telegram_text', 'telegram_voice', 'telegram_photo')),
                    raw_input TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """,
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_library_visits_date
                ON library_visits(visit_date, created_at)
                """,
            )

    def add_event(
        self,
        *,
        child: str,
        date: str | Date,
        book: str,
        minutes: int | None,
        pages: int | None,
        reaction: str | None,
        status: str,
        source: str,
        photo_path: str | None,
        raw_input: str,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        event = {
            "id": uuid4().hex,
            "child": child,
            "date": date.isoformat() if isinstance(date, Date) else date,
            "book": book,
            "minutes": minutes,
            "pages": pages,
            "reaction": reaction,
            "status": status,
            "source": source,
            "photo_path": photo_path,
            "raw_input": raw_input,
            "created_at": created_at or datetime.now().astimezone().isoformat(),
        }
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO library_reading_events (
                    id,
                    child,
                    date,
                    book,
                    minutes,
                    pages,
                    reaction,
                    status,
                    source,
                    photo_path,
                    raw_input,
                    created_at
                )
                VALUES (
                    :id,
                    :child,
                    :date,
                    :book,
                    :minutes,
                    :pages,
                    :reaction,
                    :status,
                    :source,
                    :photo_path,
                    :raw_input,
                    :created_at
                )
                """,
                event,
            )
        return event

    def list_events(
        self,
        *,
        child: str | None = None,
        start_date: str | Date | None = None,
        end_date: str | Date | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: dict[str, Any] = {}
        if child is not None:
            clauses.append("child = :child")
            params["child"] = child
        if start_date is not None:
            clauses.append("date >= :start_date")
            params["start_date"] = start_date.isoformat() if isinstance(start_date, Date) else start_date
        if end_date is not None:
            clauses.append("date <= :end_date")
            params["end_date"] = end_date.isoformat() if isinstance(end_date, Date) else end_date
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        query = (
            "SELECT * FROM library_reading_events"
            + where
            + " ORDER BY date DESC, created_at DESC"
        )
        if limit is not None:
            query += " LIMIT :limit"
            params["limit"] = max(1, int(limit))
        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def add_visit(
        self,
        *,
        visit_date: str | Date,
        due_date: str | Date | None,
        titles: list[str],
        source: str,
        raw_input: str,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        visit = {
            "id": uuid4().hex,
            "visit_date": visit_date.isoformat() if isinstance(visit_date, Date) else visit_date,
            "due_date": due_date.isoformat() if isinstance(due_date, Date) else due_date,
            "titles": titles,
            "titles_json": json.dumps(titles),
            "source": source,
            "raw_input": raw_input,
            "created_at": created_at or datetime.now().astimezone().isoformat(),
        }
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO library_visits (
                    id,
                    visit_date,
                    due_date,
                    titles_json,
                    source,
                    raw_input,
                    created_at
                )
                VALUES (
                    :id,
                    :visit_date,
                    :due_date,
                    :titles_json,
                    :source,
                    :raw_input,
                    :created_at
                )
                """,
                visit,
            )
        return {key: value for key, value in visit.items() if key != "titles_json"}

    def latest_visit(self) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM library_visits
                ORDER BY visit_date DESC, created_at DESC
                LIMIT 1
                """,
            ).fetchone()
        if row is None:
            return None
        visit = dict(row)
        visit["titles"] = json.loads(str(visit.pop("titles_json") or "[]"))
        return visit

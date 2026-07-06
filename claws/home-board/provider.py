from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_FILE = ROOT / "data" / "n4os.db"


class SQLiteHomeBoardProvider:
    """SQLite provider for the N4OS Home Board."""

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
                CREATE TABLE IF NOT EXISTS home_board_items (
                    id TEXT PRIMARY KEY,
                    person_or_group TEXT NOT NULL,
                    message TEXT NOT NULL,
                    date TEXT NOT NULL,
                    context TEXT NOT NULL,
                    trigger TEXT,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'done')),
                    priority TEXT NOT NULL CHECK (priority IN ('low', 'medium', 'high')),
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    done_at TEXT
                )
                """,
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_home_board_items_date_status
                ON home_board_items(date, status, expires_at)
                """,
            )

    def add_item(
        self,
        *,
        person_or_group: str,
        message: str,
        date: str,
        context: str,
        trigger: str | None,
        priority: str,
        expires_at: str,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        now = created_at or datetime.now().astimezone().isoformat()
        item = {
            "id": uuid4().hex,
            "person_or_group": person_or_group,
            "message": message,
            "date": date,
            "context": context,
            "trigger": trigger,
            "status": "pending",
            "priority": priority,
            "created_at": now,
            "expires_at": expires_at,
            "done_at": None,
        }
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO home_board_items (
                    id,
                    person_or_group,
                    message,
                    date,
                    context,
                    trigger,
                    status,
                    priority,
                    created_at,
                    expires_at,
                    done_at
                )
                VALUES (
                    :id,
                    :person_or_group,
                    :message,
                    :date,
                    :context,
                    :trigger,
                    :status,
                    :priority,
                    :created_at,
                    :expires_at,
                    :done_at
                )
                """,
                item,
            )
        return item

    def list_items(
        self,
        *,
        date: str | None = None,
        status: str | None = "pending",
        include_expired: bool = False,
        now: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: dict[str, Any] = {}
        if date is not None:
            clauses.append("date = :date")
            params["date"] = date
        if status is not None:
            clauses.append("status = :status")
            params["status"] = status
        if not include_expired:
            clauses.append("expires_at > :now")
            params["now"] = now or datetime.now().astimezone().isoformat()

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        query = (
            "SELECT * FROM home_board_items"
            + where
            + """
              ORDER BY
                CASE priority
                  WHEN 'high' THEN 0
                  WHEN 'medium' THEN 1
                  ELSE 2
                END,
                person_or_group COLLATE NOCASE,
                created_at
              """
        )
        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def mark_done(
        self,
        item_id: str,
        *,
        done_at: str | None = None,
    ) -> dict[str, Any] | None:
        completed_at = done_at or datetime.now().astimezone().isoformat()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE home_board_items
                SET status = 'done', done_at = :done_at
                WHERE id = :id
                """,
                {"id": item_id, "done_at": completed_at},
            )
            row = connection.execute(
                "SELECT * FROM home_board_items WHERE id = :id",
                {"id": item_id},
            ).fetchone()
        return dict(row) if row is not None else None

    def mark_pending(self, item_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE home_board_items
                SET status = 'pending', done_at = NULL
                WHERE id = :id
                """,
                {"id": item_id},
            )
            row = connection.execute(
                "SELECT * FROM home_board_items WHERE id = :id",
                {"id": item_id},
            ).fetchone()
        return dict(row) if row is not None else None

    def delete_item(self, item_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM home_board_items WHERE id = :id",
                {"id": item_id},
            ).fetchone()
            if row is None:
                return None
            item = dict(row)
            connection.execute(
                "DELETE FROM home_board_items WHERE id = :id",
                {"id": item_id},
            )
        return item

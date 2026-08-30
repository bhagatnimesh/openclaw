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
STATUS_VALUES = {"assigned", "in_progress", "submitted", "archived"}


class SQLiteHomeworkProvider:
    """SQLite source of truth for captured homework assignments and artifacts."""

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
                CREATE TABLE IF NOT EXISTS homework_items (
                    id TEXT PRIMARY KEY,
                    child TEXT NOT NULL,
                    title TEXT NOT NULL,
                    subject TEXT,
                    assigned_date TEXT NOT NULL,
                    due_date TEXT,
                    status TEXT NOT NULL CHECK (status IN ('assigned', 'in_progress', 'submitted', 'archived')),
                    notes TEXT,
                    grade TEXT,
                    week_range TEXT,
                    daily_work TEXT,
                    metadata_json TEXT,
                    content_fingerprint TEXT,
                    raw_input TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """,
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS homework_assets (
                    id TEXT PRIMARY KEY,
                    homework_item_id TEXT NOT NULL REFERENCES homework_items(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL CHECK (kind IN ('assignment_photo', 'submission_photo', 'ocr_text')),
                    path TEXT,
                    ocr_text TEXT,
                    content_fingerprint TEXT,
                    photo_sha256 TEXT,
                    source TEXT NOT NULL CHECK (source IN ('telegram_text', 'telegram_voice', 'telegram_photo')),
                    created_at TEXT NOT NULL
                )
                """,
            )
            item_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(homework_items)").fetchall()
            }
            if "metadata_json" not in item_columns:
                connection.execute("ALTER TABLE homework_items ADD COLUMN metadata_json TEXT")
            if "content_fingerprint" not in item_columns:
                connection.execute("ALTER TABLE homework_items ADD COLUMN content_fingerprint TEXT")
            for name, definition in (
                ("record_type", "TEXT NOT NULL DEFAULT 'homework'"),
                ("class_name", "TEXT"),
                ("lesson_identifier", "TEXT"),
                ("parent_notes", "TEXT"),
                ("metadata_version", "INTEGER NOT NULL DEFAULT 1"),
            ):
                if name not in item_columns:
                    connection.execute(f"ALTER TABLE homework_items ADD COLUMN {name} {definition}")
            asset_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(homework_assets)").fetchall()
            }
            if "content_fingerprint" not in asset_columns:
                connection.execute("ALTER TABLE homework_assets ADD COLUMN content_fingerprint TEXT")
            if "photo_sha256" not in asset_columns:
                connection.execute("ALTER TABLE homework_assets ADD COLUMN photo_sha256 TEXT")
            if "page_index" not in asset_columns:
                connection.execute("ALTER TABLE homework_assets ADD COLUMN page_index INTEGER")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_observations (
                    id TEXT PRIMARY KEY,
                    homework_item_id TEXT NOT NULL REFERENCES homework_items(id) ON DELETE CASCADE,
                    category TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    origin TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('active', 'corrected', 'dismissed')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """,
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS homework_events (
                    id TEXT PRIMARY KEY,
                    homework_item_id TEXT NOT NULL REFERENCES homework_items(id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL CHECK (event_type IN ('assigned', 'submitted', 'note')),
                    note TEXT,
                    created_at TEXT NOT NULL
                )
                """,
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS homework_class_schedules (
                    id TEXT PRIMARY KEY,
                    child TEXT NOT NULL,
                    class_name TEXT NOT NULL,
                    weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
                    start_time TEXT,
                    due_rule TEXT NOT NULL CHECK (due_rule IN ('next_class', 'friday')),
                    calendar_name TEXT,
                    source TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(child, class_name)
                )
                """,
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_homework_items_child_due
                ON homework_items(child, due_date, updated_at)
                """,
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_homework_items_fingerprint
                ON homework_items(child, content_fingerprint)
                """,
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_homework_assets_item
                ON homework_assets(homework_item_id, created_at)
                """,
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_homework_class_schedules_child_class
                ON homework_class_schedules(child, class_name)
                """,
            )

    def capture_assignment(
        self,
        *,
        child: str,
        title: str,
        subject: str | None,
        assigned_date: str | Date,
        due_date: str | Date | None,
        status: str = "assigned",
        notes: str | None = None,
        grade: str | None = None,
        week_range: str | None = None,
        daily_work: str | None = None,
        metadata: dict[str, Any] | None = None,
        content_fingerprint: str | None = None,
        raw_input: str,
        source: str,
        photo_path: str | None = None,
        ocr_text: str | None = None,
        photo_sha256: str | None = None,
        created_at: str | None = None,
        record_type: str = "homework",
        class_name: str | None = None,
        lesson_identifier: str | None = None,
        parent_notes: str | None = None,
        photo_assets: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        timestamp = created_at or datetime.now().astimezone().isoformat()
        metadata_json = _metadata_json(metadata)
        item = {
            "id": uuid4().hex,
            "child": child,
            "title": title,
            "subject": subject,
            "assigned_date": _date_text(assigned_date),
            "due_date": _date_text(due_date),
            "status": status if status in STATUS_VALUES else "assigned",
            "notes": notes,
            "grade": grade,
            "week_range": week_range,
            "daily_work": daily_work,
            "metadata_json": metadata_json,
            "content_fingerprint": content_fingerprint,
            "raw_input": raw_input,
            "created_at": timestamp,
            "updated_at": timestamp,
            "record_type": record_type if record_type in {"homework", "lesson"} else "homework",
            "class_name": class_name,
            "lesson_identifier": lesson_identifier,
            "parent_notes": parent_notes,
            "metadata_version": 1,
        }
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO homework_items (
                    id, child, title, subject, assigned_date, due_date, status,
                    notes, grade, week_range, daily_work, metadata_json, content_fingerprint,
                    raw_input, created_at, updated_at, record_type, class_name,
                    lesson_identifier, parent_notes, metadata_version
                )
                VALUES (
                    :id, :child, :title, :subject, :assigned_date, :due_date, :status,
                    :notes, :grade, :week_range, :daily_work, :metadata_json, :content_fingerprint,
                    :raw_input, :created_at, :updated_at, :record_type, :class_name,
                    :lesson_identifier, :parent_notes, :metadata_version
                )
                """,
                item,
            )
            self._insert_event(connection, item["id"], "assigned", notes, timestamp)
            if not photo_assets:
                self._insert_assets(
                    connection,
                    item["id"],
                    "assignment_photo",
                    source,
                    photo_path,
                    ocr_text,
                    timestamp,
                    content_fingerprint=content_fingerprint,
                    photo_sha256=photo_sha256,
                )
            for page_index, asset in enumerate(photo_assets or [], start=1):
                self._insert_assets(
                    connection, item["id"], "assignment_photo", source,
                    asset.get("path"), asset.get("ocr_text"), timestamp,
                    content_fingerprint=asset.get("content_fingerprint"),
                    photo_sha256=asset.get("photo_sha256"), page_index=page_index,
                )
        return item

    def append_parent_note(self, *, homework_item_id: str, note: str, created_at: str | None = None) -> dict[str, Any] | None:
        timestamp = created_at or datetime.now().astimezone().isoformat()
        cleaned = " ".join(note.split()).strip()
        if not cleaned:
            return None
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM homework_items WHERE id = :id", {"id": homework_item_id}).fetchone()
            if row is None:
                return None
            previous = str(row["parent_notes"] or "").strip()
            parent_notes = f"{previous}\n{cleaned}" if previous else cleaned
            connection.execute("UPDATE homework_items SET parent_notes = :parent_notes, updated_at = :updated_at WHERE id = :id", {"id": homework_item_id, "parent_notes": parent_notes, "updated_at": timestamp})
            self._insert_event(connection, homework_item_id, "note", f"Parent note: {cleaned}", timestamp)
            updated = connection.execute("SELECT * FROM homework_items WHERE id = :id", {"id": homework_item_id}).fetchone()
        return dict(updated) if updated is not None else None

    def replace_learning_observations(self, *, homework_item_id: str, observations: list[dict[str, Any]], created_at: str | None = None) -> None:
        timestamp = created_at or datetime.now().astimezone().isoformat()
        with self._connection() as connection:
            connection.execute("DELETE FROM learning_observations WHERE homework_item_id = :id AND origin = 'ai'", {"id": homework_item_id})
            for observation in observations:
                connection.execute("""INSERT INTO learning_observations (id, homework_item_id, category, statement, evidence_json, confidence, origin, status, created_at, updated_at)
                VALUES (:id, :homework_item_id, :category, :statement, :evidence_json, :confidence, 'ai', 'active', :created_at, :updated_at)""", {
                    "id": uuid4().hex, "homework_item_id": homework_item_id,
                    "category": str(observation.get("category") or "learning_observation"),
                    "statement": str(observation.get("statement") or ""),
                    "evidence_json": _metadata_json({"evidence": observation.get("evidence") or []}) or "{}",
                    "confidence": max(0.0, min(float(observation.get("confidence") or 0), 1.0)),
                    "created_at": timestamp, "updated_at": timestamp,
                })

    def list_learning_observations(self, homework_item_id: str | None = None, *, child: str | None = None) -> list[dict[str, Any]]:
        where, params = "", {}
        if homework_item_id:
            where, params = "WHERE o.homework_item_id = :id", {"id": homework_item_id}
        elif child:
            where, params = "WHERE i.child = :child", {"child": child}
        with self._connection() as connection:
            rows = connection.execute("SELECT o.* FROM learning_observations o JOIN homework_items i ON i.id = o.homework_item_id " + where + " ORDER BY o.created_at DESC", params).fetchall()
        return [dict(row) for row in rows]

    def attach_assignment_asset(
        self,
        *,
        homework_item_id: str,
        source: str,
        raw_input: str,
        photo_path: str | None = None,
        ocr_text: str | None = None,
        content_fingerprint: str | None = None,
        photo_sha256: str | None = None,
        notes: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any] | None:
        timestamp = created_at or datetime.now().astimezone().isoformat()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM homework_items WHERE id = :id",
                {"id": homework_item_id},
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE homework_items
                SET updated_at = :updated_at
                WHERE id = :id
                """,
                {"id": homework_item_id, "updated_at": timestamp},
            )
            self._insert_event(connection, homework_item_id, "note", notes or "Attached matching homework capture.", timestamp)
            self._insert_assets(
                connection,
                homework_item_id,
                "assignment_photo",
                source,
                photo_path,
                ocr_text,
                timestamp,
                content_fingerprint=content_fingerprint,
                photo_sha256=photo_sha256,
            )
            updated = connection.execute(
                "SELECT * FROM homework_items WHERE id = :id",
                {"id": homework_item_id},
            ).fetchone()
        return dict(updated) if updated is not None else None

    def capture_submission(
        self,
        *,
        homework_item_id: str,
        source: str,
        raw_input: str,
        photo_path: str | None = None,
        ocr_text: str | None = None,
        content_fingerprint: str | None = None,
        photo_sha256: str | None = None,
        notes: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any] | None:
        timestamp = created_at or datetime.now().astimezone().isoformat()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM homework_items WHERE id = :id",
                {"id": homework_item_id},
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE homework_items
                SET status = 'submitted', updated_at = :updated_at
                WHERE id = :id
                """,
                {"id": homework_item_id, "updated_at": timestamp},
            )
            self._insert_event(connection, homework_item_id, "submitted", notes or raw_input, timestamp)
            self._insert_assets(
                connection,
                homework_item_id,
                "submission_photo",
                source,
                photo_path,
                ocr_text,
                timestamp,
                content_fingerprint=content_fingerprint,
                photo_sha256=photo_sha256,
            )
            updated = connection.execute(
                "SELECT * FROM homework_items WHERE id = :id",
                {"id": homework_item_id},
            ).fetchone()
        return dict(updated) if updated is not None else None

    def update_due_date(
        self,
        *,
        homework_item_id: str,
        due_date: str | Date,
        note: str | None = None,
        updated_at: str | None = None,
    ) -> dict[str, Any] | None:
        timestamp = updated_at or datetime.now().astimezone().isoformat()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM homework_items WHERE id = :id",
                {"id": homework_item_id},
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE homework_items
                SET due_date = :due_date, updated_at = :updated_at
                WHERE id = :id
                """,
                {
                    "id": homework_item_id,
                    "due_date": _date_text(due_date),
                    "updated_at": timestamp,
                },
            )
            self._insert_event(connection, homework_item_id, "note", note or "Due date updated.", timestamp)
            updated = connection.execute(
                "SELECT * FROM homework_items WHERE id = :id",
                {"id": homework_item_id},
            ).fetchone()
        return dict(updated) if updated is not None else None

    def update_assignment_details(
        self,
        *,
        homework_item_id: str,
        title: str | None = None,
        subject: str | None = None,
        due_date: str | Date | None = None,
        notes: str | None = None,
        grade: str | None = None,
        week_range: str | None = None,
        daily_work: str | None = None,
        metadata: dict[str, Any] | None = None,
        event_note: str | None = None,
        updated_at: str | None = None,
    ) -> dict[str, Any] | None:
        timestamp = updated_at or datetime.now().astimezone().isoformat()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM homework_items WHERE id = :id",
                {"id": homework_item_id},
            ).fetchone()
            if row is None:
                return None
            current = dict(row)
            merged_metadata = _merge_metadata(current.get("metadata_json"), metadata)
            connection.execute(
                """
                UPDATE homework_items
                SET
                    title = COALESCE(:title, title),
                    subject = COALESCE(:subject, subject),
                    due_date = COALESCE(:due_date, due_date),
                    notes = COALESCE(:notes, notes),
                    grade = COALESCE(:grade, grade),
                    week_range = COALESCE(:week_range, week_range),
                    daily_work = COALESCE(:daily_work, daily_work),
                    metadata_json = COALESCE(:metadata_json, metadata_json),
                    updated_at = :updated_at
                WHERE id = :id
                """,
                {
                    "id": homework_item_id,
                    "title": _clean_update_text(title),
                    "subject": _clean_update_text(subject),
                    "due_date": _date_text(due_date),
                    "notes": _clean_update_text(notes),
                    "grade": _clean_update_text(grade),
                    "week_range": _clean_update_text(week_range),
                    "daily_work": _clean_update_text(daily_work),
                    "metadata_json": _metadata_json(merged_metadata),
                    "updated_at": timestamp,
                },
            )
            self._insert_event(connection, homework_item_id, "note", event_note or "Homework details updated.", timestamp)
            updated = connection.execute(
                "SELECT * FROM homework_items WHERE id = :id",
                {"id": homework_item_id},
            ).fetchone()
        return dict(updated) if updated is not None else None

    def upsert_class_schedule(
        self,
        *,
        child: str,
        class_name: str,
        weekday: int,
        start_time: str | None = None,
        due_rule: str = "next_class",
        calendar_name: str | None = None,
        source: str = "manual",
        updated_at: str | None = None,
    ) -> dict[str, Any]:
        timestamp = updated_at or datetime.now().astimezone().isoformat()
        item = {
            "id": f"{_schedule_key(child)}:{_schedule_key(class_name)}",
            "child": child,
            "class_name": class_name,
            "weekday": int(weekday),
            "start_time": start_time,
            "due_rule": due_rule if due_rule in {"next_class", "friday"} else "next_class",
            "calendar_name": calendar_name,
            "source": source,
            "updated_at": timestamp,
        }
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO homework_class_schedules (
                    id, child, class_name, weekday, start_time, due_rule,
                    calendar_name, source, updated_at
                )
                VALUES (
                    :id, :child, :class_name, :weekday, :start_time, :due_rule,
                    :calendar_name, :source, :updated_at
                )
                ON CONFLICT(child, class_name) DO UPDATE SET
                    weekday = excluded.weekday,
                    start_time = excluded.start_time,
                    due_rule = excluded.due_rule,
                    calendar_name = excluded.calendar_name,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                item,
            )
            row = connection.execute(
                """
                SELECT *
                FROM homework_class_schedules
                WHERE child = :child AND class_name = :class_name
                """,
                {"child": child, "class_name": class_name},
            ).fetchone()
        return dict(row)

    def list_class_schedules(self, *, child: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        where = ""
        if child:
            where = " WHERE child = :child"
            params["child"] = child
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM homework_class_schedules
                """
                + where
                + " ORDER BY child ASC, weekday ASC, COALESCE(start_time, '99:99') ASC, class_name ASC",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_items(
        self,
        *,
        child: str | None = None,
        statuses: tuple[str, ...] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: dict[str, Any] = {}
        if child:
            clauses.append("child = :child")
            params["child"] = child
        if statuses:
            placeholders = []
            for index, status in enumerate(statuses):
                key = f"status_{index}"
                placeholders.append(f":{key}")
                params[key] = status
            clauses.append(f"status IN ({', '.join(placeholders)})")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        query = (
            "SELECT * FROM homework_items"
            + where
            + " ORDER BY COALESCE(due_date, '9999-12-31') ASC, updated_at DESC"
        )
        if limit is not None:
            query += " LIMIT :limit"
            params["limit"] = max(1, int(limit))
        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def list_assets(self, homework_item_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM homework_assets
                WHERE homework_item_id = :id
                ORDER BY created_at ASC
                """,
                {"id": homework_item_id},
            ).fetchall()
        return [dict(row) for row in rows]

    def list_events(self, homework_item_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM homework_events
                WHERE homework_item_id = :id
                ORDER BY created_at ASC
                """,
                {"id": homework_item_id},
            ).fetchall()
        return [dict(row) for row in rows]

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        item_id: str,
        event_type: str,
        note: str | None,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO homework_events (id, homework_item_id, event_type, note, created_at)
            VALUES (:id, :homework_item_id, :event_type, :note, :created_at)
            """,
            {
                "id": uuid4().hex,
                "homework_item_id": item_id,
                "event_type": event_type,
                "note": note,
                "created_at": created_at,
            },
        )

    def _insert_assets(
        self,
        connection: sqlite3.Connection,
        item_id: str,
        kind: str,
        source: str,
        photo_path: str | None,
        ocr_text: str | None,
        created_at: str,
        *,
        content_fingerprint: str | None = None,
        photo_sha256: str | None = None,
        page_index: int | None = None,
    ) -> None:
        if photo_path is not None:
            connection.execute(
                """
                INSERT INTO homework_assets (
                    id, homework_item_id, kind, path, ocr_text, content_fingerprint,
                    photo_sha256, page_index, source, created_at
                )
                VALUES (
                    :id, :homework_item_id, :kind, :path, :ocr_text, :content_fingerprint,
                    :photo_sha256, :page_index, :source, :created_at
                )
                """,
                {
                    "id": uuid4().hex,
                    "homework_item_id": item_id,
                    "kind": kind,
                    "path": photo_path,
                    "ocr_text": ocr_text,
                    "content_fingerprint": content_fingerprint,
                    "photo_sha256": photo_sha256,
                    "page_index": page_index,
                    "source": source,
                    "created_at": created_at,
                },
            )
        elif ocr_text is not None:
            connection.execute(
                """
                INSERT INTO homework_assets (
                    id, homework_item_id, kind, path, ocr_text, content_fingerprint,
                    photo_sha256, source, created_at
                )
                VALUES (
                    :id, :homework_item_id, 'ocr_text', NULL, :ocr_text, :content_fingerprint,
                    :photo_sha256, :source, :created_at
                )
                """,
                {
                    "id": uuid4().hex,
                    "homework_item_id": item_id,
                    "ocr_text": ocr_text,
                    "content_fingerprint": content_fingerprint,
                    "photo_sha256": photo_sha256,
                    "source": source,
                    "created_at": created_at,
                },
            )


def _date_text(value: str | Date | None) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, Date) else value


def _metadata_json(metadata: dict[str, Any] | None) -> str | None:
    if not metadata:
        return None
    return json.dumps(metadata, sort_keys=True, separators=(",", ":"))


def _metadata_from_json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _merge_metadata(current_json: Any, update: dict[str, Any] | None) -> dict[str, Any] | None:
    current = _metadata_from_json(current_json)
    if update:
        current.update(update)
    return current or None


def _clean_update_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split()).strip()
    return cleaned or None


def _schedule_key(value: str) -> str:
    return "-".join(str(value).lower().split())

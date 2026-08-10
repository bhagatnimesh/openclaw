from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_FILE = ROOT / "data" / "n4os.db"
LEGACY_MIGRATION = "family_decisions_to_backlog_v1"
OPEN_STATUSES = {
    "open",
    "inbox",
    "clarifying",
    "researching",
    "ready",
    "deciding",
    "preparing",
}


class SQLiteFamilyDecisionProvider:
    """Canonical SQLite provider for family backlog items and decision details."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_FILE):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS n4os_schema_migrations (
                    name TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS family_backlog_items (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK(kind IN ('discussion', 'planning', 'decision')),
                    title TEXT NOT NULL,
                    context TEXT,
                    status TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    urgency TEXT NOT NULL,
                    size TEXT NOT NULL,
                    review_on TEXT,
                    due TEXT,
                    priority INTEGER NOT NULL DEFAULT 0,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    outcome TEXT,
                    rationale TEXT,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    closed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS family_backlog_activity (
                    id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(item_id) REFERENCES family_backlog_items(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS family_backlog_positions (
                    item_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    value TEXT NOT NULL CHECK(value IN ('yes', 'no', 'unsure')),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(item_id, actor),
                    FOREIGN KEY(item_id) REFERENCES family_backlog_items(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS family_backlog_links (
                    id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    source_type TEXT NOT NULL CHECK(source_type IN ('calendar_event', 'google_task')),
                    external_id TEXT NOT NULL,
                    container_id TEXT NOT NULL DEFAULT '',
                    title TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(item_id) REFERENCES family_backlog_items(id) ON DELETE CASCADE,
                    UNIQUE(source_type, external_id, container_id)
                );

                CREATE TABLE IF NOT EXISTS family_backlog_options (
                    id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    pros TEXT,
                    cons TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(item_id) REFERENCES family_backlog_items(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS family_backlog_evidence (
                    id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    source TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(item_id) REFERENCES family_backlog_items(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS family_backlog_next_steps (
                    id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    due TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(item_id) REFERENCES family_backlog_items(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_family_backlog_open
                ON family_backlog_items(kind, status, pinned, due, review_on, urgency);

                CREATE INDEX IF NOT EXISTS idx_family_backlog_activity_item
                ON family_backlog_activity(item_id, created_at);

                CREATE INDEX IF NOT EXISTS idx_family_backlog_links_item
                ON family_backlog_links(item_id, source_type);
                """,
            )
            self._migrate_legacy_decisions(connection)

    def _table_exists(self, connection: sqlite3.Connection, table: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :name",
            {"name": table},
        ).fetchone()
        return row is not None

    def _migrate_legacy_decisions(self, connection: sqlite3.Connection) -> None:
        applied = connection.execute(
            "SELECT 1 FROM n4os_schema_migrations WHERE name = :name",
            {"name": LEGACY_MIGRATION},
        ).fetchone()
        if applied is not None:
            return

        now = self._now()
        if self._table_exists(connection, "family_decisions"):
            connection.execute(
                """
                INSERT OR IGNORE INTO family_backlog_items (
                    id, kind, title, context, status, owner, urgency, size,
                    review_on, due, priority, pinned, outcome, rationale,
                    created_by, created_at, updated_at, closed_at
                )
                SELECT
                    id, 'decision', title, context, status, owner, urgency, size,
                    NULL, due, 0, 0, outcome, rationale,
                    'legacy', created_at, updated_at, decided_at
                FROM family_decisions
                """,
            )
            if self._table_exists(connection, "family_decision_events"):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO family_backlog_activity (
                        id, item_id, kind, text, actor, created_at
                    )
                    SELECT id, decision_id, kind, text, 'legacy', created_at
                    FROM family_decision_events
                    """,
                )
            if self._table_exists(connection, "family_decision_options"):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO family_backlog_options (
                        id, item_id, text, pros, cons, created_at
                    )
                    SELECT id, decision_id, text, pros, cons, created_at
                    FROM family_decision_options
                    """,
                )
            if self._table_exists(connection, "family_decision_evidence"):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO family_backlog_evidence (
                        id, item_id, text, source, created_at
                    )
                    SELECT id, decision_id, text, source, created_at
                    FROM family_decision_evidence
                    """,
                )
            if self._table_exists(connection, "family_decision_next_steps"):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO family_backlog_next_steps (
                        id, item_id, text, owner, due, status, created_at, completed_at
                    )
                    SELECT id, decision_id, text, owner, due, status, created_at, completed_at
                    FROM family_decision_next_steps
                    """,
                )

            for table in (
                "family_decision_next_steps",
                "family_decision_evidence",
                "family_decision_options",
                "family_decision_events",
                "family_decisions",
            ):
                if self._table_exists(connection, table):
                    connection.execute(f"DROP TABLE {table}")

        connection.execute(
            "INSERT INTO n4os_schema_migrations (name, applied_at) VALUES (:name, :applied_at)",
            {"name": LEGACY_MIGRATION, "applied_at": now},
        )

    def _now(self) -> str:
        return datetime.now().astimezone().isoformat()

    def _add_activity(
        self,
        connection: sqlite3.Connection,
        item_id: str,
        kind: str,
        text: str,
        actor: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO family_backlog_activity (id, item_id, kind, text, actor, created_at)
            VALUES (:id, :item_id, :kind, :text, :actor, :created_at)
            """,
            {
                "id": uuid4().hex,
                "item_id": item_id,
                "kind": kind,
                "text": text,
                "actor": actor,
                "created_at": created_at,
            },
        )

    def _resolve_id(self, connection: sqlite3.Connection, item_id: str) -> str | None:
        rows = connection.execute(
            "SELECT id FROM family_backlog_items WHERE id LIKE :id || '%' ORDER BY id LIMIT 2",
            {"id": item_id},
        ).fetchall()
        return rows[0]["id"] if len(rows) == 1 else None

    def create_item(
        self,
        *,
        kind: str,
        title: str,
        context: str | None = None,
        status: str | None = None,
        owner: str = "unknown",
        urgency: str = "normal",
        size: str = "small",
        review_on: str | None = None,
        due: str | None = None,
        priority: int = 0,
        pinned: bool = False,
        created_by: str = "family",
        created_at: str | None = None,
    ) -> dict[str, Any]:
        now = created_at or self._now()
        default_status = {"discussion": "open", "planning": "preparing", "decision": "inbox"}[kind]
        item = {
            "id": uuid4().hex,
            "kind": kind,
            "title": title,
            "context": context,
            "status": status or default_status,
            "owner": owner,
            "urgency": urgency,
            "size": size,
            "review_on": review_on,
            "due": due,
            "priority": priority,
            "pinned": int(pinned),
            "outcome": None,
            "rationale": None,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
            "closed_at": None,
        }
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO family_backlog_items (
                    id, kind, title, context, status, owner, urgency, size,
                    review_on, due, priority, pinned, outcome, rationale,
                    created_by, created_at, updated_at, closed_at
                )
                VALUES (
                    :id, :kind, :title, :context, :status, :owner, :urgency, :size,
                    :review_on, :due, :priority, :pinned, :outcome, :rationale,
                    :created_by, :created_at, :updated_at, :closed_at
                )
                """,
                item,
            )
            self._add_activity(connection, item["id"], "created", title, created_by, now)
        return self.get_item(item["id"]) or item

    def list_items(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
        include_closed: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if kind is not None:
            clauses.append("kind = :kind")
            params["kind"] = kind
        if status is not None:
            clauses.append("status = :status")
            params["status"] = status
        elif not include_closed:
            placeholders = ", ".join(f":open_{index}" for index, _ in enumerate(OPEN_STATUSES))
            clauses.append(f"status IN ({placeholders})")
            params.update({f"open_{index}": value for index, value in enumerate(sorted(OPEN_STATUSES))})
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        query = (
            "SELECT * FROM family_backlog_items"
            + where
            + """
              ORDER BY
                pinned DESC,
                CASE urgency
                  WHEN 'critical' THEN 0
                  WHEN 'high' THEN 1
                  WHEN 'normal' THEN 2
                  ELSE 3
                END,
                COALESCE(review_on, due) IS NULL,
                COALESCE(review_on, due),
                priority DESC,
                updated_at DESC
              """
        )
        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._public_base(dict(row)) for row in rows]

    def _public_base(self, item: dict[str, Any]) -> dict[str, Any]:
        item["pinned"] = bool(item.get("pinned"))
        item["decided_at"] = item.get("closed_at") if item.get("kind") == "decision" else None
        return item

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            full_id = self._resolve_id(connection, item_id)
            if full_id is None:
                return None
            row = connection.execute(
                "SELECT * FROM family_backlog_items WHERE id = :id",
                {"id": full_id},
            ).fetchone()
            if row is None:
                return None
            item = self._public_base(dict(row))
            params = {"item_id": full_id}
            activity = [
                dict(event)
                for event in connection.execute(
                    "SELECT * FROM family_backlog_activity WHERE item_id = :item_id ORDER BY created_at",
                    params,
                ).fetchall()
            ]
            item["activity"] = activity
            item["events"] = [
                {**event, "decision_id": event["item_id"]}
                for event in activity
            ]
            item["notes"] = [event for event in activity if event["kind"] == "note"]
            item["positions"] = [
                dict(position)
                for position in connection.execute(
                    "SELECT * FROM family_backlog_positions WHERE item_id = :item_id ORDER BY actor",
                    params,
                ).fetchall()
            ]
            item["links"] = [
                dict(link)
                for link in connection.execute(
                    "SELECT * FROM family_backlog_links WHERE item_id = :item_id ORDER BY source_type, created_at",
                    params,
                ).fetchall()
            ]
            item["options"] = [
                dict(option)
                for option in connection.execute(
                    "SELECT *, item_id AS decision_id FROM family_backlog_options WHERE item_id = :item_id ORDER BY created_at",
                    params,
                ).fetchall()
            ]
            item["evidence"] = [
                dict(evidence)
                for evidence in connection.execute(
                    "SELECT *, item_id AS decision_id FROM family_backlog_evidence WHERE item_id = :item_id ORDER BY created_at",
                    params,
                ).fetchall()
            ]
            item["next_steps"] = [
                dict(step)
                for step in connection.execute(
                    """
                    SELECT *, item_id AS decision_id
                    FROM family_backlog_next_steps
                    WHERE item_id = :item_id
                    ORDER BY status, due IS NULL, due, created_at
                    """,
                    params,
                ).fetchall()
            ]
        return item

    def update_item(self, item_id: str, *, actor: str = "family", **fields: Any) -> dict[str, Any] | None:
        allowed = {
            "title",
            "context",
            "status",
            "owner",
            "urgency",
            "size",
            "review_on",
            "due",
            "priority",
            "pinned",
            "outcome",
            "rationale",
            "closed_at",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if "pinned" in updates:
            updates["pinned"] = int(bool(updates["pinned"]))
        if not updates:
            return self.get_item(item_id)
        now = self._now()
        updates["updated_at"] = now
        assignments = ", ".join(f"{key} = :{key}" for key in updates)
        with self._connection() as connection:
            full_id = self._resolve_id(connection, item_id)
            if full_id is None:
                return None
            updates["id"] = full_id
            connection.execute(
                f"UPDATE family_backlog_items SET {assignments} WHERE id = :id",
                updates,
            )
            changed = ", ".join(sorted(key for key in fields if key in allowed))
            self._add_activity(connection, full_id, "updated", changed, actor, now)
        return self.get_item(full_id)

    def add_note(self, item_id: str, text: str, *, actor: str = "family") -> dict[str, Any] | None:
        now = self._now()
        with self._connection() as connection:
            full_id = self._resolve_id(connection, item_id)
            if full_id is None:
                return None
            self._add_activity(connection, full_id, "note", text, actor, now)
            connection.execute(
                "UPDATE family_backlog_items SET updated_at = :updated_at WHERE id = :id",
                {"id": full_id, "updated_at": now},
            )
        return self.get_item(full_id)

    def set_position(
        self,
        item_id: str,
        value: str,
        *,
        actor: str,
    ) -> dict[str, Any] | None:
        now = self._now()
        with self._connection() as connection:
            full_id = self._resolve_id(connection, item_id)
            if full_id is None:
                return None
            connection.execute(
                """
                INSERT INTO family_backlog_positions (item_id, actor, value, updated_at)
                VALUES (:item_id, :actor, :value, :updated_at)
                ON CONFLICT(item_id, actor) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                {"item_id": full_id, "actor": actor, "value": value, "updated_at": now},
            )
            connection.execute(
                "UPDATE family_backlog_items SET updated_at = :updated_at WHERE id = :id",
                {"id": full_id, "updated_at": now},
            )
            self._add_activity(connection, full_id, "position", value, actor, now)
        return self.get_item(full_id)

    def add_link(
        self,
        item_id: str,
        *,
        source_type: str,
        external_id: str,
        container_id: str = "",
        title: str | None = None,
        actor: str = "family",
    ) -> dict[str, Any] | None:
        now = self._now()
        with self._connection() as connection:
            full_id = self._resolve_id(connection, item_id)
            if full_id is None:
                return None
            existing = connection.execute(
                """
                SELECT item_id FROM family_backlog_links
                WHERE source_type = :source_type
                  AND external_id = :external_id
                  AND container_id = :container_id
                """,
                {
                    "source_type": source_type,
                    "external_id": external_id,
                    "container_id": container_id,
                },
            ).fetchone()
            if existing is not None and existing["item_id"] != full_id:
                return None
            connection.execute(
                """
                INSERT OR IGNORE INTO family_backlog_links (
                    id, item_id, source_type, external_id, container_id, title, created_at
                )
                VALUES (:id, :item_id, :source_type, :external_id, :container_id, :title, :created_at)
                """,
                {
                    "id": uuid4().hex,
                    "item_id": full_id,
                    "source_type": source_type,
                    "external_id": external_id,
                    "container_id": container_id,
                    "title": title,
                    "created_at": now,
                },
            )
            connection.execute(
                "UPDATE family_backlog_items SET updated_at = :updated_at WHERE id = :id",
                {"id": full_id, "updated_at": now},
            )
            self._add_activity(connection, full_id, "linked", title or external_id, actor, now)
        return self.get_item(full_id)

    def move_item(self, item_id: str, kind: str, *, actor: str = "family") -> dict[str, Any] | None:
        now = self._now()
        status = {"discussion": "open", "planning": "preparing", "decision": "inbox"}[kind]
        with self._connection() as connection:
            full_id = self._resolve_id(connection, item_id)
            if full_id is None:
                return None
            connection.execute(
                """
                UPDATE family_backlog_items
                SET kind = :kind, status = :status, updated_at = :updated_at, closed_at = NULL
                WHERE id = :id
                """,
                {"id": full_id, "kind": kind, "status": status, "updated_at": now},
            )
            self._add_activity(connection, full_id, "moved", kind, actor, now)
        return self.get_item(full_id)

    def close_item(
        self,
        item_id: str,
        *,
        outcome: str,
        rationale: str | None = None,
        actor: str = "family",
    ) -> dict[str, Any] | None:
        now = self._now()
        with self._connection() as connection:
            full_id = self._resolve_id(connection, item_id)
            if full_id is None:
                return None
            row = connection.execute(
                "SELECT kind FROM family_backlog_items WHERE id = :id",
                {"id": full_id},
            ).fetchone()
            status = "decided" if row is not None and row["kind"] == "decision" else "closed"
            connection.execute(
                """
                UPDATE family_backlog_items
                SET status = :status,
                    outcome = :outcome,
                    rationale = :rationale,
                    closed_at = :closed_at,
                    updated_at = :updated_at
                WHERE id = :id
                """,
                {
                    "id": full_id,
                    "status": status,
                    "outcome": outcome,
                    "rationale": rationale,
                    "closed_at": now,
                    "updated_at": now,
                },
            )
            self._add_activity(connection, full_id, "closed", outcome, actor, now)
        return self.get_item(full_id)

    def delete_item(self, item_id: str) -> dict[str, Any] | None:
        item = self.get_item(item_id)
        if item is None:
            return None
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM family_backlog_items WHERE id = :id",
                {"id": item["id"]},
            )
        return item

    def restore_item(self, item: dict[str, Any]) -> dict[str, Any] | None:
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            return None
        self.delete_item(item_id)
        with self._connection() as connection:
            now = self._now()
            connection.execute(
                """
                INSERT INTO family_backlog_items (
                    id, kind, title, context, status, owner, urgency, size,
                    review_on, due, priority, pinned, outcome, rationale,
                    created_by, created_at, updated_at, closed_at
                ) VALUES (
                    :id, :kind, :title, :context, :status, :owner, :urgency, :size,
                    :review_on, :due, :priority, :pinned, :outcome, :rationale,
                    :created_by, :created_at, :updated_at, :closed_at
                )
                """,
                {
                    "id": item_id,
                    "kind": item.get("kind") or "decision",
                    "title": item.get("title") or "Untitled item",
                    "context": item.get("context"),
                    "status": item.get("status") or "inbox",
                    "owner": item.get("owner") or "unknown",
                    "urgency": item.get("urgency") or "normal",
                    "size": item.get("size") or "small",
                    "review_on": item.get("review_on"),
                    "due": item.get("due"),
                    "priority": int(item.get("priority") or 0),
                    "pinned": int(bool(item.get("pinned"))),
                    "outcome": item.get("outcome"),
                    "rationale": item.get("rationale"),
                    "created_by": item.get("created_by") or "family",
                    "created_at": item.get("created_at") or now,
                    "updated_at": item.get("updated_at") or now,
                    "closed_at": item.get("closed_at") or item.get("decided_at"),
                },
            )
            activity = item.get("activity") or item.get("events") or []
            for event in activity:
                connection.execute(
                    """
                    INSERT INTO family_backlog_activity (id, item_id, kind, text, actor, created_at)
                    VALUES (:id, :item_id, :kind, :text, :actor, :created_at)
                    """,
                    {
                        "id": event.get("id") or uuid4().hex,
                        "item_id": item_id,
                        "kind": event.get("kind") or "restored",
                        "text": event.get("text") or "Restored item state",
                        "actor": event.get("actor") or "family",
                        "created_at": event.get("created_at") or now,
                    },
                )
            for position in item.get("positions", []):
                connection.execute(
                    """
                    INSERT INTO family_backlog_positions (item_id, actor, value, updated_at)
                    VALUES (:item_id, :actor, :value, :updated_at)
                    """,
                    {
                        "item_id": item_id,
                        "actor": position.get("actor") or "family",
                        "value": position.get("value") or "unsure",
                        "updated_at": position.get("updated_at") or now,
                    },
                )
            for link in item.get("links", []):
                connection.execute(
                    """
                    INSERT INTO family_backlog_links (
                        id, item_id, source_type, external_id, container_id, title, created_at
                    ) VALUES (
                        :id, :item_id, :source_type, :external_id, :container_id, :title, :created_at
                    )
                    """,
                    {
                        "id": link.get("id") or uuid4().hex,
                        "item_id": item_id,
                        "source_type": link.get("source_type") or "google_task",
                        "external_id": link.get("external_id") or "",
                        "container_id": link.get("container_id") or "",
                        "title": link.get("title"),
                        "created_at": link.get("created_at") or now,
                    },
                )
            self._restore_related(connection, item_id, "family_backlog_options", item.get("options", []), ("text", "pros", "cons"), now)
            self._restore_related(connection, item_id, "family_backlog_evidence", item.get("evidence", []), ("text", "source"), now)
            self._restore_related(
                connection,
                item_id,
                "family_backlog_next_steps",
                item.get("next_steps", []),
                ("text", "owner", "due", "status", "completed_at"),
                now,
            )
        return self.get_item(item_id)

    def _restore_related(
        self,
        connection: sqlite3.Connection,
        item_id: str,
        table: str,
        records: list[dict[str, Any]],
        fields: tuple[str, ...],
        now: str,
    ) -> None:
        for record in records:
            values = {
                "id": record.get("id") or uuid4().hex,
                "item_id": item_id,
                "created_at": record.get("created_at") or now,
                **{field: record.get(field) for field in fields},
            }
            if "text" in values:
                values["text"] = values.get("text") or ""
            if "owner" in values:
                values["owner"] = values.get("owner") or "unknown"
            if "status" in values:
                values["status"] = values.get("status") or "open"
            columns = ", ".join(values)
            placeholders = ", ".join(f":{key}" for key in values)
            connection.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                values,
            )

    def _add_related(
        self,
        item_id: str,
        table: str,
        fields: dict[str, Any],
        activity_kind: str,
        actor: str = "family",
    ) -> dict[str, Any] | None:
        now = self._now()
        with self._connection() as connection:
            full_id = self._resolve_id(connection, item_id)
            if full_id is None:
                return None
            record = {"id": uuid4().hex, "item_id": full_id, "created_at": now, **fields}
            columns = ", ".join(record)
            placeholders = ", ".join(f":{key}" for key in record)
            connection.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                record,
            )
            connection.execute(
                "UPDATE family_backlog_items SET updated_at = :updated_at WHERE id = :id",
                {"id": full_id, "updated_at": now},
            )
            self._add_activity(connection, full_id, activity_kind, str(fields.get("text") or ""), actor, now)
        return self.get_item(full_id)

    # Decision-specific methods remain the stable tool surface over the canonical backlog store.
    def create_decision(self, **fields: Any) -> dict[str, Any]:
        return self.create_item(kind="decision", created_by=fields.pop("created_by", "family"), **fields)

    def list_decisions(
        self,
        *,
        status: str | None = None,
        include_decided: bool = False,
    ) -> list[dict[str, Any]]:
        return self.list_items(kind="decision", status=status, include_closed=include_decided)

    def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        item = self.get_item(decision_id)
        return item if item is not None and item.get("kind") == "decision" else None

    def delete_decision(self, decision_id: str) -> dict[str, Any] | None:
        return self.delete_item(decision_id)

    def restore_decision(self, decision: dict[str, Any]) -> dict[str, Any] | None:
        return self.restore_item(decision)

    def update_decision(self, decision_id: str, **fields: Any) -> dict[str, Any] | None:
        if "decided_at" in fields:
            fields["closed_at"] = fields.pop("decided_at")
        return self.update_item(decision_id, **fields)

    def add_option(self, decision_id: str, text: str, pros: str | None = None, cons: str | None = None) -> dict[str, Any] | None:
        return self._add_related(
            decision_id,
            "family_backlog_options",
            {"text": text, "pros": pros, "cons": cons},
            "option_added",
        )

    def add_evidence(self, decision_id: str, text: str, source: str | None = None) -> dict[str, Any] | None:
        return self._add_related(
            decision_id,
            "family_backlog_evidence",
            {"text": text, "source": source},
            "evidence_added",
        )

    def add_next_step(self, decision_id: str, text: str, owner: str = "unknown", due: str | None = None) -> dict[str, Any] | None:
        return self._add_related(
            decision_id,
            "family_backlog_next_steps",
            {"text": text, "owner": owner, "due": due, "status": "open", "completed_at": None},
            "next_step_added",
        )

    def decide(
        self,
        decision_id: str,
        *,
        outcome: str,
        rationale: str | None = None,
    ) -> dict[str, Any] | None:
        return self.close_item(decision_id, outcome=outcome, rationale=rationale)

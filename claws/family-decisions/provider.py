from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_FILE = ROOT / "data" / "n4os.db"


class SQLiteFamilyDecisionProvider:
    """SQLite provider for durable family decision records."""

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
                CREATE TABLE IF NOT EXISTS family_decisions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    context TEXT,
                    status TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    urgency TEXT NOT NULL,
                    size TEXT NOT NULL,
                    due TEXT,
                    outcome TEXT,
                    rationale TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    decided_at TEXT
                )
                """,
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS family_decision_events (
                    id TEXT PRIMARY KEY,
                    decision_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(decision_id) REFERENCES family_decisions(id)
                )
                """,
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS family_decision_options (
                    id TEXT PRIMARY KEY,
                    decision_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    pros TEXT,
                    cons TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(decision_id) REFERENCES family_decisions(id)
                )
                """,
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS family_decision_evidence (
                    id TEXT PRIMARY KEY,
                    decision_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    source TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(decision_id) REFERENCES family_decisions(id)
                )
                """,
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS family_decision_next_steps (
                    id TEXT PRIMARY KEY,
                    decision_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    due TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(decision_id) REFERENCES family_decisions(id)
                )
                """,
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_family_decisions_status_due
                ON family_decisions(status, due, urgency)
                """,
            )

    def _now(self) -> str:
        return datetime.now().astimezone().isoformat()

    def _add_event(
        self,
        connection: sqlite3.Connection,
        decision_id: str,
        kind: str,
        text: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO family_decision_events (id, decision_id, kind, text, created_at)
            VALUES (:id, :decision_id, :kind, :text, :created_at)
            """,
            {
                "id": uuid4().hex,
                "decision_id": decision_id,
                "kind": kind,
                "text": text,
                "created_at": created_at,
            },
        )

    def create_decision(
        self,
        *,
        title: str,
        context: str | None = None,
        status: str = "inbox",
        owner: str = "unknown",
        urgency: str = "normal",
        size: str = "small",
        due: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        now = created_at or self._now()
        decision = {
            "id": uuid4().hex,
            "title": title,
            "context": context,
            "status": status,
            "owner": owner,
            "urgency": urgency,
            "size": size,
            "due": due,
            "outcome": None,
            "rationale": None,
            "created_at": now,
            "updated_at": now,
            "decided_at": None,
        }
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO family_decisions (
                    id, title, context, status, owner, urgency, size, due,
                    outcome, rationale, created_at, updated_at, decided_at
                )
                VALUES (
                    :id, :title, :context, :status, :owner, :urgency, :size, :due,
                    :outcome, :rationale, :created_at, :updated_at, :decided_at
                )
                """,
                decision,
            )
            self._add_event(connection, decision["id"], "created", title, now)
        return self.get_decision(decision["id"]) or decision

    def list_decisions(
        self,
        *,
        status: str | None = None,
        include_decided: bool = False,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: dict[str, Any] = {}
        if status is not None:
            clauses.append("status = :status")
            params["status"] = status
        elif not include_decided:
            clauses.append("status != 'decided'")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        query = (
            "SELECT * FROM family_decisions"
            + where
            + """
              ORDER BY
                CASE urgency
                  WHEN 'critical' THEN 0
                  WHEN 'high' THEN 1
                  WHEN 'normal' THEN 2
                  ELSE 3
                END,
                due IS NULL,
                due,
                updated_at DESC
              """
        )
        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM family_decisions WHERE id LIKE :id || '%'",
                {"id": decision_id},
            ).fetchone()
            if row is None:
                return None
            decision = dict(row)
            params = {"decision_id": decision["id"]}
            decision["events"] = [
                dict(event)
                for event in connection.execute(
                    "SELECT * FROM family_decision_events WHERE decision_id = :decision_id ORDER BY created_at",
                    params,
                ).fetchall()
            ]
            decision["options"] = [
                dict(option)
                for option in connection.execute(
                    "SELECT * FROM family_decision_options WHERE decision_id = :decision_id ORDER BY created_at",
                    params,
                ).fetchall()
            ]
            decision["evidence"] = [
                dict(item)
                for item in connection.execute(
                    "SELECT * FROM family_decision_evidence WHERE decision_id = :decision_id ORDER BY created_at",
                    params,
                ).fetchall()
            ]
            decision["next_steps"] = [
                dict(step)
                for step in connection.execute(
                    "SELECT * FROM family_decision_next_steps WHERE decision_id = :decision_id ORDER BY status, due IS NULL, due, created_at",
                    params,
                ).fetchall()
            ]
        return decision

    def delete_decision(self, decision_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT id FROM family_decisions WHERE id LIKE :id || '%'",
                {"id": decision_id},
            ).fetchone()
            if row is None:
                return None
            full_id = row["id"]
            decision = self.get_decision(full_id)
            params = {"decision_id": full_id}
            connection.execute(
                "DELETE FROM family_decision_next_steps WHERE decision_id = :decision_id",
                params,
            )
            connection.execute(
                "DELETE FROM family_decision_evidence WHERE decision_id = :decision_id",
                params,
            )
            connection.execute(
                "DELETE FROM family_decision_options WHERE decision_id = :decision_id",
                params,
            )
            connection.execute(
                "DELETE FROM family_decision_events WHERE decision_id = :decision_id",
                params,
            )
            connection.execute(
                "DELETE FROM family_decisions WHERE id = :decision_id",
                params,
            )
        return decision

    def restore_decision(self, decision: dict[str, Any]) -> dict[str, Any] | None:
        decision_id = str(decision.get("id") or "").strip()
        if not decision_id:
            return None
        with self._connection() as connection:
            params = {"decision_id": decision_id}
            connection.execute(
                "DELETE FROM family_decision_next_steps WHERE decision_id = :decision_id",
                params,
            )
            connection.execute(
                "DELETE FROM family_decision_evidence WHERE decision_id = :decision_id",
                params,
            )
            connection.execute(
                "DELETE FROM family_decision_options WHERE decision_id = :decision_id",
                params,
            )
            connection.execute(
                "DELETE FROM family_decision_events WHERE decision_id = :decision_id",
                params,
            )
            connection.execute(
                "DELETE FROM family_decisions WHERE id = :decision_id",
                params,
            )
            connection.execute(
                """
                INSERT INTO family_decisions (
                    id, title, context, status, owner, urgency, size, due,
                    outcome, rationale, created_at, updated_at, decided_at
                )
                VALUES (
                    :id, :title, :context, :status, :owner, :urgency, :size, :due,
                    :outcome, :rationale, :created_at, :updated_at, :decided_at
                )
                """,
                {
                    "id": decision_id,
                    "title": decision.get("title") or "Untitled decision",
                    "context": decision.get("context"),
                    "status": decision.get("status") or "inbox",
                    "owner": decision.get("owner") or "unknown",
                    "urgency": decision.get("urgency") or "normal",
                    "size": decision.get("size") or "small",
                    "due": decision.get("due"),
                    "outcome": decision.get("outcome"),
                    "rationale": decision.get("rationale"),
                    "created_at": decision.get("created_at") or self._now(),
                    "updated_at": decision.get("updated_at") or self._now(),
                    "decided_at": decision.get("decided_at"),
                },
            )
            for event in decision.get("events", []):
                connection.execute(
                    """
                    INSERT INTO family_decision_events (id, decision_id, kind, text, created_at)
                    VALUES (:id, :decision_id, :kind, :text, :created_at)
                    """,
                    {
                        "id": event.get("id") or uuid4().hex,
                        "decision_id": decision_id,
                        "kind": event.get("kind") or "restored",
                        "text": event.get("text") or "Restored decision state",
                        "created_at": event.get("created_at") or self._now(),
                    },
                )
            for option in decision.get("options", []):
                connection.execute(
                    """
                    INSERT INTO family_decision_options (id, decision_id, text, pros, cons, created_at)
                    VALUES (:id, :decision_id, :text, :pros, :cons, :created_at)
                    """,
                    {
                        "id": option.get("id") or uuid4().hex,
                        "decision_id": decision_id,
                        "text": option.get("text") or "",
                        "pros": option.get("pros"),
                        "cons": option.get("cons"),
                        "created_at": option.get("created_at") or self._now(),
                    },
                )
            for item in decision.get("evidence", []):
                connection.execute(
                    """
                    INSERT INTO family_decision_evidence (id, decision_id, text, source, created_at)
                    VALUES (:id, :decision_id, :text, :source, :created_at)
                    """,
                    {
                        "id": item.get("id") or uuid4().hex,
                        "decision_id": decision_id,
                        "text": item.get("text") or "",
                        "source": item.get("source"),
                        "created_at": item.get("created_at") or self._now(),
                    },
                )
            for step in decision.get("next_steps", []):
                connection.execute(
                    """
                    INSERT INTO family_decision_next_steps (
                        id, decision_id, text, owner, due, status, created_at, completed_at
                    )
                    VALUES (
                        :id, :decision_id, :text, :owner, :due, :status, :created_at, :completed_at
                    )
                    """,
                    {
                        "id": step.get("id") or uuid4().hex,
                        "decision_id": decision_id,
                        "text": step.get("text") or "",
                        "owner": step.get("owner") or "unknown",
                        "due": step.get("due"),
                        "status": step.get("status") or "open",
                        "created_at": step.get("created_at") or self._now(),
                        "completed_at": step.get("completed_at"),
                    },
                )
        return self.get_decision(decision_id)

    def update_decision(self, decision_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {
            "title",
            "context",
            "status",
            "owner",
            "urgency",
            "size",
            "due",
            "outcome",
            "rationale",
            "decided_at",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return self.get_decision(decision_id)
        now = self._now()
        updates["updated_at"] = now
        assignments = ", ".join(f"{key} = :{key}" for key in updates)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT id FROM family_decisions WHERE id LIKE :id || '%'",
                {"id": decision_id},
            ).fetchone()
            if row is None:
                return None
            full_id = row["id"]
            updates["id"] = full_id
            connection.execute(
                f"UPDATE family_decisions SET {assignments} WHERE id = :id",
                updates,
            )
            self._add_event(connection, full_id, "updated", ", ".join(sorted(fields)), now)
        return self.get_decision(full_id)

    def add_option(self, decision_id: str, text: str, pros: str | None = None, cons: str | None = None) -> dict[str, Any] | None:
        return self._add_related(
            decision_id,
            "family_decision_options",
            {"text": text, "pros": pros, "cons": cons},
            "option_added",
        )

    def add_evidence(self, decision_id: str, text: str, source: str | None = None) -> dict[str, Any] | None:
        return self._add_related(
            decision_id,
            "family_decision_evidence",
            {"text": text, "source": source},
            "evidence_added",
        )

    def add_next_step(self, decision_id: str, text: str, owner: str = "unknown", due: str | None = None) -> dict[str, Any] | None:
        return self._add_related(
            decision_id,
            "family_decision_next_steps",
            {"text": text, "owner": owner, "due": due, "status": "open", "completed_at": None},
            "next_step_added",
        )

    def _add_related(
        self,
        decision_id: str,
        table: str,
        fields: dict[str, Any],
        event_kind: str,
    ) -> dict[str, Any] | None:
        now = self._now()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT id FROM family_decisions WHERE id LIKE :id || '%'",
                {"id": decision_id},
            ).fetchone()
            if row is None:
                return None
            full_id = row["id"]
            record = {"id": uuid4().hex, "decision_id": full_id, "created_at": now, **fields}
            columns = ", ".join(record)
            placeholders = ", ".join(f":{key}" for key in record)
            connection.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                record,
            )
            connection.execute(
                "UPDATE family_decisions SET updated_at = :updated_at WHERE id = :id",
                {"id": full_id, "updated_at": now},
            )
            self._add_event(connection, full_id, event_kind, str(fields.get("text") or ""), now)
        return self.get_decision(full_id)

    def decide(
        self,
        decision_id: str,
        *,
        outcome: str,
        rationale: str | None = None,
    ) -> dict[str, Any] | None:
        now = self._now()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT id FROM family_decisions WHERE id LIKE :id || '%'",
                {"id": decision_id},
            ).fetchone()
            if row is None:
                return None
            full_id = row["id"]
            connection.execute(
                """
                UPDATE family_decisions
                SET status = 'decided',
                    outcome = :outcome,
                    rationale = :rationale,
                    decided_at = :decided_at,
                    updated_at = :updated_at
                WHERE id = :id
                """,
                {
                    "id": full_id,
                    "outcome": outcome,
                    "rationale": rationale,
                    "decided_at": now,
                    "updated_at": now,
                },
            )
            self._add_event(connection, full_id, "decided", outcome, now)
        return self.get_decision(full_id)

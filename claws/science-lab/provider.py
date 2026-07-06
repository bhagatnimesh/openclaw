from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_FILE = ROOT / "data" / "n4os.db"


class SQLiteScienceLabProvider:
    """SQLite provider for N4OS Science Lab planning state."""

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
                CREATE TABLE IF NOT EXISTS science_lab_experiments (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    concepts_json TEXT NOT NULL,
                    materials_json TEXT NOT NULL,
                    age_min INTEGER,
                    age_max INTEGER,
                    waiting_time TEXT NOT NULL,
                    visual_excitement TEXT NOT NULL,
                    safety_notes_json TEXT NOT NULL,
                    library_order INTEGER,
                    updated_at TEXT NOT NULL
                )
                """,
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS science_lab_inventory (
                    material_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('have', 'missing', 'low', 'unknown')),
                    quantity TEXT,
                    notes TEXT,
                    updated_at TEXT NOT NULL
                )
                """,
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS science_lab_progress (
                    experiment_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (status IN ('planned', 'completed', 'skipped')),
                    feedback TEXT,
                    updated_at TEXT NOT NULL
                )
                """,
            )

    def upsert_experiment(
        self,
        *,
        experiment_id: str,
        title: str,
        concepts: list[str] | None = None,
        materials: list[str] | None = None,
        age_min: int | None = None,
        age_max: int | None = None,
        waiting_time: str = "medium",
        visual_excitement: str = "medium",
        safety_notes: list[str] | None = None,
        library_order: int | None = None,
        updated_at: str | None = None,
    ) -> dict[str, Any]:
        record = {
            "id": experiment_id,
            "title": title,
            "concepts_json": json.dumps(concepts or []),
            "materials_json": json.dumps(materials or []),
            "age_min": age_min,
            "age_max": age_max,
            "waiting_time": waiting_time,
            "visual_excitement": visual_excitement,
            "safety_notes_json": json.dumps(safety_notes or []),
            "library_order": library_order,
            "updated_at": updated_at or datetime.now().astimezone().isoformat(),
        }
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO science_lab_experiments (
                    id, title, concepts_json, materials_json, age_min, age_max,
                    waiting_time, visual_excitement, safety_notes_json, library_order, updated_at
                )
                VALUES (
                    :id, :title, :concepts_json, :materials_json, :age_min, :age_max,
                    :waiting_time, :visual_excitement, :safety_notes_json, :library_order, :updated_at
                )
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    concepts_json = excluded.concepts_json,
                    materials_json = excluded.materials_json,
                    age_min = excluded.age_min,
                    age_max = excluded.age_max,
                    waiting_time = excluded.waiting_time,
                    visual_excitement = excluded.visual_excitement,
                    safety_notes_json = excluded.safety_notes_json,
                    library_order = excluded.library_order,
                    updated_at = excluded.updated_at
                """,
                record,
            )
        return self._decode_experiment(record)

    def list_experiments(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM science_lab_experiments
                ORDER BY library_order IS NULL, library_order, title COLLATE NOCASE
                """,
            ).fetchall()
        return [self._decode_experiment(dict(row)) for row in rows]

    def upsert_inventory(
        self,
        *,
        material_id: str,
        display_name: str,
        status: str,
        quantity: str | None = None,
        notes: str | None = None,
        updated_at: str | None = None,
    ) -> dict[str, Any]:
        record = {
            "material_id": material_id,
            "display_name": display_name,
            "status": status,
            "quantity": quantity,
            "notes": notes,
            "updated_at": updated_at or datetime.now().astimezone().isoformat(),
        }
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO science_lab_inventory (
                    material_id, display_name, status, quantity, notes, updated_at
                )
                VALUES (
                    :material_id, :display_name, :status, :quantity, :notes, :updated_at
                )
                ON CONFLICT(material_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    status = excluded.status,
                    quantity = excluded.quantity,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                record,
            )
        return record

    def list_inventory(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM science_lab_inventory
                ORDER BY display_name COLLATE NOCASE
                """,
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_progress(
        self,
        *,
        experiment_id: str,
        status: str,
        feedback: str | None = None,
        updated_at: str | None = None,
    ) -> dict[str, Any]:
        record = {
            "experiment_id": experiment_id,
            "status": status,
            "feedback": feedback,
            "updated_at": updated_at or datetime.now().astimezone().isoformat(),
        }
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO science_lab_progress (experiment_id, status, feedback, updated_at)
                VALUES (:experiment_id, :status, :feedback, :updated_at)
                ON CONFLICT(experiment_id) DO UPDATE SET
                    status = excluded.status,
                    feedback = excluded.feedback,
                    updated_at = excluded.updated_at
                """,
                record,
            )
        return record

    def list_progress(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM science_lab_progress ORDER BY updated_at DESC",
            ).fetchall()
        return [dict(row) for row in rows]

    def _decode_experiment(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": record["id"],
            "title": record["title"],
            "concepts": _loads_list(record.get("concepts_json")),
            "materials": _loads_list(record.get("materials_json")),
            "age_min": record.get("age_min"),
            "age_max": record.get("age_max"),
            "waiting_time": record.get("waiting_time") or "medium",
            "visual_excitement": record.get("visual_excitement") or "medium",
            "safety_notes": _loads_list(record.get("safety_notes_json")),
            "library_order": record.get("library_order"),
            "updated_at": record.get("updated_at"),
        }


def _loads_list(value: Any) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]

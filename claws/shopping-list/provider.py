from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from difflib import SequenceMatcher
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys
from typing import Any, Iterator, Protocol
from uuid import uuid4

from constants import SHOPPING_LISTS


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_FILE = ROOT / "data" / "n4os.db"
FAMILY_TASKS_ROOT = ROOT / "claws" / "family-tasks"
GOOGLE_CLIENT_FILE = ROOT / "secrets" / "google_client_secret.json"
GOOGLE_TOKEN_FILE = ROOT / "secrets" / "google_token.json"
MCP_COMMAND_ENV = "N4OS_OURGROCERIES_MCP_COMMAND"
GOOGLE_TASK_ITEM_ID_PREFIX = "google-tasks"
GOOGLE_SHOPPING_NOTES_MARKER = "N4OS_SHOPPING:"
GOOGLE_TASK_LIST_TITLES = {
    "indian": ("Grocery - Indian",),
    "costco": ("Grocery - Costco",),
    "whole-foods": ("Grocery - Wholefoods", "Grocery - Whole Foods"),
    "amazon": ("Shopping - Amazon",),
    "others": ("Shopping", "Shopping - Others", "Others"),
}
LOCAL_MODULES = (
    "constants",
    "intent",
    "matcher",
    "noah_assistant",
    "prompts",
    "provider",
    "tools",
)
MISSING = object()


class ShoppingProvider(Protocol):
    name: str

    def list_lists(self) -> list[dict[str, Any]]:
        ...

    def list_items(self, list_slug: str, include_checked: bool = False) -> list[dict[str, Any]]:
        ...

    def add_item(
        self,
        list_slug: str,
        item: str,
        quantity: str | None = None,
        note: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        ...

    def update_item(
        self,
        item_id: str,
        item: str | None = None,
        quantity: str | None = None,
        note: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        ...

    def set_checked(self, item_id: str, checked: bool) -> dict[str, Any]:
        ...

    def delete_item(self, item_id: str) -> dict[str, Any] | None:
        ...

    def move_item(self, item_id: str, target_list_slug: str) -> dict[str, Any]:
        ...


class SQLiteShoppingStore:
    """Local shopping history and fallback current-state store."""

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
                CREATE TABLE IF NOT EXISTS shopping_events (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    action TEXT NOT NULL,
                    list_slug TEXT,
                    item_id TEXT,
                    item_title TEXT,
                    status TEXT NOT NULL,
                    message TEXT,
                    payload_json TEXT,
                    created_at TEXT NOT NULL
                )
                """,
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS shopping_item_snapshots (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    list_slug TEXT NOT NULL,
                    title TEXT NOT NULL,
                    quantity TEXT,
                    note TEXT,
                    category TEXT,
                    checked INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    raw_json TEXT
                )
                """,
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS shopping_sync_runs (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    list_slug TEXT,
                    status TEXT NOT NULL,
                    item_count INTEGER NOT NULL,
                    message TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL
                )
                """,
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_shopping_snapshots_list_checked
                ON shopping_item_snapshots(list_slug, checked, title)
                """,
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_shopping_events_created_at
                ON shopping_events(created_at)
                """,
            )

    def now(self) -> str:
        return datetime.now().astimezone().isoformat()

    def record_event(
        self,
        *,
        provider: str,
        action: str,
        status: str,
        list_slug: str | None = None,
        item_id: str | None = None,
        item_title: str | None = None,
        message: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = {
            "id": uuid4().hex,
            "provider": provider,
            "action": action,
            "list_slug": list_slug,
            "item_id": item_id,
            "item_title": item_title,
            "status": status,
            "message": message,
            "payload_json": json.dumps(payload or {}, sort_keys=True),
            "created_at": self.now(),
        }
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO shopping_events (
                    id, provider, action, list_slug, item_id, item_title,
                    status, message, payload_json, created_at
                )
                VALUES (
                    :id, :provider, :action, :list_slug, :item_id, :item_title,
                    :status, :message, :payload_json, :created_at
                )
                """,
                event,
            )
        return event

    def upsert_snapshot(self, provider: str, list_slug: str, item: dict[str, Any]) -> dict[str, Any]:
        snapshot = normalize_provider_item(item, list_slug=list_slug, provider=provider)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO shopping_item_snapshots (
                    id, provider, list_slug, title, quantity, note, category,
                    checked, updated_at, raw_json
                )
                VALUES (
                    :id, :provider, :list_slug, :title, :quantity, :note,
                    :category, :checked, :updated_at, :raw_json
                )
                ON CONFLICT(id) DO UPDATE SET
                    provider = excluded.provider,
                    list_slug = excluded.list_slug,
                    title = excluded.title,
                    quantity = excluded.quantity,
                    note = excluded.note,
                    category = excluded.category,
                    checked = excluded.checked,
                    updated_at = excluded.updated_at,
                    raw_json = excluded.raw_json
                """,
                snapshot,
            )
        return snapshot

    def list_snapshots(self, list_slug: str, include_checked: bool = False) -> list[dict[str, Any]]:
        clauses = ["list_slug = :list_slug"]
        params: dict[str, Any] = {"list_slug": list_slug}
        if not include_checked:
            clauses.append("checked = 0")
        query = (
            "SELECT * FROM shopping_item_snapshots WHERE "
            + " AND ".join(clauses)
            + " ORDER BY checked, category COLLATE NOCASE, title COLLATE NOCASE"
        )
        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def infer_list_slug(self, item_title: str) -> str | None:
        target_key = _item_key(item_title)
        if not target_key:
            return None

        scores: dict[str, float] = {}
        with self._connection() as connection:
            event_rows = connection.execute(
                """
                SELECT list_slug, item_title
                FROM shopping_events
                WHERE status = 'ok'
                  AND list_slug IS NOT NULL
                  AND item_title IS NOT NULL
                  AND action IN (
                    'add_item',
                    'move_item',
                    'check_item',
                    'uncheck_item'
                  )
                ORDER BY created_at DESC
                LIMIT 200
                """,
            ).fetchall()
            snapshot_rows = connection.execute(
                """
                SELECT list_slug, title AS item_title
                FROM shopping_item_snapshots
                WHERE title IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT 200
                """,
            ).fetchall()

        for row in event_rows:
            _add_inference_score(scores, target_key, dict(row), weight=2.0)
        for row in snapshot_rows:
            _add_inference_score(scores, target_key, dict(row), weight=1.0)

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        if not ranked or ranked[0][1] < 2.0:
            return None
        if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 1.5:
            return None
        return ranked[0][0]

    def record_sync(
        self,
        *,
        provider: str,
        list_slug: str | None,
        status: str,
        item_count: int,
        message: str | None = None,
        started_at: str | None = None,
    ) -> dict[str, Any]:
        now = self.now()
        run = {
            "id": uuid4().hex,
            "provider": provider,
            "list_slug": list_slug,
            "status": status,
            "item_count": item_count,
            "message": message,
            "started_at": started_at or now,
            "finished_at": now,
        }
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO shopping_sync_runs (
                    id, provider, list_slug, status, item_count, message,
                    started_at, finished_at
                )
                VALUES (
                    :id, :provider, :list_slug, :status, :item_count, :message,
                    :started_at, :finished_at
                )
                """,
                run,
            )
        return run

    def reconcile_list_snapshots(
        self,
        provider: str,
        list_slug: str,
        seen_item_ids: list[str],
        *,
        include_checked: bool,
    ) -> None:
        with self._connection() as connection:
            now = self.now()
            if seen_item_ids:
                placeholders = ", ".join(f":id_{index}" for index, _ in enumerate(seen_item_ids))
                params = {
                    "provider": provider,
                    "list_slug": list_slug,
                    "updated_at": now,
                    **{f"id_{index}": item_id for index, item_id in enumerate(seen_item_ids)},
                }
                if include_checked:
                    connection.execute(
                        f"""
                        DELETE FROM shopping_item_snapshots
                        WHERE provider = :provider
                          AND list_slug = :list_slug
                          AND id NOT IN ({placeholders})
                        """,
                        params,
                    )
                else:
                    connection.execute(
                        f"""
                        UPDATE shopping_item_snapshots
                        SET checked = 1, updated_at = :updated_at
                        WHERE provider = :provider
                          AND list_slug = :list_slug
                          AND checked = 0
                          AND id NOT IN ({placeholders})
                        """,
                        params,
                    )
                return

            if include_checked:
                connection.execute(
                    """
                    DELETE FROM shopping_item_snapshots
                    WHERE provider = :provider AND list_slug = :list_slug
                    """,
                    {"provider": provider, "list_slug": list_slug},
                )
            else:
                connection.execute(
                    """
                    UPDATE shopping_item_snapshots
                    SET checked = 1, updated_at = :updated_at
                    WHERE provider = :provider
                      AND list_slug = :list_slug
                      AND checked = 0
                    """,
                    {
                        "provider": provider,
                        "list_slug": list_slug,
                        "updated_at": now,
                    },
                )

    def delete_snapshot(self, item_id: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM shopping_item_snapshots WHERE id = :id",
                {"id": item_id},
            )


def normalize_provider_item(
    item: dict[str, Any],
    *,
    list_slug: str,
    provider: str,
) -> dict[str, Any]:
    title = str(item.get("title") or item.get("name") or item.get("item") or "").strip()
    item_id = str(item.get("id") or item.get("item_id") or uuid4().hex).strip()
    checked = bool(item.get("checked") or item.get("completed") or item.get("is_checked"))
    return {
        "id": item_id,
        "provider": provider,
        "list_slug": str(item.get("list_slug") or item.get("list") or list_slug),
        "title": title or "Untitled item",
        "quantity": _clean_optional(item.get("quantity")),
        "note": _clean_optional(item.get("note") or item.get("notes")),
        "category": _clean_optional(item.get("category")),
        "checked": 1 if checked else 0,
        "updated_at": datetime.now().astimezone().isoformat(),
        "raw_json": json.dumps(item, sort_keys=True, default=str),
    }


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split()).strip()
    return cleaned or None


def _item_key(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _add_inference_score(
    scores: dict[str, float],
    target_key: str,
    row: dict[str, Any],
    weight: float,
) -> None:
    list_slug = str(row.get("list_slug") or "")
    if list_slug not in SHOPPING_LISTS:
        return
    candidate_key = _item_key(str(row.get("item_title") or ""))
    if not candidate_key:
        return
    score = 0.0
    if candidate_key == target_key:
        score = weight
    elif target_key in candidate_key or candidate_key in target_key:
        score = weight * 0.8
    else:
        ratio = SequenceMatcher(None, target_key, candidate_key).ratio()
        if ratio >= 0.9:
            score = weight * ratio
    if score:
        scores[list_slug] = scores.get(list_slug, 0.0) + score


class SQLiteShoppingProvider:
    """Fallback provider for development before live OurGroceries MCP is connected."""

    name = "sqlite"

    def __init__(self, store: SQLiteShoppingStore):
        self.store = store

    def list_lists(self) -> list[dict[str, Any]]:
        return [
            {
                "slug": slug,
                "name": name,
                "pending_count": len(self.store.list_snapshots(slug, include_checked=False)),
            }
            for slug, name in SHOPPING_LISTS.items()
        ]

    def list_items(self, list_slug: str, include_checked: bool = False) -> list[dict[str, Any]]:
        return self.store.list_snapshots(list_slug, include_checked=include_checked)

    def add_item(
        self,
        list_slug: str,
        item: str,
        quantity: str | None = None,
        note: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        return self.store.upsert_snapshot(
            self.name,
            list_slug,
            {
                "id": uuid4().hex,
                "list_slug": list_slug,
                "title": item,
                "quantity": quantity,
                "note": note,
                "category": category,
                "checked": False,
            },
        )

    def update_item(
        self,
        item_id: str,
        item: str | None = None,
        quantity: str | None = None,
        note: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        with self.store._connection() as connection:
            row = connection.execute(
                "SELECT * FROM shopping_item_snapshots WHERE id LIKE :id || '%'",
                {"id": item_id},
            ).fetchone()
            if row is None:
                raise KeyError(f"Shopping item {item_id} was not found.")
            current = dict(row)
        current.update(
            {
                "title": item or current["title"],
                "quantity": quantity if quantity is not None else current.get("quantity"),
                "note": note if note is not None else current.get("note"),
                "category": category if category is not None else current.get("category"),
            }
        )
        return self.store.upsert_snapshot(self.name, current["list_slug"], current)

    def set_checked(self, item_id: str, checked: bool) -> dict[str, Any]:
        with self.store._connection() as connection:
            row = connection.execute(
                "SELECT * FROM shopping_item_snapshots WHERE id LIKE :id || '%'",
                {"id": item_id},
            ).fetchone()
            if row is None:
                raise KeyError(f"Shopping item {item_id} was not found.")
            item = dict(row)
        item["checked"] = checked
        return self.store.upsert_snapshot(self.name, item["list_slug"], item)

    def delete_item(self, item_id: str) -> dict[str, Any] | None:
        with self.store._connection() as connection:
            row = connection.execute(
                "SELECT * FROM shopping_item_snapshots WHERE id LIKE :id || '%'",
                {"id": item_id},
            ).fetchone()
            if row is None:
                return None
            item = dict(row)
            connection.execute(
                "DELETE FROM shopping_item_snapshots WHERE id = :id",
                {"id": item["id"]},
            )
        return item

    def move_item(self, item_id: str, target_list_slug: str) -> dict[str, Any]:
        with self.store._connection() as connection:
            row = connection.execute(
                "SELECT * FROM shopping_item_snapshots WHERE id LIKE :id || '%'",
                {"id": item_id},
            ).fetchone()
            if row is None:
                raise KeyError(f"Shopping item {item_id} was not found.")
            item = dict(row)
        item["list_slug"] = target_list_slug
        return self.store.upsert_snapshot(self.name, target_list_slug, item)


@contextmanager
def _module_scope(module_root: Path) -> Iterator[None]:
    original_path = list(sys.path)
    saved_modules = {name: sys.modules.get(name, MISSING) for name in LOCAL_MODULES}
    for name in LOCAL_MODULES:
        sys.modules.pop(name, None)

    sys.path.insert(0, str(module_root))
    try:
        yield
    finally:
        sys.path[:] = original_path
        for name, module in saved_modules.items():
            if module is MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def _load_google_tasks_provider() -> Any:
    module_path = FAMILY_TASKS_ROOT / "provider.py"
    spec = importlib.util.spec_from_file_location(
        "_n4os_family_tasks_provider_for_shopping",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["_n4os_family_tasks_provider_for_shopping"] = module
    with _module_scope(FAMILY_TASKS_ROOT):
        spec.loader.exec_module(module)
    return module.GoogleTasksProvider


def _normalize_google_list_title(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _encode_google_task_item_id(list_slug: str, task_id: str) -> str:
    return f"{GOOGLE_TASK_ITEM_ID_PREFIX}:{list_slug}:{task_id}"


def _decode_google_task_item_id(item_id: str) -> tuple[str, str]:
    parts = item_id.split(":", 2)
    if len(parts) != 3 or parts[0] != GOOGLE_TASK_ITEM_ID_PREFIX:
        raise KeyError(f"Shopping item {item_id} is not a Google Tasks shopping item id.")
    list_slug, task_id = parts[1], parts[2]
    if list_slug not in SHOPPING_LISTS or not task_id:
        raise KeyError(f"Shopping item {item_id} is not a valid Google Tasks shopping item id.")
    return list_slug, task_id


def _read_google_shopping_notes(notes: Any) -> tuple[str | None, dict[str, Any]]:
    lines = str(notes or "").splitlines()
    human_lines = []
    metadata: dict[str, Any] = {}
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(GOOGLE_SHOPPING_NOTES_MARKER):
            raw_metadata = stripped.removeprefix(GOOGLE_SHOPPING_NOTES_MARKER).strip()
            if raw_metadata:
                try:
                    parsed = json.loads(raw_metadata)
                except json.JSONDecodeError:
                    parsed = {}
                if isinstance(parsed, dict):
                    metadata = parsed
            continue
        human_lines.append(line)

    human_note = "\n".join(human_lines).strip()
    return human_note or None, metadata


def _write_google_shopping_notes(
    note: str | None,
    *,
    quantity: str | None = None,
    category: str | None = None,
    existing_metadata: dict[str, Any] | None = None,
) -> str | None:
    metadata = dict(existing_metadata or {})
    if quantity is not None:
        metadata["quantity"] = quantity
    if category is not None:
        metadata["category"] = category
    metadata = {
        key: value
        for key, value in metadata.items()
        if _clean_optional(value) is not None
    }

    lines = []
    cleaned_note = _clean_optional(note)
    if cleaned_note:
        lines.append(cleaned_note)
    if metadata:
        encoded = json.dumps(metadata, sort_keys=True)
        lines.append(f"{GOOGLE_SHOPPING_NOTES_MARKER} {encoded}")
    return "\n\n".join(lines) or None


class GoogleTasksShoppingProvider:
    """Shopping provider backed by the existing Google Tasks account."""

    name = "google-tasks"

    def __init__(self, tasks_provider: Any | None = None):
        if tasks_provider is None:
            provider_class = _load_google_tasks_provider()
            tasks_provider = provider_class()
        self.tasks_provider = tasks_provider
        self._cached_task_list_ids: dict[str, str] | None = None

    def _task_list_ids(self) -> dict[str, str]:
        if self._cached_task_list_ids is not None:
            return dict(self._cached_task_list_ids)

        task_lists = self.tasks_provider.list_task_lists()
        by_title = {
            _normalize_google_list_title(task_list.get("title")): str(task_list.get("id") or "")
            for task_list in task_lists
            if task_list.get("id")
        }
        resolved: dict[str, str] = {}
        missing = []
        for slug, title_options in GOOGLE_TASK_LIST_TITLES.items():
            for title in title_options:
                task_list_id = by_title.get(_normalize_google_list_title(title))
                if task_list_id:
                    resolved[slug] = task_list_id
                    break
            else:
                missing.append(title_options[0])
        if missing:
            raise RuntimeError("Missing Google Tasks shopping lists: " + ", ".join(missing) + ".")
        self._cached_task_list_ids = dict(resolved)
        return resolved

    def _task_list_id(self, list_slug: str) -> str:
        if list_slug not in SHOPPING_LISTS:
            raise KeyError(f"Unknown shopping list: {list_slug}")
        return self._task_list_ids()[list_slug]

    def _normalize_task(self, task: dict[str, Any], list_slug: str) -> dict[str, Any]:
        note, metadata = _read_google_shopping_notes(task.get("notes"))
        task_id = str(task.get("id") or "")
        checked = task.get("status") == "completed" or bool(task.get("completed"))
        return {
            "id": _encode_google_task_item_id(list_slug, task_id),
            "google_task_id": task_id,
            "google_task_list_id": self._task_list_id(list_slug),
            "list_slug": list_slug,
            "title": str(task.get("title") or "Untitled item").strip() or "Untitled item",
            "quantity": _clean_optional(metadata.get("quantity")),
            "note": note,
            "category": _clean_optional(metadata.get("category")),
            "checked": checked,
            "status": task.get("status"),
            "raw": task,
        }

    def _find_task(self, item_id: str) -> tuple[str, str, dict[str, Any]]:
        list_slug, task_id = _decode_google_task_item_id(item_id)
        task_list_id = self._task_list_id(list_slug)
        tasks = self.tasks_provider.list_tasks(
            task_list_id=task_list_id,
            show_completed=True,
        )
        for task in tasks:
            if str(task.get("id") or "") == task_id:
                return list_slug, task_list_id, dict(task)
        raise KeyError(f"Shopping item {item_id} was not found in Google Tasks.")

    def list_lists(self) -> list[dict[str, Any]]:
        task_list_ids = self._task_list_ids()
        lists = []
        for slug, name in SHOPPING_LISTS.items():
            pending = self.tasks_provider.list_tasks(
                task_list_id=task_list_ids[slug],
                show_completed=False,
            )
            lists.append(
                {
                    "slug": slug,
                    "name": name,
                    "google_task_list_id": task_list_ids[slug],
                    "google_task_list_title": GOOGLE_TASK_LIST_TITLES[slug][0],
                    "pending_count": len(pending),
                }
            )
        return lists

    def list_items(self, list_slug: str, include_checked: bool = False) -> list[dict[str, Any]]:
        task_list_id = self._task_list_id(list_slug)
        tasks = self.tasks_provider.list_tasks(
            task_list_id=task_list_id,
            show_completed=include_checked,
        )
        return [self._normalize_task(task, list_slug) for task in tasks]

    def add_item(
        self,
        list_slug: str,
        item: str,
        quantity: str | None = None,
        note: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        task = self.tasks_provider.create_task(
            title=item,
            notes=_write_google_shopping_notes(
                note,
                quantity=quantity,
                category=category,
            ),
            task_list_id=self._task_list_id(list_slug),
        )
        return self._normalize_task(task, list_slug)

    def update_item(
        self,
        item_id: str,
        item: str | None = None,
        quantity: str | None = None,
        note: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        list_slug, task_list_id, current = self._find_task(item_id)
        current_note, current_metadata = _read_google_shopping_notes(current.get("notes"))
        updated = self.tasks_provider.update_task(
            task_id=str(current["id"]),
            title=item if item is not None else current.get("title"),
            notes=_write_google_shopping_notes(
                note if note is not None else current_note,
                quantity=(
                    quantity
                    if quantity is not None
                    else _clean_optional(current_metadata.get("quantity"))
                ),
                category=(
                    category
                    if category is not None
                    else _clean_optional(current_metadata.get("category"))
                ),
                existing_metadata=current_metadata,
            ),
            task_list_id=task_list_id,
        )
        return self._normalize_task({**current, **dict(updated or {})}, list_slug)

    def set_checked(self, item_id: str, checked: bool) -> dict[str, Any]:
        list_slug, task_list_id, current = self._find_task(item_id)
        if checked:
            updated = self.tasks_provider.complete_task(
                task_id=str(current["id"]),
                task_list_id=task_list_id,
            )
        else:
            updated = self.tasks_provider.update_task(
                task_id=str(current["id"]),
                status="needsAction",
                task_list_id=task_list_id,
            )
        return self._normalize_task({**current, **dict(updated or {})}, list_slug)

    def delete_item(self, item_id: str) -> dict[str, Any] | None:
        list_slug, task_list_id, current = self._find_task(item_id)
        normalized = self._normalize_task(current, list_slug)
        self.tasks_provider.delete_task(
            task_id=str(current["id"]),
            task_list_id=task_list_id,
        )
        return normalized

    def move_item(self, item_id: str, target_list_slug: str) -> dict[str, Any]:
        list_slug, task_list_id, current = self._find_task(item_id)
        current_note, current_metadata = _read_google_shopping_notes(current.get("notes"))
        moved = self.add_item(
            list_slug=target_list_slug,
            item=str(current.get("title") or "Untitled item"),
            quantity=_clean_optional(current_metadata.get("quantity")),
            note=current_note,
            category=_clean_optional(current_metadata.get("category")),
        )
        self.tasks_provider.delete_task(
            task_id=str(current["id"]),
            task_list_id=task_list_id,
        )
        moved["previous_list_slug"] = list_slug
        return moved


class CommandOurGroceriesProvider:
    """JSON command bridge to an OurGroceries MCP client wrapper.

    The configured command owns the actual MCP session. N4OS sends one JSON
    object on stdin: {"action": "...", "params": {...}} and expects a JSON
    response containing either {"result": ...} or the result object directly.
    """

    name = "ourgroceries"

    def __init__(self, command: str):
        self.command = command

    def _call(self, action: str, params: dict[str, Any]) -> Any:
        argv = shlex.split(self.command)
        if not argv:
            raise RuntimeError(f"{MCP_COMMAND_ENV} is empty.")
        payload = json.dumps({"action": action, "params": params}).encode("utf-8")
        completed = subprocess.run(
            argv,
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
        if completed.returncode != 0:
            error = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(error or f"OurGroceries command exited {completed.returncode}.")
        parsed = json.loads(completed.stdout.decode("utf-8"))
        if isinstance(parsed, dict) and "error" in parsed:
            raise RuntimeError(str(parsed["error"]))
        if isinstance(parsed, dict) and "result" in parsed:
            return parsed["result"]
        return parsed

    def list_lists(self) -> list[dict[str, Any]]:
        result = self._call("list_lists", {})
        return list(result or [])

    def list_items(self, list_slug: str, include_checked: bool = False) -> list[dict[str, Any]]:
        result = self._call(
            "list_items",
            {"list_slug": list_slug, "include_checked": include_checked},
        )
        return list(result or [])

    def add_item(
        self,
        list_slug: str,
        item: str,
        quantity: str | None = None,
        note: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        return dict(
            self._call(
                "add_item",
                {
                    "list_slug": list_slug,
                    "item": item,
                    "quantity": quantity,
                    "note": note,
                    "category": category,
                },
            )
            or {}
        )

    def update_item(
        self,
        item_id: str,
        item: str | None = None,
        quantity: str | None = None,
        note: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        return dict(
            self._call(
                "update_item",
                {
                    "item_id": item_id,
                    "item": item,
                    "quantity": quantity,
                    "note": note,
                    "category": category,
                },
            )
            or {}
        )

    def set_checked(self, item_id: str, checked: bool) -> dict[str, Any]:
        return dict(self._call("set_checked", {"item_id": item_id, "checked": checked}) or {})

    def delete_item(self, item_id: str) -> dict[str, Any] | None:
        result = self._call("delete_item", {"item_id": item_id})
        return dict(result) if isinstance(result, dict) else None

    def move_item(self, item_id: str, target_list_slug: str) -> dict[str, Any]:
        return dict(
            self._call(
                "move_item",
                {"item_id": item_id, "target_list_slug": target_list_slug},
            )
            or {}
        )


def build_default_provider(store: SQLiteShoppingStore | None = None) -> ShoppingProvider:
    resolved_store = store or SQLiteShoppingStore()
    command = os.environ.get(MCP_COMMAND_ENV, "").strip()
    if command:
        return CommandOurGroceriesProvider(command)
    if GOOGLE_CLIENT_FILE.exists() and GOOGLE_TOKEN_FILE.exists():
        return GoogleTasksShoppingProvider()
    return SQLiteShoppingProvider(resolved_store)

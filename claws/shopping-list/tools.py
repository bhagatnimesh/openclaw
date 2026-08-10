from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Literal, TypedDict

from constants import SHOPPING_LISTS
from intent import list_name
from provider import ShoppingProvider, SQLiteShoppingStore, build_default_provider


class ToolResponse(TypedDict, total=False):
    status: Literal["ok", "needs_information", "needs_confirmation", "error"]
    message: str
    data: dict[str, Any]


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split()).strip()
    return cleaned or None


def _missing_response(fields: list[str]) -> ToolResponse:
    return {
        "status": "needs_information",
        "message": "Missing required shopping information: " + ", ".join(fields) + ".",
        "data": {"missing_fields": fields},
    }


def _error_response(message: str, error: Exception | None = None) -> ToolResponse:
    data: dict[str, Any] = {}
    if error is not None:
        data["error_type"] = error.__class__.__name__
    return {"status": "error", "message": message, "data": data}


def _item_title(item: dict[str, Any]) -> str:
    return str(item.get("title") or item.get("name") or item.get("item") or "Untitled item")


def _item_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("item_id") or "")


def _item_checked(item: dict[str, Any]) -> bool:
    checked = item.get("checked")
    if checked is None:
        checked = item.get("completed")
    if checked is None:
        checked = item.get("is_checked")
    return bool(checked)


def _match_score(query: str, item: dict[str, Any]) -> float:
    title = _item_title(item).lower().strip()
    candidate = query.lower().strip()
    if not title or not candidate:
        return 0.0
    if title == candidate:
        return 1.0
    if candidate in title or title in candidate:
        return 0.86
    return SequenceMatcher(None, candidate, title).ratio()


class ShoppingTools:
    """Tool layer for N4OS shopping lists."""

    def __init__(self, provider: ShoppingProvider, store: SQLiteShoppingStore):
        self.provider = provider
        self.store = store

    def list_shopping_lists(self) -> ToolResponse:
        started_at = self.store.now()
        try:
            lists = self.provider.list_lists()
            self.store.record_sync(
                provider=self.provider.name,
                list_slug=None,
                status="ok",
                item_count=len(lists),
                started_at=started_at,
            )
        except Exception as error:
            self.store.record_sync(
                provider=self.provider.name,
                list_slug=None,
                status="error",
                item_count=0,
                message=str(error),
                started_at=started_at,
            )
            return _error_response(f"Shopping lists unavailable: {error}", error)

        return {
            "status": "ok",
            "message": "Shopping lists returned.",
            "data": {"lists": lists},
        }

    def list_items(self, list_slug: str | None = None, include_checked: bool = False) -> ToolResponse:
        if list_slug not in SHOPPING_LISTS:
            return _missing_response(["list_name"])
        started_at = self.store.now()
        try:
            items = self.provider.list_items(list_slug, include_checked=include_checked)
            snapshots = [
                self.store.upsert_snapshot(self.provider.name, list_slug, item)
                for item in items
            ]
            self.store.reconcile_list_snapshots(
                self.provider.name,
                list_slug,
                [str(snapshot["id"]) for snapshot in snapshots],
                include_checked=include_checked,
            )
            self.store.record_sync(
                provider=self.provider.name,
                list_slug=list_slug,
                status="ok",
                item_count=len(snapshots),
                started_at=started_at,
            )
        except Exception as error:
            self.store.record_sync(
                provider=self.provider.name,
                list_slug=list_slug,
                status="error",
                item_count=0,
                message=str(error),
                started_at=started_at,
            )
            return _error_response(f"{list_name(list_slug)} list unavailable: {error}", error)

        return {
            "status": "ok",
            "message": f"{list_name(list_slug)} items returned.",
            "data": {"items": snapshots},
        }

    def add_item(
        self,
        list_slug: str | None = None,
        item: str | None = None,
        quantity: str | None = None,
        note: str | None = None,
        category: str | None = None,
    ) -> ToolResponse:
        title = _clean_optional(item)
        missing = []
        if title is None:
            missing.append("item")
        inferred_list = False
        if list_slug not in SHOPPING_LISTS and title is not None:
            list_slug = self.store.infer_list_slug(title)
            inferred_list = list_slug is not None
        if list_slug not in SHOPPING_LISTS:
            missing.append("list_name")
        if missing:
            return _missing_response(missing)

        try:
            created = self.provider.add_item(
                list_slug=list_slug,
                item=title,
                quantity=_clean_optional(quantity),
                note=_clean_optional(note),
                category=_clean_optional(category),
            )
            snapshot = self.store.upsert_snapshot(self.provider.name, list_slug, created)
            self.store.record_event(
                provider=self.provider.name,
                action="add_item",
                status="ok",
                list_slug=list_slug,
                item_id=snapshot["id"],
                item_title=snapshot["title"],
                payload={"item": snapshot},
            )
        except Exception as error:
            self.store.record_event(
                provider=self.provider.name,
                action="add_item",
                status="error",
                list_slug=list_slug,
                item_title=title,
                message=str(error),
            )
            return _error_response(f"Could not add {title} to {list_name(list_slug)}: {error}", error)

        return {
            "status": "ok",
            "message": (
                f"Added {snapshot['title']} to {list_name(list_slug)}"
                + (" based on shopping history." if inferred_list else ".")
            ),
            "data": {"item": snapshot, "inferred_list": inferred_list},
        }

    def add_items(self, list_slug: str | None, items: list[str]) -> ToolResponse:
        created = []
        for item in items:
            response = self.add_item(list_slug=list_slug, item=item)
            if response["status"] != "ok":
                return response
            created.append(response.get("data", {}).get("item", {}))
        return {
            "status": "ok",
            "message": f"Added {len(created)} items to {list_name(list_slug)}.",
            "data": {"items": created},
        }

    def find_item(self, list_slug: str | None, item: str | None, include_checked: bool = False) -> ToolResponse:
        title = _clean_optional(item)
        missing = []
        if list_slug not in SHOPPING_LISTS:
            missing.append("list_name")
        if title is None:
            missing.append("item")
        if missing:
            return _missing_response(missing)

        response = self.list_items(list_slug, include_checked=include_checked)
        if response["status"] != "ok":
            return response
        matches = [
            {"item": candidate, "score": _match_score(title, candidate)}
            for candidate in response.get("data", {}).get("items", [])
        ]
        matches = [match for match in matches if match["score"] >= 0.55]
        matches.sort(key=lambda match: match["score"], reverse=True)
        if not matches:
            return _error_response(f"No matching item found for {title} on {list_name(list_slug)}.")
        if len(matches) > 1 and matches[0]["score"] < 0.9 and matches[0]["score"] - matches[1]["score"] < 0.12:
            return {
                "status": "needs_confirmation",
                "message": "Which item did you mean? " + ", ".join(_item_title(match["item"]) for match in matches[:3]),
                "data": {"matches": [match["item"] for match in matches[:3]]},
            }
        return {
            "status": "ok",
            "message": "Shopping item matched.",
            "data": {"item": matches[0]["item"]},
        }

    def set_checked(self, list_slug: str | None, item: str | None, checked: bool) -> ToolResponse:
        match = self.find_item(list_slug, item, include_checked=True)
        if match["status"] != "ok":
            return match
        matched_item = match.get("data", {}).get("item", {})
        item_id = _item_id(matched_item)
        if not item_id:
            return _error_response("The matched shopping item has no id.")
        try:
            updated = self.provider.set_checked(item_id, checked)
            snapshot = self.store.upsert_snapshot(
                self.provider.name,
                str(updated.get("list_slug") or list_slug),
                updated,
            )
            self.store.record_event(
                provider=self.provider.name,
                action="check_item" if checked else "uncheck_item",
                status="ok",
                list_slug=str(snapshot.get("list_slug") or list_slug),
                item_id=snapshot["id"],
                item_title=snapshot["title"],
                payload={"item": snapshot},
            )
        except Exception as error:
            self.store.record_event(
                provider=self.provider.name,
                action="check_item" if checked else "uncheck_item",
                status="error",
                list_slug=list_slug,
                item_id=item_id,
                item_title=_item_title(matched_item),
                message=str(error),
            )
            return _error_response(f"Could not update {item}: {error}", error)

        verb = "Checked off" if checked else "Restored"
        return {
            "status": "ok",
            "message": f"{verb} {snapshot['title']} on {list_name(str(snapshot.get('list_slug') or list_slug))}.",
            "data": {"item": snapshot},
        }

    def set_checked_by_id(self, item_id: str | None, checked: bool, list_slug: str | None = None) -> ToolResponse:
        cleaned_item_id = _clean_optional(item_id)
        if cleaned_item_id is None:
            return _missing_response(["item_id"])
        if list_slug is not None and list_slug not in SHOPPING_LISTS:
            return _missing_response(["list_name"])
        try:
            updated = self.provider.set_checked(cleaned_item_id, checked)
            snapshot_list_slug = str(updated.get("list_slug") or list_slug or "")
            if snapshot_list_slug not in SHOPPING_LISTS:
                snapshot_list_slug = list_slug or "others"
            snapshot = self.store.upsert_snapshot(
                self.provider.name,
                snapshot_list_slug,
                updated,
            )
            self.store.record_event(
                provider=self.provider.name,
                action="check_item" if checked else "uncheck_item",
                status="ok",
                list_slug=snapshot_list_slug,
                item_id=snapshot["id"],
                item_title=snapshot["title"],
                payload={"item": snapshot, "source": "dashboard"},
            )
        except Exception as error:
            self.store.record_event(
                provider=self.provider.name,
                action="check_item" if checked else "uncheck_item",
                status="error",
                list_slug=list_slug,
                item_id=cleaned_item_id,
                message=str(error),
            )
            return _error_response(f"Could not update shopping item: {error}", error)

        verb = "Checked off" if checked else "Restored"
        return {
            "status": "ok",
            "message": f"{verb} {snapshot['title']} on {list_name(snapshot_list_slug)}.",
            "data": {"item": snapshot},
        }

    def clear_list(self, list_slug: str | None) -> ToolResponse:
        if list_slug not in SHOPPING_LISTS:
            return _missing_response(["list_name"])
        response = self.list_items(list_slug, include_checked=False)
        if response["status"] != "ok":
            return response
        items = response.get("data", {}).get("items", [])
        cleared = []
        for item in items:
            item_id = _item_id(item)
            if not item_id:
                continue
            try:
                updated = self.provider.set_checked(item_id, True)
                snapshot = self.store.upsert_snapshot(self.provider.name, list_slug, updated)
                cleared.append(snapshot)
                self.store.record_event(
                    provider=self.provider.name,
                    action="check_item",
                    status="ok",
                    list_slug=list_slug,
                    item_id=snapshot["id"],
                    item_title=snapshot["title"],
                    payload={"item": snapshot, "source": "clear_list"},
                )
            except Exception as error:
                self.store.record_event(
                    provider=self.provider.name,
                    action="check_item",
                    status="error",
                    list_slug=list_slug,
                    item_id=item_id,
                    item_title=_item_title(item),
                    message=str(error),
                )
                return _error_response(f"Could not clear {list_name(list_slug)}: {error}", error)

        self.store.record_event(
            provider=self.provider.name,
            action="clear_list",
            status="ok",
            list_slug=list_slug,
            message=f"Cleared {len(cleared)} pending item(s).",
            payload={"items": cleared},
        )
        return {
            "status": "ok",
            "message": f"Cleared {len(cleared)} pending item(s) from {list_name(list_slug)}.",
            "data": {"items": cleared},
        }

    def delete_item(self, list_slug: str | None, item: str | None) -> ToolResponse:
        match = self.find_item(list_slug, item, include_checked=True)
        if match["status"] != "ok":
            return match
        matched_item = match.get("data", {}).get("item", {})
        item_id = _item_id(matched_item)
        try:
            deleted = self.provider.delete_item(item_id)
            self.store.delete_snapshot(item_id)
            self.store.record_event(
                provider=self.provider.name,
                action="delete_item",
                status="ok",
                list_slug=list_slug,
                item_id=item_id,
                item_title=_item_title(matched_item),
                payload={"item": deleted or matched_item},
            )
        except Exception as error:
            self.store.record_event(
                provider=self.provider.name,
                action="delete_item",
                status="error",
                list_slug=list_slug,
                item_id=item_id,
                item_title=_item_title(matched_item),
                message=str(error),
            )
            return _error_response(f"Could not delete {item}: {error}", error)
        return {
            "status": "ok",
            "message": f"Deleted {_item_title(matched_item)} from {list_name(list_slug)}.",
            "data": {"item": deleted or matched_item},
        }

    def move_item(
        self,
        list_slug: str | None,
        item: str | None,
        target_list_slug: str | None,
    ) -> ToolResponse:
        if target_list_slug not in SHOPPING_LISTS:
            return _missing_response(["target_list_name"])
        match = self.find_item(list_slug, item, include_checked=True)
        if match["status"] != "ok":
            return match
        matched_item = match.get("data", {}).get("item", {})
        item_id = _item_id(matched_item)
        try:
            moved = self.provider.move_item(item_id, target_list_slug)
            snapshot = self.store.upsert_snapshot(self.provider.name, target_list_slug, moved)
            self.store.record_event(
                provider=self.provider.name,
                action="move_item",
                status="ok",
                list_slug=target_list_slug,
                item_id=item_id,
                item_title=snapshot["title"],
                payload={"from": list_slug, "to": target_list_slug, "item": snapshot},
            )
        except Exception as error:
            self.store.record_event(
                provider=self.provider.name,
                action="move_item",
                status="error",
                list_slug=list_slug,
                item_id=item_id,
                item_title=_item_title(matched_item),
                message=str(error),
            )
            return _error_response(f"Could not move {item}: {error}", error)
        return {
            "status": "ok",
            "message": f"Moved {snapshot['title']} to {list_name(target_list_slug)}.",
            "data": {"item": snapshot},
        }


def build_default_tools() -> ShoppingTools:
    store = SQLiteShoppingStore()
    return ShoppingTools(build_default_provider(store), store)

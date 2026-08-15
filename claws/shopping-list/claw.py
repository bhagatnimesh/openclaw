from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import sys
from typing import Any

from constants import SHOPPING_LISTS
from intent import extract_intent, list_name
from provider import ShoppingProvider, SQLiteShoppingStore
from tools import ShoppingTools, ToolResponse, build_default_tools


def _format_item(item: dict[str, Any]) -> str:
    title = item.get("title") or item.get("name") or item.get("item") or "Untitled item"
    quantity = item.get("quantity")
    category = item.get("category")
    checked = bool(item.get("checked") or item.get("completed") or item.get("is_checked"))
    parts = [str(title)]
    if quantity:
        parts.append(str(quantity))
    if category:
        parts.append(str(category))
    if checked:
        parts.append("checked")
    return " | ".join(parts)


@dataclass
class ShoppingClaw:
    """Entry point for N4OS shopping list requests."""

    tools: ShoppingTools
    last_item: dict[str, Any] | None = None
    undo_stack: list[dict[str, Any]] = field(default_factory=list)
    last_result: ToolResponse | None = None

    @classmethod
    def from_provider(
        cls,
        provider: ShoppingProvider,
        store: SQLiteShoppingStore | None = None,
    ) -> "ShoppingClaw":
        resolved_store = store or SQLiteShoppingStore()
        return cls(tools=ShoppingTools(provider, resolved_store))

    @classmethod
    def default(cls) -> "ShoppingClaw":
        return cls(tools=build_default_tools())

    def tool_map(self) -> dict[str, Any]:
        return {
            "list_shopping_lists": self.tools.list_shopping_lists,
            "list_shopping_items": self.tools.list_items,
            "add_shopping_item": self.tools.add_item,
            "check_shopping_item": self.tools.set_checked,
            "clear_shopping_list": self.tools.clear_list,
            "delete_shopping_item": self.tools.delete_item,
            "move_shopping_item": self.tools.move_item,
            "undo_shopping_action": self.undo_last_action,
        }

    def handle_request(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> str:
        intent = extract_intent(request, now=reference_time)
        action = intent.get("intent")
        if action == "list_lists":
            return self.list_lists_from_request(request, reference_time=reference_time)
        if action == "list_items":
            return self.list_items_from_request(request, reference_time=reference_time)
        if action in ("add_item", "add_items"):
            return self.add_items_from_request(request, reference_time=reference_time)
        if action == "check_item":
            return self.check_item_from_request(request, checked=True)
        if action == "uncheck_item":
            return self.check_item_from_request(request, checked=False)
        if action == "delete_item":
            return self.delete_item_from_request(request)
        if action == "clear_list":
            return self.clear_list_from_request(request, reference_time=reference_time)
        if action == "move_item":
            return self.move_item_from_request(request)

        message = "That does not look like a shopping-list request."
        print(message)
        return message

    def list_lists_from_request(
        self,
        request: str = "",
        reference_time: datetime | None = None,
    ) -> str:
        del request, reference_time
        response = self.tools.list_shopping_lists()
        self.last_result = response
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message
        lists = response.get("data", {}).get("lists", [])
        if not lists:
            lists = [{"slug": slug, "name": name, "pending_count": 0} for slug, name in SHOPPING_LISTS.items()]
        lines = ["Shopping lists:"]
        for shopping_list in lists:
            name = shopping_list.get("name") or list_name(shopping_list.get("slug"))
            count = shopping_list.get("pending_count")
            suffix = f" ({count} pending)" if count is not None else ""
            lines.append(f"- {name}{suffix}")
        message = "\n".join(lines)
        print(message)
        return message

    def list_items_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> str:
        intent = extract_intent(request, now=reference_time)
        response = self.tools.list_items(intent.get("list_slug"))
        self.last_result = response
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message
        items = response.get("data", {}).get("items", [])
        list_slug = intent.get("list_slug")
        if not items:
            message = f"No pending items on {list_name(list_slug)}."
            print(message)
            return message
        lines = [f"{list_name(list_slug)}:"]
        lines.extend(f"- {_format_item(item)}" for item in items)
        self.last_item = items[0]
        message = "\n".join(lines)
        print(message)
        return message

    def add_items_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> str:
        intent = extract_intent(request, now=reference_time)
        missing = intent.get("missing_fields", [])
        if missing and missing != ["list_name"]:
            self.last_result = {
                "status": "needs_information",
                "message": "Please provide: " + ", ".join(missing) + ".",
            }
            message = self.last_result["message"]
            print(message)
            return message
        items = list(intent.get("items") or [intent.get("item")])
        if len(items) > 1:
            response = self.tools.add_items(intent.get("list_slug"), items)
            self.last_result = response
            if response["status"] != "ok":
                message = response["message"]
                print(message)
                return message
            created = response.get("data", {}).get("items", [])
            if created:
                self.last_item = created[-1]
                self.undo_stack.append({"action": "delete_items", "items": created})
            lines = [f"Added {len(created)} items to {list_name(intent.get('list_slug'))}:"]
            lines.extend(f"- {_format_item(item)}" for item in created)
            message = "\n".join(lines)
            print(message)
            return message

        response = self.tools.add_item(
            list_slug=intent.get("list_slug"),
            item=intent.get("item"),
        )
        self.last_result = response
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message
        item = response.get("data", {}).get("item", {})
        self.last_item = item
        if item.get("id"):
            self.undo_stack.append({"action": "delete_items", "items": [item]})
        message = response["message"]
        print(message)
        return message

    def check_item_from_request(self, request: str, checked: bool) -> str:
        intent = extract_intent(request)
        before = self.last_item
        response = self.tools.set_checked(
            list_slug=intent.get("list_slug"),
            item=intent.get("item"),
            checked=checked,
        )
        self.last_result = response
        if response["status"] == "ok":
            item = response.get("data", {}).get("item", {})
            self.last_item = item
            self.undo_stack.append(
                {
                    "action": "set_checked",
                    "item": item,
                    "checked": not checked,
                    "before": before,
                }
            )
        message = response["message"]
        print(message)
        return message

    def delete_item_from_request(self, request: str) -> str:
        intent = extract_intent(request)
        response = self.tools.delete_item(
            list_slug=intent.get("list_slug"),
            item=intent.get("item"),
        )
        self.last_result = response
        message = response["message"]
        print(message)
        return message

    def clear_list_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> str:
        intent = extract_intent(request, now=reference_time)
        response = self.tools.clear_list(intent.get("list_slug"))
        self.last_result = response
        if response["status"] == "ok":
            items = response.get("data", {}).get("items", [])
            if items:
                self.undo_stack.append({"action": "restore_items", "items": items})
        message = response["message"]
        print(message)
        return message

    def move_item_from_request(self, request: str) -> str:
        intent = extract_intent(request)
        response = self.tools.move_item(
            list_slug=intent.get("list_slug"),
            item=intent.get("item"),
            target_list_slug=intent.get("target_list_slug"),
        )
        self.last_result = response
        if response["status"] == "ok":
            item = response.get("data", {}).get("item", {})
            self.last_item = item
            self.undo_stack.append(
                {
                    "action": "move_item",
                    "item": item,
                    "target_list_slug": intent.get("list_slug"),
                }
            )
        message = response["message"]
        print(message)
        return message

    def undo_last_action(self) -> str:
        if not self.undo_stack:
            message = "Nothing to undo for Shopping."
            print(message)
            return message

        undo = self.undo_stack.pop()
        action = undo.get("action")
        if action == "delete_items":
            removed = []
            for item in reversed(undo.get("items", [])):
                item_id = item.get("id")
                if not item_id:
                    continue
                try:
                    deleted = self.tools.provider.delete_item(item_id)
                except Exception:
                    deleted = None
                if deleted is not None:
                    removed.append(item)
            message = (
                f"Undid shopping add: removed {len(removed)} item(s)."
                if removed
                else "I could not undo the shopping add; the item was not found."
            )
            print(message)
            return message

        if action == "set_checked":
            item = undo.get("item", {})
            item_id = item.get("id")
            if item_id:
                try:
                    restored = self.tools.provider.set_checked(item_id, bool(undo.get("checked")))
                    self.last_item = restored
                    message = "Undid shopping check state for " + _format_item(restored) + "."
                except Exception as error:
                    message = f"I could not undo that shopping check state: {error}"
            else:
                message = "I could not undo that shopping check state."
            print(message)
            return message

        if action == "restore_items":
            restored = []
            for item in undo.get("items", []):
                item_id = item.get("id")
                if not item_id:
                    continue
                try:
                    restored_item = self.tools.provider.set_checked(item_id, False)
                    restored.append(restored_item)
                except Exception:
                    continue
            message = f"Undid shopping clear: restored {len(restored)} item(s)."
            print(message)
            return message

        if action == "move_item":
            item = undo.get("item", {})
            item_id = item.get("id")
            target_list_slug = undo.get("target_list_slug")
            if item_id and target_list_slug:
                try:
                    moved = self.tools.provider.move_item(item_id, target_list_slug)
                    self.last_item = moved
                    message = f"Undid shopping move: moved {_format_item(moved)} to {list_name(target_list_slug)}."
                except Exception as error:
                    message = f"I could not undo that shopping move: {error}"
            else:
                message = "I could not undo that shopping move."
            print(message)
            return message

        message = "I do not know how to undo that shopping action."
        print(message)
        return message


def run_cli(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    claw = ShoppingClaw.default()
    request = " ".join(args).strip()
    if request:
        claw.handle_request(request)
        return

    print("Shopping. Type a shopping request, or 'exit' to quit.")
    while True:
        try:
            command = input("> ").strip()
        except EOFError:
            print()
            return
        if command.lower() in ("exit", "quit"):
            return
        if command:
            claw.handle_request(command)


if __name__ == "__main__":
    run_cli()

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import sys
from typing import Any

from intent import extract_intent
from tools import HomeBoardProvider, HomeBoardTools, build_default_tools


def _format_item(item: dict[str, Any]) -> str:
    person = item.get("person_or_group") or "Family"
    message = item.get("message") or "Untitled notice"
    context = item.get("context") or "general"
    return f"{person}: {message} ({context})"


@dataclass
class HomeBoardClaw:
    """Entry point for N4OS Home Board household notices."""

    tools: HomeBoardTools
    last_item: dict[str, Any] | None = None
    undo_stack: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_provider(cls, provider: HomeBoardProvider) -> "HomeBoardClaw":
        return cls(tools=HomeBoardTools(provider))

    @classmethod
    def default(cls) -> "HomeBoardClaw":
        return cls(tools=build_default_tools())

    def tool_map(self) -> dict[str, Any]:
        return {
            "add_home_board_item": self.tools.add_item,
            "list_home_board_items": self.tools.list_items,
            "mark_home_board_done": self.tools.mark_done,
            "undo_home_board_action": self.undo_last_action,
        }

    def add_item_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> str:
        intent = extract_intent(request, now=reference_time)
        if intent.get("intent") == "add_items":
            return self.add_items_from_request(request, reference_time=reference_time)
        if intent.get("intent") != "add_item":
            message = "That does not look like a Home Board notice."
            print(message)
            return message
        missing = intent.get("missing_fields", [])
        if missing:
            message = "Please provide: " + ", ".join(missing) + "."
            print(message)
            return message

        response = self.tools.add_item(
            person_or_group=intent["person_or_group"],
            message=intent["message"],
            date=intent["date"],
            context=intent.get("context", "general"),
            trigger=intent.get("trigger"),
            priority=intent.get("priority", "medium"),
            expires_at=intent.get("expires_at"),
        )
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message

        item = response.get("data", {}).get("item", {})
        self.last_item = item
        if item.get("id"):
            self.undo_stack.append({"action": "delete_items", "items": [item]})
        message = "Added to Today at Home: " + _format_item(item)
        print(message)
        return message

    def add_items_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> str:
        intent = extract_intent(request, now=reference_time)
        items = intent.get("items") or []
        if intent.get("intent") != "add_items" or not items:
            return self.add_item_from_request(request, reference_time=reference_time)

        created = []
        for item in items:
            response = self.tools.add_item(
                person_or_group=item["person_or_group"],
                message=item["message"],
                date=item["date"],
                context=item.get("context", "general"),
                trigger=item.get("trigger"),
                priority=item.get("priority", "medium"),
                expires_at=item.get("expires_at"),
            )
            if response["status"] != "ok":
                message = response["message"]
                print(message)
                return message
            created_item = response.get("data", {}).get("item", {})
            self.last_item = created_item
            created.append(created_item)

        undo_items = [item for item in created if item.get("id")]
        if undo_items:
            self.undo_stack.append({"action": "delete_items", "items": undo_items})
        lines = [f"Added {len(created)} items to Today at Home:"]
        lines.extend(f"- {_format_item(item)}" for item in created)
        message = "\n".join(lines)
        print(message)
        return message

    def list_items_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> str:
        intent = extract_intent(request, now=reference_time)
        item_date = intent.get("date")
        response = self.tools.list_items(
            date=item_date,
            status="pending",
            now=reference_time,
        )
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message

        items = response.get("data", {}).get("items", [])
        if not items:
            message = "No pending Home Board items."
            print(message)
            return message

        lines = ["Today at Home:"]
        lines.extend(f"- {_format_item(item)}" for item in items)
        self.last_item = items[0]
        message = "\n".join(lines)
        print(message)
        return message

    def mark_done_from_request(self, request: str) -> str:
        intent = extract_intent(request)
        item_id = intent.get("item_id")
        if not item_id and self.last_item is not None:
            item_id = self.last_item.get("id")
        before = self.last_item if item_id and self.last_item and self.last_item.get("id") == item_id else None
        response = self.tools.mark_done(item_id)
        if response["status"] == "ok":
            item = response.get("data", {}).get("item", {})
            if item.get("id"):
                self.undo_stack.append(
                    {"action": "mark_pending", "item": before or item},
                )
                self.last_item = item
        message = response["message"]
        print(message)
        return message

    def undo_last_action(self) -> str:
        if not self.undo_stack:
            message = "Nothing to undo for Home Board."
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
                response = self.tools.delete_item(item_id)
                if response["status"] == "ok":
                    removed.append(item)
            if removed:
                message = f"Undid Home Board add: removed {len(removed)} item(s)."
            else:
                message = "I could not undo the Home Board add; the item was not found."
            print(message)
            return message

        if action == "mark_pending":
            item = undo.get("item", {})
            response = self.tools.mark_pending(item.get("id"))
            if response["status"] == "ok":
                restored = response.get("data", {}).get("item", item)
                self.last_item = restored
                message = "Undid Home Board done: restored " + _format_item(restored) + "."
            else:
                message = response["message"]
            print(message)
            return message

        message = "I do not know how to undo that Home Board action."
        print(message)
        return message


def run_cli(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    claw = HomeBoardClaw.default()
    request = " ".join(args).strip()
    if request:
        claw.add_item_from_request(request)
        return

    print("Home Board. Type a household notice, or 'exit' to quit.")
    while True:
        try:
            command = input("> ").strip()
        except EOFError:
            print()
            return
        if command.lower() in ("exit", "quit"):
            return
        if command:
            claw.add_item_from_request(command)


if __name__ == "__main__":
    run_cli()

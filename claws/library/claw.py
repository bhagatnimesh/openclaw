from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import sys
from typing import Any

from intent import extract_intent
from tools import LibraryProvider, LibraryTools, build_default_tools


@dataclass
class LibraryClaw:
    """Entry point for Nysha's Reading Garden from chat channels."""

    tools: LibraryTools

    @classmethod
    def from_provider(cls, provider: LibraryProvider) -> "LibraryClaw":
        return cls(tools=LibraryTools(provider))

    @classmethod
    def default(cls) -> "LibraryClaw":
        return cls(tools=build_default_tools())

    def tool_map(self) -> dict[str, Any]:
        return {
            "record_reading_garden_event": self.tools.record_reading,
            "record_library_checkout": self.tools.record_checkout,
            "reading_garden_status": self.tools.status,
            "update_reading_garden_event": self.tools.update_reading,
            "delete_reading_garden_event": self.tools.delete_reading,
        }

    def record_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
        *,
        source: str = "telegram_text",
        photo_path: str | None = None,
    ) -> str:
        response = self.tools.record_reading(
            request,
            now=reference_time,
            source=source,
            photo_path=photo_path,
        )
        message = response["message"]
        print(message)
        return message

    def checkout_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
        *,
        source: str = "telegram_text",
    ) -> str:
        response = self.tools.record_checkout(
            request,
            now=reference_time,
            source=source,
        )
        message = response["message"]
        print(message)
        return message

    def status_from_request(
        self,
        request: str = "",
        reference_time: datetime | None = None,
    ) -> str:
        del request
        response = self.tools.status(now=reference_time)
        message = response["message"]
        print(message)
        return message


def run_cli(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    claw = LibraryClaw.default()
    request = " ".join(args).strip()
    if request:
        claw.record_from_request(request)
        return

    print("Library Claw. Type a reading event, library checkout, /status, or 'exit'.")
    while True:
        try:
            command = input("> ").strip()
        except EOFError:
            print()
            return
        if command.lower() in ("exit", "quit"):
            return
        if command:
            claw.record_from_request(command)


if __name__ == "__main__":
    run_cli()

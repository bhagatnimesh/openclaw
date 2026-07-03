from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
import re
from typing import Any
from zoneinfo import ZoneInfo

from prompts import SYSTEM_PROMPT, TOOL_GUIDANCE
from tools import DEFAULT_TIMEZONE, CalendarProvider, CalendarTools, build_default_tools


WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _clean_spaces(value: str) -> str:
    return " ".join(value.split()).strip()


def _title_case(value: str) -> str:
    return value[:1].upper() + value[1:] if value else value


def _next_weekday(reference: datetime, weekday: int) -> datetime:
    days_ahead = (weekday - reference.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7

    return reference + timedelta(days=days_ahead)


def _parse_request_date(request: str, reference: datetime) -> tuple[datetime | None, str]:
    lowered = request.lower()
    if "tomorrow" in lowered:
        return reference + timedelta(days=1), "tomorrow"
    if "today" in lowered:
        return reference, "today"

    for name, weekday in WEEKDAYS.items():
        if re.search(rf"\b{name}\b", lowered):
            return _next_weekday(reference, weekday), name

    return None, ""


def _parse_duration(request: str) -> tuple[timedelta, str]:
    match = re.search(
        r"\bfor\s+(\d+)\s*(minute|minutes|min|hour|hours|hr|hrs)\b",
        request,
        flags=re.IGNORECASE,
    )
    if match is None:
        return timedelta(hours=1), ""

    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith(("hour", "hr")):
        return timedelta(hours=amount), match.group(0)

    return timedelta(minutes=amount), match.group(0)


def _parse_time(
    request: str,
    title_hint: str,
) -> tuple[time | None, str, str | None]:
    match = re.search(
        r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b",
        request,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None, "", None

    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    meridiem = (match.group(3) or "").replace(".", "").lower()
    if hour > 23 or minute > 59:
        return None, match.group(0), "a valid time"

    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    elif meridiem == "" and 1 <= hour <= 11:
        # Milestone 1 supports the common family request "dinner at 7".
        # Other bare times need AM/PM so we do not silently guess.
        if "dinner" in title_hint.lower() and hour <= 7:
            hour += 12
        else:
            return None, match.group(0), "AM or PM"

    return time(hour=hour, minute=minute), match.group(0), None


def _remove_piece(value: str, piece: str) -> str:
    if piece:
        return re.sub(re.escape(piece), " ", value, count=1, flags=re.IGNORECASE)

    return value


def _parse_title_and_description(
    request: str,
    date_piece: str,
    time_piece: str,
    duration_piece: str,
) -> tuple[str | None, str | None]:
    title = re.sub(r"^\s*(add|create|schedule)\s+", "", request, flags=re.IGNORECASE)
    title = _remove_piece(title, date_piece)
    title = _remove_piece(title, time_piece)
    title = _remove_piece(title, duration_piece)

    description = None
    with_match = re.search(r"\bwith\s+(.+)$", title, flags=re.IGNORECASE)
    if with_match is not None:
        description = f"with {_clean_spaces(with_match.group(1))}"
        title = title[: with_match.start()]

    title = _clean_spaces(title)
    if not title:
        return None, description

    return _title_case(title), description


@dataclass
class FamilyCalendarClaw:
    """Small OpenClaw entry point for the family calendar claw.

    The claw exposes prompt text and tool callables. Calendar API behavior stays
    in the provider, while LLM-facing validation and safety rules stay in tools.
    """

    tools: CalendarTools
    system_prompt: str = SYSTEM_PROMPT
    tool_guidance: str = TOOL_GUIDANCE

    @classmethod
    def from_provider(cls, provider: CalendarProvider) -> "FamilyCalendarClaw":
        return cls(tools=CalendarTools(provider))

    @classmethod
    def default(cls, calendar_id: str = "primary") -> "FamilyCalendarClaw":
        return cls(tools=build_default_tools(calendar_id=calendar_id))

    def tool_map(self) -> dict[str, Any]:
        """Return the OpenClaw-visible tool names and their handlers."""

        return {
            "create_calendar_event": self.tools.create_calendar_event,
            "list_calendar_events": self.tools.list_calendar_events,
            "delete_calendar_event": self.tools.delete_calendar_event,
        }

    def create_event_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> str:
        """Parse one simple add-event request and create it through the tool.

        This is intentionally small for Milestone 1. It does not plan,
        categorize, remember preferences, or infer missing event details.
        """

        timezone = DEFAULT_TIMEZONE
        reference = reference_time or datetime.now(ZoneInfo(timezone))
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=ZoneInfo(timezone))

        duration, duration_piece = _parse_duration(request)
        event_date, date_piece = _parse_request_date(request, reference)
        provisional_title, _ = _parse_title_and_description(
            request,
            date_piece,
            "",
            duration_piece,
        )
        event_time, time_piece, missing_time_detail = _parse_time(
            request,
            provisional_title or "",
        )
        title, description = _parse_title_and_description(
            request,
            date_piece,
            time_piece,
            duration_piece,
        )

        missing = []
        if title is None:
            missing.append("title")
        if event_date is None:
            missing.append("date")
        if event_time is None:
            missing.append(missing_time_detail or "time")

        if missing:
            message = "Please provide: " + ", ".join(missing) + "."
            print(message)
            return message

        start = datetime.combine(
            event_date.date(),
            event_time,
            tzinfo=ZoneInfo(timezone),
        )
        end = start + duration
        response = self.tools.create_calendar_event(
            title=title,
            start_time=start.isoformat(),
            end_time=end.isoformat(),
            timezone=timezone,
            description=description,
        )
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message

        event = response.get("data", {}).get("event", {})
        event_title = event.get("summary", title)
        event_id = event.get("id")
        id_suffix = f" (event id: {event_id})" if event_id else ""
        message = (
            f"Created calendar event: {event_title} on "
            f"{start.strftime('%A, %B %-d')} from {start.strftime('%-I:%M %p')} "
            f"to {end.strftime('%-I:%M %p')} {timezone}{id_suffix}."
        )
        print(message)
        return message


def run_cli(claw: FamilyCalendarClaw | None = None) -> None:
    """Run the minimal Feature #1 terminal interface."""

    print("Family Calendar Claw")
    active_claw = claw

    while True:
        try:
            request = input("> ")
        except EOFError:
            print()
            break

        command = request.strip()
        if not command:
            continue
        if command.lower() == "exit":
            break

        if active_claw is None:
            active_claw = FamilyCalendarClaw.default()
        active_claw.create_event_from_request(command)


if __name__ == "__main__":
    run_cli()

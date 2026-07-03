from __future__ import annotations

from typing import Any, Literal, Protocol, TypedDict


DEFAULT_TIMEZONE = "America/Los_Angeles"


class CalendarProvider(Protocol):
    """Provider contract used by the OpenClaw tools layer.

    GoogleCalendarProvider satisfies this protocol, but the tools do not depend
    on that concrete class. That keeps Google Calendar API details away from
    the LLM-facing layer and makes the tools easy to test.
    """

    def create_event(
        self,
        title: str,
        start_time: str,
        end_time: str,
        timezone: str = DEFAULT_TIMEZONE,
        description: str | None = None,
        location: str | None = None,
    ) -> dict[str, Any]:
        ...

    def list_events(
        self,
        time_min: str,
        time_max: str,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        ...

    def delete_event(self, event_id: str) -> None:
        ...


class ToolResponse(TypedDict, total=False):
    status: Literal["ok", "needs_information", "error"]
    message: str
    data: dict[str, Any]


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = value.strip()
    if not cleaned:
        return None

    return cleaned


def _missing_response(fields: list[str]) -> ToolResponse:
    field_list = ", ".join(fields)
    return {
        "status": "needs_information",
        "message": f"Missing required calendar information: {field_list}.",
        "data": {"missing_fields": fields},
    }


class CalendarTools:
    """OpenClaw tool layer for the family calendar.

    Flow:
    1. Validate that the model supplied required information.
    2. Apply only safe defaults, such as the configured timezone.
    3. Delegate all calendar reads and writes to the provider.

    The provider remains the source of truth; these tools never fabricate
    events or fill in event details that the user did not supply.
    """

    def __init__(self, provider: CalendarProvider):
        self.provider = provider

    def create_calendar_event(
        self,
        title: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        timezone: str | None = DEFAULT_TIMEZONE,
        description: str | None = None,
        location: str | None = None,
    ) -> ToolResponse:
        missing_fields: list[str] = []
        cleaned_title = _clean_optional(title)
        cleaned_start = _clean_optional(start_time)
        cleaned_end = _clean_optional(end_time)

        if cleaned_title is None:
            missing_fields.append("title")
        if cleaned_start is None:
            missing_fields.append("start_time")
        if cleaned_end is None:
            missing_fields.append("end_time")

        if missing_fields:
            return _missing_response(missing_fields)

        event = self.provider.create_event(
            title=cleaned_title,
            start_time=cleaned_start,
            end_time=cleaned_end,
            timezone=_clean_optional(timezone) or DEFAULT_TIMEZONE,
            description=_clean_optional(description),
            location=_clean_optional(location),
        )
        return {
            "status": "ok",
            "message": "Calendar event created.",
            "data": {"event": event},
        }

    def list_calendar_events(
        self,
        time_min: str | None = None,
        time_max: str | None = None,
        max_results: int = 10,
    ) -> ToolResponse:
        missing_fields: list[str] = []
        cleaned_min = _clean_optional(time_min)
        cleaned_max = _clean_optional(time_max)

        if cleaned_min is None:
            missing_fields.append("time_min")
        if cleaned_max is None:
            missing_fields.append("time_max")

        if missing_fields:
            return _missing_response(missing_fields)

        events = self.provider.list_events(
            time_min=cleaned_min,
            time_max=cleaned_max,
            max_results=max_results,
        )
        return {
            "status": "ok",
            "message": "Calendar events returned from Google Calendar.",
            "data": {"events": events},
        }

    def delete_calendar_event(self, event_id: str | None = None) -> ToolResponse:
        cleaned_event_id = _clean_optional(event_id)
        if cleaned_event_id is None:
            return _missing_response(["event_id"])

        self.provider.delete_event(cleaned_event_id)
        return {
            "status": "ok",
            "message": "Calendar event deleted.",
            "data": {"event_id": cleaned_event_id},
        }


def build_default_tools(calendar_id: str = "primary") -> CalendarTools:
    """Build tools backed by the production Google Calendar provider."""

    from provider import GoogleCalendarProvider

    return CalendarTools(GoogleCalendarProvider(calendar_id=calendar_id))

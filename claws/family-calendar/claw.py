from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from datetime import datetime, timedelta
import re
from typing import Any
from zoneinfo import ZoneInfo

from intent import extract_intent
from prompts import SYSTEM_PROMPT, TOOL_GUIDANCE
from tools import DEFAULT_TIMEZONE, CalendarProvider, CalendarTools, build_default_tools


SYNONYMS = {
    "airport": ["sfo", "flight", "airport"],
    "dental": ["dentist", "dental"],
    "shopping": ["shop", "shopping", "mall", "store"],
    "pickup": ["pickup", "pick up", "school pickup"],
    "dinner": ["dinner", "meal", "restaurant"],
}

MIN_CONFIDENT_SCORE = 3

RRULE_WEEKDAY_LABELS = {
    "MO": "Monday",
    "TU": "Tuesday",
    "WE": "Wednesday",
    "TH": "Thursday",
    "FR": "Friday",
    "SA": "Saturday",
    "SU": "Sunday",
}


def _parse_event_time(value: str | None) -> datetime | None:
    if value is None:
        return None

    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _event_start(event: dict[str, Any]) -> datetime:
    start = event.get("start", {})
    parsed = _parse_event_time(start.get("dateTime"))
    if parsed is not None:
        return parsed

    parsed_date = _parse_event_time(start.get("date"))
    return parsed_date or datetime.max


def _format_event_time(event_part: dict[str, Any]) -> str:
    parsed = _parse_event_time(event_part.get("dateTime"))
    if parsed is not None:
        return parsed.strftime("%-I:%M %p")

    return "all day"


def _normalize_match_text(value: str | None) -> str:
    if value is None:
        return ""

    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _tokens(value: str | None) -> set[str]:
    return set(_normalize_match_text(value).split())


def _field_text(event: dict[str, Any], field: str) -> str:
    return _normalize_match_text(str(event.get(field) or ""))


def _event_match_text(event: dict[str, Any]) -> str:
    return _normalize_match_text(
        " ".join(
            str(part)
            for part in (
                event.get("summary"),
                event.get("description"),
                event.get("location"),
            )
            if part
        )
    )


def _expanded_query_terms(query: str) -> set[str]:
    normalized_query = _normalize_match_text(query)
    terms = set(normalized_query.split())
    for canonical, synonyms in SYNONYMS.items():
        synonym_forms = {_normalize_match_text(value) for value in synonyms}
        if canonical in terms or any(form in normalized_query for form in synonym_forms):
            terms.update(
                token
                for synonym in synonyms
                for token in _normalize_match_text(synonym).split()
            )
            terms.add(canonical)
    return terms


def _score_event_match(query: str, event: dict[str, Any]) -> int:
    normalized_query = _normalize_match_text(query)
    if not normalized_query:
        return 0

    title = _field_text(event, "summary")
    location = _field_text(event, "location")
    description = _field_text(event, "description")
    all_text = _event_match_text(event)
    query_tokens = set(normalized_query.split())
    expanded_terms = _expanded_query_terms(query)
    score = 0

    if normalized_query and normalized_query in title:
        score += 8
    if query_tokens and query_tokens.issubset(set(title.split())):
        score += 6
    if normalized_query and normalized_query in all_text:
        score += 3

    title_tokens = set(title.split())
    location_tokens = set(location.split())
    description_tokens = set(description.split())
    score += 3 * len(query_tokens & title_tokens)
    score += 2 * len(query_tokens & location_tokens)
    score += len(query_tokens & description_tokens)

    synonym_hits = expanded_terms & set(all_text.split())
    score += 2 * len(synonym_hits - query_tokens)
    if expanded_terms & title_tokens:
        score += 2

    return score


def match_events(user_request: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank possible event matches for destructive operations."""

    intent = extract_intent(user_request)
    query = intent.get("query") or user_request
    ranked = []
    for event in events:
        score = _score_event_match(query, event)
        if score > 0:
            ranked.append({"event": event, "score": score})

    return sorted(
        ranked,
        key=lambda item: (-item["score"], _event_start(item["event"])),
    )


def _format_event_choice(event: dict[str, Any]) -> str:
    title = event.get("summary") or "Untitled event"
    start_label = _format_event_time(event.get("start", {}))
    end_label = _format_event_time(event.get("end", {}))
    location = event.get("location")
    location_suffix = f" at {location}" if location else ""
    return f"{title}: {start_label} to {end_label}{location_suffix}"


def _format_confirmation_event(event: dict[str, Any]) -> str:
    title = event.get("summary") or "Untitled event"
    start = _event_start(event)
    end = _parse_event_time(event.get("end", {}).get("dateTime"))
    if start == datetime.max or end is None:
        return _format_event_choice(event)

    return (
        f"{title} {start.strftime('%A, %B %-d')} "
        f"{start.strftime('%-I:%M %p')}–{end.strftime('%-I:%M %p')}"
    )


def _parse_rrule_parts(recurrence: list[str] | None) -> dict[str, str]:
    if not recurrence:
        return {}

    rule = recurrence[0]
    if rule.startswith("RRULE:"):
        rule = rule.removeprefix("RRULE:")

    parts = {}
    for part in rule.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        parts[key] = value
    return parts


def _format_created_event_message(
    event_title: str,
    start: datetime,
    end: datetime,
    timezone: str,
    recurrence: list[str] | None,
    recurrence_label: str | None,
    event_id: str | None,
) -> str:
    id_suffix = f" (event id: {event_id})" if event_id else ""
    rrule = _parse_rrule_parts(recurrence)
    count = int(rrule["COUNT"]) if rrule.get("COUNT", "").isdigit() else None
    weekday = RRULE_WEEKDAY_LABELS.get(rrule.get("BYDAY", ""))

    if rrule.get("FREQ") == "WEEKLY" and count is not None and weekday is not None:
        final_date = start + timedelta(days=7 * (count - 1))
        return (
            f"Created calendar event: {event_title} every {weekday} from "
            f"{start.strftime('%A, %B %-d')} through "
            f"{final_date.strftime('%A, %B %-d')}, {count} occurrences, "
            f"{start.strftime('%-I:%M %p')}–{end.strftime('%-I:%M %p')} "
            f"{timezone}{id_suffix}."
        )

    recurrence_suffix = ""
    if recurrence_label:
        recurrence_suffix = f", repeating {recurrence_label}"
    return (
        f"Created calendar event: {event_title} on "
        f"{start.strftime('%A, %B %-d')} from {start.strftime('%-I:%M %p')} "
        f"to {end.strftime('%-I:%M %p')} {timezone}{recurrence_suffix}{id_suffix}."
    )


@dataclass
class PendingAction:
    action: str
    event: dict[str, Any] | None = None
    choices: list[dict[str, Any]] | None = None
    payload: dict[str, Any] | None = None


@dataclass
class FamilyCalendarClaw:
    """Small OpenClaw entry point for the family calendar claw.

    The claw exposes prompt text and tool callables. Calendar API behavior stays
    in the provider, while LLM-facing validation and safety rules stay in tools.
    """

    tools: CalendarTools
    system_prompt: str = SYSTEM_PROMPT
    tool_guidance: str = TOOL_GUIDANCE
    pending_action: PendingAction | None = None

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
            "update_calendar_event": self.tools.update_calendar_event,
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

        intent = extract_intent(request, now=reference_time)
        missing = intent.get("missing_fields", [])
        if missing:
            message = "Please provide: " + ", ".join(missing) + "."
            print(message)
            return message

        timezone = intent.get("timezone") or DEFAULT_TIMEZONE
        start = datetime.fromisoformat(f"{intent['date']}T{intent['start_time']}:00")
        start = start.replace(tzinfo=ZoneInfo(timezone))
        end = start + timedelta(minutes=int(intent["duration_minutes"]))
        response = self.tools.create_calendar_event(
            title=intent["title"],
            start_time=start.isoformat(),
            end_time=end.isoformat(),
            timezone=timezone,
            description=intent.get("description"),
            location=intent.get("location"),
            recurrence=intent.get("recurrence"),
        )
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message

        event = response.get("data", {}).get("event", {})
        event_title = event.get("summary", intent["title"])
        event_id = event.get("id")
        message = _format_created_event_message(
            event_title=event_title,
            start=start,
            end=end,
            timezone=timezone,
            recurrence=intent.get("recurrence"),
            recurrence_label=intent.get("recurrence_label"),
            event_id=event_id,
        )
        print(message)
        return message

    def list_events_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> str:
        """Parse one simple read request and print Google Calendar events."""

        intent = extract_intent(request, now=reference_time)
        missing = intent.get("missing_fields", [])
        if missing:
            message = "Please provide: " + ", ".join(missing) + "."
            print(message)
            return message

        response = self.tools.list_calendar_events(
            time_min=intent["start"],
            time_max=intent["end"],
            max_results=50,
        )
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message

        events = sorted(
            response.get("data", {}).get("events", []),
            key=_event_start,
        )
        if not events:
            message = "No calendar events found for that time."
            print(message)
            return message

        lines = ["Calendar events:"]
        for event in events:
            title = event.get("summary") or "Untitled event"
            start_part = event.get("start", {})
            end_part = event.get("end", {})
            start_label = _format_event_time(start_part)
            end_label = _format_event_time(end_part)
            location = event.get("location")
            location_suffix = f" at {location}" if location else ""
            lines.append(f"- {title}: {start_label} to {end_label}{location_suffix}")

        message = "\n".join(lines)
        print(message)
        return message

    def delete_event_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> str:
        """Find a matching Google Calendar event and delete only one strong match."""

        intent = extract_intent(request, now=reference_time)
        missing = intent.get("missing_fields", [])
        if missing:
            message = "Please provide: " + ", ".join(missing) + "."
            print(message)
            return message

        response = self.tools.list_calendar_events(
            time_min=intent["search_start"],
            time_max=intent["search_end"],
            max_results=50,
        )
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message

        events = sorted(
            response.get("data", {}).get("events", []),
            key=_event_start,
        )
        ranked_matches = match_events(request, events)
        matches = [
            item["event"]
            for item in ranked_matches
            if item["score"] >= MIN_CONFIDENT_SCORE
        ]
        if not matches:
            message = "I couldn't find a matching event. Try including the event name or date."
            print(message)
            return message

        if len(matches) > 1:
            lines = ["Multiple matching events found. Which one should I delete?"]
            for index, event in enumerate(matches, start=1):
                lines.append(f"{index}. {_format_event_choice(event)}")
            message = "\n".join(lines)
            self.pending_action = PendingAction(action="delete", choices=matches)
            print(message)
            return message

        event = matches[0]
        self.pending_action = PendingAction(action="delete", event=event)
        message = (
            f"I found this event: {_format_confirmation_event(event)}. "
            "Delete it? yes/no"
        )
        print(message)
        return message

    def update_event_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> str:
        """Find a matching event and ask before moving it."""

        intent = extract_intent(request, now=reference_time)
        missing = intent.get("missing_fields", [])
        if missing:
            message = "Please provide: " + ", ".join(missing) + "."
            print(message)
            return message

        response = self.tools.list_calendar_events(
            time_min=intent["search_start"],
            time_max=intent["search_end"],
            max_results=50,
        )
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message

        events = sorted(
            response.get("data", {}).get("events", []),
            key=_event_start,
        )
        ranked_matches = match_events(request, events)
        matches = [
            item["event"]
            for item in ranked_matches
            if item["score"] >= MIN_CONFIDENT_SCORE
        ]
        if not matches:
            message = "I couldn't find a matching event. Try including the event name or date."
            print(message)
            return message

        if len(matches) > 1:
            lines = ["Multiple matching events found. Which one should I move?"]
            for index, event in enumerate(matches, start=1):
                lines.append(f"{index}. {_format_event_choice(event)}")
            message = "\n".join(lines)
            self.pending_action = PendingAction(
                action="update",
                choices=matches,
                payload=intent,
            )
            print(message)
            return message

        event = matches[0]
        self.pending_action = PendingAction(
            action="update",
            event=event,
            payload=intent,
        )
        moved = self._build_updated_event(event, intent)
        message = (
            f"I found this event: {_format_confirmation_event(event)}. "
            f"Move it to {_format_confirmation_event(moved)}? yes/no"
        )
        print(message)
        return message

    def _build_updated_event(
        self,
        event: dict[str, Any],
        intent: dict[str, Any],
    ) -> dict[str, Any]:
        current_start = _event_start(event)
        current_end = _parse_event_time(event.get("end", {}).get("dateTime"))
        if current_start == datetime.max:
            current_start = datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
        duration = timedelta(hours=1)
        if current_end is not None:
            duration = current_end - current_start
        if intent.get("duration_specified"):
            duration = timedelta(minutes=int(intent["duration_minutes"]))

        timezone = intent.get("timezone") or DEFAULT_TIMEZONE
        relative_delta = intent.get("relative_delta_minutes")
        if relative_delta is not None:
            delta = timedelta(minutes=int(relative_delta))
            new_start = current_start + delta
            new_end = current_end + delta if current_end is not None else new_start + duration
        else:
            new_date = intent.get("new_date") or current_start.date().isoformat()
            new_start = datetime.fromisoformat(f"{new_date}T{intent['new_start_time']}:00")
            new_start = new_start.replace(tzinfo=ZoneInfo(timezone))
            new_end = new_start + duration

        updated = deepcopy(event)
        updated["start"] = {
            "dateTime": new_start.isoformat(),
            "timeZone": timezone,
        }
        updated["end"] = {
            "dateTime": new_end.isoformat(),
            "timeZone": timezone,
        }
        return updated

    def _update_confirmed_event(
        self,
        event: dict[str, Any],
        intent: dict[str, Any],
    ) -> str:
        event_id = event.get("id")
        if not event_id:
            message = "Matching event has no Google Calendar event id, so I did not move it."
            print(message)
            return message

        updated = self._build_updated_event(event, intent)
        response = self.tools.update_calendar_event(
            event_id=event_id,
            title=updated.get("summary") or "Untitled event",
            start_time=updated["start"]["dateTime"],
            end_time=updated["end"]["dateTime"],
            timezone=updated["start"].get("timeZone") or DEFAULT_TIMEZONE,
            description=updated.get("description"),
            location=updated.get("location"),
        )
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message

        message = f"Moved calendar event: {_format_event_choice(updated)}."
        print(message)
        return message

    def _delete_confirmed_event(self, event: dict[str, Any]) -> str:
        event_id = event.get("id")
        if not event_id:
            message = "Matching event has no Google Calendar event id, so I did not delete it."
            print(message)
            return message

        delete_response = self.tools.delete_calendar_event(event_id)
        if delete_response["status"] != "ok":
            message = delete_response["message"]
            print(message)
            return message

        message = f"Deleted calendar event: {_format_event_choice(event)}."
        print(message)
        return message

    def handle_pending_response(self, response: str) -> bool:
        command = response.strip().lower()
        if self.pending_action is None:
            return False

        pending = self.pending_action
        if command in ("no", "n", "cancel"):
            self.pending_action = None
            print("Okay, I did not delete anything.")
            return True

        if pending.choices is not None and command.isdigit():
            index = int(command) - 1
            if index < 0 or index >= len(pending.choices):
                print("Please choose one of the listed numbers, or say no.")
                return True

            event = pending.choices[index]
            self.pending_action = PendingAction(
                action=pending.action,
                event=event,
                payload=pending.payload,
            )
            if pending.action == "update":
                moved = self._build_updated_event(event, pending.payload or {})
                print(
                    f"I found this event: {_format_confirmation_event(event)}. "
                    f"Move it to {_format_confirmation_event(moved)}? yes/no"
                )
            else:
                print(
                    f"I found this event: {_format_confirmation_event(event)}. "
                    "Delete it? yes/no"
                )
            return True

        if command in ("yes", "y"):
            if pending.event is None:
                print("Please choose one of the listed numbers first, or say no.")
                return True

            self.pending_action = None
            if pending.action == "update":
                self._update_confirmed_event(pending.event, pending.payload or {})
            else:
                self._delete_confirmed_event(pending.event)
            return True

        print("Please answer yes or no.")
        return True


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
        if hasattr(active_claw, "handle_pending_response") and active_claw.handle_pending_response(command):
            continue

        intent = extract_intent(command)
        if intent["intent"] == "list_events":
            active_claw.list_events_from_request(command)
        elif intent["intent"] == "delete_event":
            active_claw.delete_event_from_request(command)
        elif intent["intent"] == "update_event":
            active_claw.update_event_from_request(command)
        else:
            active_claw.create_event_from_request(command)


if __name__ == "__main__":
    run_cli()

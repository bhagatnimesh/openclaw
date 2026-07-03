from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from datetime import datetime, timedelta
import re
from typing import Any
from zoneinfo import ZoneInfo

from intent import (
    METADATA_MARKER,
    extract_intent,
    read_metadata_from_description,
    write_metadata_to_description,
)
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

BUSY_DAY_EVENT_COUNT = 3
MAX_BRIEFING_CLARIFICATIONS = 5
PREP_RELEVANT_CATEGORIES = {"travel", "medical", "school", "shopping"}
CHILD_NAMES = {"nysha", "navya", "kids", "children"}


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


def _event_matches_metadata_filter(
    event: dict[str, Any],
    metadata_filter: dict[str, Any],
) -> bool:
    if not metadata_filter:
        return True

    _, metadata = read_metadata_from_description(event.get("description"))
    owner = metadata_filter.get("owner")
    if owner is not None:
        event_owner = metadata.get("owner")
        if event_owner not in (owner, "both"):
            return False

    preparation_needed = metadata_filter.get("preparation_needed")
    if preparation_needed is not None and metadata.get("preparation_needed") != preparation_needed:
        return False

    person = metadata_filter.get("person")
    if person is not None and metadata.get("person") != person:
        text_query = metadata_filter.get("text_query")
        if text_query is None:
            return False
        if _normalize_match_text(str(text_query)) not in _event_match_text(event):
            return False

    return True


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


def _event_end(event: dict[str, Any]) -> datetime:
    end = event.get("end", {})
    parsed = _parse_event_time(end.get("dateTime"))
    if parsed is not None:
        return parsed

    parsed_date = _parse_event_time(end.get("date"))
    return parsed_date or datetime.max


def _has_metadata(description: str | None) -> bool:
    return bool(description and METADATA_MARKER in description)


def _owner_label(owner: str) -> str:
    labels = {
        "dad": "dad",
        "mom": "mom",
        "both": "both",
        "unknown": "unassigned",
    }
    return labels.get(owner, "unassigned")


def _format_briefing_event(event: dict[str, Any]) -> str:
    title = event.get("summary") or "Untitled event"
    start_label = _format_event_time(event.get("start", {}))
    end_label = _format_event_time(event.get("end", {}))
    _, metadata = read_metadata_from_description(event.get("description"))
    owner = _owner_label(str(metadata.get("owner") or "unknown"))
    person = metadata.get("person")
    person_suffix = f", {person}" if person and person != "family" else ""
    prep_suffix = " prep needed" if metadata.get("preparation_needed") else ""
    return f"{start_label}-{end_label} {title} ({owner}{person_suffix}{prep_suffix})"


def _briefing_event_key(event: dict[str, Any]) -> tuple[str, str, str, str]:
    title = event.get("summary") or "Untitled event"
    start = _event_start(event)
    day_label = start.strftime("%Y-%m-%d") if start != datetime.max else "unscheduled"
    start_label = _format_event_time(event.get("start", {}))
    end_label = _format_event_time(event.get("end", {}))
    return (_normalize_match_text(title), day_label, start_label, end_label)


def _briefing_category(event: dict[str, Any], metadata: dict[str, Any]) -> str:
    category = str(metadata.get("category") or "")
    if category:
        return category

    text = _event_match_text(event)
    hints = {
        "travel": ("flight", "airport", "passport", "visa", "trip", "travel"),
        "medical": ("doctor", "dentist", "dental", "medical", "therapy"),
        "school": ("school", "class", "teacher", "homework", "pickup"),
        "shopping": ("shopping", "shop", "buy", "store", "grocery"),
        "activity": ("gymnastics", "soccer", "piano", "practice", "game"),
        "social": ("dinner", "party", "birthday", "playdate", "meet"),
        "household": ("trash", "repair", "clean", "house", "home"),
    }
    for inferred, words in hints.items():
        if any(re.search(rf"\b{re.escape(word)}s?\b", text) for word in words):
            return inferred

    return ""


def _is_child_related(event: dict[str, Any], metadata: dict[str, Any]) -> bool:
    person = str(metadata.get("person") or "").lower()
    if person in CHILD_NAMES:
        return True

    text = _event_match_text(event)
    return any(re.search(rf"\b{re.escape(name)}\b", text) for name in CHILD_NAMES)


def _clarification_score(
    event: dict[str, Any],
    metadata: dict[str, Any],
) -> int:
    score = 0
    if metadata.get("preparation_needed") and metadata.get("owner") == "unknown":
        score += 100
    if _is_child_related(event, metadata):
        score += 30
    if _briefing_category(event, metadata) in PREP_RELEVANT_CATEGORIES:
        score += 20
    if metadata.get("owner") == "unknown":
        score += 10
    return score


def _briefing_summary_sentence(
    label: str,
    event_count: int,
    busiest_day: tuple[str, int] | None,
    prep_count: int,
    conflict_count: int,
) -> str:
    display_label = label[:1].upper() + label[1:]
    event_word = "event" if event_count == 1 else "events"
    conflict_word = "conflict" if conflict_count == 1 else "conflicts"
    prep_word = "item" if prep_count == 1 else "items"
    if busiest_day is None:
        busiest = "No day is busy."
    else:
        busy_word = "event" if busiest_day[1] == 1 else "events"
        busiest = f"{busiest_day[0].split(',')[0]} is busiest with {busiest_day[1]} {busy_word}."
    return (
        f"{display_label} has {event_count} {event_word}. {busiest} "
        f"There {'is' if conflict_count == 1 else 'are'} {conflict_count} "
        f"{conflict_word} and {prep_count} prep-needed {prep_word}."
    )


def _format_briefing(
    events: list[dict[str, Any]],
    label: str,
) -> str:
    if not events:
        return f"Family calendar briefing for {label}:\nNo calendar events found."

    sorted_events = sorted(events, key=_event_start)
    events_by_day: dict[str, list[dict[str, Any]]] = {}
    for event in sorted_events:
        start = _event_start(event)
        day_label = start.strftime("%A, %B %-d") if start != datetime.max else "Unscheduled"
        events_by_day.setdefault(day_label, []).append(event)

    prep_events = []
    unassigned_events: dict[tuple[str, str, str, str], str] = {}
    clarify_candidates: list[tuple[int, str, str]] = []
    for event in sorted_events:
        title = event.get("summary") or "Untitled event"
        description = event.get("description")
        _, metadata = read_metadata_from_description(description)
        category = _briefing_category(event, metadata)
        if metadata.get("preparation_needed"):
            prep_note = metadata.get("preparation_notes") or "needs preparation"
            prep_events.append(f"- {title}: {prep_note}")
        if metadata.get("owner") == "unknown":
            unassigned_events.setdefault(_briefing_event_key(event), f"- {title}")
            owner_score = _clarification_score(event, metadata)
            clarify_candidates.append((owner_score, f"owner:{title}", f"- Who owns {title}?"))
        if not _has_metadata(description) and category in PREP_RELEVANT_CATEGORIES:
            clarify_candidates.append(
                (
                    _clarification_score(event, metadata) - 1,
                    f"prep:{title}",
                    f"- Does {title} need preparation?",
                )
            )
        elif metadata.get("preparation_needed") and not metadata.get("preparation_notes"):
            clarify_candidates.append(
                (
                    _clarification_score(event, metadata) + 20,
                    f"prep-notes:{title}",
                    f"- What preparation is needed for {title}?",
                )
            )

    conflicts = []
    conflict_questions = []
    busy_days = []
    for day_label, day_events in events_by_day.items():
        if len(day_events) >= BUSY_DAY_EVENT_COUNT:
            busy_days.append(f"- {day_label}: {len(day_events)} events")
        ordered = sorted(day_events, key=_event_start)
        for previous, current in zip(ordered, ordered[1:]):
            previous_end = _event_end(previous)
            current_start = _event_start(current)
            if previous_end != datetime.max and current_start < previous_end:
                previous_title = previous.get("summary") or "Untitled event"
                current_title = current.get("summary") or "Untitled event"
                conflicts.append(f"- {day_label}: {previous_title} overlaps {current_title}")
                conflict_questions.append(
                    (
                        80,
                        f"conflict:{day_label}:{previous_title}:{current_title}",
                        f"- Can {previous_title} and {current_title} both be covered?",
                    )
                )

    clarify_candidates.extend(conflict_questions)
    seen_questions = set()
    clarify_items = []
    for _, key, question in sorted(
        clarify_candidates,
        key=lambda item: (-item[0], item[1]),
    ):
        if key in seen_questions:
            continue
        seen_questions.add(key)
        clarify_items.append(question)
        if len(clarify_items) == MAX_BRIEFING_CLARIFICATIONS:
            break

    busiest_day = None
    if events_by_day:
        busiest_day = max(events_by_day.items(), key=lambda item: len(item[1]))
        busiest_day = (busiest_day[0], len(busiest_day[1]))

    lines = [
        f"Family calendar briefing for {label}:",
        _briefing_summary_sentence(
            label,
            len(sorted_events),
            busiest_day,
            len(prep_events),
            len(conflicts),
        ),
        "Events by day:",
    ]

    for day_label, day_events in events_by_day.items():
        lines.append(f"- {day_label}:")
        for event in day_events:
            lines.append(f"  - {_format_briefing_event(event)}")

    lines.append("Preparation-needed events:")
    lines.extend(prep_events or ["- None"])

    lines.append("Unassigned events:")
    lines.extend(list(unassigned_events.values()) or ["- None"])

    lines.append("Potential conflicts or busy days:")
    risk_items = conflicts + busy_days
    lines.extend(risk_items or ["- None"])

    lines.append("Things to clarify:")
    lines.extend(clarify_items or ["- None"])

    return "\n".join(lines)


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
    event_link: str | None,
    event_id: str | None,
) -> str:
    if event_link:
        event_suffix = f" (open: {event_link})"
    else:
        event_suffix = f" (event id: {event_id})" if event_id else ""
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
            f"{timezone}{event_suffix}."
        )

    recurrence_suffix = ""
    if recurrence_label:
        recurrence_suffix = f", repeating {recurrence_label}"
    return (
        f"Created calendar event: {event_title} on "
        f"{start.strftime('%A, %B %-d')} from {start.strftime('%-I:%M %p')} "
        f"to {end.strftime('%-I:%M %p')} {timezone}{recurrence_suffix}{event_suffix}."
    )


def _format_missing_create_message(intent: dict[str, Any], missing: list[str]) -> str:
    if missing == ["time"] and intent.get("title") and intent.get("date"):
        timezone = intent.get("timezone") or DEFAULT_TIMEZONE
        event_date = datetime.fromisoformat(f"{intent['date']}T00:00:00")
        event_date = event_date.replace(tzinfo=ZoneInfo(timezone))
        return (
            f"Please provide a time for {intent['title']} on "
            f"{event_date.strftime('%A, %B %-d')}."
        )

    return "Please provide: " + ", ".join(missing) + "."


def _merge_create_intent(
    pending: dict[str, Any],
    followup: dict[str, Any],
) -> dict[str, Any]:
    merged = deepcopy(pending)
    for field in ("title", "date", "start_time", "location", "description"):
        if not merged.get(field) and followup.get(field):
            merged[field] = followup[field]
    if followup.get("duration_minutes") and not merged.get("duration_minutes"):
        merged["duration_minutes"] = followup["duration_minutes"]

    missing_fields = []
    if merged.get("title") is None:
        missing_fields.append("title")
    if merged.get("date") is None:
        missing_fields.append("date")
    if merged.get("start_time") is None:
        missing_fields.append("time")
    merged["missing_fields"] = missing_fields
    return merged


def _extract_preparation_followup(request: str) -> str | None:
    cleaned = request.strip().lstrip("> ").strip().strip(".")
    cleaned = re.sub(r"^(?:please\s+)?(?:also\s+)?add\s+", "", cleaned, flags=re.IGNORECASE)
    if not re.search(r"\b(?:carry|bring|pack|prepare|need|snacks?|documents?)\b", cleaned, re.IGNORECASE):
        return None

    return cleaned[:1].lower() + cleaned[1:] if cleaned else None


def _append_preparation_note(existing_notes: str, preparation_note: str) -> str:
    line = f"Preparation: {preparation_note}"
    if not existing_notes:
        return line
    if line.lower() in existing_notes.lower():
        return existing_notes
    return f"{existing_notes}\n{line}"


def _extract_context_followup(request: str) -> str | None:
    cleaned = request.strip().lstrip("> ").strip().strip(".")
    patterns = (
        r"^(?:one\s+more\s+thing|also|another\s+thing)[:,]?\s+",
        r"^(?:please\s+)?(?:add|capture|remember)\s+(?:a\s+)?(?:note|context)\s+",
        r"^(?:note|context|fyi|remember)[:,]?\s+",
    )
    for pattern in patterns:
        match = re.match(pattern, cleaned, flags=re.IGNORECASE)
        if match is None:
            continue

        note = cleaned[match.end() :].strip()
        return note[:1].upper() + note[1:] if note else None

    return None


def _is_likely_recent_event_context(request: str) -> bool:
    cleaned = request.strip().lstrip("> ").strip().strip(".")
    lowered = cleaned.lower()
    if re.search(
        r"\b(?:date|time|when|what|where|who|which|cancel|delete|remove|move|reschedule|change)\b",
        lowered,
    ):
        return False

    if re.search(
        r"\b(?:also|another|note|context|remember|fyi|add|include|kids?|children|nysha|navya|"
        r"needs?|should|hungry|snacks?|art class|school)\b",
        lowered,
    ):
        return True

    return False


def _extract_recent_event_context_followup(request: str) -> str | None:
    explicit = _extract_context_followup(request)
    if explicit is not None:
        return explicit
    if not _is_likely_recent_event_context(request):
        return None

    cleaned = request.strip().lstrip("> ").strip().strip(".")
    cleaned = re.sub(r"^(?:please\s+)?(?:add|include)\s+", "", cleaned, flags=re.IGNORECASE)
    return cleaned[:1].upper() + cleaned[1:] if cleaned else None


def _append_context_note(existing_notes: str, context_note: str) -> str:
    line = f"Note: {context_note}"
    if not existing_notes:
        return line
    if line.lower() in existing_notes.lower():
        return existing_notes
    return f"{existing_notes}\n{line}"


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
    last_created_event: dict[str, Any] | None = None

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
            preparation_note = _extract_preparation_followup(request)
            if preparation_note is not None and self.last_created_event is not None:
                message = self._add_preparation_to_event(
                    self.last_created_event,
                    preparation_note,
                )
                print(message)
                return message

            context_note = _extract_recent_event_context_followup(request)
            if context_note is not None and self.last_created_event is not None:
                message = self._add_context_to_event(
                    self.last_created_event,
                    context_note,
                )
                print(message)
                return message

            self.pending_action = PendingAction(action="create", payload=intent)
            message = _format_missing_create_message(intent, missing)
            print(message)
            return message

        return self._create_event_from_intent(intent)

    def _create_event_from_intent(
        self,
        intent: dict[str, Any],
    ) -> str:
        timezone = intent.get("timezone") or DEFAULT_TIMEZONE
        start = datetime.fromisoformat(f"{intent['date']}T{intent['start_time']}:00")
        start = start.replace(tzinfo=ZoneInfo(timezone))
        end = start + timedelta(minutes=int(intent["duration_minutes"]))
        response = self.tools.create_calendar_event(
            title=intent["title"],
            start_time=start.isoformat(),
            end_time=end.isoformat(),
            timezone=timezone,
            description=write_metadata_to_description(
                intent.get("description"),
                intent.get("metadata"),
            ),
            location=intent.get("location"),
            recurrence=intent.get("recurrence"),
        )
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message

        event = response.get("data", {}).get("event", {})
        self.last_created_event = event
        event_title = event.get("summary", intent["title"])
        event_link = event.get("htmlLink")
        event_id = event.get("id")
        message = _format_created_event_message(
            event_title=event_title,
            start=start,
            end=end,
            timezone=timezone,
            recurrence=intent.get("recurrence"),
            recurrence_label=intent.get("recurrence_label"),
            event_link=event_link,
            event_id=event_id,
        )
        print(message)
        return message

    def _add_preparation_to_event(
        self,
        event: dict[str, Any],
        preparation_note: str,
    ) -> str:
        event_id = event.get("id")
        start = event.get("start", {})
        end = event.get("end", {})
        start_time = start.get("dateTime")
        end_time = end.get("dateTime")
        if not event_id or not start_time or not end_time:
            return "I could not update the previous event because it is missing Google Calendar details."

        notes, metadata = read_metadata_from_description(event.get("description"))
        metadata["preparation_needed"] = True
        metadata["preparation_notes"] = preparation_note
        description = write_metadata_to_description(
            _append_preparation_note(notes, preparation_note),
            metadata,
        )
        response = self.tools.update_calendar_event(
            event_id=event_id,
            title=event.get("summary") or "Untitled event",
            start_time=start_time,
            end_time=end_time,
            timezone=start.get("timeZone") or DEFAULT_TIMEZONE,
            description=description,
            location=event.get("location"),
        )
        if response["status"] != "ok":
            return response["message"]

        updated = deepcopy(event)
        updated.update(response.get("data", {}).get("event", {}))
        updated["description"] = description
        self.last_created_event = updated
        return f"Added preparation notes to {updated.get('summary') or 'the previous event'}."

    def _add_context_to_event(
        self,
        event: dict[str, Any],
        context_note: str,
    ) -> str:
        event_id = event.get("id")
        start = event.get("start", {})
        end = event.get("end", {})
        start_time = start.get("dateTime")
        end_time = end.get("dateTime")
        if not event_id or not start_time or not end_time:
            return "I could not update the previous event because it is missing Google Calendar details."

        notes, metadata = read_metadata_from_description(event.get("description"))
        description = write_metadata_to_description(
            _append_context_note(notes, context_note),
            metadata,
        )
        response = self.tools.update_calendar_event(
            event_id=event_id,
            title=event.get("summary") or "Untitled event",
            start_time=start_time,
            end_time=end_time,
            timezone=start.get("timeZone") or DEFAULT_TIMEZONE,
            description=description,
            location=event.get("location"),
        )
        if response["status"] != "ok":
            return response["message"]

        updated = deepcopy(event)
        updated.update(response.get("data", {}).get("event", {}))
        updated["description"] = description
        self.last_created_event = updated
        return f"Added note to {updated.get('summary') or 'the previous event'}."

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
            (
                event
                for event in response.get("data", {}).get("events", [])
                if _event_matches_metadata_filter(
                    event,
                    intent.get("metadata_filter", {}),
                )
            ),
            key=_event_start,
        )
        if not events:
            message = "No calendar events found for that time."
            print(message)
            return message

        lines = ["Calendar events:"]
        metadata_filter = intent.get("metadata_filter", {})
        for event in events:
            title = event.get("summary") or "Untitled event"
            start_part = event.get("start", {})
            end_part = event.get("end", {})
            start_label = _format_event_time(start_part)
            end_label = _format_event_time(end_part)
            location = event.get("location")
            location_suffix = f" at {location}" if location else ""
            metadata_suffix = ""
            if metadata_filter.get("preparation_needed") is True:
                _, metadata = read_metadata_from_description(event.get("description"))
                preparation_notes = metadata.get("preparation_notes")
                if preparation_notes:
                    metadata_suffix = f" (prep: {preparation_notes})"
            lines.append(
                f"- {title}: {start_label} to {end_label}{location_suffix}{metadata_suffix}"
            )

        message = "\n".join(lines)
        print(message)
        return message

    def briefing_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> str:
        intent = extract_intent(request, now=reference_time)
        response = self.tools.list_calendar_events(
            time_min=intent["start"],
            time_max=intent["end"],
            max_results=100,
        )
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message

        message = _format_briefing(
            response.get("data", {}).get("events", []),
            intent.get("label", "this week"),
        )
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
            if pending.action == "create":
                self.pending_action = None
                print("Okay, I did not create anything.")
                return True
            self.pending_action = None
            print("Okay, I did not delete anything.")
            return True

        if pending.action == "create":
            followup = extract_intent(response)
            merged = _merge_create_intent(pending.payload or {}, followup)
            missing = merged.get("missing_fields", [])
            if missing:
                self.pending_action = PendingAction(action="create", payload=merged)
                print(_format_missing_create_message(merged, missing))
                return True

            self.pending_action = None
            self._create_event_from_intent(merged)
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
        if intent["intent"] == "family_briefing":
            active_claw.briefing_from_request(command)
        elif intent["intent"] == "list_events":
            active_claw.list_events_from_request(command)
        elif intent["intent"] == "delete_event":
            active_claw.delete_event_from_request(command)
        elif intent["intent"] == "update_event":
            active_claw.update_event_from_request(command)
        else:
            active_claw.create_event_from_request(command)


if __name__ == "__main__":
    run_cli()

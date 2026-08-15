from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from datetime import date, datetime, timedelta
import re
from typing import Any
from zoneinfo import ZoneInfo

from intent import (
    METADATA_EXTENDED_PROPERTY,
    METADATA_MARKER,
    OWNER_ALIAS_PATTERN,
    OWNER_NAME_ALIASES,
    _strip_calendar_target_phrase,
    extract_intent,
    merge_ai_calendar_fields,
    read_metadata_from_event,
    write_human_description,
    write_metadata_to_private_extended_properties,
)
from prompts import SYSTEM_PROMPT, TOOL_GUIDANCE
from tools import DEFAULT_TIMEZONE, CalendarProvider, CalendarTools, build_default_tools

try:
    from ai_field_extraction import CalendarAIFieldExtractor
except ImportError:
    CalendarAIFieldExtractor = None


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
CHECKLIST_PREP_CATEGORIES = {
    "activity",
    "birthday",
    "social",
    "travel",
    "medical",
    "school",
    "paperwork",
    "appointment",
}
CHILD_NAMES = {"nysha", "navya", "kids", "children"}
ASSIGN_OWNER_RE = re.compile(
    rf"^\s*(?:assign|set|make|change|update|put)\s+"
    rf"(?P<target>.+?)\s+"
    rf"(?:to|for|owner\s+to|owner\s+as|as\s+owner)\s+"
    rf"(?P<owner>{OWNER_ALIAS_PATTERN})\b\.?\s*$",
    re.IGNORECASE,
)
OWNER_ONLY_RE = re.compile(
    rf"\b(?:owner|owned\s+by|assign(?:ed)?\s+to|belongs\s+to|for)\s*"
    rf"(?:is|:|to|as)?\s*(?P<owner>{OWNER_ALIAS_PATTERN})\b",
    re.IGNORECASE,
)
OWNER_AS_RE = re.compile(
    rf"\b(?P<owner>{OWNER_ALIAS_PATTERN})\s+as\s+(?:the\s+)?owner\b",
    re.IGNORECASE,
)
PRONOUN_TARGETS = {
    "it",
    "this",
    "that",
    "this event",
    "that event",
    "the event",
    "calendar event",
}
AI_FIELD_EXTRACTION_CUE_RE = re.compile(
    r"\b(?:invite|guest|guests?|calendar|attendee|attendees?)\b",
    re.IGNORECASE,
)


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


def _preparation_match_text(event: dict[str, Any]) -> str:
    notes, metadata = read_metadata_from_event(event)
    metadata_text = " ".join(
        str(value)
        for key, value in metadata.items()
        if key in ("category", "preparation_notes", "person") and value
    )
    return _normalize_match_text(
        " ".join(
            str(part)
            for part in (
                event.get("summary"),
                notes,
                metadata_text,
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

    _, metadata = read_metadata_from_event(event)
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
        if (
            metadata_filter.get("text_query") is None
            and metadata_filter.get("text_any_queries") is None
        ):
            return False

    text_query = metadata_filter.get("text_query")
    if text_query is not None:
        query_tokens = _tokens(str(text_query))
        event_tokens = _tokens(_event_match_text(event))
        if not query_tokens.issubset(event_tokens):
            return False

    text_any_queries = metadata_filter.get("text_any_queries")
    if text_any_queries is not None:
        event_tokens = _tokens(str(event.get("summary") or ""))
        any_match = any(
            _tokens(str(query)).issubset(event_tokens)
            for query in text_any_queries
        )
        if not any_match:
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


def _format_event_date(event: dict[str, Any]) -> str:
    start = _event_start(event)
    if start == datetime.max:
        return ""
    return start.strftime("%a, %b %-d")


def _has_metadata(event: dict[str, Any]) -> bool:
    description = event.get("description")
    if description and METADATA_MARKER in description:
        return True

    extended_properties = event.get("extendedProperties")
    if not isinstance(extended_properties, dict):
        return False
    private_properties = extended_properties.get("private")
    return (
        isinstance(private_properties, dict)
        and METADATA_EXTENDED_PROPERTY in private_properties
    )


def _private_extended_properties_for_event(
    event: dict[str, Any],
) -> dict[str, str] | None:
    if not _has_metadata(event):
        return None

    _, metadata = read_metadata_from_event(event)
    return write_metadata_to_private_extended_properties(metadata)


def _event_attendees(event: dict[str, Any]) -> list[dict[str, Any]] | None:
    attendees = event.get("attendees")
    if not isinstance(attendees, list):
        return None

    valid = [
        dict(attendee)
        for attendee in attendees
        if isinstance(attendee, dict) and isinstance(attendee.get("email"), str)
    ]
    return valid or None


def _event_calendar_id(event: dict[str, Any]) -> str | None:
    calendar_id = event.get("calendarId")
    return calendar_id if isinstance(calendar_id, str) and calendar_id.strip() else None


def _event_context_for_ai(event: dict[str, Any] | None) -> dict[str, str]:
    if not event:
        return {}
    context = {}
    for source_key, target_key in (
        ("id", "event_id"),
        ("summary", "title"),
        ("calendarId", "calendar_id"),
    ):
        value = event.get(source_key)
        if isinstance(value, str) and value.strip():
            context[target_key] = value.strip()
    return context


def _intent_context_for_ai(intent: dict[str, Any]) -> dict[str, Any]:
    context = dict(intent)
    attendees = []
    for attendee in intent.get("attendees") or []:
        if not isinstance(attendee, dict):
            continue
        display_name = attendee.get("displayName")
        if isinstance(display_name, str) and display_name.strip():
            attendees.append({"displayName": display_name.strip()})
        else:
            attendees.append({"configured": True})
    if attendees:
        context["attendees"] = attendees
    else:
        context.pop("attendees", None)
    return context


def _merge_attendees(
    existing: list[dict[str, Any]] | None,
    additions: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    merged = []
    seen = set()
    for attendee in (existing or []) + (additions or []):
        email = str(attendee.get("email") or "").strip()
        if not email:
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(dict(attendee))
    return merged or None


def _owner_label(owner: str) -> str:
    labels = {
        "dad": "dad",
        "mom": "mom",
        "both": "both",
        "grandmom": "grandmom",
        "unknown": "unassigned",
    }
    return labels.get(owner, "unassigned")


def _owner_from_alias(value: str) -> str:
    return OWNER_NAME_ALIASES.get(" ".join(value.lower().split()), "unknown")


def _assignment_parts(request: str) -> tuple[str | None, str | None]:
    match = ASSIGN_OWNER_RE.search(request)
    if match is None:
        for pattern in (OWNER_ONLY_RE, OWNER_AS_RE):
            owner_match = pattern.search(request)
            if owner_match is not None:
                return _owner_from_alias(owner_match.group("owner")), None
        return None, None

    target = " ".join(match.group("target").lower().split())
    owner = _owner_from_alias(match.group("owner"))
    if target in PRONOUN_TARGETS:
        return owner, None

    cleaned_target = re.sub(
        r"\b(?:calendar|event|appointment)\b",
        "",
        match.group("target"),
        flags=re.IGNORECASE,
    ).strip(" .")
    return owner, cleaned_target or None


def _format_briefing_event(event: dict[str, Any]) -> str:
    title = event.get("summary") or "Untitled event"
    start_label = _format_event_time(event.get("start", {}))
    end_label = _format_event_time(event.get("end", {}))
    _, metadata = read_metadata_from_event(event)
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


def _preparation_categories(
    event: dict[str, Any],
    metadata: dict[str, Any],
) -> set[str]:
    categories = set()
    category = str(metadata.get("category") or "")
    if category and category != "social":
        categories.add(category)

    text = _preparation_match_text(event)
    hints = {
        "travel": ("flight", "airport", "passport", "visa", "trip", "travel"),
        "medical": ("doctor", "dentist", "dental", "medical", "therapy", "clinic"),
        "school": ("school", "class", "teacher", "homework", "pickup", "field trip"),
        "paperwork": ("paperwork", "document", "documents", "form", "forms", "renewal"),
        "activity": ("gymnastics", "soccer", "piano", "practice", "game", "gear"),
        "birthday": ("birthday", "party", "cake", "gift"),
        "social": ("party", "playdate", "rsvp"),
        "appointment": ("appointment", "appt", "reservation"),
    }
    for inferred, words in hints.items():
        if any(re.search(rf"\b{re.escape(word)}s?\b", text) for word in words):
            categories.add(inferred)

    return categories


def _is_shopping_preparation_event(event: dict[str, Any], metadata: dict[str, Any]) -> bool:
    category = str(metadata.get("category") or "")
    text = _preparation_match_text(event)
    return category == "shopping" or bool(
        re.search(r"\b(?:shopping|shop|buy|store|mall|tshirts?|shirts?)\b", text)
    )


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
        _, metadata = read_metadata_from_event(event)
        category = _briefing_category(event, metadata)
        if metadata.get("preparation_needed"):
            prep_note = metadata.get("preparation_notes") or "needs preparation"
            prep_events.append(f"- {title}: {prep_note}")
        if metadata.get("owner") == "unknown":
            unassigned_events.setdefault(_briefing_event_key(event), f"- {title}")
            owner_score = _clarification_score(event, metadata)
            clarify_candidates.append((owner_score, f"owner:{title}", f"- Who owns {title}?"))
        if not _has_metadata(event) and category in PREP_RELEVANT_CATEGORIES:
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
    all_day: bool = False,
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
    if all_day:
        return (
            f"Created calendar event: {event_title} on "
            f"{start.strftime('%A, %B %-d')} all day"
            f"{recurrence_suffix}{event_suffix}."
        )
    return (
        f"Created calendar event: {event_title} on "
        f"{start.strftime('%A, %B %-d')} from {start.strftime('%-I:%M %p')} "
        f"to {end.strftime('%-I:%M %p')} {timezone}{recurrence_suffix}{event_suffix}."
    )


def _format_missing_create_message(intent: dict[str, Any], missing: list[str]) -> str:
    if "guest_contacts" in missing:
        missing_contacts = intent.get("missing_guest_contacts") or []
        if missing_contacts:
            labels = ", ".join(str(contact) for contact in missing_contacts)
            return f"Please configure calendar guest email contacts for: {labels}."
        return "Please configure calendar guest email contacts before adding guests."

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
    merged["attendees"] = _merge_attendees(
        merged.get("attendees"),
        followup.get("attendees"),
    )
    missing_guest_contacts = []
    seen_guest_contacts = set()
    for contact in (merged.get("missing_guest_contacts") or []) + (
        followup.get("missing_guest_contacts") or []
    ):
        contact_key = str(contact).strip()
        if not contact_key or contact_key in seen_guest_contacts:
            continue
        seen_guest_contacts.add(contact_key)
        missing_guest_contacts.append(contact_key)
    merged["missing_guest_contacts"] = missing_guest_contacts
    if not merged.get("target_calendar") and followup.get("target_calendar"):
        merged["target_calendar"] = followup["target_calendar"]
    if followup.get("all_day"):
        merged["all_day"] = True
        merged.pop("start_time", None)

    missing_fields = []
    if merged.get("title") is None:
        missing_fields.append("title")
    if merged.get("date") is None:
        missing_fields.append("date")
    if not merged.get("all_day") and merged.get("start_time") is None:
        missing_fields.append("time")
    if merged.get("missing_guest_contacts"):
        missing_fields.append("guest_contacts")
    merged["missing_fields"] = missing_fields
    return merged


BULK_DATE_LINE_RE = re.compile(
    r"^\s*(?P<month>\d{1,2})/(?P<day>\d{1,2})(?:/(?P<year>\d{2,4}))?\s*$",
)


def _parse_bulk_date_line(
    line: str,
    reference: datetime,
    previous: date | None,
) -> date | None:
    match = BULK_DATE_LINE_RE.match(line)
    if match is None:
        return None

    month = int(match.group("month"))
    day = int(match.group("day"))
    year_text = match.group("year")
    if year_text is None:
        year = reference.year
    elif len(year_text) == 2:
        year = 2000 + int(year_text)
    else:
        year = int(year_text)

    try:
        parsed = date(year, month, day)
    except ValueError:
        return None

    if year_text is not None:
        return parsed

    while parsed < reference.date() or (previous is not None and parsed <= previous):
        try:
            parsed = date(parsed.year + 1, month, day)
        except ValueError:
            return None

    return parsed


def _bulk_base_request(lines: list[str]) -> str:
    header = " ".join(line for line in lines if BULK_DATE_LINE_RE.match(line) is None)
    header = re.sub(
        r"\bfor\s+(?:the\s+)?(?:below|following)\s+(?:days?|dates?|data)\b",
        " ",
        header,
        flags=re.IGNORECASE,
    )
    header = re.sub(
        r"\bon\s+(?:the\s+)?(?:below|following)\s+(?:days?|dates?|data)\b",
        " ",
        header,
        flags=re.IGNORECASE,
    )
    return " ".join(header.split())


def _bulk_date_labels(dates: list[str]) -> str:
    labels = []
    for value in dates:
        parsed = datetime.fromisoformat(f"{value}T00:00:00")
        labels.append(parsed.strftime("%b %-d"))
    return ", ".join(labels)


def _extract_bulk_create_intent(
    request: str,
    reference_time: datetime | None,
) -> dict[str, Any] | None:
    lines = [line.strip() for line in request.splitlines() if line.strip()]
    if len(lines) < 3:
        return None

    reference = reference_time
    if reference is None:
        reference = datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
    elif reference.tzinfo is None:
        reference = reference.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

    parsed_dates: list[date] = []
    previous: date | None = None
    for line in lines:
        parsed = _parse_bulk_date_line(line, reference, previous)
        if parsed is None:
            continue
        parsed_dates.append(parsed)
        previous = parsed

    if len(parsed_dates) < 2:
        return None

    base_request = _bulk_base_request(lines)
    base_intent = extract_intent(base_request, now=reference)
    intents = []
    for parsed in parsed_dates:
        intent = deepcopy(base_intent)
        intent["date"] = parsed.isoformat()
        intent["missing_fields"] = [
            field
            for field in intent.get("missing_fields", [])
            if field != "date"
        ]
        intents.append(intent)

    missing_fields = []
    if any(intent.get("title") is None for intent in intents):
        missing_fields.append("title")
    if any(intent.get("start_time") is None for intent in intents):
        missing_fields.append("time")

    return {
        "intent": "create_events",
        "intents": intents,
        "dates": [parsed.isoformat() for parsed in parsed_dates],
        "missing_fields": missing_fields,
    }


def _merge_bulk_create_intent(
    pending: dict[str, Any],
    followup: dict[str, Any],
) -> dict[str, Any]:
    merged = deepcopy(pending)
    intents = []
    for intent in merged.get("intents", []):
        updated = _merge_create_intent(intent, followup)
        intents.append(updated)
    merged["intents"] = intents
    missing = {
        field
        for intent in intents
        for field in intent.get("missing_fields", [])
        if field in {"title", "time"}
    }
    merged["missing_fields"] = [field for field in ("title", "time") if field in missing]
    return merged


def _format_missing_bulk_create_message(intent: dict[str, Any]) -> str:
    missing = intent.get("missing_fields", [])
    if missing == ["time"]:
        first = next((item for item in intent.get("intents", []) if item.get("title")), {})
        title = first.get("title") or "these events"
        return (
            f"Please provide a time for {title} on "
            f"{len(intent.get('dates', []))} dates: {_bulk_date_labels(intent.get('dates', []))}."
        )

    return "Please provide: " + ", ".join(missing) + "."


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
    if _has_telegram_image_text(request):
        return None
    if not _is_likely_recent_event_context(request):
        return None

    cleaned = request.strip().lstrip("> ").strip().strip(".")
    cleaned = re.sub(r"^(?:please\s+)?(?:add|include)\s+", "", cleaned, flags=re.IGNORECASE)
    return cleaned[:1].upper() + cleaned[1:] if cleaned else None


def _has_telegram_image_text(request: str) -> bool:
    return bool(re.search(r"(?im)^\s*Image text:\s*$", request))


def _append_context_note(existing_notes: str, context_note: str) -> str:
    line = f"Note: {context_note}"
    if not existing_notes:
        return line
    if line.lower() in existing_notes.lower():
        return existing_notes
    return f"{existing_notes}\n{line}"


def _format_preparation_header(event: dict[str, Any]) -> str:
    title = event.get("summary") or "Untitled event"
    start = _event_start(event)
    if start == datetime.max:
        return f"{title} — unscheduled"

    start_part = event.get("start", {})
    if start_part.get("dateTime"):
        return f"{title} — {start.strftime('%A %-I:%M %p')}"

    return f"{title} — {start.strftime('%A')} all day"


def _format_preparation_note(preparation_notes: str) -> str | None:
    cleaned = preparation_notes.strip()
    if not cleaned:
        return None

    action = _note_action(cleaned)
    if action is not None:
        return action

    return cleaned[:1].upper() + cleaned[1:]


def _note_action(preparation_notes: str) -> str | None:
    notes = _normalize_match_text(preparation_notes)
    if not notes:
        return None
    if "document" in notes:
        return "Gather required documents"
    if "form" in notes:
        return "Complete required forms"
    if "snack" in notes:
        return "Pack snacks"
    if "gift" in notes:
        return "Buy or wrap gift"
    if "passport" in notes or "visa" in notes:
        return "Check passport and visa documents"
    if notes.startswith("need "):
        return f"Handle {preparation_notes[5:].strip()}"
    if notes.startswith("bring "):
        return f"Bring {preparation_notes[6:].strip()}"
    if notes.startswith("pack "):
        return f"Pack {preparation_notes[5:].strip()}"

    return preparation_notes[:1].upper() + preparation_notes[1:]


def _category_actions(categories: set[str], note_action: str | None) -> list[str]:
    actions = []
    has_explicit_documents = note_action == "Gather required documents"
    if "appointment" in categories:
        actions.append("Confirm appointment")
    if "travel" in categories and "appointment" not in categories:
        actions.extend(
            [
                "Pack essentials",
                "Check travel documents",
                "Confirm transport",
                "Check weather",
                "Pack medicines",
            ]
        )
    if "medical" in categories:
        actions.extend(
            [
                "Bring insurance card",
                "Complete forms",
                "Bring prior notes",
                "Confirm transport",
            ]
        )
    if "school" in categories:
        actions.extend(
            [
                "Complete school forms",
                "Pack costume or materials",
                "Confirm drop-off/pickup plan",
            ]
        )
    if "paperwork" in categories:
        if not has_explicit_documents:
            actions.append("Gather required documents")
        actions.extend(["Complete forms", "Prepare photos", "Confirm appointment"])
    if "activity" in categories:
        actions.extend(["Pack gear", "Set out clothes", "Confirm pickup/drop-off"])
    if "birthday" in categories:
        actions.extend(["Buy gift", "RSVP", "Plan food", "Confirm timing"])
    elif "social" in categories:
        actions.extend(["Confirm RSVP", "Plan food", "Confirm timing"])
    return actions


def _event_category_actions(
    event: dict[str, Any],
    metadata: dict[str, Any],
    categories: set[str],
    note_action: str | None,
) -> list[str]:
    if "travel" in categories and _is_shopping_preparation_event(event, metadata):
        actions = []
        if "shopping" not in _normalize_match_text(str(note_action)):
            actions.append("Make shopping list")
        actions.extend(["Confirm sizes and quantities", "Bring bags"])
        return actions

    return _category_actions(categories, note_action)


def _dedupe_actions(actions: list[str]) -> list[str]:
    deduped = []
    seen = set()
    for action in actions:
        key = _normalize_match_text(action)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(action)
    return deduped


def _preparation_checklist_for_event(event: dict[str, Any]) -> list[str]:
    notes, metadata = read_metadata_from_event(event)
    categories = _preparation_categories(event, metadata)
    actions = []
    preparation_notes = str(metadata.get("preparation_notes") or notes)
    note_item = _format_preparation_note(preparation_notes)
    if note_item is not None:
        actions.append(note_item)
    actions.extend(_event_category_actions(event, metadata, categories, _note_action(preparation_notes)))
    if metadata.get("owner") == "unknown":
        actions.append("Assign owner")
    return _dedupe_actions(actions)


def _preparation_reason(event: dict[str, Any]) -> str:
    notes, metadata = read_metadata_from_event(event)
    reasons = []
    if metadata.get("preparation_notes") or notes:
        reasons.append("prep notes")
    if metadata.get("preparation_needed"):
        reasons.append("marked prep-needed")

    categories = sorted(_preparation_categories(event, metadata) & CHECKLIST_PREP_CATEGORIES)
    if "travel" in categories and _is_shopping_preparation_event(event, metadata):
        categories.remove("travel")
        categories.append("shopping for travel")
    if categories:
        reasons.append(", ".join(categories))

    return "; ".join(reasons) if reasons else "likely prep-needed category"


def _format_suggested_deadline(event: dict[str, Any], now: datetime) -> str:
    start = _event_start(event)
    if start == datetime.max:
        return "Before the event"

    if start <= now + timedelta(hours=48):
        return "ASAP"

    deadline = start - timedelta(days=1)
    if event.get("start", {}).get("dateTime"):
        return f"By {deadline.strftime('%A %-I:%M %p')}"

    return f"By {deadline.strftime('%A')}"


def _is_urgent_preparation(event: dict[str, Any], now: datetime) -> bool:
    start = _event_start(event)
    return start != datetime.max and start <= now + timedelta(hours=48)


def _event_needs_preparation(event: dict[str, Any]) -> bool:
    _, metadata = read_metadata_from_event(event)
    if metadata.get("preparation_needed"):
        return True

    return bool(_preparation_categories(event, metadata) & CHECKLIST_PREP_CATEGORIES)


def _preparation_query_score(query: str | None, event: dict[str, Any]) -> int:
    if not query:
        return 0

    normalized_query = _normalize_match_text(query)
    query_tokens = set(normalized_query.split())
    text = _preparation_match_text(event)
    text_tokens = set(text.split())
    score = 0
    if normalized_query in text:
        score += 5
    score += 2 * len(query_tokens & text_tokens)
    return score


def _format_preparation_checklist(
    events: list[dict[str, Any]],
    query: str | None,
    label: str,
    now: datetime,
) -> str:
    ranked_events = []
    for event in events:
        query_score = _preparation_query_score(query, event)
        if query is not None and query_score == 0:
            continue
        if query is None and not _event_needs_preparation(event):
            continue
        if query is not None and not _event_needs_preparation(event):
            continue
        ranked_events.append((query_score, event))

    selected = [
        event
        for _, event in sorted(
            ranked_events,
            key=lambda item: (-item[0], _event_start(item[1])),
        )
    ]
    if not selected:
        target = f" for {query}" if query else f" {label}"
        return f"No preparation actions found{target}."

    lines = ["Preparation checklist:"]
    for event in selected:
        _, metadata = read_metadata_from_event(event)
        urgency = " (urgent)" if _is_urgent_preparation(event, now) else ""
        lines.append(f"{_format_preparation_header(event)}{urgency}")
        owner = str(metadata.get("owner") or "unknown")
        if owner != "unknown":
            lines.append(f"Owner: {_owner_label(owner)}")
        lines.append(f"Why: {_preparation_reason(event)}")
        lines.append(f"Suggested deadline: {_format_suggested_deadline(event, now)}")
        lines.append("Checklist:")
        actions = _preparation_checklist_for_event(event)
        lines.extend(f"- {action}" for action in actions)

    return "\n".join(lines)


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
    last_result: dict[str, Any] | None = None
    undo_stack: list[dict[str, Any]] = field(default_factory=list)
    field_extractor: Any | None = None

    @classmethod
    def from_provider(cls, provider: CalendarProvider) -> "FamilyCalendarClaw":
        return cls(tools=CalendarTools(provider))

    @classmethod
    def default(cls, calendar_id: str = "primary") -> "FamilyCalendarClaw":
        extractor = CalendarAIFieldExtractor.from_env_or_none() if CalendarAIFieldExtractor is not None else None
        return cls(tools=build_default_tools(calendar_id=calendar_id), field_extractor=extractor)

    def tool_map(self) -> dict[str, Any]:
        """Return the OpenClaw-visible tool names and their handlers."""

        return {
            "create_calendar_event": self.tools.create_calendar_event,
            "list_calendar_events": self.tools.list_calendar_events,
            "delete_calendar_event": self.tools.delete_calendar_event,
            "update_calendar_event": self.tools.update_calendar_event,
            "undo_calendar_action": self.undo_last_action,
        }

    def _should_extract_ai_fields(
        self,
        request: str,
        intent: dict[str, Any],
    ) -> bool:
        if self.field_extractor is None:
            return False
        if getattr(self.field_extractor, "primary_calendar_api_context", False):
            return True
        if intent.get("missing_fields"):
            return True
        if AI_FIELD_EXTRACTION_CUE_RE.search(request):
            return True
        return False

    def _extract_intent_from_request(
        self,
        request: str,
        reference_time: datetime | None,
    ) -> dict[str, Any]:
        intent = extract_intent(request, now=reference_time)
        if not self._should_extract_ai_fields(request, intent):
            return intent

        try:
            ai_fields = self.field_extractor.extract(
                request,
                now=reference_time,
                baseline_intent=_intent_context_for_ai(intent),
                context={
                    "last_created_event": _event_context_for_ai(self.last_created_event),
                },
            )
        except Exception:
            return intent
        return merge_ai_calendar_fields(
            intent,
            ai_fields,
            request,
            now=reference_time,
            primary=bool(getattr(self.field_extractor, "primary_calendar_api_context", False)),
        )

    def create_event_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
        *,
        event_id: str | None = None,
        calendar_id: str | None = None,
    ) -> str:
        """Parse one simple add-event request and create it through the tool.

        This is intentionally small for Milestone 1. It does not plan,
        categorize, remember preferences, or infer missing event details.
        """

        bulk_intent = _extract_bulk_create_intent(request, reference_time)
        if bulk_intent is not None:
            missing = bulk_intent.get("missing_fields", [])
            if missing:
                self.pending_action = PendingAction(action="create_bulk", payload=bulk_intent)
                message = _format_missing_bulk_create_message(bulk_intent)
                print(message)
                return message

            return self._create_events_from_intents(bulk_intent["intents"])

        intent = self._extract_intent_from_request(request, reference_time)
        if intent.get("intent") == "add_guests" and hasattr(self, "add_guests_from_request"):
            return self.add_guests_from_intent(
                intent,
                event_id=event_id,
                calendar_id=calendar_id,
            )
        missing = intent.get("missing_fields", [])
        if missing:
            preparation_note = _extract_preparation_followup(request)
            context_note = _extract_recent_event_context_followup(request)
            target_event = self.last_created_event
            if event_id and (preparation_note is not None or context_note is not None):
                response = self.tools.get_calendar_event(event_id, calendar_id=calendar_id)
                self.last_result = response
                if response["status"] != "ok":
                    message = response["message"]
                    print(message)
                    return message
                target_event = response.get("data", {}).get("event")

            if preparation_note is not None and target_event is not None:
                message = self._add_preparation_to_event(
                    target_event,
                    preparation_note,
                )
                print(message)
                return message

            if context_note is not None and target_event is not None:
                message = self._add_context_to_event(
                    target_event,
                    context_note,
                )
                print(message)
                return message

            self.pending_action = PendingAction(action="create", payload=intent)
            message = _format_missing_create_message(intent, missing)
            print(message)
            return message

        return self._create_event_from_intent(intent)

    def _create_events_from_intents(
        self,
        intents: list[dict[str, Any]],
    ) -> str:
        created_messages = []
        for intent in intents:
            created_messages.append(self._create_event_from_intent(intent, print_message=False))

        failures = [
            message
            for message in created_messages
            if not message.startswith("Created calendar event:")
        ]
        if failures:
            return "\n".join(failures)

        first_title = intents[0].get("title") or "calendar event"
        dates = [intent["date"] for intent in intents if intent.get("date")]
        message = (
            f"Created {len(created_messages)} calendar events for {first_title}: "
            f"{_bulk_date_labels(dates)}."
        )
        print(message)
        return message

    def _create_event_from_intent(
        self,
        intent: dict[str, Any],
        print_message: bool = True,
    ) -> str:
        title = _strip_calendar_target_phrase(str(intent["title"]))
        timezone = intent.get("timezone") or DEFAULT_TIMEZONE
        all_day = bool(intent.get("all_day"))
        if all_day:
            start = datetime.fromisoformat(f"{intent['date']}T00:00:00").replace(
                tzinfo=ZoneInfo(timezone),
            )
            end = start + timedelta(days=1)
            start_value = start.date().isoformat()
            end_value = end.date().isoformat()
        else:
            start = datetime.fromisoformat(f"{intent['date']}T{intent['start_time']}:00")
            start = start.replace(tzinfo=ZoneInfo(timezone))
            end = start + timedelta(minutes=int(intent["duration_minutes"]))
            start_value = start.isoformat()
            end_value = end.isoformat()
        response = self.tools.create_calendar_event(
            title=title or intent["title"],
            start_time=start_value,
            end_time=end_value,
            timezone=timezone,
            description=write_human_description(intent.get("description")),
            location=intent.get("location"),
            recurrence=intent.get("recurrence"),
            attendees=intent.get("attendees"),
            calendar_name=intent.get("target_calendar"),
            notify_attendees=bool(intent.get("attendees")),
            all_day=all_day,
            private_extended_properties=write_metadata_to_private_extended_properties(
                intent.get("metadata"),
            ),
        )
        self.last_result = response
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message

        event = response.get("data", {}).get("event", {})
        self.last_created_event = event
        if event.get("id"):
            self.undo_stack.append({"action": "delete_event", "event": deepcopy(event)})
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
            all_day=all_day,
        )
        if print_message:
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
            self.last_result = {"status": "error"}
            return "I could not update the previous event because it is missing Google Calendar details."

        notes, metadata = read_metadata_from_event(event)
        metadata["preparation_needed"] = True
        metadata["preparation_notes"] = preparation_note
        description = write_human_description(
            _append_preparation_note(notes, preparation_note),
        )
        response = self.tools.update_calendar_event(
            event_id=event_id,
            title=event.get("summary") or "Untitled event",
            start_time=start_time,
            end_time=end_time,
            timezone=start.get("timeZone") or DEFAULT_TIMEZONE,
            description=description,
            location=event.get("location"),
            attendees=_event_attendees(event),
            calendar_id=_event_calendar_id(event),
            private_extended_properties=write_metadata_to_private_extended_properties(
                metadata,
            ),
        )
        self.last_result = response
        if response["status"] != "ok":
            return response["message"]

        updated = deepcopy(event)
        updated.update(response.get("data", {}).get("event", {}))
        updated["description"] = description
        updated["extendedProperties"] = {
            "private": write_metadata_to_private_extended_properties(metadata),
        }
        self.last_created_event = updated
        self.undo_stack.append({"action": "restore_event", "event": deepcopy(event)})
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
            self.last_result = {"status": "error"}
            return "I could not update the previous event because it is missing Google Calendar details."

        notes, metadata = read_metadata_from_event(event)
        description = write_human_description(_append_context_note(notes, context_note))
        response = self.tools.update_calendar_event(
            event_id=event_id,
            title=event.get("summary") or "Untitled event",
            start_time=start_time,
            end_time=end_time,
            timezone=start.get("timeZone") or DEFAULT_TIMEZONE,
            description=description,
            location=event.get("location"),
            attendees=_event_attendees(event),
            calendar_id=_event_calendar_id(event),
            private_extended_properties=write_metadata_to_private_extended_properties(
                metadata,
            ),
        )
        self.last_result = response
        if response["status"] != "ok":
            return response["message"]

        updated = deepcopy(event)
        updated.update(response.get("data", {}).get("event", {}))
        updated["description"] = description
        updated["extendedProperties"] = {
            "private": write_metadata_to_private_extended_properties(metadata),
        }
        self.last_created_event = updated
        self.undo_stack.append({"action": "restore_event", "event": deepcopy(event)})
        return f"Added note to {updated.get('summary') or 'the previous event'}."

    def assign_owner_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
        *,
        event_id: str | None = None,
        calendar_id: str | None = None,
    ) -> str:
        owner, target = _assignment_parts(request)
        if owner is None or owner == "unknown":
            message = "Please say who should own the event."
            print(message)
            return message

        if event_id:
            response = self.tools.get_calendar_event(event_id, calendar_id=calendar_id)
            self.last_result = response
            if response["status"] != "ok":
                message = response["message"]
                print(message)
                return message
            message = self._assign_owner_to_event(
                response.get("data", {}).get("event", {}),
                owner,
            )
            print(message)
            return message

        if target is None:
            event = self.last_created_event
            if event is None:
                message = "I do not know which event to update."
                print(message)
                return message
            message = self._assign_owner_to_event(event, owner)
            print(message)
            return message

        reference = reference_time or datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
        response = self.tools.list_calendar_events(
            time_min=reference.isoformat(),
            time_max=(reference + timedelta(days=30)).isoformat(),
            max_results=50,
        )
        if response["status"] != "ok":
            self.last_result = response
            message = response["message"]
            print(message)
            return message

        events = sorted(response.get("data", {}).get("events", []), key=_event_start)
        matches = [
            item["event"]
            for item in (
                ranked for ranked in match_events(target, events)
                if ranked["score"] >= MIN_CONFIDENT_SCORE
            )
        ]
        if not matches:
            message = "I couldn't find a matching event. Try including the event name or date."
            print(message)
            return message
        if len(matches) > 1:
            lines = ["Multiple matching events found. Which one should I assign?"]
            for index, event in enumerate(matches, start=1):
                lines.append(f"{index}. {_format_event_choice(event)}")
            message = "\n".join(lines)
            print(message)
            return message

        message = self._assign_owner_to_event(matches[0], owner)
        print(message)
        return message

    def add_guests_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
        *,
        event_id: str | None = None,
        calendar_id: str | None = None,
    ) -> str:
        intent = self._extract_intent_from_request(request, reference_time)
        return self.add_guests_from_intent(
            intent,
            event_id=event_id,
            calendar_id=calendar_id,
        )

    def add_guests_from_intent(
        self,
        intent: dict[str, Any],
        *,
        event_id: str | None = None,
        calendar_id: str | None = None,
    ) -> str:
        attendees = intent.get("attendees") or []
        if "guest_contacts" in intent.get("missing_fields", []):
            message = _format_missing_create_message(
                intent,
                intent.get("missing_fields", []),
            )
            print(message)
            return message
        if not attendees:
            message = "Please say which guest to add: mom, dad, or family."
            print(message)
            return message

        if event_id:
            response = self.tools.get_calendar_event(event_id, calendar_id=calendar_id)
            self.last_result = response
            if response["status"] != "ok":
                message = response["message"]
                print(message)
                return message
            target_event = response.get("data", {}).get("event", {})
        else:
            target_event = self.last_created_event

        if target_event is None:
            message = "I do not know which event to update."
            print(message)
            return message

        message = self._add_guests_to_event(target_event, attendees)
        print(message)
        return message

    def _add_guests_to_event(
        self,
        event: dict[str, Any],
        attendees: list[dict[str, Any]],
    ) -> str:
        event_id = event.get("id")
        start = event.get("start", {})
        end = event.get("end", {})
        start_time = start.get("dateTime")
        end_time = end.get("dateTime")
        if not event_id or not start_time or not end_time:
            self.last_result = {"status": "error"}
            return "I could not update the event because it is missing Google Calendar details."

        merged_attendees = _merge_attendees(_event_attendees(event), attendees)
        response = self.tools.update_calendar_event(
            event_id=event_id,
            title=event.get("summary") or "Untitled event",
            start_time=start_time,
            end_time=end_time,
            timezone=start.get("timeZone") or DEFAULT_TIMEZONE,
            description=write_human_description(event.get("description")),
            location=event.get("location"),
            attendees=merged_attendees,
            calendar_id=_event_calendar_id(event),
            notify_attendees=True,
            private_extended_properties=_private_extended_properties_for_event(event),
        )
        self.last_result = response
        if response["status"] != "ok":
            return response["message"]

        updated = deepcopy(event)
        updated.update(response.get("data", {}).get("event", {}))
        updated["attendees"] = merged_attendees or []
        self.last_created_event = updated
        self.undo_stack.append({"action": "restore_event", "event": deepcopy(event)})
        names = ", ".join(str(attendee.get("displayName") or attendee["email"]) for attendee in attendees)
        return f"Added guest{'s' if len(attendees) != 1 else ''} to {updated.get('summary') or 'the event'}: {names}."

    def _contextual_event_from_intent(self, intent: dict[str, Any]) -> dict[str, Any] | None:
        query = str(intent.get("query") or "").strip().lower()
        if query in PRONOUN_TARGETS:
            return self.last_created_event
        return None

    def _assign_owner_to_event(self, event: dict[str, Any], owner: str) -> str:
        event_id = event.get("id")
        start = event.get("start", {})
        end = event.get("end", {})
        start_time = start.get("dateTime")
        end_time = end.get("dateTime")
        if not event_id or not start_time or not end_time:
            self.last_result = {"status": "error"}
            return "I could not update the event because it is missing Google Calendar details."

        notes, metadata = read_metadata_from_event(event)
        metadata["owner"] = owner
        description = write_human_description(notes)
        response = self.tools.update_calendar_event(
            event_id=event_id,
            title=event.get("summary") or "Untitled event",
            start_time=start_time,
            end_time=end_time,
            timezone=start.get("timeZone") or DEFAULT_TIMEZONE,
            description=description,
            location=event.get("location"),
            attendees=_event_attendees(event),
            calendar_id=_event_calendar_id(event),
            private_extended_properties=write_metadata_to_private_extended_properties(
                metadata,
            ),
        )
        self.last_result = response
        if response["status"] != "ok":
            return response["message"]

        updated = deepcopy(event)
        updated.update(response.get("data", {}).get("event", {}))
        updated["description"] = description
        updated["extendedProperties"] = {
            "private": write_metadata_to_private_extended_properties(metadata),
        }
        self.last_created_event = updated
        self.undo_stack.append({"action": "restore_event", "event": deepcopy(event)})
        return f"Assigned event to {owner}: {updated.get('summary') or 'the event'}."

    def list_events_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> str:
        """Parse one simple read request and print Google Calendar events."""

        intent = extract_intent(request, now=reference_time)
        missing = intent.get("missing_fields", [])
        if missing:
            self.last_result = {"status": "needs_information"}
            message = "Please provide: " + ", ".join(missing) + "."
            print(message)
            return message

        response = self.tools.list_calendar_events(
            time_min=intent["start"],
            time_max=intent["end"],
            max_results=50,
        )
        self.last_result = response
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
        range_start = _parse_event_time(intent.get("start"))
        range_end = _parse_event_time(intent.get("end"))
        include_dates = bool(metadata_filter)
        if (
            range_start is not None
            and range_end is not None
            and range_end - range_start > timedelta(days=1)
        ):
            include_dates = True
        for event in events:
            title = event.get("summary") or "Untitled event"
            start_part = event.get("start", {})
            end_part = event.get("end", {})
            start_label = _format_event_time(start_part)
            end_label = _format_event_time(end_part)
            location = event.get("location")
            location_suffix = f" at {location}" if location else ""
            date_prefix = f"{_format_event_date(event)}: " if include_dates else ""
            metadata_suffix = ""
            if metadata_filter.get("preparation_needed") is True:
                _, metadata = read_metadata_from_event(event)
                preparation_notes = metadata.get("preparation_notes")
                if preparation_notes:
                    metadata_suffix = f" (prep: {preparation_notes})"
            lines.append(
                f"- {date_prefix}{title}: {start_label} to {end_label}"
                f"{location_suffix}{metadata_suffix}"
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
        self.last_result = response
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

    def preparation_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> str:
        intent = extract_intent(request, now=reference_time)
        now = reference_time or datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
        if now.tzinfo is None:
            now = now.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        response = self.tools.list_calendar_events(
            time_min=intent["start"],
            time_max=intent["end"],
            max_results=100,
        )
        self.last_result = response
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message

        message = _format_preparation_checklist(
            response.get("data", {}).get("events", []),
            intent.get("query"),
            intent.get("label", "upcoming"),
            now,
        )
        print(message)
        return message

    def delete_event_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
        *,
        event_id: str | None = None,
        calendar_id: str | None = None,
    ) -> str:
        """Find a matching Google Calendar event and delete only one strong match."""

        self.last_result = {"status": "needs_information"}

        intent = extract_intent(request, now=reference_time)
        contextual_event = None if event_id else self._contextual_event_from_intent(intent)
        missing = [
            field
            for field in intent.get("missing_fields", [])
            if not ((event_id or contextual_event is not None) and field in {"event", "query", "target"})
        ]
        if missing:
            message = "Please provide: " + ", ".join(missing) + "."
            print(message)
            return message

        if event_id:
            response = self.tools.get_calendar_event(event_id, calendar_id=calendar_id)
        elif contextual_event is not None:
            response = {
                "status": "ok",
                "message": "Calendar event returned from context.",
                "data": {"event": contextual_event},
            }
        else:
            response = self.tools.list_calendar_events(
                time_min=intent["search_start"],
                time_max=intent["search_end"],
                max_results=50,
            )
        if response["status"] != "ok":
            self.last_result = response
            message = response["message"]
            print(message)
            return message

        events = (
            [response.get("data", {}).get("event", {})]
            if event_id or contextual_event is not None
            else sorted(response.get("data", {}).get("events", []), key=_event_start)
        )
        if event_id or contextual_event is not None:
            matches = [
                event
                for event in events
                if contextual_event is not None or str(event.get("id") or "") == event_id
            ]
        else:
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
        *,
        event_id: str | None = None,
        calendar_id: str | None = None,
    ) -> str:
        """Find a matching event and ask before moving it."""

        self.last_result = {"status": "needs_information"}

        intent = extract_intent(request, now=reference_time)
        contextual_event = None if event_id else self._contextual_event_from_intent(intent)
        missing = [
            field
            for field in intent.get("missing_fields", [])
            if not ((event_id or contextual_event is not None) and field in {"event", "query", "target"})
        ]
        if missing:
            message = "Please provide: " + ", ".join(missing) + "."
            print(message)
            return message

        if event_id:
            response = self.tools.get_calendar_event(event_id, calendar_id=calendar_id)
        elif contextual_event is not None:
            response = {
                "status": "ok",
                "message": "Calendar event returned from context.",
                "data": {"event": contextual_event},
            }
        else:
            response = self.tools.list_calendar_events(
                time_min=intent["search_start"],
                time_max=intent["search_end"],
                max_results=50,
            )
        if response["status"] != "ok":
            self.last_result = response
            message = response["message"]
            print(message)
            return message

        events = (
            [response.get("data", {}).get("event", {})]
            if event_id or contextual_event is not None
            else sorted(response.get("data", {}).get("events", []), key=_event_start)
        )
        if event_id or contextual_event is not None:
            matches = [
                event
                for event in events
                if contextual_event is not None or str(event.get("id") or "") == event_id
            ]
        else:
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
            self.last_result = {"status": "error"}
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
            description=write_human_description(updated.get("description")),
            location=updated.get("location"),
            attendees=_event_attendees(updated),
            calendar_id=_event_calendar_id(event),
            private_extended_properties=_private_extended_properties_for_event(
                updated,
            ),
        )
        self.last_result = response
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message

        self.undo_stack.append({"action": "restore_event", "event": deepcopy(event)})
        message = f"Moved calendar event: {_format_event_choice(updated)}."
        print(message)
        return message

    def _delete_confirmed_event(self, event: dict[str, Any]) -> str:
        event_id = event.get("id")
        if not event_id:
            self.last_result = {"status": "error"}
            message = "Matching event has no Google Calendar event id, so I did not delete it."
            print(message)
            return message

        delete_response = self.tools.delete_calendar_event(
            event_id,
            calendar_id=_event_calendar_id(event),
        )
        self.last_result = delete_response
        if delete_response["status"] != "ok":
            message = delete_response["message"]
            print(message)
            return message

        self.undo_stack.append({"action": "recreate_event", "event": deepcopy(event)})
        message = f"Deleted calendar event: {_format_event_choice(event)}."
        print(message)
        return message

    def undo_last_action(self) -> str:
        if not self.undo_stack:
            message = "Nothing to undo for Family Calendar."
            print(message)
            return message

        undo = self.undo_stack.pop()
        event = undo.get("event", {})
        action = undo.get("action")
        if action == "delete_event":
            response = self.tools.delete_calendar_event(
                event.get("id"),
                calendar_id=_event_calendar_id(event),
            )
            message = (
                f"Undid calendar event creation: deleted {_format_event_choice(event)}."
                if response["status"] == "ok"
                else response["message"]
            )
            print(message)
            return message

        if action == "restore_event":
            response = self._restore_calendar_event(event)
            message = (
                f"Undid calendar event update: restored {_format_event_choice(event)}."
                if response["status"] == "ok"
                else response["message"]
            )
            print(message)
            return message

        if action == "recreate_event":
            response = self._recreate_calendar_event(event)
            if response["status"] == "ok":
                recreated = response.get("data", {}).get("event", event)
                self.last_created_event = recreated
                message = f"Undid calendar event deletion: recreated {_format_event_choice(recreated)}."
            else:
                message = response["message"]
            print(message)
            return message

        message = "I do not know how to undo that Family Calendar action."
        print(message)
        return message

    def _restore_calendar_event(self, event: dict[str, Any]) -> dict[str, Any]:
        event_id = event.get("id")
        start = event.get("start", {})
        end = event.get("end", {})
        start_time = start.get("dateTime")
        end_time = end.get("dateTime")
        if not event_id or not start_time or not end_time:
            return {
                "status": "error",
                "message": "I could not undo that calendar change because the event details are incomplete.",
            }
        return self.tools.update_calendar_event(
            event_id=event_id,
            title=event.get("summary") or "Untitled event",
            start_time=start_time,
            end_time=end_time,
            timezone=start.get("timeZone") or DEFAULT_TIMEZONE,
            description=write_human_description(event.get("description")),
            location=event.get("location"),
            attendees=_event_attendees(event),
            calendar_id=_event_calendar_id(event),
            private_extended_properties=_private_extended_properties_for_event(event),
        )

    def _recreate_calendar_event(self, event: dict[str, Any]) -> dict[str, Any]:
        start = event.get("start", {})
        end = event.get("end", {})
        start_time = start.get("dateTime")
        end_time = end.get("dateTime")
        if not start_time or not end_time:
            return {
                "status": "error",
                "message": "I could not recreate that calendar event because the event time is incomplete.",
            }
        return self.tools.create_calendar_event(
            title=event.get("summary") or "Untitled event",
            start_time=start_time,
            end_time=end_time,
            timezone=start.get("timeZone") or DEFAULT_TIMEZONE,
            description=write_human_description(event.get("description")),
            location=event.get("location"),
            recurrence=event.get("recurrence"),
            attendees=_event_attendees(event),
            calendar_name=_event_calendar_id(event),
            private_extended_properties=_private_extended_properties_for_event(event),
        )

    def handle_pending_response(
        self,
        response: str,
        reference_time: datetime | None = None,
    ) -> bool:
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
            followup = extract_intent(response, now=reference_time)
            merged = _merge_create_intent(pending.payload or {}, followup)
            missing = merged.get("missing_fields", [])
            if missing:
                self.pending_action = PendingAction(action="create", payload=merged)
                print(_format_missing_create_message(merged, missing))
                return True

            self.pending_action = None
            self._create_event_from_intent(merged)
            return True

        if pending.action == "create_bulk":
            followup = extract_intent(response, now=reference_time)
            merged = _merge_bulk_create_intent(pending.payload or {}, followup)
            missing = merged.get("missing_fields", [])
            if missing:
                self.pending_action = PendingAction(action="create_bulk", payload=merged)
                print(_format_missing_bulk_create_message(merged))
                return True

            self.pending_action = None
            self._create_events_from_intents(merged.get("intents", []))
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
        if intent["intent"] == "preparation_checklist":
            active_claw.preparation_from_request(command)
        elif intent["intent"] == "family_briefing":
            active_claw.briefing_from_request(command)
        elif intent["intent"] == "list_events":
            active_claw.list_events_from_request(command)
        elif intent["intent"] == "delete_event":
            active_claw.delete_event_from_request(command)
        elif intent["intent"] == "update_event":
            active_claw.update_event_from_request(command)
        elif intent["intent"] == "add_guests":
            active_claw.add_guests_from_request(command)
        else:
            active_claw.create_event_from_request(command)


if __name__ == "__main__":
    run_cli()

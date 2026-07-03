from __future__ import annotations

from datetime import date, datetime, time, timedelta
import json
import re
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_TIMEZONE = "America/Los_Angeles"
METADATA_MARKER = "N4OS_METADATA:"

VALID_LEVELS = {"low", "medium", "high", "unknown"}
VALID_CONTEXTS = {"home", "car", "computer", "phone", "outside", "errand"}
VALID_EFFORT_TYPES = {
    "physical",
    "cognitive",
    "communication",
    "errand",
    "paperwork",
    "research",
    "admin",
    "unknown",
}
VALID_REQUIREMENTS = {
    "computer",
    "phone",
    "car",
    "internet",
    "paperwork",
    "equipment",
    "quiet",
    "focus",
}
VALID_CAN_DO_WHILE = {
    "driving",
    "commuting",
    "walking",
    "waiting",
    "watching_kids",
}
VALID_LOCATIONS = {
    "home",
    "outside",
    "anywhere",
    "specific",
    "unknown",
}
VALID_OWNERS = {"dad", "mom", "both", "unknown"}
LEGACY_MODE_TO_EFFORT_TYPE = {
    "call": "communication",
    "research": "research",
    "physical": "physical",
    "errand": "errand",
    "computer": "admin",
    "home": "physical",
    "unknown": "unknown",
}

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

DEFAULT_METADATA = {
    "context": [],
    "energy": "unknown",
    "duration_minutes": None,
    "urgency": "unknown",
    "complexity": "unknown",
    "effort_type": "unknown",
    "requires": [],
    "can_do_while": [],
    "location": "unknown",
    "owner": "unknown",
}

HOUSEHOLD_PHYSICAL_WORDS = (
    "change",
    "fix",
    "install",
    "organize",
    "filter",
    "trash",
    "laundry",
    "dishwasher",
    "garage",
    "clean",
    "repair",
    "water",
)

COMMUNICATION_WORDS = ("call", "text", "email", "message", "phone")
RESEARCH_WORDS = ("research", "look up", "lookup", "compare", "find")
PAPERWORK_WORDS = (
    "fill",
    "form",
    "forms",
    "visa",
    "passport",
    "paperwork",
    "application",
)
ADMIN_WORDS = ("book", "schedule", "reserve", "pay", "renew", "order")
ERRAND_WORDS = (
    "errand",
    "errands",
    "grocery",
    "groceries",
    "shopping",
    "store",
    "pickup",
    "pick up",
    "drop off",
    "dropoff",
)


def _default_now(now: datetime | None) -> datetime:
    if now is not None:
        if now.tzinfo is None:
            return now.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        return now

    return datetime.now(ZoneInfo(DEFAULT_TIMEZONE))


def _clean_spaces(value: str) -> str:
    return " ".join(value.split()).strip()


def _clean_list(values: Any, allowed: set[str] | None = None) -> list[str]:
    if not isinstance(values, list):
        return []

    cleaned = []
    seen = set()
    for value in values:
        normalized = str(value).strip().lower()
        aliases: dict[str, str] = {}
        if allowed == VALID_CONTEXTS:
            aliases = {
                "driving": "car",
                "commute": "car",
                "commuting": "car",
                "laptop": "computer",
                "online": "computer",
                "call": "phone",
            }
        elif allowed == VALID_REQUIREMENTS:
            aliases = {
                "laptop": "computer",
                "online": "internet",
                "document": "paperwork",
                "documents": "paperwork",
            }
        elif allowed == VALID_CAN_DO_WHILE:
            aliases = {
                "drive": "driving",
                "car": "driving",
                "commute": "commuting",
            }
        normalized = aliases.get(normalized, normalized)
        if allowed is not None and normalized not in allowed:
            continue
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    return cleaned


def _clean_level(value: Any) -> str:
    normalized = str(value or "unknown").strip().lower()
    return normalized if normalized in VALID_LEVELS else "unknown"


def _clean_choice(value: Any, allowed: set[str]) -> str:
    normalized = str(value or "unknown").strip().lower()
    return normalized if normalized in allowed else "unknown"


def _clean_owner(value: Any) -> str:
    normalized = str(value or "unknown").strip().lower()
    return normalized if normalized in VALID_OWNERS else "unknown"


def _clean_duration(value: Any) -> int | None:
    if value is None:
        return None

    try:
        duration = int(value)
    except (TypeError, ValueError):
        return None

    return duration if duration > 0 else None


def _contains_any_word(user_text: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(word)}\b", user_text) for word in words)


def _default_metadata() -> dict[str, Any]:
    return dict(DEFAULT_METADATA)


def normalize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    normalized = _default_metadata()
    if isinstance(metadata, dict):
        normalized.update(metadata)

    legacy_mode = str(normalized.pop("mode", "") or "").strip().lower()
    if normalized.get("effort_type") in (None, "", "unknown") and legacy_mode:
        normalized["effort_type"] = LEGACY_MODE_TO_EFFORT_TYPE.get(
            legacy_mode,
            "unknown",
        )

    normalized["context"] = _clean_list(normalized.get("context"), VALID_CONTEXTS)
    normalized["energy"] = _clean_level(normalized.get("energy"))
    normalized["duration_minutes"] = _clean_duration(
        normalized.get("duration_minutes"),
    )
    normalized["urgency"] = _clean_level(normalized.get("urgency"))
    normalized["complexity"] = _clean_level(normalized.get("complexity"))
    normalized["effort_type"] = _clean_choice(
        normalized.get("effort_type"),
        VALID_EFFORT_TYPES,
    )
    normalized["requires"] = _clean_list(
        normalized.get("requires"),
        VALID_REQUIREMENTS,
    )
    normalized["can_do_while"] = _clean_list(
        normalized.get("can_do_while"),
        VALID_CAN_DO_WHILE,
    )
    normalized["location"] = _clean_choice(normalized.get("location"), VALID_LOCATIONS)
    normalized["owner"] = _clean_owner(normalized.get("owner"))
    return normalized


def read_metadata_from_notes(notes: str | None) -> tuple[str, dict[str, Any]]:
    if not notes:
        return "", _default_metadata()

    marker_index = notes.find(METADATA_MARKER)
    if marker_index < 0:
        return notes.strip(), _default_metadata()

    human_notes = notes[:marker_index].strip()
    raw_metadata = notes[marker_index + len(METADATA_MARKER) :].strip()
    try:
        parsed = json.loads(raw_metadata)
    except json.JSONDecodeError:
        parsed = {}

    if not isinstance(parsed, dict):
        parsed = {}

    return human_notes, normalize_metadata(parsed)


def write_metadata_to_notes(
    notes: str | None,
    metadata: dict[str, Any] | None,
) -> str:
    human_notes, _ = read_metadata_from_notes(notes)
    metadata_json = json.dumps(normalize_metadata(metadata), indent=2)
    if human_notes:
        return f"{human_notes}\n\n{METADATA_MARKER}\n{metadata_json}"

    return f"{METADATA_MARKER}\n{metadata_json}"


def _current_or_next_weekday(reference: datetime, weekday: int) -> date:
    return (reference + timedelta(days=(weekday - reference.weekday()) % 7)).date()


def _weekday_in_next_calendar_week(reference: datetime, weekday: int) -> date:
    days_until_next_monday = 7 - reference.weekday()
    next_monday = reference + timedelta(days=days_until_next_monday)
    return (next_monday + timedelta(days=weekday)).date()


def _week_end(reference: datetime) -> date:
    return (reference + timedelta(days=6 - reference.weekday())).date()


def _extract_due_date(user_text: str, reference: datetime) -> tuple[str | None, str]:
    lowered = user_text.lower()
    if "tomorrow" in lowered:
        return (reference + timedelta(days=1)).date().isoformat(), "tomorrow"
    if "today" in lowered:
        return reference.date().isoformat(), "today"
    if "this weekend" in lowered or re.search(r"\bweekend\b", lowered):
        return _current_or_next_weekday(reference, WEEKDAYS["saturday"]).isoformat(), "this weekend"

    for name, weekday in WEEKDAYS.items():
        if re.search(rf"\b{name}\s+next week\b", lowered) or re.search(
            rf"\bnext week\s+{name}\b",
            lowered,
        ):
            return _weekday_in_next_calendar_week(reference, weekday).isoformat(), name
        if re.search(rf"\b(?:due\s+|on\s+)?{name}\b", lowered):
            return _current_or_next_weekday(reference, weekday).isoformat(), name

    match = re.search(r"\bdue\s+(\d{4}-\d{2}-\d{2})\b", lowered)
    if match is not None:
        return match.group(1), "due date"

    return None, ""


def _extract_duration_minutes(user_text: str) -> int | None:
    match = re.search(
        r"\b(?:for|takes?|under|within|in|have)\s+(\d+)\s*(minutes?|mins?|hours?|hrs?)\b",
        user_text,
        flags=re.IGNORECASE,
    )
    if match is None:
        match = re.search(
            r"\b(\d+)\s*(minutes?|mins?|hours?|hrs?)\b",
            user_text,
            flags=re.IGNORECASE,
        )
    if match is None:
        return None

    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith(("hour", "hr")):
        return amount * 60
    return amount


def _extract_level(user_text: str, field: str) -> str | None:
    lowered = user_text.lower()
    for level in ("low", "medium", "high"):
        if re.search(rf"\b{level}\s+{field}\b", lowered) or re.search(
            rf"\b{field}\s+{level}\b",
            lowered,
        ):
            return level
    return None


def _extract_contexts(user_text: str) -> tuple[list[str], list[str], list[str], str]:
    lowered = user_text.lower()
    contexts: list[str] = []
    can_do_while: list[str] = []
    requirements: list[str] = []
    location = "unknown"

    def add_context(value: str) -> None:
        if value not in contexts:
            contexts.append(value)

    def add_can_do(value: str) -> None:
        if value not in can_do_while:
            can_do_while.append(value)

    def add_requirement(value: str) -> None:
        if value not in requirements:
            requirements.append(value)

    if re.search(r"\b(?:commute|commuting)\b", lowered):
        add_context("car")
        add_context("phone")
        add_can_do("commuting")
        add_requirement("phone")
    if re.search(r"\b(?:driving|drive|car)\b", lowered):
        add_context("car")
        add_context("phone")
        add_can_do("driving")
        add_requirement("phone")
    if re.search(r"\b(?:home|house)\b", lowered):
        add_context("home")
        location = "home"
    if re.search(r"\b(?:laptop|computer|online)\b", lowered):
        add_context("computer")
        add_requirement("computer")
        if re.search(r"\b(?:online|internet|web)\b", lowered):
            add_requirement("internet")
    if re.search(r"\bquiet\b", lowered):
        add_requirement("quiet")
    if _contains_any_word(lowered, ERRAND_WORDS):
        add_context("errand")
        location = "outside"
    if re.search(r"\b(?:phone|call|text|message)\b", lowered):
        add_context("phone")
        add_requirement("phone")
    if re.search(r"\bemail\b", lowered):
        add_context("computer")
        add_requirement("computer")
        add_requirement("internet")
    if re.search(r"\b(?:outside|grocery|groceries|shopping|store)\b", lowered):
        add_context("outside")
        location = "outside"
    if re.search(r"\b(?:paperwork|forms?|documents?|application|visa|passport)\b", lowered):
        add_requirement("paperwork")
    if re.search(r"\b(?:focus|focused)\b", lowered):
        add_requirement("focus")

    return contexts, can_do_while, requirements, location


def _infer_effort_type(user_text: str) -> str:
    lowered = user_text.lower()
    if _contains_any_word(lowered, COMMUNICATION_WORDS):
        return "communication"
    if _contains_any_word(lowered, PAPERWORK_WORDS):
        return "paperwork"
    if _contains_any_word(lowered, RESEARCH_WORDS):
        return "research"
    if _contains_any_word(lowered, ADMIN_WORDS):
        return "admin"
    if _contains_any_word(lowered, ERRAND_WORDS):
        return "errand"
    if _contains_any_word(lowered, HOUSEHOLD_PHYSICAL_WORDS):
        return "physical"
    if re.search(r"\b(?:think|plan|write|learn|study)\b", lowered):
        return "cognitive"
    return "unknown"


def _infer_owner(user_text: str) -> str:
    lowered = user_text.lower()
    if re.search(r"\b(?:dad|father)\s+will\b", lowered) or re.search(
        r"\b(?:i|me)\s+(?:will|can|should|need to|have to)\b",
        lowered,
    ):
        return "dad"
    if re.search(r"\b(?:mom|mother|niyati)\s+will\b", lowered):
        return "mom"
    if re.search(r"\b(?:both|we|us|parents)\s+(?:will|can|should|need to|have to)\b", lowered):
        return "both"
    return "unknown"


def _add_communication_requirements(user_text: str, requirements: list[str]) -> None:
    lowered = user_text.lower()

    if re.search(r"\bemail\b", lowered):
        for requirement in ("computer", "internet"):
            if requirement not in requirements:
                requirements.append(requirement)
        return

    if "phone" not in requirements:
        requirements.append("phone")


def _infer_metadata(
    user_text: str,
    due: str | None,
) -> dict[str, Any]:
    metadata = _default_metadata()
    context, can_do_while, requirements, location = _extract_contexts(user_text)
    effort_type = _infer_effort_type(user_text)
    energy = _extract_level(user_text, "energy")
    urgency = _extract_level(user_text, "urgency")
    complexity = _extract_level(user_text, "complexity")
    duration = _extract_duration_minutes(user_text)

    lowered = user_text.lower()
    if urgency is None and re.search(r"\b(?:urgent|asap|soon)\b", lowered):
        urgency = "high"

    if energy is None:
        if effort_type == "communication":
            energy = "low"
        elif effort_type in ("physical", "errand", "admin", "research"):
            energy = "medium"
        elif effort_type == "paperwork":
            energy = "high"

    if duration is None:
        if effort_type == "communication":
            duration = 20
        elif effort_type == "research":
            duration = 45
        elif effort_type == "admin":
            duration = 30
        elif effort_type == "paperwork":
            duration = 60
        elif effort_type == "physical":
            duration = 15

    if complexity is None:
        if effort_type in ("research", "admin"):
            complexity = "medium"
        elif effort_type == "paperwork":
            complexity = "high"
        elif effort_type in ("communication", "physical"):
            complexity = "low"

    if urgency is None and due is not None:
        urgency = "medium"

    if effort_type == "physical":
        if "home" not in context:
            context.append("home")
        if "equipment" not in requirements:
            requirements.append("equipment")
        if location == "unknown":
            location = "home"
    if effort_type == "communication":
        _add_communication_requirements(user_text, requirements)
    if effort_type in ("research", "admin") and "computer" not in requirements:
        requirements.append("computer")
    if effort_type in ("research", "admin") and "internet" not in requirements:
        requirements.append("internet")
    if effort_type == "research" and "focus" not in requirements:
        requirements.append("focus")
    if effort_type == "paperwork":
        for requirement in ("computer", "paperwork", "focus"):
            if requirement not in requirements:
                requirements.append(requirement)
    if effort_type == "errand" and "car" not in requirements:
        requirements.append("car")
    if effort_type == "communication":
        for value in ("driving", "commuting"):
            if value not in can_do_while:
                can_do_while.append(value)
        if "phone" not in context:
            context.append("phone")
        if location == "unknown":
            location = "anywhere"
    if effort_type == "errand" and location == "unknown":
        location = "outside"
    if effort_type in ("research", "admin", "paperwork") and location == "unknown":
        location = "anywhere"

    metadata.update(
        {
            "context": context,
            "energy": energy or "unknown",
            "duration_minutes": duration,
            "urgency": urgency or "unknown",
            "complexity": complexity or "unknown",
            "effort_type": effort_type,
            "requires": requirements,
            "can_do_while": can_do_while,
            "location": location,
            "owner": _infer_owner(user_text),
        }
    )
    return normalize_metadata(metadata)


def _strip_create_words(user_text: str) -> str:
    return re.sub(
        r"^\s*(?:please\s+)?(?:add|create|capture|remember)\s+(?:an?\s+)?(?:task\s+)?",
        "",
        user_text,
        flags=re.IGNORECASE,
    ).strip()


def _strip_task_annotations(title: str) -> str:
    cleaned = title
    cleaned = re.sub(r"\s*,\s*(?:needs?|requires?).*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:this\s+weekend|today|tomorrow|tonight)\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\b(?:due\s+|on\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?:\s+next week)?\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(?:during|while|when)\s+(?:the\s+)?(?:commute|commuting|driving|drive|car)\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(?:for|takes?|under|within|in)\s+\d+\s*(?:minutes?|mins?|hours?|hrs?)\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b(?:low|medium|high)\s+(?:energy|urgency|complexity)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:urgent|asap)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip(" ,.-")
    return _clean_spaces(cleaned)


def _title_from_request(user_text: str) -> str | None:
    title = _strip_task_annotations(_strip_create_words(user_text))
    if not title:
        return None

    return title[:1].upper() + title[1:]


def _extract_create_intent(
    user_text: str,
    reference: datetime,
) -> dict[str, Any]:
    due, _ = _extract_due_date(user_text, reference)
    title = _title_from_request(user_text)
    missing_fields = []
    if title is None:
        missing_fields.append("title")

    return {
        "intent": "create_task",
        "title": title,
        "notes": None,
        "due": due,
        "metadata": _infer_metadata(user_text, due),
        "missing_fields": missing_fields,
    }


def _end_of_day(value: date, timezone: str = DEFAULT_TIMEZONE) -> str:
    return datetime.combine(value, time.max, tzinfo=ZoneInfo(timezone)).isoformat()


def _start_of_day(value: date, timezone: str = DEFAULT_TIMEZONE) -> str:
    return datetime.combine(value, time.min, tzinfo=ZoneInfo(timezone)).isoformat()


def _extract_recommendation_filters(
    user_text: str,
    reference: datetime,
) -> dict[str, Any]:
    lowered = user_text.lower()
    contexts, can_do_while, requirements, location = _extract_contexts(user_text)
    filters: dict[str, Any] = {}

    if contexts:
        filters["context"] = contexts
    if can_do_while:
        filters["can_do_while"] = can_do_while
    if requirements:
        filters["available_resources"] = requirements
    if location != "unknown":
        filters["location"] = location

    energy = _extract_level(user_text, "energy")
    if energy is not None:
        filters["energy"] = energy
    elif "bored" in lowered:
        filters["max_energy"] = "medium"
        filters["max_complexity"] = "medium"
        filters["exclude_requires"] = ["focus"]

    duration = _extract_duration_minutes(user_text)
    if duration is not None:
        filters["duration_minutes"] = duration

    if re.search(r"\b(?:urgent|asap)\b", lowered):
        filters["urgency"] = "high"

    effort_type = _infer_effort_type(user_text)
    if effort_type != "unknown":
        filters["effort_type"] = effort_type
    elif re.search(r"\bcalls?\b", lowered):
        filters["effort_type"] = "communication"
    elif re.search(r"\bphysical\s+tasks?\b|\bphysical\s+work\b", lowered):
        filters["effort_type"] = "physical"
    elif re.search(r"\bcognitive\s+(?:tasks?|work)\b", lowered):
        filters["effort_type"] = "cognitive"
    elif re.search(r"\bpaperwork\b", lowered):
        filters["effort_type"] = "paperwork"

    if filters.get("effort_type") == "communication":
        available = set(filters.get("available_resources", []))
        available.add("phone")
        filters["available_resources"] = sorted(available)
    elif filters.get("effort_type") == "paperwork":
        available = set(filters.get("available_resources", []))
        available.update(["computer", "internet", "paperwork", "focus"])
        filters["available_resources"] = sorted(available)

    if re.search(r"\b(?:driving|commuting)\b", lowered):
        filters["available_resources"] = ["phone", "car"]
        filters["unavailable_resources"] = [
            "computer",
            "paperwork",
            "equipment",
            "quiet",
            "focus",
        ]
    elif re.search(r"\b(?:laptop|computer)\b", lowered):
        available = set(filters.get("available_resources", []))
        available.update(["computer", "internet", "phone"])
        filters["available_resources"] = sorted(available)

    if "due this week" in lowered or "this week" in lowered:
        filters["due_min"] = _start_of_day(reference.date())
        filters["due_max"] = _end_of_day(_week_end(reference))
    elif "due today" in lowered or "today" in lowered:
        filters["due_min"] = _start_of_day(reference.date())
        filters["due_max"] = _end_of_day(reference.date())
    elif "due tomorrow" in lowered or "tomorrow" in lowered:
        day = (reference + timedelta(days=1)).date()
        filters["due_min"] = _start_of_day(day)
        filters["due_max"] = _end_of_day(day)

    if "context" in filters:
        filters["available_context"] = filters["context"]
    if "duration_minutes" in filters:
        filters["available_time_minutes"] = filters["duration_minutes"]
    if "effort_type" in filters:
        filters["preferred_effort_type"] = filters["effort_type"]

    return filters


def _extract_query_after_action(user_text: str) -> str | None:
    cleaned = re.sub(
        r"^\s*(?:complete|finish|mark|delete|remove)\s+(?:task\s+)?",
        "",
        user_text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+done$", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip(" .")
    return cleaned or None


def extract_intent(
    user_text: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    reference = _default_now(now)
    lowered = user_text.lower().strip()

    if re.search(r"^\s*(?:complete|finish|mark)\b", lowered):
        return {
            "intent": "complete_task",
            "query": _extract_query_after_action(user_text),
            "missing_fields": [],
        }

    if re.search(r"^\s*(?:delete|remove)\b", lowered):
        return {
            "intent": "delete_task",
            "query": _extract_query_after_action(user_text),
            "missing_fields": [],
        }

    if re.search(r"^\s*(?:add|create|capture|remember)\b", lowered):
        return _extract_create_intent(user_text, reference)

    return {
        "intent": "recommend_tasks",
        "filters": _extract_recommendation_filters(user_text, reference),
        "missing_fields": [],
    }

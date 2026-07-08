from __future__ import annotations

from datetime import date, datetime
import re
from typing import Any

from intent import extract_tags, normalize_tags, read_metadata_from_notes


ENERGY_RANK = {
    "unknown": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
}

URGENCY_RANK = {
    "unknown": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
}


def normalize_recommendation_filters(
    filters: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized = dict(filters or {})
    aliases = {
        "available_context": "context",
        "available_time_minutes": "duration_minutes",
        "preferred_effort_type": "effort_type",
    }
    for source, target in aliases.items():
        if target not in normalized and source in normalized:
            normalized[target] = normalized[source]
    return normalized


def _normalize_text(value: str | None) -> str:
    if value is None:
        return ""

    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _parse_due_date(value: str | None) -> date | None:
    if not value:
        return None

    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        pass

    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_filter_date(value: str | None) -> date | None:
    if not value:
        return None

    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        pass

    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _task_notes_and_metadata(task: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    notes, legacy_metadata = read_metadata_from_notes(task.get("notes"))
    metadata = task.get("_n4os_metadata")
    if isinstance(metadata, dict):
        return notes, metadata
    return notes, legacy_metadata


def _task_tags(task: dict[str, Any], metadata: dict[str, Any]) -> set[str]:
    return set(
        normalize_tags(
            [
                *list(metadata.get("tags") or []),
                *extract_tags(task.get("title")),
                *extract_tags(task.get("notes")),
            ],
        ),
    )


def _task_context_values(metadata: dict[str, Any]) -> set[str]:
    values = set(metadata.get("context") or [])
    values.update(metadata.get("can_do_while") or [])
    effort_type = metadata.get("effort_type")
    if effort_type:
        values.add(str(effort_type))
    return {str(value).lower() for value in values if value}


def _matches_context(metadata: dict[str, Any], desired_context: list[str] | None) -> bool:
    if not desired_context:
        return True

    desired = {str(value).lower() for value in desired_context}
    return bool(desired & _task_context_values(metadata))


def _matches_energy(metadata: dict[str, Any], available_energy: str | None) -> bool:
    if available_energy is None:
        return True

    task_energy = str(metadata.get("energy") or "unknown")
    if task_energy == "unknown":
        return True

    return ENERGY_RANK.get(task_energy, 0) <= ENERGY_RANK.get(available_energy, 0)


def _matches_max_level(metadata: dict[str, Any], field: str, max_level: str | None) -> bool:
    if max_level is None:
        return True

    task_level = str(metadata.get(field) or "unknown")
    if task_level == "unknown":
        return True

    return ENERGY_RANK.get(task_level, 0) <= ENERGY_RANK.get(max_level, 0)


def _matches_duration(metadata: dict[str, Any], available_minutes: int | None) -> bool:
    if available_minutes is None:
        return True

    duration = metadata.get("duration_minutes")
    if duration is None:
        return False

    return int(duration) <= available_minutes


def _matches_due(
    task: dict[str, Any],
    due_min: str | None,
    due_max: str | None,
) -> bool:
    if due_min is None and due_max is None:
        return True

    due = _parse_due_date(task.get("due"))
    if due is None:
        return False

    min_date = _parse_filter_date(due_min)
    max_date = _parse_filter_date(due_max)
    if min_date is not None and due < min_date:
        return False
    if max_date is not None and due > max_date:
        return False
    return True


def _matches_metadata_value(
    metadata: dict[str, Any],
    field: str,
    expected: str | None,
) -> bool:
    if expected is None:
        return True

    return str(metadata.get(field) or "unknown") == expected


def _task_requirements(metadata: dict[str, Any]) -> set[str]:
    return {str(value).lower() for value in metadata.get("requires") or [] if value}


def _matches_required_resources(
    metadata: dict[str, Any],
    available_resources: list[str] | None,
    unavailable_resources: list[str] | None,
    exclude_requires: list[str] | None,
) -> bool:
    task_requires = _task_requirements(metadata)
    unavailable = {str(value).lower() for value in unavailable_resources or []}
    excluded = {str(value).lower() for value in exclude_requires or []}
    if task_requires & (unavailable | excluded):
        return False

    if not available_resources:
        return True

    available = {str(value).lower() for value in available_resources}
    return task_requires <= available


def _matches_can_do_while(
    metadata: dict[str, Any],
    desired: list[str] | None,
) -> bool:
    if not desired:
        return True

    task_values = {str(value).lower() for value in metadata.get("can_do_while") or []}
    desired_values = {str(value).lower() for value in desired}
    return bool(task_values & desired_values)


def _matches_location(metadata: dict[str, Any], location: str | None) -> bool:
    if location is None:
        return True

    task_location = str(metadata.get("location") or "unknown")
    return task_location in (location, "anywhere", "unknown")


def _matches_tags(
    task: dict[str, Any],
    metadata: dict[str, Any],
    desired_tags: list[str] | None,
) -> bool:
    if not desired_tags:
        return True

    desired = set(normalize_tags(desired_tags))
    if not desired:
        return True

    return desired <= _task_tags(task, metadata)


def task_matches_filters(
    task: dict[str, Any],
    filters: dict[str, Any],
) -> bool:
    filters = normalize_recommendation_filters(filters)
    if task.get("status") == "completed":
        return False

    _, metadata = _task_notes_and_metadata(task)
    return (
        _matches_tags(task, metadata, filters.get("tags"))
        and _matches_context(metadata, filters.get("context"))
        and _matches_energy(metadata, filters.get("energy"))
        and _matches_max_level(metadata, "energy", filters.get("max_energy"))
        and _matches_max_level(
            metadata,
            "complexity",
            filters.get("max_complexity"),
        )
        and _matches_duration(metadata, filters.get("duration_minutes"))
        and _matches_due(task, filters.get("due_min"), filters.get("due_max"))
        and _matches_metadata_value(metadata, "urgency", filters.get("urgency"))
        and _matches_metadata_value(
            metadata,
            "effort_type",
            filters.get("effort_type"),
        )
        and _matches_can_do_while(metadata, filters.get("can_do_while"))
        and _matches_location(metadata, filters.get("location"))
        and _matches_required_resources(
            metadata,
            filters.get("available_resources"),
            filters.get("unavailable_resources"),
            filters.get("exclude_requires"),
        )
    )


def _task_match_score(task: dict[str, Any], filters: dict[str, Any]) -> int:
    filters = normalize_recommendation_filters(filters)
    _, metadata = _task_notes_and_metadata(task)
    score = 0
    tag_filter = set(normalize_tags(filters.get("tags") or []))
    score += 5 * len(tag_filter & _task_tags(task, metadata))

    context_filter = set(filters.get("context") or [])
    score += 3 * len(context_filter & _task_context_values(metadata))

    can_do_filter = set(filters.get("can_do_while") or [])
    task_can_do = set(metadata.get("can_do_while") or [])
    score += 4 * len(can_do_filter & task_can_do)

    available = set(filters.get("available_resources") or [])
    score += 2 * len(available & _task_requirements(metadata))

    for field in ("energy", "effort_type", "urgency", "location"):
        expected = filters.get(field)
        if expected is not None and metadata.get(field) == expected:
            score += 3

    available_minutes = filters.get("duration_minutes")
    duration = metadata.get("duration_minutes")
    if available_minutes is not None and duration is not None:
        score += max(0, 3 - int((int(available_minutes) - int(duration)) / 15))

    if filters.get("max_energy") and metadata.get("energy") in ("low", "medium"):
        score += 1
    if filters.get("max_complexity") and metadata.get("complexity") in ("low", "medium"):
        score += 1
    return score


def _recommendation_key(task: dict[str, Any], filters: dict[str, Any]) -> tuple[int, int, date, int, str]:
    filters = normalize_recommendation_filters(filters)
    _, metadata = _task_notes_and_metadata(task)
    urgency = str(metadata.get("urgency") or "unknown")
    due = _parse_due_date(task.get("due")) or date.max
    duration = metadata.get("duration_minutes")
    duration_value = int(duration) if duration is not None else 10_000
    title = _normalize_text(task.get("title"))
    return (
        -_task_match_score(task, filters),
        -URGENCY_RANK.get(urgency, 0),
        due,
        duration_value,
        title,
    )


def _join_values(values: set[str]) -> str:
    return ", ".join(sorted(values))


def _task_fit_reasons(task: dict[str, Any], filters: dict[str, Any]) -> list[str]:
    filters = normalize_recommendation_filters(filters)
    _, metadata = _task_notes_and_metadata(task)
    reasons: list[str] = []

    tag_filter = set(normalize_tags(filters.get("tags") or []))
    tag_matches = tag_filter & _task_tags(task, metadata)
    if tag_matches:
        reasons.append(f"tagged {_join_values(tag_matches)}")

    can_do_filter = set(filters.get("can_do_while") or [])
    task_can_do = set(metadata.get("can_do_while") or [])
    can_do_matches = can_do_filter & task_can_do
    if can_do_matches:
        reasons.append(f"can do while {_join_values(can_do_matches)}")

    context_filter = set(filters.get("context") or [])
    context_matches = context_filter & _task_context_values(metadata)
    if context_matches:
        reasons.append(f"matches {_join_values(context_matches)}")

    effort_type = filters.get("effort_type")
    if effort_type is not None and metadata.get("effort_type") == effort_type:
        reasons.append(f"{effort_type} task")

    energy = filters.get("energy")
    task_energy = metadata.get("energy")
    if energy is not None and task_energy in ("unknown", energy):
        reasons.append(f"fits {energy} energy")
    elif filters.get("max_energy") and task_energy in ("unknown", "low", "medium"):
        reasons.append("low effort")

    available_minutes = filters.get("duration_minutes")
    duration = metadata.get("duration_minutes")
    if available_minutes is not None and duration is not None:
        reasons.append(f"fits in {available_minutes} minutes")

    location = filters.get("location")
    task_location = metadata.get("location")
    if location is not None and task_location in (location, "anywhere"):
        reasons.append(f"works at {location}")

    available = set(filters.get("available_resources") or [])
    resource_matches = available & _task_requirements(metadata)
    if resource_matches:
        reasons.append(f"uses {_join_values(resource_matches)}")

    if not reasons:
        reasons.append("matches your situation")

    return reasons[:3]


def recommend_task_matches(
    tasks: list[dict[str, Any]],
    filters: dict[str, Any],
    limit: int = 5,
) -> list[dict[str, Any]]:
    normalized_filters = normalize_recommendation_filters(filters)
    matches = [
        task
        for task in tasks
        if task_matches_filters(task, normalized_filters)
    ]
    ranked = sorted(
        matches,
        key=lambda task: _recommendation_key(task, normalized_filters),
    )
    return [
        {
            "task": task,
            "score": _task_match_score(task, normalized_filters),
            "reasons": _task_fit_reasons(task, normalized_filters),
        }
        for task in ranked[:limit]
    ]


def recommend_tasks(
    tasks: list[dict[str, Any]],
    filters: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        recommendation["task"]
        for recommendation in recommend_task_matches(tasks, filters)
    ]


def match_tasks(query: str, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_query = _normalize_text(query)
    query_tokens = set(normalized_query.split())
    ranked = []
    for task in tasks:
        text = _normalize_text(
            " ".join(
                str(part)
                for part in (task.get("title"), task.get("notes"))
                if part
            )
        )
        if not text:
            continue

        score = 0
        if normalized_query and normalized_query in text:
            score += 6
        text_tokens = set(text.split())
        score += 2 * len(query_tokens & text_tokens)
        if score > 0:
            ranked.append((score, task))

    return [
        task
        for _, task in sorted(
            ranked,
            key=lambda item: (-item[0], _normalize_text(item[1].get("title"))),
        )
    ]

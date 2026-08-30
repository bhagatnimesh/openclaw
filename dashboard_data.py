from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

from dashboard_sources import (
    DEFAULT_TIMEZONE,
    DashboardSources,
    build_default_home_board_tools,
    build_default_homework_tools,
    build_default_shopping_tools,
    default_sources,
    fallback_recommend_task_matches,
)


SHOPPING_LIST_LABELS = {
    "indian": "Indian",
    "costco": "Costco",
    "whole-foods": "Whole Foods",
    "amazon": "Amazon",
    "others": "Others",
}
HOMEWORK_CHILDREN = ("Nysha", "Navya")
ROOT = Path(__file__).resolve().parent


def _local_now(now: datetime | None = None) -> datetime:
    timezone = ZoneInfo(DEFAULT_TIMEZONE)
    if now is None:
        return datetime.now(timezone)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone)
    return now.astimezone(timezone)


def _start_of_day(value: datetime) -> datetime:
    return datetime.combine(value.date(), time.min, tzinfo=value.tzinfo)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_datetime(value: str | None, fallback_tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed_date = _parse_date(value)
        if parsed_date is None:
            return None
        return datetime.combine(parsed_date, time.min, tzinfo=fallback_tz)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=fallback_tz)
    return parsed.astimezone(fallback_tz)


def _event_start_end(event: dict[str, Any], timezone: ZoneInfo) -> tuple[datetime, datetime, bool]:
    start = event.get("start") or {}
    end = event.get("end") or {}
    all_day = "date" in start and "dateTime" not in start
    start_value = start.get("dateTime") or start.get("date")
    end_value = end.get("dateTime") or end.get("date")
    parsed_start = _parse_datetime(start_value, timezone)
    parsed_end = _parse_datetime(end_value, timezone)
    if parsed_start is None:
        parsed_start = datetime.max.replace(tzinfo=timezone)
    if parsed_end is None or parsed_end <= parsed_start:
        parsed_end = parsed_start + timedelta(hours=1)
    return parsed_start, parsed_end, all_day


def _format_time(value: datetime) -> str:
    return value.strftime("%-I:%M %p")


def _format_date_label(value: date) -> str:
    return value.strftime("%a, %b %-d")


def _clean_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    cleaned = " ".join(str(value).split()).strip()
    return cleaned or fallback


def _tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    return set(re.findall(r"[a-z0-9]+", value.lower()))


def _extract_hashtag_tags(value: str | None) -> list[str]:
    if not value:
        return []
    return [
        match.group("tag")
        for match in re.finditer(r"(?<![\w/])#(?P<tag>[A-Za-z][A-Za-z0-9_-]*)", value)
    ]


def _normalize_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_tags = re.split(r"[\s,]+", value)
    elif isinstance(value, list | tuple | set):
        raw_tags = value
    else:
        raw_tags = []

    tags = []
    seen = set()
    for raw_tag in raw_tags:
        tag = _clean_text(raw_tag).lstrip("#").lower()
        tag = re.sub(r"[^a-z0-9_-]+", "-", tag).strip("-")
        if not tag or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
    return tags


def _normalize_event(
    event: dict[str, Any],
    read_metadata: CalendarMetadataReader,
    timezone: ZoneInfo,
) -> dict[str, Any]:
    start, end, all_day = _event_start_end(event, timezone)
    notes, metadata = read_metadata(event)
    owner = _clean_text(metadata.get("owner"), "unknown").lower()
    person = _clean_text(metadata.get("person"), "family")
    category = _clean_text(metadata.get("category"))
    time_label = "All day" if all_day else f"{_format_time(start)}"
    if not all_day:
        time_label = f"{time_label}-{_format_time(end)}"
    prep_notes = _clean_text(metadata.get("preparation_notes") or notes)
    return {
        "id": _clean_text(event.get("id")),
        "title": _clean_text(event.get("summary"), "Untitled event"),
        "location": _clean_text(event.get("location")),
        "notes": notes,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "start_date": start.date().isoformat(),
        "start_time": _format_time(start) if not all_day else "",
        "end_time": _format_time(end) if not all_day else "",
        "time_label": time_label,
        "day_label": _format_date_label(start.date()),
        "all_day": all_day,
        "owner": owner,
        "owner_label": "Unassigned" if owner == "unknown" else owner.title(),
        "person": person,
        "category": category,
        "preparation_needed": bool(metadata.get("preparation_needed")),
        "preparation_notes": prep_notes,
        "metadata": metadata,
        "_start_dt": start,
        "_end_dt": end,
    }


def _normalize_task(
    task: dict[str, Any],
    read_metadata: TaskMetadataReader,
    today: date,
) -> dict[str, Any]:
    notes, metadata = read_metadata(task.get("notes"))
    due = _parse_date(task.get("due"))
    owner = _clean_text(metadata.get("owner"), "unknown").lower()
    duration = metadata.get("duration_minutes")
    due_label = "No due date"
    days_until_due = None
    if due is not None:
        days_until_due = (due - today).days
        if days_until_due < 0:
            due_label = f"{abs(days_until_due)}d overdue"
        elif days_until_due == 0:
            due_label = "Due today"
        elif days_until_due == 1:
            due_label = "Due tomorrow"
        else:
            due_label = f"Due in {days_until_due}d"

    return {
        "id": _clean_text(task.get("id")),
        "title": _clean_text(task.get("title"), "Untitled task"),
        "notes": notes,
        "status": _clean_text(task.get("status"), "needsAction"),
        "due": due.isoformat() if due is not None else "",
        "due_label": due_label,
        "days_until_due": days_until_due,
        "owner": owner,
        "owner_label": "Unassigned" if owner == "unknown" else owner.title(),
        "duration_minutes": duration,
        "energy": _clean_text(metadata.get("energy"), "unknown"),
        "urgency": _clean_text(metadata.get("urgency"), "unknown"),
        "effort_type": _clean_text(metadata.get("effort_type"), "unknown"),
        "context": list(metadata.get("context") or []),
        "requires": list(metadata.get("requires") or []),
        "can_do_while": list(metadata.get("can_do_while") or []),
        "location": _clean_text(metadata.get("location"), "unknown"),
        "tags": _normalize_tags(
            [
                *_normalize_tags(metadata.get("tags")),
                *_extract_hashtag_tags(task.get("title")),
                *_extract_hashtag_tags(notes),
            ],
        ),
        "metadata": metadata,
    }


def _event_overlaps(event: dict[str, Any], start: datetime, end: datetime) -> bool:
    return event["_start_dt"] < end and event["_end_dt"] > start


def _find_conflicts(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    timed_events = [event for event in events if not event["all_day"]]
    timed_events.sort(key=lambda event: event["_start_dt"])
    conflicts = []
    for previous, current in zip(timed_events, timed_events[1:]):
        if current["_start_dt"] < previous["_end_dt"]:
            conflicts.append(
                {
                    "title": "Calendar overlap",
                    "detail": f"{previous['title']} overlaps {current['title']}.",
                    "time": current["time_label"],
                },
            )
    return conflicts


def _busy_day_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    busy_minutes = 0
    timed_count = 0
    for event in events:
        if event["all_day"]:
            continue
        timed_count += 1
        busy_minutes += max(0, int((event["_end_dt"] - event["_start_dt"]).total_seconds() / 60))
    overloaded = len(events) >= 3 or busy_minutes >= 300
    return {
        "event_count": len(events),
        "timed_event_count": timed_count,
        "busy_minutes": busy_minutes,
        "overloaded": overloaded,
        "label": f"{len(events)} events, {busy_minutes // 60}h {busy_minutes % 60}m scheduled",
    }


def _free_minutes_until_next_event(today_events: list[dict[str, Any]], now: datetime) -> tuple[int | None, dict[str, Any] | None]:
    upcoming = [
        event
        for event in today_events
        if not event["all_day"] and event["_end_dt"] >= now
    ]
    upcoming.sort(key=lambda event: event["_start_dt"])
    for event in upcoming:
        if event["_start_dt"] >= now:
            return max(0, int((event["_start_dt"] - now).total_seconds() / 60)), event
        return 0, event
    return None, None


def _recommend_tasks(
    sources: DashboardSources,
    tasks: list[dict[str, Any]],
    filters: dict[str, Any],
    limit: int = 5,
) -> list[dict[str, Any]]:
    raw_tasks = [task["raw"] for task in tasks]
    try:
        recommendations = sources.recommend_task_matches(raw_tasks, filters, limit)
    except Exception:
        recommendations = fallback_recommend_task_matches(raw_tasks, filters, limit)

    by_id = {task["id"]: task for task in tasks if task["id"]}
    normalized_recommendations = []
    for recommendation in recommendations:
        raw_task = recommendation.get("task", recommendation)
        task_id = _clean_text(raw_task.get("id"))
        normalized = by_id.get(task_id)
        if normalized is None:
            normalized = _normalize_task(raw_task, sources.read_task_metadata, _local_now().date())
        normalized_recommendations.append(
            {
                "task": normalized,
                "score": recommendation.get("score", 0),
                "reasons": list(recommendation.get("reasons") or ["matches the current window"]),
            },
        )
    return normalized_recommendations


def _task_list_entries(task_response: dict[str, Any]) -> list[dict[str, str]]:
    task_lists = task_response.get("data", {}).get("task_lists")
    if isinstance(task_lists, list):
        entries = []
        for task_list in task_lists:
            if not isinstance(task_list, dict):
                continue
            task_list_id = _clean_text(task_list.get("id"), "@default")
            entries.append(
                {
                    "id": task_list_id,
                    "title": _clean_text(task_list.get("title"), task_list_id),
                },
            )
        if entries:
            return entries
    return [{"id": "@default", "title": "My Tasks"}]


def _is_dashboard_shopping_task_list(task_list: dict[str, str]) -> bool:
    title = _clean_text(task_list.get("title")).lower()
    return title.startswith("shopping") or title.startswith("grocery")


def _list_dashboard_tasks(sources: DashboardSources) -> dict[str, Any]:
    list_task_lists = getattr(sources.task_tools, "list_task_lists", None)
    if not callable(list_task_lists):
        return sources.task_tools.list_tasks(show_completed=True)

    task_lists_response = list_task_lists()
    if task_lists_response.get("status") != "ok":
        return {
            "status": "ok",
            "message": "Tasks unavailable.",
            "data": {
                "task_lists": [],
                "tasks": [],
                "unavailable": True,
                "warnings": [
                    task_lists_response.get("message", "Task source unavailable."),
                ],
            },
        }

    all_tasks: list[dict[str, Any]] = []
    task_lists = [
        task_list
        for task_list in _task_list_entries(task_lists_response)
        if not _is_dashboard_shopping_task_list(task_list)
    ]
    warnings = []
    for task_list in task_lists:
        response = sources.task_tools.list_tasks(
            task_list_id=task_list["id"],
            show_completed=True,
        )
        if response.get("status") != "ok":
            warnings.append(
                f"{task_list['title']} task list unavailable: {response.get('message', 'Task list unavailable.')}"
            )
            continue
        for task in response.get("data", {}).get("tasks") or []:
            if not isinstance(task, dict):
                continue
            all_tasks.append(
                {
                    **task,
                    "task_list_id": task_list["id"],
                    "task_list_title": task_list["title"],
                },
            )

    return {
        "status": "ok",
        "message": "Tasks returned from Google Tasks.",
        "data": {
            "task_lists": task_lists,
            "tasks": all_tasks,
            "warnings": warnings,
        },
    }


def _decision_filters(available_minutes: int | None, prep_window: bool) -> dict[str, Any]:
    minutes = available_minutes if available_minutes is not None else 60
    usable_minutes = max(10, min(minutes - 5, 90))
    filters: dict[str, Any] = {
        "duration_minutes": usable_minutes,
        "energy": "medium",
        "context": ["home", "computer", "phone"],
        "available_resources": ["computer", "phone", "internet", "paperwork"],
        "location": "home",
    }
    if prep_window:
        filters["effort_type"] = "paperwork"
    return filters


def _task_priority_key(task: dict[str, Any]) -> tuple[int, int, int, str]:
    urgency_rank = {"high": 3, "medium": 2, "low": 1, "unknown": 0}
    due_days = task["days_until_due"] if task["days_until_due"] is not None else 999
    owner_gap = 1 if task["owner"] == "unknown" else 0
    return (
        -urgency_rank.get(str(task["urgency"]), 0),
        due_days,
        -owner_gap,
        task["title"].lower(),
    )


def _make_best_next_action(
    today_events: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    available_minutes, next_event = _free_minutes_until_next_event(today_events, now)
    upcoming_prep = [
        event
        for event in today_events
        if event["preparation_needed"] and event["_end_dt"] >= now
    ]
    upcoming_prep.sort(key=lambda event: event["_start_dt"])
    due_now = [
        task
        for task in tasks
        if task["days_until_due"] is not None and task["days_until_due"] <= 0
    ]
    due_now.sort(key=_task_priority_key)

    if upcoming_prep and (available_minutes is None or available_minutes <= 120):
        event = upcoming_prep[0]
        action = event["preparation_notes"] or f"Prep for {event['title']}"
        reasons = ["prep-needed calendar item"]
        if available_minutes is not None:
            reasons.append(f"{available_minutes} minutes until next event")
        if event["owner"] == "unknown":
            reasons.append("owner is unassigned")
        return {
            "title": action,
            "source": "calendar",
            "why": f"{event['title']} is marked prep-needed.",
            "reasons": reasons,
            "available_minutes": available_minutes,
            "next_event": next_event["title"] if next_event else "",
        }

    if due_now:
        task = due_now[0]
        reasons = [task["due_label"]]
        if task["duration_minutes"]:
            reasons.append(f"{task['duration_minutes']} minute task")
        if task["owner"] == "unknown":
            reasons.append("needs an owner")
        return {
            "title": task["title"],
            "source": "task",
            "why": "This open loop is due now.",
            "reasons": reasons,
            "available_minutes": available_minutes,
            "next_event": next_event["title"] if next_event else "",
        }

    if recommendations:
        recommendation = recommendations[0]
        task = recommendation["task"]
        return {
            "title": task["title"],
            "source": "task",
            "why": "; ".join(recommendation["reasons"]),
            "reasons": recommendation["reasons"],
            "available_minutes": available_minutes,
            "next_event": next_event["title"] if next_event else "",
        }

    if next_event:
        return {
            "title": f"Get ready for {next_event['title']}",
            "source": "calendar",
            "why": "No matching open task is ready for this window.",
            "reasons": ["next event is the anchor"],
            "available_minutes": available_minutes,
            "next_event": next_event["title"],
        }

    return {
        "title": "No urgent action right now",
        "source": "empty",
        "why": "Calendar and task data have no immediate open loop.",
        "reasons": ["protect the open space"],
        "available_minutes": available_minutes,
        "next_event": "",
    }


def _progress_from_metadata(metadata: dict[str, Any]) -> int | None:
    for key in ("prep_progress", "preparation_progress", "ready_percent", "progress"):
        value = metadata.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip().rstrip("%")
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if 0 <= number <= 1:
            number *= 100
        if 0 <= number <= 100:
            return int(number)
    return None


def _matching_action_items(event: dict[str, Any], tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    event_tokens = _tokens(" ".join([event["title"], event["category"], event["person"]]))
    if not event_tokens:
        return []
    matches = []
    for task in tasks:
        task_tokens = _tokens(" ".join([task["title"], task["notes"], task["effort_type"]]))
        if event_tokens & task_tokens:
            matches.append(task)
    matches.sort(key=_task_priority_key)
    return matches[:3]


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in task.items() if key != "raw"}


def _home_board_context_label(context: str | None) -> str:
    labels = {
        "before_leave": "Before leaving",
        "at_home": "At home",
        "school": "School",
        "kitchen": "Kitchen",
        "airport": "Airport",
        "general": "General",
    }
    return labels.get(context or "general", "General")


def _home_board_items(
    sources: DashboardSources,
    item_date: date,
    now: datetime,
) -> tuple[list[dict[str, Any]], list[str]]:
    tools = getattr(sources, "home_board_tools", None)
    if tools is None:
        return [], []

    try:
        response = tools.list_items(
            date=item_date.isoformat(),
            status="pending",
            include_expired=False,
            now=now,
        )
    except Exception as error:
        return [], [f"Home Board source unavailable: {error.__class__.__name__}."]

    if response.get("status") != "ok":
        return [], [response.get("message", "Home Board source unavailable.")]

    items = []
    for item in response.get("data", {}).get("items") or []:
        context = _clean_text(item.get("context"), "general")
        items.append(
            {
                "id": _clean_text(item.get("id")),
                "person_or_group": _clean_text(item.get("person_or_group"), "Family"),
                "message": _clean_text(item.get("message"), "Untitled notice"),
                "date": _clean_text(item.get("date")),
                "context": context,
                "context_label": _home_board_context_label(context),
                "trigger": _clean_text(item.get("trigger")),
                "status": _clean_text(item.get("status"), "pending"),
                "priority": _clean_text(item.get("priority"), "medium"),
                "expires_at": _clean_text(item.get("expires_at")),
            },
        )
    return items, []


def _home_board_for_portal(
    sources: DashboardSources,
    today: date,
    now: datetime,
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    tomorrow = today + timedelta(days=1)
    today_items, today_warnings = _home_board_items(sources, today, now)
    tomorrow_items, tomorrow_warnings = _home_board_items(sources, tomorrow, now)
    return {"today": today_items, "tomorrow": tomorrow_items}, [*today_warnings, *tomorrow_warnings]


def _decision_due_label(decision: dict[str, Any], today: date) -> str:
    due = _parse_date(_clean_text(decision.get("due")))
    if due is None:
        return "No due date"
    days = (due - today).days
    if days < 0:
        return f"{abs(days)}d overdue"
    if days == 0:
        return "Due today"
    if days == 1:
        return "Due tomorrow"
    return f"Due in {days}d"


def _decision_missing_fields(decision: dict[str, Any]) -> list[str]:
    missing = []
    if _clean_text(decision.get("owner"), "unknown") == "unknown":
        missing.append("owner")
    if not _clean_text(decision.get("due")):
        missing.append("timeline")
    if not decision.get("options"):
        missing.append("options")
    open_steps = [
        step for step in decision.get("next_steps") or []
        if _clean_text(step.get("status"), "open") == "open"
    ]
    if not open_steps:
        missing.append("next step")
    return missing


def _normalize_decision(decision: dict[str, Any], today: date) -> dict[str, Any]:
    open_steps = [
        step for step in decision.get("next_steps") or []
        if _clean_text(step.get("status"), "open") == "open"
    ]
    next_step = open_steps[0] if open_steps else {}
    owner = _clean_text(decision.get("owner"), "unknown")
    return {
        "id": _clean_text(decision.get("id")),
        "short_id": _clean_text(decision.get("id"))[:8],
        "title": _clean_text(decision.get("title"), "Untitled decision"),
        "context": _clean_text(decision.get("context")),
        "status": _clean_text(decision.get("status"), "inbox"),
        "owner": owner,
        "owner_label": "Unassigned" if owner == "unknown" else owner.title(),
        "urgency": _clean_text(decision.get("urgency"), "normal"),
        "size": _clean_text(decision.get("size"), "small"),
        "due": _clean_text(decision.get("due")),
        "due_label": _decision_due_label(decision, today),
        "option_count": len(decision.get("options") or []),
        "evidence_count": len(decision.get("evidence") or []),
        "next_step": _clean_text(next_step.get("text"), "Assign one clear next step"),
        "next_step_owner": _clean_text(next_step.get("owner"), "unknown"),
        "next_step_due": _clean_text(next_step.get("due")),
        "missing_fields": _decision_missing_fields(decision),
        "outcome": _clean_text(decision.get("outcome")),
        "updated_at": _clean_text(decision.get("updated_at")),
    }


def _normalize_backlog_item(
    item: dict[str, Any],
    today: date,
    events_by_id: dict[str, dict[str, Any]],
    tasks_by_id: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    kind = _clean_text(item.get("kind"), "discussion").lower()
    target_date = _parse_date(item.get("review_on") if kind == "discussion" else item.get("due"))
    days_until = (target_date - today).days if target_date is not None else None
    updated_on = _parse_date(_clean_text(item.get("updated_at")))
    stale = kind == "discussion" and updated_on is not None and (today - updated_on).days >= 14
    links = []
    completed_tasks = 0
    task_links = 0
    missing_sources = []
    for link in item.get("links") or []:
        source_type = _clean_text(link.get("source_type"))
        external_id = _clean_text(link.get("external_id"))
        container_id = _clean_text(link.get("container_id"), "@default")
        source = None
        if source_type == "calendar_event":
            source = events_by_id.get(external_id)
        elif source_type == "google_task":
            task_links += 1
            source = tasks_by_id.get((container_id, external_id)) or tasks_by_id.get(("@default", external_id))
            if source and source.get("status") == "completed":
                completed_tasks += 1
        if source is None:
            missing_sources.append(_clean_text(link.get("title"), external_id))
        links.append(
            {
                "id": _clean_text(link.get("id")),
                "source_type": source_type,
                "external_id": external_id,
                "container_id": container_id,
                "title": _clean_text(link.get("title") or (source or {}).get("title"), external_id),
                "available": source is not None,
                "completed": bool(source and source.get("status") == "completed"),
            },
        )

    status = _clean_text(item.get("status"), "open")
    blocked = status == "blocked" or bool(missing_sources)
    ready_to_close = kind == "planning" and (
        (task_links > 0 and completed_tasks == task_links)
        or (target_date is not None and days_until is not None and days_until < 0)
    )
    notes = item.get("notes") or []
    positions = item.get("positions") or []
    options = item.get("options") or []
    evidence = item.get("evidence") or []
    open_steps = [step for step in item.get("next_steps") or [] if step.get("status") == "open"]
    missing_fields = []
    if kind == "decision":
        if _clean_text(item.get("owner"), "unknown") == "unknown":
            missing_fields.append("owner")
        if not item.get("due"):
            missing_fields.append("timeline")
        if not options:
            missing_fields.append("options")
        if not open_steps:
            missing_fields.append("next step")
    has_calendar_link = any(link["source_type"] == "calendar_event" for link in links)
    incomplete = bool(missing_fields) or (
        kind == "planning" and (target_date is None or not has_calendar_link)
    )
    owner = _clean_text(item.get("owner"), "unknown")
    if days_until is None:
        date_label = "Needs review date" if kind == "discussion" else "Needs date"
    elif days_until < 0:
        date_label = f"{abs(days_until)}d overdue"
    elif days_until == 0:
        date_label = "Today"
    elif days_until == 1:
        date_label = "Tomorrow"
    else:
        date_label = f"In {days_until}d"
    return {
        "id": _clean_text(item.get("id")),
        "short_id": _clean_text(item.get("id"))[:8],
        "kind": kind,
        "title": _clean_text(item.get("title"), "Untitled item"),
        "context": _clean_text(item.get("context")),
        "status": status,
        "owner": owner,
        "owner_label": "Unassigned" if owner == "unknown" else owner.title(),
        "urgency": _clean_text(item.get("urgency"), "normal"),
        "size": _clean_text(item.get("size"), "small"),
        "priority": int(item.get("priority") or 0),
        "pinned": bool(item.get("pinned")),
        "review_on": _clean_text(item.get("review_on")),
        "due": _clean_text(item.get("due")),
        "date_label": date_label,
        "due_label": date_label,
        "days_until": days_until,
        "stale": stale,
        "blocked": blocked,
        "incomplete": incomplete,
        "ready_to_close": ready_to_close,
        "notes": notes,
        "positions": positions,
        "links": links,
        "missing_sources": missing_sources,
        "option_count": len(options),
        "evidence_count": len(evidence),
        "next_step": _clean_text(
            (open_steps[0] if open_steps else {}).get("text"),
            "Assign one clear next step" if kind == "decision" else "",
        ),
        "next_step_owner": _clean_text((open_steps[0] if open_steps else {}).get("owner"), "unknown"),
        "next_step_due": _clean_text((open_steps[0] if open_steps else {}).get("due")),
        "missing_fields": missing_fields,
        "outcome": _clean_text(item.get("outcome")),
        "updated_at": _clean_text(item.get("updated_at")),
        "tracked": True,
    }


def _backlog_priority_key(item: dict[str, Any]) -> tuple[Any, ...]:
    days = item.get("days_until")
    due_attention = days is not None and days <= 7
    urgency = {"critical": 0, "high": 1, "normal": 2, "low": 3}.get(item["urgency"], 2)
    return (
        not item["pinned"],
        not due_attention,
        urgency,
        not item["blocked"],
        not item["incomplete"],
        not item["stale"],
        days is None,
        days if days is not None else 10_000,
        -item["priority"],
        item["title"].lower(),
    )


def _family_backlog(
    sources: DashboardSources,
    today: date,
    events: list[dict[str, Any]],
    all_tasks: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    empty_lanes = {"discussion": [], "planning": [], "decision": []}
    tools = getattr(sources, "decision_tools", None)
    if tools is None:
        return {"counts": {kind: 0 for kind in empty_lanes}, "attention": [], "lanes": empty_lanes}, []
    try:
        if hasattr(tools, "list_backlog_items"):
            response = tools.list_backlog_items()
            raw_items = response.get("data", {}).get("items") or []
        else:
            response = tools.list_decisions(include_decided=False)
            raw_items = [{**item, "kind": "decision"} for item in response.get("data", {}).get("decisions") or []]
    except Exception as error:
        return {"counts": {kind: 0 for kind in empty_lanes}, "attention": [], "lanes": empty_lanes}, [
            f"Backlog source unavailable: {error.__class__.__name__}.",
        ]
    if response.get("status") != "ok":
        return {"counts": {kind: 0 for kind in empty_lanes}, "attention": [], "lanes": empty_lanes}, [
            response.get("message", "Backlog source unavailable."),
        ]

    events_by_id = {event["id"]: event for event in events}
    tasks_by_id = {
        (_clean_text(task.get("task_list_id"), "@default"), task["id"]): task
        for task in all_tasks
    }
    lanes = {kind: [] for kind in empty_lanes}
    for raw_item in raw_items:
        item = _normalize_backlog_item(raw_item, today, events_by_id, tasks_by_id)
        if item["kind"] in lanes:
            lanes[item["kind"]].append(item)
    for lane in lanes.values():
        lane.sort(key=_backlog_priority_key)
    all_items = [item for kind in ("discussion", "planning", "decision") for item in lanes[kind]]
    attention = [
        item for item in sorted(all_items, key=_backlog_priority_key)
        if item["pinned"] or item["blocked"] or item["stale"] or (item["days_until"] is not None and item["days_until"] <= 0)
    ][:5]
    return {
        "counts": {kind: len(items) for kind, items in lanes.items()},
        "attention": attention,
        "review": {
            "available": True,
            "callout": today.weekday() in {0, 6},
            "item_ids": [item["id"] for item in sorted(all_items, key=_backlog_priority_key)],
        },
        "lanes": lanes,
    }, []


def _open_decisions(
    sources: DashboardSources,
    today: date,
) -> tuple[list[dict[str, Any]], list[str]]:
    tools = getattr(sources, "decision_tools", None)
    if tools is None:
        return [], []

    try:
        response = tools.list_decisions(include_decided=False)
    except Exception as error:
        return [], [f"Decisions source unavailable: {error.__class__.__name__}."]

    if response.get("status") != "ok":
        return [], [response.get("message", "Decisions source unavailable.")]

    decisions = [
        _normalize_decision(decision, today)
        for decision in response.get("data", {}).get("decisions") or []
    ]
    decisions.sort(
        key=lambda decision: (
            {"critical": 0, "high": 1, "normal": 2, "low": 3}.get(decision["urgency"], 2),
            decision["due"] == "",
            decision["due"],
            -len(decision["missing_fields"]),
            decision["title"].lower(),
        ),
    )
    return decisions, []


def _source_error_response(label: str, error: Exception) -> dict[str, Any]:
    return {
        "status": "error",
        "message": f"{label} source unavailable: {error.__class__.__name__}.",
        "data": {"error_type": error.__class__.__name__},
    }


def _planning_items(events: list[dict[str, Any]], tasks: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    important_words = {
        "trip",
        "travel",
        "flight",
        "school",
        "passport",
        "paperwork",
        "birthday",
        "medical",
        "doctor",
        "dentist",
        "appointment",
    }
    items = []
    for event in events:
        text_tokens = _tokens(" ".join([event["title"], event["category"], event["preparation_notes"]]))
        if not (text_tokens & important_words or event["preparation_needed"]):
            continue
        start_date = date.fromisoformat(event["start_date"])
        if start_date < today:
            continue
        action_items = _matching_action_items(event, tasks)
        items.append(
            {
                "title": event["title"],
                "date": event["start_date"],
                "date_label": event["day_label"],
                "days_until": (start_date - today).days,
                "owner": event["owner"],
                "owner_label": event["owner_label"],
                "person": event["person"],
                "category": event["category"] or "planning",
                "prep_needed": event["preparation_needed"],
                "prep_notes": event["preparation_notes"],
                "prep_progress": _progress_from_metadata(event["metadata"]),
                "action_items": [_public_task(task) for task in action_items],
            },
        )
    items.sort(key=lambda item: (item["days_until"], item["title"].lower()))
    return items[:8]


def _family_awareness(
    today_events: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    member_names = set()
    for event in today_events:
        if event["owner"] != "unknown":
            member_names.add(event["owner"])
        if event["person"] and event["person"].lower() != "family":
            member_names.add(event["person"].lower())
    for task in tasks:
        if task["owner"] != "unknown":
            member_names.add(task["owner"])

    responsibilities = []
    for event in today_events:
        if event["owner"] != "unknown":
            responsibilities.append(
                {
                    "owner": event["owner"],
                    "title": event["title"],
                    "detail": event["time_label"],
                    "kind": "event",
                },
            )
    for task in tasks:
        if task["owner"] != "unknown" and task["days_until_due"] is not None and task["days_until_due"] <= 1:
            responsibilities.append(
                {
                    "owner": task["owner"],
                    "title": task["title"],
                    "detail": task["due_label"],
                    "kind": "task",
                },
            )

    child_events = [
        event
        for event in today_events
        if event["person"].lower() not in ("", "family")
        or event["category"] in ("school", "activity", "medical")
    ]
    unassigned = [
        {"title": event["title"], "detail": event["time_label"], "kind": "event"}
        for event in today_events
        if event["owner"] == "unknown" and (event["preparation_needed"] or event["person"].lower() != "family")
    ]
    unassigned.extend(
        {
            "title": task["title"],
            "detail": task["due_label"],
            "kind": "task",
        }
        for task in tasks
        if task["owner"] == "unknown" and task["days_until_due"] is not None and task["days_until_due"] <= 7
    )
    prep_gaps = [
        {
            "title": event["title"],
            "detail": event["preparation_notes"] or "Preparation details missing.",
        }
        for event in today_events
        if event["preparation_needed"]
    ]
    return {
        "members": [
            {
                "name": name.title(),
                "responsibility_count": sum(1 for item in responsibilities if item["owner"] == name),
            }
            for name in sorted(member_names)
        ],
        "responsibilities": responsibilities[:8],
        "child_events": child_events[:6],
        "unassigned": unassigned[:8],
        "prep_gaps": prep_gaps[:8],
    }


def _task_groups(recommendations: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def public_recommendation(recommendation: dict[str, Any]) -> dict[str, Any]:
        return {
            **recommendation,
            "task": _public_task(recommendation["task"]),
        }

    def group_matches(kind: str) -> list[dict[str, Any]]:
        matches = []
        for recommendation in recommendations:
            task = recommendation["task"]
            values = set(task["context"]) | set(task["requires"]) | set(task["can_do_while"])
            if kind == "calls" and ("phone" in values or task["effort_type"] == "communication"):
                matches.append(recommendation)
            elif kind == "computer" and ("computer" in values or "internet" in values):
                matches.append(recommendation)
            elif kind == "low_energy" and task["energy"] in ("low", "unknown"):
                matches.append(recommendation)
            elif kind == "physical" and task["effort_type"] == "physical":
                matches.append(recommendation)
            elif kind == "paperwork" and (task["effort_type"] in ("paperwork", "admin") or "paperwork" in values):
                matches.append(recommendation)
        return [public_recommendation(recommendation) for recommendation in matches[:3]]

    fallback_recommendations = [
        {"task": _public_task(task), "reasons": ["available"], "score": 0}
        for task in tasks
    ]
    if not recommendations:
        recommendations = fallback_recommendations
    return [
        {
            "label": "Calls while driving",
            "detail": "Phone-ready work for car or commute windows.",
            "items": group_matches("calls"),
        },
        {
            "label": "Computer tasks",
            "detail": "Best when a laptop and internet are available.",
            "items": group_matches("computer"),
        },
        {
            "label": "Low-energy tasks",
            "detail": "Small admin loops that do not need deep focus.",
            "items": group_matches("low_energy"),
        },
        {
            "label": "Physical home tasks",
            "detail": "Home or equipment-based tasks.",
            "items": group_matches("physical"),
        },
        {
            "label": "Paperwork tasks",
            "detail": "Forms, documents, renewals, and prep packets.",
            "items": group_matches("paperwork"),
        },
    ]


def _empty_dashboard(now: datetime, message: str = "") -> dict[str, Any]:
    return {
        "generated_at": now.isoformat(),
        "date_label": now.strftime("%A, %B %-d"),
        "greeting": "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 18 else "Good evening",
        "source_status": "empty",
        "source_message": message,
        "summary": {
            "open_loop_count": 0,
            "prep_needed_count": 0,
            "conflict_count": 0,
            "unassigned_count": 0,
            "home_board_count": 0,
            "open_decision_count": 0,
            "shopping_count": 0,
            "homework_count": 0,
        },
        "best_next_action": {
            "title": "No dashboard data yet",
            "source": "empty",
            "why": message or "Calendar and task sources returned no data.",
            "reasons": ["check Google Calendar and Tasks sync"],
            "available_minutes": None,
            "next_event": "",
        },
        "warnings": [],
        "calendar": {
            "today": [],
            "tomorrow": [],
            "next_7_days": [],
            "conflicts": [],
            "prep_needed": [],
            "busy_day": _busy_day_summary([]),
        },
        "tasks": {
            "urgent": [],
            "due_soon": [],
            "recommended": [],
            "groups": [],
            "open_loops": [],
            "pending": [],
            "available": False,
            "message": message or "Task source unavailable.",
        },
        "planning": {"items": []},
        "backlog": {
            "counts": {"discussion": 0, "planning": 0, "decision": 0},
            "attention": [],
            "review": {"available": True, "callout": now.weekday() in {0, 6}, "item_ids": []},
            "lanes": {"discussion": [], "planning": [], "decision": []},
        },
        "home_board": {"today": [], "tomorrow": []},
        "decisions": {"open": [], "attention": []},
        "shopping": {"lists": [], "pending": [], "by_list": []},
        "homework": _empty_homework(),
        "reading_garden": _empty_reading_garden(now.date()),
        "family": {
            "members": [],
            "responsibilities": [],
            "child_events": [],
            "unassigned": [],
            "prep_gaps": [],
        },
    }


def _empty_homework() -> dict[str, Any]:
    return {
        "available": False,
        "message": "Homework source unavailable.",
        "children": [
            {"child": child, "open_count": 0, "due_now_count": 0, "items": [], "classes": []}
            for child in HOMEWORK_CHILDREN
        ],
        "classes": [],
        "upcoming": [],
        "open_count": 0,
        "due_now_count": 0,
        "learning": {"recent": [], "observation_count": 0},
    }


def _empty_reading_garden(today: date) -> dict[str, Any]:
    def child_empty(child: str) -> dict[str, Any]:
        return {
            "title": f"{child}'s Reading Garden",
            "child": child,
            "today": {"read": False, "label": "Not yet today"},
            "current_book": "unknown book",
            "week": {"reading_moments": 0, "reading_days": 0, "pages": 0, "minutes": 0},
            "weekly_goal": {
                "target_days": 5,
                "reading_days": 0,
                "remaining_days": 5,
                "progress": 0,
                "percent": 0,
                "label": "0 of 5 reading days",
            },
            "streaks": {"current": 0, "best": 0, "grace_days": 1},
            "finished": {"count": 0, "recent_books": []},
            "favorite_reaction": "",
            "recent_photos": [],
            "garden": {"sprouts": 0, "leaves": 0, "flowers": 0, "butterflies": 0},
            "badges": [],
            "history": {
                "heatmap": [],
                "monthly": {
                    "reading_days": 0,
                    "moments": 0,
                    "pages": 0,
                    "minutes": 0,
                    "finished_books": 0,
                },
            },
            "book_collection": [],
            "recent_events": [],
            "library_visit": {
                "has_visit": False,
                "last_visit_date": "",
                "days_since_visit": None,
                "state": "empty",
                "label": "Paste a library checkout email to start your library bag.",
                "due_date": "",
            },
            "current_bag": {"count": 0, "titles": [], "due_date": ""},
        }

    by_child = {child: child_empty(child) for child in ("Nysha", "Navya")}
    family = child_empty("Family")
    return {
        **by_child["Nysha"],
        "title": "Reading Garden",
        "today": {"read": False, "label": "Not yet today"},
        "children": ["Nysha", "Navya"],
        "by_child": by_child,
        "family": family,
        "selected_child": "Nysha",
    }


def _reading_garden_summary(
    sources: DashboardSources,
    local_now: datetime,
) -> tuple[dict[str, Any], list[str]]:
    tools = sources.reading_garden_tools
    if tools is None:
        return _empty_reading_garden(local_now.date()), []
    try:
        response = tools.status(now=local_now)
    except Exception as error:
        response = _source_error_response("Reading Garden", error)

    if response.get("status") != "ok":
        return (
            _empty_reading_garden(local_now.date()),
            [response.get("message", "Reading Garden source unavailable.")],
        )
    summary = response.get("data", {}).get("summary")
    if not isinstance(summary, dict):
        return _empty_reading_garden(local_now.date()), []
    return summary, []


def _normalize_homework_item(item: dict[str, Any], today: date) -> dict[str, Any]:
    due_date = _parse_date(_clean_text(item.get("due_date")) or None)
    days_until_due = (due_date - today).days if due_date is not None else None
    due_label = "No due date" if due_date is None else _format_date_label(due_date)
    if days_until_due is not None:
        if days_until_due < 0:
            due_label = f"Overdue by {abs(days_until_due)}d"
        elif days_until_due == 0:
            due_label = "Due today"
        elif days_until_due == 1:
            due_label = "Due tomorrow"
    return {
        "id": _clean_text(item.get("id")),
        "child": _clean_text(item.get("child"), "Unknown"),
        "title": _clean_text(item.get("title"), "Homework"),
        "class_name": _clean_text(item.get("subject"), "Unsorted"),
        "assigned_date": _clean_text(item.get("assigned_date")),
        "due_date": _clean_text(item.get("due_date")),
        "due_label": due_label,
        "days_until_due": days_until_due,
        "status": _clean_text(item.get("status"), "assigned"),
        "notes": _clean_text(item.get("notes")),
        "grade": _clean_text(item.get("grade")),
        "week_range": _clean_text(item.get("week_range")),
        "record_type": _clean_text(item.get("record_type"), "homework"),
        "parent_notes": _clean_text(item.get("parent_notes")),
    }


def _homework_summary(sources: DashboardSources, today: date) -> tuple[dict[str, Any], list[str]]:
    tools = getattr(sources, "homework_tools", None)
    if tools is None:
        return _empty_homework(), []

    children = []
    all_items = []
    warnings = []
    successful_children = 0
    for child in HOMEWORK_CHILDREN:
        try:
            response = tools.list_homework(child=child, limit=30)
        except Exception as error:
            warnings.append(f"{child} homework source unavailable: {error.__class__.__name__}.")
            children.append({"child": child, "open_count": 0, "due_now_count": 0, "items": [], "classes": []})
            continue
        if response.get("status") != "ok":
            warnings.append(response.get("message", f"{child} homework source unavailable."))
            children.append({"child": child, "open_count": 0, "due_now_count": 0, "items": [], "classes": []})
            continue
        successful_children += 1

        child_items = [
            _normalize_homework_item(item, today)
            for item in response.get("data", {}).get("items") or []
        ]
        all_items.extend(child_items)
        by_class: dict[str, list[dict[str, Any]]] = {}
        for item in child_items:
            by_class.setdefault(item["class_name"], []).append(item)
        classes = [
            {"child": child, "class_name": class_name, "open_count": len(items), "items": items[:5]}
            for class_name, items in sorted(by_class.items())
        ]
        children.append(
            {
                "child": child,
                "open_count": len(child_items),
                "due_now_count": len(
                    [item for item in child_items if item["days_until_due"] is not None and item["days_until_due"] <= 0]
                ),
                "items": child_items[:8],
                "classes": classes,
            },
        )

    all_items.sort(
        key=lambda item: (
            item["days_until_due"] is None,
            item["days_until_due"] if item["days_until_due"] is not None else 9999,
            item["child"],
            item["class_name"],
            item["title"],
        ),
    )
    class_counts: dict[tuple[str, str], int] = {}
    for item in all_items:
        key = (item["child"], item["class_name"])
        class_counts[key] = class_counts.get(key, 0) + 1
    available = successful_children > 0
    summary = {
        "available": available,
        "partial": bool(warnings) and available,
        "message": "Live homework data" if not warnings else "Some homework data is unavailable.",
        "children": children,
        "classes": [
            {"child": child, "class_name": class_name, "open_count": count}
            for (child, class_name), count in sorted(class_counts.items())
        ],
        "upcoming": all_items[:10],
        "open_count": len(all_items),
        "due_now_count": len(
            [item for item in all_items if item["days_until_due"] is not None and item["days_until_due"] <= 0]
        ),
        "learning": {"recent": [], "observation_count": 0},
    }
    provider = getattr(tools, "provider", None)
    if provider is not None:
        try:
            recent = []
            observations = []
            for child in HOMEWORK_CHILDREN:
                recent.extend(_normalize_homework_item(item, today) for item in provider.list_items(child=child, limit=8))
                observations.extend(provider.list_learning_observations(child=child))
            recent.sort(key=lambda item: (item["assigned_date"], item["title"]), reverse=True)
            summary["learning"] = {"recent": recent[:8], "observation_count": len([item for item in observations if item.get("status") == "active"])}
        except Exception:
            pass
    return summary, warnings


def _normalize_shopping_item(item: dict[str, Any], list_slug: str, list_name: str) -> dict[str, Any]:
    title = _clean_text(item.get("title") or item.get("name") or item.get("item"), "Untitled item")
    return {
        "id": _clean_text(item.get("id") or item.get("item_id")),
        "title": title,
        "quantity": _clean_text(item.get("quantity")),
        "note": _clean_text(item.get("note")),
        "category": _clean_text(item.get("category")),
        "checked": bool(item.get("checked") or item.get("completed") or item.get("is_checked")),
        "list_slug": _clean_text(item.get("list_slug"), list_slug),
        "list_name": list_name,
        "updated_at": _clean_text(item.get("updated_at")),
    }


def _shopping_lists(sources: DashboardSources) -> tuple[dict[str, Any], list[str]]:
    tools = getattr(sources, "shopping_tools", None)
    empty = {"lists": [], "pending": [], "by_list": []}
    if tools is None:
        return empty, []

    try:
        list_response = tools.list_shopping_lists()
    except Exception as error:
        return empty, [f"Shopping source unavailable: {error.__class__.__name__}."]

    if list_response.get("status") != "ok":
        return empty, [list_response.get("message", "Shopping source unavailable.")]

    configured_lists = []
    seen_slugs = set()
    for row in list_response.get("data", {}).get("lists") or []:
        slug = _clean_text(row.get("slug") or row.get("id"))
        if not slug or slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        configured_lists.append(
            {
                "slug": slug,
                "name": _clean_text(row.get("name"), SHOPPING_LIST_LABELS.get(slug, slug)),
            },
        )
    for slug, label in SHOPPING_LIST_LABELS.items():
        if slug not in seen_slugs:
            configured_lists.append({"slug": slug, "name": label})

    by_list = []
    pending = []
    warnings = []
    for shopping_list in configured_lists:
        slug = shopping_list["slug"]
        name = shopping_list["name"]
        try:
            items_response = tools.list_items(slug, include_checked=False)
        except Exception as error:
            warnings.append(f"{name} shopping list unavailable: {error.__class__.__name__}.")
            continue
        if items_response.get("status") != "ok":
            warnings.append(items_response.get("message", f"{name} shopping list unavailable."))
            continue
        items = [
            _normalize_shopping_item(item, slug, name)
            for item in items_response.get("data", {}).get("items") or []
        ]
        by_list.append({**shopping_list, "items": items, "pending_count": len(items)})
        pending.extend(items)

    return {"lists": configured_lists, "pending": pending, "by_list": by_list}, warnings


def build_dashboard_data(
    sources: DashboardSources,
    now: datetime | None = None,
) -> dict[str, Any]:
    local_now = _local_now(now)
    timezone = ZoneInfo(DEFAULT_TIMEZONE)
    today_start = _start_of_day(local_now)
    tomorrow_start = today_start + timedelta(days=1)
    next_week_end = today_start + timedelta(days=7)
    planning_end = today_start + timedelta(days=90)

    source_warnings = []

    try:
        calendar_response = sources.calendar_tools.list_calendar_events(
            time_min=today_start.isoformat(),
            time_max=planning_end.isoformat(),
            max_results=100,
        )
    except Exception as error:
        calendar_response = _source_error_response("Calendar", error)

    try:
        task_response = _list_dashboard_tasks(sources)
    except Exception as error:
        task_response = _source_error_response("Tasks", error)

    home_board, home_board_warnings = _home_board_for_portal(
        sources,
        local_now.date(),
        local_now,
    )
    source_warnings.extend(home_board_warnings)
    open_decisions, decision_warnings = _open_decisions(
        sources,
        local_now.date(),
    )
    source_warnings.extend(decision_warnings)
    shopping, shopping_warnings = _shopping_lists(sources)
    source_warnings.extend(shopping_warnings)
    homework, homework_warnings = _homework_summary(sources, local_now.date())
    source_warnings.extend(homework_warnings)
    reading_garden, reading_warnings = _reading_garden_summary(sources, local_now)
    source_warnings.extend(reading_warnings)

    if calendar_response.get("status") != "ok":
        calendar_unavailable = True
        source_warnings.append(calendar_response.get("message", "Calendar source unavailable."))
        raw_events: list[dict[str, Any]] = []
    else:
        calendar_unavailable = False
        raw_events = list(calendar_response.get("data", {}).get("events") or [])

    if task_response.get("status") != "ok":
        tasks_unavailable = True
        task_source_message = task_response.get("message", "Task source unavailable.")
        source_warnings.append(task_source_message)
        raw_tasks: list[dict[str, Any]] = []
    else:
        tasks_unavailable = bool(task_response.get("data", {}).get("unavailable"))
        task_source_message = "Live task data"
        raw_tasks = list(task_response.get("data", {}).get("tasks") or [])
        source_warnings.extend(str(warning) for warning in task_response.get("data", {}).get("warnings") or [])
    task_lists = (
        []
        if task_response.get("data", {}).get("unavailable")
        else _task_list_entries(task_response)
    )

    events = [
        _normalize_event(event, sources.read_event_metadata, timezone)
        for event in raw_events
    ]
    events.sort(key=lambda event: event["_start_dt"])
    all_tasks = [
        {
            **_normalize_task(task, sources.read_task_metadata, local_now.date()),
            "task_list_id": _clean_text(task.get("task_list_id"), "@default"),
            "task_list_title": _clean_text(task.get("task_list_title"), "My Tasks"),
            "raw": task,
        }
        for task in raw_tasks
    ]
    tasks = [task for task in all_tasks if task["status"] != "completed"]
    tasks.sort(key=_task_priority_key)

    today_events = [event for event in events if _event_overlaps(event, today_start, tomorrow_start)]
    tomorrow_events = [
        event
        for event in events
        if _event_overlaps(event, tomorrow_start, tomorrow_start + timedelta(days=1))
    ]
    next_7_days = [event for event in events if _event_overlaps(event, today_start, next_week_end)]
    prep_window = any(event["preparation_needed"] for event in today_events)
    available_minutes, _ = _free_minutes_until_next_event(today_events, local_now)
    filters = _decision_filters(available_minutes, prep_window)
    recommendations = _recommend_tasks(sources, tasks, filters, limit=8)
    best_next_action = _make_best_next_action(today_events, tasks, recommendations, local_now)
    if calendar_unavailable and tasks_unavailable:
        best_next_action = {
            "title": "Reconnect Google sources",
            "source": "source-warning",
            "why": "Calendar and task data are not available right now.",
            "reasons": source_warnings[:3],
            "available_minutes": None,
            "next_event": "",
        }

    conflicts = _find_conflicts(today_events)
    busy_day = _busy_day_summary(today_events)
    urgent_tasks = [
        task
        for task in tasks
        if task["urgency"] == "high" or (task["days_until_due"] is not None and task["days_until_due"] <= 0)
    ][:6]
    due_soon_tasks = [
        task
        for task in tasks
        if task["days_until_due"] is not None and 0 <= task["days_until_due"] <= 7
    ][:8]
    prep_needed = [event for event in next_7_days if event["preparation_needed"]][:8]
    open_loops = urgent_tasks[:]
    open_loops.extend(
        task
        for task in due_soon_tasks
        if task["id"] not in {existing["id"] for existing in open_loops}
    )
    open_loops = open_loops[:8]
    pending_tasks = tasks
    task_tags = sorted({tag for task in pending_tasks for tag in task["tags"]})
    task_list_counts: dict[str, int] = {}
    for task in tasks:
        task_list_id = _clean_text(task.get("task_list_id"), "@default")
        task_list_counts[task_list_id] = task_list_counts.get(task_list_id, 0) + 1
    task_owner_counts: dict[tuple[str, str], int] = {}
    task_owner_today_counts: dict[tuple[str, str], int] = {}
    for task in pending_tasks:
        owner_key = (task["owner"], task["owner_label"])
        task_owner_counts[owner_key] = task_owner_counts.get(owner_key, 0) + 1
        if task["days_until_due"] == 0:
            task_owner_today_counts[owner_key] = task_owner_today_counts.get(owner_key, 0) + 1
    task_owners = sorted(
        task_owner_counts,
        key=lambda item: (item[0] == "unknown", item[1].lower()),
    )

    warnings = [
        {"level": "warning", "title": conflict["title"], "detail": conflict["detail"]}
        for conflict in conflicts
    ]
    if busy_day["overloaded"]:
        warnings.append(
            {
                "level": "warning",
                "title": "Overloaded day",
                "detail": busy_day["label"],
            },
        )
    warnings.extend(
        {"level": "info", "title": "Source warning", "detail": warning}
        for warning in source_warnings
    )

    family = _family_awareness(today_events, tasks)
    backlog, backlog_warnings = _family_backlog(
        sources,
        local_now.date(),
        events,
        all_tasks,
    )
    source_warnings.extend(backlog_warnings)
    warnings.extend(
        {"level": "info", "title": "Source warning", "detail": warning}
        for warning in backlog_warnings
    )
    planning_suggestions = _planning_items(events, tasks, local_now.date())
    planning_items = [
        *backlog["lanes"]["planning"],
        *[{**item, "tracked": False} for item in planning_suggestions],
    ]
    open_decisions = backlog["lanes"]["decision"]
    unassigned_count = len(family["unassigned"])
    attention_decisions = [
        decision for decision in open_decisions
        if decision["missing_fields"] or decision["urgency"] in ("critical", "high")
    ][:6]

    def public_event(event: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in event.items() if not key.startswith("_")}

    return {
        "generated_at": local_now.isoformat(),
        "date_label": local_now.strftime("%A, %B %-d"),
        "greeting": "Good morning" if local_now.hour < 12 else "Good afternoon" if local_now.hour < 18 else "Good evening",
        "source_status": "ok" if not source_warnings else "partial",
        "source_message": "Live read-only data" if not source_warnings else "Some sources are unavailable.",
        "summary": {
            "open_loop_count": len(open_loops),
            "prep_needed_count": len(prep_needed),
            "conflict_count": len(conflicts),
            "unassigned_count": unassigned_count,
            "home_board_count": len(home_board["today"]),
            "open_decision_count": len(open_decisions),
            "shopping_count": len(shopping["pending"]),
            "homework_count": homework["open_count"],
        },
        "best_next_action": best_next_action,
        "warnings": warnings[:8],
        "calendar": {
            "today": [public_event(event) for event in today_events],
            "tomorrow": [public_event(event) for event in tomorrow_events],
            "next_7_days": [public_event(event) for event in next_7_days],
            "conflicts": conflicts,
            "prep_needed": [public_event(event) for event in prep_needed],
            "busy_day": busy_day,
        },
        "tasks": {
            "urgent": [_public_task(task) for task in urgent_tasks],
            "due_soon": [_public_task(task) for task in due_soon_tasks],
            "pending": [_public_task(task) for task in pending_tasks],
            "available": not tasks_unavailable,
            "message": task_source_message,
            "lists": [
                {
                    "id": task_list["id"],
                    "title": task_list["title"],
                    "count": task_list_counts.get(task_list["id"], 0),
                }
                for task_list in task_lists
            ],
            "tags": task_tags,
            "owners": [
                {
                    "owner": owner,
                    "label": label,
                    "count": task_owner_counts[(owner, label)],
                    "today_count": task_owner_today_counts.get((owner, label), 0),
                }
                for owner, label in task_owners
            ],
            "recommended": [
                {
                    "task": _public_task(recommendation["task"]),
                    "score": recommendation["score"],
                    "reasons": recommendation["reasons"],
                }
                for recommendation in recommendations
            ],
            "groups": _task_groups(recommendations, tasks),
            "open_loops": [_public_task(task) for task in open_loops],
        },
        "planning": {"items": planning_items},
        "backlog": backlog,
        "home_board": home_board,
        "decisions": {
            "open": open_decisions[:12],
            "attention": attention_decisions,
        },
        "shopping": shopping,
        "homework": homework,
        "reading_garden": reading_garden,
        "family": {
            "members": family["members"],
            "responsibilities": family["responsibilities"],
            "child_events": [public_event(event) for event in family["child_events"]],
            "unassigned": family["unassigned"],
            "prep_gaps": family["prep_gaps"],
        },
    }


def get_dashboard_data(now: datetime | None = None) -> dict[str, Any]:
    local_now = _local_now(now)
    try:
        sources = default_sources()
        return build_dashboard_data(sources, local_now)
    except Exception as error:
        message = f"Dashboard sources unavailable: {error.__class__.__name__}."
        data = _empty_dashboard(local_now, message)
        try:
            home_board_sources = DashboardSources(
                calendar_tools=None,
                task_tools=None,
                read_event_metadata=lambda event: ("", {}),
                read_task_metadata=lambda notes: ("", {}),
                recommend_task_matches=lambda tasks, filters, limit: [],
                home_board_tools=build_default_home_board_tools(),
                decision_tools=None,
                shopping_tools=build_default_shopping_tools(),
                reading_garden_tools=None,
                homework_tools=build_default_homework_tools(),
            )
            home_board, warnings = _home_board_for_portal(
                home_board_sources,
                local_now.date(),
                local_now,
            )
            shopping, shopping_warnings = _shopping_lists(home_board_sources)
            homework, homework_warnings = _homework_summary(home_board_sources, local_now.date())
        except Exception:
            return data

        data["home_board"] = home_board
        data["summary"]["home_board_count"] = len(home_board["today"])
        data["shopping"] = shopping
        data["summary"]["shopping_count"] = len(shopping["pending"])
        data["homework"] = homework
        data["summary"]["homework_count"] = homework["open_count"]
        data["warnings"].extend(
            {"level": "info", "title": "Source warning", "detail": warning}
            for warning in [*warnings, *shopping_warnings, *homework_warnings]
        )
        return data


def create_dashboard_backlog_item(
    *,
    kind: str | None,
    title: str | None,
    owner: str | None = None,
    priority: int | None = None,
    date_value: str | None = None,
    sources: DashboardSources | None = None,
) -> dict[str, Any]:
    cleaned_kind = _clean_text(kind).lower()
    cleaned_title = _clean_text(title)
    if cleaned_kind not in {"discussion", "planning", "decision"} or not cleaned_title:
        missing = []
        if cleaned_kind not in {"discussion", "planning", "decision"}:
            missing.append("kind")
        if not cleaned_title:
            missing.append("title")
        return {
            "status": "error",
            "message": "Missing or invalid backlog information: " + ", ".join(missing) + ".",
            "data": {"missing_fields": missing},
        }
    tools = (sources or default_sources()).decision_tools
    kwargs: dict[str, Any] = {
        "kind": cleaned_kind,
        "title": cleaned_title,
        "owner": _clean_text(owner, "unknown").lower(),
        "priority": priority or 0,
        "actor": "family dashboard",
    }
    if cleaned_kind == "discussion":
        kwargs["review_on"] = _clean_text(date_value) or None
    else:
        kwargs["due"] = _clean_text(date_value) or None
    try:
        return tools.create_backlog_item(**kwargs)
    except Exception as error:
        return _source_error_response("Backlog", error)


def perform_dashboard_backlog_action(
    *,
    action: str | None,
    item_id: str | None,
    payload: dict[str, Any] | None = None,
    sources: DashboardSources | None = None,
) -> dict[str, Any]:
    action_code = _clean_text(action).lower()
    cleaned_item_id = _clean_text(item_id)
    valid_actions = {
        "edit",
        "add_note",
        "set_position",
        "move",
        "pin",
        "park",
        "link_event",
        "link_task",
        "create_task",
        "close",
    }
    missing = []
    if action_code not in valid_actions:
        missing.append("action")
    if not cleaned_item_id:
        missing.append("item_id")
    if missing:
        return {
            "status": "error",
            "message": "Missing or invalid backlog action information: " + ", ".join(missing) + ".",
            "data": {"missing_fields": missing},
        }
    values = payload if isinstance(payload, dict) else {}
    active_sources = sources or default_sources()
    tools = active_sources.decision_tools
    actor = "family dashboard"
    try:
        if action_code == "edit":
            allowed = {key: values[key] for key in ("title", "context", "owner", "urgency", "review_on", "due", "priority") if key in values}
            return tools.update_backlog_item(cleaned_item_id, actor=actor, **allowed)
        if action_code == "add_note":
            return tools.add_backlog_note(cleaned_item_id, values.get("text"), actor=actor)
        if action_code == "set_position":
            return tools.set_backlog_position(cleaned_item_id, values.get("value"), actor=actor)
        if action_code == "move":
            if values.get("confirmed") is not True:
                return {
                    "status": "needs_confirmation",
                    "message": "Confirm moving this backlog item.",
                    "data": {"item_id": cleaned_item_id, "action": "move"},
                }
            return tools.move_backlog_item(cleaned_item_id, values.get("kind"), confirmed=True, actor=actor)
        if action_code == "pin":
            return tools.update_backlog_item(cleaned_item_id, pinned=bool(values.get("pinned")), actor=actor)
        if action_code == "park":
            return tools.park_backlog_item(cleaned_item_id, actor=actor)
        if action_code in {"link_event", "link_task"}:
            return tools.link_backlog_item(
                cleaned_item_id,
                source_type="calendar_event" if action_code == "link_event" else "google_task",
                external_id=values.get("external_id"),
                container_id=values.get("container_id"),
                title=values.get("title"),
                actor=actor,
            )
        if action_code == "create_task":
            task_title = _clean_text(values.get("title"))
            if not task_title:
                return {
                    "status": "error",
                    "message": "Missing required follow-up task information: title.",
                    "data": {"missing_fields": ["title"]},
                }
            task_response = active_sources.task_tools.create_task(
                title=task_title,
                notes=_clean_text(values.get("notes")) or None,
                due=_clean_text(values.get("due")) or None,
                metadata={"source": "family_backlog", "backlog_item_id": cleaned_item_id},
                task_list_id=_clean_text(values.get("container_id"), "@default"),
            )
            if task_response.get("status") != "ok":
                return task_response
            task = task_response.get("data", {}).get("task")
            if not isinstance(task, dict) or not _clean_text(task.get("id")):
                return {
                    "status": "error",
                    "message": "Task was created but did not include a linkable task id.",
                    "data": {"missing_fields": ["task.id"]},
                }
            linked_response = tools.link_backlog_item(
                cleaned_item_id,
                source_type="google_task",
                external_id=task.get("id"),
                container_id=_clean_text(values.get("container_id"), "@default"),
                title=task.get("title") or task_title,
                actor=actor,
            )
            if linked_response.get("status") != "ok":
                return linked_response
            return {
                **linked_response,
                "message": "Follow-up task created and linked.",
                "data": {**linked_response.get("data", {}), "task": task},
            }
        close_outcome = _clean_text(values.get("outcome"), "Closed from dashboard.")
        if values.get("confirmed") is not True:
            return {
                "status": "needs_confirmation",
                "message": "Confirm closing this backlog item.",
                "data": {"item_id": cleaned_item_id, "action": "close"},
            }
        return tools.close_backlog_item(
            cleaned_item_id,
            close_outcome,
            rationale=values.get("rationale"),
            confirmed=True,
            actor=actor,
        )
    except (TypeError, ValueError) as error:
        return {
            "status": "error",
            "message": f"Invalid backlog action: {error}",
            "data": {"error_type": error.__class__.__name__},
        }
    except Exception as error:
        return _source_error_response("Backlog", error)


def complete_dashboard_task(
    task_id: str | None,
    task_list_id: str | None = None,
    sources: DashboardSources | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    cleaned_task_id = _clean_text(task_id)
    if not cleaned_task_id:
        return {
            "status": "error",
            "message": "Missing required task information: task_id.",
            "data": {"missing_fields": ["task_id"]},
        }

    active_sources = sources or default_sources()
    tools = active_sources.task_tools
    kwargs: dict[str, Any] = {
        "task_id": cleaned_task_id,
        "confirmed": True,
    }
    cleaned_task_list_id = _clean_text(task_list_id)
    if cleaned_task_list_id:
        kwargs["task_list_id"] = cleaned_task_list_id

    try:
        response = tools.complete_task(**kwargs)
    except Exception as error:
        return _source_error_response("Tasks", error)

    if response.get("status") != "ok":
        return response

    raw_task = response.get("data", {}).get("task")
    if not isinstance(raw_task, dict):
        return response

    normalized_task = _normalize_task(
        raw_task,
        active_sources.read_task_metadata,
        _local_now(now).date(),
    )
    return {
        **response,
        "data": {
            **response.get("data", {}),
            "task": _public_task(normalized_task),
        },
    }


def complete_dashboard_homework(
    homework_item_id: str | None,
    sources: DashboardSources | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    cleaned_homework_item_id = _clean_text(homework_item_id)
    if not cleaned_homework_item_id:
        return {
            "status": "error",
            "message": "Missing required homework information: homework_item_id.",
            "data": {"missing_fields": ["homework_item_id"]},
        }

    active_sources = sources or default_sources()
    tools = getattr(active_sources, "homework_tools", None)
    if tools is None:
        return {
            "status": "error",
            "message": "Homework source unavailable.",
            "data": {"homework_item_id": cleaned_homework_item_id},
        }

    try:
        response = tools.complete_homework(cleaned_homework_item_id, now=now)
    except Exception as error:
        return _source_error_response("Homework", error)

    if response.get("status") != "ok":
        return response

    raw_item = response.get("data", {}).get("item")
    if not isinstance(raw_item, dict):
        return response
    return {
        **response,
        "data": {
            **response.get("data", {}),
            "item": _normalize_homework_item(raw_item, _local_now(now).date()),
        },
    }


def complete_dashboard_decision(
    decision_id: str | None,
    outcome: str | None = None,
    sources: DashboardSources | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    cleaned_decision_id = _clean_text(decision_id)
    if not cleaned_decision_id:
        return {
            "status": "error",
            "message": "Missing required decision information: decision_id.",
            "data": {"missing_fields": ["decision_id"]},
        }

    active_sources = sources or default_sources()
    tools = active_sources.decision_tools
    cleaned_outcome = _clean_text(outcome, "Marked done from dashboard.")

    try:
        response = tools.decide(cleaned_decision_id, cleaned_outcome)
    except Exception as error:
        return _source_error_response("Decisions", error)

    if response.get("status") != "ok":
        return response

    raw_decision = response.get("data", {}).get("decision")
    if not isinstance(raw_decision, dict):
        return response

    normalized_decision = _normalize_decision(raw_decision, _local_now(now).date())
    return {
        **response,
        "data": {
            **response.get("data", {}),
            "decision": normalized_decision,
        },
    }


def complete_dashboard_shopping_item(
    item_id: str | None,
    list_slug: str | None = None,
    sources: DashboardSources | None = None,
) -> dict[str, Any]:
    cleaned_item_id = _clean_text(item_id)
    if not cleaned_item_id:
        return {
            "status": "error",
            "message": "Missing required shopping information: item_id.",
            "data": {"missing_fields": ["item_id"]},
        }

    active_sources = sources or default_sources()
    tools = active_sources.shopping_tools
    try:
        return tools.set_checked_by_id(
            item_id=cleaned_item_id,
            checked=True,
            list_slug=_clean_text(list_slug) or None,
        )
    except Exception as error:
        return _source_error_response("Shopping", error)


def update_dashboard_reading_event(
    event_id: str | None,
    *,
    child: str | None = None,
    date: str | None = None,
    book: str | None = None,
    minutes: int | None = None,
    pages: int | None = None,
    reaction: str | None = None,
    status: str | None = None,
    reading_mode: str | None = None,
    clear_minutes: bool = False,
    clear_pages: bool = False,
    clear_reaction: bool = False,
    sources: DashboardSources | None = None,
) -> dict[str, Any]:
    cleaned_event_id = _clean_text(event_id)
    if not cleaned_event_id:
        return {
            "status": "error",
            "message": "Missing required reading information: event_id.",
            "data": {"missing_fields": ["event_id"]},
        }

    active_sources = sources or default_sources()
    tools = active_sources.reading_garden_tools
    if tools is None:
        return _source_error_response("Reading Garden", RuntimeError("unavailable"))

    try:
        return tools.update_reading(
            event_id=cleaned_event_id,
            child=_clean_text(child) or None,
            date=_clean_text(date) or None,
            book=_clean_text(book) or None,
            minutes=minutes,
            pages=pages,
            reaction=_clean_text(reaction) or None,
            status=_clean_text(status) or None,
            reading_mode=_clean_text(reading_mode) or None,
            clear_minutes=clear_minutes,
            clear_pages=clear_pages,
            clear_reaction=clear_reaction,
        )
    except Exception as error:
        return _source_error_response("Reading Garden", error)


def delete_dashboard_reading_event(
    event_id: str | None,
    sources: DashboardSources | None = None,
) -> dict[str, Any]:
    cleaned_event_id = _clean_text(event_id)
    if not cleaned_event_id:
        return {
            "status": "error",
            "message": "Missing required reading information: event_id.",
            "data": {"missing_fields": ["event_id"]},
        }

    active_sources = sources or default_sources()
    tools = active_sources.reading_garden_tools
    if tools is None:
        return _source_error_response("Reading Garden", RuntimeError("unavailable"))

    try:
        return tools.delete_reading(event_id=cleaned_event_id)
    except Exception as error:
        return _source_error_response("Reading Garden", error)


def clear_dashboard_shopping_list(
    list_slug: str | None,
    sources: DashboardSources | None = None,
) -> dict[str, Any]:
    cleaned_list_slug = _clean_text(list_slug)
    if not cleaned_list_slug:
        return {
            "status": "error",
            "message": "Missing required shopping information: list_name.",
            "data": {"missing_fields": ["list_name"]},
        }

    active_sources = sources or default_sources()
    tools = active_sources.shopping_tools
    try:
        return tools.clear_list(cleaned_list_slug)
    except Exception as error:
        return _source_error_response("Shopping", error)

from __future__ import annotations

from datetime import date as Date, datetime, timedelta
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Literal, Protocol, TypedDict
from zoneinfo import ZoneInfo

from .ai_field_extraction import HomeworkAIFieldExtractor, merge_ai_homework_fields
from .intent import DEFAULT_CHILD, extract_intent
from .provider import SQLiteHomeworkProvider


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_N4OS_HOMEWORK_ROOT = ROOT / "n4os" / "homework"
HOMEWORK_SOURCES = {"telegram_text", "telegram_voice", "telegram_photo"}
OPEN_STATUSES = ("assigned", "in_progress")
DEFAULT_TIMEZONE = "America/Los_Angeles"
DEFAULT_HOMEWORK_DUE_TIME = "07:00"
HOMEWORK_CALENDAR_DURATION_MINUTES = 30
CHERRY_BLOSSOM_EVENT_LABEL_COLOR = "#d81b60"
WEEKDAY_LABELS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
DEFAULT_CLASS_SCHEDULES = (
    {
        "child": "Nysha",
        "class_name": "Art",
        "weekday": 5,
        "start_time": "10:00",
        "due_rule": "next_class",
        "calendar_name": "Nysha School Calendar",
        "source": "manual",
    },
    {
        "child": "Navya",
        "class_name": "Art",
        "weekday": 5,
        "start_time": "10:00",
        "due_rule": "next_class",
        "calendar_name": "Navya School Calendar",
        "source": "manual",
    },
    {
        "child": "Nysha",
        "class_name": "RSM Math",
        "weekday": 1,
        "start_time": "15:30",
        "due_rule": "next_class",
        "calendar_name": "Nysha School Calendar",
        "source": "manual",
    },
    {
        "child": "Nysha",
        "class_name": "School",
        "weekday": 4,
        "start_time": None,
        "due_rule": "friday",
        "calendar_name": "Nysha School Calendar",
        "source": "manual",
    },
    {
        "child": "Navya",
        "class_name": "School",
        "weekday": 4,
        "start_time": None,
        "due_rule": "friday",
        "calendar_name": "Navya School Calendar",
        "source": "manual",
    },
)
SCHOOL_SCHEDULE_SUBJECT_KEYS = {
    "math",
    "reading",
    "writing",
    "spelling",
    "science",
    "social studies",
}


class HomeworkProvider(Protocol):
    def capture_assignment(self, **kwargs: Any) -> dict[str, Any]:
        ...

    def attach_assignment_asset(self, **kwargs: Any) -> dict[str, Any] | None:
        ...

    def capture_submission(self, **kwargs: Any) -> dict[str, Any] | None:
        ...

    def update_due_date(self, **kwargs: Any) -> dict[str, Any] | None:
        ...

    def update_assignment_details(self, **kwargs: Any) -> dict[str, Any] | None:
        ...

    def upsert_class_schedule(self, **kwargs: Any) -> dict[str, Any]:
        ...

    def list_class_schedules(self, *, child: str | None = None) -> list[dict[str, Any]]:
        ...

    def list_items(
        self,
        *,
        child: str | None = None,
        statuses: tuple[str, ...] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        ...

    def list_assets(self, homework_item_id: str) -> list[dict[str, Any]]:
        ...

    def list_events(self, homework_item_id: str) -> list[dict[str, Any]]:
        ...


class CalendarToolsLike(Protocol):
    def create_calendar_event(
        self,
        title: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        timezone: str | None = DEFAULT_TIMEZONE,
        description: str | None = None,
        location: str | None = None,
        recurrence: list[str] | None = None,
        attendees: list[dict[str, Any]] | None = None,
        private_extended_properties: dict[str, str] | None = None,
        calendar_name: str | None = None,
        notify_attendees: bool = False,
        all_day: bool = False,
        event_label_background_color: str | None = None,
    ) -> dict[str, Any]:
        ...


class HomeworkFieldExtractorLike(Protocol):
    def extract(
        self,
        request: str,
        *,
        now: datetime | None = None,
        baseline_intent: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...


class ToolResponse(TypedDict, total=False):
    status: Literal["ok", "needs_information", "error"]
    message: str
    data: dict[str, Any]


def _homework_source(value: Any) -> str:
    source = str(value or "telegram_text").split(":", 1)[0]
    return source if source in HOMEWORK_SOURCES else "telegram_text"


def _clean_optional_text(value: Any) -> str | None:
    cleaned = " ".join(str(value or "").split()).strip()
    return cleaned or None


def _local_date(now: datetime | None) -> Date:
    if now is None:
        return datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).date()
    if now.tzinfo is None:
        return now.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE)).date()
    return now.astimezone(ZoneInfo(DEFAULT_TIMEZONE)).date()


GENERIC_HOMEWORK_TITLES = {
    "homework",
    "second grade homework",
    "2nd grade homework",
    "assignment",
    "worksheet",
}


def _normalized_identity_text(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"\bhomework\s+title\s*[:\-]\s*(?:second|2nd)\s+grade\s+homework\b", " ", text)
    text = re.sub(r"\b(?:second|2nd)\s+grade\s+homework\b", " ", text)
    text = re.sub(r"\b(?:homework title|assignment title|student|grade|subject|class)\s*[:\-]", " ", text)
    text = re.sub(r"\b(?:capture|homework|assignment|worksheet|nysha|navya|due|date|assigned)\b", " ", text)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b", " ", text)
    tokens = re.findall(r"[a-z0-9]+", text)
    return " ".join(tokens)


def homework_content_fingerprint(value: Any) -> str | None:
    normalized = _normalized_identity_text(value)
    if len(normalized) < 12:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_homework_metadata(intent: dict[str, Any], *, content_fingerprint: str | None) -> dict[str, Any]:
    title = _clean_optional_text(intent.get("title")) or "Homework"
    visible_text = str(intent.get("ocr_text") or intent.get("raw_input") or "")
    expected_minutes = _expected_minutes(visible_text)
    return {
        "worksheet_title": title,
        "generic_title_detected": title.lower() in GENERIC_HOMEWORK_TITLES,
        "identity_basis": "ocr_content" if content_fingerprint else "manual",
        "skill_tags": _skill_tags(intent, visible_text),
        "task_type": _task_type(intent, visible_text),
        "expected_minutes": expected_minutes,
        "daily_work_days": _daily_work_days(str(intent.get("daily_work") or "")),
        "parent_action_required": bool(
            re.search(r"\b(parent|guardian).{0,40}\b(sign|signature|review|return)\b", visible_text, re.IGNORECASE)
        ),
        "materials_required": _materials_required(visible_text),
        "analysis_notes": _analysis_notes(intent, expected_minutes),
    }


def _metadata_from_item(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("metadata_json")
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _expected_minutes(text: str) -> int | None:
    match = re.search(r"\b(\d{1,3})\s+minutes?\b", text, flags=re.IGNORECASE)
    if not match:
        return None
    value = int(match.group(1))
    return value if 1 <= value <= 240 else None


def _daily_work_days(daily_work: str) -> list[str]:
    days = []
    for day in ("monday", "tuesday", "wednesday", "thursday", "friday"):
        if re.search(rf"\b{day}\b", daily_work, flags=re.IGNORECASE):
            days.append(day.title())
    return days


def _skill_tags(intent: dict[str, Any], text: str) -> list[str]:
    source = f"{intent.get('subject') or ''} {intent.get('title') or ''} {text}".lower()
    tags = []
    rules = (
        ("reading_fluency", r"\bread(?:ing)?\b|read aloud|practice reading"),
        ("writing", r"\bwrite|writing|sentence|journal|facts\b"),
        ("spelling", r"\bspelling|word list|sight words?\b"),
        ("math_facts", r"\bmath|addition|subtraction|multiplication|facts?\b"),
        ("project", r"\bproject|poster|book\b"),
    )
    for tag, pattern in rules:
        if re.search(pattern, source):
            tags.append(tag)
    return tags


def _task_type(intent: dict[str, Any], text: str) -> str:
    source = f"{intent.get('title') or ''} {text}".lower()
    if "project" in source:
        return "project"
    if "packet" in source:
        return "packet"
    if "reading log" in source:
        return "reading_log"
    if "worksheet" in source:
        return "worksheet"
    return "unknown"


def _materials_required(text: str) -> list[str]:
    lowered = text.lower()
    materials = []
    for label, pattern in (
        ("pictures", r"\bpictures?|photographs?\b"),
        ("parent signature", r"\bparent.{0,30}signature|signature required\b"),
        ("printed work", r"\bprint neatly|printed\b"),
    ):
        if re.search(pattern, lowered):
            materials.append(label)
    return materials


def _analysis_notes(intent: dict[str, Any], expected_minutes: int | None) -> str | None:
    facts = []
    if intent.get("week_range"):
        facts.append(f"Week range {intent['week_range']}.")
    if expected_minutes is not None:
        facts.append(f"Expected workload {expected_minutes} minutes.")
    if intent.get("daily_work"):
        facts.append("Includes daily work cadence.")
    return " ".join(facts) or None


def _candidate_text(item: dict[str, Any], assets: list[dict[str, Any]]) -> str:
    parts = [
        item.get("title"),
        item.get("subject"),
        item.get("due_date"),
        item.get("week_range"),
        item.get("daily_work"),
        item.get("notes"),
        item.get("raw_input"),
    ]
    parts.extend(asset.get("ocr_text") for asset in assets)
    return "\n".join(str(part) for part in parts if part)


def _similarity_candidates(
    provider: HomeworkProvider,
    *,
    intent: dict[str, Any],
    content_fingerprint: str | None,
    photo_sha256: str | None,
) -> list[dict[str, Any]]:
    content_text = str(intent.get("ocr_text") or intent.get("raw_input") or "")
    normalized = _normalized_identity_text(content_text)
    candidates = provider.list_items(
        child=str(intent.get("child") or DEFAULT_CHILD),
        statuses=OPEN_STATUSES,
        limit=30,
    )
    matches = []
    for item in candidates:
        assets = provider.list_assets(str(item["id"]))
        asset_hashes = {str(asset.get("photo_sha256")) for asset in assets if asset.get("photo_sha256")}
        stored_fingerprints = {
            str(value)
            for value in [item.get("content_fingerprint"), *(asset.get("content_fingerprint") for asset in assets)]
            if value
        }
        exact_hash = bool(photo_sha256 and photo_sha256 in asset_hashes)
        exact_fingerprint = bool(content_fingerprint and content_fingerprint in stored_fingerprints)
        candidate_normalized = _normalized_identity_text(_candidate_text(item, assets))
        text_ratio = SequenceMatcher(None, normalized, candidate_normalized).ratio() if normalized and candidate_normalized else 0.0
        token_overlap = _token_overlap(normalized, candidate_normalized)
        score = max(text_ratio, token_overlap)
        if exact_hash or exact_fingerprint:
            score = 1.0
        if intent.get("due_date") and intent.get("due_date") == item.get("due_date"):
            score += 0.05
        if intent.get("week_range") and intent.get("week_range") == item.get("week_range"):
            score += 0.05
        if intent.get("subject") and intent.get("subject") == item.get("subject"):
            score += 0.03
        score = min(score, 1.0)
        if score >= 0.72:
            matches.append(
                {
                    "item": item,
                    "score": round(score, 3),
                    "reason": "same photo" if exact_hash else ("same content" if exact_fingerprint else "similar content"),
                }
            )
    matches.sort(key=lambda match: match["score"], reverse=True)
    return matches[:3]


def _token_overlap(left: str, right: str) -> float:
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _normalize_match_text(value: Any) -> set[str]:
    words = re.findall(r"[a-z0-9]+", str(value or "").lower())
    return {word for word in words if len(word) >= 3 and word not in {"homework", "assignment", "submitted"}}


def _match_score(item: dict[str, Any], intent: dict[str, Any], today: Date) -> int:
    score = 0
    title_words = _normalize_match_text(item.get("title"))
    request_words = _normalize_match_text(intent.get("title")) | _normalize_match_text(intent.get("raw_input"))
    subject = str(item.get("subject") or "").lower()
    requested_subject = str(intent.get("subject") or "").lower()
    if title_words and title_words & request_words:
        score += 5 + len(title_words & request_words)
    if subject and requested_subject and subject == requested_subject:
        score += 4
    if item.get("due_date") and item.get("due_date") == intent.get("due_date"):
        score += 3
    due_date = str(item.get("due_date") or "")
    if due_date:
        try:
            days_until_due = (Date.fromisoformat(due_date[:10]) - today).days
        except ValueError:
            days_until_due = 99
        if -14 <= days_until_due <= 30:
            score += 2
    return score


def _title_line(item: dict[str, Any]) -> str:
    subject = f" ({item['subject']})" if item.get("subject") else ""
    due = item.get("due_date") or "unknown"
    return f"{item['title']}{subject} - {item['status']}, due {due}"


def _schedule_name_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _default_class_schedule_rows(child: str | None = None) -> list[dict[str, Any]]:
    rows = [dict(schedule) for schedule in DEFAULT_CLASS_SCHEDULES]
    if child:
        rows = [row for row in rows if str(row["child"]) == child]
    return rows


def _effective_class_schedules(
    provider: HomeworkProvider,
    *,
    child: str | None = None,
) -> list[dict[str, Any]]:
    schedules = provider.list_class_schedules(child=child)
    seen = {
        (str(schedule.get("child")), _schedule_name_key(schedule.get("class_name")))
        for schedule in schedules
    }
    for schedule in _default_class_schedule_rows(child):
        key = (str(schedule["child"]), _schedule_name_key(schedule["class_name"]))
        if key not in seen:
            schedules.append(schedule)
            seen.add(key)
    return schedules


def _schedule_due_date(today: Date, schedule: dict[str, Any]) -> str:
    weekday = int(schedule["weekday"])
    days_ahead = (weekday - today.weekday()) % 7
    if str(schedule.get("due_rule") or "").lower() != "friday" and days_ahead == 0:
        days_ahead = 7
    return (today + timedelta(days=days_ahead)).isoformat()


def _matching_class_schedule(
    provider: HomeworkProvider,
    *,
    child: str,
    class_name: str | None,
) -> dict[str, Any] | None:
    schedules = _effective_class_schedules(provider, child=child)
    requested = _schedule_name_key(class_name or "School")
    if not requested:
        requested = "school"
    for schedule in schedules:
        if _schedule_name_key(schedule.get("class_name")) == requested:
            return schedule
    if not class_name or requested in SCHOOL_SCHEDULE_SUBJECT_KEYS:
        for schedule in schedules:
            if _schedule_name_key(schedule.get("class_name")) == "school":
                return schedule
    for schedule in schedules:
        schedule_key = _schedule_name_key(schedule.get("class_name"))
        if schedule_key == "school":
            continue
        if requested in schedule_key or schedule_key in requested:
            return schedule
    return None


def _infer_due_date_from_class_schedule(
    provider: HomeworkProvider,
    intent: dict[str, Any],
    *,
    now: datetime | None,
) -> tuple[str | None, dict[str, Any] | None]:
    schedule = _matching_class_schedule(
        provider,
        child=str(intent.get("child") or DEFAULT_CHILD),
        class_name=_clean_optional_text(intent.get("subject")),
    )
    existing_due_date = _clean_optional_text(intent.get("due_date"))
    caption_text = re.split(r"\bimage text\s*:", str(intent.get("raw_input") or ""), maxsplit=1, flags=re.IGNORECASE)[0]
    explicit_due_text = bool(
        re.search(r"\b(?:due|due date|due by)\b", str(intent.get("raw_input") or ""), flags=re.IGNORECASE)
        or re.search(r"\b(?:today|tomorrow)\b", caption_text, flags=re.IGNORECASE)
        or re.search(r"\b(?:next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", caption_text, flags=re.IGNORECASE)
        or re.search(r"\b(?:after|in)\s+(?:\d+|one|two|three|four)\s+weeks?\b", caption_text, flags=re.IGNORECASE)
        or re.search(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?|[A-Za-z]{3,9}\s+\d{1,2})\b", caption_text)
    )
    if schedule is None:
        return existing_due_date, None
    if existing_due_date and (not intent.get("ai_field_extraction") or explicit_due_text):
        return existing_due_date, schedule
    due_date = _schedule_due_date(_local_date(now), schedule)
    return due_date, schedule


def _is_ai_refined_intent(intent: dict[str, Any]) -> bool:
    return isinstance(intent.get("ai_field_extraction"), dict)


def _schedule_note(schedule: dict[str, Any] | None) -> str | None:
    if not schedule:
        return None
    time_text = _clean_optional_text(schedule.get("start_time"))
    weekday = WEEKDAY_LABELS[int(schedule["weekday"])]
    suffix = f" at {time_text}" if time_text else ""
    return f"Inferred from {schedule['child']} {schedule['class_name']} schedule: {weekday}{suffix}."


def _intent_context_for_ai(intent: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "intent",
        "child",
        "children",
        "title",
        "subject",
        "assigned_date",
        "due_date",
        "due_time",
        "status",
        "notes",
        "grade",
        "week_range",
        "daily_work",
        "source",
        "ocr_text",
    }
    return {key: value for key, value in intent.items() if key in allowed and value}


def _homework_context_for_ai(provider: HomeworkProvider, child: str) -> dict[str, Any]:
    return {
        "class_schedules": _effective_class_schedules(provider, child=child),
        "open_homework": [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "child": item.get("child"),
                "subject": item.get("subject"),
                "due_date": item.get("due_date"),
                "status": item.get("status"),
            }
            for item in provider.list_items(child=child, statuses=OPEN_STATUSES, limit=10)
        ],
    }


def _pending_due_date_action(item: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": "fill_homework_due_date",
        "item_id": item["id"],
        "child": item["child"],
        "title": item["title"],
        "intent": intent,
        "due_date": item.get("due_date"),
    }


def _pending_clarify_action(
    request: str,
    intent: dict[str, Any],
    *,
    source: str,
    photo_path: str | None,
    photo_sha256: str | None,
) -> dict[str, Any]:
    return {
        "action": "clarify_homework_capture",
        "request": request,
        "source": _homework_source(source),
        "photo_path": photo_path,
        "photo_sha256": photo_sha256,
        "intent": intent,
    }


def _subject_label(item: dict[str, Any]) -> str:
    return _clean_optional_text(item.get("subject")) or "Unsorted"


def _markdown_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return cleaned or "unsorted"


def _render_homework_markdown(
    provider: HomeworkProvider,
    *,
    title: str,
    child: str,
    items: list[dict[str, Any]],
) -> str:
    lines = [
        "---",
        "tags:",
        '  - "n4os/homework"',
        f"child: {child}",
        "---",
        "",
        f"# {title}",
        "",
    ]
    if not items:
        lines.append("No homework captured yet.")
        return "\n".join(lines) + "\n"

    for item in items:
        assets = provider.list_assets(str(item["id"]))
        events = provider.list_events(str(item["id"]))
        lines.extend(
            [
                f"## {item['title']}",
                "",
                f"- Status: {item['status']}",
                f"- Subject/class: {_subject_label(item)}",
                f"- Assigned: {item['assigned_date']}",
                f"- Due: {item.get('due_date') or 'Unknown'}",
            ],
        )
        if item.get("grade"):
            lines.append(f"- Grade: {item['grade']}")
        if item.get("week_range"):
            lines.append(f"- Week range: {item['week_range']}")
        metadata = _metadata_from_item(item)
        if item.get("content_fingerprint"):
            lines.append(f"- Content fingerprint: {str(item['content_fingerprint'])[:12]}")
        if metadata:
            if metadata.get("generic_title_detected"):
                lines.append("- Identity note: Generic visible homework title ignored for matching.")
            if metadata.get("skill_tags"):
                lines.append(f"- Skill tags: {', '.join(str(tag) for tag in metadata['skill_tags'])}")
            if metadata.get("task_type"):
                lines.append(f"- Task type: {metadata['task_type']}")
            if metadata.get("expected_minutes"):
                lines.append(f"- Expected minutes: {metadata['expected_minutes']}")
            if metadata.get("daily_work_days"):
                lines.append(f"- Daily work days: {', '.join(str(day) for day in metadata['daily_work_days'])}")
            if metadata.get("parent_action_required"):
                lines.append("- Parent action required: yes")
            if metadata.get("materials_required"):
                lines.append(f"- Materials: {', '.join(str(item) for item in metadata['materials_required'])}")
            if metadata.get("similar_to_item_id"):
                lines.append(f"- Similar to homework item: {metadata['similar_to_item_id']}")
        if item.get("notes"):
            lines.append(f"- Notes: {item['notes']}")
        photo_paths = [asset["path"] for asset in assets if asset.get("path")]
        if photo_paths:
            lines.append("- Captured images:")
            lines.extend([f"  - {path_value}" for path_value in photo_paths])
        if item.get("daily_work"):
            lines.extend(["", "Daily work:", ""])
            lines.extend([f"- {line}" for line in str(item["daily_work"]).splitlines() if line.strip()])
        ocr_blocks = [asset.get("ocr_text") for asset in assets if asset.get("ocr_text")]
        if ocr_blocks:
            lines.extend(["", "OCR text:", ""])
            lines.append("```text")
            lines.append(str(ocr_blocks[-1]).strip())
            lines.append("```")
        submitted_events = [event for event in events if event.get("event_type") == "submitted"]
        if submitted_events:
            lines.extend(["", "Submitted evidence:"])
            for event in submitted_events:
                lines.append(f"- {event['created_at']}: {event.get('note') or 'Submitted work captured.'}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_schedule_markdown(provider: HomeworkProvider, child: str | None = None) -> str:
    schedules = _effective_class_schedules(provider, child=child)
    title = f"{child} Homework Class Schedule" if child else "Homework Class Schedules"
    lines = [
        "---",
        "tags:",
        '  - "n4os/homework"',
        '  - "n4os/homework/class-schedule"',
        "---",
        "",
        f"# {title}",
        "",
    ]
    if not schedules:
        lines.append("No class schedules captured yet.")
        return "\n".join(lines) + "\n"
    for schedule in schedules:
        weekday = WEEKDAY_LABELS[int(schedule["weekday"])]
        time_text = schedule.get("start_time") or "no fixed time"
        lines.extend(
            [
                f"## {schedule['child']} - {schedule['class_name']}",
                "",
                f"- Day: {weekday}",
                f"- Time: {time_text}",
                f"- Due rule: {schedule['due_rule']}",
                f"- Calendar: {schedule.get('calendar_name') or 'Unknown'}",
                f"- Source: {schedule['source']}",
                "",
            ],
        )
    return "\n".join(lines).rstrip() + "\n"


def _write_class_schedule_markdown(provider: HomeworkProvider, homework_root: Path) -> None:
    homework_root.mkdir(parents=True, exist_ok=True)
    (homework_root / "class-schedules.md").write_text(
        _render_schedule_markdown(provider),
        encoding="utf-8",
    )
    children = sorted({str(schedule["child"]) for schedule in _effective_class_schedules(provider)})
    for child in children:
        child_root = homework_root / child
        child_root.mkdir(parents=True, exist_ok=True)
        (child_root / "class-schedule.md").write_text(
            _render_schedule_markdown(provider, child=child),
            encoding="utf-8",
        )


def _write_markdown(
    provider: HomeworkProvider,
    *,
    child: str,
    homework_root: Path,
) -> None:
    homework_root.mkdir(parents=True, exist_ok=True)
    items = provider.list_items(child=child, limit=100)
    path = homework_root / f"{child}.md"
    path.write_text(
        _render_homework_markdown(provider, title=f"{child} Homework", child=child, items=items),
        encoding="utf-8",
    )

    child_root = homework_root / child
    child_root.mkdir(parents=True, exist_ok=True)
    (child_root / "index.md").write_text(
        _render_homework_markdown(provider, title=f"{child} Homework", child=child, items=items),
        encoding="utf-8",
    )
    by_subject: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_subject.setdefault(_subject_label(item), []).append(item)
    current_subject_files = {
        f"{_markdown_filename(subject)}.md"
        for subject in by_subject
    }
    for existing_subject_file in child_root.glob("*.md"):
        if existing_subject_file.name in {"index.md", "class-schedule.md"}:
            continue
        if existing_subject_file.name not in current_subject_files:
            if _is_generated_homework_subject_file(existing_subject_file, child):
                existing_subject_file.unlink()
    for subject, subject_items in sorted(by_subject.items()):
        (child_root / f"{_markdown_filename(subject)}.md").write_text(
            _render_homework_markdown(
                provider,
                title=f"{child} {subject} Homework",
                child=child,
                items=subject_items,
            ),
            encoding="utf-8",
        )
    _write_class_schedule_markdown(provider, homework_root)


def _is_generated_homework_subject_file(path: Path, child: str) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(
        text.startswith("---\n")
        and '  - "n4os/homework"' in text
        and f"child: {child}" in text
        and re.search(rf"^# {re.escape(child)} .+ Homework$", text, flags=re.MULTILINE)
    )


def ensure_default_class_schedules(provider: HomeworkProvider, homework_root: Path) -> None:
    for schedule in DEFAULT_CLASS_SCHEDULES:
        provider.upsert_class_schedule(**schedule)
    _write_class_schedule_markdown(provider, homework_root)


def _default_calendar_tools() -> CalendarToolsLike | None:
    try:
        from claws.n4os.intent_router import CALENDAR_ROOT, load_scoped_module, module_scope

        with module_scope(CALENDAR_ROOT):
            module = load_scoped_module("_n4os_homework_family_calendar_tools", CALENDAR_ROOT, "tools.py")
            calendar_id = os.environ.get("N4OS_FAMILY_CALENDAR_ID") or "primary"
            return module.build_default_tools(calendar_id=calendar_id)
    except ModuleNotFoundError:
        return None


def _homework_due_start(due_date: str, due_time: str | None) -> datetime:
    hour_text, minute_text = (due_time or DEFAULT_HOMEWORK_DUE_TIME).split(":", 1)
    return datetime.combine(
        Date.fromisoformat(due_date[:10]),
        datetime.min.time(),
        tzinfo=ZoneInfo(DEFAULT_TIMEZONE),
    ).replace(hour=int(hour_text), minute=int(minute_text))


def _calendar_description(item: dict[str, Any]) -> str:
    lines = [
        f"Homework captured for {item['child']}.",
        f"Status: {item['status']}.",
    ]
    if item.get("subject"):
        lines.append(f"Subject/class: {item['subject']}.")
    if item.get("notes"):
        lines.append(f"Notes: {item['notes']}")
    return "\n".join(lines)


def _calendar_private_properties(item: dict[str, Any]) -> dict[str, str]:
    return {
        "n4os_domain": "homework",
        "n4os_homework_item_id": str(item["id"]),
        "n4os_child": str(item["child"]),
    }


class HomeworkTools:
    def __init__(
        self,
        provider: HomeworkProvider,
        *,
        homework_root: Path = DEFAULT_N4OS_HOMEWORK_ROOT,
        calendar_tools: CalendarToolsLike | None = None,
        field_extractor: HomeworkFieldExtractorLike | None = None,
    ) -> None:
        self.provider = provider
        self.homework_root = homework_root
        self.calendar_tools = calendar_tools
        self.field_extractor = field_extractor

    def _extract_intent_from_request(
        self,
        request: str,
        *,
        now: datetime | None,
        source: str,
        photo_path: str | None,
    ) -> dict[str, Any]:
        intent = extract_intent(request, now=now, source=source, photo_path=photo_path)
        if self.field_extractor is None:
            return intent
        try:
            ai_fields = self.field_extractor.extract(
                request,
                now=now,
                baseline_intent=_intent_context_for_ai(intent),
                context=_homework_context_for_ai(self.provider, str(intent.get("child") or DEFAULT_CHILD)),
            )
        except Exception:
            return intent
        return merge_ai_homework_fields(intent, ai_fields, request)

    def capture_assignment(
        self,
        request: str,
        *,
        now: datetime | None = None,
        source: str = "telegram_text",
        photo_path: str | None = None,
        photo_sha256: str | None = None,
        skip_duplicate_check: bool = False,
        metadata_overrides: dict[str, Any] | None = None,
    ) -> ToolResponse:
        try:
            intent = self._extract_intent_from_request(request, now=now, source=source, photo_path=photo_path)
            if intent.get("intent") == "clarify":
                return {
                    "status": "needs_information",
                    "message": str(
                        intent.get("clarification_question")
                        or "What child, class, and due date should I use for that homework?"
                    ),
                    "data": {
                        "missing_fields": list(intent.get("missing_fields") or []),
                        "pending_action": _pending_clarify_action(
                            request,
                            intent,
                            source=source,
                            photo_path=photo_path,
                            photo_sha256=photo_sha256,
                        ),
                    },
                }
            if intent.get("intent") == "homework_status":
                return self.homework_status(child=str(intent.get("child") or DEFAULT_CHILD))
            if intent.get("intent") == "capture_submission":
                return self.capture_submission(request, now=now, source=source, photo_path=photo_path)
            inferred_due_date, inferred_schedule = _infer_due_date_from_class_schedule(
                self.provider,
                intent,
                now=now,
            )
            ai_refined = _is_ai_refined_intent(intent)
            if inferred_due_date and (ai_refined or not _clean_optional_text(intent.get("due_date"))):
                intent = {**intent, "due_date": inferred_due_date}
            explicit_time_text = bool(
                re.search(
                    r"\b(?:at|by)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
                    str(intent.get("raw_input") or ""),
                    flags=re.IGNORECASE,
                )
            )
            if inferred_schedule and inferred_schedule.get("start_time") and (
                (ai_refined and not explicit_time_text) or not _clean_optional_text(intent.get("due_time"))
            ):
                intent["due_time"] = inferred_schedule["start_time"]
            if inferred_schedule and inferred_schedule.get("calendar_name") and (
                ai_refined or not _clean_optional_text(intent.get("calendar_name"))
            ):
                intent["calendar_name"] = inferred_schedule["calendar_name"]
            content_fingerprint = homework_content_fingerprint(intent.get("ocr_text") or intent.get("raw_input"))
            metadata = build_homework_metadata(intent, content_fingerprint=content_fingerprint)
            schedule_note = _schedule_note(inferred_schedule)
            if schedule_note:
                metadata["due_date_inference"] = schedule_note
            if metadata_overrides:
                metadata.update(metadata_overrides)
            if not skip_duplicate_check:
                matches = _similarity_candidates(
                    self.provider,
                    intent=intent,
                    content_fingerprint=content_fingerprint,
                    photo_sha256=photo_sha256,
                )
                if matches:
                    choices = "; ".join(
                        f"{_title_line(match['item'])} ({match['reason']}, {match['score']:.0%})"
                        for match in matches
                    )
                    return {
                        "status": "needs_information",
                        "message": (
                            "This looks similar to an existing homework. Reply `attach` to add this photo to it, "
                            "`new` to create a separate homework, or `cancel`. "
                            f"Candidates: {choices}"
                        ),
                        "data": {
                            "pending_action": {
                                "action": "resolve_duplicate_assignment",
                                "request": request,
                                "source": _homework_source(source),
                                "photo_path": photo_path,
                                "photo_sha256": photo_sha256,
                                "intent": intent,
                                "content_fingerprint": content_fingerprint,
                                "metadata": metadata,
                                "candidates": matches,
                            }
                        },
                    }
            item = self.provider.capture_assignment(
                child=str(intent["child"]),
                title=str(intent["title"]),
                subject=_clean_optional_text(intent.get("subject")),
                assigned_date=str(intent["assigned_date"]),
                due_date=_clean_optional_text(intent.get("due_date")),
                status=str(intent.get("status") or "assigned"),
                notes=_clean_optional_text(intent.get("notes")),
                grade=_clean_optional_text(intent.get("grade")),
                week_range=_clean_optional_text(intent.get("week_range")),
                daily_work=_clean_optional_text(intent.get("daily_work")),
                metadata=metadata,
                content_fingerprint=content_fingerprint,
                raw_input=str(intent["raw_input"]),
                source=_homework_source(source),
                photo_path=photo_path,
                ocr_text=_clean_optional_text(intent.get("ocr_text")),
                photo_sha256=photo_sha256,
            )
            _write_markdown(self.provider, child=str(item["child"]), homework_root=self.homework_root)
            if not _clean_optional_text(item.get("due_date")):
                return {
                    "status": "needs_information",
                    "message": (
                        f"Captured homework for {item['child']}: {_title_line(item)}. "
                        "What due date should I track?"
                    ),
                    "data": {
                        "item": item,
                        "pending_action": _pending_due_date_action(item, intent),
                    },
                }
            calendar_result = self._create_due_calendar_event(item, intent)
        except Exception as error:
            return {
                "status": "error",
                "message": f"Homework storage failed: {error}",
                "data": {"error_type": error.__class__.__name__},
            }
        message = f"Captured homework for {item['child']}: {_title_line(item)}."
        if calendar_result is not None:
            if calendar_result.get("status") == "ok":
                message += " Added due-date reminder to the school calendar."
            else:
                message += f" Calendar reminder was not added: {calendar_result.get('message')}"
        return {
            "status": "ok",
            "message": message,
            "data": {"item": item, "calendar": calendar_result},
        }

    def capture_submission(
        self,
        request: str,
        *,
        now: datetime | None = None,
        source: str = "telegram_text",
        photo_path: str | None = None,
    ) -> ToolResponse:
        intent = self._extract_intent_from_request(request, now=now, source=source, photo_path=photo_path)
        if intent.get("intent") == "clarify":
            return {
                "status": "needs_information",
                "message": str(
                    intent.get("clarification_question")
                    or "Which homework assignment is this submission for?"
                ),
                "data": {
                    "missing_fields": list(intent.get("missing_fields") or []),
                },
            }
        today = (now.date() if now and now.tzinfo is None else datetime.now().date()) if now else Date.today()
        candidates = self.provider.list_items(
            child=str(intent.get("child") or DEFAULT_CHILD),
            statuses=OPEN_STATUSES,
            limit=20,
        )
        scored = [
            (item, _match_score(item, intent, today))
            for item in candidates
        ]
        matches = [(item, score) for item, score in scored if score >= 4]
        matches.sort(key=lambda item_score: item_score[1], reverse=True)
        if not matches:
            return {
                "status": "needs_information",
                "message": "I could not find an open homework assignment to attach this submission to. Include the title or subject.",
                "data": {"missing_fields": ["matching_assignment"]},
            }
        top_score = matches[0][1]
        plausible = [item for item, score in matches if score == top_score]
        if len(plausible) > 1:
            choices = "; ".join(_title_line(item) for item in plausible[:3])
            return {
                "status": "needs_information",
                "message": f"Which homework is this submission for? {choices}",
                "data": {"candidates": plausible[:3]},
            }

        item = self.provider.capture_submission(
            homework_item_id=str(matches[0][0]["id"]),
            source=_homework_source(source),
            raw_input=str(intent["raw_input"]),
            photo_path=photo_path,
            ocr_text=_clean_optional_text(intent.get("ocr_text")),
            content_fingerprint=homework_content_fingerprint(intent.get("ocr_text") or intent.get("raw_input")),
            photo_sha256=None,
            notes=_clean_optional_text(intent.get("notes")),
        )
        if item is None:
            return {
                "status": "error",
                "message": "Homework storage failed: matched assignment disappeared.",
                "data": {"error_type": "MissingHomeworkItem"},
            }
        _write_markdown(self.provider, child=str(item["child"]), homework_root=self.homework_root)
        return {
            "status": "ok",
            "message": f"Marked submitted: {_title_line(item)}.",
            "data": {"item": item},
        }

    def resolve_duplicate_assignment(self, pending_action: dict[str, Any], response_text: str) -> ToolResponse:
        response = " ".join(response_text.lower().strip(" .!").split())
        if response in {"cancel", "no", "stop"}:
            return {
                "status": "ok",
                "message": "Canceled homework capture.",
                "data": {"cleanup_photo_path": pending_action.get("photo_path")},
            }
        if response not in {"attach", "same", "duplicate", "new", "separate"}:
            return {
                "status": "needs_information",
                "message": "Reply `attach`, `new`, or `cancel` for this homework photo.",
                "data": {"pending_action": pending_action},
            }
        if response in {"new", "separate"}:
            candidates = pending_action.get("candidates") if isinstance(pending_action.get("candidates"), list) else []
            similar_item_id = candidates[0]["item"]["id"] if candidates else None
            metadata = dict(pending_action.get("metadata") or {})
            if similar_item_id:
                metadata["similar_to_item_id"] = similar_item_id
            return self.capture_assignment(
                str(pending_action["request"]),
                source=str(pending_action.get("source") or "telegram_text"),
                photo_path=_clean_optional_text(pending_action.get("photo_path")),
                photo_sha256=_clean_optional_text(pending_action.get("photo_sha256")),
                skip_duplicate_check=True,
                metadata_overrides=metadata,
            )

        candidates = pending_action.get("candidates") if isinstance(pending_action.get("candidates"), list) else []
        if not candidates:
            return {
                "status": "error",
                "message": "Homework duplicate candidate disappeared.",
                "data": {"error_type": "MissingHomeworkDuplicateCandidate"},
            }
        candidate = candidates[0]["item"]
        intent = pending_action.get("intent") if isinstance(pending_action.get("intent"), dict) else {}
        item = self.provider.attach_assignment_asset(
            homework_item_id=str(candidate["id"]),
            source=str(pending_action.get("source") or "telegram_text"),
            raw_input=str(pending_action.get("request") or ""),
            photo_path=_clean_optional_text(pending_action.get("photo_path")),
            ocr_text=_clean_optional_text(intent.get("ocr_text")),
            content_fingerprint=_clean_optional_text(pending_action.get("content_fingerprint")),
            photo_sha256=_clean_optional_text(pending_action.get("photo_sha256")),
            notes="Attached similar homework capture after confirmation.",
        )
        if item is None:
            return {
                "status": "error",
                "message": "Homework storage failed: duplicate candidate disappeared.",
                "data": {"error_type": "MissingHomeworkItem"},
            }
        _write_markdown(self.provider, child=str(item["child"]), homework_root=self.homework_root)
        return {
            "status": "ok",
            "message": f"Attached homework photo to existing item: {_title_line(item)}.",
            "data": {"item": item},
        }

    def resolve_pending_action(
        self,
        pending_action: dict[str, Any],
        response_text: str,
        *,
        now: datetime | None = None,
    ) -> ToolResponse:
        if pending_action.get("action") == "clarify_homework_capture":
            return self.resolve_clarified_capture(pending_action, response_text, now=now)
        if pending_action.get("action") == "fill_homework_due_date":
            return self.resolve_due_date(pending_action, response_text, now=now)
        return self.resolve_duplicate_assignment(pending_action, response_text)

    def resolve_clarified_capture(
        self,
        pending_action: dict[str, Any],
        response_text: str,
        *,
        now: datetime | None = None,
    ) -> ToolResponse:
        response = " ".join(response_text.lower().strip(" .!").split())
        if response in {"cancel", "no", "stop"}:
            return {
                "status": "ok",
                "message": "Canceled homework capture.",
                "data": {"cleanup_photo_path": pending_action.get("photo_path")},
            }
        clarified_request = f"{pending_action.get('request') or ''}\n{response_text}".strip()
        return self.capture_assignment(
            clarified_request,
            now=now,
            source=str(pending_action.get("source") or "telegram_text"),
            photo_path=_clean_optional_text(pending_action.get("photo_path")),
            photo_sha256=_clean_optional_text(pending_action.get("photo_sha256")),
        )

    def resolve_due_date(
        self,
        pending_action: dict[str, Any],
        response_text: str,
        *,
        now: datetime | None = None,
    ) -> ToolResponse:
        response = " ".join(response_text.lower().strip(" .!").split())
        if response in {"cancel", "no", "stop"}:
            return {
                "status": "ok",
                "message": "Canceled homework due-date update.",
                "data": {},
            }
        intent = extract_intent(
            f"homework due {response_text}",
            now=now,
            source="telegram_text",
        )
        original_intent = pending_action.get("intent") if isinstance(pending_action.get("intent"), dict) else {}
        due_date = _clean_optional_text(intent.get("due_date")) or _clean_optional_text(pending_action.get("due_date"))
        due_time = _clean_optional_text(intent.get("due_time")) or _clean_optional_text(original_intent.get("due_time"))
        calendar_name = _clean_optional_text(intent.get("calendar_name")) or _clean_optional_text(original_intent.get("calendar_name"))
        if due_date is None:
            return {
                "status": "needs_information",
                "message": "What due date should I track for that homework?",
                "data": {"pending_action": pending_action},
            }

        item = self.provider.update_due_date(
            homework_item_id=str(pending_action["item_id"]),
            due_date=due_date,
            note=f"Due date set from follow-up: {response_text}",
        )
        if item is None:
            return {
                "status": "error",
                "message": "Homework storage failed: assignment disappeared.",
                "data": {"error_type": "MissingHomeworkItem"},
            }
        _write_markdown(self.provider, child=str(item["child"]), homework_root=self.homework_root)
        calendar_intent = {**original_intent, **intent, "due_time": due_time}
        if calendar_name:
            calendar_intent["calendar_name"] = calendar_name
        calendar_result = self._create_due_calendar_event(item, calendar_intent)
        message = f"Updated homework for {item['child']}: {_title_line(item)}."
        if calendar_result is not None:
            if calendar_result.get("status") == "ok":
                message += " Added due-date reminder to the school calendar."
            else:
                message += f" Calendar reminder was not added: {calendar_result.get('message')}"
        return {
            "status": "ok",
            "message": message,
            "data": {"item": item, "calendar": calendar_result},
        }

    def list_homework(self, *, child: str | None = None, limit: int = 10) -> ToolResponse:
        items = self.provider.list_items(child=child, statuses=OPEN_STATUSES, limit=limit)
        if not items:
            return {
                "status": "ok",
                "message": f"No open homework found for {child or DEFAULT_CHILD}.",
                "data": {"items": []},
            }
        lines = [f"Open homework for {child or DEFAULT_CHILD}:"]
        lines.extend([f"- {_title_line(item)}" for item in items])
        return {"status": "ok", "message": "\n".join(lines), "data": {"items": items}}

    def list_class_schedules(self, *, child: str | None = None) -> ToolResponse:
        schedules = _effective_class_schedules(self.provider, child=child)
        if not schedules:
            return {
                "status": "ok",
                "message": "No homework class schedules captured yet.",
                "data": {"schedules": []},
            }
        lines = ["Homework class schedules:"]
        for schedule in schedules:
            weekday = WEEKDAY_LABELS[int(schedule["weekday"])]
            time_text = schedule.get("start_time") or "no fixed time"
            lines.append(f"- {schedule['child']} {schedule['class_name']}: {weekday} {time_text}")
        return {
            "status": "ok",
            "message": "\n".join(lines),
            "data": {"schedules": schedules},
        }

    def homework_status(self, *, child: str | None = None) -> ToolResponse:
        return self.list_homework(child=child or DEFAULT_CHILD)

    def _create_due_calendar_event(
        self,
        item: dict[str, Any],
        intent: dict[str, Any],
    ) -> ToolResponse | None:
        due_date = _clean_optional_text(item.get("due_date"))
        if due_date is None:
            return None
        calendar_tools = self.calendar_tools
        try:
            if calendar_tools is None:
                calendar_tools = _default_calendar_tools()
            if calendar_tools is None:
                return {
                    "status": "error",
                    "message": "Google Calendar tools are not available.",
                    "data": {"error": "calendar_tools_unavailable"},
                }
            start = _homework_due_start(due_date, _clean_optional_text(intent.get("due_time")))
            end = start + timedelta(minutes=HOMEWORK_CALENDAR_DURATION_MINUTES)
            return calendar_tools.create_calendar_event(
                title=f"Homework due: {item['title']}",
                start_time=start.isoformat(),
                end_time=end.isoformat(),
                timezone=DEFAULT_TIMEZONE,
                description=_calendar_description(item),
                calendar_name=_clean_optional_text(intent.get("calendar_name")) or f"{item['child']} School Calendar",
                private_extended_properties=_calendar_private_properties(item),
                event_label_background_color=CHERRY_BLOSSOM_EVENT_LABEL_COLOR,
            )
        except Exception as error:
            return {
                "status": "error",
                "message": str(error) or "Google Calendar could not complete that request.",
                "data": {"error_type": error.__class__.__name__},
            }


def build_default_tools() -> HomeworkTools:
    provider = SQLiteHomeworkProvider()
    return HomeworkTools(provider, field_extractor=HomeworkAIFieldExtractor.from_env_or_none())

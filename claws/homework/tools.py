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


class HomeworkProvider(Protocol):
    def capture_assignment(self, **kwargs: Any) -> dict[str, Any]:
        ...

    def attach_assignment_asset(self, **kwargs: Any) -> dict[str, Any] | None:
        ...

    def capture_submission(self, **kwargs: Any) -> dict[str, Any] | None:
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
    due = item.get("due_date") or "due unknown"
    return f"{item['title']}{subject} - {item['status']}, due {due}"


def _write_markdown(
    provider: HomeworkProvider,
    *,
    child: str,
    homework_root: Path,
) -> None:
    homework_root.mkdir(parents=True, exist_ok=True)
    items = provider.list_items(child=child, limit=100)
    path = homework_root / f"{child}.md"
    lines = [
        "---",
        "tags:",
        '  - "n4os/homework"',
        f"child: {child}",
        "---",
        "",
        f"# {child} Homework",
        "",
    ]
    if not items:
        lines.append("No homework captured yet.")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return

    for item in items:
        assets = provider.list_assets(str(item["id"]))
        events = provider.list_events(str(item["id"]))
        lines.extend(
            [
                f"## {item['title']}",
                "",
                f"- Status: {item['status']}",
                f"- Subject/class: {item.get('subject') or 'Unknown'}",
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
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


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
    ) -> None:
        self.provider = provider
        self.homework_root = homework_root
        self.calendar_tools = calendar_tools

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
            intent = extract_intent(request, now=now, source=source, photo_path=photo_path)
            if intent.get("intent") == "homework_status":
                return self.homework_status(child=str(intent.get("child") or DEFAULT_CHILD))
            if intent.get("intent") == "capture_submission":
                return self.capture_submission(request, now=now, source=source, photo_path=photo_path)
            content_fingerprint = homework_content_fingerprint(intent.get("ocr_text") or intent.get("raw_input"))
            metadata = build_homework_metadata(intent, content_fingerprint=content_fingerprint)
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
        intent = extract_intent(request, now=now, source=source, photo_path=photo_path)
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
                calendar_name=f"{item['child']} School Calendar",
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
    return HomeworkTools(SQLiteHomeworkProvider())

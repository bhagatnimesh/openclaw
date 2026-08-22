from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date as Date, datetime, time, timedelta
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Iterator, Literal
from urllib.parse import urlparse
import urllib.request
from uuid import uuid4
from zoneinfo import ZoneInfo

from claws.homework.provider import SQLiteHomeworkProvider
from claws.homework.tools import (
    DEFAULT_N4OS_HOMEWORK_ROOT,
    HomeworkTools,
    _write_markdown,
    build_homework_metadata,
    homework_content_fingerprint,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_FILE = ROOT / "data" / "n4os.db"
DEFAULT_N4OS_ROOT = ROOT / "n4os"
DEFAULT_TIMEZONE = "America/Los_Angeles"
NEWSLETTER_SAVE_WORDS = {"save", "save all", "confirm", "looks good"}
NEWSLETTER_CANCEL_WORDS = {"cancel", "skip", "discard", "nevermind", "never mind"}
SLIDES_URL_RE = re.compile(
    r"https://docs\.google\.com/presentation/d/(?P<id>[A-Za-z0-9_-]+)(?:/[^\s]*)?",
    re.IGNORECASE,
)
CHILD_RE = re.compile(r"\b(?P<child>Nysha|Navya)\b", re.IGNORECASE)


@dataclass(frozen=True)
class NewsletterHomeworkCandidate:
    title: str
    subject: str | None
    assigned_date: str
    due_date: str | None
    notes: str | None
    raw_text: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class NewsletterCalendarCandidate:
    title: str
    date: str
    start_time: str | None
    end_time: str | None
    all_day: bool
    description: str | None = None
    kind: str = "school_event"


@dataclass(frozen=True)
class NewsletterTaskCandidate:
    title: str
    notes: str | None
    due: str | None = None


@dataclass(frozen=True)
class NewsletterResource:
    label: str
    kind: Literal["book", "video", "song", "platform"]


@dataclass(frozen=True)
class NewsletterKnowledge:
    topics: tuple[str, ...]
    skills: tuple[str, ...]
    routines: tuple[str, ...]
    recommendations: tuple[str, ...]
    resources: tuple[NewsletterResource, ...]
    conversation_prompts: tuple[str, ...]


@dataclass(frozen=True)
class NewsletterParsed:
    child: str
    source_url: str
    source_id: str
    source_type: str
    title: str
    teacher: str | None
    newsletter_date: str
    text: str
    content_fingerprint: str
    homework: tuple[NewsletterHomeworkCandidate, ...]
    calendar: tuple[NewsletterCalendarCandidate, ...]
    tasks: tuple[NewsletterTaskCandidate, ...]
    knowledge: NewsletterKnowledge


@dataclass(frozen=True)
class MatchResult:
    status: str
    label: str
    item: dict[str, Any] | None = None
    differences: tuple[str, ...] = ()


@dataclass(frozen=True)
class NewsletterPreview:
    parsed: NewsletterParsed
    existing_import: dict[str, Any] | None
    homework_matches: tuple[MatchResult, ...]
    calendar_matches: tuple[MatchResult, ...]
    task_matches: tuple[MatchResult, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class NewsletterSaveResult:
    message: str
    saved: dict[str, list[str]]


@dataclass
class PendingNewsletterImport:
    preview: NewsletterPreview


class SQLiteSchoolNewsletterStore:
    def __init__(self, db_path: str | Path = DEFAULT_DB_FILE):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS school_newsletter_imports (
                    id TEXT PRIMARY KEY,
                    child TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    teacher TEXT,
                    newsletter_date TEXT NOT NULL,
                    content_fingerprint TEXT NOT NULL,
                    parsed_json TEXT NOT NULL,
                    saved_json TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(child, source_type, source_id, content_fingerprint)
                )
                """,
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_school_newsletter_imports_child_date
                ON school_newsletter_imports(child, newsletter_date, updated_at)
                """,
            )

    def find(self, *, child: str, source_type: str, source_id: str, content_fingerprint: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM school_newsletter_imports
                WHERE child = :child
                    AND source_type = :source_type
                    AND source_id = :source_id
                    AND content_fingerprint = :content_fingerprint
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                {
                    "child": child,
                    "source_type": source_type,
                    "source_id": source_id,
                    "content_fingerprint": content_fingerprint,
                },
            ).fetchone()
        return dict(row) if row is not None else None

    def upsert(self, parsed: NewsletterParsed, *, saved: dict[str, list[str]], status: str) -> dict[str, Any]:
        timestamp = datetime.now().astimezone().isoformat()
        payload = _parsed_payload(parsed)
        item = {
            "id": uuid4().hex,
            "child": parsed.child,
            "source_type": parsed.source_type,
            "source_id": parsed.source_id,
            "source_url": parsed.source_url,
            "title": parsed.title,
            "teacher": parsed.teacher,
            "newsletter_date": parsed.newsletter_date,
            "content_fingerprint": parsed.content_fingerprint,
            "parsed_json": json.dumps(payload, sort_keys=True),
            "saved_json": json.dumps(saved, sort_keys=True),
            "status": status,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO school_newsletter_imports (
                    id, child, source_type, source_id, source_url, title, teacher,
                    newsletter_date, content_fingerprint, parsed_json, saved_json,
                    status, created_at, updated_at
                )
                VALUES (
                    :id, :child, :source_type, :source_id, :source_url, :title,
                    :teacher, :newsletter_date, :content_fingerprint, :parsed_json,
                    :saved_json, :status, :created_at, :updated_at
                )
                ON CONFLICT(child, source_type, source_id, content_fingerprint) DO UPDATE SET
                    title = excluded.title,
                    teacher = excluded.teacher,
                    parsed_json = excluded.parsed_json,
                    saved_json = excluded.saved_json,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                item,
            )
            row = connection.execute(
                """
                SELECT *
                FROM school_newsletter_imports
                WHERE child = :child
                    AND source_type = :source_type
                    AND source_id = :source_id
                    AND content_fingerprint = :content_fingerprint
                """,
                item,
            ).fetchone()
        return dict(row)


class SchoolNewsletterImporter:
    def __init__(
        self,
        *,
        store: SQLiteSchoolNewsletterStore | None = None,
        homework_tools: HomeworkTools | None = None,
        calendar_tools: Any | None = None,
        task_tools: Any | None = None,
        n4os_root: Path = DEFAULT_N4OS_ROOT,
        homework_root: Path = DEFAULT_N4OS_HOMEWORK_ROOT,
        fetch_text: Callable[[str], str] | None = None,
    ) -> None:
        default_sources: Any | None = None
        if homework_tools is None or calendar_tools is None or task_tools is None:
            try:
                from dashboard_sources import build_default_sources

                default_sources = build_default_sources()
            except Exception:
                default_sources = None
        self.store = store or SQLiteSchoolNewsletterStore()
        source_homework_tools = getattr(default_sources, "homework_tools", None)
        if homework_tools is not None:
            self.homework_tools = homework_tools
        elif hasattr(source_homework_tools, "provider"):
            self.homework_tools = source_homework_tools
        else:
            self.homework_tools = HomeworkTools(SQLiteHomeworkProvider())
        self.calendar_tools = calendar_tools or getattr(default_sources, "calendar_tools", None)
        self.task_tools = task_tools or getattr(default_sources, "task_tools", None)
        if self.calendar_tools is not None and getattr(self.homework_tools, "calendar_tools", None) is None:
            self.homework_tools.calendar_tools = self.calendar_tools
        self.n4os_root = n4os_root
        self.homework_root = homework_root
        self.fetch_text = fetch_text or _fetch_google_slides_text
        self.pending: dict[str, PendingNewsletterImport] = {}

    def has_pending(self, key: str) -> bool:
        return key in self.pending

    def preview_from_message(self, message: str, *, key: str) -> str:
        child = _child_from_message(message)
        source_url = _source_url_from_message(message)
        if source_url is None:
            return "Please include the Google Slides newsletter link."
        parsed = parse_newsletter_text(
            self.fetch_text(source_url),
            child=child,
            source_url=source_url,
        )
        preview = self.build_preview(parsed)
        self.pending[key] = PendingNewsletterImport(preview=preview)
        return format_newsletter_preview(preview)

    def build_preview(self, parsed: NewsletterParsed) -> NewsletterPreview:
        warnings: list[str] = []
        existing_import = self.store.find(
            child=parsed.child,
            source_type=parsed.source_type,
            source_id=parsed.source_id,
            content_fingerprint=parsed.content_fingerprint,
        )
        homework_matches = tuple(self._homework_match(candidate, parsed) for candidate in parsed.homework)
        calendar_candidates = tuple(candidate for candidate in parsed.calendar if candidate.kind != "homework_due")
        calendar_matches = tuple(self._calendar_match(candidate, parsed, warnings) for candidate in calendar_candidates)
        task_matches = tuple(self._task_match(candidate, parsed, warnings) for candidate in parsed.tasks)
        return NewsletterPreview(
            parsed=parsed,
            existing_import=existing_import,
            homework_matches=homework_matches,
            calendar_matches=calendar_matches,
            task_matches=task_matches,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def save_pending(self, *, key: str, response: str) -> NewsletterSaveResult:
        normalized = " ".join(response.lower().strip(" .!").split())
        pending = self.pending.get(key)
        if pending is None:
            return NewsletterSaveResult("No school newsletter import is waiting for confirmation.", {})
        if normalized in NEWSLETTER_CANCEL_WORDS:
            self.pending.pop(key, None)
            return NewsletterSaveResult("Canceled school newsletter import.", {})
        if normalized not in NEWSLETTER_SAVE_WORDS:
            return NewsletterSaveResult("Reply `save` to add the newsletter updates, or `cancel`.", {})

        preview = pending.preview
        if preview.existing_import is not None and preview.existing_import.get("status") == "saved":
            changes: dict[str, list[str]] = {"knowledge": [], "observations": []}
            changes["knowledge"].extend(self._save_school_knowledge(preview.parsed))
            audit = self._save_audit_record(preview.parsed)
            if audit:
                changes["observations"].append(audit)
            saved = _merge_saved_results(preview.existing_import.get("saved_json"), changes)
            self.store.upsert(preview.parsed, saved=saved, status="saved")
            self.pending.pop(key, None)
            return NewsletterSaveResult(_format_save_result(changes), changes)
        saved: dict[str, list[str]] = {
            "homework": [],
            "calendar": [],
            "tasks": [],
            "knowledge": [],
            "observations": [],
        }
        for candidate, match in zip(preview.parsed.homework, preview.homework_matches):
            label = self._save_homework(candidate, match, preview.parsed)
            if label:
                saved["homework"].append(label)
        calendar_candidates = tuple(candidate for candidate in preview.parsed.calendar if candidate.kind != "homework_due")
        for candidate, match in zip(calendar_candidates, preview.calendar_matches):
            label = self._save_calendar(candidate, match, preview.parsed)
            if label:
                saved["calendar"].append(label)
        for candidate, match in zip(preview.parsed.tasks, preview.task_matches):
            label = self._save_task(candidate, match, preview.parsed)
            if label:
                saved["tasks"].append(label)
        saved["knowledge"].extend(self._save_school_knowledge(preview.parsed))
        audit = self._save_audit_record(preview.parsed)
        if audit:
            saved["observations"].append(audit)
        self.store.upsert(preview.parsed, saved=saved, status="saved")
        self.pending.pop(key, None)
        return NewsletterSaveResult(_format_save_result(saved), saved)

    def _homework_match(self, candidate: NewsletterHomeworkCandidate, parsed: NewsletterParsed) -> MatchResult:
        best_item: dict[str, Any] | None = None
        best_score = 0.0
        provider = self.homework_tools.provider
        for item in provider.list_items(child=parsed.child, limit=100):
            score = _homework_similarity(candidate, item)
            if score > best_score:
                best_score = score
                best_item = item
        if best_item is None or best_score < 0.72:
            return MatchResult("new", candidate.title)
        differences = tuple(_homework_differences(candidate, best_item))
        return MatchResult("match", str(best_item.get("title") or candidate.title), best_item, differences)

    def _calendar_match(
        self,
        candidate: NewsletterCalendarCandidate,
        parsed: NewsletterParsed,
        warnings: list[str],
    ) -> MatchResult:
        tools = self.calendar_tools
        if tools is None:
            return MatchResult("unchecked", candidate.title)
        try:
            start = datetime.combine(
                Date.fromisoformat(candidate.date),
                time.min,
                tzinfo=ZoneInfo(DEFAULT_TIMEZONE),
            )
            end = start + timedelta(days=1)
            events = _list_calendar_events_for_name(
                tools,
                calendar_name=f"{parsed.child} School Calendar",
                time_min=start.isoformat(),
                time_max=end.isoformat(),
            )
        except Exception as error:
            warnings.append(f"Calendar duplicate check unavailable: {error.__class__.__name__}.")
            return MatchResult("unchecked", candidate.title)
        for event in events:
            if _event_matches(candidate, event):
                return MatchResult("match", str(event.get("summary") or candidate.title), event)
        return MatchResult("new", candidate.title)

    def _task_match(
        self,
        candidate: NewsletterTaskCandidate,
        parsed: NewsletterParsed,
        warnings: list[str],
    ) -> MatchResult:
        del parsed
        tools = self.task_tools
        if tools is None:
            return MatchResult("unchecked", candidate.title)
        try:
            tasks = _list_all_open_tasks(tools)
        except Exception as error:
            warnings.append(f"Task duplicate check unavailable: {str(error) or error.__class__.__name__}.")
            return MatchResult("unchecked", candidate.title)
        for task in tasks:
            if _similarity(candidate.title, str(task.get("title") or "")) >= 0.78:
                return MatchResult("match", str(task.get("title") or candidate.title), task)
        return MatchResult("new", candidate.title)

    def _save_homework(
        self,
        candidate: NewsletterHomeworkCandidate,
        match: MatchResult,
        parsed: NewsletterParsed,
    ) -> str | None:
        provider = self.homework_tools.provider
        metadata = {
            **candidate.metadata,
            "school_newsletter": {
                "source_url": parsed.source_url,
                "source_id": parsed.source_id,
                "title": parsed.title,
                "newsletter_date": parsed.newsletter_date,
                "content_fingerprint": parsed.content_fingerprint,
            },
        }
        if match.item is not None:
            updated_fields = _homework_update_fields(candidate, match.item, metadata)
            if not updated_fields:
                return None
            updated = provider.update_assignment_details(
                homework_item_id=str(match.item["id"]),
                event_note=f"Updated from school newsletter: {parsed.title}.",
                **updated_fields,
            )
            if updated is not None:
                _write_markdown(provider, child=parsed.child, homework_root=self.homework_root)
                if "due_date" in updated_fields:
                    self.homework_tools._create_due_calendar_event(
                        updated,
                        {"calendar_name": f"{parsed.child} School Calendar"},
                    )
                return f"updated {updated['title']}"
            return None

        item = provider.capture_assignment(
            child=parsed.child,
            title=candidate.title,
            subject=candidate.subject,
            assigned_date=candidate.assigned_date,
            due_date=candidate.due_date,
            status="assigned",
            notes=candidate.notes,
            metadata=metadata,
            content_fingerprint=homework_content_fingerprint(candidate.raw_text),
            raw_input=candidate.raw_text,
            source="telegram_text",
            ocr_text=candidate.raw_text,
        )
        self.homework_tools._create_due_calendar_event(
            item,
            {"calendar_name": f"{parsed.child} School Calendar"},
        )
        _write_markdown(provider, child=parsed.child, homework_root=self.homework_root)
        return f"created {item['title']}"

    def _save_calendar(
        self,
        candidate: NewsletterCalendarCandidate,
        match: MatchResult,
        parsed: NewsletterParsed,
    ) -> str | None:
        if match.status == "match":
            return None
        tools = self.calendar_tools
        if tools is None:
            return None
        description = "\n".join(
            part
            for part in (
                candidate.description,
                f"Source: {parsed.source_url}",
            )
            if part
        )
        if candidate.all_day:
            start_date = Date.fromisoformat(candidate.date)
            response = tools.create_calendar_event(
                title=candidate.title,
                start_time=start_date.isoformat(),
                end_time=(start_date + timedelta(days=1)).isoformat(),
                all_day=True,
                description=description,
                calendar_name=f"{parsed.child} School Calendar",
                private_extended_properties=_source_metadata(parsed),
            )
        else:
            start = datetime.combine(Date.fromisoformat(candidate.date), _parse_clock(candidate.start_time or "08:00"))
            end = datetime.combine(Date.fromisoformat(candidate.date), _parse_clock(candidate.end_time or candidate.start_time or "08:30"))
            response = tools.create_calendar_event(
                title=candidate.title,
                start_time=start.isoformat(),
                end_time=end.isoformat(),
                description=description,
                calendar_name=f"{parsed.child} School Calendar",
                private_extended_properties=_source_metadata(parsed),
            )
        if response.get("status") == "ok":
            return f"created {candidate.title}"
        return None

    def _save_task(
        self,
        candidate: NewsletterTaskCandidate,
        match: MatchResult,
        parsed: NewsletterParsed,
    ) -> str | None:
        if match.status == "match":
            return None
        tools = self.task_tools
        if tools is None:
            return None
        notes = "\n".join(part for part in (candidate.notes, f"Source: {parsed.source_url}") if part)
        response = tools.create_task(
            title=candidate.title,
            notes=notes,
            due=candidate.due,
            metadata={"owner": "unknown", "tags": ["school", parsed.child.lower()]},
        )
        if response.get("status") == "ok":
            return f"created {candidate.title}"
        return None

    def _save_school_knowledge(self, parsed: NewsletterParsed) -> list[str]:
        year = _school_year(parsed.newsletter_date)
        base = self.n4os_root / "school" / parsed.child / year
        source = f"{parsed.title} ({parsed.source_url})"
        sections = (
            (
                base / "School Knowledge.md",
                "n4os/school/knowledge",
                "School Knowledge",
                _knowledge_section(parsed, source=source),
            ),
            (
                base / "Curriculum Map.md",
                "n4os/school/curriculum",
                f"{parsed.child} Curriculum Map",
                _curriculum_section(parsed, source=source),
            ),
            (
                base / "Resources.md",
                "n4os/school/resources",
                f"{parsed.child} School Resources",
                _resources_section(parsed, source=source),
            ),
            (
                base / "Conversation Starters.md",
                "n4os/school/conversations",
                f"{parsed.child} Conversation Starters",
                _conversation_section(parsed, source=source),
            ),
        )
        saved: list[str] = []
        for path, tag, title, section in sections:
            if not section:
                continue
            if _write_newsletter_section(path, tag=tag, title=title, parsed=parsed, section=section):
                saved.append(_relative_n4os_path(path, self.n4os_root))
        return saved

    def _save_audit_record(self, parsed: NewsletterParsed) -> str | None:
        observations_dir = self.n4os_root / "family" / "observations"
        observations_dir.mkdir(parents=True, exist_ok=True)
        month = parsed.newsletter_date[:7]
        path = observations_dir / f"{month}.md"
        marker = f"<!-- n4os-school-newsletter:{parsed.content_fingerprint} -->"
        existing = path.read_text(encoding="utf-8") if path.exists() else f"# {month} Observations\n"
        if marker in existing:
            return None
        lines = [
            "",
            marker,
            f"## {parsed.newsletter_date} School Newsletter - {parsed.child}",
            "",
            f"- School newsletter imported: {parsed.title} ({parsed.source_url})",
            f"- Structured knowledge: school/{parsed.child}/{_school_year(parsed.newsletter_date)}/",
        ]
        path.write_text(existing.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
        try:
            return path.relative_to(ROOT).as_posix()
        except ValueError:
            return path.name


def is_school_newsletter_message(message: str) -> bool:
    lowered = message.lower()
    return bool(
        SLIDES_URL_RE.search(message)
        and re.search(r"\b(school|teacher|class|room|newsletter)\b", lowered)
        and re.search(r"\b(import|parse|newsletter|school update)\b", lowered)
    )


def is_school_newsletter_followup(message: str) -> bool:
    normalized = " ".join(message.lower().strip(" .!").split())
    return normalized in NEWSLETTER_SAVE_WORDS or normalized in NEWSLETTER_CANCEL_WORDS


def parse_newsletter_text(text: str, *, child: str, source_url: str) -> NewsletterParsed:
    source_id = _source_id_from_url(source_url)
    cleaned_text = _normalize_export_text(text)
    lines = [line.strip() for line in cleaned_text.splitlines() if line.strip()]
    newsletter_date = _newsletter_date(lines)
    title = lines[0] if lines else "School Newsletter"
    teacher = _teacher(lines)
    homework = _parse_homework(cleaned_text, child=child, newsletter_date=newsletter_date, source_url=source_url)
    calendar = _parse_calendar(cleaned_text, newsletter_date=newsletter_date)
    tasks = _parse_tasks(cleaned_text, newsletter_date=newsletter_date)
    knowledge = _parse_newsletter_knowledge(cleaned_text)
    return NewsletterParsed(
        child=child,
        source_url=source_url,
        source_id=source_id,
        source_type="google_slides",
        title=title,
        teacher=teacher,
        newsletter_date=newsletter_date,
        text=cleaned_text,
        content_fingerprint=hashlib.sha256(cleaned_text.encode("utf-8")).hexdigest(),
        homework=tuple(homework),
        calendar=tuple(calendar),
        tasks=tuple(tasks),
        knowledge=knowledge,
    )


def format_newsletter_preview(preview: NewsletterPreview) -> str:
    parsed = preview.parsed
    lines = [
        f"School newsletter parsed for {parsed.child}: {parsed.title}, {parsed.newsletter_date}",
        "",
    ]
    if preview.existing_import is not None:
        lines.extend(["Already imported:", f"- {parsed.title} from {parsed.newsletter_date}", ""])

    _add_preview_group(lines, "Homework", preview.homework_matches)
    _add_preview_group(lines, "Calendar", preview.calendar_matches)
    _add_preview_group(lines, "Reminders", preview.task_matches)
    if parsed.knowledge.topics:
        lines.append("Learning/focus:")
        lines.extend([f"- {item}" for item in parsed.knowledge.topics[:10]])
        lines.append("")
    _add_text_group(lines, "Skills", parsed.knowledge.skills, limit=8)
    _add_text_group(lines, "Classroom routines", parsed.knowledge.routines, limit=6)
    if parsed.knowledge.resources:
        lines.append("Books/media/resources:")
        lines.extend([f"- {item.kind.title()}: {item.label}" for item in parsed.knowledge.resources[:10]])
        lines.append("")
    if parsed.knowledge.recommendations:
        lines.append("Optional routines (saved as context, not created as tasks):")
        lines.extend([f"- {item}" for item in parsed.knowledge.recommendations[:6]])
        lines.append("")
    _add_text_group(lines, "Conversation prompts", parsed.knowledge.conversation_prompts, limit=5)
    if preview.warnings:
        lines.append("Could not fully check:")
        lines.extend([f"- {warning}" for warning in preview.warnings])
        lines.append("")
    lines.append("Reply `save` to add only new/enrichment items, or `cancel`.")
    return "\n".join(lines).strip()


def _add_text_group(lines: list[str], title: str, items: tuple[str, ...], *, limit: int) -> None:
    if not items:
        return
    lines.append(f"{title}:")
    lines.extend([f"- {item}" for item in items[:limit]])
    lines.append("")


def _add_preview_group(lines: list[str], title: str, matches: tuple[MatchResult, ...]) -> None:
    if not matches:
        return
    lines.append(f"{title}:")
    for match in matches:
        prefix = {
            "match": "Already present",
            "new": "New",
            "unchecked": "New/check unavailable",
        }.get(match.status, "New")
        line = f"- {prefix}: {match.label}"
        if match.differences:
            line += f" ({'; '.join(match.differences)})"
        lines.append(line)
    lines.append("")


def _format_save_result(saved: dict[str, list[str]]) -> str:
    lines = ["Saved school newsletter updates."]
    for label, items in (
        ("Homework", saved.get("homework", [])),
        ("Calendar", saved.get("calendar", [])),
        ("Tasks", saved.get("tasks", [])),
        ("School knowledge", saved.get("knowledge", [])),
        ("Import audit", saved.get("observations", [])),
    ):
        if items:
            lines.append(f"{label}:")
            lines.extend([f"- {item}" for item in items])
    if len(lines) == 1:
        lines.append("No new items were added.")
    return "\n".join(lines)


def _merge_saved_results(raw_saved: object, changes: dict[str, list[str]]) -> dict[str, list[str]]:
    try:
        decoded = json.loads(str(raw_saved or "{}"))
    except (TypeError, ValueError):
        decoded = {}
    saved = {
        str(key): [str(item) for item in value]
        for key, value in decoded.items()
        if isinstance(key, str) and isinstance(value, list)
    }
    for key, items in changes.items():
        saved[key] = list(dict.fromkeys([*saved.get(key, []), *items]))
    return saved


def _school_year(newsletter_date: str) -> str:
    parsed = Date.fromisoformat(newsletter_date)
    start = parsed.year if parsed.month >= 7 else parsed.year - 1
    return f"{start}-{start + 1}"


def _knowledge_section(parsed: NewsletterParsed, *, source: str) -> str:
    knowledge = parsed.knowledge
    groups = (
        ("Current Topics", knowledge.topics),
        ("Current Skills", knowledge.skills),
        ("Classroom Routines", knowledge.routines),
        ("At-Home Recommendations", knowledge.recommendations),
    )
    return _newsletter_section_body(parsed, source=source, groups=groups)


def _curriculum_section(parsed: NewsletterParsed, *, source: str) -> str:
    groups = (
        ("Topics", parsed.knowledge.topics),
        ("Skills", parsed.knowledge.skills),
    )
    return _newsletter_section_body(parsed, source=source, groups=groups)


def _resources_section(parsed: NewsletterParsed, *, source: str) -> str:
    resources = tuple(f"{item.kind.title()}: {item.label}" for item in parsed.knowledge.resources)
    groups = (
        ("Books, Media, And Platforms", resources),
        ("Practice Recommendations", parsed.knowledge.recommendations),
    )
    return _newsletter_section_body(parsed, source=source, groups=groups)


def _conversation_section(parsed: NewsletterParsed, *, source: str) -> str:
    groups = (("Source-Backed Prompts", parsed.knowledge.conversation_prompts),)
    return _newsletter_section_body(parsed, source=source, groups=groups)


def _newsletter_section_body(
    parsed: NewsletterParsed,
    *,
    source: str,
    groups: tuple[tuple[str, tuple[str, ...]], ...],
) -> str:
    populated = [(heading, items) for heading, items in groups if items]
    if not populated:
        return ""
    lines = [f"### {parsed.newsletter_date} - {parsed.title}", "", f"- {source}"]
    if parsed.teacher:
        lines.append(f"- Teacher/class: {parsed.teacher}")
    for heading, items in populated:
        lines.extend(["", f"#### {heading}", "", *(f"- {item}" for item in items)])
    return "\n".join(lines)


def _write_newsletter_section(
    path: Path,
    *,
    tag: str,
    title: str,
    parsed: NewsletterParsed,
    section: str,
) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else _school_note_header(tag=tag, title=title)
    marker = f"n4os-school-newsletter:{parsed.content_fingerprint}"
    start_marker = f"<!-- {marker}:start -->"
    end_marker = f"<!-- {marker}:end -->"
    block = f"{start_marker}\n{section.strip()}\n{end_marker}"
    without_existing = _remove_marked_block(existing, start_marker=start_marker, end_marker=end_marker)
    updated = _insert_newsletter_block(without_existing, block)
    if updated == existing:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def _school_note_header(*, tag: str, title: str) -> str:
    return "\n".join(
        [
            "---",
            "tags:",
            f'  - "{tag}"',
            "links:",
            '  - "[[README|N4OS]]"',
            "---",
            "",
            f"# {title}",
            "",
        ]
    )


def _remove_marked_block(existing: str, *, start_marker: str, end_marker: str) -> str:
    start = existing.find(start_marker)
    if start < 0:
        return existing
    end = existing.find(end_marker, start + len(start_marker))
    if end < 0:
        return existing
    end += len(end_marker)
    return (existing[:start].rstrip() + "\n\n" + existing[end:].lstrip()).rstrip() + "\n"


def _insert_newsletter_block(existing: str, block: str) -> str:
    heading = "## Newsletter Updates"
    if heading in existing:
        position = existing.index(heading) + len(heading)
        return (existing[:position].rstrip() + "\n\n" + block + "\n\n" + existing[position:].lstrip()).rstrip() + "\n"
    title_match = re.search(r"(?m)^# .+$", existing)
    if title_match is None:
        return (existing.rstrip() + f"\n\n{heading}\n\n{block}\n").lstrip()
    position = title_match.end()
    return (existing[:position].rstrip() + f"\n\n{heading}\n\n{block}\n\n" + existing[position:].lstrip()).rstrip() + "\n"


def _relative_n4os_path(path: Path, n4os_root: Path) -> str:
    try:
        return path.relative_to(n4os_root).as_posix()
    except ValueError:
        return path.name


def _fetch_google_slides_text(source_url: str) -> str:
    source_id = _source_id_from_url(source_url)
    export_url = f"https://docs.google.com/presentation/d/{source_id}/export/txt"
    with urllib.request.urlopen(export_url, timeout=20) as response:
        return response.read().decode("utf-8")


def _source_url_from_message(message: str) -> str | None:
    match = SLIDES_URL_RE.search(message)
    return match.group(0) if match else None


def _source_id_from_url(source_url: str) -> str:
    match = SLIDES_URL_RE.search(source_url)
    if match:
        return match.group("id")
    path_parts = [part for part in urlparse(source_url).path.split("/") if part]
    if "d" in path_parts:
        index = path_parts.index("d")
        if index + 1 < len(path_parts):
            return path_parts[index + 1]
    raise ValueError("Unsupported Google Slides newsletter URL.")


def _child_from_message(message: str) -> str:
    match = CHILD_RE.search(message)
    if not match:
        return "Nysha"
    return match.group("child").title()


def _normalize_export_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _newsletter_date(lines: list[str]) -> str:
    for line in lines[:8]:
        parsed = _parse_date_text(line, fallback_year=None)
        if parsed:
            return parsed
    return datetime.now().astimezone().date().isoformat()


def _teacher(lines: list[str]) -> str | None:
    for line in lines[:8]:
        if re.search(r"\b(?:mrs|ms|mr|miss|teacher)\b", line, re.IGNORECASE):
            return line
    return None


def _parse_homework(
    text: str,
    *,
    child: str,
    newsletter_date: str,
    source_url: str,
) -> list[NewsletterHomeworkCandidate]:
    del child
    candidates: list[NewsletterHomeworkCandidate] = []
    match = re.search(
        r"(?P<title>[A-Z][A-Za-z0-9 ]+):\s*(?P<kind>project|assignment|packet|book)?\s*"
        r"due\s+(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?"
        r"(?P<date>[A-Za-z]+\s+\d{1,2},\s+\d{4})",
        text,
        re.IGNORECASE,
    )
    if match:
        due_date = _parse_date_text(match.group("date"), fallback_year=None)
        title = f"{match.group('title').strip()} {match.group('kind') or 'project'}".strip()
        notes = _section_after(text, match.end(), stop_headings=("Growth Mindset and Art", "Wear Layers", "Reminders"))
        raw = f"{match.group(0)}\n{notes}".strip()
        fingerprint = homework_content_fingerprint(raw)
        metadata = build_homework_metadata(
            {
                "title": title,
                "subject": "School",
                "raw_input": raw,
                "ocr_text": raw,
                "notes": notes,
            },
            content_fingerprint=fingerprint,
        )
        metadata.update({"source_url": source_url, "source": "school_newsletter"})
        candidates.append(
            NewsletterHomeworkCandidate(
                title=title,
                subject="School",
                assigned_date=newsletter_date,
                due_date=due_date,
                notes=notes,
                raw_text=raw,
                metadata=metadata,
            )
        )
    return candidates


def _parse_calendar(text: str, *, newsletter_date: str) -> list[NewsletterCalendarCandidate]:
    candidates: list[NewsletterCalendarCandidate] = []
    back_to_school = re.search(
        r"Back to School Night is (?P<date>[A-Za-z]+,\s+[A-Za-z]+\s+\d{1,2},\s+\d{4})\.\s*"
        r"(?P<schedule>.+?Teacher Presentations in classrooms)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if back_to_school:
        date_text = re.sub(r"^[A-Za-z]+,\s+", "", back_to_school.group("date"))
        parsed_date = _parse_date_text(date_text, fallback_year=None)
        start, end = _time_range(back_to_school.group(0))
        if parsed_date:
            candidates.append(
                NewsletterCalendarCandidate(
                    title="Back to School Night",
                    date=parsed_date,
                    start_time=start,
                    end_time=end,
                    all_day=False,
                    description=" ".join(back_to_school.group("schedule").split()),
                )
            )
    for line in text.splitlines():
        reminder = re.match(
            r"^\s*(?P<weekday>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
            r"(?P<date>[A-Za-z]+\s+\d{1,2})(?:\s+\([^)]+\))?:\s*(?P<body>.+?)\s*$",
            line,
            re.IGNORECASE,
        )
        if not reminder:
            continue
        body = reminder.group("body").strip()
        parsed_date = _parse_date_text(reminder.group("date"), fallback_year=int(newsletter_date[:4]))
        if not parsed_date:
            continue
        kind = "homework_due" if "due" in body.lower() else "school_event"
        candidates.append(
            NewsletterCalendarCandidate(
                title=_calendar_title(body),
                date=parsed_date,
                start_time=None,
                end_time=None,
                all_day=True,
                kind=kind,
            )
        )
    return _dedupe_calendar(candidates)


def _parse_tasks(text: str, *, newsletter_date: str) -> list[NewsletterTaskCandidate]:
    tasks: list[NewsletterTaskCandidate] = []
    if "wear layers" in text.lower() or "bring a sweater" in text.lower():
        tasks.append(
            NewsletterTaskCandidate(
                title="Pack sweater/layers for school",
                notes="Teacher reminder: classroom may feel cold compared to outdoor temperatures.",
            )
        )
    if "headphones for chromebooks" in text.lower():
        due = _next_named_weekday(newsletter_date, "monday")
        tasks.append(
            NewsletterTaskCandidate(
                title="Send comfortable headphones for Chromebook",
                notes="USB-A or audio jack headphones are okay.",
                due=due,
            )
        )
    if "picnic tables after school" in text.lower():
        tasks.append(
            NewsletterTaskCandidate(
                title="Remember school dismissal pickup at picnic tables",
                notes="Grades 1-5 wait at the lunch picnic tables after school.",
            )
        )
    return tasks


def _parse_newsletter_knowledge(text: str) -> NewsletterKnowledge:
    topics = tuple(_parse_learning_context(text))
    skills = tuple(_labels_for_rules(text, _SKILL_RULES))
    routines = tuple(_labels_for_rules(text, _ROUTINE_RULES))
    recommendations = tuple(_labels_for_rules(text, _RECOMMENDATION_RULES))
    resources = tuple(_parse_resources(text))
    prompts = tuple(_conversation_prompts(topics, skills, routines, resources))
    return NewsletterKnowledge(
        topics=topics,
        skills=skills,
        routines=routines,
        recommendations=recommendations,
        resources=resources,
        conversation_prompts=prompts,
    )


def _labels_for_rules(text: str, rules: tuple[tuple[str, str], ...]) -> list[str]:
    return [label for label, pattern in rules if re.search(pattern, text, re.IGNORECASE | re.DOTALL)]


_SKILL_RULES = (
    (
        "Phonics: visual and blending drills; short vowel sounds a, e, and i",
        r"phonics drills.+?short vowel sounds?\s+a,\s*e,\s*and\s*i",
    ),
    (
        "Poetry: concrete poems and speaking with a clear presentation voice",
        r"presentation voice|concrete poem",
    ),
    (
        "Math: place value; standard, expanded, and word form; even and odd",
        r"place value.+?standard form.+?expanded form.+?word form",
    ),
    (
        "Math: money notation, 100-grid patterns, tens partners, and Math Mountains",
        r"writing money.+?(?:100 grid|tens partners|Math Mountains)",
    ),
    (
        "Art: self-portraits, color theory, art elements, and types of lines",
        r"self portraits|elements of art|color wheel",
    ),
)

_ROUTINE_RULES = (
    (
        "Morning circle meetings support emotional check-ins, sharing, and public speaking",
        r"circle meeting.+?(?:feeling|share|public speaking)",
    ),
    (
        "The class practices a poem or song weekly and presents it on Fridays",
        r"every week.+?(?:poem|song).+?on Fridays.+?present",
    ),
)

_RECOMMENDATION_RULES = (
    (
        "LEXIA recommends about 40 minutes of practice per week at home or school",
        r"LEXIA recommends about 40 minutes of practice per week",
    ),
)


def _parse_learning_context(text: str) -> list[str]:
    context = []
    rules = (
        ("Classroom community and respectful communication", r"classroom community|communicate respectfully"),
        ("PBIS behavior expectations and playground rules", r"PBIS|Playground Rules"),
        ("Growth mindset and perseverance", r"growth mindset|persevere"),
        (
            "Social-emotional check-ins, friendship, and calming strategies",
            r"circle meeting|positive qualities of a friend|belly breathing|strong emotions",
        ),
        (
            "Character: responsibility, honesty, integrity, and decision-making",
            r"importance of honesty and integrity|learns how to be responsible|plan ahead and make good decisions",
        ),
        (
            "Language arts: grammar, phonics, and short vowel sounds",
            r"Language Arts.+?(?:compound words|phonics|short vowel)",
        ),
        (
            "Poetry and public speaking",
            r"present the poem|presentation voice|concrete poem",
        ),
        ("Math: place value, base 10 patterns, even and odd", r"Place Value|base 10|even and odd"),
        (
            "Math: number forms, money, number-grid patterns, and Math Mountains",
            r"standard form.+?expanded form|writing money|100 grid|Math Mountains",
        ),
        ("LEXIA English-language practice", r"LEXIA diagnostic|LEXIA lessons"),
        ("School safety and fire-drill procedures", r"fire drill"),
        ("Art: self-portraits, color theory, and line", r"self portraits|elements of art|color wheel"),
        ("Fine motor and listening practice through directed drawing", r"fine motor skills|following directions"),
    )
    for label, pattern in rules:
        if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
            context.append(label)
    return context


def _parse_books(text: str) -> list[str]:
    books: list[str] = []
    sentence_patterns = (
        r"(?:^|[.!?]\s+)(?:On\s+\w+\s+|Today\s+)?we read\s+(?P<title>[^.!?\n]+)",
        r"(?:^|[.!?]\s+)I read aloud (?:a book called\s+)?(?P<title>[^.!?\n]+)",
    )
    read_aloud_pattern = re.compile(
        r"(?:^|[.!?]\s+)(?P<title>[A-Z][^.!?\n|]{1,100}?)(?:\s*\|\s*Kids Book)?\s+Read Aloud\b",
        re.IGNORECASE,
    )

    for line in text.splitlines():
        for pattern in sentence_patterns:
            for match in re.finditer(pattern, line, re.IGNORECASE):
                title = _clean_book_title(match.group("title"))
                if title:
                    books.append(title)
        for match in read_aloud_pattern.finditer(line):
            title = _clean_book_title(match.group("title"))
            if title:
                books.append(title)

    return list(dict.fromkeys(books))


def _parse_resources(text: str) -> list[NewsletterResource]:
    resources = [NewsletterResource(label=title, kind="book") for title in _parse_books(text)]
    for match in re.finditer(r"\bwatched (?:a|an|the) (?P<label>[A-Z][A-Za-z0-9 '&-]{1,80}?) video\b", text):
        resources.append(NewsletterResource(label=match.group("label").strip(), kind="video"))
    for line in text.splitlines():
        song = re.fullmatch(r"(?P<label>[A-Z][^.!?\n]{1,100}\bSong(?:\s*\([^)]+\))?)", line.strip())
        if song:
            resources.append(NewsletterResource(label=song.group("label"), kind="song"))
    for platform in ("LEXIA", "IXL", "iReady", "Typing.com"):
        if re.search(rf"\b{re.escape(platform)}\b", text, re.IGNORECASE):
            resources.append(NewsletterResource(label=platform, kind="platform"))
    unique: dict[tuple[str, str], NewsletterResource] = {}
    for resource in resources:
        unique.setdefault((resource.kind.casefold(), resource.label.casefold()), resource)
    return list(unique.values())


def _conversation_prompts(
    topics: tuple[str, ...],
    skills: tuple[str, ...],
    routines: tuple[str, ...],
    resources: tuple[NewsletterResource, ...],
) -> list[str]:
    prompts: list[str] = []
    books = [resource.label for resource in resources if resource.kind == "book"]
    if books:
        prompts.append(f"What do you remember about {books[0]}?")
    joined = " ".join((*topics, *skills, *routines)).casefold()
    candidates = (
        ("circle meeting", "What did someone share during circle meeting?"),
        ("honesty", "Which choice showed kindness, honesty, or responsibility this week?"),
        ("standard, expanded", "Can you show one number in standard, expanded, and word form?"),
        ("presentation voice", "What helped you use a clear presentation voice?"),
        ("color theory", "Which colors or types of lines did you use in art?"),
    )
    prompts.extend(prompt for cue, prompt in candidates if cue in joined)
    return list(dict.fromkeys(prompts))[:5]


def _clean_book_title(value: str) -> str | None:
    title = " ".join(value.strip(" “”\"'|:-").split())
    if not title or len(title.split()) > 14:
        return None
    # Newsletter prose such as "we read phonics poems" is not a named work.
    if not title[0].isupper():
        return None
    return title.replace("’", "'")


def _section_after(text: str, start: int, *, stop_headings: tuple[str, ...]) -> str | None:
    tail = text[start:]
    stop_positions = [tail.find(heading) for heading in stop_headings if tail.find(heading) >= 0]
    if stop_positions:
        tail = tail[: min(stop_positions)]
    cleaned = " ".join(tail.split()).strip()
    return cleaned or None


def _parse_date_text(value: str, *, fallback_year: int | None) -> str | None:
    cleaned = value.strip().rstrip(".,")
    formats = ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y")
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            pass
    if fallback_year is not None:
        for fmt in ("%B %d", "%b %d"):
            try:
                return datetime.strptime(f"{cleaned} {fallback_year}", f"{fmt} %Y").date().isoformat()
            except ValueError:
                pass
    return None


def _time_range(value: str) -> tuple[str | None, str | None]:
    matches = re.findall(r"\b(?P<start>\d{1,2}:\d{2})\s*-\s*(?P<end>\d{1,2}:\d{2})\b", value)
    if not matches:
        return None, None
    assume_pm = re.search(r"\b(?:pm|p\.m\.|evening|night)\b", value, re.IGNORECASE) is not None
    starts = [_to_24_hour(start, assume_pm=assume_pm) for start, _ in matches]
    ends = [_to_24_hour(end, assume_pm=assume_pm) for _, end in matches]
    return starts[0], ends[-1]


def _to_24_hour(value: str, *, assume_pm: bool) -> str:
    hour, minute = [int(part) for part in value.split(":", 1)]
    if assume_pm and 1 <= hour < 12:
        hour += 12
    return f"{hour:02d}:{minute:02d}"


def _parse_clock(value: str) -> time:
    hour, minute = [int(part) for part in value.split(":", 1)]
    return time(hour, minute)


def _calendar_title(body: str) -> str:
    cleaned = " ".join(body.split()).strip()
    if cleaned.lower() == "no school":
        return "No school"
    if cleaned.lower().startswith("wear "):
        return cleaned[:1].upper() + cleaned[1:]
    return cleaned


def _dedupe_calendar(candidates: list[NewsletterCalendarCandidate]) -> list[NewsletterCalendarCandidate]:
    seen = set()
    result = []
    for candidate in candidates:
        key = (candidate.title.lower(), candidate.date)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _next_named_weekday(start_date: str, weekday: str) -> str:
    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    start = Date.fromisoformat(start_date)
    target = weekdays[weekday.lower()]
    days = (target - start.weekday()) % 7
    if days == 0:
        days = 7
    return (start + timedelta(days=days)).isoformat()


def _homework_similarity(candidate: NewsletterHomeworkCandidate, item: dict[str, Any]) -> float:
    item_text = " ".join(
        str(item.get(key) or "")
        for key in ("title", "subject", "due_date", "week_range", "daily_work", "notes", "raw_input")
    )
    title_in_item = _contains_title_terms(candidate.title, item_text)
    parts = [
        _similarity(candidate.title, str(item.get("title") or "")),
        1.0 if candidate.due_date and candidate.due_date == item.get("due_date") else 0.0,
        _similarity(str(candidate.subject or ""), str(item.get("subject") or "")),
    ]
    if title_in_item and parts[1] >= 1.0:
        return 0.92
    if title_in_item:
        return max(0.78, parts[0])
    return max(parts[0] * 0.65 + parts[1] * 0.25 + parts[2] * 0.10, parts[0])


def _homework_differences(candidate: NewsletterHomeworkCandidate, item: dict[str, Any]) -> list[str]:
    differences = []
    fields = (
        ("due_date", candidate.due_date, "due date"),
        ("notes", candidate.notes, "newsletter notes"),
        ("subject", candidate.subject, "subject"),
    )
    for key, candidate_value, label in fields:
        if candidate_value and not item.get(key):
            differences.append(f"can add {label}")
        elif key == "notes" and candidate_value and item.get(key) and str(candidate_value) not in str(item.get(key)):
            differences.append("can add newsletter notes")
        elif key == "subject" and candidate_value and item.get(key):
            continue
        elif candidate_value and item.get(key) and str(item.get(key)) != str(candidate_value):
            differences.append(f"possible {label} conflict")
    return differences


def _homework_update_fields(
    candidate: NewsletterHomeworkCandidate,
    item: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key, value in (
        ("subject", candidate.subject),
        ("due_date", candidate.due_date),
        ("week_range", None),
        ("daily_work", None),
    ):
        if value and not item.get(key):
            fields[key] = value
    notes = _merged_notes(str(item.get("notes") or ""), candidate.notes)
    if notes != str(item.get("notes") or ""):
        fields["notes"] = notes
    if _metadata_update_changes(item.get("metadata_json"), metadata):
        fields["metadata"] = metadata
    return fields


def _merged_notes(current: str, update: str | None) -> str | None:
    if not update:
        return current or None
    if update in current:
        return current or None
    if not current:
        return update
    return f"{current}; Newsletter: {update}"


def _metadata_update_changes(current_json: Any, metadata: dict[str, Any]) -> bool:
    current = _metadata_from_json(current_json)
    merged = {**current, **metadata}
    return merged != current


def _metadata_from_json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _event_matches(candidate: NewsletterCalendarCandidate, event: dict[str, Any]) -> bool:
    title = str(event.get("summary") or "")
    candidate_title = _identity(candidate.title)
    event_title = _identity(title)
    title_matches = (
        _similarity(candidate.title, title) >= 0.78
        or bool(candidate_title) and candidate_title in event_title
        or bool(event_title) and event_title in candidate_title
    )
    if not title_matches:
        return False
    start = event.get("start") if isinstance(event.get("start"), dict) else {}
    event_date = str(start.get("date") or start.get("dateTime") or "")[:10]
    return event_date == candidate.date


def _list_calendar_events_for_name(
    tools: Any,
    *,
    calendar_name: str,
    time_min: str,
    time_max: str,
) -> list[dict[str, Any]]:
    provider = getattr(tools, "provider", None)
    calendar_id_for_name = getattr(provider, "_calendar_id_for_name", None)
    service = getattr(provider, "service", None)
    if callable(calendar_id_for_name) and service is not None:
        calendar_ids = [calendar_id_for_name(calendar_name)]
        default_calendar_id = str(getattr(provider, "calendar_id", "") or "").strip()
        if default_calendar_id and default_calendar_id not in calendar_ids:
            calendar_ids.append(default_calendar_id)
        events: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for calendar_id in calendar_ids:
            response = (
                service.events()
                .list(
                    calendarId=calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    maxResults=20,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            for event in response.get("items", []):
                event_id = str(event.get("id") or f"{event.get('summary')}:{event.get('start')}")
                if event_id in seen_ids:
                    continue
                seen_ids.add(event_id)
                events.append(event)
        return events

    response = tools.list_calendar_events(
        time_min=time_min,
        time_max=time_max,
        max_results=20,
    )
    if response.get("status") != "ok":
        raise RuntimeError(str(response.get("message") or "Calendar duplicate check unavailable."))
    return list(response.get("data", {}).get("events", []))


def _list_all_open_tasks(tools: Any) -> list[dict[str, Any]]:
    list_task_lists = getattr(tools, "list_task_lists", None)
    if not callable(list_task_lists):
        response = tools.list_tasks(show_completed=False)
        if response.get("status") != "ok":
            raise RuntimeError(str(response.get("message") or "Task duplicate check unavailable."))
        return list(response.get("data", {}).get("tasks", []))

    lists_response = list_task_lists()
    if lists_response.get("status") != "ok":
        raise RuntimeError(str(lists_response.get("message") or "Task list lookup unavailable."))
    tasks: list[dict[str, Any]] = []
    for task_list in lists_response.get("data", {}).get("task_lists", []):
        task_list_id = str(task_list.get("id") or "").strip()
        if not task_list_id:
            continue
        response = tools.list_tasks(task_list_id=task_list_id, show_completed=False)
        if response.get("status") == "ok":
            tasks.extend(response.get("data", {}).get("tasks", []))
            continue
        raise RuntimeError(str(response.get("message") or f"Task list {task_list_id} lookup unavailable."))
    return tasks


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _identity(left), _identity(right)).ratio()


def _identity(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _contains_title_terms(title: str, haystack: str) -> bool:
    title_terms = [
        term
        for term in re.findall(r"[a-z0-9]+", title.lower())
        if term not in {"project", "homework", "assignment", "packet", "book"}
    ]
    haystack_terms = set(re.findall(r"[a-z0-9]+", haystack.lower()))
    return bool(title_terms) and all(term in haystack_terms for term in title_terms)


def _source_metadata(parsed: NewsletterParsed) -> dict[str, str]:
    return {
        "n4os_domain": "school_newsletter",
        "n4os_source_id": parsed.source_id,
        "n4os_source_url": parsed.source_url,
        "n4os_content_fingerprint": parsed.content_fingerprint,
    }


def _parsed_payload(parsed: NewsletterParsed) -> dict[str, Any]:
    return {
        "child": parsed.child,
        "source_url": parsed.source_url,
        "title": parsed.title,
        "teacher": parsed.teacher,
        "newsletter_date": parsed.newsletter_date,
        "homework": [item.__dict__ for item in parsed.homework],
        "calendar": [item.__dict__ for item in parsed.calendar],
        "tasks": [item.__dict__ for item in parsed.tasks],
        "knowledge": {
            "topics": list(parsed.knowledge.topics),
            "skills": list(parsed.knowledge.skills),
            "routines": list(parsed.knowledge.routines),
            "recommendations": list(parsed.knowledge.recommendations),
            "resources": [item.__dict__ for item in parsed.knowledge.resources],
            "conversation_prompts": list(parsed.knowledge.conversation_prompts),
        },
    }

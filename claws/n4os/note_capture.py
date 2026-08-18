from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from zoneinfo import ZoneInfo


DEFAULT_TIMEZONE = "America/Los_Angeles"
N4OS_ROOT = Path(__file__).resolve().parents[2] / "n4os"
QUICK_NOTES_PATH = N4OS_ROOT / "learnings" / "Quick Notes.md"
LEARNINGS_ROOT = N4OS_ROOT / "learnings"
INBOX_PATH = LEARNINGS_ROOT / "Inbox.md"


@dataclass(frozen=True)
class CapturedNote:
    kind: str
    path: Path
    title: str


def capture_note(
    body: str,
    *,
    now: datetime | None = None,
    source: str = "telegram_text",
    n4os_root: Path | None = None,
) -> CapturedNote:
    timestamp = _default_now(now)
    parsed = _parse_capture_body(body)
    root = n4os_root or N4OS_ROOT
    if parsed.kind == "quick":
        return _append_quick_note(parsed.title, parsed.content, timestamp, source, root)
    if parsed.kind == "learning":
        return _create_learning_note(parsed.title, parsed.content, timestamp, source, root)
    return _append_inbox_note(parsed.title, parsed.content, timestamp, source, root)


@dataclass(frozen=True)
class ParsedCapture:
    kind: str
    title: str
    content: str


def _default_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
    if value.tzinfo is None:
        return value.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
    return value.astimezone(ZoneInfo(DEFAULT_TIMEZONE))


def _parse_capture_body(body: str) -> ParsedCapture:
    cleaned = body.strip()
    if not cleaned:
        return ParsedCapture(kind="inbox", title="Untitled Note", content="")

    words = cleaned.split(maxsplit=1)
    first = words[0].lower().strip(":")
    if first in {"quick", "learning", "inbox"}:
        kind = first
        remainder = words[1].strip() if len(words) > 1 else ""
    else:
        kind = "inbox"
        remainder = cleaned

    title, content = _split_title_content(remainder)
    return ParsedCapture(kind=kind, title=title, content=content)


def _split_title_content(text: str) -> tuple[str, str]:
    cleaned = text.strip()
    if not cleaned:
        return "Untitled Note", ""
    if ":" in cleaned:
        title, content = cleaned.split(":", 1)
        title = title.strip() or _title_from_text(content)
        content = content.strip()
        return title, content or title
    return _title_from_text(cleaned), cleaned


def _title_from_text(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", text)
    if not words:
        return "Untitled Note"
    return " ".join(words[:8])


def _append_quick_note(
    title: str,
    content: str,
    now: datetime,
    source: str,
    n4os_root: Path,
) -> CapturedNote:
    quick_notes_path = _quick_notes_path(n4os_root)
    _ensure_quick_notes_file(quick_notes_path)
    date_heading = f"## {now.date().isoformat()}"
    entry = f"\n\n### {title}\n\n{content.strip()}\n\nSource: {source}"
    existing = quick_notes_path.read_text(encoding="utf-8")
    if date_heading in existing:
        updated = existing.rstrip() + entry + "\n"
    else:
        updated = existing.rstrip() + f"\n\n{date_heading}" + entry + "\n"
    quick_notes_path.write_text(updated, encoding="utf-8")
    return CapturedNote(kind="quick", path=quick_notes_path, title=title)


def _create_learning_note(
    title: str,
    content: str,
    now: datetime,
    source: str,
    n4os_root: Path,
) -> CapturedNote:
    learnings_root = _learnings_root(n4os_root)
    learnings_root.mkdir(parents=True, exist_ok=True)
    path = _unique_learning_path(title, now, learnings_root)
    text = "\n".join(
        [
            "---",
            "tags:",
            '  - "n4os/learning"',
            '  - "n4os/captured"',
            "links:",
            '  - "[[playbooks/AI|AI Playbook]]"',
            '  - "[[PRIORITIES]]"',
            '  - "[[MISSION]]"',
            "---",
            "",
            f"# {title}",
            "",
            "## Source",
            "",
            f"Captured from {source} on {now.date().isoformat()}.",
            "",
            "## Note",
            "",
            content.strip(),
            "",
        ]
    )
    path.write_text(text, encoding="utf-8")
    return CapturedNote(kind="learning", path=path, title=title)


def _append_inbox_note(
    title: str,
    content: str,
    now: datetime,
    source: str,
    n4os_root: Path,
) -> CapturedNote:
    learnings_root = _learnings_root(n4os_root)
    inbox_path = _inbox_path(n4os_root)
    learnings_root.mkdir(parents=True, exist_ok=True)
    if not inbox_path.exists():
        inbox_path.write_text(
            "\n".join(
                [
                    "---",
                    "tags:",
                    '  - "n4os/learning"',
                    '  - "n4os/inbox"',
                    "links:",
                    '  - "[[README|N4OS]]"',
                    "---",
                    "",
                    "# Learning Inbox",
                    "",
                    "Unclassified notes captured for later review.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    entry = "\n".join(
        [
            f"## {now.isoformat(timespec='minutes')}",
            "",
            f"### {title}",
            "",
            content.strip(),
            "",
            f"Source: {source}",
            "",
        ]
    )
    existing = inbox_path.read_text(encoding="utf-8")
    inbox_path.write_text(existing.rstrip() + "\n\n" + entry, encoding="utf-8")
    return CapturedNote(kind="inbox", path=inbox_path, title=title)


def _ensure_quick_notes_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    path.write_text(
        "\n".join(
            [
                "---",
                "tags:",
                '  - "n4os/learning"',
                '  - "n4os/quick-notes"',
                "links:",
                '  - "[[playbooks/AI|AI Playbook]]"',
                '  - "[[PRIORITIES]]"',
                '  - "[[MISSION]]"',
                "---",
                "",
                "# Quick Notes",
                "",
                "Short one-liners and two-liners from podcasts, talks, articles, and conversations.",
                "",
                "Append new notes at the end. Create a separate learning note only when a capture has enough depth, categories, or future action value to stand alone.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _unique_learning_path(title: str, now: datetime, learnings_root: Path) -> Path:
    slug = _slugify(title)
    base = learnings_root / f"{now.date().isoformat()}-{slug}.md"
    if not base.exists():
        return base
    index = 2
    while True:
        candidate = learnings_root / f"{now.date().isoformat()}-{slug}-{index}.md"
        if not candidate.exists():
            return candidate
        index += 1


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "untitled-note"


def _learnings_root(n4os_root: Path) -> Path:
    if n4os_root == N4OS_ROOT:
        return LEARNINGS_ROOT
    return n4os_root / "learnings"


def _quick_notes_path(n4os_root: Path) -> Path:
    if n4os_root == N4OS_ROOT:
        return QUICK_NOTES_PATH
    return _learnings_root(n4os_root) / "Quick Notes.md"


def _inbox_path(n4os_root: Path) -> Path:
    if n4os_root == N4OS_ROOT:
        return INBOX_PATH
    return _learnings_root(n4os_root) / "Inbox.md"

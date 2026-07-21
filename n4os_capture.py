from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
import re

from n4os_memory_inbox import (
    DEFAULT_REPO_ROOT,
    MemoryIngestResult,
    ingest_memory_inbox_notes,
    undo_memory_observations,
)


DEFAULT_N4OS_ROOT = DEFAULT_REPO_ROOT / "n4os"
CAPTURE_PREFIX_RE = re.compile(
    r"^\s*/(?:capture|note|mem-inbox|memory-inbox|memory|mem)(?:@\w+)?(?=\s|$)",
    re.I,
)
BARE_CAPTURE_PREFIX_RE = re.compile(
    r"^\s*(?:capture|note|memory|remember)(?!\s+to\b)(?=\s|$)",
    re.I,
)
DATE_LINE_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})(?:\s*[:|-])?\s*$")
DATED_NOTE_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s+(.+)$")
PERSON_RE = re.compile(r"\b(nysha|navya)\b", re.I)
FAMILY_RE = re.compile(r"\b(family|kids|children|both kids|both)\b", re.I)
FIRST_PERSON_RE = re.compile(
    r"\b("
    r"i felt|i feel|i was|i am|i['’]m|i noticed|i avoided|i slept|i should|"
    r"my energy|my body|my mind|work felt|today i|tomorrow i"
    r")\b",
    re.I,
)
ACTION_RE = re.compile(
    r"^\s*(remind me|add event|create decision|add task|schedule|cancel|move|reschedule)\b",
    re.I,
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
CAPTURE_REPLY_SNIPPET_LIMIT = 3
CAPTURE_REPLY_SNIPPET_CHARS = 180

PEOPLE = {
    "nysha": ("Nysha", "[[family/Nysha|Nysha]]"),
    "navya": ("Navya", "[[family/Navya|Navya]]"),
}
TOPIC_RULES = [
    ("Reading", "[[Reading]]", re.compile(r"\b(read|reading|book|books|chapter|library|story|stories)\b", re.I)),
    (
        "Confidence",
        "[[Confidence]]",
        re.compile(r"\b(confiden\w*|nervous|hesitant|public|speaking|new people|eye contact)\b", re.I),
    ),
    (
        "School Transition",
        "[[School Transition]]",
        re.compile(r"\b(new school|school transition|new classmates|classmates)\b", re.I),
    ),
    (
        "Health",
        "[[playbooks/Health|Health]]",
        re.compile(r"\b(health|sleep|slept|energy|body|pain|back pain|move|movement|recovery)\b", re.I),
    ),
    (
        "Parenting",
        "[[playbooks/Parenting|Parenting]]",
        re.compile(r"\b(parenting|bedtime|kids|children|nysha|navya|family)\b", re.I),
    ),
    (
        "Attention",
        "[[Attention]]",
        re.compile(r"\b(attention|scattered|distracted|reactive|impatient|focus)\b", re.I),
    ),
    (
        "Work",
        "[[playbooks/Career|Work]]",
        re.compile(r"\b(work|career|leadership|meeting|manager|product|ai)\b", re.I),
    ),
    (
        "Fear",
        "[[playbooks/Fear|Fear]]",
        re.compile(r"\b(fear|afraid|anxious|nervous|avoid|avoided|uncertain|unsure)\b", re.I),
    ),
    (
        "Purpose",
        "[[MISSION|Purpose]]",
        re.compile(r"\b(purpose|mission|impact|meaningful|compound|compounding)\b", re.I),
    ),
]


@dataclass(frozen=True)
class CaptureNote:
    captured_on: date
    text: str
    source: str


@dataclass(frozen=True)
class JournalEntry:
    captured_on: date
    text: str
    topics: list[str]
    source: str


@dataclass(frozen=True)
class CaptureIngestResult:
    family: MemoryIngestResult
    journal_entries: list[JournalEntry]
    skipped_journal_duplicates: list[JournalEntry]
    notes: list[CaptureNote] = field(default_factory=list)

    @property
    def seen(self) -> int:
        return (
            self.family.seen
            + len(self.journal_entries)
            + len(self.skipped_journal_duplicates)
        )


def is_capture_message(text: str) -> bool:
    stripped = text.strip()
    return bool(
        CAPTURE_PREFIX_RE.match(stripped)
        or BARE_CAPTURE_PREFIX_RE.match(stripped)
        or stripped.lower().startswith("n4os capture")
    )


def ingest_capture_notes(
    text: str,
    *,
    n4os_root: Path = DEFAULT_N4OS_ROOT,
    default_date: date | None = None,
    source: str = "Telegram",
) -> CaptureIngestResult:
    notes = _parse_capture_notes(text, default_date=default_date, source=source)
    family_text = _family_ingest_text(notes)
    family_result = ingest_memory_inbox_notes(
        family_text,
        observations_root=n4os_root / "family" / "observations",
        default_date=default_date,
        source=source,
    ) if family_text else MemoryIngestResult(added=[], skipped_duplicates=[])

    added_journal: list[JournalEntry] = []
    skipped_journal: list[JournalEntry] = []
    existing_journal_keys = _load_existing_journal_keys(n4os_root / "journal")
    for note in notes:
        if not _should_write_journal(note):
            continue
        entry = JournalEntry(
            captured_on=note.captured_on,
            text=note.text,
            topics=_topic_labels(note.text),
            source=note.source,
        )
        key = _journal_key(entry)
        if key in existing_journal_keys:
            skipped_journal.append(entry)
            continue
        _append_journal_entry(n4os_root / "journal", entry)
        existing_journal_keys.add(key)
        added_journal.append(entry)

    return CaptureIngestResult(
        family=family_result,
        journal_entries=added_journal,
        skipped_journal_duplicates=skipped_journal,
        notes=notes,
    )


@dataclass(frozen=True)
class CaptureUndoResult:
    family_observations_removed: int
    journal_entries_removed: int

    @property
    def removed(self) -> int:
        return self.family_observations_removed + self.journal_entries_removed


def undo_capture_ingest(
    result: CaptureIngestResult,
    *,
    n4os_root: Path = DEFAULT_N4OS_ROOT,
) -> CaptureUndoResult:
    family_removed = undo_memory_observations(
        result.family.added,
        observations_root=n4os_root / "family" / "observations",
    )
    journal_removed = 0
    for entry in reversed(result.journal_entries):
        if _remove_journal_entry(n4os_root / "journal", entry):
            journal_removed += 1
    return CaptureUndoResult(
        family_observations_removed=family_removed,
        journal_entries_removed=journal_removed,
    )


def format_capture_undo_reply(result: CaptureUndoResult) -> str:
    if result.removed == 0:
        return "I could not undo that capture; the captured blocks were not found."

    parts = []
    if result.family_observations_removed:
        parts.append(_count_label(result.family_observations_removed, "family observation"))
    if result.journal_entries_removed:
        parts.append(_count_label(result.journal_entries_removed, "journal entry"))
    return "Undid capture: removed " + " and ".join(parts) + "."


def _count_label(count: int, label: str) -> str:
    suffix = "" if count == 1 else "s"
    return f"{count} {label}{suffix}"


def format_capture_reply(result: CaptureIngestResult) -> str:
    if result.seen == 0:
        return (
            "No capture notes found. Send /capture followed by anything worth remembering."
        )

    lines = ["Captured."]
    summary = _capture_reply_summary(result)
    if summary:
        lines.extend(["", f"Summary: {summary}"])

    snippets = _capture_reply_snippets(result)
    if snippets:
        lines.extend(["", "Captured text:", *[f"- {snippet}" for snippet in snippets]])

    lines.append("")
    if result.family.added or result.family.skipped_duplicates:
        family_counts: dict[str, int] = {}
        for observation in result.family.added:
            family_counts[observation.person] = family_counts.get(observation.person, 0) + 1
        if family_counts:
            for person in sorted(family_counts):
                lines.append(f"- Family observation: {person} ({family_counts[person]})")
        if result.family.skipped_duplicates:
            lines.append(f"- Family duplicates skipped: {len(result.family.skipped_duplicates)}")

    if result.journal_entries:
        topic_counts: dict[str, int] = {}
        for entry in result.journal_entries:
            for topic in entry.topics:
                topic_counts[topic] = topic_counts.get(topic, 0) + 1
        label = ", ".join(sorted(topic_counts)) if topic_counts else "general"
        lines.append(f"- Journal reflection: {label} ({len(result.journal_entries)})")
    if result.skipped_journal_duplicates:
        lines.append(f"- Journal duplicates skipped: {len(result.skipped_journal_duplicates)}")

    lines.extend(
        [
            "",
            "No profiles, playbooks, or goals were promoted automatically.",
        ]
    )
    return "\n".join(lines)


def _capture_reply_summary(result: CaptureIngestResult) -> str:
    snippets = _capture_reply_snippets(result)
    if not snippets:
        return ""
    if len(snippets) > 1:
        return f"Saved {len(snippets)} notes: {_second_person_summary(snippets[0])}"
    return _second_person_summary(snippets[0])


def _capture_reply_snippets(result: CaptureIngestResult) -> list[str]:
    snippets: list[str] = [note.text for note in result.notes]
    if not snippets:
        for observation in [*result.family.added, *result.family.skipped_duplicates]:
            if observation.person == "Family":
                snippets.append(observation.observation)
            else:
                snippets.append(f"{observation.person}: {observation.observation}")
        for entry in [*result.journal_entries, *result.skipped_journal_duplicates]:
            snippets.append(entry.text)

    cleaned: list[str] = []
    seen: set[str] = set()
    for snippet in snippets:
        text = _reply_snippet(snippet)
        key = _normalize_text(text)
        if not text or key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) == CAPTURE_REPLY_SNIPPET_LIMIT:
            break
    remaining = max(0, len(snippets) - len(cleaned))
    if remaining:
        cleaned.append(f"...and {remaining} more.")
    return cleaned


def _reply_snippet(text: str) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= CAPTURE_REPLY_SNIPPET_CHARS:
        return compact
    return compact[: CAPTURE_REPLY_SNIPPET_CHARS - 1].rstrip() + "..."


def _second_person_summary(text: str) -> str:
    summary = _reply_snippet(text)
    replacements = [
        (r"\bI['’]m\b", "you are"),
        (r"\bI am\b", "you are"),
        (r"\bI['’]ve\b", "you have"),
        (r"\bI have\b", "you have"),
        (r"\bI['’]ll\b", "you will"),
        (r"\bI will\b", "you will"),
        (r"\bI\b", "you"),
        (r"\bmy\b", "your"),
        (r"\bme\b", "you"),
    ]
    for pattern, replacement in replacements:
        summary = re.sub(pattern, replacement, summary, flags=re.I)
    summary = re.sub(r"\byou are\b", "you're", summary, flags=re.I)
    summary = re.sub(r"\byourself\b", "yourself", summary, flags=re.I)
    summary = re.sub(
        r"(^|[.!?]\s+)(you)\b",
        lambda match: f"{match.group(1)}You",
        summary,
    )
    summary = _compress_summary(summary)
    return summary[:1].upper() + summary[1:]


def _compress_summary(text: str) -> str:
    excited_match = re.match(
        (
            r"you're excited about (?P<subject>.+?), especially how it is "
            r"evolving into (?P<object>.+?)(?: which can (?P<capability>.+?))?\.?$"
        ),
        text,
        flags=re.I,
    )
    if not excited_match:
        return text

    subject = excited_match.group("subject")
    obj = excited_match.group("object")
    capability = excited_match.group("capability")
    obj = re.sub(r"\ba memory for the family\b", "a family memory", obj, flags=re.I)
    parts = [f"you're excited that {subject} is becoming {obj}"]
    if capability:
        capability = re.sub(r"\s+over a period of time\b", "", capability, flags=re.I)
        parts.append(f"that can {capability}")
    return " ".join(parts).rstrip(".") + "."


def _parse_capture_notes(
    text: str,
    *,
    default_date: date | None,
    source: str,
) -> list[CaptureNote]:
    current_date = default_date or datetime.now().date()
    body = _strip_capture_header(text)
    notes: list[CaptureNote] = []
    for raw_line in body.splitlines():
        line = _clean_note_line(raw_line)
        if not line:
            continue
        date_match = DATE_LINE_RE.match(line)
        if date_match:
            current_date = date.fromisoformat(date_match.group(1))
            continue
        dated_match = DATED_NOTE_RE.match(line)
        if dated_match:
            current_date = date.fromisoformat(dated_match.group(1))
            line = dated_match.group(2).strip()
        if line:
            notes.append(CaptureNote(current_date, line, source))
    return notes


def _strip_capture_header(text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return ""
    first = lines[0].strip()
    if CAPTURE_PREFIX_RE.match(first):
        first_body = CAPTURE_PREFIX_RE.sub("", first, count=1).strip(" :-")
        return "\n".join(([first_body] if first_body else []) + lines[1:])
    if BARE_CAPTURE_PREFIX_RE.match(first):
        first_body = BARE_CAPTURE_PREFIX_RE.sub("", first, count=1).strip(" :-")
        return "\n".join(([first_body] if first_body else []) + lines[1:])
    if first.lower().startswith("n4os capture"):
        first_body = first[len("n4os capture") :].strip(" :-")
        return "\n".join(([first_body] if first_body else []) + lines[1:])
    return text


def _clean_note_line(line: str) -> str:
    return line.strip().lstrip("-*\u2022").strip()


def _family_ingest_text(notes: list[CaptureNote]) -> str:
    lines: list[str] = ["/mem-inbox"]
    for note in notes:
        for person, observation in _family_observations(note):
            lines.append(note.captured_on.isoformat())
            lines.append(f"{person}: {observation}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _family_observations(note: CaptureNote) -> list[tuple[str, str]]:
    observations: list[tuple[str, str]] = []
    for sentence in _sentences(note.text):
        lowered = sentence.lower()
        for key, (person, _) in PEOPLE.items():
            if re.search(rf"\b{re.escape(key)}\b", lowered):
                observations.append((person, _remove_person_prefix(sentence, person)))
        if (
            FAMILY_RE.search(sentence)
            and not PERSON_RE.search(sentence)
            and not FIRST_PERSON_RE.search(sentence)
        ):
            observations.append(("Family", sentence))
    return observations


def _sentences(text: str) -> list[str]:
    return [part.strip(" .") for part in SENTENCE_SPLIT_RE.split(text.strip()) if part.strip(" .")]


def _remove_person_prefix(text: str, person: str) -> str:
    stripped = re.sub(rf"^\s*{re.escape(person)}\s*[:,-]?\s*", "", text, flags=re.I)
    return stripped.strip() or text.strip()


def _should_write_journal(note: CaptureNote) -> bool:
    return bool(FIRST_PERSON_RE.search(note.text) or not _family_observations(note) or ACTION_RE.match(note.text))


def _append_journal_entry(journal_root: Path, entry: JournalEntry) -> None:
    journal_root.mkdir(parents=True, exist_ok=True)
    path = journal_root / f"{entry.captured_on.isoformat()}.md"
    links = _capture_note_links(entry.text)
    if not path.exists():
        path.write_text(_journal_header(entry, links), encoding="utf-8")
    else:
        _merge_frontmatter_links(path, links)

    with path.open("a", encoding="utf-8") as file:
        file.write(_journal_entry_block(entry) + "\n")


def _journal_entry_block(entry: JournalEntry) -> str:
    linked_text = _link_text(entry.text)
    topic_links = _topic_links(entry.text)
    topic_line = [f"  Topics: {', '.join(topic_links)}"] if topic_links else []
    return "\n".join(
        [
            "",
            "## Captures",
            "",
            f"- {linked_text}",
            *topic_line,
            f"  Source: {entry.source}",
        ]
    )


def _remove_journal_entry(journal_root: Path, entry: JournalEntry) -> bool:
    path = journal_root / f"{entry.captured_on.isoformat()}.md"
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    block = _journal_entry_block(entry) + "\n"
    index = text.rfind(block)
    if index == -1:
        return False
    path.write_text(text[:index] + text[index + len(block) :], encoding="utf-8")
    return True


def _journal_header(entry: JournalEntry, links: list[str]) -> str:
    return "\n".join(
        [
            "---",
            "tags:",
            "  - \"n4os/journal\"",
            "  - \"n4os/capture\"",
            "  - \"n4os/daily\"",
            "links:",
            *[f"  - \"{link}\"" for link in links],
            "type: journal",
            f"date: {entry.captured_on.isoformat()}",
            "---",
            "",
            f"# Journal - {entry.captured_on.isoformat()}",
            "",
        ]
    )


def _load_existing_journal_keys(journal_root: Path) -> set[tuple[str, str]]:
    if not journal_root.exists():
        return set()
    keys: set[tuple[str, str]] = set()
    for path in journal_root.glob("*.md"):
        captured_on = path.stem
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("- "):
                keys.add((captured_on, _normalize_text(line.removeprefix("- "))))
    return keys


def _journal_key(entry: JournalEntry) -> tuple[str, str]:
    return (entry.captured_on.isoformat(), _normalize_text(entry.text))


def _topic_labels(text: str) -> list[str]:
    return [label for label, _, pattern in TOPIC_RULES if pattern.search(text)]


def _topic_links(text: str) -> list[str]:
    return [link for _, link, pattern in TOPIC_RULES if pattern.search(text)]


def _capture_note_links(text: str) -> list[str]:
    links = ["[[daily/Evening|Evening]]", "[[reviews/Weekly|Weekly Review]]"]
    for key, (_, link) in PEOPLE.items():
        if re.search(rf"\b{re.escape(key)}\b", text, flags=re.I):
            links.append(link)
    links.extend(_topic_links(text))
    return _dedupe_preserving_order(links)


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _merge_frontmatter_links(path: Path, links: list[str]) -> None:
    if not links:
        return
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return
    end = text.find("\n---\n", 4)
    if end == -1:
        return
    frontmatter = text[4:end].splitlines()
    existing_links = set(_frontmatter_list_values(frontmatter, "links"))
    new_links = [link for link in links if link not in existing_links]
    if not new_links:
        return

    insert_at = _frontmatter_list_end(frontmatter, "links")
    if insert_at is None:
        insert_at = len(frontmatter)
        frontmatter.append("links:")
    for link in reversed(new_links):
        frontmatter.insert(insert_at, f"  - \"{link}\"")

    body = text[end + len("\n---\n") :]
    updated = "---\n" + "\n".join(frontmatter) + "\n---\n" + body
    path.write_text(updated, encoding="utf-8")


def _frontmatter_list_values(lines: list[str], key: str) -> list[str]:
    start = _frontmatter_key_index(lines, key)
    if start is None:
        return []
    values: list[str] = []
    for line in lines[start + 1 :]:
        if line and not line.startswith(" ") and not line.startswith("-"):
            break
        match = re.match(r'^\s*-\s*"?([^"]+)"?\s*$', line)
        if match:
            values.append(match.group(1))
    return values


def _frontmatter_list_end(lines: list[str], key: str) -> int | None:
    start = _frontmatter_key_index(lines, key)
    if start is None:
        return None
    end = start + 1
    while end < len(lines) and (not lines[end] or lines[end].startswith(" ") or lines[end].startswith("-")):
        end += 1
    return end


def _frontmatter_key_index(lines: list[str], key: str) -> int | None:
    for index, line in enumerate(lines):
        if line == f"{key}:":
            return index
    return None


def _link_text(text: str) -> str:
    linked = text
    for key, (_, link) in PEOPLE.items():
        linked = re.sub(rf"\b{re.escape(key)}\b", link, linked, flags=re.I)
    linked = re.sub(r"\bnew classmates\b", "[[School Transition|new classmates]]", linked, flags=re.I)
    linked = re.sub(r"\bnew school\b", "[[School Transition|new school]]", linked, flags=re.I)
    linked = re.sub(r"\breading\b", "[[Reading|reading]]", linked, flags=re.I)
    linked = re.sub(r"\bbooks\b", "[[Reading|books]]", linked, flags=re.I)
    linked = re.sub(r"\bsleep\b", "[[playbooks/Health|sleep]]", linked, flags=re.I)
    linked = re.sub(r"\bslept\b", "[[playbooks/Health|slept]]", linked, flags=re.I)
    linked = re.sub(r"\benergy\b", "[[playbooks/Health|energy]]", linked, flags=re.I)
    linked = re.sub(r"\battention\b", "[[Attention|attention]]", linked, flags=re.I)
    linked = re.sub(r"\bscattered\b", "[[Attention|scattered]]", linked, flags=re.I)
    linked = re.sub(r"\bimpatient\b", "[[Attention|impatient]]", linked, flags=re.I)
    linked = re.sub(r"\bwork\b", "[[playbooks/Career|work]]", linked, flags=re.I)
    linked = re.sub(r"\bunsure\b", "[[playbooks/Fear|unsure]]", linked, flags=re.I)
    return linked


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", _plain_wiki_text(text).strip().lower())


def _plain_wiki_text(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        target = match.group(1)
        if "|" in target:
            return target.rsplit("|", 1)[1]
        return target.rsplit("/", 1)[-1]

    return re.sub(r"\[\[([^\]]+)\]\]", replace, text)

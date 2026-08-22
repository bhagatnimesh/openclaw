from __future__ import annotations

from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import os
import re
import threading


DEFAULT_REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_OBSERVATIONS_ROOT = DEFAULT_REPO_ROOT / "n4os" / "family" / "observations"
_FILE_WRITE_LOCKS: dict[str, threading.Lock] = {}
KNOWN_PEOPLE = {
    "nysha": "Nysha",
    "navya": "Navya",
    "family": "Family",
    "both": "Family",
    "both kids": "Family",
}
PERSON_LINKS = {
    "Nysha": "[[family/Nysha|Nysha]]",
    "Navya": "[[family/Navya|Navya]]",
}
CONCEPT_RULES = [
    (
        "Reading",
        re.compile(
            r"\b(read|reading|book|books|chapter|library|libraries|story|stories)\b",
            re.I,
        ),
    ),
    (
        "Confidence",
        re.compile(
            r"\b("
            r"confiden\w*|new people|eye contact|low voice|public|speaking|"
            r"hesitant|nervous|brave|independent\w*"
            r")\b",
            re.I,
        ),
    ),
    (
        "School Transition",
        re.compile(r"\b(starting new school|new school|school transition|new classmates)\b", re.I),
    ),
]
COMMAND_PREFIX_RE = re.compile(
    r"^\s*/(?:mem-inbox|memory-inbox|memory|mem)(?:@\w+)?(?=\s|$)",
    re.I,
)
DATE_LINE_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})(?:\s*[:|-])?\s*$")
DATED_NOTE_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s+(.+)$")
PERSON_PREFIX_RE = re.compile(
    r"^\s*(nysha|navya|family|both kids|both)\s*[:,-]\s*(.+)$",
    re.I,
)
PERSON_LEADING_RE = re.compile(
    r"^\s*(nysha|navya|family|both kids|both)\s+(.+)$",
    re.I,
)


@dataclass(frozen=True)
class MemoryObservation:
    observed_on: date
    person: str
    observation: str
    source: str


@dataclass(frozen=True)
class MemoryIngestResult:
    added: list[MemoryObservation]
    skipped_duplicates: list[MemoryObservation]

    @property
    def seen(self) -> int:
        return len(self.added) + len(self.skipped_duplicates)


def is_memory_inbox_message(text: str) -> bool:
    stripped = text.strip()
    return bool(
        COMMAND_PREFIX_RE.match(stripped)
        or stripped.lower().startswith("n4os memory inbox")
    )


def parse_memory_inbox_notes(
    text: str,
    *,
    default_date: date | None = None,
    source: str = "Telegram",
) -> list[MemoryObservation]:
    current_date = default_date or datetime.now().date()
    observations: list[MemoryObservation] = []
    body = _strip_inbox_header(text)

    for raw_line in body.splitlines():
        line = _clean_note_line(raw_line)
        if not line:
            continue

        date_match = DATE_LINE_RE.match(line)
        if date_match:
            current_date = _parse_iso_date(date_match.group(1))
            continue

        dated_match = DATED_NOTE_RE.match(line)
        if dated_match:
            current_date = _parse_iso_date(dated_match.group(1))
            line = dated_match.group(2).strip()

        person, observation = _split_person_prefix(line)
        if observation:
            observations.append(
                MemoryObservation(
                    observed_on=current_date,
                    person=person,
                    observation=observation,
                    source=source,
                )
            )

    return observations


def ingest_memory_inbox_notes(
    text: str,
    *,
    observations_root: Path = DEFAULT_OBSERVATIONS_ROOT,
    default_date: date | None = None,
    source: str = "Telegram",
) -> MemoryIngestResult:
    observations = parse_memory_inbox_notes(
        text,
        default_date=default_date,
        source=source,
    )
    snapshots = _snapshot_files(
        [_month_path(observations_root, observation.observed_on) for observation in observations]
    )
    existing_keys = _load_existing_keys(observations_root)
    added: list[MemoryObservation] = []
    skipped: list[MemoryObservation] = []

    try:
        for observation in observations:
            key = _observation_key(observation)
            if key in existing_keys:
                skipped.append(observation)
                continue
            _append_observation(observations_root, observation)
            existing_keys.add(key)
            added.append(observation)
    except Exception:
        with suppress(Exception):
            undo_memory_observations(added, observations_root=observations_root)
        with suppress(Exception):
            _restore_file_snapshots(snapshots)
        raise

    return MemoryIngestResult(added=added, skipped_duplicates=skipped)


def undo_memory_observations(
    observations: list[MemoryObservation],
    *,
    observations_root: Path = DEFAULT_OBSERVATIONS_ROOT,
) -> int:
    removed = 0
    for observation in reversed(observations):
        path = _month_path(observations_root, observation.observed_on)
        if _remove_observation_block(path, observation):
            removed += 1
    return removed


def format_memory_ingest_reply(result: MemoryIngestResult) -> str:
    if result.seen == 0:
        return (
            "No memory notes found. Send notes after /mem-inbox, one per line, "
            "using prefixes like Nysha:, Navya:, or Family:."
        )

    counts: dict[str, int] = {}
    for observation in result.added:
        counts[observation.person] = counts.get(observation.person, 0) + 1

    lines = [
        "N4OS memory inbox processed.",
        f"Captured {len(result.added)} new observations.",
    ]
    if result.skipped_duplicates:
        lines.append(f"Skipped {len(result.skipped_duplicates)} duplicates.")
    if counts:
        lines.append("New observations by person:")
        for person in sorted(counts):
            lines.append(f"- {person}: {counts[person]}")
    lines.append("Saved to n4os/family/observations/YYYY-MM.md.")
    lines.append("No child profiles were promoted automatically.")
    return "\n".join(lines)


def _strip_inbox_header(text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return ""

    first = lines[0].strip()
    if COMMAND_PREFIX_RE.match(first):
        first_body = COMMAND_PREFIX_RE.sub("", first, count=1).strip(" :-")
        return "\n".join(([first_body] if first_body else []) + lines[1:])
    if first.lower().startswith("n4os memory inbox"):
        first_body = first[len("n4os memory inbox") :].strip(" :-")
        return "\n".join(([first_body] if first_body else []) + lines[1:])
    return text


def _clean_note_line(line: str) -> str:
    return line.strip().lstrip("-*\u2022").strip()


def _split_person_prefix(line: str) -> tuple[str, str]:
    match = PERSON_PREFIX_RE.match(line) or PERSON_LEADING_RE.match(line)
    if not match:
        return "Unknown", line.strip()

    raw_person = re.sub(r"\s+", " ", match.group(1).lower())
    return KNOWN_PEOPLE[raw_person], match.group(2).strip()


def _parse_iso_date(value: str) -> date:
    return date.fromisoformat(value)


def _month_path(observations_root: Path, observed_on: date) -> Path:
    return observations_root / f"{observed_on:%Y-%m}.md"


def _append_observation(observations_root: Path, observation: MemoryObservation) -> None:
    observations_root.mkdir(parents=True, exist_ok=True)
    path = _month_path(observations_root, observation.observed_on)
    links = _observation_note_links(observation)
    with _file_write_lock(path):
        if path.exists():
            text = _merge_frontmatter_links_text(path.read_text(encoding="utf-8"), links)
        else:
            text = _month_header(observation, links)
        _write_text_atomic(path, text + _observation_block(observation) + "\n")


def _observation_block(observation: MemoryObservation) -> str:
    linked_observation = _link_observation_text(observation.observation)
    concept_links = _infer_concept_links(observation.observation)
    topic_line = [f"  Topics: {', '.join(concept_links)}"] if concept_links else []
    return "\n".join(
        [
            "",
            f"## {observation.observed_on.isoformat()}",
            "",
            f"### {_person_heading(observation.person)}",
            f"- Observation: {linked_observation}",
            *topic_line,
            f"  Source: {observation.source}",
            "  Freshness: new",
            "  Confidence: low",
        ]
    )


def _remove_observation_block(path: Path, observation: MemoryObservation) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    block = _observation_block(observation) + "\n"
    index = text.rfind(block)
    if index == -1:
        return False
    path.write_text(text[:index] + text[index + len(block) :], encoding="utf-8")
    return True


def _month_header(observation: MemoryObservation, links: list[str]) -> str:
    return "\n".join(
        [
            "---",
            "tags:",
            "  - \"n4os/family\"",
            "  - \"n4os/memory\"",
            "  - \"n4os/observation\"",
            "links:",
            *[f"  - \"{link}\"" for link in links],
            f"month: \"{observation.observed_on:%Y-%m}\"",
            "---",
            "",
            f"# Family Observations - {observation.observed_on:%Y-%m}",
            "",
        ]
    )


def _observation_note_links(observation: MemoryObservation) -> list[str]:
    links = [
        "[[playbooks/Parenting|Parenting]]",
        "[[family/FamilyValues|Family Values]]",
    ]
    person_link = PERSON_LINKS.get(observation.person)
    if person_link:
        links.append(person_link)
    links.extend(_infer_concept_links(observation.observation))
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
    with _file_write_lock(path):
        text = path.read_text(encoding="utf-8")
        updated = _merge_frontmatter_links_text(text, links)
        if updated != text:
            _write_text_atomic(path, updated)


def _merge_frontmatter_links_text(text: str, links: list[str]) -> str:
    if not links:
        return text
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end == -1:
        return text
    frontmatter = text[4:end].splitlines()
    existing_links = set(_frontmatter_list_values(frontmatter, "links"))
    new_links = [link for link in links if link not in existing_links]
    if not new_links:
        return text

    insert_at = _frontmatter_list_end(frontmatter, "links")
    if insert_at is None:
        insert_at = len(frontmatter)
        frontmatter.append("links:")
    for link in reversed(new_links):
        frontmatter.insert(insert_at, f"  - \"{link}\"")

    body = text[end + len("\n---\n") :]
    return "---\n" + "\n".join(frontmatter) + "\n---\n" + body


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{id(text)}.tmp")
    try:
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(path)
    finally:
        with suppress(FileNotFoundError):
            tmp_path.unlink()


def _snapshot_files(paths: list[Path]) -> dict[Path, str | None]:
    snapshots: dict[Path, str | None] = {}
    for path in paths:
        if path in snapshots:
            continue
        snapshots[path] = path.read_text(encoding="utf-8") if path.exists() else None
    return snapshots


def _restore_file_snapshots(snapshots: dict[Path, str | None]) -> None:
    for path, text in snapshots.items():
        if text is None:
            with suppress(FileNotFoundError):
                path.unlink()
            continue
        _write_text_atomic(path, text)


@contextmanager
def _file_write_lock(path: Path):
    key = str(path)
    lock = _FILE_WRITE_LOCKS.setdefault(key, threading.Lock())
    with lock:
        yield


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


def _load_existing_keys(observations_root: Path) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    if not observations_root.exists():
        return keys

    for path in observations_root.glob("*.md"):
        current_date: str | None = None
        current_person = "Unknown"
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                parsed = line.removeprefix("## ").strip()
                current_date = parsed if _looks_like_date(parsed) else current_date
            elif line.startswith("### "):
                current_person = _plain_wiki_text(line.removeprefix("### ").strip()) or "Unknown"
            elif line.startswith("- Observation: ") and current_date:
                observation = line.removeprefix("- Observation: ").strip()
                keys.add((current_date, current_person, _normalize_observation(observation)))
    return keys


def _looks_like_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _observation_key(observation: MemoryObservation) -> tuple[str, str, str]:
    return (
        observation.observed_on.isoformat(),
        observation.person,
        _normalize_observation(observation.observation),
    )


def _normalize_observation(value: str) -> str:
    plain = _plain_wiki_text(value)
    return re.sub(r"\s+", " ", plain.strip().lower())


def _person_heading(person: str) -> str:
    return PERSON_LINKS.get(person, person)


def _infer_concept_links(text: str) -> list[str]:
    links: list[str] = []
    for name, pattern in CONCEPT_RULES:
        if pattern.search(text):
            links.append(f"[[{name}]]")
    return links


def _link_observation_text(text: str) -> str:
    linked = text
    linked = re.sub(
        r"\bstarting new school\b",
        "[[School Transition|starting new school]]",
        linked,
        flags=re.I,
    )
    linked = re.sub(
        r"\bnew classmates\b",
        "[[School Transition|new classmates]]",
        linked,
        flags=re.I,
    )
    linked = re.sub(r"\breading\b", "[[Reading|reading]]", linked, flags=re.I)
    linked = re.sub(r"\bbooks\b", "[[Reading|books]]", linked, flags=re.I)
    linked = re.sub(r"\bread\b", "[[Reading|read]]", linked, flags=re.I)
    linked = re.sub(r"\bconfidence\b", "[[Confidence|confidence]]", linked, flags=re.I)
    linked = re.sub(
        r"\bindependent\w*\b",
        lambda match: f"[[Confidence|{match.group(0)}]]",
        linked,
        flags=re.I,
    )
    linked = re.sub(r"\bnew people\b", "[[Confidence|new people]]", linked, flags=re.I)
    linked = re.sub(r"\beye contact\b", "[[Confidence|eye contact]]", linked, flags=re.I)
    linked = re.sub(r"\blow voice\b", "[[Confidence|low voice]]", linked, flags=re.I)
    linked = re.sub(r"\bpublic\b", "[[Confidence|public]]", linked, flags=re.I)
    linked = re.sub(r"\bspeaking\b", "[[Confidence|speaking]]", linked, flags=re.I)
    linked = re.sub(r"\bhesitant\b", "[[Confidence|hesitant]]", linked, flags=re.I)
    return linked


def _plain_wiki_text(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        target = match.group(1)
        if "|" in target:
            return target.rsplit("|", 1)[1]
        return target.rsplit("/", 1)[-1]

    return re.sub(r"\[\[([^\]]+)\]\]", replace, text)

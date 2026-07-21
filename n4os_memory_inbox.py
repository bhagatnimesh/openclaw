from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import re


DEFAULT_REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_OBSERVATIONS_ROOT = DEFAULT_REPO_ROOT / "n4os" / "family" / "observations"
KNOWN_PEOPLE = {
    "nysha": "Nysha",
    "navya": "Navya",
    "family": "Family",
    "both": "Family",
    "both kids": "Family",
}
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
    existing_keys = _load_existing_keys(observations_root)
    added: list[MemoryObservation] = []
    skipped: list[MemoryObservation] = []

    for observation in observations:
        key = _observation_key(observation)
        if key in existing_keys:
            skipped.append(observation)
            continue
        _append_observation(observations_root, observation)
        existing_keys.add(key)
        added.append(observation)

    return MemoryIngestResult(added=added, skipped_duplicates=skipped)


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
    if not path.exists():
        path.write_text(f"# Family Observations - {observation.observed_on:%Y-%m}\n", encoding="utf-8")

    block = "\n".join(
        [
            "",
            f"## {observation.observed_on.isoformat()}",
            "",
            f"### {observation.person}",
            f"- Observation: {observation.observation}",
            f"  Source: {observation.source}",
            "  Freshness: new",
            "  Confidence: low",
        ]
    )
    with path.open("a", encoding="utf-8") as file:
        file.write(block + "\n")


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
                current_person = line.removeprefix("### ").strip() or "Unknown"
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
    return re.sub(r"\s+", " ", value.strip().lower())

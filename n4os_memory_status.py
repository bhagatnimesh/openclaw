from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


DEFAULT_REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_FAMILY_ROOT = DEFAULT_REPO_ROOT / "n4os" / "family"
STATUS_COMMAND_RE = re.compile(r"^\s*/memory-status(?:@\w+)?(?:\s+(.+))?\s*$", re.I)
VALID_TARGETS = {"family", "nysha", "navya"}


@dataclass(frozen=True)
class ObservationRecord:
    date: str
    person: str
    text: str
    source: str | None = None


def is_memory_status_message(text: str) -> bool:
    return bool(STATUS_COMMAND_RE.match(text.strip()))


def parse_memory_status_target(text: str) -> str:
    match = STATUS_COMMAND_RE.match(text.strip())
    if not match:
        return "family"
    raw_target = (match.group(1) or "family").strip().lower()
    if raw_target in {"nysha", "navya", "family"}:
        return raw_target
    return "family"


def format_memory_status(
    target: str = "family",
    *,
    family_root: Path = DEFAULT_FAMILY_ROOT,
    recent_limit: int = 8,
) -> str:
    normalized_target = target.lower()
    if normalized_target not in VALID_TARGETS:
        normalized_target = "family"

    observations = _read_observations(family_root / "observations")
    relevant_people = _relevant_people(normalized_target)
    relevant_observations = [
        observation
        for observation in observations
        if observation.person.lower() in relevant_people
    ]

    lines = [_title_for(normalized_target), ""]
    lines.extend(_stable_context_lines(normalized_target, family_root))
    lines.append("")
    lines.append("Recent observations:")
    if relevant_observations:
        for observation in relevant_observations[-recent_limit:]:
            lines.append(
                f"- {observation.date} {observation.person}: {observation.text}"
            )
    else:
        lines.append("- None captured yet.")

    counts = _counts_by_person(relevant_observations)
    lines.append("")
    lines.append("Observation counts:")
    if counts:
        for person in sorted(counts):
            lines.append(f"- {person}: {counts[person]}")
    else:
        lines.append("- None")

    lines.append("")
    lines.append("Loaded memory files:")
    for path in _loaded_paths(normalized_target, family_root):
        lines.append(f"- {path}")

    lines.append("")
    lines.append("Important: recent observations are signals, not fixed identity.")
    lines.append("No raw observations are promoted into child profiles automatically.")
    return "\n".join(lines)


def _relevant_people(target: str) -> set[str]:
    if target == "nysha":
        return {"nysha"}
    if target == "navya":
        return {"navya"}
    return {"family", "nysha", "navya"}


def _title_for(target: str) -> str:
    if target == "nysha":
        return "N4OS memory status: Nysha"
    if target == "navya":
        return "N4OS memory status: Navya"
    return "N4OS memory status: family"


def _stable_context_lines(target: str, family_root: Path) -> list[str]:
    if target == "nysha":
        return _person_profile_lines(family_root / "Nysha.md", "Nysha")
    if target == "navya":
        return _person_profile_lines(family_root / "Navya.md", "Navya")
    return _family_context_lines(family_root)


def _family_context_lines(family_root: Path) -> list[str]:
    values = _section_bullets(family_root / "FamilyValues.md", "Values", limit=5)
    practices = _section_bullets(family_root / "FamilyValues.md", "Family Practices", limit=4)
    lines = ["Stable family memory:"]
    lines.append("- North star: a house full of love, health, curiosity, learning, and trust.")
    if values:
        lines.append("- Values: " + "; ".join(_trim_period(value) for value in values) + ".")
    if practices:
        lines.append("- Practices: " + "; ".join(_trim_period(value) for value in practices) + ".")
    return lines


def _person_profile_lines(path: Path, name: str) -> list[str]:
    focus = _first_paragraph_after_heading(path, ["2026 Focus", "Focus"])
    protected = _section_bullets(path, "What To Protect", limit=5)
    success = _section_bullets(path, "Love For Reading", limit=4)

    lines = [f"Stable {name} profile:"]
    if focus:
        lines.append(f"- Focus: {focus}")
    if protected:
        lines.append("- Protect: " + "; ".join(_trim_period(item) for item in protected) + ".")
    if success:
        lines.append("- Reading signals to build: " + "; ".join(_trim_period(item) for item in success) + ".")
    if len(lines) == 1:
        lines.append("- No stable profile details found.")
    return lines


def _loaded_paths(target: str, family_root: Path) -> list[str]:
    paths = ["n4os/family/FamilyValues.md"]
    if target in {"family", "nysha"}:
        paths.append("n4os/family/Nysha.md")
    if target in {"family", "navya"}:
        paths.append("n4os/family/Navya.md")
    if (family_root / "observations").exists():
        paths.extend(
            f"n4os/family/observations/{path.name}"
            for path in sorted((family_root / "observations").glob("*.md"))
        )
    return paths


def _read_observations(observations_root: Path) -> list[ObservationRecord]:
    if not observations_root.exists():
        return []

    records: list[ObservationRecord] = []
    for path in sorted(observations_root.glob("*.md")):
        current_date = ""
        current_person = "Unknown"
        current_text: str | None = None
        current_source: str | None = None
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                if current_text is not None:
                    records.append(
                        ObservationRecord(current_date, current_person, current_text, current_source)
                    )
                    current_text = None
                    current_source = None
                current_date = line.removeprefix("## ").strip()
            elif line.startswith("### "):
                if current_text is not None:
                    records.append(
                        ObservationRecord(current_date, current_person, current_text, current_source)
                    )
                    current_text = None
                    current_source = None
                current_person = _plain_wiki_text(line.removeprefix("### ").strip()) or "Unknown"
            elif line.startswith("- Observation: "):
                if current_text is not None:
                    records.append(
                        ObservationRecord(current_date, current_person, current_text, current_source)
                    )
                current_text = _plain_wiki_text(line.removeprefix("- Observation: ").strip())
                current_source = None
            elif line.strip().startswith("Source: ") and current_text is not None:
                current_source = line.strip().removeprefix("Source: ").strip()
        if current_text is not None:
            records.append(
                ObservationRecord(current_date, current_person, current_text, current_source)
            )
    return records


def _counts_by_person(observations: list[ObservationRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for observation in observations:
        counts[observation.person] = counts.get(observation.person, 0) + 1
    return counts


def _first_paragraph_after_heading(path: Path, headings: list[str]) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    heading_set = {f"## {heading}" for heading in headings}
    in_section = False
    for line in lines:
        if line in heading_set:
            in_section = True
            continue
        if in_section and line.startswith("## "):
            return ""
        if in_section and line.strip() and not line.startswith("#"):
            return line.strip()
    return ""


def _section_bullets(path: Path, heading: str, *, limit: int) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    bullets: list[str] = []
    in_section = False
    for line in lines:
        if line == f"## {heading}":
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section and line.startswith("- "):
            bullets.append(line.removeprefix("- ").strip())
            if len(bullets) >= limit:
                break
    return bullets


def _trim_period(value: str) -> str:
    return value.rstrip(".")


def _plain_wiki_text(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        target = match.group(1)
        if "|" in target:
            return target.rsplit("|", 1)[1]
        return target.rsplit("/", 1)[-1]

    return re.sub(r"\[\[([^\]]+)\]\]", replace, text)

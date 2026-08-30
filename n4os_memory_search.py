from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from n4os_structured_memory import (
    DEFAULT_N4OS_ROOT,
    format_structured_memory_query,
    has_structured_memory_query_match,
)
from n4os_trajectories import expand_n4os_query_terms


BROAD_MEMORY_SEARCH_RE = re.compile(
    r"^\s*(?P<prefix>"
    r"(?:find|look\s+up|lookup|search|show(?:\s+me)?)\s+(?:my\s+)?memory\s+everywhere|"
    r"(?:find|look\s+up|lookup|search|show(?:\s+me)?)\s+all\s+memory|"
    r"(?:find|look\s+up|lookup|search|show(?:\s+me)?)\s+(?:my\s+)?memory\s+files|"
    r"(?:find|look\s+up|lookup|search|show(?:\s+me)?)\s+captured\s+memory|"
    r"(?:find|look\s+up|lookup|search|show(?:\s+me)?)\s+in\s+observations"
    r")\b(?P<body>.*)$",
    re.I,
)

MEMORY_SEARCH_STOP_WORDS = frozenset(
    {
        "all",
        "any",
        "captured",
        "everywhere",
        "file",
        "files",
        "find",
        "in",
        "look",
        "lookup",
        "me",
        "memories",
        "memory",
        "my",
        "observation",
        "observations",
        "please",
        "search",
        "show",
        "the",
        "up",
    }
)

SELECTED_TOP_LEVEL_FILES = (
    "SOUL.md",
    "MISSION.md",
    "VISION.md",
    "IDENTITY.md",
    "PRIORITIES.md",
    "PRINCIPLES.md",
    "PERSONAL_MODEL.md",
    "OPERATING_RULES.md",
    "DECISION_FILTER.md",
    "FAMILY_DECISIONS_GUIDE.md",
    "Reading.md",
    "School Transition.md",
    "Confidence.md",
)


@dataclass(frozen=True)
class BroadMemoryMatch:
    path: str
    label: str
    snippet: str
    score: int
    line_number: int


def is_broad_memory_search_query(text: str) -> bool:
    return BROAD_MEMORY_SEARCH_RE.match(text.strip()) is not None


def broad_memory_structured_query(text: str) -> str:
    match = BROAD_MEMORY_SEARCH_RE.match(text.strip())
    if match is None:
        return text
    body = match.group("body").strip(" .,:;?!")
    return f"find memory {body}".strip()


def broad_memory_query_body(text: str) -> str:
    match = BROAD_MEMORY_SEARCH_RE.match(text.strip())
    if match is None:
        return text.strip()
    return match.group("body").strip(" .,:;?!")


def search_broad_memory(
    text: str,
    *,
    n4os_root: Path = DEFAULT_N4OS_ROOT,
    limit: int = 5,
) -> list[BroadMemoryMatch]:
    query = broad_memory_query_body(text)
    original_terms = _query_terms(query)
    if not original_terms:
        return []
    expanded_terms = expand_n4os_query_terms(original_terms)
    original = set(original_terms)
    expanded = set(expanded_terms)
    matches: list[BroadMemoryMatch] = []
    for path in _broad_memory_paths(n4os_root):
        rel_path = _relative_n4os_path(path, n4os_root)
        for line_number, label, snippet in _candidate_lines(path, n4os_root):
            score = _score(snippet, original=original, expanded=expanded)
            if score <= 0:
                continue
            matches.append(
                BroadMemoryMatch(
                    path=rel_path,
                    label=label,
                    snippet=_compact(snippet, limit=220),
                    score=score,
                    line_number=line_number,
                )
            )
    matches.sort(key=lambda match: (match.score, match.path, -match.line_number), reverse=True)
    return matches[: max(1, limit)]


def format_broad_memory_search_query(
    text: str,
    *,
    n4os_root: Path = DEFAULT_N4OS_ROOT,
    limit: int = 5,
) -> str:
    structured_query = broad_memory_structured_query(text)
    sections: list[str] = []
    if has_structured_memory_query_match(structured_query, n4os_root=n4os_root):
        sections.append(format_structured_memory_query(structured_query, n4os_root=n4os_root))

    broad_matches = search_broad_memory(text, n4os_root=n4os_root, limit=limit)
    if broad_matches:
        lines = ["Broader N4OS memory matches:"]
        for index, match in enumerate(broad_matches, start=1):
            if index > 1:
                lines.append("")
            location = f"{match.path}:{match.line_number}"
            lines.append(f"{index}. {match.label} ({location})")
            lines.append(f"   {match.snippet}")
        sections.append("\n".join(lines))

    if sections:
        return "\n\n".join(sections)
    return (
        "I searched structured /remember memory and broader N4OS memory files, "
        "but did not find a matching memory."
    )


def _query_terms(text: str) -> list[str]:
    terms: list[str] = []
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if len(word) < 2 or word in MEMORY_SEARCH_STOP_WORDS:
            continue
        if word not in terms:
            terms.append(word)
    return terms


def _score(text: str, *, original: set[str], expanded: set[str]) -> int:
    haystack = text.lower()
    score = 0
    for term in expanded:
        if term not in haystack:
            continue
        score += 3 if term in original else 1
    return score


def _broad_memory_paths(n4os_root: Path) -> list[Path]:
    paths: list[Path] = []
    for rel in SELECTED_TOP_LEVEL_FILES:
        path = n4os_root / rel
        if path.is_file():
            paths.append(path)
    for root in (
        n4os_root / "family" / "observations",
        n4os_root / "journal",
        n4os_root / "trajectories",
        n4os_root / "family",
        n4os_root / "playbooks",
    ):
        if not root.exists():
            continue
        paths.extend(path for path in sorted(root.glob("*.md")) if path.is_file())
    return sorted(dict.fromkeys(paths))


def _candidate_lines(path: Path, n4os_root: Path) -> list[tuple[int, str, str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rel_path = _relative_n4os_path(path, n4os_root)
    if "/family/observations/" in rel_path:
        return _observation_candidates(lines)
    candidates: list[tuple[int, str, str]] = []
    current_heading = ""
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            current_heading = _plain_markdown(stripped.lstrip("#").strip())
            continue
        if stripped.startswith("- ") or stripped.startswith("Summary:") or stripped.startswith("- Summary:"):
            snippet = _plain_markdown(stripped.removeprefix("- ").strip())
            label = current_heading or Path(rel_path).stem
            candidates.append((index, label, snippet))
    return candidates


def _observation_candidates(lines: list[str]) -> list[tuple[int, str, str]]:
    candidates: list[tuple[int, str, str]] = []
    current_date = ""
    current_person = ""
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("## "):
            current_date = _plain_markdown(stripped.removeprefix("## ").strip())
            continue
        if stripped.startswith("### "):
            current_person = _plain_markdown(stripped.removeprefix("### ").strip())
            continue
        if not stripped.startswith("- Observation: "):
            continue
        snippet = _plain_markdown(stripped.removeprefix("- Observation: ").strip())
        label = " ".join(part for part in (current_date, current_person) if part)
        candidates.append((index, label or "Observation", snippet))
    return candidates


def _relative_n4os_path(path: Path, n4os_root: Path) -> str:
    try:
        return f"n4os/{path.relative_to(n4os_root)}"
    except ValueError:
        return str(path)


def _plain_markdown(text: str) -> str:
    cleaned = re.sub(r"\[\[([^|\]]+)\|([^\]]+)\]\]", r"\2", text)
    cleaned = re.sub(r"\[\[([^\]]+)\]\]", r"\1", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    return _compact(cleaned)


def _compact(text: str, *, limit: int = 240) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."

from __future__ import annotations

from pathlib import Path
import re
from typing import Any


def retrieve_school_coach_sources(
    n4os_root: Path,
    *,
    request: str,
    relationship: dict[str, Any] | None,
) -> dict[str, str]:
    terms = _search_terms(request, relationship)
    sources: dict[str, str] = {}
    school_root = n4os_root / "school"
    if school_root.exists():
        for child_root in sorted(path for path in school_root.iterdir() if path.is_dir()):
            if relationship and relationship.get("child"):
                if child_root.name.casefold() != str(relationship["child"]).casefold():
                    continue
            years = sorted(path for path in child_root.iterdir() if path.is_dir())
            if not years:
                continue
            for name in ("School Knowledge.md", "Room 13.md"):
                path = years[-1] / name
                if not path.exists():
                    continue
                text = path.read_text(encoding="utf-8")
                if terms and not any(term in text.casefold() for term in terms):
                    continue
                ref = path.relative_to(n4os_root.parent).as_posix()
                sources[ref] = text[:5000]

    journal_root = n4os_root / "journal"
    if journal_root.exists():
        for path in sorted(journal_root.glob("*.md"))[-3:]:
            lines = []
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                lowered = line.casefold()
                if any(term in lowered for term in terms):
                    ref = f"{path.relative_to(n4os_root.parent).as_posix()}#L{line_number}"
                    sources[ref] = line.strip()
                    lines.append(line)
                if len(lines) >= 8:
                    break
    return sources


def discover_teacher_candidates(n4os_root: Path) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    school_root = n4os_root / "school"
    if not school_root.exists():
        return candidates
    for child_root in sorted(path for path in school_root.iterdir() if path.is_dir()):
        years = sorted(path for path in child_root.iterdir() if path.is_dir())
        if not years:
            continue
        path = years[-1] / "School Knowledge.md"
        if not path.exists():
            continue
        section = _markdown_section(path.read_text(encoding="utf-8"), "People And Relationships")
        source_ref = path.relative_to(n4os_root.parent).as_posix()
        for match in re.finditer(r"(?im)^-\s+((?:Mr|Mrs|Ms|Miss|Dr)\.\s+[^\n,;]+)$", section):
            name = match.group(1).strip()
            if not any(item["person_name"].casefold() == name.casefold() for item in candidates):
                candidates.append(
                    {
                        "person_name": name,
                        "child": child_root.name,
                        "source_ref": source_ref,
                    }
                )
    return candidates


def _search_terms(request: str, relationship: dict[str, Any] | None) -> set[str]:
    terms = {term for term in re.findall(r"[a-z0-9]+", request.casefold()) if len(term) >= 4}
    if relationship:
        for key in ("person_name", "child", "school"):
            value = relationship.get(key)
            if value:
                terms.update(
                    term for term in re.findall(r"[a-z0-9]+", str(value).casefold()) if len(term) >= 3
                )
    terms.update({"teacher", "school"})
    return terms


def _markdown_section(text: str, heading: str) -> str:
    match = re.search(
        rf"(?ims)^###\s+{re.escape(heading)}\s*$\n(.*?)(?=^###\s+|\Z)",
        text,
    )
    return match.group(1) if match else ""

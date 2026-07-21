from __future__ import annotations

from pathlib import Path
import re


DEFAULT_REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_N4OS_ROOT = DEFAULT_REPO_ROOT / "n4os"
GOALS_QUERY_RE = re.compile(
    r"\b(?:current\s+goals?|my\s+goals?|priorities|2026\s+goals?|ten[- ]year\s+goals?|10[- ]year\s+goals?)\b",
    re.I,
)


def is_goals_status_message(text: str) -> bool:
    lowered = text.strip().lower()
    if lowered in {"/goals", "/goals-status", "goals", "current goals"}:
        return True
    return bool(GOALS_QUERY_RE.search(text))


def format_goals_status(*, n4os_root: Path = DEFAULT_N4OS_ROOT) -> str:
    goals_2026 = _read_goal_sections(n4os_root / "goals" / "2026.md")
    goals_2036 = _read_goal_sections(n4os_root / "goals" / "2036.md")

    lines = [
        "N4OS current goals",
        "",
        "2026 theme:",
        f"- {_read_theme(n4os_root / 'goals' / '2026.md')}",
        "",
        "2026 focus areas:",
    ]
    for heading in ["Health", "Family", "AI", "Work And Leadership", "Personal Operating System"]:
        bullets = goals_2026.get(heading, [])
        if bullets:
            lines.append(f"- {heading}: {_join_short(bullets)}")

    lines.extend(["", "2036 north star:"])
    for heading in ["Family", "Health", "AI And Purpose", "Relationships", "Inner Life"]:
        values = goals_2036.get(heading, [])
        if values:
            lines.append(f"- {heading}: {_join_short(values)}")

    lines.extend(
        [
            "",
            "Loaded memory files:",
            "- n4os/goals/2026.md",
            "- n4os/goals/2036.md",
            "- n4os/PRIORITIES.md",
            "",
            "Use these as the filter for planning, tradeoffs, and weekly review.",
        ]
    )
    return "\n".join(lines)


def _read_theme(path: Path) -> str:
    sections = _read_goal_sections(path)
    theme = sections.get("Theme", [])
    return theme[0] if theme else "No theme found."


def _read_goal_sections(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}

    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            current = line.removeprefix("## ").strip()
            sections.setdefault(current, [])
            continue
        if current is None or not line or line.startswith("#"):
            continue
        if line.startswith("- "):
            sections[current].append(line.removeprefix("- ").strip())
        else:
            sections[current].append(line)
    return sections


def _join_short(values: list[str]) -> str:
    return "; ".join(value.rstrip(".") for value in values[:3]) + "."

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import re
from textwrap import indent


DEFAULT_REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_N4OS_ROOT = DEFAULT_REPO_ROOT / "n4os"
TRAJECTORY_TEXT_LIMIT = 8000
SUMMARY_LIMIT = 420

QUERY_EXPANSION_GROUPS: tuple[tuple[str, ...], ...] = (
    (
        "puzzle",
        "puzzles",
        "game",
        "games",
        "logic",
        "logical",
        "pattern",
        "patterns",
        "brain",
        "teaser",
        "teasers",
        "playful",
        "silly",
    ),
    (
        "read",
        "reading",
        "book",
        "books",
        "story",
        "stories",
        "storytelling",
        "teach",
        "teaching",
        "explain",
        "explaining",
    ),
    (
        "confidence",
        "confident",
        "hesitant",
        "speaking",
        "voice",
        "public",
        "greet",
        "greeting",
        "people",
        "adult",
        "adults",
    ),
    ("school", "class", "classroom", "classmates", "teacher", "transition"),
    ("health", "sleep", "slept", "energy", "body", "pain", "movement", "recovery"),
    ("attention", "scattered", "focus", "focused", "distracted", "reactive"),
)


@dataclass(frozen=True)
class N4OSTrajectoryRecord:
    captured_at: datetime
    mode: str
    source: str
    user_text: str
    assistant_text: str
    context_labels: list[str]
    summary: str
    model: str | None = None
    knowledge_preview: str | None = None
    reasoning_summary: str | None = None


def record_n4os_trajectory(
    *,
    mode: str,
    user_text: str,
    assistant_text: str,
    context_labels: list[str],
    summary: str | None = None,
    source: str = "Telegram",
    n4os_root: Path = DEFAULT_N4OS_ROOT,
    captured_at: datetime | None = None,
    model: str | None = None,
    knowledge_preview: str | None = None,
    reasoning_summary: str | None = None,
) -> N4OSTrajectoryRecord:
    record = N4OSTrajectoryRecord(
        captured_at=captured_at or datetime.now(),
        mode=_clean_inline(mode) or "unknown",
        source=_clean_inline(source) or "Telegram",
        user_text=_limit_block(user_text),
        assistant_text=_limit_block(assistant_text),
        context_labels=_dedupe_labels(context_labels),
        summary=_summarize(summary or assistant_text),
        model=_clean_inline(model) or None,
        knowledge_preview=_limit_block(knowledge_preview or "") or None,
        reasoning_summary=_limit_block(reasoning_summary or "") or None,
    )
    _append_trajectory(n4os_root / "trajectories", record)
    return record


def read_recent_trajectory_summaries(
    trajectories_root: Path,
    *,
    lowered_request: str = "",
    limit: int = 8,
) -> list[str]:
    if not trajectories_root.exists():
        return []

    topic_terms = _topic_terms(lowered_request)
    records: list[str] = []
    current_date = ""
    current_mode = ""
    current_summary = ""
    current_topics: list[str] = []

    def flush() -> None:
        nonlocal current_date, current_mode, current_summary, current_topics
        if not current_summary:
            return
        haystack = " ".join([current_summary, " ".join(current_topics)]).lower()
        if topic_terms and not any(term in haystack for term in topic_terms):
            return
        prefix = current_date
        if current_mode:
            prefix = f"{prefix} {current_mode}".strip()
        records.append(f"{prefix}: {current_summary}".strip(": "))

    for path in sorted(trajectories_root.glob("*.md")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                flush()
                current_date = line.removeprefix("## ").strip()[:10]
                current_mode = ""
                current_summary = ""
                current_topics = []
            elif line.startswith("- Mode: "):
                current_mode = line.removeprefix("- Mode: ").strip()
            elif line.startswith("- Summary: "):
                current_summary = line.removeprefix("- Summary: ").strip()
            elif line.startswith("- Topics: "):
                current_topics = [
                    item.strip()
                    for item in line.removeprefix("- Topics: ").split(",")
                    if item.strip()
                ]
        flush()
        current_date = ""
        current_mode = ""
        current_summary = ""
        current_topics = []
    return records[-limit:]


def trajectory_review_signals(
    trajectories_root: Path,
    *,
    start: date,
    end: date,
) -> list[tuple[date, str, list[str]]]:
    if not trajectories_root.exists():
        return []

    signals: list[tuple[date, str, list[str]]] = []
    current_date: date | None = None
    current_summary = ""
    current_topics: list[str] = []

    def flush() -> None:
        if current_date is None or not current_summary:
            return
        if start <= current_date <= end:
            signals.append((current_date, current_summary, current_topics))

    for path in sorted(trajectories_root.glob("*.md")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                flush()
                current_date = _parse_heading_date(line.removeprefix("## ").strip())
                current_summary = ""
                current_topics = []
            elif line.startswith("- Summary: "):
                current_summary = line.removeprefix("- Summary: ").strip()
            elif line.startswith("- Topics: "):
                current_topics = [
                    item.strip()
                    for item in line.removeprefix("- Topics: ").split(",")
                    if item.strip()
                ]
        flush()
        current_date = None
        current_summary = ""
        current_topics = []
    return signals


def _append_trajectory(trajectories_root: Path, record: N4OSTrajectoryRecord) -> None:
    trajectories_root.mkdir(parents=True, exist_ok=True)
    path = trajectories_root / f"{record.captured_at:%Y-%m}.md"
    if not path.exists():
        path.write_text(_trajectory_header(record.captured_at.date()), encoding="utf-8")

    block = _trajectory_block(record)
    with path.open("a", encoding="utf-8") as file:
        file.write(block + "\n")


def _trajectory_header(captured_on: date) -> str:
    return "\n".join(
        [
            "---",
            "tags:",
            "  - \"n4os/trajectory\"",
            "  - \"n4os/review\"",
            "links:",
            "  - \"[[reviews/Weekly|Weekly Review]]\"",
            "type: trajectory",
            f"month: {captured_on:%Y-%m}",
            "---",
            "",
            f"# N4OS Trajectories - {captured_on:%Y-%m}",
            "",
        ]
    )


def _trajectory_block(record: N4OSTrajectoryRecord) -> str:
    topics = _topic_labels(" ".join([record.user_text, record.assistant_text, record.summary]))
    labels = ", ".join(record.context_labels) if record.context_labels else "None"
    model = record.model or "unknown"
    trace_sections: list[str] = []
    if record.knowledge_preview:
        trace_sections.extend(
            [
                "Knowledge selected:",
                "",
                indent(record.knowledge_preview, "  "),
                "",
            ]
        )
    if record.reasoning_summary:
        trace_sections.extend(
            [
                "Reasoning summary:",
                "",
                indent(record.reasoning_summary, "  "),
                "",
            ]
        )
    return "\n".join(
        [
            f"## {record.captured_at.isoformat(timespec='seconds')}",
            "",
            f"- Mode: {record.mode}",
            f"- Source: {record.source}",
            f"- Model: {model}",
            f"- Context: {labels}",
            f"- Topics: {', '.join(topics) if topics else 'General'}",
            f"- Summary: {record.summary}",
            "",
            "User:",
            "",
            indent(_limit_block(record.user_text), "  "),
            "",
            *trace_sections,
            "Assistant:",
            "",
            indent(_limit_block(record.assistant_text), "  "),
            "",
        ]
    )


def _topic_labels(text: str) -> list[str]:
    lowered = text.lower()
    rules = [
        ("Reading", ("read", "reading", "book", "library", "story")),
        ("Confidence", ("confidence", "nervous", "hesitant", "public", "speaking", "new people")),
        ("School Transition", ("school", "classmates", "transition")),
        ("Health", ("health", "sleep", "energy", "body", "pain", "movement")),
        ("Parenting", ("parenting", "kids", "children", "nysha", "navya", "family")),
        ("Attention", ("attention", "scattered", "focus", "reactive")),
        ("Work", ("work", "career", "leadership", "meeting", "ai")),
        ("Fear", ("fear", "afraid", "anxious", "unsure", "avoid")),
        ("Purpose", ("purpose", "mission", "impact", "compound")),
    ]
    return [label for label, terms in rules if any(term in lowered for term in terms)]


def _topic_terms(lowered_request: str) -> list[str]:
    if not lowered_request:
        return []
    terms = re.findall(r"[a-z0-9']+", lowered_request.lower())
    stopwords = {
        "a",
        "an",
        "and",
        "do",
        "for",
        "how",
        "i",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "should",
        "the",
        "this",
        "to",
        "we",
        "what",
        "with",
    }
    base_terms = [term for term in terms if len(term) >= 4 and term not in stopwords]
    return expand_n4os_query_terms(base_terms)


def expand_n4os_query_terms(terms: list[str]) -> list[str]:
    expanded: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        if term not in seen:
            seen.add(term)
            expanded.append(term)

    for term in terms:
        add(term)
        for group in QUERY_EXPANSION_GROUPS:
            if term in group:
                for related in group:
                    add(related)
    return expanded


def _summarize(text: str) -> str:
    cleaned = _clean_inline(text)
    if len(cleaned) <= SUMMARY_LIMIT:
        return cleaned
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    summary = ""
    for sentence in sentences:
        candidate = " ".join([summary, sentence]).strip()
        if len(candidate) > SUMMARY_LIMIT:
            break
        summary = candidate
    if summary:
        return summary
    return cleaned[: SUMMARY_LIMIT - 3].rstrip() + "..."


def _limit_block(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) <= TRAJECTORY_TEXT_LIMIT:
        return cleaned
    return cleaned[: TRAJECTORY_TEXT_LIMIT - 26].rstrip() + "\n[trajectory truncated]"


def _clean_inline(text: str | None) -> str:
    return " ".join((text or "").split())


def _dedupe_labels(labels: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for label in labels:
        cleaned = _clean_inline(label)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
    return deduped


def _parse_heading_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None

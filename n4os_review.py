from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
import re


DEFAULT_REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_N4OS_ROOT = DEFAULT_REPO_ROOT / "n4os"
REVIEW_COMMAND_RE = re.compile(r"^\s*/review(?:@\w+)?(?:\s+(.+))?\s*$", re.I)
VALID_PERIODS = {"day", "week", "month"}


@dataclass(frozen=True)
class ReviewSignal:
    captured_on: date
    source: str
    text: str
    topics: list[str]


def is_n4os_review_message(text: str) -> bool:
    return bool(REVIEW_COMMAND_RE.match(text.strip()))


def parse_review_period(text: str) -> str:
    match = REVIEW_COMMAND_RE.match(text.strip())
    if not match:
        return "week"
    raw = (match.group(1) or "week").strip().lower()
    if raw in VALID_PERIODS:
        return raw
    if raw in {"daily", "today"}:
        return "day"
    if raw in {"weekly", "this week"}:
        return "week"
    if raw in {"monthly", "this month"}:
        return "month"
    return "week"


def format_n4os_review(
    period: str = "week",
    *,
    n4os_root: Path = DEFAULT_N4OS_ROOT,
    reference_date: date | None = None,
) -> str:
    normalized_period = period if period in VALID_PERIODS else "week"
    today = reference_date or date.today()
    start = _start_date(normalized_period, today)
    signals = [
        signal
        for signal in [*_read_journal_signals(n4os_root / "journal"), *_read_family_signals(n4os_root / "family" / "observations")]
        if start <= signal.captured_on <= today
    ]

    lines = [f"N4OS {normalized_period} review", ""]
    if not signals:
        lines.extend(
            [
                "No recent captures found for this review window.",
                "",
                "Promotion candidates:",
                "- None. Capture more raw signals before changing stable N4OS files.",
            ]
        )
        return "\n".join(lines)

    topic_counts = _topic_counts(signals)
    lines.append("Repeated signals:")
    repeated = [(topic, count) for topic, count in sorted(topic_counts.items()) if count >= 2]
    if repeated:
        lines.extend(f"- {topic}: {count} signals" for topic, count in repeated[:6])
    else:
        lines.extend(f"- {topic}: {count} signal" for topic, count in sorted(topic_counts.items())[:6])

    lines.extend(["", "What compounded:"])
    lines.extend(_compounding_lines(topic_counts))

    lines.extend(["", "What needs attention:"])
    lines.extend(_attention_lines(topic_counts))

    lines.extend(["", "Promotion candidates:"])
    lines.extend(_promotion_candidates(topic_counts, signals))

    lines.extend(["", "Next action:", f"- {_next_action(topic_counts)}"])
    lines.extend(
        [
            "",
            "No stable N4OS files were changed. Promote only after you confirm a candidate.",
        ]
    )
    return "\n".join(lines)


def _start_date(period: str, today: date) -> date:
    if period == "day":
        return today
    if period == "month":
        return today - timedelta(days=31)
    return today - timedelta(days=7)


def _read_journal_signals(journal_root: Path) -> list[ReviewSignal]:
    if not journal_root.exists():
        return []
    signals: list[ReviewSignal] = []
    for path in sorted(journal_root.glob("*.md")):
        captured_on = _parse_date(path.stem)
        if captured_on is None:
            continue
        current_text: str | None = None
        current_topics: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("- "):
                if current_text is not None:
                    signals.append(ReviewSignal(captured_on, "journal", current_text, current_topics))
                current_text = line.removeprefix("- ").strip()
                current_topics = []
            elif line.strip().startswith("Topics: ") and current_text is not None:
                current_topics = _plain_topics(line.strip().removeprefix("Topics: "))
        if current_text is not None:
            signals.append(ReviewSignal(captured_on, "journal", current_text, current_topics))
    return signals


def _read_family_signals(observations_root: Path) -> list[ReviewSignal]:
    if not observations_root.exists():
        return []
    signals: list[ReviewSignal] = []
    for path in sorted(observations_root.glob("*.md")):
        current_date: date | None = None
        current_text: str | None = None
        current_topics: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                if current_date is not None and current_text is not None:
                    signals.append(ReviewSignal(current_date, "family", current_text, current_topics))
                current_date = _parse_date(line.removeprefix("## ").strip())
                current_text = None
                current_topics = []
            elif line.startswith("- Observation: "):
                if current_date is not None and current_text is not None:
                    signals.append(ReviewSignal(current_date, "family", current_text, current_topics))
                current_text = line.removeprefix("- Observation: ").strip()
                current_topics = []
            elif line.strip().startswith("Topics: ") and current_text is not None:
                current_topics = _plain_topics(line.strip().removeprefix("Topics: "))
        if current_date is not None and current_text is not None:
            signals.append(ReviewSignal(current_date, "family", current_text, current_topics))
    return signals


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _plain_topics(value: str) -> list[str]:
    topics: list[str] = []
    for raw in value.split(","):
        clean = _plain_wiki_text(raw).strip()
        if clean:
            topics.append(clean)
    return topics


def _topic_counts(signals: list[ReviewSignal]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for signal in signals:
        topics = signal.topics or _infer_topics(signal.text)
        for topic in topics:
            counts[topic] = counts.get(topic, 0) + 1
    return counts


def _infer_topics(text: str) -> list[str]:
    lowered = text.lower()
    topics: list[str] = []
    if any(term in lowered for term in ("sleep", "energy", "body", "pain", "health")):
        topics.append("Health")
    if any(term in lowered for term in ("nysha", "navya", "family", "kids", "bedtime")):
        topics.append("Parenting")
    if any(term in lowered for term in ("scattered", "attention", "focus", "impatient", "reactive")):
        topics.append("Attention")
    if any(term in lowered for term in ("read", "book", "reading")):
        topics.append("Reading")
    if any(term in lowered for term in ("school", "classmates")):
        topics.append("School Transition")
    return topics or ["General"]


def _compounding_lines(topic_counts: dict[str, int]) -> list[str]:
    lines: list[str] = []
    if topic_counts.get("Parenting") or topic_counts.get("Reading"):
        lines.append("- Family learning is being captured instead of disappearing.")
    if topic_counts.get("Health"):
        lines.append("- Health signals are visible enough to adjust routines.")
    if topic_counts.get("Work") or topic_counts.get("Purpose"):
        lines.append("- Work and purpose signals are available for sharper prioritization.")
    return lines or ["- Captures created a reviewable record."]


def _attention_lines(topic_counts: dict[str, int]) -> list[str]:
    lines: list[str] = []
    if topic_counts.get("Health"):
        lines.append("- Health may be a constraint; check sleep, movement, pain, and recovery.")
    if topic_counts.get("Attention"):
        lines.append("- Attention/reactivity may need a simpler next-day plan.")
    if topic_counts.get("School Transition") or topic_counts.get("Confidence"):
        lines.append("- Family confidence or transition signals deserve gentle observation.")
    return lines or ["- No repeated risk signal yet; keep capturing."]


def _promotion_candidates(topic_counts: dict[str, int], signals: list[ReviewSignal]) -> list[str]:
    candidates: list[str] = []
    if topic_counts.get("Health", 0) >= 2 and topic_counts.get("Attention", 0) >= 1:
        candidates.append(
            "- Consider adding to PERSONAL_MODEL.md: when sleep or energy drops, reactivity and scattered attention become more likely."
        )
    if topic_counts.get("School Transition", 0) >= 2 or topic_counts.get("Confidence", 0) >= 2:
        candidates.append(
            "- Consider adding to family/Nysha.md after review: currently tends to need gentle support around confidence in new settings."
        )
    if any(signal.source == "journal" for signal in signals) and topic_counts.get("Parenting", 0) >= 2:
        candidates.append(
            "- Consider adding a weekly Parenting review prompt around presence, patience, and repair."
        )
    return candidates or ["- None yet. Keep raw captures as signals."]


def _next_action(topic_counts: dict[str, int]) -> str:
    if topic_counts.get("Health"):
        return "Choose one health-first reset for tomorrow before optimizing work."
    if topic_counts.get("School Transition") or topic_counts.get("Confidence"):
        return "Create one low-pressure family experiment and review it in 7 days."
    if topic_counts.get("Attention"):
        return "Pick one hard priority for tomorrow and protect the first work block."
    return "Capture three more real signals, then run the next review."


def _plain_wiki_text(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        target = match.group(1)
        if "|" in target:
            return target.rsplit("|", 1)[1]
        return target.rsplit("/", 1)[-1]

    return re.sub(r"\[\[([^\]]+)\]\]", replace, text)

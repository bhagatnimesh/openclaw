from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import json
import os
import re
import urllib.request
from typing import Any, Callable

from n4os_review import format_n4os_review


DEFAULT_REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_N4OS_ROOT = DEFAULT_REPO_ROOT / "n4os"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.4-mini"
ADVICE_TRIGGER_RE = re.compile(
    r"^\s*/(?:ask|n4os|coach|advice)(?:@\w+)?(?:\s+|$)|"
    r"\b(?:n4os|coach me|give me advice|what should|how should|approach)\b",
    re.I,
)

UrlOpen = Callable[..., Any]


def is_n4os_advice_message(text: str) -> bool:
    return bool(ADVICE_TRIGGER_RE.search(text.strip()))


def format_n4os_advice(
    request: str,
    *,
    n4os_root: Path = DEFAULT_N4OS_ROOT,
    api_key: str | None = None,
    model: str | None = None,
    urlopen: UrlOpen = urllib.request.urlopen,
) -> str:
    cleaned_request = _strip_advice_prefix(request)
    context = _build_context(cleaned_request, n4os_root)
    if _is_week_ahead_request(cleaned_request.lower()):
        return _fallback_week_ahead(context)
    if _is_school_transition_request(cleaned_request.lower()):
        return _fallback_advice(cleaned_request, context)
    ai_text = _try_openai_advice(
        cleaned_request,
        context,
        api_key=api_key,
        model=model,
        urlopen=urlopen,
    )
    if ai_text:
        return ai_text
    return _fallback_advice(cleaned_request, context)


def _strip_advice_prefix(request: str) -> str:
    return re.sub(
        r"^\s*/(?:ask|n4os|coach|advice)(?:@\w+)?\s*",
        "",
        request,
        flags=re.I,
    ).strip()


def _build_context(request: str, n4os_root: Path) -> dict[str, Any]:
    lowered = request.lower()
    week_target = _week_ahead_target(request)
    files = [
        "SOUL.md",
        "AGENTS.md",
        "MISSION.md",
        "VISION.md",
        "IDENTITY.md",
        "PRIORITIES.md",
        "PRINCIPLES.md",
        "PERSONAL_MODEL.md",
    ]
    if "nysha" in lowered or "reading" in lowered or "book" in lowered:
        files.extend(["family/FamilyValues.md", "family/Nysha.md", "playbooks/Parenting.md"])
    if "confidence" in lowered:
        files.append("Confidence.md")
    if "school" in lowered or "classmates" in lowered:
        files.append("School Transition.md")
    if "reading" in lowered or "book" in lowered:
        files.append("Reading.md")
    if "navya" in lowered:
        files.extend(["family/FamilyValues.md", "family/Navya.md", "playbooks/Parenting.md"])
    if "health" in lowered or "sleep" in lowered or "fitness" in lowered:
        files.append("playbooks/Health.md")
    if "career" in lowered or "work" in lowered or "leadership" in lowered:
        files.extend(["playbooks/Career.md", "playbooks/Leadership.md"])
    if "fear" in lowered or "afraid" in lowered or "anxious" in lowered:
        files.append("playbooks/Fear.md")
    if "overwhelmed" in lowered or "too much" in lowered:
        files.append("playbooks/Overwhelmed.md")
    is_week_ahead = _is_week_ahead_request(lowered)
    if is_week_ahead:
        files.extend(
            [
                "goals/2026.md",
                "goals/2036.md",
                "OPERATING_RULES.md",
                "reviews/Weekly.md",
                "playbooks/Health.md",
                "playbooks/Parenting.md",
                "playbooks/AI.md",
                "playbooks/Relationships.md",
            ]
        )

    observations = _recent_observations(n4os_root / "family" / "observations", lowered)
    journal = _recent_journal_entries(n4os_root / "journal", lowered)
    return {
        "request": request,
        "files": _read_files(n4os_root, files),
        "observations": observations,
        "journal": journal,
        "operations": _load_week_ahead_operations(target=week_target) if is_week_ahead else {},
        "target": week_target,
    }


def _read_files(n4os_root: Path, paths: list[str]) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for rel_path in paths:
        if rel_path in seen:
            continue
        seen.add(rel_path)
        path = n4os_root / rel_path
        if path.exists():
            result.append({"path": f"n4os/{rel_path}", "text": path.read_text(encoding="utf-8")[:5000]})
    return result


def _recent_observations(observations_root: Path, lowered_request: str) -> list[str]:
    if not observations_root.exists():
        return []
    wanted_people = []
    if "nysha" in lowered_request:
        wanted_people.append("Nysha")
    if "navya" in lowered_request:
        wanted_people.append("Navya")
    if not wanted_people and any(term in lowered_request for term in ("family", "kids", "children", "reading")):
        wanted_people.extend(["Family", "Nysha", "Navya"])

    records: list[str] = []
    current_date = ""
    current_person = ""
    for path in sorted(observations_root.glob("*.md")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                current_date = line.removeprefix("## ").strip()
            elif line.startswith("### "):
                current_person = _plain_wiki_text(line.removeprefix("### ").strip())
            elif line.startswith("- Observation: "):
                if wanted_people and current_person not in wanted_people:
                    continue
                observation = line.removeprefix("- Observation: ").strip()
                records.append(f"{current_date} {current_person}: {_plain_wiki_text(observation)}")

    if "reading" in lowered_request or "book" in lowered_request:
        reading_terms = (
            "read",
            "book",
            "reflection",
            "teaching",
            "group",
            "score",
            "game",
            "explain",
        )
        relevant = [record for record in records if any(term in record.lower() for term in reading_terms)]
        if relevant:
            return relevant[-12:]
    return records[-12:]


def _recent_journal_entries(journal_root: Path, lowered_request: str) -> list[str]:
    if not journal_root.exists():
        return []

    topic_terms = {
        "health": ("health", "sleep", "energy", "pain", "body", "movement"),
        "family": ("family", "nysha", "navya", "kids", "parenting", "bedtime"),
        "work": ("work", "career", "leadership", "meeting", "ai"),
        "attention": ("attention", "scattered", "focus", "distracted", "reactive"),
        "fear": ("fear", "afraid", "anxious", "unsure", "avoid"),
    }
    wanted_terms: list[str] = []
    for cue, terms in topic_terms.items():
        if cue in lowered_request:
            wanted_terms.extend(terms)

    records: list[str] = []
    for path in sorted(journal_root.glob("*.md")):
        captured_on = path.stem
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.startswith("- "):
                continue
            text = _plain_wiki_text(line.removeprefix("- ").strip())
            if wanted_terms and not any(term in text.lower() for term in wanted_terms):
                continue
            records.append(f"{captured_on}: {text}")
    return records[-12:]


def _try_openai_advice(
    request: str,
    context: dict[str, Any],
    *,
    api_key: str | None,
    model: str | None,
    urlopen: UrlOpen,
) -> str | None:
    resolved_key = (api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")).strip()
    if not resolved_key:
        return None

    body = {
        "model": (model or os.environ.get("N4OS_ADVICE_MODEL") or DEFAULT_MODEL).strip(),
        "store": False,
        "max_output_tokens": 420,
        "input": [
            {
                "role": "system",
                "content": (
                    "You are N4OS, a practical personal coach. Use the provided memory. "
                    "Be warm, direct, specific, and action-oriented. Write plain text for Telegram: "
                    "no Markdown, no bold markers, no decorative headings, and no raw links. "
                    "Start with one warm diagnosis sentence. Keep the answer under 14 lines. "
                    "Use at most 3 action bullets and 2 watch bullets. "
                    "Do not overstate raw observations as fixed identity; say 'currently tends to' for child patterns. "
                    "When family memory is used, include a capture loop for family/observations/YYYY-MM.md. "
                    "End with short Decision, Next action, and Review lines."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": request,
                        "memory": context,
                        "format": "concise Telegram-friendly answer",
                    },
                    sort_keys=True,
                ),
            },
        ],
    }
    request_obj = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {resolved_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request_obj, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    text = _extract_response_text(payload)
    if not text:
        return None
    return _normalize_advice_output(text, context)


def _extract_response_text(payload: dict[str, Any]) -> str | None:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    chunks: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                chunks.append(content["text"].strip())
    text = "\n".join(chunk for chunk in chunks if chunk).strip()
    return text or None


def _normalize_advice_output(text: str, context: dict[str, Any]) -> str:
    cleaned = _strip_basic_markdown(text)
    cleaned = _collapse_excess_blank_lines(cleaned)
    if _needs_family_capture_loop(cleaned, context):
        cleaned = "\n".join(
            [
                cleaned.rstrip(),
                "",
                f"Capture loop: after the review, save what worked in {_current_observations_path()}.",
            ]
        )
    return cleaned.strip()


def _strip_basic_markdown(text: str) -> str:
    cleaned = text.replace("**", "").replace("__", "")
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", cleaned)
    return cleaned


def _collapse_excess_blank_lines(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    collapsed: list[str] = []
    blank_seen = False
    for line in lines:
        if not line.strip():
            if not blank_seen:
                collapsed.append("")
            blank_seen = True
            continue
        collapsed.append(line)
        blank_seen = False
    return "\n".join(collapsed)


def _needs_family_capture_loop(text: str, context: dict[str, Any]) -> bool:
    lowered = text.lower()
    if "family/observations" in lowered or "capture loop" in lowered:
        return False
    if context.get("observations"):
        return True
    return any(str(item.get("path", "")).startswith("n4os/family/") for item in context.get("files", []))


def _fallback_advice(request: str, context: dict[str, Any]) -> str:
    observations = context.get("observations") or []
    journal = context.get("journal") or []
    loaded = [item["path"] for item in context.get("files", [])]
    if _is_week_ahead_request(request.lower()):
        return _fallback_week_ahead(context)

    lowered_request = request.lower()
    lines = []
    is_nysha_reading = "nysha" in lowered_request and "reading" in lowered_request
    is_nysha_school_transition = _is_school_transition_request(lowered_request)
    if not is_nysha_school_transition:
        lines.extend(["N4OS advice", ""])
    if is_nysha_school_transition:
        lines.extend(
            [
                "Nysha likely needs practice + safety here, not more explanation.",
                "",
                "Do this for 7 days:",
                "1. Rehearse 3 lines at home: Hi, I'm Nysha. Can I sit here? Can you help me?",
                "2. Pick one bridge person at school: a safe peer, teacher, or helper role.",
                "3. Make it a tiny game: one greeting, one help question, one win.",
                "",
                "Use her strengths: groups, games, making, movement, and teaching.",
                "Watch: easier entry, louder voice, or one self-started interaction.",
                "If tears, attendance, or daily functioning suffer for more than a few weeks, involve the teacher early.",
            ]
        )
    elif is_nysha_reading:
        lines.extend(
            [
                "For Nysha's reading, treat reading as joy plus identity, not performance pressure.",
                "Use her current signals: games, teaching, group learning, scores, and hands-on curiosity.",
                "",
                "Try:",
                "- Let her teach you or Navya one thing from the book.",
                "- Use visible progress, like books finished or pages read, but keep it playful.",
                "- Mix solo reading with group reading, audiobooks, library visits, and discussion.",
                "- For complex emotional topics, use stories, play, drawing, or conversation rather than only reflection books.",
            ]
        )
    else:
        lines.extend(
            [
                "Start from health, family, purpose, relationships, and learning.",
                "Choose the smallest useful action that compounds this week.",
            ]
        )
    if observations and not is_nysha_school_transition:
        lines.extend(["", "Memory signals used:"])
        lines.extend(f"- {item}" for item in observations[-5:])
    if journal:
        lines.extend(["", "Journal signals used:"])
        lines.extend(f"- {item}" for item in journal[-5:])
    lines.extend(
        [
            "",
            "Decision: use small real-world reps.",
            (
                "Next action: do a 10-minute rehearsal tonight."
            )
            if is_nysha_school_transition
            else (
                "Next action: pick one book this week and let Nysha either teach back one idea, "
                "track progress, or discuss it with someone."
            )
            if is_nysha_reading
            else "Next action: choose one experiment for this week.",
            f"Review/Capture: check in 1 week and save what worked in {_current_observations_path()}."
            if is_nysha_school_transition
            else "Review point: check in after 7 days.",
        ]
    )
    if not is_nysha_school_transition and _needs_family_capture_loop("\n".join(lines), context):
        lines.extend(
            [
                "",
                f"Capture: save what worked in {_current_observations_path()}.",
            ]
        )
    loaded_label = "Used" if is_nysha_school_transition else "Loaded"
    loaded_items = _compact_loaded_files(loaded) if is_nysha_school_transition else loaded
    lines.extend(["", f"{loaded_label}: " + ", ".join(loaded_items)])
    return "\n".join(lines)


def _compact_loaded_files(paths: list[str]) -> list[str]:
    priority = {
        "n4os/SOUL.md": "SOUL",
        "n4os/MISSION.md": "MISSION",
        "n4os/VISION.md": "VISION",
        "n4os/family/FamilyValues.md": "FamilyValues",
        "n4os/family/Nysha.md": "Nysha",
        "n4os/family/Navya.md": "Navya",
        "n4os/playbooks/Parenting.md": "Parenting",
        "n4os/School Transition.md": "School Transition",
        "n4os/Reading.md": "Reading",
        "n4os/goals/2026.md": "2026 Goals",
        "n4os/goals/2036.md": "2036 Goals",
        "n4os/reviews/Weekly.md": "Weekly Review",
    }
    compact: list[str] = []
    for path in paths:
        name = priority.get(path)
        if name is not None:
            compact.append(name)
    return compact


def _is_week_ahead_request(lowered: str) -> bool:
    return bool(
        re.search(
            r"\b(week ahead|next week|this week|weekly plan|plan my week|week plan|focus this week)\b",
            lowered,
        )
    )


def _is_school_transition_request(lowered: str) -> bool:
    return "nysha" in lowered and ("school" in lowered or "transition" in lowered)


def _week_ahead_target(request: str) -> str | None:
    lowered = request.lower()
    if re.search(r"\b(nysha|nisha|nyshoo|nyshuu|big n|elder one)\b", lowered):
        return "Nysha"
    if re.search(r"\b(navya|little n|younger one)\b", lowered):
        return "Navya"
    return None


def _fallback_week_ahead(context: dict[str, Any]) -> str:
    loaded = [item["path"] for item in context.get("files", [])]
    observations = context.get("observations") or []
    journal = context.get("journal") or []
    operations = context.get("operations") or {}
    target = context.get("target")
    review = format_n4os_review("week")

    lines = [f"N4OS week ahead for {target}" if target else "N4OS week ahead", ""]
    if target == "Nysha":
        lines.append("This looks like a keep-it-calm, make-it-concrete week for Nysha.")
    elif target == "Navya":
        lines.append("This looks like a simple-rhythm week for Navya.")
    else:
        lines.append("This looks like a protect-attention week: fewer moving parts, clearer follow-through.")

    lines.append("")
    lines.extend(_week_ahead_summary_lines(operations, target))

    unavailable = operations.get("unavailable") or []
    if unavailable:
        lines.extend(["", "Operational context not loaded:"])
        lines.extend(f"- {item}" for item in unavailable)

    lines.extend(["", "Focus tomorrow:"])
    if target == "Nysha":
        lines.extend(
            [
                "1. One concrete, low-pressure school-facing step.",
                "2. One kid-only or familiar confidence bridge.",
                "3. One playful learning win connected to curiosity.",
            ]
        )
    elif target == "Navya":
        lines.extend(
            [
                "1. One simple predictable routine.",
                "2. One playful learning win.",
                "3. One small step that is easy to repeat.",
            ]
        )
    else:
        lines.extend(
            [
                "1. Protect sleep and movement before optimizing anything else.",
                "2. Choose one compounding build or decision.",
                "3. Decide tomorrow's hard priority tonight.",
            ]
        )
    if journal:
        lines.extend(["", "Personal signal:"])
        lines.extend(f"- {item}" for item in journal[-2:])
    if observations:
        lines.extend(["", * _family_signal_summary_lines(observations, target)])

    review_lines = [
        line
        for line in review.splitlines()
        if line.startswith("- ") and "stable N4OS files" not in line
    ][:4]
    if review_lines and target is None:
        lines.extend(["", "Review signals:"])
        lines.extend(review_lines)

    lines.extend(
        [
            "",
            "Decision: make this a family-present, one-hard-thing week.",
            _week_ahead_next_action(target),
            f"Review/Capture: run /review week and save what worked in {_current_observations_path()}.",
        ]
    )
    lines.extend(["", "Used: " + ", ".join(_compact_loaded_files(loaded))])
    return "\n".join(lines)


def _week_ahead_summary_lines(operations: dict[str, Any], target: str | None) -> list[str]:
    summary = [
        _count_summary("calendar", operations.get("events")),
        _count_summary("prep", operations.get("prep_events")),
        _count_summary("tasks", operations.get("tasks")),
        _count_summary("Home Board", operations.get("home_board")),
    ]
    if _has_any_operations(operations):
        lines = ["- " + "; ".join(summary) + "."]
        lines.extend(_operation_lines(operations.get("events"), "")[:2])
        lines.extend(_operation_lines(operations.get("prep_events"), "")[:2])
        lines.extend(_operation_lines(operations.get("tasks"), "")[:2])
        lines.extend(_operation_lines(operations.get("home_board"), "")[:2])
        return [line for line in lines if line]
    owner = f" for {target}" if target else ""
    return [f"No scheduled, prep, urgent task, or Home Board items found{owner}."]


def _count_summary(label: str, items: Any) -> str:
    count = len(items) if isinstance(items, list) else 0
    if count == 1:
        return f"1 {label} item"
    return f"{count} {label} items"


def _has_any_operations(operations: dict[str, Any]) -> bool:
    return any(
        isinstance(operations.get(key), list) and bool(operations.get(key))
        for key in ("events", "prep_events", "tasks", "home_board")
    )


def _family_signal_summary_lines(observations: list[str], target: str | None) -> list[str]:
    if target == "Nysha":
        return [
            "Family signal: Nysha currently benefits from concrete practice and familiar or kid-only bridges.",
        ]
    if target == "Navya":
        return [
            "Family signal: Navya currently benefits from simple rhythms, play, and small repeatable wins.",
        ]
    return [f"Family signal: {observations[-1]}"]


def _week_ahead_next_action(target: str | None) -> str:
    if target == "Nysha":
        return "Next action: choose one school-facing rehearsal or confidence bridge for tomorrow."
    if target == "Navya":
        return "Next action: choose one simple routine or learning win for tomorrow."
    return "Next action: choose tomorrow's one hard priority tonight, then schedule movement before work."


def _current_observations_path() -> str:
    return f"family/observations/{datetime.now():%Y-%m}.md"


def _operation_lines(items: Any, empty: str) -> list[str]:
    if not isinstance(items, list) or not items:
        return [empty]
    return [str(item) for item in items[:6]]


def _load_week_ahead_operations(
    reference_time: datetime | None = None,
    *,
    target: str | None = None,
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {
        "events": [],
        "prep_events": [],
        "tasks": [],
        "home_board": [],
        "unavailable": [],
    }

    try:
        from claws.n4os.claw import (
            CALENDAR_ROOT,
            HOME_BOARD_ROOT,
            TASKS_ROOT,
            _calendar_module,
            _default_now,
            _format_event_line,
            _format_task_line,
            _home_board_module,
            _parse_due_date,
            _prep_line,
            _start_of_day,
            _tasks_module,
            module_scope,
        )
    except Exception as error:
        result["unavailable"].append(f"Calendar/tasks modules: {error}")
        return result

    now = _default_now(reference_time)
    week_start = _start_of_day(now)
    week_end = week_start + timedelta(days=7)

    try:
        calendar_module = _calendar_module()
        with module_scope(CALENDAR_ROOT):
            calendar = calendar_module.FamilyCalendarClaw.default()
            response = calendar.tools.list_calendar_events(
                time_min=week_start.isoformat(),
                time_max=week_end.isoformat(),
                max_results=100,
            )
        if response.get("status") == "ok":
            events = sorted(
                [
                    event
                    for event in response.get("data", {}).get("events", [])
                    if _calendar_event_matches_target(event, calendar_module, target)
                ],
                key=calendar_module._event_start,
            )
            result["events"] = [_format_event_line(event, calendar_module) for event in events[:6]]
            prep_events = [
                event
                for event in events
                if calendar_module._event_needs_preparation(event)
            ]
            result["prep_events"] = [_prep_line(event, calendar_module) for event in prep_events[:6]]
        else:
            result["unavailable"].append(f"Calendar: {response.get('message', 'not available')}")
    except Exception as error:
        result["unavailable"].append(f"Calendar: {error}")

    try:
        tasks_module = _tasks_module()
        with module_scope(TASKS_ROOT):
            tasks = tasks_module.FamilyTasksClaw.default()
            response = tasks.tools.list_tasks(show_completed=False)
        if response.get("status") == "ok":
            open_tasks = [
                task
                for task in response.get("data", {}).get("tasks", [])
                if task.get("status") != "completed"
            ]
            selected = []
            for task in open_tasks:
                due = _parse_due_date(task)
                _, metadata = tasks_module.read_metadata_from_notes(task.get("notes"))
                if not _task_matches_target(task, metadata, target):
                    continue
                is_urgent = metadata.get("urgency") == "high"
                is_due_this_week = due is not None and due.date() <= week_end.date()
                if is_urgent or is_due_this_week:
                    selected.append(task)
            selected.sort(
                key=lambda task: (
                    _parse_due_date(task) or datetime.max,
                    str(task.get("title") or ""),
                )
            )
            result["tasks"] = [_format_task_line(task, tasks_module) for task in selected[:6]]
        else:
            result["unavailable"].append(f"Tasks: {response.get('message', 'not available')}")
    except Exception as error:
        result["unavailable"].append(f"Tasks: {error}")

    try:
        home_board_module = _home_board_module()
        with module_scope(HOME_BOARD_ROOT):
            home_board = home_board_module.HomeBoardClaw.default()
        seen: set[str] = set()
        for offset in range(7):
            day = (week_start + timedelta(days=offset)).date().isoformat()
            with module_scope(HOME_BOARD_ROOT):
                response = home_board.tools.list_items(date=day, status="pending", now=now)
            if response.get("status") != "ok":
                result["unavailable"].append(f"Home Board: {response.get('message', 'not available')}")
                break
            for item in response.get("data", {}).get("items", []):
                if not _home_board_item_matches_target(item, target):
                    continue
                item_id = str(item.get("id") or item)
                if item_id in seen:
                    continue
                seen.add(item_id)
                result["home_board"].append(f"- {day}: {home_board_module._format_item(item)}")
                if len(result["home_board"]) >= 6:
                    break
            if len(result["home_board"]) >= 6:
                break
    except Exception as error:
        result["unavailable"].append(f"Home Board: {error}")

    return result


def _calendar_event_matches_target(event: dict[str, Any], calendar_module: Any, target: str | None) -> bool:
    if target is None:
        return True

    notes, metadata = calendar_module.read_metadata_from_event(event)
    if str(metadata.get("person") or "").lower() == target.lower():
        return True

    text = " ".join(
        str(part)
        for part in (
            event.get("summary"),
            notes,
            event.get("description"),
            event.get("location"),
            json.dumps(metadata, sort_keys=True),
        )
        if part
    )
    return _target_in_text(text, target)


def _task_matches_target(task: dict[str, Any], metadata: dict[str, Any], target: str | None) -> bool:
    if target is None:
        return True

    metadata_person = str(metadata.get("person") or metadata.get("child") or "").lower()
    if metadata_person == target.lower():
        return True

    text = " ".join(
        str(part)
        for part in (
            task.get("title"),
            task.get("notes"),
            json.dumps(metadata, sort_keys=True),
        )
        if part
    )
    return _target_in_text(text, target)


def _home_board_item_matches_target(item: dict[str, Any], target: str | None) -> bool:
    if target is None:
        return True
    person = str(item.get("person_or_group") or "").lower()
    if person == target.lower():
        return True
    return _target_in_text(str(item.get("message") or ""), target)


def _target_in_text(text: str, target: str) -> bool:
    normalized = text.lower()
    aliases = {
        "Nysha": ("nysha", "nisha", "nyshoo", "nyshuu", "big n", "elder one"),
        "Navya": ("navya", "little n", "younger one"),
    }.get(target, (target.lower(),))
    return any(re.search(rf"\b{re.escape(alias)}\b", normalized) for alias in aliases)


def _plain_wiki_text(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        target = match.group(1)
        if "|" in target:
            return target.rsplit("|", 1)[1]
        return target.rsplit("/", 1)[-1]

    return re.sub(r"\[\[([^\]]+)\]\]", replace, text)

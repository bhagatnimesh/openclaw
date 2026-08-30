from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import json
import os
import re
import sqlite3
import urllib.request
from typing import Any, Callable

from n4os_review import format_n4os_review
from n4os_trajectories import expand_n4os_query_terms, read_recent_trajectory_summaries


DEFAULT_REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_N4OS_ROOT = DEFAULT_REPO_ROOT / "n4os"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.4-mini"
N4OS_TRANSPARENT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "name": "n4os_transparent_response",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "reasoning_summary": {"type": "string"},
            "answer": {"type": "string"},
        },
        "required": ["reasoning_summary", "answer"],
        "additionalProperties": False,
    },
}
ADVICE_TRIGGER_RE = re.compile(
    r"^\s*/(?:ask|n4os|coach|advice)(?:@\w+)?(?:\s+|$)|"
    r"\b(?:n4os|coach me|give me advice|what should|how should|approach)\b|"
    r"\b(?:run|start|do)\s+(?:the\s+)?(?:morning\s+)?check-?in\b|"
    r"\b(?:morning\s+check-?in|evening\s+(?:reflection|review|check-?in)|daily\s+check-?in)\b|"
    r"\bhelp\s+me\s+plan\s+(?:tomorrow\s+)?morning\b|"
    r"\bplan\s+(?:my\s+)?(?:tomorrow\s+)?morning\b",
    re.I,
)

UrlOpen = Callable[..., Any]


@dataclass(frozen=True)
class N4OSAdviceResult:
    reply: str
    reasoning_summary: str
    context_labels: list[str]
    knowledge_preview: str
    model: str | None


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
    return generate_n4os_advice(
        request,
        n4os_root=n4os_root,
        api_key=api_key,
        model=model,
        urlopen=urlopen,
    ).reply


def generate_n4os_advice(
    request: str,
    *,
    context: dict[str, Any] | None = None,
    n4os_root: Path = DEFAULT_N4OS_ROOT,
    api_key: str | None = None,
    model: str | None = None,
    urlopen: UrlOpen = urllib.request.urlopen,
) -> N4OSAdviceResult:
    cleaned_request = _strip_advice_prefix(request)
    prepared_context = context if context is not None else _build_context(cleaned_request, n4os_root)
    labels = context_labels_from_context(prepared_context)
    knowledge_preview = format_n4os_knowledge_preview(prepared_context)
    if _is_morning_checkin_request(cleaned_request.lower()):
        return _advice_result(
            _fallback_morning_checkin(cleaned_request),
            "Matched the morning check-in request and used the deterministic N4OS check-in template.",
            labels,
            knowledge_preview,
        )
    if _is_evening_reflection_request(cleaned_request.lower()):
        return _advice_result(
            _fallback_evening_reflection(),
            "Matched the evening reflection request and used the deterministic N4OS reflection template.",
            labels,
            knowledge_preview,
        )
    if _is_week_ahead_request(cleaned_request.lower()):
        return _advice_result(
            _fallback_week_ahead(prepared_context),
            "Matched a week-ahead request and combined the prepared N4OS memory with current operations.",
            labels,
            knowledge_preview,
        )
    if _is_school_transition_request(cleaned_request.lower()):
        return _advice_result(
            _fallback_advice(cleaned_request, prepared_context),
            "Matched the school-transition playbook and used its deterministic practice-and-safety guidance.",
            labels,
            knowledge_preview,
        )
    ai_text, reasoning_summary, resolved_model = _try_openai_advice(
        cleaned_request,
        prepared_context,
        api_key=api_key,
        model=model,
        urlopen=urlopen,
    )
    if ai_text:
        return _advice_result(
            ai_text,
            reasoning_summary or "The model did not return a reasoning summary.",
            labels,
            knowledge_preview,
            model=resolved_model,
        )
    return _advice_result(
        _fallback_advice(cleaned_request, prepared_context),
        "No model response was available, so N4OS used its deterministic fallback over the prepared context.",
        labels,
        knowledge_preview,
    )


def _advice_result(
    reply: str,
    reasoning_summary: str,
    context_labels: list[str],
    knowledge_preview: str,
    *,
    model: str | None = None,
) -> N4OSAdviceResult:
    return N4OSAdviceResult(
        reply=reply,
        reasoning_summary=reasoning_summary,
        context_labels=context_labels,
        knowledge_preview=knowledge_preview,
        model=model,
    )


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
    files.extend(_school_context_files(n4os_root, lowered))
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
        "reading_garden": _reading_garden_context(lowered),
        "school_newsletters": _school_newsletter_context(n4os_root, lowered),
        "trajectories": read_recent_trajectory_summaries(
            n4os_root / "trajectories",
            lowered_request=lowered,
        ),
        "operations": _load_week_ahead_operations(target=week_target) if is_week_ahead else {},
        "target": week_target,
    }


def context_labels_from_context(context: dict[str, Any]) -> list[str]:
    loaded = [str(item.get("path", "")) for item in context.get("files", [])]
    labels: list[str] = []
    for path in loaded:
        label = _context_file_label(path)
        if label and label not in labels:
            labels.append(label)
    return labels


def _context_file_label(path: str) -> str | None:
    labels = {
        "n4os/SOUL.md": "SOUL",
        "n4os/AGENTS.md": "N4OS Instructions",
        "n4os/MISSION.md": "MISSION",
        "n4os/VISION.md": "VISION",
        "n4os/IDENTITY.md": "Identity",
        "n4os/PRIORITIES.md": "Priorities",
        "n4os/PRINCIPLES.md": "Principles",
        "n4os/PERSONAL_MODEL.md": "Personal Model",
        "n4os/OPERATING_RULES.md": "Operating Rules",
        "n4os/family/FamilyValues.md": "Family Values",
        "n4os/family/Nysha.md": "Nysha",
        "n4os/family/Navya.md": "Navya",
    }
    if path in labels:
        return labels[path]
    school_label = _compact_school_file_label(path)
    if school_label:
        return school_label
    if not path.startswith("n4os/") or not path.endswith(".md"):
        return None
    return Path(path).stem.replace("_", " ")


def format_n4os_knowledge_preview(
    context: dict[str, Any],
    *,
    history_turns: int = 0,
) -> str:
    labels = context_labels_from_context(context)
    observations = context.get("observations") or []
    journal = context.get("journal") or []
    trajectories = context.get("trajectories") or []
    reading_garden = context.get("reading_garden") or {}
    school_newsletters = context.get("school_newsletters") or []

    lines = ["Knowledge selected", f"Sources: {', '.join(labels) if labels else 'None'}"]
    lines.append(
        "Recent context: "
        f"{_counted(len(observations), 'observation')}, "
        f"{_counted(len(journal), 'journal note')}, "
        f"{_counted(len(trajectories), 'prior answer')}"
    )
    if school_newsletters:
        lines.append(f"Structured context: {_counted(len(school_newsletters), 'school newsletter')}")
    if isinstance(reading_garden, dict) and reading_garden.get("available"):
        lines.append("Live context: Reading Garden")
    if context.get("operations"):
        lines.append("Live context: current operations")
    if history_turns:
        lines.append(f"Chat history: {_counted(history_turns, 'turn')}")
    return "\n".join(lines)


def _counted(count: int, label: str) -> str:
    suffix = "" if count == 1 else "s"
    return f"{count} {label}{suffix}"


def format_n4os_reasoning_preview(summary: str, *, model: str | None) -> str:
    cleaned = _collapse_excess_blank_lines(_strip_basic_markdown(summary)).strip()
    summary_lines = [line.strip().lstrip("-*• ").strip() for line in cleaned.splitlines()]
    summary_lines = [line for line in summary_lines if line][:4]
    if not summary_lines:
        summary_lines = ["No reasoning summary was available."]
    heading = "Model reasoning summary (high level)" if model else "N4OS decision path"
    lines = [heading, *(f"- {line}" for line in summary_lines)]
    lines.extend(["", 'Tune this: reply with "capture: ..."'])
    return "\n".join(lines)


def _school_context_files(n4os_root: Path, lowered_request: str) -> list[str]:
    school_terms = (
        "school",
        "class",
        "teacher",
        "homework",
        "learning",
        "curriculum",
        "spring break",
        "break",
        "holiday",
        "calendar",
        "ask",
        "talk",
        "conversation",
        "prompt",
        "practice",
        "resource",
        "book",
        "books",
        "lexia",
        "newsletter",
        "newsletters",
        "letter",
        "letters",
        "imported",
    )
    if "nysha" not in lowered_request or not any(term in lowered_request for term in school_terms):
        return []

    school_root = n4os_root / "school" / "Nysha"
    if not school_root.exists():
        return []
    years = sorted(path for path in school_root.iterdir() if path.is_dir())
    if not years:
        return []

    current_year = years[-1]
    selected = [
        "School Knowledge.md",
        "Room 13.md",
    ]
    if any(term in lowered_request for term in ("homework", "packet", "folder", "friday")):
        selected.append("Homework System.md")
    if any(term in lowered_request for term in ("reading", "book", "learning", "learn", "curriculum")):
        selected.extend(["Curriculum Map.md", "Parent Support Playbook.md", "Resources.md"])
    if any(term in lowered_request for term in ("ask", "talk", "conversation", "prompt")):
        selected.append("Conversation Starters.md")
    if any(term in lowered_request for term in ("practice", "resource", "lexia")):
        selected.append("Resources.md")

    rel_base = Path("school") / "Nysha" / current_year.name
    return [(rel_base / name).as_posix() for name in selected]


def _reading_garden_context(lowered_request: str) -> dict[str, Any]:
    if "nysha" not in lowered_request or not any(term in lowered_request for term in ("reading", "book", "books")):
        return {}
    try:
        from claws.n4os.intent_router import LIBRARY_ROOT, load_scoped_module, module_scope
    except Exception as error:
        return {"available": False, "error": str(error)}

    try:
        with module_scope(LIBRARY_ROOT):
            library_module = load_scoped_module("_n4os_library_claw_for_advice", LIBRARY_ROOT, "claw.py")
            response = library_module.LibraryClaw.default().tools.status(child="Nysha")
    except Exception as error:
        return {"available": False, "error": str(error)}

    if response.get("status") != "ok":
        return {"available": False, "error": response.get("message", "Reading Garden unavailable.")}

    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    by_child = summary.get("by_child") if isinstance(summary.get("by_child"), dict) else {}
    nysha = by_child.get("Nysha") if isinstance(by_child.get("Nysha"), dict) else {}
    if not nysha:
        return {}

    collection = nysha.get("book_collection") if isinstance(nysha.get("book_collection"), list) else []
    recent_events = nysha.get("recent_events") if isinstance(nysha.get("recent_events"), list) else []
    return {
        "available": True,
        "current_book": nysha.get("current_book") or "",
        "book_collection": collection[:10],
        "recent_events": recent_events[:5],
        "current_bag": nysha.get("current_bag") or {},
        "weekly_goal": nysha.get("weekly_goal") or {},
    }


def _school_newsletter_context(n4os_root: Path, lowered_request: str) -> list[dict[str, Any]]:
    mentions_newsletter = any(
        term in lowered_request for term in ("newsletter", "newsletters", "letter", "letters", "imported")
    )
    child = "Nysha" if "nysha" in lowered_request or (mentions_newsletter and "navya" not in lowered_request) else ""
    if not child or not any(
        term in lowered_request
        for term in (
            "school",
            "class",
            "teacher",
            "newsletter",
            "newsletters",
            "letter",
            "letters",
            "imported",
            "book",
            "books",
            "reading",
        )
    ):
        return []

    db_path = n4os_root.parent / "data" / "n4os.db"
    if not db_path.exists() and n4os_root == DEFAULT_N4OS_ROOT:
        db_path = DEFAULT_REPO_ROOT / "data" / "n4os.db"
    if not db_path.exists():
        return []

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT title, teacher, newsletter_date, source_url, parsed_json
            FROM school_newsletter_imports
            WHERE lower(child) = lower(?)
                AND status IN ('saved', 'previewed')
            ORDER BY newsletter_date ASC, updated_at ASC
            """,
            (child,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        if connection is not None:
            connection.close()

    newsletters: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        try:
            parsed = json.loads(str(row["parsed_json"] or "{}"))
        except ValueError:
            continue
        key = (str(row["newsletter_date"] or ""), str(row["source_url"] or ""))
        if key in seen:
            continue
        seen.add(key)
        newsletters.append(
            {
                "date": str(row["newsletter_date"] or parsed.get("newsletter_date") or ""),
                "title": str(row["title"] or parsed.get("title") or "School Newsletter"),
                "teacher": str(row["teacher"] or parsed.get("teacher") or ""),
                "source_url": str(row["source_url"] or parsed.get("source_url") or ""),
                "books": _newsletter_books_from_payload(parsed),
                "topics": _limited_strings(_read_nested(parsed, "knowledge", "topics") or parsed.get("learning_context"), 10),
                "skills": _limited_strings(_read_nested(parsed, "knowledge", "skills"), 8),
                "routines": _limited_strings(_read_nested(parsed, "knowledge", "routines"), 6),
                "recommendations": _limited_strings(_read_nested(parsed, "knowledge", "recommendations"), 6),
                "conversation_prompts": _limited_strings(
                    _read_nested(parsed, "knowledge", "conversation_prompts"),
                    5,
                ),
            }
        )
    return newsletters[-8:]


def _newsletter_books_from_payload(parsed: dict[str, Any]) -> list[str]:
    books = _limited_strings(parsed.get("books"), 20)
    resources = _read_nested(parsed, "knowledge", "resources")
    if isinstance(resources, list):
        for item in resources:
            if not isinstance(item, dict) or str(item.get("kind") or "").casefold() != "book":
                continue
            label = str(item.get("label") or "").strip()
            if label:
                books.append(label)
    return list(dict.fromkeys(books))


def _read_nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _limited_strings(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    items = [str(item).strip() for item in value if str(item).strip()]
    return list(dict.fromkeys(items))[:limit]


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

    request_terms = _expanded_context_terms(lowered_request)
    if request_terms:
        relevant = [record for record in records if _matches_any_context_term(record, request_terms)]
        if relevant:
            return relevant[-12:]
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
    request_terms = _expanded_context_terms(lowered_request)
    for path in sorted(journal_root.glob("*.md")):
        captured_on = path.stem
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.startswith("- "):
                continue
            text = _plain_wiki_text(line.removeprefix("- ").strip())
            if wanted_terms and not any(term in text.lower() for term in wanted_terms):
                continue
            if request_terms and not _matches_any_context_term(text, request_terms):
                continue
            records.append(f"{captured_on}: {text}")
    return records[-12:]


def _expanded_context_terms(lowered_request: str) -> list[str]:
    terms = re.findall(r"[a-z0-9']+", lowered_request.lower())
    stopwords = {
        "about",
        "approach",
        "does",
        "give",
        "have",
        "help",
        "n4os",
        "should",
        "what",
        "when",
        "where",
        "which",
        "with",
    }
    meaningful = [term for term in terms if len(term) >= 4 and term not in stopwords]
    return expand_n4os_query_terms(meaningful)


def _matches_any_context_term(text: str, terms: list[str]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def _try_openai_advice(
    request: str,
    context: dict[str, Any],
    *,
    api_key: str | None,
    model: str | None,
    urlopen: UrlOpen,
) -> tuple[str | None, str | None, str | None]:
    resolved_key = (api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")).strip()
    if not resolved_key:
        return None, None, None

    resolved_model = (model or os.environ.get("N4OS_ADVICE_MODEL") or DEFAULT_MODEL).strip()
    body = {
        "model": resolved_model,
        "store": False,
        "max_output_tokens": 600,
        "reasoning": {"summary": "concise"},
        "text": {"format": N4OS_TRANSPARENT_RESPONSE_FORMAT},
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
                    "End with short Decision, Next action, and Review lines. "
                    "Return a concise reasoning_summary as 2-4 short newline-separated statements that name "
                    "the relevant signals, assumptions, and why they support the answer. This is a high-level "
                    "decision rationale, not hidden chain-of-thought."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": request,
                        "memory": context,
                        "conversation_trajectories": context.get("trajectories") or [],
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
        return None, None, None
    text, disclosed_summary = _extract_transparent_response(payload)
    if not text:
        return None, None, None
    reasoning_summary = _extract_reasoning_summary(payload) or disclosed_summary
    return _normalize_advice_output(text, context), reasoning_summary, resolved_model


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


def _extract_reasoning_summary(payload: dict[str, Any]) -> str | None:
    chunks: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "reasoning":
            continue
        for summary in item.get("summary", []):
            if isinstance(summary, dict) and isinstance(summary.get("text"), str):
                chunks.append(summary["text"].strip())
    text = "\n".join(chunk for chunk in chunks if chunk).strip()
    return text or None


def _extract_transparent_response(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    raw = _extract_response_text(payload)
    if not raw:
        return None, None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None, None
    if not isinstance(parsed, dict):
        return None, None
    answer = parsed.get("answer")
    reasoning_summary = parsed.get("reasoning_summary")
    if not isinstance(answer, str) or not answer.strip():
        return None, None
    if not isinstance(reasoning_summary, str) or not reasoning_summary.strip():
        return None, None
    return answer.strip(), reasoning_summary.strip()


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
    trajectories = context.get("trajectories") or []
    loaded = [item["path"] for item in context.get("files", [])]
    if _is_week_ahead_request(request.lower()):
        return _fallback_week_ahead(context)

    lowered_request = request.lower()
    lines = []
    is_nysha_reading = "nysha" in lowered_request and "reading" in lowered_request
    is_nysha_school_transition = _is_school_transition_request(lowered_request)
    if is_nysha_reading and _is_current_books_lookup(lowered_request):
        reading_answer = _fallback_reading_garden_answer(context)
        if reading_answer:
            return reading_answer
    if _is_newsletter_books_lookup(lowered_request):
        newsletter_answer = _fallback_newsletter_books_answer(context)
        if newsletter_answer:
            return newsletter_answer
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
    if journal and not is_nysha_school_transition:
        lines.extend(["", "Journal signals used:"])
        lines.extend(f"- {item}" for item in journal[-5:])
    if trajectories and not is_nysha_school_transition:
        lines.extend(["", "Conversation signals used:"])
        lines.extend(f"- {item}" for item in trajectories[-3:])
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
        name = priority.get(path) or _compact_school_file_label(path)
        if name is not None:
            compact.append(name)
    return compact


def _compact_school_file_label(path: str) -> str | None:
    if not path.startswith("n4os/school/Nysha/"):
        return None
    filename = Path(path).name
    labels = {
        "School Knowledge.md": "Nysha School Knowledge",
        "Room 13.md": "Room 13",
        "Curriculum Map.md": "Curriculum Map",
        "Homework System.md": "Homework System",
        "Parent Support Playbook.md": "Parent Support Playbook",
        "Resources.md": "School Resources",
        "Conversation Starters.md": "School Conversation Starters",
    }
    return labels.get(filename)


def _is_current_books_lookup(lowered: str) -> bool:
    return bool(
        re.search(
            r"\b(?:what|which)\s+books?\b|\bbooks?\s+(?:is|are|was|were)\b|\bcurrent(?:ly)?\s+reading\b",
            lowered,
        )
    )


def _is_newsletter_books_lookup(lowered: str) -> bool:
    return "book" in lowered and any(
        term in lowered for term in ("newsletter", "newsletters", "letter", "letters", "imported", "mentioned")
    )


def _fallback_newsletter_books_answer(context: dict[str, Any]) -> str | None:
    newsletters = context.get("school_newsletters")
    if not isinstance(newsletters, list) or not newsletters:
        return None

    lines = ["Nysha's imported school newsletters mention these books:"]
    found = False
    for item in newsletters:
        if not isinstance(item, dict):
            continue
        books = [str(book).strip() for book in item.get("books") or [] if str(book).strip()]
        if not books:
            continue
        found = True
        date = str(item.get("date") or "unknown date").strip()
        lines.append(f"{date}:")
        lines.extend(f"- {book}" for book in books[:12])
    if not found:
        return None
    lines.extend(["", "Used: School Newsletters"])
    return "\n".join(lines)


def _fallback_reading_garden_answer(context: dict[str, Any]) -> str | None:
    reading_garden = context.get("reading_garden")
    if not isinstance(reading_garden, dict) or not reading_garden.get("available"):
        return None

    collection = reading_garden.get("book_collection")
    books = collection if isinstance(collection, list) else []
    if not books:
        return None

    lines = ["Nysha's recent Reading Garden titles:"]
    for item in books[:6]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        status = str(item.get("status") or "Reading").strip()
        last_read = str(item.get("last_read") or "").strip()
        suffix = f" ({status}" + (f", last read {last_read}" if last_read else "") + ")"
        lines.append(f"- {title}{suffix}")
    if len(lines) == 1:
        return None

    current = str(reading_garden.get("current_book") or "").strip()
    if current and current.lower() != "unknown book":
        lines.extend(["", f"Current latest title: {current}."])
    lines.extend(["", "Used: Reading Garden, Nysha"])
    return "\n".join(lines)


def _is_week_ahead_request(lowered: str) -> bool:
    return bool(
        re.search(
            r"\b(week ahead|next week|this week|weekly plan|plan my week|week plan|focus this week)\b",
            lowered,
        )
    )


def _is_morning_checkin_request(lowered: str) -> bool:
    return bool(
        re.search(
            r"\b(?:run|start|do)\s+(?:the\s+)?(?:morning\s+)?check-?in\b|"
            r"\bmorning\s+check-?in\b|"
            r"\bhelp\s+me\s+plan\s+(?:tomorrow\s+)?morning\b|"
            r"\bplan\s+(?:my\s+)?(?:tomorrow\s+)?morning\b",
            lowered,
        )
    )


def _is_evening_reflection_request(lowered: str) -> bool:
    return bool(
        re.search(
            r"\b(?:run|start|do)\s+(?:the\s+)?evening\s+(?:reflection|review|check-?in)\b|"
            r"\bevening\s+(?:reflection|review|check-?in)\b|"
            r"\bclose\s+(?:the\s+)?loop\b",
            lowered,
        )
    )


def _fallback_morning_checkin(request: str) -> str:
    lowered = request.lower()
    title = "Tomorrow morning plan" if "tomorrow" in lowered else "Morning check-in"
    return "\n".join(
        [
            f"{title}: keep this simple and concrete.",
            "",
            "Answer these:",
            "1. Energy/body/mind: what state are you starting from?",
            "2. Priority: what matters most, and what can be ignored?",
            "3. Family: who needs presence from you today?",
            "",
            "Commit to 3 things:",
            "1.",
            "2.",
            "3.",
            "",
            "Decision: protect health, family presence, and one highest-leverage action.",
            "Next action: write the 3 commitments now.",
            "Review: run evening reflection tonight.",
        ]
    )


def _fallback_evening_reflection() -> str:
    return "\n".join(
        [
            "Evening reflection: close the loop without judgment.",
            "",
            "Answer these:",
            "1. Did you spend time on the highest priorities?",
            "2. What gave energy, and what drained it?",
            "3. What family moment mattered?",
            "",
            "Adjustment:",
            "1. What should stop, replace, or simplify?",
            "2. What is tomorrow's highest-leverage action?",
            "",
            "Decision: carry the lesson, not the guilt.",
            "Next action: choose tomorrow's one important move.",
            "Review: save anything worth remembering with capture.",
        ]
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
    if journal and target is None:
        lines.extend(["", "Personal signal:"])
        lines.extend(f"- {_compact_signal_line(item)}" for item in journal[-2:])
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
    lines.append("Used: " + ", ".join(_compact_loaded_files(loaded)))
    return "\n".join(lines)


def _week_ahead_summary_lines(operations: dict[str, Any], target: str | None) -> list[str]:
    unavailable = operations.get("unavailable") or []
    if unavailable and not _has_any_operations(operations):
        return ["Calendar/tasks were not available, so this uses N4OS memory only."]

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


def _compact_signal_line(value: str, max_length: int = 180) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[: max_length - 3].rstrip() + "..."


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

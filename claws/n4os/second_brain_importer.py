from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from html.parser import HTMLParser
from pathlib import Path
import re
from typing import Callable, Literal
from urllib.parse import unquote, urlparse
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo


DEFAULT_TIMEZONE = "America/Los_Angeles"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_N4OS_ROOT = ROOT / "n4os"
SAVE_WORDS = {"save", "save all", "confirm", "looks good"}
CANCEL_WORDS = {"cancel", "skip", "discard", "nevermind", "never mind"}
ADJUST_RE = re.compile(r"^\s*adjust\b\s*:?\s*(?P<instructions>.+)$", re.IGNORECASE | re.DOTALL)
URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
IMPORT_RE = re.compile(
    r"^\s*/?import\s+(?:second\s+brain|n4os|knowledge|brain)\b(?P<body>.*)$",
    re.IGNORECASE | re.DOTALL,
)
INSTRUCTIONS_RE = re.compile(r"\bInstructions?\s*:\s*", re.IGNORECASE)
CHILD_RE = re.compile(r"\b(?P<child>Nysha|Navya)\b", re.IGNORECASE)
GOOGLE_SLIDES_RE = re.compile(
    r"https://docs\.google\.com/presentation/d/(?P<id>[A-Za-z0-9_-]+)(?:/[^\s]*)?",
    re.IGNORECASE,
)
SHORTENED_URL_RE = re.compile(r"https?://\S*(?:\.\.\.|…)\S*", re.IGNORECASE)


class SecondBrainImportUserError(ValueError):
    pass


@dataclass(frozen=True)
class ImportSource:
    source_type: str
    title: str
    url: str | None
    text: str
    source_id: str
    fingerprint: str


@dataclass(frozen=True)
class ImportFilePlan:
    path: str
    purpose: str
    content: str
    write_mode: Literal["replace", "profile_link", "marked_section"] = "replace"


@dataclass(frozen=True)
class SecondBrainImportPlan:
    title: str
    instructions: str
    source: ImportSource
    files: tuple[ImportFilePlan, ...]
    future_uses: tuple[str, ...]
    uncertainties: tuple[str, ...]


@dataclass(frozen=True)
class SecondBrainSaveResult:
    message: str
    saved_paths: tuple[str, ...]


@dataclass
class PendingSecondBrainImport:
    plan: SecondBrainImportPlan


class SecondBrainImporter:
    def __init__(
        self,
        *,
        n4os_root: Path = DEFAULT_N4OS_ROOT,
        fetch_text: Callable[[str], str] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.n4os_root = n4os_root
        self.fetch_text = fetch_text or fetch_source_text
        self.now = now or (lambda: datetime.now(ZoneInfo(DEFAULT_TIMEZONE)))
        self.pending: dict[str, PendingSecondBrainImport] = {}

    def has_pending(self, key: str) -> bool:
        return key in self.pending

    def preview_from_message(self, message: str, *, key: str) -> str:
        request = parse_import_request(message)
        source = self._source_from_request(request)
        plan = build_import_plan(
            source,
            instructions=request.instructions,
            n4os_root=self.n4os_root,
            imported_on=self.now().date().isoformat(),
        )
        self.pending[key] = PendingSecondBrainImport(plan=plan)
        return format_import_preview(plan)

    def save_pending(self, *, key: str, response: str) -> SecondBrainSaveResult:
        normalized = " ".join(response.lower().strip(" .!").split())
        pending = self.pending.get(key)
        if pending is None:
            return SecondBrainSaveResult("No second brain import is waiting for confirmation.", ())
        if normalized in CANCEL_WORDS:
            self.pending.pop(key, None)
            return SecondBrainSaveResult("Canceled second brain import.", ())
        adjust_match = ADJUST_RE.match(response)
        if adjust_match:
            instructions = adjust_match.group("instructions").strip()
            plan = build_import_plan(
                pending.plan.source,
                instructions=instructions,
                n4os_root=self.n4os_root,
                imported_on=self.now().date().isoformat(),
            )
            self.pending[key] = PendingSecondBrainImport(plan=plan)
            return SecondBrainSaveResult(format_import_preview(plan), ())
        if normalized not in SAVE_WORDS:
            return SecondBrainSaveResult(
                "Reply `save` to write the N4OS import, `adjust: <changes>`, or `cancel`.",
                (),
            )
        saved = tuple(self._write_file(file_plan) for file_plan in pending.plan.files)
        self.pending.pop(key, None)
        return SecondBrainSaveResult(format_save_result(saved), saved)

    def _source_from_request(self, request: "ParsedImportRequest") -> ImportSource:
        if request.url:
            text = self.fetch_text(request.url)
            source_type = source_type_from_url(request.url)
            source_id = source_id_from_url(request.url)
            title = title_from_text(text) or title_from_url(request.url)
            fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
            return ImportSource(source_type, title, request.url, text, source_id, fingerprint)
        if request.file_path is not None:
            text = request.file_path.read_text(encoding="utf-8")
            fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
            return ImportSource(
                source_type_from_path(request.file_path),
                title_from_text(text) or request.file_path.stem,
                request.file_path.as_posix(),
                text,
                hashlib.sha256(request.file_path.as_posix().encode("utf-8")).hexdigest()[:16],
                fingerprint,
            )
        text = request.body.strip()
        if not text:
            raise ValueError("Please include a link, file text, or pasted source material.")
        fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return ImportSource("text", title_from_text(text), None, text, fingerprint[:16], fingerprint)

    def _write_file(self, file_plan: ImportFilePlan) -> str:
        path = self.n4os_root / file_plan.path
        path.parent.mkdir(parents=True, exist_ok=True)
        if file_plan.write_mode == "profile_link":
            write_profile_link(path, file_plan.content)
        elif file_plan.write_mode == "marked_section":
            write_marked_section(path, file_plan.content)
        else:
            path.write_text(file_plan.content, encoding="utf-8")
        return file_plan.path


@dataclass(frozen=True)
class ParsedImportRequest:
    body: str
    url: str | None
    file_path: Path | None
    instructions: str


def is_second_brain_import_message(message: str) -> bool:
    return bool(IMPORT_RE.search(message))


def is_second_brain_import_followup(message: str) -> bool:
    normalized = " ".join(message.lower().strip(" .!").split())
    return normalized in SAVE_WORDS or normalized in CANCEL_WORDS or bool(ADJUST_RE.match(message))


def parse_import_request(message: str) -> ParsedImportRequest:
    match = IMPORT_RE.search(message)
    if not match:
        raise ValueError("Second brain import must start with `/import second brain` or `/import n4os`.")
    body = match.group("body").strip()
    url_match = URL_RE.search(body)
    raw_url = url_match.group(0) if url_match else None
    if raw_url is not None and is_shortened_url(raw_url):
        raise SecondBrainImportUserError(
            "That link is shortened with `...`, so I cannot fetch it. Paste the full Google Slides URL from Share/Copy Link, then send `/import second brain <full link>` again."
        )
    url = raw_url.rstrip(".,)") if raw_url else None
    instruction_parts = INSTRUCTIONS_RE.split(body, maxsplit=1)
    source_part = instruction_parts[0].replace(url or "", "").strip()
    file_path = parse_file_path(source_part) if url is None else None
    if len(instruction_parts) > 1:
        instructions = instruction_parts[1].strip()
    else:
        instructions = source_part
    return ParsedImportRequest(body=body, url=url, file_path=file_path, instructions=instructions)


def build_import_plan(
    source: ImportSource,
    *,
    instructions: str,
    n4os_root: Path,
    imported_on: str,
) -> SecondBrainImportPlan:
    child = child_from_text(" ".join([instructions, source.text]))
    is_school = looks_like_school_material(instructions, source.text)
    year = school_year_from_text(source.text) or school_year_from_date(imported_on)
    if is_school and child:
        files = school_file_plans(source, child=child, year=year, instructions=instructions, imported_on=imported_on)
        profile_path = Path("family") / f"{child}.md"
        if (n4os_root / profile_path).exists():
            files += (
                ImportFilePlan(
                    profile_path.as_posix(),
                    f"Backlink {child}'s profile to the imported school knowledge pack.",
                    f"[[school/{child}/{year}/Room 13|{year} Room 13 school guide]]",
                    write_mode="profile_link",
                ),
            )
    else:
        files = generic_file_plans(source, instructions=instructions, imported_on=imported_on)
    return SecondBrainImportPlan(
        title=source.title,
        instructions=instructions or "Store this as reusable N4OS second-brain material.",
        source=source,
        files=files,
        future_uses=future_uses_for(instructions, is_school=is_school),
        uncertainties=uncertainties_for(child=child, is_school=is_school),
    )


def school_file_plans(
    source: ImportSource,
    *,
    child: str,
    year: str,
    instructions: str,
    imported_on: str,
) -> tuple[ImportFilePlan, ...]:
    base = f"school/{child}/{year}"
    marker = import_marker(source)
    return (
        ImportFilePlan(
            f"{base}/Source - {slug_title(source.title)}.md",
            "Preserve the factual source extract and provenance.",
            source_note(source, imported_on=imported_on, marker=marker, extra_tags=("n4os/school",)),
        ),
        ImportFilePlan(
            f"{base}/School Knowledge.md",
            "Cumulative school-year facts, routines, resources, guardrails, and future action hooks extracted from the source.",
            school_knowledge_section(source, child=child, imported_on=imported_on, marker=marker),
            write_mode="marked_section",
        ),
        ImportFilePlan(
            f"{base}/Room 13.md",
            "Stable class profile: teacher, schedule, routines, and expectations.",
            school_overview_note(source, child=child, year=year, imported_on=imported_on, marker=marker),
        ),
        ImportFilePlan(
            f"{base}/Curriculum Map.md",
            "Reusable lens for what the class is learning across subjects.",
            curriculum_note(source, child=child, imported_on=imported_on, marker=marker),
        ),
        ImportFilePlan(
            f"{base}/Homework System.md",
            "Reusable lens for homework routines and parent support.",
            homework_system_note(source, child=child, imported_on=imported_on, marker=marker),
        ),
        ImportFilePlan(
            f"{base}/Parent Support Playbook.md",
            "Action layer for home support and prep material.",
            parent_support_note(source, child=child, imported_on=imported_on, marker=marker),
        ),
        ImportFilePlan(
            f"{base}/Conversation Starters.md",
            "Action layer for low-pressure school conversations.",
            conversation_starters_note(source, child=child, imported_on=imported_on, marker=marker),
        ),
        ImportFilePlan(
            f"{base}/Resources.md",
            "Resource links, class codes, and online tools from the source.",
            resources_note(source, child=child, imported_on=imported_on, marker=marker),
        ),
    )


def generic_file_plans(
    source: ImportSource,
    *,
    instructions: str,
    imported_on: str,
) -> tuple[ImportFilePlan, ...]:
    slug = slug_title(source.title)
    marker = import_marker(source)
    base = f"imports/{slug}"
    return (
        ImportFilePlan(
            f"{base}/Source.md",
            "Preserve source extract and provenance.",
            source_note(source, imported_on=imported_on, marker=marker),
        ),
        ImportFilePlan(
            f"{base}/Overview.md",
            "Reusable summary and operating meaning for future N4OS lookup.",
            generic_overview_note(source, instructions=instructions, imported_on=imported_on, marker=marker),
        ),
    )


def source_note(
    source: ImportSource,
    *,
    imported_on: str,
    marker: str,
    extra_tags: tuple[str, ...] = (),
) -> str:
    tags = "\n".join([tag_line("n4os/imported"), *(tag_line(tag) for tag in extra_tags)])
    return "\n".join(
        [
            "---",
            "tags:",
            tags,
            "source:",
            f'  type: "{yaml_escape(source.source_type)}"',
            f'  title: "{yaml_escape(source.title)}"',
            f'  url: "{yaml_escape(source.url or "")}"',
            f'  imported: "{imported_on}"',
            '  confidence: "high"',
            "links:",
            '  - "[[README|N4OS]]"',
            "---",
            "",
            f"# Source - {source.title}",
            "",
            marker,
            "",
            "## What This Is",
            "",
            f"Imported source material for N4OS second-brain use on {imported_on}.",
            "",
            "## Source",
            "",
            f"- Type: {source.source_type}",
            f"- URL: {source.url or 'pasted text'}",
            f"- Source ID: {source.source_id}",
            f"- Fingerprint: {source.fingerprint}",
            "",
            "## Extracted Text",
            "",
            source.text.strip(),
            "",
        ]
    )


def school_knowledge_section(source: ImportSource, *, child: str, imported_on: str, marker: str) -> str:
    return "\n".join(
        [
            marker,
            "",
            f"## Imported Source: {source.title}",
            "",
            f"- Imported: {imported_on}",
            f"- Source type: {source.source_type}",
            f"- Source URL: {source.url or 'pasted text'}",
            f"- Source ID: {source.source_id}",
            "",
            "## What This Is",
            "",
            "Normalized source-backed knowledge extracted for N4OS. Use this as the bridge from imported material to future reminders, prep, coaching, and family context.",
            "",
            "### People And Relationships",
            "",
            bullets(extract_people_lines(source.text)),
            "",
            "### Recurring Routines",
            "",
            bullets(extract_lines(source.text, ("daily schedule", "prep schedule", "homework", "friday", "8:30", "pe", "library", "music", "science lab"))),
            "",
            "### Learning Context",
            "",
            bullets(
                extract_lines(
                    source.text,
                    (
                        "science of reading",
                        "structured literacy",
                        "phonics",
                        "mathematics",
                        "fluency",
                        "conceptual understanding",
                        "application",
                        "Amplify",
                        "Second Step",
                        "social science",
                    ),
                    limit=14,
                )
            ),
            "",
            "### Values And Philosophy",
            "",
            bullets(extract_lines(source.text, ("kind", "responsible", "safe", "respect", "accountability", "growth mindset", "empathy", "problem solving"))),
            "",
            "### Parent Expectations",
            "",
            bullets(
                extract_lines(
                    source.text,
                    (
                        "parents can assist",
                        "parent signature",
                        "listen",
                        "read aloud",
                        "ask questions",
                        "math facts",
                        "spelling",
                        "checking",
                        "homework folder",
                    ),
                    limit=12,
                )
            ),
            "",
            "### Resources",
            "",
            bullets(extract_resources(source.text)),
            "",
            "### Guardrails",
            "",
            bullets(extract_lines(source.text, ("0-25 minutes", "kid time", "independent", "responsible", "accountable", "upper limit"))),
            "",
            "### Suggested Future Actions",
            "",
            bullets(
                (
                    "Review extracted routines before creating reminders, calendar events, or dashboard surfaces.",
                    f"Use school language when coaching {child}: respect, accountability, growth mindset, and problem solving.",
                    "Prefer prompts that help the child remember and plan instead of making parents responsible for every routine.",
                    "Use source-backed resources before recommending new tools.",
                )
            ),
            "",
            "### Questions This Can Answer",
            "",
            bullets(future_uses_for("school extracted knowledge", is_school=True)),
            "",
        ]
    )


def school_overview_note(source: ImportSource, *, child: str, year: str, imported_on: str, marker: str) -> str:
    return frontmatter("n4os/school", source, imported_on) + "\n".join(
        [
            f"# {child} School Guide - {year}",
            "",
            marker,
            "",
            "## What This Is",
            "",
            f"Stable class guide imported for {child}. Use this as context for school expectations, routines, and parent support.",
            "",
            "## Key Facts",
            "",
            bullets(extract_key_facts(source.text)),
            "",
            "## Daily And Weekly Routines",
            "",
            bullets(extract_lines(source.text, ("daily schedule", "prep schedule", "homework", "friday", "pe", "library"))),
            "",
            "## How N4OS Should Use This",
            "",
            bullets(
                (
                    f"Answer what {child} is likely learning or practicing at school.",
                    "Design home prep that matches the teacher's classroom approach.",
                    "Create parent checklists for recurring school routines.",
                    "Suggest low-pressure school conversation prompts.",
                )
            ),
            "",
        ]
    )


def curriculum_note(source: ImportSource, *, child: str, imported_on: str, marker: str) -> str:
    return frontmatter("n4os/school/curriculum", source, imported_on) + "\n".join(
        [
            f"# {child} Curriculum Map",
            "",
            marker,
            "",
            "## Reading And Structured Literacy",
            "",
            bullets(extract_lines(source.text, ("science of reading", "structured literacy", "phonics", "reading", "Benchmark"))),
            "",
            "## Writing",
            "",
            bullets(extract_lines(source.text, ("writer", "writing", "cursive", "sentence dictation"))),
            "",
            "## Math",
            "",
            bullets(extract_lines(source.text, ("mathematics", "addition", "subtraction", "fluency", "conceptual understanding", "application"))),
            "",
            "## Science And Social Studies",
            "",
            bullets(extract_lines(source.text, ("science", "Amplify", "social science", "geography", "community", "government", "economics"))),
            "",
            "## SEL And Classroom Skills",
            "",
            bullets(extract_lines(source.text, ("growth mindset", "Second Step", "empathy", "problem-solving", "accountability", "respect"))),
            "",
        ]
    )


def homework_system_note(source: ImportSource, *, child: str, imported_on: str, marker: str) -> str:
    return frontmatter("n4os/school/homework", source, imported_on) + "\n".join(
        [
            f"# {child} Homework System",
            "",
            marker,
            "",
            "## Routine",
            "",
            bullets(extract_lines(source.text, ("homework", "Friday", "Homework Folder", "parent signature", "0-25 minutes", "reading minutes"))),
            "",
            "## Parent Support",
            "",
            bullets(
                (
                    "Provide a study area away from family activity.",
                    f"Listen to {child} read aloud and ask questions about the reading.",
                    "Help with math facts and spelling words.",
                    "Check written work for accuracy, legibility, and sensible thought.",
                    "Protect kid time when homework has already reached the expected daily limit.",
                )
            ),
            "",
        ]
    )


def parent_support_note(source: ImportSource, *, child: str, imported_on: str, marker: str) -> str:
    return frontmatter("n4os/school/parenting", source, imported_on) + "\n".join(
        [
            f"# {child} Parent Support Playbook",
            "",
            marker,
            "",
            "## Operating Meaning",
            "",
            f"Support {child} by matching school language: respect, accountability, growth mindset, persistence, and problem solving.",
            "",
            "## Practice Ideas",
            "",
            bullets(
                (
                    "Use manipulatives, drawings, and words when practicing math.",
                    "Make phonics practice multimodal: see it, say it, blend it, spell it, write it.",
                    "Turn mistakes into evidence for what to practice next.",
                    "Use reading, poems, songs, and art as connected ways to build memory and confidence.",
                )
            ),
            "",
            "## Source Cues",
            "",
            bullets(extract_lines(source.text, ("respect", "accountability", "growth mindset", "mistakes", "problem", "practice"))),
            "",
        ]
    )


def conversation_starters_note(source: ImportSource, *, child: str, imported_on: str, marker: str) -> str:
    return frontmatter("n4os/school/conversations", source, imported_on) + "\n".join(
        [
            f"# {child} Conversation Starters",
            "",
            marker,
            "",
            "## Low-Pressure Prompts",
            "",
            bullets(
                (
                    "What was one kind, responsible, or safe thing someone did today?",
                    "What mistake helped you learn something today?",
                    "What did your class read, spell, build, draw, sing, or solve today?",
                    "What was one problem you solved with STEP: say, think, explore, pick?",
                    "What was the most interesting word, number, fact, or question from school?",
                    "If you were the teacher for five minutes, what would you teach the class?",
                    "What part of today felt easy, hard, funny, or surprising?",
                )
            ),
            "",
            "## Questions This Can Answer",
            "",
            bullets(future_uses_for("conversation starters school prep class material", is_school=True)),
            "",
        ]
    )


def resources_note(source: ImportSource, *, child: str, imported_on: str, marker: str) -> str:
    return frontmatter("n4os/school/resources", source, imported_on) + "\n".join(
        [
            f"# {child} School Resources",
            "",
            marker,
            "",
            "## Links And Codes",
            "",
            bullets(extract_resources(source.text)),
            "",
            "## Practice Platforms Mentioned",
            "",
            bullets(extract_lines(source.text, ("Lexia", "IXL", "iReady", "Typing", "Scholastic", "Clever", "Dance Mat"))),
            "",
        ]
    )


def generic_overview_note(source: ImportSource, *, instructions: str, imported_on: str, marker: str) -> str:
    return frontmatter("n4os/import", source, imported_on) + "\n".join(
        [
            f"# {source.title}",
            "",
            marker,
            "",
            "## What This Is",
            "",
            instructions or "Reusable source material imported into N4OS.",
            "",
            "## Key Facts",
            "",
            bullets(extract_key_facts(source.text)),
            "",
            "## How N4OS Should Use This",
            "",
            bullets(future_uses_for(instructions, is_school=False)),
            "",
        ]
    )


def frontmatter(tag: str, source: ImportSource, imported_on: str) -> str:
    return "\n".join(
        [
            "---",
            "tags:",
            tag_line("n4os/imported"),
            tag_line(tag),
            "source:",
            f'  type: "{yaml_escape(source.source_type)}"',
            f'  title: "{yaml_escape(source.title)}"',
            f'  url: "{yaml_escape(source.url or "")}"',
            f'  imported: "{imported_on}"',
            '  confidence: "high"',
            "links:",
            '  - "[[README|N4OS]]"',
            "---",
            "",
        ]
    )


def format_import_preview(plan: SecondBrainImportPlan) -> str:
    new_files = [file_plan for file_plan in plan.files if file_plan.write_mode == "replace"]
    updates = [file_plan for file_plan in plan.files if file_plan.write_mode != "replace"]
    lines = [
        f"N4OS import preview: {plan.title}",
        "",
        "Source:",
        f"- {plan.source.title} ({plan.source.source_type})",
    ]
    if plan.source.url:
        lines.append(f"- {plan.source.url}")
    lines.extend(["", "Proposed new files:"])
    lines.extend([f"- n4os/{file_plan.path}: {file_plan.purpose}" for file_plan in new_files])
    if updates:
        lines.extend(["", "Proposed updates:"])
        lines.extend([f"- n4os/{file_plan.path}: {file_plan.purpose}" for file_plan in updates])
    lines.extend(["", "Future uses enabled:"])
    lines.extend([f"- {item}" for item in plan.future_uses])
    if plan.uncertainties:
        lines.extend(["", "Needs confirmation:"])
        lines.extend([f"- {item}" for item in plan.uncertainties])
    lines.extend(["", "Reply `save` to approve this plan, `adjust: <changes>`, or `cancel`."])
    return "\n".join(lines)


def format_save_result(paths: tuple[str, ...]) -> str:
    lines = ["Saved second brain import."]
    if paths:
        lines.append("Files:")
        lines.extend([f"- n4os/{path}" for path in paths])
    return "\n".join(lines)


def write_profile_link(path: Path, link: str) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if link in existing:
        return
    section = "## School Knowledge"
    if section in existing:
        updated = existing.rstrip() + f"\n- {link}\n"
    else:
        updated = existing.rstrip() + f"\n\n{section}\n\n- {link}\n"
    path.write_text(updated + "\n", encoding="utf-8")


def write_marked_section(path: Path, section: str) -> None:
    marker = import_marker_from_section(section)
    if marker is None:
        raise ValueError("Marked section imports require an n4os-import marker.")
    existing = path.read_text(encoding="utf-8") if path.exists() else school_knowledge_header()
    updated = upsert_marked_section(existing, marker=marker, section=section.strip())
    path.write_text(updated.rstrip() + "\n", encoding="utf-8")


def school_knowledge_header() -> str:
    return "\n".join(
        [
            "---",
            "tags:",
            '  - "n4os/imported"',
            '  - "n4os/school/knowledge"',
            "links:",
            '  - "[[README|N4OS]]"',
            "---",
            "",
            "# School Knowledge",
            "",
            "Cumulative source-backed school-year knowledge imported into N4OS.",
            "",
        ]
    )


def import_marker_from_section(section: str) -> str | None:
    match = re.search(r"<!-- n4os-import:[^>]+ -->", section)
    return match.group(0) if match else None


def upsert_marked_section(existing: str, *, marker: str, section: str) -> str:
    start = existing.find(marker)
    if start == -1:
        return existing.rstrip() + "\n\n" + section + "\n"
    next_marker = existing.find("\n\n<!-- n4os-import:", start + len(marker))
    end = len(existing) if next_marker == -1 else next_marker
    return existing[:start].rstrip() + "\n\n" + section + existing[end:].rstrip() + "\n"


def fetch_source_text(url: str) -> str:
    slides_match = GOOGLE_SLIDES_RE.search(url)
    fetch_url = url
    if slides_match:
        fetch_url = f"https://docs.google.com/presentation/d/{slides_match.group('id')}/export/txt"
    try:
        with urllib.request.urlopen(fetch_url, timeout=20) as response:
            raw = response.read().decode("utf-8", errors="replace")
            content_type = response.headers.get("content-type", "")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise SecondBrainImportUserError(
                "I could not fetch that source. If this is a Google Slides deck, paste the full share link with the real presentation ID, not the shortened display link."
            ) from error
        raise
    if "html" in content_type.lower():
        return html_to_text(raw)
    return raw


class _HTMLTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        cleaned = " ".join(data.split())
        if cleaned:
            self.parts.append(cleaned)


def html_to_text(html: str) -> str:
    parser = _HTMLTextParser()
    parser.feed(html)
    return "\n".join(parser.parts)


def source_type_from_url(url: str) -> str:
    if GOOGLE_SLIDES_RE.search(url):
        return "slides"
    return "web"


def is_shortened_url(url: str) -> bool:
    return bool(SHORTENED_URL_RE.search(url))


def source_type_from_path(path: Path) -> str:
    suffix = path.suffix.lower().strip(".")
    return suffix or "file"


def source_id_from_url(url: str) -> str:
    slides_match = GOOGLE_SLIDES_RE.search(url)
    if slides_match:
        return slides_match.group("id")
    parsed = urlparse(url)
    stable = f"{parsed.netloc}{parsed.path}".strip("/") or url
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]


def title_from_url(url: str) -> str:
    parsed = urlparse(url)
    name = unquote(Path(parsed.path).name).strip() or parsed.netloc
    return title_from_text(name.replace("-", " ").replace("_", " "))


def parse_file_path(text: str) -> Path | None:
    cleaned = text.strip()
    if cleaned.lower().startswith("file "):
        cleaned = cleaned[5:].strip()
    cleaned = cleaned.strip("'\"")
    if not cleaned:
        return None
    path = Path(cleaned).expanduser()
    if path.is_file():
        return path
    return None


def title_from_text(text: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip()
        if cleaned and not cleaned.isdigit():
            return cleaned[:80]
    return "Imported Source"


def child_from_text(text: str) -> str | None:
    match = CHILD_RE.search(text)
    return match.group("child").title() if match else None


def looks_like_school_material(instructions: str, text: str) -> bool:
    haystack = " ".join([instructions, text[:4000]]).lower()
    return any(term in haystack for term in ("school", "class", "teacher", "curriculum", "homework", "back-to-school"))


def school_year_from_text(text: str) -> str | None:
    match = re.search(r"\b(20\d{2})\s*[-–]\s*(20\d{2})\b", text)
    return f"{match.group(1)}-{match.group(2)}" if match else None


def school_year_from_date(value: str) -> str:
    year = int(value[:4])
    month = int(value[5:7])
    start = year if month >= 7 else year - 1
    return f"{start}-{start + 1}"


def slug_title(title: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", title.lower())
    return "-".join(words[:8]) or "imported-source"


def import_marker(source: ImportSource) -> str:
    return f"<!-- n4os-import:{source.fingerprint[:16]} -->"


def yaml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def tag_line(tag: str) -> str:
    return f'  - "{tag}"'


def bullets(items: tuple[str, ...]) -> str:
    if not items:
        return "- No source-backed items extracted yet."
    return "\n".join(f"- {item}" for item in items)


def extract_key_facts(text: str) -> tuple[str, ...]:
    return extract_lines(
        text,
        ("teacher", "grade", "schedule", "homework", "curriculum", "reading", "math", "science", "respect", "accountability"),
        limit=12,
    )


def extract_people_lines(text: str, *, limit: int = 12) -> tuple[str, ...]:
    selected: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if len(line) < 4 or line.isdigit():
            continue
        lowered = line.lower()
        has_role = any(
            keyword in lowered
            for keyword in ("teacher", "principal", "secretary", "attendance", "office", "contact", "counselor")
        )
        has_contact = "@" in line or re.search(r"\b\d{3}\s+\d{3}\s+\d{4}\b", line)
        has_name_prefix = bool(re.search(r"\b(?:Mr|Mrs|Ms|Miss|Dr)\.\s+[A-Z][A-Za-z]+", line))
        if (has_role or has_contact or has_name_prefix) and line not in seen:
            selected.append(line)
            seen.add(line)
        if len(selected) >= limit:
            break
    return tuple(selected)


def extract_lines(text: str, keywords: tuple[str, ...], *, limit: int = 10) -> tuple[str, ...]:
    selected: list[str] = []
    seen: set[str] = set()
    lowered_keywords = tuple(keyword.lower() for keyword in keywords)
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if len(line) < 6 or line.isdigit():
            continue
        lowered = line.lower()
        if any(keyword in lowered for keyword in lowered_keywords) and line not in seen:
            selected.append(line)
            seen.add(line)
        if len(selected) >= limit:
            break
    return tuple(selected)


def extract_resources(text: str) -> tuple[str, ...]:
    resources: list[str] = []
    resources.extend(URL_RE.findall(text))
    for line in extract_lines(text, ("code", "Clever", "Lexia", "IXL", "iReady", "Typing", "Scholastic", "Reading Club"), limit=12):
        if line not in resources:
            resources.append(line)
    return tuple(resources[:16])


def future_uses_for(instructions: str, *, is_school: bool) -> tuple[str, ...]:
    base_uses = (
        "Answer future family questions with source-backed context, not memory or guesswork.",
        "Turn important material into plans, prep checklists, routines, prompts, tasks, and review questions.",
        "Connect this source to the right N4OS people, domains, decisions, goals, and playbooks.",
        "Preserve both factual details and operating meaning so the family second brain compounds over time.",
    )
    if is_school:
        return base_uses + (
            "For this school source, also support learning context, class prep, school routines, teacher communication, and child-specific conversation starters.",
        )
    if instructions:
        return base_uses
    return base_uses[:1]


def uncertainties_for(*, child: str | None, is_school: bool) -> tuple[str, ...]:
    if is_school and child is None:
        return ("Which child this school material belongs to.",)
    return ()

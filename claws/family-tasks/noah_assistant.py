from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Protocol
import urllib.error
import urllib.request


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_NOAH_ASSISTANT_MODEL = ""
DEFAULT_OPENAI_NOAH_ASSISTANT_MODEL = "gpt-5.4-mini"
DEFAULT_SEARCH_CONTEXT_SIZE = "low"
DEFAULT_WEB_SEARCH_LIMIT = 5
DEFAULT_TIMEOUT_SECONDS = 45
DEFAULT_WEB_SEARCH_TIMEOUT_SECONDS = 12
MAX_WEB_CONTEXT_CHARS = 8000
MAX_RESULT_SOURCES = 5
MAX_ERROR_CHARS = 700
PROMPT_PLACEHOLDERS = ("{{prompt}}", "{prompt}", "{{Prompt}}")
QUERY_PLACEHOLDERS = ("{{query}}", "{query}", "{{Query}}")
COMMON_NODE_CANDIDATES = (
    "/opt/homebrew/bin/node",
    "/usr/local/bin/node",
    "/usr/bin/node",
)
URL_RE = re.compile(r"https?://[^\s<>)\\]]+")
SOURCE_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?P<title>[^:\n]{1,160}):\s*(?P<url>https?://\S+)",
    re.MULTILINE,
)


@dataclass(frozen=True)
class NoahSource:
    title: str
    url: str


@dataclass(frozen=True)
class NoahResearchResult:
    text: str
    sources: list[NoahSource]


class NoahResearchClient(Protocol):
    def research(
        self,
        *,
        task_title: str,
        help_request: str,
        assistant_context: str,
    ) -> NoahResearchResult:
        ...


def _clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _source_from_mapping(value: Any) -> NoahSource | None:
    if not isinstance(value, dict):
        return None

    data = value.get("url_citation")
    if isinstance(data, dict):
        value = data
    elif value.get("type") != "url_citation" and (
        "url" not in value or "title" not in value
    ):
        return None

    url = _clean_string(value.get("url"))
    if not url:
        return None

    title = _clean_string(value.get("title")) or url
    return NoahSource(title=title, url=url)


def _collect_sources_from_value(value: Any, sources: list[NoahSource]) -> None:
    if isinstance(value, dict):
        source = _source_from_mapping(value)
        if source is not None:
            sources.append(source)
        for child in value.values():
            _collect_sources_from_value(child, sources)
    elif isinstance(value, list):
        for child in value:
            _collect_sources_from_value(child, sources)


def _dedupe_sources(sources: list[NoahSource]) -> list[NoahSource]:
    seen: set[str] = set()
    deduped = []
    for source in sources:
        if source.url in seen:
            continue
        seen.add(source.url)
        deduped.append(source)
    return deduped[:MAX_RESULT_SOURCES]


def _collect_sources_from_text(value: str) -> list[NoahSource]:
    sources: list[NoahSource] = []
    for match in SOURCE_LINE_RE.finditer(value):
        url = match.group("url").rstrip(".,)")
        title = match.group("title").strip(" -*") or url
        sources.append(NoahSource(title=title, url=url))

    for match in URL_RE.finditer(value):
        url = match.group(0).rstrip(".,)")
        sources.append(NoahSource(title=url, url=url))

    return _dedupe_sources(sources)


def _extract_response_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    chunks = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())

    return "\n\n".join(chunks).strip()


def _openai_error_message(error: urllib.error.HTTPError) -> str:
    body = error.read().decode("utf-8", errors="replace").strip()
    if not body:
        return f"OpenAI request failed with HTTP {error.code}."

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        details = body[:MAX_ERROR_CHARS]
    else:
        details = _clean_string(parsed.get("error", {}).get("message")) or body
        details = details[:MAX_ERROR_CHARS]

    return f"OpenAI request failed with HTTP {error.code}: {details}"


def _repo_openclaw_entrypoint() -> Path:
    return Path(__file__).resolve().parents[2] / "openclaw.mjs"


def _resolve_node_command() -> str | None:
    node = shutil.which("node")
    if node:
        return node

    for candidate in COMMON_NODE_CANDIDATES:
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def _default_openclaw_command(args: tuple[str, ...]) -> tuple[str, ...] | None:
    openclaw = shutil.which("openclaw")
    if openclaw:
        return (openclaw, *args)

    node = _resolve_node_command()
    local_entry = _repo_openclaw_entrypoint()
    if node and local_entry.exists():
        return (node, str(local_entry), *args)

    return None


def _default_openclaw_model_command(model: str) -> tuple[str, ...] | None:
    args = ("infer", "model", "run", "--json", "--prompt", "{{prompt}}")
    if model:
        args = (*args, "--model", model)
    return _default_openclaw_command(args)


def _default_openclaw_web_search_command(limit: int) -> tuple[str, ...] | None:
    return _default_openclaw_command(
        (
            "infer",
            "web",
            "search",
            "--json",
            "--query",
            "{{query}}",
            "--limit",
            str(limit),
        )
    )


def _render_command(
    command: tuple[str, ...],
    *,
    placeholders: tuple[str, ...],
    value: str,
) -> list[str]:
    rendered: list[str] = []
    changed = False
    for part in command:
        next_part = part
        for placeholder in placeholders:
            if placeholder in next_part:
                next_part = next_part.replace(placeholder, value)
                changed = True
        rendered.append(next_part)

    if not changed:
        rendered.append(value)
    return rendered


def _parse_json_payload(body: str) -> Any | None:
    stripped = body.strip()
    if not stripped:
        return None

    candidate_starts = [
        0,
        *[
            index
            for index, char in enumerate(stripped)
            if char in "[{" and index > 0 and stripped[index - 1] == "\n"
        ],
    ]
    for start in candidate_starts:
        try:
            return json.loads(stripped[start:])
        except json.JSONDecodeError:
            continue
    return None


def _extract_capability_outputs(payload: Any) -> list[Any]:
    if isinstance(payload, dict):
        outputs = payload.get("outputs")
        if isinstance(outputs, list):
            return outputs

        result = payload.get("result")
        if isinstance(result, dict):
            payloads = result.get("payloads")
            if isinstance(payloads, list):
                return payloads
    return []


def _extract_model_text(payload: Any) -> str:
    chunks: list[str] = []
    for output in _extract_capability_outputs(payload):
        if not isinstance(output, dict):
            continue
        text = output.get("text")
        if isinstance(text, str) and text.strip():
            chunks.append(text.strip())

    if chunks:
        return "\n\n".join(chunks).strip()

    if isinstance(payload, dict):
        text = payload.get("text") or payload.get("output_text")
        if isinstance(text, str):
            return text.strip()
    return ""


def _extract_web_result(payload: Any) -> Any:
    for output in _extract_capability_outputs(payload):
        if isinstance(output, dict) and "result" in output:
            return output["result"]
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


def _format_web_context(value: Any) -> str:
    if value is None:
        return ""
    rendered = json.dumps(value, ensure_ascii=False, indent=2)
    if len(rendered) <= MAX_WEB_CONTEXT_CHARS:
        return rendered
    return rendered[:MAX_WEB_CONTEXT_CHARS].rstrip() + "\n...[truncated]"


def _build_noah_prompt(
    *,
    task_title: str,
    help_request: str,
    assistant_context: str,
    web_context: str,
) -> str:
    lines = [
        "You are Noah, a family operations research assistant.",
        "Return a concise parent-ready update with concrete next steps.",
        "Use supplied OpenClaw web search results when present. Do not invent current public facts.",
        (
            "If current facts are needed and no source is available, say what should be "
            "verified before acting."
        ),
        'When sources are available, end with a "Sources:" section using "- Title: URL".',
        "",
        f"Task: {task_title}",
        f"Help request: {help_request}",
        f"Context: {assistant_context or 'None provided.'}",
    ]
    if web_context:
        lines.extend(["", "OpenClaw web search results:", web_context])
    else:
        lines.extend(["", "OpenClaw web search results: none available."])
    return "\n".join(lines)


class OpenClawNoahResearchClient:
    def __init__(
        self,
        *,
        command: tuple[str, ...] | None = None,
        web_search_command: tuple[str, ...] | None = None,
        model: str = DEFAULT_NOAH_ASSISTANT_MODEL,
        search_limit: int = DEFAULT_WEB_SEARCH_LIMIT,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        web_search_timeout_seconds: int = DEFAULT_WEB_SEARCH_TIMEOUT_SECONDS,
    ) -> None:
        cleaned_model = model.strip()
        self.command = command or _default_openclaw_model_command(cleaned_model)
        if self.command is None:
            raise RuntimeError(
                "Noah assistant needs OpenClaw CLI access before it can research tasks."
            )
        self.web_search_command = web_search_command or _default_openclaw_web_search_command(
            search_limit
        )
        self.timeout_seconds = timeout_seconds
        self.web_search_timeout_seconds = web_search_timeout_seconds

    @classmethod
    def from_env(cls) -> "OpenClawNoahResearchClient":
        return cls(model=os.environ.get("NOAH_ASSISTANT_MODEL", DEFAULT_NOAH_ASSISTANT_MODEL))

    def _run_command(self, command: list[str], timeout_seconds: int) -> str:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                f"Noah assistant command timed out after {timeout_seconds:g}s."
            ) from error

        if completed.returncode != 0:
            details = (completed.stderr or completed.stdout).strip()
            if details:
                raise RuntimeError(details[-MAX_ERROR_CHARS:])
            raise RuntimeError(
                f"Noah assistant command exited with {completed.returncode}."
            )
        return completed.stdout

    def _run_web_search(self, query: str) -> tuple[str, list[NoahSource]]:
        if self.web_search_command is None:
            return "", []

        command = _render_command(
            self.web_search_command,
            placeholders=QUERY_PLACEHOLDERS,
            value=query,
        )
        try:
            stdout = self._run_command(command, self.web_search_timeout_seconds)
        except Exception:
            return "", []

        payload = _parse_json_payload(stdout)
        search_result = _extract_web_result(payload)
        sources: list[NoahSource] = []
        _collect_sources_from_value(search_result, sources)
        return _format_web_context(search_result), _dedupe_sources(sources)

    def research(
        self,
        *,
        task_title: str,
        help_request: str,
        assistant_context: str,
    ) -> NoahResearchResult:
        query = " ".join(
            part
            for part in (task_title, help_request, assistant_context)
            if part.strip()
        )
        web_context, web_sources = self._run_web_search(query)
        prompt = _build_noah_prompt(
            task_title=task_title,
            help_request=help_request,
            assistant_context=assistant_context,
            web_context=web_context,
        )
        command = _render_command(
            self.command,
            placeholders=PROMPT_PLACEHOLDERS,
            value=prompt,
        )
        stdout = self._run_command(command, self.timeout_seconds)
        payload = _parse_json_payload(stdout)
        text = _extract_model_text(payload) if payload is not None else stdout.strip()
        if not text:
            raise RuntimeError("OpenClaw returned no assistant result text.")

        return NoahResearchResult(
            text=text,
            sources=_dedupe_sources([*web_sources, *_collect_sources_from_text(text)]),
        )


class OpenAINoahResearchClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_OPENAI_NOAH_ASSISTANT_MODEL,
        search_context_size: str = DEFAULT_SEARCH_CONTEXT_SIZE,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ):
        cleaned_key = api_key.strip()
        if not cleaned_key:
            raise RuntimeError("Noah assistant needs OPENAI_API_KEY before it can research tasks.")

        self.api_key = cleaned_key
        self.model = model.strip() or DEFAULT_OPENAI_NOAH_ASSISTANT_MODEL
        self.search_context_size = search_context_size.strip() or DEFAULT_SEARCH_CONTEXT_SIZE
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls) -> "OpenAINoahResearchClient":
        return cls(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            model=os.environ.get("NOAH_ASSISTANT_MODEL", DEFAULT_OPENAI_NOAH_ASSISTANT_MODEL),
            search_context_size=os.environ.get(
                "NOAH_ASSISTANT_SEARCH_CONTEXT_SIZE",
                DEFAULT_SEARCH_CONTEXT_SIZE,
            ),
        )

    def research(
        self,
        *,
        task_title: str,
        help_request: str,
        assistant_context: str,
    ) -> NoahResearchResult:
        request_body = {
            "model": self.model,
            "store": False,
            "tools": [
                {
                    "type": "web_search",
                    "search_context_size": self.search_context_size,
                }
            ],
            "tool_choice": "required",
            "include": ["web_search_call.action.sources"],
            "max_output_tokens": 700,
            "input": [
                {
                    "role": "system",
                    "content": (
                        "You are Noah, a family operations research assistant. "
                        "Use web search for current public facts. Prefer official "
                        "sources, do not guess, and say what is still uncertain. "
                        "Return a concise parent-ready update with concrete next steps."
                    ),
                },
                {
                    "role": "user",
                    "content": "\n".join(
                        [
                            f"Task: {task_title}",
                            f"Help request: {help_request}",
                            f"Context: {assistant_context or 'None provided.'}",
                        ]
                    ),
                },
            ],
        }
        request = urllib.request.Request(
            OPENAI_RESPONSES_URL,
            data=json.dumps(request_body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "openclaw-noah-assistant/0.1",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise RuntimeError(_openai_error_message(error)) from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"OpenAI request failed: {error.reason}") from error

        text = _extract_response_text(payload)
        if not text:
            raise RuntimeError("OpenAI returned no assistant result text.")

        sources: list[NoahSource] = []
        _collect_sources_from_value(payload, sources)
        return NoahResearchResult(
            text=text,
            sources=_dedupe_sources(sources),
        )

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
from typing import Any, Protocol, Sequence


VOICE_TRANSCRIBE_COMMAND_ENV = "N4OS_VOICE_TRANSCRIBE_COMMAND"
VOICE_TRANSCRIPTION_UNAVAILABLE_MESSAGE = (
    "Voice transcription is not configured. Configure OpenClaw audio transcription or set "
    "N4OS_VOICE_TRANSCRIBE_COMMAND."
)
VOICE_TRANSCRIBE_TIMEOUT_SECONDS = 120
VOICE_PATH_PLACEHOLDERS = ("{{path}}", "{path}", "{{MediaPath}}")
COMMON_NODE_CANDIDATES = (
    "/opt/homebrew/bin/node",
    "/usr/local/bin/node",
    "/usr/bin/node",
)


class AudioTranscriber(Protocol):
    async def transcribe(self, message: Any) -> str:
        ...


class VoiceTranscriptionUnavailable(RuntimeError):
    pass


def parse_voice_transcribe_command(raw_command: str) -> tuple[str, ...] | None:
    if not raw_command:
        return None

    try:
        command = tuple(shlex.split(raw_command))
    except ValueError as error:
        raise RuntimeError(
            f"{VOICE_TRANSCRIBE_COMMAND_ENV} is not a valid shell command."
        ) from error

    return command or None


def has_audio(message: Any) -> bool:
    return _first_audio_media(message) is not None


def _first_audio_media(message: Any) -> Any | None:
    voice = getattr(message, "voice", None)
    if voice is not None:
        return voice

    audio = getattr(message, "audio", None)
    if audio is not None:
        return audio

    document = getattr(message, "document", None)
    document_mime = str(getattr(document, "mime_type", "") or "").lower()
    if document is not None and document_mime.startswith("audio/"):
        return document

    return None


def _audio_suffix(media: Any) -> str:
    file_name = str(getattr(media, "file_name", "") or "")
    suffix = Path(file_name).suffix
    if suffix and len(suffix) <= 10:
        return suffix

    mime_type = (
        str(getattr(media, "mime_type", "") or "")
        .split(";")[0]
        .strip()
        .lower()
    )
    return {
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/ogg": ".ogg",
        "audio/opus": ".opus",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/webm": ".webm",
    }.get(mime_type, ".audio")


def _default_openclaw_transcribe_command() -> tuple[str, ...] | None:
    openclaw = shutil.which("openclaw")
    if openclaw:
        return (
            openclaw,
            "infer",
            "audio",
            "transcribe",
            "--file",
            "{{path}}",
            "--json",
        )

    node = _resolve_node_command()
    local_entry = Path(__file__).resolve().with_name("openclaw.mjs")
    if node and local_entry.exists():
        return (
            node,
            str(local_entry),
            "infer",
            "audio",
            "transcribe",
            "--file",
            "{{path}}",
            "--json",
        )

    return None


def _resolve_node_command() -> str | None:
    node = shutil.which("node")
    if node:
        return node

    for candidate in COMMON_NODE_CANDIDATES:
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def _command_for_audio_path(command: Sequence[str], audio_path: Path) -> list[str]:
    path_value = str(audio_path)
    rendered = [_replace_audio_path_placeholders(part, path_value) for part in command]
    if rendered == list(command):
        rendered.append(path_value)
    return rendered


def _replace_audio_path_placeholders(value: str, path_value: str) -> str:
    rendered = value
    for placeholder in VOICE_PATH_PLACEHOLDERS:
        rendered = rendered.replace(placeholder, path_value)
    return rendered


def _extract_transcript(stdout: str) -> str:
    body = stdout.strip()
    if not body:
        return ""

    parsed = _parse_json_payload(body)
    if parsed is None:
        return body

    if isinstance(parsed, dict):
        outputs = parsed.get("outputs")
        if isinstance(outputs, list):
            for output in outputs:
                if isinstance(output, dict):
                    text = output.get("text")
                    if isinstance(text, str) and text.strip():
                        return text.strip()
        text = parsed.get("text")
        if isinstance(text, str):
            return text.strip()

    return body


def _parse_json_payload(body: str) -> Any | None:
    candidate_starts = [
        0,
        *[
            index
            for index, char in enumerate(body)
            if char == "{" and index > 0 and body[index - 1] == "\n"
        ],
    ]
    for start in candidate_starts:
        try:
            return json.loads(body[start:])
        except json.JSONDecodeError:
            continue
    return None


class CommandAudioTranscriber:
    def __init__(
        self,
        command: Sequence[str] | None = None,
        cwd: Path | None = None,
    ) -> None:
        self.command = (
            tuple(command)
            if command is not None
            else _default_openclaw_transcribe_command()
        )
        self.cwd = cwd or Path(__file__).resolve().parent

    async def transcribe(self, message: Any) -> str:
        media = _first_audio_media(message)
        if media is None:
            return ""
        if self.command is None:
            raise VoiceTranscriptionUnavailable(VOICE_TRANSCRIPTION_UNAVAILABLE_MESSAGE)

        with tempfile.TemporaryDirectory(prefix="n4os-telegram-audio-") as tmpdir:
            audio_path = Path(tmpdir) / f"message{_audio_suffix(media)}"
            telegram_file = await media.get_file()
            await telegram_file.download_to_drive(audio_path)
            return await asyncio.to_thread(self._run_command, audio_path)

    def _run_command(self, audio_path: Path) -> str:
        command = _command_for_audio_path(self.command, audio_path)
        completed = subprocess.run(
            command,
            cwd=self.cwd,
            capture_output=True,
            text=True,
            timeout=VOICE_TRANSCRIBE_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode != 0:
            details = (completed.stderr or completed.stdout).strip()
            if details:
                raise RuntimeError(details[-500:])
            raise RuntimeError(
                f"Voice transcription command exited with {completed.returncode}."
            )

        return _extract_transcript(completed.stdout)

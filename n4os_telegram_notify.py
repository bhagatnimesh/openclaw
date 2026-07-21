from __future__ import annotations

from pathlib import Path
import json
import os
import urllib.parse
import urllib.request


DEFAULT_ENV_PATH = Path(__file__).resolve().parent / ".env"
TELEGRAM_API_ROOT = "https://api.telegram.org"


def send_telegram_notification(
    text: str,
    *,
    env_path: Path = DEFAULT_ENV_PATH,
    token: str | None = None,
    chat_id: str | None = None,
) -> dict:
    env_values = _read_env_file(env_path)
    resolved_token = token or os.getenv("TELEGRAM_BOT_TOKEN") or env_values.get("TELEGRAM_BOT_TOKEN")
    resolved_chat_id = (
        chat_id
        or os.getenv("N4OS_TELEGRAM_NOTIFY_CHAT_ID")
        or env_values.get("N4OS_TELEGRAM_NOTIFY_CHAT_ID")
        or os.getenv("ALLOWED_TELEGRAM_USER_ID")
        or env_values.get("ALLOWED_TELEGRAM_USER_ID")
    )
    if not resolved_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing.")
    if not resolved_chat_id:
        raise RuntimeError(
            "N4OS_TELEGRAM_NOTIFY_CHAT_ID or ALLOWED_TELEGRAM_USER_ID is missing."
        )

    payload = urllib.parse.urlencode(
        {
            "chat_id": resolved_chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{TELEGRAM_API_ROOT}/bot{resolved_token}/sendMessage",
        data=payload,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read().decode("utf-8")
    data = json.loads(raw)
    if not data.get("ok"):
        raise RuntimeError("Telegram sendMessage returned ok=false.")
    return data


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values

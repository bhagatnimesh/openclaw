#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from n4os_telegram_notify import send_telegram_notification  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send an N4OS notification to the configured Telegram chat.",
    )
    parser.add_argument(
        "message",
        nargs="?",
        help="Notification text. Omit or pass - to read from stdin.",
    )
    args = parser.parse_args()

    text = sys.stdin.read() if not args.message or args.message == "-" else args.message
    if not text.strip():
        raise RuntimeError("Notification text is empty.")

    send_telegram_notification(text.strip())
    print("Telegram notification sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

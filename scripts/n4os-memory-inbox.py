#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from n4os_capture import (  # noqa: E402
    DEFAULT_N4OS_ROOT,
    format_capture_reply,
    ingest_capture_notes,
)
from n4os_memory_inbox import (  # noqa: E402
    DEFAULT_OBSERVATIONS_ROOT,
    format_memory_ingest_reply,
    ingest_memory_inbox_notes,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest N4OS memory inbox notes into dated family observations.",
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="Plain text file exported/copied from Google Docs. Omit or pass - for stdin.",
    )
    parser.add_argument(
        "--observations-root",
        type=Path,
        default=DEFAULT_OBSERVATIONS_ROOT,
        help=(
            "Legacy directory for n4os family observation month files. "
            "When changed from the default, only family observations are written."
        ),
    )
    parser.add_argument(
        "--n4os-root",
        type=Path,
        default=DEFAULT_N4OS_ROOT,
        help="N4OS vault root for capture routing, observations, and journal entries.",
    )
    parser.add_argument(
        "--source",
        default="Google Docs",
        help="Source label recorded next to each observation.",
    )
    parser.add_argument(
        "--default-date",
        type=date.fromisoformat,
        help="Date for notes that do not include an explicit YYYY-MM-DD line.",
    )
    args = parser.parse_args()

    inbox_text = _read_input(args.input)
    if args.observations_root != DEFAULT_OBSERVATIONS_ROOT:
        result = ingest_memory_inbox_notes(
            inbox_text,
            observations_root=args.observations_root,
            default_date=args.default_date,
            source=args.source,
        )
        print(format_memory_ingest_reply(result))
        return 0

    result = ingest_capture_notes(
        inbox_text,
        n4os_root=args.n4os_root,
        default_date=args.default_date,
        source=args.source,
    )
    print(format_capture_reply(result))
    return 0


def _read_input(input_path: str | None) -> str:
    if not input_path or input_path == "-":
        return sys.stdin.read()
    return Path(input_path).read_text(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from datetime import date as Date, datetime, timedelta
from html import unescape
import re
from typing import Any, Literal, Protocol, TypedDict

from intent import DEFAULT_CHILD, extract_intent
from provider import SQLiteLibraryProvider


class LibraryProvider(Protocol):
    def add_event(
        self,
        *,
        child: str,
        date: str | Date,
        book: str,
        minutes: int | None,
        pages: int | None,
        reaction: str | None,
        status: str,
        source: str,
        photo_path: str | None,
        raw_input: str,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        ...

    def list_events(
        self,
        *,
        child: str | None = None,
        start_date: str | Date | None = None,
        end_date: str | Date | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        ...

    def add_visit(
        self,
        *,
        visit_date: str | Date,
        due_date: str | Date | None,
        titles: list[str],
        source: str,
        raw_input: str,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        ...

    def latest_visit(self) -> dict[str, Any] | None:
        ...


class ToolResponse(TypedDict, total=False):
    status: Literal["ok", "needs_information", "not_counted", "error"]
    message: str
    data: dict[str, Any]


def _error_response(error: Exception) -> ToolResponse:
    return {
        "status": "error",
        "message": f"Reading Garden storage failed: {error}",
        "data": {"error_type": error.__class__.__name__},
    }


def _growth_message(event: dict[str, Any]) -> str:
    if event.get("status") == "completed":
        return "Saved. A new flower bloomed for finishing a book herself."
    if event.get("reaction"):
        return "Saved. A butterfly visited Nysha's Reading Garden."
    pages = event.get("pages")
    if isinstance(pages, int) and pages >= 5:
        return "Saved. Nysha's Reading Garden grew a new leaf."
    return "Saved as a reading moment. Nysha's Reading Garden grew a new sprout."


def _week_start(today: Date) -> Date:
    return today - timedelta(days=today.weekday())


def _event_date(event: dict[str, Any]) -> Date:
    return Date.fromisoformat(str(event["date"])[:10])


def _parse_date_candidate(value: str, today: Date) -> str | None:
    cleaned = value.strip().rstrip(".,")
    formats = [
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y-%m-%d",
        "%B %d, %Y",
        "%b %d, %Y",
        "%B %d %Y",
        "%b %d %Y",
        "%B %d",
        "%b %d",
    ]
    for date_format in formats:
        try:
            parsed = datetime.strptime(cleaned, date_format).date()
        except ValueError:
            continue
        if "%Y" not in date_format and "%y" not in date_format:
            parsed = parsed.replace(year=today.year)
            if parsed < today:
                parsed = parsed.replace(year=today.year + 1)
        return parsed.isoformat()
    return None


def _extract_due_date(request: str, today: Date) -> str | None:
    patterns = [
        r"\b(?:due date|due by|due)\s*[:\-]?\s*([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})",
        r"\b(?:due date|due by|due)\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{2,4})",
        r"\b(?:due date|due by|due)\s*[:\-]?\s*(\d{4}-\d{2}-\d{2})",
        r"\b(?:due date|due by|due)\s*[:\-]?\s*([A-Za-z]{3,9}\s+\d{1,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, request, flags=re.IGNORECASE)
        if match:
            parsed = _parse_date_candidate(match.group(1), today)
            if parsed:
                return parsed
    return None


def _title_from_line(line: str, *, allow_plain: bool = False) -> str | None:
    cleaned = unescape(re.sub(r"<[^>]+>", " ", line))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -*•\t")
    if not cleaned:
        return None

    label_match = re.match(r"^(?:title|book|item)\s*[:\-]\s*(.+)$", cleaned, flags=re.IGNORECASE)
    if label_match:
        cleaned = label_match.group(1).strip()
    elif not allow_plain and not re.match(r"^(?:[-*•]|\d+[.)])\s*", line.strip()):
        return None

    lowered = cleaned.lower()
    blocked = (
        "account",
        "barcode",
        "card",
        "checked out",
        "checkout",
        "courtesy",
        "due",
        "email",
        "fine",
        "library",
        "receipt",
        "renew",
        "return",
        "subject",
        "thank",
    )
    if any(word in lowered for word in blocked):
        return None
    if "@" in cleaned or "http" in lowered:
        return None
    if re.search(r"\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}", cleaned):
        return None
    cleaned = re.sub(r"\s+(?:by|author)\s+.+$", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip(" .,:;-\"'")
    if len(cleaned) < 2 or len(cleaned) > 120:
        return None
    return cleaned


def _extract_titles(request: str) -> list[str]:
    titles = []
    seen = set()
    allow_plain = False
    for line in request.splitlines():
        if re.search(r"\b(?:checked out|checkout|items?|titles?)\b", line, flags=re.IGNORECASE):
            allow_plain = True
        title = _title_from_line(line, allow_plain=allow_plain)
        if not title:
            continue
        key = title.lower()
        if key in seen:
            continue
        titles.append(title)
        seen.add(key)
    return titles[:30]


def parse_checkout(request: str, today: Date) -> dict[str, Any]:
    return {
        "visit_date": today.isoformat(),
        "due_date": _extract_due_date(request, today),
        "titles": _extract_titles(request),
        "raw_input": request.strip(),
    }


def _empty_library_visit() -> dict[str, Any]:
    return {
        "has_visit": False,
        "last_visit_date": "",
        "days_since_visit": None,
        "state": "empty",
        "label": "Paste a library checkout email to start your library bag.",
        "due_date": "",
    }


def _empty_current_bag() -> dict[str, Any]:
    return {
        "count": 0,
        "titles": [],
        "due_date": "",
    }


def _library_visit_summary(visit: dict[str, Any] | None, today: Date) -> tuple[dict[str, Any], dict[str, Any]]:
    if visit is None:
        return _empty_library_visit(), _empty_current_bag()

    visit_date = Date.fromisoformat(str(visit["visit_date"])[:10])
    days_since_visit = max(0, (today - visit_date).days)
    if days_since_visit >= 22:
        state = "ready"
        label = "Ready for the next library adventure."
    elif days_since_visit >= 14:
        state = "good_week"
        label = "Good week for a library visit."
    else:
        state = "enjoy"
        label = "Enjoy this library bag."

    due_date = str(visit.get("due_date") or "")
    titles = list(visit.get("titles") or [])
    return (
        {
            "has_visit": True,
            "last_visit_date": visit_date.isoformat(),
            "days_since_visit": days_since_visit,
            "state": state,
            "label": label,
            "due_date": due_date,
        },
        {
            "count": len(titles),
            "titles": titles,
            "due_date": due_date,
        },
    )


def build_summary(
    events: list[dict[str, Any]],
    today: Date,
    latest_visit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    week_start = _week_start(today)
    week_events = [event for event in events if _event_date(event) >= week_start]
    today_events = [event for event in events if _event_date(event) == today]
    books = [event for event in events if event.get("book") and event.get("book") != "unknown book"]
    completed = [event for event in events if event.get("status") == "completed"]
    reactions = [event for event in events if event.get("reaction")]
    photos = [event for event in events if event.get("photo_path")]
    page_events = [
        event for event in events
        if isinstance(event.get("pages"), int) and event["pages"] >= 5
    ]
    distinct_days = {event["date"] for event in events}
    library_visit, current_bag = _library_visit_summary(latest_visit, today)

    return {
        "title": "Nysha’s Reading Garden: I Read It Myself",
        "child": DEFAULT_CHILD,
        "today": {
            "read": bool(today_events),
            "label": "I read myself today" if today_events else "Not yet today",
        },
        "current_book": books[0]["book"] if books else "unknown book",
        "week": {
            "reading_moments": len(week_events),
            "pages": sum(event.get("pages") or 0 for event in week_events),
            "minutes": sum(event.get("minutes") or 0 for event in week_events),
        },
        "finished": {
            "count": len(completed),
            "recent_books": [event["book"] for event in completed[:5]],
        },
        "favorite_reaction": reactions[0]["reaction"] if reactions else "",
        "recent_photos": [
            {"path": event["photo_path"], "book": event.get("book") or "unknown book"}
            for event in photos[:6]
        ],
        "garden": {
            "sprouts": len(distinct_days),
            "leaves": len(page_events),
            "flowers": len(completed),
            "butterflies": len(reactions),
        },
        "recent_events": events[:8],
        "library_visit": library_visit,
        "current_bag": current_bag,
    }


class LibraryTools:
    """Tool layer for Nysha's independent Reading Garden."""

    def __init__(self, provider: LibraryProvider):
        self.provider = provider

    def record_reading(
        self,
        request: str,
        *,
        now: datetime | None = None,
        source: str = "telegram_text",
        photo_path: str | None = None,
    ) -> ToolResponse:
        intent = extract_intent(request, now=now, source=source, photo_path=photo_path)
        action = intent.get("intent")
        if action == "status":
            return self.status(now=now)
        if action == "record_checkout":
            return self.record_checkout(request, now=now, source=source)
        if action == "not_counted":
            return {
                "status": "not_counted",
                "message": "Not counted for the Reading Garden because this tracks only books Nysha reads herself.",
                "data": {"reason": intent.get("reason")},
            }
        if action == "clarify":
            return {
                "status": "needs_information",
                "message": str(intent.get("question") or "Did Nysha read this herself?"),
                "data": {"missing_fields": intent.get("missing_fields", [])},
            }
        if action != "record_reading":
            return {
                "status": "needs_information",
                "message": "Did Nysha read this herself?",
                "data": {"missing_fields": intent.get("missing_fields", ["independent_reading"])},
            }

        try:
            event = self.provider.add_event(
                child=str(intent["child"]),
                date=str(intent["date"]),
                book=str(intent["book"]),
                minutes=intent.get("minutes"),
                pages=intent.get("pages"),
                reaction=intent.get("reaction"),
                status=str(intent["status"]),
                source=str(intent["source"]),
                photo_path=intent.get("photo_path"),
                raw_input=str(intent["raw_input"]),
            )
        except Exception as error:
            return _error_response(error)

        return {
            "status": "ok",
            "message": _growth_message(event),
            "data": {"event": event},
        }

    def record_checkout(
        self,
        request: str,
        *,
        now: datetime | None = None,
        source: str = "telegram_text",
    ) -> ToolResponse:
        today = (now or datetime.now().astimezone()).date()
        checkout = parse_checkout(request, today)
        titles = checkout["titles"]
        if not titles:
            return {
                "status": "needs_information",
                "message": "I could not find book titles in that library bag yet.",
                "data": {"missing_fields": ["titles"]},
            }
        try:
            visit = self.provider.add_visit(
                visit_date=str(checkout["visit_date"]),
                due_date=checkout["due_date"],
                titles=titles,
                source=source,
                raw_input=str(checkout["raw_input"]),
            )
        except Exception as error:
            return _error_response(error)

        book_label = "book" if len(titles) == 1 else "books"
        return {
            "status": "ok",
            "message": f"Saved this library bag with {len(titles)} {book_label} at home.",
            "data": {"visit": visit},
        }

    def status(self, *, now: datetime | None = None) -> ToolResponse:
        today = (now or datetime.now().astimezone()).date()
        try:
            events = self.provider.list_events(child=DEFAULT_CHILD, limit=250)
            latest_visit = self.provider.latest_visit()
        except Exception as error:
            return _error_response(error)
        summary = build_summary(events, today, latest_visit)
        label = summary["today"]["label"]
        week = summary["week"]
        return {
            "status": "ok",
            "message": (
                f"{label}. This week: {week['reading_moments']} reading moments, "
                f"{week['pages']} pages, {week['minutes']} minutes."
            ),
            "data": {"summary": summary},
        }


def build_default_tools() -> LibraryTools:
    return LibraryTools(SQLiteLibraryProvider())

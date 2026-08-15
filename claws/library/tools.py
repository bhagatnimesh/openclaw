from __future__ import annotations

from datetime import date as Date, datetime, timedelta
from html import unescape
import re
from typing import Any, Literal, Protocol, TypedDict

from intent import CHILDREN, DEFAULT_CHILD, extract_intent
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
        reading_mode: str,
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

    def update_event(
        self,
        event_id: str,
        *,
        child: str | None = None,
        date: str | Date | None = None,
        book: str | None = None,
        minutes: int | None = None,
        pages: int | None = None,
        reaction: str | None = None,
        status: str | None = None,
        reading_mode: str | None = None,
        clear_minutes: bool = False,
        clear_pages: bool = False,
        clear_reaction: bool = False,
    ) -> dict[str, Any] | None:
        ...

    def delete_event(self, event_id: str) -> dict[str, Any] | None:
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


LIBRARY_SOURCES = {"telegram_text", "telegram_voice", "telegram_photo"}


def _error_response(error: Exception) -> ToolResponse:
    return {
        "status": "error",
        "message": f"Reading Garden storage failed: {error}",
        "data": {"error_type": error.__class__.__name__},
    }


def _growth_message(event: dict[str, Any]) -> str:
    child = str(event.get("child") or DEFAULT_CHILD)
    mode = str(event.get("reading_mode") or "unknown")
    if mode == "read_aloud":
        return f"Saved family read-aloud for {child}."
    if mode == "read_together":
        return f"Saved family reading moment for {child}."
    if event.get("status") == "completed":
        return f"Saved. A new flower bloomed for {child} finishing a book."
    if event.get("reaction"):
        return f"Saved. A butterfly visited {child}'s Reading Garden."
    pages = event.get("pages")
    if isinstance(pages, int) and pages >= 5:
        return f"Saved. {child}'s Reading Garden grew a new leaf."
    return f"Saved as a reading moment. {child}'s Reading Garden grew a new sprout."


def _week_start(today: Date) -> Date:
    return today - timedelta(days=today.weekday())


def _counts_for_kid_reading(event: dict[str, Any]) -> bool:
    # Kid dashboards are self-reading scoreboards; read-aloud/together stay in Family.
    return str(event.get("reading_mode") or "unknown") in {"independent", "unknown"}


def _event_date(event: dict[str, Any]) -> Date:
    return Date.fromisoformat(str(event["date"])[:10])


def _valid_book_title(value: Any) -> bool:
    title = str(value or "").strip().lower()
    if title in {"", "unknown book", "this", "this book", "a book"}:
        return False
    return not any(
        phrase in title
        for phrase in (
            "no visible",
            "no readable",
            "no book",
            "no title",
            "checklist entries",
            "unable to",
            "cannot determine",
        )
    )


def _clean_optional_text(value: Any) -> str | None:
    cleaned = " ".join(str(value or "").split()).strip()
    return cleaned or None


def _library_source(value: Any) -> str:
    source = str(value or "telegram_text").split(":", 1)[0]
    return source if source in LIBRARY_SOURCES else "telegram_text"


def _positive_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _public_event_label(event: dict[str, Any]) -> str:
    child = str(event.get("child") or DEFAULT_CHILD)
    book = str(event.get("book") or "reading moment")
    date = str(event.get("date") or "")
    return f"{child}: {book}" + (f" on {date}" if date else "")


def _matches_book(event: dict[str, Any], target_book: str | None) -> bool:
    if not target_book:
        return True
    event_book = str(event.get("book") or "").lower()
    target = target_book.lower()
    return target in event_book or event_book in target


def _select_reading_event(
    events: list[dict[str, Any]],
    *,
    event_id: str | None = None,
    child: str | None = None,
    target_book: str | None = None,
) -> dict[str, Any] | None:
    if event_id:
        for event in events:
            if str(event.get("id") or "") == event_id:
                return event
        return None
    candidates = events
    if child:
        candidates = [event for event in candidates if str(event.get("child") or "") == child]
    if target_book:
        candidates = [event for event in candidates if _matches_book(event, target_book)]
    return candidates[0] if candidates else None


def _date_range(start: Date, end: Date) -> list[Date]:
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def _streaks(reading_days: set[Date], today: Date) -> dict[str, Any]:
    current = 0
    cursor = today
    while cursor in reading_days:
        current += 1
        cursor -= timedelta(days=1)

    best = 0
    run = 0
    previous: Date | None = None
    for day in sorted(reading_days):
        if previous is not None and day == previous + timedelta(days=1):
            run += 1
        else:
            run = 1
        best = max(best, run)
        previous = day

    return {"current": current, "best": best, "grace_days": 1}


def _badge(id_value: str, label: str, detail: str, earned: bool) -> dict[str, Any]:
    return {"id": id_value, "label": label, "detail": detail, "earned": earned}


def _badges(
    events: list[dict[str, Any]],
    reading_days: set[Date],
    completed: list[dict[str, Any]],
    today: Date,
    *,
    has_library_visit: bool = False,
) -> list[dict[str, Any]]:
    modes = {str(event.get("reading_mode") or "unknown") for event in events}
    return [
        _badge("first-sprout", "First Sprout", "Log the first reading moment.", bool(events)),
        _badge("five-days", "Five Reading Days", "Read on five different days.", len(reading_days) >= 5),
        _badge("book-bloom", "Book Bloom", "Finish a book.", bool(completed)),
        _badge("library-explorer", "Library Explorer", "Bring home a library bag.", has_library_visit),
        _badge("read-together", "Read Together", "Log a family reading moment.", "read_together" in modes),
        _badge("story-streak", "Story Streak", "Reach a three-day streak.", _streaks(reading_days, today)["best"] >= 3),
    ]


def _history(events: list[dict[str, Any]], today: Date) -> dict[str, Any]:
    month_start = today.replace(day=1)
    calendar_start = today - timedelta(days=83)
    day_counts: dict[str, int] = {}
    month_events = []
    for event in events:
        event_day = _event_date(event)
        day_counts[event_day.isoformat()] = day_counts.get(event_day.isoformat(), 0) + 1
        if event_day >= month_start:
            month_events.append(event)

    heatmap = [
        {"date": day.isoformat(), "count": day_counts.get(day.isoformat(), 0)}
        for day in _date_range(calendar_start, today)
    ]
    return {
        "heatmap": heatmap,
        "monthly": {
            "reading_days": len({_event_date(event) for event in month_events}),
            "moments": len(month_events),
            "pages": sum(event.get("pages") or 0 for event in month_events),
            "minutes": sum(event.get("minutes") or 0 for event in month_events),
            "finished_books": len([event for event in month_events if event.get("status") == "completed"]),
        },
    }


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

    label_match = re.match(r"^(?:book\s+title|title|book|item)\s*[:\-]\s*(.+)$", cleaned, flags=re.IGNORECASE)
    if label_match:
        cleaned = label_match.group(1).strip()
    elif not allow_plain and not re.match(r"^(?:[-*•]|\d+[.)])\s*", line.strip()):
        return None

    lowered = cleaned.lower()
    blocked = (
        "account",
        "author",
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
        if re.search(r"\b(?:checked out|checkout|items?|titles)\b", line, flags=re.IGNORECASE):
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
    library_visit, current_bag = _library_visit_summary(latest_visit, today)
    has_library_visit = latest_visit is not None

    def child_summary(
        child: str,
        child_events: list[dict[str, Any]],
        title: str,
        *,
        count_all_modes: bool = False,
    ) -> dict[str, Any]:
        tracked_events = child_events if count_all_modes else [
            event for event in child_events
            if _counts_for_kid_reading(event)
        ]
        week_start = _week_start(today)
        week_events = [event for event in tracked_events if _event_date(event) >= week_start]
        today_events = [event for event in tracked_events if _event_date(event) == today]
        books = [
            event for event in tracked_events
            if _valid_book_title(event.get("book"))
        ]
        completed = [event for event in tracked_events if event.get("status") == "completed"]
        reactions = [event for event in tracked_events if event.get("reaction")]
        photos = [event for event in tracked_events if event.get("photo_path")]
        page_events = [
            event for event in tracked_events
            if isinstance(event.get("pages"), int) and event["pages"] >= 5
        ]
        reading_days = {_event_date(event) for event in tracked_events}
        week_days = {_event_date(event) for event in week_events}
        weekly_target = 5
        weekly_count = len(week_days)
        progress = min(1.0, weekly_count / weekly_target)
        collection = []
        seen_books = set()
        for event in books:
            book = str(event["book"])
            key = book.lower()
            if key in seen_books:
                continue
            seen_books.add(key)
            collection.append(
                {
                    "title": book,
                    "status": "Finished" if event.get("status") == "completed" else "Reading",
                    "last_read": str(event.get("date") or ""),
                },
            )

        return {
            "title": title,
            "child": child,
            "today": {
                "read": bool(today_events),
                "label": "I read today" if today_events else "Not yet today",
            },
            "current_book": books[0]["book"] if books else "unknown book",
            "week": {
                "reading_moments": len(week_events),
                "reading_days": weekly_count,
                "pages": sum(event.get("pages") or 0 for event in week_events),
                "minutes": sum(event.get("minutes") or 0 for event in week_events),
            },
            "weekly_goal": {
                "target_days": weekly_target,
                "reading_days": weekly_count,
                "remaining_days": max(0, weekly_target - weekly_count),
                "progress": progress,
                "percent": round(progress * 100),
                "label": f"{weekly_count} of {weekly_target} reading days",
            },
            "streaks": _streaks(reading_days, today),
            "finished": {
                "count": len(completed),
                "recent_books": [event["book"] for event in completed[:5]],
            },
            "favorite_reaction": reactions[0]["reaction"] if reactions else "",
            "recent_photos": [
                {
                    "path": event["photo_path"],
                    "book": event.get("book") if _valid_book_title(event.get("book")) else "Book snap",
                }
                for event in photos[:6]
            ],
            "garden": {
                "sprouts": len(reading_days),
                "leaves": len(page_events),
                "flowers": len(completed),
                "butterflies": len(reactions),
            },
            "badges": _badges(tracked_events, reading_days, completed, today, has_library_visit=has_library_visit),
            "history": _history(tracked_events, today),
            "book_collection": collection[:30],
            "recent_events": tracked_events[:8],
            "library_visit": library_visit,
            "current_bag": current_bag,
        }

    by_child = {
        child: child_summary(child, [event for event in events if event.get("child") == child], f"{child}'s Reading Garden")
        for child in CHILDREN
    }
    family = child_summary("Family", events, "Family Reading Garden", count_all_modes=True)

    return {
        **by_child[DEFAULT_CHILD],
        "title": "Reading Garden",
        "children": list(CHILDREN),
        "by_child": by_child,
        "family": family,
        "selected_child": DEFAULT_CHILD,
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
            children = intent.get("children") or []
            return self.status(
                now=now,
                child=str(children[0]) if len(children) == 1 else None,
            )
        if action == "record_checkout":
            return self.record_checkout(request, now=now, source=source)
        if action == "update_reading":
            children = intent.get("children") or []
            return self.update_reading(
                child=str(children[0]) if len(children) == 1 else None,
                target_book=str(intent.get("target_book") or "") or None,
                date=str(intent.get("date") or "") or None,
                book=str(intent.get("book") or "") or None,
                minutes=intent.get("minutes"),
                pages=intent.get("pages"),
                status=str(intent.get("status") or "") or None,
                reading_mode=str(intent.get("reading_mode") or "") or None,
            )
        if action == "delete_reading":
            children = intent.get("children") or []
            return self.delete_reading(
                child=str(children[0]) if len(children) == 1 else None,
                target_book=str(intent.get("target_book") or "") or None,
            )
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
            events = [
                self.provider.add_event(
                    child=str(child),
                    date=str(intent["date"]),
                    book=str(intent["book"]),
                    minutes=intent.get("minutes"),
                    pages=intent.get("pages"),
                    reaction=intent.get("reaction"),
                    status=str(intent["status"]),
                    reading_mode=str(intent.get("reading_mode") or "unknown"),
                    source=_library_source(intent["source"]),
                    photo_path=intent.get("photo_path"),
                    raw_input=str(intent["raw_input"]),
                )
                for child in intent.get("children", [intent["child"]])
            ]
        except Exception as error:
            return _error_response(error)

        event = events[0]
        message = _growth_message(event)
        if len(events) > 1:
            message = f"Saved {len(events)} reading moments. The family Reading Garden grew."
        return {
            "status": "ok",
            "message": message,
            "data": {"event": event, "events": events},
        }

    def update_reading(
        self,
        *,
        event_id: str | None = None,
        child: str | None = None,
        target_book: str | None = None,
        date: str | None = None,
        book: str | None = None,
        minutes: int | None = None,
        pages: int | None = None,
        reaction: str | None = None,
        status: str | None = None,
        reading_mode: str | None = None,
        clear_minutes: bool = False,
        clear_pages: bool = False,
        clear_reaction: bool = False,
    ) -> ToolResponse:
        cleaned_child = _clean_optional_text(child)
        cleaned_target_book = _clean_optional_text(target_book)
        cleaned_book = _clean_optional_text(book)
        cleaned_date = _clean_optional_text(date)
        cleaned_status = _clean_optional_text(status)
        cleaned_mode = _clean_optional_text(reading_mode)
        cleaned_reaction = _clean_optional_text(reaction)
        if (
            not cleaned_book
            and not cleaned_date
            and minutes is None
            and pages is None
            and cleaned_reaction is None
            and cleaned_status is None
            and cleaned_mode is None
            and not clear_minutes
            and not clear_pages
            and not clear_reaction
        ):
            return {
                "status": "needs_information",
                "message": "What should I change on that reading moment?",
                "data": {"missing_fields": ["change"]},
            }

        try:
            events = self.provider.list_events(limit=500)
            target = _select_reading_event(
                events,
                event_id=_clean_optional_text(event_id),
                child=cleaned_child,
                target_book=cleaned_target_book,
            )
            if target is None:
                return {
                    "status": "error",
                    "message": "I could not find that reading moment to update.",
                    "data": {"event_id": event_id, "child": cleaned_child, "target_book": cleaned_target_book},
                }
            updated = self.provider.update_event(
                str(target["id"]),
                child=cleaned_child,
                date=cleaned_date,
                book=cleaned_book,
                minutes=_positive_int(minutes),
                pages=_positive_int(pages),
                reaction=cleaned_reaction,
                status=cleaned_status,
                reading_mode=cleaned_mode,
                clear_minutes=clear_minutes,
                clear_pages=clear_pages,
                clear_reaction=clear_reaction,
            )
        except Exception as error:
            return _error_response(error)

        if updated is None:
            return {
                "status": "error",
                "message": "I could not find that reading moment to update.",
                "data": {"event_id": event_id},
            }
        return {
            "status": "ok",
            "message": f"Updated reading moment: {_public_event_label(updated)}.",
            "data": {"event": updated},
        }

    def delete_reading(
        self,
        *,
        event_id: str | None = None,
        child: str | None = None,
        target_book: str | None = None,
    ) -> ToolResponse:
        try:
            events = self.provider.list_events(limit=500)
            target = _select_reading_event(
                events,
                event_id=_clean_optional_text(event_id),
                child=_clean_optional_text(child),
                target_book=_clean_optional_text(target_book),
            )
            if target is None:
                return {
                    "status": "error",
                    "message": "I could not find that reading moment to delete.",
                    "data": {"event_id": event_id, "child": child, "target_book": target_book},
                }
            deleted = self.provider.delete_event(str(target["id"]))
        except Exception as error:
            return _error_response(error)

        if deleted is None:
            return {
                "status": "error",
                "message": "I could not find that reading moment to delete.",
                "data": {"event_id": event_id},
            }
        return {
            "status": "ok",
            "message": f"Deleted reading moment: {_public_event_label(deleted)}.",
            "data": {"event": deleted},
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
                source=_library_source(source),
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

    def status(
        self,
        *,
        now: datetime | None = None,
        child: str | None = None,
    ) -> ToolResponse:
        today = (now or datetime.now().astimezone()).date()
        try:
            events = self.provider.list_events(limit=500)
            latest_visit = self.provider.latest_visit()
        except Exception as error:
            return _error_response(error)
        summary = build_summary(events, today, latest_visit)
        selected = summary
        if child:
            child_summary = summary["by_child"].get(child)
            if child_summary is None:
                return {
                    "status": "needs_information",
                    "message": f"I do not have a Reading Garden for {child}.",
                    "data": {"child": child},
                }
            selected = child_summary
        label = selected["today"]["label"]
        week = selected["week"]
        return {
            "status": "ok",
            "message": (
                f"{label}. This week: {week['reading_moments']} reading moments, "
                f"{week['pages']} pages, {week['minutes']} minutes."
            ),
            "data": {"summary": summary, "selected_child": child},
        }


def build_default_tools() -> LibraryTools:
    return LibraryTools(SQLiteLibraryProvider())

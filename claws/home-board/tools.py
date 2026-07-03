from __future__ import annotations

from datetime import date as Date, datetime
from typing import Any, Literal, Protocol, TypedDict

from intent import extract_intent
from provider import SQLiteHomeBoardProvider


VALID_CONTEXTS = {
    "before_leave",
    "at_home",
    "school",
    "kitchen",
    "airport",
    "general",
}
VALID_PRIORITIES = {"low", "medium", "high"}


class HomeBoardProvider(Protocol):
    def add_item(
        self,
        *,
        person_or_group: str,
        message: str,
        date: str,
        context: str,
        trigger: str | None,
        priority: str,
        expires_at: str,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        ...

    def list_items(
        self,
        *,
        date: str | None = None,
        status: str | None = "pending",
        include_expired: bool = False,
        now: str | None = None,
    ) -> list[dict[str, Any]]:
        ...

    def mark_done(
        self,
        item_id: str,
        *,
        done_at: str | None = None,
    ) -> dict[str, Any] | None:
        ...


class ToolResponse(TypedDict, total=False):
    status: Literal["ok", "needs_information", "error"]
    message: str
    data: dict[str, Any]


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split()).strip()
    return cleaned or None


def _missing_response(fields: list[str]) -> ToolResponse:
    return {
        "status": "needs_information",
        "message": "Missing required Home Board information: " + ", ".join(fields) + ".",
        "data": {"missing_fields": fields},
    }


def _error_response(error: Exception) -> ToolResponse:
    return {
        "status": "error",
        "message": f"Home Board storage failed: {error}",
        "data": {"error_type": error.__class__.__name__},
    }


def _normalize_date(value: str | Date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, Date):
        return value.isoformat()
    cleaned = str(value).strip()
    if not cleaned:
        return None
    try:
        return Date.fromisoformat(cleaned[:10]).isoformat()
    except ValueError:
        return None


def _default_expires_at(item_date: str) -> str:
    return extract_intent(f"Family notice {item_date}")["expires_at"]


class HomeBoardTools:
    """Tool layer for short-lived N4OS household notices."""

    def __init__(self, provider: HomeBoardProvider):
        self.provider = provider

    def add_item(
        self,
        person_or_group: str | None = None,
        message: str | None = None,
        date: str | Date | datetime | None = None,
        context: str = "general",
        trigger: str | None = None,
        priority: str = "medium",
        expires_at: str | None = None,
    ) -> ToolResponse:
        cleaned_person = _clean_optional(person_or_group)
        cleaned_message = _clean_optional(message)
        normalized_date = _normalize_date(date)
        missing = []
        if cleaned_person is None:
            missing.append("person_or_group")
        if cleaned_message is None:
            missing.append("message")
        if normalized_date is None:
            missing.append("date")
        if missing:
            return _missing_response(missing)

        normalized_context = context if context in VALID_CONTEXTS else "general"
        normalized_priority = priority if priority in VALID_PRIORITIES else "medium"
        try:
            item = self.provider.add_item(
                person_or_group=cleaned_person,
                message=cleaned_message,
                date=normalized_date,
                context=normalized_context,
                trigger=_clean_optional(trigger),
                priority=normalized_priority,
                expires_at=expires_at or _default_expires_at(normalized_date),
            )
        except Exception as error:
            return _error_response(error)

        return {
            "status": "ok",
            "message": "Home Board item added.",
            "data": {"item": item},
        }

    def list_items(
        self,
        date: str | Date | datetime | None = None,
        status: str | None = "pending",
        include_expired: bool = False,
        now: datetime | None = None,
    ) -> ToolResponse:
        normalized_date = _normalize_date(date)
        normalized_status = status if status in ("pending", "done", None) else "pending"
        try:
            items = self.provider.list_items(
                date=normalized_date,
                status=normalized_status,
                include_expired=include_expired,
                now=now.isoformat() if now is not None else None,
            )
        except Exception as error:
            return _error_response(error)

        return {
            "status": "ok",
            "message": "Home Board items returned.",
            "data": {"items": items},
        }

    def mark_done(self, item_id: str | None = None) -> ToolResponse:
        cleaned_item_id = _clean_optional(item_id)
        if cleaned_item_id is None:
            return _missing_response(["item_id"])

        try:
            item = self.provider.mark_done(cleaned_item_id)
        except Exception as error:
            return _error_response(error)
        if item is None:
            return {
                "status": "error",
                "message": f"Home Board item {cleaned_item_id} was not found.",
                "data": {"item_id": cleaned_item_id},
            }

        return {
            "status": "ok",
            "message": "Home Board item marked done.",
            "data": {"item": item},
        }


def build_default_tools() -> HomeBoardTools:
    return HomeBoardTools(SQLiteHomeBoardProvider())

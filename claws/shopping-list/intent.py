from __future__ import annotations

import re
from typing import Any

from constants import EXPLICIT_SHOPPING_PREFIXES, LIST_ALIASES, SHOPPING_LISTS


LIST_PATTERN = "|".join(
    re.escape(alias)
    for alias in sorted(LIST_ALIASES, key=len, reverse=True)
)
ITEM_SEPARATORS_RE = re.compile(r"\s*(?:,|\band\b)\s*", re.IGNORECASE)
EXPLICIT_PREFIX_RE = re.compile(
    r"^\s*(?P<prefix>/cart|/shop|/shopping)(?:@[A-Za-z0-9_]+)?(?:\s+|:\s*)?(?P<body>.*)$",
    re.IGNORECASE,
)


def normalize_list_slug(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).lower().replace("-", " ").split()).strip()
    return LIST_ALIASES.get(cleaned)


def list_name(slug: str | None) -> str:
    if slug is None:
        return "shopping"
    return SHOPPING_LISTS.get(slug, slug)


def strip_explicit_prefix(request: str) -> tuple[str, bool]:
    match = EXPLICIT_PREFIX_RE.match(request)
    if match is None:
        return request.strip(), False
    return match.group("body").strip(), True


def is_explicit_shopping_request(request: str) -> bool:
    lowered = request.strip().lower()
    return any(
        lowered == prefix or lowered.startswith(prefix + " ") or lowered.startswith(prefix + ":")
        for prefix in EXPLICIT_SHOPPING_PREFIXES
    )


def _clean_item(value: str) -> str:
    cleaned = " ".join(value.split()).strip(" ,.;:-")
    cleaned = re.sub(
        r"^(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)"
        r"\s*[,.:-]\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^(?:add\s+another\s+item\s+to\s+it|add\s+another\s+item|another\s+item)\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^(?:need\s+to\s+find|need\s+to\s+buy|need\s+to\s+get|find|get|buy|add)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^(?:some|the|a|an)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*(?:do|shopping|list)\s*$", "", cleaned, flags=re.IGNORECASE)
    return cleaned


def _split_items(value: str) -> list[str]:
    value = re.sub(
        r"\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)"
        r"\s*[,.:-]\s*",
        ", ",
        value,
        flags=re.IGNORECASE,
    )
    return [
        item
        for item in (_clean_item(part) for part in ITEM_SEPARATORS_RE.split(value))
        if item
    ][:50]


def _extract_list(text: str) -> tuple[str | None, str]:
    match = re.search(rf"\b(?P<list>{LIST_PATTERN})\s+list\b", text, flags=re.IGNORECASE)
    if match is None:
        match = re.search(
            rf"\b(?:to|in|on|from)\s+(?P<list>{LIST_PATTERN})\b",
            text,
            flags=re.IGNORECASE,
        )
    if match is None:
        match = re.search(rf"\b(?P<list>{LIST_PATTERN})\b", text, flags=re.IGNORECASE)
    if match is None:
        return None, text

    slug = normalize_list_slug(match.group("list"))
    remainder = (text[: match.start()] + " " + text[match.end() :]).strip()
    remainder = re.sub(r"\bshopping\s+list\b|\blist\b", "", remainder, flags=re.IGNORECASE)
    return slug, " ".join(remainder.split()).strip()


def _list_request(text: str, explicit: bool, list_slug: str | None) -> bool:
    lowered = text.lower().strip()
    if re.search(
        r"^\s*(?:add|buy|need|get|put|cross\s+off|check\s+off|mark\s+(?:off|done)|got|bought|done|clear|uncheck|restore|delete|remove|move|transfer)\b",
        lowered,
    ):
        return False
    if explicit and (not lowered or list_slug is not None or lowered in {"all", "lists", "list"}):
        return True
    return bool(
        re.search(r"^\s*(?:show|list|view|what(?:'s| is)|whats)\b", lowered)
        and (
            "shopping" in lowered
            or "cart" in lowered
            or "list" in lowered
            or list_slug is not None
        )
    )


def _remove_action_prefix(text: str, pattern: str) -> str:
    return re.sub(pattern, "", text, count=1, flags=re.IGNORECASE).strip(" ,.;:-")


def extract_intent(request: str, now: Any | None = None) -> dict[str, Any]:
    del now
    body, explicit = strip_explicit_prefix(request)
    text = body or request
    list_slug, remainder = _extract_list(text)
    lowered = text.lower().strip()

    if (
        explicit
        and list_slug is not None
        and remainder
        and not re.search(
            r"^\s*(?:move|transfer|cross\s+off|check\s+off|mark\s+(?:off|done)|got|bought|done|clear|uncheck|restore|delete|remove)\b",
            remainder,
            flags=re.IGNORECASE,
        )
    ):
        add_candidate = _remove_action_prefix(
            remainder,
            r"^\s*(?:add\s+to\s+cart|add|buy|need|get|put|cart)\b(?:\s+(?:some|the|a|an))?",
        )
        items = _split_items(add_candidate)
        if items:
            return {
                "intent": "add_items" if len(items) > 1 else "add_item",
                "list_slug": list_slug,
                "items": items,
                "item": items[0],
                "missing_fields": [],
            }

    if _list_request(text, explicit, list_slug):
        if list_slug is None and re.search(r"\ball\b|\blists?\b", lowered):
            return {"intent": "list_lists", "missing_fields": []}
        return {
            "intent": "list_items",
            "list_slug": list_slug,
            "missing_fields": [] if list_slug is not None else ["list_name"],
        }

    move_match = re.search(
        rf"^\s*(?:move|transfer)\s+(?P<item>.+?)\s+from\s+(?P<source>{LIST_PATTERN})\s+to\s+(?P<target>{LIST_PATTERN})\s*$",
        text,
        flags=re.IGNORECASE,
    )
    if move_match is not None:
        return {
            "intent": "move_item",
            "item": _clean_item(move_match.group("item")),
            "list_slug": normalize_list_slug(move_match.group("source")),
            "target_list_slug": normalize_list_slug(move_match.group("target")),
            "missing_fields": [],
        }

    clear_text = " ".join((remainder or text).lower().split())
    if list_slug is not None and re.search(r"^\s*(?:clear|done)\b", clear_text):
        return {
            "intent": "clear_list",
            "list_slug": list_slug,
            "missing_fields": [],
        }

    if re.search(r"^\s*(?:cross\s+off|check\s+off|mark\s+(?:off|done)|got|bought|done)\b", lowered):
        item_text = _remove_action_prefix(
            remainder or text,
            r"^\s*(?:cross\s+off|check\s+off|mark\s+(?:off|done)|got|bought|done)\b",
        )
        return {
            "intent": "check_item",
            "list_slug": list_slug,
            "item": _clean_item(item_text),
            "missing_fields": [
                field
                for field, value in (("list_name", list_slug), ("item", _clean_item(item_text)))
                if not value
            ],
        }

    if re.search(r"^\s*(?:uncheck|restore|put\s+back)\b", lowered):
        item_text = _remove_action_prefix(remainder or text, r"^\s*(?:uncheck|restore|put\s+back)\b")
        return {
            "intent": "uncheck_item",
            "list_slug": list_slug,
            "item": _clean_item(item_text),
            "missing_fields": [
                field
                for field, value in (("list_name", list_slug), ("item", _clean_item(item_text)))
                if not value
            ],
        }

    if re.search(r"^\s*(?:delete|remove)\b", lowered):
        item_text = _remove_action_prefix(remainder or text, r"^\s*(?:delete|remove)\b")
        return {
            "intent": "delete_item",
            "list_slug": list_slug,
            "item": _clean_item(item_text),
            "missing_fields": [
                field
                for field, value in (("list_name", list_slug), ("item", _clean_item(item_text)))
                if not value
            ],
        }

    add_candidate = remainder
    add_candidate = _remove_action_prefix(
        add_candidate,
        r"^\s*(?:add\s+to\s+cart|add|buy|need|get|put|cart)\b(?:\s+(?:some|the|a|an))?",
    )
    if explicit or list_slug is not None or re.search(r"^\s*(?:add|buy|need|get|put)\b", lowered):
        items = _split_items(add_candidate)
        return {
            "intent": "add_items" if len(items) > 1 else "add_item",
            "list_slug": list_slug,
            "items": items,
            "item": items[0] if items else "",
            "missing_fields": [
                field
                for field, value in (("list_name", list_slug), ("item", items))
                if not value
            ],
        }

    return {
        "intent": "unknown",
        "list_slug": list_slug,
        "missing_fields": ["shopping_request"],
    }

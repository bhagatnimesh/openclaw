from __future__ import annotations

from datetime import date as Date, datetime
from typing import Any, Literal, Protocol, TypedDict

from intent import VALID_OWNERS, VALID_SIZES, VALID_STATUSES, VALID_URGENCIES
from provider import SQLiteFamilyDecisionProvider


class FamilyDecisionProvider(Protocol):
    def create_decision(self, **fields: Any) -> dict[str, Any]:
        ...

    def list_decisions(self, *, status: str | None = None, include_decided: bool = False) -> list[dict[str, Any]]:
        ...

    def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        ...

    def update_decision(self, decision_id: str, **fields: Any) -> dict[str, Any] | None:
        ...

    def add_option(self, decision_id: str, text: str, pros: str | None = None, cons: str | None = None) -> dict[str, Any] | None:
        ...

    def add_evidence(self, decision_id: str, text: str, source: str | None = None) -> dict[str, Any] | None:
        ...

    def add_next_step(self, decision_id: str, text: str, owner: str = "unknown", due: str | None = None) -> dict[str, Any] | None:
        ...

    def decide(self, decision_id: str, *, outcome: str, rationale: str | None = None) -> dict[str, Any] | None:
        ...


class ToolResponse(TypedDict, total=False):
    status: Literal["ok", "needs_information", "error"]
    message: str
    data: dict[str, Any]


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split()).strip()
    return cleaned or None


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


def _missing_response(fields: list[str]) -> ToolResponse:
    return {
        "status": "needs_information",
        "message": "Missing required decision information: " + ", ".join(fields) + ".",
        "data": {"missing_fields": fields},
    }


def _error_response(error: Exception) -> ToolResponse:
    return {
        "status": "error",
        "message": f"Family Decisions storage failed: {error}",
        "data": {"error_type": error.__class__.__name__},
    }


def _normalize_status(value: Any, fallback: str = "inbox") -> str:
    cleaned = str(value or "").strip().lower()
    return cleaned if cleaned in VALID_STATUSES else fallback


def _normalize_owner(value: Any) -> str:
    cleaned = str(value or "").strip().lower()
    return cleaned if cleaned in VALID_OWNERS else "unknown"


def _normalize_urgency(value: Any) -> str:
    cleaned = str(value or "").strip().lower()
    return cleaned if cleaned in VALID_URGENCIES else "normal"


def _normalize_size(value: Any) -> str:
    cleaned = str(value or "").strip().lower()
    return cleaned if cleaned in VALID_SIZES else "small"


def _decision_id_label(decision: dict[str, Any]) -> str:
    return str(decision.get("id") or "")[:8]


def decision_gaps(decision: dict[str, Any]) -> list[str]:
    gaps = []
    if decision.get("owner") == "unknown":
        gaps.append("owner")
    if decision.get("status") not in {"decided", "parked"} and not decision.get("due"):
        gaps.append("timeline")
    if not decision.get("options"):
        gaps.append("options")
    open_steps = [
        step for step in decision.get("next_steps", [])
        if step.get("status") == "open"
    ]
    if decision.get("status") not in {"decided", "parked"} and not open_steps:
        gaps.append("next_step")
    return gaps


def build_decision_brief(decision: dict[str, Any]) -> str:
    lines = [
        f"Decision brief: {decision.get('title') or 'Untitled decision'}",
        f"Status: {decision.get('status')} | owner: {decision.get('owner')} | due: {decision.get('due') or 'not set'}",
    ]
    if decision.get("context"):
        lines.append(f"Context: {decision['context']}")
    options = decision.get("options", [])
    lines.append("Options:")
    lines.extend(f"- {option.get('text')}" for option in options) if options else lines.append("- None yet")
    evidence = decision.get("evidence", [])
    lines.append("Evidence:")
    lines.extend(f"- {item.get('text')}" for item in evidence[:5]) if evidence else lines.append("- None yet")
    next_steps = [
        step for step in decision.get("next_steps", [])
        if step.get("status") == "open"
    ]
    lines.append("Next steps:")
    if next_steps:
        lines.extend(
            f"- {step.get('text')} ({step.get('owner')}, due {step.get('due') or 'not set'})"
            for step in next_steps
        )
    else:
        lines.append("- Assign one clear next step")
    gaps = decision_gaps(decision)
    lines.append("AI assist:")
    if gaps:
        lines.append("- Missing: " + ", ".join(gaps))
    if decision.get("status") == "researching" or "options" in gaps:
        lines.append("- Generate options or research evidence before asking the family to decide")
    elif not gaps:
        lines.append("- Ready for a family decision conversation")
    if decision.get("outcome"):
        lines.append(f"Outcome: {decision['outcome']}")
    return "\n".join(lines)


class FamilyDecisionTools:
    """Tool layer for N4OS family decision tracking."""

    def __init__(self, provider: FamilyDecisionProvider):
        self.provider = provider

    def create_decision(
        self,
        title: str | None = None,
        *,
        context: str | None = None,
        status: str = "inbox",
        owner: str = "unknown",
        urgency: str = "normal",
        size: str = "small",
        due: str | Date | datetime | None = None,
    ) -> ToolResponse:
        cleaned_title = _clean_optional(title)
        if cleaned_title is None:
            return _missing_response(["title"])
        try:
            decision = self.provider.create_decision(
                title=cleaned_title,
                context=_clean_optional(context),
                status=_normalize_status(status),
                owner=_normalize_owner(owner),
                urgency=_normalize_urgency(urgency),
                size=_normalize_size(size),
                due=_normalize_date(due),
            )
        except Exception as error:
            return _error_response(error)
        return {
            "status": "ok",
            "message": "Family decision captured.",
            "data": {"decision": decision, "gaps": decision_gaps(decision)},
        }

    def list_decisions(self, status: str | None = None, include_decided: bool = False) -> ToolResponse:
        normalized_status = _normalize_status(status) if status else None
        try:
            decisions = self.provider.list_decisions(
                status=normalized_status,
                include_decided=include_decided,
            )
        except Exception as error:
            return _error_response(error)
        return {"status": "ok", "message": "Family decisions returned.", "data": {"decisions": decisions}}

    def latest_open_decision(self) -> ToolResponse:
        response = self.list_decisions()
        if response["status"] != "ok":
            return response
        decisions = response.get("data", {}).get("decisions", [])
        if not decisions:
            return {
                "status": "needs_information",
                "message": "No open family decisions found.",
                "data": {"missing_fields": ["decision"]},
            }
        decision_id = decisions[0]["id"]
        return self.read_decision(decision_id)

    def read_decision(self, decision_id: str | None = None) -> ToolResponse:
        cleaned_id = _clean_optional(decision_id)
        if cleaned_id is None:
            return self.latest_open_decision()
        try:
            decision = self.provider.get_decision(cleaned_id)
        except Exception as error:
            return _error_response(error)
        if decision is None:
            return {"status": "error", "message": "Decision not found.", "data": {"decision_id": cleaned_id}}
        return {"status": "ok", "message": "Family decision returned.", "data": {"decision": decision, "gaps": decision_gaps(decision)}}

    def add_option(self, decision_id: str | None, text: str | None, pros: str | None = None, cons: str | None = None) -> ToolResponse:
        cleaned_id = _clean_optional(decision_id)
        cleaned_text = _clean_optional(text)
        if cleaned_id is None:
            latest = self.latest_open_decision()
            if latest["status"] != "ok":
                return latest
            cleaned_id = latest.get("data", {}).get("decision", {}).get("id")
        missing = [name for name, value in (("decision_id", cleaned_id), ("text", cleaned_text)) if value is None]
        if missing:
            return _missing_response(missing)
        try:
            decision = self.provider.add_option(cleaned_id, cleaned_text, _clean_optional(pros), _clean_optional(cons))
        except Exception as error:
            return _error_response(error)
        if decision is None:
            return {"status": "error", "message": "Decision not found.", "data": {"decision_id": cleaned_id}}
        return {"status": "ok", "message": "Decision option added.", "data": {"decision": decision}}

    def add_evidence(self, decision_id: str | None, text: str | None, source: str | None = None) -> ToolResponse:
        cleaned_id = _clean_optional(decision_id)
        cleaned_text = _clean_optional(text)
        if cleaned_id is None:
            latest = self.latest_open_decision()
            if latest["status"] != "ok":
                return latest
            cleaned_id = latest.get("data", {}).get("decision", {}).get("id")
        missing = [name for name, value in (("decision_id", cleaned_id), ("text", cleaned_text)) if value is None]
        if missing:
            return _missing_response(missing)
        try:
            decision = self.provider.add_evidence(cleaned_id, cleaned_text, _clean_optional(source))
        except Exception as error:
            return _error_response(error)
        if decision is None:
            return {"status": "error", "message": "Decision not found.", "data": {"decision_id": cleaned_id}}
        return {"status": "ok", "message": "Decision evidence added.", "data": {"decision": decision}}

    def add_next_step(self, decision_id: str | None, text: str | None, owner: str = "unknown", due: str | Date | datetime | None = None) -> ToolResponse:
        cleaned_id = _clean_optional(decision_id)
        cleaned_text = _clean_optional(text)
        if cleaned_id is None:
            latest = self.latest_open_decision()
            if latest["status"] != "ok":
                return latest
            cleaned_id = latest.get("data", {}).get("decision", {}).get("id")
        missing = [name for name, value in (("decision_id", cleaned_id), ("text", cleaned_text)) if value is None]
        if missing:
            return _missing_response(missing)
        try:
            decision = self.provider.add_next_step(
                cleaned_id,
                cleaned_text,
                _normalize_owner(owner),
                _normalize_date(due),
            )
        except Exception as error:
            return _error_response(error)
        if decision is None:
            return {"status": "error", "message": "Decision not found.", "data": {"decision_id": cleaned_id}}
        return {"status": "ok", "message": "Decision next step added.", "data": {"decision": decision}}

    def decide(self, decision_id: str | None, outcome: str | None, rationale: str | None = None) -> ToolResponse:
        cleaned_id = _clean_optional(decision_id)
        cleaned_outcome = _clean_optional(outcome)
        if cleaned_id is None:
            latest = self.latest_open_decision()
            if latest["status"] != "ok":
                return latest
            cleaned_id = latest.get("data", {}).get("decision", {}).get("id")
        missing = [name for name, value in (("decision_id", cleaned_id), ("outcome", cleaned_outcome)) if value is None]
        if missing:
            return _missing_response(missing)
        try:
            decision = self.provider.decide(cleaned_id, outcome=cleaned_outcome, rationale=_clean_optional(rationale))
        except Exception as error:
            return _error_response(error)
        if decision is None:
            return {"status": "error", "message": "Decision not found.", "data": {"decision_id": cleaned_id}}
        return {"status": "ok", "message": "Family decision recorded.", "data": {"decision": decision}}

    def decision_brief(self, decision_id: str | None) -> ToolResponse:
        response = self.read_decision(decision_id)
        if response["status"] != "ok":
            return response
        decision = response.get("data", {}).get("decision", {})
        brief = build_decision_brief(decision)
        return {
            "status": "ok",
            "message": brief,
            "data": {"decision": decision, "brief": brief, "decision_id": _decision_id_label(decision)},
        }


def build_default_tools() -> FamilyDecisionTools:
    return FamilyDecisionTools(SQLiteFamilyDecisionProvider())

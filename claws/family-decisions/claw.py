from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import sys
from typing import Any

from intent import extract_intent
from tools import (
    FamilyDecisionProvider,
    FamilyDecisionTools,
    build_decision_brief,
    build_default_tools,
    decision_gaps,
)


def _looks_like_accidental_command_capture(decision: dict[str, Any]) -> bool:
    title = str(decision.get("title") or "").strip().lower()
    return title.startswith(
        (
            "tell me ",
            "give me ",
            "show ",
            "list ",
            "what are ",
            "what is ",
        ),
    ) and "decision" in title


def _format_decision_line(index: int, decision: dict[str, Any]) -> str:
    due = decision.get("due") or "not set"
    owner = decision.get("owner") or "unknown"
    owner_label = "unassigned" if owner == "unknown" else owner
    gaps = decision_gaps(decision)
    next_steps = [
        step for step in decision.get("next_steps", [])
        if step.get("status") == "open"
    ]
    next_step = next_steps[0].get("text") if next_steps else "Assign one clear next step"
    lines = [
        f"{index}. {decision.get('title')}",
        f"   Owner: {owner_label} | Due: {due} | Status: {decision.get('status')}",
    ]
    if gaps:
        lines.append("   Missing: " + ", ".join(gaps))
    lines.append(f"   Next: {next_step}")
    if _looks_like_accidental_command_capture(decision):
        lines.append("   Note: this looks like an accidental command capture.")
    lines.append(f"   Ref: {decision.get('id', '')[:8]}")
    return "\n".join(lines)


def _format_created_decision(decision: dict[str, Any], gaps: list[str]) -> str:
    lines = [f"Captured decision: {decision.get('title')} ({decision.get('id', '')[:8]})."]
    lines.append(
        f"Owner: {decision.get('owner')}; due: {decision.get('due') or 'not set'}; status: {decision.get('status')}."
    )
    if gaps:
        lines.append("AI assist: missing " + ", ".join(gaps) + ".")
    else:
        lines.append("AI assist: ready to build a decision brief.")
    return "\n".join(lines)


def _detail_count_line(options: list[str], evidence: list[str]) -> str | None:
    parts = []
    if options:
        parts.append(f"{len(options)} option{'s' if len(options) != 1 else ''}")
    if evidence:
        parts.append(f"{len(evidence)} evidence note{'s' if len(evidence) != 1 else ''}")
    if not parts:
        return None
    return "Captured details: " + ", ".join(parts) + "."


def _bulk_close_message() -> str:
    return (
        "I can close one decision at a time. Use the displayed number or ref, "
        "for example: close decision 2 done."
    )


def _actor_from_source(source: str, default_owner: str | None) -> str:
    if ":" in source:
        actor = source.rsplit(":", 1)[-1].strip()
        if actor:
            return actor
    return default_owner or "family"


def _format_backlog_line(index: int, item: dict[str, Any]) -> str:
    target_date = item.get("review_on") or item.get("due") or "no date"
    owner = item.get("owner") or "unknown"
    pin = " | pinned" if item.get("pinned") else ""
    return (
        f"{index}. [{str(item.get('kind') or 'discussion').title()}] {item.get('title')}\n"
        f"   Owner: {owner} | Date: {target_date} | Status: {item.get('status')}{pin}\n"
        f"   Ref: {str(item.get('id') or '')[:8]}"
    )


@dataclass
class FamilyDecisionsClaw:
    """Entry point for N4OS family decision tracking."""

    tools: FamilyDecisionTools
    undo_stack: list[dict[str, Any]] = field(default_factory=list)
    pending_action: dict[str, Any] | None = None

    @classmethod
    def from_provider(cls, provider: FamilyDecisionProvider) -> "FamilyDecisionsClaw":
        return cls(tools=FamilyDecisionTools(provider))

    @classmethod
    def default(cls) -> "FamilyDecisionsClaw":
        return cls(tools=build_default_tools())

    def tool_map(self) -> dict[str, Any]:
        return {
            "create_family_backlog_item": self.tools.create_backlog_item,
            "list_family_backlog": self.tools.list_backlog_items,
            "update_family_backlog_item": self.tools.update_backlog_item,
            "add_family_backlog_note": self.tools.add_backlog_note,
            "set_family_backlog_position": self.tools.set_backlog_position,
            "move_family_backlog_item": self.tools.move_backlog_item,
            "close_family_backlog_item": self.tools.close_backlog_item,
            "create_family_decision": self.tools.create_decision,
            "list_family_decisions": self.tools.list_decisions,
            "read_family_decision": self.tools.read_decision,
            "add_family_decision_option": self.tools.add_option,
            "add_family_decision_evidence": self.tools.add_evidence,
            "add_family_decision_next_step": self.tools.add_next_step,
            "record_family_decision": self.tools.decide,
            "family_decision_brief": self.tools.decision_brief,
            "delete_family_decision": self.tools.delete_decision,
            "undo_family_decision_action": self.undo_last_action,
        }

    def handle_request(
        self,
        request: str,
        reference_time: datetime | None = None,
        source: str = "telegram_text",
        default_owner: str | None = None,
    ) -> str:
        intent = extract_intent(request, now=reference_time)
        action = intent.get("intent")
        actor = _actor_from_source(source, default_owner)
        if action == "create_backlog_item":
            return self.capture_backlog_from_request(
                request,
                reference_time=reference_time,
                actor=actor,
                default_owner=default_owner,
            )
        if action == "list_backlog":
            return self.list_backlog_from_request()
        if action == "add_backlog_note":
            return self.add_backlog_note_from_request(request, reference_time=reference_time, actor=actor)
        if action == "set_backlog_position":
            return self.set_backlog_position_from_request(request, reference_time=reference_time, actor=actor)
        if action == "move_backlog_item":
            return self.move_backlog_from_request(request, reference_time=reference_time, actor=actor)
        if action == "pin_backlog_item":
            return self.pin_backlog_from_request(request, reference_time=reference_time, actor=actor)
        if action == "park_backlog_item":
            return self.park_backlog_from_request(request, reference_time=reference_time, actor=actor)
        if action == "close_backlog_item":
            return self.close_backlog_from_request(request, reference_time=reference_time, actor=actor)
        if action == "list_decisions":
            return self.list_decisions_from_request(request)
        if action == "decision_brief":
            return self.decision_brief_from_request(request)
        if action == "add_option":
            return self.add_option_from_request(request, reference_time=reference_time)
        if action == "add_evidence":
            return self.add_evidence_from_request(request, reference_time=reference_time)
        if action == "add_next_step":
            return self.add_next_step_from_request(request, reference_time=reference_time)
        if action == "bulk_record_decisions":
            message = _bulk_close_message()
            print(message)
            return message
        if action == "record_decision":
            return self.record_decision_from_request(request, reference_time=reference_time)
        return self.capture_decision_from_request(request, reference_time=reference_time)

    def handle_pending_response(self, request: str) -> bool:
        pending = self.pending_action
        if pending is None:
            return False
        lowered = request.strip().lower()
        if lowered not in {"yes", "y", "confirm", "confirmed", "no", "n", "cancel"}:
            return False
        self.pending_action = None
        if lowered in {"no", "n", "cancel"}:
            print("Backlog move cancelled.")
            return True
        before = self.tools.read_backlog_item(pending.get("item_id"))
        if pending.get("action") == "close":
            response = self.tools.close_backlog_item(
                pending.get("item_id"),
                pending.get("outcome"),
                confirmed=True,
                actor=pending.get("actor") or "family",
            )
        else:
            response = self.tools.move_backlog_item(
                pending.get("item_id"),
                pending.get("kind"),
                confirmed=True,
                actor=pending.get("actor") or "family",
            )
        if before.get("status") == "ok" and response.get("status") == "ok":
            snapshot = before.get("data", {}).get("item")
            if snapshot:
                self.undo_stack.append({"action": "restore_decision", "decision": snapshot})
        print(response["message"])
        return True

    def capture_backlog_from_request(
        self,
        request: str,
        *,
        reference_time: datetime | None = None,
        actor: str = "family",
        default_owner: str | None = None,
    ) -> str:
        intent = extract_intent(request, now=reference_time)
        owner = intent.get("owner") or "unknown"
        if owner == "unknown" and default_owner:
            owner = default_owner
        response = self.tools.create_backlog_item(
            intent.get("title"),
            kind=intent.get("kind", "discussion"),
            context=intent.get("context"),
            owner=owner,
            urgency=intent.get("urgency", "normal"),
            review_on=intent.get("review_on"),
            due=intent.get("due"),
            actor=actor,
        )
        if response["status"] != "ok":
            print(response["message"])
            return response["message"]
        item = response.get("data", {}).get("item", {})
        if item.get("id"):
            self.undo_stack.append({"action": "delete_decision", "decision": dict(item)})
        date_value = item.get("review_on") or item.get("due") or "not set"
        message = (
            f"{response['message']} {item.get('title')} ({str(item.get('id') or '')[:8]}).\n"
            f"Owner: {item.get('owner')}; date: {date_value}."
        )
        if item.get("kind") == "planning" and not item.get("due"):
            message += "\nNeeds date or calendar link before progress can be tracked."
        print(message)
        return message

    def list_backlog_from_request(self) -> str:
        response = self.tools.list_backlog_items()
        if response["status"] != "ok":
            print(response["message"])
            return response["message"]
        items = response.get("data", {}).get("items", [])
        if not items:
            message = "Family backlog is clear."
        else:
            lines = [f"Family backlog ({len(items)} open):"]
            lines.extend(_format_backlog_line(index, item) for index, item in enumerate(items, start=1))
            message = "\n".join(lines)
        print(message)
        return message

    def _resolve_backlog_target(self, intent: dict[str, Any]) -> tuple[str | None, str | None]:
        item_id = intent.get("item_id")
        if item_id:
            response = self.tools.read_backlog_item(item_id)
            if response["status"] == "ok":
                return response.get("data", {}).get("item", {}).get("id"), None
        target = str(intent.get("target") or "").strip().lower()
        response = self.tools.list_backlog_items()
        if response["status"] != "ok":
            return None, response["message"]
        matches = [
            item for item in response.get("data", {}).get("items", [])
            if target and (item.get("title", "").lower() == target or target in item.get("title", "").lower())
        ]
        if len(matches) == 1:
            return matches[0]["id"], None
        if len(matches) > 1:
            return None, "More than one backlog item matches. Use the displayed ref."
        return None, "Backlog item not found. Ask to review the backlog, then use its ref."

    def add_backlog_note_from_request(self, request: str, *, reference_time: datetime | None, actor: str) -> str:
        intent = extract_intent(request, now=reference_time)
        item_id, error = self._resolve_backlog_target(intent)
        response = self.tools.add_backlog_note(item_id, intent.get("text"), actor=actor) if item_id else None
        message = response["message"] if response is not None else str(error)
        print(message)
        return message

    def set_backlog_position_from_request(self, request: str, *, reference_time: datetime | None, actor: str) -> str:
        intent = extract_intent(request, now=reference_time)
        item_id, error = self._resolve_backlog_target(intent)
        response = self.tools.set_backlog_position(item_id, intent.get("value"), actor=actor) if item_id else None
        message = response["message"] if response is not None else str(error)
        print(message)
        return message

    def move_backlog_from_request(self, request: str, *, reference_time: datetime | None, actor: str) -> str:
        intent = extract_intent(request, now=reference_time)
        item_id, error = self._resolve_backlog_target(intent)
        if item_id is None:
            message = str(error)
        else:
            response = self.tools.move_backlog_item(item_id, intent.get("kind"), actor=actor)
            message = response["message"]
            if response["status"] == "needs_confirmation":
                self.pending_action = {
                    "action": "move",
                    "item_id": item_id,
                    "kind": intent.get("kind"),
                    "actor": actor,
                }
        print(message)
        return message

    def pin_backlog_from_request(self, request: str, *, reference_time: datetime | None, actor: str) -> str:
        intent = extract_intent(request, now=reference_time)
        item_id, error = self._resolve_backlog_target(intent)
        response = self.tools.update_backlog_item(item_id, pinned=intent.get("pinned"), actor=actor) if item_id else None
        message = response["message"] if response is not None else str(error)
        print(message)
        return message

    def park_backlog_from_request(self, request: str, *, reference_time: datetime | None, actor: str) -> str:
        intent = extract_intent(request, now=reference_time)
        item_id, error = self._resolve_backlog_target(intent)
        response = self.tools.park_backlog_item(item_id, actor=actor) if item_id else None
        message = response["message"] if response is not None else str(error)
        print(message)
        return message

    def close_backlog_from_request(self, request: str, *, reference_time: datetime | None, actor: str) -> str:
        intent = extract_intent(request, now=reference_time)
        item_id, error = self._resolve_backlog_target(intent)
        response = self.tools.close_backlog_item(item_id, intent.get("outcome"), actor=actor) if item_id else None
        message = response["message"] if response is not None else str(error)
        if response is not None and response["status"] == "needs_confirmation":
            self.pending_action = {
                "action": "close",
                "item_id": item_id,
                "outcome": intent.get("outcome"),
                "actor": actor,
            }
        print(message)
        return message

    def capture_decision_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> str:
        intent = extract_intent(request, now=reference_time)
        response = self.tools.create_decision(
            title=intent.get("title"),
            context=intent.get("context"),
            status=intent.get("status", "inbox"),
            owner=intent.get("owner", "unknown"),
            urgency=intent.get("urgency", "normal"),
            size=intent.get("size", "small"),
            due=intent.get("due"),
        )
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message
        decision = response.get("data", {}).get("decision", {})
        initial_options = intent.get("initial_options") or []
        initial_evidence = intent.get("initial_evidence") or []
        for option in initial_options:
            option_response = self.tools.add_option(decision.get("id"), option)
            if option_response["status"] == "ok":
                decision = option_response.get("data", {}).get("decision", decision)
        for evidence in initial_evidence:
            evidence_response = self.tools.add_evidence(decision.get("id"), evidence)
            if evidence_response["status"] == "ok":
                decision = evidence_response.get("data", {}).get("decision", decision)
        refreshed = self.tools.read_decision(decision.get("id"))
        if refreshed["status"] == "ok":
            decision = refreshed.get("data", {}).get("decision", decision)
            gaps = refreshed.get("data", {}).get("gaps", [])
        else:
            gaps = response.get("data", {}).get("gaps", [])
        if decision.get("id"):
            self.undo_stack.append({"action": "delete_decision", "decision": dict(decision)})
        message = _format_created_decision(decision, gaps)
        detail_line = _detail_count_line(initial_options, initial_evidence)
        if detail_line:
            message += f"\n{detail_line}"
        if intent.get("assistant_help_needed"):
            message += "\nAI assist: add research/evidence or options, then ask for a decision brief."
        print(message)
        return message

    def list_decisions_from_request(self, request: str) -> str:
        response = self.tools.list_decisions()
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message
        decisions = response.get("data", {}).get("decisions", [])
        if not decisions:
            message = "No open family decisions."
            print(message)
            return message
        detailed_decisions = []
        for decision in decisions:
            detail_response = self.tools.read_decision(decision.get("id"))
            if detail_response["status"] == "ok":
                detailed_decisions.append(detail_response.get("data", {}).get("decision", decision))
            else:
                detailed_decisions.append(decision)
        lines = [f"Pending family decisions ({len(detailed_decisions)}):"]
        lines.extend(
            _format_decision_line(index, decision)
            for index, decision in enumerate(detailed_decisions, start=1)
        )
        message = "\n".join(lines)
        print(message)
        return message

    def decision_brief_from_request(self, request: str) -> str:
        decision_id = extract_intent(request).get("decision_id")
        response = self.tools.decision_brief(decision_id)
        message = response["message"]
        decision = response.get("data", {}).get("decision")
        if decision and decision_id is None:
            message = f"Using latest open decision: {decision.get('title')} ({decision.get('id', '')[:8]}).\n{message}"
        print(message)
        return message

    def add_option_from_request(self, request: str, reference_time: datetime | None = None) -> str:
        intent = extract_intent(request, now=reference_time)
        texts = intent.get("texts") or [intent.get("text")]
        before = self._snapshot_for_undo(intent.get("decision_id"))
        response = self._add_many_options(intent.get("decision_id"), texts)
        self._remember_restore_undo(before, response)
        prefix = "Added options." if len([text for text in texts if text]) > 1 else "Added option."
        return self._format_mutation_response(response, prefix)

    def add_evidence_from_request(self, request: str, reference_time: datetime | None = None) -> str:
        intent = extract_intent(request, now=reference_time)
        texts = intent.get("texts") or [intent.get("text")]
        before = self._snapshot_for_undo(intent.get("decision_id"))
        response = self._add_many_evidence(intent.get("decision_id"), texts)
        self._remember_restore_undo(before, response)
        prefix = "Added evidence." if len([text for text in texts if text]) == 1 else "Added evidence notes."
        return self._format_mutation_response(response, prefix)

    def add_next_step_from_request(self, request: str, reference_time: datetime | None = None) -> str:
        intent = extract_intent(request, now=reference_time)
        before = self._snapshot_for_undo(intent.get("decision_id"))
        response = self.tools.add_next_step(
            intent.get("decision_id"),
            intent.get("text"),
            owner=intent.get("owner", "unknown"),
            due=intent.get("due"),
        )
        self._remember_restore_undo(before, response)
        return self._format_mutation_response(response, "Added next step.")

    def record_decision_from_request(self, request: str, reference_time: datetime | None = None) -> str:
        intent = extract_intent(request, now=reference_time)
        if intent.get("intent") == "bulk_record_decisions":
            message = _bulk_close_message()
            print(message)
            return message
        decision_id = intent.get("decision_id")
        if decision_id is None and intent.get("decision_index") is not None:
            response = self._decision_id_from_list_index(intent.get("decision_index"))
            if response["status"] != "ok":
                return self._format_mutation_response(response, "Recorded decision.")
            decision_id = response.get("data", {}).get("decision_id")
        before = self._snapshot_for_undo(decision_id)
        response = self.tools.decide(decision_id, intent.get("outcome"))
        self._remember_restore_undo(before, response)
        return self._format_mutation_response(response, "Recorded decision.")

    def _snapshot_for_undo(self, decision_id: str | None) -> dict[str, Any] | None:
        response = self.tools.read_decision(decision_id)
        if response["status"] != "ok":
            return None
        return dict(response.get("data", {}).get("decision", {}))

    def _remember_restore_undo(
        self,
        before: dict[str, Any] | None,
        response: dict[str, Any],
    ) -> None:
        if before and before.get("id") and response["status"] == "ok":
            self.undo_stack.append({"action": "restore_decision", "decision": before})

    def undo_last_action(self) -> str:
        if not self.undo_stack:
            message = "Nothing to undo for Family Decisions."
            print(message)
            return message

        undo = self.undo_stack.pop()
        decision = undo.get("decision", {})
        if undo.get("action") == "delete_decision":
            response = self.tools.delete_decision(decision.get("id"))
            if response["status"] == "ok":
                message = f"Undid decision capture: removed {decision.get('title') or 'Untitled decision'}."
            else:
                message = response["message"]
            print(message)
            return message

        if undo.get("action") == "restore_decision":
            response = self.tools.restore_decision(decision)
            if response["status"] == "ok":
                restored = response.get("data", {}).get("decision", decision)
                message = f"Undid decision update: restored {restored.get('title') or 'Untitled decision'}."
            else:
                message = response["message"]
            print(message)
            return message

        message = "I do not know how to undo that Family Decisions action."
        print(message)
        return message

    def _format_mutation_response(self, response: dict[str, Any], prefix: str) -> str:
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message
        decision = response.get("data", {}).get("decision", {})
        message = f"{prefix}\n{build_decision_brief(decision)}"
        print(message)
        return message

    def _add_many_options(self, decision_id: str | None, texts: list[str]) -> dict[str, Any]:
        response: dict[str, Any] | None = None
        for text in [value for value in texts if value]:
            response = self.tools.add_option(decision_id, text)
            if response["status"] != "ok":
                return response
            decision_id = response.get("data", {}).get("decision", {}).get("id")
        return response or self.tools.add_option(decision_id, None)

    def _add_many_evidence(self, decision_id: str | None, texts: list[str]) -> dict[str, Any]:
        response: dict[str, Any] | None = None
        for text in [value for value in texts if value]:
            response = self.tools.add_evidence(decision_id, text)
            if response["status"] != "ok":
                return response
            decision_id = response.get("data", {}).get("decision", {}).get("id")
        return response or self.tools.add_evidence(decision_id, None)

    def _decision_id_from_list_index(self, decision_index: int) -> dict[str, Any]:
        response = self.tools.list_decisions()
        if response["status"] != "ok":
            return response
        decisions = response.get("data", {}).get("decisions", [])
        if decision_index < 1 or decision_index > len(decisions):
            return {
                "status": "needs_information",
                "message": (
                    f"Decision {decision_index} is not in the pending list. "
                    "Ask for pending decisions, then use the displayed number or ref."
                ),
                "data": {"missing_fields": ["decision"]},
            }
        return {
            "status": "ok",
            "message": "Decision selected.",
            "data": {"decision_id": decisions[decision_index - 1].get("id")},
        }


def run_cli(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    claw = FamilyDecisionsClaw.default()
    request = " ".join(args).strip()
    if request:
        claw.handle_request(request)
        return

    print("Family Decisions. Type a decision request, or 'exit' to quit.")
    while True:
        try:
            command = input("> ").strip()
        except EOFError:
            print()
            return
        if command.lower() in ("exit", "quit"):
            return
        if command:
            claw.handle_request(command)


if __name__ == "__main__":
    run_cli()

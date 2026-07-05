from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import sys
from typing import Any

from intent import extract_intent
from tools import FamilyDecisionProvider, FamilyDecisionTools, build_decision_brief, build_default_tools


def _format_decision_line(decision: dict[str, Any]) -> str:
    due = decision.get("due") or "no due date"
    owner = decision.get("owner") or "unknown"
    return (
        f"{decision.get('id', '')[:8]} {decision.get('status')} "
        f"{decision.get('urgency')} owner={owner} due={due}: {decision.get('title')}"
    )


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


@dataclass
class FamilyDecisionsClaw:
    """Entry point for N4OS family decision tracking."""

    tools: FamilyDecisionTools

    @classmethod
    def from_provider(cls, provider: FamilyDecisionProvider) -> "FamilyDecisionsClaw":
        return cls(tools=FamilyDecisionTools(provider))

    @classmethod
    def default(cls) -> "FamilyDecisionsClaw":
        return cls(tools=build_default_tools())

    def tool_map(self) -> dict[str, Any]:
        return {
            "create_family_decision": self.tools.create_decision,
            "list_family_decisions": self.tools.list_decisions,
            "read_family_decision": self.tools.read_decision,
            "add_family_decision_option": self.tools.add_option,
            "add_family_decision_evidence": self.tools.add_evidence,
            "add_family_decision_next_step": self.tools.add_next_step,
            "record_family_decision": self.tools.decide,
            "family_decision_brief": self.tools.decision_brief,
        }

    def handle_request(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> str:
        intent = extract_intent(request, now=reference_time)
        action = intent.get("intent")
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
        if action == "record_decision":
            return self.record_decision_from_request(request, reference_time=reference_time)
        return self.capture_decision_from_request(request, reference_time=reference_time)

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
        lines = ["Open family decisions:"]
        lines.extend(f"- {_format_decision_line(decision)}" for decision in decisions)
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
        response = self._add_many_options(intent.get("decision_id"), texts)
        prefix = "Added options." if len([text for text in texts if text]) > 1 else "Added option."
        return self._format_mutation_response(response, prefix)

    def add_evidence_from_request(self, request: str, reference_time: datetime | None = None) -> str:
        intent = extract_intent(request, now=reference_time)
        texts = intent.get("texts") or [intent.get("text")]
        response = self._add_many_evidence(intent.get("decision_id"), texts)
        prefix = "Added evidence." if len([text for text in texts if text]) == 1 else "Added evidence notes."
        return self._format_mutation_response(response, prefix)

    def add_next_step_from_request(self, request: str, reference_time: datetime | None = None) -> str:
        intent = extract_intent(request, now=reference_time)
        response = self.tools.add_next_step(
            intent.get("decision_id"),
            intent.get("text"),
            owner=intent.get("owner", "unknown"),
            due=intent.get("due"),
        )
        return self._format_mutation_response(response, "Added next step.")

    def record_decision_from_request(self, request: str, reference_time: datetime | None = None) -> str:
        intent = extract_intent(request, now=reference_time)
        response = self.tools.decide(intent.get("decision_id"), intent.get("outcome"))
        return self._format_mutation_response(response, "Recorded decision.")

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

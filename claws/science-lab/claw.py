from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any

from tools import ScienceLabProvider, ScienceLabTools, build_default_tools


def _requested_count(request: str) -> int:
    match = re.search(r"\b(?:next|plan)\s+(\d{1,2})\b", request.lower())
    if not match:
        return 4
    return max(1, min(int(match.group(1)), 20))


def _format_experiment(index: int, experiment: dict[str, Any]) -> str:
    title = experiment.get("title") or "Untitled experiment"
    concepts = experiment.get("concepts") or []
    concept_suffix = f" ({', '.join(concepts)})" if concepts else ""
    return f"{index}. {title}{concept_suffix}"


def _format_material_plan(material_plan: dict[str, list[str]]) -> list[str]:
    lines = ["Materials:"]
    for category in (
        "Already in Home Inventory",
        "Please Confirm",
        "Recommended Amazon Order",
    ):
        values = material_plan.get(category) or []
        label = ", ".join(values) if values else "none"
        lines.append(f"- {category}: {label}")
    return lines


@dataclass
class ScienceLabClaw:
    """Entry point for N4OS Science Lab planning from chat channels."""

    tools: ScienceLabTools
    last_result: dict[str, Any] | None = None

    @classmethod
    def from_provider(cls, provider: ScienceLabProvider) -> "ScienceLabClaw":
        return cls(tools=ScienceLabTools(provider))

    @classmethod
    def default(cls) -> "ScienceLabClaw":
        return cls(tools=build_default_tools())

    def tool_map(self) -> dict[str, Any]:
        return {
            "plan_science_lab_experiments": self.tools.plan_next,
        }

    def plan_from_request(
        self,
        request: str,
        reference_time: datetime | None = None,
    ) -> str:
        del reference_time
        response = self.tools.plan_next(count=_requested_count(request))
        self.last_result = response
        if response["status"] != "ok":
            message = response["message"]
            print(message)
            return message

        data = response.get("data", {})
        experiments = data.get("experiments", [])
        lines = ["Science Lab plan:"]
        lines.extend(
            _format_experiment(index, experiment)
            for index, experiment in enumerate(experiments, start=1)
        )
        lines.extend(_format_material_plan(data.get("material_plan", {})))
        lines.append("Next: ask for a parent guide when you pick the first experiment.")
        message = "\n".join(lines)
        print(message)
        return message

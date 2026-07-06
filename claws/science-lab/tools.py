from __future__ import annotations

import re
from typing import Any, Literal, Protocol, TypedDict

from provider import SQLiteScienceLabProvider


class ScienceLabProvider(Protocol):
    def list_experiments(self) -> list[dict[str, Any]]:
        ...

    def list_inventory(self) -> list[dict[str, Any]]:
        ...

    def list_progress(self) -> list[dict[str, Any]]:
        ...


class ToolResponse(TypedDict, total=False):
    status: Literal["ok", "needs_information", "error"]
    message: str
    data: dict[str, Any]


def _material_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "material"


def _score_experiment(
    experiment: dict[str, Any],
    inventory_by_id: dict[str, dict[str, Any]],
) -> float:
    score = 0.0
    visual = experiment.get("visual_excitement")
    waiting = experiment.get("waiting_time")
    if visual == "high":
        score += 3
    elif visual == "medium":
        score += 2
    if waiting == "low":
        score += 3
    elif waiting == "medium":
        score += 1
    for material in experiment.get("materials", []):
        if inventory_by_id.get(_material_id(str(material)), {}).get("status") == "have":
            score += 0.5
    return score


def _material_plan(
    experiments: list[dict[str, Any]],
    inventory: list[dict[str, Any]],
) -> dict[str, list[str]]:
    inventory_by_id = {str(item["material_id"]): item for item in inventory}
    plan = {
        "Already in Home Inventory": [],
        "Please Confirm": [],
        "Recommended Amazon Order": [],
    }
    seen: set[str] = set()
    for experiment in experiments:
        for material in experiment.get("materials", []):
            display = str(material).strip()
            if not display:
                continue
            material_id = _material_id(display)
            if material_id in seen:
                continue
            seen.add(material_id)
            status = inventory_by_id.get(material_id, {}).get("status")
            if status == "have":
                plan["Already in Home Inventory"].append(display)
            elif status in {"missing", "low"}:
                plan["Recommended Amazon Order"].append(display)
            else:
                plan["Please Confirm"].append(display)
    return {key: sorted(values, key=str.lower) for key, values in plan.items()}


class ScienceLabTools:
    """Tool layer for N4OS Science Lab planning."""

    def __init__(self, provider: ScienceLabProvider):
        self.provider = provider

    def plan_next(self, count: int = 4) -> ToolResponse:
        count = max(1, min(count, 20))
        experiments = self.provider.list_experiments()
        if not experiments:
            return {
                "status": "needs_information",
                "message": (
                    "Science Lab is ready, but I do not have experiment records yet. "
                    "Send experiment pages/photos or paste experiment titles with materials, "
                    "then ask me to plan the next experiments again."
                ),
                "data": {"missing_fields": ["experiments"]},
            }

        progress = {
            str(item.get("experiment_id")): item
            for item in self.provider.list_progress()
        }
        inventory = self.provider.list_inventory()
        inventory_by_id = {str(item["material_id"]): item for item in inventory}
        candidates = [
            experiment
            for experiment in experiments
            if progress.get(str(experiment.get("id")), {}).get("status")
            not in {"completed", "skipped"}
        ]
        ranked = sorted(
            candidates,
            key=lambda experiment: (
                -_score_experiment(experiment, inventory_by_id),
                experiment.get("library_order") is None,
                experiment.get("library_order") or 0,
                str(experiment.get("title", "")).lower(),
            ),
        )
        selected = ranked[:count]
        if not selected:
            return {
                "status": "needs_information",
                "message": (
                    "All imported Science Lab experiments are completed or skipped. "
                    "Import more experiments or ask me to include a specific one again."
                ),
                "data": {"missing_fields": ["available_experiments"]},
            }

        return {
            "status": "ok",
            "message": f"Science Lab plan ready for {len(selected)} experiment(s).",
            "data": {
                "experiments": selected,
                "material_plan": _material_plan(selected, inventory),
            },
        }


def build_default_tools() -> ScienceLabTools:
    return ScienceLabTools(SQLiteScienceLabProvider())

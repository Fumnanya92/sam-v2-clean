"""Operational goal state for the runtime foundation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _unwrap(value: Any) -> Any:
    while isinstance(value, dict) and "value" in value:
        value = value.get("value")
    return value


@dataclass
class GoalState:
    current_objective: str = ""
    active_work: str = ""
    pending_decisions: list[str] = field(default_factory=list)
    current_candidates: list[str] = field(default_factory=list)
    current_tool: str = ""
    last_observation: str = ""
    unresolved_blockers: list[str] = field(default_factory=list)
    next_expected_action: str = ""
    workflow_stage: str = "idle"
    status: str = "idle"
    turn_count: int = 0

    def to_memory(self) -> dict[str, Any]:
        return {
            "current_objective": self.current_objective,
            "active_work": self.active_work,
            "pending_decisions": self.pending_decisions,
            "current_candidates": self.current_candidates,
            "current_tool": self.current_tool,
            "last_observation": self.last_observation,
            "unresolved_blockers": self.unresolved_blockers,
            "next_expected_action": self.next_expected_action,
            "workflow_stage": self.workflow_stage,
            "status": self.status,
            "turn_count": self.turn_count,
        }

    @classmethod
    def from_memory(cls, memory_block: dict[str, Any] | None) -> "GoalState":
        if not isinstance(memory_block, dict):
            return cls()
        raw = memory_block.get("goal_state", {})
        if isinstance(raw, dict) and "value" in raw:
            raw = raw.get("value", {})
        if not isinstance(raw, dict):
            return cls()

        pending = _unwrap(raw.get("pending_decisions", []))
        blockers = _unwrap(raw.get("unresolved_blockers", []))
        candidates = _unwrap(raw.get("current_candidates", []))
        return cls(
            current_objective=str(_unwrap(raw.get("current_objective", ""))),
            active_work=str(_unwrap(raw.get("active_work", ""))),
            pending_decisions=[str(item) for item in pending if str(item).strip()] if isinstance(pending, list) else [],
            current_candidates=[str(item) for item in candidates if str(item).strip()] if isinstance(candidates, list) else [],
            current_tool=str(_unwrap(raw.get("current_tool", ""))),
            last_observation=str(_unwrap(raw.get("last_observation", ""))),
            unresolved_blockers=[str(item) for item in blockers if str(item).strip()] if isinstance(blockers, list) else [],
            next_expected_action=str(_unwrap(raw.get("next_expected_action", ""))),
            workflow_stage=str(_unwrap(raw.get("workflow_stage", "idle")) or "idle"),
            status=str(_unwrap(raw.get("status", "idle")) or "idle"),
            turn_count=int(_unwrap(raw.get("turn_count", 0)) or 0),
        )

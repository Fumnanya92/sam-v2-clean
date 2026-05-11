"""Task planning primitives for Sam.

Phase 1 keeps the planner intentionally small. It gives the router a clean
place to ask, "what should happen next?" without putting business logic inside
the router itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(slots=True)
class TaskPlan:
    """A lightweight execution plan produced from a user request."""

    goal: str
    tool_name: str
    payload: dict[str, Any] = field(default_factory=dict)
    mode: str = "direct"


class TaskPlanner:
    """Create execution plans from user goals and available tool metadata.

    This class should remain generic. It should inspect capabilities/tools and
    produce a plan; it should not execute tools or contain project-specific
    routing assumptions.
    """

    def __init__(self, capability_registry: Any | None = None) -> None:
        self.capability_registry = capability_registry

    def plan(self, request: str, context: Mapping[str, Any] | None = None) -> TaskPlan:
        """Return a direct execution plan for the current Phase 1 runtime."""
        normalized_request = request.strip()
        payload: dict[str, Any] = {
            "request": normalized_request,
            "context": dict(context or {}),
        }

        tool_name = self._select_default_tool()
        return TaskPlan(
            goal=normalized_request,
            tool_name=tool_name,
            payload=payload,
            mode="direct",
        )

    def _select_default_tool(self) -> str:
        """Select a default tool without adding intent-style routing logic."""
        if self.capability_registry and hasattr(self.capability_registry, "default_tool"):
            default_tool = self.capability_registry.default_tool()
            if default_tool:
                return str(default_tool)

        return "assistant.respond"

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
        context_data = dict(context or {})
        available_tools = self._normalize_available_tools(context_data.get("available_tools"))
        requested_tool = self._first_present(
            context_data.get("tool_name"),
            context_data.get("intent"),
            context_data.get("capability"),
        )

        tool_name = self._select_tool(requested_tool, available_tools)
        payload: dict[str, Any] = {
            "request": normalized_request,
            "context": context_data,
        }

        return TaskPlan(
            goal=normalized_request,
            tool_name=tool_name,
            payload=payload,
            mode="direct",
        )

    def _select_tool(self, requested_tool: str | None, available_tools: set[str]) -> str:
        """Select the best tool for a direct Phase 1 execution plan."""
        if requested_tool:
            if not available_tools or requested_tool in available_tools:
                return requested_tool

        default_tool = self._select_default_tool()
        if not available_tools or default_tool in available_tools:
            return default_tool

        if "assistant.respond" in available_tools:
            return "assistant.respond"

        return sorted(available_tools)[0]

    def _select_default_tool(self) -> str:
        """Select a default tool without adding intent-style routing logic."""
        if self.capability_registry and hasattr(self.capability_registry, "default_tool"):
            default_tool = self.capability_registry.default_tool()
            if default_tool:
                return str(default_tool)

        return "assistant.respond"

    def _normalize_available_tools(self, value: Any) -> set[str]:
        if value is None:
            return set()
        if isinstance(value, str):
            return {value}
        try:
            return {str(item) for item in value if str(item).strip()}
        except TypeError:
            return set()

    def _first_present(self, *values: Any) -> str | None:
        for value in values:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return None

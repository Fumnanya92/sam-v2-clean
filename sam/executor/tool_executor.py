from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult


@dataclass(slots=True)
class ToolDefinition:
    name: str
    handler: Callable[[dict[str, Any]], Any]
    description: str = ""
    action_category: str = "read_data"
    requires_write: bool = False


class ToolExecutor:
    """Thin execution layer for invoking registered tools dynamically."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self,
        tool_name: str,
        handler: Callable[[dict[str, Any]], Any],
        *,
        description: str = "",
        action_category: str = "read_data",
        requires_write: bool = False,
    ) -> None:
        self._tools[tool_name] = ToolDefinition(
            name=tool_name,
            handler=handler,
            description=description,
            action_category=action_category,
            requires_write=requires_write,
        )

    def execute(self, tool_name: str, payload: dict[str, Any] | None = None) -> SamResult:
        if tool_name not in self._tools:
            return SamResult(
                status="failed",
                summary="Tool is not registered.",
                error_type=ErrorType.MISSING_CAPABILITY,
                error_message=tool_name,
                next_action="ask_user",
                metadata={"tool": tool_name},
            )

        definition = self._tools[tool_name]
        result = definition.handler(payload or {})
        if isinstance(result, SamResult):
            result.metadata.setdefault("tool", tool_name)
            return result

        return SamResult(
            status="success",
            summary=str(result) if result is not None else "Tool executed successfully.",
            next_action="stop",
            metadata={"tool": tool_name, "result": result},
        )

    def get(self, tool_name: str) -> ToolDefinition | None:
        return self._tools.get(tool_name)

    @property
    def available_tools(self) -> list[str]:
        return sorted(self._tools.keys())

    def list_metadata(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "action_category": tool.action_category,
                "requires_write": tool.requires_write,
            }
            for tool in sorted(self._tools.values(), key=lambda item: item.name)
        ]

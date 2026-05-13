from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult
from diagnostics.trace import append_trace, trace_step


@dataclass(slots=True)
class ToolDefinition:
    name: str
    handler: Callable[[dict[str, Any]], Any]
    description: str = ""
    action_category: str = "read_data"
    requires_write: bool = False

    def __call__(self, payload: dict[str, Any] | None = None) -> Any:
        return self.handler(payload or {})


class ToolExecutor:
    """Thin execution layer for invoking registered tools dynamically."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._aliases: dict[str, str] = {
            "inspect_repo": "inspect_project_repo",
            "inspect_repository": "inspect_project_repo",
            "repo_status": "inspect_project_repo",
            "git_status": "inspect_git_state",
            "check_git_status": "inspect_git_state",
        }

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
        resolved_tool_name = self.resolve_tool_name(tool_name)
        if resolved_tool_name not in self._tools:
            return append_trace(
                SamResult(
                status="failed",
                summary="Tool is not registered.",
                error_type=ErrorType.MISSING_CAPABILITY,
                error_message=tool_name,
                next_action="ask_user",
                metadata={"tool": tool_name},
                ),
                trace_step("Tool selection failed", status="failed", tool=tool_name, reason="Tool is not registered"),
            )

        definition = self._tools[resolved_tool_name]
        result = definition.handler(payload or {})
        if isinstance(result, SamResult):
            result.metadata.setdefault("tool", resolved_tool_name)
            if resolved_tool_name != tool_name:
                result.metadata.setdefault("requested_tool", tool_name)
            return append_trace(
                result,
                trace_step("Tool selected", tool=resolved_tool_name, action=resolved_tool_name),
                trace_step("Observation", status=result.status, observation=result.summary),
            )

        return append_trace(
            SamResult(
            status="success",
            summary=str(result) if result is not None else "Tool executed successfully.",
            next_action="stop",
            metadata={"tool": resolved_tool_name, "requested_tool": tool_name, "result": result},
            ),
            trace_step("Tool selected", tool=resolved_tool_name, action=resolved_tool_name),
            trace_step("Observation", status="success", observation=str(result) if result is not None else "Tool executed successfully."),
        )

    def resolve_tool_name(self, tool_name: str) -> str:
        if tool_name in self._tools:
            return tool_name
        return self._aliases.get(tool_name, tool_name)

    def get(self, tool_name: str) -> ToolDefinition | None:
        return self._tools.get(self.resolve_tool_name(tool_name))

    @property
    def available_tools(self) -> list[str]:
        return sorted(set(self._tools.keys()) | set(self._aliases.keys()))

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

from typing import Any, Callable


class ToolExecutor:
    """Thin execution layer for invoking registered tools dynamically."""

    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., Any]] = {}

    def register(self, tool_name: str, handler: Callable[..., Any]) -> None:
        self._tools[tool_name] = handler

    def execute(self, tool_name: str, payload: dict[str, Any] | None = None) -> Any:
        if tool_name not in self._tools:
            raise ValueError("Tool not registered")

        tool = self._tools[tool_name]
        return tool(payload or {})

    @property
    def available_tools(self) -> list[str]:
        return sorted(self._tools.keys())

"""Shared execution trace helpers for Sam results."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from diagnostics.result import SamResult


def trace_step(
    label: str,
    *,
    status: str = "info",
    tool: str = "",
    action: str = "",
    path: str = "",
    command: str | list[str] = "",
    observation: str = "",
    reason: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    step: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "label": label,
        "status": status,
    }
    for key, value in {
        "tool": tool,
        "action": action,
        "path": path,
        "command": command,
        "observation": observation,
        "reason": reason,
    }.items():
        if value:
            step[key] = value
    if metadata:
        step["metadata"] = metadata
    return step


def append_trace(result: SamResult, *steps: dict[str, Any]) -> SamResult:
    trace = result.metadata.get("execution_trace", [])
    if not isinstance(trace, list):
        trace = []
    trace.extend(step for step in steps if isinstance(step, dict))
    result.metadata["execution_trace"] = trace
    return result


def ensure_trace(result: SamResult) -> SamResult:
    """Ensure every result has a readable execution trace."""
    trace = result.metadata.get("execution_trace")
    if isinstance(trace, list) and trace:
        return result

    fallback: list[dict[str, Any]] = []
    tool = str(result.metadata.get("tool", "") or result.metadata.get("intent", "") or "")
    if tool:
        fallback.append(trace_step("Tool selected", tool=tool, action=tool))
    if result.metadata.get("path"):
        fallback.append(trace_step("Path resolved", path=str(result.metadata["path"])))
    if result.metadata.get("repo_root"):
        fallback.append(trace_step("Repository resolved", path=str(result.metadata["repo_root"])))
    if result.metadata.get("root_path"):
        fallback.append(trace_step("Project root resolved", path=str(result.metadata["root_path"])))
    if result.metadata.get("command"):
        fallback.append(trace_step("Command prepared", command=result.metadata["command"]))
    if result.metadata.get("run_command"):
        fallback.append(trace_step("Command prepared", command=result.metadata["run_command"]))
    if result.error_message:
        fallback.append(trace_step("Failure observed", status="failed", observation=result.error_message))
    else:
        fallback.append(trace_step("Observation", status=result.status, observation=result.summary))
    result.metadata["execution_trace"] = fallback
    return result

"""Operational tool registry for observation-driven runtime work."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any, Callable

from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult
from projects import inspection_metadata
from tools import CodebaseCleanupService
from workers import worker_monitor


Handler = Callable[[dict[str, Any], Any, dict[str, Any] | None], SamResult]


@dataclass(frozen=True)
class OperationalTool:
    name: str
    description: str
    arguments: dict[str, Any]
    handler: Handler


@dataclass(frozen=True)
class OperationalToolContext:
    awareness: Any
    project_registry: Any
    tool_executor: Any
    project_inspector: Any
    workspace_cleanup: Any
    resolve_project_or_directory: Callable[[str, dict[str, Any] | None], tuple[SamResult, Path | None]]
    service_result: Callable[..., SamResult]
    check_python_syntax: Callable[[Path, Any], SamResult]
    inspect_recent_changes: Callable[[Path, Any], SamResult]
    memory_roots: Callable[[dict[str, Any] | None], list[str]]


class OperationalToolRegistry:
    """Registry of operational capabilities available to autonomous runtime."""

    def __init__(self) -> None:
        self._tools: dict[str, OperationalTool] = {}
        self._aliases: dict[str, str] = {"inspect_project_repo": "inspect_repo"}

    def register(self, tool: OperationalTool) -> None:
        self._tools[tool.name] = tool

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        request: Any,
        memory_block: dict[str, Any] | None,
    ) -> SamResult:
        resolved_name = self.resolve(tool_name)
        tool = self._tools.get(resolved_name)
        if tool is None:
            return SamResult(
                status="failed",
                summary=f"Tool {tool_name} is not available to the autonomous read-only loop.",
                error_type=ErrorType.MISSING_CAPABILITY,
                error_message=tool_name,
                next_action="ask_user",
                metadata={"intent": "autonomous_request"},
            )
        result = tool.handler(arguments, request, memory_block)
        result.metadata.setdefault("tool", resolved_name)
        if resolved_name != tool_name:
            result.metadata.setdefault("requested_tool", tool_name)
        return result

    def manifest(self) -> list[dict[str, Any]]:
        return [
            {"name": tool.name, "description": tool.description, "arguments": tool.arguments}
            for tool in sorted(self._tools.values(), key=lambda item: item.name)
        ]

    def resolve(self, tool_name: str) -> str:
        if tool_name in self._tools:
            return tool_name
        return self._aliases.get(tool_name, tool_name)


def build_default_operational_registry(context: OperationalToolContext) -> OperationalToolRegistry:
    registry = OperationalToolRegistry()

    def register(name: str, description: str, arguments: dict[str, Any], handler: Handler) -> None:
        registry.register(OperationalTool(name=name, description=description, arguments=arguments, handler=handler))

    register("capabilities", "List available Sam capabilities.", {}, _capabilities(context))
    register("list_projects", "List registered projects.", {}, _list_projects(context))
    register("read_file", "Read a UTF-8 text file.", {"path": "file path or name", "max_chars": 6000}, _read_file(context))
    register("list_directory", "List files in a directory.", {"path": "directory path or project path"}, _list_directory(context))
    register("inspect_repo", "Inspect repo branch, working tree, and top-level files.", {"query": "project name or path"}, _inspect_repo(context))
    register("inspect_git_state", "Inspect git branch and changed files.", {"query": "project name or path"}, _inspect_git_state(context))
    register("scan_codebase_patterns", "Scan codebase for exact text patterns.", {"query": "project name or path", "patterns": ["text"]}, _scan_codebase_patterns(context))
    register("check_python_syntax", "Parse Python files and report syntax errors.", {"query": "project name or path"}, _check_python_syntax(context))
    register("inspect_recent_changes", "Summarize current git working-tree changes.", {"query": "project name or path"}, _inspect_recent_changes(context))
    register("inspect_workspace_cleanup", "Inspect duplicate cleanup candidates without deleting.", {"scope": "all|projects|runtime"}, _inspect_workspace_cleanup(context))
    register("list_executor_tools", "List registered executor tools.", {}, _list_executor_tools(context))
    register("list_worker_tasks", "List running and recent worker tasks.", {}, _list_worker_tasks())

    return registry


def _capabilities(context: OperationalToolContext) -> Handler:
    def handler(arguments: dict[str, Any], request: Any, memory_block: dict[str, Any] | None) -> SamResult:
        result = context.awareness.describe_self()
        result.metadata.setdefault("intent", "capabilities")
        return result

    return handler


def _list_projects(context: OperationalToolContext) -> Handler:
    def handler(arguments: dict[str, Any], request: Any, memory_block: dict[str, Any] | None) -> SamResult:
        project_result, projects = context.project_registry.list_projects()
        if not project_result.ok:
            return context.service_result("list_projects", project_result)
        names = [project.name for project in projects]
        return SamResult(
            status="success",
            summary=f"I know about {len(names)} project(s): {', '.join(names)}.",
            next_action="stop",
            metadata={"intent": "list_projects", "count": len(names), "projects": names},
        )

    return handler


def _list_executor_tools(context: OperationalToolContext) -> Handler:
    def handler(arguments: dict[str, Any], request: Any, memory_block: dict[str, Any] | None) -> SamResult:
        tools = context.tool_executor.list_metadata()
        return SamResult(
            status="success",
            summary=f"{len(tools)} executor tool(s) registered.",
            next_action="stop",
            metadata={"intent": "list_executor_tools", "tools": tools, "count": len(tools)},
        )

    return handler


def _list_worker_tasks() -> Handler:
    def handler(arguments: dict[str, Any], request: Any, memory_block: dict[str, Any] | None) -> SamResult:
        tasks = sorted(worker_monitor.list_tasks(), key=lambda item: item.created_at, reverse=True)[:20]
        return SamResult(
            status="success",
            summary=f"{len(tasks)} recent worker task(s) found.",
            next_action="stop",
            metadata={
                "intent": "list_worker_tasks",
                "tasks": [
                    {
                        "task_id": task.task_id,
                        "name": task.name,
                        "worker_name": task.worker_name,
                        "status": task.status,
                        "description": task.description,
                        "last_output": task.output_lines[-3:],
                    }
                    for task in tasks
                ],
            },
        )

    return handler


def _read_file(context: OperationalToolContext) -> Handler:
    def handler(arguments: dict[str, Any], request: Any, memory_block: dict[str, Any] | None) -> SamResult:
        path = str(arguments.get("path", "")).strip()
        if not path:
            return SamResult(
                status="failed",
                summary="File path is required.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing path",
                next_action="ask_user",
                metadata={"intent": "read_file"},
            )
        additional_roots = context.memory_roots(memory_block)
        resolve_result, resolved = context.project_inspector.tools.resolve_file_query(path, additional_roots=additional_roots)
        if not resolve_result.ok or resolved is None:
            return context.service_result("read_file", resolve_result, metadata={"path": path})
        max_chars = int(arguments.get("max_chars", 6000) or 6000)
        file_result, content = context.project_inspector.tools.read_text_file(resolved, max_chars=max_chars)
        if file_result.ok and content is not None:
            file_result.metadata["content"] = content
            file_result.metadata.setdefault("intent", "read_file")
        return file_result

    return handler


def _list_directory(context: OperationalToolContext) -> Handler:
    def handler(arguments: dict[str, Any], request: Any, memory_block: dict[str, Any] | None) -> SamResult:
        path = str(arguments.get("path", "") or arguments.get("query", "")).strip()
        root_result, root = context.resolve_project_or_directory(path, memory_block)
        if not root_result.ok or root is None:
            return context.service_result("list_directory", root_result, metadata={"path": path})
        directory_result, entries = context.project_inspector.tools.list_directory(root)
        if directory_result.ok:
            directory_result.metadata.update({"intent": "list_directory", "entries": entries[:100]})
        return directory_result

    return handler


def _inspect_repo(context: OperationalToolContext) -> Handler:
    def handler(arguments: dict[str, Any], request: Any, memory_block: dict[str, Any] | None) -> SamResult:
        query = str(arguments.get("query", "") or arguments.get("path", "")).strip()
        root_result, root = context.resolve_project_or_directory(query, memory_block)
        if not root_result.ok or root is None:
            return context.service_result("inspect_repo", root_result, metadata={"query": query})
        inspect_result, inspection = context.project_inspector.inspect(str(root))
        if not inspect_result.ok or inspection is None:
            return context.service_result("inspect_repo", inspect_result, metadata={"query": str(root)})
        metadata = inspection_metadata(inspection)
        metadata["intent"] = "inspect_repo"
        return SamResult(
            status="success",
            summary=f"{inspection.name} is on branch {inspection.branch or 'unknown'} with {len(inspection.changed_files)} changed file(s).",
            next_action="stop",
            metadata=metadata,
        )

    return handler


def _inspect_git_state(context: OperationalToolContext) -> Handler:
    def handler(arguments: dict[str, Any], request: Any, memory_block: dict[str, Any] | None) -> SamResult:
        query = str(arguments.get("query", "") or arguments.get("repo_path", "") or arguments.get("path", "")).strip()
        root_result, root = context.resolve_project_or_directory(query, memory_block)
        if not root_result.ok or root is None:
            return context.service_result("inspect_git_state", root_result, metadata={"query": query})
        git_result, snapshot = context.project_inspector.tools.inspect_git_state(root)
        if git_result.ok and snapshot is not None:
            git_result.metadata.update(
                {
                    "intent": "inspect_git_state",
                    "repo_root": snapshot.repo_root,
                    "branch": snapshot.branch,
                    "is_clean": snapshot.is_clean,
                    "changed_files": snapshot.changed_files,
                    "staged_files": snapshot.staged_files,
                    "unstaged_files": snapshot.unstaged_files,
                    "untracked_files": snapshot.untracked_files,
                }
            )
        return git_result

    return handler


def _scan_codebase_patterns(context: OperationalToolContext) -> Handler:
    def handler(arguments: dict[str, Any], request: Any, memory_block: dict[str, Any] | None) -> SamResult:
        query = str(arguments.get("query", "") or arguments.get("path", "")).strip()
        root_result, root = context.resolve_project_or_directory(query, memory_block)
        if not root_result.ok or root is None:
            return context.service_result("scan_codebase_patterns", root_result, metadata={"query": query})
        patterns = arguments.get("patterns", [])
        if isinstance(patterns, str):
            patterns = [patterns]
        patterns = [str(item).strip() for item in patterns if str(item).strip()]
        if not patterns:
            patterns = patterns_from_request(request)
        if not patterns:
            return SamResult(
                status="failed",
                summary="Search patterns are required.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing patterns",
                next_action="ask_user",
                metadata={"intent": "scan_codebase_patterns", "root_path": str(root)},
            )
        scan_result, report = CodebaseCleanupService(root).inspect(patterns)
        scan_result.metadata.update(
            {
                "intent": "scan_codebase_patterns",
                "root_path": str(root),
                "patterns": patterns,
                "matches": [match.__dict__ for match in report.matches[:100]],
                "match_count": len(report.matches),
            }
        )
        return scan_result

    return handler


def _check_python_syntax(context: OperationalToolContext) -> Handler:
    def handler(arguments: dict[str, Any], request: Any, memory_block: dict[str, Any] | None) -> SamResult:
        query = str(arguments.get("query", "") or arguments.get("path", "")).strip()
        root_result, root = context.resolve_project_or_directory(query, memory_block)
        if not root_result.ok or root is None:
            return context.service_result("check_python_syntax", root_result, metadata={"query": query})
        child = _child_request("check_python_syntax", arguments, request)
        return context.check_python_syntax(root, child)

    return handler


def _inspect_recent_changes(context: OperationalToolContext) -> Handler:
    def handler(arguments: dict[str, Any], request: Any, memory_block: dict[str, Any] | None) -> SamResult:
        query = str(arguments.get("query", "") or arguments.get("path", "")).strip()
        root_result, root = context.resolve_project_or_directory(query, memory_block)
        if not root_result.ok or root is None:
            return context.service_result("inspect_recent_changes", root_result, metadata={"query": query})
        child = _child_request("inspect_recent_changes", arguments, request)
        return context.inspect_recent_changes(root, child)

    return handler


def _inspect_workspace_cleanup(context: OperationalToolContext) -> Handler:
    def handler(arguments: dict[str, Any], request: Any, memory_block: dict[str, Any] | None) -> SamResult:
        scope = str(arguments.get("scope", "all") or "all")
        result, _metadata = context.workspace_cleanup.inspect(scope)
        result.metadata.setdefault("intent", "inspect_workspace_cleanup")
        return result

    return handler


def _child_request(tool_name: str, arguments: dict[str, Any], parent_request: Any) -> Any:
    return SimpleNamespace(
        intent=tool_name,
        parameters=dict(arguments),
        raw_text=getattr(parent_request, "raw_text", ""),
        confidence=getattr(parent_request, "confidence", "low"),
        source="autonomous_loop",
    )


def patterns_from_request(request: Any) -> list[str]:
    parameters = getattr(request, "parameters", {})
    raw_patterns = parameters.get("patterns", []) if isinstance(parameters, dict) else []
    patterns: list[str] = []
    if isinstance(raw_patterns, str):
        patterns = [raw_patterns]
    elif isinstance(raw_patterns, list):
        patterns = [str(item) for item in raw_patterns]

    clean = [item.strip().strip("`'\"") for item in patterns if item and item.strip()]
    if clean:
        return clean

    text = getattr(request, "raw_text", "")
    quoted = re.findall(r"`([^`]+)`|'([^']+)'|\"([^\"]+)\"", text)
    for groups in quoted:
        for value in groups:
            if value.strip():
                clean.append(value.strip())

    lowered = text.lower()
    scan_match = re.search(r"\b(?:for|patterns?)\s+(.+?)(?:\.|$)", text, flags=re.IGNORECASE)
    if scan_match and "hardcoded project names" not in lowered:
        fragment = scan_match.group(1)
        for part in re.split(r",|\band\b", fragment):
            token = part.strip().strip("`'\"")
            if token and len(token.split()) <= 4:
                clean.append(token)

    return list(dict.fromkeys(item for item in clean if item))

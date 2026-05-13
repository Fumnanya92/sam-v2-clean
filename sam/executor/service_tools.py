"""Phase 3: Service-backed tool handlers for codebase cleanup and local file inspection.

These handlers wrap utility services (CodebaseCleanupService, SafeLocalTools) and
register them as executor tools, making them available to the task planner and router.

Service handlers are separated from intent handlers to maintain clear layering:
- Runtime handlers: compatibility capability logic (runtime_tools_registry.py)
- Service handlers: reusable utility services (this module)
"""

from __future__ import annotations

from typing import Any

from diagnostics.result import SamResult
from tools import CodebaseCleanupService, SafeLocalTools


def register_service_tools(executor: Any, repo_root: str, db_path: str | None = None) -> None:
    """Register all service-backed tools with the executor.
    
    Args:
        executor: ToolExecutor instance to register tools with
        repo_root: Root path for codebase cleanup service
        db_path: Optional database path for audit logging
    """
    cleanup_service = CodebaseCleanupService(repo_root=repo_root)
    local_tools = SafeLocalTools(db_path=db_path)
    
    # Codebase cleanup handlers
    _register_codebase_tools(executor, cleanup_service)
    
    # Local file/git inspection handlers
    _register_local_tools(executor, local_tools)


def _register_codebase_tools(executor: Any, cleanup_service: CodebaseCleanupService) -> None:
    """Register CodebaseCleanupService handlers."""
    has_scan_handler = executor.get("scan_codebase_patterns") is not None
    
    def _scan_codebase_patterns_handler(payload: dict[str, Any] | None = None) -> SamResult:
        """Scan codebase for requested patterns.
        
        Payload:
            patterns: list[str] - patterns to search for (required)
            request: str - original user request (optional)
        """
        if not payload:
            return SamResult(
                status="failed",
                summary="Payload is required.",
                error_type="TOOL_FAILED",
                next_action="ask_user",
            )
        
        patterns = payload.get("patterns", [])
        if isinstance(patterns, str):
            patterns = [patterns]
        
        if not patterns:
            return SamResult(
                status="failed",
                summary="Search patterns are required.",
                error_type="TOOL_FAILED",
                error_message="empty patterns",
                next_action="ask_user",
            )
        
        result, report = cleanup_service.inspect(patterns)
        if result.ok:
            result.metadata.setdefault("matches_detail", [])
            for match in report.matches[:50]:  # Limit to first 50 matches
                result.metadata["matches_detail"].append({
                    "file": match.path,
                    "line": match.line_number,
                    "pattern": match.pattern,
                    "content": match.line,
                })
        return result
    
    if not has_scan_handler:
        executor.register(
            "scan_codebase_patterns",
            _scan_codebase_patterns_handler,
            description="Scan codebase for specific text patterns",
            action_category="read_data",
            requires_write=False,
        )
    
    def _replace_codebase_patterns_handler(payload: dict[str, Any] | None = None) -> SamResult:
        """Apply replacements to codebase.
        
        Payload:
            replacements: dict[str, str] - old_text → new_text mappings (required)
            request: str - original user request (optional)
        """
        if not payload:
            return SamResult(
                status="failed",
                summary="Payload is required.",
                error_type="TOOL_FAILED",
                next_action="ask_user",
            )
        
        replacements = payload.get("replacements", {})
        if not isinstance(replacements, dict):
            return SamResult(
                status="failed",
                summary="Replacements must be a dictionary.",
                error_type="TOOL_FAILED",
                error_message="invalid replacements type",
                next_action="ask_user",
            )
        
        if not replacements:
            return SamResult(
                status="failed",
                summary="Replacement mappings are required.",
                error_type="TOOL_FAILED",
                error_message="empty replacements",
                next_action="ask_user",
            )
        
        result, report = cleanup_service.replace(replacements)
        if result.ok:
            result.metadata.setdefault("changed_files", report.changed_files)
            result.metadata.setdefault("files_scanned", report.scanned_files)
        return result
    
    executor.register(
        "replace_codebase_patterns",
        _replace_codebase_patterns_handler,
        description="Apply text replacements across codebase",
        action_category="write_data",
        requires_write=True,
    )


def _register_local_tools(executor: Any, local_tools: SafeLocalTools) -> None:
    """Register SafeLocalTools handlers."""
    
    def _read_local_file_handler(payload: dict[str, Any] | None = None) -> SamResult:
        """Read a local file's content.
        
        Payload:
            path: str - file path to read (required)
            max_chars: int - max characters to read (default: 4000)
        """
        if not payload:
            return SamResult(
                status="failed",
                summary="Payload is required.",
                error_type="TOOL_FAILED",
                next_action="ask_user",
            )
        
        path = payload.get("path")
        if not path:
            return SamResult(
                status="failed",
                summary="File path is required.",
                error_type="TOOL_FAILED",
                error_message="missing path",
                next_action="ask_user",
            )
        
        max_chars = payload.get("max_chars", 4000)
        result, content = local_tools.read_text_file(path, max_chars=max_chars)
        if result.ok and content:
            result.metadata["content"] = content
        return result
    
    executor.register(
        "read_local_file",
        _read_local_file_handler,
        description="Read content from a local file",
        action_category="read_data",
        requires_write=False,
    )
    
    def _list_local_directory_handler(payload: dict[str, Any] | None = None) -> SamResult:
        """List contents of a local directory.
        
        Payload:
            path: str - directory path to list (required)
        """
        if not payload:
            return SamResult(
                status="failed",
                summary="Payload is required.",
                error_type="TOOL_FAILED",
                next_action="ask_user",
            )
        
        path = payload.get("path")
        if not path:
            return SamResult(
                status="failed",
                summary="Directory path is required.",
                error_type="TOOL_FAILED",
                error_message="missing path",
                next_action="ask_user",
            )
        
        result, items = local_tools.list_directory(path)
        if result.ok and items:
            result.metadata["items"] = items
            result.metadata["item_count"] = len(items)
        return result
    
    executor.register(
        "list_local_directory",
        _list_local_directory_handler,
        description="List files and directories in a local directory",
        action_category="read_data",
        requires_write=False,
    )
    
    def _inspect_git_repository_handler(payload: dict[str, Any] | None = None) -> SamResult:
        """Inspect git repository state.
        
        Payload:
            repo_path: str - path to git repository (required)
        """
        if not payload:
            return SamResult(
                status="failed",
                summary="Payload is required.",
                error_type="TOOL_FAILED",
                next_action="ask_user",
            )
        
        repo_path = payload.get("repo_path")
        if not repo_path:
            return SamResult(
                status="failed",
                summary="Repository path is required.",
                error_type="TOOL_FAILED",
                error_message="missing repo_path",
                next_action="ask_user",
            )
        
        result, snapshot = local_tools.inspect_git_state(repo_path)
        if result.ok and snapshot:
            result.metadata.update({
                "repo_root": snapshot.repo_root,
                "branch": snapshot.branch,
                "is_clean": snapshot.is_clean,
                "changed_files": snapshot.changed_files,
                "staged_files": snapshot.staged_files,
                "unstaged_files": snapshot.unstaged_files,
                "untracked_files": snapshot.untracked_files,
            })
        return result
    
    executor.register(
        "inspect_git_repository",
        _inspect_git_repository_handler,
        description="Inspect git repository status (branch, changes, etc.)",
        action_category="read_data",
        requires_write=False,
    )

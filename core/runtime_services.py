"""Shared runtime service helpers for tool execution."""

from __future__ import annotations

import ast
from pathlib import Path
import re
from typing import Any

from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult
from core.request_model import IntentRequest


class RuntimeToolServices:
    """Service operations used by runtime/executor tools.

    These helpers used to live on ``IntentRouter``. Keeping them here prevents
    router-private methods from becoming the execution substrate.
    """

    def __init__(
        self,
        *,
        project_registry: Any,
        project_inspector: Any,
        approval_manager: Any,
    ) -> None:
        self.project_registry = project_registry
        self.project_inspector = project_inspector
        self.approval_manager = approval_manager

    @staticmethod
    def query_or_path_from_request(request: IntentRequest) -> str:
        for key in ("query", "path", "repo_path", "root_path"):
            value = str(request.parameters.get(key, "")).strip()
            if value:
                return value

        match = re.search(r"[A-Za-z]:[\\/][^\r\n\"']+", request.raw_text)
        if not match:
            return ""

        candidate = match.group(0).strip().rstrip(".,;")
        while candidate:
            path = Path(candidate)
            if path.exists():
                return str(path)
            if " " in candidate:
                candidate = candidate.rsplit(" ", 1)[0].rstrip(".,;")
                continue
            parent = str(path.parent)
            if parent == candidate:
                break
            candidate = parent.rstrip("\\/")
        return match.group(0).strip().rstrip(".,;")

    def resolve_project_or_directory(
        self,
        query: str,
        memory_block: dict[str, Any] | None,
    ) -> tuple[SamResult, Path | None]:
        if query:
            project_result, project = self.project_registry.find_project(query)
            if project_result.ok and project is not None:
                return project_result, Path(project.root_path)

            directory_result, directory = self.project_inspector.tools.resolve_directory_query(query)
            if directory_result.ok and directory is not None:
                return directory_result, directory

        daily_state = memory_block.get("daily_state", {}) if isinstance(memory_block, dict) else {}
        last_project_root = str(daily_state.get("last_project_root_path", {}).get("value", "")).strip()
        if last_project_root:
            directory_result, directory = self.project_inspector.tools.resolve_directory_query(last_project_root)
            if directory_result.ok and directory is not None:
                return directory_result, directory

        if query:
            return (
                SamResult(
                    status="failed",
                    summary="Project or directory could not be resolved.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=query,
                    next_action="ask_user",
                ),
                None,
            )

        return (
            SamResult(
                status="failed",
                summary="I need a project name, repo path, or previous project context first.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing project context",
                next_action="ask_user",
            ),
            None,
        )

    @staticmethod
    def patterns_from_request(request: IntentRequest) -> list[str]:
        raw_patterns = request.parameters.get("patterns", [])
        patterns: list[str] = []
        if isinstance(raw_patterns, str):
            patterns = [raw_patterns]
        elif isinstance(raw_patterns, list):
            patterns = [str(item) for item in raw_patterns]

        clean = [item.strip().strip("`'\"") for item in patterns if item and item.strip()]
        if clean:
            return clean

        text = request.raw_text
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

    def check_python_syntax(self, root: Path, request: IntentRequest) -> SamResult:
        errors: list[dict[str, object]] = []
        scanned = 0
        ignored_parts = {".git", ".venv", "venv", "__pycache__", "node_modules"}
        for path in root.rglob("*.py"):
            if any(part in ignored_parts for part in path.parts):
                continue
            scanned += 1
            try:
                ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
            except SyntaxError as exc:
                errors.append(
                    {
                        "path": str(path.relative_to(root)),
                        "line": exc.lineno,
                        "message": exc.msg,
                    }
                )
        if errors:
            preview = "; ".join(f"{item['path']}:{item['line']} {item['message']}" for item in errors[:5])
            summary = f"Python syntax check found {len(errors)} error(s) across {scanned} file(s): {preview}."
        else:
            summary = f"Python syntax check passed for {scanned} file(s)."
        return SamResult(
            status="success" if not errors else "failed",
            summary=summary,
            next_action="stop" if not errors else "ask_user",
            metadata={
                "intent": "check_python_syntax",
                "root_path": str(root),
                "files_scanned": scanned,
                "errors": errors,
                "source": request.source,
                "confidence": request.confidence,
            },
        )

    def inspect_recent_changes(self, root: Path, request: IntentRequest) -> SamResult:
        git_result, snapshot = self.project_inspector.tools.inspect_git_state(root)
        if not git_result.ok or snapshot is None:
            return self.service_result("inspect_recent_changes", git_result, metadata={"root_path": str(root)})
        summary = (
            f"{root.name} is on branch {snapshot.branch or 'unknown'} with "
            f"{len(snapshot.changed_files)} changed file(s)."
        )
        if snapshot.changed_files:
            summary += " Changed: " + ", ".join(snapshot.changed_files[:12])
            if len(snapshot.changed_files) > 12:
                summary += f", and {len(snapshot.changed_files) - 12} more"
            summary += "."
        return SamResult(
            status="success",
            summary=summary,
            next_action="stop",
            metadata={
                "intent": "inspect_recent_changes",
                "root_path": str(root),
                "branch": snapshot.branch,
                "is_clean": snapshot.is_clean,
                "changed_files": snapshot.changed_files,
                "staged_files": snapshot.staged_files,
                "unstaged_files": snapshot.unstaged_files,
                "untracked_files": snapshot.untracked_files,
                "source": request.source,
                "confidence": request.confidence,
            },
        )

    def request_push_approval(self, request: IntentRequest) -> SamResult:
        if self.approval_manager is None:
            return SamResult(
                status="needs_approval",
                summary="Pushing changes requires approval before I continue.",
                error_type=ErrorType.MISSING_PERMISSION,
                error_message="git push requires approval",
                next_action="request_approval",
                metadata={"intent": "push_changes", "source": request.source, "confidence": request.confidence},
            )

        ensure_result = self.approval_manager.ensure_schema()
        if not ensure_result.ok:
            return SamResult(
                status="failed",
                summary="Approval store could not be prepared for a push request.",
                error_type=ensure_result.error_type or ErrorType.FILE_ACCESS_ERROR,
                error_message=ensure_result.error_message,
                next_action=ensure_result.next_action or "retry",
                metadata={"intent": "push_changes", "source": request.source},
            )

        create_result, approval = self.approval_manager.create_request(
            agent_id="sam_v2_runtime",
            agent_name="Sam v2 Runtime",
            tool_name="git.push",
            tool_arguments={"request_text": request.raw_text},
            action_category="execute_command",
            reason="Git push is approval-sensitive.",
            context=request.raw_text,
        )
        if not create_result.ok or approval is None:
            return SamResult(
                status="failed",
                summary="Approval request creation failed for push action.",
                error_type=create_result.error_type or ErrorType.FILE_ACCESS_ERROR,
                error_message=create_result.error_message,
                next_action=create_result.next_action or "retry",
                metadata={"intent": "push_changes", "source": request.source},
            )

        return SamResult(
            status="needs_approval",
            summary="Pushing changes requires approval before I continue.",
            error_type=ErrorType.MISSING_PERMISSION,
            error_message="git push requires approval",
            next_action="request_approval",
            metadata={
                "intent": "push_changes",
                "approval_id": approval.id,
                "source": request.source,
                "confidence": request.confidence,
            },
        )

    @staticmethod
    def service_result(
        intent: str,
        result: SamResult,
        identifier: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SamResult:
        merged_metadata = dict(result.metadata)
        merged_metadata["intent"] = intent
        if identifier is not None:
            merged_metadata["id"] = identifier
        merged_metadata.setdefault("source", "rules")
        if metadata:
            merged_metadata.update(metadata)
        return SamResult(
            status=result.status,
            summary=result.summary,
            error_type=result.error_type,
            error_message=result.error_message,
            next_action=result.next_action,
            metadata=merged_metadata,
        )

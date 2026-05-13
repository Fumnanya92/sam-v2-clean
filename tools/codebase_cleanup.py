"""Generic codebase cleanup helpers for Sam.

This module is intentionally project-agnostic. It does not know about any
specific app, game, or feature name. Sam can use it to inspect source files for
caller-provided patterns and optionally apply caller-provided replacements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path

from diagnostics.error_types import ErrorType
from diagnostics.permission_errors import is_permission_error
from diagnostics.result import SamResult
from workers import worker_monitor
from workers.names import resolve_worker_identity


DEFAULT_EXTENSIONS = {
    ".py",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".html",
    ".css",
    ".js",
    ".ts",
}

DEFAULT_IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "logs",
    "workspace/runtime",
    ".dart_tool",
    ".gradle",
    ".idea",
    ".vscode",
    "build",
    "dist",
    "coverage",
    "Pods",
    "DerivedData",
}

_IGNORED_DIR_NAMES = {item for item in DEFAULT_IGNORED_DIRS if "/" not in item}


@dataclass
class CodebaseMatch:
    path: str
    line_number: int
    pattern: str
    line: str


@dataclass
class CodebaseCleanupReport:
    scanned_files: int = 0
    matches: list[CodebaseMatch] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    skipped_paths: list[dict[str, str]] = field(default_factory=list)


class CodebaseCleanupService:
    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root).resolve()
        self._walk_errors: list[dict[str, str]] = []

    def inspect(self, patterns: list[str]) -> tuple[SamResult, CodebaseCleanupReport]:
        identity = resolve_worker_identity(tool_name="scan_codebase_patterns", action_category="read_data")
        task = worker_monitor.create_task(
            name="inspect_codebase_patterns",
            worker_type=identity.role,
            worker_name=identity.name,
            description="Inspect codebase for requested patterns.",
            worker_role=identity.role,
            responsibility=identity.responsibility,
        )
        worker_monitor.mark_running(task.task_id)

        report = CodebaseCleanupReport()
        clean_patterns = [item for item in (p.strip() for p in patterns) if item]
        worker_monitor.append_output(task.task_id, f"Root: {self.repo_root}")
        worker_monitor.append_output(task.task_id, f"Patterns: {', '.join(clean_patterns) if clean_patterns else '(none)'}")
        if not clean_patterns:
            result = SamResult(
                status="failed",
                summary="No search patterns were provided.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing patterns",
                next_action="ask_user",
            )
            worker_monitor.mark_failed(task.task_id, result.error_message or result.summary)
            return result, report

        self._walk_errors = []
        for path in self._iter_source_files():
            report.scanned_files += 1
            if report.scanned_files == 1 or report.scanned_files % 25 == 0:
                worker_monitor.append_output(task.task_id, f"Scanning file {report.scanned_files}: {path.relative_to(self.repo_root).as_posix()}")
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError as exc:
                rel_path = path.relative_to(self.repo_root).as_posix()
                reason = "permission denied" if is_permission_error(exc) else "unreadable"
                report.skipped_paths.append({"path": rel_path, "reason": reason, "error": str(exc)})
                worker_monitor.append_output(task.task_id, f"Skipped {reason} file: {rel_path}")
                continue
            rel_path = path.relative_to(self.repo_root).as_posix()
            for line_number, line in enumerate(lines, start=1):
                lowered = line.lower()
                for pattern in clean_patterns:
                    if pattern.lower() in lowered:
                        if len(report.matches) < 10:
                            worker_monitor.append_output(task.task_id, f"Match: {rel_path}:{line_number} ({pattern})")
                        report.matches.append(
                            CodebaseMatch(
                                path=rel_path,
                                line_number=line_number,
                                pattern=pattern,
                                line=line.strip(),
                            )
                        )

        worker_monitor.append_output(task.task_id, f"Scanned {report.scanned_files} file(s).")
        worker_monitor.append_output(task.task_id, f"Found {len(report.matches)} match(es).")
        report.skipped_paths.extend(self._walk_errors)
        if report.skipped_paths:
            worker_monitor.append_output(task.task_id, f"Skipped {len(report.skipped_paths)} blocked/unreadable path(s).")
        worker_monitor.mark_done(task.task_id)
        blocked_count = sum(1 for item in report.skipped_paths if item.get("reason") == "permission denied")
        summary = f"Codebase inspection found {len(report.matches)} match(es)."
        if report.skipped_paths:
            summary += f" Skipped {len(report.skipped_paths)} unreadable path(s)."
        if blocked_count:
            summary += f" {blocked_count} path(s) were blocked by permissions."
        return (
            SamResult(
                status="success",
                summary=summary,
                next_action="stop",
                metadata={
                    "scanned_files": report.scanned_files,
                    "match_count": len(report.matches),
                    "matches": [match.__dict__ for match in report.matches[:100]],
                    "skipped_paths": report.skipped_paths[:50],
                    "permission_blocked_count": blocked_count,
                    "worker_updates": [
                        f"{identity.name} scanned {report.scanned_files} file(s).",
                        f"{identity.name} found {len(report.matches)} match(es).",
                    ],
                },
            ),
            report,
        )

    def replace(self, replacements: dict[str, str]) -> tuple[SamResult, CodebaseCleanupReport]:
        identity = resolve_worker_identity(tool_name="replace_codebase_patterns", action_category="write_data")
        task = worker_monitor.create_task(
            name="replace_codebase_patterns",
            worker_type=identity.role,
            worker_name=identity.name,
            description="Apply requested codebase replacements.",
            worker_role=identity.role,
            responsibility=identity.responsibility,
        )
        worker_monitor.mark_running(task.task_id)

        report = CodebaseCleanupReport()
        clean_replacements = {
            old: new for old, new in replacements.items() if old and old != new
        }
        if not clean_replacements:
            result = SamResult(
                status="failed",
                summary="No valid replacements were provided.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing replacements",
                next_action="ask_user",
            )
            worker_monitor.mark_failed(task.task_id, result.error_message or result.summary)
            return result, report

        self._walk_errors = []
        for path in self._iter_source_files():
            report.scanned_files += 1
            try:
                original = path.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                rel_path = path.relative_to(self.repo_root).as_posix()
                reason = "permission denied" if is_permission_error(exc) else "unreadable"
                report.skipped_paths.append({"path": rel_path, "reason": reason, "error": str(exc)})
                continue
            updated = original
            for old, new in clean_replacements.items():
                updated = updated.replace(old, new)
            if updated != original:
                path.write_text(updated, encoding="utf-8")
                rel_path = path.relative_to(self.repo_root).as_posix()
                report.changed_files.append(rel_path)
                worker_monitor.append_output(task.task_id, f"Updated {rel_path}")

        report.skipped_paths.extend(self._walk_errors)
        worker_monitor.append_output(task.task_id, f"Scanned {report.scanned_files} file(s).")
        worker_monitor.append_output(task.task_id, f"Changed {len(report.changed_files)} file(s).")
        if report.skipped_paths:
            worker_monitor.append_output(task.task_id, f"Skipped {len(report.skipped_paths)} blocked/unreadable path(s).")
        worker_monitor.mark_done(task.task_id)
        return (
            SamResult(
                status="success",
                summary=f"Codebase cleanup changed {len(report.changed_files)} file(s).",
                next_action="stop",
                metadata={
                    "scanned_files": report.scanned_files,
                    "changed_files": report.changed_files,
                    "skipped_paths": report.skipped_paths[:50],
                    "permission_blocked_count": sum(1 for item in report.skipped_paths if item.get("reason") == "permission denied"),
                    "worker_updates": [
                        f"{identity.name} scanned {report.scanned_files} file(s).",
                        f"{identity.name} changed {len(report.changed_files)} file(s).",
                    ],
                },
            ),
            report,
        )

    def _iter_source_files(self):
        def onerror(exc: OSError) -> None:
            filename = str(getattr(exc, "filename", "") or "")
            try:
                rel = Path(filename).relative_to(self.repo_root).as_posix() if filename else filename
            except ValueError:
                rel = filename
            reason = "permission denied" if is_permission_error(exc) else "unreadable"
            self._walk_errors.append({"path": rel, "reason": reason, "error": str(exc)})

        for current_root, dir_names, file_names in os.walk(self.repo_root, onerror=onerror):
            current = Path(current_root)
            try:
                rel_root = current.relative_to(self.repo_root).as_posix()
            except ValueError:
                continue
            if rel_root == ".":
                rel_root = ""

            dir_names[:] = [
                name
                for name in dir_names
                if name not in _IGNORED_DIR_NAMES
                and not _is_ignored_rel_path(f"{rel_root}/{name}".strip("/"))
            ]

            for file_name in file_names:
                path = current / file_name
                rel = path.relative_to(self.repo_root).as_posix()
                if _is_ignored_rel_path(rel):
                    continue
                if path.suffix.lower() not in DEFAULT_EXTENSIONS:
                    continue
                yield path


def _is_ignored_rel_path(rel_path: str) -> bool:
    return any(rel_path == ignored or rel_path.startswith(f"{ignored}/") for ignored in DEFAULT_IGNORED_DIRS)

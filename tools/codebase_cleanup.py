"""Generic codebase cleanup helpers for Sam.

This module is intentionally project-agnostic. It does not know about any
specific app, game, or feature name. Sam can use it to inspect source files for
caller-provided patterns and optionally apply caller-provided replacements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult
from workers import worker_monitor


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
}


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


class CodebaseCleanupService:
    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root).resolve()

    def inspect(self, patterns: list[str]) -> tuple[SamResult, CodebaseCleanupReport]:
        task = worker_monitor.create_task(
            name="inspect_codebase_patterns",
            worker_type="codebase",
            worker_name="Inspector",
            description="Inspect codebase for requested patterns.",
        )
        worker_monitor.mark_running(task.task_id)

        report = CodebaseCleanupReport()
        clean_patterns = [item for item in (p.strip() for p in patterns) if item]
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

        for path in self._iter_source_files():
            report.scanned_files += 1
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            rel_path = path.relative_to(self.repo_root).as_posix()
            for line_number, line in enumerate(lines, start=1):
                lowered = line.lower()
                for pattern in clean_patterns:
                    if pattern.lower() in lowered:
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
        worker_monitor.mark_done(task.task_id)
        return (
            SamResult(
                status="success",
                summary=f"Codebase inspection found {len(report.matches)} match(es).",
                next_action="stop",
                metadata={
                    "scanned_files": report.scanned_files,
                    "match_count": len(report.matches),
                    "matches": [match.__dict__ for match in report.matches[:100]],
                    "worker_updates": [
                        f"Inspector scanned {report.scanned_files} file(s).",
                        f"Inspector found {len(report.matches)} match(es).",
                    ],
                },
            ),
            report,
        )

    def replace(self, replacements: dict[str, str]) -> tuple[SamResult, CodebaseCleanupReport]:
        task = worker_monitor.create_task(
            name="replace_codebase_patterns",
            worker_type="codebase",
            worker_name="Refactorer",
            description="Apply requested codebase replacements.",
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

        for path in self._iter_source_files():
            report.scanned_files += 1
            try:
                original = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            updated = original
            for old, new in clean_replacements.items():
                updated = updated.replace(old, new)
            if updated != original:
                path.write_text(updated, encoding="utf-8")
                rel_path = path.relative_to(self.repo_root).as_posix()
                report.changed_files.append(rel_path)
                worker_monitor.append_output(task.task_id, f"Updated {rel_path}")

        worker_monitor.append_output(task.task_id, f"Scanned {report.scanned_files} file(s).")
        worker_monitor.append_output(task.task_id, f"Changed {len(report.changed_files)} file(s).")
        worker_monitor.mark_done(task.task_id)
        return (
            SamResult(
                status="success",
                summary=f"Codebase cleanup changed {len(report.changed_files)} file(s).",
                next_action="stop",
                metadata={
                    "scanned_files": report.scanned_files,
                    "changed_files": report.changed_files,
                    "worker_updates": [
                        f"Refactorer scanned {report.scanned_files} file(s).",
                        f"Refactorer changed {len(report.changed_files)} file(s).",
                    ],
                },
            ),
            report,
        )

    def _iter_source_files(self):
        for path in self.repo_root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(self.repo_root).as_posix()
            if any(rel == ignored or rel.startswith(f"{ignored}/") for ignored in DEFAULT_IGNORED_DIRS):
                continue
            if path.suffix.lower() not in DEFAULT_EXTENSIONS:
                continue
            yield path

"""Real git diff summarization helpers for Sam v2."""

from __future__ import annotations

from dataclasses import dataclass

from sam_v2.diagnostics.error_types import ErrorType
from sam_v2.diagnostics.result import SamResult


@dataclass
class DiffFileSummary:
    path: str
    added_lines: int
    removed_lines: int


@dataclass
class DiffSummary:
    files: list[DiffFileSummary]
    total_files: int
    total_added_lines: int
    total_removed_lines: int
    text: str


class DiffSummaryService:
    def summarize(self, diff_text: str) -> tuple[SamResult, DiffSummary | None]:
        text = diff_text.strip("\n")
        if not text.strip():
            return (
                SamResult(
                    status="failed",
                    summary="Diff text is required.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="empty diff",
                    next_action="ask_user",
                ),
                None,
            )

        file_summaries: list[DiffFileSummary] = []
        current_path = ""
        added_lines = 0
        removed_lines = 0

        def flush_current() -> None:
            nonlocal current_path, added_lines, removed_lines
            if current_path:
                file_summaries.append(
                    DiffFileSummary(
                        path=current_path,
                        added_lines=added_lines,
                        removed_lines=removed_lines,
                    )
                )
            current_path = ""
            added_lines = 0
            removed_lines = 0

        for raw_line in text.splitlines():
            line = raw_line.rstrip("\n")
            if line.startswith("diff --git "):
                flush_current()
                continue
            if line.startswith("+++ b/"):
                flush_current()
                current_path = line[6:]
                continue
            if not current_path:
                continue
            if line.startswith("@@") or line.startswith("--- ") or line.startswith("index "):
                continue
            if line.startswith("+") and not line.startswith("+++"):
                added_lines += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed_lines += 1

        flush_current()

        if not file_summaries:
            return (
                SamResult(
                    status="failed",
                    summary="Could not extract any file changes from the diff.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="no changed files parsed",
                    next_action="ask_user",
                ),
                None,
            )

        total_files = len(file_summaries)
        total_added = sum(item.added_lines for item in file_summaries)
        total_removed = sum(item.removed_lines for item in file_summaries)

        file_fragments = [
            f"{item.path} (+{item.added_lines}/-{item.removed_lines})"
            for item in file_summaries
        ]
        summary_text = (
            f"Updated {total_files} file(s): {', '.join(file_fragments)}. "
            f"Total line changes: +{total_added}/-{total_removed}."
        )

        return (
            SamResult(
                status="success",
                summary="Diff summary generated.",
                next_action="stop",
                metadata={
                    "total_files": total_files,
                    "total_added_lines": total_added,
                    "total_removed_lines": total_removed,
                },
            ),
            DiffSummary(
                files=file_summaries,
                total_files=total_files,
                total_added_lines=total_added,
                total_removed_lines=total_removed,
                text=summary_text,
            ),
        )

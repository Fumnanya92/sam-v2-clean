"""Minimal safe local file, directory, command, and git inspection tools."""

from __future__ import annotations

import json
import os
import html
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult
from storage.db import log_audit_event
from storage.models import AuditEvent


@dataclass
class GitStatusSnapshot:
    repo_root: str
    branch: str
    is_clean: bool
    changed_files: list[str]
    staged_files: list[str]
    unstaged_files: list[str]
    untracked_files: list[str]


class SafeLocalTools:
    """Restricted local tools for low-risk inspection tasks."""

    SAFE_COMMANDS = {"git", "rg"}

    def __init__(self, *, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else None

    @staticmethod
    def resolve_directory_query(query: str | Path) -> tuple[SamResult, Path | None]:
        raw = _clean_path_query(query)
        if not raw:
            return (
                SamResult(
                    status="failed",
                    summary="Directory query is required.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="empty directory query",
                    next_action="ask_user",
                ),
                None,
            )

        expanded = Path(os.path.expandvars(raw)).expanduser()
        candidate_paths: list[Path] = []
        if expanded.is_absolute():
            candidate_paths.append(expanded)
        else:
            candidate_paths.append(Path.cwd() / expanded)
            candidate_paths.append(Path.home() / expanded)

        normalized = raw.lower().strip().replace("\\", "/").rstrip("/").strip()
        alias_map = {
            "downloads": Path.home() / "Downloads",
            "download": Path.home() / "Downloads",
            "documents": Path.home() / "Documents",
            "document": Path.home() / "Documents",
            "desktop": Path.home() / "Desktop",
            "pictures": Path.home() / "Pictures",
            "music": Path.home() / "Music",
            "videos": Path.home() / "Videos",
            "sam-agent": Path.cwd(),
            "sam agent": Path.cwd(),
            "sam_v2": Path.cwd() / "sam_v2",
            "sam v2": Path.cwd() / "sam_v2",
        }
        aliased = alias_map.get(normalized)
        if aliased is not None:
            candidate_paths.insert(0, aliased)

        search_name = raw.strip().lower()
        search_roots = [
            Path.cwd(),
            Path.cwd().parent,
            Path.cwd().parent.parent,
            Path.home(),
            Path.home() / "Desktop",
            Path.home() / "Documents",
            Path.home() / "Downloads",
        ]
        for root in search_roots:
            try:
                if not root.exists() or not root.is_dir():
                    continue
                for child in root.iterdir():
                    if child.is_dir() and child.name.lower() == search_name:
                        candidate_paths.append(child)
            except OSError:
                continue

        seen: set[str] = set()
        for candidate in candidate_paths:
            candidate_str = str(candidate)
            if candidate_str in seen:
                continue
            seen.add(candidate_str)
            if candidate.exists() and candidate.is_dir():
                return (
                    SamResult(
                        status="success",
                        summary="Directory resolved successfully.",
                        next_action="stop",
                        metadata={"path": str(candidate), "query": raw},
                    ),
                    candidate,
                )

        return (
            SamResult(
                status="failed",
                summary="Requested directory could not be resolved.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=raw,
                next_action="ask_user",
                metadata={"query": raw},
            ),
            None,
        )

    def read_text_file(self, path: str | Path, *, max_chars: int = 4000) -> tuple[SamResult, str | None]:
        target = Path(path)
        try:
            if not target.exists() or not target.is_file():
                return (
                    SamResult(
                        status="failed",
                        summary="Requested file does not exist.",
                        error_type=ErrorType.FILE_ACCESS_ERROR,
                        error_message=str(target),
                        next_action="ask_user",
                    ),
                    None,
                )
            content = target.read_text(encoding="utf-8")
            snippet = content[:max_chars]
            self._audit(
                event_type="tool_file_read",
                summary=f"Read file {target.name}",
                metadata={"path": str(target), "max_chars": max_chars},
            )
            return (
                SamResult(
                    status="success",
                    summary="File read succeeded.",
                    next_action="stop",
                    metadata={"path": str(target), "chars_returned": len(snippet)},
                ),
                snippet,
            )
        except UnicodeDecodeError as exc:
            return (
                SamResult(
                    status="failed",
                    summary="File is not valid UTF-8 text.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="ask_user",
                    metadata={"path": str(target)},
                ),
                None,
            )
        except OSError as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Failed to read file.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="retry",
                    metadata={"path": str(target)},
                ),
                None,
            )

    @staticmethod
    def resolve_file_query(
        query: str | Path,
        *,
        additional_roots: list[str | Path] | None = None,
    ) -> tuple[SamResult, Path | None]:
        raw = _clean_path_query(query)
        if not raw:
            return (
                SamResult(
                    status="failed",
                    summary="File query is required.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="empty file query",
                    next_action="ask_user",
                ),
                None,
            )

        expanded = Path(os.path.expandvars(raw)).expanduser()
        candidate_paths: list[Path] = []
        if expanded.is_absolute():
            candidate_paths.append(expanded)
        else:
            candidate_paths.append(Path.cwd() / expanded)
            candidate_paths.append(Path.home() / expanded)

        roots: list[Path] = [Path.cwd(), Path.cwd() / "sam_v2", Path.home(), Path.home() / "Desktop", Path.home() / "Documents"]
        if additional_roots:
            roots = [Path(root) for root in additional_roots] + roots

        for root in roots:
            candidate_paths.append(root / raw)

        search_name = Path(raw).name.lower()
        for root in roots:
            try:
                if not root.exists() or not root.is_dir():
                    continue
                for child in root.iterdir():
                    if child.is_file() and child.name.lower() == search_name:
                        candidate_paths.append(child)
            except OSError:
                continue

        seen: set[str] = set()
        for candidate in candidate_paths:
            candidate_str = str(candidate)
            if candidate_str in seen:
                continue
            seen.add(candidate_str)
            if candidate.exists() and candidate.is_file():
                return (
                    SamResult(
                        status="success",
                        summary="File resolved successfully.",
                        next_action="stop",
                        metadata={"path": str(candidate), "query": raw},
                    ),
                    candidate,
                )

        return (
            SamResult(
                status="failed",
                summary="Requested file could not be resolved.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=raw,
                next_action="ask_user",
                metadata={"query": raw},
            ),
            None,
        )

    def list_directory(self, path: str | Path) -> tuple[SamResult, list[str]]:
        target = Path(_clean_path_query(path))
        try:
            if not target.exists() or not target.is_dir():
                return (
                    SamResult(
                        status="failed",
                        summary="Requested directory does not exist.",
                        error_type=ErrorType.FILE_ACCESS_ERROR,
                        error_message=str(target),
                        next_action="ask_user",
                    ),
                    [],
                )
            entries = sorted(item.name for item in target.iterdir())
            self._audit(
                event_type="tool_directory_listed",
                summary=f"Listed directory {target.name}",
                metadata={"path": str(target), "entry_count": len(entries)},
            )
            return (
                SamResult(
                    status="success",
                    summary="Directory listing succeeded.",
                    next_action="stop",
                    metadata={"path": str(target), "entry_count": len(entries)},
                ),
                entries,
            )
        except OSError as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Failed to list directory.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="retry",
                    metadata={"path": str(target)},
                ),
                [],
            )

    def open_directory(self, path: str | Path) -> SamResult:
        target = Path(_clean_path_query(path))
        try:
            if not target.exists() or not target.is_dir():
                return SamResult(
                    status="failed",
                    summary="Requested directory does not exist.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(target),
                    next_action="ask_user",
                )
            if not hasattr(os, "startfile"):
                return SamResult(
                    status="failed",
                    summary="Directory opening is not supported on this platform.",
                    error_type=ErrorType.MISSING_CAPABILITY,
                    error_message="os.startfile unavailable",
                    next_action="stop",
                    metadata={"path": str(target)},
                )
            os.startfile(str(target))
            self._audit(
                event_type="tool_directory_opened",
                summary=f"Opened directory {target.name}",
                metadata={"path": str(target)},
            )
            return SamResult(
                status="success",
                summary="Directory opened successfully.",
                next_action="stop",
                metadata={"path": str(target)},
            )
        except OSError as exc:
            return SamResult(
                status="failed",
                summary="Failed to open directory.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="retry",
                metadata={"path": str(target)},
            )

    def open_directory_query(self, query: str | Path) -> SamResult:
        resolve_result, target = self.resolve_directory_query(query)
        if not resolve_result.ok or target is None:
            return resolve_result
        open_result = self.open_directory(target)
        open_result.metadata.setdefault("query", str(query).strip())
        return open_result

    def open_file(self, path: str | Path) -> SamResult:
        target = Path(_clean_path_query(path))
        try:
            if not target.exists() or not target.is_file():
                return SamResult(
                    status="failed",
                    summary="Requested file does not exist.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(target),
                    next_action="ask_user",
                )
            if not hasattr(os, "startfile"):
                return SamResult(
                    status="failed",
                    summary="File opening is not supported on this platform.",
                    error_type=ErrorType.MISSING_CAPABILITY,
                    error_message="os.startfile unavailable",
                    next_action="stop",
                    metadata={"path": str(target)},
                )
            os.startfile(str(target))
            self._audit(
                event_type="tool_file_opened",
                summary=f"Opened file {target.name}",
                metadata={"path": str(target)},
            )
            return SamResult(
                status="success",
                summary="File opened successfully.",
                next_action="stop",
                metadata={"path": str(target)},
            )
        except OSError as exc:
            return SamResult(
                status="failed",
                summary="Failed to open file.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="retry",
                metadata={"path": str(target)},
            )

    def open_file_query(
        self,
        query: str | Path,
        *,
        additional_roots: list[str | Path] | None = None,
    ) -> SamResult:
        resolve_result, target = self.resolve_file_query(query, additional_roots=additional_roots)
        if not resolve_result.ok or target is None:
            return resolve_result
        open_result = self.open_file(target)
        open_result.metadata.setdefault("query", str(query).strip())
        return open_result

    def run_safe_command(
        self,
        command: list[str],
        *,
        cwd: str | Path | None = None,
        timeout_seconds: int = 15,
    ) -> tuple[SamResult, dict[str, str | int | list[str]]]:
        if not command:
            return (
                SamResult(
                    status="failed",
                    summary="Command is required.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="empty command",
                    next_action="ask_user",
                ),
                {},
            )

        executable = Path(command[0]).name.lower()
        if executable not in self.SAFE_COMMANDS:
            return (
                SamResult(
                    status="blocked",
                    summary="Command is not allowed by the safe local tools policy.",
                    error_type=ErrorType.MISSING_PERMISSION,
                    error_message=command[0],
                    next_action="ask_user",
                    metadata={"allowed_commands": sorted(self.SAFE_COMMANDS)},
                ),
                {},
            )

        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd) if cwd is not None else None,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            payload: dict[str, str | int | list[str]] = {
                "stdout": completed.stdout.rstrip(),
                "stderr": completed.stderr.strip(),
                "returncode": completed.returncode,
                "command": command,
            }
            if completed.returncode != 0:
                return (
                    SamResult(
                        status="failed",
                        summary="Safe command failed.",
                        error_type=ErrorType.COMMAND_FAILED,
                        error_message=completed.stderr.strip() or f"exit_code={completed.returncode}",
                        next_action="retry",
                        metadata={"command": command},
                    ),
                    payload,
                )

            self._audit(
                event_type="tool_command_executed",
                summary="Executed safe local command.",
                metadata={"command": command, "cwd": str(cwd) if cwd is not None else ""},
            )
            return (
                SamResult(
                    status="success",
                    summary="Safe command executed.",
                    next_action="stop",
                    metadata={"command": command},
                ),
                payload,
            )
        except subprocess.TimeoutExpired as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Safe command timed out.",
                    error_type=ErrorType.TIMEOUT,
                    error_message=str(exc),
                    next_action="retry",
                    metadata={"command": command},
                ),
                {},
            )
        except OSError as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Safe command could not start.",
                    error_type=ErrorType.COMMAND_FAILED,
                    error_message=str(exc),
                    next_action="ask_user",
                    metadata={"command": command},
                ),
                {},
            )

    def inspect_git_state(self, repo_path: str | Path) -> tuple[SamResult, GitStatusSnapshot | None]:
        repo = Path(_clean_path_query(repo_path))
        git_base = ["git", "-c", f"safe.directory={repo}"]
        inside_result, inside_payload = self.run_safe_command(
            [*git_base, "rev-parse", "--is-inside-work-tree"],
            cwd=repo,
        )
        if not inside_result.ok or inside_payload.get("stdout") != "true":
            return (
                SamResult(
                    status="failed",
                    summary="Path is not a git repository.",
                    error_type=ErrorType.GIT_ERROR,
                    error_message=str(repo),
                    next_action="ask_user",
                ),
                None,
            )

        branch_result, branch_payload = self.run_safe_command([*git_base, "branch", "--show-current"], cwd=repo)
        if not branch_result.ok:
            return branch_result, None

        status_result, status_payload = self.run_safe_command([*git_base, "status", "--porcelain"], cwd=repo)
        if not status_result.ok:
            return status_result, None

        staged_files: list[str] = []
        unstaged_files: list[str] = []
        untracked_files: list[str] = []
        changed_files: list[str] = []

        for line in str(status_payload.get("stdout", "")).splitlines():
            if len(line) < 3:
                continue
            index_status = line[0]
            worktree_status = line[1]
            filename = line[3:].strip().split(" -> ")[-1]
            if filename and filename not in changed_files:
                changed_files.append(filename)
            if index_status not in {" ", "?"}:
                staged_files.append(filename)
            if worktree_status not in {" ", "?"}:
                unstaged_files.append(filename)
            if index_status == "?" and worktree_status == "?":
                untracked_files.append(filename)

        snapshot = GitStatusSnapshot(
            repo_root=str(repo),
            branch=str(branch_payload.get("stdout", "")).strip(),
            is_clean=len(changed_files) == 0,
            changed_files=changed_files,
            staged_files=staged_files,
            unstaged_files=unstaged_files,
            untracked_files=untracked_files,
        )
        self._audit(
            event_type="tool_git_inspected",
            summary="Inspected git state.",
            metadata=asdict(snapshot),
        )
        return (
            SamResult(
                status="success",
                summary="Git inspection succeeded.",
                next_action="stop",
                metadata={"repo_root": snapshot.repo_root, "branch": snapshot.branch, "is_clean": snapshot.is_clean},
            ),
            snapshot,
        )

    def _audit(self, *, event_type: str, summary: str, metadata: dict[str, object]) -> None:
        if self.db_path is None:
            return
        try:
            log_audit_event(
                self.db_path,
                AuditEvent(
                    event_type=event_type,
                    actor="tools.safe_local",
                    summary=summary,
                    metadata_json=json.dumps(metadata),
                ),
            )
        except Exception:
            pass


def _clean_path_query(value: str | Path) -> str:
    cleaned = str(value).strip()
    for _ in range(5):
        unescaped = html.unescape(cleaned)
        if unescaped == cleaned:
            break
        cleaned = unescaped
    return cleaned.strip().strip("'\"`")

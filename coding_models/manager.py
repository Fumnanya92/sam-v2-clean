"""Provider-neutral bridge to external coding CLIs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult
from workers import worker_monitor


@dataclass(frozen=True)
class CodingModelStatus:
    name: str
    available: bool
    executable: str = ""


@dataclass(frozen=True)
class CodingDelegationRequest:
    goal: str
    project_root: str
    context: str = ""
    mode: str = "code"
    timeout_seconds: int = 900


class CodingModelProvider:
    name = "local"
    known_relative_paths: tuple[str, ...] = ()

    def executable(self) -> str:
        found = shutil.which(self.name)
        if found:
            return found
        for candidate in self._known_candidates():
            if candidate.exists():
                return str(candidate)
        return ""

    def status(self) -> CodingModelStatus:
        executable = self.executable()
        return CodingModelStatus(name=self.name, available=bool(executable), executable=executable)

    def command(self, root: Path) -> list[str]:
        raise NotImplementedError

    def _known_candidates(self) -> list[Path]:
        home = Path.home()
        local_app_data = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        user_profile = Path(os.environ.get("USERPROFILE", home))
        expanded: list[Path] = []
        for raw in self.known_relative_paths:
            text = raw.format(home=home, local_app_data=local_app_data, user_profile=user_profile)
            expanded.append(Path(text))
        return expanded

    def prompt(self, request: CodingDelegationRequest) -> str:
        return "\n".join(
            [
                "You are an external coding worker called by Sam.",
                "Work only inside the project root.",
                "Inspect before changing files.",
                "For investigation or data questions, read the relevant project files and return the direct answer without editing.",
                "Only change files when the user explicitly asks for a code or project modification.",
                "Do not make unrelated edits.",
                "After working, report the answer first, then what you inspected, files changed, verification run, and blockers when relevant.",
                f"Project root: {request.project_root}",
                f"Mode: {request.mode}",
                f"Goal: {request.goal}",
                f"Context: {request.context}",
            ]
        )

    def run(self, request: CodingDelegationRequest, *, task_id: str) -> SamResult:
        root = Path(request.project_root)
        if not root.exists() or not root.is_dir():
            return SamResult(
                status="failed",
                summary="Coding model delegation needs a valid project folder.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(root),
                next_action="ask_user",
                metadata={"coding_model": self.name, "root_path": str(root)},
            )

        status = self.status()
        if not status.available:
            return SamResult(
                status="failed",
                summary=f"{self.name} CLI is not available on this machine.",
                error_type=ErrorType.MISSING_CAPABILITY,
                error_message=f"{self.name} command not found",
                next_action="ask_user",
                metadata={"coding_model": self.name},
            )

        before = _git_diff(root)
        command = self.command(root)
        prompt = self.prompt(request)
        worker_monitor.append_output(task_id, f"Provider: {self.name}")
        worker_monitor.append_output(task_id, f"Folder: {root}")
        worker_monitor.append_output(task_id, "Starting external coding model...")
        output_lines: list[str] = []
        started = time.time()
        try:
            process = subprocess.Popen(
                command,
                cwd=str(root),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=os.environ.copy(),
            )
            if process.stdin:
                process.stdin.write(prompt)
                process.stdin.close()
            assert process.stdout is not None
            for raw in process.stdout:
                line = raw.rstrip()
                if not line:
                    continue
                output_lines.append(line)
                worker_monitor.append_output(task_id, line[:700])
                if time.time() - started > request.timeout_seconds:
                    process.kill()
                    raise subprocess.TimeoutExpired(command, request.timeout_seconds)
            returncode = process.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            return SamResult(
                status="failed",
                summary=f"{self.name} timed out before completing the coding task.",
                error_type=ErrorType.COMMAND_FAILED,
                error_message=str(exc),
                next_action="retry",
                metadata={"coding_model": self.name, "root_path": str(root), "command": command},
            )
        except OSError as exc:
            return SamResult(
                status="failed",
                summary=f"{self.name} could not start.",
                error_type=ErrorType.COMMAND_FAILED,
                error_message=str(exc),
                next_action="ask_user",
                metadata={"coding_model": self.name, "root_path": str(root), "command": command},
            )

        after = _git_diff(root)
        changed = _git_changed_files(root)
        summary = _coding_answer_summary(output_lines) or f"{self.name} completed."
        metadata = {
            "coding_model": self.name,
            "root_path": str(root),
            "command": command,
            "stdout": "\n".join(output_lines[-120:]),
            "changed_files": changed,
            "diff_changed": before != after,
            "duration_seconds": int(time.time() - started),
        }
        if returncode != 0:
            return SamResult(
                status="failed",
                summary=f"{self.name} failed while handling the coding task.",
                error_type=ErrorType.COMMAND_FAILED,
                error_message=summary,
                next_action="retry",
                metadata=metadata,
            )
        return SamResult(
            status="success",
            summary=summary,
            next_action="stop",
            metadata=metadata,
        )


class CodexCliProvider(CodingModelProvider):
    name = "codex"
    known_relative_paths = (
        r"{local_app_data}\OpenAI\Codex\bin\codex.exe",
        r"{user_profile}\AppData\Local\OpenAI\Codex\bin\codex.exe",
    )

    def command(self, root: Path) -> list[str]:
        return [
            self.executable() or "codex",
            "exec",
            "--cd",
            str(root),
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            "-",
        ]


class ClaudeCliProvider(CodingModelProvider):
    name = "claude"
    known_relative_paths = (
        r"{user_profile}\.local\bin\claude.exe",
        r"{home}\.local\bin\claude.exe",
    )

    def command(self, root: Path) -> list[str]:
        return [
            self.executable() or "claude",
            "--print",
            "--permission-mode",
            "acceptEdits",
            "--output-format",
            "text",
            "-",
        ]


class CodingModelManager:
    def __init__(self, settings_path: str | Path) -> None:
        self.settings_path = Path(settings_path)
        self.providers: dict[str, CodingModelProvider] = {
            "codex": CodexCliProvider(),
            "claude": ClaudeCliProvider(),
        }

    def active_model(self) -> str:
        data = self._read_settings()
        active = str(data.get("active_model", "local") or "local").strip().lower()
        return active if active in {*self.providers.keys(), "local"} else "local"

    def set_active_model(self, name: str) -> SamResult:
        normalized = name.strip().lower()
        if normalized not in {*self.providers.keys(), "local"}:
            return SamResult(
                status="failed",
                summary=f"Unknown coding model: {name}.",
                error_type=ErrorType.MISSING_CAPABILITY,
                error_message=name,
                next_action="ask_user",
                metadata={"available_models": self.available_model_names()},
            )
        if normalized != "local" and not self.providers[normalized].status().available:
            return SamResult(
                status="failed",
                summary=f"{normalized} is selected but its CLI is not available.",
                error_type=ErrorType.MISSING_CAPABILITY,
                error_message=f"{normalized} command not found",
                next_action="ask_user",
                metadata=self.status_metadata(active_override=normalized),
            )
        self._write_settings({"active_model": normalized})
        return SamResult(
            status="success",
            summary=f"Coding model set to {normalized}.",
            next_action="stop",
            metadata=self.status_metadata(active_override=normalized),
        )

    def status_result(self) -> SamResult:
        active = self.active_model()
        return SamResult(
            status="success",
            summary=f"Active coding model: {active}.",
            next_action="stop",
            metadata=self.status_metadata(active_override=active),
        )

    def delegate(self, request: CodingDelegationRequest) -> SamResult:
        active = self.active_model()
        if active == "local":
            return SamResult(
                status="failed",
                summary="External coding delegation is off. Use /codex or /claude to enable it.",
                error_type=ErrorType.MISSING_CAPABILITY,
                next_action="ask_user",
                metadata=self.status_metadata(active_override=active),
            )
        provider = self.providers[active]
        task = worker_monitor.create_task(
            name="delegate_coding_task",
            worker_type="implementation",
            worker_name="Forge",
            worker_role="implementation",
            responsibility="external coding model delegation",
            description=f"Delegating repo/code task to {active}",
        )
        worker_monitor.mark_running(task.task_id)
        try:
            result = provider.run(request, task_id=task.task_id)
        finally:
            latest = worker_monitor.get_task(task.task_id)
            if latest and latest.status == "running":
                worker_monitor.mark_done(task.task_id)
        if result.ok:
            worker_monitor.mark_done(task.task_id)
        else:
            worker_monitor.mark_failed(task.task_id, result.error_message or result.summary)
        result.metadata.setdefault("worker_name", "Forge")
        result.metadata.setdefault("coding_model", active)
        result.metadata.setdefault("task_id", task.task_id)
        result.metadata.update({k: v for k, v in self.status_metadata(active_override=active).items() if k not in result.metadata})
        return result

    def status_metadata(self, *, active_override: str | None = None) -> dict[str, object]:
        statuses = [status.__dict__ for status in self.statuses()]
        return {
            "active_coding_model": active_override or self.active_model(),
            "coding_models": statuses,
        }

    def statuses(self) -> list[CodingModelStatus]:
        return [provider.status() for provider in self.providers.values()]

    def available_model_names(self) -> list[str]:
        return ["local", *self.providers.keys()]

    def _read_settings(self) -> dict[str, object]:
        if not self.settings_path.exists():
            return {}
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_settings(self, data: dict[str, object]) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _git_diff(root: Path) -> str:
    return _run_git(root, ["git", "diff", "--", "."])


def _git_changed_files(root: Path) -> list[str]:
    output = _run_git(root, ["git", "status", "--short"])
    return [line.strip() for line in output.splitlines() if line.strip()]


def _run_git(root: Path, command: Iterable[str]) -> str:
    try:
        completed = subprocess.run(
            list(command),
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=20,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout if completed.returncode == 0 else ""


def _coding_answer_summary(lines: list[str]) -> str:
    answer = _extract_answer_block(lines)
    if answer:
        return answer[:2000]
    for line in reversed(lines):
        stripped = line.strip()
        if stripped and not _is_status_line(stripped):
            return stripped[:1000]
    return ""


def _extract_answer_block(lines: list[str]) -> str:
    cleaned = [_strip_terminal_line(line) for line in lines]
    starts = [
        index
        for index, line in enumerate(cleaned)
        if line.lower().startswith(("answer:", "final answer:", "result:"))
    ]
    if not starts:
        starts = [
            index
            for index, line in enumerate(cleaned)
            if line and not _is_status_line(line) and not _looks_like_tool_noise(line)
        ]
    for start in reversed(starts):
        block: list[str] = []
        for line in cleaned[start:]:
            if not line:
                if block:
                    break
                continue
            if block and _is_section_after_answer(line):
                break
            if _looks_like_tool_noise(line):
                if block:
                    break
                continue
            block.append(line)
        text = " ".join(block).strip()
        if text and not _is_status_line(text):
            return text
    return ""


def _strip_terminal_line(line: str) -> str:
    stripped = line.strip()
    if stripped.lower() in {"codex", "claude"}:
        return ""
    return stripped


def _is_section_after_answer(line: str) -> bool:
    lowered = line.lower()
    return lowered.startswith(
        (
            "inspected:",
            "files changed:",
            "verification run:",
            "blockers:",
            "what i inspected:",
        )
    )


def _is_status_line(line: str) -> bool:
    lowered = line.lower().strip()
    return (
        lowered in {"finished.", "done.", "completed."}
        or lowered.startswith("verification run:")
        or lowered.startswith("files changed:")
        or lowered.startswith("tool succeeded:")
        or lowered.startswith("result: verification run:")
    )


def _looks_like_tool_noise(line: str) -> bool:
    lowered = line.lower().strip()
    return (
        lowered.startswith(("exec ", "wall time:", "exit code:", "output:", "error:", "warning:"))
        or " codex_core::" in lowered
        or " openai codex " in lowered
        or lowered.startswith(("workdir:", "model:", "provider:", "approval:", "sandbox:", "session id:"))
        or lowered in {"--------", "user"}
    )

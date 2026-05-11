"""Failure understanding helpers for real project command output."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from sam_v2.diagnostics.error_types import ErrorType
from sam_v2.diagnostics.result import SamResult


@dataclass
class CommandFailureAnalysis:
    project_id: str
    command: list[str]
    cwd: str
    returncode: int
    category: str
    explanation: str
    suggested_next_action: str
    evidence_lines: list[str]
    stdout: str
    stderr: str


def resolve_flutter_command() -> str:
    configured = os.getenv("SAM_V2_FLUTTER_BIN")
    if configured:
        return configured
    windows_default = Path(r"C:\flutter\bin\flutter.bat")
    if windows_default.exists():
        return str(windows_default)
    return "flutter"


class FailureAnalysisService:
    def run_command(
        self,
        *,
        project_id: str,
        command: list[str],
        cwd: str | Path,
        timeout_seconds: int = 180,
    ) -> tuple[SamResult, CommandFailureAnalysis | None]:
        target = Path(cwd)
        try:
            completed = subprocess.run(
                command,
                cwd=str(target),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            analysis = CommandFailureAnalysis(
                project_id=project_id,
                command=command,
                cwd=str(target),
                returncode=-1,
                category="timeout",
                explanation="The command timed out before it completed.",
                suggested_next_action="retry",
                evidence_lines=[str(exc)],
                stdout="",
                stderr=str(exc),
            )
            return (
                SamResult(
                    status="failed",
                    summary="Project command timed out.",
                    error_type=ErrorType.TIMEOUT,
                    error_message=str(exc),
                    next_action="retry",
                    metadata={"project_id": project_id, "category": analysis.category},
                ),
                analysis,
            )
        except OSError as exc:
            analysis = CommandFailureAnalysis(
                project_id=project_id,
                command=command,
                cwd=str(target),
                returncode=-1,
                category="command_start_failed",
                explanation="The command could not be started in this environment.",
                suggested_next_action="ask_user",
                evidence_lines=[str(exc)],
                stdout="",
                stderr=str(exc),
            )
            return (
                SamResult(
                    status="failed",
                    summary="Project command could not start.",
                    error_type=ErrorType.COMMAND_FAILED,
                    error_message=str(exc),
                    next_action="ask_user",
                    metadata={"project_id": project_id, "category": analysis.category},
                ),
                analysis,
            )

        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        combined = "\n".join(line for line in [stdout, stderr] if line)

        if completed.returncode == 0:
            return (
                SamResult(
                    status="success",
                    summary="Project command succeeded.",
                    next_action="stop",
                    metadata={"project_id": project_id, "returncode": completed.returncode},
                ),
                None,
            )

        analysis = self._analyze_failure(
            project_id=project_id,
            command=command,
            cwd=target,
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            combined_output=combined,
        )
        return (
            SamResult(
                status="failed",
                summary=analysis.explanation,
                error_type=ErrorType.TEST_FAILED,
                error_message=stderr or stdout or f"exit_code={completed.returncode}",
                next_action=analysis.suggested_next_action,
                metadata={
                    "project_id": project_id,
                    "category": analysis.category,
                    "evidence_lines": analysis.evidence_lines,
                    "returncode": completed.returncode,
                },
            ),
            analysis,
        )

    def _analyze_failure(
        self,
        *,
        project_id: str,
        command: list[str],
        cwd: Path,
        returncode: int,
        stdout: str,
        stderr: str,
        combined_output: str,
    ) -> CommandFailureAnalysis:
        lines = [line.strip() for line in combined_output.splitlines() if line.strip()]
        lowered = combined_output.lower()

        if "no file or variants found for" in lowered:
            evidence = [line for line in lines if "No file or variants found for" in line][:2]
            return CommandFailureAnalysis(
                project_id=project_id,
                command=command,
                cwd=str(cwd),
                returncode=returncode,
                category="missing_test_target",
                explanation="Flutter could not find the requested test target in the project.",
                suggested_next_action="ask_user",
                evidence_lines=evidence or lines[:2],
                stdout=stdout,
                stderr=stderr,
            )

        if "failed to load" in lowered and "does not exist" in lowered:
            evidence = [line for line in lines if "Failed to load" in line or "Does not exist" in line][:3]
            return CommandFailureAnalysis(
                project_id=project_id,
                command=command,
                cwd=str(cwd),
                returncode=returncode,
                category="missing_test_target",
                explanation="Flutter could not load the requested test target because the file does not exist in the project.",
                suggested_next_action="ask_user",
                evidence_lines=evidence or lines[:3],
                stdout=stdout,
                stderr=stderr,
            )

        if "expected:" in lowered and "actual:" in lowered:
            evidence = [line for line in lines if "Expected:" in line or "Actual:" in line][:4]
            return CommandFailureAnalysis(
                project_id=project_id,
                command=command,
                cwd=str(cwd),
                returncode=returncode,
                category="test_assertion_failed",
                explanation="A real test assertion failed in the project test suite.",
                suggested_next_action="inspect_repo",
                evidence_lines=evidence or lines[:4],
                stdout=stdout,
                stderr=stderr,
            )

        if "target of uri doesn't exist" in lowered or "error when reading" in lowered:
            evidence = [line for line in lines if "Error" in line or "Target of URI" in line][:4]
            return CommandFailureAnalysis(
                project_id=project_id,
                command=command,
                cwd=str(cwd),
                returncode=returncode,
                category="missing_dependency_or_import",
                explanation="The project command failed because a source file or import could not be resolved.",
                suggested_next_action="inspect_repo",
                evidence_lines=evidence or lines[:4],
                stdout=stdout,
                stderr=stderr,
            )

        return CommandFailureAnalysis(
            project_id=project_id,
            command=command,
            cwd=str(cwd),
            returncode=returncode,
            category="generic_test_failure",
            explanation="The project command failed, but the output needs manual inspection for the exact cause.",
            suggested_next_action="ask_user",
            evidence_lines=lines[:4],
            stdout=stdout,
            stderr=stderr,
        )

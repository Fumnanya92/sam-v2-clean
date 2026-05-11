"""Minimal command-based tooling workers for Sam v2."""

from __future__ import annotations

import difflib
import json
import subprocess
from dataclasses import dataclass, field
from dataclasses import dataclass, field
from pathlib import Path

from approvals import ApprovalManager, AuthorityConfig, AuthorityEngine
from diagnostics.error_types import ErrorType
from diagnostics.reporting import ActionLogger, ErrorLogger, SummaryLogger
from diagnostics.result import SamResult
from diagnostics.run_logger import RunLogger
from storage.db import log_audit_event
from storage.models import AuditEvent

from .monitor import WorkerTask, worker_monitor
from .names import resolve_worker_name


def _debug_print(message: str) -> None:
    print(f"[SAM_WORKER] {message}", flush=True)


@dataclass
class CommandSpec:
    name: str
    worker_type: str
    command: list[str]
    description: str
    worker_name: str = ""
    cwd: str | Path | None = None
    timeout_seconds: int = 60
    action_category: str = "execute_command"
    environment: dict[str, str] = field(default_factory=dict)


@dataclass
class FileEditSpec:
    name: str
    worker_type: str
    target_path: str | Path
    search_text: str
    replace_text: str
    description: str
    worker_name: str = ""
    action_category: str = "write_data"


@dataclass
class FileWriteSpec:
    name: str
    worker_type: str
    target_path: str | Path
    content: str
    description: str
    worker_name: str = ""
    overwrite: bool = True
    action_category: str = "write_data"


class ToolingWorker:
    def __init__(
        self,
        *,
        db_path: str | Path,
        authority_engine: AuthorityEngine | None = None,
        approval_manager: ApprovalManager | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.authority_engine = authority_engine or AuthorityEngine(AuthorityConfig(default_level=5))
        self.approval_manager = approval_manager or ApprovalManager(self.db_path)

    def execute(self, spec: CommandSpec) -> tuple[SamResult, WorkerTask]:
        worker_name = resolve_worker_name(spec.worker_type, spec.worker_name)
        task = worker_monitor.create_task(
            name=spec.name,
            worker_type=spec.worker_type,
            worker_name=worker_name,
            description=spec.description,
        )
        run_logger = RunLogger(f"sam_v2 worker {worker_name} {spec.name}")
        action_logger = ActionLogger(f"sam_v2 worker {worker_name} {spec.name}", correlation_id=run_logger.run_id)
        error_logger = ErrorLogger(f"workers.{spec.worker_type}")
        summary_logger = SummaryLogger(f"sam_v2 worker {worker_name} {spec.name}", correlation_id=run_logger.run_id)
        run_logger.log(
            "worker_task_created",
            {
                "task_id": task.task_id,
                "worker_type": spec.worker_type,
                "worker_name": worker_name,
                "command": spec.command,
                "cwd": str(spec.cwd) if spec.cwd else "",
            },
        )
        _debug_print(
            f"Created command task task_id={task.task_id} worker={worker_name} "
            f"command={' '.join(spec.command)} cwd={str(spec.cwd) if spec.cwd else ''}"
        )
        action_logger.log(
            "worker_task_created",
            status="started",
            data={"task_id": task.task_id, "worker_name": worker_name},
        )

        decision = self.authority_engine.check(
            agent_id=f"worker:{spec.worker_type}",
            agent_level=5,
            role_id="worker",
            tool_name=spec.name,
            action_category=spec.action_category,
        )
        if not decision.allowed:
            worker_monitor.mark_failed(task.task_id, decision.reason)
            result = SamResult(
                status="blocked",
                summary="Worker command blocked by authority rules.",
                error_type=ErrorType.MISSING_PERMISSION,
                error_message=decision.reason,
                next_action="request_approval",
                metadata={"task_id": task.task_id, "worker_type": spec.worker_type},
            )
            action_logger.log("worker_blocked", status="blocked", data={"task_id": task.task_id})
            error_logger.log(
                event="worker_blocked",
                error_type=result.error_type,
                error_message=result.error_message or result.summary,
                metadata={"task_id": task.task_id, "worker_type": spec.worker_type},
            )
            summary_logger.write(result, metadata={"task_id": task.task_id})
            return (result, worker_monitor.get_task(task.task_id) or task)

        if decision.requires_approval:
            schema_result = self.approval_manager.ensure_schema()
            if not schema_result.ok:
                worker_monitor.mark_failed(task.task_id, schema_result.error_message or schema_result.summary)
                result = SamResult(
                    status="failed",
                    summary="Approval schema initialization failed.",
                    error_type=schema_result.error_type or ErrorType.FILE_ACCESS_ERROR,
                    error_message=schema_result.error_message,
                    next_action=schema_result.next_action or "retry",
                    metadata={"task_id": task.task_id},
                )
                error_logger.log(
                    event="approval_schema_failed",
                    error_type=result.error_type,
                    error_message=result.error_message or result.summary,
                    metadata={"task_id": task.task_id},
                )
                summary_logger.write(result, metadata={"task_id": task.task_id})
                return (result, worker_monitor.get_task(task.task_id) or task)

            create_result, approval = self.approval_manager.create_request(
                agent_id=f"worker:{spec.worker_type}",
                agent_name=f"{worker_name} ({spec.worker_type})",
                tool_name=spec.name,
                tool_arguments={
                    "command": spec.command,
                    "cwd": str(spec.cwd) if spec.cwd else "",
                    "worker_type": spec.worker_type,
                },
                action_category=spec.action_category,
                reason=decision.reason,
                context=spec.description,
            )
            if not create_result.ok or approval is None:
                worker_monitor.mark_failed(task.task_id, create_result.error_message or create_result.summary)
                result = SamResult(
                    status="failed",
                    summary="Approval request creation failed.",
                    error_type=create_result.error_type or ErrorType.FILE_ACCESS_ERROR,
                    error_message=create_result.error_message,
                    next_action=create_result.next_action or "retry",
                    metadata={"task_id": task.task_id},
                )
                error_logger.log(
                    event="approval_request_failed",
                    error_type=result.error_type,
                    error_message=result.error_message or result.summary,
                    metadata={"task_id": task.task_id},
                )
                summary_logger.write(result, metadata={"task_id": task.task_id})
                return (result, worker_monitor.get_task(task.task_id) or task)

            worker_monitor.mark_needs_approval(task.task_id, decision.reason)
            run_logger.log("worker_needs_approval", {"task_id": task.task_id, "approval_id": approval.id})
            result = SamResult(
                status="needs_approval",
                summary="Worker command requires approval.",
                error_type=ErrorType.MISSING_PERMISSION,
                error_message=decision.reason,
                next_action="request_approval",
                metadata={"task_id": task.task_id, "approval_id": approval.id},
            )
            action_logger.log(
                "worker_needs_approval",
                status="needs_approval",
                data={"task_id": task.task_id, "worker_name": worker_name},
            )
            summary_logger.write(result, metadata={"task_id": task.task_id})
            return (result, worker_monitor.get_task(task.task_id) or task)

        worker_monitor.mark_running(task.task_id)
        run_logger.log("worker_started", {"task_id": task.task_id, "worker_name": worker_name})
        worker_monitor.append_output(task.task_id, f"{worker_name} is executing a command.")
        worker_monitor.append_output(task.task_id, f"Command: {' '.join(spec.command)}")
        if spec.cwd:
            worker_monitor.append_output(task.task_id, f"Folder: {spec.cwd}")
        _debug_print(
            f"Starting command task task_id={task.task_id} worker={worker_name} "
            f"command={' '.join(spec.command)} cwd={str(spec.cwd) if spec.cwd else ''}"
        )
        action_logger.log(
            "worker_started",
            status="running",
            data={"task_id": task.task_id, "worker_name": worker_name},
        )

        writable_result = self._ensure_workdir_writable(spec.cwd)
        if writable_result is not None:
            worker_monitor.mark_failed(task.task_id, writable_result.error_message or writable_result.summary)
            run_logger.log(
                "worker_workdir_blocked",
                {"task_id": task.task_id, "cwd": str(spec.cwd) if spec.cwd else ""},
            )
            error_logger.log(
                event="worker_workdir_blocked",
                error_type=writable_result.error_type,
                error_message=writable_result.error_message or writable_result.summary,
                metadata={"task_id": task.task_id, "cwd": str(spec.cwd) if spec.cwd else ""},
            )
            summary_logger.write(writable_result, metadata={"task_id": task.task_id})
            return (writable_result, worker_monitor.get_task(task.task_id) or task)

        try:
            completed = subprocess.run(
                spec.command,
                cwd=str(spec.cwd) if spec.cwd else None,
                capture_output=True,
                text=True,
                timeout=spec.timeout_seconds,
                env=None if not spec.environment else spec.environment,
            )
            output = completed.stdout.strip()
            error_output = completed.stderr.strip()
            _debug_print(
                f"Finished command task task_id={task.task_id} returncode={completed.returncode} "
                f"stdout={output!r} stderr={error_output!r}"
            )

            for line in [*output.splitlines(), *error_output.splitlines()]:
                if line.strip():
                    worker_monitor.append_output(task.task_id, line.strip())

            if completed.returncode != 0:
                error_type = ErrorType.TEST_FAILED if spec.worker_type == "test" else ErrorType.COMMAND_FAILED
                worker_monitor.mark_failed(task.task_id, error_output or f"exit_code={completed.returncode}")
                run_logger.log(
                    "worker_failed",
                    {
                        "task_id": task.task_id,
                        "returncode": completed.returncode,
                        "stderr": error_output[:500],
                    },
                )
                result = SamResult(
                    status="failed",
                    summary="Worker command failed.",
                    error_type=error_type,
                    error_message=error_output or f"exit_code={completed.returncode}",
                    next_action="retry",
                    metadata={
                        "task_id": task.task_id,
                        "worker_type": spec.worker_type,
                        "worker_name": worker_name,
                        "returncode": completed.returncode,
                        "stdout": output,
                    },
                )
                action_logger.log(
                    "worker_failed",
                    status="failed",
                    data={"task_id": task.task_id, "worker_name": worker_name},
                )
                error_logger.log(
                    event="worker_failed",
                    error_type=result.error_type,
                    error_message=result.error_message or result.summary,
                    metadata={"task_id": task.task_id, "returncode": completed.returncode},
                )
                summary_logger.write(result, metadata={"task_id": task.task_id})
                return (result, worker_monitor.get_task(task.task_id) or task)

            audit_result, audit_id = log_audit_event(
                self.db_path,
                AuditEvent(
                    event_type="worker_command_executed",
                    actor=f"workers.{worker_name.lower()}",
                    summary=spec.description,
                    metadata_json=json.dumps(
                        {
                            "task_id": task.task_id,
                            "command": spec.command,
                            "cwd": str(spec.cwd) if spec.cwd else "",
                            "worker_type": spec.worker_type,
                            "worker_name": worker_name,
                        }
                    ),
                ),
            )
            worker_monitor.mark_done(task.task_id)
            run_logger.log(
                "worker_completed",
                {
                    "task_id": task.task_id,
                    "audit_id": audit_id,
                    "audit_status": audit_result.status,
                },
            )
            summary = output.splitlines()[-1] if output else f"{spec.worker_type} worker completed."
            result = SamResult(
                status="success",
                summary=summary,
                next_action="stop",
                metadata={
                    "task_id": task.task_id,
                    "worker_type": spec.worker_type,
                    "worker_name": worker_name,
                    "stdout": output,
                    "audit_event_id": audit_id,
                },
            )
            action_logger.log(
                "worker_completed",
                status="success",
                data={"task_id": task.task_id, "audit_event_id": audit_id, "worker_name": worker_name},
            )
            _debug_print(f"Command task succeeded task_id={task.task_id} worker={worker_name}")
            summary_logger.write(result, metadata={"task_id": task.task_id})
            return (result, worker_monitor.get_task(task.task_id) or task)
        except subprocess.TimeoutExpired as exc:
            worker_monitor.mark_failed(task.task_id, f"timed out after {spec.timeout_seconds}s")
            run_logger.log("worker_timeout", {"task_id": task.task_id})
            result = SamResult(
                status="failed",
                summary="Worker command timed out.",
                error_type=ErrorType.TIMEOUT,
                error_message=str(exc),
                next_action="retry",
                metadata={"task_id": task.task_id, "worker_type": spec.worker_type, "worker_name": worker_name},
            )
            error_logger.log(
                event="worker_timeout",
                error_type=result.error_type,
                error_message=result.error_message or result.summary,
                metadata={"task_id": task.task_id},
            )
            _debug_print(f"Command task timed out task_id={task.task_id} worker={worker_name} error={exc!r}")
            summary_logger.write(result, metadata={"task_id": task.task_id})
            return (result, worker_monitor.get_task(task.task_id) or task)
        except OSError as exc:
            worker_monitor.mark_failed(task.task_id, str(exc))
            run_logger.log("worker_os_error", {"task_id": task.task_id, "error": str(exc)})
            result = SamResult(
                status="failed",
                summary="Worker command could not start.",
                error_type=ErrorType.COMMAND_FAILED,
                error_message=str(exc),
                next_action="ask_user",
                metadata={"task_id": task.task_id, "worker_type": spec.worker_type, "worker_name": worker_name},
            )
            error_logger.log(
                event="worker_os_error",
                error_type=result.error_type,
                error_message=result.error_message or result.summary,
                metadata={"task_id": task.task_id},
            )
            _debug_print(f"Command task OS error task_id={task.task_id} worker={worker_name} error={exc!r}")
            summary_logger.write(result, metadata={"task_id": task.task_id})
            return (result, worker_monitor.get_task(task.task_id) or task)

    def execute_edit(self, spec: FileEditSpec) -> tuple[SamResult, WorkerTask]:
        worker_name = resolve_worker_name(spec.worker_type, spec.worker_name)
        task = worker_monitor.create_task(
            name=spec.name,
            worker_type=spec.worker_type,
            worker_name=worker_name,
            description=spec.description,
        )
        run_logger = RunLogger(f"sam_v2 worker {worker_name} {spec.name}")
        action_logger = ActionLogger(f"sam_v2 worker {worker_name} {spec.name}", correlation_id=run_logger.run_id)
        error_logger = ErrorLogger(f"workers.{spec.worker_type}")
        summary_logger = SummaryLogger(f"sam_v2 worker {worker_name} {spec.name}", correlation_id=run_logger.run_id)
        target_path = Path(spec.target_path)
        run_logger.log(
            "worker_edit_task_created",
            {
                "task_id": task.task_id,
                "worker_type": spec.worker_type,
                "worker_name": worker_name,
                "target_path": str(target_path),
            },
        )
        action_logger.log(
            "worker_edit_task_created",
            status="started",
            data={"task_id": task.task_id, "worker_name": worker_name},
        )

        decision = self.authority_engine.check(
            agent_id=f"worker:{spec.worker_type}",
            agent_level=5,
            role_id="worker",
            tool_name=spec.name,
            action_category=spec.action_category,
        )
        if not decision.allowed:
            worker_monitor.mark_failed(task.task_id, decision.reason)
            result = SamResult(
                status="blocked",
                summary="Worker file edit blocked by authority rules.",
                error_type=ErrorType.MISSING_PERMISSION,
                error_message=decision.reason,
                next_action="request_approval",
                metadata={"task_id": task.task_id, "worker_type": spec.worker_type, "target_path": str(target_path)},
            )
            action_logger.log("worker_edit_blocked", status="blocked", data={"task_id": task.task_id})
            error_logger.log(
                event="worker_edit_blocked",
                error_type=result.error_type,
                error_message=result.error_message or result.summary,
                metadata={"task_id": task.task_id, "target_path": str(target_path)},
            )
            summary_logger.write(result, metadata={"task_id": task.task_id})
            return (result, worker_monitor.get_task(task.task_id) or task)

        if decision.requires_approval:
            schema_result = self.approval_manager.ensure_schema()
            if not schema_result.ok:
                worker_monitor.mark_failed(task.task_id, schema_result.error_message or schema_result.summary)
                result = SamResult(
                    status="failed",
                    summary="Approval schema initialization failed.",
                    error_type=schema_result.error_type or ErrorType.FILE_ACCESS_ERROR,
                    error_message=schema_result.error_message,
                    next_action=schema_result.next_action or "retry",
                    metadata={"task_id": task.task_id},
                )
                error_logger.log(
                    event="edit_approval_schema_failed",
                    error_type=result.error_type,
                    error_message=result.error_message or result.summary,
                    metadata={"task_id": task.task_id},
                )
                summary_logger.write(result, metadata={"task_id": task.task_id})
                return (result, worker_monitor.get_task(task.task_id) or task)

            create_result, approval = self.approval_manager.create_request(
                agent_id=f"worker:{spec.worker_type}",
                agent_name=f"{worker_name} ({spec.worker_type})",
                tool_name=spec.name,
                tool_arguments={
                    "target_path": str(target_path),
                    "worker_type": spec.worker_type,
                },
                action_category=spec.action_category,
                reason=decision.reason,
                context=spec.description,
            )
            if not create_result.ok or approval is None:
                worker_monitor.mark_failed(task.task_id, create_result.error_message or create_result.summary)
                result = SamResult(
                    status="failed",
                    summary="Approval request creation failed.",
                    error_type=create_result.error_type or ErrorType.FILE_ACCESS_ERROR,
                    error_message=create_result.error_message,
                    next_action=create_result.next_action or "retry",
                    metadata={"task_id": task.task_id},
                )
                error_logger.log(
                    event="edit_approval_request_failed",
                    error_type=result.error_type,
                    error_message=result.error_message or result.summary,
                    metadata={"task_id": task.task_id},
                )
                summary_logger.write(result, metadata={"task_id": task.task_id})
                return (result, worker_monitor.get_task(task.task_id) or task)

            worker_monitor.mark_needs_approval(task.task_id, decision.reason)
            run_logger.log("worker_edit_needs_approval", {"task_id": task.task_id, "approval_id": approval.id})
            result = SamResult(
                status="needs_approval",
                summary="Worker file edit requires approval.",
                error_type=ErrorType.MISSING_PERMISSION,
                error_message=decision.reason,
                next_action="request_approval",
                metadata={"task_id": task.task_id, "approval_id": approval.id},
            )
            action_logger.log(
                "worker_edit_needs_approval",
                status="needs_approval",
                data={"task_id": task.task_id, "worker_name": worker_name},
            )
            summary_logger.write(result, metadata={"task_id": task.task_id})
            return (result, worker_monitor.get_task(task.task_id) or task)

        worker_monitor.mark_running(task.task_id)
        run_logger.log("worker_edit_started", {"task_id": task.task_id, "target_path": str(target_path)})
        worker_monitor.append_output(task.task_id, f"{worker_name} is editing {target_path.name}.")
        worker_monitor.append_output(task.task_id, f"File: {target_path}")
        action_logger.log(
            "worker_edit_started",
            status="running",
            data={"task_id": task.task_id, "worker_name": worker_name},
        )

        writable_result = self._ensure_workdir_writable(target_path.parent)
        if writable_result is not None:
            worker_monitor.mark_failed(task.task_id, writable_result.error_message or writable_result.summary)
            error_logger.log(
                event="worker_edit_workdir_blocked",
                error_type=writable_result.error_type,
                error_message=writable_result.error_message or writable_result.summary,
                metadata={"task_id": task.task_id, "target_path": str(target_path)},
            )
            summary_logger.write(writable_result, metadata={"task_id": task.task_id})
            return (writable_result, worker_monitor.get_task(task.task_id) or task)

        try:
            original_text = target_path.read_text(encoding="utf-8")
        except OSError as exc:
            worker_monitor.mark_failed(task.task_id, str(exc))
            result = SamResult(
                status="failed",
                summary="Worker could not read the target file.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="ask_user",
                metadata={"task_id": task.task_id, "target_path": str(target_path)},
            )
            error_logger.log(
                event="worker_edit_read_failed",
                error_type=result.error_type,
                error_message=result.error_message or result.summary,
                metadata={"task_id": task.task_id, "target_path": str(target_path)},
            )
            summary_logger.write(result, metadata={"task_id": task.task_id})
            return (result, worker_monitor.get_task(task.task_id) or task)

        if spec.search_text not in original_text:
            worker_monitor.mark_failed(task.task_id, "search text not found")
            result = SamResult(
                status="failed",
                summary="Worker could not find the requested code to edit.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="search text not found",
                next_action="ask_user",
                metadata={"task_id": task.task_id, "target_path": str(target_path)},
            )
            error_logger.log(
                event="worker_edit_search_not_found",
                error_type=result.error_type,
                error_message=result.error_message or result.summary,
                metadata={"task_id": task.task_id, "target_path": str(target_path)},
            )
            summary_logger.write(result, metadata={"task_id": task.task_id})
            return (result, worker_monitor.get_task(task.task_id) or task)

        updated_text = original_text.replace(spec.search_text, spec.replace_text, 1)
        diff_text = "\n".join(
            difflib.unified_diff(
                original_text.splitlines(),
                updated_text.splitlines(),
                fromfile=str(target_path),
                tofile=str(target_path),
                lineterm="",
            )
        )

        try:
            target_path.write_text(updated_text, encoding="utf-8")
            audit_result, audit_id = log_audit_event(
                self.db_path,
                AuditEvent(
                    event_type="worker_file_edited",
                    actor=f"workers.{worker_name.lower()}",
                    summary=spec.description,
                    metadata_json=json.dumps(
                        {
                            "task_id": task.task_id,
                            "target_path": str(target_path),
                            "worker_type": spec.worker_type,
                            "worker_name": worker_name,
                        }
                    ),
                ),
            )
            worker_monitor.append_output(task.task_id, f"edited {target_path.name}")
            worker_monitor.mark_done(task.task_id)
            run_logger.log(
                "worker_edit_completed",
                {"task_id": task.task_id, "audit_id": audit_id, "target_path": str(target_path)},
            )
            result = SamResult(
                status="success",
                summary=f"Edited {target_path.name}.",
                next_action="stop",
                metadata={
                    "task_id": task.task_id,
                    "worker_type": spec.worker_type,
                    "worker_name": worker_name,
                    "target_path": str(target_path),
                    "diff": diff_text,
                    "audit_event_id": audit_id,
                    "audit_status": audit_result.status,
                },
            )
            action_logger.log(
                "worker_edit_completed",
                status="success",
                data={"task_id": task.task_id, "audit_event_id": audit_id, "worker_name": worker_name},
            )
            summary_logger.write(result, metadata={"task_id": task.task_id})
            return (result, worker_monitor.get_task(task.task_id) or task)
        except OSError as exc:
            worker_monitor.mark_failed(task.task_id, str(exc))
            result = SamResult(
                status="failed",
                summary="Worker could not write the requested code edit.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="retry",
                metadata={"task_id": task.task_id, "target_path": str(target_path)},
            )
            error_logger.log(
                event="worker_edit_write_failed",
                error_type=result.error_type,
                error_message=result.error_message or result.summary,
                metadata={"task_id": task.task_id, "target_path": str(target_path)},
            )
            summary_logger.write(result, metadata={"task_id": task.task_id})
            return (result, worker_monitor.get_task(task.task_id) or task)

    def execute_write(self, spec: FileWriteSpec) -> tuple[SamResult, WorkerTask]:
        worker_name = resolve_worker_name(spec.worker_type, spec.worker_name)
        task = worker_monitor.create_task(
            name=spec.name,
            worker_type=spec.worker_type,
            worker_name=worker_name,
            description=spec.description,
        )
        run_logger = RunLogger(f"sam_v2 worker {worker_name} {spec.name}")
        action_logger = ActionLogger(f"sam_v2 worker {worker_name} {spec.name}", correlation_id=run_logger.run_id)
        error_logger = ErrorLogger(f"workers.{spec.worker_type}")
        summary_logger = SummaryLogger(f"sam_v2 worker {worker_name} {spec.name}", correlation_id=run_logger.run_id)
        target_path = Path(spec.target_path)
        run_logger.log(
            "worker_write_task_created",
            {
                "task_id": task.task_id,
                "worker_type": spec.worker_type,
                "worker_name": worker_name,
                "target_path": str(target_path),
            },
        )
        action_logger.log(
            "worker_write_task_created",
            status="started",
            data={"task_id": task.task_id, "worker_name": worker_name},
        )

        decision = self.authority_engine.check(
            agent_id=f"worker:{spec.worker_type}",
            agent_level=5,
            role_id="worker",
            tool_name=spec.name,
            action_category=spec.action_category,
        )
        if not decision.allowed:
            worker_monitor.mark_failed(task.task_id, decision.reason)
            result = SamResult(
                status="blocked",
                summary="Worker file write blocked by authority rules.",
                error_type=ErrorType.MISSING_PERMISSION,
                error_message=decision.reason,
                next_action="request_approval",
                metadata={"task_id": task.task_id, "worker_type": spec.worker_type, "target_path": str(target_path)},
            )
            action_logger.log("worker_write_blocked", status="blocked", data={"task_id": task.task_id})
            error_logger.log(
                event="worker_write_blocked",
                error_type=result.error_type,
                error_message=result.error_message or result.summary,
                metadata={"task_id": task.task_id, "target_path": str(target_path)},
            )
            summary_logger.write(result, metadata={"task_id": task.task_id})
            return (result, worker_monitor.get_task(task.task_id) or task)

        if decision.requires_approval:
            schema_result = self.approval_manager.ensure_schema()
            if not schema_result.ok:
                worker_monitor.mark_failed(task.task_id, schema_result.error_message or schema_result.summary)
                result = SamResult(
                    status="failed",
                    summary="Approval schema initialization failed.",
                    error_type=schema_result.error_type or ErrorType.FILE_ACCESS_ERROR,
                    error_message=schema_result.error_message,
                    next_action=schema_result.next_action or "retry",
                    metadata={"task_id": task.task_id},
                )
                error_logger.log(
                    event="write_approval_schema_failed",
                    error_type=result.error_type,
                    error_message=result.error_message or result.summary,
                    metadata={"task_id": task.task_id},
                )
                summary_logger.write(result, metadata={"task_id": task.task_id})
                return (result, worker_monitor.get_task(task.task_id) or task)

            create_result, approval = self.approval_manager.create_request(
                agent_id=f"worker:{spec.worker_type}",
                agent_name=f"{worker_name} ({spec.worker_type})",
                tool_name=spec.name,
                tool_arguments={"target_path": str(target_path), "worker_type": spec.worker_type},
                action_category=spec.action_category,
                reason=decision.reason,
                context=spec.description,
            )
            if not create_result.ok or approval is None:
                worker_monitor.mark_failed(task.task_id, create_result.error_message or create_result.summary)
                result = SamResult(
                    status="failed",
                    summary="Approval request creation failed.",
                    error_type=create_result.error_type or ErrorType.FILE_ACCESS_ERROR,
                    error_message=create_result.error_message,
                    next_action=create_result.next_action or "retry",
                    metadata={"task_id": task.task_id},
                )
                error_logger.log(
                    event="write_approval_request_failed",
                    error_type=result.error_type,
                    error_message=result.error_message or result.summary,
                    metadata={"task_id": task.task_id},
                )
                summary_logger.write(result, metadata={"task_id": task.task_id})
                return (result, worker_monitor.get_task(task.task_id) or task)

            worker_monitor.mark_needs_approval(task.task_id, decision.reason)
            run_logger.log("worker_write_needs_approval", {"task_id": task.task_id, "approval_id": approval.id})
            result = SamResult(
                status="needs_approval",
                summary="Worker file write requires approval.",
                error_type=ErrorType.MISSING_PERMISSION,
                error_message=decision.reason,
                next_action="request_approval",
                metadata={"task_id": task.task_id, "approval_id": approval.id},
            )
            action_logger.log(
                "worker_write_needs_approval",
                status="needs_approval",
                data={"task_id": task.task_id, "worker_name": worker_name},
            )
            summary_logger.write(result, metadata={"task_id": task.task_id})
            return (result, worker_monitor.get_task(task.task_id) or task)

        worker_monitor.mark_running(task.task_id)
        worker_monitor.append_output(task.task_id, f"{worker_name} is writing {target_path.name}.")
        worker_monitor.append_output(task.task_id, f"File: {target_path}")
        writable_result = self._ensure_workdir_writable(target_path.parent)
        if writable_result is not None:
            worker_monitor.mark_failed(task.task_id, writable_result.error_message or writable_result.summary)
            error_logger.log(
                event="worker_write_workdir_blocked",
                error_type=writable_result.error_type,
                error_message=writable_result.error_message or writable_result.summary,
                metadata={"task_id": task.task_id, "target_path": str(target_path)},
            )
            summary_logger.write(writable_result, metadata={"task_id": task.task_id})
            return (writable_result, worker_monitor.get_task(task.task_id) or task)

        if target_path.exists() and not spec.overwrite:
            worker_monitor.mark_failed(task.task_id, "target file already exists")
            result = SamResult(
                status="failed",
                summary="Worker will not overwrite an existing file.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message="target file already exists",
                next_action="ask_user",
                metadata={"task_id": task.task_id, "target_path": str(target_path)},
            )
            error_logger.log(
                event="worker_write_exists",
                error_type=result.error_type,
                error_message=result.error_message or result.summary,
                metadata={"task_id": task.task_id, "target_path": str(target_path)},
            )
            summary_logger.write(result, metadata={"task_id": task.task_id})
            return (result, worker_monitor.get_task(task.task_id) or task)

        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(spec.content, encoding="utf-8")
            audit_result, audit_id = log_audit_event(
                self.db_path,
                AuditEvent(
                    event_type="worker_file_written",
                    actor=f"workers.{worker_name.lower()}",
                    summary=spec.description,
                    metadata_json=json.dumps(
                        {
                            "task_id": task.task_id,
                            "target_path": str(target_path),
                            "worker_type": spec.worker_type,
                            "worker_name": worker_name,
                        }
                    ),
                ),
            )
            worker_monitor.append_output(task.task_id, f"wrote {target_path.name}")
            worker_monitor.mark_done(task.task_id)
            result = SamResult(
                status="success",
                summary=f"Wrote {target_path.name}.",
                next_action="stop",
                metadata={
                    "task_id": task.task_id,
                    "worker_type": spec.worker_type,
                    "worker_name": worker_name,
                    "target_path": str(target_path),
                    "audit_event_id": audit_id,
                    "audit_status": audit_result.status,
                },
            )
            action_logger.log(
                "worker_write_completed",
                status="success",
                data={"task_id": task.task_id, "audit_event_id": audit_id, "worker_name": worker_name},
            )
            summary_logger.write(result, metadata={"task_id": task.task_id})
            return (result, worker_monitor.get_task(task.task_id) or task)
        except OSError as exc:
            worker_monitor.mark_failed(task.task_id, str(exc))
            result = SamResult(
                status="failed",
                summary="Worker could not write the requested file.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="retry",
                metadata={"task_id": task.task_id, "target_path": str(target_path)},
            )
            error_logger.log(
                event="worker_write_failed",
                error_type=result.error_type,
                error_message=result.error_message or result.summary,
                metadata={"task_id": task.task_id, "target_path": str(target_path)},
            )
            summary_logger.write(result, metadata={"task_id": task.task_id})
            return (result, worker_monitor.get_task(task.task_id) or task)

    def _ensure_workdir_writable(self, cwd: str | Path | None) -> SamResult | None:
        if cwd is None:
            return None

        target = Path(cwd)
        probe = target / ".sam_v2_write_probe"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return None
        except PermissionError as exc:
            return SamResult(
                status="blocked",
                summary="Command work directory is not writable in the current execution environment.",
                error_type=ErrorType.MISSING_PERMISSION,
                error_message=str(exc),
                next_action="ask_user",
                metadata={"cwd": str(target)},
            )
        except OSError:
            return None

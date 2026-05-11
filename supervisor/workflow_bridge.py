"""Bridge a small execution plan into workers, approvals, and reporting."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from sam_v2.approvals import ApprovalManager, AuthorityEngine
from sam_v2.diagnostics.error_types import ErrorType
from sam_v2.diagnostics.reporting import ActionLogger, ErrorLogger, SummaryLogger
from sam_v2.diagnostics.result import SamResult
from sam_v2.diagnostics.run_logger import RunLogger
from sam_v2.workers import CommandSpec, ToolingWorker, WorkerQueue

from .recovery import RecoveryPolicy

StepType = Literal["worker_command", "pause"]


@dataclass
class ExecutionStep:
    step_id: str
    title: str
    step_type: StepType
    command_spec: CommandSpec | None = None
    max_attempts: int = 2
    pause_reason: str = ""


@dataclass
class ExecutionPlan:
    plan_id: str
    goal: str
    steps: list[ExecutionStep] = field(default_factory=list)


@dataclass
class PausedWorkflowRecord:
    paused_plan_id: str
    plan_id: str
    goal: str
    completed_steps: list[str]
    remaining_steps: list[dict[str, Any]]
    pause_reason: str


class WorkflowBridge:
    def __init__(
        self,
        *,
        db_path: str | Path,
        worker: ToolingWorker | None = None,
        queue: WorkerQueue | None = None,
        recovery_policy: RecoveryPolicy | None = None,
        authority_engine: AuthorityEngine | None = None,
        approval_manager: ApprovalManager | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.paused_store_path = self.db_path.with_name("paused_workflows.json")
        self.worker = worker or ToolingWorker(
            db_path=self.db_path,
            authority_engine=authority_engine,
            approval_manager=approval_manager,
        )
        self.queue = queue or WorkerQueue(self.worker)
        self.recovery_policy = recovery_policy or RecoveryPolicy()

    def execute_plan(self, plan: ExecutionPlan) -> SamResult:
        return self._execute_plan(plan, completed_steps=[])

    def resume_plan(self, paused_plan_id: str) -> SamResult:
        load_result, records = self._load_paused_records()
        if not load_result.ok:
            return load_result
        record = next((item for item in records if item.paused_plan_id == paused_plan_id), None)
        if record is None:
            return SamResult(
                status="failed",
                summary="Paused workflow was not found.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=paused_plan_id,
                next_action="ask_user",
            )
        plan = ExecutionPlan(
            plan_id=record.plan_id,
            goal=record.goal,
            steps=[self._step_from_dict(item) for item in record.remaining_steps],
        )
        result = self._execute_plan(plan, completed_steps=list(record.completed_steps))
        if result.ok or result.status == "failed":
            remaining = [item for item in records if item.paused_plan_id != paused_plan_id]
            self._save_paused_records(remaining)
        return result

    def _execute_plan(self, plan: ExecutionPlan, *, completed_steps: list[str]) -> SamResult:
        run_logger = RunLogger(f"sam_v2 workflow {plan.plan_id}")
        action_logger = ActionLogger(f"sam_v2 workflow {plan.plan_id}", correlation_id=run_logger.run_id)
        error_logger = ErrorLogger("sam_v2.supervisor.workflow")
        summary_logger = SummaryLogger(f"sam_v2 workflow {plan.plan_id}", correlation_id=run_logger.run_id)

        run_logger.log("plan_started", {"plan_id": plan.plan_id, "goal": plan.goal, "step_count": len(plan.steps)})
        action_logger.log("plan_started", status="started", data={"plan_id": plan.plan_id, "goal": plan.goal})

        for step in plan.steps:
            if step.step_type == "pause":
                completed_steps.append(step.step_id)
                pause_result = self._pause_plan(
                    plan=plan,
                    current_step_id=step.step_id,
                    completed_steps=completed_steps,
                    pause_reason=step.pause_reason or step.title,
                )
                action_logger.log(
                    "plan_paused",
                    status="partial",
                    data={"step_id": step.step_id, "paused_plan_id": pause_result.metadata.get("paused_plan_id", "")},
                )
                summary_logger.write(
                    pause_result,
                    metadata={"plan_id": plan.plan_id, "completed_steps": completed_steps},
                )
                return pause_result

            run_logger.log("step_started", {"step_id": step.step_id, "title": step.title})
            action_logger.log("step_started", status="started", data={"step_id": step.step_id, "title": step.title})

            attempt = 1
            while attempt <= step.max_attempts:
                if step.command_spec is None:
                    result = SamResult(
                        status="failed",
                        summary="Workflow step is missing its command.",
                        error_type=ErrorType.TOOL_FAILED,
                        error_message=step.step_id,
                        next_action="ask_user",
                        metadata={"plan_id": plan.plan_id, "step_id": step.step_id},
                    )
                    error_logger.log(
                        event="step_missing_command",
                        error_type=result.error_type,
                        error_message=result.error_message or result.summary,
                        metadata={"plan_id": plan.plan_id, "step_id": step.step_id},
                    )
                    summary_logger.write(result, metadata={"plan_id": plan.plan_id, "completed_steps": completed_steps})
                    return result
                queue_result = self.queue.submit(step.command_spec)
                if not queue_result.ok:
                    result = SamResult(
                        status="failed",
                        summary="Failed to queue workflow step.",
                        error_type=queue_result.error_type,
                        error_message=queue_result.error_message,
                        next_action=queue_result.next_action,
                        metadata={"plan_id": plan.plan_id, "step_id": step.step_id},
                    )
                    error_logger.log(
                        event="step_queue_failed",
                        error_type=result.error_type,
                        error_message=result.error_message or result.summary,
                        metadata={"plan_id": plan.plan_id, "step_id": step.step_id},
                    )
                    summary_logger.write(result, metadata={"plan_id": plan.plan_id, "completed_steps": completed_steps})
                    return result

                step_result = self.queue.run_next()
                step_result.metadata.setdefault("plan_id", plan.plan_id)
                step_result.metadata.setdefault("step_id", step.step_id)
                step_result.metadata.setdefault("attempt", attempt)

                decision = self.recovery_policy.decide(
                    step_result,
                    attempt=attempt,
                    max_attempts=step.max_attempts,
                )
                run_logger.log(
                    "step_result",
                    {
                        "step_id": step.step_id,
                        "attempt": attempt,
                        "status": step_result.status,
                        "decision": decision.action,
                    },
                )

                if step_result.ok:
                    completed_steps.append(step.step_id)
                    action_logger.log(
                        "step_completed",
                        status="success",
                        data={"step_id": step.step_id, "attempt": attempt},
                    )
                    break

                if decision.should_retry:
                    action_logger.log(
                        "step_retrying",
                        status="retry",
                        data={"step_id": step.step_id, "attempt": attempt},
                    )
                    attempt += 1
                    continue

                if step_result.status in {"needs_approval", "blocked"}:
                    action_logger.log(
                        "plan_paused_for_approval",
                        status=step_result.status,
                        data={"step_id": step.step_id, "approval_id": step_result.metadata.get("approval_id")},
                    )
                    summary_logger.write(
                        step_result,
                        metadata={"plan_id": plan.plan_id, "completed_steps": completed_steps},
                    )
                    return step_result

                error_logger.log(
                    event="step_failed",
                    error_type=step_result.error_type,
                    error_message=step_result.error_message or step_result.summary,
                    metadata={"plan_id": plan.plan_id, "step_id": step.step_id, "attempt": attempt},
                )
                summary_logger.write(
                    step_result,
                    metadata={"plan_id": plan.plan_id, "completed_steps": completed_steps},
                )
                return step_result

        result = SamResult(
            status="success",
            summary=f"Workflow plan '{plan.goal}' completed.",
            next_action="stop",
            metadata={
                "plan_id": plan.plan_id,
                "completed_steps": completed_steps,
                "step_count": len(plan.steps),
            },
        )
        run_logger.log("plan_completed", result.metadata)
        action_logger.log("plan_completed", status="success", data=result.metadata)
        summary_logger.write(result, metadata={"plan_id": plan.plan_id})
        return result

    def _pause_plan(
        self,
        *,
        plan: ExecutionPlan,
        current_step_id: str,
        completed_steps: list[str],
        pause_reason: str,
    ) -> SamResult:
        paused_plan_id = f"paused-{uuid4().hex[:8]}"
        remaining_steps = []
        seen_current = False
        for step in plan.steps:
            if not seen_current:
                if step.step_id == current_step_id:
                    seen_current = True
                continue
            remaining_steps.append(self._step_to_dict(step))

        load_result, records = self._load_paused_records()
        if not load_result.ok:
            return load_result
        records.append(
            PausedWorkflowRecord(
                paused_plan_id=paused_plan_id,
                plan_id=plan.plan_id,
                goal=plan.goal,
                completed_steps=list(completed_steps),
                remaining_steps=remaining_steps,
                pause_reason=pause_reason,
            )
        )
        save_result = self._save_paused_records(records)
        if not save_result.ok:
            return save_result
        return SamResult(
            status="partial",
            summary=f"Workflow paused: {pause_reason}",
            next_action="resume_workflow",
            metadata={
                "plan_id": plan.plan_id,
                "paused_plan_id": paused_plan_id,
                "completed_steps": list(completed_steps),
                "remaining_step_count": len(remaining_steps),
                "pause_reason": pause_reason,
            },
        )

    def _load_paused_records(self) -> tuple[SamResult, list[PausedWorkflowRecord]]:
        if not self.paused_store_path.exists():
            return (
                SamResult(status="success", summary="Paused workflow store not found; using empty store.", next_action="stop"),
                [],
            )
        try:
            raw = json.loads(self.paused_store_path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("Paused workflow store must contain a list.")
            return (
                SamResult(status="success", summary="Paused workflows loaded.", next_action="stop"),
                [PausedWorkflowRecord(**item) for item in raw],
            )
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Paused workflow store is invalid.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="ask_user",
                ),
                [],
            )
        except OSError as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Failed to read paused workflow store.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="retry",
                ),
                [],
            )

    def _save_paused_records(self, records: list[PausedWorkflowRecord]) -> SamResult:
        try:
            payload = [record.__dict__ for record in records]
            self.paused_store_path.parent.mkdir(parents=True, exist_ok=True)
            self.paused_store_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return SamResult(status="success", summary="Paused workflow store saved.", next_action="stop")
        except OSError as exc:
            return SamResult(
                status="failed",
                summary="Failed to save paused workflow store.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="retry",
            )

    def _step_to_dict(self, step: ExecutionStep) -> dict[str, Any]:
        return {
            "step_id": step.step_id,
            "title": step.title,
            "step_type": step.step_type,
                "command_spec": None
                if step.command_spec is None
                else {
                    "name": step.command_spec.name,
                    "worker_type": step.command_spec.worker_type,
                    "command": step.command_spec.command,
                    "description": step.command_spec.description,
                    "worker_name": step.command_spec.worker_name,
                    "cwd": str(step.command_spec.cwd) if step.command_spec.cwd else "",
                    "timeout_seconds": step.command_spec.timeout_seconds,
                    "action_category": step.command_spec.action_category,
                    "environment": step.command_spec.environment,
                },
            "max_attempts": step.max_attempts,
            "pause_reason": step.pause_reason,
        }

    def _step_from_dict(self, data: dict[str, Any]) -> ExecutionStep:
        command_spec_data = data.get("command_spec")
        command_spec = None
        if command_spec_data is not None:
            command_spec = CommandSpec(
                name=command_spec_data["name"],
                worker_type=command_spec_data["worker_type"],
                command=list(command_spec_data["command"]),
                description=command_spec_data["description"],
                worker_name=command_spec_data.get("worker_name", ""),
                cwd=command_spec_data.get("cwd") or None,
                timeout_seconds=int(command_spec_data.get("timeout_seconds", 60)),
                action_category=command_spec_data.get("action_category", "execute_command"),
                environment=dict(command_spec_data.get("environment", {})),
            )
        return ExecutionStep(
            step_id=data["step_id"],
            title=data["title"],
            step_type=data["step_type"],
            command_spec=command_spec,
            max_attempts=int(data.get("max_attempts", 2)),
            pause_reason=data.get("pause_reason", ""),
        )

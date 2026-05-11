"""Minimal supervisor controller for coding, testing, and project execution."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from uuid import uuid4

from sam_v2.diagnostics.error_types import ErrorType
from sam_v2.diagnostics.reporting import ActionLogger, ErrorLogger, SummaryLogger
from sam_v2.diagnostics.result import SamResult
from sam_v2.diagnostics.run_logger import RunLogger
from sam_v2.workers import CommandSpec

from .workflow_bridge import ExecutionPlan, ExecutionStep, WorkflowBridge

TaskKind = Literal["code", "test", "build", "command"]


@dataclass
class ProjectProfile:
    project_id: str
    root_path: str | Path
    test_command: list[str] = field(default_factory=list)
    build_command: list[str] = field(default_factory=list)
    default_branch: str = ""
    stack: str = ""

    @property
    def root(self) -> Path:
        return Path(self.root_path)


@dataclass
class SupervisorRequest:
    goal: str
    task_kind: TaskKind = "command"
    project_id: str = ""
    command: list[str] = field(default_factory=list)
    requires_approval: bool = False


@dataclass
class SupervisorDecision:
    role_name: str
    worker_type: str
    summary: str
    plan: ExecutionPlan | None = None


class SupervisorController:
    def __init__(self, bridge: WorkflowBridge) -> None:
        self.bridge = bridge
        self.projects: dict[str, ProjectProfile] = {}

    def register_project(self, profile: ProjectProfile) -> None:
        self.projects[profile.project_id] = profile

    def decide(self, request: SupervisorRequest) -> SamResult:
        run_logger = RunLogger("sam_v2 supervisor decision")
        action_logger = ActionLogger("sam_v2 supervisor decision", correlation_id=run_logger.run_id)
        error_logger = ErrorLogger("sam_v2.supervisor.controller")
        summary_logger = SummaryLogger("sam_v2 supervisor decision", correlation_id=run_logger.run_id)

        run_logger.log("decision_started", {"goal": request.goal, "task_kind": request.task_kind})
        action_logger.log("decision_started", status="started", data={"goal": request.goal, "task_kind": request.task_kind})

        profile = self.projects.get(request.project_id) if request.project_id else None
        decision = self._build_decision(request, profile)
        if isinstance(decision, SamResult):
            error_logger.log(
                event="decision_failed",
                error_type=decision.error_type,
                error_message=decision.error_message or decision.summary,
                metadata={"goal": request.goal, "task_kind": request.task_kind},
            )
            summary_logger.write(decision, metadata={"goal": request.goal})
            return decision

        result = SamResult(
            status="success",
            summary=decision.summary,
            next_action="stop",
            metadata={
                "role_name": decision.role_name,
                "worker_type": decision.worker_type,
                "plan_id": decision.plan.plan_id if decision.plan else "",
                "project_id": request.project_id,
            },
        )
        action_logger.log(
            "decision_completed",
            status="success",
            data={
                "role_name": decision.role_name,
                "worker_type": decision.worker_type,
                "project_id": request.project_id,
            },
        )
        summary_logger.write(result, metadata={"goal": request.goal})
        return result

    def execute(self, request: SupervisorRequest) -> SamResult:
        decision_result = self.decide(request)
        if not decision_result.ok:
            return decision_result

        profile = self.projects.get(request.project_id) if request.project_id else None
        decision = self._build_decision(request, profile)
        if isinstance(decision, SamResult):
            return decision

        plan_result = self.bridge.execute_plan(decision.plan)
        plan_result.metadata.setdefault("role_name", decision.role_name)
        plan_result.metadata.setdefault("worker_type", decision.worker_type)
        return plan_result

    def _build_decision(
        self,
        request: SupervisorRequest,
        profile: ProjectProfile | None,
    ) -> SupervisorDecision | SamResult:
        if request.task_kind == "test":
            if profile is None:
                return self._missing_project_result("test")
            if not profile.test_command:
                return self._missing_command_result("test", request.project_id)
            return self._decision_from_command(
                request=request,
                role_name="dev-lead",
                worker_type="test",
                summary=f"Supervisor selected test worker for {request.project_id}.",
                command=profile.test_command,
                cwd=profile.root,
            )

        if request.task_kind == "build":
            if profile is None:
                return self._missing_project_result("build")
            if not profile.build_command:
                return self._missing_command_result("build", request.project_id)
            return self._decision_from_command(
                request=request,
                role_name="dev-lead",
                worker_type="dev",
                summary=f"Supervisor selected build worker for {request.project_id}.",
                command=profile.build_command,
                cwd=profile.root,
            )

        if request.task_kind == "code":
            if not request.command:
                return SamResult(
                    status="failed",
                    summary="Code execution request needs a command.",
                    error_type=ErrorType.MISSING_CAPABILITY,
                    error_message="missing command",
                    next_action="ask_user",
                )
            return self._decision_from_command(
                request=request,
                role_name="dev-lead",
                worker_type="code",
                summary="Supervisor selected code worker.",
                command=request.command,
                cwd=profile.root if profile else None,
            )

        command = request.command or self._parse_command_from_goal(request.goal)
        if not command:
            return SamResult(
                status="failed",
                summary="Supervisor could not determine a command to run.",
                error_type=ErrorType.MISSING_CAPABILITY,
                error_message="no executable command found",
                next_action="ask_user",
            )
        return self._decision_from_command(
            request=request,
            role_name="system-admin",
            worker_type="dev",
            summary="Supervisor selected command worker.",
            command=command,
            cwd=profile.root if profile else None,
        )

    def _decision_from_command(
        self,
        *,
        request: SupervisorRequest,
        role_name: str,
        worker_type: str,
        summary: str,
        command: list[str],
        cwd: Path | None,
    ) -> SupervisorDecision:
        spec = CommandSpec(
            name=f"{worker_type}_{uuid4().hex[:8]}",
            worker_type=worker_type,
            command=command,
            description=request.goal,
            cwd=cwd,
        )
        plan = ExecutionPlan(
            plan_id=f"plan-{uuid4().hex[:8]}",
            goal=request.goal,
            steps=[
                ExecutionStep(
                    step_id="step-1",
                    title=request.goal,
                    step_type="worker_command",
                    command_spec=spec,
                    max_attempts=2,
                )
            ],
        )
        return SupervisorDecision(
            role_name=role_name,
            worker_type=worker_type,
            summary=summary,
            plan=plan,
        )

    def _missing_project_result(self, task_kind: str) -> SamResult:
        return SamResult(
            status="failed",
            summary=f"Supervisor needs a registered project profile for {task_kind} tasks.",
            error_type=ErrorType.MISSING_CAPABILITY,
            error_message="missing project profile",
            next_action="ask_user",
        )

    def _missing_command_result(self, task_kind: str, project_id: str) -> SamResult:
        return SamResult(
            status="failed",
            summary=f"Project '{project_id}' does not have a {task_kind} command configured.",
            error_type=ErrorType.MISSING_CAPABILITY,
            error_message=f"missing {task_kind} command",
            next_action="ask_user",
        )

    def _parse_command_from_goal(self, goal: str) -> list[str]:
        lowered = goal.lower().strip()
        if lowered.startswith("run command:"):
            raw = goal.split(":", 1)[1].strip()
            return shlex.split(raw, posix=False) if raw else []
        return []

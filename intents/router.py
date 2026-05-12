"""Minimal intent parser and router for Sam v2."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

from approvals import ApprovalManager, AuthorityEngine
from capabilities import CapabilityAwarenessService, CapabilityRegistry, build_default_registry
# Phase 1 planner and executor imports (surgical addition)
from sam.planner.task_planner import TaskPlanner
from sam.executor.tool_executor import ToolExecutor
from intents._executor_tools_registry import register_all_executor_tools
from sam.executor.service_tools import register_service_tools
# Phase 4 worker-centric runtime import (surgical addition)
from sam.executor.worker_runtime import create_worker_centric_executor
# Phase 5 observation loop import (surgical addition)
from sam.planner.observation_loop import create_observation_loop
from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult
from llm import OllamaClient, OllamaIntentOutput
from projects import (
    ProjectExecutionRequest,
    ProjectInspector,
    ProjectPlanRequest,
    ProjectPlanner,
    ProjectRegistry,
    ProjectScaffoldRequest,
    ProjectScaffolder,
    inspection_metadata,
)
from storage import TaskRecord, create_task, list_tasks, update_task
from tools import CodebaseCleanupService, SafeLocalTools, WorkspaceCleanupService
from upgrades import UpgradeProposalManager
from workers import CommandSpec, ToolingWorker, worker_monitor
from workflows import GoalService, PipelineService


@dataclass
class IntentRequest:
    intent: str
    parameters: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    needs_clarification: bool = False
    clarification_question: str = ""
    response_text: str = ""
    confidence: str = "low"
    source: str = "llm"


class IntentRouter:
    def __init__(
        self,
        *,
        db_path: str | Path,
        workspace_root: str | Path | None = None,
        registry: CapabilityRegistry | None = None,
        authority_engine: AuthorityEngine | None = None,
        approval_manager: ApprovalManager | None = None,
        model_client: OllamaClient | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.registry = registry or build_default_registry()
        self.authority_engine = authority_engine
        self.approval_manager = approval_manager
        self.goal_service = GoalService(self.db_path)
        self.pipeline_service = PipelineService(self.db_path)
        self.model_client = model_client or OllamaClient()
        self.workspace_root = Path(workspace_root) if workspace_root is not None else Path.cwd() / "sam_v2" / "workspace"
        self.project_registry = ProjectRegistry(self.db_path.with_name("projects.json"))
        self.upgrade_manager = UpgradeProposalManager(self.db_path.with_name("upgrades.json"))
        self.project_inspector = ProjectInspector(
            registry=self.project_registry,
            tools=SafeLocalTools(db_path=self.db_path),
        )
        self.workspace_cleanup = WorkspaceCleanupService(self.workspace_root, db_path=self.db_path)
        self.tooling_worker = ToolingWorker(
            db_path=self.db_path,
            authority_engine=self.authority_engine,
            approval_manager=self.approval_manager,
        )
        self.project_scaffolder = ProjectScaffolder(
            workspace_root=self.workspace_root / "projects",
            project_registry=self.project_registry,
            tooling_worker=self.tooling_worker,
        )
        self.project_planner = ProjectPlanner(
            project_registry=self.project_registry,
            tooling_worker=self.tooling_worker,
            project_inspector=self.project_inspector,
        )
        self.awareness = CapabilityAwarenessService(
            self.registry,
            project_registry=self.project_registry,
            upgrade_manager=self.upgrade_manager,
        )
        # Initialise Phase 1 planner and executor
        self.task_planner = TaskPlanner(self.registry)
        # Use base executor for registration, then wrap for worker tracking
        self.tool_executor = ToolExecutor()
        # Register the safe executor tools for the initial intents
        self._register_executor_tools()
        # Phase 4: Wrap executor with worker-centric tracking
        self._base_executor = self.tool_executor
        self.tool_executor = create_worker_centric_executor(self._base_executor)
        # Phase 5: Create observation loop for adaptive plan execution
        self.observation_loop = create_observation_loop(self.task_planner, self.tool_executor)

    def parse(self, user_text: str, memory_block: dict[str, Any] | None = None) -> IntentRequest:
        """Understand a user request with the model first.

        This must not become a phrase-matching command bot.
        Rules are only allowed for empty input, approval/stop safety,
        and exact fallback commands when the LLM is unavailable.
        """
        text = user_text.strip()
        if not text:
            return IntentRequest(
                intent="chat",
                raw_text=user_text,
                needs_clarification=True,
                clarification_question="What would you like me to do?",
                source="safety_rule",
                confidence="high",
            )

        safety_request = self._parse_with_safety_rules(text)
        if safety_request is not None:
            return safety_request

        llm_request = self._parse_with_llm(text, memory_block)
        if llm_request is not None:
            return self._apply_contextual_request(text, llm_request, memory_block)

        return self._parse_with_rules(text)

    def _parse_with_safety_rules(self, text: str) -> IntentRequest | None:
        """Only deterministic safety/approval rules live here.

        Do not add business/task/project phrases here.
        Natural language should go through the LLM.
        """
        lowered = text.lower().strip()

        if lowered in {"yes", "confirm", "approved", "approve", "go ahead", "continue"}:
            return IntentRequest(
                intent="chat",
                raw_text=text,
                needs_clarification=True,
                clarification_question=(
                    "What exactly are you approving? Mention the action, like push, delete, cleanup, or send."
                ),
                source="safety_rule",
                confidence="high",
            )

        if lowered in {"cancel", "stop", "abort"}:
            return IntentRequest(
                intent="chat",
                raw_text=text,
                response_text="Okay. I will stop that action unless you ask me to continue.",
                source="safety_rule",
                confidence="high",
            )

        return None

    def _parse_with_rules(self, text: str) -> IntentRequest:
        """Fallback only when the LLM is unavailable.

        This fallback must stay tiny and exact.
        It is not Sam's brain.
        """
        lowered = text.lower().strip()
        exact_fallbacks = {
            "what can you do": "capabilities",
            "list capabilities": "capabilities",
            "show capabilities": "capabilities",
            "list tasks": "list_tasks",
            "show tasks": "list_tasks",
            "list goals": "list_goals",
            "show goals": "list_goals",
            "list projects": "list_projects",
            "show projects": "list_projects",
            "show approvals": "list_approvals",
            "list approvals": "list_approvals",
        }

        if lowered in exact_fallbacks:
            return IntentRequest(
                intent=exact_fallbacks[lowered],
                raw_text=text,
                source="fallback_rule",
                confidence="medium",
            )

        return IntentRequest(
            intent="chat",
            raw_text=text,
            response_text=(
                "My local understanding model is unavailable, so I cannot safely decide how to act on that. "
                "Please start the model, then try again."
            ),
            source="llm_unavailable",
            confidence="low",
        )

    def _parse_followup_with_memory(
        self,
        text: str,
        memory_block: dict[str, Any] | None = None,
    ) -> IntentRequest | None:
        """Deprecated.

        Follow-up understanding must be handled by the LLM using memory context,
        not by project-specific phrase rules or hardcoded heuristics.
        """
        return None

    def handle(self, user_text: str, memory_block: dict[str, Any] | None = None) -> SamResult:
        request = self.parse(user_text, memory_block)
        if request.needs_clarification:
            return SamResult(
                status="success",
                summary=request.clarification_question or "I need a bit more detail before I act.",
                next_action="ask_user",
                metadata={
                    "intent": "clarify",
                    "source": request.source,
                    "confidence": request.confidence,
                },
            )

        capability = self.registry.get(request.intent)
        if capability is None:
            return SamResult(
                status="failed",
                summary="Intent is not registered.",
                error_type=ErrorType.MISSING_CAPABILITY,
                error_message=request.intent,
                next_action="ask_user",
            )

        approval_result = self._check_authority(request, capability.action_category)
        if approval_result is not None:
            return approval_result

        # Phase 1 routing for a limited set of intents via planner & executor
        if request.intent in {"capabilities", "list_tasks", "list_goals", "list_projects"}:
            return self._execute_with_planner(request, memory_block)

        if request.intent == "capabilities":
            awareness_result = self.awareness.describe_self()
            if not awareness_result.ok:
                return self._service_result("capabilities", awareness_result)
            awareness_result.metadata.setdefault("intent", request.intent)
            awareness_result.metadata.setdefault("source", request.source)
            awareness_result.metadata.setdefault("confidence", request.confidence)
            return awareness_result

        if request.intent == "awareness_check":
            capability_name = str(request.parameters.get("capability_name", "")).strip()
            if not capability_name:
                return SamResult(
                    status="failed",
                    summary="Capability name is required.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing capability name",
                    next_action="ask_user",
                    metadata={"intent": "awareness_check", "source": request.source},
                )
            awareness_result = self.awareness.check_request(capability_name)
            awareness_result.metadata.setdefault("intent", "awareness_check")
            awareness_result.metadata.setdefault("source", request.source)
            awareness_result.metadata.setdefault("confidence", request.confidence)
            return awareness_result

        if request.intent == "propose_upgrade":
            capability_name = str(request.parameters.get("capability_name", "")).strip().replace(" ", "_")
            if not capability_name:
                return SamResult(
                    status="failed",
                    summary="Capability name is required for an upgrade proposal.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing capability name",
                    next_action="ask_user",
                    metadata={"intent": "propose_upgrade", "source": request.source},
                )
            proposal_result = self.awareness.propose_upgrade(
                capability_name,
                f"User requested upgrade support for {capability_name}.",
            )
            proposal_result.metadata.setdefault("intent", "propose_upgrade")
            proposal_result.metadata.setdefault("source", request.source)
            proposal_result.metadata.setdefault("confidence", request.confidence)
            return proposal_result

        if request.intent == "create_goal":
            title = request.parameters.get("title", "").strip()
            if not title:
                return SamResult(
                    status="failed",
                    summary="Goal title is required.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing title",
                    next_action="ask_user",
                )
            result, goal = self.goal_service.create_goal(title=title)
            return self._service_result(
                "create_goal",
                result,
                goal.id if goal else None,
                metadata={
                    "source": request.source,
                    "confidence": request.confidence,
                },
            )

        if request.intent == "create_task":
            title = str(request.parameters.get("title", "")).strip()
            if not title:
                return SamResult(
                    status="failed",
                    summary="Task title is required.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing title",
                    next_action="ask_user",
                )
            result, task_id = create_task(self.db_path, TaskRecord(title=title))
            return self._service_result("create_task", result, identifier=str(task_id) if task_id is not None else None)

        if request.intent == "scaffold_project":
            project_name = str(request.parameters.get("name", "")).strip()
            project_type = str(request.parameters.get("project_type", "html_game")).strip() or "html_game"
            scaffold_result = self.project_scaffolder.scaffold(
                ProjectScaffoldRequest(name=project_name, project_type=project_type)
            )
            scaffold_result.metadata.setdefault("intent", "scaffold_project")
            scaffold_result.metadata.setdefault("source", request.source)
            scaffold_result.metadata.setdefault("confidence", request.confidence)
            return scaffold_result

        if request.intent == "update_task":
            task_id_text = str(request.parameters.get("task_id", "")).strip()
            status_text = str(request.parameters.get("status", "")).strip()
            notes_text = str(request.parameters.get("notes", "")).strip()
            if not task_id_text.isdigit():
                return SamResult(
                    status="failed",
                    summary="Task id is required.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing or invalid task id",
                    next_action="ask_user",
                )
            if not status_text and not notes_text:
                return SamResult(
                    status="failed",
                    summary="Task update needs a status or notes value.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing update values",
                    next_action="ask_user",
                )
            result, task = update_task(
                self.db_path,
                int(task_id_text),
                status=status_text or None,
                notes=notes_text or None,
            )
            return self._service_result("update_task", result, identifier=str(task.id) if task is not None else task_id_text)

        if request.intent == "list_goals":
            result, goals = self.goal_service.list_goals(status="active")
            return self._service_result(
                "list_goals",
                result,
                metadata={
                    "count": len(goals),
                    "titles": [goal.title for goal in goals],
                    "source": request.source,
                    "confidence": request.confidence,
                },
            )

        if request.intent == "inspect_workspace_cleanup":
            scope = str(request.parameters.get("scope", "all")).strip() or "all"
            inspect_result, _metadata = self.workspace_cleanup.inspect(scope)
            inspect_result.metadata.setdefault("intent", "inspect_workspace_cleanup")
            inspect_result.metadata.setdefault("source", request.source)
            inspect_result.metadata.setdefault("confidence", request.confidence)
            inspect_result.next_action = "ask_user"
            return inspect_result

        if request.intent == "cleanup_workspace_duplicates":
            scope = str(request.parameters.get("scope", "all")).strip() or "all"
            cleanup_result = self.workspace_cleanup.cleanup(scope)
            cleanup_result.metadata.setdefault("intent", "cleanup_workspace_duplicates")
            cleanup_result.metadata.setdefault("source", request.source)
            cleanup_result.metadata.setdefault("confidence", request.confidence)
            return cleanup_result

        if request.intent == "list_tasks":
            task_result, tasks = list_tasks(self.db_path)
            if not task_result.ok:
                return self._service_result("list_tasks", task_result)
            if not tasks:
                return SamResult(
                    status="success",
                    summary="I do not have any tracked tasks yet.",
                    next_action="ask_user",
                    metadata={
                        "intent": "list_tasks",
                        "count": 0,
                        "tasks": [],
                        "source": request.source,
                        "confidence": request.confidence,
                    },
                )
            task_items = [
                {
                    "id": task.id,
                    "title": task.title,
                    "status": task.status,
                    "priority": task.priority,
                    "notes": task.notes,
                }
                for task in tasks
            ]
            preview = ", ".join(f"#{item['id']} {item['title']} [{item['status']}]" for item in task_items[:3])
            remaining = len(task_items) - min(len(task_items), 3)
            if remaining > 0:
                preview = f"{preview}, and {remaining} more"
            return SamResult(
                status="success",
                summary=f"I have {len(task_items)} tracked task(s): {preview}.",
                next_action="stop",
                metadata={
                    "intent": "list_tasks",
                    "count": len(task_items),
                    "tasks": task_items,
                    "source": request.source,
                    "confidence": request.confidence,
                },
            )

        if request.intent == "read_file":
            path_text = str(request.parameters.get("path", "")).strip()
            if not path_text:
                return SamResult(
                    status="failed",
                    summary="File path is required.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing file path",
                    next_action="ask_user",
                )
            additional_roots: list[str] = []
            daily_state = memory_block.get("daily_state", {}) if isinstance(memory_block, dict) else {}
            last_project_root = str(daily_state.get("last_project_root_path", {}).get("value", "")).strip()
            if last_project_root:
                additional_roots.append(last_project_root)
            resolve_result, resolved_file = self.project_inspector.tools.resolve_file_query(
                path_text,
                additional_roots=additional_roots,
            )
            if not resolve_result.ok or resolved_file is None:
                return self._service_result("read_file", resolve_result, metadata={"path": path_text})
            file_result, content = self.project_inspector.tools.read_text_file(resolved_file)
            if not file_result.ok or content is None:
                return self._service_result("read_file", file_result, metadata={"path": path_text})
            wants_summary = self._wants_summary(request.raw_text)
            summary = self._summarize_text(content, str(resolved_file)) if wants_summary else "File read succeeded."
            return SamResult(
                status="success",
                summary=summary,
                next_action="stop",
                metadata={
                    "intent": "read_file",
                    "path": file_result.metadata.get("path", str(resolved_file)),
                    "content": content,
                    "chars_returned": file_result.metadata.get("chars_returned", len(content)),
                    "summary_mode": wants_summary,
                    "source": request.source,
                    "confidence": request.confidence,
                },
            )

        if request.intent == "open_file":
            path_text = str(request.parameters.get("path", "")).strip()
            if not path_text:
                return SamResult(
                    status="failed",
                    summary="File path is required.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing file path",
                    next_action="ask_user",
                    metadata={"intent": "open_file", "source": request.source},
                )
            additional_roots: list[str] = []
            daily_state = memory_block.get("daily_state", {}) if isinstance(memory_block, dict) else {}
            last_project_root = str(daily_state.get("last_project_root_path", {}).get("value", "")).strip()
            if last_project_root:
                additional_roots.append(last_project_root)
            open_result = self.project_inspector.tools.open_file_query(path_text, additional_roots=additional_roots)
            open_result.metadata.setdefault("intent", "open_file")
            open_result.metadata.setdefault("source", request.source)
            open_result.metadata.setdefault("confidence", request.confidence)
            return open_result

        if request.intent == "list_directory":
            path_text = str(request.parameters.get("path", "")).strip()
            if not path_text:
                return SamResult(
                    status="failed",
                    summary="Directory path is required.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing directory path",
                    next_action="ask_user",
                )
            directory_result, entries = self.project_inspector.tools.list_directory(path_text)
            if not directory_result.ok:
                return self._service_result("list_directory", directory_result, metadata={"path": path_text})
            preview = entries[:12]
            remaining = max(0, len(entries) - len(preview))
            summary = f"{len(entries)} item(s) in {directory_result.metadata.get('path', path_text)}."
            if preview:
                summary += " " + ", ".join(preview)
                if remaining:
                    summary += f", and {remaining} more"
                summary += "."
            return SamResult(
                status="success",
                summary=summary,
                next_action="stop",
                metadata={
                    "intent": "list_directory",
                    "path": directory_result.metadata.get("path", path_text),
                    "entries": preview,
                    "entry_count": directory_result.metadata.get("entry_count", len(entries)),
                    "source": request.source,
                    "confidence": request.confidence,
                },
            )

        if request.intent == "list_approvals":
            approval_result, approvals = self.approval_manager.list_pending()
            if not approval_result.ok:
                return self._service_result("list_approvals", approval_result)
            if not approvals:
                return SamResult(
                    status="success",
                    summary="I do not have any pending approvals right now.",
                    next_action="stop",
                    metadata={
                        "intent": "list_approvals",
                        "count": 0,
                        "approvals": [],
                        "source": request.source,
                        "confidence": request.confidence,
                    },
                )
            approval_items = [
                {
                    "id": approval.id,
                    "agent_name": approval.agent_name,
                    "tool_name": approval.tool_name,
                    "reason": approval.reason,
                    "urgency": approval.urgency,
                    "status": approval.status,
                }
                for approval in approvals
            ]
            preview = ", ".join(
                f"{item['tool_name']} by {item['agent_name']} [{item['urgency']}]"
                for item in approval_items[:3]
            )
            remaining = len(approval_items) - min(len(approval_items), 3)
            if remaining > 0:
                preview = f"{preview}, and {remaining} more"
            return SamResult(
                status="success",
                summary=f"I have {len(approval_items)} pending approval request(s): {preview}.",
                next_action="stop",
                metadata={
                    "intent": "list_approvals",
                    "count": len(approval_items),
                    "approvals": approval_items,
                    "source": request.source,
                    "confidence": request.confidence,
                },
            )

        if request.intent == "list_projects":
            project_result, projects = self.project_registry.list_projects()
            if not project_result.ok:
                return self._service_result("list_projects", project_result)
            if not projects:
                return SamResult(
                    status="success",
                    summary="I do not have any registered projects yet.",
                    next_action="ask_user",
                    metadata={
                        "intent": "list_projects",
                        "count": 0,
                        "projects": [],
                        "source": request.source,
                        "confidence": request.confidence,
                    },
                )
            names = [project.name for project in projects]
            return SamResult(
                status="success",
                summary=f"I know about {len(names)} project(s): {', '.join(names)}.",
                next_action="stop",
                metadata={
                    "intent": "list_projects",
                    "count": len(names),
                    "projects": names,
                    "source": request.source,
                    "confidence": request.confidence,
                },
            )

        if request.intent == "project_details":
            query = str(request.parameters.get("query", "")).strip()
            if request.parameters.get("use_memory"):
                daily_state = memory_block.get("daily_state", {}) if isinstance(memory_block, dict) else {}
                query = (
                    str(daily_state.get("last_project_id", {}).get("value", "")).strip()
                    or str(daily_state.get("last_project_name", {}).get("value", "")).strip()
                )
            if not query:
                return SamResult(
                    status="failed",
                    summary="Project name is required.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing project query",
                    next_action="ask_user",
                    metadata={"intent": "project_details", "source": request.source},
                )
            project_result, project = self.project_registry.find_project(query)
            if not project_result.ok or project is None:
                return self._service_result("project_details", project_result, metadata={"query": query})
            return SamResult(
                status="success",
                summary=(
                    f"Project {project.name} is a {project.stack or 'unspecified'} project on branch "
                    f"{project.active_branch or 'unknown'}. It is saved at {project.root_path}."
                ),
                next_action="stop",
                metadata={
                    "intent": "project_details",
                    "project_id": project.project_id,
                    "name": project.name,
                    "root_path": project.root_path,
                    "stack": project.stack,
                    "test_command": project.test_command or [],
                    "build_command": project.build_command or [],
                    "active_branch": project.active_branch,
                    "source": request.source,
                    "confidence": request.confidence,
                },
            )

        if request.intent == "plan_project":
            query = str(request.parameters.get("query", "")).strip()
            if not query:
                return SamResult(
                    status="failed",
                    summary="Project name is required to plan a project.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing project query",
                    next_action="ask_user",
                    metadata={"intent": "plan_project", "source": request.source},
                )
            plan_result = self.project_planner.plan(ProjectPlanRequest(query=query))
            plan_result.metadata.setdefault("intent", "plan_project")
            plan_result.metadata.setdefault("source", request.source)
            plan_result.metadata.setdefault("confidence", request.confidence)
            return plan_result

        if request.intent == "show_delegation":
            query = str(request.parameters.get("query", "")).strip()
            if not query:
                return SamResult(
                    status="failed",
                    summary="Project name is required to show delegation.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing project query",
                    next_action="ask_user",
                    metadata={"intent": "show_delegation", "source": request.source},
                )
            report_result = self.project_planner.show_delegation(query)
            report_result.metadata.setdefault("intent", "show_delegation")
            report_result.metadata.setdefault("source", request.source)
            report_result.metadata.setdefault("confidence", request.confidence)
            return report_result

        if request.intent == "show_project_progress":
            query = str(request.parameters.get("query", "")).strip()
            if not query:
                return SamResult(
                    status="failed",
                    summary="Project name is required to show progress.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing project query",
                    next_action="ask_user",
                    metadata={"intent": "show_project_progress", "source": request.source},
                )
            progress_result = self.project_planner.show_progress(query)
            progress_result.metadata.setdefault("intent", "show_project_progress")
            progress_result.metadata.setdefault("source", request.source)
            progress_result.metadata.setdefault("confidence", request.confidence)
            return progress_result

        if request.intent == "show_project_status":
            query = str(request.parameters.get("query", "")).strip()
            if not query:
                return SamResult(
                    status="failed",
                    summary="Project name is required to show status.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing project query",
                    next_action="ask_user",
                    metadata={"intent": "show_project_status", "source": request.source},
                )
            status_result = self.project_planner.show_status(query)
            status_result.metadata.setdefault("intent", "show_project_status")
            status_result.metadata.setdefault("source", request.source)
            status_result.metadata.setdefault("confidence", request.confidence)
            return status_result

        if request.intent == "execute_project_task":
            query = str(request.parameters.get("query", "")).strip()
            task_name = str(request.parameters.get("task_name", "")).strip()
            if not query or not task_name:
                return SamResult(
                    status="failed",
                    summary="Project name and delegated task are required.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing project query or task name",
                    next_action="ask_user",
                    metadata={"intent": "execute_project_task", "source": request.source},
                )
            execute_result = self.project_planner.execute_task(
                ProjectExecutionRequest(query=query, task_name=task_name)
            )
            execute_result.metadata.setdefault("intent", "execute_project_task")
            execute_result.metadata.setdefault("source", request.source)
            execute_result.metadata.setdefault("confidence", request.confidence)
            return execute_result

        if request.intent == "run_project":
            query = str(request.parameters.get("query", "")).strip()
            if request.parameters.get("use_memory"):
                daily_state = memory_block.get("daily_state", {}) if isinstance(memory_block, dict) else {}
                query = (
                    str(daily_state.get("last_project_id", {}).get("value", "")).strip()
                    or str(daily_state.get("last_project_name", {}).get("value", "")).strip()
                )
            if not query:
                return SamResult(
                    status="failed",
                    summary="Project name is required to run a project.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing project query",
                    next_action="ask_user",
                    metadata={"intent": "run_project", "source": request.source},
                )
            project_result, project = self.project_registry.find_project(query)
            if not project_result.ok or project is None:
                return self._service_result("run_project", project_result, metadata={"query": query})
            if not project.run_command:
                return SamResult(
                    status="failed",
                    summary=f"Project {project.name} does not have a run command configured.",
                    error_type=ErrorType.MISSING_CAPABILITY,
                    error_message="missing run command",
                    next_action="ask_user",
                    metadata={
                        "intent": "run_project",
                        "project_id": project.project_id,
                        "name": project.name,
                        "source": request.source,
                    },
                )
            worker_result, _task = self.tooling_worker.execute(
                CommandSpec(
                    name=f"run_project_{project.project_id}",
                    worker_type="dev",
                    command=project.run_command,
                    description=f"Run project {project.name}",
                    cwd=project.root_path,
                    timeout_seconds=30,
                )
            )
            worker_result.metadata.setdefault("intent", "run_project")
            worker_result.metadata.setdefault("project_id", project.project_id)
            worker_result.metadata.setdefault("name", project.name)
            worker_result.metadata.setdefault("root_path", project.root_path)
            worker_result.metadata.setdefault("run_command", project.run_command)
            worker_result.metadata.setdefault("source", request.source)
            worker_result.metadata.setdefault("confidence", request.confidence)
            return worker_result

        if request.intent == "open_project_folder":
            query = str(request.parameters.get("query", "")).strip()
            if request.parameters.get("use_memory"):
                daily_state = memory_block.get("daily_state", {}) if isinstance(memory_block, dict) else {}
                query = (
                    str(daily_state.get("last_project_id", {}).get("value", "")).strip()
                    or str(daily_state.get("last_project_name", {}).get("value", "")).strip()
                )
            if not query:
                return SamResult(
                    status="failed",
                    summary="Project name is required to open a project folder.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing project query",
                    next_action="ask_user",
                    metadata={"intent": "open_project_folder", "source": request.source},
                )
            project_result, project = self.project_registry.find_project(query)
            if not project_result.ok or project is None:
                return self._service_result("open_project_folder", project_result, metadata={"query": query})
            open_result = self.project_inspector.tools.open_directory(project.root_path)
            open_result.metadata.setdefault("intent", "open_project_folder")
            open_result.metadata.setdefault("project_id", project.project_id)
            open_result.metadata.setdefault("name", project.name)
            open_result.metadata.setdefault("root_path", project.root_path)
            open_result.metadata.setdefault("source", request.source)
            open_result.metadata.setdefault("confidence", request.confidence)
            return open_result

        if request.intent == "open_folder":
            query = str(request.parameters.get("query", "")).strip()
            if not query:
                return SamResult(
                    status="failed",
                    summary="Folder name or path is required.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing folder query",
                    next_action="ask_user",
                    metadata={"intent": "open_folder", "source": request.source},
                )
            open_result = self.project_inspector.tools.open_directory_query(query)
            open_result.metadata.setdefault("intent", "open_folder")
            open_result.metadata.setdefault("source", request.source)
            open_result.metadata.setdefault("confidence", request.confidence)
            return open_result

        if request.intent == "create_draft":
            title = str(request.parameters.get("title", "")).strip()
            body = str(request.parameters.get("body", "")).strip()
            if not title or not body:
                return SamResult(
                    status="failed",
                    summary="Draft title and body are required.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing title/body",
                    next_action="ask_user",
                )
            result, draft = self.pipeline_service.create_draft(
                title=title,
                body=body,
                content_type=request.parameters.get("content_type", "report"),
            )
            return self._service_result("create_draft", result, draft.id if draft else None)

        if request.intent == "list_workflows":
            result, drafts = self.pipeline_service.list_documents(limit=20)
            return self._service_result(
                "list_workflows",
                result,
                metadata={
                    "count": len(drafts),
                    "titles": [draft.title for draft in drafts],
                },
            )

        if request.intent == "push_changes":
            return self._request_push_approval(request)

        if request.intent == "inspect_repo":
            query = self._query_or_path_from_request(request)
            if not query:
                return SamResult(
                    status="success",
                    summary="I can inspect a repo, but I need the project path or registered project name first.",
                    next_action="ask_user",
                    metadata={
                        "intent": "inspect_repo",
                        "source": request.source,
                        "confidence": request.confidence,
                    },
                )
            inspect_result, inspection = self.project_inspector.inspect(query)
            if not inspect_result.ok or inspection is None:
                return self._service_result("inspect_repo", inspect_result, metadata={"query": query})
            metadata = inspection_metadata(inspection)
            metadata["intent"] = "inspect_repo"
            metadata["source"] = request.source
            metadata["confidence"] = request.confidence
            changed_summary = (
                "clean working tree"
                if inspection.is_clean
                else f"{len(inspection.changed_files)} changed file(s)"
            )
            return SamResult(
                status="success",
                summary=(
                    f"{inspection.name} is on branch {inspection.branch or 'unknown'} with a "
                    f"{changed_summary}."
                ),
                next_action="stop",
                metadata=metadata,
            )

        if request.intent == "inspect_project_repo":
            query = self._query_or_path_from_request(request)
            if not query:
                return SamResult(
                    status="failed",
                    summary="Project name is required for repo inspection.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing project query",
                    next_action="ask_user",
                    metadata={"intent": "inspect_project_repo", "source": request.source},
                )
            inspect_result, inspection = self.project_inspector.inspect(query)
            if not inspect_result.ok or inspection is None:
                return self._service_result("inspect_project_repo", inspect_result, metadata={"query": query})
            metadata = inspection_metadata(inspection)
            metadata["intent"] = "inspect_project_repo"
            metadata["source"] = request.source
            metadata["confidence"] = request.confidence
            changed_summary = (
                "clean working tree"
                if inspection.is_clean
                else f"{len(inspection.changed_files)} changed file(s)"
            )
            return SamResult(
                status="success",
                summary=(
                    f"{inspection.name} is on branch {inspection.branch or 'unknown'} with a "
                    f"{changed_summary}."
                ),
                next_action="stop",
                metadata=metadata,
            )

        if request.intent == "inspect_git_state":
            query = self._query_or_path_from_request(request)
            if not query:
                return SamResult(
                    status="failed",
                    summary="Project name is required for git inspection.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing project query",
                    next_action="ask_user",
                    metadata={"intent": "inspect_git_state", "source": request.source},
                )
            project_result, project = self.project_registry.find_project(query)
            project_id = ""
            project_name = ""
            repo_path = query
            if project_result.ok and project is not None:
                project_id = project.project_id
                project_name = project.name
                repo_path = project.root_path
            else:
                directory_result, directory = self.project_inspector.tools.resolve_directory_query(query)
                if not directory_result.ok or directory is None:
                    return self._service_result("inspect_git_state", project_result, metadata={"query": query})
                project_name = directory.name
                repo_path = str(directory)

            git_result, snapshot = self.project_inspector.tools.inspect_git_state(repo_path)
            if not git_result.ok or snapshot is None:
                return self._service_result("inspect_git_state", git_result, metadata={"query": query})
            return SamResult(
                status="success",
                summary=f"Git state for {project_name}: branch {snapshot.branch}, clean={snapshot.is_clean}.",
                next_action="stop",
                metadata={
                    "intent": "inspect_git_state",
                    "project_id": project_id,
                    "name": project_name,
                    "repo_root": snapshot.repo_root,
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

        if request.intent == "scan_codebase_patterns":
            query = self._query_or_path_from_request(request)
            root_result, root = self._resolve_project_or_directory(query, memory_block)
            if not root_result.ok or root is None:
                return self._service_result("scan_codebase_patterns", root_result, metadata={"query": query})
            patterns = self._patterns_from_request(request)
            if not patterns:
                return SamResult(
                    status="success",
                    summary="Which exact text patterns should I scan for?",
                    next_action="ask_user",
                    metadata={"intent": "scan_codebase_patterns", "root_path": str(root), "source": request.source},
                )
            scan_result, report = CodebaseCleanupService(root).inspect(patterns)
            metadata = dict(scan_result.metadata)
            metadata.update(
                {
                    "intent": "scan_codebase_patterns",
                    "root_path": str(root),
                    "patterns": patterns,
                    "source": request.source,
                    "confidence": request.confidence,
                }
            )
            sample = report.matches[:10]
            if scan_result.ok:
                if sample:
                    preview = "; ".join(
                        f"{match.path}:{match.line_number} ({match.pattern})"
                        for match in sample
                    )
                    summary = f"Found {len(report.matches)} match(es) for {', '.join(patterns)}. Sample: {preview}."
                else:
                    summary = f"No matches found for {', '.join(patterns)}."
                scan_result.summary = summary
            scan_result.metadata = metadata
            return scan_result

        if request.intent == "list_executor_tools":
            tools = self.tool_executor.list_metadata()
            names = [item.get("name", "") for item in tools if item.get("name")]
            preview = ", ".join(names[:20])
            if len(names) > 20:
                preview += f", and {len(names) - 20} more"
            return SamResult(
                status="success",
                summary=f"{len(names)} executor tool(s) registered: {preview}.",
                next_action="stop",
                metadata={"intent": "list_executor_tools", "tools": tools, "count": len(names)},
            )

        if request.intent == "list_worker_tasks":
            tasks = sorted(worker_monitor.list_tasks(), key=lambda item: item.created_at, reverse=True)[:20]
            task_items = [
                {
                    "task_id": task.task_id,
                    "name": task.name,
                    "worker_type": task.worker_type,
                    "worker_name": task.worker_name,
                    "description": task.description,
                    "status": task.status,
                    "elapsed_seconds": task.elapsed_seconds,
                    "error_message": task.error_message,
                    "last_output": task.output_lines[-3:],
                }
                for task in tasks
            ]
            if not task_items:
                summary = "No worker tasks are currently recorded in this runtime session."
            else:
                preview = "; ".join(f"{item['worker_name']}:{item['status']}:{item['name']}" for item in task_items[:8])
                summary = f"{len(task_items)} recent worker task(s): {preview}."
            return SamResult(
                status="success",
                summary=summary,
                next_action="stop",
                metadata={"intent": "list_worker_tasks", "tasks": task_items, "count": len(task_items)},
            )

        if request.intent == "check_python_syntax":
            query = self._query_or_path_from_request(request)
            root_result, root = self._resolve_project_or_directory(query, memory_block)
            if not root_result.ok or root is None:
                return self._service_result("check_python_syntax", root_result, metadata={"query": query})
            return self._check_python_syntax(root, request)

        if request.intent == "inspect_recent_changes":
            query = self._query_or_path_from_request(request)
            root_result, root = self._resolve_project_or_directory(query, memory_block)
            if not root_result.ok or root is None:
                return self._service_result("inspect_recent_changes", root_result, metadata={"query": query})
            return self._inspect_recent_changes(root, request)

        if request.intent in {"plan_request", "autonomous_request"}:
            return self._run_autonomous_loop(request, memory_block)

        return SamResult(
            status="success",
            summary=request.response_text or "No actionable intent matched; treating as chat.",
            next_action="stop",
            metadata={
                "intent": "chat",
                "message": request.raw_text,
                "source": request.source,
                "confidence": request.confidence,
            },
        )

    # ---------------------------------------------------------------------
    # =========================================================================
    # Phase 1: Executive Tool Registration
    # =========================================================================

    def _register_executor_tools(self) -> None:
        """Register all tools/intents as executable handlers.

        Delegates to:
        1. Comprehensive intent tool registry (_executor_tools_registry)
        2. Service tools registry (service_tools) for utility operations
        
        All intent business logic and service handlers are extracted into
        reusable tool handlers.
        """
        register_all_executor_tools(self)
        register_service_tools(
            self.tool_executor,
            repo_root=str(self.workspace_root),
            db_path=str(self.db_path),
        )

    def _execute_with_planner(self, request: IntentRequest, memory_block: dict[str, Any] | None) -> SamResult:
        """Create a plan and execute with observation loop for adaptive execution.

        Phase 5 plan-act-observe-continue cycle:
        1. Plan: TaskPlanner generates direct or multi-step plan
        2. Act: ObservationLoop executes via WorkerCentricExecutor with monitoring
        3. Observe: Extracts observations and results
        4. Continue: Makes adaptive decisions (retry, skip, ask user, etc.)
        """
        # Get available tools for planning context
        available_tools = self.tool_executor.available_tools
        
        # Create planning context with intent, available tools, and memory
        plan = self.task_planner.plan(
            request.intent,
            context={
                "request": request,
                "memory": memory_block,
                "intent": request.intent,
                "available_tools": available_tools,
            }
        )
        
        # Use observation loop for adaptive execution (handles direct and multi-step modes)
        result, step_executions = self.observation_loop.execute_plan(plan, memory_block)
        
        # Attach execution metadata
        result.metadata.setdefault("execution_steps", len(step_executions))
        result.metadata.setdefault("plan_mode", plan.mode)
        result.metadata.setdefault("request_intent", request.intent)
        result.metadata.setdefault("intent", request.intent)
        
        return result

    @staticmethod
    def _query_or_path_from_request(request: IntentRequest) -> str:
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

    def _apply_contextual_request(
        self,
        text: str,
        request: IntentRequest,
        memory_block: dict[str, Any] | None,
    ) -> IntentRequest:
        daily_state = memory_block.get("daily_state", {}) if isinstance(memory_block, dict) else {}
        last_file_path = str(daily_state.get("last_file_path", {}).get("value", "")).strip()
        last_project_root = str(daily_state.get("last_project_root_path", {}).get("value", "")).strip()

        if self._wants_summary(text):
            path = str(request.parameters.get("path", "")).strip()
            if not path:
                path = self._path_from_text(text)
            if not path and last_file_path:
                path = last_file_path
            if path:
                request.intent = "read_file"
                request.parameters["path"] = path
                request.source = request.source or "context"

        project_context_intents = {
            "inspect_repo",
            "inspect_project_repo",
            "inspect_git_state",
            "scan_codebase_patterns",
            "check_python_syntax",
            "inspect_recent_changes",
            "read_file",
            "list_directory",
        }
        if request.intent in project_context_intents and last_project_root:
            has_location = any(str(request.parameters.get(key, "")).strip() for key in ("query", "path", "repo_path", "root_path"))
            if not has_location and self._refers_to_current_context(text):
                key = "path" if request.intent in {"read_file", "list_directory"} else "query"
                request.parameters[key] = last_project_root

        return request

    @staticmethod
    def _path_from_text(text: str) -> str:
        match = re.search(r"[A-Za-z]:[\\/][^\r\n\"']+", text)
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

    @staticmethod
    def _wants_summary(text: str) -> bool:
        lowered = text.lower()
        return any(token in lowered for token in ("summarize", "summary", "summarise", "summarized version", "summarised version"))

    @staticmethod
    def _summarize_text(content: str, label: str = "file") -> str:
        normalized = re.sub(r"\s+", " ", content).strip()
        if not normalized:
            return f"{Path(label).name} is empty."

        sentences = re.split(r"(?<=[.!?])\s+", normalized)
        selected = [sentence.strip() for sentence in sentences if sentence.strip()][:4]
        if not selected:
            selected = [normalized[:500].strip()]
        summary = " ".join(selected)
        if len(summary) > 900:
            summary = summary[:897].rstrip() + "..."
        return f"Summary of {Path(label).name}: {summary}"

    @staticmethod
    def _refers_to_current_context(text: str) -> bool:
        lowered = text.lower()
        return any(phrase in lowered for phrase in ("this project", "this repo", "this file", "the project", "the repo", "it", "that project"))

    def _resolve_project_or_directory(
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
    def _patterns_from_request(request: IntentRequest) -> list[str]:
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

    def _check_python_syntax(self, root: Path, request: IntentRequest) -> SamResult:
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

    def _inspect_recent_changes(self, root: Path, request: IntentRequest) -> SamResult:
        git_result, snapshot = self.project_inspector.tools.inspect_git_state(root)
        if not git_result.ok or snapshot is None:
            return self._service_result("inspect_recent_changes", git_result, metadata={"root_path": str(root)})
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

    def _run_autonomous_loop(
        self,
        request: IntentRequest,
        memory_block: dict[str, Any] | None,
    ) -> SamResult:
        if not self.model_client.is_available():
            return SamResult(
                status="failed",
                summary="My local reasoning model is unavailable, so I cannot run an autonomous investigation.",
                error_type=ErrorType.MISSING_CAPABILITY,
                error_message="llm unavailable",
                next_action="ask_user",
                metadata={"intent": request.intent, "source": request.source},
            )

        tools = self._autonomous_tool_manifest()
        observations: list[dict[str, Any]] = []
        tool_trace: list[dict[str, Any]] = []

        for step_index in range(5):
            try:
                decision = self.model_client.choose_autonomous_action(
                    user_text=request.raw_text,
                    tools=tools,
                    observations=observations,
                    memory_block=memory_block,
                    workspace_root=str(self.workspace_root),
                )
            except Exception as exc:
                if observations:
                    return self._final_from_observations(request, observations, tool_trace, str(exc))
                return SamResult(
                    status="failed",
                    summary="Autonomous reasoning failed before I could choose a safe tool.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message=str(exc),
                    next_action="retry",
                    metadata={"intent": request.intent, "source": request.source},
                )

            action = str(decision.get("action", "")).lower()
            if action == "final":
                answer = str(decision.get("answer", "")).strip() or self._observations_summary(observations)
                return SamResult(
                    status="success",
                    summary=answer,
                    next_action="stop",
                    metadata={
                        "intent": "autonomous_request",
                        "source": request.source,
                        "confidence": request.confidence,
                        "autonomous_steps": len(tool_trace),
                        "tool_trace": tool_trace,
                        "observations": observations,
                    },
                )

            if action == "ask_user":
                question = str(decision.get("question", "")).strip() or "I need one more detail before I can continue."
                return SamResult(
                    status="success",
                    summary=question,
                    next_action="ask_user",
                    metadata={
                        "intent": "clarify",
                        "source": "autonomous_loop",
                        "autonomous_steps": len(tool_trace),
                        "tool_trace": tool_trace,
                        "observations": observations,
                    },
                )

            if action != "tool":
                observations.append({"step": step_index + 1, "status": "failed", "summary": "Model chose an unsupported action."})
                continue

            tool_name = str(decision.get("tool", "")).strip()
            arguments = decision.get("arguments", {})
            if not isinstance(arguments, dict):
                arguments = {}
            worker_name, worker_type = self._autonomous_worker_identity(tool_name)
            task = worker_monitor.create_task(
                name=f"autonomous_{tool_name or 'unknown'}",
                worker_type=worker_type,
                worker_name=worker_name,
                description=f"Autonomous step {step_index + 1}: {tool_name}",
            )
            worker_monitor.mark_running(task.task_id)
            worker_monitor.append_output(task.task_id, f"Tool: {tool_name}")
            if arguments:
                worker_monitor.append_output(task.task_id, f"Arguments: {arguments}")
            tool_result = self._execute_autonomous_tool(tool_name, arguments, request, memory_block)
            if tool_result.ok:
                worker_monitor.append_output(task.task_id, f"Observation: {tool_result.summary}")
                worker_monitor.mark_done(task.task_id)
            else:
                worker_monitor.append_output(task.task_id, f"Failure: {tool_result.error_message or tool_result.summary}")
                worker_monitor.mark_failed(task.task_id, tool_result.error_message or tool_result.summary)
            observation = self._compact_result(tool_name, arguments, tool_result, step_index + 1)
            observations.append(observation)
            tool_trace.append(
                {
                    "step": step_index + 1,
                    "tool": tool_name,
                    "worker_name": worker_name,
                    "worker_type": worker_type,
                    "arguments": arguments,
                    "status": tool_result.status,
                    "summary": tool_result.summary,
                }
            )

            if tool_result.next_action == "ask_user" and not tool_result.ok:
                return SamResult(
                    status="success",
                    summary=tool_result.summary,
                    next_action="ask_user",
                    metadata={
                        "intent": "clarify",
                        "source": "autonomous_loop",
                        "autonomous_steps": len(tool_trace),
                        "tool_trace": tool_trace,
                        "observations": observations,
                    },
                )

        return self._final_from_observations(request, observations, tool_trace, "")

    def _execute_autonomous_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        parent_request: IntentRequest,
        memory_block: dict[str, Any] | None,
    ) -> SamResult:
        allowed_tools = {item["name"] for item in self._autonomous_tool_manifest()}
        if tool_name not in allowed_tools:
            return SamResult(
                status="failed",
                summary=f"Tool {tool_name} is not available to the autonomous read-only loop.",
                error_type=ErrorType.MISSING_CAPABILITY,
                error_message=tool_name,
                next_action="ask_user",
                metadata={"intent": "autonomous_request"},
            )

        child = IntentRequest(
            intent=tool_name,
            parameters=dict(arguments),
            raw_text=parent_request.raw_text,
            confidence=parent_request.confidence,
            source="autonomous_loop",
        )

        if tool_name == "capabilities":
            result = self.awareness.describe_self()
            result.metadata.setdefault("intent", "capabilities")
            return result
        if tool_name == "list_projects":
            project_result, projects = self.project_registry.list_projects()
            if not project_result.ok:
                return self._service_result("list_projects", project_result)
            names = [project.name for project in projects]
            return SamResult(
                status="success",
                summary=f"I know about {len(names)} project(s): {', '.join(names)}.",
                next_action="stop",
                metadata={"intent": "list_projects", "count": len(names), "projects": names},
            )
        if tool_name == "list_executor_tools":
            tools = self.tool_executor.list_metadata()
            return SamResult(
                status="success",
                summary=f"{len(tools)} executor tool(s) registered.",
                next_action="stop",
                metadata={"intent": "list_executor_tools", "tools": tools, "count": len(tools)},
            )
        if tool_name == "list_worker_tasks":
            tasks = sorted(worker_monitor.list_tasks(), key=lambda item: item.created_at, reverse=True)[:20]
            return SamResult(
                status="success",
                summary=f"{len(tasks)} recent worker task(s) found.",
                next_action="stop",
                metadata={
                    "intent": "list_worker_tasks",
                    "tasks": [
                        {
                            "task_id": task.task_id,
                            "name": task.name,
                            "worker_name": task.worker_name,
                            "status": task.status,
                            "description": task.description,
                            "last_output": task.output_lines[-3:],
                        }
                        for task in tasks
                    ],
                },
            )
        if tool_name == "read_file":
            path = str(arguments.get("path", "")).strip()
            if not path:
                return SamResult(
                    status="failed",
                    summary="File path is required.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing path",
                    next_action="ask_user",
                    metadata={"intent": "read_file"},
                )
            additional_roots = self._memory_roots(memory_block)
            resolve_result, resolved = self.project_inspector.tools.resolve_file_query(path, additional_roots=additional_roots)
            if not resolve_result.ok or resolved is None:
                return self._service_result("read_file", resolve_result, metadata={"path": path})
            max_chars = int(arguments.get("max_chars", 6000) or 6000)
            file_result, content = self.project_inspector.tools.read_text_file(resolved, max_chars=max_chars)
            if file_result.ok and content is not None:
                file_result.metadata["content"] = content
                file_result.metadata.setdefault("intent", "read_file")
            return file_result
        if tool_name == "list_directory":
            path = str(arguments.get("path", "") or arguments.get("query", "")).strip()
            root_result, root = self._resolve_project_or_directory(path, memory_block)
            if not root_result.ok or root is None:
                return self._service_result("list_directory", root_result, metadata={"path": path})
            directory_result, entries = self.project_inspector.tools.list_directory(root)
            if directory_result.ok:
                directory_result.metadata.update({"intent": "list_directory", "entries": entries[:100]})
            return directory_result
        if tool_name in {"inspect_repo", "inspect_project_repo"}:
            query = str(arguments.get("query", "") or arguments.get("path", "")).strip()
            root_result, root = self._resolve_project_or_directory(query, memory_block)
            if not root_result.ok or root is None:
                return self._service_result(tool_name, root_result, metadata={"query": query})
            inspect_result, inspection = self.project_inspector.inspect(str(root))
            if not inspect_result.ok or inspection is None:
                return self._service_result(tool_name, inspect_result, metadata={"query": str(root)})
            metadata = inspection_metadata(inspection)
            metadata["intent"] = tool_name
            return SamResult(
                status="success",
                summary=f"{inspection.name} is on branch {inspection.branch or 'unknown'} with {len(inspection.changed_files)} changed file(s).",
                next_action="stop",
                metadata=metadata,
            )
        if tool_name == "inspect_git_state":
            query = str(arguments.get("query", "") or arguments.get("repo_path", "") or arguments.get("path", "")).strip()
            root_result, root = self._resolve_project_or_directory(query, memory_block)
            if not root_result.ok or root is None:
                return self._service_result("inspect_git_state", root_result, metadata={"query": query})
            git_result, snapshot = self.project_inspector.tools.inspect_git_state(root)
            if git_result.ok and snapshot is not None:
                git_result.metadata.update(
                    {
                        "intent": "inspect_git_state",
                        "repo_root": snapshot.repo_root,
                        "branch": snapshot.branch,
                        "is_clean": snapshot.is_clean,
                        "changed_files": snapshot.changed_files,
                        "staged_files": snapshot.staged_files,
                        "unstaged_files": snapshot.unstaged_files,
                        "untracked_files": snapshot.untracked_files,
                    }
                )
            return git_result
        if tool_name == "scan_codebase_patterns":
            query = str(arguments.get("query", "") or arguments.get("path", "")).strip()
            root_result, root = self._resolve_project_or_directory(query, memory_block)
            if not root_result.ok or root is None:
                return self._service_result("scan_codebase_patterns", root_result, metadata={"query": query})
            patterns = arguments.get("patterns", [])
            if isinstance(patterns, str):
                patterns = [patterns]
            patterns = [str(item).strip() for item in patterns if str(item).strip()]
            if not patterns:
                patterns = self._patterns_from_request(parent_request)
            if not patterns:
                return SamResult(
                    status="failed",
                    summary="Search patterns are required.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing patterns",
                    next_action="ask_user",
                    metadata={"intent": "scan_codebase_patterns", "root_path": str(root)},
                )
            scan_result, report = CodebaseCleanupService(root).inspect(patterns)
            scan_result.metadata.update(
                {
                    "intent": "scan_codebase_patterns",
                    "root_path": str(root),
                    "patterns": patterns,
                    "matches": [match.__dict__ for match in report.matches[:100]],
                    "match_count": len(report.matches),
                }
            )
            return scan_result
        if tool_name == "check_python_syntax":
            query = str(arguments.get("query", "") or arguments.get("path", "")).strip()
            root_result, root = self._resolve_project_or_directory(query, memory_block)
            if not root_result.ok or root is None:
                return self._service_result("check_python_syntax", root_result, metadata={"query": query})
            return self._check_python_syntax(root, child)
        if tool_name == "inspect_recent_changes":
            query = str(arguments.get("query", "") or arguments.get("path", "")).strip()
            root_result, root = self._resolve_project_or_directory(query, memory_block)
            if not root_result.ok or root is None:
                return self._service_result("inspect_recent_changes", root_result, metadata={"query": query})
            return self._inspect_recent_changes(root, child)
        if tool_name == "inspect_workspace_cleanup":
            scope = str(arguments.get("scope", "all") or "all")
            result, _metadata = self.workspace_cleanup.inspect(scope)
            result.metadata.setdefault("intent", "inspect_workspace_cleanup")
            return result

        return SamResult(
            status="failed",
            summary=f"Autonomous tool {tool_name} is not implemented.",
            error_type=ErrorType.MISSING_CAPABILITY,
            error_message=tool_name,
            next_action="ask_user",
            metadata={"intent": "autonomous_request"},
        )

    def _autonomous_tool_manifest(self) -> list[dict[str, Any]]:
        return [
            {"name": "capabilities", "description": "List available Sam capabilities.", "arguments": {}},
            {"name": "list_projects", "description": "List registered projects.", "arguments": {}},
            {"name": "read_file", "description": "Read a UTF-8 text file.", "arguments": {"path": "file path or name", "max_chars": 6000}},
            {"name": "list_directory", "description": "List files in a directory.", "arguments": {"path": "directory path or project path"}},
            {"name": "inspect_repo", "description": "Inspect repo branch, working tree, and top-level files.", "arguments": {"query": "project name or path"}},
            {"name": "inspect_git_state", "description": "Inspect git branch and changed files.", "arguments": {"query": "project name or path"}},
            {"name": "scan_codebase_patterns", "description": "Scan codebase for exact text patterns.", "arguments": {"query": "project name or path", "patterns": ["text"]}},
            {"name": "check_python_syntax", "description": "Parse Python files and report syntax errors.", "arguments": {"query": "project name or path"}},
            {"name": "inspect_recent_changes", "description": "Summarize current git working-tree changes.", "arguments": {"query": "project name or path"}},
            {"name": "inspect_workspace_cleanup", "description": "Inspect duplicate cleanup candidates without deleting.", "arguments": {"scope": "all|projects|runtime"}},
            {"name": "list_executor_tools", "description": "List registered executor tools.", "arguments": {}},
            {"name": "list_worker_tasks", "description": "List running and recent worker tasks.", "arguments": {}},
        ]

    @staticmethod
    def _autonomous_worker_identity(tool_name: str) -> tuple[str, str]:
        normalized = tool_name.lower()
        if any(token in normalized for token in ("scan", "inspect", "read", "list")):
            return "Inspector", "inspect"
        if any(token in normalized for token in ("syntax", "compile", "health", "test")):
            return "Nigel", "test"
        if "recent_changes" in normalized or "git" in normalized:
            return "Auditor", "inspect"
        return "Coordinator", "planning"

    @staticmethod
    def _memory_roots(memory_block: dict[str, Any] | None) -> list[str]:
        daily_state = memory_block.get("daily_state", {}) if isinstance(memory_block, dict) else {}
        roots = []
        for key in ("last_project_root_path", "last_file_path"):
            value = str(daily_state.get(key, {}).get("value", "")).strip()
            if value:
                roots.append(str(Path(value).parent if key == "last_file_path" else Path(value)))
        return roots

    @staticmethod
    def _compact_result(
        tool_name: str,
        arguments: dict[str, Any],
        result: SamResult,
        step: int,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        for key in (
            "intent",
            "path",
            "root_path",
            "repo_root",
            "branch",
            "is_clean",
            "changed_files",
            "entries",
            "entry_count",
            "patterns",
            "match_count",
            "matches",
            "errors",
            "files_scanned",
            "tasks",
            "tools",
            "projects",
            "content",
        ):
            if key in result.metadata:
                value = result.metadata[key]
                if key == "content" and isinstance(value, str):
                    value = value[:4000]
                elif isinstance(value, list):
                    value = value[:25]
                metadata[key] = value
        return {
            "step": step,
            "tool": tool_name,
            "arguments": arguments,
            "status": result.status,
            "summary": result.summary,
            "error": result.error_message,
            "metadata": metadata,
        }

    def _final_from_observations(
        self,
        request: IntentRequest,
        observations: list[dict[str, Any]],
        tool_trace: list[dict[str, Any]],
        reason: str,
    ) -> SamResult:
        summary = self._observations_summary(observations)
        if reason:
            summary += f" I stopped after observation because final synthesis failed: {reason}"
        return SamResult(
            status="success" if observations else "failed",
            summary=summary,
            error_type=None if observations else ErrorType.TOOL_FAILED,
            error_message=reason or None,
            next_action="stop" if observations else "retry",
            metadata={
                "intent": "autonomous_request",
                "source": request.source,
                "confidence": request.confidence,
                "autonomous_steps": len(tool_trace),
                "tool_trace": tool_trace,
                "observations": observations,
            },
        )

    @staticmethod
    def _observations_summary(observations: list[dict[str, Any]]) -> str:
        if not observations:
            return "I could not gather enough observations to answer."
        lines = [str(item.get("summary", "")).strip() for item in observations if str(item.get("summary", "")).strip()]
        return " ".join(lines[-3:]) or "I gathered observations, but they did not include a clear answer."


    def _parse_with_llm(self, text: str, memory_block: dict[str, Any] | None = None) -> IntentRequest | None:
        if not text:
            return None
        if not self.model_client.is_available():
            return None
        try:
            list_result, projects = self.project_registry.list_projects()
            known_projects = []
            if list_result.ok:
                known_projects = [
                    {"project_id": project.project_id, "name": project.name, "root_path": project.root_path}
                    for project in projects[:25]
                ]

            output = self.model_client.classify_request(
                text,
                capabilities=[item.intent for item in self.registry.list_all()],
                memory_block=memory_block,
                known_projects=known_projects,
                workspace_root=str(self.workspace_root),
            )
        except Exception:
            return None
        return self._intent_request_from_llm(text, output)

    def _intent_request_from_llm(self, text: str, output: OllamaIntentOutput) -> IntentRequest:
        supported_intents = {item.intent for item in self.registry.list_all()}
        raw_intent = (output.intent or "chat").strip()

        if raw_intent not in supported_intents:
            return IntentRequest(
                intent=raw_intent or "unknown",
                parameters=output.parameters or {},
                raw_text=text,
                needs_clarification=False,
                response_text=(
                    f"I understood this as `{raw_intent}`, but I do not have that capability registered yet. "
                    "I can propose an upgrade for it if you want."
                ),
                confidence=output.confidence or "low",
                source=output.source or "llm",
            )

        return IntentRequest(
            intent=raw_intent,
            parameters=output.parameters or {},
            raw_text=text,
            needs_clarification=output.needs_clarification,
            clarification_question=output.clarification_question,
            response_text=output.response_text,
            confidence=output.confidence,
            source=output.source or "llm",
        )

    @staticmethod
    def _looks_like_file_query(text: str) -> bool:
        candidate = text.strip().lower()
        if not candidate:
            return False
        if any(sep in candidate for sep in ("\\", "/")):
            return True
        known_extensions = (
            ".md",
            ".txt",
            ".py",
            ".json",
            ".html",
            ".js",
            ".css",
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".csv",
            ".log",
        )
        return candidate.endswith(known_extensions)

    @staticmethod
    def _is_meaningful_llm_request(request: IntentRequest) -> bool:
        return True

    def _request_push_approval(self, request: IntentRequest) -> SamResult:
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
            agent_id="sam_v2_router",
            agent_name="Sam v2 Router",
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

    def _check_authority(self, request: IntentRequest, action_category: str) -> SamResult | None:
        if self.authority_engine is None:
            return None

        decision = self.authority_engine.check(
            agent_id="sam_v2_router",
            agent_level=5,
            role_id="supervisor",
            tool_name=request.intent,
            action_category=action_category,
        )
        if not decision.allowed:
            return SamResult(
                status="blocked",
                summary="Intent blocked by authority rules.",
                error_type=ErrorType.MISSING_PERMISSION,
                error_message=decision.reason,
                next_action="request_approval",
                metadata={"intent": request.intent, "action_category": action_category},
            )

        if decision.requires_approval:
            if self.approval_manager is None:
                return SamResult(
                    status="needs_approval",
                    summary="Intent requires approval.",
                    error_type=ErrorType.MISSING_PERMISSION,
                    error_message=decision.reason,
                    next_action="request_approval",
                    metadata={"intent": request.intent, "action_category": action_category},
                )

            create_result, approval = self.approval_manager.create_request(
                agent_id="sam_v2_router",
                agent_name="Sam v2 Router",
                tool_name=request.intent,
                tool_arguments=request.parameters,
                action_category=action_category,
                reason=decision.reason,
                context=request.raw_text,
            )
            if not create_result.ok or approval is None:
                return SamResult(
                    status="failed",
                    summary="Approval was required but request creation failed.",
                    error_type=create_result.error_type or ErrorType.FILE_ACCESS_ERROR,
                    error_message=create_result.error_message,
                    next_action="retry",
                )

            return SamResult(
                status="needs_approval",
                summary="Intent requires approval before execution.",
                error_type=ErrorType.MISSING_PERMISSION,
                error_message=decision.reason,
                next_action="request_approval",
                metadata={"approval_id": approval.id, "intent": request.intent},
            )

        return None

    def _service_result(
        self,
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

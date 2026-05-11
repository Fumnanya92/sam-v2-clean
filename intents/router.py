"""Minimal intent parser and router for Sam v2."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

from sam_v2.approvals import ApprovalManager, AuthorityEngine
from sam_v2.capabilities import CapabilityAwarenessService, CapabilityRegistry, build_default_registry
from sam_v2.diagnostics.error_types import ErrorType
from sam_v2.diagnostics.result import SamResult
from sam_v2.llm import OllamaClient, OllamaIntentOutput
from sam_v2.projects import (
    ProjectExecutionRequest,
    ProjectInspector,
    ProjectPlanRequest,
    ProjectPlanner,
    ProjectRegistry,
    ProjectScaffoldRequest,
    ProjectScaffolder,
    inspection_metadata,
)
from sam_v2.storage import TaskRecord, create_task, list_tasks, update_task
from sam_v2.tools import SafeLocalTools, WorkspaceCleanupService
from sam_v2.upgrades import UpgradeProposalManager
from sam_v2.workers import CommandSpec, ToolingWorker
from sam_v2.workflows import GoalService, PipelineService


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
            return llm_request

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
        not by tic-tac/game-specific phrase rules.
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
            file_result, content = self.project_inspector.tools.read_text_file(path_text)
            if not file_result.ok or content is None:
                return self._service_result("read_file", file_result, metadata={"path": path_text})
            return SamResult(
                status="success",
                summary="File read succeeded.",
                next_action="stop",
                metadata={
                    "intent": "read_file",
                    "path": file_result.metadata.get("path", path_text),
                    "content": content,
                    "chars_returned": file_result.metadata.get("chars_returned", len(content)),
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
            return SamResult(
                status="success",
                summary="Directory listing succeeded.",
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

        if request.intent == "count_tictac_projects":
            project_result, projects = self.project_registry.list_projects()
            if not project_result.ok:
                return self._service_result("count_tictac_projects", project_result)
            tictac_projects = [
                project
                for project in projects
                if any(
                    token in project.name.lower()
                    for token in ("tictac", "tic tac", "tic-tac", "tic tac toe")
                )
            ]
            if not tictac_projects:
                daily_state = memory_block.get("daily_state", {}) if isinstance(memory_block, dict) else {}
                last_created_name = str(daily_state.get("last_created_project_name", {}).get("value", "")).strip().lower()
                if last_created_name:
                    tictac_projects = [
                        project
                        for project in projects
                        if project.name.lower() == last_created_name
                    ]
            latest = tictac_projects[-1] if tictac_projects else None
            if not tictac_projects:
                return SamResult(
                    status="success",
                    summary="I have not created any tic-tac game projects yet.",
                    next_action="stop",
                    metadata={
                        "intent": "count_tictac_projects",
                        "count": 0,
                        "projects": [],
                        "source": request.source,
                        "confidence": request.confidence,
                    },
                )
            latest_text = (
                f" The latest one is {latest.name}."
                if latest is not None
                else ""
            )
            return SamResult(
                status="success",
                summary=f"I have created {len(tictac_projects)} tic-tac game project(s) so far.{latest_text}",
                next_action="stop",
                metadata={
                    "intent": "count_tictac_projects",
                    "count": len(tictac_projects),
                    "projects": [project.name for project in tictac_projects],
                    "latest_project_name": latest.name if latest is not None else "",
                    "latest_project_root_path": latest.root_path if latest is not None else "",
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

        if request.intent == "inspect_project_repo":
            query = str(request.parameters.get("query", "")).strip()
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
            query = str(request.parameters.get("query", "")).strip()
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
            if not project_result.ok or project is None:
                return self._service_result("inspect_git_state", project_result, metadata={"query": query})
            git_result, snapshot = self.project_inspector.tools.inspect_git_state(project.root_path)
            if not git_result.ok or snapshot is None:
                return self._service_result("inspect_git_state", git_result, metadata={"query": query})
            return SamResult(
                status="success",
                summary=f"Git state for {project.name}: branch {snapshot.branch}, clean={snapshot.is_clean}.",
                next_action="stop",
                metadata={
                    "intent": "inspect_git_state",
                    "project_id": project.project_id,
                    "name": project.name,
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

        if request.intent == "plan_request":
            return SamResult(
                status="success",
                summary="I can help with that, but I need the project name or the specific issue first.",
                next_action="ask_user",
                metadata={
                    "intent": "plan_request",
                    "source": request.source,
                    "confidence": request.confidence,
                },
            )

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

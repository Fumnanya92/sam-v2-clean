"""Phase 1: All executor tool handlers extracted from router.

This module contains the complete tool registry setup that used to be
scattered across the router's massive handle() method.

Each tool is registered with the executor, taking a payload dict containing:
- request: IntentRequest
- memory: optional memory_block
"""

from __future__ import annotations

from typing import Any

from approvals import ApprovalManager, AuthorityEngine
from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult
from projects import ProjectExecutionRequest, ProjectPlanRequest, ProjectScaffoldRequest, inspection_metadata
from storage import TaskRecord, create_task, list_tasks, update_task
from workers import CommandSpec


def register_all_executor_tools(router: Any) -> None:
    """Register all intent handlers as executor tools.

    Args:
        router: The IntentRouter instance. Used for accessing services like
                goal_service, project_inspector, project_planner, etc.
    """
    executor = router.tool_executor

    # =========================================================================
    # Metadata & Capability Tools
    # =========================================================================

    executor.register(
        "capabilities",
        lambda payload: router.awareness.describe_self(),
        description="Describe Sam's capabilities and awareness",
        action_category="read_data",
    )

    def _awareness_check_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        capability_name = str((request.parameters if request else {}).get("capability_name", "")).strip()
        if not capability_name:
            return SamResult(
                status="failed",
                summary="Capability name is required.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing capability name",
                next_action="ask_user",
                metadata={"intent": "awareness_check"},
            )
        return router.awareness.check_request(capability_name)

    executor.register(
        "awareness_check",
        _awareness_check_handler,
        description="Check availability of a specific capability",
        action_category="read_data",
    )

    def _propose_upgrade_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        capability_name = str((request.parameters if request else {}).get("capability_name", "")).strip().replace(" ", "_")
        if not capability_name:
            return SamResult(
                status="failed",
                summary="Capability name is required for an upgrade proposal.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing capability name",
                next_action="ask_user",
                metadata={"intent": "propose_upgrade"},
            )
        return router.awareness.propose_upgrade(
            capability_name,
            f"User requested upgrade support for {capability_name}.",
        )

    executor.register(
        "propose_upgrade",
        _propose_upgrade_handler,
        description="Propose a capability upgrade",
        action_category="read_data",
    )

    # =========================================================================
    # Task & Goal Management
    # =========================================================================

    def _list_tasks_handler(payload: dict[str, Any] | None = None) -> SamResult:
        task_result, tasks = list_tasks(router.db_path)
        if not task_result.ok:
            return router._service_result("list_tasks", task_result)
        if not tasks:
            return SamResult(
                status="success",
                summary="I do not have any tracked tasks yet.",
                next_action="ask_user",
                metadata={"intent": "list_tasks", "count": 0, "tasks": []},
            )
        task_items = [
            {"id": t.id, "title": t.title, "status": t.status, "priority": t.priority, "notes": t.notes}
            for t in tasks
        ]
        preview = ", ".join(f"#{item['id']} {item['title']} [{item['status']}]" for item in task_items[:3])
        remaining = len(task_items) - min(len(task_items), 3)
        if remaining > 0:
            preview = f"{preview}, and {remaining} more"
        return SamResult(
            status="success",
            summary=f"I have {len(task_items)} tracked task(s): {preview}.",
            next_action="stop",
            metadata={"intent": "list_tasks", "count": len(task_items), "tasks": task_items},
        )

    executor.register(
        "list_tasks",
        _list_tasks_handler,
        description="List all tracked tasks",
        action_category="read_data",
    )

    def _create_task_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        title = str((request.parameters if request else {}).get("title", "")).strip()
        if not title:
            return SamResult(
                status="failed",
                summary="Task title is required.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing title",
                next_action="ask_user",
                metadata={"intent": "create_task"},
            )
        result, task_id = create_task(router.db_path, TaskRecord(title=title))
        return router._service_result("create_task", result, identifier=str(task_id) if task_id is not None else None)

    executor.register(
        "create_task",
        _create_task_handler,
        description="Create a new tracked task",
        action_category="write_data",
        requires_write=True,
    )

    def _update_task_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        params = request.parameters if request else {}
        task_id_text = str(params.get("task_id", "")).strip()
        status_text = str(params.get("status", "")).strip()
        notes_text = str(params.get("notes", "")).strip()
        if not task_id_text.isdigit():
            return SamResult(
                status="failed",
                summary="Task id is required.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing or invalid task id",
                next_action="ask_user",
                metadata={"intent": "update_task"},
            )
        if not status_text and not notes_text:
            return SamResult(
                status="failed",
                summary="Task update needs a status or notes value.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing update values",
                next_action="ask_user",
                metadata={"intent": "update_task"},
            )
        result, task = update_task(
            router.db_path,
            int(task_id_text),
            status=status_text or None,
            notes=notes_text or None,
        )
        return router._service_result("update_task", result, identifier=str(task.id) if task is not None else task_id_text)

    executor.register(
        "update_task",
        _update_task_handler,
        description="Update task status or notes",
        action_category="write_data",
        requires_write=True,
    )

    def _list_goals_handler(payload: dict[str, Any] | None = None) -> SamResult:
        result, goals = router.goal_service.list_goals(status="active")
        return router._service_result("list_goals", result, metadata={"count": len(goals), "goals": goals})

    executor.register(
        "list_goals",
        _list_goals_handler,
        description="List active goals",
        action_category="read_data",
    )

    def _create_goal_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        title = str((request.parameters if request else {}).get("title", "")).strip()
        if not title:
            return SamResult(
                status="failed",
                summary="Goal title is required.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing title",
                next_action="ask_user",
                metadata={"intent": "create_goal"},
            )
        result, goal = router.goal_service.create_goal(title=title)
        return router._service_result(
            "create_goal",
            result,
            goal.id if goal else None,
            metadata={"source": "executor"},
        )

    executor.register(
        "create_goal",
        _create_goal_handler,
        description="Create a new goal",
        action_category="write_data",
        requires_write=True,
    )

    # =========================================================================
    # File & Directory Operations
    # =========================================================================

    def _read_file_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        path_text = str((request.parameters if request else {}).get("path", "")).strip()
        if not path_text:
            return SamResult(
                status="failed",
                summary="File path is required.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing file path",
                next_action="ask_user",
                metadata={"intent": "read_file"},
            )
        file_result, content = router.project_inspector.tools.read_text_file(path_text)
        if not file_result.ok or content is None:
            return router._service_result("read_file", file_result, metadata={"path": path_text})
        return SamResult(
            status="success",
            summary="File read succeeded.",
            next_action="stop",
            metadata={
                "intent": "read_file",
                "path": file_result.metadata.get("path", path_text),
                "content": content,
                "chars_returned": file_result.metadata.get("chars_returned", len(content)),
            },
        )

    executor.register(
        "read_file",
        _read_file_handler,
        description="Read text file contents",
        action_category="read_data",
    )

    def _open_file_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        memory_block = payload.get("memory") if payload else None
        path_text = str((request.parameters if request else {}).get("path", "")).strip()
        if not path_text:
            return SamResult(
                status="failed",
                summary="File path is required.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing file path",
                next_action="ask_user",
                metadata={"intent": "open_file"},
            )
        additional_roots: list[str] = []
        if memory_block:
            daily_state = memory_block.get("daily_state", {}) if isinstance(memory_block, dict) else {}
            last_project_root = str(daily_state.get("last_project_root_path", {}).get("value", "")).strip()
            if last_project_root:
                additional_roots.append(last_project_root)
        return router.project_inspector.tools.open_file_query(path_text, additional_roots=additional_roots)

    executor.register(
        "open_file",
        _open_file_handler,
        description="Open a file in the editor",
        action_category="write_data",
    )

    def _list_directory_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        path_text = str((request.parameters if request else {}).get("path", "")).strip()
        if not path_text:
            return SamResult(
                status="failed",
                summary="Directory path is required.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing directory path",
                next_action="ask_user",
                metadata={"intent": "list_directory"},
            )
        directory_result, entries = router.project_inspector.tools.list_directory(path_text)
        if not directory_result.ok:
            return router._service_result("list_directory", directory_result, metadata={"path": path_text})
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
            },
        )

    executor.register(
        "list_directory",
        _list_directory_handler,
        description="List directory contents",
        action_category="read_data",
    )

    def _open_folder_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        query = str((request.parameters if request else {}).get("query", "")).strip()
        if not query:
            return SamResult(
                status="failed",
                summary="Folder name or path is required.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing folder query",
                next_action="ask_user",
                metadata={"intent": "open_folder"},
            )
        return router.project_inspector.tools.open_directory_query(query)

    executor.register(
        "open_folder",
        _open_folder_handler,
        description="Open a folder in the editor",
        action_category="write_data",
    )

    # =========================================================================
    # Approval & Workspace Management
    # =========================================================================

    def _list_approvals_handler(payload: dict[str, Any] | None = None) -> SamResult:
        if not router.approval_manager:
            return SamResult(
                status="failed",
                summary="Approval manager is not configured.",
                error_type=ErrorType.MISSING_CAPABILITY,
                next_action="ask_user",
                metadata={"intent": "list_approvals"},
            )
        approval_result, approvals = router.approval_manager.list_pending()
        if not approval_result.ok:
            return router._service_result("list_approvals", approval_result)
        if not approvals:
            return SamResult(
                status="success",
                summary="I do not have any pending approvals right now.",
                next_action="stop",
                metadata={"intent": "list_approvals", "count": 0, "approvals": []},
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
            metadata={"intent": "list_approvals", "count": len(approval_items), "approvals": approval_items},
        )

    executor.register(
        "list_approvals",
        _list_approvals_handler,
        description="List pending approvals",
        action_category="read_data",
    )

    def _inspect_workspace_cleanup_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        scope = str((request.parameters if request else {}).get("scope", "all")).strip() or "all"
        inspect_result, _metadata = router.workspace_cleanup.inspect(scope)
        inspect_result.metadata.setdefault("intent", "inspect_workspace_cleanup")
        inspect_result.next_action = "ask_user"
        return inspect_result

    executor.register(
        "inspect_workspace_cleanup",
        _inspect_workspace_cleanup_handler,
        description="Inspect workspace for duplicates/cleanup",
        action_category="read_data",
    )

    def _cleanup_workspace_duplicates_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        scope = str((request.parameters if request else {}).get("scope", "all")).strip() or "all"
        return router.workspace_cleanup.cleanup(scope)

    executor.register(
        "cleanup_workspace_duplicates",
        _cleanup_workspace_duplicates_handler,
        description="Clean up duplicate workspace files",
        action_category="write_data",
        requires_write=True,
    )

    # =========================================================================
    # Project Management
    # =========================================================================

    def _list_projects_handler(payload: dict[str, Any] | None = None) -> SamResult:
        result, projects = router.project_registry.list_projects()
        if not result.ok:
            return router._service_result("list_projects", result)
        if not projects:
            return SamResult(
                status="success",
                summary="I do not have any registered projects yet.",
                next_action="ask_user",
                metadata={"intent": "list_projects", "count": 0, "projects": []},
            )
        names = [project.name for project in projects]
        return SamResult(
            status="success",
            summary=f"I know about {len(names)} project(s): {', '.join(names)}.",
            next_action="stop",
            metadata={"intent": "list_projects", "count": len(names), "projects": names},
        )

    executor.register(
        "list_projects",
        _list_projects_handler,
        description="List known projects",
        action_category="read_data",
    )

    def _scaffold_project_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        project_name = str((request.parameters if request else {}).get("name", "")).strip()
        project_type = str((request.parameters if request else {}).get("project_type", "html_game")).strip() or "html_game"
        return router.project_scaffolder.scaffold(
            ProjectScaffoldRequest(name=project_name, project_type=project_type)
        )

    executor.register(
        "scaffold_project",
        _scaffold_project_handler,
        description="Create a new project from a template",
        action_category="write_data",
        requires_write=True,
    )

    def _project_details_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        memory_block = payload.get("memory") if payload else None
        query = str((request.parameters if request else {}).get("query", "")).strip()
        if request and request.parameters.get("use_memory"):
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
                metadata={"intent": "project_details"},
            )
        project_result, project = router.project_registry.find_project(query)
        if not project_result.ok or project is None:
            return router._service_result("project_details", project_result, metadata={"query": query})
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
            },
        )

    executor.register(
        "project_details",
        _project_details_handler,
        description="Get details about a project",
        action_category="read_data",
    )

    def _run_project_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        memory_block = payload.get("memory") if payload else None
        query = str((request.parameters if request else {}).get("query", "")).strip()
        if request and request.parameters.get("use_memory"):
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
                metadata={"intent": "run_project"},
            )
        project_result, project = router.project_registry.find_project(query)
        if not project_result.ok or project is None:
            return router._service_result("run_project", project_result, metadata={"query": query})
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
                },
            )
        worker_result, _task = router.tooling_worker.execute(
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
        return worker_result

    executor.register(
        "run_project",
        _run_project_handler,
        description="Run a project's run command",
        action_category="execute_command",
        requires_write=True,
    )

    def _open_project_folder_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        memory_block = payload.get("memory") if payload else None
        query = str((request.parameters if request else {}).get("query", "")).strip()
        if request and request.parameters.get("use_memory"):
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
                metadata={"intent": "open_project_folder"},
            )
        project_result, project = router.project_registry.find_project(query)
        if not project_result.ok or project is None:
            return router._service_result("open_project_folder", project_result, metadata={"query": query})
        open_result = router.project_inspector.tools.open_directory(project.root_path)
        open_result.metadata.setdefault("intent", "open_project_folder")
        open_result.metadata.setdefault("project_id", project.project_id)
        open_result.metadata.setdefault("name", project.name)
        open_result.metadata.setdefault("root_path", project.root_path)
        return open_result

    executor.register(
        "open_project_folder",
        _open_project_folder_handler,
        description="Open a project folder in the editor",
        action_category="write_data",
    )

    def _inspect_project_repo_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        query = str((request.parameters if request else {}).get("query", "")).strip()
        if not query:
            return SamResult(
                status="failed",
                summary="Project name is required for repo inspection.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing project query",
                next_action="ask_user",
                metadata={"intent": "inspect_project_repo"},
            )
        inspect_result, inspection = router.project_inspector.inspect(query)
        if not inspect_result.ok or inspection is None:
            return router._service_result("inspect_project_repo", inspect_result, metadata={"query": query})
        metadata = inspection_metadata(inspection)
        metadata["intent"] = "inspect_project_repo"
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

    executor.register(
        "inspect_project_repo",
        _inspect_project_repo_handler,
        description="Inspect a project's repository state",
        action_category="read_data",
    )

    def _inspect_git_state_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        query = str((request.parameters if request else {}).get("query", "")).strip()
        if not query:
            return SamResult(
                status="failed",
                summary="Project name is required for git inspection.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing project query",
                next_action="ask_user",
                metadata={"intent": "inspect_git_state"},
            )
        project_result, project = router.project_registry.find_project(query)
        if not project_result.ok or project is None:
            return router._service_result("inspect_git_state", project_result, metadata={"query": query})
        git_result, snapshot = router.project_inspector.tools.inspect_git_state(project.root_path)
        if not git_result.ok or snapshot is None:
            return router._service_result("inspect_git_state", git_result, metadata={"query": query})
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
            },
        )

    executor.register(
        "inspect_git_state",
        _inspect_git_state_handler,
        description="Inspect git state of a project",
        action_category="read_data",
    )

    # =========================================================================
    # Project Planning & Execution
    # =========================================================================

    def _plan_project_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        query = str((request.parameters if request else {}).get("query", "")).strip()
        if not query:
            return SamResult(
                status="failed",
                summary="Project name is required to plan a project.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing project query",
                next_action="ask_user",
                metadata={"intent": "plan_project"},
            )
        return router.project_planner.plan(ProjectPlanRequest(query=query))

    executor.register(
        "plan_project",
        _plan_project_handler,
        description="Plan a project's work",
        action_category="read_data",
    )

    def _show_delegation_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        query = str((request.parameters if request else {}).get("query", "")).strip()
        if not query:
            return SamResult(
                status="failed",
                summary="Project name is required to show delegation.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing project query",
                next_action="ask_user",
                metadata={"intent": "show_delegation"},
            )
        return router.project_planner.show_delegation(query)

    executor.register(
        "show_delegation",
        _show_delegation_handler,
        description="Show project delegation status",
        action_category="read_data",
    )

    def _show_project_progress_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        query = str((request.parameters if request else {}).get("query", "")).strip()
        if not query:
            return SamResult(
                status="failed",
                summary="Project name is required to show progress.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing project query",
                next_action="ask_user",
                metadata={"intent": "show_project_progress"},
            )
        return router.project_planner.show_progress(query)

    executor.register(
        "show_project_progress",
        _show_project_progress_handler,
        description="Show project progress",
        action_category="read_data",
    )

    def _show_project_status_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        query = str((request.parameters if request else {}).get("query", "")).strip()
        if not query:
            return SamResult(
                status="failed",
                summary="Project name is required to show status.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing project query",
                next_action="ask_user",
                metadata={"intent": "show_project_status"},
            )
        return router.project_planner.show_status(query)

    executor.register(
        "show_project_status",
        _show_project_status_handler,
        description="Show project status",
        action_category="read_data",
    )

    def _execute_project_task_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        query = str((request.parameters if request else {}).get("query", "")).strip()
        task_name = str((request.parameters if request else {}).get("task_name", "")).strip()
        if not query or not task_name:
            return SamResult(
                status="failed",
                summary="Project name and delegated task are required.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing project query or task name",
                next_action="ask_user",
                metadata={"intent": "execute_project_task"},
            )
        return router.project_planner.execute_task(
            ProjectExecutionRequest(query=query, task_name=task_name)
        )

    executor.register(
        "execute_project_task",
        _execute_project_task_handler,
        description="Execute a project task",
        action_category="execute_command",
        requires_write=True,
    )

    # =========================================================================
    # Workflow & Document Management
    # =========================================================================

    def _create_draft_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        title = str((request.parameters if request else {}).get("title", "")).strip()
        body = str((request.parameters if request else {}).get("body", "")).strip()
        if not title or not body:
            return SamResult(
                status="failed",
                summary="Draft title and body are required.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing title/body",
                next_action="ask_user",
                metadata={"intent": "create_draft"},
            )
        result, draft = router.pipeline_service.create_draft(
            title=title,
            body=body,
            content_type=(request.parameters if request else {}).get("content_type", "report"),
        )
        return router._service_result("create_draft", result, draft.id if draft else None)

    executor.register(
        "create_draft",
        _create_draft_handler,
        description="Create a workflow draft",
        action_category="write_data",
        requires_write=True,
    )

    def _list_workflows_handler(payload: dict[str, Any] | None = None) -> SamResult:
        result, drafts = router.pipeline_service.list_documents(limit=20)
        return router._service_result(
            "list_workflows",
            result,
            metadata={
                "count": len(drafts),
                "titles": [draft.title for draft in drafts],
            },
        )

    executor.register(
        "list_workflows",
        _list_workflows_handler,
        description="List workflow documents",
        action_category="read_data",
    )

    # =========================================================================
    # Repository & Planning (generic stubs)
    # =========================================================================

    def _inspect_repo_handler(payload: dict[str, Any] | None = None) -> SamResult:
        return SamResult(
            status="success",
            summary="I can inspect a repo, but I need the project path or registered project name first.",
            next_action="ask_user",
            metadata={"intent": "inspect_repo"},
        )

    executor.register(
        "inspect_repo",
        _inspect_repo_handler,
        description="Inspect a repository (needs project path)",
        action_category="read_data",
    )

    def _plan_request_handler(payload: dict[str, Any] | None = None) -> SamResult:
        return SamResult(
            status="success",
            summary="I can help with that, but I need the project name or the specific issue first.",
            next_action="ask_user",
            metadata={"intent": "plan_request"},
        )

    executor.register(
        "plan_request",
        _plan_request_handler,
        description="Plan a generic request",
        action_category="read_data",
    )

    def _push_changes_handler(payload: dict[str, Any] | None = None) -> SamResult:
        request = payload.get("request") if payload else None
        return router._request_push_approval(request)

    executor.register(
        "push_changes",
        _push_changes_handler,
        description="Push git changes (requires approval)",
        action_category="execute_command",
        requires_write=True,
    )

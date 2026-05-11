"""Capability registry for the Sam v2 intent layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Capability:
    intent: str
    description: str
    action_category: str
    requires_write: bool = False


class CapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}

    def register(self, capability: Capability) -> None:
        self._capabilities[capability.intent] = capability

    def get(self, intent: str) -> Capability | None:
        return self._capabilities.get(intent)

    def list_all(self) -> list[Capability]:
        return sorted(self._capabilities.values(), key=lambda item: item.intent)


def build_default_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register(
        Capability(
            intent="capabilities",
            description="List currently migrated Sam v2 capabilities.",
            action_category="read_data",
        )
    )
    registry.register(
        Capability(
            intent="awareness_check",
            description="Truthfully report whether a requested capability currently exists.",
            action_category="read_data",
        )
    )
    registry.register(
        Capability(
            intent="propose_upgrade",
            description="Record an approval-gated upgrade proposal for a missing capability.",
            action_category="write_data",
            requires_write=True,
        )
    )
    registry.register(
        Capability(
            intent="chat",
            description="Fallback conversational response when no actionable intent matches.",
            action_category="read_data",
        )
    )
    registry.register(
        Capability(
            intent="plan_request",
            description="Higher-level request that needs planning or clarification before action.",
            action_category="read_data",
        )
    )
    registry.register(
        Capability(
            intent="create_goal",
            description="Create a new goal record in the workflow store.",
            action_category="write_data",
            requires_write=True,
        )
    )
    registry.register(
        Capability(
            intent="create_task",
            description="Create a simple task record in the storage layer.",
            action_category="write_data",
            requires_write=True,
        )
    )
    registry.register(
        Capability(
            intent="update_task",
            description="Update a simple task record in the storage layer.",
            action_category="write_data",
            requires_write=True,
        )
    )
    registry.register(
        Capability(
            intent="list_tasks",
            description="List task records from the storage layer.",
            action_category="read_data",
        )
    )
    registry.register(
        Capability(
            intent="list_approvals",
            description="List pending approval requests from the approval store.",
            action_category="read_data",
        )
    )
    registry.register(
        Capability(
            intent="read_file",
            description="Read a UTF-8 text file from the local workspace or project path.",
            action_category="read_data",
        )
    )
    registry.register(
        Capability(
            intent="open_file",
            description="Open a local file such as README.md, a project file, or an explicit file path.",
            action_category="execute_command",
            requires_write=True,
        )
    )
    registry.register(
        Capability(
            intent="list_directory",
            description="List entries in a local directory from the workspace or project path.",
            action_category="read_data",
        )
    )
    registry.register(
        Capability(
            intent="open_project_folder",
            description="Open a registered project's root folder on the local machine.",
            action_category="execute_command",
            requires_write=True,
        )
    )
    registry.register(
        Capability(
            intent="open_folder",
            description="Open a local folder such as Downloads, Documents, Desktop, Sam-Agent, or an explicit path.",
            action_category="execute_command",
            requires_write=True,
        )
    )
    registry.register(
        Capability(
            intent="inspect_workspace_cleanup",
            description="Inspect the Sam v2 workspace and propose duplicate cleanup candidates.",
            action_category="read_data",
        )
    )
    registry.register(
        Capability(
            intent="cleanup_workspace_duplicates",
            description="Delete duplicate Sam v2 workspace projects and runtime artifacts after explicit confirmation.",
            action_category="write_data",
            requires_write=True,
        )
    )
    registry.register(
        Capability(
            intent="list_goals",
            description="List goals from the workflow store.",
            action_category="read_data",
        )
    )
    registry.register(
        Capability(
            intent="list_projects",
            description="List known projects from the project registry.",
            action_category="read_data",
        )
    )
    registry.register(
        Capability(
            intent="count_tictac_projects",
            description="Count known tic-tac-style projects in the project registry and report the latest one.",
            action_category="read_data",
        )
    )
    registry.register(
        Capability(
            intent="project_details",
            description="Find a known project and describe its stored context.",
            action_category="read_data",
        )
    )
    registry.register(
        Capability(
            intent="scaffold_project",
            description="Create a modular starter project in the managed workspace and register it.",
            action_category="write_data",
            requires_write=True,
        )
    )
    registry.register(
        Capability(
            intent="plan_project",
            description="Create project planning and delegation documents for a registered project.",
            action_category="write_data",
            requires_write=True,
        )
    )
    registry.register(
        Capability(
            intent="show_delegation",
            description="Show the saved named-worker delegation report for a registered project.",
            action_category="read_data",
        )
    )
    registry.register(
        Capability(
            intent="execute_project_task",
            description="Execute one planned delegated task for a registered project and update its reports.",
            action_category="write_data",
            requires_write=True,
        )
    )
    registry.register(
        Capability(
            intent="show_project_progress",
            description="Summarize completed work, next steps, and worker updates for a registered project.",
            action_category="read_data",
        )
    )
    registry.register(
        Capability(
            intent="show_project_status",
            description="Merge repo inspection and saved progress into one project status report.",
            action_category="read_data",
        )
    )
    registry.register(
        Capability(
            intent="inspect_git_state",
            description="Inspect a registered project's git branch and working tree state.",
            action_category="read_data",
        )
    )
    registry.register(
        Capability(
            intent="run_project",
            description="Run a registered project's saved run command.",
            action_category="execute_command",
            requires_write=True,
        )
    )
    registry.register(
        Capability(
            intent="create_draft",
            description="Create a pipeline draft document.",
            action_category="write_data",
            requires_write=True,
        )
    )
    registry.register(
        Capability(
            intent="list_workflows",
            description="List workflow draft documents.",
            action_category="read_data",
        )
    )
    registry.register(
        Capability(
            intent="push_changes",
            description="Sensitive git push-style action that always requires approval.",
            action_category="execute_command",
            requires_write=True,
        )
    )
    registry.register(
        Capability(
            intent="inspect_repo",
            description="Repo inspection request that needs a project path or registered project name.",
            action_category="read_data",
        )
    )
    registry.register(
        Capability(
            intent="inspect_project_repo",
            description="Inspect a registered project's repository and report safe repo context.",
            action_category="read_data",
        )
    )
    return registry

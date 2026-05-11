"""Service-backed tool handlers used by the Phase 1 router migration.

These handlers move execution bodies away from IntentRouter so the router can
become a dispatcher over time. They deliberately avoid project-specific routing
rules and only wrap existing services.
"""

from __future__ import annotations

from typing import Any, Protocol

from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult
from storage import list_tasks


class RouterToolContext(Protocol):
    db_path: Any
    awareness: Any
    goal_service: Any
    project_registry: Any

    def _service_result(
        self,
        intent: str,
        result: SamResult,
        identifier: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SamResult:
        ...


class RouterToolHandlers:
    """Executor handlers for existing router-backed services."""

    def __init__(self, context: RouterToolContext) -> None:
        self.context = context

    def capabilities(self, payload: dict[str, Any]) -> SamResult:
        request_context = self._request_context(payload)
        result = self.context.awareness.describe_self()
        if not result.ok:
            return self.context._service_result("capabilities", result)
        return self._with_request_metadata(result, "capabilities", request_context)

    def list_tasks(self, payload: dict[str, Any]) -> SamResult:
        request_context = self._request_context(payload)
        task_result, tasks = list_tasks(self.context.db_path)
        if not task_result.ok:
            return self.context._service_result("list_tasks", task_result)
        if not tasks:
            return SamResult(
                status="success",
                summary="I do not have any tracked tasks yet.",
                next_action="ask_user",
                metadata={
                    "intent": "list_tasks",
                    "count": 0,
                    "tasks": [],
                    "source": request_context.get("source", "executor"),
                    "confidence": request_context.get("confidence", "medium"),
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
                "source": request_context.get("source", "executor"),
                "confidence": request_context.get("confidence", "medium"),
            },
        )

    def list_goals(self, payload: dict[str, Any]) -> SamResult:
        request_context = self._request_context(payload)
        result, goals = self.context.goal_service.list_goals(status="active")
        return self.context._service_result(
            "list_goals",
            result,
            metadata={
                "count": len(goals),
                "titles": [goal.title for goal in goals],
                "source": request_context.get("source", "executor"),
                "confidence": request_context.get("confidence", "medium"),
            },
        )

    def list_projects(self, payload: dict[str, Any]) -> SamResult:
        request_context = self._request_context(payload)
        project_result, projects = self.context.project_registry.list_projects()
        if not project_result.ok:
            return self.context._service_result("list_projects", project_result)
        if not projects:
            return SamResult(
                status="success",
                summary="I do not have any registered projects yet.",
                next_action="ask_user",
                metadata={
                    "intent": "list_projects",
                    "count": 0,
                    "projects": [],
                    "source": request_context.get("source", "executor"),
                    "confidence": request_context.get("confidence", "medium"),
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
                "source": request_context.get("source", "executor"),
                "confidence": request_context.get("confidence", "medium"),
            },
        )

    def _request_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        context = payload.get("context", {})
        return context if isinstance(context, dict) else {}

    def _with_request_metadata(
        self,
        result: SamResult,
        intent: str,
        request_context: dict[str, Any],
    ) -> SamResult:
        result.metadata.setdefault("intent", intent)
        result.metadata.setdefault("source", request_context.get("source", "executor"))
        result.metadata.setdefault("confidence", request_context.get("confidence", "medium"))
        return result

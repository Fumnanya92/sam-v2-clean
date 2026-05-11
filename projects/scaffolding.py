"""Project building helpers for Sam."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult
from llm import OllamaClient
from workers import FileWriteSpec, ToolingWorker

from .registry import ProjectRecord, ProjectRegistry


@dataclass
class ProjectScaffoldRequest:
    name: str
    project_type: str = "project"
    user_request: str = ""


class ProjectScaffolder:
    def __init__(
        self,
        *,
        workspace_root: str | Path,
        project_registry: ProjectRegistry,
        tooling_worker: ToolingWorker,
        model_client: OllamaClient | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.project_registry = project_registry
        self.tooling_worker = tooling_worker
        self.model_client = model_client or OllamaClient()

    def scaffold(self, request: ProjectScaffoldRequest) -> SamResult:
        title = request.name.strip()
        if not title:
            return SamResult(
                status="failed",
                summary="Project name is required.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing project name",
                next_action="ask_user",
            )

        project_type = self._normalize_project_type(request.project_type)
        project_id = self._slugify(title)
        project_root = self.workspace_root / project_id
        project_root.mkdir(parents=True, exist_ok=True)

        build_request = request.user_request.strip() or f"Create a {project_type} project named {title}."
        try:
            generated = self.model_client.generate_project(
                project_name=title,
                project_type=project_type,
                user_request=build_request,
            )
        except Exception as exc:
            return SamResult(
                status="failed",
                summary="Sam could not generate the project files.",
                error_type=ErrorType.TOOL_FAILED,
                error_message=str(exc),
                next_action="retry",
                metadata={
                    "project_id": project_id,
                    "name": title,
                    "root_path": str(project_root),
                    "project_type": project_type,
                },
            )

        delegation: list[dict[str, str]] = []
        written_files: list[str] = []
        for generated_file in generated.files:
            safe_path = self._safe_project_path(project_root, generated_file.path)
            write_result, task = self.tooling_worker.execute_write(
                FileWriteSpec(
                    name=f"build_{project_id}_{generated_file.path.replace('/', '_').replace('.', '_')}",
                    worker_type="code",
                    worker_name="Mason",
                    target_path=safe_path,
                    content=generated_file.content,
                    description=f"Write generated project file {generated_file.path}.",
                    overwrite=True,
                )
            )
            if not write_result.ok:
                write_result.metadata.setdefault("project_id", project_id)
                write_result.metadata.setdefault("name", title)
                write_result.metadata.setdefault("root_path", str(project_root))
                write_result.metadata.setdefault("delegation", delegation)
                return write_result
            written_files.append(generated_file.path)
            delegation.append(
                {
                    "task_id": task.task_id,
                    "worker_type": task.worker_type,
                    "worker_name": task.worker_name,
                    "file": generated_file.path,
                    "status": task.status,
                }
            )

        register_result = self.project_registry.register(
            ProjectRecord(
                project_id=project_id,
                name=title,
                root_path=str(project_root),
                stack=generated.stack,
                test_command=generated.test_command,
                run_command=generated.run_command,
                important_files=written_files,
            )
        )
        if not register_result.ok:
            register_result.metadata.setdefault("project_id", project_id)
            register_result.metadata.setdefault("name", title)
            register_result.metadata.setdefault("root_path", str(project_root))
            register_result.metadata.setdefault("delegation", delegation)
            return register_result

        return SamResult(
            status="success",
            summary=f"Mason built {title} at {project_root}. {generated.summary}",
            next_action="stop",
            metadata={
                "project_id": project_id,
                "name": title,
                "root_path": str(project_root),
                "stack": generated.stack,
                "project_type": project_type,
                "run_command": generated.run_command,
                "test_command": generated.test_command,
                "important_files": written_files,
                "delegation": delegation,
            },
        )

    @staticmethod
    def _normalize_project_type(project_type: str) -> str:
        normalized = project_type.strip().lower().replace(" ", "_")
        return normalized or "project"

    @staticmethod
    def _safe_project_path(project_root: Path, relative_path: str) -> Path:
        cleaned = relative_path.strip().replace("\\", "/")
        if not cleaned or cleaned.startswith("/"):
            raise ValueError(f"Unsafe generated file path: {relative_path}")
        parts = Path(cleaned).parts
        if ".." in parts:
            raise ValueError(f"Unsafe generated file path: {relative_path}")
        return project_root.joinpath(*parts)

    @staticmethod
    def _slugify(text: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
        return normalized or "sam_project"

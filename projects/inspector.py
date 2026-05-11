"""Low-risk project repository inspection for Sam v2."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from sam_v2.diagnostics.error_types import ErrorType
from sam_v2.diagnostics.result import SamResult
from sam_v2.tools import GitStatusSnapshot, SafeLocalTools

from .registry import ProjectRecord, ProjectRegistry


@dataclass
class ProjectInspection:
    project_id: str
    name: str
    root_path: str
    stack: str
    branch: str
    is_clean: bool
    changed_files: list[str]
    top_level_entries: list[str]
    important_file_samples: dict[str, str]
    test_command: list[str]
    build_command: list[str]


class ProjectInspector:
    def __init__(self, *, registry: ProjectRegistry, tools: SafeLocalTools) -> None:
        self.registry = registry
        self.tools = tools

    def inspect(self, query: str) -> tuple[SamResult, ProjectInspection | None]:
        project_result, project = self.registry.find_project(query)
        if not project_result.ok or project is None:
            return project_result, None

        git_result, snapshot = self.tools.inspect_git_state(project.root_path)
        if not git_result.ok or snapshot is None:
            return git_result, None

        dir_result, entries = self.tools.list_directory(project.root_path)
        if not dir_result.ok:
            return dir_result, None

        samples = self._read_important_files(project)
        inspection = ProjectInspection(
            project_id=project.project_id,
            name=project.name,
            root_path=project.root_path,
            stack=project.stack,
            branch=snapshot.branch,
            is_clean=snapshot.is_clean,
            changed_files=snapshot.changed_files,
            top_level_entries=entries,
            important_file_samples=samples,
            test_command=project.test_command or [],
            build_command=project.build_command or [],
        )
        return (
            SamResult(
                status="success",
                summary=f"Inspected project {project.name}.",
                next_action="stop",
                metadata={
                    "project_id": project.project_id,
                    "name": project.name,
                    "branch": snapshot.branch,
                    "is_clean": snapshot.is_clean,
                },
            ),
            inspection,
        )

    def _read_important_files(self, project: ProjectRecord) -> dict[str, str]:
        samples: dict[str, str] = {}
        root = Path(project.root_path)
        for relative_path in (project.important_files or [])[:3]:
            target = root / relative_path
            read_result, content = self.tools.read_text_file(target, max_chars=300)
            if read_result.ok and content is not None:
                samples[relative_path] = content
        return samples


def inspection_metadata(inspection: ProjectInspection) -> dict[str, object]:
    return asdict(inspection)

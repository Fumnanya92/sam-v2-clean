"""Low-risk project repository inspection for Sam v2."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from diagnostics.result import SamResult
from tools import SafeLocalTools

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
            directory_result, directory = self.tools.resolve_directory_query(query)
            if not directory_result.ok or directory is None:
                return project_result, None
            return self.inspect_path(directory)

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

    def inspect_path(self, root_path: str | Path) -> tuple[SamResult, ProjectInspection | None]:
        root = Path(root_path)
        git_result, snapshot = self.tools.inspect_git_state(root)
        if not git_result.ok or snapshot is None:
            return git_result, None

        dir_result, entries = self.tools.list_directory(root)
        if not dir_result.ok:
            return dir_result, None

        samples = self._read_important_files_from_root(root)
        inspection = ProjectInspection(
            project_id="",
            name=root.name,
            root_path=str(root),
            stack="",
            branch=snapshot.branch,
            is_clean=snapshot.is_clean,
            changed_files=snapshot.changed_files,
            top_level_entries=entries,
            important_file_samples=samples,
            test_command=[],
            build_command=[],
        )
        return (
            SamResult(
                status="success",
                summary=f"Inspected repository {root.name}.",
                next_action="stop",
                metadata={
                    "name": root.name,
                    "root_path": str(root),
                    "branch": snapshot.branch,
                    "is_clean": snapshot.is_clean,
                },
            ),
            inspection,
        )

    def _read_important_files(self, project: ProjectRecord) -> dict[str, str]:
        return self._read_important_files_from_root(
            Path(project.root_path),
            relative_paths=project.important_files or [],
        )

    def _read_important_files_from_root(
        self,
        root: Path,
        *,
        relative_paths: list[str] | None = None,
    ) -> dict[str, str]:
        samples: dict[str, str] = {}
        candidates = relative_paths or [
            "README.md",
            "package.json",
            "pyproject.toml",
            "requirements.txt",
            "main.py",
        ]
        for relative_path in candidates[:3]:
            target = root / relative_path
            read_result, content = self.tools.read_text_file(target, max_chars=300)
            if read_result.ok and content is not None:
                samples[relative_path] = content
        return samples


def inspection_metadata(inspection: ProjectInspection) -> dict[str, object]:
    return asdict(inspection)

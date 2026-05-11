"""Persistent project registry for Sam v2."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from sam_v2.diagnostics.error_types import ErrorType
from sam_v2.diagnostics.result import SamResult


@dataclass
class ProjectRecord:
    project_id: str
    name: str
    root_path: str
    stack: str = ""
    test_command: list[str] | None = None
    build_command: list[str] | None = None
    run_command: list[str] | None = None
    deployment_method: str = ""
    risk_level: str = ""
    active_branch: str = ""
    important_files: list[str] | None = None


class ProjectRegistry:
    def __init__(self, registry_path: str | Path) -> None:
        self.registry_path = Path(registry_path)

    def register(self, record: ProjectRecord) -> SamResult:
        result, records = self._load_all()
        if not result.ok:
            return result

        existing = [item for item in records if item.project_id != record.project_id]
        existing.append(record)
        return self._save_all(existing, "Project registered.")

    def list_projects(self) -> tuple[SamResult, list[ProjectRecord]]:
        return self._load_all()

    def get_project(self, project_id: str) -> tuple[SamResult, ProjectRecord | None]:
        result, records = self._load_all()
        if not result.ok:
            return result, None
        for record in records:
            if record.project_id == project_id:
                return SamResult(status="success", summary="Project fetched.", next_action="stop"), record
        return (
            SamResult(
                status="failed",
                summary="Project not found.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=project_id,
                next_action="ask_user",
            ),
            None,
        )

    def find_project(self, query: str) -> tuple[SamResult, ProjectRecord | None]:
        result, records = self._load_all()
        if not result.ok:
            return result, None

        normalized = query.strip().lower()
        if not normalized:
            return (
                SamResult(
                    status="failed",
                    summary="Project query is required.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="empty query",
                    next_action="ask_user",
                ),
                None,
            )

        for record in records:
            if record.project_id.lower() == normalized or record.name.lower() == normalized:
                return SamResult(status="success", summary="Project matched.", next_action="stop"), record

        partial_matches = [
            record
            for record in records
            if normalized in record.project_id.lower() or normalized in record.name.lower()
        ]
        if len(partial_matches) == 1:
            return SamResult(status="success", summary="Project matched.", next_action="stop"), partial_matches[0]
        if len(partial_matches) > 1:
            names = [record.name for record in partial_matches]
            return (
                SamResult(
                    status="failed",
                    summary=f"Project query matched multiple projects: {', '.join(names)}.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="ambiguous project query",
                    next_action="ask_user",
                    metadata={"matches": names},
                ),
                None,
            )

        return (
            SamResult(
                status="failed",
                summary="Project not found.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=query,
                next_action="ask_user",
            ),
            None,
        )

    def _load_all(self) -> tuple[SamResult, list[ProjectRecord]]:
        if not self.registry_path.exists():
            return (
                SamResult(
                    status="success",
                    summary="Project registry not found; using empty registry.",
                    next_action="stop",
                    metadata={"created_default": True},
                ),
                [],
            )
        try:
            raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("Project registry must contain a list.")
            return (
                SamResult(status="success", summary="Projects loaded.", next_action="stop"),
                [ProjectRecord(**item) for item in raw],
            )
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Project registry is invalid.",
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
                    summary="Failed to read project registry.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="retry",
                ),
                [],
            )

    def _save_all(self, records: list[ProjectRecord], summary: str) -> SamResult:
        try:
            self.registry_path.parent.mkdir(parents=True, exist_ok=True)
            payload = [asdict(record) for record in records]
            self.registry_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return SamResult(status="success", summary=summary, next_action="stop")
        except OSError as exc:
            return SamResult(
                status="failed",
                summary="Failed to write project registry.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="retry",
            )

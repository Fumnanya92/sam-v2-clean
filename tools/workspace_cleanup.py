"""Workspace duplicate inspection and cleanup helpers for Sam v2."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult
from storage.db import log_audit_event
from storage.models import AuditEvent


@dataclass
class DuplicateGroup:
    family: str
    keep_path: str
    duplicate_paths: list[str]


class WorkspaceCleanupService:
    def __init__(self, workspace_root: str | Path, *, db_path: str | Path | None = None) -> None:
        self.workspace_root = Path(workspace_root)
        self.projects_root = self.workspace_root / "projects"
        self.runtime_root = self.workspace_root / "runtime"
        self.db_path = Path(db_path) if db_path is not None else None

    def inspect(self, scope: str = "all") -> tuple[SamResult, dict[str, object]]:
        projects = self._inspect_project_duplicates() if scope in {"all", "projects"} else []
        runtime = self._inspect_runtime_duplicates() if scope in {"all", "runtime"} else []
        metadata: dict[str, object] = {
            "workspace_root": str(self.workspace_root),
            "scope": scope,
            "project_duplicate_groups": [self._group_dict(item) for item in projects],
            "runtime_duplicate_groups": [self._group_dict(item) for item in runtime],
            "project_duplicate_count": len(projects),
            "runtime_duplicate_count": len(runtime),
            "proposed_delete_paths": self._proposed_delete_paths(projects, runtime, scope),
            "confirmation_commands": self._confirmation_commands(scope),
        }
        summary_parts: list[str] = []
        if scope in {"all", "projects"}:
            summary_parts.append(f"{len(projects)} duplicate project group(s)")
        if scope in {"all", "runtime"}:
            summary_parts.append(f"{len(runtime)} duplicate runtime group(s)")
        summary = "Workspace cleanup inspection complete: " + ", ".join(summary_parts) + "."
        self._audit(
            "workspace_cleanup_inspected",
            summary,
            metadata,
        )
        return SamResult(status="success", summary=summary, next_action="stop", metadata=metadata), metadata

    def cleanup(self, scope: str = "all") -> SamResult:
        inspect_result, metadata = self.inspect(scope)
        if not inspect_result.ok:
            return inspect_result

        proposed = [Path(item) for item in metadata.get("proposed_delete_paths", [])]
        deleted_paths: list[str] = []
        for target in proposed:
            verified = self._verify_within_workspace(target)
            if not verified.ok:
                return verified
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
                deleted_paths.append(str(target))
            except OSError as exc:
                return SamResult(
                    status="failed",
                    summary="Workspace cleanup failed during deletion.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="retry",
                    metadata={"path": str(target), "scope": scope},
                )

        result = SamResult(
            status="success",
            summary=f"Workspace cleanup deleted {len(deleted_paths)} duplicate path(s).",
            next_action="stop",
            metadata={
                "workspace_root": str(self.workspace_root),
                "scope": scope,
                "deleted_paths": deleted_paths,
                "deleted_count": len(deleted_paths),
            },
        )
        self._audit("workspace_cleanup_deleted", result.summary, result.metadata)
        return result

    def _inspect_project_duplicates(self) -> list[DuplicateGroup]:
        if not self.projects_root.exists():
            return []
        families: dict[str, list[Path]] = {}
        for child in self.projects_root.iterdir():
            if not child.is_dir():
                continue
            family = self._project_family(child.name)
            families.setdefault(family, []).append(child)

        groups: list[DuplicateGroup] = []
        for family, items in families.items():
            if len(items) < 2:
                continue
            keep = self._choose_keep_path(items)
            duplicates = [str(path) for path in items if path != keep]
            if duplicates:
                groups.append(DuplicateGroup(family=family, keep_path=str(keep), duplicate_paths=duplicates))
        return sorted(groups, key=lambda item: item.family)

    def _inspect_runtime_duplicates(self) -> list[DuplicateGroup]:
        if not self.runtime_root.exists():
            return []
        session_groups: dict[str, list[Path]] = {}
        for child in self.runtime_root.iterdir():
            if not child.is_file():
                continue
            session_key = self._runtime_session_key(child.name)
            family = self._runtime_family(session_key)
            session_groups.setdefault(family, []).append(child)

        families: list[DuplicateGroup] = []
        for family, items in session_groups.items():
            session_map: dict[str, list[Path]] = {}
            for item in items:
                session_map.setdefault(self._runtime_session_key(item.name), []).append(item)
            if len(session_map) < 2:
                continue
            keep_session = self._choose_keep_session(session_map)
            keep_paths = [str(path) for path in session_map[keep_session]]
            duplicate_paths: list[str] = []
            for session_key, paths in session_map.items():
                if session_key == keep_session:
                    continue
                duplicate_paths.extend(str(path) for path in paths)
            families.append(
                DuplicateGroup(
                    family=family,
                    keep_path=", ".join(sorted(keep_paths)),
                    duplicate_paths=sorted(duplicate_paths),
                )
            )
        return sorted(families, key=lambda item: item.family)

    def _choose_keep_path(self, items: list[Path]) -> Path:
        unsuffixed = [item for item in items if self._project_family(item.name) == item.name]
        candidates = unsuffixed or items
        return max(candidates, key=lambda item: item.stat().st_mtime)

    @staticmethod
    def _choose_keep_session(session_map: dict[str, list[Path]]) -> str:
        return max(
            session_map,
            key=lambda key: max(path.stat().st_mtime for path in session_map[key]),
        )

    @staticmethod
    def _project_family(name: str) -> str:
        return re.sub(r"_[0-9a-f]{6,10}$", "", name, flags=re.IGNORECASE)

    @staticmethod
    def _runtime_session_key(name: str) -> str:
        session_key = name
        for suffix in (".session.json", ".json", ".db"):
            if session_key.endswith(suffix):
                return session_key[: -len(suffix)]
        return Path(name).stem

    @staticmethod
    def _runtime_family(session_key: str) -> str:
        return re.sub(r"_[0-9a-f]{6,10}$", "", session_key, flags=re.IGNORECASE)

    def _proposed_delete_paths(
        self,
        project_groups: list[DuplicateGroup],
        runtime_groups: list[DuplicateGroup],
        scope: str,
    ) -> list[str]:
        paths: list[str] = []
        if scope in {"all", "projects"}:
            for group in project_groups:
                paths.extend(group.duplicate_paths)
        if scope in {"all", "runtime"}:
            for group in runtime_groups:
                paths.extend(group.duplicate_paths)
        return paths

    @staticmethod
    def _confirmation_commands(scope: str) -> list[str]:
        commands = []
        if scope in {"all", "projects"}:
            commands.append("confirm cleanup duplicated projects")
        if scope in {"all", "runtime"}:
            commands.append("confirm cleanup duplicated runtime")
        if scope == "all":
            commands.insert(0, "confirm cleanup workspace duplicates")
        return commands

    @staticmethod
    def _group_dict(group: DuplicateGroup) -> dict[str, object]:
        return {
            "family": group.family,
            "keep_path": group.keep_path,
            "duplicate_paths": group.duplicate_paths,
        }

    def _verify_within_workspace(self, target: Path) -> SamResult:
        try:
            resolved_target = target.resolve()
            resolved_root = self.workspace_root.resolve()
            resolved_target.relative_to(resolved_root)
            return SamResult(status="success", summary="Path is within workspace.", next_action="stop")
        except ValueError:
            return SamResult(
                status="failed",
                summary="Refusing to delete a path outside the Sam v2 workspace.",
                error_type=ErrorType.MISSING_PERMISSION,
                error_message=str(target),
                next_action="stop",
                metadata={"path": str(target), "workspace_root": str(self.workspace_root)},
            )

    def _audit(self, event_type: str, summary: str, metadata: dict[str, object]) -> None:
        if self.db_path is None:
            return
        try:
            log_audit_event(
                self.db_path,
                AuditEvent(
                    event_type=event_type,
                    actor="tools.workspace_cleanup",
                    summary=summary,
                    metadata_json=json.dumps(metadata),
                ),
            )
        except Exception:
            pass

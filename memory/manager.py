"""Persistent JSON memory helpers for Sam v2."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

from sam_v2.diagnostics.error_types import ErrorType
from sam_v2.diagnostics.result import SamResult
from sam_v2.storage.db import log_audit_event
from sam_v2.storage.models import AuditEvent

_lock = Lock()


def empty_memory() -> dict[str, Any]:
    """Return the base Sam v2 memory shape."""
    return {
        "identity": {},
        "preferences": {},
        "relationships": {},
        "goals": {},
        "projects": {},
        "tasks": {},
        "notes": {},
        "daily_state": {},
    }


def load_memory(memory_path: str | Path) -> tuple[SamResult, dict[str, Any]]:
    """Load persistent memory from disk."""
    path = Path(memory_path)
    if not path.exists():
        return (
            SamResult(
                status="success",
                summary="Memory file not found; using empty memory.",
                next_action="stop",
                metadata={"path": str(path), "created_default": True},
            ),
            empty_memory(),
        )

    try:
        with _lock:
            data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return (
                SamResult(
                    status="failed",
                    summary="Memory file did not contain an object.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message="Expected top-level JSON object.",
                    next_action="ask_user",
                    metadata={"path": str(path)},
                ),
                empty_memory(),
            )
        return (
            SamResult(
                status="success",
                summary="Memory loaded.",
                next_action="stop",
                metadata={"path": str(path)},
            ),
            data,
        )
    except json.JSONDecodeError as exc:
        return (
            SamResult(
                status="failed",
                summary="Memory file is invalid JSON.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="ask_user",
                metadata={"path": str(path)},
            ),
            empty_memory(),
        )
    except OSError as exc:
        return (
            SamResult(
                status="failed",
                summary="Failed to read memory file.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="retry",
                metadata={"path": str(path)},
            ),
            empty_memory(),
        )


def save_memory(
    memory_path: str | Path,
    memory: dict[str, Any],
    *,
    audit_db_path: str | Path | None = None,
) -> SamResult:
    """Persist memory to disk and optionally audit the write."""
    if not isinstance(memory, dict):
        return SamResult(
            status="failed",
            summary="Memory payload must be a dictionary.",
            error_type=ErrorType.TOOL_FAILED,
            error_message=f"received={type(memory).__name__}",
            next_action="ask_user",
        )

    path = Path(memory_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            path.write_text(
                json.dumps(memory, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        if audit_db_path is not None:
            log_audit_event(
                audit_db_path,
                AuditEvent(
                    event_type="memory_saved",
                    actor="sam_v2.memory",
                    summary=f"Saved memory to {path.name}",
                    metadata_json='{"path":"%s"}' % str(path).replace("\\", "\\\\"),
                ),
            )
        return SamResult(
            status="success",
            summary="Memory saved.",
            next_action="stop",
            metadata={"path": str(path)},
        )
    except OSError as exc:
        return SamResult(
            status="failed",
            summary="Failed to save memory file.",
            error_type=ErrorType.FILE_ACCESS_ERROR,
            error_message=str(exc),
            next_action="retry",
            metadata={"path": str(path)},
        )


def update_memory(
    memory_path: str | Path,
    updates: dict[str, Any],
    *,
    audit_db_path: str | Path | None = None,
) -> tuple[SamResult, dict[str, Any]]:
    """Merge updates into persistent memory and save the result."""
    if not isinstance(updates, dict):
        return (
            SamResult(
                status="failed",
                summary="Memory updates must be a dictionary.",
                error_type=ErrorType.TOOL_FAILED,
                error_message=f"received={type(updates).__name__}",
                next_action="ask_user",
            ),
            empty_memory(),
        )

    load_result, current = load_memory(memory_path)
    if load_result.status == "failed":
        return load_result, current

    changed = _recursive_update(current, updates)
    if not changed:
        return (
            SamResult(
                status="success",
                summary="Memory already up to date.",
                next_action="stop",
                metadata={"path": str(memory_path), "changed": False},
            ),
            current,
        )

    save_result = save_memory(memory_path, current, audit_db_path=audit_db_path)
    save_result.metadata["changed"] = True
    return save_result, current


def _recursive_update(target: dict[str, Any], updates: dict[str, Any]) -> bool:
    changed = False

    for key, value in updates.items():
        if value is None:
            continue

        if isinstance(value, dict) and "value" not in value:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
                changed = True
            if _recursive_update(target[key], value):
                changed = True
            continue

        entry = value if isinstance(value, dict) and "value" in value else {"value": value}
        existing = target.get(key)
        if existing != entry:
            target[key] = entry
            changed = True

    if changed:
        target["_last_updated_at"] = {"value": datetime.utcnow().isoformat() + "Z"}
    return changed

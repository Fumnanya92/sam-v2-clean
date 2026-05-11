"""DB helpers for Sam v2 storage foundation."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional, Tuple

from sam_v2.diagnostics.error_types import ErrorType
from sam_v2.diagnostics.result import SamResult
from sam_v2.storage.models import AuditEvent, TaskRecord
from sam_v2.storage.schema import CREATE_INDEXES, CREATE_TABLES


def _connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_storage(db_path: str | Path) -> SamResult:
    """Initialize the SQLite schema for Sam v2 storage."""
    try:
        with _connect(db_path) as conn:
            for stmt in CREATE_TABLES:
                conn.execute(stmt)
            for stmt in CREATE_INDEXES:
                conn.execute(stmt)
            conn.execute(
                """
                INSERT INTO schema_info(key, value, updated_at)
                VALUES ('schema_version', '1', datetime('now'))
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """
            )
            conn.commit()
        return SamResult(
            status="success",
            summary="Storage schema initialized.",
            next_action="stop",
            metadata={"db_path": str(db_path)},
        )
    except sqlite3.Error as exc:
        return SamResult(
            status="failed",
            summary="Failed to initialize storage schema.",
            error_type=ErrorType.FILE_ACCESS_ERROR,
            error_message=str(exc),
            next_action="retry",
            metadata={"db_path": str(db_path)},
        )
    except Exception as exc:
        return SamResult(
            status="failed",
            summary="Unexpected error while initializing storage schema.",
            error_type=ErrorType.UNKNOWN_ERROR,
            error_message=str(exc),
            next_action="escalate_worker",
            metadata={"db_path": str(db_path)},
        )


def log_audit_event(db_path: str | Path, event: AuditEvent) -> Tuple[SamResult, Optional[int]]:
    """Insert one audit event."""
    try:
        with _connect(db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO audit_events(event_type, actor, summary, metadata_json)
                VALUES (?, ?, ?, ?)
                """,
                (event.event_type, event.actor, event.summary, event.metadata_json),
            )
            conn.commit()
            event_id = int(cur.lastrowid)
        return (
            SamResult(
                status="success",
                summary="Audit event stored.",
                next_action="stop",
                metadata={"event_id": event_id},
            ),
            event_id,
        )
    except sqlite3.Error as exc:
        return (
            SamResult(
                status="failed",
                summary="Failed to store audit event.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="retry",
            ),
            None,
        )


def fetch_audit_event(db_path: str | Path, event_id: int) -> Tuple[SamResult, Optional[AuditEvent]]:
    """Fetch one audit event by id."""
    try:
        with _connect(db_path) as conn:
            row = conn.execute(
                "SELECT id, event_type, actor, summary, metadata_json, created_at FROM audit_events WHERE id = ?",
                (event_id,),
            ).fetchone()
        if row is None:
            return (
                SamResult(
                    status="failed",
                    summary="Audit event not found.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=f"id={event_id}",
                    next_action="stop",
                ),
                None,
            )
        return (
            SamResult(status="success", summary="Audit event fetched.", next_action="stop"),
            AuditEvent(
                id=row["id"],
                event_type=row["event_type"],
                actor=row["actor"],
                summary=row["summary"],
                metadata_json=row["metadata_json"],
                created_at=row["created_at"],
            ),
        )
    except sqlite3.Error as exc:
        return (
            SamResult(
                status="failed",
                summary="Failed to fetch audit event.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="retry",
            ),
            None,
        )


def create_task(db_path: str | Path, task: TaskRecord) -> Tuple[SamResult, Optional[int]]:
    """Insert one task-like record."""
    try:
        with _connect(db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO tasks(title, status, priority, notes)
                VALUES (?, ?, ?, ?)
                """,
                (task.title, task.status, task.priority, task.notes),
            )
            conn.commit()
            task_id = int(cur.lastrowid)
        return (
            SamResult(
                status="success",
                summary="Task stored.",
                next_action="stop",
                metadata={"task_id": task_id},
            ),
            task_id,
        )
    except sqlite3.IntegrityError as exc:
        return (
            SamResult(
                status="failed",
                summary="Task insert failed integrity checks.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="ask_user",
            ),
            None,
        )
    except sqlite3.Error as exc:
        return (
            SamResult(
                status="failed",
                summary="Failed to store task.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="retry",
            ),
            None,
        )


def fetch_task(db_path: str | Path, task_id: int) -> Tuple[SamResult, Optional[TaskRecord]]:
    """Fetch one task by id."""
    try:
        with _connect(db_path) as conn:
            row = conn.execute(
                "SELECT id, title, status, priority, notes, created_at, updated_at FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return (
                SamResult(
                    status="failed",
                    summary="Task not found.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=f"id={task_id}",
                    next_action="stop",
                ),
                None,
            )
        return (
            SamResult(status="success", summary="Task fetched.", next_action="stop"),
            TaskRecord(
                id=row["id"],
                title=row["title"],
                status=row["status"],
                priority=row["priority"],
                notes=row["notes"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            ),
        )
    except sqlite3.Error as exc:
        return (
            SamResult(
                status="failed",
                summary="Failed to fetch task.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="retry",
            ),
            None,
        )


def list_tasks(db_path: str | Path, *, limit: int = 20) -> Tuple[SamResult, list[TaskRecord]]:
    """List recent task rows."""
    try:
        with _connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, title, status, priority, notes, created_at, updated_at
                FROM tasks
                ORDER BY COALESCE(updated_at, created_at) DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        tasks = [
            TaskRecord(
                id=row["id"],
                title=row["title"],
                status=row["status"],
                priority=row["priority"],
                notes=row["notes"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]
        return (
            SamResult(
                status="success",
                summary="Tasks listed.",
                next_action="stop",
                metadata={"count": len(tasks)},
            ),
            tasks,
        )
    except sqlite3.Error as exc:
        return (
            SamResult(
                status="failed",
                summary="Failed to list tasks.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="retry",
            ),
            [],
        )


def update_task(
    db_path: str | Path,
    task_id: int,
    *,
    status: str | None = None,
    notes: str | None = None,
    priority: str | None = None,
) -> Tuple[SamResult, Optional[TaskRecord]]:
    """Update a task-like record and return the refreshed row."""
    fields: list[str] = []
    values: list[str | int] = []

    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if notes is not None:
        fields.append("notes = ?")
        values.append(notes)
    if priority is not None:
        fields.append("priority = ?")
        values.append(priority)

    if not fields:
        return (
            SamResult(
                status="failed",
                summary="At least one task field must be updated.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="no update fields provided",
                next_action="ask_user",
            ),
            None,
        )

    values.append(task_id)
    try:
        with _connect(db_path) as conn:
            cursor = conn.execute(
                f"""
                UPDATE tasks
                SET {", ".join(fields)}, updated_at = datetime('now')
                WHERE id = ?
                """,
                tuple(values),
            )
            conn.commit()
        if cursor.rowcount == 0:
            return (
                SamResult(
                    status="failed",
                    summary="Task not found.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=f"id={task_id}",
                    next_action="ask_user",
                ),
                None,
            )
        fetch_result, task = fetch_task(db_path, task_id)
        if not fetch_result.ok or task is None:
            return fetch_result, None
        return (
            SamResult(
                status="success",
                summary="Task updated.",
                next_action="stop",
                metadata={"task_id": task_id},
            ),
            task,
        )
    except sqlite3.Error as exc:
        return (
            SamResult(
                status="failed",
                summary="Failed to update task.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="retry",
            ),
            None,
        )

"""Goal workflow service for Sam v2."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult
from storage.db import _connect, log_audit_event
from storage.models import AuditEvent

from .schema import ensure_workflow_schema

GoalLevel = Literal["objective", "key_result", "milestone", "task", "daily_action"]
GoalHealth = Literal["on_track", "at_risk", "behind", "critical"]


@dataclass
class GoalRecord:
    id: str
    title: str
    description: str
    level: GoalLevel
    score: float
    status: str
    health: GoalHealth
    time_horizon: str
    success_criteria: str
    parent_id: Optional[str]
    deadline: Optional[str]
    tags_json: str
    created_at: str
    updated_at: str


def _score_to_health(score: float) -> GoalHealth:
    if score >= 0.7:
        return "on_track"
    if score >= 0.5:
        return "at_risk"
    if score >= 0.3:
        return "behind"
    return "critical"


class GoalService:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def create_goal(
        self,
        *,
        title: str,
        description: str = "",
        level: GoalLevel = "task",
        time_horizon: str = "weekly",
        success_criteria: str = "",
        parent_id: Optional[str] = None,
        deadline: Optional[str] = None,
        tags: list[str] | None = None,
    ) -> tuple[SamResult, Optional[GoalRecord]]:
        schema_result = ensure_workflow_schema(self.db_path)
        if not schema_result.ok:
            return schema_result, None

        goal_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat() + "Z"
        tags_json = json.dumps(tags or [])
        try:
            with _connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO goals (
                        id, parent_id, level, title, description, success_criteria,
                        time_horizon, score, status, health, deadline, tags_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0.0, 'active', 'on_track', ?, ?, ?, ?)
                    """,
                    (
                        goal_id,
                        parent_id,
                        level,
                        title,
                        description,
                        success_criteria,
                        time_horizon,
                        deadline,
                        tags_json,
                        now,
                        now,
                    ),
                )
                conn.commit()
            log_audit_event(
                self.db_path,
                AuditEvent(
                    event_type="goal_created",
                    actor="workflows.goals",
                    summary=title,
                    metadata_json='{"goal_id":"%s"}' % goal_id,
                ),
            )
            return self.get_goal(goal_id)
        except sqlite3.Error as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Failed to create goal.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="retry",
                ),
                None,
            )

    def update_score(self, goal_id: str, score: float, note: str = "") -> tuple[SamResult, Optional[GoalRecord]]:
        bounded_score = max(0.0, min(1.0, score))
        health = _score_to_health(bounded_score)
        now = datetime.utcnow().isoformat() + "Z"
        try:
            with _connect(self.db_path) as conn:
                cursor = conn.execute(
                    "UPDATE goals SET score = ?, health = ?, updated_at = ? WHERE id = ?",
                    (bounded_score, health, now, goal_id),
                )
                conn.commit()
            if cursor.rowcount == 0:
                return (
                    SamResult(
                        status="failed",
                        summary="Goal not found.",
                        error_type=ErrorType.FILE_ACCESS_ERROR,
                        error_message=f"id={goal_id}",
                        next_action="stop",
                    ),
                    None,
                )
            log_audit_event(
                self.db_path,
                AuditEvent(
                    event_type="goal_score_updated",
                    actor="workflows.goals",
                    summary=note or f"score={bounded_score}",
                    metadata_json='{"goal_id":"%s","score":"%s"}' % (goal_id, bounded_score),
                ),
            )
            return self.get_goal(goal_id)
        except sqlite3.Error as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Failed to update goal score.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="retry",
                ),
                None,
            )

    def list_goals(
        self,
        *,
        status: str = "",
        level: str = "",
        limit: int = 50,
    ) -> tuple[SamResult, list[GoalRecord]]:
        schema_result = ensure_workflow_schema(self.db_path)
        if not schema_result.ok:
            return schema_result, []

        conditions = []
        values: list[object] = []
        if status:
            conditions.append("status = ?")
            values.append(status)
        if level:
            conditions.append("level = ?")
            values.append(level)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        values.append(limit)
        try:
            with _connect(self.db_path) as conn:
                rows = conn.execute(
                    f"SELECT * FROM goals {where} ORDER BY updated_at DESC LIMIT ?",
                    values,
                ).fetchall()
            return (
                SamResult(status="success", summary="Goals listed.", next_action="stop"),
                [GoalRecord(**dict(row)) for row in rows],
            )
        except sqlite3.Error as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Failed to list goals.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="retry",
                ),
                [],
            )

    def get_goal(self, goal_id: str) -> tuple[SamResult, Optional[GoalRecord]]:
        try:
            with _connect(self.db_path) as conn:
                row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
            if row is None:
                return (
                    SamResult(
                        status="failed",
                        summary="Goal not found.",
                        error_type=ErrorType.FILE_ACCESS_ERROR,
                        error_message=f"id={goal_id}",
                        next_action="stop",
                    ),
                    None,
                )
            return (
                SamResult(status="success", summary="Goal fetched.", next_action="stop"),
                GoalRecord(**dict(row)),
            )
        except sqlite3.Error as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Failed to fetch goal.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="retry",
                ),
                None,
            )

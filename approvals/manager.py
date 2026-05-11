"""Approval request lifecycle for Sam v2."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, Optional

from sam_v2.diagnostics.error_types import ErrorType
from sam_v2.diagnostics.result import SamResult
from sam_v2.storage.db import _connect

from .schema import CREATE_INDEXES, CREATE_TABLES

ApprovalStatus = Literal["pending", "approved", "denied", "expired", "executed"]
ApprovalUrgency = Literal["urgent", "normal"]


@dataclass
class ApprovalRequest:
    id: str
    agent_id: str
    agent_name: str
    tool_name: str
    tool_arguments_json: str
    action_category: str
    urgency: ApprovalUrgency
    reason: str
    context: str
    status: ApprovalStatus
    decided_at: Optional[str]
    decided_by: Optional[str]
    executed_at: Optional[str]
    execution_result: Optional[str]
    created_at: str


class ApprovalManager:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def ensure_schema(self) -> SamResult:
        try:
            with _connect(self.db_path) as conn:
                for statement in CREATE_TABLES:
                    conn.execute(statement)
                for statement in CREATE_INDEXES:
                    conn.execute(statement)
                conn.commit()
            return SamResult(status="success", summary="Approval schema ready.", next_action="stop")
        except sqlite3.Error as exc:
            return SamResult(
                status="failed",
                summary="Failed to initialize approval schema.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="retry",
            )

    def create_request(
        self,
        *,
        agent_id: str,
        agent_name: str,
        tool_name: str,
        tool_arguments: dict,
        action_category: str,
        urgency: ApprovalUrgency = "normal",
        reason: str,
        context: str = "",
    ) -> tuple[SamResult, Optional[ApprovalRequest]]:
        request_id = str(uuid.uuid4())
        created_at = datetime.utcnow().isoformat() + "Z"
        tool_arguments_json = json.dumps(tool_arguments)

        try:
            with _connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO approval_requests (
                        id, agent_id, agent_name, tool_name, tool_arguments_json,
                        action_category, urgency, reason, context, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        request_id,
                        agent_id,
                        agent_name,
                        tool_name,
                        tool_arguments_json,
                        action_category,
                        urgency,
                        reason,
                        context,
                        created_at,
                    ),
                )
                conn.commit()
            return (
                SamResult(
                    status="success",
                    summary="Approval request created.",
                    next_action="stop",
                    metadata={"request_id": request_id},
                ),
                ApprovalRequest(
                    id=request_id,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    tool_name=tool_name,
                    tool_arguments_json=tool_arguments_json,
                    action_category=action_category,
                    urgency=urgency,
                    reason=reason,
                    context=context,
                    status="pending",
                    decided_at=None,
                    decided_by=None,
                    executed_at=None,
                    execution_result=None,
                    created_at=created_at,
                ),
            )
        except sqlite3.Error as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Failed to create approval request.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="retry",
                ),
                None,
            )

    def get(self, request_id: str) -> tuple[SamResult, Optional[ApprovalRequest]]:
        try:
            with _connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT * FROM approval_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()
            if row is None:
                return (
                    SamResult(
                        status="failed",
                        summary="Approval request not found.",
                        error_type=ErrorType.FILE_ACCESS_ERROR,
                        error_message=f"id={request_id}",
                        next_action="stop",
                    ),
                    None,
                )
            return (
                SamResult(status="success", summary="Approval request fetched.", next_action="stop"),
                ApprovalRequest(**dict(row)),
            )
        except sqlite3.Error as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Failed to fetch approval request.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="retry",
                ),
                None,
            )

    def list_pending(self) -> tuple[SamResult, list[ApprovalRequest]]:
        try:
            with _connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT * FROM approval_requests WHERE status = 'pending' ORDER BY created_at DESC"
                ).fetchall()
            return (
                SamResult(status="success", summary="Pending approval requests listed.", next_action="stop"),
                [ApprovalRequest(**dict(row)) for row in rows],
            )
        except sqlite3.Error as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Failed to list pending approvals.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="retry",
                ),
                [],
            )

    def approve(self, request_id: str, decided_by: str) -> tuple[SamResult, Optional[ApprovalRequest]]:
        return self._set_status(request_id, "approved", decided_by)

    def deny(self, request_id: str, decided_by: str) -> tuple[SamResult, Optional[ApprovalRequest]]:
        return self._set_status(request_id, "denied", decided_by)

    def mark_executed(self, request_id: str, execution_result: str) -> SamResult:
        executed_at = datetime.utcnow().isoformat() + "Z"
        try:
            with _connect(self.db_path) as conn:
                cursor = conn.execute(
                    """
                    UPDATE approval_requests
                    SET status = 'executed', executed_at = ?, execution_result = ?
                    WHERE id = ? AND status = 'approved'
                    """,
                    (executed_at, execution_result, request_id),
                )
                conn.commit()
            if cursor.rowcount == 0:
                return SamResult(
                    status="failed",
                    summary="Approval request could not be marked executed.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=f"id={request_id}",
                    next_action="ask_user",
                )
            return SamResult(status="success", summary="Approval marked executed.", next_action="stop")
        except sqlite3.Error as exc:
            return SamResult(
                status="failed",
                summary="Failed to mark approval executed.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="retry",
            )

    def expire_old(self, max_age_seconds: int = 3600) -> tuple[SamResult, int]:
        cutoff = (datetime.utcnow() - timedelta(seconds=max_age_seconds)).isoformat() + "Z"
        try:
            with _connect(self.db_path) as conn:
                cursor = conn.execute(
                    """
                    UPDATE approval_requests
                    SET status = 'expired'
                    WHERE status = 'pending' AND created_at < ?
                    """,
                    (cutoff,),
                )
                conn.commit()
            return (
                SamResult(status="success", summary="Old approval requests expired.", next_action="stop"),
                int(cursor.rowcount),
            )
        except sqlite3.Error as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Failed to expire approval requests.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="retry",
                ),
                0,
            )

    def _set_status(
        self,
        request_id: str,
        status: Literal["approved", "denied"],
        decided_by: str,
    ) -> tuple[SamResult, Optional[ApprovalRequest]]:
        decided_at = datetime.utcnow().isoformat() + "Z"
        try:
            with _connect(self.db_path) as conn:
                cursor = conn.execute(
                    """
                    UPDATE approval_requests
                    SET status = ?, decided_at = ?, decided_by = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (status, decided_at, decided_by, request_id),
                )
                conn.commit()
            if cursor.rowcount == 0:
                return (
                    SamResult(
                        status="failed",
                        summary="Approval request was not pending.",
                        error_type=ErrorType.FILE_ACCESS_ERROR,
                        error_message=f"id={request_id}",
                        next_action="ask_user",
                    ),
                    None,
                )
            return self.get(request_id)
        except sqlite3.Error as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Failed to update approval request status.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="retry",
                ),
                None,
            )

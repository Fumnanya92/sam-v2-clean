"""Authority audit trail for Sam v2."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from sam_v2.diagnostics.error_types import ErrorType
from sam_v2.diagnostics.result import SamResult
from sam_v2.storage.db import _connect

from .schema import CREATE_INDEXES, CREATE_TABLES

AuthorityDecisionType = Literal["allowed", "denied", "approval_required"]


@dataclass
class AuditRecord:
    id: str
    agent_id: str
    agent_name: str
    tool_name: str
    action_category: str
    authority_decision: AuthorityDecisionType
    approval_id: Optional[str]
    executed: bool
    execution_time_ms: Optional[int]
    created_at: str


class AuthorityAuditTrail:
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
            return SamResult(status="success", summary="Authority audit schema ready.", next_action="stop")
        except sqlite3.Error as exc:
            return SamResult(
                status="failed",
                summary="Failed to initialize authority audit schema.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="retry",
            )

    def log(
        self,
        *,
        agent_id: str,
        agent_name: str,
        tool_name: str,
        action_category: str,
        authority_decision: AuthorityDecisionType,
        approval_id: Optional[str] = None,
        executed: bool = False,
        execution_time_ms: Optional[int] = None,
    ) -> tuple[SamResult, Optional[AuditRecord]]:
        record_id = str(uuid.uuid4())
        created_at = datetime.utcnow().isoformat() + "Z"
        try:
            with _connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO authority_audit_log (
                        id, agent_id, agent_name, tool_name, action_category,
                        authority_decision, approval_id, executed, execution_time_ms, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record_id,
                        agent_id,
                        agent_name,
                        tool_name,
                        action_category,
                        authority_decision,
                        approval_id,
                        1 if executed else 0,
                        execution_time_ms,
                        created_at,
                    ),
                )
                conn.commit()
            return (
                SamResult(status="success", summary="Authority audit record stored.", next_action="stop"),
                AuditRecord(
                    id=record_id,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    tool_name=tool_name,
                    action_category=action_category,
                    authority_decision=authority_decision,
                    approval_id=approval_id,
                    executed=executed,
                    execution_time_ms=execution_time_ms,
                    created_at=created_at,
                ),
            )
        except sqlite3.Error as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Failed to store authority audit record.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="retry",
                ),
                None,
            )

    def query(self, *, limit: int = 100) -> tuple[SamResult, list[AuditRecord]]:
        try:
            with _connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT * FROM authority_audit_log ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return (
                SamResult(status="success", summary="Authority audit records listed.", next_action="stop"),
                [
                    AuditRecord(
                        id=row["id"],
                        agent_id=row["agent_id"],
                        agent_name=row["agent_name"],
                        tool_name=row["tool_name"],
                        action_category=row["action_category"],
                        authority_decision=row["authority_decision"],
                        approval_id=row["approval_id"],
                        executed=bool(row["executed"]),
                        execution_time_ms=row["execution_time_ms"],
                        created_at=row["created_at"],
                    )
                    for row in rows
                ],
            )
        except sqlite3.Error as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Failed to query authority audit records.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="retry",
                ),
                [],
            )

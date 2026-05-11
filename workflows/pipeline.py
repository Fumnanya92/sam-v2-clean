"""Pipeline workflow service for Sam v2."""

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

ContentStage = Literal["draft", "review", "approved", "published", "rejected", "archived"]
ContentType = Literal["post", "email", "blog", "thread", "report", "script"]


@dataclass
class PipelineDocument:
    id: str
    title: str
    body: str
    content_type: str
    stage: str
    tags_json: str
    history_json: str
    published_channel: Optional[str]
    published_result: Optional[str]
    created_at: str
    updated_at: str


class PipelineService:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def create_draft(
        self,
        *,
        title: str,
        body: str,
        content_type: ContentType = "post",
        tags: list[str] | None = None,
    ) -> tuple[SamResult, Optional[PipelineDocument]]:
        schema_result = ensure_workflow_schema(self.db_path)
        if not schema_result.ok:
            return schema_result, None

        document_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat() + "Z"
        tags_json = json.dumps(tags or [])
        history_json = json.dumps([{"stage": "draft", "at": now}])

        try:
            with _connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO workflow_documents (
                        id, title, body, content_type, stage, tags_json, history_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?)
                    """,
                    (document_id, title, body, content_type, tags_json, history_json, now, now),
                )
                conn.commit()
            log_audit_event(
                self.db_path,
                AuditEvent(
                    event_type="pipeline_draft_created",
                    actor="workflows.pipeline",
                    summary=title,
                    metadata_json='{"document_id":"%s"}' % document_id,
                ),
            )
            return self.get_document(document_id)
        except sqlite3.Error as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Failed to create draft.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="retry",
                ),
                None,
            )

    def submit_for_review(self, document_id: str) -> tuple[SamResult, Optional[PipelineDocument]]:
        return self._transition(document_id, "review")

    def approve(self, document_id: str) -> tuple[SamResult, Optional[PipelineDocument]]:
        return self._transition(document_id, "approved")

    def publish(self, document_id: str, channel: str = "log") -> tuple[SamResult, Optional[PipelineDocument]]:
        fetch_result, document = self.get_document(document_id)
        if not fetch_result.ok or document is None:
            return fetch_result, None
        if document.stage != "approved":
            return (
                SamResult(
                    status="failed",
                    summary="Document must be approved before publish.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message=f"current_stage={document.stage}",
                    next_action="ask_user",
                ),
                None,
            )
        return self._transition(
            document_id,
            "published",
            published_channel=channel,
            published_result=f"Published to {channel}: {document.title}",
        )

    def list_documents(self, *, stage: str = "", limit: int = 50) -> tuple[SamResult, list[PipelineDocument]]:
        values: list[object] = []
        where = ""
        if stage:
            where = "WHERE stage = ?"
            values.append(stage)
        values.append(limit)
        try:
            with _connect(self.db_path) as conn:
                rows = conn.execute(
                    f"SELECT * FROM workflow_documents {where} ORDER BY updated_at DESC LIMIT ?",
                    values,
                ).fetchall()
            return (
                SamResult(status="success", summary="Workflow documents listed.", next_action="stop"),
                [PipelineDocument(**dict(row)) for row in rows],
            )
        except sqlite3.Error as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Failed to list workflow documents.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="retry",
                ),
                [],
            )

    def get_document(self, document_id: str) -> tuple[SamResult, Optional[PipelineDocument]]:
        try:
            with _connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT * FROM workflow_documents WHERE id = ?",
                    (document_id,),
                ).fetchone()
            if row is None:
                return (
                    SamResult(
                        status="failed",
                        summary="Workflow document not found.",
                        error_type=ErrorType.FILE_ACCESS_ERROR,
                        error_message=f"id={document_id}",
                        next_action="stop",
                    ),
                    None,
                )
            return (
                SamResult(status="success", summary="Workflow document fetched.", next_action="stop"),
                PipelineDocument(**dict(row)),
            )
        except sqlite3.Error as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Failed to fetch workflow document.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="retry",
                ),
                None,
            )

    def _transition(
        self,
        document_id: str,
        stage: ContentStage,
        *,
        published_channel: Optional[str] = None,
        published_result: Optional[str] = None,
    ) -> tuple[SamResult, Optional[PipelineDocument]]:
        fetch_result, document = self.get_document(document_id)
        if not fetch_result.ok or document is None:
            return fetch_result, None

        now = datetime.utcnow().isoformat() + "Z"
        history = json.loads(document.history_json)
        history.append({"stage": stage, "at": now})
        history_json = json.dumps(history)

        try:
            with _connect(self.db_path) as conn:
                cursor = conn.execute(
                    """
                    UPDATE workflow_documents
                    SET stage = ?, history_json = ?, published_channel = ?, published_result = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (stage, history_json, published_channel, published_result, now, document_id),
                )
                conn.commit()
            if cursor.rowcount == 0:
                return (
                    SamResult(
                        status="failed",
                        summary="Workflow document not found during transition.",
                        error_type=ErrorType.FILE_ACCESS_ERROR,
                        error_message=f"id={document_id}",
                        next_action="stop",
                    ),
                    None,
                )
            log_audit_event(
                self.db_path,
                AuditEvent(
                    event_type="pipeline_stage_changed",
                    actor="workflows.pipeline",
                    summary=f"{document.title} -> {stage}",
                    metadata_json='{"document_id":"%s","stage":"%s"}' % (document_id, stage),
                ),
            )
            return self.get_document(document_id)
        except sqlite3.Error as exc:
            return (
                SamResult(
                    status="failed",
                    summary="Failed to transition workflow document.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=str(exc),
                    next_action="retry",
                ),
                None,
            )

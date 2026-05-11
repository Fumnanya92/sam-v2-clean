"""Minimal request coordinator for Sam v2 runtime."""

from __future__ import annotations

import json
from pathlib import Path

from sam_v2.approvals import ApprovalManager, AuthorityEngine
from sam_v2.capabilities import CapabilityRegistry
from sam_v2.diagnostics.error_types import ErrorType
from sam_v2.diagnostics.reporting import ActionLogger, ErrorLogger, SummaryLogger
from sam_v2.diagnostics.result import SamResult
from sam_v2.diagnostics.run_logger import RunLogger
from sam_v2.intents import IntentRouter
from sam_v2.memory.manager import load_memory, update_memory
from sam_v2.memory.session import save_session_state
from sam_v2.storage.db import log_audit_event
from sam_v2.storage.models import AuditEvent

from .session import RuntimeSession


class RequestHandler:
    def __init__(
        self,
        *,
        db_path: str | Path,
        memory_path: str | Path,
        session_path: str | Path,
        workspace_root: str | Path | None = None,
        registry: CapabilityRegistry | None = None,
        authority_engine: AuthorityEngine | None = None,
        approval_manager: ApprovalManager | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.memory_path = Path(memory_path)
        self.session_path = Path(session_path)
        self.approval_manager = approval_manager or ApprovalManager(self.db_path)
        self.router = IntentRouter(
            db_path=self.db_path,
            workspace_root=workspace_root,
            registry=registry,
            authority_engine=authority_engine,
            approval_manager=self.approval_manager,
        )

    def handle(self, user_text: str, session: RuntimeSession) -> SamResult:
        run_logger = RunLogger("sam_v2 core request")
        action_logger = ActionLogger("sam_v2 core request", correlation_id=run_logger.run_id)
        error_logger = ErrorLogger("sam_v2.core.request")
        summary_logger = SummaryLogger("sam_v2 core request", correlation_id=run_logger.run_id)
        text = user_text.strip()
        run_logger.log("request_received", {"session_id": session.session_id, "text": text})
        action_logger.log("request_received", status="started", data={"session_id": session.session_id, "text": text})

        if not text:
            result = SamResult(
                status="failed",
                summary="Request text is required.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="empty request",
                next_action="ask_user",
            )
            session.record(user_text, result)
            self._save_session_state(session, run_logger)
            run_logger.log("request_rejected", {"reason": "empty_request"})
            action_logger.log("request_rejected", status="failed", data={"reason": "empty_request"})
            error_logger.log(
                event="request_rejected",
                error_type=result.error_type,
                error_message=result.error_message or result.summary,
                metadata={"session_id": session.session_id},
            )
            summary_logger.write(result, metadata={"session_id": session.session_id})
            return result

        memory_result, _memory = load_memory(self.memory_path)
        run_logger.log(
            "memory_loaded",
            {
                "status": memory_result.status,
                "summary": memory_result.summary,
                "path": str(self.memory_path),
            },
        )
        if not memory_result.ok:
            result = SamResult(
                status="failed",
                summary="Runtime could not load memory.",
                error_type=memory_result.error_type or ErrorType.FILE_ACCESS_ERROR,
                error_message=memory_result.error_message,
                next_action=memory_result.next_action or "ask_user",
                metadata={"memory_path": str(self.memory_path)},
            )
            session.record(user_text, result)
            self._save_session_state(session, run_logger)
            action_logger.log("memory_load_failed", status="failed", data={"path": str(self.memory_path)})
            error_logger.log(
                event="memory_load_failed",
                error_type=result.error_type,
                error_message=result.error_message or result.summary,
                metadata={"memory_path": str(self.memory_path)},
            )
            summary_logger.write(result, metadata={"session_id": session.session_id})
            return result

        result = self.router.handle(text, memory_block=_memory)
        session.record(text, result)
        run_logger.log(
            "request_routed",
            {
                "intent": result.metadata.get("intent", ""),
                "status": result.status,
                "summary": result.summary,
            },
        )
        action_logger.log(
            "intent_routed",
            status=result.status,
            data={"intent": result.metadata.get("intent", ""), "summary": result.summary},
        )

        daily_state_updates = {
            "last_runtime_request": text,
            "last_runtime_intent": result.metadata.get("intent", ""),
            "last_runtime_status": result.status,
            "last_runtime_summary": result.summary,
        }
        if result.metadata.get("project_id"):
            daily_state_updates["last_project_id"] = result.metadata.get("project_id", "")
        if result.metadata.get("name"):
            daily_state_updates["last_project_name"] = result.metadata.get("name", "")
        if result.metadata.get("root_path"):
            daily_state_updates["last_project_root_path"] = result.metadata.get("root_path", "")
        if result.metadata.get("intent") == "scaffold_project":
            if result.metadata.get("project_id"):
                daily_state_updates["last_created_project_id"] = result.metadata.get("project_id", "")
            if result.metadata.get("name"):
                daily_state_updates["last_created_project_name"] = result.metadata.get("name", "")
            if result.metadata.get("root_path"):
                daily_state_updates["last_created_project_root_path"] = result.metadata.get("root_path", "")

        memory_update_result, _ = update_memory(
            self.memory_path,
            {"daily_state": daily_state_updates},
            audit_db_path=self.db_path,
        )
        run_logger.log(
            "memory_updated",
            {
                "status": memory_update_result.status,
                "summary": memory_update_result.summary,
            },
        )
        if not memory_update_result.ok:
            partial = self._partial_result(
                base_result=result,
                summary="Request handled but memory update failed.",
                error_type=memory_update_result.error_type,
                error_message=memory_update_result.error_message,
                next_action=memory_update_result.next_action,
            )
            error_logger.log(
                event="memory_update_failed",
                error_type=partial.error_type,
                error_message=partial.error_message or partial.summary,
                metadata={"intent": result.metadata.get("intent", "")},
            )
            summary_logger.write(partial, metadata={"session_id": session.session_id})
            return partial

        audit_payload = {
            "session_id": session.session_id,
            "intent": result.metadata.get("intent", ""),
            "status": result.status,
            "request_count": session.request_count,
        }
        audit_result, event_id = log_audit_event(
            self.db_path,
            AuditEvent(
                event_type="runtime_request_handled",
                actor="sam_v2.core",
                summary=result.summary,
                metadata_json=json.dumps(audit_payload),
            ),
        )
        run_logger.log(
            "audit_logged",
            {
                "status": audit_result.status,
                "event_id": event_id,
                "summary": audit_result.summary,
            },
        )
        if not audit_result.ok:
            partial = self._partial_result(
                base_result=result,
                summary="Request handled but audit logging failed.",
                error_type=audit_result.error_type,
                error_message=audit_result.error_message,
                next_action=audit_result.next_action,
            )
            error_logger.log(
                event="audit_log_failed",
                error_type=partial.error_type,
                error_message=partial.error_message or partial.summary,
                metadata={"intent": result.metadata.get("intent", "")},
            )
            summary_logger.write(partial, metadata={"session_id": session.session_id})
            return partial

        save_result = self._save_session_state(session, run_logger)
        if not save_result.ok:
            partial = self._partial_result(
                base_result=result,
                summary="Request handled but session save failed.",
                error_type=save_result.error_type,
                error_message=save_result.error_message,
                next_action=save_result.next_action,
            )
            error_logger.log(
                event="session_save_failed",
                error_type=partial.error_type,
                error_message=partial.error_message or partial.summary,
                metadata={"intent": result.metadata.get("intent", "")},
            )
            summary_logger.write(partial, metadata={"session_id": session.session_id})
            return partial

        result.metadata.setdefault("session_id", session.session_id)
        if event_id is not None:
            result.metadata.setdefault("audit_event_id", event_id)
        run_logger.log("request_completed", {"status": result.status, "next_action": result.next_action})
        action_logger.log("request_completed", status=result.status, data={"next_action": result.next_action})
        if not result.ok:
            error_logger.log(
                event="request_completed_non_success",
                error_type=result.error_type,
                error_message=result.error_message or result.summary,
                metadata={"intent": result.metadata.get("intent", "")},
            )
        summary_logger.write(result, metadata={"session_id": session.session_id})
        return result

    def _save_session_state(self, session: RuntimeSession, run_logger: RunLogger) -> SamResult:
        save_result = save_session_state(self.session_path, session.to_state())
        run_logger.log(
            "session_saved",
            {
                "status": save_result.status,
                "summary": save_result.summary,
                "path": str(self.session_path),
            },
        )
        return save_result

    def _partial_result(
        self,
        *,
        base_result: SamResult,
        summary: str,
        error_type: ErrorType | None,
        error_message: str | None,
        next_action: str | None,
    ) -> SamResult:
        metadata = dict(base_result.metadata)
        return SamResult(
            status="partial",
            summary=summary,
            error_type=error_type,
            error_message=error_message,
            next_action=next_action or base_result.next_action,
            metadata=metadata,
        )

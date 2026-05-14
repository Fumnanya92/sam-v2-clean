"""Minimal request coordinator for Sam v2 runtime."""

from __future__ import annotations

import json
from pathlib import Path

from approvals import ApprovalManager, AuthorityEngine
from capabilities import CapabilityRegistry
from diagnostics.error_types import ErrorType
from diagnostics.reporting import ActionLogger, ErrorLogger, SummaryLogger
from diagnostics.result import SamResult
from diagnostics.trace import ensure_trace
from diagnostics.run_logger import RunLogger
from core.conversation_state import ConversationState
from intents import IntentRouter
from memory.manager import load_memory, update_memory
from memory.long_term import (
    classify_intent,
    consolidate_memories,
    create_coding_review,
    extract_candidate_memories,
    learn,
    log_turn,
    save_memory_candidate,
    snapshot as long_term_snapshot,
    summarize_conversation_if_needed,
)
from memory.session import save_session_state
from storage.db import log_audit_event
from storage.models import AuditEvent

from .goal_state import GoalState
from .execution_engine import RuntimeExecutionEngine
from .session import RuntimeSession
from .workflow_runtime import WorkflowRuntime


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
        self.request_parser = self.router
        self.legacy_executor = self.router
        self.conversation_state = self.router.conversation_state
        self.registry = self.router.registry
        self.tool_executor = self.router.tool_executor
        self.task_planner = self.router.task_planner
        self.observation_loop = self.router.observation_loop
        self.authority_gate = self.router.authority_gate
        self.execution_engine = RuntimeExecutionEngine(
            tool_executor=self.tool_executor,
            task_planner=self.task_planner,
            observation_loop=self.observation_loop,
            db_path=str(self.db_path),
        )
        self.workflow_runtime = WorkflowRuntime()

    def handle(self, user_text: str, session: RuntimeSession) -> SamResult:
        run_logger = RunLogger("sam_v2 core request")
        action_logger = ActionLogger("sam_v2 core request", correlation_id=run_logger.run_id)
        error_logger = ErrorLogger("core.request")
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

        long_term = long_term_snapshot(
            self.db_path,
            session_id=session.session_id,
            query=text,
            scope=str(_memory.get("daily_state", {}).get("last_project_root_path", {}).get("value", "")),
        )
        _memory["long_term"] = {"value": long_term}
        _memory["compact_context"] = {"value": long_term.get("compact_context", {})}
        _memory["detected_intent"] = {"value": long_term.get("detected_intent", classify_intent(text))}
        coding_model = self.router.coding_model_status() if hasattr(self.router, "coding_model_status") else {}
        _memory["coding_model"] = {"value": coding_model}

        parsed_hint = self.request_parser.parse(text, _memory)
        result = ensure_trace(
            self.workflow_runtime.run_turn(
                user_text=text,
                parsed_hint=parsed_hint,
                memory_block=_memory,
                authorize=self._authorize_request,
                execute=lambda req: self.execution_engine.execute(
                    req,
                    memory_block=_memory,
                    legacy_execute=lambda legacy_req: self.legacy_executor.handle_compatibility(
                        text,
                        memory_block=_memory,
                        parsed_request=legacy_req,
                        prechecked_authority=True,
                    ),
                ),
            )
        )
        self._record_long_term_turns(
            session_id=session.session_id,
            user_text=text,
            result=result,
            long_term=long_term,
            request_count=session.request_count + 1,
        )
        # Preserve router-produced conversation state; synthesize only when missing.
        conversation_state_updates = result.metadata.get("conversation_state")
        if not isinstance(conversation_state_updates, dict):
            prior_state = ConversationState.from_memory(_memory)
            conversation_state_updates = self.conversation_state.writeback(result, prior_state)
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
            "last_detected_intent": long_term.get("detected_intent", ""),
            "last_runtime_status": result.status,
            "last_runtime_summary": result.summary,
        }
        coding_model_metadata = result.metadata.get("active_coding_model")
        if coding_model_metadata:
            daily_state_updates["active_coding_model"] = coding_model_metadata
        if result.metadata.get("project_id"):
            daily_state_updates["last_project_id"] = result.metadata.get("project_id", "")
        if result.metadata.get("name"):
            daily_state_updates["last_project_name"] = result.metadata.get("name", "")
        if result.metadata.get("root_path"):
            daily_state_updates["last_project_root_path"] = result.metadata.get("root_path", "")
        if result.metadata.get("path"):
            daily_state_updates["last_file_path"] = result.metadata.get("path", "")
        discovery_updates = result.metadata.get("discovery_state")
        discovery_payload = {"value": discovery_updates} if discovery_updates else {"value": {}}
        scaffold_pending = result.metadata.get("pending_scaffold")
        scaffold_payload = {"value": scaffold_pending} if isinstance(scaffold_pending, dict) else {"value": {}}
        conversation_state_payload = {"value": conversation_state_updates} if isinstance(conversation_state_updates, dict) else {"value": {}}
        goal_state = result.metadata.get("goal_state")
        goal_state_payload = {"value": goal_state} if isinstance(goal_state, dict) else {"value": GoalState().to_memory()}
        if result.metadata.get("intent") == "scaffold_project":
            if result.metadata.get("project_id"):
                daily_state_updates["last_created_project_id"] = result.metadata.get("project_id", "")
            if result.metadata.get("name"):
                daily_state_updates["last_created_project_name"] = result.metadata.get("name", "")
            if result.metadata.get("root_path"):
                daily_state_updates["last_created_project_root_path"] = result.metadata.get("root_path", "")

        existing_recent = _memory.get("conversation", {}).get("recent_requests", {}).get("value", [])
        if not isinstance(existing_recent, list):
            existing_recent = []
        recent_entry = {
            "request": text,
            "intent": result.metadata.get("intent", ""),
            "status": result.status,
            "summary": result.summary,
        }
        if result.metadata.get("name"):
            recent_entry["project"] = result.metadata.get("name", "")
        if result.metadata.get("root_path"):
            recent_entry["root_path"] = result.metadata.get("root_path", "")
        if result.metadata.get("path"):
            recent_entry["path"] = result.metadata.get("path", "")
        recent_requests = [*existing_recent, recent_entry][-20:]

        memory_update_result, _ = update_memory(
            self.memory_path,
            {
                "daily_state": daily_state_updates,
                "discovery": discovery_payload,
                "scaffold_pending": scaffold_payload,
                "conversation_state": conversation_state_payload,
                "goal_state": goal_state_payload,
                "conversation": {"recent_requests": recent_requests},
            },
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
                actor="core",
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

    def discover_projects(self) -> SamResult:
        if hasattr(self.router, "discover_projects"):
            return self.router.discover_projects()
        return SamResult(status="success", summary="Project discovery is not available.", next_action="stop")

    def _record_long_term_turns(
        self,
        *,
        session_id: str,
        user_text: str,
        result: SamResult,
        long_term: dict[str, object],
        request_count: int = 0,
    ) -> None:
        scope = str(result.metadata.get("root_path") or result.metadata.get("path") or "")
        action = str(result.metadata.get("intent", ""))
        user_log = log_turn(
            self.db_path,
            session_id=session_id,
            role="user",
            message=user_text,
            action=action,
            scope=scope,
        )
        sam_log = log_turn(
            self.db_path,
            session_id=session_id,
            role="sam",
            message=result.summary,
            action=action,
            scope=scope,
            metadata={
                "status": result.status,
                "next_action": result.next_action,
                "autonomous_steps": result.metadata.get("autonomous_steps", 0),
            },
        )
        source_conversation_id = str(
            sam_log.metadata.get("conversation_id")
            or user_log.metadata.get("conversation_id")
            or ""
        )
        related_project_id = str(
            result.metadata.get("project_id")
            or result.metadata.get("root_path")
            or result.metadata.get("path")
            or ""
        )
        detected_intent = classify_intent(user_text)
        for candidate in extract_candidate_memories(
            source_conversation_id=source_conversation_id,
            source_session_id=session_id,
            user_text=user_text,
            assistant_text=result.summary,
            intent=detected_intent if detected_intent != "unclear" else action,
            related_project_id=related_project_id,
        ):
            save_memory_candidate(self.db_path, candidate)

        if detected_intent in {"coding", "debugging", "review"} or action in {"autonomous_request", "delegate_coding_task"}:
            create_coding_review(
                self.db_path,
                session_id=session_id,
                related_project_id=related_project_id,
                source_conversation_id=source_conversation_id,
                planned_requirements=user_text if detected_intent in {"coding", "debugging"} else "",
                implementation_status="completed" if result.ok else "blocked",
                missing_items="" if result.ok else (result.error_message or result.summary),
                test_status=str(result.metadata.get("test_status", "")),
                review_notes=result.summary,
            )

        summarize_conversation_if_needed(self.db_path, session_id=session_id)
        if request_count and request_count % 10 == 0:
            consolidate_memories(self.db_path)

        if result.ok:
            learn(
                self.db_path,
                situation=user_text,
                what_worked=result.summary[:1000],
                tool_used=action,
                scope=scope,
                metadata={"source": "request_handler"},
            )
        elif result.error_message or result.summary:
            learn(
                self.db_path,
                situation=user_text,
                what_failed=(result.error_message or result.summary)[:1000],
                tool_used=action,
                scope=scope,
                metadata={"source": "request_handler", "long_term_snapshot": bool(long_term)},
            )

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

    def _authorize_request(self, request: object) -> SamResult | None:
        if not hasattr(request, "intent"):
            return None
        intent = str(getattr(request, "intent", "")).strip()
        if not intent:
            return None
        if intent in {"chat", "clarify"}:
            # Let router/runtime follow-up resolution run before capability gating.
            return None
        capability = self.registry.get(intent)
        if capability is None:
            # Planner-executable tools are allowed during migration even if capability registry lags.
            if self.tool_executor.get(intent) is not None:
                return None
            return SamResult(
                status="failed",
                summary="Intent is not registered.",
                error_type=ErrorType.MISSING_CAPABILITY,
                error_message=intent,
                next_action="ask_user",
                metadata={"intent": intent},
            )
        return self.authority_gate.check(request, capability.action_category)

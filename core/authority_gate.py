"""Runtime authority and approval gate."""

from __future__ import annotations

from typing import Protocol

from approvals import ApprovalManager, AuthorityEngine
from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult


class AuthorityRequest(Protocol):
    intent: str
    raw_text: str
    parameters: dict[str, object]


class RuntimeAuthorityGate:
    """Checks runtime authority without making the router own policy."""

    def __init__(
        self,
        *,
        authority_engine: AuthorityEngine | None,
        approval_manager: ApprovalManager | None,
        agent_id: str = "sam_v2_runtime",
        agent_name: str = "Sam v2 Runtime",
    ) -> None:
        self.authority_engine = authority_engine
        self.approval_manager = approval_manager
        self.agent_id = agent_id
        self.agent_name = agent_name

    def check(self, request: AuthorityRequest, action_category: str) -> SamResult | None:
        if self.authority_engine is None:
            return None

        decision = self.authority_engine.check(
            agent_id=self.agent_id,
            agent_level=5,
            role_id="supervisor",
            tool_name=request.intent,
            action_category=action_category,
        )
        if not decision.allowed:
            return SamResult(
                status="blocked",
                summary="Action blocked by authority rules.",
                error_type=ErrorType.MISSING_PERMISSION,
                error_message=decision.reason,
                next_action="request_approval",
                metadata={"intent": request.intent, "action_category": action_category},
            )

        if not decision.requires_approval:
            return None

        if self.approval_manager is None:
            return SamResult(
                status="needs_approval",
                summary="Action requires approval.",
                error_type=ErrorType.MISSING_PERMISSION,
                error_message=decision.reason,
                next_action="request_approval",
                metadata={"intent": request.intent, "action_category": action_category},
            )

        create_result, approval = self.approval_manager.create_request(
            agent_id=self.agent_id,
            agent_name=self.agent_name,
            tool_name=request.intent,
            tool_arguments=request.parameters,
            action_category=action_category,
            reason=decision.reason,
            context=request.raw_text,
        )
        if not create_result.ok or approval is None:
            return SamResult(
                status="failed",
                summary="Approval was required but request creation failed.",
                error_type=create_result.error_type or ErrorType.FILE_ACCESS_ERROR,
                error_message=create_result.error_message,
                next_action="retry",
            )

        return SamResult(
            status="needs_approval",
            summary="Action requires approval before execution.",
            error_type=ErrorType.MISSING_PERMISSION,
            error_message=decision.reason,
            next_action="request_approval",
            metadata={"approval_id": approval.id, "intent": request.intent},
        )

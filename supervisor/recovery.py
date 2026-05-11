"""Shared retry and escalation policy for Sam v2."""

from __future__ import annotations

from dataclasses import dataclass

from sam_v2.diagnostics.error_types import ErrorType
from sam_v2.diagnostics.result import SamResult


@dataclass
class RecoveryDecision:
    action: str
    reason: str
    should_retry: bool = False
    should_escalate: bool = False
    should_stop: bool = False


class RecoveryPolicy:
    """Central decision layer for retry and escalation behavior."""

    RETRYABLE_ERRORS = {
        ErrorType.COMMAND_FAILED,
        ErrorType.TEST_FAILED,
        ErrorType.TIMEOUT,
    }

    def decide(self, result: SamResult, *, attempt: int, max_attempts: int) -> RecoveryDecision:
        if result.status in {"needs_approval", "blocked"}:
            return RecoveryDecision(
                action="request_approval",
                reason=result.summary,
                should_stop=True,
            )

        if result.ok:
            return RecoveryDecision(
                action="stop",
                reason="Step succeeded.",
                should_stop=True,
            )

        if (
            result.error_type in self.RETRYABLE_ERRORS
            and attempt < max_attempts
            and result.next_action == "retry"
        ):
            return RecoveryDecision(
                action="retry",
                reason=result.summary,
                should_retry=True,
            )

        if result.next_action == "escalate_worker":
            return RecoveryDecision(
                action="escalate_worker",
                reason=result.summary,
                should_escalate=True,
            )

        return RecoveryDecision(
            action="stop",
            reason=result.summary,
            should_stop=True,
        )

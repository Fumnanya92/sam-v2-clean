"""Legacy compatibility adapter for runtime execution."""

from __future__ import annotations

from typing import Any

from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult


class LegacyIntentAdapter:
    """Compatibility layer for requests that still miss the runtime path."""

    def __init__(self, router: Any) -> None:
        self.router = router

    def handle(
        self,
        *,
        user_text: str,
        request: Any,
        state: Any,
        memory_block: dict[str, Any] | None,
    ) -> SamResult:
        if request.intent == "chat":
            summary = request.response_text or "I need a bit more detail before I act."
            result = SamResult(
                status="success",
                summary=summary,
                next_action="ask_user",
                metadata={
                    "intent": "chat",
                    "source": request.source,
                    "confidence": request.confidence,
                    "execution_path": "legacy_adapter",
                },
            )
            self.router.conversation_state.writeback(result, state)
            return result

        tool = self.router.tool_executor.get(request.intent)
        if tool is not None:
            result = self.router.tool_executor.execute_with_tracking(
                request.intent,
                {"request": request, "memory": memory_block},
            )
            result.metadata.setdefault("intent", request.intent)
            result.metadata.setdefault("source", request.source)
            result.metadata.setdefault("confidence", request.confidence)
            self.router.conversation_state.writeback(result, state)
            return result

        fallback = SamResult(
            status="failed",
            summary="Legacy adapter could not resolve this request.",
            error_type=ErrorType.MISSING_CAPABILITY,
            error_message=request.intent,
            next_action="ask_user",
            metadata={
                "intent": request.intent,
                "source": request.source,
                "confidence": request.confidence,
                "execution_path": "legacy_adapter",
            },
        )
        self.router.conversation_state.writeback(fallback, state)
        return fallback

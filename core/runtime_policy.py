"""Central runtime decision policy for operational control flow."""

from __future__ import annotations

from dataclasses import dataclass

from diagnostics.result import SamResult

from .goal_state import GoalState
from .request_model import IntentRequest


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    reason: str = ""


class RuntimeDecisionPolicy:
    """Single policy surface for pre-action and post-observation decisions."""

    def decide_pre_action(
        self,
        *,
        user_text: str,
        hint: IntentRequest,
        state: GoalState,
        recent_history: list[dict[str, object]],
    ) -> PolicyDecision:
        lowered = user_text.strip().lower()
        if lowered in {"continue", "go on", "again", "yes"} and _is_operational_tool(state.current_tool):
            return PolicyDecision("continue", "user_requested_continue")
        if lowered in {"stop", "cancel", "abort"}:
            return PolicyDecision("stop", "user_requested_stop")
        if any(token in lowered for token in ("summarize", "synthesize")):
            return PolicyDecision("synthesize", "user_requested_synthesis")
        if any(token in lowered for token in ("delegate", "hand off")):
            return PolicyDecision("delegate", "user_requested_delegation")
        if "switch tool" in lowered:
            return PolicyDecision("switch_tool", "user_requested_tool_switch")
        if hint.needs_clarification:
            if self._has_enough_context_to_execute(hint=hint, state=state, recent_history=recent_history):
                return PolicyDecision("execute", "parser_clarification_overridden_by_operational_context")
            return PolicyDecision("clarify", "parser_requested_clarification")
        if lowered in {"retry", "try again"}:
            return PolicyDecision("retry", "user_requested_retry")
        if (
            hint.intent in {"chat", "clarify"}
            and _is_operational_tool(state.current_tool)
            and state.status in {"running", "awaiting_input"}
            and state.next_expected_action != "stop"
        ):
            return PolicyDecision("continue", "stateful_followup")
        if recent_history:
            last = recent_history[-1]
            if str(last.get("status", "")).lower() in {"failed", "partial"} and _is_operational_tool(state.current_tool):
                return PolicyDecision("retry", "prior_step_failed")
        return PolicyDecision("execute", "default_execute")

    @staticmethod
    def _has_enough_context_to_execute(
        *,
        hint: IntentRequest,
        state: GoalState,
        recent_history: list[dict[str, object]],
    ) -> bool:
        if hint.intent in {"chat", "clarify"}:
            return False
        if hint.intent in {"set_coding_model", "show_coding_model"}:
            return True
        if hint.intent == "autonomous_request":
            if state.current_candidates or state.last_observation or state.current_tool:
                return True
            for item in reversed(recent_history[-5:]):
                if str(item.get("root_path", "") or item.get("path", "")).strip():
                    return True
        return False

    def synthesize_post_result(
        self,
        *,
        decision: PolicyDecision,
        state: GoalState,
        result: SamResult,
    ) -> SamResult:
        # Keep explicit executor intent when present.
        if result.next_action:
            return result
        if result.ok:
            result.next_action = "stop"
            return result
        # Prefer retry early in a sequence, then ask user.
        result.next_action = "retry" if state.turn_count < 2 or decision.action == "retry" else "ask_user"
        return result


def _is_operational_tool(tool_name: str) -> bool:
    return tool_name.strip() not in {"", "chat", "clarify"}

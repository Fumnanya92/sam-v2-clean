"""Goal-driven runtime coordinator that treats parse as a weak signal."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

from diagnostics.result import SamResult
from diagnostics.trace import append_trace, trace_step
from intents import IntentRequest

from .goal_state import GoalState
from .runtime_policy import PolicyDecision, RuntimeDecisionPolicy


class WorkflowRuntime:
    """Operational coordinator for one runtime turn.

    This layer sits above intent routing and decides whether to:
    - continue active work,
    - honor parser hints,
    - or ask for clarification.
    """

    def __init__(self, policy: RuntimeDecisionPolicy | None = None) -> None:
        self.policy = policy or RuntimeDecisionPolicy()

    def run_turn(
        self,
        *,
        user_text: str,
        parsed_hint: IntentRequest,
        memory_block: dict[str, Any] | None,
        authorize: Callable[[IntentRequest], SamResult | None] | None = None,
        execute: Callable[[IntentRequest], SamResult],
    ) -> SamResult:
        state = GoalState.from_memory(memory_block)
        events: list[dict[str, Any]] = []
        events.append(
            trace_step(
                "Runtime stage resolved",
                action="resolve_stage",
                observation=state.workflow_stage or "idle",
                metadata={"status": state.status, "turn_count": state.turn_count},
            )
        )
        recent_history = self._recent_history(memory_block)

        policy_decision = self.policy.decide_pre_action(
            user_text=user_text,
            hint=parsed_hint,
            state=state,
            recent_history=recent_history,
        )
        effective_request = self._decide_request(
            user_text=user_text,
            hint=parsed_hint,
            state=state,
            policy_decision=policy_decision,
            events=events,
        )
        if not isinstance(effective_request.parameters, dict):
            effective_request.parameters = {}
        effective_request.parameters["_runtime"] = {
            "decision": policy_decision.action,
            "decision_reason": policy_decision.reason,
            "goal_state": state.to_memory(),
            "recent_history": recent_history[-5:],
            "last_observation": state.last_observation,
        }
        events.append(
            trace_step(
                "Runtime action selected",
                action=effective_request.intent,
                observation=f"{effective_request.source} ({policy_decision.action})",
            )
        )
        if authorize is not None:
            auth_result = authorize(effective_request)
            if auth_result is not None:
                events.append(
                    trace_step(
                        "Runtime policy gate",
                        status=auth_result.status,
                        action=effective_request.intent,
                        observation=auth_result.summary,
                    )
                )
                state = self._update_state_from_result(state, user_text, effective_request, auth_result)
                auth_result.metadata.setdefault("parsed_intent_hint", parsed_hint.intent)
                auth_result.metadata["goal_state"] = state.to_memory()
                auth_result.metadata["operational_events"] = events
                auth_result.metadata["runtime_events"] = self._build_runtime_events(
                    user_text=user_text,
                    request=effective_request,
                    result=auth_result,
                    events=events,
                )
                return append_trace(auth_result, *events)
        result = execute(effective_request)
        result = self.policy.synthesize_post_result(decision=policy_decision, state=state, result=result)
        state = self._update_state_from_result(state, user_text, effective_request, result)

        result.metadata.setdefault("parsed_intent_hint", parsed_hint.intent)
        result.metadata["goal_state"] = state.to_memory()
        result.metadata["operational_events"] = events
        result.metadata["runtime_events"] = self._build_runtime_events(
            user_text=user_text,
            request=effective_request,
            result=result,
            events=events,
        )
        result = append_trace(result, *events)
        return result

    def _decide_request(
        self,
        *,
        user_text: str,
        hint: IntentRequest,
        state: GoalState,
        policy_decision: PolicyDecision,
        events: list[dict[str, Any]],
    ) -> IntentRequest:
        lowered = user_text.strip().lower()
        followup_markers = {"continue", "go on", "again", "yes"}
        retry_markers = {"retry", "try again"}
        if (
            hint.intent in {"chat", "clarify"}
            and state.current_tool
            and (
                lowered in followup_markers
                or lowered in retry_markers
                or "it" in lowered
                or "that" in lowered
                or policy_decision.action in {"continue", "retry"}
            )
        ):
            events.append(
                trace_step(
                    "Intent hint overridden",
                    status="info",
                    action=state.current_tool,
                    reason=f"continuing active operational state ({policy_decision.action})",
                )
            )
            return replace(
                hint,
                intent=state.current_tool,
                needs_clarification=False,
                clarification_question="",
                source="workflow_runtime",
            )
        return hint

    def _update_state_from_result(
        self,
        state: GoalState,
        user_text: str,
        request: IntentRequest,
        result: SamResult,
    ) -> GoalState:
        next_state = GoalState(**state.to_memory())
        next_state.turn_count += 1
        next_state.current_objective = state.current_objective or user_text.strip()
        next_state.active_work = request.intent
        next_state.current_tool = str(result.metadata.get("tool", request.intent))
        next_state.last_observation = result.summary
        next_state.current_candidates = self._extract_candidates(result)
        next_state.next_expected_action = result.next_action or "stop"
        next_state.workflow_stage = "observing" if result.ok else "blocked"
        next_state.status = "running" if result.ok else "awaiting_input"
        next_state.pending_decisions = []
        next_state.unresolved_blockers = []
        if not result.ok:
            next_state.unresolved_blockers.append(result.error_message or result.summary)
            next_state.pending_decisions.append("retry_or_clarify")
        if result.next_action == "ask_user" or request.needs_clarification:
            next_state.status = "awaiting_input"
            next_state.workflow_stage = "awaiting_user"
            next_state.pending_decisions.append("user_input_required")
        return next_state

    @staticmethod
    def _extract_candidates(result: SamResult) -> list[str]:
        raw = result.metadata.get("candidates", [])
        if not isinstance(raw, list):
            return []
        return [str(item) for item in raw[:20]]

    @staticmethod
    def _recent_history(memory_block: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not isinstance(memory_block, dict):
            return []
        recent = memory_block.get("conversation", {}).get("recent_requests", {}).get("value", [])
        if not isinstance(recent, list):
            return []
        return [item for item in recent if isinstance(item, dict)]

    @staticmethod
    def _build_runtime_events(
        *,
        user_text: str,
        request: IntentRequest,
        result: SamResult,
        events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        runtime_events: list[dict[str, Any]] = [
            {"type": "runtime.input", "message": user_text},
            {"type": "runtime.action", "intent": request.intent, "source": request.source},
        ]
        for event in events:
            if not isinstance(event, dict):
                continue
            runtime_events.append(
                {
                    "type": "runtime.event",
                    "label": str(event.get("label", "")),
                    "status": str(event.get("status", "")),
                    "tool": str(event.get("tool", "")),
                    "action": str(event.get("action", "")),
                    "observation": str(event.get("observation", "")),
                    "reason": str(event.get("reason", "")),
                }
            )
        runtime_events.append(
            {
                "type": "runtime.result",
                "status": result.status,
                "summary": result.summary,
                "next_action": result.next_action or "stop",
                "tool": str(result.metadata.get("tool", "")),
                "command": result.metadata.get("run_command", result.metadata.get("command", "")),
                "error": result.error_message or "",
            }
        )
        return runtime_events

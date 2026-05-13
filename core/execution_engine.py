"""Primary runtime execution engine (planner -> tool -> observe)."""

from __future__ import annotations

from typing import Any, Callable

from diagnostics.result import SamResult
from diagnostics.trace import append_trace, trace_step
from intents import IntentRequest, IntentRouter


class RuntimeExecutionEngine:
    """Executes requests via planner/observation loop with legacy fallback."""

    def __init__(self, router: IntentRouter) -> None:
        self.router = router

    def execute(
        self,
        request: IntentRequest,
        *,
        memory_block: dict[str, Any] | None,
        legacy_execute: Callable[[IntentRequest], SamResult],
    ) -> SamResult:
        if request.intent in {"chat", "clarify"}:
            return legacy_execute(request)

        tool_def = self.router.tool_executor.get(request.intent)
        if tool_def is None:
            result = legacy_execute(request)
            result.metadata.setdefault("execution_path", "legacy_fallback")
            return append_trace(
                result,
                trace_step(
                    "Execution path",
                    action="legacy_fallback",
                    reason=f"no registered tool for {request.intent}",
                ),
            )

        runtime_context = request.parameters.get("_runtime", {}) if isinstance(request.parameters, dict) else {}
        goal_state = runtime_context.get("goal_state", {}) if isinstance(runtime_context, dict) else {}
        recent_history = runtime_context.get("recent_history", []) if isinstance(runtime_context, dict) else []
        observations: list[dict[str, Any]] = []
        if isinstance(recent_history, list):
            for item in recent_history[-3:]:
                if isinstance(item, dict):
                    observations.append(
                        {
                            "summary": str(item.get("summary", "")),
                            "status": str(item.get("status", "")),
                            "intent": str(item.get("intent", "")),
                        }
                    )

        plan = self.router.task_planner.plan(
            str(goal_state.get("current_objective", "")).strip() or request.raw_text or request.intent,
            context={
                "request": request,
                "memory": memory_block,
                "intent": request.intent,
                "available_tools": self.router.tool_executor.available_tools,
                "goal_state": goal_state,
                "runtime_context": runtime_context,
                "recent_history": recent_history,
                "observations": observations,
            },
        )
        result, step_executions = self.router.observation_loop.execute_plan(plan, memory_block)
        result.metadata.setdefault("execution_path", "runtime_engine")
        result.metadata.setdefault("execution_steps", len(step_executions))
        result.metadata.setdefault("plan_mode", plan.mode)
        result.metadata.setdefault("plan_action", plan.plan_action.value)
        result.metadata.setdefault("request_intent", request.intent)
        result.metadata.setdefault("intent", request.intent)
        return append_trace(
            result,
            trace_step("Execution path", action="runtime_engine", tool=plan.tool_name),
            trace_step("Observation loop", observation=f"{len(step_executions)} step(s)"),
        )

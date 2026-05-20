"""Task planning primitives for Sam.

Phase 1 keeps the planner intentionally small. It gives the router a clean
place to ask, "what should happen next?" without putting business logic inside
the router itself.

Phase 2 adds multi-step planning: PlanningStep models track thought/action/tool/worker/status/observation
throughout plan execution. TaskPlanner inspects goals and available tools, generating either:
- direct: simple tasks execute immediately
- multi_step: complex tasks generate execution steps for workers
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping


class PlanningStepStatus(str, Enum):
    """Status of a planning step during execution."""
    pending = "pending"
    running = "running"
    observing = "observing"
    completed = "completed"
    failed = "failed"


class PlanAction(str, Enum):
    execute = "execute"
    continue_flow = "continue"
    retry = "retry"
    clarify = "clarify"
    stop = "stop"
    switch_tool = "switch_tool"
    delegate = "delegate"
    synthesize = "synthesize"


@dataclass(slots=True)
class PlanningStep:
    """A single step in a multi-step execution plan.
    
    Tracks the thought process, action, tool selection, worker assignment,
    status, and observations throughout plan execution.
    """
    
    thought: str  # Reasoning for this step
    action: str   # What action to take
    tool: str     # Tool name to execute
    worker: str   # Worker assignment (e.g., "tool_executor", "project_inspector")
    status: PlanningStepStatus = PlanningStepStatus.pending
    observation: str = ""  # Result/output from execution


@dataclass(slots=True)
class MultiStepPlan:
    """A multi-step execution plan with worker steps."""
    
    goal: str
    steps: list[PlanningStep] = field(default_factory=list)
    current_step_idx: int = 0


@dataclass(slots=True)
class TaskPlan:
    """A lightweight execution plan produced from a user request.
    
    Supports both direct mode (single-step) and multi-step mode (complex plans).
    """

    goal: str
    tool_name: str
    payload: dict[str, Any] = field(default_factory=dict)
    mode: str = "direct"  # "direct" or "multi_step"
    multi_step_plan: MultiStepPlan | None = None
    plan_action: PlanAction = PlanAction.execute


class TaskPlanner:
    """Create execution plans from user goals and available tool metadata.

    This class should remain generic. It should inspect capabilities/tools and
    produce a plan; it should not execute tools or contain project-specific
    routing assumptions.
    
    Phase 2 enhancement: Generates either direct (simple) or multi_step (complex) plans
    by analyzing the user goal and available tools.
    """

    def __init__(
        self,
        capability_registry: Any | None = None,
        *,
        work_kind_classifier: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    ) -> None:
        self.capability_registry = capability_registry
        self.work_kind_classifier = work_kind_classifier

    def plan(self, request: str, context: Mapping[str, Any] | None = None) -> TaskPlan:
        """Generate a direct or multi-step execution plan based on request complexity.
        
        Simple tasks (e.g., "list tasks", "show capabilities") → direct mode
        Complex tasks (e.g., "create project with scaffolding") → multi_step mode
        """
        normalized_request = request.strip()
        context_data = dict(context or {})
        available_tools = self._normalize_available_tools(context_data.get("available_tools"))
        runtime_context = context_data.get("runtime_context", {})
        if not isinstance(runtime_context, dict):
            runtime_context = {}
        goal_state = context_data.get("goal_state", {})
        if not isinstance(goal_state, dict):
            goal_state = {}
        requested_tool = self._first_present(
            runtime_context.get("requested_tool"),
            context_data.get("tool_name"),
            context_data.get("intent"),
            context_data.get("capability"),
        )
        runtime_decision = str(runtime_context.get("decision", "")).strip().lower()
        if runtime_decision in {"continue", "retry"}:
            requested_tool = self._first_present(goal_state.get("current_tool"), requested_tool)

        plan_action = self._plan_action_from_context(runtime_context, goal_state)
        coding_gate = self._classify_for_coding_delegation(normalized_request, context_data, available_tools)
        if coding_gate.get("delegate"):
            payload = {
                "request": context_data.get("request", normalized_request),
                "context": context_data,
                "runtime_context": runtime_context,
                "goal_state": goal_state,
                "coding_gate": coding_gate,
            }
            request_obj = context_data.get("request")
            request_parameters = getattr(request_obj, "parameters", None)
            if isinstance(request_parameters, dict):
                payload.update({key: value for key, value in request_parameters.items() if not str(key).startswith("_")})
            memory_block = context_data.get("memory")
            if memory_block is not None:
                payload["memory"] = memory_block
            return TaskPlan(
                goal=normalized_request,
                tool_name="delegate_coding_task",
                payload=payload,
                mode="direct",
                plan_action=PlanAction.delegate,
            )

        # Explicit tool requests already have an execution target. Keep those
        # direct so metadata and tool observations are not swallowed by generic
        # planner-only steps.
        has_explicit_tool = bool(requested_tool)
        is_complex = False if has_explicit_tool else self._is_complex_request(normalized_request, available_tools)
        mode = "multi_step" if is_complex and plan_action == PlanAction.execute else "direct"
        
        payload: dict[str, Any] = {
            "request": context_data.get("request", normalized_request),
            "context": context_data,
            "runtime_context": runtime_context,
            "goal_state": goal_state,
        }
        request_obj = context_data.get("request")
        request_parameters = getattr(request_obj, "parameters", None)
        if isinstance(request_parameters, dict):
            payload.update({key: value for key, value in request_parameters.items() if not str(key).startswith("_")})
        memory_block = context_data.get("memory")
        if memory_block is not None:
            payload["memory"] = memory_block
        
        # For multi-step, generate execution steps
        multi_step_plan = None
        if mode == "multi_step":
            multi_step_plan = self._generate_steps(normalized_request, available_tools, context_data)
            # First tool in multi-step plan
            tool_name = multi_step_plan.steps[0].tool if multi_step_plan.steps else self._select_tool(requested_tool, available_tools)
        else:
            # Direct mode: select single tool
            tool_name = self._select_tool(requested_tool, available_tools)

        return TaskPlan(
            goal=normalized_request,
            tool_name=tool_name,
            payload=payload,
            mode=mode,
            multi_step_plan=multi_step_plan,
            plan_action=plan_action,
        )

    def _classify_for_coding_delegation(
        self,
        request: str,
        context_data: Mapping[str, Any],
        available_tools: set[str],
    ) -> dict[str, Any]:
        if "delegate_coding_task" not in available_tools:
            return {"delegate": False, "work_kind": "conversational", "reason": "delegation tool unavailable"}

        memory_block = context_data.get("memory")
        coding_model = {}
        if isinstance(memory_block, dict):
            raw = memory_block.get("coding_model", {})
            coding_model = raw.get("value", raw) if isinstance(raw, dict) else {}
        active_model = str(coding_model.get("active_coding_model", "local") if isinstance(coding_model, dict) else "local").lower()
        if active_model in {"", "local"}:
            return {"delegate": False, "work_kind": "conversational", "reason": "external coding model is off"}

        request_obj = context_data.get("request")
        requested_intent = str(getattr(request_obj, "intent", context_data.get("intent", ""))).strip()
        if requested_intent in {"chat", "clarify", "set_coding_model", "show_coding_model", "capabilities", "discovery_workflow", "list_projects", "list_directory"}:
            return {"delegate": False, "work_kind": "conversational", "reason": "local/control request"}

        if self.work_kind_classifier is None:
            if requested_intent == "autonomous_request":
                return {
                    "delegate": True,
                    "work_kind": "autonomous_request",
                    "requires_repo_code": True,
                    "confidence": "high",
                    "reason": "active external coding model handles autonomous investigation",
                    "active_coding_model": active_model,
                }
            return {"delegate": False, "work_kind": "conversational", "reason": "classifier unavailable"}

        try:
            classification = dict(self.work_kind_classifier(request, context_data))
        except Exception as exc:
            if requested_intent == "autonomous_request":
                return {
                    "delegate": True,
                    "work_kind": "autonomous_request",
                    "requires_repo_code": True,
                    "confidence": "high",
                    "reason": f"active external coding model handles autonomous investigation; classifier failed: {exc}",
                    "active_coding_model": active_model,
                }
            return {"delegate": False, "work_kind": "conversational", "reason": f"classifier failed: {exc}"}

        work_kind = str(classification.get("work_kind", "conversational")).lower()
        requires_repo_code = bool(classification.get("requires_repo_code", work_kind == "repo_code"))
        confidence = str(classification.get("confidence", "low")).lower()
        delegate = work_kind == "repo_code" and requires_repo_code and confidence in {"medium", "high"}
        if requested_intent == "autonomous_request" and not delegate:
            classification.setdefault("classifier_work_kind", work_kind)
            classification["work_kind"] = "autonomous_request"
            classification["requires_repo_code"] = True
            classification["confidence"] = "high"
            classification["delegate"] = True
            classification["active_coding_model"] = active_model
            classification["reason"] = "active external coding model handles autonomous investigation"
            return classification
        classification["delegate"] = delegate
        classification.setdefault("active_coding_model", active_model)
        if not delegate:
            classification.setdefault("reason", "classifier did not select repo/code delegation")
        return classification

    def _plan_action_from_context(self, runtime_context: dict[str, Any], goal_state: dict[str, Any]) -> PlanAction:
        decision = str(runtime_context.get("decision", "")).strip().lower()
        decision_map = {
            "continue": PlanAction.continue_flow,
            "retry": PlanAction.retry,
            "clarify": PlanAction.clarify,
            "stop": PlanAction.stop,
            "delegate": PlanAction.delegate,
            "switch_tool": PlanAction.switch_tool,
            "synthesize": PlanAction.synthesize,
            "execute": PlanAction.execute,
        }
        if decision == "clarify" and self._has_operational_context_for_tool(runtime_context, goal_state):
            return PlanAction.execute
        if decision in decision_map:
            return decision_map[decision]
        if str(goal_state.get("next_expected_action", "")).strip().lower() == "ask_user":
            return PlanAction.clarify
        return PlanAction.execute

    @staticmethod
    def _has_operational_context_for_tool(runtime_context: dict[str, Any], goal_state: dict[str, Any]) -> bool:
        if str(goal_state.get("current_tool", "")).strip() or str(goal_state.get("last_observation", "")).strip():
            return True
        candidates = goal_state.get("current_candidates", [])
        if isinstance(candidates, list) and candidates:
            return True
        recent_history = runtime_context.get("recent_history", [])
        if isinstance(recent_history, list):
            for item in reversed(recent_history[-5:]):
                if isinstance(item, dict) and str(item.get("root_path", "") or item.get("path", "")).strip():
                    return True
        return False

    def _is_complex_request(self, request: str, available_tools: set[str]) -> bool:
        """Determine if a request requires multi-step planning.
        
        Complex requests are those that:
        - Explicitly ask for planning ("plan", "steps", "break down", "workflow")
        - Involve multiple operations (keywords like "and", "then", "also", "first")
        - Are open-ended or exploratory
        """
        request_lower = request.lower()
        
        # Explicit planning keywords
        planning_keywords = {"plan", "steps", "workflow", "break down", "how to", "strategy", "approach"}
        if any(keyword in request_lower for keyword in planning_keywords):
            return True
        
        # Multi-operation indicators
        multi_op_keywords = {" and ", " then ", " also ", " first ", " next ", "multiple", "several"}
        multi_op_count = sum(1 for keyword in multi_op_keywords if keyword in request_lower)
        if multi_op_count >= 1:
            return True
        
        # Short, specific requests are simple
        if len(request.split()) <= 5:
            return False
        
        # Default to direct for now (Phase 2 establishes framework)
        return False

    def _generate_steps(self, request: str, available_tools: set[str], context: dict[str, Any]) -> MultiStepPlan:
        """Generate multi-step execution plan from complex request.
        
        Returns a MultiStepPlan with PlanningSteps for each operation.
        Phase 2 implementation: basic step generation.
        Phase 5+ will enhance with observation loop and plan refinement.
        """
        goal = request
        steps = []
        
        # Simple heuristic: break request into operations
        # Example: "create project named X and scaffold it" → 2 steps
        request_lower = request.lower()
        
        # Step 1: Analysis/Planning
        steps.append(PlanningStep(
            thought="Analyze user request and available tools",
            action="Assess what tools are available for this task",
            tool="assistant.respond",  # Plan inspection tool
            worker="planner"
        ))
        
        # Step 2: Primary action (inferred from keywords)
        if any(keyword in request_lower for keyword in ["create", "make", "build", "generate", "scaffold"]):
            primary_tool = self._select_tool("create", available_tools) if "create" in available_tools else self._select_tool(None, available_tools)
            steps.append(PlanningStep(
                thought="Execute primary creation/build action",
                action="Create/build what user requested",
                tool=primary_tool,
                worker="tool_executor"
            ))
        
        # Step 3: Validation/Observation
        steps.append(PlanningStep(
            thought="Verify result matches user intent",
            action="Inspect outcome and gather observations",
            tool="assistant.respond",  # Observation tool
            worker="observer"
        ))
        
        return MultiStepPlan(goal=goal, steps=steps)

    def _select_tool(self, requested_tool: str | None, available_tools: set[str]) -> str:
        """Select the best tool for a direct Phase 1 execution plan."""
        if requested_tool:
            if not available_tools or requested_tool in available_tools:
                return requested_tool

        default_tool = self._select_default_tool()
        if not available_tools or default_tool in available_tools:
            return default_tool

        if "assistant.respond" in available_tools:
            return "assistant.respond"

        return sorted(available_tools)[0]

    def _select_default_tool(self) -> str:
        """Select a default tool without adding intent-style routing logic."""
        if self.capability_registry and hasattr(self.capability_registry, "default_tool"):
            default_tool = self.capability_registry.default_tool()
            if default_tool:
                return str(default_tool)

        return "assistant.respond"

    def _normalize_available_tools(self, value: Any) -> set[str]:
        if value is None:
            return set()
        if isinstance(value, str):
            return {value}
        try:
            return {str(item) for item in value if str(item).strip()}
        except TypeError:
            return set()

    def _first_present(self, *values: Any) -> str | None:
        for value in values:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return None

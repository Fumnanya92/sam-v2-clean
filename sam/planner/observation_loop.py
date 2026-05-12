"""Phase 5: Observation loop for adaptive plan execution.

Implements plan-act-observe-continue cycle:
1. Plan: Generate execution steps from user goal
2. Act: Execute current step via tool executor
3. Observe: Gather result, extract observations
4. Continue: Planner decides next action based on result

The planner adapts: retry on failure, skip on success, ask user if stuck.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from diagnostics.result import SamResult
from sam.planner.task_planner import TaskPlan, TaskPlanner, PlanningStep, PlanningStepStatus


class ContinueDecision(str, Enum):
    """Planner's decision after observing a step result."""
    continue_next = "continue_next"      # Success, move to next step
    retry_current = "retry_current"      # Failure, retry current step
    skip_remaining = "skip_remaining"    # Skip rest, enough progress
    ask_user = "ask_user"                # Stuck, need user guidance
    stop_success = "stop_success"        # Plan complete, success
    stop_failure = "stop_failure"        # Plan failed, cannot continue


@dataclass
class ObservationData:
    """Extracted observations from a tool execution result."""
    
    success: bool
    summary: str
    error: str | None = None
    details: dict[str, Any] | None = None
    raw_result: SamResult | None = None


@dataclass
class StepExecution:
    """Result of executing a single planning step."""
    
    step: PlanningStep
    result: SamResult
    observations: ObservationData
    decision: ContinueDecision


class ObservationLoop:
    """Manages the plan-act-observe-continue cycle for adaptive execution.
    
    Given a multi-step plan, executes steps sequentially, gathering observations
    and making adaptive decisions about continuation.
    """
    
    def __init__(self, planner: TaskPlanner, executor: Any) -> None:
        """Initialize observation loop with planner and executor.
        
        Args:
            planner: TaskPlanner instance for generating/adapting plans
            executor: Tool executor for performing actions
        """
        self.planner = planner
        self.executor = executor
        self.max_retries = 2
    
    def execute_plan(
        self,
        plan: TaskPlan,
        memory_block: dict[str, Any] | None = None,
    ) -> tuple[SamResult, list[StepExecution]]:
        """Execute a multi-step plan with observation loop.
        
        Args:
            plan: TaskPlan to execute (may be direct or multi_step mode)
            memory_block: Optional memory/context data
        
        Returns:
            Tuple of (final result, list of step executions)
        """
        executions: list[StepExecution] = []
        
        # Direct mode: single step
        if plan.mode == "direct" or plan.multi_step_plan is None:
            result = self._execute_single_step(plan, memory_block)
            final_summary = result.summary if hasattr(result, 'summary') else str(result)
            metadata = dict(result.metadata) if isinstance(result, SamResult) else {}
            metadata["execution_mode"] = "direct"
            return (
                SamResult(
                    status="success" if result.ok else "failed",
                    summary=final_summary,
                    error_type=result.error_type if isinstance(result, SamResult) else None,
                    error_message=result.error_message if isinstance(result, SamResult) else None,
                    next_action=result.next_action if isinstance(result, SamResult) else "stop",
                    metadata=metadata,
                ),
                [],
            )
        
        # Multi-step mode: execute with observation loop
        return self._execute_multi_step(plan, memory_block)
    
    def _execute_single_step(self, plan: TaskPlan, memory_block: dict[str, Any] | None = None) -> SamResult:
        """Execute a single-step plan."""
        try:
            result = self.executor.execute_with_tracking(plan.tool_name, plan.payload)
            return result if isinstance(result, SamResult) else SamResult(
                status="success",
                summary=str(result),
                next_action="stop",
            )
        except Exception as e:
            return SamResult(
                status="failed",
                summary=f"Execution failed: {str(e)}",
                error_type="EXECUTION_ERROR",
                error_message=str(e),
                next_action="stop",
            )
    
    def _execute_multi_step(
        self,
        plan: TaskPlan,
        memory_block: dict[str, Any] | None = None,
    ) -> tuple[SamResult, list[StepExecution]]:
        """Execute multi-step plan with observation loop."""
        executions: list[StepExecution] = []
        retry_counts: dict[int, int] = {}
        
        step_idx = 0
        while step_idx < len(plan.multi_step_plan.steps):
            step = plan.multi_step_plan.steps[step_idx]
            retry_count = retry_counts.get(step_idx, 0)
            
            # Mark step as running
            step.status = PlanningStepStatus.running
            
            # Execute step
            result = self._execute_single_step(
                TaskPlan(
                    goal=plan.goal,
                    tool_name=step.tool,
                    payload={"request": step.action, "context": memory_block or {}},
                    mode="direct",
                ),
                memory_block,
            )
            
            # Extract observations
            step.observation = result.summary if hasattr(result, 'summary') else str(result)
            observations = self._extract_observations(result)
            
            # Make continuation decision
            decision = self._decide_continuation(observations, retry_count)
            
            # Record execution
            execution = StepExecution(step, result, observations, decision)
            executions.append(execution)
            
            # Update step status
            if decision in (ContinueDecision.stop_success, ContinueDecision.skip_remaining):
                step.status = PlanningStepStatus.completed
            elif decision == ContinueDecision.stop_failure:
                step.status = PlanningStepStatus.failed
            else:
                step.status = PlanningStepStatus.completed
            
            # Act on decision
            if decision == ContinueDecision.continue_next:
                step_idx += 1
            elif decision == ContinueDecision.retry_current:
                retry_counts[step_idx] = retry_count + 1
                # Don't increment step_idx, will retry same step
            elif decision == ContinueDecision.skip_remaining:
                break
            elif decision == ContinueDecision.ask_user:
                break
            elif decision in (ContinueDecision.stop_success, ContinueDecision.stop_failure):
                break
            else:
                step_idx += 1
        
        # Generate final result
        final_status = "success" if all(
            e.step.status == PlanningStepStatus.completed for e in executions
        ) else "partial"
        
        return (
            SamResult(
                status=final_status,
                summary=f"Executed {len(executions)} steps, completed {sum(1 for e in executions if e.step.status == PlanningStepStatus.completed)}",
                next_action="stop",
                metadata={
                    "execution_mode": "multi_step",
                    "steps_executed": len(executions),
                    "steps_completed": sum(1 for e in executions if e.step.status == PlanningStepStatus.completed),
                },
            ),
            executions,
        )
    
    def _extract_observations(self, result: SamResult) -> ObservationData:
        """Extract actionable observations from an execution result."""
        return ObservationData(
            success=result.ok,
            summary=result.summary if hasattr(result, 'summary') else str(result),
            error=result.error_message if hasattr(result, 'error_message') else None,
            details=result.metadata if hasattr(result, 'metadata') else {},
            raw_result=result,
        )
    
    def _decide_continuation(self, observations: ObservationData, retry_count: int) -> ContinueDecision:
        """Decide what to do next based on observations.
        
        Decision tree:
        - Success: continue to next step
        - Failure + retries available: retry current step
        - Failure + no retries: ask user
        - Error contains "not found": skip remaining (can't complete)
        """
        if observations.success:
            return ContinueDecision.continue_next
        
        if retry_count < self.max_retries:
            return ContinueDecision.retry_current
        
        # Max retries reached - check if we should ask user or skip
        error_lower = (observations.error or "").lower()
        if any(phrase in error_lower for phrase in ["not found", "missing", "unavailable"]):
            return ContinueDecision.skip_remaining
        
        return ContinueDecision.ask_user


def create_observation_loop(planner: TaskPlanner, executor: Any) -> ObservationLoop:
    """Factory for creating an observation loop.
    
    Args:
        planner: TaskPlanner instance
        executor: Tool executor (WorkerCentricExecutor recommended)
    
    Returns:
        ObservationLoop configured with planner and executor
    """
    return ObservationLoop(planner, executor)

"""Primary runtime execution engine (planner -> tool -> observe).

NOTE: All execution through this engine only happens after action_gate
has approved the request (should_act=true). Therefore, keyword-based
helper functions like _requires_project_context() and _is_mutating_request()
are safe to use for planning purposes - they cannot accidentally trigger
execution from conversational input.

The action_gate (core/action_gate.py) is the single authority that
determines whether external action is requested before this engine runs.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Callable

from diagnostics.result import SamResult
from diagnostics.trace import append_trace, trace_step
from memory.long_term import (
    MemoryCandidate,
    create_operational_task,
    save_memory_candidate,
    update_operational_task,
    upsert_project_state,
)

from .request_model import IntentRequest


class RuntimeExecutionEngine:
    """Executes requests via planner/observation loop with legacy fallback."""

    def __init__(
        self,
        *,
        tool_executor: Any,
        task_planner: Any,
        observation_loop: Any,
        db_path: str | None = None,
    ) -> None:
        self.tool_executor = tool_executor
        self.task_planner = task_planner
        self.observation_loop = observation_loop
        self.db_path = db_path

    def execute(
        self,
        request: IntentRequest,
        *,
        memory_block: dict[str, Any] | None,
        legacy_execute: Callable[[IntentRequest], SamResult],
    ) -> SamResult:
        if request.intent in {"chat", "clarify"}:
            return legacy_execute(request)

        tool_def = self.tool_executor.get(request.intent)
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

        if self._should_use_pipeline(request):
            return self._execute_pipeline(
                request,
                memory_block=memory_block,
                legacy_execute=legacy_execute,
                runtime_context=request.parameters.get("_runtime", {}) if isinstance(request.parameters, dict) else {},
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

        plan = self.task_planner.plan(
            str(goal_state.get("current_objective", "")).strip() or request.raw_text or request.intent,
            context={
                "request": request,
                "memory": memory_block,
                "intent": request.intent,
                "available_tools": self.tool_executor.available_tools,
                "goal_state": goal_state,
                "runtime_context": runtime_context,
                "recent_history": recent_history,
                "observations": observations,
            },
        )
        result, step_executions = self.observation_loop.execute_plan(plan, memory_block)
        result.metadata.setdefault("execution_path", "runtime_engine")
        autonomous_steps = int(result.metadata.get("autonomous_steps", 0) or 0)
        visible_steps = len(step_executions) + autonomous_steps
        result.metadata.setdefault("execution_steps", visible_steps)
        result.metadata.setdefault("plan_mode", plan.mode)
        result.metadata.setdefault("plan_action", plan.plan_action.value)
        result.metadata.setdefault("request_intent", request.intent)
        result.metadata.setdefault("intent", request.intent)
        return append_trace(
            result,
            trace_step("Execution path", action="runtime_engine", tool=plan.tool_name),
            trace_step("Observation loop", observation=f"{visible_steps} step(s)"),
        )

    def _execute_pipeline(
        self,
        request: IntentRequest,
        *,
        memory_block: dict[str, Any] | None,
        legacy_execute: Callable[[IntentRequest], SamResult],
        runtime_context: dict[str, Any],
    ) -> SamResult:
        planner_output = self._planner_stage(request, memory_block, runtime_context)
        task_id = str(planner_output["task_id"])
        if self.db_path:
            create_operational_task(
                self.db_path,
                task_id=task_id,
                project_id=str(planner_output.get("project_id", "")),
                goal=str(planner_output.get("goal", "")),
                planner_output=planner_output,
                linked_memories=_compact_context_memory_ids(memory_block),
            )
            save_memory_candidate(
                self.db_path,
                MemoryCandidate(
                    content=f"Planned task {task_id}: {planner_output.get('goal', '')}",
                    memory_type="episodic",
                    importance_score=7,
                    confidence_score=0.78,
                    source_conversation_id=task_id,
                    related_project_id=str(planner_output.get("project_id", "")),
                    tags=["plan", "operational_task"],
                ),
            )

        missing = planner_output.get("missing_information", [])
        if isinstance(missing, list) and missing:
            if self.db_path:
                update_operational_task(
                    self.db_path,
                    task_id=task_id,
                    state="blocked",
                    blocked_reason="; ".join(str(item) for item in missing),
                )
            return SamResult(
                status="success",
                summary=f"I need one detail before executing: {missing[0]}",
                next_action="ask_user",
                metadata={
                    "intent": request.intent,
                    "execution_pipeline": "planner_only",
                    "pipeline_stage": "planner",
                    "task_id": task_id,
                    "planner_output": planner_output,
                    "blocked_reasons": missing,
                },
            )

        executor_result, step_executions, executor_output = self._executor_stage(
            request,
            planner_output=planner_output,
            memory_block=memory_block,
            legacy_execute=legacy_execute,
        )
        reviewer_output = self._reviewer_stage(
            request=request,
            planner_output=planner_output,
            executor_result=executor_result,
            executor_output=executor_output,
        )

        if self.db_path:
            update_operational_task(
                self.db_path,
                task_id=task_id,
                state="executing",
                execution_event=executor_output,
            )
            update_operational_task(
                self.db_path,
                task_id=task_id,
                state="completed" if reviewer_output["ready_for_user"] else "review_failed",
                review_event=reviewer_output,
                completion_score=float(reviewer_output["completion_score"]),
                blocked_reason="; ".join(reviewer_output["missing_items"]) if reviewer_output["missing_items"] else "",
            )

        if not reviewer_output["ready_for_user"] and reviewer_output.get("can_continue"):
            continuation_result, continuation_steps, continuation_output = self._executor_stage(
                request,
                planner_output={**planner_output, "execution_plan": reviewer_output["missing_items"]},
                memory_block=memory_block,
                legacy_execute=legacy_execute,
                continuation=True,
            )
            final_review = self._reviewer_stage(
                request=request,
                planner_output=planner_output,
                executor_result=continuation_result,
                executor_output=_merge_executor_outputs(executor_output, continuation_output),
            )
            if self.db_path:
                update_operational_task(
                    self.db_path,
                    task_id=task_id,
                    state="completed" if final_review["ready_for_user"] else "review_failed",
                    execution_event=continuation_output,
                    review_event=final_review,
                    completion_score=float(final_review["completion_score"]),
                    auto_correction={"trigger": "reviewer_continuation", "missing_items": reviewer_output["missing_items"]},
                )
            executor_result = continuation_result
            step_executions = [*step_executions, *continuation_steps]
            executor_output = _merge_executor_outputs(executor_output, continuation_output)
            reviewer_output = final_review

        final_status = "success" if executor_result.ok else "partial"
        final_next_action = "stop" if reviewer_output["ready_for_user"] else "ask_user"
        metadata = dict(executor_result.metadata)
        visible_steps = len(step_executions) + int(metadata.get("autonomous_steps", 0) or 0)
        metadata.update(
            {
                "intent": metadata.get("intent", request.intent),
                "request_intent": request.intent,
                "execution_path": "runtime_engine",
                "execution_pipeline": "planner_executor_reviewer",
                "task_id": task_id,
                "planner_output": planner_output,
                "executor_output": executor_output,
                "reviewer_output": reviewer_output,
                "execution_steps": visible_steps,
            }
        )
        if self.db_path and reviewer_output["ready_for_user"]:
            project_id = str(planner_output.get("project_id", ""))
            if project_id:
                upsert_project_state(
                    self.db_path,
                    project_id=project_id,
                    title=project_id,
                    status="active",
                    current_focus=str(planner_output.get("goal", "")),
                    next_steps=[str(item) for item in executor_output.get("pending_work", [])],
                    related_memories=_compact_context_memory_ids(memory_block),
                    implementation_status="completed",
                    blockers=[],
                    last_discussed_topic=str(planner_output.get("goal", "")),
                )
        summary = executor_result.summary
        if not reviewer_output["ready_for_user"] and executor_result.next_action != "ask_user":
            summary = f"Execution is incomplete. {reviewer_output['review_summary']}"
        return append_trace(
            SamResult(
                status=final_status,
                summary=summary,
                error_type=executor_result.error_type,
                error_message=executor_result.error_message,
                next_action=final_next_action,
                metadata=metadata,
            ),
            trace_step("Pipeline planner", action="planner", observation=f"{len(planner_output['execution_plan'])} checklist item(s)"),
            trace_step("Pipeline executor", action="executor", observation=f"{len(executor_output['completed_steps'])} completed"),
            trace_step("Pipeline reviewer", action="reviewer", observation=reviewer_output["review_summary"]),
            trace_step("Observation loop", observation=f"{visible_steps} step(s)"),
        )

    def _planner_stage(
        self,
        request: IntentRequest,
        memory_block: dict[str, Any] | None,
        runtime_context: dict[str, Any],
    ) -> dict[str, Any]:
        text = request.raw_text or str(request.parameters.get("goal", "") if isinstance(request.parameters, dict) else "") or request.intent
        project_id = _project_id_from_request(request, memory_block)
        missing = []
        if request.intent in {"delegate_coding_task"} and not project_id and _requires_project_context(text, request.intent):
            missing.append("Which project or repository should I use?")
        if request.intent == "autonomous_request" and not project_id and _is_mutating_request(text, request.intent) and _requires_project_context(text, request.intent):
            missing.append("Which project or repository should I use?")
        checklist = _implementation_checklist(text, request.intent)
        return {
            "task_id": _task_id(text),
            "project_id": project_id,
            "goal": text,
            "assumptions": _planner_assumptions(request, project_id),
            "execution_plan": checklist,
            "file_targets": _file_targets(text),
            "risks": _planner_risks(text, request.intent),
            "required_tests": _required_tests(text, request.intent),
            "success_criteria": [f"Completed: {item}" for item in checklist],
            "missing_information": missing,
            "created_at": datetime.now(UTC).isoformat(),
            "future_stages": ["tester", "debugger", "browser_agent", "desktop_agent"],
        }

    def _executor_stage(
        self,
        request: IntentRequest,
        *,
        planner_output: dict[str, Any],
        memory_block: dict[str, Any] | None,
        legacy_execute: Callable[[IntentRequest], SamResult],
        continuation: bool = False,
    ) -> tuple[SamResult, list[Any], dict[str, Any]]:
        runtime_context = request.parameters.get("_runtime", {}) if isinstance(request.parameters, dict) else {}
        goal_state = runtime_context.get("goal_state", {}) if isinstance(runtime_context, dict) else {}
        recent_history = runtime_context.get("recent_history", []) if isinstance(runtime_context, dict) else []
        plan = self.task_planner.plan(
            str(planner_output.get("goal", "")),
            context={
                "request": request,
                "memory": memory_block,
                "intent": request.intent,
                "available_tools": self.tool_executor.available_tools,
                "goal_state": goal_state,
                "runtime_context": runtime_context,
                "recent_history": recent_history,
                "observations": [],
                "pipeline_plan": planner_output,
            },
        )
        result, step_executions = self.observation_loop.execute_plan(plan, memory_block)
        result.metadata.setdefault("execution_path", "runtime_engine")
        result.metadata.setdefault("intent", request.intent)
        completed = list(planner_output.get("execution_plan", [])) if result.ok else []
        failed = [] if result.ok else list(planner_output.get("execution_plan", []))
        changed_files = _changed_files(result)
        output = {
            "status": result.status,
            "summary": result.summary,
            "completed_steps": completed,
            "failed_steps": failed,
            "changed_files": changed_files,
            "generated_artifacts": _generated_artifacts(result),
            "pending_work": [] if result.ok else failed,
            "continuation": continuation,
        }
        return result, step_executions, output

    def _reviewer_stage(
        self,
        *,
        request: IntentRequest,
        planner_output: dict[str, Any],
        executor_result: SamResult,
        executor_output: dict[str, Any],
    ) -> dict[str, Any]:
        checklist = [str(item) for item in planner_output.get("execution_plan", []) if str(item).strip()]
        completed = set(str(item) for item in executor_output.get("completed_steps", []))
        missing_items = [item for item in checklist if item not in completed]
        warnings = []
        if planner_output.get("file_targets") and not executor_output.get("changed_files") and _expects_file_changes(request):
            warnings.append("Expected file changes were not reported.")
        if planner_output.get("required_tests") and not _has_test_signal(executor_result):
            warnings.append("Required tests were not reported.")
        if not executor_result.ok:
            warnings.append(executor_result.error_message or executor_result.summary)
        if executor_result.next_action == "ask_user":
            warnings.append("Executor needs user input before completion.")
        completion_score = 1.0 if not checklist else len(completed) / len(checklist)
        if warnings:
            completion_score = min(completion_score, 0.75)
        ready = executor_result.ok and not missing_items and not warnings
        return {
            "completion_score": round(completion_score, 2),
            "missing_items": missing_items,
            "warnings": warnings,
            "review_summary": "Implementation matches the plan." if ready else "Reviewer found incomplete work.",
            "ready_for_user": ready,
            "can_continue": bool(missing_items and executor_result.ok),
        }

    @staticmethod
    def _should_use_pipeline(request: IntentRequest) -> bool:
        # Conversational and info requests never use pipeline
        if request.intent in {"chat", "clarify", "capabilities", "list_tasks", "list_projects", "show_coding_model", "set_coding_model"}:
            return False
        
        # Operational intents always use pipeline (action_gate has already approved)
        operational_intents = {
            "autonomous_request",
            "delegate_coding_task",
            "scaffold_project",
            "run_project",
            "inspect_project_repo",
            "inspect_git_state",
            "read_file",
            "open_file",
            "list_directory",
            "open_folder",
            "open_project_folder",
            "check_python_syntax",
            "inspect_recent_changes",
            "scan_codebase_patterns",
        }
        if request.intent in operational_intents:
            return True
        
        # No keyword-based pipeline triggering; action_gate is the authority
        return False


def _task_id(text: str) -> str:
    import hashlib

    digest = hashlib.sha1(f"{datetime.now(UTC).isoformat()}:{text}".encode("utf-8")).hexdigest()[:10]
    return f"task_{digest}"


def _project_id_from_request(request: IntentRequest, memory_block: dict[str, Any] | None) -> str:
    params = request.parameters if isinstance(request.parameters, dict) else {}
    for key in ("project_id", "query", "path", "root_path"):
        value = str(params.get(key, "")).strip()
        if value:
            return value
    path_match = re.search(r"[A-Za-z]:[\\/][^\r\n\"']+", request.raw_text or "")
    if path_match:
        return path_match.group(0).strip().rstrip(".,;")
    if isinstance(memory_block, dict):
        compact = memory_block.get("compact_context", {})
        if isinstance(compact, dict) and "value" in compact:
            compact = compact.get("value", {})
        if isinstance(compact, dict):
            project = compact.get("active_project", {})
            if isinstance(project, dict):
                return str(project.get("project_id", "") or project.get("root_path", "")).strip()
    return ""


def _requires_project_context(text: str, intent: str | None = None) -> bool:
    """Decide if the request needs a project context.

    Prefer explicit intent signals over token scanning. If an intent is provided
    and represents an operational request, return True. Otherwise fall back to
    a lightweight token check.
    """
    operational_like_intents = {"delegate_coding_task", "autonomous_request", "scaffold_project", "inspect_project_repo", "inspect_repo"}
    if intent and intent in operational_like_intents:
        return True
    lowered = text.lower()
    return any(token in lowered for token in ("project", "repo", "codebase", "app"))


def _is_mutating_request(text: str, intent: str | None = None) -> bool:
    """Detect if a request is mutating (code change) based primarily on intent.

    Uses intent when available; falls back to token scanning of the text.
    """
    mutating_intents = {"delegate_coding_task", "scaffold_project", "autonomous_request", "create_task", "update_task"}
    if intent and intent in mutating_intents:
        return True
    lowered = text.lower()
    return any(token in lowered for token in ("implement", "fix", "edit", "modify", "write", "build", "create", "delete", "scaffold"))


def _implementation_checklist(text: str, intent: str) -> list[str]:
    checklist = ["Understand request and confirm execution target"]
    lowered = text.lower()
    if any(token in lowered for token in ("implement", "fix", "edit", "build", "scaffold", "create")) or intent in {"scaffold_project", "delegate_coding_task"}:
        checklist.append("Apply implementation changes")
    elif intent in {"read_file", "scan_codebase_patterns", "inspect_project_repo", "autonomous_request"}:
        checklist.append("Inspect requested evidence")
    if any(token in lowered for token in ("test", "verify", "debug", "fix")):
        checklist.append("Run or report relevant verification")
    checklist.append("Review result against request")
    return checklist


def _file_targets(text: str) -> list[str]:
    return re.findall(r"[\w./\\-]+\.(?:py|js|ts|tsx|dart|json|md|yaml|yml|html|css)", text)


def _planner_assumptions(request: IntentRequest, project_id: str) -> list[str]:
    assumptions = [f"Parsed intent is {request.intent}."]
    if project_id:
        assumptions.append(f"Project context is {project_id}.")
    return assumptions


def _planner_risks(text: str, intent: str) -> list[str]:
    risks = []
    if "delete" in text.lower():
        risks.append("Destructive file operation requires care.")
    if intent in {"delegate_coding_task", "autonomous_request"}:
        risks.append("Project context may be incomplete if the target is ambiguous.")
    return risks


def _required_tests(text: str, intent: str) -> list[str]:
    lowered = text.lower()
    if "test" in lowered:
        return ["Run requested tests"]
    if any(token in lowered for token in ("fix", "debug", "implement")):
        return ["Run focused verification when available"]
    return []


def _changed_files(result: SamResult) -> list[str]:
    raw = result.metadata.get("changed_files", [])
    if isinstance(raw, list):
        return [str(item) for item in raw]
    path = result.metadata.get("path") or result.metadata.get("root_path")
    return [str(path)] if path else []


def _generated_artifacts(result: SamResult) -> list[str]:
    raw = result.metadata.get("generated_artifacts", result.metadata.get("plan_files", []))
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return []


def _expects_file_changes(request: IntentRequest) -> bool:
    return any(token in (request.raw_text or "").lower() for token in ("implement", "fix", "edit", "build", "scaffold", "create"))


def _has_test_signal(result: SamResult) -> bool:
    if result.metadata.get("test_status"):
        return True
    text = f"{result.summary} {result.metadata}".lower()
    return "test" in text or "verified" in text or "verification" in text


def _compact_context_memory_ids(memory_block: dict[str, Any] | None) -> list[int]:
    if not isinstance(memory_block, dict):
        return []
    compact = memory_block.get("compact_context", {})
    if isinstance(compact, dict) and "value" in compact:
        compact = compact.get("value", {})
    if not isinstance(compact, dict):
        return []
    return [int(item) for item in compact.get("injected_memory_ids", []) if str(item).isdigit()]


def _merge_executor_outputs(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    merged = dict(first)
    for key in ("completed_steps", "failed_steps", "changed_files", "generated_artifacts", "pending_work"):
        merged[key] = [*merged.get(key, []), *second.get(key, [])]
    merged["summary"] = " ".join(str(item) for item in (first.get("summary", ""), second.get("summary", "")) if item)
    merged["status"] = second.get("status", first.get("status", ""))
    return merged

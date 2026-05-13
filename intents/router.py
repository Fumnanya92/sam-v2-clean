"""Minimal intent parser and router for Sam v2."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

from approvals import ApprovalManager, AuthorityEngine
from capabilities import CapabilityAwarenessService, CapabilityRegistry, build_default_registry
# Phase 1 planner and executor imports (surgical addition)
from sam.planner.task_planner import TaskPlanner
from sam.executor.tool_executor import ToolExecutor
from intents._executor_tools_registry import register_all_executor_tools
from intents.conversation_state import ConversationStateEngine
from intents.contextual_resolver import ContextualRequestResolver
from intents.legacy_adapter import LegacyIntentAdapter
from sam.executor.service_tools import register_service_tools
# Phase 4 worker-centric runtime import (surgical addition)
from sam.executor.worker_runtime import create_worker_centric_executor
# Phase 5 observation loop import (surgical addition)
from sam.planner.observation_loop import create_observation_loop
from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult
from llm import OllamaClient, OllamaIntentOutput
from projects import (
    ProjectExecutionRequest,
    ProjectInspector,
    ProjectPlanRequest,
    ProjectPlanner,
    ProjectRegistry,
    ProjectScaffoldRequest,
    ProjectScaffolder,
    inspection_metadata,
)
from storage import TaskRecord, create_task, list_tasks, update_task
from tools import CodebaseCleanupService, SafeLocalTools, WorkspaceCleanupService
from upgrades import UpgradeProposalManager
from workers import CommandSpec, ToolingWorker, worker_monitor
from workers.names import resolve_worker_identity
from workflows import DiscoveryWorkflow, GoalService, PipelineService


@dataclass
class IntentRequest:
    intent: str
    parameters: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    needs_clarification: bool = False
    clarification_question: str = ""
    response_text: str = ""
    confidence: str = "low"
    source: str = "llm"


class IntentRouter:
    def __init__(
        self,
        *,
        db_path: str | Path,
        workspace_root: str | Path | None = None,
        registry: CapabilityRegistry | None = None,
        authority_engine: AuthorityEngine | None = None,
        approval_manager: ApprovalManager | None = None,
        model_client: OllamaClient | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.registry = registry or build_default_registry()
        self.authority_engine = authority_engine
        self.approval_manager = approval_manager
        self.goal_service = GoalService(self.db_path)
        self.pipeline_service = PipelineService(self.db_path)
        self.model_client = model_client or OllamaClient()
        self.workspace_root = Path(workspace_root) if workspace_root is not None else Path.cwd() / "sam_v2" / "workspace"
        self.project_registry = ProjectRegistry(self.db_path.with_name("projects.json"))
        self.upgrade_manager = UpgradeProposalManager(self.db_path.with_name("upgrades.json"))
        self.project_inspector = ProjectInspector(
            registry=self.project_registry,
            tools=SafeLocalTools(db_path=self.db_path),
        )
        self.discovery_workflow = DiscoveryWorkflow(self.project_inspector.tools, search_roots=[self.workspace_root])
        self.conversation_state = ConversationStateEngine()
        self.contextual_resolver = ContextualRequestResolver()
        self.workspace_cleanup = WorkspaceCleanupService(self.workspace_root, db_path=self.db_path)
        self.tooling_worker = ToolingWorker(
            db_path=self.db_path,
            authority_engine=self.authority_engine,
            approval_manager=self.approval_manager,
        )
        self.project_scaffolder = ProjectScaffolder(
            workspace_root=self.workspace_root / "projects",
            project_registry=self.project_registry,
            tooling_worker=self.tooling_worker,
        )
        self.project_planner = ProjectPlanner(
            project_registry=self.project_registry,
            tooling_worker=self.tooling_worker,
            project_inspector=self.project_inspector,
        )
        self.awareness = CapabilityAwarenessService(
            self.registry,
            project_registry=self.project_registry,
            upgrade_manager=self.upgrade_manager,
        )
        self.legacy_adapter = LegacyIntentAdapter(self)
        # Initialise Phase 1 planner and executor
        self.task_planner = TaskPlanner(self.registry)
        # Use base executor for registration, then wrap for worker tracking
        self.tool_executor = ToolExecutor()
        # Register the safe executor tools for the initial intents
        self._register_executor_tools()
        # Phase 4: Wrap executor with worker-centric tracking
        self._base_executor = self.tool_executor
        self.tool_executor = create_worker_centric_executor(self._base_executor)
        # Phase 5: Create observation loop for adaptive plan execution
        self.observation_loop = create_observation_loop(self.task_planner, self.tool_executor)

    def parse(self, user_text: str, memory_block: dict[str, Any] | None = None) -> IntentRequest:
        """Understand a user request with the model first.

        This must not become a phrase-matching command bot.
        Rules are only allowed for empty input, approval/stop safety,
        and exact fallback commands when the LLM is unavailable.
        """
        text = user_text.strip()
        if not text:
            return IntentRequest(
                intent="chat",
                raw_text=user_text,
                needs_clarification=True,
                clarification_question="What would you like me to do?",
                source="safety_rule",
                confidence="high",
            )

        safety_request = self._parse_with_safety_rules(text)
        if safety_request is not None:
            return safety_request

        llm_request = self._parse_with_llm(text, memory_block)
        if llm_request is not None:
            contextual = self._apply_contextual_request(text, llm_request, memory_block)
            enriched, _state = self.conversation_state.apply(text, contextual, memory_block)
            return enriched

        return self._parse_with_rules(text)

    def _parse_with_safety_rules(self, text: str) -> IntentRequest | None:
        """Only deterministic safety/approval rules live here.

        Do not add business/task/project phrases here.
        Natural language should go through the LLM.
        """
        lowered = text.lower().strip()

        if lowered in {"yes", "confirm", "approved", "approve", "go ahead", "continue"}:
            return IntentRequest(
                intent="chat",
                raw_text=text,
                needs_clarification=True,
                clarification_question=(
                    "What exactly are you approving? Mention the action, like push, delete, cleanup, or send."
                ),
                source="safety_rule",
                confidence="high",
            )

        if lowered in {"cancel", "stop", "abort"}:
            return IntentRequest(
                intent="chat",
                raw_text=text,
                response_text="Okay. I will stop that action unless you ask me to continue.",
                source="safety_rule",
                confidence="high",
            )

        return None

    def _parse_with_rules(self, text: str) -> IntentRequest:
        """Fallback only when the LLM is unavailable.

        This fallback must stay tiny and exact.
        It is not Sam's brain.
        """
        lowered = text.lower().strip()
        exact_fallbacks = {
            "what can you do": "capabilities",
            "list capabilities": "capabilities",
            "show capabilities": "capabilities",
            "list tasks": "list_tasks",
            "show tasks": "list_tasks",
            "list goals": "list_goals",
            "show goals": "list_goals",
            "list projects": "list_projects",
            "show projects": "list_projects",
            "show approvals": "list_approvals",
            "list approvals": "list_approvals",
        }

        if lowered in exact_fallbacks:
            return IntentRequest(
                intent=exact_fallbacks[lowered],
                raw_text=text,
                source="fallback_rule",
                confidence="medium",
            )

        return IntentRequest(
            intent="chat",
            raw_text=text,
            response_text=(
                "My local understanding model is unavailable, so I cannot safely decide how to act on that. "
                "Please start the model, then try again."
            ),
            source="llm_unavailable",
            confidence="low",
        )

    def _parse_followup_with_memory(
        self,
        text: str,
        memory_block: dict[str, Any] | None = None,
    ) -> IntentRequest | None:
        """Deprecated.

        Follow-up understanding must be handled by the LLM using memory context,
        not by project-specific phrase rules or hardcoded heuristics.
        """
        return None

    def handle(
        self,
        user_text: str,
        memory_block: dict[str, Any] | None = None,
        parsed_request: IntentRequest | None = None,
        prechecked_authority: bool = False,
    ) -> SamResult:
        request = parsed_request or self.parse(user_text, memory_block)
        request, state = self.conversation_state.apply(user_text.strip(), request, memory_block)
        if request.needs_clarification:
            pending_scaffold = request.parameters.get("pending_scaffold", {}) if isinstance(request.parameters, dict) else {}
            result = SamResult(
                status="success",
                summary=request.clarification_question or "I need a bit more detail before I act.",
                next_action="ask_user",
                metadata={
                    "intent": "clarify",
                    "source": request.source,
                    "confidence": request.confidence,
                    "pending_scaffold": pending_scaffold if isinstance(pending_scaffold, dict) else {},
                },
            )
            self.conversation_state.writeback(result, state)
            return result

        if not prechecked_authority:
            capability = self.registry.get(request.intent)
            if capability is None:
                result = SamResult(
                    status="failed",
                    summary="Intent is not registered.",
                    error_type=ErrorType.MISSING_CAPABILITY,
                    error_message=request.intent,
                    next_action="ask_user",
                )
                self.conversation_state.writeback(result, state)
                return result

            approval_result = self._check_authority(request, capability.action_category)
            if approval_result is not None:
                self.conversation_state.writeback(approval_result, state)
                return approval_result

        # Runtime-first routing: use planner/executor whenever a registered tool exists.
        if self._should_route_through_planner(request):
            planned_result = self._execute_with_planner(request, memory_block)
            planned_result.metadata.setdefault("source", request.source)
            planned_result.metadata.setdefault("confidence", request.confidence)
            self.conversation_state.writeback(planned_result, state)
            return planned_result

        # Legacy compatibility path lives in a dedicated adapter.
        return self.legacy_adapter.handle(
            user_text=user_text,
            request=request,
            state=state,
            memory_block=memory_block,
        )

    # ---------------------------------------------------------------------
    # =========================================================================
    # Phase 1: Executive Tool Registration
    # =========================================================================

    def handle_compatibility(
        self,
        user_text: str,
        memory_block: dict[str, Any] | None = None,
        parsed_request: IntentRequest | None = None,
        prechecked_authority: bool = False,
    ) -> SamResult:
        """Legacy compatibility executor path.

        RuntimeExecutionEngine should be the default execution path.
        """
        result = self.handle(
            user_text,
            memory_block=memory_block,
            parsed_request=parsed_request,
            prechecked_authority=prechecked_authority,
        )
        result.metadata["execution_path"] = "legacy_router_compat"
        return result

    def _register_executor_tools(self) -> None:
        """Register all tools/intents as executable handlers.

        Delegates to:
        1. Comprehensive intent tool registry (_executor_tools_registry)
        2. Service tools registry (service_tools) for utility operations
        
        All intent business logic and service handlers are extracted into
        reusable tool handlers.
        """
        register_all_executor_tools(self)
        register_service_tools(
            self.tool_executor,
            repo_root=str(self.workspace_root),
            db_path=str(self.db_path),
        )

    def _execute_with_planner(self, request: IntentRequest, memory_block: dict[str, Any] | None) -> SamResult:
        """Create a plan and execute with observation loop for adaptive execution.

        Phase 5 plan-act-observe-continue cycle:
        1. Plan: TaskPlanner generates direct or multi-step plan
        2. Act: ObservationLoop executes via WorkerCentricExecutor with monitoring
        3. Observe: Extracts observations and results
        4. Continue: Makes adaptive decisions (retry, skip, ask user, etc.)
        """
        # Get available tools for planning context
        available_tools = self.tool_executor.available_tools
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
        
        # Create planning context with intent, available tools, and memory
        plan = self.task_planner.plan(
            str(goal_state.get("current_objective", "")).strip() or request.raw_text or request.intent,
            context={
                "request": request,
                "memory": memory_block,
                "intent": request.intent,
                "available_tools": available_tools,
                "goal_state": goal_state,
                "runtime_context": runtime_context,
                "recent_history": recent_history,
                "observations": observations,
            }
        )
        
        # Use observation loop for adaptive execution (handles direct and multi-step modes)
        result, step_executions = self.observation_loop.execute_plan(plan, memory_block)
        
        # Attach execution metadata
        result.metadata.setdefault("execution_steps", len(step_executions))
        result.metadata.setdefault("plan_mode", plan.mode)
        result.metadata.setdefault("request_intent", request.intent)
        result.metadata.setdefault("intent", request.intent)
        
        return result

    def _should_route_through_planner(self, request: IntentRequest) -> bool:
        excluded = {"chat", "clarify"}
        if request.intent in excluded:
            return False
        if request.needs_clarification:
            return False
        return self.tool_executor.get(request.intent) is not None

    @staticmethod
    def _query_or_path_from_request(request: IntentRequest) -> str:
        for key in ("query", "path", "repo_path", "root_path"):
            value = str(request.parameters.get(key, "")).strip()
            if value:
                return value

        match = re.search(r"[A-Za-z]:[\\/][^\r\n\"']+", request.raw_text)
        if not match:
            return ""

        candidate = match.group(0).strip().rstrip(".,;")
        while candidate:
            path = Path(candidate)
            if path.exists():
                return str(path)
            if " " in candidate:
                candidate = candidate.rsplit(" ", 1)[0].rstrip(".,;")
                continue
            parent = str(path.parent)
            if parent == candidate:
                break
            candidate = parent.rstrip("\\/")
        return match.group(0).strip().rstrip(".,;")

    def _apply_contextual_request(
        self,
        text: str,
        request: IntentRequest,
        memory_block: dict[str, Any] | None,
    ) -> IntentRequest:
        return self.contextual_resolver.apply(
            text=text,
            request=request,
            memory_block=memory_block,
        )

    def _resolve_project_or_directory(
        self,
        query: str,
        memory_block: dict[str, Any] | None,
    ) -> tuple[SamResult, Path | None]:
        if query:
            project_result, project = self.project_registry.find_project(query)
            if project_result.ok and project is not None:
                return project_result, Path(project.root_path)

            directory_result, directory = self.project_inspector.tools.resolve_directory_query(query)
            if directory_result.ok and directory is not None:
                return directory_result, directory

        daily_state = memory_block.get("daily_state", {}) if isinstance(memory_block, dict) else {}
        last_project_root = str(daily_state.get("last_project_root_path", {}).get("value", "")).strip()
        if last_project_root:
            directory_result, directory = self.project_inspector.tools.resolve_directory_query(last_project_root)
            if directory_result.ok and directory is not None:
                return directory_result, directory

        if query:
            return (
                SamResult(
                    status="failed",
                    summary="Project or directory could not be resolved.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message=query,
                    next_action="ask_user",
                ),
                None,
            )

        return (
            SamResult(
                status="failed",
                summary="I need a project name, repo path, or previous project context first.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="missing project context",
                next_action="ask_user",
            ),
            None,
        )

    @staticmethod
    def _patterns_from_request(request: IntentRequest) -> list[str]:
        raw_patterns = request.parameters.get("patterns", [])
        patterns: list[str] = []
        if isinstance(raw_patterns, str):
            patterns = [raw_patterns]
        elif isinstance(raw_patterns, list):
            patterns = [str(item) for item in raw_patterns]

        clean = [item.strip().strip("`'\"") for item in patterns if item and item.strip()]
        if clean:
            return clean

        text = request.raw_text
        quoted = re.findall(r"`([^`]+)`|'([^']+)'|\"([^\"]+)\"", text)
        for groups in quoted:
            for value in groups:
                if value.strip():
                    clean.append(value.strip())

        lowered = text.lower()
        scan_match = re.search(r"\b(?:for|patterns?)\s+(.+?)(?:\.|$)", text, flags=re.IGNORECASE)
        if scan_match and "hardcoded project names" not in lowered:
            fragment = scan_match.group(1)
            for part in re.split(r",|\band\b", fragment):
                token = part.strip().strip("`'\"")
                if token and len(token.split()) <= 4:
                    clean.append(token)

        return list(dict.fromkeys(item for item in clean if item))

    def _check_python_syntax(self, root: Path, request: IntentRequest) -> SamResult:
        errors: list[dict[str, object]] = []
        scanned = 0
        ignored_parts = {".git", ".venv", "venv", "__pycache__", "node_modules"}
        for path in root.rglob("*.py"):
            if any(part in ignored_parts for part in path.parts):
                continue
            scanned += 1
            try:
                ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
            except SyntaxError as exc:
                errors.append(
                    {
                        "path": str(path.relative_to(root)),
                        "line": exc.lineno,
                        "message": exc.msg,
                    }
                )
        if errors:
            preview = "; ".join(f"{item['path']}:{item['line']} {item['message']}" for item in errors[:5])
            summary = f"Python syntax check found {len(errors)} error(s) across {scanned} file(s): {preview}."
        else:
            summary = f"Python syntax check passed for {scanned} file(s)."
        return SamResult(
            status="success" if not errors else "failed",
            summary=summary,
            next_action="stop" if not errors else "ask_user",
            metadata={
                "intent": "check_python_syntax",
                "root_path": str(root),
                "files_scanned": scanned,
                "errors": errors,
                "source": request.source,
                "confidence": request.confidence,
            },
        )

    def _inspect_recent_changes(self, root: Path, request: IntentRequest) -> SamResult:
        git_result, snapshot = self.project_inspector.tools.inspect_git_state(root)
        if not git_result.ok or snapshot is None:
            return self._service_result("inspect_recent_changes", git_result, metadata={"root_path": str(root)})
        summary = (
            f"{root.name} is on branch {snapshot.branch or 'unknown'} with "
            f"{len(snapshot.changed_files)} changed file(s)."
        )
        if snapshot.changed_files:
            summary += " Changed: " + ", ".join(snapshot.changed_files[:12])
            if len(snapshot.changed_files) > 12:
                summary += f", and {len(snapshot.changed_files) - 12} more"
            summary += "."
        return SamResult(
            status="success",
            summary=summary,
            next_action="stop",
            metadata={
                "intent": "inspect_recent_changes",
                "root_path": str(root),
                "branch": snapshot.branch,
                "is_clean": snapshot.is_clean,
                "changed_files": snapshot.changed_files,
                "staged_files": snapshot.staged_files,
                "unstaged_files": snapshot.unstaged_files,
                "untracked_files": snapshot.untracked_files,
                "source": request.source,
                "confidence": request.confidence,
            },
        )

    def _run_autonomous_loop(
        self,
        request: IntentRequest,
        memory_block: dict[str, Any] | None,
    ) -> SamResult:
        if not self.model_client.is_available():
            return SamResult(
                status="failed",
                summary="My local reasoning model is unavailable, so I cannot run an autonomous investigation.",
                error_type=ErrorType.MISSING_CAPABILITY,
                error_message="llm unavailable",
                next_action="ask_user",
                metadata={"intent": request.intent, "source": request.source},
            )

        tools = self._autonomous_tool_manifest()
        observations: list[dict[str, Any]] = []
        tool_trace: list[dict[str, Any]] = []

        for step_index in range(5):
            try:
                decision = self.model_client.choose_autonomous_action(
                    user_text=request.raw_text,
                    tools=tools,
                    observations=observations,
                    memory_block=memory_block,
                    workspace_root=str(self.workspace_root),
                )
            except Exception as exc:
                if observations:
                    return self._final_from_observations(request, observations, tool_trace, str(exc))
                fallback = self._fallback_autonomous_read(request, memory_block, str(exc))
                if fallback is not None:
                    return fallback
                return SamResult(
                    status="failed",
                    summary="Autonomous reasoning failed before I could choose a safe tool.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message=str(exc),
                    next_action="retry",
                    metadata={"intent": request.intent, "source": request.source},
                )

            action = str(decision.get("action", "")).lower()
            if action == "final":
                answer = str(decision.get("answer", "")).strip() or self._observations_summary(observations)
                return SamResult(
                    status="success",
                    summary=answer,
                    next_action="stop",
                    metadata={
                        "intent": "autonomous_request",
                        "source": request.source,
                        "confidence": request.confidence,
                        "autonomous_steps": len(tool_trace),
                        "tool_trace": tool_trace,
                        "observations": observations,
                    },
                )

            if action == "ask_user":
                question = str(decision.get("question", "")).strip() or "I need one more detail before I can continue."
                return SamResult(
                    status="success",
                    summary=question,
                    next_action="ask_user",
                    metadata={
                        "intent": "clarify",
                        "source": "autonomous_loop",
                        "autonomous_steps": len(tool_trace),
                        "tool_trace": tool_trace,
                        "observations": observations,
                    },
                )

            if action != "tool":
                observations.append({"step": step_index + 1, "status": "failed", "summary": "Model chose an unsupported action."})
                continue

            tool_name = str(decision.get("tool", "")).strip()
            arguments = decision.get("arguments", {})
            if not isinstance(arguments, dict):
                arguments = {}
            worker_identity = self._autonomous_worker_identity(tool_name)
            task = worker_monitor.create_task(
                name=f"autonomous_{tool_name or 'unknown'}",
                worker_type=worker_identity.role,
                worker_name=worker_identity.name,
                description=f"Autonomous step {step_index + 1}: {tool_name}",
                worker_role=worker_identity.role,
                responsibility=worker_identity.responsibility,
            )
            worker_monitor.mark_running(task.task_id)
            worker_monitor.append_output(task.task_id, f"Tool: {tool_name}")
            if arguments:
                worker_monitor.append_output(task.task_id, f"Arguments: {arguments}")
            tool_result = self._execute_autonomous_tool(tool_name, arguments, request, memory_block)
            if tool_result.ok:
                worker_monitor.append_output(task.task_id, f"Observation: {tool_result.summary}")
                worker_monitor.mark_done(task.task_id)
            else:
                worker_monitor.append_output(task.task_id, f"Failure: {tool_result.error_message or tool_result.summary}")
                worker_monitor.mark_failed(task.task_id, tool_result.error_message or tool_result.summary)
            observation = self._compact_result(tool_name, arguments, tool_result, step_index + 1)
            observations.append(observation)
            tool_trace.append(
                {
                    "step": step_index + 1,
                    "tool": tool_name,
                    "worker_name": worker_identity.name,
                    "worker_type": worker_identity.role,
                    "worker_role": worker_identity.role,
                    "worker_responsibility": worker_identity.responsibility,
                    "arguments": arguments,
                    "status": tool_result.status,
                    "summary": tool_result.summary,
                }
            )

            if tool_result.next_action == "ask_user" and not tool_result.ok:
                return SamResult(
                    status="success",
                    summary=tool_result.summary,
                    next_action="ask_user",
                    metadata={
                        "intent": "clarify",
                        "source": "autonomous_loop",
                        "autonomous_steps": len(tool_trace),
                        "tool_trace": tool_trace,
                        "observations": observations,
                    },
                )

        return self._final_from_observations(request, observations, tool_trace, "")

    def _fallback_autonomous_read(
        self,
        request: IntentRequest,
        memory_block: dict[str, Any] | None,
        reason: str,
    ) -> SamResult | None:
        roots = self._memory_roots(memory_block)
        query = str(request.parameters.get("query", "") if isinstance(request.parameters, dict) else "").strip()
        if query:
            root_result, root = self._resolve_project_or_directory(query, memory_block)
            if root_result.ok and root is not None:
                roots.insert(0, str(root))
        roots = list(dict.fromkeys(root for root in roots if root))
        patterns = _autonomous_search_terms(request.raw_text)
        if not roots or not patterns:
            return None

        root = Path(roots[0])
        scan_result, report = CodebaseCleanupService(root).inspect(patterns)
        matches = report.matches[:25]
        if matches:
            preview = "; ".join(f"{match.path}:{match.line_number} ({match.pattern})" for match in matches[:8])
            summary = (
                f"I could not use the reasoning model for tool choice, so I used the current project context. "
                f"Found {len(report.matches)} match(es) for {', '.join(patterns)} in {root}: {preview}."
            )
        else:
            summary = (
                f"I could not use the reasoning model for tool choice, so I searched the current project context. "
                f"No matches found for {', '.join(patterns)} in {root}."
            )
        return SamResult(
            status="success" if scan_result.ok else scan_result.status,
            summary=summary,
            error_type=None if scan_result.ok else scan_result.error_type,
            error_message=None if scan_result.ok else scan_result.error_message or reason,
            next_action="stop" if scan_result.ok else "ask_user",
            metadata={
                "intent": "autonomous_request",
                "source": "autonomous_fallback",
                "fallback_reason": reason,
                "root_path": str(root),
                "patterns": patterns,
                "match_count": len(report.matches),
                "matches": [match.__dict__ for match in matches],
                "observations": [
                    {
                        "step": 1,
                        "tool": "scan_codebase_patterns",
                        "status": scan_result.status,
                        "summary": scan_result.summary,
                    }
                ],
            },
        )

    def _execute_autonomous_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        parent_request: IntentRequest,
        memory_block: dict[str, Any] | None,
    ) -> SamResult:
        allowed_tools = {item["name"] for item in self._autonomous_tool_manifest()}
        if tool_name not in allowed_tools:
            return SamResult(
                status="failed",
                summary=f"Tool {tool_name} is not available to the autonomous read-only loop.",
                error_type=ErrorType.MISSING_CAPABILITY,
                error_message=tool_name,
                next_action="ask_user",
                metadata={"intent": "autonomous_request"},
            )

        child = IntentRequest(
            intent=tool_name,
            parameters=dict(arguments),
            raw_text=parent_request.raw_text,
            confidence=parent_request.confidence,
            source="autonomous_loop",
        )

        if tool_name == "capabilities":
            result = self.awareness.describe_self()
            result.metadata.setdefault("intent", "capabilities")
            return result
        if tool_name == "list_projects":
            project_result, projects = self.project_registry.list_projects()
            if not project_result.ok:
                return self._service_result("list_projects", project_result)
            names = [project.name for project in projects]
            return SamResult(
                status="success",
                summary=f"I know about {len(names)} project(s): {', '.join(names)}.",
                next_action="stop",
                metadata={"intent": "list_projects", "count": len(names), "projects": names},
            )
        if tool_name == "list_executor_tools":
            tools = self.tool_executor.list_metadata()
            return SamResult(
                status="success",
                summary=f"{len(tools)} executor tool(s) registered.",
                next_action="stop",
                metadata={"intent": "list_executor_tools", "tools": tools, "count": len(tools)},
            )
        if tool_name == "list_worker_tasks":
            tasks = sorted(worker_monitor.list_tasks(), key=lambda item: item.created_at, reverse=True)[:20]
            return SamResult(
                status="success",
                summary=f"{len(tasks)} recent worker task(s) found.",
                next_action="stop",
                metadata={
                    "intent": "list_worker_tasks",
                    "tasks": [
                        {
                            "task_id": task.task_id,
                            "name": task.name,
                            "worker_name": task.worker_name,
                            "status": task.status,
                            "description": task.description,
                            "last_output": task.output_lines[-3:],
                        }
                        for task in tasks
                    ],
                },
            )
        if tool_name == "read_file":
            path = str(arguments.get("path", "")).strip()
            if not path:
                return SamResult(
                    status="failed",
                    summary="File path is required.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing path",
                    next_action="ask_user",
                    metadata={"intent": "read_file"},
                )
            additional_roots = self._memory_roots(memory_block)
            resolve_result, resolved = self.project_inspector.tools.resolve_file_query(path, additional_roots=additional_roots)
            if not resolve_result.ok or resolved is None:
                return self._service_result("read_file", resolve_result, metadata={"path": path})
            max_chars = int(arguments.get("max_chars", 6000) or 6000)
            file_result, content = self.project_inspector.tools.read_text_file(resolved, max_chars=max_chars)
            if file_result.ok and content is not None:
                file_result.metadata["content"] = content
                file_result.metadata.setdefault("intent", "read_file")
            return file_result
        if tool_name == "list_directory":
            path = str(arguments.get("path", "") or arguments.get("query", "")).strip()
            root_result, root = self._resolve_project_or_directory(path, memory_block)
            if not root_result.ok or root is None:
                return self._service_result("list_directory", root_result, metadata={"path": path})
            directory_result, entries = self.project_inspector.tools.list_directory(root)
            if directory_result.ok:
                directory_result.metadata.update({"intent": "list_directory", "entries": entries[:100]})
            return directory_result
        if tool_name in {"inspect_repo", "inspect_project_repo"}:
            query = str(arguments.get("query", "") or arguments.get("path", "")).strip()
            root_result, root = self._resolve_project_or_directory(query, memory_block)
            if not root_result.ok or root is None:
                return self._service_result(tool_name, root_result, metadata={"query": query})
            inspect_result, inspection = self.project_inspector.inspect(str(root))
            if not inspect_result.ok or inspection is None:
                return self._service_result(tool_name, inspect_result, metadata={"query": str(root)})
            metadata = inspection_metadata(inspection)
            metadata["intent"] = tool_name
            return SamResult(
                status="success",
                summary=f"{inspection.name} is on branch {inspection.branch or 'unknown'} with {len(inspection.changed_files)} changed file(s).",
                next_action="stop",
                metadata=metadata,
            )
        if tool_name == "inspect_git_state":
            query = str(arguments.get("query", "") or arguments.get("repo_path", "") or arguments.get("path", "")).strip()
            root_result, root = self._resolve_project_or_directory(query, memory_block)
            if not root_result.ok or root is None:
                return self._service_result("inspect_git_state", root_result, metadata={"query": query})
            git_result, snapshot = self.project_inspector.tools.inspect_git_state(root)
            if git_result.ok and snapshot is not None:
                git_result.metadata.update(
                    {
                        "intent": "inspect_git_state",
                        "repo_root": snapshot.repo_root,
                        "branch": snapshot.branch,
                        "is_clean": snapshot.is_clean,
                        "changed_files": snapshot.changed_files,
                        "staged_files": snapshot.staged_files,
                        "unstaged_files": snapshot.unstaged_files,
                        "untracked_files": snapshot.untracked_files,
                    }
                )
            return git_result
        if tool_name == "scan_codebase_patterns":
            query = str(arguments.get("query", "") or arguments.get("path", "")).strip()
            root_result, root = self._resolve_project_or_directory(query, memory_block)
            if not root_result.ok or root is None:
                return self._service_result("scan_codebase_patterns", root_result, metadata={"query": query})
            patterns = arguments.get("patterns", [])
            if isinstance(patterns, str):
                patterns = [patterns]
            patterns = [str(item).strip() for item in patterns if str(item).strip()]
            if not patterns:
                patterns = self._patterns_from_request(parent_request)
            if not patterns:
                return SamResult(
                    status="failed",
                    summary="Search patterns are required.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message="missing patterns",
                    next_action="ask_user",
                    metadata={"intent": "scan_codebase_patterns", "root_path": str(root)},
                )
            scan_result, report = CodebaseCleanupService(root).inspect(patterns)
            scan_result.metadata.update(
                {
                    "intent": "scan_codebase_patterns",
                    "root_path": str(root),
                    "patterns": patterns,
                    "matches": [match.__dict__ for match in report.matches[:100]],
                    "match_count": len(report.matches),
                }
            )
            return scan_result
        if tool_name == "check_python_syntax":
            query = str(arguments.get("query", "") or arguments.get("path", "")).strip()
            root_result, root = self._resolve_project_or_directory(query, memory_block)
            if not root_result.ok or root is None:
                return self._service_result("check_python_syntax", root_result, metadata={"query": query})
            return self._check_python_syntax(root, child)
        if tool_name == "inspect_recent_changes":
            query = str(arguments.get("query", "") or arguments.get("path", "")).strip()
            root_result, root = self._resolve_project_or_directory(query, memory_block)
            if not root_result.ok or root is None:
                return self._service_result("inspect_recent_changes", root_result, metadata={"query": query})
            return self._inspect_recent_changes(root, child)
        if tool_name == "inspect_workspace_cleanup":
            scope = str(arguments.get("scope", "all") or "all")
            result, _metadata = self.workspace_cleanup.inspect(scope)
            result.metadata.setdefault("intent", "inspect_workspace_cleanup")
            return result

        return SamResult(
            status="failed",
            summary=f"Autonomous tool {tool_name} is not implemented.",
            error_type=ErrorType.MISSING_CAPABILITY,
            error_message=tool_name,
            next_action="ask_user",
            metadata={"intent": "autonomous_request"},
        )

    def _autonomous_tool_manifest(self) -> list[dict[str, Any]]:
        return [
            {"name": "capabilities", "description": "List available Sam capabilities.", "arguments": {}},
            {"name": "list_projects", "description": "List registered projects.", "arguments": {}},
            {"name": "read_file", "description": "Read a UTF-8 text file.", "arguments": {"path": "file path or name", "max_chars": 6000}},
            {"name": "list_directory", "description": "List files in a directory.", "arguments": {"path": "directory path or project path"}},
            {"name": "inspect_repo", "description": "Inspect repo branch, working tree, and top-level files.", "arguments": {"query": "project name or path"}},
            {"name": "inspect_git_state", "description": "Inspect git branch and changed files.", "arguments": {"query": "project name or path"}},
            {"name": "scan_codebase_patterns", "description": "Scan codebase for exact text patterns.", "arguments": {"query": "project name or path", "patterns": ["text"]}},
            {"name": "check_python_syntax", "description": "Parse Python files and report syntax errors.", "arguments": {"query": "project name or path"}},
            {"name": "inspect_recent_changes", "description": "Summarize current git working-tree changes.", "arguments": {"query": "project name or path"}},
            {"name": "inspect_workspace_cleanup", "description": "Inspect duplicate cleanup candidates without deleting.", "arguments": {"scope": "all|projects|runtime"}},
            {"name": "list_executor_tools", "description": "List registered executor tools.", "arguments": {}},
            {"name": "list_worker_tasks", "description": "List running and recent worker tasks.", "arguments": {}},
        ]

    @staticmethod
    def _autonomous_worker_identity(tool_name: str):
        return resolve_worker_identity(tool_name=tool_name, action_category="read_data")

    @staticmethod
    def _memory_roots(memory_block: dict[str, Any] | None) -> list[str]:
        daily_state = memory_block.get("daily_state", {}) if isinstance(memory_block, dict) else {}
        roots = []
        for key in ("last_project_root_path", "last_file_path"):
            value = str(daily_state.get(key, {}).get("value", "")).strip()
            if value:
                roots.append(str(Path(value).parent if key == "last_file_path" else Path(value)))
        return roots

    @staticmethod
    def _compact_result(
        tool_name: str,
        arguments: dict[str, Any],
        result: SamResult,
        step: int,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        for key in (
            "intent",
            "path",
            "root_path",
            "repo_root",
            "branch",
            "is_clean",
            "changed_files",
            "entries",
            "entry_count",
            "patterns",
            "match_count",
            "matches",
            "errors",
            "files_scanned",
            "tasks",
            "tools",
            "projects",
            "content",
        ):
            if key in result.metadata:
                value = result.metadata[key]
                if key == "content" and isinstance(value, str):
                    value = value[:4000]
                elif isinstance(value, list):
                    value = value[:25]
                metadata[key] = value
        return {
            "step": step,
            "tool": tool_name,
            "arguments": arguments,
            "status": result.status,
            "summary": result.summary,
            "error": result.error_message,
            "metadata": metadata,
        }

    def _final_from_observations(
        self,
        request: IntentRequest,
        observations: list[dict[str, Any]],
        tool_trace: list[dict[str, Any]],
        reason: str,
    ) -> SamResult:
        summary = self._observations_summary(observations)
        if reason:
            summary += f" I stopped after observation because final synthesis failed: {reason}"
        return SamResult(
            status="success" if observations else "failed",
            summary=summary,
            error_type=None if observations else ErrorType.TOOL_FAILED,
            error_message=reason or None,
            next_action="stop" if observations else "retry",
            metadata={
                "intent": "autonomous_request",
                "source": request.source,
                "confidence": request.confidence,
                "autonomous_steps": len(tool_trace),
                "tool_trace": tool_trace,
                "observations": observations,
            },
        )

    @staticmethod
    def _observations_summary(observations: list[dict[str, Any]]) -> str:
        if not observations:
            return "I could not gather enough observations to answer."
        lines = [str(item.get("summary", "")).strip() for item in observations if str(item.get("summary", "")).strip()]
        return " ".join(lines[-3:]) or "I gathered observations, but they did not include a clear answer."


    def _parse_with_llm(self, text: str, memory_block: dict[str, Any] | None = None) -> IntentRequest | None:
        if not text:
            return None
        if not self.model_client.is_available():
            return None
        try:
            list_result, projects = self.project_registry.list_projects()
            known_projects = []
            if list_result.ok:
                known_projects = [
                    {"project_id": project.project_id, "name": project.name, "root_path": project.root_path}
                    for project in projects[:25]
                ]

            output = self.model_client.classify_request(
                text,
                capabilities=[item.intent for item in self.registry.list_all()],
                memory_block=memory_block,
                known_projects=known_projects,
                workspace_root=str(self.workspace_root),
            )
        except Exception:
            return None
        return self._intent_request_from_llm(text, output)

    def _intent_request_from_llm(self, text: str, output: OllamaIntentOutput) -> IntentRequest:
        supported_intents = {item.intent for item in self.registry.list_all()}
        raw_intent = (output.intent or "chat").strip()

        if raw_intent not in supported_intents:
            return IntentRequest(
                intent=raw_intent or "unknown",
                parameters=output.parameters or {},
                raw_text=text,
                needs_clarification=False,
                response_text=(
                    f"I understood this as `{raw_intent}`, but I do not have that capability registered yet. "
                    "I can propose an upgrade for it if you want."
                ),
                confidence=output.confidence or "low",
                source=output.source or "llm",
            )

        return IntentRequest(
            intent=raw_intent,
            parameters=output.parameters or {},
            raw_text=text,
            needs_clarification=output.needs_clarification,
            clarification_question=output.clarification_question,
            response_text=output.response_text,
            confidence=output.confidence,
            source=output.source or "llm",
        )

    @staticmethod
    def _looks_like_file_query(text: str) -> bool:
        candidate = text.strip().lower()
        if not candidate:
            return False
        if any(sep in candidate for sep in ("\\", "/")):
            return True
        known_extensions = (
            ".md",
            ".txt",
            ".py",
            ".json",
            ".html",
            ".js",
            ".css",
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".csv",
            ".log",
        )
        return candidate.endswith(known_extensions)

    @staticmethod
    def _is_meaningful_llm_request(request: IntentRequest) -> bool:
        return True

    def _request_push_approval(self, request: IntentRequest) -> SamResult:
        if self.approval_manager is None:
            return SamResult(
                status="needs_approval",
                summary="Pushing changes requires approval before I continue.",
                error_type=ErrorType.MISSING_PERMISSION,
                error_message="git push requires approval",
                next_action="request_approval",
                metadata={"intent": "push_changes", "source": request.source, "confidence": request.confidence},
            )

        ensure_result = self.approval_manager.ensure_schema()
        if not ensure_result.ok:
            return SamResult(
                status="failed",
                summary="Approval store could not be prepared for a push request.",
                error_type=ensure_result.error_type or ErrorType.FILE_ACCESS_ERROR,
                error_message=ensure_result.error_message,
                next_action=ensure_result.next_action or "retry",
                metadata={"intent": "push_changes", "source": request.source},
            )

        create_result, approval = self.approval_manager.create_request(
            agent_id="sam_v2_router",
            agent_name="Sam v2 Router",
            tool_name="git.push",
            tool_arguments={"request_text": request.raw_text},
            action_category="execute_command",
            reason="Git push is approval-sensitive.",
            context=request.raw_text,
        )
        if not create_result.ok or approval is None:
            return SamResult(
                status="failed",
                summary="Approval request creation failed for push action.",
                error_type=create_result.error_type or ErrorType.FILE_ACCESS_ERROR,
                error_message=create_result.error_message,
                next_action=create_result.next_action or "retry",
                metadata={"intent": "push_changes", "source": request.source},
            )

        return SamResult(
            status="needs_approval",
            summary="Pushing changes requires approval before I continue.",
            error_type=ErrorType.MISSING_PERMISSION,
            error_message="git push requires approval",
            next_action="request_approval",
            metadata={
                "intent": "push_changes",
                "approval_id": approval.id,
                "source": request.source,
                "confidence": request.confidence,
            },
        )

    def _check_authority(self, request: IntentRequest, action_category: str) -> SamResult | None:
        if self.authority_engine is None:
            return None

        decision = self.authority_engine.check(
            agent_id="sam_v2_router",
            agent_level=5,
            role_id="supervisor",
            tool_name=request.intent,
            action_category=action_category,
        )
        if not decision.allowed:
            return SamResult(
                status="blocked",
                summary="Intent blocked by authority rules.",
                error_type=ErrorType.MISSING_PERMISSION,
                error_message=decision.reason,
                next_action="request_approval",
                metadata={"intent": request.intent, "action_category": action_category},
            )

        if decision.requires_approval:
            if self.approval_manager is None:
                return SamResult(
                    status="needs_approval",
                    summary="Intent requires approval.",
                    error_type=ErrorType.MISSING_PERMISSION,
                    error_message=decision.reason,
                    next_action="request_approval",
                    metadata={"intent": request.intent, "action_category": action_category},
                )

            create_result, approval = self.approval_manager.create_request(
                agent_id="sam_v2_router",
                agent_name="Sam v2 Router",
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
                summary="Intent requires approval before execution.",
                error_type=ErrorType.MISSING_PERMISSION,
                error_message=decision.reason,
                next_action="request_approval",
                metadata={"approval_id": approval.id, "intent": request.intent},
            )

        return None

    def _service_result(
        self,
        intent: str,
        result: SamResult,
        identifier: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SamResult:
        merged_metadata = dict(result.metadata)
        merged_metadata["intent"] = intent
        if identifier is not None:
            merged_metadata["id"] = identifier
        merged_metadata.setdefault("source", "rules")
        if metadata:
            merged_metadata.update(metadata)
        return SamResult(
            status=result.status,
            summary=result.summary,
            error_type=result.error_type,
            error_message=result.error_message,
            next_action=result.next_action,
            metadata=merged_metadata,
        )


def _autonomous_search_terms(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9_.-]+", text.lower())
    ignored = {
        "a",
        "an",
        "are",
        "can",
        "check",
        "could",
        "due",
        "for",
        "in",
        "is",
        "it",
        "me",
        "may",
        "please",
        "the",
        "their",
        "this",
        "to",
        "when",
        "you",
    }
    terms = [word for word in words if len(word) > 2 and word not in ignored]
    return list(dict.fromkeys(terms))[:6]

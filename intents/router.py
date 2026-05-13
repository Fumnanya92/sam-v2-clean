"""Minimal intent parser and router for Sam v2."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

from approvals import ApprovalManager, AuthorityEngine
from capabilities import CapabilityAwarenessService, CapabilityRegistry, build_default_registry
from core.autonomous_runtime import AutonomousRuntime, RuntimeServices
from sam.planner.task_planner import TaskPlanner
from sam.executor.tool_executor import ToolExecutor
from intents._executor_tools_registry import register_all_executor_tools
from intents.conversation_state import ConversationStateEngine
from intents.contextual_resolver import ContextualRequestResolver
from intents.legacy_adapter import LegacyIntentAdapter
from sam.executor.service_tools import register_service_tools
from sam.executor.worker_runtime import create_worker_centric_executor
from sam.planner.observation_loop import create_observation_loop
from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult
from llm import OllamaClient, OllamaIntentOutput
from projects import (
    ProjectInspector,
    ProjectPlanner,
    ProjectRegistry,
    ProjectScaffolder,
)
from tools import SafeLocalTools, WorkspaceCleanupService
from upgrades import UpgradeProposalManager
from workers import ToolingWorker
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
        self.task_planner = TaskPlanner(self.registry)
        self.tool_executor = ToolExecutor()
        self._register_executor_tools()
        self._base_executor = self.tool_executor
        self.tool_executor = create_worker_centric_executor(self._base_executor)
        self.autonomous_runtime = AutonomousRuntime(
            model_client=self.model_client,
            workspace_root=self.workspace_root,
            awareness=self.awareness,
            project_registry=self.project_registry,
            tool_executor=self.tool_executor,
            project_inspector=self.project_inspector,
            workspace_cleanup=self.workspace_cleanup,
            services=RuntimeServices(
                resolve_project_or_directory=self._resolve_project_or_directory,
                service_result=self._service_result,
                check_python_syntax=self._check_python_syntax,
                inspect_recent_changes=self._inspect_recent_changes,
            ),
        )
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
        self.autonomous_runtime.model_client = self.model_client
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

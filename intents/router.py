"""Minimal intent parser and router for Sam v2."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from approvals import ApprovalManager, AuthorityEngine
from capabilities import CapabilityAwarenessService, CapabilityRegistry, build_default_registry
from core.autonomous_runtime import AutonomousRuntime, RuntimeServices
from core.authority_gate import RuntimeAuthorityGate
from core.contextual_resolver import ContextualRequestResolver
from core.conversation_state import ConversationStateEngine
from core.execution_engine import RuntimeExecutionEngine
from core.legacy_adapter import LegacyIntentAdapter
from core.request_model import IntentRequest
from core.runtime_services import RuntimeToolServices
from sam.executor.runtime_tools_registry import register_all_executor_tools
from sam.executor.service_tools import register_service_tools
from sam.executor.tool_executor import ToolExecutor
from sam.executor.worker_runtime import create_worker_centric_executor
from sam.planner.observation_loop import create_observation_loop
from sam.planner.task_planner import TaskPlanner
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
        self.authority_gate = RuntimeAuthorityGate(
            authority_engine=self.authority_engine,
            approval_manager=self.approval_manager,
        )
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
        self.runtime_services = RuntimeToolServices(
            project_registry=self.project_registry,
            project_inspector=self.project_inspector,
            approval_manager=self.approval_manager,
        )
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
                resolve_project_or_directory=self.runtime_services.resolve_project_or_directory,
                service_result=self.runtime_services.service_result,
                check_python_syntax=self.runtime_services.check_python_syntax,
                inspect_recent_changes=self.runtime_services.inspect_recent_changes,
            ),
        )
        self.observation_loop = create_observation_loop(self.task_planner, self.tool_executor)
        self.execution_engine = RuntimeExecutionEngine(
            tool_executor=self.tool_executor,
            task_planner=self.task_planner,
            observation_loop=self.observation_loop,
        )

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
                if self.tool_executor.get(request.intent) is None:
                    result = SamResult(
                        status="failed",
                        summary="Intent is not registered.",
                        error_type=ErrorType.MISSING_CAPABILITY,
                        error_message=request.intent,
                        next_action="ask_user",
                    )
                    self.conversation_state.writeback(result, state)
                    return result
            else:
                approval_result = self.authority_gate.check(request, capability.action_category)
                if approval_result is not None:
                    self.conversation_state.writeback(approval_result, state)
                    return approval_result

        result = self.execution_engine.execute(
            request,
            memory_block=memory_block,
            legacy_execute=lambda legacy_req: self.legacy_adapter.handle(
                user_text=user_text,
                request=legacy_req,
                state=state,
                memory_block=memory_block,
            ),
        )
        result.metadata.setdefault("source", request.source)
        result.metadata.setdefault("confidence", request.confidence)
        self.conversation_state.writeback(result, state)
        return result

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
        1. Runtime tool registry for compatibility capabilities
        2. Service tools registry for utility operations
        
        All intent business logic and service handlers are extracted into
        reusable tool handlers.
        """
        register_all_executor_tools(self)
        register_service_tools(
            self.tool_executor,
            repo_root=str(self.workspace_root),
            db_path=str(self.db_path),
        )

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


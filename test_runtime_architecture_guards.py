from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from core.runtime import SamRuntime
from llm import OllamaIntentOutput


class _Model:
    def __init__(self, mapping: dict[str, OllamaIntentOutput]) -> None:
        self.mapping = mapping

    def is_available(self) -> bool:
        return True

    def classify_request(self, user_text: str, *args: Any, **kwargs: Any) -> OllamaIntentOutput:
        return self.mapping.get(
            user_text.strip().lower(),
            OllamaIntentOutput(intent="chat", parameters={}, confidence="medium", source="test"),
        )


class _UnavailableModel:
    def is_available(self) -> bool:
        return False


class _AutonomousModel(_Model):
    def __init__(self, mapping: dict[str, OllamaIntentOutput], actions: list[dict[str, Any]]) -> None:
        super().__init__(mapping)
        self.actions = actions
        self.action_index = 0

    def choose_autonomous_action(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if self.action_index >= len(self.actions):
            return {"action": "final", "answer": "done"}
        action = self.actions[self.action_index]
        self.action_index += 1
        return action


def test_primary_execution_path_uses_runtime_engine_for_registered_tool() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        runtime = SamRuntime(
            db_path=root / "sam.db",
            memory_path=root / "memory.json",
            session_path=root / "session.json",
            workspace_root=root / "workspace",
        )
        runtime.handler.router.model_client = _Model(
            {"list tasks": OllamaIntentOutput(intent="list_tasks", parameters={}, confidence="high", source="test")}
        )
        result = runtime.handle_text("list tasks")
        assert result.ok
        assert result.metadata.get("execution_path") == "runtime_engine"


def test_followup_overrides_chat_hint_using_goal_state() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        runtime = SamRuntime(
            db_path=root / "sam.db",
            memory_path=root / "memory.json",
            session_path=root / "session.json",
            workspace_root=root / "workspace",
        )
        runtime.handler.router.model_client = _Model(
            {
                "list tasks": OllamaIntentOutput(intent="list_tasks", parameters={}, confidence="high", source="test"),
                "continue": OllamaIntentOutput(intent="chat", parameters={}, confidence="high", source="test"),
            }
        )
        first = runtime.handle_text("list tasks")
        second = runtime.handle_text("continue")
        assert first.ok and second.ok
        assert second.metadata.get("parsed_intent_hint") == "chat"
        assert second.metadata.get("intent") == "list_tasks"


def test_runtime_events_stream_is_present() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        runtime = SamRuntime(
            db_path=root / "sam.db",
            memory_path=root / "memory.json",
            session_path=root / "session.json",
            workspace_root=root / "workspace",
        )
        runtime.handler.router.model_client = _Model(
            {"list tasks": OllamaIntentOutput(intent="list_tasks", parameters={}, confidence="high", source="test")}
        )
        result = runtime.handle_text("list tasks")
        events = result.metadata.get("runtime_events", [])
        assert isinstance(events, list) and events
        assert any(isinstance(item, dict) and item.get("type") == "runtime.result" for item in events)
        messages = [item.get("message", "") for item in events if isinstance(item, dict)]
        assert any(str(message).startswith("[Orbit]") for message in messages)
        assert any(str(message).startswith("[Echo]") for message in messages)


def test_runtime_events_include_autonomous_tool_observations() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        runtime = SamRuntime(
            db_path=root / "sam.db",
            memory_path=root / "memory.json",
            session_path=root / "session.json",
            workspace_root=root / "workspace",
        )
        model = _AutonomousModel(
            {
                "check renewal in the project": OllamaIntentOutput(
                    intent="autonomous_request",
                    parameters={},
                    confidence="high",
                    source="test",
                )
            },
            actions=[
                {
                    "action": "tool",
                    "tool": "scan_codebase_patterns",
                    "arguments": {"query": str(root / "missing_project"), "patterns": ["renewal"]},
                },
                {"action": "final", "answer": "Project or directory could not be resolved."},
            ],
        )
        runtime.handler.router.model_client = model
        runtime.handler.request_parser.model_client = model
        runtime.handler.request_parser.autonomous_runtime.model_client = model

        result = runtime.handle_text("check renewal in the project")

        events = result.metadata.get("runtime_events", [])
        messages = [item.get("message", "") for item in events if isinstance(item, dict)]
        assert result.next_action == "ask_user"
        assert any("scan_codebase_patterns -> failed" in str(message) for message in messages)
        assert any("error=" in str(message) for message in messages)
        assert any("1 step(s)" in str(step.get("observation", "")) for step in result.metadata["execution_trace"])


def test_chat_like_requests_use_legacy_compatibility_path() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        runtime = SamRuntime(
            db_path=root / "sam.db",
            memory_path=root / "memory.json",
            session_path=root / "session.json",
            workspace_root=root / "workspace",
        )
        runtime.handler.router.model_client = _Model(
            {"hello": OllamaIntentOutput(intent="chat", parameters={}, confidence="high", source="test")}
        )
        result = runtime.handle_text("hello")
        assert result.ok
        assert result.metadata.get("execution_path") == "legacy_router_compat"


def test_model_unavailable_operational_goal_enters_autonomous_loop() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        project = root / "TargetProject"
        project.mkdir()
        (project / "billing_rules.txt").write_text("The renewal fee grace window ends on day 14.\n", encoding="utf-8")
        runtime = SamRuntime(
            db_path=root / "sam.db",
            memory_path=root / "memory.json",
            session_path=root / "session.json",
            workspace_root=root / "workspace",
        )
        runtime.handler.request_parser.model_client = _UnavailableModel()
        runtime.handler.request_parser.autonomous_runtime.model_client = _UnavailableModel()

        result = runtime.handle_text(f"check when the renewal fee grace window ends in this project {project}")

        assert result.ok
        assert result.metadata.get("intent") == "autonomous_request"
        assert result.metadata.get("autonomous_steps", 0) >= 1
        assert "day 14" in result.summary or "renewal" in result.summary.lower()


def test_parser_clarification_does_not_block_known_project_context() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        project = root / "Estate"
        project.mkdir()
        runtime = SamRuntime(
            db_path=root / "sam.db",
            memory_path=root / "memory.json",
            session_path=root / "session.json",
            workspace_root=root / "sam_v2" / "workspace",
        )
        model = _AutonomousModel(
            {
                "can you find the estate app": OllamaIntentOutput(
                    intent="discovery_workflow",
                    parameters={},
                    confidence="high",
                    source="test",
                ),
                "1": OllamaIntentOutput(
                    intent="discovery_workflow",
                    parameters={},
                    confidence="high",
                    source="test",
                ),
                "can you check when the renewal window is due": OllamaIntentOutput(
                    intent="autonomous_request",
                    parameters={},
                    needs_clarification=True,
                    clarification_question="Which project should I inspect?",
                    confidence="medium",
                    source="test",
                ),
            },
            actions=[{"action": "final", "answer": "I used the selected project context and continued."}],
        )
        runtime.handler.router.model_client = model
        runtime.handler.request_parser.model_client = model
        runtime.handler.request_parser.autonomous_runtime.model_client = model

        found = runtime.handle_text("can you find the estate app")
        selected = runtime.handle_text("1")
        continued = runtime.handle_text("can you check when the renewal window is due")

        assert found.ok and selected.ok and continued.ok
        assert selected.metadata.get("root_path") == str(project)
        assert continued.metadata.get("intent") == "autonomous_request"
        assert continued.summary == "I used the selected project context and continued."
        assert "need a bit more detail" not in continued.summary.lower()


def test_question_like_scan_hint_enters_autonomous_evidence_loop() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        project = root / "Project"
        project.mkdir()
        (project / "rules.txt").write_text("The billing window closes on day 21.\n", encoding="utf-8")
        runtime = SamRuntime(
            db_path=root / "sam.db",
            memory_path=root / "memory.json",
            session_path=root / "session.json",
            workspace_root=root / "workspace",
        )
        model = _AutonomousModel(
            {
                "when does the billing window close in this project": OllamaIntentOutput(
                    intent="scan_codebase_patterns",
                    parameters={"query": str(project), "patterns": ["billing window", "close"]},
                    confidence="high",
                    source="test",
                )
            },
            actions=[
                {
                    "action": "tool",
                    "tool": "scan_codebase_patterns",
                    "arguments": {"query": str(project), "patterns": ["billing window", "close"]},
                },
                {"action": "final", "answer": "The billing window closes on day 21."},
            ],
        )
        runtime.handler.router.model_client = model
        runtime.handler.request_parser.model_client = model
        runtime.handler.request_parser.autonomous_runtime.model_client = model

        result = runtime.handle_text("when does the billing window close in this project")

        assert result.ok
        assert result.metadata.get("intent") == "autonomous_request"
        assert result.metadata.get("autonomous_steps", 0) >= 1
        assert "day 21" in result.summary


def test_router_does_not_own_autonomous_runtime_loop() -> None:
    router_source = Path("intents/router.py").read_text(encoding="utf-8")
    assert "_run_autonomous_loop" not in router_source
    assert "_execute_autonomous_tool" not in router_source
    assert "choose_autonomous_action" not in router_source

    runtime_source = Path("core/autonomous_runtime.py").read_text(encoding="utf-8")
    assert "class AutonomousRuntime" in runtime_source
    assert "choose_autonomous_action" in runtime_source


def test_autonomous_runtime_uses_operational_tool_registry() -> None:
    runtime_source = Path("core/autonomous_runtime.py").read_text(encoding="utf-8")
    execute_body = runtime_source.split("    def execute_tool(", 1)[1].split("    def tool_manifest(", 1)[0]

    assert "self.tool_registry.execute" in execute_body
    assert "if tool_name ==" not in execute_body
    assert "if tool_name in" not in execute_body

    registry_source = Path("core/operational_tools.py").read_text(encoding="utf-8")
    assert "class OperationalToolRegistry" in registry_source
    assert "build_default_operational_registry" in registry_source


def test_autonomous_runtime_has_model_unavailable_policy_fallback() -> None:
    runtime_source = Path("core/autonomous_runtime.py").read_text(encoding="utf-8")
    policy_source = Path("core/autonomy_policy.py").read_text(encoding="utf-8")

    assert "AutonomousDecisionPolicy" in runtime_source
    assert "My local reasoning model is unavailable" not in runtime_source
    assert "class AutonomousDecisionPolicy" in policy_source


def test_intents_package_does_not_own_runtime_tool_registry() -> None:
    assert not Path("intents/_executor_tools_registry.py").exists()
    assert Path("sam/executor/runtime_tools_registry.py").exists()

    router_source = Path("intents/router.py").read_text(encoding="utf-8")
    assert "from intents._executor_tools_registry" not in router_source
    assert "from sam.executor.runtime_tools_registry import register_all_executor_tools" in router_source


def test_intents_package_does_not_own_runtime_state_helpers() -> None:
    assert not Path("intents/conversation_state.py").exists()
    assert not Path("intents/contextual_resolver.py").exists()
    assert Path("core/conversation_state.py").exists()
    assert Path("core/contextual_resolver.py").exists()

    router_source = Path("intents/router.py").read_text(encoding="utf-8")
    assert "from intents.conversation_state" not in router_source
    assert "from intents.contextual_resolver" not in router_source


def test_intents_package_does_not_own_legacy_execution_adapter() -> None:
    assert not Path("intents/legacy_adapter.py").exists()
    assert Path("core/legacy_adapter.py").exists()

    router_source = Path("intents/router.py").read_text(encoding="utf-8")
    assert "from intents.legacy_adapter" not in router_source
    assert "from core.legacy_adapter import LegacyIntentAdapter" in router_source


def test_core_runtime_does_not_depend_on_intents_package_for_request_model() -> None:
    for path in (
        Path("core/workflow_runtime.py"),
        Path("core/runtime_policy.py"),
        Path("core/execution_engine.py"),
    ):
        source = path.read_text(encoding="utf-8")
        assert "from intents import IntentRequest" not in source
        assert "from intents import IntentRequest, IntentRouter" not in source
    assert Path("core/request_model.py").exists()


def test_router_does_not_own_runtime_tool_service_helpers() -> None:
    router_source = Path("intents/router.py").read_text(encoding="utf-8")
    for name in (
        "_resolve_project_or_directory",
        "_service_result",
        "_check_python_syntax",
        "_inspect_recent_changes",
        "_patterns_from_request",
        "_query_or_path_from_request",
        "_request_push_approval",
    ):
        assert f"def {name}" not in router_source

    assert Path("core/runtime_services.py").exists()


def test_router_does_not_own_planner_observation_execution() -> None:
    router_source = Path("intents/router.py").read_text(encoding="utf-8")
    execution_engine_source = Path("core/execution_engine.py").read_text(encoding="utf-8")

    assert "def _should_route_through_planner" not in router_source
    assert "def _execute_with_planner" not in router_source
    assert "self.task_planner.plan(" not in router_source
    assert "self.observation_loop.execute_plan(" not in router_source
    assert "RuntimeExecutionEngine" in router_source
    assert "self.router" not in execution_engine_source


def test_router_does_not_own_authority_gate() -> None:
    router_source = Path("intents/router.py").read_text(encoding="utf-8")
    request_handler_source = Path("core/request_handler.py").read_text(encoding="utf-8")

    assert "def _check_authority" not in router_source
    assert "authority_engine.check(" not in router_source
    assert "router._check_authority" not in request_handler_source
    assert Path("core/authority_gate.py").exists()

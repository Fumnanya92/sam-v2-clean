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

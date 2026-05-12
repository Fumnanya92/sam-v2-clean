#!/usr/bin/env python3
"""Integration checks through the real Sam runtime API."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from core.runtime import SamRuntime
from diagnostics.result import SamResult
from llm import OllamaIntentOutput
from projects.registry import ProjectRecord


class _ScriptedModel:
    def __init__(self) -> None:
        self.calls = 0

    def is_available(self) -> bool:
        return True

    def classify_request(self, user_text: str, *args: Any, **kwargs: Any) -> OllamaIntentOutput:
        self.calls += 1
        lowered = user_text.lower()

        if "create a new tic" in lowered:
            return OllamaIntentOutput(
                intent="chat",
                parameters={},
                needs_clarification=True,
                clarification_question="What should the new Tic-Tac-Toe project be named and what type should it be?",
                confidence="high",
                source="test",
            )
        if lowered.strip() == "web app":
            return OllamaIntentOutput(intent="chat", parameters={}, confidence="high", source="test")
        if lowered.strip() == "tictacmay12":
            return OllamaIntentOutput(intent="chat", parameters={}, confidence="high", source="test")
        if "completed any personal projects" in lowered:
            return OllamaIntentOutput(intent="chat", parameters={}, confidence="high", source="test")
        if "need a favour in the estate project" in lowered:
            return OllamaIntentOutput(
                intent="chat",
                parameters={},
                needs_clarification=True,
                clarification_question="What would you like me to do in the estate project?",
                confidence="high",
                source="test",
            )
        if "open it on vscode" in lowered:
            return OllamaIntentOutput(intent="chat", parameters={}, confidence="high", source="test")
        return OllamaIntentOutput(intent="chat", parameters={}, confidence="high", source="test")


def test_runtime_scaffold_followup_resolves_slots_end_to_end() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        runtime = SamRuntime(
            db_path=root / "sam.db",
            memory_path=root / "memory.json",
            session_path=root / "session.json",
            workspace_root=root / "workspace",
        )
        runtime.handler.router.model_client = _ScriptedModel()

        def _fake_scaffold(_request: Any) -> SamResult:
            project_root = root / "workspace" / "projects" / "tictacmay12"
            project_root.mkdir(parents=True, exist_ok=True)
            return SamResult(
                status="success",
                summary="Project scaffolded.",
                next_action="stop",
                metadata={
                    "intent": "scaffold_project",
                    "project_id": "tictacmay12",
                    "name": "tictacmay12",
                    "root_path": str(project_root),
                    "pending_scaffold": {},
                },
            )

        runtime.handler.router.project_scaffolder.scaffold = _fake_scaffold  # type: ignore[method-assign]

        first = runtime.handle_text("create a new Tic-Tac-Toe project")
        assert first.ok
        assert first.metadata.get("intent") == "clarify"

        second = runtime.handle_text("web app")
        assert second.ok
        assert second.metadata.get("intent") == "clarify"
        assert "name" in second.summary.lower()

        third = runtime.handle_text("tictacmay12")
        assert third.ok
        assert third.metadata.get("intent") == "scaffold_project"
        assert third.metadata.get("name") == "tictacmay12"


def test_runtime_completed_projects_query_uses_registry_not_chat_claims() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        runtime = SamRuntime(
            db_path=root / "sam.db",
            memory_path=root / "memory.json",
            session_path=root / "session.json",
            workspace_root=root / "workspace",
        )
        runtime.handler.router.model_client = _ScriptedModel()
        runtime.handler.router.project_registry.register(
            ProjectRecord(
                project_id="estate",
                name="Estate",
                root_path=str(root / "workspace" / "projects" / "Estate"),
            )
        )

        result = runtime.handle_text("sam have you completed any personal projects")

        assert result.ok
        assert result.metadata.get("intent") == "list_projects"


def test_runtime_pronoun_followup_resolves_project_action() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        project_root = root / "workspace" / "projects" / "Estate"
        project_root.mkdir(parents=True, exist_ok=True)
        runtime = SamRuntime(
            db_path=root / "sam.db",
            memory_path=root / "memory.json",
            session_path=root / "session.json",
            workspace_root=root / "workspace",
        )
        runtime.handler.router.model_client = _ScriptedModel()
        runtime.handler.router.project_registry.register(
            ProjectRecord(
                project_id="estate",
                name="estate",
                root_path=str(project_root),
            )
        )
        runtime.handler.router.project_inspector.tools.open_directory = lambda _path: SamResult(  # type: ignore[method-assign]
            status="success",
            summary="Directory opened successfully.",
            next_action="stop",
            metadata={"path": str(project_root)},
        )

        first = runtime.handle_text("sam I need a favour in the estate project")
        second = runtime.handle_text("open it on vscode")

        assert first.ok
        assert second.ok
        assert second.metadata.get("intent") == "open_project_folder"
        assert second.metadata.get("name") == "estate"


def test_runtime_conversation_state_persists_across_restart() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        project_root = root / "workspace" / "projects" / "Estate"
        project_root.mkdir(parents=True, exist_ok=True)
        memory_path = root / "memory.json"
        session_path = root / "session.json"
        db_path = root / "sam.db"

        runtime_one = SamRuntime(
            db_path=db_path,
            memory_path=memory_path,
            session_path=session_path,
            workspace_root=root / "workspace",
        )
        runtime_one.handler.router.model_client = _ScriptedModel()
        runtime_one.handler.router.project_registry.register(
            ProjectRecord(
                project_id="estate",
                name="estate",
                root_path=str(project_root),
            )
        )
        runtime_one.handle_text("sam I need a favour in the estate project")
        runtime_one.shutdown()

        runtime_two = SamRuntime(
            db_path=db_path,
            memory_path=memory_path,
            session_path=session_path,
            workspace_root=root / "workspace",
        )
        runtime_two.handler.router.model_client = _ScriptedModel()
        runtime_two.handler.router.project_registry.register(
            ProjectRecord(
                project_id="estate",
                name="estate",
                root_path=str(project_root),
            )
        )
        runtime_two.handler.router.project_inspector.tools.open_directory = lambda _path: SamResult(  # type: ignore[method-assign]
            status="success",
            summary="Directory opened successfully.",
            next_action="stop",
            metadata={"path": str(project_root)},
        )
        second = runtime_two.handle_text("open it on vscode")

        assert second.ok
        assert second.metadata.get("intent") == "open_project_folder"

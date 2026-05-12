#!/usr/bin/env python3
"""Regression checks for generic autonomous inspection capabilities."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

from intents.router import IntentRouter
from llm import OllamaIntentOutput


class _FakeModel:
    def __init__(
        self,
        intent: str,
        parameters: dict[str, Any] | None = None,
        actions: list[dict[str, Any]] | None = None,
    ) -> None:
        self.intent = intent
        self.parameters = parameters or {}
        self.actions = actions or []
        self.action_index = 0

    def is_available(self) -> bool:
        return True

    def classify_request(self, *args: Any, **kwargs: Any) -> OllamaIntentOutput:
        return OllamaIntentOutput(
            intent=self.intent,
            parameters=dict(self.parameters),
            confidence="high",
            source="test",
        )

    def choose_autonomous_action(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if self.action_index >= len(self.actions):
            return {"action": "final", "answer": "done"}
        action = self.actions[self.action_index]
        self.action_index += 1
        return action


def _router(
    root: Path,
    intent: str,
    parameters: dict[str, Any] | None = None,
    actions: list[dict[str, Any]] | None = None,
) -> IntentRouter:
    return IntentRouter(
        db_path=root / "sam.db",
        workspace_root=root,
        model_client=_FakeModel(intent, parameters, actions),
    )


def test_scan_codebase_patterns_is_generic() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        (root / "app.py").write_text("PROJECT_TOKEN = 'alpha'\n", encoding="utf-8")
        router = _router(root, "scan_codebase_patterns", {"query": str(root), "patterns": ["PROJECT_TOKEN"]})

        result = router.handle("Sam, scan this codebase for PROJECT_TOKEN")

        assert result.ok, result
        assert result.metadata["intent"] == "scan_codebase_patterns"
        assert result.metadata["patterns"] == ["PROJECT_TOKEN"]
        assert result.metadata["match_count"] == 1


def test_check_python_syntax_does_not_write_bytecode() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        (root / "ok.py").write_text("x = 1\n", encoding="utf-8")
        router = _router(root, "check_python_syntax", {"query": str(root)})

        result = router.handle("Sam, compile the project")

        assert result.ok, result
        assert result.metadata["intent"] == "check_python_syntax"
        assert not (root / "__pycache__").exists()


def test_contextual_project_followup_uses_last_project_root() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        router = _router(root, "check_python_syntax", {})
        memory = {"daily_state": {"last_project_root_path": {"value": str(root)}}}

        request = router.parse("check this project for syntax errors", memory)

        assert request.intent == "check_python_syntax"
        assert request.parameters["query"] == str(root)


def test_autonomous_loop_can_tool_then_answer() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        (root / "app.py").write_text("TOKEN = 'alpha'\n", encoding="utf-8")
        router = _router(
            root,
            "autonomous_request",
            {},
            actions=[
                {
                    "action": "tool",
                    "tool": "scan_codebase_patterns",
                    "arguments": {"query": str(root), "patterns": ["TOKEN"]},
                },
                {"action": "final", "answer": "I found TOKEN in the codebase."},
            ],
        )

        result = router.handle("Sam, inspect this codebase for TOKEN and tell me what you find.")

        assert result.ok, result
        assert result.metadata["intent"] == "autonomous_request"
        assert result.metadata["autonomous_steps"] == 1
        assert "I found TOKEN" in result.summary


if __name__ == "__main__":
    try:
        test_scan_codebase_patterns_is_generic()
        test_check_python_syntax_does_not_write_bytecode()
        test_contextual_project_followup_uses_last_project_root()
        test_autonomous_loop_can_tool_then_answer()
    except Exception as exc:
        print(f"FAILED: {exc}")
        raise
    print("autonomy capability tests passed")
    sys.exit(0)

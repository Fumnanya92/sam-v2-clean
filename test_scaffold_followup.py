#!/usr/bin/env python3
"""Regression checks for scaffold follow-up memory handling."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

from intents.router import IntentRouter
from llm import OllamaIntentOutput


class _FakeModel:
    def __init__(self, intent: str = "chat", parameters: dict[str, Any] | None = None) -> None:
        self.intent = intent
        self.parameters = parameters or {}

    def is_available(self) -> bool:
        return True

    def classify_request(self, *args: Any, **kwargs: Any) -> OllamaIntentOutput:
        return OllamaIntentOutput(
            intent=self.intent,
            parameters=dict(self.parameters),
            confidence="high",
            source="test",
        )


def _router(root: Path, intent: str = "chat", parameters: dict[str, Any] | None = None) -> IntentRouter:
    return IntentRouter(
        db_path=root / "sam.db",
        workspace_root=root,
        model_client=_FakeModel(intent, parameters),
    )


def test_web_app_reply_sets_pending_type_and_asks_name() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        router = _router(root, "chat")
        memory = {
            "daily_state": {
                "last_runtime_intent": {"value": "clarify"},
                "last_runtime_summary": {"value": "What should the new project be named and what type of project would you like?"},
            },
            "scaffold_pending": {"value": {}},
        }

        request = router.parse("web app", memory)

        assert request.needs_clarification
        assert request.parameters["pending_scaffold"]["project_type"] == "web app"
        assert "name" in request.clarification_question.lower()


def test_name_reply_after_type_completes_scaffold_request() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        router = _router(root, "chat")
        memory = {
            "daily_state": {
                "last_runtime_intent": {"value": "clarify"},
                "last_runtime_summary": {"value": "What should the new web app project be named?"},
            },
            "scaffold_pending": {"value": {"project_type": "web app"}},
        }

        request = router.parse("tictacmay12", memory)

        assert request.intent == "scaffold_project"
        assert request.parameters["project_type"] == "web app"
        assert request.parameters["name"] == "tictacmay12"


if __name__ == "__main__":
    try:
        test_web_app_reply_sets_pending_type_and_asks_name()
        test_name_reply_after_type_completes_scaffold_request()
    except Exception as exc:
        print(f"FAILED: {exc}")
        raise
    print("scaffold follow-up tests passed")
    sys.exit(0)

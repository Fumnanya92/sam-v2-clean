#!/usr/bin/env python3
"""Regression checks for missing project-folder handling."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

from intents.router import IntentRouter
from llm import OllamaIntentOutput
from projects.registry import ProjectRecord


class _FakeModel:
    def __init__(self, intent: str, parameters: dict[str, Any] | None = None) -> None:
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


def test_open_project_folder_reports_missing_path() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        router = IntentRouter(
            db_path=root / "sam.db",
            workspace_root=root,
            model_client=_FakeModel("open_project_folder", {"query": "tictac game"}),
        )
        router.project_registry.register(
            ProjectRecord(
                project_id="tictac_game",
                name="tictac game",
                root_path=str(root / "sam_v2" / "workspace" / "projects" / "tictac_game"),
            )
        )

        result = router.handle("show me the tictac game")

        assert not result.ok
        assert result.metadata.get("intent") == "open_project_folder"
        assert "folder is missing" in result.summary.lower()


if __name__ == "__main__":
    try:
        test_open_project_folder_reports_missing_path()
    except Exception as exc:
        print(f"FAILED: {exc}")
        raise
    print("project folder validation tests passed")
    sys.exit(0)

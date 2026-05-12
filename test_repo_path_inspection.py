#!/usr/bin/env python3
"""Regression checks for repo/git inspection by explicit local path."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from intents.router import IntentRouter
from llm import OllamaIntentOutput


class _FakeModel:
    def __init__(self, intent: str, parameters: dict[str, Any] | None = None) -> None:
        self.intent = intent
        self.parameters = parameters or {}

    def is_available(self) -> bool:
        return True

    def classify_request(self, *args: Any, **kwargs: Any) -> OllamaIntentOutput:
        return OllamaIntentOutput(
            intent=self.intent,
            parameters=self.parameters,
            confidence="high",
            source="test",
        )


def _make_git_repo(root: Path) -> Path:
    repo = root / "headcount"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "README.md").write_text("# Headcount\n", encoding="utf-8")
    return repo


def test_inspect_repo_recovers_raw_windows_path() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        repo = _make_git_repo(root)
        router = IntentRouter(
            db_path=root / "sam.db",
            workspace_root=root / "workspace",
            model_client=_FakeModel("inspect_repo", {}),
        )

        result = router.handle(f"Sam, inspect this repo {repo} and tell me what state it is in")

        assert result.ok, result
        assert result.metadata["intent"] == "inspect_repo"
        assert Path(result.metadata["root_path"]) == repo


def test_git_state_accepts_path_query() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        repo = _make_git_repo(root)
        router = IntentRouter(
            db_path=root / "sam.db",
            workspace_root=root / "workspace",
            model_client=_FakeModel("inspect_git_state", {"query": str(repo)}),
        )

        result = router.handle(f"Sam, check git status for {repo}")

        assert result.ok, result
        assert result.metadata["intent"] == "inspect_git_state"
        assert Path(result.metadata["repo_root"]) == repo
        assert "README.md" in result.metadata["untracked_files"]


if __name__ == "__main__":
    try:
        test_inspect_repo_recovers_raw_windows_path()
        test_git_state_accepts_path_query()
    except Exception as exc:
        print(f"FAILED: {exc}")
        raise
    print("repo path inspection tests passed")
    sys.exit(0)

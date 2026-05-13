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


class _FailingActionModel(_FakeModel):
    def choose_autonomous_action(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValueError("bad model output")


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
        assert result.metadata["autonomous_steps"] == 2
        assert "TOKEN = 'alpha'" in result.summary


def test_autonomous_loop_falls_back_to_current_project_scan_when_model_action_fails() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        (root / "billing_rules.txt").write_text("The renewal fee grace window ends on day 14.\n", encoding="utf-8")
        router = IntentRouter(
            db_path=root / "sam.db",
            workspace_root=root,
            model_client=_FailingActionModel("autonomous_request", {}),
        )
        memory = {"daily_state": {"last_project_root_path": {"value": str(root)}}}

        result = router.handle("can you check when the renewal fee grace window ends", memory)

        assert result.ok, result
        assert result.metadata["source"] == "autonomous_fallback"
        assert result.metadata["root_path"] == str(root)
        assert result.metadata["match_count"] >= 1
        assert "renewal" in result.summary.lower()
        assert "match(es)" not in result.summary


def test_autonomous_fallback_prefers_explicit_path_and_plain_english() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        stale = root / "OldProject"
        target = root / "TargetProject"
        stale.mkdir()
        target.mkdir()
        (stale / "notes.txt").write_text("renewal fee unknown\n", encoding="utf-8")
        (target / "billing_rules.txt").write_text("The renewal fee grace window ends on day 14.\n", encoding="utf-8")
        router = IntentRouter(
            db_path=root / "sam.db",
            workspace_root=root,
            model_client=_FailingActionModel("autonomous_request", {}),
        )
        memory = {"daily_state": {"last_project_root_path": {"value": str(stale)}}}

        result = router.handle(
            f"need you to check when the renewal fee grace window ends in this app {target}",
            memory,
        )

        assert result.ok, result
        assert result.metadata["root_path"] == str(target)
        assert "users" not in result.metadata["patterns"]
        assert "app" not in result.metadata["patterns"]
        assert "match(es)" not in result.summary
        assert "strongest evidence" in result.summary.lower()
        assert "day 14" in result.summary


def test_autonomous_loop_overrides_irrelevant_model_read_file_choice() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        project = root / "TargetProject"
        project.mkdir()
        (project / "todo.md").write_text("The renewal project review is still open.\n", encoding="utf-8")
        (project / "billing_rules.txt").write_text("The renewal fee grace window ends on day 14.\n", encoding="utf-8")
        router = _router(
            root,
            "autonomous_request",
            {},
            actions=[
                {
                    "action": "tool",
                    "tool": "scan_codebase_patterns",
                    "arguments": {"query": str(project), "patterns": ["renewal", "fee", "grace", "window"]},
                },
                {
                    "action": "tool",
                    "tool": "read_file",
                    "arguments": {"path": str(project / "todo.md"), "max_chars": 10000},
                },
            ],
        )

        result = router.handle(f"when does the renewal fee grace window end? check {project}")

        assert result.ok, result
        assert str(result.metadata.get("path", "")).endswith("billing_rules.txt")
        assert "todo.md" not in result.summary


def test_autonomous_loop_requires_evidence_reads_before_final_answer() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        project = root / "TargetProject"
        project.mkdir()
        (project / "todo.md").write_text("The renewal project review is still open.\n", encoding="utf-8")
        (project / "src").mkdir()
        (project / "src" / "current_rule.ts").write_text(
            "\n".join(
                [
                    "export function dueDateForMonth(month: string) {",
                    "  if (month === '2026-05') {",
                    "    return '2026-06-01';",
                    "  }",
                    "}",
                ]
            ),
            encoding="utf-8",
        )
        (project / "src" / "legacy_rule.ts").write_text(
            "export const legacyRule = '2026-05 can use the end of the current month in older jobs';\n",
            encoding="utf-8",
        )
        router = _router(
            root,
            "autonomous_request",
            {},
            actions=[
                {
                    "action": "tool",
                    "tool": "scan_codebase_patterns",
                    "arguments": {"query": str(project), "patterns": ["renewal", "dueDate", "2026-05"]},
                },
                {"action": "final", "answer": "done"},
                {"action": "final", "answer": "done"},
                {"action": "final", "answer": "done"},
            ],
        )

        result = router.handle(f"when is the renewal due for 2026-05? check {project}")

        assert result.ok, result
        trace = result.metadata["tool_trace"]
        assert trace[0]["tool"] == "scan_codebase_patterns"
        assert trace[1]["tool"] == "read_file_region"
        assert result.metadata["autonomous_steps"] >= 2
        assert "current_rule.ts" in result.summary
        assert "2026-06-01" in result.summary
        assert result.summary != "done"


def test_autonomous_loop_lets_model_repair_stale_project_memory() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        stale = root / "workspace" / "projects" / "old_app"
        target = root / "TargetProject"
        target.mkdir()
        (target / "rules.txt").write_text("The renewal fee grace window ends on day 14.\n", encoding="utf-8")
        router = _router(
            root,
            "autonomous_request",
            {},
            actions=[
                {
                    "action": "tool",
                    "tool": "scan_codebase_patterns",
                    "arguments": {
                        "query": f"{stale}&#x27;",
                        "patterns": ["renewal", "fee", "grace", "window"],
                    },
                },
                {
                    "action": "tool",
                    "tool": "scan_codebase_patterns",
                    "arguments": {
                        "query": str(target),
                        "patterns": ["renewal", "fee", "grace", "window"],
                    },
                },
                {"action": "final", "answer": "done"},
            ],
        )
        memory = {"daily_state": {"last_project_root_path": {"value": str(stale)}}}

        result = router.handle(f"check the renewal fee grace window in TargetProject app", memory)

        assert result.ok, result
        assert result.metadata["root_path"] == str(target)
        assert "day 14" in result.summary
        trace = result.metadata["tool_trace"]
        assert trace[0]["status"] == "failed"
        assert trace[1]["status"] == "success"


def test_autonomous_loop_does_not_stop_on_failed_tool_echo_final() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        missing = root / "missing_project"
        router = _router(
            root,
            "autonomous_request",
            {},
            actions=[
                {
                    "action": "tool",
                    "tool": "scan_codebase_patterns",
                    "arguments": {
                        "query": str(missing),
                        "patterns": ["renewal", "fee"],
                    },
                },
                {"action": "final", "answer": "Project or directory could not be resolved."},
            ],
        )

        result = router.handle("check the renewal fee in the project")

        assert result.ok, result
        assert result.next_action == "ask_user"
        assert result.metadata["intent"] == "clarify"
        assert result.metadata["autonomous_steps"] == 1
        assert result.metadata["tool_trace"][0]["status"] == "failed"


def test_autonomous_tool_sanitizes_html_escaped_paths() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        project = root / "TargetProject"
        project.mkdir()
        router = _router(root, "autonomous_request", {})

        result = router.autonomous_runtime.execute_tool(
            "list_directory",
            {"path": f"{project}&#x27;"},
            router.parse("list it"),
            {},
        )

        assert result.ok, result
        assert result.metadata["path"] == str(project)


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

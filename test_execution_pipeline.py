from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from core.execution_engine import RuntimeExecutionEngine
from core.request_model import IntentRequest
from diagnostics.result import SamResult
from memory.long_term import get_operational_task, get_project_state
from sam.planner.task_planner import PlanAction, TaskPlan


class _ToolExecutor:
    available_tools = {"read_file", "autonomous_request"}

    def get(self, intent: str) -> object | None:
        return object() if intent in self.available_tools else None


class _Planner:
    def plan(self, request: str, context: dict[str, Any]) -> TaskPlan:
        intent = str(context.get("intent", "read_file"))
        return TaskPlan(goal=request, tool_name=intent, payload={"request": context.get("request")}, plan_action=PlanAction.execute)


class _Loop:
    def __init__(self, result: SamResult) -> None:
        self.result = result
        self.calls = 0

    def execute_plan(self, plan: TaskPlan, memory_block: dict[str, Any] | None = None) -> tuple[SamResult, list[Any]]:
        self.calls += 1
        return self.result, []


def _engine(db_path: Path, result: SamResult) -> tuple[RuntimeExecutionEngine, _Loop]:
    loop = _Loop(result)
    return (
        RuntimeExecutionEngine(
            tool_executor=_ToolExecutor(),
            task_planner=_Planner(),
            observation_loop=loop,
            db_path=str(db_path),
        ),
        loop,
    )


def test_pipeline_plans_without_execution_when_requirements_unclear() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        engine, loop = _engine(db_path, SamResult(status="success", summary="should not execute", next_action="stop"))

        result = engine.execute(
            IntentRequest(intent="autonomous_request", raw_text="implement the billing fix in the project"),
            memory_block={},
            legacy_execute=lambda request: SamResult(status="success", summary="legacy", next_action="stop"),
        )

        task = get_operational_task(db_path, result.metadata["task_id"])
        assert result.ok
        assert result.metadata["execution_pipeline"] == "planner_only"
        assert result.next_action == "ask_user"
        assert loop.calls == 0
        assert task is not None
        assert task["state"] == "blocked"


def test_execution_lifecycle_state_transitions_to_completed() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        engine, _loop = _engine(
            db_path,
            SamResult(
                status="success",
                summary="Read file successfully.",
                next_action="stop",
                metadata={"changed_files": ["app.py"]},
            ),
        )

        result = engine.execute(
            IntentRequest(intent="read_file", raw_text="read app.py", parameters={"project_id": "sam", "path": "app.py"}),
            memory_block={},
            legacy_execute=lambda request: SamResult(status="success", summary="legacy", next_action="stop"),
        )
        task = get_operational_task(db_path, result.metadata["task_id"])

        assert result.ok
        assert result.metadata["execution_pipeline"] == "planner_executor_reviewer"
        assert task is not None
        assert task["state"] == "completed"
        assert task["execution_history"]
        assert task["review_history"]


def test_reviewer_catches_missing_checklist_items() -> None:
    engine, _loop = _engine(Path("unused.db"), SamResult(status="success", summary="ok", next_action="stop"))
    review = engine._reviewer_stage(  # noqa: SLF001 - intentional coverage of reviewer policy
        request=IntentRequest(intent="read_file", raw_text="read app.py"),
        planner_output={"execution_plan": ["Inspect requested evidence", "Review result against request"], "required_tests": []},
        executor_result=SamResult(status="success", summary="Only inspected.", next_action="stop"),
        executor_output={"completed_steps": ["Inspect requested evidence"], "changed_files": [], "pending_work": []},
    )

    assert review["ready_for_user"] is False
    assert "Review result against request" in review["missing_items"]


def test_incomplete_work_does_not_report_success() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        engine, _loop = _engine(
            db_path,
            SamResult(status="failed", summary="Could not read file.", error_message="missing file", next_action="ask_user"),
        )

        result = engine.execute(
            IntentRequest(intent="read_file", raw_text="read app.py", parameters={"project_id": "sam", "path": "app.py"}),
            memory_block={},
            legacy_execute=lambda request: SamResult(status="success", summary="legacy", next_action="stop"),
        )

        assert result.status == "partial"
        assert result.next_action == "ask_user"
        assert result.metadata["reviewer_output"]["ready_for_user"] is False


def test_reviewer_triggers_continuation_when_safe() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        engine, _loop = _engine(db_path, SamResult(status="success", summary="ok", next_action="stop"))
        calls = {"count": 0}

        def _executor_stage(*args: Any, **kwargs: Any) -> tuple[SamResult, list[Any], dict[str, Any]]:
            calls["count"] += 1
            if calls["count"] == 1:
                return (
                    SamResult(status="success", summary="Partial work.", next_action="stop"),
                    [],
                    {
                        "status": "success",
                        "summary": "Partial work.",
                        "completed_steps": ["Understand request and confirm execution target"],
                        "failed_steps": [],
                        "changed_files": [],
                        "generated_artifacts": [],
                        "pending_work": ["Review result against request"],
                    },
                )
            return (
                SamResult(status="success", summary="Continuation completed.", next_action="stop"),
                [],
                {
                    "status": "success",
                    "summary": "Continuation completed.",
                    "completed_steps": [
                        "Understand request and confirm execution target",
                        "Inspect requested evidence",
                        "Review result against request",
                    ],
                    "failed_steps": [],
                    "changed_files": [],
                    "generated_artifacts": [],
                    "pending_work": [],
                },
            )

        engine._executor_stage = _executor_stage  # type: ignore[method-assign]
        result = engine.execute(
            IntentRequest(intent="read_file", raw_text="read app.py", parameters={"project_id": "sam", "path": "app.py"}),
            memory_block={},
            legacy_execute=lambda request: SamResult(status="success", summary="legacy", next_action="stop"),
        )
        task = get_operational_task(db_path, result.metadata["task_id"])

        assert result.ok
        assert calls["count"] == 2
        assert task is not None
        assert task["auto_corrections"]


def test_conversational_chat_bypasses_pipeline() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        engine, loop = _engine(db_path, SamResult(status="success", summary="should not execute", next_action="stop"))

        result = engine.execute(
            IntentRequest(intent="chat", raw_text="hello"),
            memory_block={},
            legacy_execute=lambda request: SamResult(status="success", summary="hi", next_action="ask_user", metadata={"intent": "chat"}),
        )

        assert result.ok
        assert result.metadata.get("execution_pipeline") is None
        assert loop.calls == 0


def test_project_state_updates_after_completion() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"
        engine, _loop = _engine(
            db_path,
            SamResult(status="success", summary="Implementation completed.", next_action="stop", metadata={"changed_files": ["app.py"]}),
        )

        result = engine.execute(
            IntentRequest(intent="read_file", raw_text="read app.py", parameters={"project_id": "sam", "path": "app.py"}),
            memory_block={},
            legacy_execute=lambda request: SamResult(status="success", summary="legacy", next_action="stop"),
        )
        state = get_project_state(db_path, "sam")

        assert result.ok
        assert state is not None
        assert state["implementation_status"] == "completed"
        assert state["current_focus"] == "read app.py"

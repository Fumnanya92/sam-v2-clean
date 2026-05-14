from __future__ import annotations

from pathlib import Path

from coding_models import CodingModelManager
from coding_models.manager import _coding_answer_summary
from core.contextual_resolver import ContextualRequestResolver
from core.request_model import IntentRequest
from intents.router import IntentRouter
from sam.planner.task_planner import PlanAction, TaskPlanner


def _memory(active: str = "codex") -> dict[str, object]:
    return {"coding_model": {"value": {"active_coding_model": active}}}


def test_planner_does_not_delegate_conversation() -> None:
    planner = TaskPlanner(
        work_kind_classifier=lambda _text, _context: {
            "work_kind": "conversational",
            "requires_repo_code": False,
            "confidence": "high",
            "reason": "simple conversation",
        }
    )
    request = IntentRequest(intent="chat", raw_text="how are you")
    plan = planner.plan(
        "how are you",
        context={
            "request": request,
            "intent": request.intent,
            "memory": _memory(),
            "available_tools": ["delegate_coding_task", "assistant.respond"],
        },
    )
    assert plan.tool_name != "delegate_coding_task"


def test_planner_delegates_only_confirmed_repo_code_work() -> None:
    planner = TaskPlanner(
        work_kind_classifier=lambda _text, _context: {
            "work_kind": "repo_code",
            "requires_repo_code": True,
            "confidence": "medium",
            "reason": "needs repository inspection",
            "project_query": "example",
        }
    )
    request = IntentRequest(intent="autonomous_request", raw_text="inspect this repo and fix the failing tests")
    plan = planner.plan(
        "inspect this repo and fix the failing tests",
        context={
            "request": request,
            "intent": request.intent,
            "memory": _memory(),
            "available_tools": ["delegate_coding_task", "autonomous_request"],
        },
    )
    assert plan.tool_name == "delegate_coding_task"
    assert plan.plan_action == PlanAction.delegate
    assert plan.payload["coding_gate"]["work_kind"] == "repo_code"


def test_active_coding_model_delegates_autonomous_investigation_without_classifier_gate() -> None:
    planner = TaskPlanner(
        work_kind_classifier=lambda _text, _context: {
            "work_kind": "conversational",
            "requires_repo_code": False,
            "confidence": "low",
            "reason": "classifier missed the project evidence need",
        }
    )
    request = IntentRequest(
        intent="autonomous_request",
        raw_text="when is may levies due, check the resident docs and let me know",
    )
    plan = planner.plan(
        request.raw_text,
        context={
            "request": request,
            "intent": request.intent,
            "memory": _memory(),
            "available_tools": ["delegate_coding_task", "autonomous_request"],
        },
    )
    assert plan.tool_name == "delegate_coding_task"
    assert plan.plan_action == PlanAction.delegate
    assert plan.payload["coding_gate"]["work_kind"] == "autonomous_request"


def test_coding_summary_prefers_answer_over_verification_tail() -> None:
    summary = _coding_answer_summary(
        [
            "codex",
            "Answer: for most existing residents, the May 2026 levy is due June 1, 2026.",
            "I found two due-date paths.",
            "Inspected:",
            "- lib/core/services/firebase_sync_service.dart",
            "Verification run: searched levy due-date logic with rg.",
            "Tool succeeded: Verification run: searched levy due-date logic with rg.",
            "finished.",
        ]
    )
    assert "May 2026 levy is due June 1, 2026" in summary
    assert not summary.startswith("Verification run")


def test_corrected_repo_path_replays_prior_delegated_goal() -> None:
    memory = {
        "coding_model": {"value": {"active_coding_model": "codex"}},
        "conversation": {
            "recent_requests": {
                "value": [
                    {
                        "request": "in the Estate app when is may levies due",
                        "intent": "delegate_coding_task",
                        "status": "success",
                        "summary": "Verification run: searched the wrong repo.",
                        "root_path": r"C:\Users\DELL.COM\Desktop\Darey\headcount",
                    }
                ]
            }
        },
    }
    request = IntentRequest(
        intent="inspect_repo",
        raw_text=r"thats the wrong repo this is the correct repo (.venv) C:\Users\DELL.COM\Desktop\Darey\Estate",
        parameters={"query": r"C:\Users\DELL.COM\Desktop\Darey\Estate"},
    )
    resolved = ContextualRequestResolver().apply(
        text=request.raw_text,
        request=request,
        memory_block=memory,
    )
    assert resolved.intent == "autonomous_request"
    assert resolved.parameters["query"] == r"C:\Users\DELL.COM\Desktop\Darey\Estate"
    assert resolved.parameters["goal"] == "in the Estate app when is may levies due"


def test_local_coding_model_disables_delegation() -> None:
    planner = TaskPlanner(
        work_kind_classifier=lambda _text, _context: {
            "work_kind": "repo_code",
            "requires_repo_code": True,
            "confidence": "high",
        }
    )
    request = IntentRequest(intent="autonomous_request", raw_text="fix this repo")
    plan = planner.plan(
        "fix this repo",
        context={
            "request": request,
            "intent": request.intent,
            "memory": _memory("local"),
            "available_tools": ["delegate_coding_task", "autonomous_request"],
        },
    )
    assert plan.tool_name == "autonomous_request"


def test_router_parses_coding_model_switches(tmp_path: Path) -> None:
    router = IntentRouter(db_path=tmp_path / "sam.db", workspace_root=tmp_path / "workspace")
    parsed = router.parse("/codex", {})
    assert parsed.intent == "set_coding_model"
    assert parsed.parameters["model"] == "codex"

    parsed = router.parse("/coding-model", {})
    assert parsed.intent == "show_coding_model"


def test_coding_model_manager_persists_active_model(tmp_path: Path) -> None:
    manager = CodingModelManager(tmp_path / "coding_models.json")
    manager.providers["codex"].executable = lambda: "codex"  # type: ignore[method-assign]
    result = manager.set_active_model("codex")
    assert result.ok
    assert CodingModelManager(tmp_path / "coding_models.json").active_model() == "codex"

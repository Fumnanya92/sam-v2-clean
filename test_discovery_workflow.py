#!/usr/bin/env python3
"""Regression checks for generic project/folder discovery workflow."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

from diagnostics.result import SamResult
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
        workspace_root=root / "workspace",
        model_client=_FakeModel(intent, parameters),
    )


def _memory_from(result: SamResult) -> dict[str, Any]:
    return {"discovery": {"value": result.metadata["discovery_state"]}}


def _nested_memory_from(result: SamResult) -> dict[str, Any]:
    state = result.metadata["discovery_state"]
    return {"discovery": {key: {"value": value} for key, value in state.items()}}


def _corrupt_string_memory_from(result: SamResult) -> dict[str, Any]:
    state = result.metadata["discovery_state"]
    return {
        "discovery": {
            "value": {
                key: str({"value": value}) if isinstance(value, str) else value
                for key, value in state.items()
            }
        }
    }


def _candidate_names(result: SamResult) -> list[str]:
    return [Path(item).name for item in result.metadata.get("candidates", [])]


def _trace_labels(result: SamResult) -> list[str]:
    return [str(item.get("label", "")) for item in result.metadata.get("execution_trace", []) if isinstance(item, dict)]


def test_discovery_asks_for_root_before_opening() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        router = _router(root, "open_folder", {"query": "attendance app"})

        with patch("workflows.discovery.Path.cwd", return_value=root / "workspace"):
            result = router.handle("can you find the attendance app")

        assert result.ok, result
        assert result.metadata["workflow"] == "discovery"
        assert result.metadata["workflow_action"] == "ask_for_root"
        assert result.metadata["intent"] != "open_folder"
        assert result.next_action == "ask_user"
        assert "Goal detected" in _trace_labels(result)
        assert "Keyword extracted" in _trace_labels(result)


def test_new_find_request_resets_stale_active_discovery() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        router = _router(root, "chat")
        old = SamResult(
            status="success",
            summary="old",
            metadata={
                "discovery_state": {
                    "active_goal": "find folder matching estate",
                    "search_keyword": "estate",
                    "search_root": str(root),
                    "candidates": [],
                    "last_listing": [str(root / "Estate")],
                    "selected_candidate": "",
                }
            },
        )

        with patch("workflows.discovery.Path.cwd", return_value=root / "workspace"):
            result = router.handle("can you find the attendance app", _memory_from(old))

        assert result.ok, result
        assert result.metadata["workflow_action"] == "ask_for_root"
        assert result.metadata["search_keyword"] == "attendance"
        assert result.metadata["search_root"] == ""


def test_fresh_find_uses_inferred_workspace_parent_before_asking() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        (root / "attendance-app").mkdir()
        (root / "Guest-Welcome-attendance-app").mkdir()
        router = _router(root, "chat")

        with patch("workflows.discovery.Path.cwd", return_value=root / "workspace"):
            result = router.handle("can you find the attendance app")

        assert result.ok, result
        assert result.metadata["workflow_action"] == "filter_candidates"
        assert result.metadata["search_keyword"] == "attendance"
        assert _candidate_names(result) == ["Guest-Welcome-attendance-app", "attendance-app"]
        assert "Attempting path resolution" in _trace_labels(result)


def test_discovery_filters_candidates_after_root() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        (root / "attendance-app").mkdir()
        (root / "Guest-Welcome-attendance-app").mkdir()
        (root / "Estate").mkdir()
        router = _router(root, "chat")
        first = router.handle("can you find the attendance app")

        result = router.handle(f"check here {root}", _memory_from(first))

        assert result.ok, result
        assert result.metadata["workflow_action"] == "filter_candidates"
        assert result.metadata["intent"] != "open_folder"
        assert _candidate_names(result) == ["Guest-Welcome-attendance-app", "attendance-app"]
        assert "Path resolved" in _trace_labels(result)
        assert "Tool selected" in _trace_labels(result)
        assert "Filtering" in _trace_labels(result)
        assert "Matches found" in _trace_labels(result)


def test_discovery_resolves_named_folder_on_desktop() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        home = Path(tmp)
        root = home / "Desktop" / "Darey"
        root.mkdir(parents=True)
        (root / "attendance-app").mkdir()
        router = _router(root, "chat")
        first = router.handle("can you find the attendance app")

        with patch("workflows.discovery.Path.home", return_value=home):
            result = router.handle("check the Darey folder in desktop", _memory_from(first))

        assert result.ok, result
        assert result.metadata["search_root"] == str(root)
        assert _candidate_names(result) == ["attendance-app"]


def test_discovery_filters_previous_listing_without_opening() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        (root / "attendance-app").mkdir()
        (root / "Guest-Welcome-attendance-app").mkdir()
        (root / "Estate").mkdir()
        router = _router(root, "open_folder", {"query": "attendance-app"})
        first = router.handle("can you find the attendance app")
        second = router.handle(f"check here {root}", _memory_from(first))

        result = router.handle("show me only ones with attendance", _memory_from(second))

        assert result.ok, result
        assert result.metadata["workflow_action"] == "filter_candidates"
        assert result.metadata["intent"] != "open_folder"
        assert _candidate_names(result) == ["Guest-Welcome-attendance-app", "attendance-app"]


def test_discovery_reads_nested_memory_values_from_runtime_storage() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        (root / "attendance-app").mkdir()
        router = _router(root, "chat")
        first = router.handle("can you find the attendance app")

        result = router.handle(str(root), _nested_memory_from(first))

        assert result.ok, result
        assert result.metadata["search_keyword"] == "attendance"
        assert _candidate_names(result) == ["attendance-app"]


def test_discovery_repairs_corrupt_stringified_memory_values() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        (root / "attendance-app").mkdir()
        router = _router(root, "chat")
        first = router.handle("can you find the attendance app")

        result = router.handle(str(root), _corrupt_string_memory_from(first))

        assert result.ok, result
        assert result.metadata["search_keyword"] == "attendance"
        assert _candidate_names(result) == ["attendance-app"]


def test_discovery_rechecks_original_keyword_when_user_challenges_result() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        (root / "attendance-app").mkdir()
        router = _router(root, "chat")
        first = router.handle("can you find the attendance app")
        second = router.handle(str(root), _memory_from(first))

        result = router.handle("are you sure because I can see the attendance-app staring at me", _memory_from(second))

        assert result.ok, result
        assert result.metadata["search_keyword"] == "attendance"
        assert _candidate_names(result) == ["attendance-app"]


def test_discovery_opens_selected_candidate_only_on_explicit_open() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        (root / "attendance-app").mkdir()
        (root / "Guest-Welcome-attendance-app").mkdir()
        router = _router(root, "chat")
        opened: list[str] = []

        def fake_open(path: str | Path) -> SamResult:
            opened.append(str(path))
            return SamResult(status="success", summary="Directory opened successfully.", metadata={"path": str(path)})

        router.project_inspector.tools.open_directory = fake_open  # type: ignore[method-assign]
        first = router.handle("can you find the attendance app")
        second = router.handle(f"check here {root}", _memory_from(first))

        result = router.handle("open the first one", _memory_from(second))

        assert result.ok, result
        assert result.metadata["workflow_action"] == "open_candidate"
        assert result.metadata["intent"] == "open_folder"
        assert opened == [str(root / "Guest-Welcome-attendance-app")]
        assert "Candidate selected" in _trace_labels(result)


def test_numeric_reply_selects_candidate_without_re_filtering() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        first_candidate = root / "Guest-Welcome-attendance-app"
        second_candidate = root / "attendance-app"
        first_candidate.mkdir()
        second_candidate.mkdir()
        (second_candidate / "README.md").write_text("hello\n", encoding="utf-8")
        router = _router(root, "chat")
        first = router.handle("can you find the attendance app")
        second = router.handle(f"check here {root}", _memory_from(first))

        result = router.handle("2", _memory_from(second))

        assert result.ok, result
        assert result.metadata["workflow_action"] == "inspect_candidate"
        assert result.metadata["path"] == str(second_candidate)
        assert result.metadata["selected_candidate"] == str(second_candidate)


def test_open_confirmation_uses_single_candidate() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        target = root / "ChipIn"
        target.mkdir()
        router = _router(root, "chat")
        opened: list[str] = []

        def fake_open(path: str | Path) -> SamResult:
            opened.append(str(path))
            return SamResult(status="success", summary="Directory opened successfully.", metadata={"path": str(path)})

        router.project_inspector.tools.open_directory = fake_open  # type: ignore[method-assign]
        first = router.handle("please find the ChipIn project")

        result = router.handle("yes thats it open it", _memory_from(first))

        assert result.ok, result
        assert result.metadata["workflow_action"] == "open_candidate"
        assert opened == [str(target)]


def test_discovery_is_generic_for_estate() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        (root / "Estate").mkdir()
        (root / "estate-mobile").mkdir()
        (root / "attendance-app").mkdir()
        router = _router(root, "chat")
        first = router.handle("can you find the estate app")

        result = router.handle(f"check here {root}", _memory_from(first))

        assert result.ok, result
        assert _candidate_names(result) == ["Estate", "estate-mobile"]


def test_discovery_is_generic_for_bulkbay() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        (root / "BulkBay").mkdir()
        (root / "bulkbay-api").mkdir()
        (root / "headcount").mkdir()
        router = _router(root, "chat")
        first = router.handle("can you find the bulkbay project")

        result = router.handle(f"check here {root}", _memory_from(first))

        assert result.ok, result
        assert _candidate_names(result) == ["BulkBay", "bulkbay-api"]


def test_active_discovery_overrides_bad_open_folder_intent() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        (root / "invoice-web").mkdir()
        (root / "invoice-mobile").mkdir()
        (root / "Estate").mkdir()
        router = _router(root, "open_folder", {"query": "invoice-web"})
        first = router.handle("can you find the invoice app")
        second = router.handle(f"check here {root}", _memory_from(first))

        result = router.handle("show me only ones with invoice", _memory_from(second))

        assert result.ok, result
        assert result.metadata["workflow_action"] == "filter_candidates"
        assert result.metadata["intent"] != "open_folder"
        assert _candidate_names(result) == ["invoice-mobile", "invoice-web"]


def test_non_discovery_message_does_not_get_hijacked_by_stale_discovery() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        router = _router(root, "chat")
        stale = SamResult(
            status="success",
            summary="stale",
            metadata={
                "discovery_state": {
                    "active_goal": "find folder matching chipin",
                    "search_keyword": "chipin",
                    "search_root": str(root),
                    "candidates": [str(root / "ChipIn")],
                    "last_listing": [str(root / "ChipIn")],
                    "selected_candidate": "",
                }
            },
        )

        result = router.handle("sam I need a favour in the estate project", _memory_from(stale))

        assert result.ok, result
        assert result.metadata.get("intent") != "discovery_workflow"


if __name__ == "__main__":
    try:
        test_discovery_asks_for_root_before_opening()
        test_new_find_request_resets_stale_active_discovery()
        test_fresh_find_uses_inferred_workspace_parent_before_asking()
        test_discovery_filters_candidates_after_root()
        test_discovery_resolves_named_folder_on_desktop()
        test_discovery_filters_previous_listing_without_opening()
        test_discovery_reads_nested_memory_values_from_runtime_storage()
        test_discovery_repairs_corrupt_stringified_memory_values()
        test_discovery_rechecks_original_keyword_when_user_challenges_result()
        test_discovery_opens_selected_candidate_only_on_explicit_open()
        test_numeric_reply_selects_candidate_without_re_filtering()
        test_open_confirmation_uses_single_candidate()
        test_discovery_is_generic_for_estate()
        test_discovery_is_generic_for_bulkbay()
        test_active_discovery_overrides_bad_open_folder_intent()
        test_non_discovery_message_does_not_get_hijacked_by_stale_discovery()
    except Exception as exc:
        print(f"FAILED: {exc}")
        raise
    print("discovery workflow tests passed")
    sys.exit(0)

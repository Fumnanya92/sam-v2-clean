#!/usr/bin/env python3
"""Regression checks for the generic execution trace contract."""

from __future__ import annotations

import sys

from diagnostics.result import SamResult
from diagnostics.trace import ensure_trace
from sam.executor.tool_executor import ToolExecutor


def test_tool_executor_attaches_generic_execution_trace() -> None:
    executor = ToolExecutor()
    executor.register(
        "demo_tool",
        lambda _payload: SamResult(status="success", summary="Demo completed.", next_action="stop"),
    )

    result = executor.execute("demo_tool", {"value": 1})

    trace = result.metadata.get("execution_trace", [])
    assert isinstance(trace, list)
    assert any(step.get("label") == "Tool selected" and step.get("tool") == "demo_tool" for step in trace)
    assert any(step.get("label") == "Observation" and step.get("observation") == "Demo completed." for step in trace)


def test_ensure_trace_adds_failure_reason_for_plain_result() -> None:
    result = ensure_trace(
        SamResult(
            status="failed",
            summary="Could not resolve folder.",
            error_message="Requested directory could not be resolved.",
            metadata={"intent": "list_directory"},
        )
    )

    trace = result.metadata.get("execution_trace", [])
    assert any(step.get("label") == "Tool selected" for step in trace)
    assert any(step.get("label") == "Failure observed" for step in trace)


if __name__ == "__main__":
    try:
        test_tool_executor_attaches_generic_execution_trace()
        test_ensure_trace_adds_failure_reason_for_plain_result()
    except Exception as exc:
        print(f"FAILED: {exc}")
        raise
    print("execution trace tests passed")
    sys.exit(0)

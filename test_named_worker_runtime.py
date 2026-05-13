from __future__ import annotations

from sam.executor.tool_executor import ToolExecutor
from sam.executor.worker_runtime import WorkerCentricExecutor
from diagnostics.result import SamResult
from workers.monitor import WorkerMonitor
from workers.names import WORKER_IDENTITIES, resolve_worker_identity


def test_operational_worker_identities_are_stable() -> None:
    assert {identity.name for identity in WORKER_IDENTITIES.values()} == {
        "Atlas",
        "Forge",
        "Echo",
        "Sentinel",
        "Nova",
        "Pulse",
        "Vector",
        "Orbit",
    }
    assert resolve_worker_identity(tool_name="inspect_repo").name == "Atlas"
    assert resolve_worker_identity(tool_name="discovery_workflow").name == "Nova"
    assert resolve_worker_identity(tool_name="check_python_syntax").name == "Sentinel"
    assert resolve_worker_identity(tool_name="replace_codebase_patterns").name == "Forge"


def test_worker_executor_attaches_operational_identity_metadata() -> None:
    base = ToolExecutor()
    base.register(
        "inspect_repo",
        lambda _payload: SamResult(status="success", summary="inspected", next_action="stop"),
        action_category="read_data",
    )
    monitor = WorkerMonitor()
    executor = WorkerCentricExecutor(base, monitor)

    result = executor.execute_with_tracking("inspect_repo", {"request": "inspect repo"})
    tasks = monitor.list_tasks()

    assert result.ok
    assert result.metadata["worker_name"] == "Atlas"
    assert result.metadata["worker_role"] == "research"
    assert result.metadata["worker_responsibility"] == "repo research and investigation"
    assert len(tasks) == 1
    assert tasks[0].worker_name == "Atlas"
    assert tasks[0].worker_role == "research"
    assert tasks[0].responsibility == "repo research and investigation"

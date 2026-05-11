"""Simple sequential worker queue for Sam v2."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult

from .tooling import CommandSpec, ToolingWorker


@dataclass
class QueuedJob:
    job_id: str
    spec: CommandSpec


class WorkerQueue:
    def __init__(self, worker: ToolingWorker) -> None:
        self.worker = worker
        self._queue: deque[QueuedJob] = deque()
        self._counter = 0

    def submit(self, spec: CommandSpec) -> SamResult:
        self._counter += 1
        job = QueuedJob(job_id=f"job-{self._counter}", spec=spec)
        self._queue.append(job)
        return SamResult(
            status="success",
            summary="Worker job queued.",
            next_action="stop",
            metadata={"job_id": job.job_id, "queue_depth": len(self._queue)},
        )

    def run_next(self) -> SamResult:
        if not self._queue:
            return SamResult(
                status="failed",
                summary="Worker queue is empty.",
                error_type=ErrorType.TOOL_FAILED,
                error_message="no queued jobs",
                next_action="stop",
            )

        job = self._queue.popleft()
        result, task = self.worker.execute(job.spec)
        result.metadata.setdefault("job_id", job.job_id)
        result.metadata.setdefault("queue_depth", len(self._queue))
        result.metadata.setdefault("task_status", task.status)
        return result

    def depth(self) -> int:
        return len(self._queue)

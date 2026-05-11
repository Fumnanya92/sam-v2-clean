"""Worker task monitor for Sam v2."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable
from uuid import uuid4


@dataclass
class WorkerTask:
    task_id: str
    name: str
    worker_type: str
    worker_name: str
    description: str
    status: str = "pending"
    output_lines: list[str] = field(default_factory=list)
    error_message: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    ended_at: float | None = None

    @property
    def elapsed_seconds(self) -> int:
        end = self.ended_at or time.time()
        start = self.started_at or self.created_at
        return int(end - start)


class WorkerMonitor:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, WorkerTask] = {}
        self._callbacks: list[Callable[[WorkerTask], None]] = []

    def create_task(self, *, name: str, worker_type: str, worker_name: str, description: str) -> WorkerTask:
        task = WorkerTask(
            task_id=str(uuid4())[:8],
            name=name,
            worker_type=worker_type,
            worker_name=worker_name,
            description=description,
        )
        with self._lock:
            self._tasks[task.task_id] = task
        self._notify(task)
        return task

    def mark_running(self, task_id: str) -> None:
        self._update(task_id, status="running", started_at=time.time())

    def mark_done(self, task_id: str) -> None:
        self._update(task_id, status="done", ended_at=time.time())

    def mark_failed(self, task_id: str, error_message: str) -> None:
        self._update(task_id, status="failed", error_message=error_message, ended_at=time.time())

    def mark_needs_approval(self, task_id: str, reason: str) -> None:
        self._update(task_id, status="needs_approval", error_message=reason)

    def append_output(self, task_id: str, line: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.output_lines.append(line)
        self._notify(task)

    def get_task(self, task_id: str) -> WorkerTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return None if task is None else WorkerTask(**task.__dict__)

    def list_tasks(self) -> list[WorkerTask]:
        with self._lock:
            return [WorkerTask(**task.__dict__) for task in self._tasks.values()]

    def subscribe(self, callback: Callable[[WorkerTask], None]) -> None:
        self._callbacks.append(callback)

    def _update(self, task_id: str, **changes: object) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            for key, value in changes.items():
                setattr(task, key, value)
        self._notify(task)

    def _notify(self, task: WorkerTask) -> None:
        for callback in list(self._callbacks):
            try:
                callback(task)
            except Exception:
                pass


worker_monitor = WorkerMonitor()

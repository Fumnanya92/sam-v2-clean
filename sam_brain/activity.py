"""
Thread-safe activity feed for Sam's brain.

Brain workers emit lines here. The UI polls this feed on the main thread
and streams them into the LiveActivityCard.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class ActivityLine:
    worker: str
    message: str
    at: float = field(default_factory=time.time)


class ActivityFeed:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._lines: list[ActivityLine] = []

    def emit(self, worker: str, message: str) -> None:
        with self._lock:
            self._lines.append(ActivityLine(worker=worker, message=message))

    def drain(self, cursor: int) -> tuple[list[ActivityLine], int]:
        """Return new lines since cursor and the new cursor position."""
        with self._lock:
            new = self._lines[cursor:]
            return list(new), len(self._lines)

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()


# Module-level singleton — brain writes, UI reads
feed = ActivityFeed()

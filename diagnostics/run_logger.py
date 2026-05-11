"""Basic run logger for Sam v2."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .log_manager import ensure_log_directories

LOG_DIR = Path("sam_v2/logs/runs")
ensure_log_directories()


class RunLogger:
    def __init__(self, task: str):
        ensure_log_directories()
        self.run_id = str(uuid4())
        self.task = task
        self.started_at = datetime.utcnow().isoformat()
        self.log_file = LOG_DIR / f"{self.run_id}.jsonl"

    def log(self, event: str, data: dict | None = None):
        ensure_log_directories()
        payload = {
            "timestamp": datetime.utcnow().isoformat(),
            "run_id": self.run_id,
            "event": event,
            "data": data or {},
        }

        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")

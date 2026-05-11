"""Structured action, error, and summary reporting for Sam v2."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .error_types import ErrorType
from .log_manager import ensure_log_directories
from .result import SamResult

ACTION_LOG_DIR = Path("sam_v2/logs/actions")
ERROR_LOG_DIR = Path("sam_v2/logs/errors")
SUMMARY_LOG_DIR = Path("sam_v2/logs/summaries")

ensure_log_directories()


def _utc_now() -> str:
    return datetime.utcnow().isoformat()


class ActionLogger:
    def __init__(self, scope: str, correlation_id: str | None = None) -> None:
        ensure_log_directories()
        self.scope = scope
        self.correlation_id = correlation_id or str(uuid4())
        safe_scope = scope.replace(" ", "_")
        self.log_file = ACTION_LOG_DIR / f"{safe_scope}_{self.correlation_id}.jsonl"

    def log(self, action: str, *, status: str, data: dict | None = None) -> None:
        ensure_log_directories()
        payload = {
            "timestamp": _utc_now(),
            "scope": self.scope,
            "correlation_id": self.correlation_id,
            "action": action,
            "status": status,
            "data": data or {},
        }
        with open(self.log_file, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")


class ErrorLogger:
    def __init__(self, scope: str) -> None:
        ensure_log_directories()
        self.scope = scope
        safe_scope = scope.replace(" ", "_")
        self.log_file = ERROR_LOG_DIR / f"{safe_scope}.jsonl"

    def log(
        self,
        *,
        event: str,
        error_type: ErrorType | None,
        error_message: str,
        metadata: dict | None = None,
    ) -> None:
        ensure_log_directories()
        payload = {
            "timestamp": _utc_now(),
            "scope": self.scope,
            "event": event,
            "error_type": error_type.value if error_type else ErrorType.UNKNOWN_ERROR.value,
            "error_message": error_message,
            "metadata": metadata or {},
        }
        with open(self.log_file, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")


class SummaryLogger:
    def __init__(self, scope: str, correlation_id: str | None = None) -> None:
        ensure_log_directories()
        self.scope = scope
        self.correlation_id = correlation_id or str(uuid4())

    def write(self, result: SamResult, *, metadata: dict | None = None) -> Path:
        ensure_log_directories()
        safe_scope = self.scope.replace(" ", "_")
        log_file = SUMMARY_LOG_DIR / f"{safe_scope}_{self.correlation_id}.json"
        payload = {
            "timestamp": _utc_now(),
            "scope": self.scope,
            "correlation_id": self.correlation_id,
            "result": {
                "status": result.status,
                "summary": result.summary,
                "error_type": result.error_type.value if result.error_type else None,
                "error_message": result.error_message,
                "next_action": result.next_action,
                "metadata": result.metadata,
            },
            "metadata": metadata or {},
        }
        log_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return log_file

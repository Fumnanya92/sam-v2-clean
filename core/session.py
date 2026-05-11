"""Runtime session state for Sam v2."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from sam_v2.diagnostics.result import SamResult


@dataclass
class RuntimeSession:
    session_id: str = field(default_factory=lambda: str(uuid4()))
    started_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    mode: str = "normal"
    request_count: int = 0
    successful_requests: int = 0
    blocked_requests: int = 0
    failed_requests: int = 0
    last_intent: str = ""
    last_status: str = ""
    last_summary: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)
    max_history: int = 20

    def record(self, user_text: str, result: SamResult) -> None:
        self.request_count += 1
        if result.ok:
            self.successful_requests += 1
        elif result.status in {"blocked", "needs_approval"}:
            self.blocked_requests += 1
        else:
            self.failed_requests += 1

        self.last_intent = str(result.metadata.get("intent", ""))
        self.last_status = result.status
        self.last_summary = result.summary
        self.history.append(
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "user_text": user_text,
                "intent": self.last_intent,
                "status": result.status,
                "summary": result.summary,
            }
        )
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history :]

    def to_state(self) -> dict[str, Any]:
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "session_id": self.session_id,
            "started_at": self.started_at,
            "mode": self.mode,
            "request_count": self.request_count,
            "successful_requests": self.successful_requests,
            "blocked_requests": self.blocked_requests,
            "failed_requests": self.failed_requests,
            "last_intent": self.last_intent,
            "last_status": self.last_status,
            "last_summary": self.last_summary,
            "history": self.history,
        }

"""Last-session persistence helpers for Sam v2."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sam_v2.diagnostics.error_types import ErrorType
from sam_v2.diagnostics.result import SamResult


def save_session_state(session_path: str | Path, state: dict[str, Any]) -> SamResult:
    """Persist session context to disk."""
    if not isinstance(state, dict):
        return SamResult(
            status="failed",
            summary="Session state must be a dictionary.",
            error_type=ErrorType.TOOL_FAILED,
            error_message=f"received={type(state).__name__}",
            next_action="ask_user",
        )

    path = Path(session_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return SamResult(
            status="success",
            summary="Session state saved.",
            next_action="stop",
            metadata={"path": str(path)},
        )
    except OSError as exc:
        return SamResult(
            status="failed",
            summary="Failed to save session state.",
            error_type=ErrorType.FILE_ACCESS_ERROR,
            error_message=str(exc),
            next_action="retry",
            metadata={"path": str(path)},
        )


def load_last_session(session_path: str | Path) -> tuple[SamResult, dict[str, Any] | None]:
    """Load the last session dict, if present."""
    path = Path(session_path)
    if not path.exists():
        return (
            SamResult(
                status="success",
                summary="No previous session found.",
                next_action="stop",
                metadata={"path": str(path)},
            ),
            None,
        )

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return (
                SamResult(
                    status="failed",
                    summary="Session state did not contain an object.",
                    error_type=ErrorType.FILE_ACCESS_ERROR,
                    error_message="Expected top-level JSON object.",
                    next_action="ask_user",
                    metadata={"path": str(path)},
                ),
                None,
            )
        return (
            SamResult(
                status="success",
                summary="Session state loaded.",
                next_action="stop",
                metadata={"path": str(path)},
            ),
            data,
        )
    except json.JSONDecodeError as exc:
        return (
            SamResult(
                status="failed",
                summary="Session state file is invalid JSON.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="ask_user",
                metadata={"path": str(path)},
            ),
            None,
        )
    except OSError as exc:
        return (
            SamResult(
                status="failed",
                summary="Failed to load session state.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="retry",
                metadata={"path": str(path)},
            ),
            None,
        )


def is_session_recent(session: dict[str, Any], max_hours: int = 20) -> bool:
    """Return True when a session timestamp is recent enough to reference."""
    try:
        timestamp = datetime.fromisoformat(str(session.get("timestamp", "")))
        delta = datetime.now() - timestamp
        return delta.total_seconds() < max_hours * 3600
    except Exception:
        return False

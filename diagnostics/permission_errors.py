"""Helpers for recognizing OS permission failures."""

from __future__ import annotations

from typing import Any

from diagnostics.error_types import ErrorType


def is_permission_error(exc: BaseException | str | None) -> bool:
    if isinstance(exc, PermissionError):
        return True
    text = str(exc or "").lower()
    return any(
        marker in text
        for marker in (
            "access is denied",
            "access denied",
            "permission denied",
            "unauthorized",
            "winerror 5",
            "errno 13",
        )
    )


def file_error_type(exc: BaseException | str | None) -> ErrorType:
    return ErrorType.MISSING_PERMISSION if is_permission_error(exc) else ErrorType.FILE_ACCESS_ERROR


def permission_metadata(path: str, exc: BaseException | str | None, **extra: Any) -> dict[str, Any]:
    metadata = {
        "path": path,
        "recoverable": True,
        "recovery": "skip_or_retry_with_permission",
    }
    if is_permission_error(exc):
        metadata["permission_blocked"] = True
    metadata.update(extra)
    return metadata

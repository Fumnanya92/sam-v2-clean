"""Read recent Sam v2 log artifacts for UI and diagnostics surfaces."""

from __future__ import annotations

import json
from pathlib import Path

from .log_manager import LOG_ROOT, ensure_log_directories


def _recent_files(directory: Path, pattern: str, limit: int) -> list[Path]:
    return sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def _read_jsonl_tail(path: Path, limit: int) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    payloads: list[dict] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            payloads.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return payloads


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def recent_log_overview(*, limit_per_type: int = 2) -> dict[str, list[str] | int]:
    ensure_log_directories()
    actions_dir = LOG_ROOT / "actions"
    errors_dir = LOG_ROOT / "errors"
    summaries_dir = LOG_ROOT / "summaries"

    action_lines: list[str] = []
    for path in _recent_files(actions_dir, "*.jsonl", limit_per_type):
        for payload in _read_jsonl_tail(path, 1):
            action_lines.append(
                f"{payload.get('action', 'action')} [{payload.get('status', 'unknown')}]"
            )

    error_lines: list[str] = []
    for path in _recent_files(errors_dir, "*.jsonl", limit_per_type):
        for payload in _read_jsonl_tail(path, 1):
            error_lines.append(
                f"{payload.get('event', 'error')}: {payload.get('error_message', 'unknown error')}"
            )

    summary_lines: list[str] = []
    for path in _recent_files(summaries_dir, "*.json", limit_per_type):
        payload = _read_json(path)
        if not payload:
            continue
        result = payload.get("result", {})
        summary_lines.append(
            f"{result.get('status', 'unknown')}: {result.get('summary', 'no summary')}"
        )

    return {
        "action_count": len(list(actions_dir.glob("*.jsonl"))),
        "error_count": len(list(errors_dir.glob("*.jsonl"))),
        "summary_count": len(list(summaries_dir.glob("*.json"))),
        "actions": action_lines,
        "errors": error_lines,
        "summaries": summary_lines,
    }

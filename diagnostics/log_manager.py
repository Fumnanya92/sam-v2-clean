"""Log workspace helpers for Sam."""

from __future__ import annotations

import shutil
from pathlib import Path


LOG_ROOT = Path("logs")
LOG_SUBDIRS = ("actions", "errors", "runs", "summaries", "tests")


def ensure_log_directories() -> None:
    for name in LOG_SUBDIRS:
        (LOG_ROOT / name).mkdir(parents=True, exist_ok=True)


def reset_log_workspace() -> None:
    if LOG_ROOT.exists():
        shutil.rmtree(LOG_ROOT, ignore_errors=True)
    ensure_log_directories()

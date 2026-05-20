"""
Sam's self-repair system.

When Sam hits an error he can't handle:
  1. Classify it — missing package, file not found, code bug, etc.
  2. Auto-fix common patterns (pip install missing package, search for file)
  3. If still broken — call Claude API with the error and apply the patch
  4. Log to error queue so a human can review anything Sam couldn't fix
  5. Retry the original task after a successful fix

The error queue (sam_error_queue.json) is Sam's way of saying:
  "I found a bug in myself. Here's everything you need to know to fix it."
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import traceback as _tb
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Error queue
# ---------------------------------------------------------------------------

def _queue_path(memory_path: Path | None) -> Path | None:
    if memory_path is None:
        return None
    return Path(memory_path).parent / "sam_error_queue.json"


def _load_queue(memory_path: Path | None) -> dict:
    path = _queue_path(memory_path)
    if path is None or not path.exists():
        return {"errors": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"errors": []}


def _save_queue(queue: dict, memory_path: Path | None) -> None:
    path = _queue_path(memory_path)
    if path is None:
        return
    path.write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")


def log_error(
    exc: Exception,
    *,
    task: str = "",
    source_file: str = "",
    memory_path: Path | None = None,
) -> str:
    """
    Write an error to the queue.
    Returns the error ID so Sam can reference it to the user.
    """
    error_id = f"err_{int(time.time())}"
    queue = _load_queue(memory_path)

    entry = {
        "id": error_id,
        "timestamp": time.strftime("%Y-%m-%d %H:%M"),
        "status": "pending",
        "task": task[:300],
        "error_type": type(exc).__name__,
        "error_message": str(exc)[:500],
        "traceback": _tb.format_exc()[:1500],
        "source_file": source_file,
        "fix_attempts": [],
    }
    queue["errors"].append(entry)
    # Keep last 50 errors
    queue["errors"] = queue["errors"][-50:]
    _save_queue(queue, memory_path)
    return error_id


def mark_fixed(error_id: str, note: str, memory_path: Path | None) -> None:
    queue = _load_queue(memory_path)
    for entry in queue["errors"]:
        if entry["id"] == error_id:
            entry["status"] = "fixed"
            entry["fix_note"] = note
            entry["fixed_at"] = time.strftime("%Y-%m-%d %H:%M")
            break
    _save_queue(queue, memory_path)


def pending_errors(memory_path: Path | None) -> list[dict]:
    queue = _load_queue(memory_path)
    return [e for e in queue["errors"] if e.get("status") == "pending"]


def format_queue_for_claude(memory_path: Path | None) -> str:
    """
    Produce a readable summary of pending errors for Claude to review.
    Call this when the user says 'what's broken?' or 'check your error queue'.
    """
    errors = pending_errors(memory_path)
    if not errors:
        return "No pending errors in my repair queue."
    lines = [f"I have {len(errors)} unresolved error(s) in my queue:\n"]
    for e in errors:
        lines.append(f"ID: {e['id']}  [{e['timestamp']}]")
        lines.append(f"  Task:  {e['task']}")
        lines.append(f"  Error: {e['error_type']}: {e['error_message']}")
        if e.get("source_file"):
            lines.append(f"  File:  {e['source_file']}")
        if e.get("fix_attempts"):
            for attempt in e["fix_attempts"]:
                lines.append(f"  Tried: {attempt}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Auto-fix: classify and attempt repair
# ---------------------------------------------------------------------------

def attempt_fix(
    exc: Exception,
    *,
    task: str = "",
    memory_path: Path | None = None,
    llm_client: Any = None,
) -> tuple[bool, str]:
    """
    Try to fix the error automatically.
    Returns (success: bool, message: str).

    Order of attempts:
      1. Missing Python package  → pip install
      2. Code bug in Sam's files → call Claude API for a patch
      3. Give up → log to queue
    """
    error_str = str(exc)
    tb = _tb.format_exc()

    # ── Pattern 1: Missing Python package ────────────────────────────────────
    pkg_match = re.search(r"No module named '([^']+)'", error_str)
    if pkg_match:
        raw_pkg = pkg_match.group(1)
        # Map internal names to pip names
        pkg = _pip_name(raw_pkg)
        success, msg = _pip_install(pkg)
        if success:
            return True, f"I installed `{pkg}` and I'm ready to retry."
        log_error(exc, task=task, memory_path=memory_path)
        return False, f"I tried to install `{pkg}` but it failed: {msg}"

    # ── Pattern 2: Sam's own code bug — escalate to Claude API ───────────────
    sam_file = _find_sam_file_in_traceback(tb)
    if sam_file and llm_client:
        fixed, patch_msg = _ask_claude_for_fix(exc, tb, sam_file, task, llm_client)
        if fixed:
            error_id = log_error(exc, task=task, source_file=sam_file, memory_path=memory_path)
            mark_fixed(error_id, f"Claude patched {sam_file}", memory_path)
            return True, f"I found a bug in `{sam_file}` and patched it. Retrying now."

    # ── Give up — log and tell the user ──────────────────────────────────────
    error_id = log_error(
        exc, task=task,
        source_file=sam_file or "",
        memory_path=memory_path,
    )
    return False, (
        f"I couldn't fix this automatically (ref: `{error_id}`).\n\n"
        f"I've logged everything to my repair queue. "
        f"Ask Claude to check my queue and it'll patch me — "
        f"then I'll retry your task."
    )


def _pip_name(internal: str) -> str:
    """Map Python module names to their pip package names."""
    mapping = {
        "firebase_admin": "firebase-admin",
        "bs4": "beautifulsoup4",
        "cv2": "opencv-python",
        "sklearn": "scikit-learn",
        "PIL": "Pillow",
        "dotenv": "python-dotenv",
        "yaml": "PyYAML",
        "git": "GitPython",
    }
    return mapping.get(internal, internal.replace("_", "-"))


def _pip_install(package: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", package],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            return True, f"installed {package}"
        return False, (result.stderr or result.stdout)[:300]
    except Exception as e:
        return False, str(e)


def _find_sam_file_in_traceback(tb: str) -> str | None:
    """Find the Sam source file most likely responsible for the error."""
    sam_root = str(Path(__file__).parent.parent)
    lines = tb.splitlines()
    last_sam_file = None
    for line in lines:
        if "File" in line and sam_root.lower() in line.lower():
            m = re.search(r'File "([^"]+)"', line)
            if m:
                last_sam_file = m.group(1)
    return last_sam_file


def _ask_claude_for_fix(
    exc: Exception,
    tb: str,
    broken_file: str,
    task: str,
    llm_client: Any,
) -> tuple[bool, str]:
    """
    Send the error + broken file to the Claude API and apply the patch.
    Returns (patched: bool, message: str).
    """
    try:
        broken = Path(broken_file)
        if not broken.exists():
            return False, "file not found"
        source = broken.read_text(encoding="utf-8", errors="replace")[:6000]

        prompt = (
            f"Sam (an AI assistant) has a bug in one of his source files.\n\n"
            f"File: {broken_file}\n\n"
            f"Task Sam was trying to do: {task}\n\n"
            f"Error:\n{type(exc).__name__}: {exc}\n\n"
            f"Traceback:\n{tb[:1000]}\n\n"
            f"Source file (first 6000 chars):\n```python\n{source}\n```\n\n"
            f"Return ONLY the fixed Python source file. No explanation. No markdown fences."
        )

        from coding_models.manager import CodingModelManager
        from coding_models import CodingDelegationRequest
        from pathlib import Path as _Path

        sam_root = _Path(__file__).parent.parent
        settings_path = sam_root / "workspace" / "runtime" / "coding_models.json"
        if not settings_path.exists():
            # Try the memory dir
            settings_path = _Path(llm_client.settings.base_url).parent / "coding_models.json"

        if not settings_path.exists():
            return False, "coding_models.json not found"

        manager = CodingModelManager(settings_path)
        if manager.active_model() == "local":
            return False, "no Claude/Codex model active"

        request = CodingDelegationRequest(
            goal=prompt,
            project_root=str(sam_root),
            context="Sam self-repair",
            mode="repo_code",
        )
        result = manager.delegate(request)
        if not result.ok:
            return False, result.summary or "delegation failed"

        # The result should be the fixed source. Apply it.
        fixed_source = (result.summary or "").strip()
        if len(fixed_source) < 50:
            return False, "response too short to be valid code"

        # Back up and write the fix
        backup = broken.with_suffix(".py.bak")
        backup.write_text(source, encoding="utf-8")
        broken.write_text(fixed_source, encoding="utf-8")
        return True, f"patched {broken_file}"

    except Exception as patch_exc:
        return False, str(patch_exc)


# ---------------------------------------------------------------------------
# Pending task retry store
# ---------------------------------------------------------------------------

def save_retry_task(task: str, memory_path: Path | None) -> None:
    """Save the task that failed so Sam can retry it after a fix."""
    path = _queue_path(memory_path)
    if path is None:
        return
    queue = _load_queue(memory_path)
    queue["retry_task"] = {"task": task, "saved_at": time.strftime("%Y-%m-%d %H:%M")}
    _save_queue(queue, memory_path)


def pop_retry_task(memory_path: Path | None) -> str | None:
    """Return and clear the saved retry task, if any."""
    queue = _load_queue(memory_path)
    retry = queue.pop("retry_task", None)
    if retry:
        _save_queue(queue, memory_path)
        return retry.get("task")
    return None

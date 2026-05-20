"""
Block 4: Sam's coding agent.

Flow:
  1. Extract the coding task + project name from the user's message
  2. Use discovery to find the project on the machine
  3. Present what was found — ask the user to confirm
  4. On confirmation ("yes", "go ahead"), delegate to the active coding model

The active coding model is whatever is set in coding_models.json
(currently "codex"). Sam doesn't hardcode it — it reads the file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Task + project extraction
# ---------------------------------------------------------------------------

_EXTRACT_PROMPT = """\
From the user's message, extract two things:
1. The coding task they want done (what to fix/build/change)
2. The project or app name they mentioned (if any)

Reply in JSON only:
{{"task": "<the coding goal>", "project_hint": "<project name or empty string>"}}

Rules:
- task: a clear, concise goal statement (e.g. "fix the login bug", "add dark mode", "scaffold a Flutter app")
- project_hint: just the name of the project/app (e.g. "Estate", "PlanFlow", "Lina") — empty string if not mentioned
- If the user says "this app" or "my app" without naming it, set project_hint to empty string
- Do not include any explanation outside the JSON

User message: {message}"""


def extract_code_task(message: str, llm_client: Any) -> dict[str, str]:
    """
    Extract {task, project_hint} from the user's message.
    Returns {"task": "...", "project_hint": "..."} — either may be empty string.
    """
    prompt = _EXTRACT_PROMPT.format(message=message.strip())

    try:
        import json as _j
        from urllib import request as _req

        payload = _j.dumps({
            "model": llm_client.resolve_model(),
            "prompt": prompt,
            "stream": False,
        }).encode()

        req = _req.Request(
            f"{llm_client.settings.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _req.urlopen(req, timeout=15) as resp:
            body = _j.loads(resp.read())
            raw = str(body.get("response", "")).strip()

        # Extract the JSON object from the response
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            parsed = _j.loads(raw[start:end])
            return {
                "task": str(parsed.get("task", "")).strip(),
                "project_hint": str(parsed.get("project_hint", "")).strip(),
            }
    except Exception:
        pass

    return {"task": message.strip(), "project_hint": ""}


# ---------------------------------------------------------------------------
# Pending task storage (written to memory dir, separate from main memory)
# ---------------------------------------------------------------------------

def _pending_path(memory_path: Path | None) -> Path | None:
    if memory_path is None:
        return None
    return memory_path.parent / "sam_brain_pending.json"


def save_pending(goal: str, project_path: str, project_name: str, memory_path: Path | None) -> None:
    path = _pending_path(memory_path)
    if path is None:
        return
    data = {"goal": goal, "project_path": project_path, "project_name": project_name}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_pending(memory_path: Path | None) -> dict | None:
    path = _pending_path(memory_path)
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def clear_pending(memory_path: Path | None) -> None:
    path = _pending_path(memory_path)
    if path and path.exists():
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Confirmation detection
# ---------------------------------------------------------------------------

_CONFIRM_WORDS = {
    "yes", "yeah", "yep", "yup", "yea", "ya", "yh", "y",
    "sure", "ok", "okay", "k", "kk",
    "go", "go ahead", "proceed", "do it",
    "let's go", "lets go", "let's do it", "lets do it",
    "correct", "that's right", "thats right",
    "that's the one", "thats the one",
    "go for it", "confirmed", "confirm", "affirmative",
    "please", "yes please", "please do", "do it please",
}

# Common typos for "yes"
_CONFIRM_TYPOS = {"ywa", "yse", "eys", "ys", "ye", "yess", "yyes"}


def looks_like_confirmation(text: str) -> bool:
    """Return True if the message is a short yes/go/confirm (including typos)."""
    lower = text.strip().lower().rstrip(".! ").strip()

    if lower in _CONFIRM_WORDS:
        return True

    if lower in _CONFIRM_TYPOS:
        return True

    # Match "yes please", "go ahead please", etc.
    for word in _CONFIRM_WORDS:
        if lower.startswith(word + " ") or lower.endswith(" " + word):
            return True

    # Short message (≤ 4 chars) that starts with 'y' — probably a yes typo
    if len(lower) <= 4 and lower.startswith("y"):
        return True

    return False


# ---------------------------------------------------------------------------
# Delegation — calls CodingModelManager.delegate()
# ---------------------------------------------------------------------------

def delegate_to_coding_model(
    goal: str,
    project_path: str,
    project_name: str,
    *,
    settings_path: Path,
) -> str:
    """
    Send the task to the active coding model (codex/claude).
    Returns a summary string of what happened.
    """
    try:
        from coding_models import CodingDelegationRequest
        from coding_models.manager import CodingModelManager

        manager = CodingModelManager(settings_path)
        active = manager.active_model()

        if active == "local":
            return (
                f"I found the project at {project_path}, but no external coding model is active. "
                f"Run `/codex` or `/claude` to enable one, then I'll delegate the task."
            )

        request = CodingDelegationRequest(
            goal=goal,
            project_root=project_path,
            context=f"Project: {project_name}. Sam delegating via sam_brain Block 4.",
            mode="repo_code",
        )

        result = manager.delegate(request)

        if result.ok:
            return _human_success_report(project_name, goal, result)
        else:
            return _human_failure_report(project_name, result)

    except Exception as exc:
        return (
            f"Something went wrong while I was working on {project_name}. "
            f"Want me to try again, or take a different approach?"
        )


def _human_success_report(project_name: str, goal: str, result) -> str:
    """Turn a raw codex result into a friendly, plain-English report."""
    metadata = result.metadata or {}
    changed = metadata.get("changed_files", [])
    duration = metadata.get("duration_seconds", 0)

    lines = [f"Done! I finished working on {project_name}."]
    lines.append("")

    # What was the task
    short_goal = goal.split("\n")[0][:120]
    lines.append(f"What I did: {short_goal}")

    # Files changed
    if changed:
        lines.append("")
        if len(changed) == 1:
            lines.append(f"I updated 1 file: {changed[0]}")
        else:
            lines.append(f"I updated {len(changed)} files:")
            for f in changed[:6]:
                lines.append(f"  • {f}")
            if len(changed) > 6:
                lines.append(f"  • ...and {len(changed) - 6} more")

    # Time taken
    if duration:
        mins = duration // 60
        secs = duration % 60
        if mins:
            lines.append(f"\nTook about {mins} min {secs}s.")
        else:
            lines.append(f"\nTook about {secs} seconds.")

    lines.append("")
    lines.append("Want me to do anything else with it?")
    return "\n".join(lines)


def _human_failure_report(project_name: str, result) -> str:
    """Turn a codex failure into a friendly, plain-English message."""
    return (
        f"I ran into a snag while working on {project_name}. "
        f"The coding agent couldn't finish — {result.summary or 'something unexpected came up'}. "
        f"Want me to try a different approach, or can you give me more details about what you need?"
    )

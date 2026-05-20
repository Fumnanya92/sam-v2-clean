"""
Sam's skills registry.

Tracks what Sam knows how to do:
  - built_in  : core capabilities, always available
  - acquired  : scripts and workflows Sam has learned through use

Skills are injected into _think() so Sam knows his own capabilities.
When Sam successfully completes a novel task, it can be saved as a new skill —
so he never has to learn the same thing twice.

Skill scripts live in: <sam_root>/sam_skills/<skill_name>.py
Registry lives at: <memory_dir>/sam_skills.json
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Built-in skills (always present — these are Sam's native capabilities)
# ---------------------------------------------------------------------------

_BUILT_IN_SKILLS: list[dict] = [
    {
        "name": "find_project",
        "description": "Search for a project, folder, or file on this Windows machine by name",
        "action": "find",
        "tags": ["search", "file", "folder", "project"],
    },
    {
        "name": "write_or_fix_code",
        "description": "Write, fix, edit, or review code in any project using the coding agent",
        "action": "code",
        "tags": ["code", "develop", "fix", "edit", "flutter", "python", "javascript"],
    },
    {
        "name": "run_command",
        "description": "Execute a terminal command, run tests, or start an application",
        "action": "run",
        "tags": ["terminal", "test", "start", "execute", "command"],
    },
    {
        "name": "query_firestore",
        "description": "Read live data from a Firebase Firestore database using a service account key",
        "action": "query",
        "tags": ["firebase", "firestore", "database", "query", "data"],
    },
    {
        "name": "open_in_explorer",
        "description": "Open any folder or file in Windows Explorer",
        "action": "open",
        "tags": ["open", "folder", "explorer", "browse"],
    },
    {
        "name": "save_to_memory",
        "description": "Store facts, paths, API key locations, or preferences for future sessions",
        "action": "remember",
        "tags": ["memory", "save", "remember", "store"],
    },
    {
        "name": "test_flutter_ui",
        "description": "Run a Flutter app on Chrome and test UI flows (login, buttons, navigation) with Playwright",
        "action": "skill",
        "skill_name": "test_flutter_ui",
        "tags": ["flutter", "test", "ui", "chrome", "playwright", "browser"],
        "status": "planned",  # will be implemented soon
    },
]


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _skills_path(memory_path: Path | None) -> Path | None:
    if memory_path is None:
        return None
    return Path(memory_path).parent / "sam_skills.json"


def _scripts_dir() -> Path:
    """Directory where acquired skill scripts are stored."""
    root = Path(__file__).parent.parent
    d = root / "sam_skills"
    d.mkdir(exist_ok=True)
    return d


def _load(memory_path: Path | None) -> dict:
    path = _skills_path(memory_path)
    if path is None:
        return {"built_in": _BUILT_IN_SKILLS, "acquired": []}
    if not path.exists():
        data = {"built_in": _BUILT_IN_SKILLS, "acquired": [], "version": 1}
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return data
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"built_in": _BUILT_IN_SKILLS, "acquired": []}


def _save(data: dict, memory_path: Path | None) -> None:
    path = _skills_path(memory_path)
    if path is None:
        return
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_skills(memory_path: Path | None) -> dict:
    """Return the full skills registry."""
    return _load(memory_path)


def list_acquired(memory_path: Path | None) -> list[dict]:
    return _load(memory_path).get("acquired", [])


def save_acquired_skill(
    name: str,
    description: str,
    script: str,
    tags: list[str],
    memory_path: Path | None,
) -> str:
    """
    Save a newly learned skill.
    Writes the script to sam_skills/<name>.py and records it in the registry.
    Returns the skill name.
    """
    # Normalise name
    import re
    slug = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')

    # Write script
    script_path = _scripts_dir() / f"{slug}.py"
    script_path.write_text(script, encoding="utf-8")

    data = _load(memory_path)
    # Dedup by name
    data["acquired"] = [s for s in data["acquired"] if s.get("name") != slug]
    data["acquired"].append({
        "name": slug,
        "description": description,
        "script_path": str(script_path),
        "tags": tags,
        "learned_at": time.strftime("%Y-%m-%d %H:%M"),
        "run_count": 0,
        "status": "active",
    })
    _save(data, memory_path)
    print(f"[SKILLS] Saved new skill: {slug}", flush=True)
    return slug


def find_skill(query: str, memory_path: Path | None) -> dict | None:
    """
    Find a skill by name or description match (substring, then tag match).
    Returns the skill dict or None.
    """
    query_lower = query.lower()
    data = _load(memory_path)

    # Check built-ins
    for skill in data.get("built_in", []):
        if query_lower in skill["name"] or query_lower in skill["description"].lower():
            return skill
        if any(query_lower in tag for tag in skill.get("tags", [])):
            return skill

    # Check acquired
    for skill in data.get("acquired", []):
        if query_lower in skill["name"] or query_lower in skill["description"].lower():
            return skill
        if any(query_lower in tag for tag in skill.get("tags", [])):
            return skill

    return None


def run_skill(skill_name: str, memory_path: Path | None, *, args: dict | None = None) -> str:
    """
    Execute an acquired skill script.
    Returns the output (stdout + stderr).
    """
    data = _load(memory_path)
    skill = next((s for s in data.get("acquired", []) if s["name"] == skill_name), None)
    if not skill:
        return f"No acquired skill named '{skill_name}' found."

    script_path = Path(skill.get("script_path", ""))
    if not script_path.exists():
        return f"Skill script not found at: {script_path}"

    # Inject args as environment variables if provided
    import os
    env = os.environ.copy()
    if args:
        for k, v in args.items():
            env[f"SKILL_{k.upper()}"] = str(v)

    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace", env=env,
        )
        output = (result.stdout + result.stderr).strip()

        # Bump run count
        for s in data.get("acquired", []):
            if s["name"] == skill_name:
                s["run_count"] = s.get("run_count", 0) + 1
                s["last_run"] = time.strftime("%Y-%m-%d %H:%M")
        _save(data, memory_path)

        return output or "(skill ran with no output)"
    except subprocess.TimeoutExpired:
        return f"Skill '{skill_name}' timed out after 60 seconds."
    except Exception as e:
        return f"Error running skill '{skill_name}': {e}"


# ---------------------------------------------------------------------------
# Prompt injection — gives _think() Sam's full capability list
# ---------------------------------------------------------------------------

def get_skills_for_prompt(memory_path: Path | None) -> str:
    """
    Format the skills list for injection into _think() prompt.
    Shows built-ins + any acquired skills.
    """
    data = _load(memory_path)
    lines: list[str] = []

    lines.append("Built-in capabilities:")
    for skill in data.get("built_in", []):
        status = " [coming soon]" if skill.get("status") == "planned" else ""
        lines.append(f"  {skill['name']}: {skill['description']}{status}")

    acquired = [s for s in data.get("acquired", []) if s.get("status") == "active"]
    if acquired:
        lines.append("")
        lines.append("Learned skills (use action='skill' with skill_name):")
        for skill in acquired:
            lines.append(f"  {skill['name']}: {skill['description']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Skill suggestion — offer to save after a successful novel task
# ---------------------------------------------------------------------------

def should_offer_save(goal: str, memory_path: Path | None) -> bool:
    """
    Return True if this completed task looks reusable and not already saved.
    Used to prompt Sam to offer saving a new skill.
    """
    reusable_signals = [
        "send", "notify", "automate", "schedule", "generate report",
        "scrape", "download", "convert", "export", "backup",
        "whatsapp", "email", "sms", "upload", "deploy",
    ]
    goal_lower = goal.lower()
    if not any(sig in goal_lower for sig in reusable_signals):
        return False

    # Check if a similar skill already exists
    return find_skill(goal_lower[:30], memory_path) is None

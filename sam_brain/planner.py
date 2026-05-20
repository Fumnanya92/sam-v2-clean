"""
Block 6: Sam's project planner.

When a user asks Sam to BUILD something new (not fix an existing project),
Sam doesn't just dive in. He plans first:

  1. Understand what the user wants
  2. Generate a structured plan (name, phases, files, how to run/test)
  3. Present it conversationally — "Here's how I'm thinking about this..."
  4. Wait for the user's go-ahead
  5. On approval, format the plan as a rich prompt and delegate to codex

This is the planning → approval → build loop.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Plan structure
# ---------------------------------------------------------------------------

@dataclass
class ProjectPhase:
    title: str
    tasks: list[str] = field(default_factory=list)


@dataclass
class ProjectPlan:
    name: str
    description: str
    stack: str
    phases: list[ProjectPhase] = field(default_factory=list)
    key_files: list[str] = field(default_factory=list)
    run_command: str = ""
    test_command: str = ""
    workspace_path: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "stack": self.stack,
            "phases": [{"title": p.title, "tasks": p.tasks} for p in self.phases],
            "key_files": self.key_files,
            "run_command": self.run_command,
            "test_command": self.test_command,
            "workspace_path": self.workspace_path,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProjectPlan":
        phases = [
            ProjectPhase(title=p["title"], tasks=p.get("tasks", []))
            for p in d.get("phases", [])
        ]
        return cls(
            name=d.get("name", ""),
            description=d.get("description", ""),
            stack=d.get("stack", ""),
            phases=phases,
            key_files=d.get("key_files", []),
            run_command=d.get("run_command", ""),
            test_command=d.get("test_command", ""),
            workspace_path=d.get("workspace_path", ""),
        )

    def format_for_user(self) -> str:
        """Render the plan as a friendly, readable conversation message."""
        try:
            from workers.names import resolve_worker_identity
            forge    = resolve_worker_identity("code").name
            sentinel = resolve_worker_identity("test").name
            vector   = resolve_worker_identity("plan").name
        except Exception:
            forge = sentinel = vector = "the agent"

        lines = [
            f"Here's the plan for {self.name} — {vector} put this together:",
            "",
            f"{self.description}",
            "",
            f"Stack: {self.stack}",
            "",
            "Phases:",
        ]
        for i, phase in enumerate(self.phases, 1):
            lines.append(f"  Phase {i} — {phase.title}")
            for task in phase.tasks:
                lines.append(f"    • {task}")

        if self.key_files:
            lines.append("")
            lines.append(f"{forge} will create these key files:")
            for f in self.key_files:
                lines.append(f"  {f}")

        if self.run_command:
            lines.append("")
            lines.append(f"To run it: {self.run_command}")
        if self.test_command:
            lines.append(f"{sentinel} will verify with: {self.test_command}")

        lines.append("")
        lines.append(f"Does this look right? Say go and {forge} will start building.")
        return "\n".join(lines)

    def format_as_codex_prompt(self) -> str:
        """Turn the plan into a rich implementation prompt for codex."""
        lines = [
            f"Build the project: {self.name}",
            f"Description: {self.description}",
            f"Tech stack: {self.stack}",
            f"Project folder: {self.workspace_path}",
            "",
            "Implementation plan:",
        ]
        for i, phase in enumerate(self.phases, 1):
            lines.append(f"Phase {i}: {phase.title}")
            for task in phase.tasks:
                lines.append(f"  - {task}")

        if self.key_files:
            lines.append("")
            lines.append("Files to create:")
            for f in self.key_files:
                lines.append(f"  {f}")

        lines.append("")
        lines.append("Instructions:")
        lines.append("- Create ALL files from scratch in the project folder")
        lines.append("- Follow the phases in order")
        lines.append("- Make it fully runnable with real working code — no placeholders")
        lines.append("- Add a brief README.md explaining how to run and test")
        if self.run_command:
            lines.append(f"- Verify it runs with: {self.run_command}")
        if self.test_command:
            lines.append(f"- Run tests with: {self.test_command}")
        lines.append("- Report what was built, what was tested, and the result")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Plan generation using LLM
# ---------------------------------------------------------------------------

_PLAN_PROMPT = """\
You are Sam's project planning brain. The user wants to build something new.

User's request: {request}

Generate a clear, structured project plan as JSON.

Return ONLY a JSON object (no markdown, no explanation):
{{
  "name": "short project name",
  "description": "one sentence describing what it does and why it's useful",
  "stack": "technology stack (e.g. Python 3, Flutter/Dart, HTML/CSS/JS)",
  "phases": [
    {{"title": "Phase title", "tasks": ["specific task", "specific task"]}}
  ],
  "key_files": ["list of key files to create"],
  "run_command": "how to run it",
  "test_command": "how to test it (or empty string)"
}}

Rules:
- 2 to 4 phases
- Each phase has 2 to 4 concrete tasks
- key_files: only the most important files (3-8 files)
- run_command and test_command: real commands the user can copy-paste
- Make it realistic and buildable in one codex session
- Use the exact tech stack the user mentioned, or pick the best fit if they didn't specify"""


def generate_plan(request: str, workspace_path: str, llm_client: Any) -> ProjectPlan | None:
    """
    Use the LLM to generate a project plan from the user's request.
    Returns a ProjectPlan or None on failure.
    """
    prompt = _PLAN_PROMPT.format(request=request.strip())

    try:
        from urllib import request as _req

        payload = json.dumps({
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
        with _req.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
            raw = str(body.get("response", "")).strip()

        # Extract JSON object from response
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end <= start:
            return None

        data = json.loads(raw[start:end])
        plan = ProjectPlan.from_dict(data)
        plan.workspace_path = workspace_path
        return plan

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Detect new project vs existing project task
# ---------------------------------------------------------------------------

_CREATE_VERBS = {
    "create", "build", "make", "develop", "scaffold",
    "generate", "new project", "from scratch", "a new app", "a new tool",
    "a new project", "a new flutter", "a new python",
}

# Phrases that signal the task is on an EXISTING project, not a new one
_EXISTING_SIGNALS = {
    "to my ", "to the ", "in my ", "in the ", "for my ", "for the ",
    "add to", "update the", "fix the", "edit the", "modify the",
    "change the", "open the", "read the", "check the",
}


def is_new_project_request(message: str, project_hint: str = "") -> bool:
    """
    Return True only when the user wants to build something brand new.

    Rules:
    - If a specific existing project is named (project_hint), it's never new.
    - If the message contains signals of working on an existing thing, it's never new.
    - Only True when clear new-build verbs appear with no existing-project signals.
    """
    lower = message.lower()

    # A named project means we're working on something that already exists
    if project_hint and project_hint.lower() in lower:
        return False

    # Phrases like "add X to my project", "fix X in the app" = existing project
    for signal in _EXISTING_SIGNALS:
        if signal in lower:
            return False

    for verb in _CREATE_VERBS:
        if verb in lower:
            return True
    return False

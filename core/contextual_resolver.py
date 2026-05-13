"""Contextual request resolution for parsed intent hints.

This module keeps follow-up and memory-based interpretation out of the router.
The router can parse, then ask this resolver to fold in conversation state,
workflow state, and recent runtime memory before the core runtime decides how
to execute.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from workflows import should_route_discovery


class ContextualRequestResolver:
    """Apply memory/context signals to a parsed request hint."""

    def apply(
        self,
        *,
        text: str,
        request: Any,
        memory_block: dict[str, Any] | None,
    ) -> Any:
        daily_state = memory_block.get("daily_state", {}) if isinstance(memory_block, dict) else {}
        last_file_path = str(daily_state.get("last_file_path", {}).get("value", "")).strip()
        last_project_root = str(daily_state.get("last_project_root_path", {}).get("value", "")).strip()
        last_runtime_intent = str(daily_state.get("last_runtime_intent", {}).get("value", "")).strip().lower()
        last_runtime_summary = str(daily_state.get("last_runtime_summary", {}).get("value", "")).strip()
        pending_scaffold = _pending_scaffold(memory_block)

        if request.intent == "chat" and _asks_about_known_projects(text):
            request.intent = "list_projects"
            request.parameters = {}
            request.needs_clarification = False
            request.clarification_question = ""

        if self._apply_scaffold_followup(
            text=text,
            request=request,
            last_runtime_intent=last_runtime_intent,
            last_runtime_summary=last_runtime_summary,
            pending_scaffold=pending_scaffold,
        ):
            return request

        if _wants_summary(text):
            path = str(request.parameters.get("path", "")).strip()
            if not path:
                path = _path_from_text(text)
            if not path and last_file_path:
                path = last_file_path
            if path:
                request.intent = "read_file"
                request.parameters["path"] = path
                request.source = request.source or "context"

        project_context_intents = {
            "inspect_repo",
            "inspect_project_repo",
            "inspect_git_state",
            "scan_codebase_patterns",
            "check_python_syntax",
            "inspect_recent_changes",
            "read_file",
            "list_directory",
        }
        if request.intent in project_context_intents and last_project_root:
            has_location = any(
                str(request.parameters.get(key, "")).strip()
                for key in ("query", "path", "repo_path", "root_path")
            )
            if not has_location and _refers_to_current_context(text):
                key = "path" if request.intent in {"read_file", "list_directory"} else "query"
                request.parameters[key] = last_project_root

        if should_route_discovery(text, parsed_intent=request.intent, memory_block=memory_block):
            original_intent = request.intent
            request.intent = "discovery_workflow"
            request.parameters["_discovery_parsed_intent"] = original_intent
            request.needs_clarification = False
            request.clarification_question = ""

        return request

    def _apply_scaffold_followup(
        self,
        *,
        text: str,
        request: Any,
        last_runtime_intent: str,
        last_runtime_summary: str,
        pending_scaffold: dict[str, Any],
    ) -> bool:
        lowered = text.lower().strip()
        if not lowered:
            return False

        recent_scaffold_context = (
            request.intent == "scaffold_project"
            or "tic-tac-toe" in lowered
            or "tictac" in lowered
            or "new project" in lowered
            or "new one" in lowered
            or "create a new" in lowered
            or bool(pending_scaffold)
            or (
                last_runtime_intent == "clarify"
                and "new" in last_runtime_summary.lower()
                and ("project" in last_runtime_summary.lower() or "tic-tac-toe" in last_runtime_summary.lower())
            )
        )
        if not recent_scaffold_context:
            return False

        merged = {
            "name": str(pending_scaffold.get("name", "")).strip(),
            "project_type": str(pending_scaffold.get("project_type", "")).strip(),
        }
        extracted_type = _extract_project_type(text)
        extracted_name = _extract_project_name(text)
        if extracted_type:
            merged["project_type"] = extracted_type
        if extracted_name:
            merged["name"] = extracted_name

        asks_for_type = "type" in last_runtime_summary.lower()
        asks_for_name = "name" in last_runtime_summary.lower() or "named" in last_runtime_summary.lower()

        request.raw_text = text
        request.confidence = request.confidence
        request.source = request.source or "context"

        if merged["name"] and merged["project_type"]:
            request.intent = "scaffold_project"
            request.parameters = {"name": merged["name"], "project_type": merged["project_type"]}
            request.needs_clarification = False
            request.clarification_question = ""
            return True

        if asks_for_type and merged["project_type"] and not merged["name"]:
            request.intent = "clarify"
            request.parameters = {"pending_scaffold": merged}
            request.needs_clarification = True
            request.clarification_question = "What should I name the new project?"
            return True

        if asks_for_name and merged["name"] and not merged["project_type"]:
            request.intent = "clarify"
            request.parameters = {"pending_scaffold": merged}
            request.needs_clarification = True
            request.clarification_question = "What project type should I use (for example: web app, python console app)?"
            return True

        if merged["name"] or merged["project_type"]:
            request.intent = "clarify"
            request.parameters = {"pending_scaffold": merged}
            request.needs_clarification = True
            request.clarification_question = (
                "I can create that project. I still need both a project name and type."
                if not merged["name"] and not merged["project_type"]
                else ("What should I name the new project?" if not merged["name"] else "What project type should I use?")
            )
            return True

        return False


def _pending_scaffold(memory_block: dict[str, Any] | None) -> dict[str, Any]:
    pending_scaffold_raw = memory_block.get("scaffold_pending", {}) if isinstance(memory_block, dict) else {}
    if isinstance(pending_scaffold_raw, dict) and "value" in pending_scaffold_raw:
        pending_scaffold_raw = pending_scaffold_raw.get("value", {})
    return pending_scaffold_raw if isinstance(pending_scaffold_raw, dict) else {}


def _asks_about_known_projects(text: str) -> bool:
    lowered = text.lower()
    return (
        ("project" in lowered or "projects" in lowered)
        and any(token in lowered for token in ("completed", "finished", "done", "built"))
    )


def _extract_project_type(text: str) -> str:
    lowered = text.lower()
    if "web app" in lowered or lowered == "web":
        return "web app"
    if "python console" in lowered or "console app" in lowered:
        return "python console app"
    if "flutter" in lowered:
        return "flutter app"
    if "html" in lowered:
        return "html app"
    return ""


def _extract_project_name(text: str) -> str:
    lowered = text.lower().strip()
    direct = re.match(r"^(?:name\s+it|call\s+it)\s+(.+)$", lowered)
    if direct:
        return direct.group(1).strip()
    generic = {"web app", "web", "python", "console app", "yes", "no", "create a new one", "new one"}
    cleaned = text.strip().strip(".!,")
    if not cleaned:
        return ""
    if lowered in generic:
        return ""
    if len(cleaned.split()) <= 4 and any(ch.isalnum() for ch in cleaned):
        return cleaned
    return ""


def _path_from_text(text: str) -> str:
    match = re.search(r"[A-Za-z]:[\\/][^\r\n\"']+", text)
    if not match:
        return ""
    candidate = match.group(0).strip().rstrip(".,;")
    while candidate:
        path = Path(candidate)
        if path.exists():
            return str(path)
        if " " in candidate:
            candidate = candidate.rsplit(" ", 1)[0].rstrip(".,;")
            continue
        parent = str(path.parent)
        if parent == candidate:
            break
        candidate = parent.rstrip("\\/")
    return match.group(0).strip().rstrip(".,;")


def _wants_summary(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ("summarize", "summary", "summarise", "summarized version", "summarised version"))


def _refers_to_current_context(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in ("this project", "this repo", "this file", "the project", "the repo", "it", "that project"))

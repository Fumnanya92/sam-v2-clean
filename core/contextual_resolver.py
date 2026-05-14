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
        last_file_path = str(daily_state.get("last_file_path", {}).get("value", "") or daily_state.get("last_file_path", "")).strip()
        last_project_root = str(daily_state.get("last_project_root_path", {}).get("value", "") or daily_state.get("last_project_root_path", "")).strip()
        last_runtime_intent = str(daily_state.get("last_runtime_intent", {}).get("value", "") or daily_state.get("last_runtime_intent", "")).strip().lower()
        last_runtime_summary = str(daily_state.get("last_runtime_summary", {}).get("value", "") or daily_state.get("last_runtime_summary", "")).strip()
        pending_scaffold = _pending_scaffold(memory_block)
        detected_intent = _detected_intent(memory_block)

        if request.intent in {"autonomous_request", "delegate_coding_task"} and detected_intent in {
            "casual_chat",
            "planning",
            "architecture",
            "unclear",
        } and not self._looks_like_task(text):
            request.intent = "chat" if detected_intent != "unclear" else "clarify"
            request.parameters = {}
            request.needs_clarification = detected_intent == "unclear"
            request.clarification_question = (
                "Do you want to keep planning, or should I implement changes in the codebase?"
                if detected_intent == "unclear"
                else ""
            )
            request.response_text = request.response_text or ""
            request.source = request.source or "context_intent_guard"
            return request
        
        # Check if a coding model is active
        active_coding_model = ""
        if isinstance(memory_block, dict):
            coding_model_info = memory_block.get("coding_model", {})
            if isinstance(coding_model_info, dict):
                coding_model_value = coding_model_info.get("value", {})
                if isinstance(coding_model_value, dict):
                    active_coding_model = str(coding_model_value.get("active_coding_model", "")).strip()
        
        corrected_repo = _corrected_repo_path(text)
        if active_coding_model and active_coding_model not in {"local", ""} and corrected_repo:
            previous_goal = _previous_delegated_goal(memory_block)
            if previous_goal:
                request.intent = "autonomous_request"
                request.parameters = {"query": corrected_repo, "path": corrected_repo, "goal": previous_goal}
                request.raw_text = previous_goal
                request.needs_clarification = False
                request.clarification_question = ""
                request.source = "corrected_repo_context"
                return request

        # If a coding model (not local) is active, delegate only explicit coding/debug/review work.
        if active_coding_model and active_coding_model not in {"local", ""}:
            if request.intent in {"clarify", "chat"} and detected_intent in {"coding", "debugging", "review"}:
                request.intent = "autonomous_request"
                request.parameters = {"query": text}
                request.needs_clarification = False
                request.clarification_question = ""
                request.source = request.source or "coding_delegation"
                return request

        # Handle path/file follow-ups: if previous turn asked about files and current is a path, resolve it
        if (
            request.intent in {"clarify", "chat"}
            and self._is_likely_path(text)
            and self._was_asking_for_file_or_path(last_runtime_summary)
        ):
            # User is providing a path in response to "which file" or "what path" question
            request.intent = "list_directory"
            request.parameters = {"path": text}
            request.needs_clarification = False
            request.clarification_question = ""
            request.source = request.source or "context"
            return request

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

    def _is_likely_path(self, text: str) -> bool:
        """Detect if text looks like a file or directory path."""
        t = text.strip()
        # Windows paths: C:\, D:\, etc.
        if len(t) > 2 and t[1] == ":" and t[0].isalpha():
            return True
        # Unix paths: /home, /Users, /tmp, etc.
        if t.startswith("/"):
            return True
        # UNC paths: \\server\share
        if t.startswith("\\\\"):
            return True
        return False

    def _was_asking_for_file_or_path(self, last_summary: str) -> bool:
        """Check if the last response was asking for a file or path."""
        if not last_summary:
            return False
        lowered = last_summary.lower()
        keywords = {
            "file",
            "path",
            "directory",
            "folder",
            "location",
            "where",
            "which file",
            "exact file",
            "spreadsheet",
            "document",
        }
        return any(keyword in lowered for keyword in keywords)

    def _looks_like_task(self, text: str) -> bool:
        """Detect if text looks like a task/goal request, not pure conversation."""
        lowered = text.lower()
        # Task indicators
        task_keywords = {
            "help",
            "check",
            "find",
            "inspect",
            "analyze",
            "review",
            "fix",
            "build",
            "create",
            "debug",
            "optimize",
            "refactor",
            "test",
            "verify",
            "validate",
            "diagnose",
            "investigate",
            "improve",
            "enhance",
            "solve",
        }
        conversation_only = {
            "how are you",
            "what's up",
            "hello",
            "hi",
            "thanks",
            "thank you",
            "please",
            "sorry",
        }
        if lowered.strip() in conversation_only:
            return False
        return any(keyword in lowered for keyword in task_keywords)

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


def _detected_intent(memory_block: dict[str, Any] | None) -> str:
    if not isinstance(memory_block, dict):
        return ""
    raw = memory_block.get("detected_intent", "")
    if isinstance(raw, dict) and "value" in raw:
        raw = raw.get("value", "")
    if isinstance(raw, str):
        return raw.strip()
    compact = memory_block.get("compact_context", {})
    if isinstance(compact, dict) and "value" in compact:
        compact = compact.get("value", {})
    if isinstance(compact, dict):
        return str(compact.get("intent", "")).strip()
    return ""


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


def _corrected_repo_path(text: str) -> str:
    lowered = text.lower()
    correction_markers = (
        "wrong repo",
        "correct repo",
        "right repo",
        "wrong project",
        "correct project",
        "right project",
    )
    if not any(marker in lowered for marker in correction_markers):
        return ""
    return _path_from_text(text)


def _previous_delegated_goal(memory_block: dict[str, Any] | None) -> str:
    if not isinstance(memory_block, dict):
        return ""
    recent = memory_block.get("conversation", {}).get("recent_requests", {}).get("value", [])
    if not isinstance(recent, list):
        return ""
    for item in reversed(recent):
        if not isinstance(item, dict):
            continue
        intent = str(item.get("intent", "")).strip()
        request_text = str(item.get("request", "")).strip()
        if intent not in {"delegate_coding_task", "autonomous_request"} or not request_text:
            continue
        if _corrected_repo_path(request_text):
            continue
        return request_text
    return ""


def _wants_summary(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ("summarize", "summary", "summarise", "summarized version", "summarised version"))


def _refers_to_current_context(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in ("this project", "this repo", "this file", "the project", "the repo", "it", "that project"))

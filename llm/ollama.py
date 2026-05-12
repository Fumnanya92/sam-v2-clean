"""Minimal Ollama client for Sam understanding and code generation tasks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import request

from config import load_config


@dataclass
class OllamaSettings:
    base_url: str = "http://localhost:11434"
    model: str = "llama3.2"
    timeout_seconds: int = 20


@dataclass
class OllamaIntentOutput:
    intent: str
    parameters: dict[str, Any] = field(default_factory=dict)
    needs_clarification: bool = False
    clarification_question: str = ""
    response_text: str = ""
    confidence: str = "low"
    model: str = ""
    source: str = "ollama"


@dataclass
class GeneratedProjectFile:
    path: str
    content: str


@dataclass
class GeneratedProject:
    summary: str
    stack: str
    files: list[GeneratedProjectFile]
    run_command: list[str] = field(default_factory=list)
    test_command: list[str] = field(default_factory=list)


class OllamaClient:
    def __init__(
        self,
        settings: OllamaSettings | None = None,
        *,
        config_path: str | Path | None = None,
        env_file: str | Path | None = None,
    ) -> None:
        self.settings = settings or self._load_settings(config_path=config_path, env_file=env_file)
        self._resolved_model: str | None = None

    def is_available(self) -> bool:
        try:
            response = self._request("GET", "/api/tags")
            return bool(response.get("models", []))
        except Exception:
            return False

    def resolve_model(self) -> str:
        if self._resolved_model:
            return self._resolved_model

        configured = self.settings.model
        try:
            response = self._request("GET", "/api/tags")
            models = [item.get("name", "") for item in response.get("models", []) if item.get("name")]
            if not models:
                self._resolved_model = configured
                return self._resolved_model

            for model_name in models:
                if configured == model_name or model_name.startswith(configured.split(":")[0]):
                    self._resolved_model = model_name
                    return self._resolved_model

            self._resolved_model = models[0]
            return self._resolved_model
        except Exception:
            self._resolved_model = configured
            return self._resolved_model

    def classify_request(
        self,
        user_text: str,
        *,
        capabilities: list[str],
        memory_block: dict[str, Any] | None = None,
        known_projects: list[dict[str, str]] | None = None,
        workspace_root: str = "",
    ) -> OllamaIntentOutput:
        model = self.resolve_model()
        memory_json = json.dumps(memory_block or {}, ensure_ascii=True)
        capability_text = ", ".join(capabilities)
        projects_json = json.dumps(known_projects or [], ensure_ascii=True)
        prompt = "\n".join(
            [
                "You are Sam's autonomous request understanding layer.",
                "Return JSON only.",
                "Interpret the user's meaning, not just keywords.",
                "Use the closest supported intent when the request clearly maps to one.",
                "Do not invent unsupported capabilities.",
                "Do not hardcode or assume any specific project name unless it appears in the user's request or Known projects JSON.",
                "If the user is asking a question about existing work, do not convert it into a create action.",
                "If the request is ambiguous or unsafe to guess, set needs_clarification to true and provide clarification_question.",
                "If the request does not map cleanly to a supported action, use intent chat.",
                "Prefer action intents over chat when the user is clearly asking Sam to do something local or project-related.",
                "Supported intents and parameter shapes:",
                "- capabilities: {}",
                "- awareness_check: {\"capability_name\": \"...\"}",
                "- propose_upgrade: {\"capability_name\": \"...\"}",
                "- create_goal: {\"title\": \"...\"}",
                "- list_goals: {}",
                "- create_task: {\"title\": \"...\"}",
                "- update_task: {\"task_id\": \"...\", \"status\": \"...\", \"notes\": \"...\"}",
                "- list_tasks: {}",
                "- list_approvals: {}",
                "- create_draft: {\"title\": \"...\", \"body\": \"...\", \"content_type\": \"report\"}",
                "- list_workflows: {}",
                "- list_projects: {}",
                "- project_details: {\"query\": \"project name or id\"}",
                "- scaffold_project: {\"name\": \"...\", \"project_type\": \"...\"}",
                "- plan_project: {\"query\": \"project name or id\"}",
                "- show_delegation: {\"query\": \"project name or id\"}",
                "- show_project_progress: {\"query\": \"project name or id\"}",
                "- show_project_status: {\"query\": \"project name or id\"}",
                "- execute_project_task: {\"query\": \"project name or id\", \"task_name\": \"...\"}",
                "- run_project: {\"query\": \"project name or id\"}",
                "- inspect_project_repo: {\"query\": \"project name, id, or local repo path\"}",
                "- inspect_git_state: {\"query\": \"project name, id, or local repo path\"}",
                "- read_file: {\"path\": \"...\"}",
                "- open_file: {\"path\": \"...\"}",
                "- list_directory: {\"path\": \"...\"}",
                "- open_folder: {\"query\": \"folder name or path\"}",
                "- open_project_folder: {\"query\": \"project name or id\"}",
                "- inspect_workspace_cleanup: {\"scope\": \"all|projects|runtime\"}",
                "- cleanup_workspace_duplicates: {\"scope\": \"all|projects|runtime\"}",
                "- push_changes: {}",
                "- inspect_repo: {\"query\": \"project name, id, or local repo path\"}",
                "- scan_codebase_patterns: {\"query\": \"project name, id, or local repo path\", \"patterns\": [\"...\"]}",
                "- list_executor_tools: {}",
                "- list_worker_tasks: {}",
                "- check_python_syntax: {\"query\": \"project name, id, or local path\"}",
                "- inspect_recent_changes: {\"query\": \"project name, id, or local repo path\"}",
                "- autonomous_request: {\"query\": \"optional project name, id, local path, or data source\"}",
                "- plan_request: {}",
                "- chat: {}",
                "Use autonomous_request for open-ended investigation, multi-step diagnostic questions, data-source questions, architecture review questions, or requests that need choosing tools before answering.",
                "For codebase scans, extract the exact user-provided terms into patterns. Do not invent project names.",
                "For questions about registered executor tools, use list_executor_tools.",
                "For questions about worker logs, running workers, or recently completed workers, use list_worker_tasks.",
                "For compile/syntax-error checks on this Python app, use check_python_syntax.",
                "For latest changes, last changes, or what changed since last run, use inspect_recent_changes.",
                "For scaffold_project, only choose it when the user is actually asking to create/build/start a new project.",
                "For questions about how many projects exist, use list_projects unless a more specific project-inspection capability is available.",
                "For questions about a specific project type or name, use list_projects or project_details with the user's exact query.",
                "For open_folder and open_file, prefer them when the user asks to open a local folder or file on the machine.",
                "For inspect_workspace_cleanup, use it when the user asks to inspect, organize, or find duplicates in the workspace.",
                "For cleanup_workspace_duplicates, use it only when the user is explicitly confirming cleanup or deletion of duplicates.",
                "For chat, provide a brief conversational response_text.",
                "For clarification, response_text may be empty.",
                f"Available capabilities: {capability_text}",
                f"Workspace root: {workspace_root}",
                f"Known projects JSON: {projects_json}",
                f"Memory context JSON: {memory_json}",
                f"User request: {user_text}",
                'Return fields: intent, parameters, needs_clarification, clarification_question, response_text, confidence.',
            ]
        )
        payload = {"model": model, "prompt": prompt, "stream": False, "format": "json"}
        response = self._request("POST", "/api/generate", payload)
        raw_body = str(response.get("response", "")).strip()
        parsed = self._parse_json_object(raw_body)
        if not isinstance(parsed, dict):
            raise ValueError("Ollama response was not valid JSON.")

        return OllamaIntentOutput(
            intent=str(parsed.get("intent", "chat") or "chat"),
            parameters=parsed.get("parameters", {}) if isinstance(parsed.get("parameters"), dict) else {},
            needs_clarification=bool(parsed.get("needs_clarification", False)),
            clarification_question=str(parsed.get("clarification_question", "") or ""),
            response_text=str(parsed.get("response_text", "") or ""),
            confidence=str(parsed.get("confidence", "low") or "low"),
            model=model,
        )

    def generate_project(self, *, project_name: str, project_type: str, user_request: str) -> GeneratedProject:
        model = self.resolve_model()
        prompt = "\n".join(
            [
                "You are Sam's coding worker.",
                "Generate a small, runnable project from the user's request.",
                "Return JSON only. Do not include markdown fences.",
                "Do not use hardcoded examples unless the user asks for that exact thing.",
                "Prefer simple, dependency-free code unless the user asks for a framework.",
                "For a browser project, create index.html, styles.css, app.js, README.md, and run_project.py.",
                "run_project.py must validate the project and open the main file in the browser when run.",
                "The JSON shape must be:",
                '{"summary":"...","stack":"...","run_command":["python","run_project.py"],"test_command":["python","run_project.py","--check"],"files":[{"path":"index.html","content":"..."}]}',
                "Never use absolute paths. Never write outside the project folder.",
                f"Project name: {project_name}",
                f"Project type: {project_type}",
                f"User request: {user_request}",
            ]
        )
        payload = {"model": model, "prompt": prompt, "stream": False, "format": "json"}
        response = self._request("POST", "/api/generate", payload)
        raw_body = str(response.get("response", "")).strip()
        parsed = self._parse_json_object(raw_body)
        if not isinstance(parsed, dict):
            raise ValueError("Ollama project generation response was not valid JSON.")

        files_raw = parsed.get("files", [])
        if not isinstance(files_raw, list) or not files_raw:
            raise ValueError("Ollama project generation did not return files.")

        files: list[GeneratedProjectFile] = []
        for item in files_raw:
            if not isinstance(item, dict):
                continue
            file_path = str(item.get("path", "")).strip().replace("\\", "/")
            content = str(item.get("content", ""))
            if not file_path or file_path.startswith("/") or ".." in Path(file_path).parts:
                raise ValueError(f"Unsafe generated file path: {file_path}")
            files.append(GeneratedProjectFile(path=file_path, content=content))

        if not files:
            raise ValueError("Ollama project generation returned no usable files.")

        return GeneratedProject(
            summary=str(parsed.get("summary", "Project generated.") or "Project generated."),
            stack=str(parsed.get("stack", project_type) or project_type),
            run_command=self._string_list(parsed.get("run_command")) or ["python", "run_project.py"],
            test_command=self._string_list(parsed.get("test_command")) or ["python", "run_project.py", "--check"],
            files=files,
        )

    def choose_autonomous_action(
        self,
        *,
        user_text: str,
        tools: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        memory_block: dict[str, Any] | None = None,
        workspace_root: str = "",
    ) -> dict[str, Any]:
        model = self.resolve_model()
        prompt = "\n".join(
            [
                "You are Sam's autonomous reasoning loop.",
                "Return JSON only. Do not include markdown fences.",
                "Your job is to decide the next safe read-only action, or answer if enough evidence exists.",
                "Use tools to inspect real local state instead of guessing.",
                "Do not invent tool names. Choose only from Available tools JSON.",
                "Do not request destructive, write, send, delete, push, or open-window actions.",
                "If credentials or a data source are needed and not available in observations or memory, ask the user for the path or detail needed.",
                "For codebase scans, pass exact user-provided search terms as patterns.",
                "For follow-ups, use memory and observations to preserve context.",
                "JSON shape:",
                '{"action":"tool","tool":"tool_name","arguments":{}}',
                "or",
                '{"action":"final","answer":"..."}',
                "or",
                '{"action":"ask_user","question":"..."}',
                f"Workspace root: {workspace_root}",
                f"Available tools JSON: {json.dumps(tools, ensure_ascii=True)}",
                f"Memory JSON: {json.dumps(memory_block or {}, ensure_ascii=True)[:6000]}",
                f"Observations JSON: {json.dumps(observations, ensure_ascii=True)[:10000]}",
                f"User request: {user_text}",
            ]
        )
        payload = {"model": model, "prompt": prompt, "stream": False, "format": "json"}
        response = self._request("POST", "/api/generate", payload)
        raw_body = str(response.get("response", "")).strip()
        parsed = self._parse_json_object(raw_body)
        if not isinstance(parsed, dict):
            raise ValueError("Ollama autonomous action response was not valid JSON.")
        action = str(parsed.get("action", "") or "").strip().lower()
        if action not in {"tool", "final", "ask_user"}:
            raise ValueError(f"Unsupported autonomous action: {action}")
        parsed["action"] = action
        if isinstance(parsed.get("arguments"), dict):
            return parsed
        parsed["arguments"] = {}
        return parsed

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(
            f"{self.settings.base_url.rstrip('/')}{path}",
            method=method,
            data=body,
            headers=headers,
        )
        with request.urlopen(req, timeout=self.settings.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def _load_settings(
        self,
        *,
        config_path: str | Path | None = None,
        env_file: str | Path | None = None,
    ) -> OllamaSettings:
        result, config = load_config(config_path=config_path, env_file=env_file)
        if not result.ok or config is None:
            return OllamaSettings()
        primary = config.llm.primary
        return OllamaSettings(
            base_url=primary.base_url,
            model=primary.model,
            timeout_seconds=primary.timeout_seconds,
        )

    def _parse_json_object(self, text: str) -> dict[str, Any] | None:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            try:
                start = cleaned.index("{")
                end = cleaned.rindex("}") + 1
            except ValueError:
                return None
            try:
                return json.loads(cleaned[start:end])
            except json.JSONDecodeError:
                return None

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]

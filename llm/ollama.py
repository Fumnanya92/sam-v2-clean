"""Minimal Ollama client for Sam v2 understanding tasks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import request

from sam_v2.config import load_config


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
                "You are Sam v2's autonomous request understanding layer.",
                "Return JSON only.",
                "Interpret the user's meaning, not just keywords.",
                "Use the closest supported intent when the request clearly maps to one.",
                "Do not invent unsupported capabilities.",
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
                "- scaffold_project: {\"name\": \"...\", \"project_type\": \"html_game\"}",
                "- plan_project: {\"query\": \"project name or id\"}",
                "- show_delegation: {\"query\": \"project name or id\"}",
                "- show_project_progress: {\"query\": \"project name or id\"}",
                "- show_project_status: {\"query\": \"project name or id\"}",
                "- execute_project_task: {\"query\": \"project name or id\", \"task_name\": \"...\"}",
                "- run_project: {\"query\": \"project name or id\"}",
                "- inspect_project_repo: {\"query\": \"project name or id\"}",
                "- inspect_git_state: {\"query\": \"project name or id\"}",
                "- read_file: {\"path\": \"...\"}",
                "- open_file: {\"path\": \"...\"}",
                "- list_directory: {\"path\": \"...\"}",
                "- open_folder: {\"query\": \"folder name or path\"}",
                "- open_project_folder: {\"query\": \"project name or id\"}",
                "- inspect_workspace_cleanup: {\"scope\": \"all|projects|runtime\"}",
                "- cleanup_workspace_duplicates: {\"scope\": \"all|projects|runtime\"}",
                "- count_tictac_projects: {}",
                "- push_changes: {}",
                "- inspect_repo: {}",
                "- plan_request: {}",
                "- chat: {}",
                "For scaffold_project, only choose it when the user is actually asking to create/build/start a new project.",
                "For count_tictac_projects, use it when the user asks how many tic-tac projects exist or asks about previously created tic-tac games.",
                "For open_folder and open_file, prefer them when the user asks to open a local folder or file on the machine.",
                "For inspect_workspace_cleanup, use it when the user asks to inspect, organize, or find duplicates in sam_v2/workspace.",
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
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }
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

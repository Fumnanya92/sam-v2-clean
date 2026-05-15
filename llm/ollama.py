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
class OllamaWorkKindOutput:
    work_kind: str
    requires_repo_code: bool
    reason: str = ""
    confidence: str = "low"
    project_query: str = ""


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
        capability_text = ", ".join(capabilities)
        projects_json = json.dumps(known_projects or [], ensure_ascii=True)
        
        # Extract recent conversation history for context
        recent_conversation = ""
        if isinstance(memory_block, dict):
            long_term = memory_block.get("long_term", {})
            if isinstance(long_term, dict):
                long_term_value = long_term.get("value", {})
                if isinstance(long_term_value, dict):
                    conversation_items = long_term_value.get("recent_conversation", [])
                    if isinstance(conversation_items, list) and conversation_items:
                        # Build conversation context from recent turns
                        recent_turns = []
                        for item in conversation_items[-10:]:  # Last 10 turns for context
                            if isinstance(item, dict):
                                role = item.get("role", "unknown")
                                message = item.get("message", "")
                                if message:
                                    recent_turns.append(f"{role.capitalize()}: {message}")
                        if recent_turns:
                            recent_conversation = "Recent conversation context:\n" + "\n".join(recent_turns) + "\n"
        
        # Build memory summary without recent conversation to avoid duplication
        memory_summary = {}
        if isinstance(memory_block, dict):
            memory_summary = {k: v for k, v in memory_block.items() if k != "long_term"}
            if memory_block.get("long_term"):
                long_term_value = memory_block["long_term"].get("value", {})
                if isinstance(long_term_value, dict):
                    memory_summary["facts"] = long_term_value.get("relevant_facts", [])[:5]
                    memory_summary["lessons"] = long_term_value.get("relevant_lessons", [])[:3]
        
        memory_json = json.dumps(memory_summary, ensure_ascii=True)
        
        # Check if a coding model (not local) is actively selected
        active_coding_model = ""
        detected_intent = ""
        if isinstance(memory_block, dict):
            coding_model_info = memory_block.get("coding_model", {})
            if isinstance(coding_model_info, dict):
                coding_model_value = coding_model_info.get("value", {})
                if isinstance(coding_model_value, dict):
                    active_coding_model = str(coding_model_value.get("active_coding_model", "")).strip()
            detected_intent_info = memory_block.get("detected_intent", "")
            if isinstance(detected_intent_info, dict):
                detected_intent_info = detected_intent_info.get("value", "")
            detected_intent = str(detected_intent_info or "").strip()
        
        coding_model_instruction = ""
        if active_coding_model and active_coding_model not in {"local", ""}:
            coding_model_instruction = (
                f"ACTIVE CODING MODEL: {active_coding_model} is selected. "
                "Route to autonomous_request only when the compact context detected_intent is coding, debugging, or review, "
                "or when the user explicitly asks to implement, edit code/files, fix code, run tests, generate code, or review a repo. "
                "Do not route normal conversation, planning, architecture discussion, or thinking aloud into coding automatically.\n"
            )
        
        prompt = "\n".join(
            [
                "You are Sam's autonomous request understanding layer.",
                "Return JSON only.",
                "Interpret the user's meaning, not just keywords.",
                "Use the closest supported intent when the request clearly maps to one.",
                "Do not invent unsupported capabilities.",
                "Do not hardcode or assume any specific project name unless it appears in the user's request or Known projects JSON.",
                "If the user is asking a question about existing work, do not convert it into a create action.",
                coding_model_instruction,
                "CRITICAL INSTRUCTION: Use recent conversation context to understand follow-up messages.",
                "If the user previously asked a question and now is providing information (path, filename, folder, directory, confirmation), recognize this as answering the prior question, NOT as a new ambiguous request.",
                "Patterns that indicate follow-up information (NOT clarification candidates):",
                "  - User previously asked for a file/path, now provides a path (Windows or Unix style)",
                "  - User previously asked for confirmation, now says yes/no/confirm",
                "  - User previously asked for details, now provides specific details",
                "  - Recent conversation shows same topic/intent from 1-2 turns ago",
                "When you see a bare path like 'C:\\Users\\...' or '/home/...' in recent context of a file search, it's the USER ANSWERING YOUR QUESTION, not a new request.",
                "Action: Map the path to list_directory, open_folder, or read_file based on context. Set needs_clarification=false.",
                "Only ask for clarification if the request is genuinely ambiguous AND cannot be understood from recent conversation history AND is not a follow-up information provision.",
                "If the request does not map cleanly to a supported action, use intent chat with confidence.",
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
                "- set_coding_model: {\"model\": \"codex|claude|local\"}",
                "- show_coding_model: {}",
                "- delegate_coding_task: {\"query\": \"project name, id, or local repo path\", \"goal\": \"...\"}",
                "- plan_request: {}",
                "- chat: {}",
                "Do not choose delegate_coding_task directly for simple conversation. It is only for confirmed repo/code work after Sam's planner delegation gate.",
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
                f"Compact detected intent: {detected_intent}",
                recent_conversation,
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

    def decide_action_gate(
        self,
        user_text: str,
        *,
        memory_block: dict[str, Any] | None = None,
        capabilities: list[str] | None = None,
        known_projects: list[dict[str, str]] | None = None,
        workspace_root: str = "",
    ) -> dict[str, Any]:
        """LLM judgment for whether Sam should take external action now."""
        model = self.resolve_model()
        prompt = "\n".join(
            [
                "You are Sam's centralized Action Gate.",
                "Return JSON only. Do not include markdown fences.",
                "Answer one question: should Sam take external action now, or only reply conversationally?",
                "",
                "External action includes calling Codex, delegating to a coding model, running planner/executor/reviewer, editing files, inspecting a repo, reading files, querying Firebase/Firestore, setting environment variables, running commands/tests, opening a browser/desktop, deploying, pushing git changes, or using tools.",
                "",
                "Default to should_act=false. If uncertain, return should_act=false.",
                "Mentioning technical words is not enough.",
                "Only return should_act=true when the user is clearly instructing Sam to do something now.",
                "The user may discuss coding, testing, fixing, bugs, files, browser automation, repositories, implementation, Codex, Firebase, Firestore, SDK keys, or service-account files without asking for action.",
                "",
                "Important distinction:",
                "- 'When I say go fix it, then you should use Codex' is conversation about future behavior, so should_act=false.",
                "- 'Give this to Codex to check resident docs at C:\\path\\key.json' is a current instruction to use Codex/tools, so should_act=true.",
                "- 'Use this SDK key path so Codex can query Firestore now' is a current instruction, so should_act=true.",
                "",
                "Examples where should_act=false:",
                "- I will test you when I am done.",
                "- We need to test this later.",
                "- I am still fixing you.",
                "- The button may need fixing.",
                "- I want browser automation later.",
                "- When I say go fix it, then you should use Codex.",
                "- We should add memory before coding.",
                "- I'm talking about the code, not asking you to code.",
                "- Sam should know when to code.",
                "- I don't want you to call Codex in the middle of conversation.",
                "",
                "Examples where should_act=true:",
                "- Go run the tests now.",
                "- Use Codex to fix the login button.",
                "- Open the repo and inspect app.py.",
                "- Create a new file called memory_debug.py.",
                "- Deploy this now.",
                "- Push the changes to git.",
                "- Check the repo and tell me why the button is broken.",
                "- Give this to Codex to check resident docs using C:\\Users\\me\\key.json.",
                "- Set env so Codex can query Firestore and tell me the April levy.",
                "",
                "Choose action_type from: none, read_only, code_change, test_run, browser, desktop, deploy, git, other.",
                "Use read_only for inspection/querying data/files without code edits.",
                "Use code_change for Codex handoff when code changes may be needed.",
                "Use other only for explicit runtime controls or actions that do not fit another type.",
                'JSON shape: {"should_act": true|false, "action_type": "none|read_only|code_change|test_run|browser|desktop|deploy|git|other", "reason": "...", "confidence": 0.0}',
                f"Available capabilities: {', '.join(capabilities or [])}",
                f"Workspace root: {workspace_root}",
                f"Known projects JSON: {json.dumps(known_projects or [], ensure_ascii=True)[:4000]}",
                f"Memory JSON: {json.dumps(memory_block or {}, ensure_ascii=True)[:6000]}",
                f"User message: {user_text}",
            ]
        )
        payload = {"model": model, "prompt": prompt, "stream": False, "format": "json"}
        response = self._request("POST", "/api/generate", payload)
        raw_body = str(response.get("response", "")).strip()
        parsed = self._parse_json_object(raw_body)
        if not isinstance(parsed, dict):
            raise ValueError("Ollama action-gate response was not valid JSON.")
        return parsed

    def generate_conversational_response(
        self,
        user_text: str,
        *,
        memory_block: dict[str, Any] | None = None,
        action_gate_reason: str = "",
    ) -> str:
        """Generate Sam's normal conversational reply through the local LLM."""
        model = self.resolve_model()
        prompt = self._build_conversational_prompt(
            user_text=user_text,
            memory_block=memory_block,
            action_gate_reason=action_gate_reason,
        )
        payload = {"model": model, "prompt": prompt, "stream": False}
        response = self._request("POST", "/api/generate", payload)
        response_text = str(response.get("response", "") or "").strip()
        return self._clean_assistant_prefix(response_text)

    def _build_conversational_prompt(
        self,
        *,
        user_text: str,
        memory_block: dict[str, Any] | None,
        action_gate_reason: str,
    ) -> str:
        context_lines: list[str] = []

        if isinstance(memory_block, dict):
            last_project = memory_block.get("daily_state", {}).get("last_project_name", {}).get("value")
            if last_project:
                context_lines.append(f"- Current project context: {last_project}")

            detected_intent = memory_block.get("detected_intent", {})
            if isinstance(detected_intent, dict):
                detected_intent = detected_intent.get("value", "")
            if detected_intent:
                context_lines.append(f"- Conversation label for context only: {detected_intent}")

            long_term = memory_block.get("long_term", {})
            if isinstance(long_term, dict):
                long_term_value = long_term.get("value", {})
                if isinstance(long_term_value, dict):
                    facts = long_term_value.get("relevant_facts", [])[:4]
                    lessons = long_term_value.get("relevant_lessons", [])[:3]
                    if facts:
                        context_lines.append(f"- Relevant remembered facts: {json.dumps(facts, ensure_ascii=True)}")
                    if lessons:
                        context_lines.append(f"- Relevant remembered lessons: {json.dumps(lessons, ensure_ascii=True)}")

                    recent_turns = []
                    conversation_items = long_term_value.get("recent_conversation", [])
                    if isinstance(conversation_items, list):
                        for item in conversation_items[-8:]:
                            if isinstance(item, dict):
                                role = str(item.get("role", "unknown")).strip() or "unknown"
                                message = str(item.get("message", "")).strip()
                                if message:
                                    recent_turns.append(f"{role.capitalize()}: {message}")
                    if recent_turns:
                        context_lines.append("Recent conversation:\n" + "\n".join(recent_turns))

        if action_gate_reason:
            context_lines.append(f"- Action gate result: no external action now. Reason: {action_gate_reason}")

        context = "\n".join(context_lines) if context_lines else "No special context."

        return "\n".join(
            [
                "You are Sam, the user's conversational AI assistant.",
                "Respond naturally to the user's latest message.",
                "The action gate has decided this turn is conversational only, so do not claim you ran tools, inspected files, called Codex, edited code, opened a browser, or executed commands.",
                "The user may mention coding, testing, fixing, repositories, browser automation, or implementation as part of normal conversation.",
                "Do not treat those mentions as requests to act unless the action gate has approved action, which it has not for this prompt.",
                "Keep the reply concise and human. Match the user's tone.",
                "Do not return JSON. Do not prefix with 'Sam:' or 'Assistant:'.",
                "",
                "Context:",
                context,
                "",
                f"User: {user_text}",
                "Sam:",
            ]
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
        
        # Check what tools have been used so far
        used_tools = {str(obs.get("tool", "")).lower() for obs in observations if isinstance(obs, dict)}
        has_scan_only = used_tools and all("scan" in tool or "search" in tool for tool in used_tools)
        no_file_reads = not any("read" in tool for tool in used_tools)
        must_read_files = has_scan_only and no_file_reads
        
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
                "** CRITICAL RULE **: If observations contain scan/search results (finding patterns in files) but NO file reads yet, you MUST NOT answer 'final'. You MUST use read_file or read_file_region to examine the actual file content. After scan results, always read the relevant files to extract concrete information before providing a final answer.",
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
                (">> ENFORCEMENT: You found scan results but no file reads. NEXT ACTION MUST BE: read_file_region or read_file." if must_read_files else ""),
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

    def classify_work_kind(
        self,
        user_text: str,
        *,
        memory_block: dict[str, Any] | None = None,
        known_projects: list[dict[str, str]] | None = None,
    ) -> OllamaWorkKindOutput:
        model = self.resolve_model()
        prompt = "\n".join(
            [
                "You are Sam's delegation gate.",
                "Return JSON only. Answer one question: should this user request be handled conversationally/local, or delegated to an external coding model because it requires reading or editing a repo/code project?",
                "Choose repo_code only when the request requires inspecting source files, debugging code, writing code, editing files, running tests, reviewing a repository, or reasoning over a project codebase.",
                "Choose conversational for greetings, general questions, status questions, simple explanations, capability questions, or anything that can be answered without reading/editing a repo.",
                "Do not use domain keywords as rules. Decide from the goal and available context.",
                'JSON shape: {"work_kind":"conversational|repo_code","requires_repo_code":true|false,"reason":"...","confidence":"low|medium|high","project_query":"optional project name/path from user or memory"}',
                f"Known projects JSON: {json.dumps(known_projects or [], ensure_ascii=True)}",
                f"Memory JSON: {json.dumps(memory_block or {}, ensure_ascii=True)[:6000]}",
                f"User request: {user_text}",
            ]
        )
        payload = {"model": model, "prompt": prompt, "stream": False, "format": "json"}
        response = self._request("POST", "/api/generate", payload)
        raw_body = str(response.get("response", "")).strip()
        parsed = self._parse_json_object(raw_body)
        if not isinstance(parsed, dict):
            raise ValueError("Ollama work-kind response was not valid JSON.")
        work_kind = str(parsed.get("work_kind", "conversational") or "conversational").strip().lower()
        if work_kind not in {"conversational", "repo_code"}:
            work_kind = "conversational"
        return OllamaWorkKindOutput(
            work_kind=work_kind,
            requires_repo_code=bool(parsed.get("requires_repo_code", work_kind == "repo_code")),
            reason=str(parsed.get("reason", "") or ""),
            confidence=str(parsed.get("confidence", "low") or "low"),
            project_query=str(parsed.get("project_query", "") or ""),
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

    @staticmethod
    def _clean_assistant_prefix(text: str) -> str:
        cleaned = text.strip()
        for prefix in ("Assistant:", "Sam:"):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):].strip()
        return cleaned

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]

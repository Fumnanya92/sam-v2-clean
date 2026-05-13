"""Observation-driven autonomous runtime.

This module owns the read-only operational loop that used to live inside
``intents.router``. The router may still register a compatibility capability
named ``autonomous_request``, but tool choice, execution, observations, worker
tracking, and final synthesis belong here.
"""

from __future__ import annotations

from dataclasses import dataclass
import html
from pathlib import Path
import re
from typing import Any, Callable

from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult
from core.operational_tools import (
    OperationalToolContext,
    build_default_operational_registry,
)
from tools import CodebaseCleanupService
from workers import worker_monitor
from workers.names import resolve_worker_identity


@dataclass(frozen=True)
class RuntimeServices:
    resolve_project_or_directory: Callable[[str, dict[str, Any] | None], tuple[SamResult, Path | None]]
    service_result: Callable[..., SamResult]
    check_python_syntax: Callable[[Path, Any], SamResult]
    inspect_recent_changes: Callable[[Path, Any], SamResult]


class AutonomousRuntime:
    """Run read-only operational work as a plan-act-observe loop."""

    def __init__(
        self,
        *,
        model_client: Any,
        workspace_root: str | Path,
        awareness: Any,
        project_registry: Any,
        tool_executor: Any,
        project_inspector: Any,
        workspace_cleanup: Any,
        services: RuntimeServices,
    ) -> None:
        self.model_client = model_client
        self.workspace_root = Path(workspace_root)
        self.awareness = awareness
        self.project_registry = project_registry
        self.tool_executor = tool_executor
        self.project_inspector = project_inspector
        self.workspace_cleanup = workspace_cleanup
        self.services = services
        self.tool_registry = build_default_operational_registry(
            OperationalToolContext(
                awareness=self.awareness,
                project_registry=self.project_registry,
                tool_executor=self.tool_executor,
                project_inspector=self.project_inspector,
                workspace_cleanup=self.workspace_cleanup,
                resolve_project_or_directory=self.services.resolve_project_or_directory,
                service_result=self.services.service_result,
                check_python_syntax=self.services.check_python_syntax,
                inspect_recent_changes=self.services.inspect_recent_changes,
                memory_roots=self._memory_roots,
            )
        )

    def run(self, request: Any, memory_block: dict[str, Any] | None) -> SamResult:
        if not self.model_client.is_available():
            return SamResult(
                status="failed",
                summary="My local reasoning model is unavailable, so I cannot run an autonomous investigation.",
                error_type=ErrorType.MISSING_CAPABILITY,
                error_message="llm unavailable",
                next_action="ask_user",
                metadata={"intent": getattr(request, "intent", "autonomous_request"), "source": getattr(request, "source", "")},
            )

        tools = self.tool_manifest()
        observations: list[dict[str, Any]] = []
        tool_trace: list[dict[str, Any]] = []

        for step_index in range(5):
            try:
                decision = self.model_client.choose_autonomous_action(
                    user_text=getattr(request, "raw_text", ""),
                    tools=tools,
                    observations=observations,
                    memory_block=memory_block,
                    workspace_root=str(self.workspace_root),
                )
            except Exception as exc:
                if observations:
                    return self._final_from_observations(request, observations, tool_trace, str(exc))
                fallback = self._fallback_read(request, memory_block, str(exc))
                if fallback is not None:
                    return fallback
                return SamResult(
                    status="failed",
                    summary="Autonomous reasoning failed before I could choose a safe tool.",
                    error_type=ErrorType.TOOL_FAILED,
                    error_message=str(exc),
                    next_action="retry",
                    metadata={"intent": getattr(request, "intent", "autonomous_request"), "source": getattr(request, "source", "")},
                )

            action = str(decision.get("action", "")).lower()
            if action == "final":
                answer = str(decision.get("answer", "")).strip() or self._observations_summary(observations)
                return SamResult(
                    status="success",
                    summary=answer,
                    next_action="stop",
                    metadata={
                        "intent": "autonomous_request",
                        "source": getattr(request, "source", ""),
                        "confidence": getattr(request, "confidence", "low"),
                        "autonomous_steps": len(tool_trace),
                        "tool_trace": tool_trace,
                        "observations": observations,
                    },
                )

            if action == "ask_user":
                question = str(decision.get("question", "")).strip() or "I need one more detail before I can continue."
                return SamResult(
                    status="success",
                    summary=question,
                    next_action="ask_user",
                    metadata={
                        "intent": "clarify",
                        "source": "autonomous_loop",
                        "autonomous_steps": len(tool_trace),
                        "tool_trace": tool_trace,
                        "observations": observations,
                    },
                )

            if action != "tool":
                observations.append({"step": step_index + 1, "status": "failed", "summary": "Model chose an unsupported action."})
                continue

            tool_name = str(decision.get("tool", "")).strip()
            arguments = decision.get("arguments", {})
            if not isinstance(arguments, dict):
                arguments = {}

            worker_identity = self._worker_identity(tool_name)
            task = worker_monitor.create_task(
                name=f"autonomous_{tool_name or 'unknown'}",
                worker_type=worker_identity.role,
                worker_name=worker_identity.name,
                description=f"Autonomous step {step_index + 1}: {tool_name}",
                worker_role=worker_identity.role,
                responsibility=worker_identity.responsibility,
            )
            worker_monitor.mark_running(task.task_id)
            worker_monitor.append_output(task.task_id, f"Tool: {tool_name}")
            if arguments:
                worker_monitor.append_output(task.task_id, f"Arguments: {arguments}")

            tool_result = self.execute_tool(tool_name, arguments, request, memory_block)
            if tool_result.ok:
                worker_monitor.append_output(task.task_id, f"Observation: {tool_result.summary}")
                worker_monitor.mark_done(task.task_id)
            else:
                failure = tool_result.error_message or tool_result.summary
                worker_monitor.append_output(task.task_id, f"Failure: {failure}")
                worker_monitor.mark_failed(task.task_id, failure)

            observation = self._compact_result(tool_name, arguments, tool_result, step_index + 1)
            observations.append(observation)
            tool_trace.append(
                {
                    "step": step_index + 1,
                    "tool": tool_name,
                    "worker_name": worker_identity.name,
                    "worker_type": worker_identity.role,
                    "worker_role": worker_identity.role,
                    "worker_responsibility": worker_identity.responsibility,
                    "arguments": arguments,
                    "status": tool_result.status,
                    "summary": tool_result.summary,
                }
            )

            if tool_result.next_action == "ask_user" and not tool_result.ok:
                return SamResult(
                    status="success",
                    summary=tool_result.summary,
                    next_action="ask_user",
                    metadata={
                        "intent": "clarify",
                        "source": "autonomous_loop",
                        "autonomous_steps": len(tool_trace),
                        "tool_trace": tool_trace,
                        "observations": observations,
                    },
                )

        return self._final_from_observations(request, observations, tool_trace, "")

    def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        parent_request: Any,
        memory_block: dict[str, Any] | None,
    ) -> SamResult:
        arguments = _normalize_arguments(arguments)
        return self.tool_registry.execute(tool_name, arguments, parent_request, memory_block)

    def tool_manifest(self) -> list[dict[str, Any]]:
        return self.tool_registry.manifest()

    def _fallback_read(self, request: Any, memory_block: dict[str, Any] | None, reason: str) -> SamResult | None:
        roots = self._memory_roots(memory_block)
        explicit_path = _local_path_from_text(getattr(request, "raw_text", ""))
        if explicit_path:
            roots.insert(0, explicit_path)
        parameters = getattr(request, "parameters", {})
        query = str(parameters.get("query", "") if isinstance(parameters, dict) else "").strip()
        if query:
            root_result, root = self.services.resolve_project_or_directory(query, memory_block)
            if root_result.ok and root is not None:
                roots.insert(0, str(root))
        roots = list(dict.fromkeys(root for root in roots if root))
        patterns = _autonomous_search_terms(getattr(request, "raw_text", ""))
        if not roots or not patterns:
            return None

        root = Path(roots[0])
        scan_result, report = CodebaseCleanupService(root).inspect(patterns)
        matches = report.matches[:25]
        summary = _plain_english_scan_summary(
            request_text=getattr(request, "raw_text", ""),
            root=root,
            patterns=patterns,
            matches=report.matches,
            scanned_files=report.scanned_files,
            fallback_reason=reason,
        )
        return SamResult(
            status="success" if scan_result.ok else scan_result.status,
            summary=summary,
            error_type=None if scan_result.ok else scan_result.error_type,
            error_message=None if scan_result.ok else scan_result.error_message or reason,
            next_action="stop" if scan_result.ok else "ask_user",
            metadata={
                "intent": "autonomous_request",
                "source": "autonomous_fallback",
                "fallback_reason": reason,
                "root_path": str(root),
                "patterns": patterns,
                "match_count": len(report.matches),
                "matches": [match.__dict__ for match in matches],
                "observations": [
                    {
                        "step": 1,
                        "tool": "scan_codebase_patterns",
                        "status": scan_result.status,
                        "summary": scan_result.summary,
                    }
                ],
            },
        )

    @staticmethod
    def _worker_identity(tool_name: str) -> Any:
        return resolve_worker_identity(tool_name=tool_name, action_category="read_data")

    @staticmethod
    def _memory_roots(memory_block: dict[str, Any] | None) -> list[str]:
        daily_state = memory_block.get("daily_state", {}) if isinstance(memory_block, dict) else {}
        roots = []
        for key in ("last_project_root_path", "last_file_path"):
            value = str(daily_state.get(key, {}).get("value", "")).strip()
            if value:
                roots.append(str(Path(value).parent if key == "last_file_path" else Path(value)))
        return roots

    @staticmethod
    def _compact_result(tool_name: str, arguments: dict[str, Any], result: SamResult, step: int) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        for key in (
            "intent",
            "path",
            "root_path",
            "repo_root",
            "branch",
            "is_clean",
            "changed_files",
            "entries",
            "entry_count",
            "patterns",
            "match_count",
            "matches",
            "errors",
            "files_scanned",
            "tasks",
            "tools",
            "projects",
            "content",
        ):
            if key in result.metadata:
                value = result.metadata[key]
                if key == "content" and isinstance(value, str):
                    value = value[:4000]
                elif isinstance(value, list):
                    value = value[:25]
                metadata[key] = value
        return {
            "step": step,
            "tool": tool_name,
            "arguments": arguments,
            "status": result.status,
            "summary": result.summary,
            "error": result.error_message,
            "metadata": metadata,
        }

    def _final_from_observations(
        self,
        request: Any,
        observations: list[dict[str, Any]],
        tool_trace: list[dict[str, Any]],
        reason: str,
    ) -> SamResult:
        summary = self._observations_summary(observations)
        if reason:
            summary += f" I stopped after observation because final synthesis failed: {reason}"
        return SamResult(
            status="success" if observations else "failed",
            summary=summary,
            error_type=None if observations else ErrorType.TOOL_FAILED,
            error_message=reason or None,
            next_action="stop" if observations else "retry",
            metadata={
                "intent": "autonomous_request",
                "source": getattr(request, "source", ""),
                "confidence": getattr(request, "confidence", "low"),
                "autonomous_steps": len(tool_trace),
                "tool_trace": tool_trace,
                "observations": observations,
            },
        )

    @staticmethod
    def _observations_summary(observations: list[dict[str, Any]]) -> str:
        if not observations:
            return "I could not gather enough observations to answer."
        lines = [str(item.get("summary", "")).strip() for item in observations if str(item.get("summary", "")).strip()]
        return " ".join(lines[-3:]) or "I gathered observations, but they did not include a clear answer."


def _autonomous_search_terms(text: str) -> list[str]:
    text_without_paths = _strip_local_paths(text)
    words = re.findall(r"[A-Za-z0-9_.-]+", text_without_paths.lower())
    ignored = {
        "a",
        "an",
        "app",
        "are",
        "can",
        "check",
        "could",
        "for",
        "gave",
        "here",
        "in",
        "is",
        "it",
        "me",
        "need",
        "please",
        "project",
        "the",
        "their",
        "this",
        "to",
        "when",
        "you",
    }
    terms = [word for word in words if len(word) > 2 and word not in ignored and not _looks_like_path_fragment(word)]
    expanded: list[str] = []
    if any(term in {"levy", "levies"} for term in terms):
        expanded.extend(["levies", "levy"])
    if any(term in {"resident", "residents"} for term in terms):
        expanded.extend(["residents", "resident"])
    if "due" in terms or "due" in words:
        expanded.extend(["due_date", "dueDate", "due"])
    expanded.extend(term for term in terms if term not in expanded)
    return list(dict.fromkeys(expanded))[:8]


def _plain_english_scan_summary(
    *,
    request_text: str,
    root: Path,
    patterns: list[str],
    matches: list[Any],
    scanned_files: int,
    fallback_reason: str,
) -> str:
    if not matches:
        return (
            f"I searched the project at {root} for the relevant terms ({', '.join(patterns)}) "
            f"and did not find matching source or data files. I could not confirm the answer from this scan alone."
        )

    ranked = _rank_relevant_matches(matches, patterns)
    file_counts: dict[str, int] = {}
    for match in ranked:
        file_counts[match.path] = file_counts.get(match.path, 0) + 1
    files = list(file_counts)[:5]
    file_text = ", ".join(files)
    month_hint = _month_hint(request_text)
    due_hint = "due date" if any("due" in pattern.lower() for pattern in patterns) else "requested value"
    return (
        f"I searched {scanned_files} project file(s) in {root} for the {due_hint}"
        f"{f' around {month_hint}' if month_hint else ''}. "
        f"I found relevant references, but I cannot honestly confirm the final answer from a text scan alone yet. "
        f"The strongest places to inspect next are: {file_text}. "
        f"I filtered out dependency/build folders and kept the raw evidence in the activity stream."
    )


def _rank_relevant_matches(matches: list[Any], patterns: list[str]) -> list[Any]:
    important = ("levy", "levies", "due_date", "duedate", "due", "resident", "residents")

    def score(match: Any) -> tuple[int, int, str]:
        path = str(getattr(match, "path", "")).lower()
        pattern = str(getattr(match, "pattern", "")).lower()
        line = str(getattr(match, "line", "")).lower()
        value = 0
        if any(token in pattern for token in important):
            value += 4
        if any(token in path for token in ("levy", "levies", "resident", "firebase", "firestore")):
            value += 3
        if any(token in line for token in ("due_date", "duedate", "due date", "resident_levies")):
            value += 3
        if any(path.endswith(ext) for ext in (".md", ".txt")):
            value -= 1
        return (-value, int(getattr(match, "line_number", 0)), path)

    return sorted(matches, key=score)


def _local_path_from_text(text: str) -> str:
    match = re.search(r"[A-Za-z]:[\\/][^\r\n\"']+", text)
    if not match:
        return ""
    return str(_normalize_value(match.group(0).strip().rstrip(".,;")))


def _strip_local_paths(text: str) -> str:
    return re.sub(r"[A-Za-z]:[\\/][^\r\n\"']+", " ", text)


def _looks_like_path_fragment(word: str) -> bool:
    lowered = word.lower().strip(".-_")
    return lowered in {"c", "users", "desktop", "darey", "dell.com"} or "\\" in word or "/" in word


def _month_hint(text: str) -> str:
    months = {
        "jan": "January",
        "january": "January",
        "feb": "February",
        "february": "February",
        "mar": "March",
        "march": "March",
        "apr": "April",
        "april": "April",
        "may": "May",
        "jun": "June",
        "june": "June",
        "jul": "July",
        "july": "July",
        "aug": "August",
        "august": "August",
        "sep": "September",
        "sept": "September",
        "september": "September",
        "oct": "October",
        "october": "October",
        "nov": "November",
        "november": "November",
        "dec": "December",
        "december": "December",
    }
    words = re.findall(r"[A-Za-z]+", text.lower())
    for word in words:
        if word in months:
            return months[word]
    return ""


def _normalize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _normalize_value(value) for key, value in arguments.items()}


def _normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _normalize_arguments(value)
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if not isinstance(value, str):
        return value

    cleaned = value.strip()
    for _ in range(5):
        unescaped = html.unescape(cleaned)
        if unescaped == cleaned:
            break
        cleaned = unescaped
    cleaned = cleaned.strip().strip("'\"`")
    return cleaned

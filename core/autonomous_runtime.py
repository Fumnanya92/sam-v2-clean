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
from core.autonomy_policy import (
    AutonomousDecisionPolicy,
    evidence_path_score,
    ranked_evidence_matches,
    strongest_evidence_match,
    synthesize_evidence_answer,
)
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
        self.fallback_policy = AutonomousDecisionPolicy()
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
        tools = self.tool_manifest()
        observations: list[dict[str, Any]] = []
        tool_trace: list[dict[str, Any]] = []

        for step_index in range(5):
            decision = self._choose_next_action(
                request=request,
                tools=tools,
                observations=observations,
                memory_block=memory_block,
            )

            action = str(decision.get("action", "")).lower()
            
            # ENFORCEMENT: If we only have scan results and no file reads, force file reading before allowing "final"
            if action == "final":
                used_tools = {str(obs.get("tool", "")).lower() for obs in observations if isinstance(obs, dict)}
                has_scan = any("scan" in tool or "search" in tool for tool in used_tools)
                has_read = any("read" in tool for tool in used_tools)
                if has_scan and not has_read and observations:  # Only enforce if we have observations with scans
                    # Force file reading before finalizing
                    forced_read = AutonomousRuntime._force_read_from_scan(observations, tools)
                    if forced_read is not None:
                        decision = forced_read
                        action = "tool"
            
            if action == "final":
                guarded_final = self._guard_final_decision(decision, tools, observations, memory_block, request)
                if guarded_final is not None:
                    decision = guarded_final
                    action = str(decision.get("action", "")).lower()
            if action == "final":
                failed_final = self._guard_failed_final_decision(decision, observations, tool_trace)
                if failed_final is not None:
                    return failed_final
                answer = str(decision.get("answer", "")).strip() or self._observations_summary(observations)
                if _thin_final_answer(answer) and observations:
                    answer = synthesize_evidence_answer(getattr(request, "raw_text", ""), observations)
                metadata = self._evidence_metadata(observations)
                metadata.update(
                    {
                        "intent": "autonomous_request",
                        "source": str(decision.get("source") or getattr(request, "source", "")),
                        "confidence": getattr(request, "confidence", "low"),
                        "autonomous_steps": len(tool_trace),
                        "tool_trace": tool_trace,
                        "observations": observations,
                    }
                )
                return SamResult(
                    status="success",
                    summary=answer,
                    next_action="stop",
                    metadata=metadata,
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
            guard_result = self._guard_tool_decision(tool_name, arguments, observations)
            if guard_result is not None:
                if guard_result.get("action") == "final":
                    return self._final_from_observations(
                        request,
                        observations,
                        tool_trace,
                        str(guard_result.get("reason", "")),
                    )
                tool_name = str(guard_result.get("tool", tool_name))
                replacement_arguments = guard_result.get("arguments", arguments)
                arguments = replacement_arguments if isinstance(replacement_arguments, dict) else arguments

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
                continue

        return self._final_from_observations(request, observations, tool_trace, "")

    def _choose_next_action(
        self,
        *,
        request: Any,
        tools: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        memory_block: dict[str, Any] | None,
    ) -> dict[str, Any]:
        # ALWAYS use fallback policy first to get its intelligent decision
        fallback_decision = self.fallback_policy.decide(
            user_text=getattr(request, "raw_text", ""),
            tools=tools,
            observations=observations,
            memory_block=memory_block,
            workspace_root=str(self.workspace_root),
        )
        
        # If fallback says "tool" and it's a file read after scans, trust it
        if str(fallback_decision.get("action", "")).lower() == "tool":
            tool_name = str(fallback_decision.get("tool", "")).lower()
            if "read" in tool_name:
                # This is the fallback policy instructing file reading after scans - use it
                return fallback_decision
        
        # For other cases, try LLM if available
        if self.model_client.is_available():
            try:
                llm_decision = self.model_client.choose_autonomous_action(
                    user_text=getattr(request, "raw_text", ""),
                    tools=tools,
                    observations=observations,
                    memory_block=memory_block,
                    workspace_root=str(self.workspace_root),
                )
                # If LLM wants to finalize but fallback says an evidence read is still needed, use fallback.
                if (
                    str(llm_decision.get("action", "")).lower() == "final"
                    and str(fallback_decision.get("action", "")).lower() == "tool"
                    and "read" in str(fallback_decision.get("tool", "")).lower()
                ):
                    return fallback_decision
                return llm_decision
            except Exception as exc:
                decision = self.fallback_policy.decide(
                    user_text=getattr(request, "raw_text", ""),
                    tools=tools,
                    observations=observations,
                    memory_block=memory_block,
                    workspace_root=str(self.workspace_root),
                )
                decision.setdefault("source", "autonomous_fallback")
                decision.setdefault("fallback_reason", str(exc))
                return decision

        return fallback_decision

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

    def _guard_tool_decision(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        observations: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if tool_name not in {"read_file", "read_file_region"}:
            return None
        prior_scan = self._latest_scan_observation(observations)
        if not prior_scan:
            return None
        metadata = prior_scan.get("metadata", {})
        if not isinstance(metadata, dict):
            return None
        matches = metadata.get("matches", [])
        patterns = metadata.get("patterns", [])
        if not isinstance(matches, list) or not isinstance(patterns, list):
            return None
        requested_path = str(arguments.get("path", "")).strip()
        pattern_text = [str(item) for item in patterns]
        strongest = strongest_evidence_match(matches, pattern_text, excluded_paths=self._read_paths(observations))
        strongest_path = str(strongest.get("path", "")).strip()
        requested_score = evidence_path_score(requested_path, matches, pattern_text) if requested_path else 0
        strongest_score = evidence_path_score(strongest_path, matches, pattern_text) if strongest_path else 0
        if requested_path and requested_score >= strongest_score and requested_score > 0:
            requested_candidate = Path(requested_path)
            if not requested_candidate.is_absolute() and str(metadata.get("root_path", "")).strip():
                arguments["path"] = str(Path(str(metadata.get("root_path", "")).strip()) / requested_path)
            return None
        replacement_path = str(strongest.get("path", "")).strip()
        if not replacement_path:
            return {
                "action": "final",
                "reason": "no scan result was strong enough to justify reading a file",
            }
        root = str(metadata.get("root_path", "")).strip()
        candidate = Path(replacement_path)
        resolved = str(candidate if candidate.is_absolute() or not root else Path(root) / replacement_path)
        replacement_tool = "read_file_region" if tool_name == "read_file_region" else "read_file"
        replacement_arguments = {**arguments, "path": resolved}
        if replacement_tool == "read_file_region":
            replacement_arguments["line"] = int(strongest.get("line_number", 1) or 1)
            replacement_arguments.setdefault("context_before", 18)
            replacement_arguments.setdefault("context_after", 70)
        return {"action": "tool", "tool": replacement_tool, "arguments": replacement_arguments}

    def _guard_final_decision(
        self,
        decision: dict[str, Any],
        tools: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        memory_block: dict[str, Any] | None,
        request: Any,
    ) -> dict[str, Any] | None:
        prior_scan = self._latest_scan_observation(observations)
        if not prior_scan:
            return None
        read_paths = self._read_paths(observations)
        if len(read_paths) >= 2:
            return None
        available = {str(item.get("name", "")) for item in tools if isinstance(item, dict)}
        if "read_file_region" not in available and "read_file" not in available:
            return None
        metadata = prior_scan.get("metadata", {})
        if not isinstance(metadata, dict):
            return None
        matches = metadata.get("matches", [])
        patterns = metadata.get("patterns", [])
        if not isinstance(matches, list) or not isinstance(patterns, list) or not matches:
            return None
        ranked = ranked_evidence_matches(matches, [str(item) for item in patterns], excluded_paths=read_paths)
        if not ranked:
            return None
        strongest = ranked[0]
        file_path = str(strongest.get("path", "")).strip()
        root = str(metadata.get("root_path", "")).strip()
        if not file_path:
            return None
        candidate = Path(file_path)
        resolved = str(candidate if candidate.is_absolute() or not root else Path(root) / file_path)
        tool_name = "read_file_region" if "read_file_region" in available else "read_file"
        if tool_name == "read_file_region":
            arguments = {
                "path": resolved,
                "line": int(strongest.get("line_number", 1) or 1),
                "context_before": 18,
                "context_after": 70,
            }
        else:
            arguments = {"path": resolved, "max_chars": 12000}
        decision["deferred_answer"] = str(decision.get("answer", ""))
        return {"action": "tool", "tool": tool_name, "arguments": arguments, "source": "evidence_guard"}

    def _guard_failed_final_decision(
        self,
        decision: dict[str, Any],
        observations: list[dict[str, Any]],
        tool_trace: list[dict[str, Any]],
    ) -> SamResult | None:
        if not observations:
            return None
        last = observations[-1]
        if str(last.get("status", "")).lower() in {"success", "ok"}:
            return None
        answer = str(decision.get("answer", "")).strip()
        failure_text = f"{last.get('summary', '')} {last.get('error', '')}".strip()
        if answer and failure_text and answer.lower() not in failure_text.lower() and failure_text.lower() not in answer.lower():
            return None
        metadata = self._evidence_metadata(observations)
        metadata.update(
            {
                "intent": "clarify",
                "source": str(decision.get("source") or "autonomous_loop"),
                "autonomous_steps": len(tool_trace),
                "tool_trace": tool_trace,
                "observations": observations,
            }
        )
        return SamResult(
            status="success",
            summary=(
                "I could not resolve the project or directory from the current context. "
                "I tried the tool, captured the failure, and need a valid project path or registered project name to continue."
            ),
            error_type=ErrorType.FILE_ACCESS_ERROR,
            error_message=str(last.get("error") or last.get("summary") or ""),
            next_action="ask_user",
            metadata=metadata,
        )

    @staticmethod
    def _latest_scan_observation(observations: list[dict[str, Any]]) -> dict[str, Any]:
        for observation in reversed(observations):
            if str(observation.get("tool", "")) == "scan_codebase_patterns":
                return observation
        return {}

    @staticmethod
    def _read_paths(observations: list[dict[str, Any]]) -> set[str]:
        paths: set[str] = set()
        for observation in observations:
            if str(observation.get("tool", "")) not in {"read_file", "read_file_region"}:
                continue
            metadata = observation.get("metadata", {})
            path = str(metadata.get("path", "")).strip() if isinstance(metadata, dict) else ""
            if path:
                paths.add(path.replace("\\", "/").lower())
        return paths

    @staticmethod
    def _force_read_from_scan(observations: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Force reading a file from scan results when no files have been read yet."""
        # Find latest scan observation
        latest_scan = None
        for obs in reversed(observations):
            if str(obs.get("tool", "")) == "scan_codebase_patterns":
                latest_scan = obs
                break
        
        if not latest_scan:
            return None
        
        metadata = latest_scan.get("metadata", {})
        if not isinstance(metadata, dict):
            return None
        
        matches = metadata.get("matches", [])
        if not isinstance(matches, list) or not matches:
            return None
        
        # Get the strongest match (first one, already ranked)
        strongest = matches[0] if matches else None
        if not strongest:
            return None
        
        file_path = str(strongest.get("path", "")).strip()
        line_number = int(strongest.get("line_number", 1) or 1)
        root = str(metadata.get("root_path", "")).strip()
        
        if not file_path:
            return None
        
        # Resolve to absolute path for read operation, but use relative path in decision for guard to validate
        from pathlib import Path
        candidate = Path(file_path)
        resolved = str(candidate if candidate.is_absolute() else Path(root) / file_path if root else candidate)
        
        # Choose read tool
        available_tools = {str(tool.get("name", "")) for tool in tools if isinstance(tool, dict)}
        if "read_file_region" in available_tools:
            return {
                "action": "tool",
                "tool": "read_file_region",
                "arguments": {
                    "path": file_path,  # Keep as relative path so guard can score it against matches
                    "line": line_number,
                    "context_before": 18,
                    "context_after": 70,
                },
            }
        elif "read_file" in available_tools:
            return {
                "action": "tool",
                "tool": "read_file",
                "arguments": {"path": file_path, "max_chars": 12000},  # Keep as relative path
            }
        
        return None

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
            "skipped_paths",
            "permission_blocked_count",
            "errors",
            "files_scanned",
            "tasks",
            "tools",
            "projects",
            "content",
            "line",
            "start_line",
            "end_line",
            "line_count",
            "requested_query",
            "recovered_from_query",
            "resolution_attempts",
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
                **self._evidence_metadata(observations),
            },
        )

    @staticmethod
    def _evidence_metadata(observations: list[dict[str, Any]]) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        for observation in observations:
            raw = observation.get("metadata", {})
            if not isinstance(raw, dict):
                continue
            for key in (
                "root_path",
                "repo_root",
                "path",
                "patterns",
                "match_count",
                "matches",
                "skipped_paths",
                "permission_blocked_count",
                "requested_query",
                "recovered_from_query",
                "resolution_attempts",
            ):
                if key in raw and key not in metadata:
                    metadata[key] = raw[key]
        return metadata

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
        "about",
        "after",
        "again",
        "all",
        "an",
        "app",
        "are",
        "because",
        "before",
        "can",
        "check",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "gave",
        "have",
        "he",
        "her",
        "here",
        "him",
        "his",
        "i",
        "in",
        "is",
        "it",
        "let",
        "look",
        "me",
        "most",
        "need",
        "now",
        "of",
        "on",
        "or",
        "our",
        "people",
        "please",
        "project",
        "saw",
        "she",
        "show",
        "something",
        "tell",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "they",
        "things",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "who",
        "why",
        "you",
        "your",
    }
    terms = []
    for word in words:
        term = word.strip("._-")
        if len(term) > 2 and term not in ignored and not term.isdigit() and not _looks_like_path_fragment(term):
            terms.append(term)
    expanded: list[str] = []
    for term in terms:
        expanded.append(term)
        if term.endswith("ies") and len(term) > 4:
            expanded.append(term[:-3] + "y")
        elif term.endswith("s") and len(term) > 4:
            expanded.append(term[:-1])
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
    return (
        f"I searched {scanned_files} project file(s) in {root} for the requested evidence. "
        f"I found relevant references, but I cannot honestly confirm the final answer from a text scan alone yet. "
        f"The strongest places to inspect next are: {file_text}. "
        f"I filtered out dependency/build folders and kept the raw evidence in the activity stream."
    )


def _rank_relevant_matches(matches: list[Any], patterns: list[str]) -> list[Any]:
    requested = {
        token
        for pattern in patterns
        for token in re.findall(r"[A-Za-z0-9_]+", str(pattern).lower())
        if len(token) > 2
    }

    def score(match: Any) -> tuple[int, int, str]:
        path = str(getattr(match, "path", "")).lower()
        pattern = str(getattr(match, "pattern", "")).lower()
        line = str(getattr(match, "line", "")).lower()
        value = 0
        for token in requested:
            if token in pattern:
                value += 3
            if token in path:
                value += 2
            if token in line:
                value += 1
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


def _normalize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _normalize_value(value) for key, value in arguments.items()}


def _thin_final_answer(answer: str) -> bool:
    words = re.findall(r"[A-Za-z0-9_]+", answer)
    lowered = answer.strip().lower()
    return len(words) < 5 or lowered in {"done", "ok", "complete", "completed", "finished"}


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

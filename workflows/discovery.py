"""Generic project and folder discovery workflow."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

from diagnostics.error_types import ErrorType
from diagnostics.result import SamResult
from diagnostics.trace import trace_step
from tools import SafeLocalTools


_DISCOVERY_VERBS = {
    "find",
    "search",
    "locate",
    "look",
    "check",
}
_FRESH_DISCOVERY_VERBS = {"find", "search", "locate"}
_TARGET_WORDS = {"app", "apps", "project", "projects", "folder", "folders", "repo", "repos", "directory", "directories"}
_FILLER_WORDS = {
    "a",
    "an",
    "and",
    "can",
    "could",
    "for",
    "here",
    "inside",
    "in",
    "it",
    "me",
    "only",
    "please",
    "show",
    "the",
    "there",
    "this",
    "to",
    "with",
    "you",
    "your",
}
_ORDINALS = {
    "first": 0,
    "1st": 0,
    "one": 0,
    "second": 1,
    "2nd": 1,
    "two": 1,
    "third": 2,
    "3rd": 2,
    "three": 2,
    "fourth": 3,
    "4th": 3,
    "four": 3,
    "fifth": 4,
    "5th": 4,
    "five": 4,
}
_INSPECTION_INTENTS = {
    "inspect_repo",
    "inspect_project_repo",
    "inspect_git_state",
    "scan_codebase_patterns",
    "check_python_syntax",
    "inspect_recent_changes",
}


def _memory_value(value: Any) -> Any:
    while isinstance(value, dict) and "value" in value:
        value = value.get("value")
    if isinstance(value, str) and value.strip().startswith("{") and "'value'" in value:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return value
        return _memory_value(parsed)
    return value


@dataclass
class DiscoveryState:
    active_goal: str = ""
    search_keyword: str = ""
    search_root: str = ""
    candidates: list[str] = field(default_factory=list)
    last_listing: list[str] = field(default_factory=list)
    selected_candidate: str = ""

    @classmethod
    def from_memory(cls, memory_block: dict[str, Any] | None) -> "DiscoveryState":
        if not isinstance(memory_block, dict):
            return cls()
        raw = memory_block.get("discovery", {})
        if isinstance(raw, dict) and "value" in raw:
            raw = raw.get("value", {})
        if not isinstance(raw, dict):
            return cls()
        candidates = _memory_value(raw.get("candidates", []))
        last_listing = _memory_value(raw.get("last_listing", []))
        return cls(
            active_goal=str(_memory_value(raw.get("active_goal", "")) or ""),
            search_keyword=str(_memory_value(raw.get("search_keyword", "")) or ""),
            search_root=str(_memory_value(raw.get("search_root", "")) or ""),
            candidates=[str(_memory_value(item)) for item in candidates if str(_memory_value(item)).strip()]
            if isinstance(candidates, list)
            else [],
            last_listing=[str(_memory_value(item)) for item in last_listing if str(_memory_value(item)).strip()]
            if isinstance(last_listing, list)
            else [],
            selected_candidate=str(_memory_value(raw.get("selected_candidate", "")) or ""),
        )

    def to_memory(self) -> dict[str, Any]:
        return {
            "active_goal": self.active_goal,
            "search_keyword": self.search_keyword,
            "search_root": self.search_root,
            "candidates": self.candidates,
            "last_listing": self.last_listing,
            "selected_candidate": self.selected_candidate,
        }

    @property
    def is_active(self) -> bool:
        return bool(self.active_goal and self.search_keyword)


class DiscoveryWorkflow:
    """Goal-driven workflow for finding, filtering, selecting, and opening folders."""

    def __init__(self, tools: SafeLocalTools, *, search_roots: list[str | Path] | None = None) -> None:
        self.tools = tools
        self.search_roots = [Path(root) for root in (search_roots or [])]

    def maybe_handle(
        self,
        text: str,
        *,
        parsed_intent: str = "",
        parsed_parameters: dict[str, Any] | None = None,
        memory_block: dict[str, Any] | None = None,
    ) -> SamResult | None:
        state = DiscoveryState.from_memory(memory_block)
        path = _path_from_text(text) or _natural_root_from_text(text)
        selection = _selection_index(text)
        filter_keyword = _keyword_from_filter(text)
        start_keyword = filter_keyword or _keyword_from_discovery_goal(text)

        if start_keyword and _looks_like_fresh_discovery_start(text) and parsed_intent not in _INSPECTION_INTENTS:
            state = DiscoveryState(active_goal=f"find folder matching {start_keyword}", search_keyword=start_keyword)
            if path:
                return self._list_and_filter_root(state, path)
            inferred = self._search_inferred_roots(state)
            if inferred is not None:
                return inferred
            return self._ask_for_root(state)

        if state.is_active:
            if _asks_current_discovery_goal(text):
                return self._describe_active_goal(state)
            if selection is not None:
                return self._open_or_inspect_candidate(state, selection, text)
            if _explicitly_wants_open(text):
                fallback_selection = self._fallback_open_selection(state)
                if fallback_selection is not None:
                    return self._open_or_inspect_candidate(state, fallback_selection, text)
                if state.candidates:
                    return self._ask_for_selection(state)
            if path:
                provided = Path(path)
                if _path_matches_keyword(provided, state.search_keyword):
                    return self._accept_provided_candidate(state, provided)
                return self._list_and_filter_root(state, path)
            if filter_keyword or _looks_like_filter_followup(text) or _mentions_keyword(text, state.search_keyword):
                if filter_keyword:
                    state.search_keyword = filter_keyword
                    state.active_goal = f"find folder matching {filter_keyword}"
                return self._filter_existing_or_root(state)

        if path and parsed_intent == "list_directory":
            state = DiscoveryState(active_goal="browse folders", search_root=path)
            return self._list_root(state)

        return None

    def _ask_for_root(self, state: DiscoveryState) -> SamResult:
        return SamResult(
            status="success",
            summary=f"Where should I search for folders matching '{state.search_keyword}'?",
            next_action="ask_user",
            metadata=self._metadata(
                "ask_for_root",
                state,
                [
                    trace_step("Goal detected", action="find project/folder", observation=state.active_goal),
                    trace_step("Keyword extracted", observation=state.search_keyword),
                    trace_step("Waiting for search root", status="waiting", reason="No root path is known yet"),
                ],
            ),
        )

    def _list_root(self, state: DiscoveryState) -> SamResult:
        root_result, root = self.tools.resolve_directory_query(state.search_root)
        if not root_result.ok or root is None:
            return root_result
        folders = _folder_paths(root)
        state.search_root = str(root)
        state.last_listing = folders
        state.candidates = folders
        return self._present_candidates(
            state,
            action="list_root",
            trace=[
                trace_step("Path resolved", path=str(root)),
                trace_step("Tool selected", tool="list_directory", action="list_root"),
                trace_step("Observation", observation=f"Found {len(folders)} folder(s)"),
            ],
        )

    def _list_and_filter_root(self, state: DiscoveryState, root_path: str) -> SamResult:
        root_result, root = self.tools.resolve_directory_query(root_path)
        if not root_result.ok or root is None:
            return root_result
        state.search_root = str(root)
        state.last_listing = _folder_paths(root)
        state.candidates = _filter_names(state.last_listing, state.search_keyword)
        return self._present_candidates(
            state,
            action="filter_candidates",
            trace=[
                trace_step("Path resolved", path=str(root)),
                trace_step("Tool selected", tool="list_directory", action="list_root"),
                trace_step("Observation", observation=f"Found {len(state.last_listing)} folder(s)"),
                trace_step("Filtering", action="filter_candidates", observation=state.search_keyword),
                trace_step("Matches found", observation=f"{len(state.candidates)} match(es)"),
            ],
        )

    def _search_inferred_roots(self, state: DiscoveryState) -> SamResult | None:
        attempted: list[str] = []
        all_matches: list[str] = []
        last_listing: list[str] = []
        matching_root = ""

        for root in _inferred_roots(self.search_roots):
            attempted.append(str(root))
            if not root.exists() or not root.is_dir():
                continue
            listing = _folder_paths(root)
            matches = _filter_names(listing, state.search_keyword)
            if listing:
                last_listing = listing
            if matches:
                all_matches.extend(matches)
                matching_root = str(root) if not matching_root else matching_root

        if not all_matches:
            return None

        state.search_root = matching_root
        state.last_listing = last_listing
        state.candidates = _dedupe_candidates_by_name(all_matches)
        return self._present_candidates(
            state,
            action="filter_candidates",
            trace=[
                trace_step("Goal detected", action="find project/folder", observation=state.active_goal),
                trace_step("Keyword extracted", observation=state.search_keyword),
                trace_step("Attempting path resolution", observation=", ".join(attempted[:8])),
                trace_step("Path resolved", path=state.search_root),
                trace_step("Tool selected", tool="list_directory", action="list_root"),
                trace_step("Observation", observation=f"Found {len(state.last_listing)} folder(s)"),
                trace_step("Filtering", action="filter_candidates", observation=state.search_keyword),
                trace_step("Matches found", observation=f"{len(state.candidates)} match(es)"),
            ],
        )

    def _filter_existing_or_root(self, state: DiscoveryState) -> SamResult:
        if state.last_listing:
            state.candidates = _filter_names(state.last_listing, state.search_keyword)
            return self._present_candidates(
                state,
                action="filter_candidates",
                trace=[
                    trace_step("Using previous listing", observation=f"{len(state.last_listing)} item(s)"),
                    trace_step("Filtering", action="filter_candidates", observation=state.search_keyword),
                    trace_step("Matches found", observation=f"{len(state.candidates)} match(es)"),
                ],
            )
        if state.search_root:
            return self._list_and_filter_root(state, state.search_root)
        return self._ask_for_root(state)

    def _describe_active_goal(self, state: DiscoveryState) -> SamResult:
        location = f" in {state.search_root}" if state.search_root else ""
        candidate_text = ""
        if state.candidates:
            names = ", ".join(Path(path).name for path in state.candidates[:5])
            candidate_text = f" Current candidate(s): {names}."
        summary = f"I am looking for a project or folder matching '{state.search_keyword}'{location}.{candidate_text}"
        return SamResult(
            status="success",
            summary=summary,
            next_action="ask_user",
            metadata=self._metadata(
                "describe_active_goal",
                state,
                [
                    trace_step("Active discovery goal", observation=state.active_goal),
                    trace_step("Keyword in focus", observation=state.search_keyword),
                ],
            ),
        )

    def _accept_provided_candidate(self, state: DiscoveryState, path: Path) -> SamResult:
        state.search_root = str(path.parent)
        state.selected_candidate = str(path)
        state.candidates = [str(path)]
        state.last_listing = [str(path)]
        summary = f"Found the project folder matching '{state.search_keyword}': {path}."
        return SamResult(
            status="success",
            summary=summary,
            next_action="stop",
            metadata=self._metadata(
                "accept_provided_candidate",
                state,
                [
                    trace_step("Path resolved", path=str(path)),
                    trace_step("Candidate accepted", path=str(path), reason="Provided path matches active discovery keyword"),
                ],
            ) | {
                "path": str(path),
                "root_path": str(path),
            },
        )

    def _present_candidates(self, state: DiscoveryState, *, action: str, trace: list[dict[str, Any]] | None = None) -> SamResult:
        count = len(state.candidates)
        if count:
            preview = ", ".join(Path(item).name for item in state.candidates[:8])
            summary = (
                f"Found {count} folder(s) matching '{state.search_keyword}' in {state.search_root}: "
                f"{preview}. Which one should I inspect or open?"
            )
        else:
            summary = f"No folders matching '{state.search_keyword}' were found in {state.search_root}."
        return SamResult(
            status="success",
            summary=summary,
            next_action="ask_user" if count else "stop",
            metadata=self._metadata(action, state, trace or []),
        )

    def _ask_for_selection(self, state: DiscoveryState) -> SamResult:
        names = ", ".join(f"{idx}. {Path(path).name}" for idx, path in enumerate(state.candidates[:8], start=1))
        summary = f"I found multiple matches. Reply with a number to choose one: {names}."
        return SamResult(
            status="success",
            summary=summary,
            next_action="ask_user",
            metadata=self._metadata(
                "present_candidates",
                state,
                [
                    trace_step("Selection required", status="waiting", reason="Open requested but multiple candidates are available"),
                    trace_step("Matches found", observation=f"{len(state.candidates)} match(es)"),
                ],
            ),
        )

    def _open_or_inspect_candidate(self, state: DiscoveryState, selection: int, text: str) -> SamResult:
        if selection < 0 or selection >= len(state.candidates):
            return SamResult(
                status="failed",
                summary="That selection is outside the current candidate list.",
                error_type=ErrorType.TOOL_FAILED,
                error_message=str(selection + 1),
                next_action="ask_user",
                metadata=self._metadata(
                    "select_candidate",
                    state,
                    [trace_step("Selection failed", status="failed", reason="Selection was outside the candidate list")],
                ),
            )
        selected = state.candidates[selection]
        state.selected_candidate = selected
        if _explicitly_wants_open(text):
            open_result = self.tools.open_directory(selected)
            open_result.metadata.update(
                self._metadata(
                    "open_candidate",
                    state,
                    [
                        trace_step("Candidate selected", path=selected),
                        trace_step("Tool selected", tool="open_folder", action="open_candidate"),
                        trace_step("Observation", status=open_result.status, observation=open_result.summary),
                    ],
                )
            )
            open_result.metadata["intent"] = "open_folder"
            return open_result

        result, entries = self.tools.list_directory(selected)
        if not result.ok:
            result.metadata.update(
                self._metadata(
                    "inspect_candidate",
                    state,
                    [
                        trace_step("Candidate selected", path=selected),
                        trace_step("Tool selected", tool="list_directory", action="inspect_candidate"),
                        trace_step("Observation", status=result.status, observation=result.summary),
                    ],
                )
            )
            return result
        state.last_listing = entries
        summary = f"{len(entries)} item(s) in {selected}."
        if entries:
            summary += " " + ", ".join(entries[:12])
            if len(entries) > 12:
                summary += f", and {len(entries) - 12} more"
            summary += "."
        return SamResult(
            status="success",
            summary=summary,
            next_action="stop",
            metadata=self._metadata(
                "inspect_candidate",
                state,
                [
                    trace_step("Candidate selected", path=selected),
                    trace_step("Tool selected", tool="list_directory", action="inspect_candidate"),
                    trace_step("Observation", observation=f"Listed {len(entries)} item(s) inside selected folder"),
                ],
            ) | {
                "intent": "list_directory",
                "path": selected,
                "root_path": selected,
                "entries": entries[:100],
                "entry_count": len(entries),
            },
        )

    @staticmethod
    def _fallback_open_selection(state: DiscoveryState) -> int | None:
        if state.selected_candidate and state.selected_candidate in state.candidates:
            return state.candidates.index(state.selected_candidate)
        if len(state.candidates) == 1:
            return 0
        return None

    @staticmethod
    def _metadata(action: str, state: DiscoveryState, trace: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        execution_trace = trace or []
        return {
            "intent": "discovery_workflow",
            "workflow": "discovery",
            "workflow_action": action,
            "active_goal": state.active_goal,
            "search_keyword": state.search_keyword,
            "search_root": state.search_root,
            "candidates": state.candidates,
            "candidate_count": len(state.candidates),
            "last_listing": state.last_listing,
            "selected_candidate": state.selected_candidate,
            "discovery_state": state.to_memory(),
            "workflow_trace": [str(step.get("label", "")) for step in execution_trace],
            "execution_trace": execution_trace,
        }


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


def should_route_discovery(
    text: str,
    *,
    parsed_intent: str = "",
    memory_block: dict[str, Any] | None = None,
) -> bool:
    """Pure routing predicate for the runtime/parser layer.

    Discovery execution stays in DiscoveryWorkflow; this function only says
    whether the current turn belongs to an active or newly-started discovery
    goal.
    """
    state = DiscoveryState.from_memory(memory_block)
    path = _path_from_text(text) or _natural_root_from_text(text)
    selection = _selection_index(text)
    filter_keyword = _keyword_from_filter(text)
    start_keyword = filter_keyword or _keyword_from_discovery_goal(text)

    if start_keyword and _looks_like_fresh_discovery_start(text) and parsed_intent not in _INSPECTION_INTENTS:
        return True

    if state.is_active:
        return bool(
            selection is not None
            or _explicitly_wants_open(text)
            or path
            or _asks_current_discovery_goal(text)
            or filter_keyword
            or _looks_like_filter_followup(text)
            or _mentions_keyword(text, state.search_keyword)
        )

    return bool(path and parsed_intent == "list_directory")


def _natural_root_from_text(text: str) -> str:
    lowered = text.lower()
    base_map = {
        "desktop": Path.home() / "Desktop",
        "downloads": Path.home() / "Downloads",
        "download": Path.home() / "Downloads",
        "documents": Path.home() / "Documents",
        "document": Path.home() / "Documents",
    }
    bases = [base for name, base in base_map.items() if name in lowered]
    if not bases:
        return ""

    words = _words(text)
    ignored = _FILLER_WORDS | _DISCOVERY_VERBS | _TARGET_WORDS | set(base_map)
    candidates = [word for word in words if word not in ignored]
    for base in bases:
        if not base.exists() or not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir() and any(word == child.name.lower() for word in candidates):
                return str(child)
        for child in base.iterdir():
            if child.is_dir() and any(word in child.name.lower() for word in candidates):
                return str(child)
    return ""


def _looks_like_discovery_start(text: str) -> bool:
    words = set(_words(text))
    return bool(words & _DISCOVERY_VERBS)


def _looks_like_fresh_discovery_start(text: str) -> bool:
    lowered = text.lower()
    words = set(_words(text))
    return bool(words & _FRESH_DISCOVERY_VERBS) or "look for" in lowered


def _looks_like_filter_followup(text: str) -> bool:
    lowered = text.lower()
    return "only" in lowered or "matching" in lowered or "with " in lowered or "containing" in lowered


def _mentions_keyword(text: str, keyword: str) -> bool:
    return bool(keyword and keyword.lower() in text.lower())


def _explicitly_wants_open_or_inspect(text: str) -> bool:
    lowered = text.lower()
    return _explicitly_wants_open(text) or "inspect" in lowered or "check" in lowered or "look inside" in lowered


def _explicitly_wants_open(text: str) -> bool:
    lowered = text.lower()
    return "open" in lowered or "launch" in lowered


def _selection_index(text: str) -> int | None:
    lowered = text.lower()
    for token, index in _ORDINALS.items():
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            return index
    match = re.search(r"\b(?:number|#)?\s*(\d+)\b", lowered)
    if match:
        return max(0, int(match.group(1)) - 1)
    return None


def _keyword_from_filter(text: str) -> str:
    match = re.search(r"\b(?:with|containing|matching|called|named)\s+([A-Za-z0-9_.-]+)", text, flags=re.IGNORECASE)
    if match:
        token = match.group(1).strip(" .,:;!?`'\"").lower()
        if token and token not in _FILLER_WORDS:
            return token
    return ""


def _keyword_from_discovery_goal(text: str) -> str:
    direct_target = _keyword_before_target_word(text, ("app", "project", "repo", "directory"))
    if direct_target:
        return direct_target
    folder_target = _keyword_before_target_word(text, ("folder",))
    if folder_target:
        return folder_target
    words = _words(text)
    useful = [
        word
        for word in words
        if word not in _FILLER_WORDS
        and word not in _DISCOVERY_VERBS
        and word not in _TARGET_WORDS
        and not _looks_like_path_word(word)
    ]
    return useful[-1] if useful else ""


def _keyword_before_target_word(text: str, targets: tuple[str, ...]) -> str:
    target_pattern = "|".join(re.escape(target) for target in targets)
    matches = re.findall(rf"\b([A-Za-z0-9_.-]+)\s+(?:{target_pattern})s?\b", text, flags=re.IGNORECASE)
    for raw in matches:
        token = raw.strip(" .,:;!?`'\"").lower()
        if token and token not in _FILLER_WORDS and token not in _DISCOVERY_VERBS and token not in _TARGET_WORDS:
            return token
    return ""


def _asks_current_discovery_goal(text: str) -> bool:
    lowered = text.lower()
    return (
        ("what" in lowered and any(token in lowered for token in ("looking for", "lookign for", "searching for")))
        or "what are you trying to find" in lowered
        or "what was the folder" in lowered
    )


def _path_matches_keyword(path: Path, keyword: str) -> bool:
    if not keyword:
        return False
    name = path.name.lower()
    needle = keyword.lower().strip()
    return needle == name or needle in name


def _looks_like_path_word(word: str) -> bool:
    return bool(re.fullmatch(r"[a-z]:", word.lower()))


def _words(text: str) -> list[str]:
    return [word.lower() for word in re.findall(r"[A-Za-z0-9_.-]+", text)]


def _folder_paths(root: Path) -> list[str]:
    try:
        return sorted(str(item) for item in root.iterdir() if item.is_dir())
    except OSError:
        return []


def _inferred_roots(configured_roots: list[Path]) -> list[Path]:
    roots: list[Path] = []
    for root in configured_roots:
        roots.extend([root, root.parent, root.parent.parent])

    cwd = Path.cwd()
    roots.extend([cwd, cwd.parent, cwd.parent.parent, Path.home() / "Desktop", Path.home() / "Documents"])

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(resolved)
    return unique


def _filter_names(names: list[str], keyword: str) -> list[str]:
    needle = keyword.lower().strip()
    if not needle:
        return names
    return [name for name in names if needle in Path(name).name.lower()]


def _dedupe_candidates_by_name(paths: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for path in paths:
        key = Path(path).name.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped

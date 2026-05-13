"""Deterministic operational policy for safe autonomous read loops."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any


@dataclass(frozen=True)
class AutonomousDecision:
    action: str
    tool: str = ""
    arguments: dict[str, Any] | None = None
    answer: str = ""
    question: str = ""

    def to_model_shape(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"action": self.action}
        if self.tool:
            payload["tool"] = self.tool
        if self.arguments is not None:
            payload["arguments"] = self.arguments
        if self.answer:
            payload["answer"] = self.answer
        if self.question:
            payload["question"] = self.question
        return payload


class AutonomousDecisionPolicy:
    """Chooses conservative read-only actions when model planning is unavailable."""

    def decide(
        self,
        *,
        user_text: str,
        tools: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        memory_block: dict[str, Any] | None,
        workspace_root: str,
    ) -> dict[str, Any]:
        available = {str(item.get("name", "")) for item in tools if isinstance(item, dict)}
        path = _explicit_path(user_text) or _memory_root(memory_block) or workspace_root
        patterns = _search_terms(user_text)

        if not observations:
            if patterns and "scan_codebase_patterns" in available:
                return AutonomousDecision(
                    "tool",
                    "scan_codebase_patterns",
                    {"query": path, "patterns": patterns},
                ).to_model_shape()
            if "inspect_repo" in available:
                return AutonomousDecision("tool", "inspect_repo", {"query": path}).to_model_shape()
            if "list_directory" in available:
                return AutonomousDecision("tool", "list_directory", {"path": path}).to_model_shape()
            return AutonomousDecision("ask_user", question="I need a project path or clearer target before I can continue.").to_model_shape()

        last = observations[-1]
        if _failed(last):
            if _missing_target(last):
                return AutonomousDecision(
                    "ask_user",
                    question="I could not resolve the project or file to inspect. Please give me the exact path.",
                ).to_model_shape()
            return AutonomousDecision("final", answer=_synthesize(user_text, observations)).to_model_shape()

        last_tool = str(last.get("tool", ""))
        metadata = last.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}

        if last_tool == "scan_codebase_patterns":
            decision = _next_evidence_read(
                metadata=metadata,
                patterns=patterns,
                available=available,
                observations=observations,
                fallback_root=path,
            )
            if decision is not None:
                return decision.to_model_shape()
            return AutonomousDecision("final", answer=_synthesize(user_text, observations)).to_model_shape()

        if last_tool in {"read_file", "read_file_region"}:
            scan_metadata = _latest_scan_metadata(observations)
            decision = _next_evidence_read(
                metadata=scan_metadata,
                patterns=patterns,
                available=available,
                observations=observations,
                fallback_root=path,
            )
            if decision is not None:
                return decision.to_model_shape()
            return AutonomousDecision("final", answer=_synthesize(user_text, observations)).to_model_shape()

        if last_tool in {"inspect_repo", "inspect_git_state", "list_directory", "inspect_recent_changes"}:
            return AutonomousDecision("final", answer=_synthesize(user_text, observations)).to_model_shape()

        if len(observations) >= 3:
            return AutonomousDecision("final", answer=_synthesize(user_text, observations)).to_model_shape()

        return AutonomousDecision("final", answer=_synthesize(user_text, observations)).to_model_shape()


def _explicit_path(text: str) -> str:
    match = re.search(r"[A-Za-z]:[\\/][^\r\n\"']+", text)
    if not match:
        return ""
    return match.group(0).strip().rstrip(".,;")


def _memory_root(memory_block: dict[str, Any] | None) -> str:
    if not isinstance(memory_block, dict):
        return ""
    daily_state = memory_block.get("daily_state", {})
    if not isinstance(daily_state, dict):
        return ""
    for key in ("last_project_root_path", "last_file_path"):
        item = daily_state.get(key, {})
        value = item.get("value", "") if isinstance(item, dict) else ""
        text = str(value).strip()
        if text:
            return str(Path(text).parent if key == "last_file_path" else Path(text))
    return ""


def _search_terms(text: str) -> list[str]:
    without_paths = re.sub(r"[A-Za-z]:[\\/][^\r\n\"']+", " ", text.lower())
    words = re.findall(r"[a-zA-Z0-9_.-]+", without_paths)
    ignored = {
        "about",
        "after",
        "again",
        "all",
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
        "find",
        "for",
        "from",
        "gave",
        "have",
        "he",
        "her",
        "here",
        "him",
        "his",
        "help",
        "i",
        "in",
        "into",
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
        "with",
        "would",
        "you",
        "your",
    }
    terms = []
    for word in words:
        term = word.strip("._-")
        if len(term) > 2 and term not in ignored and not term.isdigit():
            terms.append(term)
    expanded: list[str] = []
    for term in terms:
        expanded.append(term)
        if term.endswith("ies"):
            expanded.append(term[:-3] + "y")
        if term.endswith("s") and len(term) > 4:
            expanded.append(term[:-1])
    return list(dict.fromkeys(expanded))[:8]


def _failed(observation: dict[str, Any]) -> bool:
    return str(observation.get("status", "")).lower() not in {"success", "ok"}


def _missing_target(observation: dict[str, Any]) -> bool:
    text = f"{observation.get('summary', '')} {observation.get('error', '')}".lower()
    return any(token in text for token in ("not found", "missing", "could not resolve", "required"))


def strongest_evidence_match(matches: list[Any], patterns: list[str], excluded_paths: set[str] | None = None) -> dict[str, Any]:
    ranked = ranked_evidence_matches(matches, patterns, excluded_paths=excluded_paths)
    return ranked[0] if ranked else {}


def ranked_evidence_matches(
    matches: list[Any],
    patterns: list[str],
    *,
    excluded_paths: set[str] | None = None,
) -> list[dict[str, Any]]:
    dict_matches = [item for item in matches if isinstance(item, dict)]
    if not dict_matches:
        return []
    excluded = {_normalize_path_key(path) for path in excluded_paths or set()}
    lowered_patterns = [pattern.lower() for pattern in patterns]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in dict_matches:
        path = str(item.get("path", "")).strip()
        if path and not _path_excluded(path, excluded):
            grouped.setdefault(path, []).append(item)

    scored = [(file_evidence_score(path, items, lowered_patterns), path, items) for path, items in grouped.items()]
    scored = [item for item in scored if item[0] >= _minimum_relevance_score(lowered_patterns)]
    if not scored:
        return []
    scored.sort(key=lambda item: (-item[0], item[1].lower()))
    ranked: list[dict[str, Any]] = []
    for _score, _path, items in scored:
        ranked.append(sorted(items, key=lambda item: int(item.get("line_number", 0) or 0))[0])
    return ranked


def evidence_path_is_relevant(path: str, matches: list[Any], patterns: list[str]) -> bool:
    return evidence_path_score(path, matches, patterns) >= _minimum_relevance_score([pattern.lower() for pattern in patterns])


def evidence_path_score(path: str, matches: list[Any], patterns: list[str]) -> int:
    dict_matches = [item for item in matches if isinstance(item, dict)]
    normalized = path.replace("\\", "/").lower()
    items = [
        item
        for item in dict_matches
        if _same_or_suffix_path(normalized, str(item.get("path", "")).replace("\\", "/").lower())
    ]
    if not items:
        return 0
    return file_evidence_score(path, items, [pattern.lower() for pattern in patterns])


def file_evidence_score(path: str, items: list[dict[str, Any]], lowered_patterns: list[str]) -> int:
    path_lower = path.lower()
    lines = " ".join(str(item.get("line", "")).lower() for item in items)
    patterns_hit = {str(item.get("pattern", "")).lower() for item in items}
    evidence_text = f"{path_lower} {lines} {' '.join(patterns_hit)}"
    query_terms = _query_terms(lowered_patterns)
    if not query_terms:
        return len(items)

    covered_terms = {term for term in query_terms if term in evidence_text}
    coverage = len(covered_terms)
    value = coverage * 4
    value += min(len(items), 8)
    value += min(len(patterns_hit), 4)

    for pattern in lowered_patterns:
        if pattern and pattern in path_lower:
            value += 2
        if pattern and pattern in lines:
            value += 1

    if coverage < max(1, min(3, len(query_terms))):
        value -= 8
    if _looks_like_general_notes_file(path_lower) and coverage < len(query_terms):
        value -= 4
    if any(token in path_lower for token in ("test", "spec", "node_modules", "build")):
        value -= 3
    return value


def _query_terms(patterns: list[str]) -> set[str]:
    terms: set[str] = set()
    for pattern in patterns:
        for token in re.findall(r"[a-zA-Z0-9_]+", pattern.lower()):
            if len(token) <= 2 or token in _QUERY_STOPWORDS:
                continue
            terms.add(token)
            if token.endswith("ies") and len(token) > 4:
                terms.add(token[:-3] + "y")
            elif token.endswith("s") and len(token) > 4:
                terms.add(token[:-1])
    return terms


def _minimum_relevance_score(patterns: list[str]) -> int:
    return max(3, min(6, len(_query_terms(patterns)) * 2))


def _looks_like_general_notes_file(path_lower: str) -> bool:
    name = Path(path_lower).name
    general_names = {
        "todo.md",
        "readme.md",
        "changelog.md",
        "status.md",
        "notes.md",
    }
    return name in general_names or "status" in name or "changelog" in name


def _same_or_suffix_path(candidate: str, match_path: str) -> bool:
    return candidate == match_path or candidate.endswith(f"/{match_path}")


def _next_evidence_read(
    *,
    metadata: dict[str, Any],
    patterns: list[str],
    available: set[str],
    observations: list[dict[str, Any]],
    fallback_root: str,
) -> AutonomousDecision | None:
    matches = metadata.get("matches", []) if isinstance(metadata, dict) else []
    if not isinstance(matches, list) or not matches:
        return None
    read_paths = _read_paths(observations)
    read_count = len(read_paths)
    if read_count >= 3:
        return None
    strongest = strongest_evidence_match(matches, patterns, excluded_paths=read_paths)
    file_path = str(strongest.get("path", "")).strip()
    if not file_path:
        return None
    root = str(metadata.get("root_path", fallback_root)).strip()
    candidate = Path(file_path)
    resolved = str(candidate if candidate.is_absolute() else Path(root) / file_path)
    line = int(strongest.get("line_number", 1) or 1)
    if "read_file_region" in available:
        return AutonomousDecision(
            "tool",
            "read_file_region",
            {"path": resolved, "line": line, "context_before": 18, "context_after": 70},
        )
    if "read_file" in available:
        return AutonomousDecision("tool", "read_file", {"path": resolved, "max_chars": 12000})
    return None


def _latest_scan_metadata(observations: list[dict[str, Any]]) -> dict[str, Any]:
    for observation in reversed(observations):
        if str(observation.get("tool", "")) != "scan_codebase_patterns":
            continue
        metadata = observation.get("metadata", {})
        return metadata if isinstance(metadata, dict) else {}
    return {}


def _read_paths(observations: list[dict[str, Any]]) -> set[str]:
    paths: set[str] = set()
    for observation in observations:
        if str(observation.get("tool", "")) not in {"read_file", "read_file_region"}:
            continue
        metadata = observation.get("metadata", {})
        if isinstance(metadata, dict):
            path = str(metadata.get("path", "")).strip()
        else:
            path = ""
        if path:
            paths.add(_normalize_path_key(path))
    return paths


def _normalize_path_key(path: str) -> str:
    return path.replace("\\", "/").lower().strip()


def _path_excluded(path: str, excluded: set[str]) -> bool:
    candidate = _normalize_path_key(path)
    return any(candidate == item or item.endswith(f"/{candidate}") for item in excluded)


_QUERY_STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "app",
    "are",
    "because",
    "before",
    "can",
    "check",
    "code",
    "could",
    "doc",
    "docs",
    "file",
    "find",
    "folder",
    "for",
    "from",
    "have",
    "help",
    "into",
    "know",
    "let",
    "look",
    "need",
    "please",
    "project",
    "repo",
    "saw",
    "show",
    "tell",
    "that",
    "the",
    "this",
    "was",
    "what",
    "when",
    "where",
    "with",
    "you",
}


def _synthesize(user_text: str, observations: list[dict[str, Any]]) -> str:
    useful = [item for item in observations if str(item.get("summary", "")).strip()]
    if not useful:
        return "I could not gather enough evidence to answer that safely."
    read_observations = [
        item
        for item in useful
        if str(item.get("tool", "")) in {"read_file", "read_file_region"}
        and isinstance(item.get("metadata", {}), dict)
        and str(item.get("metadata", {}).get("content", "")).strip()
    ]
    if read_observations:
        evidence_lines: list[str] = []
        for item in read_observations[-3:]:
            metadata = item.get("metadata", {})
            path = metadata.get("path") or metadata.get("root_path") or metadata.get("repo_root")
            start_line = metadata.get("start_line")
            end_line = metadata.get("end_line")
            line_suffix = f":{start_line}-{end_line}" if start_line and end_line else ""
            snippet = _plain_evidence_snippet(str(metadata.get("content", "")))
            evidence_lines.append(f"{path}{line_suffix} says: {snippet}")
        return (
            f"I checked the strongest evidence I found for: {user_text}. "
            + " ".join(evidence_lines)
        )

    final = useful[-1]
    summary = str(final.get("summary", "")).strip()
    metadata = final.get("metadata", {})
    if isinstance(metadata, dict):
        content = str(metadata.get("content", "")).strip()
        if content:
            path = metadata.get("path") or metadata.get("root_path") or metadata.get("repo_root")
            snippet = " ".join(content.split())[:500]
            return (
                f"I inspected the strongest places to inspect next and verified the best one: {path}. "
                f"The evidence I found says: {snippet}"
            )
        match_count = metadata.get("match_count")
        if match_count is not None:
            return f"I checked the available evidence for: {user_text}. I found {match_count} relevant match(es). {summary}"
        path = metadata.get("path") or metadata.get("root_path") or metadata.get("repo_root")
        if path:
            return f"I checked {path}. {summary}"
    return summary


def synthesize_evidence_answer(user_text: str, observations: list[dict[str, Any]]) -> str:
    return _synthesize(user_text, observations)


def _plain_evidence_snippet(content: str) -> str:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    joined = " ".join(lines)
    return re.sub(r"\s+", " ", joined)[:700]

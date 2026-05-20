"""
Block 3: Sam's discovery engine.

Searches the real file system — no sandboxing, no hardcoded paths.
Looks across Desktop, Documents, Desktop subdirectories, home, and CWD.

Flow:
  1. Extract what the user is looking for (keyword) from their message
  2. Walk the search roots for directories that match
  3. Detect project type (Flutter, React, Python, etc.)
  4. Return a ranked list of candidates
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Project type detection — look for marker files
# ---------------------------------------------------------------------------

_PROJECT_MARKERS: dict[str, list[str]] = {
    "Flutter / Dart": ["pubspec.yaml"],
    "React / Node":   ["package.json"],
    "Python":         ["requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile"],
    "Go":             ["go.mod"],
    "Rust":           ["Cargo.toml"],
    "Java / Gradle":  ["build.gradle", "build.gradle.kts"],
    "Java / Maven":   ["pom.xml"],
    "Ruby":           ["Gemfile"],
    ".NET":           [".csproj", ".fsproj", ".vbproj"],
}


def _detect_project_type(path: Path) -> str:
    """Return a human-readable project type string, or empty string if unknown."""
    try:
        children = {p.name for p in path.iterdir()}
    except OSError:
        return ""

    for label, markers in _PROJECT_MARKERS.items():
        for marker in markers:
            if marker.startswith("."):
                # Check for extension match
                if any(c.endswith(marker) for c in children):
                    return label
            elif marker in children:
                return label
    return ""


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


# ---------------------------------------------------------------------------
# Search roots — where to look on the machine
# ---------------------------------------------------------------------------

def _search_roots() -> list[Path]:
    """
    Build the list of directories to search.
    Includes: Desktop, Desktop subdirs (one level), Documents, home, CWD.
    """
    home = Path.home()
    desktop = home / "Desktop"
    roots: list[Path] = [
        home,
        desktop,
        home / "Documents",
        home / "Downloads",
        Path.cwd(),
        Path.cwd().parent,
    ]

    # One level deep into Desktop — catches Desktop/Darey, Desktop/Projects, etc.
    try:
        for child in desktop.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                roots.append(child)
    except OSError:
        pass

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root
        key = str(resolved).lower()
        if key not in seen and resolved.exists():
            seen.add(key)
            unique.append(resolved)

    return unique


# ---------------------------------------------------------------------------
# Content-aware project name extraction
# ---------------------------------------------------------------------------

def _read_text_safe(path: Path, max_bytes: int = 2000) -> str:
    """Read a file's first `max_bytes` bytes as text, tolerating encoding issues."""
    try:
        with open(path, "rb") as f:
            raw = f.read(max_bytes)
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return ""


def _project_display_name(path: Path) -> str:
    """
    Try to extract the real app/project name from inside the project folder.
    Checks: README.md heading, package.json name, pubspec.yaml name.
    Returns empty string if nothing useful found.
    """
    # README.md — first H1 heading
    readme = path / "README.md"
    if readme.exists():
        text = _read_text_safe(readme, 500)
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("# "):
                name = line[2:].strip()
                # Strip emoji, badges, special chars at end
                name = name.split("—")[0].split("-")[0].split("|")[0].strip()
                if 2 < len(name) < 60:
                    return name

    # package.json — "name" field (Node/React)
    pkg = path / "package.json"
    if pkg.exists():
        try:
            import json as _j
            data = _j.loads(_read_text_safe(pkg, 1000))
            name = data.get("name", "")
            # Skip generic scaffolding names
            skip = {"vite_react_shadcn_ts", "my-app", "app", "project", ""}
            if name and name not in skip:
                return name
        except Exception:
            pass

    # pubspec.yaml — "name:" field (Flutter/Dart)
    pubspec = path / "pubspec.yaml"
    if pubspec.exists():
        text = _read_text_safe(pubspec, 500)
        for line in text.splitlines():
            if line.startswith("name:"):
                name = line[5:].strip().strip('"').strip("'")
                if name:
                    return name

    return ""


# ---------------------------------------------------------------------------
# Found project dataclass
# ---------------------------------------------------------------------------

@dataclass
class FoundProject:
    name: str
    path: Path
    project_type: str = ""
    is_git: bool = False
    score: int = 0  # Higher = better match

    def display(self, index: int) -> str:
        parts = [f"{index}. {self.name}"]
        parts.append(f"   Path: {self.path}")
        if self.project_type:
            parts.append(f"   Type: {self.project_type}")
        if self.is_git:
            parts.append(f"   Git:  yes")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Core search function
# ---------------------------------------------------------------------------

def search(keyword: str, extra_roots: list[Path] | None = None) -> list[FoundProject]:
    """
    Search the file system for directories matching `keyword`.

    Scoring:
      +10  exact name match (case-insensitive)
      +5   name starts with keyword
      +3   keyword found anywhere in name
      +2   has a project marker file
      +1   is a git repo
    """
    needle = keyword.lower().strip()
    if not needle:
        return []

    roots = _search_roots()
    if extra_roots:
        roots.extend(extra_roots)

    candidates: dict[str, FoundProject] = {}  # path string → FoundProject

    for root in roots:
        if not root.is_dir():
            continue
        try:
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                name = child.name
                name_lower = name.lower()

                # Score the match — folder name first
                score = 0
                if name_lower == needle:
                    score += 10
                elif name_lower.startswith(needle):
                    score += 5
                elif needle in name_lower:
                    score += 3
                else:
                    # Check with hyphens/underscores/dots removed
                    flat = name_lower.replace("-", "").replace("_", "").replace(".", "")
                    flat_needle = needle.replace("-", "").replace("_", "").replace(".", "")
                    if flat_needle in flat:
                        score += 2

                # Content-aware: check the real app name inside the project
                display_name = _project_display_name(child)
                if display_name:
                    dn_lower = display_name.lower()
                    dn_flat = dn_lower.replace("-", "").replace("_", "").replace(" ", "")
                    needle_flat = needle.replace("-", "").replace("_", "").replace(" ", "")
                    if dn_lower == needle or dn_flat == needle_flat:
                        score += 10  # Exact app name match
                    elif needle in dn_lower or needle_flat in dn_flat:
                        score += 6   # Partial app name match

                if score == 0:
                    continue  # No match at all

                project_type = _detect_project_type(child)
                is_git = _is_git_repo(child)

                if project_type:
                    score += 2
                if is_git:
                    score += 1

                # Use the real app name as the display name if available
                display = display_name if display_name else name

                key = str(child.resolve()).lower()
                if key not in candidates or score > candidates[key].score:
                    candidates[key] = FoundProject(
                        name=display,
                        path=child.resolve(),
                        project_type=project_type,
                        is_git=is_git,
                        score=score,
                    )
        except OSError:
            continue

    results = sorted(candidates.values(), key=lambda p: p.score, reverse=True)
    return results[:10]  # Top 10 max


# ---------------------------------------------------------------------------
# Keyword extraction — pull "planflow" out of "find the planflow project"
# ---------------------------------------------------------------------------

_KEYWORD_PROMPT = """\
Extract the project or file name the user is searching for.

Rules:
- Return ONLY the search keyword/name — nothing else
- Strip filler words: find, search, locate, the, a, an, for, me, project, folder, app, repo
- Preserve the actual name as-is (e.g. "planflow", "Lina", "doc_to_pptx", "Estate")
- If you cannot extract a clear name, return: UNCLEAR

Examples:
  "find the planflow project" → planflow
  "search for Lina on my machine" → Lina
  "locate doc_to_pptx" → doc_to_pptx
  "find the Estate Firebase app" → Estate
  "Sam, search my Desktop for archplan-ai" → archplan-ai
  "where is the flutter app I made?" → UNCLEAR

User message: {message}"""


def extract_keyword(message: str, llm_client: Any) -> str | None:
    """
    Use the LLM to pull the search keyword out of the user's message.
    Returns None if no clear keyword could be extracted.
    """
    prompt = _KEYWORD_PROMPT.format(message=message.strip())

    try:
        import json as _json
        from urllib import request as _req

        payload = _json.dumps({
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
        with _req.urlopen(req, timeout=15) as resp:
            body = _json.loads(resp.read())
            raw = str(body.get("response", "")).strip()
            # Take first line in case the model adds explanation
            keyword = raw.splitlines()[0].strip() if raw else ""

        if keyword.upper() == "UNCLEAR" or not keyword:
            return None
        return keyword

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Format results for Sam to present to the user
# ---------------------------------------------------------------------------

def format_results(keyword: str, results: list[FoundProject]) -> str:
    if not results:
        return (
            f"I searched everywhere I know to look and couldn't find anything matching "
            f'"{keyword}". It might be under a different name, or in a location I haven\'t '
            f"checked yet. Can you give me a hint — roughly where did you put it?"
        )

    if len(results) == 1:
        p = results[0]
        lines = [f'Found it — looks like this is the one:']
        lines.append("")
        lines.append(p.display(1))
        lines.append("")
        lines.append("Is that the right one? Say yes and I'll get to work on it.")
        return "\n".join(lines)

    lines = [f'I found {len(results)} match(es) for "{keyword}". Which one?']
    lines.append("")
    for i, p in enumerate(results, 1):
        lines.append(p.display(i))
        lines.append("")
    lines.append("Tell me the number (or the name) and I'll take it from there.")
    return "\n".join(lines)

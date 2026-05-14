"""Project discovery from local filesystem evidence."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from diagnostics.result import SamResult

from .registry import ProjectRecord, ProjectRegistry


@dataclass(frozen=True)
class ProjectDiscoveryReport:
    scanned_roots: list[str]
    discovered: list[ProjectRecord]


class ProjectDiscoveryService:
    """Discover project roots from marker files and register them."""

    SKIP_DIRS = {
        ".git",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
        ".dart_tool",
        ".gradle",
    }

    MARKERS = {
        "pubspec.yaml",
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "firebase.json",
        "Cargo.toml",
        "go.mod",
    }

    def __init__(self, registry: ProjectRegistry) -> None:
        self.registry = registry

    def scan(self, roots: Iterable[str | Path], *, max_depth: int = 2) -> tuple[SamResult, ProjectDiscoveryReport]:
        scanned: list[str] = []
        records: list[ProjectRecord] = []
        seen: set[str] = set()
        for root in roots:
            base = Path(root).expanduser()
            try:
                if not base.exists() or not base.is_dir():
                    continue
                resolved = base.resolve()
            except OSError:
                continue
            scanned.append(str(resolved))
            for project_root in self._candidate_project_roots(resolved, max_depth=max_depth):
                key = str(project_root).lower()
                if key in seen:
                    continue
                seen.add(key)
                record = self._record_for(project_root)
                register_result = self.registry.register(record)
                if register_result.ok:
                    records.append(record)

        report = ProjectDiscoveryReport(scanned_roots=scanned, discovered=records)
        return (
            SamResult(
                status="success",
                summary=f"Discovered {len(records)} project(s) from {len(scanned)} root(s).",
                next_action="stop",
                metadata={
                    "scanned_roots": scanned,
                    "project_count": len(records),
                    "projects": [record.name for record in records],
                },
            ),
            report,
        )

    def _candidate_project_roots(self, root: Path, *, max_depth: int) -> list[Path]:
        candidates: list[Path] = []
        queue: list[tuple[Path, int]] = [(root, 0)]
        while queue:
            current, depth = queue.pop(0)
            if current.name in self.SKIP_DIRS:
                continue
            try:
                children = list(current.iterdir())
            except OSError:
                continue
            names = {child.name for child in children}
            if names & self.MARKERS or (current / ".git").exists():
                candidates.append(current)
                continue
            if depth >= max_depth:
                continue
            for child in children:
                if child.is_dir() and child.name not in self.SKIP_DIRS and not child.name.startswith("."):
                    queue.append((child, depth + 1))
        return candidates

    def _record_for(self, root: Path) -> ProjectRecord:
        name = self._read_name(root)
        stack = self._detect_stack(root)
        description = self._read_description(root)
        return ProjectRecord(
            project_id=_slugify(str(root)),
            name=name,
            root_path=str(root),
            stack=stack,
            important_files=self._important_files(root),
        )

    def _read_name(self, root: Path) -> str:
        pubspec = root / "pubspec.yaml"
        if pubspec.exists():
            value = _read_simple_yaml_field(pubspec, "name")
            if value:
                return value
        package = root / "package.json"
        if package.exists():
            try:
                data = json.loads(package.read_text(encoding="utf-8", errors="replace"))
                value = str(data.get("name", "")).strip()
                if value:
                    return value
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        for readme in (root / "README.md", root / "readme.md"):
            if not readme.exists():
                continue
            try:
                for line in readme.read_text(encoding="utf-8", errors="replace").splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        title = stripped.lstrip("#").strip()
                        if title:
                            return title[:80]
            except OSError:
                pass
        return root.name

    def _read_description(self, root: Path) -> str:
        parts: list[str] = []
        pubspec = root / "pubspec.yaml"
        if pubspec.exists():
            value = _read_simple_yaml_field(pubspec, "description")
            if value:
                parts.append(value)
        package = root / "package.json"
        if package.exists():
            try:
                data = json.loads(package.read_text(encoding="utf-8", errors="replace"))
                value = str(data.get("description", "")).strip()
                if value:
                    parts.append(value)
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        for readme in (root / "README.md", root / "readme.md"):
            if not readme.exists():
                continue
            try:
                text = readme.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            paragraph = _first_readme_paragraph(text)
            if paragraph:
                parts.append(paragraph)
                break
        return " | ".join(dict.fromkeys(parts))[:500]

    def _detect_stack(self, root: Path) -> str:
        markers = {
            "pubspec.yaml": "flutter",
            "package.json": "node",
            "pyproject.toml": "python",
            "requirements.txt": "python",
            "Cargo.toml": "rust",
            "go.mod": "go",
        }
        found = [stack for marker, stack in markers.items() if (root / marker).exists()]
        if (root / "firebase.json").exists():
            found.append("firebase")
        return "+".join(dict.fromkeys(found)) or "unknown"

    def _important_files(self, root: Path) -> list[str]:
        candidates = [
            "README.md",
            "readme.md",
            "pubspec.yaml",
            "package.json",
            "pyproject.toml",
            "requirements.txt",
            "firebase.json",
        ]
        return [item for item in candidates if (root / item).exists()]


def _read_simple_yaml_field(path: Path, field: str) -> str:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{field}:"):
                return stripped.split(":", 1)[1].strip().strip("'\"")
    except OSError:
        return ""
    return ""


def _first_readme_paragraph(text: str) -> str:
    lines: list[str] = []
    seen_heading = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            seen_heading = True
            continue
        if not line:
            if lines:
                break
            continue
        if seen_heading or not lines:
            lines.append(line)
        if len(lines) >= 3:
            break
    return " ".join(lines)[:300]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return slug[-80:] or "project"

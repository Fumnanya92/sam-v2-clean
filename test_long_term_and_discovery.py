from __future__ import annotations

import json
import tempfile
from pathlib import Path

from core.runtime import SamRuntime
from memory.long_term import ensure_schema, learn, log_turn, recall_lessons, recall_recent_conversation, store_fact, recall
from projects import ProjectDiscoveryService, ProjectRegistry


def test_long_term_memory_stores_and_recalls_generic_context() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "sam.db"

        assert ensure_schema(db_path).ok
        assert store_fact(db_path, key="deployment rule", value="Run checks before release.", scope="repo").ok
        assert learn(db_path, situation="release failed", what_worked="run tests first", scope="repo").ok
        assert log_turn(db_path, session_id="s1", role="user", message="check release", action="inspect", scope="repo").ok

        assert recall(db_path, "release", scope="repo")[0]["key"] == "deployment rule"
        assert recall_lessons(db_path, "release", scope="repo")[0]["what_worked"] == "run tests first"
        assert recall_recent_conversation(db_path)[0]["message"] == "check release"


def test_project_discovery_registers_projects_from_their_own_files() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        project = root / "UsefulApp"
        project.mkdir()
        (project / "package.json").write_text(
            json.dumps({"name": "useful-app", "description": "A local project for billing checks."}),
            encoding="utf-8",
        )
        registry = ProjectRegistry(root / "projects.json")

        result, report = ProjectDiscoveryService(registry).scan([root])
        match_result, match = registry.find_project("billing app")

        assert result.ok
        assert len(report.discovered) == 1
        assert match_result.ok
        assert match is not None
        assert match.root_path == str(project.resolve())


def test_runtime_start_discovers_nearby_projects() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        workspace = root / "workspace"
        nearby = root / "NearbyProject"
        nearby.mkdir()
        (nearby / "pyproject.toml").write_text("[project]\nname = 'nearby'\n", encoding="utf-8")
        runtime = SamRuntime(
            db_path=root / "sam.db",
            memory_path=root / "memory.json",
            session_path=root / "session.json",
            workspace_root=workspace,
        )

        result = runtime.start()
        projects_result, projects = runtime.handler.router.project_registry.list_projects()

        assert result.ok
        assert projects_result.ok
        assert any(Path(project.root_path) == nearby.resolve() for project in projects)
